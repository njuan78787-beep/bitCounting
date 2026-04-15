# =============================================================================
# tax_form_engine/models.py
# Pydantic schemas for Puerto Rico tax forms.
#
# FORMS SUPPORTED:
#   SC2915   — IVU mensual / trimestral (Hacienda PR)
#   480_20   — Planilla de Corporación (Corporate income tax)
#   941_PR   — Planilla Trimestral de Empleador (Payroll taxes)
#   W2_PR    — 499R-2 / W-2PR (Employee wage statement)
#   AS2879   — Planilla de Desempleo (SUTA, DTRH)
#   480_6A   — Declaración Informativa — Ingresos Pasivos
#   480_6B   — Declaración Informativa — Servicios / Contratistas
#
# OUTPUT FLOW:
#   FiscalResultV2 → TaxFormEngine.generate() → TaxForm (PENDING_CPA_SIGNATURE)
#   CPA reviews in CAPA 2 → signs digitally → TaxForm (SIGNED)
#   TaxForm (SIGNED) → Hacienda PR submission
#
# SECURITY:
#   - No SSN stored in plaintext — ssn_last4 only for display; full SSN encrypted
#   - EIN stored as-is (public entity identifier, not PII)
#   - FormStatus.SIGNED is immutable — once signed, form cannot be altered
# =============================================================================

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class FormType(str, Enum):
    SC2915  = "SC2915"    # IVU mensual/trimestral
    F480_20 = "480_20"    # Planilla corporativa
    F941_PR = "941_PR"    # Planilla trimestral empleador
    W2_PR   = "W2_PR"     # 499R-2 / W-2PR por empleado
    AS2879  = "AS2879"    # Planilla desempleo SUTA
    F480_6A = "480_6A"    # Informativa — ingresos pasivos
    F480_6B = "480_6B"    # Informativa — servicios/contratistas


class FormStatus(str, Enum):
    DRAFT                = "DRAFT"                 # being assembled
    PENDING_CPA_REVIEW   = "PENDING_CPA_REVIEW"    # ready, awaiting CPA
    PENDING_CPA_SIGNATURE= "PENDING_CPA_SIGNATURE" # reviewed, awaiting signature
    SIGNED               = "SIGNED"                # digitally signed by CPA
    SUBMITTED            = "SUBMITTED"             # filed with Hacienda PR
    REJECTED             = "REJECTED"              # Hacienda rejected
    SUPERSEDED           = "SUPERSEDED"            # replaced by amended form


class TaxPeriod(str, Enum):
    MONTHLY    = "MONTHLY"
    QUARTERLY  = "QUARTERLY"
    ANNUAL     = "ANNUAL"


# ---------------------------------------------------------------------------
# Individual form line item
# ---------------------------------------------------------------------------

class FormLine(BaseModel):
    """A single line on a tax form."""
    model_config = ConfigDict(frozen=True)

    line_number:    str             # e.g. "1a", "5", "Part II Line 3"
    description:    str             # label as it appears on the official form
    amount:         Optional[Decimal] = None
    text_value:     Optional[str]   = None   # for non-numeric fields
    is_computed:    bool            = True   # False = requires CPA manual entry
    rule_ref:       Optional[str]   = None   # tax_rules rule_id that drove this value
    sandbox_result_id: Optional[str] = None  # CalculationSandbox result ID
    note:           Optional[str]   = None   # CPA annotation (added during review)


# ---------------------------------------------------------------------------
# CPA signature record
# ---------------------------------------------------------------------------

class CPASignature(BaseModel):
    """Digital signature record attached to a signed form."""
    model_config = ConfigDict(frozen=True)

    cpa_license:    str
    cpa_name:       str
    signed_at:      datetime
    signature_hash: str    # SHA-256(form_id + cpa_license + signed_at.isoformat())
    ip_address:     Optional[str] = None
    declaration:    str = (
        "Bajo penalidad de perjurio, certifico que esta declaración ha sido examinada "
        "por mí y que, a mi mejor entender y creencia, es correcta y completa."
    )


# ---------------------------------------------------------------------------
# Master TaxForm document
# ---------------------------------------------------------------------------

class TaxForm(BaseModel):
    """
    A generated Puerto Rico tax form, ready for CPA review and digital signature.

    Immutable once status=SIGNED — the engine enforces this via FormStatus checks.
    """
    model_config = ConfigDict(frozen=True)

    form_id:        str  = Field(default_factory=lambda: str(uuid.uuid4()))
    form_type:      FormType
    form_status:    FormStatus = FormStatus.DRAFT
    tax_period:     TaxPeriod

    # Taxpayer identity
    client_id:      str             # Bit-Counting client UUID
    business_name:  str
    ein_pr:         str             # 9-digit EIN, public — not PII
    tax_year:       int
    period_start:   date
    period_end:     date

    # Source traceability
    fiscal_result_ids: List[str] = Field(default_factory=list)   # FiscalResultV2 IDs used
    generated_at:   datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    generated_by:   str = "TaxFormEngine/1.0"

    # Form content
    lines:          List[FormLine] = Field(default_factory=list)
    summary:        Dict[str, Any] = Field(default_factory=dict)  # form-specific totals

    # CPA workflow
    assigned_cpa_license:  Optional[str]    = None
    reviewed_at:           Optional[datetime] = None
    review_notes:          Optional[str]    = None
    signature:             Optional[CPASignature] = None
    submitted_at:          Optional[datetime] = None
    submission_ref:        Optional[str]    = None   # Hacienda confirmation number

    # Amendment chain
    amends_form_id:  Optional[str] = None   # if this is an amended return
    amended_by:      Optional[str] = None   # form_id of the amendment


# ---------------------------------------------------------------------------
# Form generation request
# ---------------------------------------------------------------------------

class FormGenerationRequest(BaseModel):
    """Input to TaxFormEngine.generate()."""
    model_config = ConfigDict(frozen=True)

    client_id:          str
    business_name:      str
    ein_pr:             str
    tax_year:           int
    period_start:       date
    period_end:         date
    tax_period:         TaxPeriod
    form_types:         List[FormType]   # which forms to generate
    fiscal_result_ids:  List[str]        # FiscalResultV2 IDs to aggregate
    # Pre-aggregated fiscal data (passed in from orchestrator)
    fiscal_data:        Dict[str, Any]   # keyed by TaxType enum values


# ---------------------------------------------------------------------------
# Form review / signing request
# ---------------------------------------------------------------------------

class FormReviewRequest(BaseModel):
    form_id:       str
    cpa_license:   str
    review_notes:  Optional[str] = None
    lines_amended: List[Dict[str, Any]] = Field(default_factory=list)


class FormSignRequest(BaseModel):
    form_id:       str
    cpa_license:   str
    cpa_name:      str
    declaration_accepted: bool = Field(description="CPA must explicitly accept")

    @model_validator(mode="after")
    def _check_declaration(self) -> "FormSignRequest":
        if not self.declaration_accepted:
            raise ValueError("CPA must accept the declaration to sign a tax form")
        return self
