# =============================================================================
# firm_connector/account_mapper.py
# Maps external firm account codes to Bit-Counting's internal chart of accounts.
#
# BEHAVIOUR:
#   - If a mapping exists and is CPA-confirmed: return the internal code.
#   - If a mapping exists but is NOT confirmed: return it with UNMAPPED status
#     and queue a CPA notification.
#   - If no mapping exists at all: create an UNMAPPED placeholder, notify CPA,
#     and return None for the internal code so the caller can defer the record.
#
# STORAGE:
#   - In-memory dict for fast lookup (populated from DB at startup).
#   - Changes written through to DB via async helpers.
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from .models import AccountMapping, AccountMappingStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory mapping store  {(firm_id, firm_account_code): AccountMapping}
# ---------------------------------------------------------------------------

_mappings: Dict[Tuple[str, str], AccountMapping] = {}

# Pending CPA notifications (firm_id, firm_account_code) — deduplicated
_pending_notifications: set = set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def map_account(
    firm_account_code: str,
    firm_id: str,
) -> Optional[str]:
    """
    Look up the internal account code for a firm's account code.

    Returns:
        The internal_account_code string if MAPPED and CPA-confirmed.
        None if UNMAPPED or not yet confirmed — caller should defer the record.

    Side effects:
        - Creates an UNMAPPED placeholder if no mapping exists.
        - Queues a CPA notification if status is UNMAPPED.
    """
    key = (firm_id, firm_account_code)
    mapping = _mappings.get(key)

    if mapping is None:
        _create_unmapped(firm_id, firm_account_code)
        _queue_cpa_notification(firm_id, firm_account_code)
        return None

    if mapping.status == AccountMappingStatus.UNMAPPED or not mapping.cpa_confirmed:
        _queue_cpa_notification(firm_id, firm_account_code)
        return None

    return mapping.internal_account_code


def set_mapping(
    firm_id: str,
    firm_account_code: str,
    internal_account_code: str,
    cpa_confirmed: bool = False,
) -> AccountMapping:
    """
    Create or update a mapping entry.

    Args:
        firm_id:               The firm this mapping belongs to.
        firm_account_code:     Code from the external firm's chart of accounts.
        internal_account_code: Bit-Counting's internal account code.
        cpa_confirmed:         True once a CPA has verified the mapping.

    Returns:
        The updated AccountMapping.
    """
    key = (firm_id, firm_account_code)
    existing = _mappings.get(key)

    mapping = AccountMapping(
        firm_id=firm_id,
        firm_account_code=firm_account_code,
        internal_account_code=internal_account_code,
        status=AccountMappingStatus.MAPPED if cpa_confirmed else AccountMappingStatus.UNMAPPED,
        cpa_confirmed=cpa_confirmed,
        created_at=existing.created_at if existing else datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    _mappings[key] = mapping

    # If now confirmed, remove from pending notifications
    if cpa_confirmed:
        _pending_notifications.discard(key)

    logger.info(
        "ACCOUNT_MAP: firm=%s code=%s → %s confirmed=%s",
        firm_id, firm_account_code, internal_account_code, cpa_confirmed,
    )
    return mapping


def confirm_mapping(firm_id: str, firm_account_code: str) -> AccountMapping:
    """
    Mark an existing mapping as CPA-confirmed.

    Raises:
        KeyError: If no mapping exists for the given firm/code.
    """
    key = (firm_id, firm_account_code)
    existing = _mappings.get(key)
    if existing is None:
        raise KeyError(f"No mapping found for firm={firm_id} code={firm_account_code}")
    if existing.internal_account_code is None:
        raise ValueError("Cannot confirm a mapping with no internal_account_code set")

    confirmed = AccountMapping(
        firm_id=existing.firm_id,
        firm_account_code=existing.firm_account_code,
        internal_account_code=existing.internal_account_code,
        status=AccountMappingStatus.MAPPED,
        cpa_confirmed=True,
        created_at=existing.created_at,
        updated_at=datetime.now(timezone.utc),
    )
    _mappings[key] = confirmed
    _pending_notifications.discard(key)

    logger.info(
        "ACCOUNT_MAP: Confirmed mapping firm=%s code=%s → %s",
        firm_id, firm_account_code, existing.internal_account_code,
    )
    return confirmed


def get_pending_notifications() -> list:
    """Return list of (firm_id, firm_account_code) tuples awaiting CPA review."""
    return list(_pending_notifications)


def load_mappings_from_db(rows) -> None:
    """
    Populate the in-memory cache from DB rows.
    Call this at startup after querying AppAccountMapping.

    Args:
        rows: Iterable of AppAccountMapping ORM objects or dicts.
    """
    loaded = 0
    for row in rows:
        if hasattr(row, "__dict__"):
            data = {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
        else:
            data = dict(row)

        mapping = AccountMapping(
            firm_id=data["firm_id"],
            firm_account_code=data["firm_account_code"],
            internal_account_code=data.get("internal_account_code"),
            status=AccountMappingStatus(data.get("status", "UNMAPPED")),
            cpa_confirmed=bool(data.get("cpa_confirmed", False)),
            created_at=data.get("created_at", datetime.now(timezone.utc)),
            updated_at=data.get("updated_at"),
        )
        _mappings[(mapping.firm_id, mapping.firm_account_code)] = mapping
        loaded += 1

    logger.info("ACCOUNT_MAP: Loaded %d mappings from DB", loaded)


def get_all_mappings(firm_id: Optional[str] = None) -> list:
    """Return all known mappings, optionally filtered by firm_id."""
    if firm_id is None:
        return [m.model_dump() for m in _mappings.values()]
    return [m.model_dump() for m in _mappings.values() if m.firm_id == firm_id]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _create_unmapped(firm_id: str, firm_account_code: str) -> None:
    """Create an UNMAPPED placeholder so future calls can detect the gap."""
    key = (firm_id, firm_account_code)
    if key in _mappings:
        return
    placeholder = AccountMapping(
        firm_id=firm_id,
        firm_account_code=firm_account_code,
        internal_account_code=None,
        status=AccountMappingStatus.UNMAPPED,
        cpa_confirmed=False,
        created_at=datetime.now(timezone.utc),
    )
    _mappings[key] = placeholder
    logger.warning(
        "ACCOUNT_MAP: No mapping for firm=%s code=%s — created UNMAPPED placeholder",
        firm_id, firm_account_code,
    )


def _queue_cpa_notification(firm_id: str, firm_account_code: str) -> None:
    """Enqueue a CPA notification for an unmapped account code."""
    key = (firm_id, firm_account_code)
    if key not in _pending_notifications:
        _pending_notifications.add(key)
        logger.warning(
            "ACCOUNT_MAP: CPA notification queued — firm=%s unmapped code=%s",
            firm_id, firm_account_code,
        )


async def notify_cpa_unmapped_async(firm_id: str, firm_account_code: str) -> None:
    """
    Async hook: send a CPA notification for an unmapped account code.
    In production this would call the notification service or write to a queue.
    Currently logs and stores in pending set.
    """
    _queue_cpa_notification(firm_id, firm_account_code)
    # TODO: integrate with notification service (email, in-app, etc.)
    logger.info(
        "ACCOUNT_MAP: CPA notification dispatched for firm=%s code=%s",
        firm_id, firm_account_code,
    )
