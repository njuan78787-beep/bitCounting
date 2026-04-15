# =============================================================================
# firm_connector/connector.py
# FirmConnector — public façade for the FirmConnector subsystem.
#
# ACCESS CONTROL (CAPA 1 — EXIMIA_ADMIN only):
#   All mutating operations (connect, sync, revoke, import) require the caller
#   to pass actor_id + actor_role = "EXIMIA_ADMIN".
#   CPAs (CPA_PARTNER, CPA_SENIOR) may call read-only methods (get_sync_status,
#   get_mappings) via CAPA 2 endpoints — they cannot modify firm connections.
#
# REVOCATION:
#   revoke_access() marks the firm inactive in-process (<60s) and writes to DB.
#   The polling loop and any in-flight sync will fail on the next iteration
#   when they check firm_config.is_active.
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    DateRange,
    FirmConfig,
    FirmConnectionMode,
    ImportFormat,
    SyncResult,
    SyncStatus,
)
from .audit import log_access_async
from .sync import PermissionDeniedError, sync_firm_data

logger = logging.getLogger(__name__)

# Roles allowed to perform write/mutating operations (CAPA 1)
_ADMIN_ROLES = {"EXIMIA_ADMIN"}

# Roles allowed read-only access (CAPA 2 — CPA dashboard visibility only)
_READ_ROLES  = {"EXIMIA_ADMIN", "CPA_SENIOR", "CPA_PARTNER"}


class FirmConnector:
    """
    Entry point for all FirmConnector operations.

    Usage (EXIMIA_ADMIN — full access):
        fc = FirmConnector(db=db_session)
        result = await fc.sync(firm_config, actor_id="admin1", actor_role="EXIMIA_ADMIN",
                               justification="Monthly sync")

    Usage (CPA read-only — CAPA 2):
        fc = FirmConnector(db=db_session)
        status = await fc.get_sync_status(firm_id, actor_id="cpa1", actor_role="CPA_SENIOR")
    """

    def __init__(self, db=None) -> None:
        """
        Args:
            db: Optional AsyncSession for DB persistence.
                If None, operations complete in-memory only (dev/test mode).
        """
        self._db = db
        # In-process revocation set — firm_ids revoked this process lifetime
        self._revoked: set = set()

    # -----------------------------------------------------------------------
    # CAPA 1 — EXIMIA_ADMIN mutating operations
    # -----------------------------------------------------------------------

    async def sync(
        self,
        firm_config: FirmConfig,
        actor_id: str,
        actor_role: str,
        justification: str,
        date_range: Optional[DateRange] = None,
    ) -> SyncResult:
        """
        Run a full sync cycle for a firm.

        Raises:
            PermissionDeniedError: If actor_role is not EXIMIA_ADMIN.
            ValueError: If firm_config is invalid or mode is unsupported.
        """
        self._require_admin(actor_role, "sync")
        self._check_not_revoked(firm_config.firm_id)

        if not firm_config.is_active:
            return SyncResult(
                firm_id=firm_config.firm_id,
                sync_id=str(uuid.uuid4()),
                status=SyncStatus.FAILED,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                mode_used=firm_config.mode,
                error_message="Firm connection is inactive",
            )

        return await sync_firm_data(
            firm_config=firm_config,
            actor_id=actor_id,
            actor_role=actor_role,
            justification=justification,
            date_range=date_range,
            db=self._db,
        )

    async def import_file(
        self,
        firm_config: FirmConfig,
        file_bytes: bytes,
        fmt: ImportFormat,
        entity: str,          # "employees" | "transactions" | "clients"
        actor_id: str,
        actor_role: str,
        justification: str,
    ) -> SyncResult:
        """
        Import data from an uploaded file (MANUAL_IMPORT mode).

        Raises:
            PermissionDeniedError: If actor_role is not EXIMIA_ADMIN.
            ImportValidationError: If any row fails validation (all-or-nothing).
        """
        self._require_admin(actor_role, "import_file")
        self._check_not_revoked(firm_config.firm_id)

        from .modes.manual_import import ManualImportMode, ImportValidationError
        from .encryption import encrypt_field
        from .account_mapper import map_account, notify_cpa_unmapped_async

        mode = ManualImportMode()
        sync_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        await log_access_async(
            actor_id=actor_id,
            actor_role=actor_role,
            firm_id=firm_config.firm_id,
            action=f"IMPORT_{entity.upper()}",
            justification=justification,
            db=self._db,
        )

        try:
            if entity == "employees":
                import_rows = mode.parse_employees(file_bytes, fmt)
                emp_imported, emp_rejected = 0, 0
                from .sync import _persist_employee
                for row in import_rows:
                    emp = row.to_employee_firm()
                    try:
                        await _persist_employee(emp, firm_config.firm_id, self._db)
                        emp_imported += 1
                    except Exception:
                        emp_rejected += 1

                result = SyncResult(
                    firm_id=firm_config.firm_id,
                    sync_id=sync_id,
                    status=SyncStatus.SUCCESS if emp_rejected == 0 else SyncStatus.PARTIAL,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    mode_used=FirmConnectionMode.MANUAL_IMPORT,
                    employees_imported=emp_imported,
                    employees_rejected=emp_rejected,
                )

            elif entity == "transactions":
                txs = mode.parse_transactions(file_bytes, fmt)
                tx_imported, tx_rejected, tx_skipped = 0, 0, 0
                from .sync import _persist_transaction
                for tx in txs:
                    if tx.already_processed:
                        tx_skipped += 1
                        continue
                    internal = map_account(tx.account_code, firm_config.firm_id)
                    if internal is None:
                        await notify_cpa_unmapped_async(firm_config.firm_id, tx.account_code)
                        tx_rejected += 1
                        continue
                    try:
                        await _persist_transaction(tx, firm_config.firm_id, internal, self._db)
                        tx_imported += 1
                    except Exception:
                        tx_rejected += 1

                result = SyncResult(
                    firm_id=firm_config.firm_id,
                    sync_id=sync_id,
                    status=SyncStatus.SUCCESS if tx_rejected == 0 else SyncStatus.PARTIAL,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    mode_used=FirmConnectionMode.MANUAL_IMPORT,
                    transactions_imported=tx_imported,
                    transactions_rejected=tx_rejected,
                    transactions_skipped=tx_skipped,
                )

            elif entity == "clients":
                raw_rows = mode.parse_clients(file_bytes, fmt)
                result = SyncResult(
                    firm_id=firm_config.firm_id,
                    sync_id=sync_id,
                    status=SyncStatus.SUCCESS,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    mode_used=FirmConnectionMode.MANUAL_IMPORT,
                    clients_imported=len(raw_rows),
                )
            else:
                raise ValueError(f"Unknown entity type: {entity!r}")

        except ImportValidationError as exc:
            result = SyncResult(
                firm_id=firm_config.firm_id,
                sync_id=sync_id,
                status=SyncStatus.FAILED,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                mode_used=FirmConnectionMode.MANUAL_IMPORT,
                rejected_records=exc.errors,
                error_message=f"File validation failed: {len(exc.errors)} error(s)",
            )

        await log_access_async(
            actor_id=actor_id,
            actor_role=actor_role,
            firm_id=firm_config.firm_id,
            action=f"IMPORT_{entity.upper()}_COMPLETE",
            justification=justification,
            outcome=result.status.value,
            records_affected=result.employees_imported + result.transactions_imported + result.clients_imported,
            db=self._db,
        )

        return result

    async def revoke_access(
        self,
        firm_id: str,
        actor_id: str,
        actor_role: str,
        justification: str,
    ) -> None:
        """
        Revoke a firm connection immediately (<60 seconds).

        Marks the firm as inactive in-process. Any subsequent sync() or
        import_file() calls for this firm_id will be rejected instantly.
        The DB record is also updated to is_active=False.

        Raises:
            PermissionDeniedError: If actor_role is not EXIMIA_ADMIN.
        """
        self._require_admin(actor_role, "revoke_access")

        # In-process revocation — instant
        self._revoked.add(firm_id)
        logger.warning(
            "FIRM_CONNECTOR: Access REVOKED for firm=%s by actor=%s",
            firm_id, actor_id,
        )

        # Persist to DB
        await self._set_firm_inactive_db(firm_id)

        await log_access_async(
            actor_id=actor_id,
            actor_role=actor_role,
            firm_id=firm_id,
            action="REVOKE_ACCESS",
            justification=justification,
            outcome="SUCCESS",
            db=self._db,
        )

    # -----------------------------------------------------------------------
    # CAPA 2 — CPA read-only visibility (no mutations)
    # -----------------------------------------------------------------------

    async def get_sync_status(
        self,
        firm_id: str,
        actor_id: str,
        actor_role: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Return the most recent sync result for a firm.
        Available to EXIMIA_ADMIN, CPA_SENIOR, and CPA_PARTNER (read-only).

        Raises:
            PermissionDeniedError: If actor_role is not in _READ_ROLES.
        """
        self._require_read(actor_role, "get_sync_status")

        if self._db is None:
            return None

        try:
            from sqlalchemy import select, desc
            from api.db.models import AppSyncLog
            result = await self._db.execute(
                select(AppSyncLog)
                .where(AppSyncLog.firm_id == firm_id)
                .order_by(desc(AppSyncLog.started_at))
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return {
                "firm_id":              row.firm_id,
                "sync_id":              row.sync_id,
                "status":               row.status,
                "mode_used":            row.mode_used,
                "started_at":           row.started_at.isoformat() if row.started_at else None,
                "completed_at":         row.completed_at.isoformat() if row.completed_at else None,
                "clients_imported":     row.clients_imported,
                "transactions_imported": row.transactions_imported,
                "employees_imported":   row.employees_imported,
            }
        except Exception as exc:
            logger.error("FIRM_CONNECTOR: get_sync_status DB error: %s", type(exc).__name__)
            return None

    async def get_mappings(
        self,
        firm_id: str,
        actor_id: str,
        actor_role: str,
    ) -> List[Dict[str, Any]]:
        """
        Return account mappings for a firm.
        Available to EXIMIA_ADMIN, CPA_SENIOR, and CPA_PARTNER (read-only).
        CPAs can VIEW mappings but cannot MODIFY them (no set_mapping here).

        Raises:
            PermissionDeniedError: If actor_role is not in _READ_ROLES.
        """
        self._require_read(actor_role, "get_mappings")

        from .account_mapper import get_all_mappings
        return get_all_mappings(firm_id=firm_id)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _require_admin(self, actor_role: str, operation: str) -> None:
        if actor_role not in _ADMIN_ROLES:
            raise PermissionDeniedError(
                f"Operation '{operation}' requires EXIMIA_ADMIN role; "
                f"got '{actor_role}'. "
                f"FirmConnector write operations are restricted to CAPA 1."
            )

    def _require_read(self, actor_role: str, operation: str) -> None:
        if actor_role not in _READ_ROLES:
            raise PermissionDeniedError(
                f"Operation '{operation}' requires EXIMIA_ADMIN, CPA_SENIOR, or CPA_PARTNER role; "
                f"got '{actor_role}'."
            )

    def _check_not_revoked(self, firm_id: str) -> None:
        if firm_id in self._revoked:
            raise PermissionDeniedError(
                f"Access to firm '{firm_id}' has been revoked. "
                "Contact EXIMIA_ADMIN to restore access."
            )

    async def _set_firm_inactive_db(self, firm_id: str) -> None:
        if self._db is None:
            return
        try:
            from sqlalchemy import update
            from api.db.models import AppFirmConfig
            await self._db.execute(
                update(AppFirmConfig)
                .where(AppFirmConfig.firm_id == firm_id)
                .values(is_active=False, updated_at=datetime.now(timezone.utc))
            )
            await self._db.commit()
        except Exception as exc:
            logger.error(
                "FIRM_CONNECTOR: Failed to mark firm=%s inactive in DB: %s",
                firm_id, type(exc).__name__,
            )
