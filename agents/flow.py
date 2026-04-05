# =============================================================================
# agents/flow.py
# Coordinador de flujo del sistema Bit-Counting Puerto Rico.
#
# PIPELINE:
#   INTAKE → CENTINELA → CLASIFICADOR → AUDITOR → FISCAL PR → ORQUESTADOR
#
# DISENO:
#   - process_document(): funcion de nivel superior para uso rapido.
#   - FlowCoordinator: clase con estado propio (Orchestrator, Centinela, agentes).
#   - FlowResult: modelo Pydantic frozen con todo el output del pipeline.
#   - Si CENTINELA emite PAUSE, retorna inmediatamente con final_decision="PAUSED".
#   - Si AUDITOR no verifica, marca final_decision="FAILED" (sin pasar a FISCAL PR).
#   - Cada paso se registra en el Orchestrator via log_decision().
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from agents import Orchestrator, Centinela, Intake
from agents.clasificador import ClasificadorAgent
from agents.auditor import AuditorAgent
from agents.fiscal_pr import FiscalPRAgent
from agents.messages import (
    AuditorVerification,
    CentinelaDecision,
    CentinelaEvaluation,
    ClassifierOutput,
    FiscalOutput,
    IntakeOutput,
    OrchestratorDecision,
)


# ---------------------------------------------------------------------------
# FlowResult — output inmutable del pipeline completo
# ---------------------------------------------------------------------------

class FlowResult(BaseModel):
    """
    Resultado completo de un ciclo del pipeline de procesamiento.

    Todos los campos son inmutables una vez creados (frozen=True).
    Los campos opcionales son None cuando el paso no fue ejecutado
    (por ejemplo, classifier_output=None si CENTINELA emitio PAUSE).
    """

    model_config = ConfigDict(frozen=True)

    intake_output: IntakeOutput
    centinela_evaluation: CentinelaEvaluation
    classifier_output: Optional[ClassifierOutput]
    auditor_verification: Optional[AuditorVerification]
    fiscal_output: Optional[FiscalOutput]
    final_decision: str                          # "COMPLETED", "PAUSED", "FAILED"
    pause_id: Optional[str]
    log: tuple[OrchestratorDecision, ...]
    total_confidence: Decimal


# ---------------------------------------------------------------------------
# FlowCoordinator — clase principal con estado propio por instancia
# ---------------------------------------------------------------------------

class FlowCoordinator:
    """
    Coordinador del flujo completo de procesamiento de documentos.

    Cada instancia crea su propio Orchestrator, Centinela y agentes.
    Esto garantiza que los logs y pausas de un flujo no interfieran
    con los de otro flujo paralelo.

    Uso tipico:
        coordinator = FlowCoordinator()
        result = coordinator.process(raw_input, source_format="json")
        log = coordinator.get_log()
    """

    def __init__(self) -> None:
        self._orchestrator = Orchestrator()
        self._centinela = Centinela()
        self._intake = Intake()
        self._clasificador = ClasificadorAgent()
        self._auditor = AuditorAgent()
        self._fiscal = FiscalPRAgent()

    def process(
        self,
        raw_input: dict,
        source_format: str = "manual",
    ) -> FlowResult:
        """
        Ejecuta el pipeline completo sobre un documento crudo.

        Args:
            raw_input:     Dict con los campos del documento (vendor, amount, etc.).
            source_format: Formato del documento fuente ("manual", "json", "pdf", etc.).

        Returns:
            FlowResult con todos los outputs del pipeline y el log de decisiones.
        """
        return _run_pipeline(
            raw_input=raw_input,
            source_format=source_format,
            orchestrator=self._orchestrator,
            centinela=self._centinela,
            intake=self._intake,
            clasificador=self._clasificador,
            auditor=self._auditor,
            fiscal=self._fiscal,
        )

    def get_log(self) -> tuple[OrchestratorDecision, ...]:
        """
        Retorna el log inmutable de decisiones del Orchestrator de esta instancia.
        """
        return self._orchestrator.get_decision_log()


# ---------------------------------------------------------------------------
# process_document() — funcion de nivel superior (convenience wrapper)
# ---------------------------------------------------------------------------

def process_document(
    raw_input: dict,
    source_format: str = "manual",
) -> FlowResult:
    """
    Procesa un documento a traves del pipeline completo.

    Crea una instancia fresca de FlowCoordinator por cada llamada,
    garantizando aislamiento completo entre invocaciones.

    Args:
        raw_input:     Dict con los campos del documento.
        source_format: Formato del documento fuente.

    Returns:
        FlowResult con el resultado completo del pipeline.
    """
    coordinator = FlowCoordinator()
    return coordinator.process(raw_input, source_format)


# ---------------------------------------------------------------------------
# _run_pipeline() — implementacion interna del pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    raw_input: dict,
    source_format: str,
    orchestrator: Orchestrator,
    centinela: Centinela,
    intake: Intake,
    clasificador: ClasificadorAgent,
    auditor: AuditorAgent,
    fiscal: FiscalPRAgent,
) -> FlowResult:
    """
    Implementacion del pipeline. Separada para permitir inyeccion de
    dependencias tanto desde FlowCoordinator como desde process_document().
    """

    # -------------------------------------------------------------------------
    # PASO 1: INTAKE — extrae campos del documento crudo
    # -------------------------------------------------------------------------
    intake_output: IntakeOutput = intake.process_document(
        raw_input=raw_input,
        source_format=source_format,
        orchestrator=orchestrator,
    )

    # -------------------------------------------------------------------------
    # PASO 2: CENTINELA — evalua riesgo y decide PROCEED o PAUSE
    # -------------------------------------------------------------------------
    centinela_eval: CentinelaEvaluation = centinela.evaluate(
        intake_output=intake_output,
        client_history=(),
        active_rules=(),
    )

    orchestrator.log_decision(
        agent_name="CENTINELA",
        input_msg=intake_output,
        output_msg=centinela_eval,
        rule_ids=centinela_eval.rule_conflicts,
        confidence=centinela_eval.confidence_at_evaluation,
        requires_cpa_review=(centinela_eval.decision == CentinelaDecision.PAUSE),
    )

    if centinela_eval.decision == CentinelaDecision.PAUSE:
        return FlowResult(
            intake_output=intake_output,
            centinela_evaluation=centinela_eval,
            classifier_output=None,
            auditor_verification=None,
            fiscal_output=None,
            final_decision="PAUSED",
            pause_id=centinela_eval.pause_id,
            log=orchestrator.get_decision_log(),
            total_confidence=centinela_eval.confidence_at_evaluation,
        )

    # -------------------------------------------------------------------------
    # PASO 3: CLASIFICADOR — asigna cuentas contables (partida doble)
    # -------------------------------------------------------------------------
    debit_entry, credit_entry = clasificador.classify(intake_output)

    orchestrator.log_decision(
        agent_name="CLASIFICADOR",
        input_msg=intake_output,
        output_msg=debit_entry,
        rule_ids=(debit_entry.rule_ref,),
        confidence=debit_entry.confidence,
    )

    # -------------------------------------------------------------------------
    # PASO 4: AUDITOR — verificacion cruzada independiente
    # -------------------------------------------------------------------------
    auditor_verification: AuditorVerification = auditor.verify(
        debit_entry=debit_entry,
        credit_entry=credit_entry,
        intake_output=intake_output,
    )

    orchestrator.log_decision(
        agent_name="AUDITOR",
        input_msg=debit_entry,
        output_msg=auditor_verification,
        rule_ids=(auditor_verification.independent_rule_ref,),
        confidence=Decimal("1.00") if auditor_verification.verified else Decimal("0.00"),
        requires_cpa_review=not auditor_verification.verified or bool(auditor_verification.fraud_flags),
    )

    if not auditor_verification.verified:
        return FlowResult(
            intake_output=intake_output,
            centinela_evaluation=centinela_eval,
            classifier_output=debit_entry,
            auditor_verification=auditor_verification,
            fiscal_output=None,
            final_decision="FAILED",
            pause_id=None,
            log=orchestrator.get_decision_log(),
            total_confidence=_average_confidence(
                centinela_eval.confidence_at_evaluation,
                debit_entry.confidence,
                Decimal("0.00"),
            ),
        )

    # -------------------------------------------------------------------------
    # PASO 5: FISCAL PR — calcula obligaciones tributarias
    # -------------------------------------------------------------------------
    fiscal_output: FiscalOutput = fiscal._process_impl(intake_output)

    orchestrator.log_decision(
        agent_name="FISCAL_PR",
        input_msg=intake_output,
        output_msg=fiscal_output,
        rule_ids=(fiscal_output.rule_ref,),
        confidence=Decimal("0.95"),
    )

    total_confidence = _average_confidence(
        centinela_eval.confidence_at_evaluation,
        debit_entry.confidence,
        Decimal("0.95"),
    )

    return FlowResult(
        intake_output=intake_output,
        centinela_evaluation=centinela_eval,
        classifier_output=debit_entry,
        auditor_verification=auditor_verification,
        fiscal_output=fiscal_output,
        final_decision="COMPLETED",
        pause_id=None,
        log=orchestrator.get_decision_log(),
        total_confidence=total_confidence,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _average_confidence(*values: Decimal) -> Decimal:
    """Promedio de varios scores de confianza. Redondea a 4 decimales."""
    if not values:
        return Decimal("0.00")
    total = sum(values, Decimal("0"))
    return (total / Decimal(len(values))).quantize(Decimal("0.0001"))


# ===========================================================================
# BitCountingFlow — Coordinador del flujo completo (API del spec)
# ===========================================================================

class BitCountingFlow:
    """
    Coordinador del flujo completo de procesamiento de documentos Bit-Counting.

    Orquesta los agentes en el pipeline:
      INTAKE → CENTINELA → CLASIFICADOR → AUDITOR → FISCAL_PR → ORQUESTADOR

    Retorna dict estructurado con:
      - status: "PROCESSED" | "PAUSED"
      - journal_entries: lista de asientos contables (debit + credit)
      - tax_liability: output fiscal calculado
      - pause_info: None si PROCESSED, dict con detalles si PAUSED
      - decision_log: lista de decisiones del ORQUESTADOR
    """

    def __init__(self) -> None:
        self._orchestrator = Orchestrator()
        self._centinela = Centinela()
        from agents.intake import IntakeAgent
        self._intake = IntakeAgent()
        self._clasificador = ClasificadorAgent()
        self._auditor = AuditorAgent()
        self._fiscal = FiscalPRAgent()

        # Estado interno del flujo
        self._active_pauses: list[str] = []
        self._decisions_logged: int = 0
        self._last_processed: Optional[str] = None

    def process_document(
        self,
        raw_input: dict,
        source_format: str,
        client_context: dict,
    ) -> dict:
        """
        Ejecuta el flujo completo de procesamiento de un documento.

        Pasos:
          1. INTAKE extrae campos del documento crudo.
          2. CENTINELA evalua — si PAUSE, retorna inmediatamente con pause_info.
          3. CLASIFICADOR asigna cuentas contables (partida doble).
          4. AUDITOR verifica independientemente.
          5. Si AUDITOR falla → CENTINELA pausa.
          6. FISCAL_PR calcula obligacion tributaria.
          7. ORQUESTADOR registra todas las decisiones.

        Args:
            raw_input:       Dict con campos del documento (vendor, amount, date, etc.).
            source_format:   Formato del documento ("json", "pdf", "manual", etc.).
            client_context:  Contexto del cliente (historial, industry, policies, etc.).

        Returns:
            Dict con: status, journal_entries, tax_liability, pause_info, decision_log.
        """
        from agents.messages import CentinelaDecision, EntryType

        # --- Paso 1: INTAKE ---
        intake_output: IntakeOutput = self._intake.process_document(
            raw_input=raw_input,
            source_format=source_format,
            orchestrator=self._orchestrator,
        )

        # --- Paso 2: CENTINELA ---
        client_history = tuple(client_context.get("history", []))
        active_rules   = tuple(client_context.get("active_rules", []))

        centinela_eval: CentinelaEvaluation = self._centinela.evaluate(
            intake_output=intake_output,
            client_history=client_history,
            active_rules=active_rules,
        )

        self._orchestrator.log_decision(
            agent_name="CENTINELA",
            input_msg=intake_output,
            output_msg=centinela_eval,
            rule_ids=centinela_eval.rule_conflicts,
            confidence=centinela_eval.confidence_at_evaluation,
            requires_cpa_review=(centinela_eval.decision == CentinelaDecision.PAUSE),
        )
        self._decisions_logged += 1

        if centinela_eval.decision == CentinelaDecision.PAUSE:
            if centinela_eval.pause_id:
                self._active_pauses.append(centinela_eval.pause_id)
            decision_log = [
                d.model_dump() for d in self._orchestrator.get_decision_log()
            ]
            return {
                "status":          "PAUSED",
                "journal_entries": [],
                "tax_liability":   None,
                "pause_info": {
                    "pause_id":    centinela_eval.pause_id,
                    "reason":      centinela_eval.reason,
                    "trigger":     "CENTINELA_EVALUATE",
                    "intake_id":   intake_output.message_id,
                },
                "decision_log": decision_log,
            }

        # --- Paso 3: CLASIFICADOR ---
        debit_entry, credit_entry = self._clasificador.classify(
            intake_output=intake_output,
            client_context=client_context or None,
        )

        self._orchestrator.log_decision(
            agent_name="CLASIFICADOR",
            input_msg=intake_output,
            output_msg=debit_entry,
            rule_ids=(debit_entry.rule_ref,),
            confidence=debit_entry.confidence,
        )
        self._decisions_logged += 1

        # --- Paso 4: AUDITOR ---
        auditor_verification: AuditorVerification = self._auditor.verify(
            debit_entry=debit_entry,
            credit_entry=credit_entry,
            intake_output=intake_output,
        )

        self._orchestrator.log_decision(
            agent_name="AUDITOR",
            input_msg=debit_entry,
            output_msg=auditor_verification,
            rule_ids=(auditor_verification.independent_rule_ref,),
            confidence=Decimal("1.00") if auditor_verification.verified else Decimal("0.00"),
            requires_cpa_review=not auditor_verification.verified,
        )
        self._decisions_logged += 1

        # --- Paso 5: Si AUDITOR falla → CENTINELA pausa ---
        if not auditor_verification.verified:
            pause_eval: CentinelaEvaluation = self._centinela.evaluate(
                intake_output=intake_output,
                client_history=client_history,
                active_rules=active_rules,
            )
            if pause_eval.pause_id:
                self._active_pauses.append(pause_eval.pause_id)
            decision_log = [
                d.model_dump() for d in self._orchestrator.get_decision_log()
            ]
            return {
                "status":          "PAUSED",
                "journal_entries": [],
                "tax_liability":   None,
                "pause_info": {
                    "pause_id":      pause_eval.pause_id,
                    "reason":        f"AUDITOR verification failed: {list(auditor_verification.discrepancies)}",
                    "trigger":       "AUDITOR_VERIFICATION_FAILED",
                    "discrepancies": list(auditor_verification.discrepancies),
                    "fraud_flags":   list(auditor_verification.fraud_flags),
                },
                "decision_log": decision_log,
            }

        # --- Paso 6: FISCAL_PR ---
        fiscal_output: FiscalOutput = self._fiscal._process_impl(intake_output)

        self._orchestrator.log_decision(
            agent_name="FISCAL_PR",
            input_msg=intake_output,
            output_msg=fiscal_output,
            rule_ids=(fiscal_output.rule_ref,),
            confidence=Decimal("0.95"),
        )
        self._decisions_logged += 1

        # --- Paso 7: ORQUESTADOR log ---
        decision_log = [
            d.model_dump() for d in self._orchestrator.get_decision_log()
        ]

        self._last_processed = intake_output.message_id

        # Build journal entries
        journal_entries = [
            {
                "entry_type":   debit_entry.entry_type.value,
                "account_code": debit_entry.account_code,
                "account_name": debit_entry.account_name,
                "amount":       str(debit_entry.amount),
                "rule_ref":     debit_entry.rule_ref,
            },
            {
                "entry_type":   credit_entry.entry_type.value,
                "account_code": credit_entry.account_code,
                "account_name": credit_entry.account_name,
                "amount":       str(credit_entry.amount),
                "rule_ref":     credit_entry.rule_ref,
            },
        ]

        tax_liability = {
            "tax_type":       fiscal_output.tax_type,
            "tax_liability":  str(fiscal_output.tax_liability),
            "form_id":        fiscal_output.form_id,
            "rule_ref":       fiscal_output.rule_ref,
            "taxable_base":   str(fiscal_output.taxable_base),
        }

        return {
            "status":          "PROCESSED",
            "journal_entries": journal_entries,
            "tax_liability":   tax_liability,
            "pause_info":      None,
            "decision_log":    decision_log,
        }

    def get_flow_status(self) -> dict:
        """
        Retorna el estado actual del flujo.

        Returns:
            Dict con: active_pauses, decisions_logged, last_processed.
        """
        return {
            "active_pauses":    list(self._active_pauses),
            "decisions_logged": self._decisions_logged,
            "last_processed":   self._last_processed,
        }
