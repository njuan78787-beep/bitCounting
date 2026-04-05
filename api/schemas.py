# =============================================================================
# api/schemas.py
# Pydantic schemas for Bit-Counting API request/response bodies.
#
# These are separate from agents/messages.py (which defines inter-agent
# communication).  These schemas define the HTTP contract for external
# consumers (React dashboard, CPA portal, integrations).
#
# Naming convention:
#   *Request  — inbound from HTTP client
#   *Response — outbound to HTTP client
# =============================================================================

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# ENUMS
# ---------------------------------------------------------------------------

class DocumentStatus(str, Enum):
    PROCESSING = "processing"
    PROCESSED  = "processed"
    PAUSED     = "paused"
    FAILED     = "failed"


class TransactionStatus(str, Enum):
    PENDING   = "pending"
    PROCESSED = "processed"
    PAUSED    = "paused"
    REJECTED  = "rejected"


class ApprovalAction(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class FrictionLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class NormativeUpdateStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# DOCUMENT SCHEMAS
# ---------------------------------------------------------------------------

class DocumentProcessRequest(BaseModel):
    """Request body for POST /api/v1/documents/process-text."""
    raw_text: str = Field(
        min_length=1,
        description="Raw text content of the document to process",
    )
    client_id: str = Field(
        description="UUID of the client this document belongs to",
    )
    source_format: str = Field(
        default="UNKNOWN",
        description="Source format of the original document (PDF, CSV, JSON, etc.)",
    )


class DocumentProcessResponse(BaseModel):
    """Response body for document processing endpoints."""
    document_id: str = Field(description="UUID assigned to this document")
    status: DocumentStatus = Field(description="Current processing status")
    intake_result: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured intake output if processing is complete",
    )
    centinela_decision: Optional[str] = Field(
        default=None,
        description="PROCEED or PAUSE decision from CENTINELA",
    )
    pause_id: Optional[str] = Field(
        default=None,
        description="pause_id if processing is paused for CPA review",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.utcnow(),
        description="Timestamp when the document was submitted",
    )


class DocumentStatusResponse(BaseModel):
    """Response body for GET /api/v1/documents/{document_id}/status."""
    document_id: str
    status: DocumentStatus
    intake_result: Optional[dict[str, Any]] = None
    centinela_decision: Optional[str] = None
    pause_id: Optional[str] = None
    processing_log: list[str] = Field(
        default_factory=list,
        description="Ordered log of processing steps completed so far",
    )
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# TRANSACTION SCHEMAS
# ---------------------------------------------------------------------------

class TransactionCreateRequest(BaseModel):
    """Request body for POST /api/v1/transactions (manual creation)."""
    client_id: str
    vendor: str
    amount: Decimal = Field(ge=Decimal("0.01"))
    date: str = Field(description="ISO 8601 date string YYYY-MM-DD")
    description: str = Field(default="")
    source_format: str = Field(default="JSON")
    payment_method: Optional[str] = None
    tax_amount: Optional[Decimal] = Field(default=None, ge=Decimal("0.00"))


class TransactionResponse(BaseModel):
    """Representation of a single transaction."""
    transaction_id: str
    client_id: str
    vendor: Optional[str] = None
    amount: Decimal
    date: Optional[str] = None
    status: TransactionStatus
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    confidence: Optional[Decimal] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class TransactionListResponse(BaseModel):
    """Paginated list of transactions."""
    items: list[TransactionResponse]
    total: int
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


# ---------------------------------------------------------------------------
# JOURNAL ENTRY SCHEMAS
# ---------------------------------------------------------------------------

class JournalEntryResponse(BaseModel):
    """A single line in a double-entry journal."""
    entry_id: str
    transaction_id: str
    account_code: str
    account_name: str
    entry_type: str = Field(description="'debit' or 'credit'")
    amount: Decimal
    rule_ref: str
    reasoning: str
    contra_account_code: Optional[str] = None
    contra_account_name: Optional[str] = None
    created_at: datetime


class TaxAnalysisResponse(BaseModel):
    """FISCAL PR tax analysis for a transaction."""
    transaction_id: str
    tax_type: str
    tax_liability: Decimal
    taxable_base: Decimal
    form_id: str
    rule_ref: str
    rate_version: str
    calc_hash: str
    period_from: str
    period_to: str
    exemptions_applied: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CPA DASHBOARD SCHEMAS
# ---------------------------------------------------------------------------

class PauseResponse(BaseModel):
    """Representation of a CENTINELA pause for the CPA dashboard."""
    pause_id: str
    trigger_type: str
    affected_transaction_ids: list[str]
    conflicting_rules: list[str]
    interpretations: list[str]
    pre_processed_analysis: str
    sla_hours: int
    sla_deadline: Optional[datetime] = None
    status: str = Field(description="ACTIVE or RESOLVED")
    created_at: datetime
    assigned_cpa_license: Optional[str] = None


class CPAApprovalRequest(BaseModel):
    """Request body for POST /api/v1/cpa/approve/{item_id}."""
    cpa_license: str = Field(description="CPA license number")
    cpa_token: str = Field(description="Authentication token for the CPA")
    action: ApprovalAction = Field(description="approved or rejected")
    notes: str = Field(default="", description="CPA notes on the decision")
    # For HIGH-friction items: the answer to the challenge question
    challenge_answer: Optional[str] = Field(
        default=None,
        description="Answer to friction challenge question (required for HIGH items)",
    )
    # For MEDIUM-friction items: confirm after expanding detail
    detail_confirmed: Optional[bool] = Field(
        default=None,
        description="Explicit confirmation that CPA expanded and read the details (MEDIUM items)",
    )


class PauseResolveRequest(BaseModel):
    """Request body for POST /api/v1/cpa/pauses/{pause_id}/resolve."""
    cpa_license: str
    cpa_token: str
    resolution_notes: str = Field(min_length=10)
    instruction: str = Field(
        description="The interpretation chosen by the CPA from the pause's interpretations list",
    )


class CPAMetricsResponse(BaseModel):
    """CPA performance and vigilance metrics."""
    cpa_license: str
    pauses_resolved_today: int
    avg_response_time_hours: Decimal
    approval_accuracy_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    suspicious_fast_approvals: int = Field(
        description="Approvals completed in < 5s on HIGH-consequence items",
    )
    random_verification_accuracy: Optional[Decimal] = None


class CPAInstructionRequest(BaseModel):
    """Request body for POST /api/v1/cpa/instructions."""
    instruction_text: str = Field(min_length=10)
    cpa_license: str
    resolves_pause_id: Optional[str] = None


class PolicyDraftResponse(BaseModel):
    """INTERPRETE-generated policy draft returned to CPA for confirmation."""
    draft_id: str
    policy_description: str
    examples: list[str] = Field(
        min_length=3,
        max_length=3,
        description="Exactly 3 concrete examples of how the policy would apply",
    )
    rules_json: dict[str, Any]
    awaiting_confirmation: bool = Field(default=True)
    created_at: datetime


class PolicyDraftConfirmRequest(BaseModel):
    """Request body for POST /api/v1/cpa/instructions/{draft_id}/confirm."""
    cpa_license: str
    cpa_token: str
    confirmed: bool = Field(description="True = activate policy; False = reject draft")
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Reason for rejection if confirmed=False",
    )


# ---------------------------------------------------------------------------
# REPORT SCHEMAS
# ---------------------------------------------------------------------------

class ReportRequest(BaseModel):
    """Generic report request parameters (used internally; query params in routes)."""
    client_id: str
    as_of_date: Optional[str] = Field(
        default=None,
        description="ISO 8601 date for point-in-time reports (balance sheet)",
    )
    period_start: Optional[str] = Field(
        default=None,
        description="ISO 8601 start date for period reports",
    )
    period_end: Optional[str] = Field(
        default=None,
        description="ISO 8601 end date for period reports",
    )


class BalanceSheetResponse(BaseModel):
    """Balance sheet financial report."""
    client_id: str
    as_of_date: str
    assets: dict[str, Any] = Field(description="Current and non-current assets")
    liabilities: dict[str, Any] = Field(description="Current and long-term liabilities")
    equity: dict[str, Any] = Field(description="Owner's equity components")
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    generated_at: datetime


class IncomeStatementResponse(BaseModel):
    """Income statement (profit & loss) report."""
    client_id: str
    period_start: str
    period_end: str
    revenue: dict[str, Any]
    cost_of_goods_sold: dict[str, Any]
    gross_profit: Decimal
    operating_expenses: dict[str, Any]
    operating_income: Decimal
    other_income_expense: dict[str, Any]
    net_income: Decimal
    generated_at: datetime


class CashFlowResponse(BaseModel):
    """Cash flow statement report."""
    client_id: str
    period_start: str
    period_end: str
    operating_activities: dict[str, Any]
    investing_activities: dict[str, Any]
    financing_activities: dict[str, Any]
    net_change_in_cash: Decimal
    beginning_cash: Decimal
    ending_cash: Decimal
    generated_at: datetime


class IVUSummaryResponse(BaseModel):
    """IVU (Puerto Rico sales tax) summary for SC 2915 form."""
    client_id: str
    period: str = Field(description="Period in YYYY-MM format")
    ivu_collected: Decimal = Field(description="Total IVU collected from customers")
    ivu_remitted: Decimal = Field(description="Total IVU remitted to Hacienda PR")
    ivu_balance: Decimal = Field(description="ivu_collected - ivu_remitted")
    ivu_rate_municipal: Decimal = Field(default=Decimal("0.01"))
    ivu_rate_state: Decimal = Field(default=Decimal("0.105"))
    transactions_count: int
    form_sc2915_ready: bool = Field(
        description="True if all required data is present for SC 2915 filing",
    )
    generated_at: datetime


# ---------------------------------------------------------------------------
# NORMATIVE SCHEMAS
# ---------------------------------------------------------------------------

class NormativeUpdateResponse(BaseModel):
    """A pending normative update detected by the monitor."""
    update_id: str
    source: str = Field(
        description="One of the 7 normative sources: Hacienda, CRIM, Municipio, etc.",
    )
    title: str
    description: str
    effective_date: Optional[str] = None
    detected_at: datetime
    status: NormativeUpdateStatus
    affected_rules: list[str] = Field(
        default_factory=list,
        description="rule_ids from tax_rules that this update affects",
    )
    approved_by_cpa: Optional[str] = None
    approved_at: Optional[datetime] = None


class NormativeApproveRequest(BaseModel):
    """Request body for POST /api/v1/normative/updates/{update_id}/approve."""
    cpa_license: str
    cpa_token: str
    approval_notes: str = Field(default="")


# ---------------------------------------------------------------------------
# EXPORTS
# ---------------------------------------------------------------------------

__all__ = [
    "DocumentStatus",
    "TransactionStatus",
    "ApprovalAction",
    "FrictionLevel",
    "NormativeUpdateStatus",
    "DocumentProcessRequest",
    "DocumentProcessResponse",
    "DocumentStatusResponse",
    "TransactionCreateRequest",
    "TransactionResponse",
    "TransactionListResponse",
    "JournalEntryResponse",
    "TaxAnalysisResponse",
    "PauseResponse",
    "CPAApprovalRequest",
    "PauseResolveRequest",
    "CPAMetricsResponse",
    "CPAInstructionRequest",
    "PolicyDraftResponse",
    "PolicyDraftConfirmRequest",
    "ReportRequest",
    "BalanceSheetResponse",
    "IncomeStatementResponse",
    "CashFlowResponse",
    "IVUSummaryResponse",
    "NormativeUpdateResponse",
    "NormativeApproveRequest",
]
