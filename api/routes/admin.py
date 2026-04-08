# =============================================================================
# api/routes/admin.py
# Endpoints de administración — exclusivos de EXIMIA_ADMIN.
#
# Endpoints:
#   GET  /api/v1/admin/normative-updates          — cambios pendientes de revisión
#   POST /api/v1/admin/normative-updates/{id}/activate — activar con firma CPA
#
# SEGURIDAD:
#   - Todos los endpoints requieren role=EXIMIA_ADMIN
#   - La activación requiere los 3 factores del ActualizadorV2:
#       1. digital_signature (hex ≥16 chars)
#       2. effective_date (no en el pasado)
#       3. interpretation_note (≥20 chars)
#   - El sistema NUNCA activa automáticamente — requiere acción explícita del admin
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import Role, TokenUser
from ..dependencies import require_role, rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# Solo EXIMIA_ADMIN puede acceder a este router
_admin_only = require_role(Role.EXIMIA_ADMIN)

# ---------------------------------------------------------------------------
# Store en-memoria (Phase 1 — Phase 3 usará la tabla normative_updates)
# ---------------------------------------------------------------------------

try:
    from agents.actualizador_v2 import (
        ActualizadorV2,
        ActivationRequest,
        ActivationError,
        ImpactLevel,
        UpdateStatus,
        ChangeType,
    )
    _actualizador = ActualizadorV2()
    # Seed con algunas actualizaciones demo para el dashboard
    _actualizador.register_update_manual(
        source_name="hacienda.pr.gov",
        source_url="https://hacienda.pr.gov/noticias-recientes",
        change_type=ChangeType.RATE_CHANGE,
        description="Circular 2026-01: Ajuste tasa IVU municipal al 1.5% efectivo 2026-07-01",
        raw_excerpt=(
            "El Departamento de Hacienda notifica el ajuste de la tasa del IVU municipal "
            "al 1.5% efectivo a partir del 1 de julio de 2026. Los comerciantes deberán "
            "actualizar sus sistemas de punto de venta antes de esa fecha."
        ),
        affected_rule_ids=("IVU.PR.MUNICIPAL.001",),
    )
    _actualizador.register_update_manual(
        source_name="dtrh.pr.gov",
        source_url="https://dtrh.pr.gov/publicaciones",
        change_type=ChangeType.RULE_MODIFICATION,
        description="SUTA 2026: Tasa base ajustada al 2.4% para nuevos empleadores",
        raw_excerpt=(
            "El DTRH notifica que la tasa SUTA base para nuevos empleadores "
            "en Puerto Rico para el año 2026 es de 2.4%. Los empleadores con "
            "historial de desempleo superior al promedio pueden tener tasas más altas."
        ),
        affected_rule_ids=("SUTA.PR.2026.001",),
    )
    _HAS_ACTUALIZADOR = True
except Exception as _exc:
    logger.warning("ActualizadorV2 no disponible en admin routes: %s", _exc)
    _HAS_ACTUALIZADOR = False
    _actualizador = None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class NormativeActivationRequest(BaseModel):
    """Los 3 factores requeridos para activar una actualización normativa."""
    cpa_license:       str = Field(min_length=3, description="Licencia CPA del administrador")
    digital_signature: str = Field(
        min_length=1,
        description="Firma digital hexadecimal (mínimo 16 chars)",
    )
    effective_date:    str = Field(
        description="Fecha de vigencia ISO 8601 (YYYY-MM-DD) — no puede ser pasada",
    )
    interpretation_note: str = Field(
        min_length=20,
        description="Comentario de interpretación (mínimo 20 caracteres)",
    )


class NormativeUpdateSummary(BaseModel):
    update_id:      str
    source_name:    str
    change_type:    str
    impact_level:   str
    sla_hours:      int
    sla_deadline:   str
    description:    str
    status:         str
    detected_at:    str
    requires_human_review: bool


# ---------------------------------------------------------------------------
# GET /admin/normative-updates — cambios pendientes de revisión
# ---------------------------------------------------------------------------

@router.get(
    "/normative-updates",
    response_model=list[NormativeUpdateSummary],
    status_code=status.HTTP_200_OK,
    summary="Listar actualizaciones normativas pendientes de revisión (EXIMIA_ADMIN)",
)
async def list_normative_updates(
    user: TokenUser = Depends(_admin_only),
) -> list[NormativeUpdateSummary]:
    """
    Lista todas las actualizaciones normativas detectadas por el ACTUALIZADOR V2.

    Incluye las pendientes de revisión, bajo revisión y todas las registradas.
    Solo accesible por EXIMIA_ADMIN.
    """
    if not _HAS_ACTUALIZADOR or _actualizador is None:
        return []

    updates = _actualizador.get_all_updates()
    return [
        NormativeUpdateSummary(
            update_id=u.update_id,
            source_name=u.source_name,
            change_type=u.change_type.value,
            impact_level=u.impact_level.value,
            sla_hours=u.sla_hours,
            sla_deadline=u.sla_deadline,
            description=u.description[:300],
            status=u.status.value,
            detected_at=u.detected_at,
            requires_human_review=u.requires_human_review,
        )
        for u in updates
    ]


# ---------------------------------------------------------------------------
# POST /admin/normative-updates/{id}/activate — activar con firma
# ---------------------------------------------------------------------------

@router.post(
    "/normative-updates/{update_id}/activate",
    status_code=status.HTTP_200_OK,
    summary="Activar actualización normativa con firma CPA (EXIMIA_ADMIN)",
)
async def activate_normative_update(
    update_id: str,
    body:      NormativeActivationRequest,
    user:      TokenUser = Depends(_admin_only),
    _rate:     None = Depends(rate_limit("admin:write")),
) -> dict:
    """
    Activa una actualización normativa con los 3 factores requeridos.

    **Los 3 factores son obligatorios y simultáneos:**
    1. `cpa_license` + `digital_signature` (hex ≥16 chars) — identificación CPA
    2. `effective_date` (ISO YYYY-MM-DD, no en el pasado) — fecha de vigencia
    3. `interpretation_note` (≥20 chars) — comentario de interpretación

    Si falta cualquiera de los 3 factores, la activación es rechazada.

    El sistema **NUNCA activa automáticamente** — toda activación requiere
    esta acción explícita del EXIMIA_ADMIN.
    """
    if not _HAS_ACTUALIZADOR or _actualizador is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Motor de actualización normativa no disponible",
        )

    # Construir ActivationRequest con los 3 factores
    try:
        activation = ActivationRequest(
            cpa_license=body.cpa_license,
            digital_signature=body.digital_signature,
            effective_date=body.effective_date,
            interpretation_note=body.interpretation_note,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Datos de activación inválidos: {exc}",
        )

    try:
        approved = _actualizador.activate_update(update_id, activation)
    except ActivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("Error activando update %s: %s", update_id, exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Actualización '{update_id}' no encontrada",
        )

    logger.warning(
        "ADMIN: actualización normativa %s ACTIVADA por %s (%s). Vigencia: %s",
        update_id, user.username, body.cpa_license, body.effective_date,
    )

    return {
        "update_id":           approved.update_id,
        "status":              approved.status.value,
        "approved_by_cpa":     approved.approved_by_cpa,
        "approved_at":         approved.approved_at,
        "effective_date":      approved.effective_date,
        "interpretation_note": approved.interpretation_note,
        "activated_by_user":   user.username,
    }


# ---------------------------------------------------------------------------
# GET /admin/normative-updates/{id} — detalle de una actualización
# ---------------------------------------------------------------------------

@router.get(
    "/normative-updates/{update_id}",
    response_model=NormativeUpdateSummary,
    status_code=status.HTTP_200_OK,
    summary="Detalle de una actualización normativa (EXIMIA_ADMIN)",
)
async def get_normative_update(
    update_id: str,
    user:      TokenUser = Depends(_admin_only),
) -> NormativeUpdateSummary:
    """Retorna el detalle completo de una actualización normativa específica."""
    if not _HAS_ACTUALIZADOR or _actualizador is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Motor de actualización normativa no disponible",
        )

    for u in _actualizador.get_all_updates():
        if u.update_id == update_id:
            return NormativeUpdateSummary(
                update_id=u.update_id,
                source_name=u.source_name,
                change_type=u.change_type.value,
                impact_level=u.impact_level.value,
                sla_hours=u.sla_hours,
                sla_deadline=u.sla_deadline,
                description=u.description,
                status=u.status.value,
                detected_at=u.detected_at,
                requires_human_review=u.requires_human_review,
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Actualización normativa '{update_id}' no encontrada",
    )
