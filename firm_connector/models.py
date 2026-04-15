# =============================================================================
# firm_connector/models.py
# Pydantic schemas for all data exchanged between Bit-Counting and
# external accounting firm systems.
#
# SECURITY NOTE:
#   Fields named *_encrypted (ssn_encrypted, ein_federal_encrypted) store
#   AES-256-GCM ciphertext — never the plaintext value.
#   The plain equivalents (ssn, ssn_or_ein_federal) exist only transiently
#   during import and are never persisted or logged.
# =============================================================================

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class FirmConnectionMode(str, Enum):
    DB_DIRECT     = "DB_DIRECT"    # Mode 1: direct PostgreSQL/MySQL connection
    REST_API      = "REST_API"     # Mode 2: OAuth 2.0 REST API
    MANUAL_IMPORT = "MANUAL_IMPORT"  # Mode 3: file upload (CSV/Excel/JSON/XML)


class BusinessType(str, Enum):
    CORPORATION  = "CORPORATION"
    LLC          = "LLC"
    SOLE_PROP    = "SOLE_PROP"
    PARTNERSHIP  = "PARTNERSHIP"
    NONPROFIT    = "NONPROFIT"


class TransactionType(str, Enum):
    INCOME    = "INCOME"
    EXPENSE   = "EXPENSE"
    ASSET     = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY    = "EQUITY"


class PayType(str, Enum):
    HOURLY = "HOURLY"
    SALARY = "SALARY"


class AccountingMethod(str, Enum):
    CASH    = "CASH"
    ACCRUAL = "ACCRUAL"


class SyncStatus(str, Enum):
    SUCCESS         = "SUCCESS"
    PARTIAL         = "PARTIAL"   # some records rejected
    FAILED          = "FAILED"    # complete failure (firm unreachable, etc.)
    CACHED_FALLBACK = "CACHED_FALLBACK"  # firm down, using last sync


class AccountMappingStatus(str, Enum):
    MAPPED   = "MAPPED"
    UNMAPPED = "UNMAPPED"   # no mapping configured — CPA notification sent


class ImportFormat(str, Enum):
    CSV   = "CSV"
    EXCEL = "EXCEL"
    JSON  = "JSON"
    XML   = "XML"


# ---------------------------------------------------------------------------
# Firm configuration
# ---------------------------------------------------------------------------

class DBDirectConfig(BaseModel):
    """Connection parameters for Mode 1 (direct database)."""
    model_config = ConfigDict(frozen=True)

    host:          str
    port:          int   = 5432
    database:      str
    username:      str
    # password is stored encrypted in the DB; this field holds the ciphertext
    password_encrypted: str
    db_type:       str   = "postgresql"   # "postgresql" | "mysql"
    ssl_mode:      str   = "require"
    max_pool_size: int   = Field(default=5, le=5)   # hard cap at 5
    timeout_s:     int   = 30


class OAuthConfig(BaseModel):
    """OAuth 2.0 credentials for Mode 2 (REST API)."""
    model_config = ConfigDict(frozen=True)

    token_url:              str
    client_id:              str
    client_secret_encrypted: str   # encrypted AES-256-GCM
    base_api_url:           str
    webhook_secret_encrypted: Optional[str] = None
    rate_limit_rps:         int   = 10   # requests per second ceiling
    polling_interval_min:   int   = 15   # fallback polling period


class FirmConfig(BaseModel):
    """
    Master configuration record for a connected external firm.
    Stored in app_firm_configs DB table.
    """
    model_config = ConfigDict(frozen=True)

    firm_id:      str              # unique identifier for this firm connection
    firm_name:    str
    mode:         FirmConnectionMode
    is_active:    bool             = True

    # Only one of these will be set depending on mode
    db_config:    Optional[DBDirectConfig]  = None
    api_config:   Optional[OAuthConfig]     = None

    created_by:   str              # user_id of EXIMIA_ADMIN who added the firm
    created_at:   datetime         = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def _check_config_present(self) -> "FirmConfig":
        if self.mode == FirmConnectionMode.DB_DIRECT and not self.db_config:
            raise ValueError("db_config is required for DB_DIRECT mode")
        if self.mode == FirmConnectionMode.REST_API and not self.api_config:
            raise ValueError("api_config is required for REST_API mode")
        return self


# ---------------------------------------------------------------------------
# Client schema (clients_firm)
# ---------------------------------------------------------------------------

class ClientFirm(BaseModel):
    """
    Client record received from the external firm system.
    ssn_or_ein_federal_encrypted holds the AES-256-GCM ciphertext —
    the plaintext field is transient and never persisted.
    """
    model_config = ConfigDict(frozen=True)

    firm_client_id:              str
    bit_counting_client_id:      Optional[str]   = None   # set after mapping
    business_name:               str
    ein_pr:                      str             # Employer Identification Number PR
    ssn_or_ein_federal_encrypted: str            # AES-256-GCM ciphertext — NEVER plaintext
    business_type:               BusinessType
    naics_code:                  Optional[str]   = None
    municipality_pr:             str
    fiscal_year_end:             date
    act_60_decree:               bool            = False
    act_60_decree_number:        Optional[str]   = None
    ivu_merchant_number:         str
    employer_registration_pr:    str
    tax_year:                    int
    accounting_method:           AccountingMethod
    cpa_assigned_id:             Optional[str]   = None

    @field_validator("ein_pr")
    @classmethod
    def _validate_ein_pr(cls, v: str) -> str:
        cleaned = re.sub(r"[^0-9]", "", v)
        if len(cleaned) < 9:
            raise ValueError(f"EIN PR must have at least 9 digits, got: {mask_for_log(v)}")
        return cleaned

    @field_validator("municipality_pr")
    @classmethod
    def _validate_municipality(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("municipality_pr cannot be empty")
        return v.strip().title()


def mask_for_log(v: str) -> str:
    """Return last 4 chars only for safe logging."""
    if len(v) <= 4:
        return "****"
    return "*" * (len(v) - 4) + v[-4:]


# ---------------------------------------------------------------------------
# Transaction schema (transactions_firm)
# ---------------------------------------------------------------------------

class TransactionFirm(BaseModel):
    """
    Financial transaction record from the external firm.
    Non-sensitive — no field-level encryption required.
    """
    model_config = ConfigDict(frozen=True)

    transaction_id:      str
    firm_client_id:      str
    date:                date
    description:         str   = Field(min_length=1)
    amount:              Decimal
    type:                TransactionType
    account_code:        str
    vendor_or_client:    Optional[str]   = None
    invoice_number:      Optional[str]   = None
    ivu_collected:       Optional[Decimal] = None
    ivu_paid:            Optional[Decimal] = None
    is_payroll:          bool            = False
    payroll_details:     Optional[Dict[str, Any]] = None
    document_url:        Optional[str]   = None
    already_processed:   bool            = False   # skip if True

    @field_validator("amount")
    @classmethod
    def _amount_not_zero(cls, v: Decimal) -> Decimal:
        if v is None:
            raise ValueError("amount cannot be null")
        return v

    @field_validator("date", mode="before")
    @classmethod
    def _parse_date(cls, v):
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid date format: '{v}' — expected YYYY-MM-DD")
        raise ValueError(f"Cannot parse date from {type(v).__name__}")


# ---------------------------------------------------------------------------
# Employee schema (employees_firm) — HIGHEST SENSITIVITY
# ---------------------------------------------------------------------------

class EmployeeFirm(BaseModel):
    """
    Payroll employee record from the external firm.

    SSN is the most sensitive field in the system:
      - Accepted as plaintext only during import
      - Immediately encrypted before any storage, logging, or serialization
      - ssn_encrypted is what gets stored and serialized
      - plaintext ssn is never retained after the encrypt() call

    INVARIANT: this model's ssn field must NEVER appear in:
      - Log files (any level)
      - Serialized DB output
      - API responses
      - Sync reports
    """
    model_config = ConfigDict(frozen=True)

    employee_id:             str
    firm_client_id:          str
    name_encrypted:          str            # AES-256-GCM ciphertext
    ssn_encrypted:           str            # AES-256-GCM ciphertext — NEVER plaintext
    hire_date:               date
    termination_date:        Optional[date] = None
    pay_type:                PayType
    pay_rate:                Decimal
    filing_status_federal:   str
    allowances_federal:      int            = 0
    filing_status_pr:        str
    allowances_pr:           int            = 0
    ytd_gross:               Decimal        = Decimal("0.00")
    ytd_ss:                  Decimal        = Decimal("0.00")
    ytd_medicare:            Decimal        = Decimal("0.00")
    ytd_federal_tax:         Decimal        = Decimal("0.00")
    ytd_pr_tax:              Decimal        = Decimal("0.00")
    ytd_futa:                Decimal        = Decimal("0.00")
    ytd_suta:                Decimal        = Decimal("0.00")

    def safe_log_repr(self) -> str:
        """Return a sanitized representation safe for log files."""
        return (
            f"EmployeeFirm(id={self.employee_id!r}, "
            f"client={self.firm_client_id!r}, "
            f"ssn=[ENCRYPTED], name=[ENCRYPTED])"
        )


class EmployeeImportRow(BaseModel):
    """
    Transient model used ONLY during file import parsing.
    The plaintext ssn is encrypted immediately; this object is never
    stored or logged.  Use EmployeeFirm for the persistent form.
    """
    model_config = ConfigDict(frozen=True)

    employee_id:           str
    firm_client_id:        str
    name:                  str     # plaintext — encrypt immediately
    ssn:                   str     # plaintext — encrypt immediately — NEVER LOG
    hire_date:             date
    termination_date:      Optional[date] = None
    pay_type:              PayType
    pay_rate:              Decimal
    filing_status_federal: str
    allowances_federal:    int     = 0
    filing_status_pr:      str
    allowances_pr:         int     = 0
    ytd_gross:             Decimal = Decimal("0.00")
    ytd_ss:                Decimal = Decimal("0.00")
    ytd_medicare:          Decimal = Decimal("0.00")
    ytd_federal_tax:       Decimal = Decimal("0.00")
    ytd_pr_tax:            Decimal = Decimal("0.00")
    ytd_futa:              Decimal = Decimal("0.00")
    ytd_suta:              Decimal = Decimal("0.00")

    @field_validator("ssn")
    @classmethod
    def _validate_ssn_format(cls, v: str) -> str:
        digits = re.sub(r"[^0-9]", "", v)
        if len(digits) != 9:
            # Do NOT include the value in the error message
            raise ValueError("SSN must be exactly 9 digits (dashes optional)")
        return digits   # store only digits

    def to_employee_firm(self) -> EmployeeFirm:
        """
        Encrypt sensitive fields and return a storable EmployeeFirm.
        The original EmployeeImportRow (with plaintext ssn/name) should
        be discarded immediately after calling this method.
        """
        from .encryption import encrypt_field
        return EmployeeFirm(
            employee_id=self.employee_id,
            firm_client_id=self.firm_client_id,
            name_encrypted=encrypt_field(self.name),
            ssn_encrypted=encrypt_field(self.ssn),
            hire_date=self.hire_date,
            termination_date=self.termination_date,
            pay_type=self.pay_type,
            pay_rate=self.pay_rate,
            filing_status_federal=self.filing_status_federal,
            allowances_federal=self.allowances_federal,
            filing_status_pr=self.filing_status_pr,
            allowances_pr=self.allowances_pr,
            ytd_gross=self.ytd_gross,
            ytd_ss=self.ytd_ss,
            ytd_medicare=self.ytd_medicare,
            ytd_federal_tax=self.ytd_federal_tax,
            ytd_pr_tax=self.ytd_pr_tax,
            ytd_futa=self.ytd_futa,
            ytd_suta=self.ytd_suta,
        )


# ---------------------------------------------------------------------------
# Sync result
# ---------------------------------------------------------------------------

class RecordError(BaseModel):
    """A single record rejection with sanitized reason."""
    record_id:  str    # transaction_id or employee_id — no sensitive values
    reason:     str    # human-readable rejection cause (no field values)
    field:      Optional[str] = None   # field that caused rejection, if known


class SyncResult(BaseModel):
    """
    Summary of a sync_firm_data() execution.
    Contains only metadata — no field values, no SSN/EIN.
    """
    firm_id:              str
    sync_id:              str   # UUID for this sync run
    status:               SyncStatus
    started_at:           datetime
    completed_at:         Optional[datetime] = None
    mode_used:            FirmConnectionMode

    # Counts only — never actual values
    clients_imported:     int = 0
    clients_rejected:     int = 0
    transactions_imported: int = 0
    transactions_rejected: int = 0
    transactions_skipped:  int = 0   # already_processed == True
    employees_imported:   int = 0
    employees_rejected:   int = 0

    rejected_records:     List[RecordError] = []
    error_message:        Optional[str] = None   # top-level error if FAILED
    is_cached_data:       bool          = False


# ---------------------------------------------------------------------------
# Account mapping
# ---------------------------------------------------------------------------

class AccountMapping(BaseModel):
    """Maps a firm's account code to Bit-Counting's internal account code."""
    firm_id:              str
    firm_account_code:    str
    internal_account_code: Optional[str] = None
    status:               AccountMappingStatus = AccountMappingStatus.UNMAPPED
    cpa_confirmed:        bool          = False
    created_at:           datetime      = Field(default_factory=datetime.utcnow)
    updated_at:           Optional[datetime] = None


# ---------------------------------------------------------------------------
# Date range for sync
# ---------------------------------------------------------------------------

class DateRange(BaseModel):
    start: date
    end:   date

    @model_validator(mode="after")
    def _check_order(self) -> "DateRange":
        if self.start > self.end:
            raise ValueError("start date must be before end date")
        return self
