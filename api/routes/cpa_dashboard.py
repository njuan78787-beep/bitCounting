# =============================================================================
# api/routes/cpa_dashboard.py
# CPA dashboard endpoints for Bit-Counting.
#
# Storage: PostgreSQL (app_centinela_pauses, app_review_queue, app_policy_drafts)
#          via SQLAlchemy async.
#
# Friction levels govern approval UX:
#   LOW    → simple click confirmation
#   MEDIUM → must expand detail first, then confirm
#   HIGH   → must answer a challenge question about the transaction
# =============================================================================

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import TokenUser
from ..database import get_db
from ..dependencies import get_current_user
from ..db.models import AppApprovalSession, AppCentinaelaPause, AppPolicyDraft, AppReviewQueueItem
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

# Singletons (stateless-safe; Orchestrator decision log is in-memory Phase 1)
_centinela_singleton = Centinela()
_orchestrator_singleton = Orchestrator()
_vigilance = CPAVigilanceSystem()


# ---------------------------------------------------------------------------
# TOKEN VALIDATION STUB
# ---------------------------------------------------------------------------

def _validate_cpa_token(cpa_license: str, cpa_token: str) -> bool:
    """Phase 1 stub: accepts any non-empty license+token pair."""
    return bool(cpa_license and cpa_token)


# ---------------------------------------------------------------------------
# GET /pauses
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
    db:   AsyncSession = Depends(get_db),
) -> list[PauseResponse]:
    target_status = (status_filter or "ACTIVE").upper()

    stmt = select(AppCentinaelaPause)
    if target_status != "ALL":
        stmt = stmt.where(AppCentinaelaPause.status == target_status)
    if cpa_license:
        stmt = stmt.where(
            (AppCentinaelaPause.assigned_cpa_license == cpa_license) |
            (AppCentinaelaPause.assigned_cpa_license == None)  # noqa: E711
        )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    pauses = [_row_to_pause_response(r) for r in rows]
    pauses.sort(key=lambda p: p.sla_deadline or datetime.max)
    return pauses


# ---------------------------------------------------------------------------
# POST /pauses/{pause_id}/resolve
# ---------------------------------------------------------------------------

@router.post(
    "/pauses/{pause_id}/resolve",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="CPA resolves an active pause",
)
async def resolve_pause(
    pause_id: str,
    request:  PauseResolveRequest,
    db:       AsyncSession = Depends(get_db),
) -> dict:
    if not _validate_cpa_token(request.cpa_license, request.cpa_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid CPA license or token")

    row = await _get_pause_or_404(db, pause_id)
    if row.status == "RESOLVED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Pause '{pause_id}' is already resolved")

    now = datetime.utcnow()
    row.status = "RESOLVED"
    row.resolved_by = request.cpa_license
    row.resolution_notes = request.resolution_notes
    row.chosen_instruction = request.instruction
    row.resolved_at = now
    await db.commit()

    logger.info("Pause %s resolved by CPA %s", pause_id, request.cpa_license)
    return {"pause_id": pause_id, "status": "RESOLVED", "resolved_by": request.cpa_license, "resolved_at": now.isoformat()}


# ---------------------------------------------------------------------------
# GET /review-queue
# ---------------------------------------------------------------------------

@router.get(
    "/review-queue",
    response_model=list[dict],
    status_code=status.HTTP_200_OK,
    summary="Get items in CPA review queue, sorted by SLA deadline",
)
async def get_review_queue(
    cpa_license: Optional[str] = Query(default=None),
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(
        select(AppReviewQueueItem).where(AppReviewQueueItem.status == "pending_review")
    )
    rows = result.scalars().all()
    items = [_row_to_review_dict(r) for r in rows]
    items.sort(key=lambda i: i.get("sla_deadline") or "9999-99-99")
    return items


# ---------------------------------------------------------------------------
# POST /approve/{item_id}
# ---------------------------------------------------------------------------

@router.post(
    "/approve/{item_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Approve or reject a review-queue item with CPA friction",
)
async def approve_item(
    item_id: str,
    request: CPAApprovalRequest,
    _user:   TokenUser = Depends(get_current_user),
    db:      AsyncSession = Depends(get_db),
) -> dict:
    if not _validate_cpa_token(request.cpa_license, request.cpa_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid CPA license or token")

    result = await db.execute(
        select(AppReviewQueueItem).where(AppReviewQueueItem.item_id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Review item '{item_id}' not found")
    if item.status != "pending_review":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Item '{item_id}' is no longer pending review (status: {item.status})")

    level = item.consequence_level or "LOW"

    # Track session start time
    sess_result = await db.execute(
        select(AppApprovalSession).where(
            and_(AppApprovalSession.cpa_license == request.cpa_license,
                 AppApprovalSession.item_id == item_id)
        )
    )
    session = sess_result.scalar_one_or_none()
    if session is None:
        session = AppApprovalSession(
            cpa_license=request.cpa_license,
            item_id=item_id,
            level=level,
        )
        db.add(session)
        await db.flush()

    # --- LOW friction ---
    if level == "LOW":
        elapsed = int((datetime.utcnow() - session.started_at).total_seconds())
        _vigilance.track_approval_time(request.cpa_license, item_id, elapsed, level)
        item.status = "approved" if request.action == "approved" else "rejected"
        item.resolved_by = request.cpa_license
        item.resolution_notes = request.notes
        item.resolved_at = datetime.utcnow()
        await db.delete(session)
        await db.commit()
        return {"item_id": item_id, "status": item.status, "friction_level": "LOW"}

    # --- MEDIUM friction ---
    if level == "MEDIUM":
        if not request.detail_confirmed:
            await db.commit()   # persist session
            return {
                "item_id": item_id,
                "friction_level": "MEDIUM",
                "action_required": "expand_detail",
                "detail": item.detail or {},
                "instruction": "Expanda los detalles, léalos y reenvíe con detail_confirmed=true para confirmar.",
            }
        elapsed = int((datetime.utcnow() - session.started_at).total_seconds())
        _vigilance.track_approval_time(request.cpa_license, item_id, elapsed, level)
        item.status = "approved" if request.action == "approved" else "rejected"
        item.resolved_by = request.cpa_license
        item.resolution_notes = request.notes
        item.resolved_at = datetime.utcnow()
        await db.delete(session)
        await db.commit()
        return {"item_id": item_id, "status": item.status, "friction_level": "MEDIUM"}

    # --- HIGH friction ---
    if level == "HIGH":
        challenge = _vigilance.generate_friction_challenge(_row_to_review_dict(item), "HIGH")
        if not request.challenge_answer:
            await db.commit()   # persist session
            return {
                "item_id": item_id,
                "friction_level": "HIGH",
                "action_required": "answer_question",
                "question": challenge.get("question", ""),
                "instruction": "Responda la pregunta sobre la transacción para continuar.",
            }
        correct_answer = challenge.get("correct_answer", "")
        if request.challenge_answer.strip().lower() != correct_answer.strip().lower():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail={"error": "Respuesta incorrecta al desafío de verificación.",
                                        "hint": "Revise los detalles de la transacción y responda nuevamente."})
        elapsed = int((datetime.utcnow() - session.started_at).total_seconds())
        _vigilance.track_approval_time(request.cpa_license, item_id, elapsed, level)
        item.status = "approved" if request.action == "approved" else "rejected"
        item.resolved_by = request.cpa_license
        item.resolution_notes = request.notes
        item.resolved_at = datetime.utcnow()
        await db.delete(session)
        await db.commit()
        return {"item_id": item_id, "status": item.status, "friction_level": "HIGH"}

    raise HTTPException(status_code=500, detail=f"Unknown consequence level '{level}'")


# ---------------------------------------------------------------------------
# GET /metrics
# ---------------------------------------------------------------------------

@router.get(
    "/metrics",
    response_model=CPAMetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get CPA performance and vigilance metrics",
)
async def get_cpa_metrics(
    cpa_license: str = Query(..., description="CPA license number"),
    _user: TokenUser = Depends(get_current_user),
    db:    AsyncSession = Depends(get_db),
) -> CPAMetricsResponse:
    raw = _vigilance.get_cpa_vigilance_metrics(cpa_license)

    today = datetime.utcnow().date().isoformat()
    result = await db.execute(
        select(AppCentinaelaPause).where(
            and_(AppCentinaelaPause.resolved_by == cpa_license,
                 AppCentinaelaPause.resolved_at >= datetime.utcnow().replace(hour=0, minute=0, second=0))
        )
    )
    pauses_today = len(result.scalars().all())

    avg_secs = raw.get("avg_approval_time_by_level", {}).get("ALL", 0)
    avg_hours = Decimal(str(avg_secs / 3600)).quantize(Decimal("0.01"))

    return CPAMetricsResponse(
        cpa_license=cpa_license,
        pauses_resolved_today=pauses_today,
        avg_response_time_hours=avg_hours,
        approval_accuracy_rate=Decimal("1.00"),
        suspicious_fast_approvals=raw.get("suspicious_fast_approvals", 0),
        random_verification_accuracy=Decimal(str(raw.get("random_verification_accuracy", 1.0))),
    )


# ---------------------------------------------------------------------------
# POST /instructions
# ---------------------------------------------------------------------------

@router.post(
    "/instructions",
    response_model=PolicyDraftResponse,
    status_code=status.HTTP_200_OK,
    summary="CPA submits a natural language instruction to INTERPRETE",
)
async def submit_instruction(
    request: CPAInstructionRequest,
    db:      AsyncSession = Depends(get_db),
) -> PolicyDraftResponse:
    draft_id = str(uuid.uuid4())
    now      = datetime.utcnow()
    examples = _generate_synthetic_examples(request.instruction_text)
    rules_json = {
        "conditions": [{"field": "instruction_text", "contains": request.instruction_text[:50]}],
        "actions":    [{"type": "FLAG_FOR_REVIEW", "reason": "CPA policy rule"}],
        "exceptions": [],
    }

    draft = AppPolicyDraft(
        draft_id=draft_id,
        cpa_license=request.cpa_license,
        instruction_text=request.instruction_text,
        policy_description=f"Política derivada de instrucción CPA: {request.instruction_text[:100]}",
        examples=examples,
        rules_json=rules_json,
        awaiting_confirmation=True,
        status="pending_confirmation",
        resolves_pause_id=request.resolves_pause_id,
    )
    db.add(draft)
    await db.commit()

    logger.info("Policy draft %s created for CPA %s", draft_id, request.cpa_license)
    return PolicyDraftResponse(
        draft_id=draft_id,
        policy_description=draft.policy_description,
        examples=examples,
        rules_json=rules_json,
        awaiting_confirmation=True,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# POST /instructions/{draft_id}/confirm
# ---------------------------------------------------------------------------

@router.post(
    "/instructions/{draft_id}/confirm",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="CPA confirms or rejects a policy draft",
)
async def confirm_instruction_draft(
    draft_id: str,
    request:  PolicyDraftConfirmRequest,
    db:       AsyncSession = Depends(get_db),
) -> dict:
    if not _validate_cpa_token(request.cpa_license, request.cpa_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid CPA license or token")

    result = await db.execute(select(AppPolicyDraft).where(AppPolicyDraft.draft_id == draft_id))
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Policy draft '{draft_id}' not found")
    if draft.status != "pending_confirmation":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Draft '{draft_id}' has already been processed (status: {draft.status})")

    now = datetime.utcnow()
    if request.confirmed:
        draft.status = "activated"
        draft.awaiting_confirmation = False
        draft.activated_by = request.cpa_license
        draft.activated_at = now

        # Resolve associated pause if any
        if draft.resolves_pause_id:
            pause_result = await db.execute(
                select(AppCentinaelaPause).where(AppCentinaelaPause.pause_id == draft.resolves_pause_id)
            )
            pause = pause_result.scalar_one_or_none()
            if pause and pause.status == "ACTIVE":
                pause.status = "RESOLVED"
                pause.resolved_by = request.cpa_license
                pause.resolved_at = now

        await db.commit()
        logger.info("Policy draft %s activated by CPA %s", draft_id, request.cpa_license)
        return {
            "draft_id": draft_id,
            "status": "activated",
            "policy_id": str(uuid.uuid4()),
            "message": "Política activada. El CENTINELA aplicará esta regla a transacciones futuras.",
        }
    else:
        draft.status = "rejected"
        draft.rejection_reason = request.rejection_reason
        draft.rejected_by = request.cpa_license
        draft.rejected_at = now
        await db.commit()
        logger.info("Policy draft %s rejected by CPA %s", draft_id, request.cpa_license)
        return {"draft_id": draft_id, "status": "rejected", "rejection_reason": request.rejection_reason}


# ---------------------------------------------------------------------------
# POST /pauses/{pause_id}/release  (alias for resolve)
# ---------------------------------------------------------------------------

@router.post(
    "/pauses/{pause_id}/release",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="CPA releases an active pause (requires cpa_license + cpa_token)",
)
async def release_pause(
    pause_id: str,
    request:  PauseResolveRequest,
    db:       AsyncSession = Depends(get_db),
) -> dict:
    return await resolve_pause(pause_id, request, db)


# ---------------------------------------------------------------------------
# GET /decisions  —  Orchestrator audit log (in-memory Phase 1)
# ---------------------------------------------------------------------------

@router.get(
    "/decisions",
    response_model=list[dict],
    status_code=status.HTTP_200_OK,
    summary="Get full Orchestrator decision log",
)
async def get_decisions(
    limit: int = Query(default=50, ge=1, le=500),
    requires_cpa_review: Optional[bool] = Query(default=None),
) -> list[dict]:
    log = _orchestrator_singleton.get_decision_log()
    results = []
    for d in log:
        if requires_cpa_review is not None and d.requires_cpa_review != requires_cpa_review:
            continue
        results.append({
            "decision_id":            d.decision_id,
            "agent_name":             d.agent_name,
            "timestamp":              d.timestamp.isoformat(),
            "confidence":             str(d.confidence),
            "requires_cpa_review":    d.requires_cpa_review,
            "rule_ids_applied":       list(d.rule_ids_applied),
            "processing_duration_ms": d.processing_duration_ms,
        })
    results.reverse()
    return results[:limit]


@router.get(
    "/decisions/{decision_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Get a specific Orchestrator decision by ID",
)
async def get_decision(decision_id: str) -> dict:
    log = _orchestrator_singleton.get_decision_log()
    for d in log:
        if d.decision_id == decision_id:
            return {
                "decision_id":            d.decision_id,
                "message_id":             d.message_id,
                "agent_name":             d.agent_name,
                "timestamp":              d.timestamp.isoformat(),
                "confidence":             str(d.confidence),
                "requires_cpa_review":    d.requires_cpa_review,
                "rule_ids_applied":       list(d.rule_ids_applied),
                "input_snapshot":         d.input_snapshot,
                "output_snapshot":        d.output_snapshot,
                "processing_duration_ms": d.processing_duration_ms,
            }
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Decision '{decision_id}' not found in Orchestrator log")


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

async def _get_pause_or_404(db: AsyncSession, pause_id: str) -> AppCentinaelaPause:
    result = await db.execute(
        select(AppCentinaelaPause).where(AppCentinaelaPause.pause_id == pause_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Pause '{pause_id}' not found")
    return row


def _row_to_pause_response(r: AppCentinaelaPause) -> PauseResponse:
    return PauseResponse(
        pause_id=r.pause_id,
        trigger_type=r.trigger_type,
        affected_transaction_ids=r.affected_transaction_ids or [],
        conflicting_rules=r.conflicting_rules or [],
        interpretations=r.interpretations or [],
        pre_processed_analysis=r.pre_processed_analysis or "",
        sla_hours=r.sla_hours,
        sla_deadline=r.sla_deadline,
        status=r.status,
        created_at=r.created_at or datetime.utcnow(),
        assigned_cpa_license=r.assigned_cpa_license,
    )


def _row_to_review_dict(r: AppReviewQueueItem) -> dict:
    return {
        "item_id":           r.item_id,
        "item_type":         r.item_type,
        "transaction_id":    r.transaction_id,
        "client_id":         r.client_id,
        "vendor":            r.vendor,
        "amount":            str(r.amount) if r.amount is not None else None,
        "description":       r.description,
        "consequence_level": r.consequence_level,
        "friction_level":    r.consequence_level,
        "sla_deadline":      r.sla_deadline.isoformat() if r.sla_deadline else None,
        "created_at":        r.created_at.isoformat() if r.created_at else None,
        "status":            r.status,
        "detail":            r.detail or {},
    }


def _generate_synthetic_examples(instruction_text: str) -> list[str]:
    base = instruction_text[:60]
    return [
        f"Ejemplo 1: Si una transacción cumple con '{base}', el sistema aplicará la nueva política.",
        f"Ejemplo 2: Transacción de $5,000 de tipo gastos operacionales — '{base}' se aplicaría así.",
        f"Ejemplo 3: Transacción de tipo nómina — '{base}' resultaría en revisión automática del CPA.",
    ]
