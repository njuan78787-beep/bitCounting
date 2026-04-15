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

import hashlib
import logging
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Role, TokenUser
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


# =============================================================================
# SECCIÓN A — GESTIÓN DE SUS CLIENTES
# CPAs see only clients assigned to them.
# =============================================================================

@router.get(
    "/clients",
    status_code=status.HTTP_200_OK,
    summary="Listar clientes asignados a este CPA",
)
async def list_my_clients(
    accounting_status: Optional[str] = Query(default=None, description="UP_TO_DATE | NEEDS_REVIEW | PENDING_DOCUMENTS | OVERDUE"),
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    """
    Retorna solo los clientes asignados al CPA autenticado.
    EXIMIA_ADMIN ve todos. CPAs ven únicamente los suyos.
    """
    from ..db.models import AppClientProfile, AppCpaClientAssignment

    if db is None:
        return []

    if user.role == Role.EXIMIA_ADMIN:
        stmt = select(AppClientProfile)
    else:
        # CPA: filtrar por asignaciones activas
        cpa_license = _get_cpa_license(user)
        assigned = await db.execute(
            select(AppCpaClientAssignment.client_id).where(
                and_(AppCpaClientAssignment.cpa_license == cpa_license,
                     AppCpaClientAssignment.is_active == True)  # noqa: E712
            )
        )
        client_ids = [r[0] for r in assigned.all()]
        if not client_ids:
            return []
        stmt = select(AppClientProfile).where(AppClientProfile.client_id.in_(client_ids))

    if accounting_status:
        stmt = stmt.where(AppClientProfile.accounting_status == accounting_status.upper())
    stmt = stmt.order_by(AppClientProfile.business_name)

    result = await db.execute(stmt)
    return [_client_profile_to_dict(c) for c in result.scalars().all()]


@router.get(
    "/clients/{client_id}",
    status_code=status.HTTP_200_OK,
    summary="Perfil completo de un cliente asignado",
)
async def get_client_profile(
    client_id: str,
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    Retorna perfil completo del cliente: datos básicos, estado contable,
    transacciones recientes (conteo), formularios pendientes.
    CPA solo puede acceder a sus clientes asignados.
    """
    from ..db.models import AppClientProfile, AppReviewQueueItem, AppTaxForm

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    if user.role != Role.EXIMIA_ADMIN:
        await _assert_cpa_assigned_to_client(_get_cpa_license(user), client_id, db)

    profile_result = await db.execute(
        select(AppClientProfile).where(AppClientProfile.client_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(404, detail=f"Cliente '{client_id}' no encontrado.")

    # Outstanding review items
    review_result = await db.execute(
        select(func.count()).select_from(AppReviewQueueItem).where(
            and_(AppReviewQueueItem.client_id == client_id,
                 AppReviewQueueItem.status == "pending_review")
        )
    )
    pending_reviews = review_result.scalar() or 0

    # Pending tax forms
    forms_result = await db.execute(
        select(func.count()).select_from(AppTaxForm).where(
            and_(AppTaxForm.client_id == client_id,
                 AppTaxForm.form_status.in_(["PENDING_CPA_REVIEW", "PENDING_CPA_SIGNATURE"]))
        )
    )
    pending_forms = forms_result.scalar() or 0

    d = _client_profile_to_dict(profile)
    d["pending_review_items"] = pending_reviews
    d["pending_tax_forms"]    = pending_forms
    return d


@router.get(
    "/clients/{client_id}/accounting-status",
    status_code=status.HTTP_200_OK,
    summary="Estado contable real-time de un cliente",
)
async def get_client_accounting_status(
    client_id: str,
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    Snapshot del estado contable: colas pendientes, formularios, pausas CENTINELA
    y última actividad.
    """
    from ..db.models import AppClientProfile, AppReviewQueueItem, AppTaxForm, AppCentinaelaPause, AppTransaction

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    if user.role != Role.EXIMIA_ADMIN:
        await _assert_cpa_assigned_to_client(_get_cpa_license(user), client_id, db)

    # Review queue
    rq = await db.execute(
        select(func.count()).select_from(AppReviewQueueItem).where(
            and_(AppReviewQueueItem.client_id == client_id,
                 AppReviewQueueItem.status == "pending_review")
        )
    )
    # CENTINELA pauses
    cp = await db.execute(
        select(func.count()).select_from(AppCentinaelaPause).where(
            and_(AppCentinaelaPause.client_id == client_id,
                 AppCentinaelaPause.status == "ACTIVE")
        )
    )
    # Tax forms
    tf_review = await db.execute(
        select(func.count()).select_from(AppTaxForm).where(
            and_(AppTaxForm.client_id == client_id,
                 AppTaxForm.form_status == "PENDING_CPA_REVIEW")
        )
    )
    tf_sign = await db.execute(
        select(func.count()).select_from(AppTaxForm).where(
            and_(AppTaxForm.client_id == client_id,
                 AppTaxForm.form_status == "PENDING_CPA_SIGNATURE")
        )
    )
    # Latest transaction
    latest_tx = await db.execute(
        select(AppTransaction).where(AppTransaction.client_id == client_id)
        .order_by(AppTransaction.created_at.desc()).limit(1)
    )
    last_tx = latest_tx.scalar_one_or_none()

    pending_reviews = rq.scalar() or 0
    active_pauses   = cp.scalar() or 0
    forms_to_review = tf_review.scalar() or 0
    forms_to_sign   = tf_sign.scalar() or 0
    total_outstanding = pending_reviews + active_pauses + forms_to_review + forms_to_sign

    if total_outstanding == 0:
        computed_status = "UP_TO_DATE"
    elif active_pauses > 0 or forms_to_sign > 0:
        computed_status = "NEEDS_REVIEW"
    else:
        computed_status = "PENDING_DOCUMENTS"

    return {
        "client_id":           client_id,
        "computed_status":     computed_status,
        "pending_reviews":     pending_reviews,
        "active_pauses":       active_pauses,
        "forms_pending_review": forms_to_review,
        "forms_pending_sign":   forms_to_sign,
        "total_outstanding":   total_outstanding,
        "last_transaction_date": last_tx.date if last_tx else None,
        "last_transaction_amount": str(last_tx.amount) if last_tx else None,
        "as_of": datetime.utcnow().isoformat(),
    }


@router.put(
    "/clients/{client_id}/thresholds",
    status_code=status.HTTP_200_OK,
    summary="Configurar umbrales de alerta para un cliente",
)
async def update_client_thresholds(
    client_id:  str,
    thresholds: Dict[str, Any],
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    Configura umbrales personalizados de alerta del CPA para este cliente.
    Ejemplo: {"large_transaction_usd": 10000, "daily_tx_count": 50}
    """
    from ..db.models import AppClientProfile

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    if user.role != Role.EXIMIA_ADMIN:
        await _assert_cpa_assigned_to_client(_get_cpa_license(user), client_id, db)

    profile_result = await db.execute(
        select(AppClientProfile).where(AppClientProfile.client_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(404, detail=f"Cliente '{client_id}' no encontrado.")

    profile.alert_thresholds = thresholds
    await db.commit()

    return {
        "client_id":         client_id,
        "alert_thresholds":  profile.alert_thresholds,
        "updated_at":        datetime.utcnow().isoformat(),
    }


# =============================================================================
# SECCIÓN B — COLA DE TRABAJO UNIFICADA
# Combina: pausas CENTINELA + items de revisión + formularios pendientes.
# Ordenados por urgencia (SLA más próximo primero).
# =============================================================================

@router.get(
    "/work-queue",
    status_code=status.HTTP_200_OK,
    summary="Cola de trabajo unificada del CPA, ordenada por urgencia",
)
async def get_work_queue(
    client_id: Optional[str] = Query(default=None, description="Filtrar por cliente"),
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    """
    Retorna todos los items pendientes del CPA ordenados por SLA (más urgente primero):
    - Pausas CENTINELA activas (con análisis pre-procesado)
    - Transacciones con confianza 80–94% esperando revisión
    - Formularios fiscales pendientes de revisión o firma

    CPAs ven solo sus clientes asignados.
    """
    from ..db.models import AppCentinaelaPause, AppReviewQueueItem, AppTaxForm, AppCpaClientAssignment

    if db is None:
        return []

    cpa_license = _get_cpa_license(user) if user.role != Role.EXIMIA_ADMIN else None
    allowed_clients: Optional[List[str]] = None

    if cpa_license:
        assigned = await db.execute(
            select(AppCpaClientAssignment.client_id).where(
                and_(AppCpaClientAssignment.cpa_license == cpa_license,
                     AppCpaClientAssignment.is_active == True)  # noqa: E712
            )
        )
        allowed_clients = [r[0] for r in assigned.all()]

    items = []

    # --- CENTINELA pauses ---
    pause_stmt = select(AppCentinaelaPause).where(AppCentinaelaPause.status == "ACTIVE")
    if client_id:
        pause_stmt = pause_stmt.where(AppCentinaelaPause.client_id == client_id)
    elif allowed_clients is not None:
        if not allowed_clients:
            return []
        pause_stmt = pause_stmt.where(AppCentinaelaPause.client_id.in_(allowed_clients))

    pauses_result = await db.execute(pause_stmt)
    for p in pauses_result.scalars().all():
        sla_str = p.sla_deadline.isoformat() if p.sla_deadline else None
        items.append({
            "item_type":         "CENTINELA_PAUSE",
            "item_id":           p.pause_id,
            "client_id":         p.client_id,
            "priority":          "HIGH",
            "sla_deadline":      sla_str,
            "trigger_type":      p.trigger_type,
            "pre_analysis":      p.pre_processed_analysis or "",
            "conflicting_rules": p.conflicting_rules or [],
            "affected_transactions": p.affected_transaction_ids or [],
            "created_at":        p.created_at.isoformat() if p.created_at else None,
            "action_url":        f"/api/v1/cpa/pauses/{p.pause_id}/resolve",
        })

    # --- Review queue items ---
    review_stmt = select(AppReviewQueueItem).where(AppReviewQueueItem.status == "pending_review")
    if client_id:
        review_stmt = review_stmt.where(AppReviewQueueItem.client_id == client_id)
    elif allowed_clients is not None:
        review_stmt = review_stmt.where(AppReviewQueueItem.client_id.in_(allowed_clients))

    review_result = await db.execute(review_stmt)
    for r in review_result.scalars().all():
        sla_str = r.sla_deadline.isoformat() if r.sla_deadline else None
        items.append({
            "item_type":    "REVIEW_QUEUE",
            "item_id":      r.item_id,
            "client_id":    r.client_id,
            "priority":     r.consequence_level or "LOW",
            "sla_deadline": sla_str,
            "description":  r.description,
            "vendor":       r.vendor,
            "amount":       str(r.amount) if r.amount else None,
            "created_at":   r.created_at.isoformat() if r.created_at else None,
            "action_url":   f"/api/v1/cpa/approve/{r.item_id}",
        })

    # --- Tax forms pending CPA action ---
    forms_stmt = select(AppTaxForm).where(
        AppTaxForm.form_status.in_(["PENDING_CPA_REVIEW", "PENDING_CPA_SIGNATURE"])
    )
    if cpa_license:
        forms_stmt = forms_stmt.where(AppTaxForm.assigned_cpa_license == cpa_license)
    if client_id:
        forms_stmt = forms_stmt.where(AppTaxForm.client_id == client_id)
    elif allowed_clients is not None:
        forms_stmt = forms_stmt.where(AppTaxForm.client_id.in_(allowed_clients))

    forms_result = await db.execute(forms_stmt)
    for f in forms_result.scalars().all():
        priority = "HIGH" if f.form_status == "PENDING_CPA_SIGNATURE" else "MEDIUM"
        items.append({
            "item_type":    "TAX_FORM",
            "item_id":      f.form_id,
            "client_id":    f.client_id,
            "priority":     priority,
            "sla_deadline": None,
            "form_type":    f.form_type,
            "form_status":  f.form_status,
            "business_name": f.business_name,
            "tax_year":     f.tax_year,
            "created_at":   f.generated_at.isoformat() if f.generated_at else None,
            "action_url":   f"/api/v1/tax-forms/{f.form_id}",
        })

    # Sort: SLA deadline nulls last, then HIGH > MEDIUM > LOW
    _priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    def _sort_key(item):
        sla = item.get("sla_deadline") or "9999-99-99T99:99:99"
        pri = _priority_order.get(item.get("priority", "LOW"), 2)
        return (sla, pri)

    items.sort(key=_sort_key)
    return items


# =============================================================================
# SECCIÓN C — MÓDULO DE INTERPRETACIÓN (historial)
# POST /instructions y POST /instructions/{id}/confirm already exist above.
# =============================================================================

@router.get(
    "/instructions",
    status_code=status.HTTP_200_OK,
    summary="Historial de instrucciones del CPA",
)
async def list_instructions(
    instruction_status: Optional[str] = Query(default=None, alias="status"),
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    """
    Retorna el historial de instrucciones en lenguaje natural del CPA.
    CPAs ven solo las suyas. EXIMIA_ADMIN ve todas.
    """
    from ..db.models import AppPolicyDraft

    if db is None:
        return []

    cpa_license = _get_cpa_license(user) if user.role != Role.EXIMIA_ADMIN else None

    stmt = select(AppPolicyDraft)
    if cpa_license:
        stmt = stmt.where(AppPolicyDraft.cpa_license == cpa_license)
    if instruction_status:
        stmt = stmt.where(AppPolicyDraft.status == instruction_status.lower())
    stmt = stmt.order_by(AppPolicyDraft.created_at.desc())

    result = await db.execute(stmt)
    return [_draft_to_dict(d) for d in result.scalars().all()]


@router.get(
    "/instructions/{draft_id}",
    status_code=status.HTTP_200_OK,
    summary="Detalle de una instrucción / borrador de política",
)
async def get_instruction_detail(
    draft_id: str,
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppPolicyDraft

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    result = await db.execute(select(AppPolicyDraft).where(AppPolicyDraft.draft_id == draft_id))
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(404, detail=f"Instrucción '{draft_id}' no encontrada.")

    cpa_license = _get_cpa_license(user) if user.role != Role.EXIMIA_ADMIN else None
    if cpa_license and draft.cpa_license != cpa_license:
        raise HTTPException(403, detail="No tienes acceso a esta instrucción.")

    return _draft_to_dict(draft)


# =============================================================================
# SECCIÓN D — PAQUETE DE REVISIÓN DE PLANILLAS
# Returns an executive summary package for CPA review — line-by-line
# with computation explanations, before the CPA proceeds to sign.
# =============================================================================

@router.get(
    "/tax-forms/package/{form_id}",
    status_code=status.HTTP_200_OK,
    summary="Paquete ejecutivo de revisión de planilla para firma CPA",
)
async def get_form_review_package(
    form_id: str,
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    Retorna un paquete de revisión completo:
    - Resumen ejecutivo (totales, período, empresa)
    - Líneas con descripción, valor computado, y regla aplicada
    - Preguntas de fricción proporcionales al monto total
    - Instrucciones para el proceso de firma
    - Historial de versiones/enmiendas si aplica

    CPAs solo pueden acceder a planillas de sus clientes asignados.
    """
    from ..db.models import AppTaxForm

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    form_result = await db.execute(select(AppTaxForm).where(AppTaxForm.form_id == form_id))
    form_row = form_result.scalar_one_or_none()
    if form_row is None:
        raise HTTPException(404, detail=f"Planilla '{form_id}' no encontrada.")

    if user.role != Role.EXIMIA_ADMIN:
        cpa_license = _get_cpa_license(user)
        await _assert_cpa_assigned_to_client(cpa_license, form_row.client_id, db)

    summary = form_row.summary_json or {}
    lines   = form_row.lines_json   or []

    total_amount = summary.get("total_tax_due", summary.get("net_tax", 0))

    # Proportional friction questions based on amount
    friction_questions = _generate_friction_questions(form_row.form_type, total_amount, lines)

    # Amended form reference
    amends_info = None
    if form_row.amends_form_id:
        amends_result = await db.execute(
            select(AppTaxForm).where(AppTaxForm.form_id == form_row.amends_form_id)
        )
        original = amends_result.scalar_one_or_none()
        if original:
            amends_info = {
                "original_form_id": original.form_id,
                "original_status":  original.form_status,
                "original_signed_at": original.signature_json.get("signed_at") if original.signature_json else None,
            }

    return {
        "form_id":     form_row.form_id,
        "form_type":   form_row.form_type,
        "form_status": form_row.form_status,
        "executive_summary": {
            "business_name":  form_row.business_name,
            "ein_pr":         form_row.ein_pr,
            "tax_year":       form_row.tax_year,
            "period":         f"{form_row.period_start} → {form_row.period_end}",
            "tax_period":     form_row.tax_period,
            "total_tax_due":  total_amount,
            "summary_fields": summary,
            "generated_at":   form_row.generated_at.isoformat() if form_row.generated_at else None,
            "generated_by":   form_row.generated_by,
        },
        "lines":           lines,
        "review_notes":    form_row.review_notes,
        "reviewed_at":     form_row.reviewed_at.isoformat() if form_row.reviewed_at else None,
        "assigned_cpa":    form_row.assigned_cpa_license,
        "friction_questions": friction_questions,
        "signature_instructions": (
            "1. Revise cada línea computada y sus reglas aplicadas.\n"
            "2. Si necesita enmendar valores, use POST /{form_id}/review con justificación por línea.\n"
            "3. Responda las preguntas de fricción para confirmar su comprensión.\n"
            "4. Firme con POST /{form_id}/sign indicando declaration_accepted=true.\n"
            "5. La firma es digital e irrevocable."
        ),
        "amends_form": amends_info,
    }


# =============================================================================
# SECCIÓN E — APRENDIZAJE CPA
# CPAs submit corrections → anonymized → sent to admin approval pool.
# =============================================================================

class LearningCorrectionRequest(BaseModel):
    card_type:              str   = Field(description="CLASSIFICATION_CORRECTION | POLICY_INSTRUCTION | SIGNATURE_PATTERN")
    anonymized_description: str   = Field(min_length=10)
    original_agent_decision: Optional[Dict[str, Any]] = None
    cpa_correction:          Optional[Dict[str, Any]] = None
    context_tags:            List[str] = Field(default_factory=list)


@router.get(
    "/learning/corrections",
    status_code=status.HTTP_200_OK,
    summary="Fichas de corrección enviadas por este CPA",
)
async def list_my_corrections(
    card_status: Optional[str] = Query(default=None, alias="status"),
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    from ..db.models import AppLearningCard

    if db is None:
        return []

    cpa_license = _get_cpa_license(user)
    license_hash = hashlib.sha256(cpa_license.encode()).hexdigest()

    stmt = select(AppLearningCard).where(AppLearningCard.contributed_by_hash == license_hash)
    if card_status:
        stmt = stmt.where(AppLearningCard.status == card_status.upper())
    stmt = stmt.order_by(AppLearningCard.created_at.desc())

    result = await db.execute(stmt)
    cards = result.scalars().all()
    # CPAs see anonymized view — no other CPA's corrections
    return [_learning_card_cpa_view(c) for c in cards]


@router.post(
    "/learning/corrections",
    status_code=status.HTTP_201_CREATED,
    summary="Enviar corrección al pool de aprendizaje",
)
async def submit_learning_correction(
    body: LearningCorrectionRequest,
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    El CPA envía una corrección al pool de aprendizaje.
    La ficha se anonimiza automáticamente: el contributed_by_hash es SHA-256 de la licencia.
    Queda en PENDING_APPROVAL hasta que EXIMIA_ADMIN la revise.
    """
    from ..db.models import AppLearningCard

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    cpa_license = _get_cpa_license(user)
    license_hash = hashlib.sha256(cpa_license.encode()).hexdigest()

    card = AppLearningCard(
        card_type=body.card_type.upper(),
        anonymized_description=body.anonymized_description,
        original_agent_decision=body.original_agent_decision,
        cpa_correction=body.cpa_correction,
        context_tags=body.context_tags,
        status="PENDING_APPROVAL",
        contributed_by_hash=license_hash,
    )
    db.add(card)
    await db.commit()

    logger.info("CPA %s submitted learning card %s", cpa_license, card.card_id)
    return _learning_card_cpa_view(card)


@router.get(
    "/performance",
    status_code=status.HTTP_200_OK,
    summary="Métricas de desempeño del CPA",
)
async def get_cpa_performance(
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    Retorna métricas de desempeño del CPA:
    - Items resueltos (total, últimas 24h, últimos 7 días)
    - Formularios firmados
    - Instrucciones enviadas y activadas
    - Fichas de aprendizaje contribuidas
    - Tiempo promedio de resolución (basado en AppApprovalSession)
    """
    from ..db.models import (AppReviewQueueItem, AppCentinaelaPause,
                              AppTaxForm, AppPolicyDraft, AppLearningCard, AppApprovalSession)

    if db is None:
        return {"cpa_license": _get_cpa_license(user), "error": "database_unavailable"}

    cpa_license = _get_cpa_license(user)
    license_hash = hashlib.sha256(cpa_license.encode()).hexdigest()
    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)
    last_7d  = now - timedelta(days=7)

    async def _count(model, *wheres):
        r = await db.execute(
            select(func.count()).select_from(model).where(and_(*wheres))
        )
        return r.scalar() or 0

    reviews_total  = await _count(AppReviewQueueItem, AppReviewQueueItem.resolved_by == cpa_license)
    pauses_total   = await _count(AppCentinaelaPause, AppCentinaelaPause.resolved_by == cpa_license)
    forms_signed   = await _count(AppTaxForm,
                                   AppTaxForm.assigned_cpa_license == cpa_license,
                                   AppTaxForm.form_status == "SIGNED")
    instructions   = await _count(AppPolicyDraft, AppPolicyDraft.cpa_license == cpa_license)
    active_instrs  = await _count(AppPolicyDraft, AppPolicyDraft.cpa_license == cpa_license,
                                   AppPolicyDraft.status == "activated")
    learning_cards = await _count(AppLearningCard, AppLearningCard.contributed_by_hash == license_hash)

    reviews_24h    = await _count(AppReviewQueueItem,
                                   AppReviewQueueItem.resolved_by == cpa_license,
                                   AppReviewQueueItem.resolved_at >= last_24h)
    pauses_24h     = await _count(AppCentinaelaPause,
                                   AppCentinaelaPause.resolved_by == cpa_license,
                                   AppCentinaelaPause.resolved_at >= last_24h)

    # Average resolution time from sessions
    sessions_result = await db.execute(
        select(AppApprovalSession).where(AppApprovalSession.cpa_license == cpa_license)
    )
    # Sessions table tracks in-flight sessions; completed ones are deleted, so we use vigilance
    raw_metrics = _vigilance.get_cpa_vigilance_metrics(cpa_license)

    return {
        "cpa_license":              cpa_license,
        "as_of":                    now.isoformat(),
        "items_resolved": {
            "review_queue_total":   reviews_total,
            "pauses_total":         pauses_total,
            "last_24h":             reviews_24h + pauses_24h,
        },
        "forms_signed":             forms_signed,
        "instructions": {
            "submitted":            instructions,
            "activated":            active_instrs,
            "activation_rate":      round(active_instrs / max(instructions, 1), 4),
        },
        "learning_contributions":   learning_cards,
        "avg_approval_time_secs":   raw_metrics.get("avg_approval_time_by_level", {}).get("ALL", 0),
        "suspicious_fast_approvals": raw_metrics.get("suspicious_fast_approvals", 0),
    }


# =============================================================================
# CPA DASHBOARD HELPERS
# =============================================================================

def _get_cpa_license(user: TokenUser) -> str:
    """
    Returns the CPA license for the authenticated user.
    For CPAs the license is stored as `sub` (username) in the JWT.
    EXIMIA_ADMIN should not call this without checking role first.
    """
    return user.username


async def _assert_cpa_assigned_to_client(cpa_license: str, client_id: str, db) -> None:
    """Enforces CPA data isolation — raises 403 if not assigned."""
    if db is None:
        return
    from sqlalchemy import text
    result = await db.execute(
        text("SELECT 1 FROM cpa_client_assignments WHERE cpa_license = :cpa AND client_id = :cid AND is_active = TRUE LIMIT 1"),
        {"cpa": cpa_license, "cid": client_id},
    )
    if result.fetchone() is None:
        raise HTTPException(403, detail="No estás asignado a este cliente.")


def _client_profile_to_dict(p) -> dict:
    return {
        "client_id":            p.client_id,
        "business_name":        p.business_name,
        "ein_pr":               p.ein_pr,
        "business_type":        p.business_type,
        "municipality_pr":      p.municipality_pr,
        "assigned_cpa_license": p.assigned_cpa_license,
        "accounting_status":    p.accounting_status,
        "last_transaction_date": p.last_transaction_date,
        "outstanding_items":    p.outstanding_items,
        "alert_thresholds":     p.alert_thresholds or {},
        "created_at":           p.created_at.isoformat() if p.created_at else None,
    }


def _draft_to_dict(d) -> dict:
    return {
        "draft_id":              d.draft_id,
        "cpa_license":           d.cpa_license,
        "instruction_text":      d.instruction_text,
        "policy_description":    d.policy_description,
        "examples":              d.examples or [],
        "rules_json":            d.rules_json or {},
        "awaiting_confirmation": d.awaiting_confirmation,
        "status":                d.status,
        "resolves_pause_id":     d.resolves_pause_id,
        "activated_at":          d.activated_at.isoformat() if d.activated_at else None,
        "rejection_reason":      d.rejection_reason,
        "created_at":            d.created_at.isoformat() if d.created_at else None,
    }


def _learning_card_cpa_view(c) -> dict:
    """CPA view — excludes admin-only fields; shows only their own cards."""
    return {
        "card_id":                c.card_id,
        "card_type":              c.card_type,
        "anonymized_description": c.anonymized_description,
        "context_tags":           c.context_tags or [],
        "status":                 c.status,
        "rejection_reason":       c.rejection_reason if c.status == "REJECTED" else None,
        "created_at":             c.created_at.isoformat() if c.created_at else None,
    }


def _generate_friction_questions(form_type: str, total_amount: Any, lines: list) -> List[dict]:
    """
    Generates proportional friction questions based on form type and total amount.
    Higher amounts → more verification questions.
    """
    questions = []
    amount = float(total_amount or 0)

    questions.append({
        "id":       "q_period",
        "required": True,
        "question": f"¿Confirmaste que el período fiscal cubre todas las transacciones incluidas en este formulario {form_type}?",
        "type":     "boolean",
    })

    if amount > 10_000:
        questions.append({
            "id":       "q_large_amount",
            "required": True,
            "question": f"El impuesto total calculado es ${amount:,.2f}. ¿Verificaste que las tasas aplicadas son las vigentes para el período?",
            "type":     "boolean",
        })

    if amount > 50_000:
        questions.append({
            "id":       "q_high_amount",
            "required": True,
            "question": f"Este formulario tiene un impacto fiscal superior a $50,000. ¿Revisaste la base imponible y los créditos aplicados línea por línea?",
            "type":     "boolean",
        })

    if form_type in ("SC2915", "480_20"):
        questions.append({
            "id":       "q_pr_specific",
            "required": True,
            "question": f"Para el formulario {form_type}, ¿confirmaste que los datos de Hacienda PR corresponden al EIN registrado?",
            "type":     "boolean",
        })

    return questions
