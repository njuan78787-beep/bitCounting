# =============================================================================
# api/routes/normative.py
# Normative monitor endpoints for Bit-Counting.
#
# Monitors 7 Puerto Rico regulatory sources for tax rule changes:
#   1. Departamento de Hacienda de Puerto Rico
#   2. CRIM (Centro de Recaudación de Ingresos Municipales)
#   3. Municipio (local municipal changes)
#   4. Junta de Supervisión Fiscal (PROMESA oversight board)
#   5. US IRS (federal changes affecting PR)
#   6. Tribunal Supremo de PR (court rulings)
#   7. Código de Rentas Internas de PR (statutory updates)
#
# Endpoints:
#   GET  /api/v1/normative/pending-updates          — list pending updates
#   POST /api/v1/normative/updates/{id}/approve     — CPA approves an update
#   POST /api/v1/normative/check-now                — trigger immediate check
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status

from ..schemas import (
    NormativeApproveRequest,
    NormativeUpdateResponse,
    NormativeUpdateStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/normative", tags=["normative"])

# ---------------------------------------------------------------------------
# The 7 monitored normative sources
# ---------------------------------------------------------------------------
NORMATIVE_SOURCES = [
    "Hacienda PR",
    "CRIM",
    "Municipio",
    "Junta de Supervisión Fiscal",
    "US IRS",
    "Tribunal Supremo PR",
    "Código de Rentas Internas PR",
]

# ---------------------------------------------------------------------------
# In-memory normative update store (Phase 1)
# ---------------------------------------------------------------------------
_normative_updates: dict[str, dict] = {}

# Seed demo pending updates
_DEMO_UPDATES = [
    {
        "update_id": "norm-demo-001",
        "source": "Hacienda PR",
        "title": "Actualización Tasa IVU Municipal — Determinación 2026-03",
        "description": (
            "Hacienda PR emitió la Determinación Administrativa 2026-03 modificando "
            "la base imponible para servicios digitales. Aplica a partir del 1ro de mayo 2026."
        ),
        "effective_date": "2026-05-01",
        "detected_at": datetime(2026, 4, 1, 8, 0, 0).isoformat(),
        "status": NormativeUpdateStatus.PENDING,
        "affected_rules": ["IVU-PR-2021-001", "IVU-PR-DIGITAL-001"],
        "approved_by_cpa": None,
        "approved_at": None,
    },
    {
        "update_id": "norm-demo-002",
        "source": "Junta de Supervisión Fiscal",
        "title": "Plan Fiscal 2026 — Revisión de Incentivos Industriales",
        "description": (
            "La Junta de Supervisión Fiscal publicó revisiones al Plan Fiscal 2026 "
            "que afectan los incentivos del Acta 60. Período de comentarios abierto."
        ),
        "effective_date": "2026-07-01",
        "detected_at": datetime(2026, 3, 28, 14, 0, 0).isoformat(),
        "status": NormativeUpdateStatus.PENDING,
        "affected_rules": ["PR-ACT60-INCENTIVE-001"],
        "approved_by_cpa": None,
        "approved_at": None,
    },
    {
        "update_id": "norm-demo-003",
        "source": "CRIM",
        "title": "Tasas de Contribución sobre la Propiedad 2026-2027",
        "description": (
            "CRIM publicó las tasas actualizadas de contribución sobre la propiedad "
            "para el año fiscal 2026-2027."
        ),
        "effective_date": "2026-07-01",
        "detected_at": datetime(2026, 4, 2, 10, 0, 0).isoformat(),
        "status": NormativeUpdateStatus.APPROVED,
        "affected_rules": ["CRIM-PROPERTY-TAX-001"],
        "approved_by_cpa": "CPA-PR-12345",
        "approved_at": datetime(2026, 4, 3, 9, 0, 0).isoformat(),
    },
]
for u in _DEMO_UPDATES:
    _normative_updates[u["update_id"]] = u


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@router.get(
    "/pending-updates",
    response_model=list[NormativeUpdateResponse],
    status_code=status.HTTP_200_OK,
    summary="List pending normative updates requiring CPA review",
)
async def list_pending_updates() -> list[NormativeUpdateResponse]:
    """
    Return all normative updates detected from the 7 monitored sources
    that are still pending CPA approval.
    """
    pending = [
        u for u in _normative_updates.values()
        if u["status"] == NormativeUpdateStatus.PENDING
    ]
    pending.sort(key=lambda u: u.get("detected_at", ""), reverse=True)
    return [_dict_to_update_response(u) for u in pending]


@router.post(
    "/updates/{update_id}/approve",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="CPA approves a normative update, activating the rule change",
)
async def approve_normative_update(
    update_id: str,
    request: NormativeApproveRequest,
) -> dict:
    """
    CPA approves a pending normative update.  Once approved, the updated
    rules become active in the tax_rules registry on or after the effective date.
    """
    if not request.cpa_license or not request.cpa_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="CPA license and token are required",
        )

    update = _normative_updates.get(update_id)
    if update is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Normative update '{update_id}' not found",
        )

    if update["status"] == NormativeUpdateStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Update '{update_id}' has already been approved",
        )

    now = datetime.utcnow()
    update["status"] = NormativeUpdateStatus.APPROVED
    update["approved_by_cpa"] = request.cpa_license
    update["approved_at"] = now.isoformat()
    if request.approval_notes:
        update["approval_notes"] = request.approval_notes

    logger.info(
        "Normative update %s approved by CPA %s", update_id, request.cpa_license
    )

    return {
        "update_id": update_id,
        "status": "approved",
        "approved_by": request.cpa_license,
        "approved_at": now.isoformat(),
        "affected_rules": update.get("affected_rules", []),
        "effective_date": update.get("effective_date"),
        "message": (
            "Actualización normativa aprobada. Las reglas afectadas se "
            f"activarán el {update.get('effective_date', 'fecha indicada')}."
        ),
    }


@router.post(
    "/check-now",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Trigger immediate check of all 7 normative sources",
)
async def check_normative_sources_now() -> dict:
    """
    Trigger an immediate normative check across all 7 PR regulatory sources.
    In Phase 1, this returns a synthetic scan result.
    In Phase 2, this triggers the NORMATIVE monitor agent pipeline.
    """
    # Phase 1: simulate a check run
    check_id = str(uuid.uuid4())
    now = datetime.utcnow()

    # Simulate finding one new update
    new_update_id = f"norm-scan-{check_id[:8]}"
    _normative_updates[new_update_id] = {
        "update_id": new_update_id,
        "source": "Código de Rentas Internas PR",
        "title": f"Verificación automática — {now.strftime('%Y-%m-%d %H:%M')} UTC",
        "description": (
            "Verificación programática de cambios en el Código de Rentas Internas de PR. "
            "No se detectaron cambios materiales en esta verificación."
        ),
        "effective_date": None,
        "detected_at": now.isoformat(),
        "status": NormativeUpdateStatus.APPROVED,  # Auto-approved if no material changes
        "affected_rules": [],
        "approved_by_cpa": None,
        "approved_at": None,
    }

    sources_checked = [
        {"source": src, "status": "checked", "new_updates": 0}
        for src in NORMATIVE_SOURCES
    ]
    sources_checked[-1]["new_updates"] = 1  # Simulate 1 finding on last source

    logger.info("Normative check %s completed across all 7 sources", check_id)

    return {
        "check_id": check_id,
        "checked_at": now.isoformat(),
        "sources_checked": len(NORMATIVE_SOURCES),
        "sources": sources_checked,
        "new_updates_detected": 1,
        "message": (
            "Verificación completada. Se revisaron todas las 7 fuentes normativas. "
            "Revise /pending-updates para ver cualquier nuevo cambio detectado."
        ),
    }


# ---------------------------------------------------------------------------
# SPEC-REQUIRED ALIASES
# The spec uses /normative/updates and /normative/check; existing endpoints
# are /pending-updates and /check-now.  These aliases delegate to the same
# handlers for full compatibility.
# ---------------------------------------------------------------------------

@router.get(
    "/updates",
    response_model=list[NormativeUpdateResponse],
    status_code=status.HTTP_200_OK,
    summary="List pending normative update proposals (spec alias for /pending-updates)",
)
async def list_updates() -> list[NormativeUpdateResponse]:
    """Alias for GET /pending-updates — returns all pending normative updates."""
    return await list_pending_updates()


@router.post(
    "/check",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Trigger a normative check for today's date (spec alias for /check-now)",
)
async def check_normative() -> dict:
    """Alias for POST /check-now — triggers an immediate normative source check."""
    return await check_normative_sources_now()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _dict_to_update_response(u: dict) -> NormativeUpdateResponse:
    detected_at = datetime.utcnow()
    if u.get("detected_at"):
        try:
            detected_at = datetime.fromisoformat(u["detected_at"])
        except ValueError:
            pass
    approved_at = None
    if u.get("approved_at"):
        try:
            approved_at = datetime.fromisoformat(u["approved_at"])
        except ValueError:
            pass
    return NormativeUpdateResponse(
        update_id=u["update_id"],
        source=u["source"],
        title=u["title"],
        description=u["description"],
        effective_date=u.get("effective_date"),
        detected_at=detected_at,
        status=u["status"],
        affected_rules=u.get("affected_rules", []),
        approved_by_cpa=u.get("approved_by_cpa"),
        approved_at=approved_at,
    )
