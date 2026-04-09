# =============================================================================
# api/routes/centinela.py
# Endpoints del módulo CENTINELA para Bit-Counting.
#
# Storage: PostgreSQL (app_centinela_pauses) via SQLAlchemy async.
#
# Endpoints:
#   GET  /api/v1/centinela/pauses            — pausas activas del cliente
#   GET  /api/v1/centinela/pauses/{id}       — detalle con análisis pre-procesado
#   POST /api/v1/centinela/pauses/{id}/release — liberar pausa (solo CPA)
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Role, TokenUser
from ..database import get_db
from ..dependencies import get_current_user, require_role, verify_client_scope
from ..db.models import AppCentinaelaPause
from ..schemas import PauseResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/centinela", tags=["centinela"])


class ReleasePauseRequest(BaseModel):
    resolution_notes: str = Field(min_length=10, description="Notas de resolución (mínimo 10 chars)")
    instruction:      str = Field(min_length=5,  description="Instrucción elegida por el CPA")


# ---------------------------------------------------------------------------
# GET /pauses
# ---------------------------------------------------------------------------

@router.get(
    "/pauses",
    response_model=list[PauseResponse],
    status_code=status.HTTP_200_OK,
    summary="Listar pausas activas del cliente autenticado",
)
async def list_pauses(
    status_filter: Optional[str] = Query(default="ACTIVE", alias="status"),
    user:          TokenUser = Depends(get_current_user),
    db:            AsyncSession = Depends(get_db),
) -> list[PauseResponse]:
    target_status = (status_filter or "ACTIVE").upper()

    stmt = select(AppCentinaelaPause)
    if target_status != "ALL":
        stmt = stmt.where(AppCentinaelaPause.status == target_status)

    # CLIENT role: restrict to own pauses
    if user.role == Role.CLIENT and user.client_id:
        stmt = stmt.where(AppCentinaelaPause.client_id == user.client_id)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    pauses = [_row_to_pause(r) for r in rows]
    pauses.sort(key=lambda p: p.sla_deadline or datetime.max)
    return pauses


# ---------------------------------------------------------------------------
# GET /pauses/{pause_id}
# ---------------------------------------------------------------------------

@router.get(
    "/pauses/{pause_id}",
    response_model=PauseResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtener detalle de una pausa con análisis pre-procesado",
)
async def get_pause(
    pause_id: str,
    user:     TokenUser = Depends(get_current_user),
    db:       AsyncSession = Depends(get_db),
) -> PauseResponse:
    result = await db.execute(
        select(AppCentinaelaPause).where(AppCentinaelaPause.pause_id == pause_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Pausa '{pause_id}' no encontrada")

    if user.role == Role.CLIENT:
        if row.client_id and user.client_id and row.client_id != user.client_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="No tiene acceso a esta pausa")

    return _row_to_pause(row)


# ---------------------------------------------------------------------------
# POST /pauses/{pause_id}/release
# ---------------------------------------------------------------------------

_CPA_ROLES = (Role.CPA_PARTNER, Role.CPA_SENIOR, Role.EXIMIA_ADMIN)


@router.post(
    "/pauses/{pause_id}/release",
    status_code=status.HTTP_200_OK,
    summary="Liberar una pausa activa (solo roles CPA)",
)
async def release_pause(
    pause_id: str,
    body:     ReleasePauseRequest,
    user:     TokenUser = Depends(get_current_user),
    db:       AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in _CPA_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Solo roles CPA pueden liberar pausas. Rol actual: {user.role.value}",
        )

    result = await db.execute(
        select(AppCentinaelaPause).where(AppCentinaelaPause.pause_id == pause_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Pausa '{pause_id}' no encontrada")
    if row.status == "RESOLVED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Pausa '{pause_id}' ya está resuelta")

    now = datetime.utcnow()
    row.status = "RESOLVED"
    row.resolved_by = user.username
    row.resolution_notes = body.resolution_notes
    row.chosen_instruction = body.instruction
    row.resolved_at = now
    await db.commit()

    logger.info("CENTINELA: pausa %s liberada por %s (%s)", pause_id, user.username, user.role.value)
    return {"pause_id": pause_id, "status": "RESOLVED", "resolved_by": user.username, "resolved_at": now.isoformat()}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _row_to_pause(r: AppCentinaelaPause) -> PauseResponse:
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
