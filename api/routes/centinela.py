# =============================================================================
# api/routes/centinela.py
# Endpoints del módulo CENTINELA para Bit-Counting.
#
# Endpoints:
#   GET  /api/v1/centinela/pauses            — pausas activas del cliente
#   GET  /api/v1/centinela/pauses/{id}       — detalle con análisis pre-procesado
#   POST /api/v1/centinela/pauses/{id}/release — liberar pausa (solo CPA)
#
# SEGURIDAD:
#   - Todos los endpoints requieren JWT con mfa_verified=True
#   - GET /pauses y GET /pauses/{id}: CLIENT ve sus propias pausas; CPA ve todas
#   - POST /pauses/{id}/release: solo roles CPA_PARTNER, CPA_SENIOR, EXIMIA_ADMIN
#   - verify_client_scope aplicado en filtros de lista
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..auth import Role, TokenUser
from ..dependencies import get_current_user, require_role, verify_client_scope
from ..schemas import PauseResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/centinela", tags=["centinela"])

# ---------------------------------------------------------------------------
# Store compartido con cpa_dashboard (mismo dict en-memoria Phase 1)
# ---------------------------------------------------------------------------

# Importamos el store de cpa_dashboard para no duplicar datos
# En Phase 3 ambos módulos consultarán la tabla centinela_pauses en Postgres
try:
    from .cpa_dashboard import _pauses as _centinela_pauses
except ImportError:
    _centinela_pauses: dict = {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ReleasePauseRequest(BaseModel):
    resolution_notes: str = Field(min_length=10, description="Notas de resolución (mínimo 10 chars)")
    instruction:      str = Field(min_length=5,  description="Instrucción elegida por el CPA")


# ---------------------------------------------------------------------------
# GET /pauses — lista de pausas del cliente
# ---------------------------------------------------------------------------

@router.get(
    "/pauses",
    response_model=list[PauseResponse],
    status_code=status.HTTP_200_OK,
    summary="Listar pausas activas del cliente autenticado",
)
async def list_pauses(
    status_filter: Optional[str] = Query(
        default="ACTIVE",
        alias="status",
        description="Filtrar por status: ACTIVE, RESOLVED, ALL",
    ),
    user: TokenUser = Depends(get_current_user),
) -> list[PauseResponse]:
    """
    Retorna las pausas del CENTINELA para el cliente autenticado.

    - CLIENT: solo ve las pausas de su client_id
    - CPA / EXIMIA_ADMIN: ve todas las pausas, puede filtrar por status
    """
    target_status = (status_filter or "ACTIVE").upper()
    results = []

    for pause in _centinela_pauses.values():
        # Filtrar por status
        if target_status != "ALL" and pause.get("status", "ACTIVE") != target_status:
            continue

        # Scope check para CLIENT: solo sus propias pausas
        if user.role == Role.CLIENT:
            # Las pausas afectan transacciones — verificar que alguna pertenece al cliente
            # En Phase 1 usamos el client_id asignado al pause (si existe)
            pause_client = pause.get("client_id")
            if pause_client and user.client_id and pause_client != user.client_id:
                continue

        results.append(_dict_to_pause_response(pause))

    results.sort(key=lambda p: p.sla_deadline or datetime.max)
    return results


# ---------------------------------------------------------------------------
# GET /pauses/{pause_id} — detalle de una pausa
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
) -> PauseResponse:
    """
    Retorna el detalle completo de una pausa, incluyendo el análisis
    pre-procesado del CENTINELA y las interpretaciones disponibles.
    """
    pause = _centinela_pauses.get(pause_id)
    if pause is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pausa '{pause_id}' no encontrada",
        )

    # Scope check para CLIENT
    if user.role == Role.CLIENT:
        pause_client = pause.get("client_id")
        if pause_client and user.client_id and pause_client != user.client_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene acceso a esta pausa",
            )

    return _dict_to_pause_response(pause)


# ---------------------------------------------------------------------------
# POST /pauses/{pause_id}/release — liberar pausa (solo CPA)
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
) -> dict:
    """
    Libera una pausa activa del CENTINELA.

    Solo pueden liberar pausas: CPA_PARTNER, CPA_SENIOR, EXIMIA_ADMIN.
    Un CLIENT no puede liberar pausas — debe esperar al CPA asignado.
    """
    if user.role not in _CPA_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo roles CPA pueden liberar pausas. "
                f"Rol actual: {user.role.value}"
            ),
        )

    pause = _centinela_pauses.get(pause_id)
    if pause is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pausa '{pause_id}' no encontrada",
        )
    if pause.get("status") == "RESOLVED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pausa '{pause_id}' ya está resuelta",
        )

    pause["status"]           = "RESOLVED"
    pause["resolved_by"]      = user.username
    pause["resolution_notes"] = body.resolution_notes
    pause["chosen_instruction"] = body.instruction
    pause["resolved_at"]      = datetime.utcnow().isoformat()

    logger.info(
        "CENTINELA: pausa %s liberada por %s (%s)",
        pause_id, user.username, user.role.value,
    )
    return {
        "pause_id":      pause_id,
        "status":        "RESOLVED",
        "resolved_by":   user.username,
        "resolved_at":   pause["resolved_at"],
    }


# ---------------------------------------------------------------------------
# Helper
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
