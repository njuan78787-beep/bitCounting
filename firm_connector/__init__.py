# =============================================================================
# firm_connector/__init__.py
# FirmConnector — bidirectional adapter between Bit-Counting and external
# accounting firm databases.
#
# Supports three connection modes:
#   MODE 1 — Direct PostgreSQL/MySQL (read-only, pooled, 30s timeout)
#   MODE 2 — OAuth 2.0 REST API (with webhook receiver + polling fallback)
#   MODE 3 — Manual structured import (CSV, Excel, JSON, XML)
#
# ACCESS CONTROL (CAPA 1 — EXIMIA_ADMIN only):
#   All write/mutating operations require EXIMIA_ADMIN role.
#   CPA_SENIOR and CPA_PARTNER can read sync results and account mappings
#   via CAPA 2 (CPA Dashboard) but cannot configure or trigger connections.
#
# Security:
#   - SSN / EIN / EIN-federal always encrypted with AES-256-GCM
#   - Bit-Counting NEVER writes to the firm's own database
#   - Every access recorded in append-only audit trail
#   - Connection revocable in < 60 s from the admin panel
# =============================================================================

from .connector import FirmConnector
from .models import (
    AccountMapping,
    AccountMappingStatus,
    ClientFirm,
    DateRange,
    DBDirectConfig,
    EmployeeFirm,
    EmployeeImportRow,
    FirmConfig,
    FirmConnectionMode,
    ImportFormat,
    OAuthConfig,
    RecordError,
    SyncResult,
    SyncStatus,
    TransactionFirm,
)
from .sync import PermissionDeniedError, sync_firm_data
from .encryption import decrypt_field, encrypt_field, mask_for_log
from .audit import log_access_async
from .account_mapper import map_account, set_mapping, confirm_mapping

__all__ = [
    # Façade
    "FirmConnector",
    # Models
    "AccountMapping",
    "AccountMappingStatus",
    "ClientFirm",
    "DateRange",
    "DBDirectConfig",
    "EmployeeFirm",
    "EmployeeImportRow",
    "FirmConfig",
    "FirmConnectionMode",
    "ImportFormat",
    "OAuthConfig",
    "RecordError",
    "SyncResult",
    "SyncStatus",
    "TransactionFirm",
    # Core functions
    "sync_firm_data",
    "PermissionDeniedError",
    # Encryption
    "encrypt_field",
    "decrypt_field",
    "mask_for_log",
    # Audit
    "log_access_async",
    # Account mapping
    "map_account",
    "set_mapping",
    "confirm_mapping",
]

