# =============================================================================
# api/db/models.py
# SQLAlchemy 2.0 ORM models for Bit-Counting.
#
# TABLE NAMING:
#   All tables use the "app_" prefix to co-exist with the reference schema.sql
#   tables that are managed separately (cpa_partners, clients, etc.).
#
# IMPORTANT:
#   - Models must be imported before Base.metadata.create_all() is called.
#   - Import this module in api/database.py create_tables() to trigger registration.
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())

def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# AppUser — authentication store
# (separate from schema.sql cpa_partners / clients)
# ---------------------------------------------------------------------------

class AppUser(Base):
    """
    Stores auth credentials for all system users (clients, CPAs, admin).
    Phase 3 can link this to cpa_partners / clients via user_id FK.
    """
    __tablename__ = "app_users"

    id:            Mapped[str]            = mapped_column(String(36), primary_key=True, default=_uuid)
    username:      Mapped[str]            = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str]            = mapped_column(String(64),  nullable=False)  # SHA-256 hex
    role:          Mapped[str]            = mapped_column(String(20),  nullable=False)  # Role enum value
    client_id:     Mapped[Optional[str]]  = mapped_column(String(36),  nullable=True)   # UUID of linked client
    totp_secret:   Mapped[str]            = mapped_column(String(64),  nullable=False)
    mfa_enabled:   Mapped[bool]           = mapped_column(Boolean,     default=True,  nullable=False)
    is_active:     Mapped[bool]           = mapped_column(Boolean,     default=True,  nullable=False)
    created_at:    Mapped[datetime]       = mapped_column(DateTime,    default=_now,  nullable=False)
    updated_at:    Mapped[datetime]       = mapped_column(DateTime,    default=_now,  onupdate=_now, nullable=False)


# ---------------------------------------------------------------------------
# AppTransaction — financial transactions
# ---------------------------------------------------------------------------

class AppTransaction(Base):
    """
    Financial transactions processed by the autonomous agent pipeline.
    Extends schema.sql transactions with API-specific fields (vendor, confidence).
    """
    __tablename__ = "app_transactions"

    transaction_id: Mapped[str]            = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id:      Mapped[str]            = mapped_column(String(36), nullable=False, index=True)
    vendor:         Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    amount:         Mapped[Decimal]        = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    date:           Mapped[Optional[str]]  = mapped_column(String(10),  nullable=True)   # YYYY-MM-DD
    status:         Mapped[str]            = mapped_column(String(20),  nullable=False, default="pending", index=True)
    account_code:   Mapped[Optional[str]]  = mapped_column(String(20),  nullable=True)
    account_name:   Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    confidence:     Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    source_format:  Mapped[Optional[str]]  = mapped_column(String(50),  nullable=True)
    extra:          Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime,    default=_now,  nullable=False)
    updated_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


# ---------------------------------------------------------------------------
# AppDocument — document intake records
# ---------------------------------------------------------------------------

class AppDocument(Base):
    """Processed document records from INTAKE + CENTINELA pipeline."""
    __tablename__ = "app_documents"

    document_id:         Mapped[str]            = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id:           Mapped[str]            = mapped_column(String(36), nullable=False, index=True)
    status:              Mapped[str]            = mapped_column(String(20), nullable=False, default="pending")
    centinela_decision:  Mapped[Optional[str]]  = mapped_column(String(20), nullable=True)
    pause_id:            Mapped[Optional[str]]  = mapped_column(String(36), nullable=True)
    intake_result:       Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at:          Mapped[datetime]        = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:          Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


# ---------------------------------------------------------------------------
# AppCentinaelaPause — CENTINELA supervision pauses
# ---------------------------------------------------------------------------

class AppCentinaelaPause(Base):
    """
    Supervision pauses created when autonomous agent confidence is too low
    or conflicting rules are detected.
    """
    __tablename__ = "app_centinela_pauses"

    pause_id:                  Mapped[str]            = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id:                 Mapped[Optional[str]]  = mapped_column(String(36), nullable=True, index=True)
    trigger_type:              Mapped[str]            = mapped_column(String(100), nullable=False)
    affected_transaction_ids:  Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    conflicting_rules:         Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    interpretations:           Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    pre_processed_analysis:    Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    sla_hours:                 Mapped[int]            = mapped_column(Integer, default=48, nullable=False)
    sla_deadline:              Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status:                    Mapped[str]            = mapped_column(String(20), nullable=False, default="ACTIVE", index=True)
    assigned_cpa_license:      Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    resolved_by:               Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    resolution_notes:          Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    chosen_instruction:        Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    resolved_at:               Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:                Mapped[datetime]       = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:                Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


# ---------------------------------------------------------------------------
# AppReviewQueueItem — items awaiting CPA review
# ---------------------------------------------------------------------------

class AppReviewQueueItem(Base):
    """Items placed in the CPA review queue by the orchestrator."""
    __tablename__ = "app_review_queue"

    item_id:           Mapped[str]            = mapped_column(String(36), primary_key=True, default=_uuid)
    item_type:         Mapped[str]            = mapped_column(String(50), nullable=False)
    transaction_id:    Mapped[Optional[str]]  = mapped_column(String(36), nullable=True, index=True)
    client_id:         Mapped[Optional[str]]  = mapped_column(String(36), nullable=True, index=True)
    vendor:            Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    amount:            Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    description:       Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    consequence_level: Mapped[str]            = mapped_column(String(10), nullable=False, default="LOW")
    sla_deadline:      Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status:            Mapped[str]            = mapped_column(String(30), nullable=False, default="pending_review", index=True)
    detail:            Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    resolved_by:       Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    resolution_notes:  Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    resolved_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:        Mapped[datetime]       = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:        Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


# ---------------------------------------------------------------------------
# AppPolicyDraft — pending CPA policy drafts
# ---------------------------------------------------------------------------

class AppPolicyDraft(Base):
    """
    Draft policies produced by the INTERPRETE agent from CPA natural language
    instructions; awaiting CPA confirmation before activation.
    """
    __tablename__ = "app_policy_drafts"

    draft_id:              Mapped[str]            = mapped_column(String(36), primary_key=True, default=_uuid)
    cpa_license:           Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    instruction_text:      Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    policy_description:    Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    examples:              Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    rules_json:            Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    awaiting_confirmation: Mapped[bool]           = mapped_column(Boolean, default=True, nullable=False)
    status:                Mapped[str]            = mapped_column(String(30), nullable=False, default="pending_confirmation")
    resolves_pause_id:     Mapped[Optional[str]]  = mapped_column(String(36), nullable=True)
    activated_by:          Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    activated_at:          Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rejection_reason:      Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    rejected_by:           Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    rejected_at:           Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:            Mapped[datetime]       = mapped_column(DateTime, default=_now, nullable=False)


# ---------------------------------------------------------------------------
# AppNormativeUpdate — regulatory change tracking
# ---------------------------------------------------------------------------

class AppNormativeUpdate(Base):
    """
    Normative updates detected from the 7 monitored Puerto Rico regulatory
    sources. Require CPA review before being applied to the tax rules registry.
    """
    __tablename__ = "app_normative_updates"

    update_id:      Mapped[str]            = mapped_column(String(36), primary_key=True, default=_uuid)
    source:         Mapped[str]            = mapped_column(String(255), nullable=False)
    title:          Mapped[str]            = mapped_column(String(500), nullable=False)
    description:    Mapped[str]            = mapped_column(Text, nullable=False)
    effective_date: Mapped[Optional[str]]  = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    detected_at:    Mapped[datetime]       = mapped_column(DateTime, default=_now, nullable=False, index=True)
    status:         Mapped[str]            = mapped_column(String(20), nullable=False, default="pending", index=True)
    affected_rules: Mapped[Optional[List[str]]] = mapped_column(JSONB, nullable=True)
    approved_by_cpa: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approved_at:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approval_notes: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    created_at:     Mapped[datetime]       = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


# ---------------------------------------------------------------------------
# AppApprovalSession — tracks CPA friction timing (in-flight approvals)
# ---------------------------------------------------------------------------

class AppApprovalSession(Base):
    """
    Tracks in-flight CPA approval sessions for friction measurement.
    A session starts on the first call to approve an item; latency is measured
    on the final confirmation call.
    """
    __tablename__ = "app_approval_sessions"

    id:           Mapped[str]  = mapped_column(String(36), primary_key=True, default=_uuid)
    cpa_license:  Mapped[str]  = mapped_column(String(100), nullable=False, index=True)
    item_id:      Mapped[str]  = mapped_column(String(36),  nullable=False, index=True)
    level:        Mapped[str]  = mapped_column(String(10),  nullable=False)
    started_at:   Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


# =============================================================================
# FirmConnector tables  (all prefixed app_firm_*)
# Owned entirely by the firm_connector module.
# Write access: EXIMIA_ADMIN only (CAPA 1).
# Read access:  EXIMIA_ADMIN + CPA_SENIOR/CPA_PARTNER for sync results (CAPA 2).
# =============================================================================

class AppFirmConfig(Base):
    """
    Master configuration record for a connected external accounting firm.
    Sensitive credential fields (passwords, secrets) are stored encrypted.
    """
    __tablename__ = "app_firm_configs"

    firm_id:    Mapped[str]  = mapped_column(String(36),  primary_key=True)
    firm_name:  Mapped[str]  = mapped_column(String(255), nullable=False)
    mode:       Mapped[str]  = mapped_column(String(20),  nullable=False)   # FirmConnectionMode value
    is_active:  Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str]  = mapped_column(String(36),  nullable=False)   # EXIMIA_ADMIN user_id
    # Serialized JSON of DBDirectConfig or OAuthConfig (no plaintext secrets)
    config_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


class AppSyncLog(Base):
    """
    Append-only log of every sync_firm_data() execution.
    Contains counts only — no field values, no SSN/EIN.
    """
    __tablename__ = "app_sync_logs"

    sync_id:               Mapped[str]  = mapped_column(String(36), primary_key=True)
    firm_id:               Mapped[str]  = mapped_column(String(36), nullable=False, index=True)
    status:                Mapped[str]  = mapped_column(String(20), nullable=False)
    mode_used:             Mapped[str]  = mapped_column(String(20), nullable=False)
    started_at:            Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    completed_at:          Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    clients_imported:      Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    clients_rejected:      Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    transactions_imported: Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    transactions_rejected: Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    transactions_skipped:  Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    employees_imported:    Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    employees_rejected:    Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    rejected_records:      Mapped[Optional[List[Any]]] = mapped_column(JSONB, nullable=True)
    error_message:         Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    is_cached_data:        Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AppAccountMapping(Base):
    """
    Maps a firm's account code to Bit-Counting's internal chart of accounts.
    Must be CPA-confirmed before the mapped code is used in processing.
    """
    __tablename__ = "app_account_mappings"

    id:                    Mapped[str]  = mapped_column(String(36), primary_key=True, default=_uuid)
    firm_id:               Mapped[str]  = mapped_column(String(36), nullable=False, index=True)
    firm_account_code:     Mapped[str]  = mapped_column(String(100), nullable=False)
    internal_account_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status:                Mapped[str]  = mapped_column(String(20), nullable=False, default="UNMAPPED")
    cpa_confirmed:         Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at:            Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:            Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


class AppFirmAuditLog(Base):
    """
    Append-only audit trail for all FirmConnector access.
    No sensitive field values — actor, action, outcome, and count only.
    """
    __tablename__ = "app_firm_audit_log"

    entry_id:         Mapped[str]  = mapped_column(String(36), primary_key=True)
    actor_id:         Mapped[str]  = mapped_column(String(36),  nullable=False, index=True)
    actor_role:       Mapped[str]  = mapped_column(String(20),  nullable=False)
    firm_id:          Mapped[str]  = mapped_column(String(36),  nullable=False, index=True)
    action:           Mapped[str]  = mapped_column(String(50),  nullable=False)
    justification:    Mapped[str]  = mapped_column(Text,        nullable=False)
    outcome:          Mapped[str]  = mapped_column(String(20),  nullable=False, default="SUCCESS")
    records_affected: Mapped[int]  = mapped_column(Integer,     default=0, nullable=False)
    timestamp:        Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    detail:           Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AppFirmClient(Base):
    """
    Client records imported from external accounting firms.
    ssn_or_ein_federal_encrypted stores AES-256-GCM ciphertext — never plaintext.
    """
    __tablename__ = "app_firm_clients"

    id:                          Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    firm_id:                     Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    firm_client_id:              Mapped[str] = mapped_column(String(100), nullable=False)
    business_name:               Mapped[str] = mapped_column(String(500), nullable=False)
    ein_pr:                      Mapped[str] = mapped_column(String(20),  nullable=False)
    ssn_or_ein_federal_encrypted: Mapped[str] = mapped_column(Text, nullable=False)  # AES-256-GCM
    business_type:               Mapped[str] = mapped_column(String(20),  nullable=False)
    municipality_pr:             Mapped[str] = mapped_column(String(100), nullable=False)
    tax_year:                    Mapped[int] = mapped_column(Integer,     nullable=False)
    accounting_method:           Mapped[str] = mapped_column(String(10),  nullable=False)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


class AppFirmTransaction(Base):
    """
    Transaction records imported from external accounting firms.
    Non-sensitive — no field-level encryption required.
    """
    __tablename__ = "app_firm_transactions"

    id:                   Mapped[str]  = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id:       Mapped[str]  = mapped_column(String(100), nullable=False, unique=True, index=True)
    firm_id:              Mapped[str]  = mapped_column(String(36),  nullable=False, index=True)
    firm_client_id:       Mapped[str]  = mapped_column(String(100), nullable=False, index=True)
    date:                 Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    description:          Mapped[str]  = mapped_column(Text, nullable=False)
    amount:               Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    transaction_type:     Mapped[str]  = mapped_column(String(20), nullable=False)
    firm_account_code:    Mapped[str]  = mapped_column(String(100), nullable=False)
    internal_account_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    vendor_or_client:     Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    invoice_number:       Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ivu_collected:        Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    ivu_paid:             Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    is_payroll:           Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class AppFirmEmployee(Base):
    """
    Employee payroll records imported from external accounting firms.
    name_encrypted and ssn_encrypted store AES-256-GCM ciphertext — never plaintext.
    """
    __tablename__ = "app_firm_employees"

    id:                    Mapped[str]  = mapped_column(String(36), primary_key=True, default=_uuid)
    firm_id:               Mapped[str]  = mapped_column(String(36),  nullable=False, index=True)
    employee_id:           Mapped[str]  = mapped_column(String(100), nullable=False)
    firm_client_id:        Mapped[str]  = mapped_column(String(100), nullable=False, index=True)
    name_encrypted:        Mapped[str]  = mapped_column(Text, nullable=False)   # AES-256-GCM
    ssn_encrypted:         Mapped[str]  = mapped_column(Text, nullable=False)   # AES-256-GCM — NEVER plaintext
    hire_date:             Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    termination_date:      Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    pay_type:              Mapped[str]  = mapped_column(String(10),  nullable=False)
    pay_rate:              Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    filing_status_federal: Mapped[str]  = mapped_column(String(20),  nullable=False)
    allowances_federal:    Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    filing_status_pr:      Mapped[str]  = mapped_column(String(20),  nullable=False)
    allowances_pr:         Mapped[int]  = mapped_column(Integer, default=0, nullable=False)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at:  Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)


# =============================================================================
# TaxFormEngine tables  (app_tax_*)
# Generated by TaxFormEngine; reviewed and signed by CPAs in CAPA 2.
# Write: TaxFormEngine (internal). Read: CPA_SENIOR, CPA_PARTNER, EXIMIA_ADMIN.
# =============================================================================

class AppTaxForm(Base):
    """
    A generated Puerto Rico tax form awaiting CPA review and digital signature.

    lines_json stores computed FormLine list; summary_json stores form totals.
    signature_json is set once a CPA digitally signs — form becomes immutable.
    """
    __tablename__ = "app_tax_forms"

    form_id:               Mapped[str]  = mapped_column(String(36),  primary_key=True)
    form_type:             Mapped[str]  = mapped_column(String(20),  nullable=False, index=True)
    form_status:           Mapped[str]  = mapped_column(String(30),  nullable=False, default="DRAFT", index=True)
    tax_period:            Mapped[str]  = mapped_column(String(20),  nullable=False)
    client_id:             Mapped[str]  = mapped_column(String(36),  nullable=False, index=True)
    business_name:         Mapped[str]  = mapped_column(String(500), nullable=False)
    ein_pr:                Mapped[str]  = mapped_column(String(20),  nullable=False)
    tax_year:              Mapped[int]  = mapped_column(Integer,     nullable=False, index=True)
    period_start:          Mapped[str]  = mapped_column(String(10),  nullable=False)
    period_end:            Mapped[str]  = mapped_column(String(10),  nullable=False)
    fiscal_result_ids:     Mapped[Optional[List[Any]]] = mapped_column(JSONB, nullable=True)
    generated_at:          Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    generated_by:          Mapped[str]  = mapped_column(String(100), nullable=False, default="TaxFormEngine/1.0")
    lines_json:            Mapped[Optional[List[Any]]] = mapped_column(JSONB, nullable=True)
    summary_json:          Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    assigned_cpa_license:  Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    reviewed_at:           Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    review_notes:          Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    signature_json:        Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    submitted_at:          Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    submission_ref:        Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    amends_form_id:        Mapped[Optional[str]] = mapped_column(String(36),  nullable=True)
    updated_at:            Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=_now, nullable=True)
