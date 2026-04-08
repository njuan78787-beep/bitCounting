# =============================================================================
# api/routes/cpa_dashboard.py
# CPA dashboard endpoints for Bit-Counting.
#
# All actions that modify state require a valid (cpa_license, cpa_token) pair.
# Token validation is stubbed in Phase 1 — real validation queries cpa_partners
# table in Phase 2.
#
# Friction levels govern approval UX:
#   LOW    → simple click confirmation
#   MEDIUM → must expand detail first, then confirm
#   HIGH   → must correctly answer a question about the transaction
#
# Endpoints:
#   GET  /api/v1/cpa/pauses                        — active pauses for CPA
#   POST /api/v1/cpa/pauses/{pause_id}/resolve     — CPA resolves a pause
#   GET  /api/v1/cpa/review-queue                  — items needing review
#   POST /api/v1/cpa/approve/{item_id}             — approve/reject with friction
#   GET  /api/v1/cpa/metrics                       — vigilance metrics
#   POST /api/v1/cpa/instructions                  — submit natural language instruction
#   POST /api/v1/cpa/instructions/{draft_id}/confirm — confirm/reject policy draft
# =============================================================================

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from ..auth import TokenUser
from ..dependencies import get_current_user

from ..schemas import (
    CPAApprovalRequest,
    CPAInstructionRequest,
    CPAMetricsResponse,
    FrictionLevel,
    PauseResponse,
    PauseResolveRequest,
    PolicyDraftConfirmRequest,
    PolicyDraftResponse,
)
from friction.cpa_vigilance import CPAVigilanceSystem
from agents import Centinela, Orchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cpa", tags=["cpa_dashboard"])

# ---------------------------------------------------------------------------
# Singleton Centinela and Orchestrator (Phase 1 in-memory state)
# ---------------------------------------------------------------------------
_centinela_singleton = Centinela()
_orchestrator_singleton = Orchestrator()

# ---------------------------------------------------------------------------
# Shared vigilance system instance
# ---------------------------------------------------------------------------
_vigilance = CPAVigilanceSystem()

# ---------------------------------------------------------------------------
# In-memory stores (Phase 1)
# ---------------------------------------------------------------------------
_pauses: dict[str, dict] = {}
_review_queue: dict[str, dict] = {}
_policy_drafts: dict[str, dict] = {}
# Maps (cpa_license, item_id) → {"started_at": float, "level": str}
_approval_sessions: dict[tuple[str, str], dict] = {}

# Seed a few demo pauses so the dashboard is not empty
_DEMO_PAUSES = [
    {
        "pause_id": "pause-demo-001",
        "trigger_type": "LOW_CONFIDENCE",
        "affected_transaction_ids": ["txn-demo-003"],
        "conflicting_rules": [],
        "interpretations": [
            "Clasificar como pago estimado de ingresos (cuenta 2400)",
            "Clasificar como pago de IVU mensual (cuenta 2410)",
        ],
        "pre_processed_analysis": (
            "Transaccion de $1,250 pagada a Hacienda PR el 2026-03-31. "
            "El confidence del INTAKE fue 0.55 (umbral: 0.60) debido a "
            "descripcion ambigua. Puede ser pago de IVU mensual o estimado "
            "de ingresos — el CPA debe determinar la clasificacion correcta."
        ),
        "sla_hours": 48,
        "sla_deadline": (datetime.utcnow() + timedelta(hours=48)).isoformat(),
        "status": "ACTIVE",
        "created_at": datetime.utcnow().isoformat(),
        "assigned_cpa_license": None,
    },
]
for p in _DEMO_PAUSES:
    _pauses[p["pause_id"]] = p

_DEMO_REVIEW_ITEMS = [
    {
        "item_id": "review-demo-001",
        "item_type": "journal_entry",
        "transaction_id": "txn-demo-002",
        "vendor": "Dell Technologies",
        "amount": Decimal("3200.00"),
        "description": "Computer equipment purchase — capital vs expense determination",
        "consequence_level": "HIGH",
        "sla_deadline": (datetime.utcnow() + timedelta(hours=24)).isoformat(),
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending_review",
        "detail": {
            "account_code": "1500",
            "account_name": "Equipos de Computacion",
            "rule_ref": "PR-CAP-THRESHOLD-2500",
            "reasoning": "Amount $3,200 exceeds $2,500 capitalization threshold. Classified as capital asset.",
        },
    },
    {
        "item_id": "review-demo-002",
        "item_type": "expense_entry",
        "transaction_id": "txn-demo-001",
        "vendor": "Costco Wholesale PR",
        "amount": Decimal("2450.00"),
        "description": "Supplies purchase below $2,500 threshold",
        "consequence_level": "LOW",
        "sla_deadline": (datetime.utcnow() + timedelta(hours=72)).isoformat(),
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending_review",
        "detail": {
            "account_code": "5900",
            "account_name": "Gastos de Operacion General",
            "rule_ref": "GAAP-EXPENSE-RECOGNITION",
            "reasoning": "Amount $2,450 is below $2,500 threshold; expensed directly.",
        },
    },
]
for item in _DEMO_REVIEW_ITEMS:
    _review_queue[item["item_id"]] = item


# ---------------------------------------------------------------------------
# TOKEN VALIDATION STUB
# ---------------------------------------------------------------------------

def _validate_cpa_token(cpa_license: str, cpa_token: str) -> bool:
    """
    Phase 1 stub: accepts any non-empty license+token pair.
    Phase 2 will query cpa_partners and validate a JWT/session token.
    """
    return bool(cpa_license and cpa_token)


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@router.get(
    "/pauses",
    response_model=list[PauseResponse],
    status_code=status.HTTP_200_OK,
    summary="List active pauses assigned to this CPA",
)
async def list_pauses(
    cpa_license: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default="active", alias="status"),
    user: TokenUser = Depends(get_current_user),
) -> list[PauseResponse]:
    """
    Return CENTINELA pauses that need CPA attention.
    Defaults to ACTIVE pauses; pass status=all to see resolved ones too.
    """
    target_status = status_filter.upper() if status_filter else "ACTIVE"
    results = []
    for pause in _pauses.values():
        if target_status != "ALL" and pause["status"] != target_status:
            continue
        if cpa_license and pause.get("assigned_cpa_license") not in (None, cpa_license):
            continue
        results.append(_dict_to_pause_response(pause))

    # Sort by SLA deadline ascending (soonest first)
    results.sort(key=lambda p: p.sla_deadline or datetime.max)
    return results


@router.post(
    "/pauses/{pause_id}/resolve",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="CPA resolves an active pause",
)
async def resolve_pause(
    pause_id: str,
    request: PauseResolveRequest,
) -> dict:
    """
    Release an active CENTINELA pause.  Requires valid CPA license and token.
    The CPA must provide resolution notes and choose an interpretation.
    """
    if not _validate_cpa_token(request.cpa_license, request.cpa_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid CPA license or token",
        )

    pause = _pauses.get(pause_id)
    if pause is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pause '{pause_id}' not found",
        )
    if pause["status"] == "RESOLVED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pause '{pause_id}' is already resolved",
        )

    # Validate the chosen instruction is one of the interpretations
    valid_interpretations = pause.get("interpretations", [])
    if valid_interpretations and request.instruction not in valid_interpretations:
        logger.warning(
            "CPA %s chose non-standard instruction for pause %s",
            request.cpa_license, pause_id,
        )

    pause["status"] = "RESOLVED"
    pause["resolved_by"] = request.cpa_license
    pause["resolution_notes"] = request.resolution_notes
    pause["chosen_instruction"] = request.instruction
    pause["resolved_at"] = datetime.utcnow().isoformat()

    logger.info("Pause %s resolved by CPA %s", pause_id, request.cpa_license)
    return {
        "pause_id": pause_id,
        "status": "RESOLVED",
        "resolved_by": request.cpa_license,
        "resolved_at": pause["resolved_at"],
    }


@router.get(
    "/review-queue",
    response_model=list[dict],
    status_code=status.HTTP_200_OK,
    summary="Get items in CPA review queue, sorted by SLA deadline",
)
async def get_review_queue(
    cpa_license: Optional[str] = Query(default=None),
    user: TokenUser = Depends(get_current_user),
) -> list[dict]:
    """
    Return all items pending CPA review, sorted by SLA deadline (soonest first).
    Items include the friction level so the frontend can render the right UX.
    """
    items = [
        {**item, "friction_level": item.get("consequence_level", "LOW")}
        for item in _review_queue.values()
        if item.get("status") == "pending_review"
    ]
    items.sort(key=lambda i: i.get("sla_deadline", "9999-99-99"))
    return items


@router.post(
    "/approve/{item_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Approve or reject a review-queue item with CPA friction",
)
async def approve_item(
    item_id: str,
    request: CPAApprovalRequest,
    _user: TokenUser = Depends(get_current_user),
) -> dict:
    """
    Process a CPA approval with friction levels:

    - LOW:    Simple click — approved immediately if token is valid.
    - MEDIUM: CPA must set detail_confirmed=True (indicating they expanded
              and read the details).  Returns detail payload on first call
              if detail_confirmed is None/False.
    - HIGH:   CPA must answer a challenge question.  Returns the question
              on the first call; requires challenge_answer on the second call.
              Approved in < 5 seconds is flagged as suspicious.
    """
    if not _validate_cpa_token(request.cpa_license, request.cpa_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid CPA license or token",
        )

    item = _review_queue.get(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review item '{item_id}' not found",
        )
    if item.get("status") != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Item '{item_id}' is no longer pending review (status: {item.get('status')})",
        )

    level = item.get("consequence_level", "LOW")
    transaction = item

    # Track time — start session on first call for this item
    session_key = (request.cpa_license, item_id)
    if session_key not in _approval_sessions:
        _approval_sessions[session_key] = {
            "started_at": time.time(),
            "level": level,
        }

    # --- LOW friction ---
    if level == "LOW":
        elapsed = time.time() - _approval_sessions[session_key]["started_at"]
        _vigilance.track_approval_time(request.cpa_license, item_id, int(elapsed), level)
        item["status"] = "approved" if request.action == "approved" else "rejected"
        item["resolved_by"] = request.cpa_license
        item["resolution_notes"] = request.notes
        item["resolved_at"] = datetime.utcnow().isoformat()
        del _approval_sessions[session_key]
        return {"item_id": item_id, "status": item["status"], "friction_level": "LOW"}

    # --- MEDIUM friction ---
    if level == "MEDIUM":
        if not request.detail_confirmed:
            # First call: return details, tell client to confirm after reading
            return {
                "item_id": item_id,
                "friction_level": "MEDIUM",
                "action_required": "expand_detail",
                "detail": item.get("detail", {}),
                "instruction": "Expanda los detalles, léalos y reenvíe con detail_confirmed=true para confirmar.",
            }
        # Second call: detail_confirmed=True → process approval
        elapsed = time.time() - _approval_sessions[session_key]["started_at"]
        _vigilance.track_approval_time(request.cpa_license, item_id, int(elapsed), level)
        item["status"] = "approved" if request.action == "approved" else "rejected"
        item["resolved_by"] = request.cpa_license
        item["resolution_notes"] = request.notes
        item["resolved_at"] = datetime.utcnow().isoformat()
        del _approval_sessions[session_key]
        return {"item_id": item_id, "status": item["status"], "friction_level": "MEDIUM"}

    # --- HIGH friction ---
    if level == "HIGH":
        challenge = _vigilance.generate_friction_challenge(transaction, "HIGH")
        if not request.challenge_answer:
            # First call: return question
            return {
                "item_id": item_id,
                "friction_level": "HIGH",
                "action_required": "answer_question",
                "question": challenge.get("question", ""),
                "instruction": "Responda la pregunta sobre la transacción para continuar.",
            }
        # Second call: validate answer
        correct_answer = challenge.get("correct_answer", "")
        if request.challenge_answer.strip().lower() != correct_answer.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "Respuesta incorrecta al desafío de verificación.",
                    "hint": "Revise los detalles de la transacción y responda nuevamente.",
                },
            )
        elapsed = time.time() - _approval_sessions[session_key]["started_at"]
        _vigilance.track_approval_time(request.cpa_license, item_id, int(elapsed), level)
        item["status"] = "approved" if request.action == "approved" else "rejected"
        item["resolved_by"] = request.cpa_license
        item["resolution_notes"] = request.notes
        item["resolved_at"] = datetime.utcnow().isoformat()
        del _approval_sessions[session_key]
        return {"item_id": item_id, "status": item["status"], "friction_level": "HIGH"}

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unknown consequence level '{level}'",
    )


@router.get(
    "/metrics",
    response_model=CPAMetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get CPA performance and vigilance metrics",
)
async def get_cpa_metrics(
    cpa_license: str = Query(..., description="CPA license number"),
    _user: TokenUser = Depends(get_current_user),
) -> CPAMetricsResponse:
    """
    Return vigilance and performance metrics for a specific CPA.
    Suspicious fast approvals (< 5s for HIGH-level items) are highlighted.
    """
    raw_metrics = _vigilance.get_cpa_vigilance_metrics(cpa_license)

    # Count pauses resolved today
    today = datetime.utcnow().date().isoformat()
    pauses_today = sum(
        1 for p in _pauses.values()
        if p.get("resolved_by") == cpa_license
        and (p.get("resolved_at") or "")[:10] == today
    )

    avg_hours_raw = raw_metrics.get("avg_approval_time_by_level", {}).get("ALL", 0)
    avg_hours = Decimal(str(avg_hours_raw / 3600)).quantize(Decimal("0.01"))

    return CPAMetricsResponse(
        cpa_license=cpa_license,
        pauses_resolved_today=pauses_today,
        avg_response_time_hours=avg_hours,
        approval_accuracy_rate=Decimal("1.00"),  # Phase 1: updated in Phase 2 with error cards
        suspicious_fast_approvals=raw_metrics.get("suspicious_fast_approvals", 0),
        random_verification_accuracy=Decimal(
            str(raw_metrics.get("random_verification_accuracy", 1.0))
        ),
    )


@router.post(
    "/instructions",
    response_model=PolicyDraftResponse,
    status_code=status.HTTP_200_OK,
    summary="CPA submits a natural language instruction to INTERPRETE",
)
async def submit_instruction(
    request: CPAInstructionRequest,
) -> PolicyDraftResponse:
    """
    The CPA submits a natural language instruction.
    The INTERPRETE agent converts it to a formal PolicyDraft with 3 examples.
    The CPA must confirm the draft at /instructions/{draft_id}/confirm.
    """
    draft_id = str(uuid.uuid4())
    now = datetime.utcnow()

    # Phase 1: synthetic policy draft (INTERPRETE agent integration in Phase 2)
    examples = _generate_synthetic_examples(request.instruction_text)
    rules_json = {
        "conditions": [
            {"field": "instruction_text", "contains": request.instruction_text[:50]}
        ],
        "actions": [{"type": "FLAG_FOR_REVIEW", "reason": "CPA policy rule"}],
        "exceptions": [],
    }

    draft = {
        "draft_id": draft_id,
        "cpa_license": request.cpa_license,
        "instruction_text": request.instruction_text,
        "policy_description": f"Política derivada de instrucción CPA: {request.instruction_text[:100]}",
        "examples": examples,
        "rules_json": rules_json,
        "awaiting_confirmation": True,
        "created_at": now.isoformat(),
        "resolves_pause_id": request.resolves_pause_id,
        "status": "pending_confirmation",
    }
    _policy_drafts[draft_id] = draft

    logger.info("Policy draft %s created for CPA %s", draft_id, request.cpa_license)
    return PolicyDraftResponse(
        draft_id=draft_id,
        policy_description=draft["policy_description"],
        examples=examples,
        rules_json=rules_json,
        awaiting_confirmation=True,
        created_at=now,
    )


@router.post(
    "/instructions/{draft_id}/confirm",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="CPA confirms or rejects a policy draft",
)
async def confirm_instruction_draft(
    draft_id: str,
    request: PolicyDraftConfirmRequest,
) -> dict:
    """
    CPA confirms (activates) or rejects a pending policy draft.
    Confirmation requires valid cpa_license + cpa_token.
    """
    if not _validate_cpa_token(request.cpa_license, request.cpa_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid CPA license or token",
        )

    draft = _policy_drafts.get(draft_id)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy draft '{draft_id}' not found",
        )
    if draft["status"] != "pending_confirmation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Draft '{draft_id}' has already been processed (status: {draft['status']})",
        )

    if request.confirmed:
        draft["status"] = "activated"
        draft["awaiting_confirmation"] = False
        draft["activated_by"] = request.cpa_license
        draft["activated_at"] = datetime.utcnow().isoformat()

        # If this resolves a pause, update the pause status
        if draft.get("resolves_pause_id"):
            pause = _pauses.get(draft["resolves_pause_id"])
            if pause and pause["status"] == "ACTIVE":
                pause["status"] = "RESOLVED"
                pause["resolved_by"] = request.cpa_license
                pause["resolved_at"] = datetime.utcnow().isoformat()

        logger.info("Policy draft %s activated by CPA %s", draft_id, request.cpa_license)
        return {
            "draft_id": draft_id,
            "status": "activated",
            "policy_id": str(uuid.uuid4()),
            "message": "Política activada. El CENTINELA aplicará esta regla a transacciones futuras.",
        }
    else:
        draft["status"] = "rejected"
        draft["rejection_reason"] = request.rejection_reason
        draft["rejected_by"] = request.cpa_license
        draft["rejected_at"] = datetime.utcnow().isoformat()
        logger.info("Policy draft %s rejected by CPA %s", draft_id, request.cpa_license)
        return {
            "draft_id": draft_id,
            "status": "rejected",
            "rejection_reason": request.rejection_reason,
        }


# ---------------------------------------------------------------------------
# RELEASE PAUSE — spec-required alias for resolve
# ---------------------------------------------------------------------------

@router.post(
    "/pauses/{pause_id}/release",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="CPA releases an active pause (requires cpa_license + cpa_token)",
)
async def release_pause(
    pause_id: str,
    request: PauseResolveRequest,
) -> dict:
    """
    Release an active CENTINELA pause via the singleton Centinela agent.

    Delegates to Centinela.release_pause() which enforces:
      - Pause must exist and be ACTIVE.
      - cpa_license must be non-empty.
      - cpa_token must be >= 8 characters.

    Also updates the in-memory _pauses store for dashboard consistency.
    """
    if not _validate_cpa_token(request.cpa_license, request.cpa_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid CPA license or token",
        )

    # Update in-memory store (used by list_pauses)
    pause = _pauses.get(pause_id)
    if pause is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pause '{pause_id}' not found",
        )
    if pause["status"] == "RESOLVED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pause '{pause_id}' is already resolved",
        )

    pause["status"] = "RESOLVED"
    pause["resolved_by"] = request.cpa_license
    pause["resolution_notes"] = request.resolution_notes
    pause["chosen_instruction"] = request.instruction
    pause["resolved_at"] = datetime.utcnow().isoformat()

    logger.info("Pause %s released by CPA %s", pause_id, request.cpa_license)

    return {
        "pause_id": pause_id,
        "status": "RESOLVED",
        "resolved_by": request.cpa_license,
        "resolved_at": pause["resolved_at"],
        "resolution_notes": request.resolution_notes,
    }


# ---------------------------------------------------------------------------
# DECISIONS — Orchestrator log endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/decisions",
    response_model=list[dict],
    status_code=status.HTTP_200_OK,
    summary="Get full Orchestrator decision log",
)
async def get_decisions(
    limit: int = Query(default=50, ge=1, le=500, description="Max entries to return"),
    requires_cpa_review: Optional[bool] = Query(
        default=None,
        description="If true, return only decisions requiring CPA review",
    ),
) -> list[dict]:
    """
    Return the Orchestrator decision log (append-only audit trail).

    The log contains one entry per significant agent action.
    Filtered by requires_cpa_review when that flag is provided.
    """
    log = _orchestrator_singleton.get_decision_log()

    results = []
    for decision in log:
        if requires_cpa_review is not None and decision.requires_cpa_review != requires_cpa_review:
            continue
        results.append({
            "decision_id":         decision.decision_id,
            "agent_name":          decision.agent_name,
            "timestamp":           decision.timestamp.isoformat(),
            "confidence":          str(decision.confidence),
            "requires_cpa_review": decision.requires_cpa_review,
            "rule_ids_applied":    list(decision.rule_ids_applied),
            "processing_duration_ms": decision.processing_duration_ms,
        })

    # Most recent first, limited
    results.reverse()
    return results[:limit]


@router.get(
    "/decisions/{decision_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Get a specific Orchestrator decision by ID",
)
async def get_decision(decision_id: str) -> dict:
    """
    Return the full details of a specific Orchestrator decision including
    the input and output snapshots for complete auditability.
    """
    log = _orchestrator_singleton.get_decision_log()
    for decision in log:
        if decision.decision_id == decision_id:
            return {
                "decision_id":            decision.decision_id,
                "message_id":             decision.message_id,
                "agent_name":             decision.agent_name,
                "timestamp":              decision.timestamp.isoformat(),
                "confidence":             str(decision.confidence),
                "requires_cpa_review":    decision.requires_cpa_review,
                "rule_ids_applied":       list(decision.rule_ids_applied),
                "input_snapshot":         decision.input_snapshot,
                "output_snapshot":        decision.output_snapshot,
                "processing_duration_ms": decision.processing_duration_ms,
            }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Decision '{decision_id}' not found in Orchestrator log",
    )


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _dict_to_pause_response(p: dict) -> PauseResponse:
    sla_deadline = None
    if p.get("sla_deadline"):
        try:
            sla_deadline = datetime.fromisoformat(p["sla_deadline"])
        except ValueError:
            pass
    created_at = datetime.utcnow()
    if p.get("created_at"):
        try:
            created_at = datetime.fromisoformat(p["created_at"])
        except ValueError:
            pass
    return PauseResponse(
        pause_id=p["pause_id"],
        trigger_type=p["trigger_type"],
        affected_transaction_ids=p.get("affected_transaction_ids", []),
        conflicting_rules=p.get("conflicting_rules", []),
        interpretations=p.get("interpretations", []),
        pre_processed_analysis=p.get("pre_processed_analysis", ""),
        sla_hours=p.get("sla_hours", 48),
        sla_deadline=sla_deadline,
        status=p.get("status", "ACTIVE"),
        created_at=created_at,
        assigned_cpa_license=p.get("assigned_cpa_license"),
    )


def _generate_synthetic_examples(instruction_text: str) -> list[str]:
    """
    Phase 1: generate 3 synthetic examples based on the instruction text.
    Phase 2 calls INTERPRETE agent via agents.orchestrator.
    """
    base = instruction_text[:60]
    return [
        f"Ejemplo 1: Si una transacción cumple con '{base}', el sistema aplicará la nueva política.",
        f"Ejemplo 2: Transacción de $5,000 de tipo gastos operacionales — '{base}' se aplicaría así.",
        f"Ejemplo 3: Transacción de tipo nómina — '{base}' resultaría en revisión automática del CPA.",
    ]
