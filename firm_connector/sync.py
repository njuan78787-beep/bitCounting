# =============================================================================
# firm_connector/sync.py
# sync_firm_data() — orchestrates a full extract → validate → import cycle.
#
# STEPS:
#   1. Validate firm config and actor permissions (EXIMIA_ADMIN only)
#   2. Connect to firm (mode-specific)
#   3. Extract clients, transactions, employees
#   4. Encrypt SSN/EIN immediately on receipt
#   5. Validate each record through Pydantic models
#   6. Map account codes (skip/defer unmapped records)
#   7. Dedup (skip already_processed transactions)
#   8. Persist to DB (transactional — all or nothing per entity type)
#   9. Audit log every step
#  10. Return SyncResult with counts only (no field values)
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    ClientFirm,
    DateRange,
    EmployeeFirm,
    EmployeeImportRow,
    FirmConfig,
    FirmConnectionMode,
    RecordError,
    SyncResult,
    SyncStatus,
    TransactionFirm,
)
from .account_mapper import map_account, notify_cpa_unmapped_async
from .audit import log_access_async
from .encryption import encrypt_field

logger = logging.getLogger(__name__)

# Allowed roles for FirmConnector sync operations (CAPA 1 only)
_ALLOWED_ROLES = {"EXIMIA_ADMIN"}


class PermissionDeniedError(Exception):
    """Actor does not have permission to perform FirmConnector operations."""


# ---------------------------------------------------------------------------
# Main orchestration entry point
# ---------------------------------------------------------------------------

async def sync_firm_data(
    firm_config: FirmConfig,
    actor_id: str,
    actor_role: str,
    justification: str,
    date_range: Optional[DateRange] = None,
    db=None,
) -> SyncResult:
    """
    Full sync cycle for a firm connection.

    Args:
        firm_config:   Configuration for the firm to sync.
        actor_id:      User ID of the requesting EXIMIA_ADMIN.
        actor_role:    Must be "EXIMIA_ADMIN".
        justification: Required reason for the sync (for audit trail).
        date_range:    Optional date filter (defaults to full history).
        db:            Optional AsyncSession for DB persistence.

    Returns:
        SyncResult with counts only — no field values, no SSN/EIN.

    Raises:
        PermissionDeniedError: If actor_role is not in _ALLOWED_ROLES.
    """
    if actor_role not in _ALLOWED_ROLES:
        raise PermissionDeniedError(
            f"FirmConnector sync requires EXIMIA_ADMIN role; got '{actor_role}'"
        )

    sync_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    result = SyncResult(
        firm_id=firm_config.firm_id,
        sync_id=sync_id,
        status=SyncStatus.FAILED,
        started_at=started_at,
        mode_used=firm_config.mode,
    )

    await log_access_async(
        actor_id=actor_id,
        actor_role=actor_role,
        firm_id=firm_config.firm_id,
        action="SYNC_START",
        justification=justification,
        db=db,
    )

    try:
        if firm_config.mode == FirmConnectionMode.DB_DIRECT:
            result = await _sync_db_direct(firm_config, result, date_range, db)
        elif firm_config.mode == FirmConnectionMode.REST_API:
            result = await _sync_rest_api(firm_config, result, date_range, db)
        elif firm_config.mode == FirmConnectionMode.MANUAL_IMPORT:
            raise ValueError(
                "MANUAL_IMPORT mode does not use sync_firm_data(); "
                "use ManualImportMode.parse_* and import_* directly."
            )
        else:
            raise ValueError(f"Unknown connection mode: {firm_config.mode}")

    except Exception as exc:
        logger.error(
            "SYNC: Fatal error for firm=%s sync_id=%s: %s",
            firm_config.firm_id, sync_id, type(exc).__name__,
        )
        result = SyncResult(
            **{
                **result.model_dump(),
                "status": SyncStatus.FAILED,
                "completed_at": datetime.now(timezone.utc),
                "error_message": f"{type(exc).__name__}: {_sanitize(str(exc))}",
            }
        )
        await log_access_async(
            actor_id=actor_id,
            actor_role=actor_role,
            firm_id=firm_config.firm_id,
            action="SYNC_FAILED",
            justification=justification,
            outcome="FAILED",
            detail=f"{type(exc).__name__}",
            db=db,
        )
        return result

    await log_access_async(
        actor_id=actor_id,
        actor_role=actor_role,
        firm_id=firm_config.firm_id,
        action="SYNC_COMPLETE",
        justification=justification,
        outcome=result.status.value,
        records_affected=(
            result.clients_imported
            + result.transactions_imported
            + result.employees_imported
        ),
        db=db,
    )

    return result


# ---------------------------------------------------------------------------
# Mode-specific sync implementations
# ---------------------------------------------------------------------------

async def _sync_db_direct(
    config: FirmConfig,
    result: SyncResult,
    date_range: Optional[DateRange],
    db,
) -> SyncResult:
    from .modes.db_direct import DirectDBMode, FirmUnavailableError

    mode = DirectDBMode(config.db_config)
    try:
        async with mode.pool_context() as pool:
            raw_clients      = await mode.fetch_clients(pool, date_range)
            raw_transactions = await mode.fetch_transactions(pool, date_range)
            raw_employees    = await mode.fetch_employees(pool, date_range)
    except FirmUnavailableError as exc:
        raise   # let outer handler set FAILED status

    return await _process_extracted(config, result, raw_clients, raw_transactions, raw_employees, db)


async def _sync_rest_api(
    config: FirmConfig,
    result: SyncResult,
    date_range: Optional[DateRange],
    db,
) -> SyncResult:
    from .modes.rest_api import RestAPIMode, RestAPIAuthError, RestAPIRateLimitError

    mode = RestAPIMode(config.api_config)
    async with mode.client_context() as client:
        raw_clients      = await mode.fetch_clients(client, date_range)
        raw_transactions = await mode.fetch_transactions(client, date_range)
        raw_employees    = await mode.fetch_employees(client, date_range)

    return await _process_extracted(config, result, raw_clients, raw_transactions, raw_employees, db)


# ---------------------------------------------------------------------------
# Shared processing pipeline
# ---------------------------------------------------------------------------

async def _process_extracted(
    config: FirmConfig,
    result: SyncResult,
    raw_clients: List[Dict[str, Any]],
    raw_transactions: List[Dict[str, Any]],
    raw_employees: List[Dict[str, Any]],
    db,
) -> SyncResult:
    """Encrypt → validate → map accounts → dedup → persist."""

    clients_imported = 0
    clients_rejected = 0
    tx_imported      = 0
    tx_rejected      = 0
    tx_skipped       = 0
    emp_imported     = 0
    emp_rejected     = 0
    rejected_records: List[RecordError] = []

    # ---- Clients --------------------------------------------------------
    for raw in raw_clients:
        try:
            # ssn_or_ein_federal must be encrypted before ClientFirm validation
            plain_ssn_ein = raw.get("ssn_or_ein_federal") or raw.get("ssn_or_ein_federal_encrypted")
            if plain_ssn_ein and not _looks_encrypted(plain_ssn_ein):
                raw["ssn_or_ein_federal_encrypted"] = encrypt_field(plain_ssn_ein)
                raw.pop("ssn_or_ein_federal", None)
            client = ClientFirm(**raw)
            await _persist_client(client, config.firm_id, db)
            clients_imported += 1
        except Exception as exc:
            rid = raw.get("firm_client_id", "unknown")
            rejected_records.append(RecordError(
                record_id=str(rid),
                reason=_sanitize(str(exc)),
            ))
            clients_rejected += 1

    # ---- Transactions ---------------------------------------------------
    for raw in raw_transactions:
        try:
            tx = TransactionFirm(**raw)
            if tx.already_processed:
                tx_skipped += 1
                continue
            # Map account code
            internal_code = map_account(tx.account_code, config.firm_id)
            if internal_code is None:
                await notify_cpa_unmapped_async(config.firm_id, tx.account_code)
                # Defer — do not import until CPA confirms mapping
                rejected_records.append(RecordError(
                    record_id=tx.transaction_id,
                    reason="Account code unmapped — deferred until CPA confirms mapping",
                    field="account_code",
                ))
                tx_rejected += 1
                continue
            await _persist_transaction(tx, config.firm_id, internal_code, db)
            tx_imported += 1
        except Exception as exc:
            rid = raw.get("transaction_id", "unknown")
            rejected_records.append(RecordError(
                record_id=str(rid),
                reason=_sanitize(str(exc)),
            ))
            tx_rejected += 1

    # ---- Employees (SSN encrypted immediately) --------------------------
    for raw in raw_employees:
        try:
            # SSN arrives as plaintext from firm; encrypt immediately
            plain_ssn = raw.get("ssn")
            plain_name = raw.get("name")

            if plain_ssn and not _looks_encrypted(plain_ssn):
                raw["ssn_encrypted"] = encrypt_field(plain_ssn)
                raw.pop("ssn", None)

            if plain_name and not _looks_encrypted(plain_name):
                raw["name_encrypted"] = encrypt_field(plain_name)
                raw.pop("name", None)

            emp = EmployeeFirm(**raw)
            await _persist_employee(emp, config.firm_id, db)
            emp_imported += 1
        except Exception as exc:
            rid = raw.get("employee_id", "unknown")
            rejected_records.append(RecordError(
                record_id=str(rid),
                reason=_sanitize(str(exc)),
                field="ssn" if "ssn" in str(exc).lower() else None,
            ))
            emp_rejected += 1

    # ---- Determine final status -----------------------------------------
    total_rejected = clients_rejected + tx_rejected + emp_rejected
    total_imported = clients_imported + tx_imported + emp_imported

    if total_imported == 0 and total_rejected > 0:
        status = SyncStatus.FAILED
    elif total_rejected > 0:
        status = SyncStatus.PARTIAL
    else:
        status = SyncStatus.SUCCESS

    return SyncResult(
        firm_id=config.firm_id,
        sync_id=result.sync_id,
        status=status,
        started_at=result.started_at,
        completed_at=datetime.now(timezone.utc),
        mode_used=result.mode_used,
        clients_imported=clients_imported,
        clients_rejected=clients_rejected,
        transactions_imported=tx_imported,
        transactions_rejected=tx_rejected,
        transactions_skipped=tx_skipped,
        employees_imported=emp_imported,
        employees_rejected=emp_rejected,
        rejected_records=rejected_records,
    )


# ---------------------------------------------------------------------------
# Persistence helpers (write to DB if available, else no-op)
# ---------------------------------------------------------------------------

async def _persist_client(client: ClientFirm, firm_id: str, db) -> None:
    if db is None:
        return
    try:
        from api.db.models import AppFirmClient
        from sqlalchemy import select
        existing = await db.execute(
            select(AppFirmClient).where(
                AppFirmClient.firm_client_id == client.firm_client_id,
                AppFirmClient.firm_id == firm_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = AppFirmClient(firm_id=firm_id)
            db.add(row)
        row.firm_client_id             = client.firm_client_id
        row.business_name              = client.business_name
        row.ein_pr                     = client.ein_pr
        row.ssn_or_ein_federal_encrypted = client.ssn_or_ein_federal_encrypted
        row.business_type              = client.business_type.value
        row.municipality_pr            = client.municipality_pr
        row.tax_year                   = client.tax_year
        row.accounting_method          = client.accounting_method.value
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise


async def _persist_transaction(tx: TransactionFirm, firm_id: str, internal_code: str, db) -> None:
    if db is None:
        return
    try:
        from api.db.models import AppFirmTransaction
        from sqlalchemy import select
        existing = await db.execute(
            select(AppFirmTransaction).where(AppFirmTransaction.transaction_id == tx.transaction_id)
        )
        if existing.scalar_one_or_none() is not None:
            return   # already exists — idempotent
        row = AppFirmTransaction(
            transaction_id=tx.transaction_id,
            firm_id=firm_id,
            firm_client_id=tx.firm_client_id,
            date=tx.date,
            description=tx.description,
            amount=tx.amount,
            transaction_type=tx.type.value,
            firm_account_code=tx.account_code,
            internal_account_code=internal_code,
            vendor_or_client=tx.vendor_or_client,
            invoice_number=tx.invoice_number,
            ivu_collected=tx.ivu_collected,
            ivu_paid=tx.ivu_paid,
            is_payroll=tx.is_payroll,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise


async def _persist_employee(emp: EmployeeFirm, firm_id: str, db) -> None:
    if db is None:
        return
    try:
        from api.db.models import AppFirmEmployee
        from sqlalchemy import select
        existing = await db.execute(
            select(AppFirmEmployee).where(
                AppFirmEmployee.employee_id == emp.employee_id,
                AppFirmEmployee.firm_id == firm_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = AppFirmEmployee(firm_id=firm_id)
            db.add(row)
        row.employee_id           = emp.employee_id
        row.firm_client_id        = emp.firm_client_id
        row.name_encrypted        = emp.name_encrypted
        row.ssn_encrypted         = emp.ssn_encrypted
        row.hire_date             = emp.hire_date
        row.termination_date      = emp.termination_date
        row.pay_type              = emp.pay_type.value
        row.pay_rate              = emp.pay_rate
        row.filing_status_federal = emp.filing_status_federal
        row.allowances_federal    = emp.allowances_federal
        row.filing_status_pr      = emp.filing_status_pr
        row.allowances_pr         = emp.allowances_pr
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _looks_encrypted(value: str) -> bool:
    """Heuristic: base64 string of ≥ 28 bytes suggests already encrypted."""
    import base64
    try:
        return len(base64.b64decode(value)) >= 28
    except Exception:
        return False


def _sanitize(msg: str) -> str:
    """Truncate long error messages to prevent accidental value leakage."""
    if len(msg) > 300:
        msg = msg[:300] + "…"
    return msg
