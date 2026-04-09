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
