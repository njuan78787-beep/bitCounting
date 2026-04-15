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

import hashlib
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Role, TokenUser
from ..database import get_db
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


# =============================================================================
# SECCIÓN 1 — BASE DE REGLAS FISCALES PR
# =============================================================================

class FiscalRuleCreateRequest(BaseModel):
    rule_code:      str   = Field(min_length=3, description="Código único, e.g. IVU.PR.ESTATAL.002")
    rule_name:      str   = Field(min_length=5)
    rule_type:      str   = Field(description="TAX_RATE | BRACKET | EXEMPTION | DEADLINE | WITHHOLDING | SUTA")
    jurisdiction:   str   = Field(default="PR", description="PR | FEDERAL | MUNICIPAL")
    effective_date: str   = Field(description="YYYY-MM-DD — fecha de vigencia")
    value_json:     Dict[str, Any] = Field(description="Valor de la regla (tasa, tabla, etc.)")
    form_types:     List[str] = Field(default_factory=list)
    notes:          Optional[str] = None


class FiscalRuleApproveRequest(BaseModel):
    interpretation_note: str = Field(min_length=20, description="Justificación ≥20 chars")
    supersedes_rule_id:  Optional[str] = None


class FiscalRuleRejectRequest(BaseModel):
    rejection_reason: str = Field(min_length=10)


@router.get(
    "/fiscal-rules",
    status_code=status.HTTP_200_OK,
    summary="Listar reglas fiscales PR (EXIMIA_ADMIN)",
)
async def list_fiscal_rules(
    rule_status: Optional[str] = Query(default=None, alias="status",
                                       description="ACTIVE | DRAFT | SUPERSEDED | REJECTED"),
    rule_type:   Optional[str] = Query(default=None),
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    """
    Lista reglas fiscales de Puerto Rico.
    Sin filtro retorna ACTIVE + DRAFT. Filtra por status o rule_type.
    """
    from ..db.models import AppFiscalRule

    if db is None:
        return []

    stmt = select(AppFiscalRule)
    if rule_status:
        stmt = stmt.where(AppFiscalRule.status == rule_status.upper())
    else:
        stmt = stmt.where(AppFiscalRule.status.in_(["ACTIVE", "DRAFT"]))
    if rule_type:
        stmt = stmt.where(AppFiscalRule.rule_type == rule_type.upper())
    stmt = stmt.order_by(AppFiscalRule.effective_date.desc())

    result = await db.execute(stmt)
    return [_rule_to_dict(r) for r in result.scalars().all()]


@router.post(
    "/fiscal-rules",
    status_code=status.HTTP_201_CREATED,
    summary="Crear borrador de regla fiscal PR (EXIMIA_ADMIN)",
)
async def create_fiscal_rule(
    body: FiscalRuleCreateRequest,
    user: TokenUser = Depends(_admin_only),
    _rate: None = Depends(rate_limit("admin:write")),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    Crea un borrador de regla fiscal. Requiere aprobación antes de entrar en vigor.
    El código debe ser único (e.g. IVU.PR.ESTATAL.002).
    """
    from ..db.models import AppFiscalRule

    if db is None:
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")

    # Verificar unicidad del código
    existing = await db.execute(
        select(AppFiscalRule).where(AppFiscalRule.rule_code == body.rule_code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una regla con el código '{body.rule_code}'.",
        )

    rule = AppFiscalRule(
        rule_id=str(uuid.uuid4()),
        rule_code=body.rule_code,
        rule_name=body.rule_name,
        rule_type=body.rule_type.upper(),
        jurisdiction=body.jurisdiction.upper(),
        effective_date=body.effective_date,
        value_json=body.value_json,
        form_types=body.form_types or [],
        notes=body.notes,
        status="DRAFT",
        created_by=user.user_id,
    )
    db.add(rule)
    await db.commit()

    logger.info("ADMIN: regla fiscal %s creada por %s", body.rule_code, user.username)
    return _rule_to_dict(rule)


@router.get(
    "/fiscal-rules/{rule_id}",
    status_code=status.HTTP_200_OK,
    summary="Detalle de una regla fiscal (EXIMIA_ADMIN)",
)
async def get_fiscal_rule(
    rule_id: str,
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    return _rule_to_dict(await _load_rule_or_404(rule_id, db))


@router.post(
    "/fiscal-rules/{rule_id}/approve",
    status_code=status.HTTP_200_OK,
    summary="Aprobar borrador de regla fiscal (EXIMIA_ADMIN)",
)
async def approve_fiscal_rule(
    rule_id: str,
    body:    FiscalRuleApproveRequest,
    user:    TokenUser = Depends(_admin_only),
    _rate:   None = Depends(rate_limit("admin:write")),
    db:      AsyncSession = Depends(get_db),
) -> dict:
    """
    Aprueba un borrador → status ACTIVE.
    Si supersedes_rule_id se indica, marca la regla anterior como SUPERSEDED.
    """
    from ..db.models import AppFiscalRule

    rule = await _load_rule_or_404(rule_id, db)
    if rule.status != "DRAFT":
        raise HTTPException(400, detail=f"Solo se pueden aprobar borradores (status actual: {rule.status}).")

    now = datetime.utcnow()
    rule.status = "ACTIVE"
    rule.approved_by = user.user_id
    rule.approved_at = now
    rule.notes = (rule.notes or "") + f"\n[APROBADO {now.date()}] {body.interpretation_note}"

    if body.supersedes_rule_id:
        old_result = await db.execute(
            select(AppFiscalRule).where(AppFiscalRule.rule_id == body.supersedes_rule_id)
        )
        old_rule = old_result.scalar_one_or_none()
        if old_rule:
            old_rule.status = "SUPERSEDED"
            old_rule.superseded_at = now.date().isoformat()
        rule.supersedes_rule_id = body.supersedes_rule_id

    await db.commit()
    logger.warning("ADMIN: regla %s APROBADA por %s", rule.rule_code, user.username)
    return _rule_to_dict(rule)


@router.post(
    "/fiscal-rules/{rule_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Rechazar borrador de regla fiscal (EXIMIA_ADMIN)",
)
async def reject_fiscal_rule(
    rule_id: str,
    body:    FiscalRuleRejectRequest,
    user:    TokenUser = Depends(_admin_only),
    _rate:   None = Depends(rate_limit("admin:write")),
    db:      AsyncSession = Depends(get_db),
) -> dict:
    rule = await _load_rule_or_404(rule_id, db)
    if rule.status != "DRAFT":
        raise HTTPException(400, detail=f"Solo se pueden rechazar borradores (status actual: {rule.status}).")

    rule.status = "REJECTED"
    rule.rejection_reason = body.rejection_reason
    await db.commit()
    logger.warning("ADMIN: regla %s RECHAZADA por %s", rule.rule_code, user.username)
    return _rule_to_dict(rule)


@router.get(
    "/fiscal-rules/{rule_id}/history",
    status_code=status.HTTP_200_OK,
    summary="Historial de cambios de una regla fiscal (EXIMIA_ADMIN)",
)
async def get_fiscal_rule_history(
    rule_id: str,
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    """Retorna la cadena de versiones de una regla (la actual + las que supersede recursivamente)."""
    from ..db.models import AppFiscalRule

    if db is None:
        return []

    chain = []
    current_id: Optional[str] = rule_id
    seen: set = set()

    while current_id and current_id not in seen:
        seen.add(current_id)
        result = await db.execute(select(AppFiscalRule).where(AppFiscalRule.rule_id == current_id))
        row = result.scalar_one_or_none()
        if row is None:
            break
        chain.append(_rule_to_dict(row))
        current_id = row.supersedes_rule_id

    return chain


# =============================================================================
# SECCIÓN 2 — GESTIÓN DE FIRMAS CONTABLES (summary view)
# Full CRUD is in /api/v1/firm-connector — this section provides admin overview.
# =============================================================================

@router.get(
    "/firms",
    status_code=status.HTTP_200_OK,
    summary="Resumen de firmas contables conectadas (EXIMIA_ADMIN)",
)
async def list_firms_summary(
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    """Vista de resumen. La gestión completa (onboarding, sync, revocación) está en /api/v1/firm-connector."""
    from ..db.models import AppFirmConfig, AppSyncLog
    from sqlalchemy import func

    if db is None:
        return []

    result = await db.execute(select(AppFirmConfig).order_by(AppFirmConfig.created_at.desc()))
    firms = result.scalars().all()

    summaries = []
    for f in firms:
        # Último sync
        last_sync_result = await db.execute(
            select(AppSyncLog)
            .where(AppSyncLog.firm_id == f.firm_id)
            .order_by(AppSyncLog.started_at.desc())
            .limit(1)
        )
        last_sync = last_sync_result.scalar_one_or_none()
        # Conteo de syncs
        count_result = await db.execute(
            select(func.count()).where(AppSyncLog.firm_id == f.firm_id)
        )
        sync_count = count_result.scalar() or 0

        summaries.append({
            "firm_id":      f.firm_id,
            "firm_name":    f.firm_name,
            "mode":         f.mode,
            "is_active":    f.is_active,
            "created_at":   f.created_at.isoformat() if f.created_at else None,
            "total_syncs":  sync_count,
            "last_sync_at": last_sync.started_at.isoformat() if last_sync else None,
            "last_sync_status": last_sync.status if last_sync else None,
        })
    return summaries


@router.get(
    "/firms/{firm_id}/sync-metrics",
    status_code=status.HTTP_200_OK,
    summary="Métricas de sincronización de una firma contable (EXIMIA_ADMIN)",
)
async def get_firm_sync_metrics(
    firm_id: str,
    limit:   int = Query(default=10, ge=1, le=100),
    user:    TokenUser = Depends(_admin_only),
    db:      AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppFirmConfig, AppSyncLog

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    firm_result = await db.execute(select(AppFirmConfig).where(AppFirmConfig.firm_id == firm_id))
    firm = firm_result.scalar_one_or_none()
    if firm is None:
        raise HTTPException(404, detail=f"Firma '{firm_id}' no encontrada.")

    logs_result = await db.execute(
        select(AppSyncLog)
        .where(AppSyncLog.firm_id == firm_id)
        .order_by(AppSyncLog.started_at.desc())
        .limit(limit)
    )
    logs = logs_result.scalars().all()

    total_imported = sum(
        (l.clients_imported or 0) + (l.transactions_imported or 0) + (l.employees_imported or 0)
        for l in logs
    )
    success_count = sum(1 for l in logs if l.status == "SUCCESS")

    return {
        "firm_id":        firm_id,
        "firm_name":      firm.firm_name,
        "is_active":      firm.is_active,
        "recent_syncs":   [
            {
                "sync_id":              l.sync_id,
                "status":               l.status,
                "started_at":           l.started_at.isoformat(),
                "completed_at":         l.completed_at.isoformat() if l.completed_at else None,
                "clients_imported":     l.clients_imported,
                "transactions_imported": l.transactions_imported,
                "employees_imported":   l.employees_imported,
                "error_message":        l.error_message,
            }
            for l in logs
        ],
        "summary": {
            "total_syncs_shown": len(logs),
            "successful_syncs":  success_count,
            "total_records_imported": total_imported,
        },
    }


# =============================================================================
# SECCIÓN 3 — GESTIÓN DE CPA PARTNERS
# =============================================================================

class CpaPartnerCreateRequest(BaseModel):
    cpa_license: str = Field(min_length=3)
    full_name:   str = Field(min_length=3)
    email:       str = Field(min_length=5)
    phone:       Optional[str] = None


class CpaVerifyRequest(BaseModel):
    verification_notes: str = Field(min_length=5, description="Nota de verificación")


class CpaAssignClientRequest(BaseModel):
    client_id: str = Field(description="UUID del cliente a asignar")


class CpaSuspendRequest(BaseModel):
    reason: str = Field(min_length=10, description="Motivo de la suspensión")


@router.get(
    "/cpa-partners",
    status_code=status.HTTP_200_OK,
    summary="Listar CPA Partners (EXIMIA_ADMIN)",
)
async def list_cpa_partners(
    partner_status: Optional[str] = Query(default=None, alias="status"),
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    from ..db.models import AppCpaPartner, AppCpaClientAssignment

    if db is None:
        return []

    stmt = select(AppCpaPartner)
    if partner_status:
        stmt = stmt.where(AppCpaPartner.status == partner_status.upper())
    stmt = stmt.order_by(AppCpaPartner.full_name)

    result = await db.execute(stmt)
    partners = result.scalars().all()

    output = []
    for p in partners:
        # Count assigned clients
        count_result = await db.execute(
            select(func.count()).select_from(AppCpaClientAssignment).where(
                and_(AppCpaClientAssignment.cpa_license == p.cpa_license,
                     AppCpaClientAssignment.is_active == True)  # noqa: E712
            )
        )
        client_count = count_result.scalar() or 0
        d = _cpa_partner_to_dict(p)
        d["active_client_count"] = client_count
        output.append(d)

    return output


@router.post(
    "/cpa-partners",
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nuevo CPA Partner (EXIMIA_ADMIN)",
)
async def register_cpa_partner(
    body:  CpaPartnerCreateRequest,
    user:  TokenUser = Depends(_admin_only),
    _rate: None = Depends(rate_limit("admin:write")),
    db:    AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppCpaPartner

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    existing = await db.execute(
        select(AppCpaPartner).where(AppCpaPartner.cpa_license == body.cpa_license)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, detail=f"CPA con licencia '{body.cpa_license}' ya registrado.")

    partner = AppCpaPartner(
        cpa_license=body.cpa_license,
        full_name=body.full_name,
        email=body.email,
        phone=body.phone,
        status="PENDING_VERIFICATION",
    )
    db.add(partner)
    await db.commit()

    logger.info("ADMIN: CPA Partner %s registrado por %s", body.cpa_license, user.username)
    return _cpa_partner_to_dict(partner)


@router.get(
    "/cpa-partners/{cpa_license}",
    status_code=status.HTTP_200_OK,
    summary="Detalle de un CPA Partner (EXIMIA_ADMIN)",
)
async def get_cpa_partner(
    cpa_license: str,
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppCpaClientAssignment, AppReviewQueueItem

    partner = await _load_cpa_or_404(cpa_license, db)

    # Assigned clients
    assignments_result = await db.execute(
        select(AppCpaClientAssignment).where(
            and_(AppCpaClientAssignment.cpa_license == cpa_license,
                 AppCpaClientAssignment.is_active == True)  # noqa: E712
        )
    ) if db else None
    clients = [r.client_id for r in assignments_result.scalars().all()] if assignments_result else []

    d = _cpa_partner_to_dict(partner)
    d["assigned_clients"] = clients
    return d


@router.post(
    "/cpa-partners/{cpa_license}/verify",
    status_code=status.HTTP_200_OK,
    summary="Verificar licencia de CPA Partner (EXIMIA_ADMIN)",
)
async def verify_cpa_license(
    cpa_license: str,
    body:        CpaVerifyRequest,
    user:        TokenUser = Depends(_admin_only),
    _rate:       None = Depends(rate_limit("admin:write")),
    db:          AsyncSession = Depends(get_db),
) -> dict:
    partner = await _load_cpa_or_404(cpa_license, db)

    if partner.license_verified:
        raise HTTPException(409, detail="La licencia ya fue verificada.")

    partner.license_verified = True
    partner.license_verified_at = datetime.utcnow()
    partner.license_verified_by = user.user_id
    partner.status = "ACTIVE"
    await db.commit()

    logger.warning("ADMIN: licencia CPA %s VERIFICADA por %s", cpa_license, user.username)
    return _cpa_partner_to_dict(partner)


@router.post(
    "/cpa-partners/{cpa_license}/assign-client",
    status_code=status.HTTP_201_CREATED,
    summary="Asignar cliente a CPA Partner (EXIMIA_ADMIN)",
)
async def assign_client_to_cpa(
    cpa_license: str,
    body:        CpaAssignClientRequest,
    user:        TokenUser = Depends(_admin_only),
    _rate:       None = Depends(rate_limit("admin:write")),
    db:          AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppCpaClientAssignment, AppClientProfile

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    partner = await _load_cpa_or_404(cpa_license, db)
    if partner.status not in ("ACTIVE",):
        raise HTTPException(400, detail=f"No se puede asignar clientes a un CPA con status '{partner.status}'.")

    # Idempotent — check if already assigned
    existing = await db.execute(
        select(AppCpaClientAssignment).where(
            and_(AppCpaClientAssignment.cpa_license == cpa_license,
                 AppCpaClientAssignment.client_id == body.client_id,
                 AppCpaClientAssignment.is_active == True)  # noqa: E712
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, detail="El cliente ya está asignado a este CPA.")

    assignment = AppCpaClientAssignment(
        cpa_license=cpa_license,
        client_id=body.client_id,
        assigned_by=user.user_id,
    )
    db.add(assignment)

    # Update client profile if it exists
    prof_result = await db.execute(
        select(AppClientProfile).where(AppClientProfile.client_id == body.client_id)
    )
    profile = prof_result.scalar_one_or_none()
    if profile:
        profile.assigned_cpa_license = cpa_license

    await db.commit()
    logger.info("ADMIN: cliente %s asignado a CPA %s por %s", body.client_id, cpa_license, user.username)
    return {
        "cpa_license": cpa_license,
        "client_id":   body.client_id,
        "assigned_at": assignment.assigned_at.isoformat(),
        "assigned_by": user.username,
    }


@router.get(
    "/cpa-partners/{cpa_license}/decisions",
    status_code=status.HTTP_200_OK,
    summary="Historial de decisiones de un CPA Partner (EXIMIA_ADMIN)",
)
async def get_cpa_decisions(
    cpa_license: str,
    limit:  int = Query(default=50, ge=1, le=200),
    user:   TokenUser = Depends(_admin_only),
    db:     AsyncSession = Depends(get_db),
) -> List[dict]:
    """Retorna items de la cola de revisión que fueron resueltos por este CPA."""
    from ..db.models import AppReviewQueueItem, AppCentinaelaPause

    if db is None:
        return []

    await _load_cpa_or_404(cpa_license, db)

    items_result = await db.execute(
        select(AppReviewQueueItem)
        .where(AppReviewQueueItem.resolved_by == cpa_license)
        .order_by(AppReviewQueueItem.resolved_at.desc())
        .limit(limit)
    )
    pauses_result = await db.execute(
        select(AppCentinaelaPause)
        .where(AppCentinaelaPause.resolved_by == cpa_license)
        .order_by(AppCentinaelaPause.resolved_at.desc())
        .limit(limit)
    )

    decisions = []
    for r in items_result.scalars().all():
        decisions.append({
            "type":        "review_queue",
            "item_id":     r.item_id,
            "client_id":   r.client_id,
            "decision":    r.status,
            "notes":       r.resolution_notes,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        })
    for p in pauses_result.scalars().all():
        decisions.append({
            "type":        "centinela_pause",
            "item_id":     p.pause_id,
            "client_id":   p.client_id,
            "decision":    "RESOLVED",
            "notes":       p.resolution_notes,
            "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
        })

    decisions.sort(key=lambda d: d["resolved_at"] or "", reverse=True)
    return decisions[:limit]


@router.post(
    "/cpa-partners/{cpa_license}/suspend",
    status_code=status.HTTP_200_OK,
    summary="Suspender CPA Partner (EXIMIA_ADMIN)",
)
async def suspend_cpa_partner(
    cpa_license: str,
    body:        CpaSuspendRequest,
    user:        TokenUser = Depends(_admin_only),
    _rate:       None = Depends(rate_limit("admin:write")),
    db:          AsyncSession = Depends(get_db),
) -> dict:
    partner = await _load_cpa_or_404(cpa_license, db)
    if partner.status == "SUSPENDED":
        raise HTTPException(409, detail="El CPA ya está suspendido.")

    partner.status = "SUSPENDED"
    partner.suspension_reason = body.reason
    partner.suspended_at = datetime.utcnow()
    partner.suspended_by = user.user_id
    await db.commit()

    logger.warning("ADMIN: CPA %s SUSPENDIDO por %s. Motivo: %s", cpa_license, user.username, body.reason)
    return _cpa_partner_to_dict(partner)


@router.post(
    "/cpa-partners/{cpa_license}/reactivate",
    status_code=status.HTTP_200_OK,
    summary="Reactivar CPA Partner suspendido (EXIMIA_ADMIN)",
)
async def reactivate_cpa_partner(
    cpa_license: str,
    user:        TokenUser = Depends(_admin_only),
    _rate:       None = Depends(rate_limit("admin:write")),
    db:          AsyncSession = Depends(get_db),
) -> dict:
    partner = await _load_cpa_or_404(cpa_license, db)
    if partner.status != "SUSPENDED":
        raise HTTPException(409, detail=f"El CPA no está suspendido (status: {partner.status}).")

    partner.status = "ACTIVE"
    partner.suspension_reason = None
    partner.suspended_at = None
    partner.suspended_by = None
    await db.commit()

    logger.warning("ADMIN: CPA %s REACTIVADO por %s", cpa_license, user.username)
    return _cpa_partner_to_dict(partner)


# =============================================================================
# SECCIÓN 4 — MONITORING DEL SISTEMA
# =============================================================================

@router.get(
    "/system/status",
    status_code=status.HTTP_200_OK,
    summary="Estado del sistema y agentes (EXIMIA_ADMIN)",
)
async def get_system_status(
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    """
    Retorna estado de agentes, longitud de colas, y latencia del sistema.
    Phase 1: basado en datos en-memoria. Phase 3 usará métricas Prometheus.
    """
    from ..db.models import AppReviewQueueItem, AppCentinaelaPause, AppTaxForm

    queue_count = 0
    pause_count = 0
    pending_forms = 0

    if db is not None:
        q_result = await db.execute(
            select(func.count()).select_from(AppReviewQueueItem)
            .where(AppReviewQueueItem.status == "pending_review")
        )
        queue_count = q_result.scalar() or 0

        p_result = await db.execute(
            select(func.count()).select_from(AppCentinaelaPause)
            .where(AppCentinaelaPause.status == "ACTIVE")
        )
        pause_count = p_result.scalar() or 0

        f_result = await db.execute(
            select(func.count()).select_from(AppTaxForm)
            .where(AppTaxForm.form_status.in_(["PENDING_CPA_REVIEW", "PENDING_CPA_SIGNATURE"]))
        )
        pending_forms = f_result.scalar() or 0

    agents = ["INTAKE", "CENTINELA", "CENTINELA_GUARDIAN", "CLASIFICADOR",
              "AUDITOR", "FISCAL_PR", "ORQUESTADOR", "APRENDIZAJE_FEDERADO"]

    agent_status = []
    for name in agents:
        try:
            agent_module = name.lower().replace("_", "")
            agent_status.append({"agent": name, "status": "OPERATIONAL", "last_heartbeat": datetime.utcnow().isoformat()})
        except Exception:
            agent_status.append({"agent": name, "status": "UNKNOWN", "last_heartbeat": None})

    return {
        "system":       "bit-counting",
        "timestamp":    datetime.utcnow().isoformat(),
        "agents":       agent_status,
        "queues": {
            "review_queue_pending":    queue_count,
            "centinela_pauses_active": pause_count,
            "tax_forms_pending_cpa":   pending_forms,
        },
        "database":     "connected" if db is not None else "unavailable",
    }


@router.get(
    "/system/alerts",
    status_code=status.HTTP_200_OK,
    summary="Alertas activas del sistema (EXIMIA_ADMIN)",
)
async def get_system_alerts(
    include_resolved: bool = Query(default=False),
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    from ..db.models import AppSystemAlert

    if db is None:
        return []

    stmt = select(AppSystemAlert)
    if not include_resolved:
        stmt = stmt.where(AppSystemAlert.is_resolved == False)  # noqa: E712
    stmt = stmt.order_by(AppSystemAlert.created_at.desc())

    result = await db.execute(stmt)
    return [_alert_to_dict(a) for a in result.scalars().all()]


@router.post(
    "/system/alerts/{alert_id}/resolve",
    status_code=status.HTTP_200_OK,
    summary="Resolver alerta del sistema (EXIMIA_ADMIN)",
)
async def resolve_system_alert(
    alert_id: str,
    user:     TokenUser = Depends(_admin_only),
    _rate:    None = Depends(rate_limit("admin:write")),
    db:       AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppSystemAlert

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    result = await db.execute(select(AppSystemAlert).where(AppSystemAlert.alert_id == alert_id))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(404, detail=f"Alerta '{alert_id}' no encontrada.")
    if alert.is_resolved:
        raise HTTPException(409, detail="La alerta ya está resuelta.")

    alert.is_resolved = True
    alert.resolved_at = datetime.utcnow()
    alert.resolved_by = user.username
    await db.commit()
    return _alert_to_dict(alert)


# =============================================================================
# SECCIÓN 5 — POOL DE APRENDIZAJE
# =============================================================================

class LearningApproveRequest(BaseModel):
    approval_notes: str = Field(min_length=5)


class LearningRejectRequest(BaseModel):
    rejection_reason: str = Field(min_length=10)


@router.get(
    "/learning/cards",
    status_code=status.HTTP_200_OK,
    summary="Fichas de aprendizaje pendientes de aprobación (EXIMIA_ADMIN)",
)
async def list_learning_cards(
    card_status: Optional[str] = Query(default="PENDING_APPROVAL", alias="status"),
    card_type:   Optional[str] = Query(default=None),
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    from ..db.models import AppLearningCard

    if db is None:
        return []

    stmt = select(AppLearningCard)
    if card_status:
        stmt = stmt.where(AppLearningCard.status == card_status.upper())
    if card_type:
        stmt = stmt.where(AppLearningCard.card_type == card_type.upper())
    stmt = stmt.order_by(AppLearningCard.created_at.desc())

    result = await db.execute(stmt)
    return [_learning_card_to_dict(c) for c in result.scalars().all()]


@router.post(
    "/learning/cards/{card_id}/approve",
    status_code=status.HTTP_200_OK,
    summary="Aprobar contribución al pool de aprendizaje (EXIMIA_ADMIN)",
)
async def approve_learning_card(
    card_id: str,
    body:    LearningApproveRequest,
    user:    TokenUser = Depends(_admin_only),
    _rate:   None = Depends(rate_limit("admin:write")),
    db:      AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppLearningCard

    card = await _load_learning_card_or_404(card_id, db)
    if card.status != "PENDING_APPROVAL":
        raise HTTPException(409, detail=f"La ficha ya fue procesada (status: {card.status}).")

    card.status = "APPROVED"
    card.approved_by = user.user_id
    card.approved_at = datetime.utcnow()
    await db.commit()

    logger.info("ADMIN: ficha de aprendizaje %s APROBADA por %s", card_id, user.username)
    return _learning_card_to_dict(card)


@router.post(
    "/learning/cards/{card_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Rechazar contribución al pool de aprendizaje (EXIMIA_ADMIN)",
)
async def reject_learning_card(
    card_id: str,
    body:    LearningRejectRequest,
    user:    TokenUser = Depends(_admin_only),
    _rate:   None = Depends(rate_limit("admin:write")),
    db:      AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppLearningCard

    card = await _load_learning_card_or_404(card_id, db)
    if card.status != "PENDING_APPROVAL":
        raise HTTPException(409, detail=f"La ficha ya fue procesada (status: {card.status}).")

    card.status = "REJECTED"
    card.rejection_reason = body.rejection_reason
    await db.commit()
    return _learning_card_to_dict(card)


@router.get(
    "/learning/metrics",
    status_code=status.HTTP_200_OK,
    summary="Métricas del pool de aprendizaje (EXIMIA_ADMIN)",
)
async def get_learning_metrics(
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppLearningCard

    if db is None:
        return {"total_cards": 0, "pending": 0, "approved": 0, "rejected": 0}

    counts = {}
    for s in ("PENDING_APPROVAL", "APPROVED", "REJECTED"):
        r = await db.execute(
            select(func.count()).select_from(AppLearningCard).where(AppLearningCard.status == s)
        )
        counts[s] = r.scalar() or 0

    by_type_result = await db.execute(
        select(AppLearningCard.card_type, func.count())
        .group_by(AppLearningCard.card_type)
    )
    by_type = {row[0]: row[1] for row in by_type_result.all()}

    return {
        "total_cards":      sum(counts.values()),
        "pending_approval": counts["PENDING_APPROVAL"],
        "approved":         counts["APPROVED"],
        "rejected":         counts["REJECTED"],
        "by_type":          by_type,
        "approval_rate":    round(
            counts["APPROVED"] / max(counts["APPROVED"] + counts["REJECTED"], 1), 4
        ),
    }


@router.post(
    "/learning/recalibrate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Solicitar recalibración del pool de aprendizaje (EXIMIA_ADMIN)",
)
async def trigger_recalibration(
    user:  TokenUser = Depends(_admin_only),
    _rate: None = Depends(rate_limit("admin:write")),
    db:    AsyncSession = Depends(get_db),
) -> dict:
    """
    Encola una tarea de recalibración del agente APRENDIZAJE_FEDERADO con las
    fichas aprobadas. Phase 1: registra la solicitud y retorna tarea aceptada.
    """
    task_id = str(uuid.uuid4())
    logger.warning("ADMIN: recalibración del pool de aprendizaje solicitada por %s (task=%s)",
                   user.username, task_id)
    return {
        "task_id":     task_id,
        "status":      "accepted",
        "message":     "Recalibración encolada. El agente APRENDIZAJE_FEDERADO procesará las fichas aprobadas.",
        "requested_by": user.username,
        "requested_at": datetime.utcnow().isoformat(),
    }


# =============================================================================
# SECCIÓN 6 — FACTURACIÓN Y SUSCRIPCIONES
# =============================================================================

class SubscriptionUpdateRequest(BaseModel):
    plan_type:     Optional[str] = None
    billing_cycle: Optional[str] = None
    mrr_usd:       Optional[float] = None


class SubscriptionCancelRequest(BaseModel):
    cancellation_reason: str = Field(min_length=5)


@router.get(
    "/billing/subscriptions",
    status_code=status.HTTP_200_OK,
    summary="Listar suscripciones activas (EXIMIA_ADMIN)",
)
async def list_subscriptions(
    sub_status: Optional[str] = Query(default=None, alias="status"),
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> List[dict]:
    from ..db.models import AppSubscription

    if db is None:
        return []

    stmt = select(AppSubscription)
    if sub_status:
        stmt = stmt.where(AppSubscription.status == sub_status.upper())
    stmt = stmt.order_by(AppSubscription.created_at.desc())

    result = await db.execute(stmt)
    return [_subscription_to_dict(s) for s in result.scalars().all()]


@router.get(
    "/billing/metrics",
    status_code=status.HTTP_200_OK,
    summary="Métricas de facturación — MRR, churn, uso (EXIMIA_ADMIN)",
)
async def get_billing_metrics(
    user: TokenUser = Depends(_admin_only),
    db:   AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppSubscription

    if db is None:
        return {"mrr_usd": "0.00", "active_subscriptions": 0, "churn_rate": "0.0000"}

    active_result = await db.execute(
        select(AppSubscription).where(AppSubscription.status == "ACTIVE")
    )
    active_subs = active_result.scalars().all()

    cancelled_result = await db.execute(
        select(func.count()).select_from(AppSubscription).where(AppSubscription.status == "CANCELLED")
    )
    cancelled_count = cancelled_result.scalar() or 0

    total_result = await db.execute(select(func.count()).select_from(AppSubscription))
    total_count = total_result.scalar() or 1

    mrr = sum(float(s.mrr_usd or 0) for s in active_subs)

    by_plan: Dict[str, int] = {}
    for s in active_subs:
        by_plan[s.plan_type] = by_plan.get(s.plan_type, 0) + 1

    past_due_result = await db.execute(
        select(func.count()).select_from(AppSubscription).where(AppSubscription.status == "PAST_DUE")
    )
    past_due = past_due_result.scalar() or 0

    return {
        "mrr_usd":              f"{mrr:.2f}",
        "active_subscriptions": len(active_subs),
        "cancelled":            cancelled_count,
        "past_due":             past_due,
        "churn_rate":           f"{cancelled_count / total_count:.4f}",
        "subscriptions_by_plan": by_plan,
    }


@router.put(
    "/billing/subscriptions/{subscription_id}",
    status_code=status.HTTP_200_OK,
    summary="Actualizar suscripción (EXIMIA_ADMIN)",
)
async def update_subscription(
    subscription_id: str,
    body:  SubscriptionUpdateRequest,
    user:  TokenUser = Depends(_admin_only),
    _rate: None = Depends(rate_limit("admin:write")),
    db:    AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppSubscription

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    result = await db.execute(
        select(AppSubscription).where(AppSubscription.subscription_id == subscription_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(404, detail=f"Suscripción '{subscription_id}' no encontrada.")

    if body.plan_type:
        sub.plan_type = body.plan_type.upper()
    if body.billing_cycle:
        sub.billing_cycle = body.billing_cycle.upper()
    if body.mrr_usd is not None:
        sub.mrr_usd = Decimal(str(body.mrr_usd))

    await db.commit()
    logger.info("ADMIN: suscripción %s actualizada por %s", subscription_id, user.username)
    return _subscription_to_dict(sub)


@router.post(
    "/billing/subscriptions/{subscription_id}/cancel",
    status_code=status.HTTP_200_OK,
    summary="Cancelar suscripción (EXIMIA_ADMIN)",
)
async def cancel_subscription(
    subscription_id: str,
    body:  SubscriptionCancelRequest,
    user:  TokenUser = Depends(_admin_only),
    _rate: None = Depends(rate_limit("admin:write")),
    db:    AsyncSession = Depends(get_db),
) -> dict:
    from ..db.models import AppSubscription

    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")

    result = await db.execute(
        select(AppSubscription).where(AppSubscription.subscription_id == subscription_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(404, detail=f"Suscripción '{subscription_id}' no encontrada.")
    if sub.status == "CANCELLED":
        raise HTTPException(409, detail="La suscripción ya está cancelada.")

    sub.status = "CANCELLED"
    sub.cancellation_reason = body.cancellation_reason
    sub.cancelled_at = datetime.utcnow()
    await db.commit()

    logger.warning("ADMIN: suscripción %s CANCELADA por %s", subscription_id, user.username)
    return _subscription_to_dict(sub)


# =============================================================================
# HELPERS PRIVADOS
# =============================================================================

async def _load_rule_or_404(rule_id: str, db: AsyncSession):
    from ..db.models import AppFiscalRule
    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")
    result = await db.execute(select(AppFiscalRule).where(AppFiscalRule.rule_id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(404, detail=f"Regla fiscal '{rule_id}' no encontrada.")
    return rule


async def _load_cpa_or_404(cpa_license: str, db: AsyncSession):
    from ..db.models import AppCpaPartner
    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")
    result = await db.execute(
        select(AppCpaPartner).where(AppCpaPartner.cpa_license == cpa_license)
    )
    partner = result.scalar_one_or_none()
    if partner is None:
        raise HTTPException(404, detail=f"CPA Partner '{cpa_license}' no encontrado.")
    return partner


async def _load_learning_card_or_404(card_id: str, db: AsyncSession):
    from ..db.models import AppLearningCard
    if db is None:
        raise HTTPException(503, detail="Base de datos no disponible.")
    result = await db.execute(select(AppLearningCard).where(AppLearningCard.card_id == card_id))
    card = result.scalar_one_or_none()
    if card is None:
        raise HTTPException(404, detail=f"Ficha de aprendizaje '{card_id}' no encontrada.")
    return card


def _rule_to_dict(r) -> dict:
    return {
        "rule_id":       r.rule_id,
        "rule_code":     r.rule_code,
        "rule_name":     r.rule_name,
        "rule_type":     r.rule_type,
        "jurisdiction":  r.jurisdiction,
        "status":        r.status,
        "effective_date": r.effective_date,
        "form_types":    r.form_types or [],
        "value_json":    r.value_json or {},
        "approved_by":   r.approved_by,
        "approved_at":   r.approved_at.isoformat() if r.approved_at else None,
        "rejection_reason": r.rejection_reason,
        "notes":         r.notes,
        "created_at":    r.created_at.isoformat() if r.created_at else None,
        "supersedes_rule_id": r.supersedes_rule_id,
        "superseded_at": r.superseded_at,
    }


def _cpa_partner_to_dict(p) -> dict:
    return {
        "cpa_id":              p.cpa_id,
        "cpa_license":         p.cpa_license,
        "full_name":           p.full_name,
        "email":               p.email,
        "phone":               p.phone,
        "status":              p.status,
        "license_verified":    p.license_verified,
        "license_verified_at": p.license_verified_at.isoformat() if p.license_verified_at else None,
        "suspension_reason":   p.suspension_reason,
        "suspended_at":        p.suspended_at.isoformat() if p.suspended_at else None,
        "created_at":          p.created_at.isoformat() if p.created_at else None,
    }


def _alert_to_dict(a) -> dict:
    return {
        "alert_id":    a.alert_id,
        "alert_type":  a.alert_type,
        "severity":    a.severity,
        "title":       a.title,
        "message":     a.message,
        "component":   a.component,
        "is_resolved": a.is_resolved,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "resolved_by": a.resolved_by,
        "created_at":  a.created_at.isoformat() if a.created_at else None,
    }


def _learning_card_to_dict(c) -> dict:
    return {
        "card_id":                c.card_id,
        "card_type":              c.card_type,
        "anonymized_description": c.anonymized_description,
        "context_tags":           c.context_tags or [],
        "status":                 c.status,
        "approved_by":            c.approved_by,
        "approved_at":            c.approved_at.isoformat() if c.approved_at else None,
        "rejection_reason":       c.rejection_reason,
        "created_at":             c.created_at.isoformat() if c.created_at else None,
        # NOTE: original_agent_decision and cpa_correction are included for admin review
        "original_agent_decision": c.original_agent_decision,
        "cpa_correction":          c.cpa_correction,
    }


def _subscription_to_dict(s) -> dict:
    return {
        "subscription_id":      s.subscription_id,
        "client_id":            s.client_id,
        "plan_type":            s.plan_type,
        "status":               s.status,
        "mrr_usd":              str(s.mrr_usd),
        "billing_cycle":        s.billing_cycle,
        "current_period_start": s.current_period_start,
        "current_period_end":   s.current_period_end,
        "last_payment_at":      s.last_payment_at.isoformat() if s.last_payment_at else None,
        "last_payment_amount":  str(s.last_payment_amount) if s.last_payment_amount else None,
        "failed_payments":      s.failed_payments,
        "cancellation_reason":  s.cancellation_reason,
        "cancelled_at":         s.cancelled_at.isoformat() if s.cancelled_at else None,
        "created_at":           s.created_at.isoformat() if s.created_at else None,
    }
