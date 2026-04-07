# =============================================================================
# agents/orchestrator_v2.py
# ORCHESTRATOR V2 — Redis-backed pipeline coordinator for Bit-Counting.
#
# DESIGN GUARANTEES:
#   - Does NOT make accounting decisions — only manages flow, queues,
#     traceability, and fault isolation.
#   - All exceptions from agents are caught — _process_job never raises.
#   - Retry with exponential backoff (stored in job metadata, not enforced
#     in-process). MAX_RETRIES=4, then → Q_FAILED + EximiaAlert.
#   - Every step is recorded in Redis trace (append-only RPUSH).
#   - CENTINELA pause is irrevocable — only CPA can release.
# =============================================================================

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .intake_v2 import IntakeAgentV2, MissingCriticalFieldsError
from .clasificador_v2 import ClasificadorAgentV2
from .fiscal_v2 import FiscalAgentV2
from .centinela_guardian import CentinelaGuardian, ClientConfig
from .independent_auditor import IndependentAuditor, AuditRequest
from .core_decisions import AgentDecisionsLog, get_decisions_log

logger = logging.getLogger(__name__)


# =============================================================================
# REDIS QUEUE KEYS
# =============================================================================

Q_NORMAL     = "bc:q:normal"
Q_PAUSES     = "bc:q:pauses"
Q_CPA        = "bc:q:cpa_review"
Q_BACKGROUND = "bc:q:background"
Q_FAILED     = "bc:q:failed"


# =============================================================================
# ENUMS
# =============================================================================

class FlowStatus(str, Enum):
    QUEUED      = "QUEUED"
    PROCESSING  = "PROCESSING"
    CONFIRMED   = "CONFIRMED"
    PAUSED      = "PAUSED"
    CPA_REVIEW  = "CPA_REVIEW"
    FAILED      = "FAILED"
    RETRYING    = "RETRYING"


class StepName(str, Enum):
    INTAKE        = "INTAKE"
    CENTINELA_PRE = "CENTINELA_PRE"
    CLASIFICADOR  = "CLASIFICADOR"
    FISCAL        = "FISCAL"
    AUDITOR       = "AUDITOR"
    CENTINELA_POST= "CENTINELA_POST"
    CONFIRMED     = "CONFIRMED"
    FAILED        = "FAILED"


# =============================================================================
# MODELS — all frozen
# =============================================================================

class TraceStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_id:     str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id:    str
    step_name:   StepName
    agent:       str
    status:      str        # "ok", "error", "paused", "skipped"
    detail:      str        # human-readable summary
    timestamp:   str        # ISO 8601 UTC
    duration_ms: int = 0
    error_msg:   Optional[str] = None


class ProcessingJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    trace_id:      str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_id:     str
    source_format: str        # "pdf", "jpg", "png", etc.
    document_b64:  str        # base64-encoded document bytes
    metadata:      dict = Field(default_factory=dict)
    attempt:       int = 0
    created_at:    str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class QueueStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    normal:     int
    pauses:     int
    cpa_review: int
    background: int
    failed:     int
    confirmed:  int


class EximiaAlert(BaseModel):
    model_config = ConfigDict(frozen=True)

    alert_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id:   str
    client_id:  str
    reason:     str
    attempts:   int
    alerted_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# =============================================================================
# ORCHESTRATOR V2
# =============================================================================

class OrchestratorV2:
    MAX_RETRIES     = 4
    BACKOFF_SECONDS = [2, 4, 8, 16]
    AGENT_NAME      = "ORCHESTRATOR_V2"

    def __init__(
        self,
        redis_client,
        vision_client=None,
        decisions_log: Optional[AgentDecisionsLog] = None,
    ) -> None:
        self._redis         = redis_client
        self._decisions_log = decisions_log if decisions_log is not None else get_decisions_log()

        # Instantiate all agents
        self._intake      = IntakeAgentV2(
            vision_client=vision_client,
            decisions_log=self._decisions_log,
        )
        self._clasificador = ClasificadorAgentV2(decisions_log=self._decisions_log)
        self._fiscal       = FiscalAgentV2(decisions_log=self._decisions_log)
        self._centinela    = CentinelaGuardian()
        self._auditor      = IndependentAuditor()

        self._eximia_alerts: list[EximiaAlert] = []

    # ------------------------------------------------------------------
    # PUBLIC API — submit
    # ------------------------------------------------------------------

    def submit_document(
        self,
        document_bytes: bytes,
        source_format:  str,
        client_id:      str,
        metadata:       Optional[dict] = None,
    ) -> str:
        """Enqueue a document for processing. Returns trace_id."""
        trace_id = str(uuid.uuid4())
        job = ProcessingJob(
            trace_id=trace_id,
            client_id=client_id,
            source_format=source_format,
            document_b64=base64.b64encode(document_bytes).decode(),
            metadata=metadata or {},
        )
        self._redis.rpush(Q_NORMAL, job.model_dump_json())

        # Write initial trace meta
        now_iso = datetime.now(timezone.utc).isoformat()
        self._redis.hset(
            f"bc:trace:{trace_id}:meta",
            mapping={
                "status":     FlowStatus.QUEUED.value,
                "client_id":  client_id,
                "created_at": now_iso,
            },
        )
        return trace_id

    def submit_background_task(
        self,
        task_type: str,
        payload:   dict,
        client_id: str,
    ) -> str:
        """Enqueue a background task (HISTORICAL_REVIEW, CALIBRATION, STATS_REFRESH)."""
        trace_id = str(uuid.uuid4())
        job = ProcessingJob(
            trace_id=trace_id,
            client_id=client_id,
            source_format="json",
            document_b64=base64.b64encode(json.dumps(payload).encode()).decode(),
            metadata={"task_type": task_type},
        )
        self._redis.rpush(Q_BACKGROUND, job.model_dump_json())

        now_iso = datetime.now(timezone.utc).isoformat()
        self._redis.hset(
            f"bc:trace:{trace_id}:meta",
            mapping={
                "status":     FlowStatus.QUEUED.value,
                "client_id":  client_id,
                "created_at": now_iso,
                "task_type":  task_type,
            },
        )
        return trace_id

    # ------------------------------------------------------------------
    # PUBLIC API — consume
    # ------------------------------------------------------------------

    def run_one(self, queue: str = Q_NORMAL) -> Optional[str]:
        """LPOP one job from queue, process it, return trace_id or None."""
        raw = self._redis.lpop(queue)
        if raw is None:
            return None
        job = ProcessingJob.model_validate_json(raw)
        self._process_job(job)
        return job.trace_id

    def run_one_priority(self) -> Optional[str]:
        """Check Q_PAUSES first, then Q_NORMAL."""
        raw = self._redis.lpop(Q_PAUSES)
        if raw is not None:
            job = ProcessingJob.model_validate_json(raw)
            self._process_job(job)
            return job.trace_id

        raw = self._redis.lpop(Q_NORMAL)
        if raw is not None:
            job = ProcessingJob.model_validate_json(raw)
            self._process_job(job)
            return job.trace_id

        return None

    # ------------------------------------------------------------------
    # CORE PIPELINE
    # ------------------------------------------------------------------

    def _process_job(self, job: ProcessingJob) -> FlowStatus:
        """
        Run the full pipeline for a job. NEVER raises — all exceptions caught.
        """
        trace_id = job.trace_id

        # 1. Update trace meta: PROCESSING
        self._redis.hset(f"bc:trace:{trace_id}:meta", "status", FlowStatus.PROCESSING.value)

        # ---- STEP 1: INTAKE ----
        try:
            document_bytes = base64.b64decode(job.document_b64)
            result = self._intake.process_document(
                document_bytes,
                job.source_format,
                raise_on_missing_critical=False,
            )
            if result.critical_fields_missing:
                self._push_to_cpa_review(
                    job,
                    reason=f"INTAKE: campos criticos faltantes: {list(result.missing_fields)}",
                )
                self._record_step(
                    trace_id,
                    StepName.INTAKE,
                    "paused",
                    "Campos criticos faltantes — enviado a revision CPA",
                )
                return FlowStatus.CPA_REVIEW
            self._record_step(
                trace_id,
                StepName.INTAKE,
                "ok",
                f"Extraccion OK. Confianza: {result.confidence}. Vendor: {result.vendor}",
            )
        except Exception as e:
            return self._handle_agent_error(job, StepName.INTAKE, e)

        # ---- STEP 2: CENTINELA PRE ----
        try:
            centinela_decision = self._centinela.evaluate(
                transaction_id=job.trace_id,
                client_id=job.client_id,
                transaction_date=result.date or date.today().isoformat(),
                amount=result.amount or Decimal("0"),
                vendor=result.vendor,
                transaction_type="GASTO",
                confidence=result.confidence,
                current_rule_id=None,
                history_90d=(),
                history_6m=(),
                cross_check_results=(),
                audit_result=None,
            )
            if centinela_decision.decision == "PAUSE":
                self._record_step(
                    trace_id,
                    StepName.CENTINELA_PRE,
                    "paused",
                    f"Pausa emitida: {centinela_decision.reason}",
                )
                self._push_to_pauses(job, centinela_decision)
                return FlowStatus.PAUSED
            self._record_step(
                trace_id,
                StepName.CENTINELA_PRE,
                "ok",
                f"PROCEED. Confianza: {centinela_decision.confidence}",
            )
        except Exception as e:
            return self._handle_agent_error(job, StepName.CENTINELA_PRE, e)

        # ---- STEP 3: CLASIFICADOR ----
        try:
            classification = self._clasificador.classify(result)
            if classification.escalate_to_centinela:
                self._record_step(
                    trace_id,
                    StepName.CLASIFICADOR,
                    "ok",
                    f"UNCLASSIFIED items: {classification.unclassified_count} — escalando CENTINELA",
                )
                self._push_to_cpa_review(
                    job,
                    f"Clasificador: {classification.unclassified_count} items UNCLASSIFIED",
                )
                return FlowStatus.CPA_REVIEW
            self._record_step(
                trace_id,
                StepName.CLASIFICADOR,
                "ok",
                f"Clasificados: {len(classification.classified_items)} items. "
                f"Confianza prom: {classification.average_confidence}",
            )
        except Exception as e:
            return self._handle_agent_error(job, StepName.CLASIFICADOR, e)

        # ---- STEP 4: FISCAL ----
        try:
            txn_date_str = result.date or date.today().isoformat()
            txn_date     = date.fromisoformat(txn_date_str)
            fiscal_result = self._fiscal.compute_fiscal_obligations(classification, txn_date)
            rule_str = (
                ", ".join(fiscal_result.rule_refs[:3])
                if fiscal_result.rule_refs
                else "N/A"
            )
            self._record_step(
                trace_id,
                StepName.FISCAL,
                "ok",
                f"Obligaciones: {len(fiscal_result.tax_liabilities)}. "
                f"Total: {fiscal_result.total_tax_due}. Reglas: {rule_str}",
            )
        except Exception as e:
            return self._handle_agent_error(job, StepName.FISCAL, e)

        # ---- STEP 5: AUDITOR ----
        try:
            journal_entries = self._build_journal_entries(classification, fiscal_result)
            audit_request = AuditRequest(
                transaction_ref=job.trace_id,
                transaction_date=result.date or date.today().isoformat(),
                journal_entries=tuple(journal_entries),
                ivu_base_amount=result.amount,
                ivu_reported_amount=(
                    fiscal_result.total_tax_due
                    if fiscal_result.total_tax_due > 0
                    else None
                ),
                vendor=result.vendor,
            )
            self._auditor.submit(audit_request)
            audit_result = self._auditor.process_next()

            max_severity = self._max_severity(audit_result)

            if max_severity in ("HIGH", "CRITICAL"):
                audit_dict = {
                    "passed": audit_result.passed,
                    "alerts": [
                        {
                            "severity":    a.severity.value,
                            "check_name":  a.check_name,
                            "description": a.description,
                        }
                        for a in audit_result.alerts
                    ],
                }
                centinela_post = self._centinela.evaluate(
                    transaction_id=job.trace_id,
                    client_id=job.client_id,
                    transaction_date=result.date or date.today().isoformat(),
                    amount=result.amount or Decimal("0"),
                    vendor=result.vendor,
                    transaction_type="GASTO",
                    confidence=result.confidence,
                    current_rule_id=None,
                    history_90d=(),
                    history_6m=(),
                    cross_check_results=(),
                    audit_result=audit_dict,
                )
                self._record_step(
                    trace_id,
                    StepName.AUDITOR,
                    "error",
                    f"Auditoria FAILED severity={max_severity}. "
                    f"Alerts: {len(audit_result.alerts)}",
                )
                self._record_step(
                    trace_id,
                    StepName.CENTINELA_POST,
                    "paused",
                    f"Pausa por auditoria: {centinela_post.reason}",
                )
                self._push_to_pauses(job, centinela_post)
                return FlowStatus.PAUSED

            elif max_severity == "MEDIUM":
                self._record_step(
                    trace_id,
                    StepName.AUDITOR,
                    "ok",
                    f"Auditoria MEDIUM — marcado para revision CPA. "
                    f"Alerts: {len(audit_result.alerts)}",
                )
                self._push_to_cpa_review(
                    job,
                    f"AUDITOR MEDIUM: {'; '.join(a.description for a in audit_result.alerts)}",
                )
                return FlowStatus.CPA_REVIEW

            else:  # LOW or passed
                flag = " [FLAG:LOW]" if max_severity == "LOW" else ""
                self._record_step(
                    trace_id,
                    StepName.AUDITOR,
                    "ok",
                    f"Auditoria {'PASSED' if audit_result.passed else 'LOW'}{flag}",
                )
        except Exception as e:
            return self._handle_agent_error(job, StepName.AUDITOR, e)

        # ---- STEP 6: CONFIRMED ----
        self._record_step(
            trace_id,
            StepName.CONFIRMED,
            "ok",
            "Transaccion confirmada y registrada",
        )
        self._redis.rpush("bc:confirmed", trace_id)
        self._redis.hset(
            f"bc:trace:{trace_id}:meta",
            mapping={
                "status":       FlowStatus.CONFIRMED.value,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return FlowStatus.CONFIRMED

    # ------------------------------------------------------------------
    # ERROR HANDLING
    # ------------------------------------------------------------------

    def _handle_agent_error(
        self,
        job: ProcessingJob,
        step_name: StepName,
        exception: Exception,
    ) -> FlowStatus:
        """Record error, retry if attempts < MAX_RETRIES, else FAILED."""
        trace_id  = job.trace_id
        error_msg = f"{type(exception).__name__}: {exception}"

        self._record_step(
            trace_id,
            step_name,
            "error",
            f"Error en agente {step_name.value}: {error_msg}",
            error_msg=error_msg,
        )

        if job.attempt < self.MAX_RETRIES:
            # Create new job with incremented attempt
            retry_metadata = dict(job.metadata)
            retry_metadata["last_error"]       = error_msg
            retry_metadata["last_failed_step"] = step_name.value
            retry_metadata["retry_delay_s"]    = self.BACKOFF_SECONDS[
                min(job.attempt, len(self.BACKOFF_SECONDS) - 1)
            ]

            # Build updated job — ProcessingJob is frozen, so we reconstruct
            retry_job = ProcessingJob(
                trace_id=job.trace_id,
                client_id=job.client_id,
                source_format=job.source_format,
                document_b64=job.document_b64,
                metadata=retry_metadata,
                attempt=job.attempt + 1,
                created_at=job.created_at,
            )
            self._redis.rpush(Q_NORMAL, retry_job.model_dump_json())
            self._record_step(
                trace_id,
                step_name,
                "ok",
                f"RETRYING: intento {retry_job.attempt}/{self.MAX_RETRIES}",
            )
            self._redis.hset(
                f"bc:trace:{trace_id}:meta",
                "status",
                FlowStatus.RETRYING.value,
            )
            return FlowStatus.RETRYING

        # Exhausted retries → FAILED
        self._redis.rpush(Q_FAILED, job.model_dump_json())

        alert = EximiaAlert(
            trace_id=job.trace_id,
            client_id=job.client_id,
            reason=f"Agente {step_name.value} fallo {job.attempt + 1} veces: {error_msg}",
            attempts=job.attempt + 1,
        )
        self._eximia_alerts.append(alert)

        self._record_step(
            trace_id,
            StepName.FAILED,
            "error",
            f"FAILED after {job.attempt + 1} attempts. Last error: {error_msg}",
            error_msg=error_msg,
        )
        self._redis.hset(
            f"bc:trace:{trace_id}:meta",
            "status",
            FlowStatus.FAILED.value,
        )
        return FlowStatus.FAILED

    # ------------------------------------------------------------------
    # TRACE HELPERS
    # ------------------------------------------------------------------

    def _record_step(
        self,
        trace_id:    str,
        step_name:   StepName,
        status:      str,
        detail:      str,
        error_msg:   Optional[str] = None,
        duration_ms: int = 0,
    ) -> None:
        """Create a TraceStep and RPUSH to Redis."""
        step = TraceStep(
            trace_id=trace_id,
            step_name=step_name,
            agent=self.AGENT_NAME,
            status=status,
            detail=detail,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
            error_msg=error_msg,
        )
        self._redis.rpush(
            f"bc:trace:{trace_id}:steps",
            step.model_dump_json(),
        )

    def _push_to_cpa_review(self, job: ProcessingJob, reason: str) -> None:
        """Update metadata with reason and push to CPA review queue."""
        updated_meta = dict(job.metadata)
        updated_meta["cpa_review_reason"] = reason

        cpa_job = ProcessingJob(
            trace_id=job.trace_id,
            client_id=job.client_id,
            source_format=job.source_format,
            document_b64=job.document_b64,
            metadata=updated_meta,
            attempt=job.attempt,
            created_at=job.created_at,
        )
        self._redis.rpush(Q_CPA, cpa_job.model_dump_json())
        self._redis.hset(
            f"bc:trace:{job.trace_id}:meta",
            "status",
            FlowStatus.CPA_REVIEW.value,
        )

    def _push_to_pauses(self, job: ProcessingJob, centinela_decision) -> None:
        """Push job to pauses queue and mark trace as PAUSED."""
        self._redis.rpush(Q_PAUSES, job.model_dump_json())
        self._redis.hset(
            f"bc:trace:{job.trace_id}:meta",
            "status",
            FlowStatus.PAUSED.value,
        )

    # ------------------------------------------------------------------
    # TRACE QUERY
    # ------------------------------------------------------------------

    def get_trace(self, trace_id: str) -> dict:
        """Return full trace: steps list and meta dict."""
        raw_steps = self._redis.lrange(f"bc:trace:{trace_id}:steps", 0, -1)
        steps = []
        for raw in raw_steps:
            if isinstance(raw, bytes):
                raw = raw.decode()
            steps.append(json.loads(raw))

        raw_meta = self._redis.hgetall(f"bc:trace:{trace_id}:meta")
        meta = {}
        for k, v in raw_meta.items():
            if isinstance(k, bytes):
                k = k.decode()
            if isinstance(v, bytes):
                v = v.decode()
            meta[k] = v

        return {"steps": steps, "meta": meta}

    # ------------------------------------------------------------------
    # QUEUE STATS
    # ------------------------------------------------------------------

    def queue_stats(self) -> QueueStats:
        """Return current lengths of all queues."""
        return QueueStats(
            normal=self._redis.llen(Q_NORMAL),
            pauses=self._redis.llen(Q_PAUSES),
            cpa_review=self._redis.llen(Q_CPA),
            background=self._redis.llen(Q_BACKGROUND),
            failed=self._redis.llen(Q_FAILED),
            confirmed=self._redis.llen("bc:confirmed"),
        )

    # ------------------------------------------------------------------
    # EXIMIA ALERTS
    # ------------------------------------------------------------------

    def get_eximia_alerts(self) -> tuple[EximiaAlert, ...]:
        """Return immutable tuple of all Eximia alerts."""
        return tuple(self._eximia_alerts)

    # ------------------------------------------------------------------
    # PRIVATE HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _build_journal_entries(classification, fiscal_result) -> list[dict]:
        """
        Build double-entry journal entries from classification and fiscal result.
        For each classified item: debit (expense account) + credit (AP 2000).
        """
        entries: list[dict] = []
        for item in classification.classified_items:
            amt_str = str(item.amount)
            # Debit: expense/asset account
            entries.append({
                "account_code": item.account_code,
                "entry_type":   "debit",
                "amount":       amt_str,
                "description":  item.original_description,
            })
            # Credit: Accounts Payable (2000)
            entries.append({
                "account_code": "2000",
                "entry_type":   "credit",
                "amount":       amt_str,
                "description":  f"AP — {item.original_description}",
            })
        return entries

    @staticmethod
    def _max_severity(audit_result) -> str:
        """Return max severity string from audit_result alerts."""
        if audit_result is None:
            return "PASSED"
        if audit_result.passed and not audit_result.alerts:
            return "PASSED"

        severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        max_level = 0
        max_name  = "PASSED"

        for alert in audit_result.alerts:
            level = severity_order.get(alert.severity.value, 0)
            if level > max_level:
                max_level = level
                max_name  = alert.severity.value

        return max_name if max_level > 0 else "PASSED"
