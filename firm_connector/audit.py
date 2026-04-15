# =============================================================================
# firm_connector/audit.py
# Sanitized audit trail for all FirmConnector access.
#
# INVARIANTS:
#   - No sensitive field values are ever written to the audit log
#     (no SSN, EIN, passwords, secrets)
#   - Every access to firm data requires a justification string
#   - Actor must be EXIMIA_ADMIN or CPA_SENIOR (enforced by connector.py)
#   - Audit entries are append-only; no update or delete operations
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process audit buffer (also written to DB via _persist_entry)
# ---------------------------------------------------------------------------

_audit_log: List[Dict[str, Any]] = []


class AuditEntry:
    """A single sanitized audit event."""

    __slots__ = (
        "entry_id", "actor_id", "actor_role", "firm_id",
        "action", "justification", "outcome",
        "records_affected", "timestamp", "detail",
    )

    def __init__(
        self,
        actor_id: str,
        actor_role: str,
        firm_id: str,
        action: str,
        justification: str,
        outcome: str = "SUCCESS",
        records_affected: int = 0,
        detail: Optional[str] = None,
    ) -> None:
        self.entry_id        = str(uuid.uuid4())
        self.actor_id        = actor_id
        self.actor_role      = actor_role
        self.firm_id         = firm_id
        self.action          = action
        self.justification   = justification
        self.outcome         = outcome
        self.records_affected = records_affected
        self.timestamp       = datetime.now(timezone.utc)
        self.detail          = detail    # sanitized context — never field values

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id":         self.entry_id,
            "actor_id":         self.actor_id,
            "actor_role":       self.actor_role,
            "firm_id":          self.firm_id,
            "action":           self.action,
            "justification":    self.justification,
            "outcome":          self.outcome,
            "records_affected": self.records_affected,
            "timestamp":        self.timestamp.isoformat(),
            "detail":           self.detail,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_access(
    actor_id: str,
    actor_role: str,
    firm_id: str,
    action: str,
    justification: str,
    outcome: str = "SUCCESS",
    records_affected: int = 0,
    detail: Optional[str] = None,
    db=None,
) -> AuditEntry:
    """
    Record a FirmConnector access event.

    Args:
        actor_id:         User ID of the actor performing the action.
        actor_role:       Role string (EXIMIA_ADMIN, CPA_SENIOR, etc.).
        firm_id:          Firm being accessed.
        action:           Short action code (e.g. SYNC, REVOKE, IMPORT, VIEW_MAPPING).
        justification:    Required human-readable reason for access.
        outcome:          "SUCCESS", "FAILED", or "PARTIAL".
        records_affected: Count of records touched (never actual values).
        detail:           Optional sanitized context — no field values.
        db:               Optional AsyncSession for DB persistence.

    Returns:
        The created AuditEntry.
    """
    if not justification or not justification.strip():
        raise ValueError("Audit justification is required for all FirmConnector access")

    entry = AuditEntry(
        actor_id=actor_id,
        actor_role=actor_role,
        firm_id=firm_id,
        action=action,
        justification=justification.strip(),
        outcome=outcome,
        records_affected=records_affected,
        detail=detail,
    )

    _audit_log.append(entry.to_dict())

    logger.info(
        "FIRM_AUDIT: actor=%s role=%s firm=%s action=%s outcome=%s records=%d",
        actor_id, actor_role, firm_id, action, outcome, records_affected,
    )

    if db is not None:
        import asyncio
        # Fire-and-forget persistence if called from sync context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_persist_entry(db, entry))
        except RuntimeError:
            pass   # no event loop — skip async persistence

    return entry


async def log_access_async(
    actor_id: str,
    actor_role: str,
    firm_id: str,
    action: str,
    justification: str,
    outcome: str = "SUCCESS",
    records_affected: int = 0,
    detail: Optional[str] = None,
    db=None,
) -> AuditEntry:
    """Async version of log_access — awaits DB persistence."""
    if not justification or not justification.strip():
        raise ValueError("Audit justification is required for all FirmConnector access")

    entry = AuditEntry(
        actor_id=actor_id,
        actor_role=actor_role,
        firm_id=firm_id,
        action=action,
        justification=justification.strip(),
        outcome=outcome,
        records_affected=records_affected,
        detail=detail,
    )

    _audit_log.append(entry.to_dict())

    logger.info(
        "FIRM_AUDIT: actor=%s role=%s firm=%s action=%s outcome=%s records=%d",
        actor_id, actor_role, firm_id, action, outcome, records_affected,
    )

    if db is not None:
        await _persist_entry(db, entry)

    return entry


async def _persist_entry(db, entry: AuditEntry) -> None:
    """Persist an AuditEntry to the app_firm_audit_log table."""
    try:
        from api.db.models import AppFirmAuditLog
        row = AppFirmAuditLog(
            entry_id=entry.entry_id,
            actor_id=entry.actor_id,
            actor_role=entry.actor_role,
            firm_id=entry.firm_id,
            action=entry.action,
            justification=entry.justification,
            outcome=entry.outcome,
            records_affected=entry.records_affected,
            timestamp=entry.timestamp,
            detail=entry.detail,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        logger.error("FIRM_AUDIT: Failed to persist audit entry to DB: %s", type(exc).__name__)
        # Never raise — audit persistence failure must not block business logic


def get_audit_log(firm_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return in-memory audit entries (use DB query for production).
    Optionally filter by firm_id.
    """
    if firm_id is None:
        return list(_audit_log)
    return [e for e in _audit_log if e.get("firm_id") == firm_id]
