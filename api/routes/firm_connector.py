# =============================================================================
# api/routes/firm_connector.py
# FirmConnector REST endpoints.
#
# ACCESS CONTROL — CAPA 1 / CAPA 2:
#
#   CAPA 1 (EXIMIA_ADMIN only):
#     POST   /api/v1/firm-connector/firms                 — register firm
#     DELETE /api/v1/firm-connector/firms/{firm_id}       — revoke access
#     POST   /api/v1/firm-connector/firms/{firm_id}/sync  — trigger sync
#     POST   /api/v1/firm-connector/firms/{firm_id}/import — file import
#     PUT    /api/v1/firm-connector/firms/{firm_id}/mappings/{code} — set mapping
#
#   CAPA 2 (EXIMIA_ADMIN + CPA_SENIOR + CPA_PARTNER — read-only):
#     GET    /api/v1/firm-connector/firms                        — list firms
#     GET    /api/v1/firm-connector/firms/{firm_id}/sync-status  — last sync
#     GET    /api/v1/firm-connector/firms/{firm_id}/mappings     — account mappings
#     GET    /api/v1/firm-connector/audit                        — audit log
#
# No CLIENT or API_KEY role may access any of these endpoints.
# =============================================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from ..auth import Role, TokenData, get_current_user
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/firm-connector", tags=["firm-connector"])

# ---------------------------------------------------------------------------
# Role guards
# ---------------------------------------------------------------------------

_ADMIN_ROLES  = {Role.EXIMIA_ADMIN}
_READ_ROLES   = {Role.EXIMIA_ADMIN, Role.CPA_SENIOR, Role.CPA_PARTNER}


def _require_admin(current_user: TokenData) -> TokenData:
    if current_user.role not in _ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="FirmConnector write operations require EXIMIA_ADMIN role (CAPA 1).",
        )
    return current_user


def _require_read(current_user: TokenData) -> TokenData:
    if current_user.role not in _READ_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="FirmConnector access requires EXIMIA_ADMIN, CPA_SENIOR, or CPA_PARTNER role.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RegisterFirmRequest(BaseModel):
    firm_id:   str  = Field(min_length=1, max_length=36)
    firm_name: str  = Field(min_length=1, max_length=255)
    mode:      str  = Field(description="DB_DIRECT | REST_API | MANUAL_IMPORT")
    # For DB_DIRECT: pass db_config fields
    db_config: Optional[Dict[str, Any]] = None
    # For REST_API: pass api_config fields
    api_config: Optional[Dict[str, Any]] = None
    justification: str = Field(min_length=5)


class SyncRequest(BaseModel):
    justification: str  = Field(min_length=5)
    date_from:     Optional[str] = None   # YYYY-MM-DD
    date_to:       Optional[str] = None   # YYYY-MM-DD


class RevokeRequest(BaseModel):
    justification: str = Field(min_length=5)


class SetMappingRequest(BaseModel):
    internal_account_code: str  = Field(min_length=1)
    cpa_confirmed:         bool = False
    justification:         str  = Field(min_length=5)


class FirmSummaryResponse(BaseModel):
    firm_id:   str
    firm_name: str
    mode:      str
    is_active: bool
    created_by: str
    created_at: str


# ---------------------------------------------------------------------------
# CAPA 1 — EXIMIA_ADMIN write endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/firms",
    status_code=status.HTTP_201_CREATED,
    summary="[ADMIN] Register a new firm connection",
    description="EXIMIA_ADMIN only. Registers an external accounting firm and stores its connection configuration.",
)
async def register_firm(
    body: RegisterFirmRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_admin(current_user)

    from firm_connector.models import FirmConnectionMode, FirmConfig, DBDirectConfig, OAuthConfig
    from firm_connector.audit import log_access_async

    try:
        mode = FirmConnectionMode(body.mode)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {body.mode!r}")

    db_config  = DBDirectConfig(**body.db_config)  if body.db_config  else None
    api_config = OAuthConfig(**body.api_config)    if body.api_config else None

    try:
        firm_config = FirmConfig(
            firm_id=body.firm_id,
            firm_name=body.firm_name,
            mode=mode,
            db_config=db_config,
            api_config=api_config,
            created_by=current_user.sub,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Persist to DB
    if db is not None:
        from api.db.models import AppFirmConfig
        from sqlalchemy import select
        existing = await db.execute(
            select(AppFirmConfig).where(AppFirmConfig.firm_id == body.firm_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail=f"Firm '{body.firm_id}' already registered.")

        row = AppFirmConfig(
            firm_id=firm_config.firm_id,
            firm_name=firm_config.firm_name,
            mode=firm_config.mode.value,
            is_active=True,
            created_by=firm_config.created_by,
            config_json=_safe_config_json(body),
        )
        db.add(row)
        await db.commit()

    await log_access_async(
        actor_id=current_user.sub,
        actor_role=current_user.role.value,
        firm_id=body.firm_id,
        action="REGISTER_FIRM",
        justification=body.justification,
        db=db,
    )

    logger.info("FIRM_CONNECTOR: Firm registered firm=%s by admin=%s", body.firm_id, current_user.sub)
    return {"firm_id": firm_config.firm_id, "status": "registered"}


@router.post(
    "/firms/{firm_id}/sync",
    summary="[ADMIN] Trigger a sync for a firm",
    description="EXIMIA_ADMIN only. Initiates a full data sync from the external firm.",
)
async def trigger_sync(
    firm_id: str,
    body: SyncRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_admin(current_user)

    firm_config = await _load_firm_config(firm_id, db)

    date_range = None
    if body.date_from and body.date_to:
        from firm_connector.models import DateRange
        from datetime import date
        try:
            date_range = DateRange(
                start=date.fromisoformat(body.date_from),
                end=date.fromisoformat(body.date_to),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    from firm_connector.connector import FirmConnector
    from firm_connector.sync import PermissionDeniedError

    fc = FirmConnector(db=db)
    try:
        result = await fc.sync(
            firm_config=firm_config,
            actor_id=current_user.sub,
            actor_role=current_user.role.value,
            justification=body.justification,
            date_range=date_range,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        logger.error("FIRM_CONNECTOR: sync error firm=%s: %s", firm_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Sync failed — see server logs.")

    # Persist sync log
    if db is not None:
        await _save_sync_log(result, db)

    return result.model_dump()


@router.post(
    "/firms/{firm_id}/import",
    summary="[ADMIN] Import data from uploaded file",
    description="EXIMIA_ADMIN only. Accepts CSV, Excel, JSON, or XML. All-or-nothing validation.",
)
async def import_file(
    firm_id: str,
    entity: str = Form(..., description="employees | transactions | clients"),
    fmt: str    = Form(..., description="CSV | EXCEL | JSON | XML"),
    justification: str = Form(..., min_length=5),
    file: UploadFile = File(...),
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_admin(current_user)

    firm_config = await _load_firm_config(firm_id, db)

    from firm_connector.models import ImportFormat
    try:
        import_fmt = ImportFormat(fmt.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid format: {fmt!r}")

    if entity not in ("employees", "transactions", "clients"):
        raise HTTPException(status_code=400, detail=f"Invalid entity: {entity!r}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    from firm_connector.connector import FirmConnector
    from firm_connector.modes.manual_import import ImportValidationError

    fc = FirmConnector(db=db)
    try:
        result = await fc.import_file(
            firm_config=firm_config,
            file_bytes=file_bytes,
            fmt=import_fmt,
            entity=entity,
            actor_id=current_user.sub,
            actor_role=current_user.role.value,
            justification=justification,
        )
    except ImportValidationError as exc:
        return {
            "status": "validation_failed",
            "errors": [e.model_dump() for e in exc.errors],
        }
    except Exception as exc:
        logger.error("FIRM_CONNECTOR: import error firm=%s: %s", firm_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Import failed — see server logs.")

    return result.model_dump()


@router.delete(
    "/firms/{firm_id}",
    summary="[ADMIN] Revoke a firm connection",
    description="EXIMIA_ADMIN only. Immediately revokes access to the firm (<60s). Irreversible without re-registration.",
)
async def revoke_firm(
    firm_id: str,
    body: RevokeRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_admin(current_user)

    from firm_connector.connector import FirmConnector
    fc = FirmConnector(db=db)
    await fc.revoke_access(
        firm_id=firm_id,
        actor_id=current_user.sub,
        actor_role=current_user.role.value,
        justification=body.justification,
    )
    return {"firm_id": firm_id, "status": "revoked"}


@router.put(
    "/firms/{firm_id}/mappings/{account_code}",
    summary="[ADMIN] Set an account code mapping",
    description="EXIMIA_ADMIN only. Maps a firm account code to an internal Bit-Counting account.",
)
async def set_mapping(
    firm_id: str,
    account_code: str,
    body: SetMappingRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_admin(current_user)

    from firm_connector.account_mapper import set_mapping as _set_mapping, confirm_mapping
    from firm_connector.audit import log_access_async

    mapping = _set_mapping(
        firm_id=firm_id,
        firm_account_code=account_code,
        internal_account_code=body.internal_account_code,
        cpa_confirmed=body.cpa_confirmed,
    )

    # Persist to DB
    if db is not None:
        await _upsert_mapping_db(mapping, db)

    await log_access_async(
        actor_id=current_user.sub,
        actor_role=current_user.role.value,
        firm_id=firm_id,
        action="SET_MAPPING",
        justification=body.justification,
        db=db,
    )

    return mapping.model_dump()


# ---------------------------------------------------------------------------
# CAPA 2 — read-only (EXIMIA_ADMIN + CPA_SENIOR + CPA_PARTNER)
# ---------------------------------------------------------------------------

@router.get(
    "/firms",
    summary="List registered firms",
    description="Returns all registered firms. EXIMIA_ADMIN, CPA_SENIOR, CPA_PARTNER.",
)
async def list_firms(
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_read(current_user)

    if db is None:
        return []

    from sqlalchemy import select
    from api.db.models import AppFirmConfig

    result = await db.execute(select(AppFirmConfig).order_by(AppFirmConfig.firm_name))
    rows = result.scalars().all()
    return [
        {
            "firm_id":   r.firm_id,
            "firm_name": r.firm_name,
            "mode":      r.mode,
            "is_active": r.is_active,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get(
    "/firms/{firm_id}/sync-status",
    summary="Get last sync status for a firm",
    description="Read-only. EXIMIA_ADMIN, CPA_SENIOR, CPA_PARTNER. CPAs can see sync results but cannot trigger syncs.",
)
async def get_sync_status(
    firm_id: str,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_read(current_user)

    from firm_connector.connector import FirmConnector
    fc = FirmConnector(db=db)
    result = await fc.get_sync_status(
        firm_id=firm_id,
        actor_id=current_user.sub,
        actor_role=current_user.role.value,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"No sync history for firm '{firm_id}'.")
    return result


@router.get(
    "/firms/{firm_id}/mappings",
    summary="Get account mappings for a firm",
    description="Read-only. EXIMIA_ADMIN, CPA_SENIOR, CPA_PARTNER. CPAs can view but not modify mappings.",
)
async def get_mappings(
    firm_id: str,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_read(current_user)

    from firm_connector.connector import FirmConnector
    fc = FirmConnector(db=db)
    return await fc.get_mappings(
        firm_id=firm_id,
        actor_id=current_user.sub,
        actor_role=current_user.role.value,
    )


@router.get(
    "/audit",
    summary="[ADMIN] View FirmConnector audit log",
    description="EXIMIA_ADMIN only. Returns all FirmConnector access events.",
)
async def get_audit_log(
    firm_id: Optional[str] = None,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_admin(current_user)

    if db is not None:
        from sqlalchemy import select, desc
        from api.db.models import AppFirmAuditLog
        q = select(AppFirmAuditLog).order_by(desc(AppFirmAuditLog.timestamp)).limit(500)
        if firm_id:
            q = q.where(AppFirmAuditLog.firm_id == firm_id)
        result = await db.execute(q)
        rows = result.scalars().all()
        return [
            {
                "entry_id":         r.entry_id,
                "actor_id":         r.actor_id,
                "actor_role":       r.actor_role,
                "firm_id":          r.firm_id,
                "action":           r.action,
                "justification":    r.justification,
                "outcome":          r.outcome,
                "records_affected": r.records_affected,
                "timestamp":        r.timestamp.isoformat() if r.timestamp else None,
                "detail":           r.detail,
            }
            for r in rows
        ]

    # Fallback: in-memory audit log
    from firm_connector.audit import get_audit_log as _get_log
    return _get_log(firm_id=firm_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_firm_config(firm_id: str, db):
    """Load FirmConfig from DB or raise 404."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available.")

    from sqlalchemy import select
    from api.db.models import AppFirmConfig
    result = await db.execute(select(AppFirmConfig).where(AppFirmConfig.firm_id == firm_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Firm '{firm_id}' not found.")
    if not row.is_active:
        raise HTTPException(status_code=409, detail=f"Firm '{firm_id}' connection is revoked.")

    from firm_connector.models import FirmConfig, FirmConnectionMode
    cfg_json = row.config_json or {}
    mode = FirmConnectionMode(row.mode)

    db_config  = None
    api_config = None
    if mode == FirmConnectionMode.DB_DIRECT and cfg_json.get("db_config"):
        from firm_connector.models import DBDirectConfig
        db_config = DBDirectConfig(**cfg_json["db_config"])
    elif mode == FirmConnectionMode.REST_API and cfg_json.get("api_config"):
        from firm_connector.models import OAuthConfig
        api_config = OAuthConfig(**cfg_json["api_config"])

    return FirmConfig(
        firm_id=row.firm_id,
        firm_name=row.firm_name,
        mode=mode,
        is_active=row.is_active,
        db_config=db_config,
        api_config=api_config,
        created_by=row.created_by,
        created_at=row.created_at,
    )


async def _save_sync_log(result, db) -> None:
    try:
        from api.db.models import AppSyncLog
        import json
        row = AppSyncLog(
            sync_id=result.sync_id,
            firm_id=result.firm_id,
            status=result.status.value,
            mode_used=result.mode_used.value,
            started_at=result.started_at,
            completed_at=result.completed_at,
            clients_imported=result.clients_imported,
            clients_rejected=result.clients_rejected,
            transactions_imported=result.transactions_imported,
            transactions_rejected=result.transactions_rejected,
            transactions_skipped=result.transactions_skipped,
            employees_imported=result.employees_imported,
            employees_rejected=result.employees_rejected,
            rejected_records=[e.model_dump() for e in result.rejected_records],
            error_message=result.error_message,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        logger.error("FIRM_CONNECTOR: Failed to save sync log: %s", type(exc).__name__)


async def _upsert_mapping_db(mapping, db) -> None:
    try:
        from sqlalchemy import select
        from api.db.models import AppAccountMapping
        result = await db.execute(
            select(AppAccountMapping).where(
                AppAccountMapping.firm_id == mapping.firm_id,
                AppAccountMapping.firm_account_code == mapping.firm_account_code,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AppAccountMapping(
                firm_id=mapping.firm_id,
                firm_account_code=mapping.firm_account_code,
            )
            db.add(row)
        row.internal_account_code = mapping.internal_account_code
        row.status = mapping.status.value
        row.cpa_confirmed = mapping.cpa_confirmed
        await db.commit()
    except Exception as exc:
        logger.error("FIRM_CONNECTOR: Failed to upsert mapping: %s", type(exc).__name__)


def _safe_config_json(body: RegisterFirmRequest) -> Dict[str, Any]:
    """
    Store config as JSON — credential fields are already encrypted strings
    (password_encrypted, client_secret_encrypted) so safe to store in DB.
    Never stores plaintext credentials.
    """
    result: Dict[str, Any] = {}
    if body.db_config:
        result["db_config"] = body.db_config
    if body.api_config:
        result["api_config"] = body.api_config
    return result
