# =============================================================================
# api/routes/tax_forms.py
# TaxFormEngine REST endpoints — CAPA 2.
#
# ACCESS CONTROL:
#   CAPA 2 (CPA_SENIOR, CPA_PARTNER, EXIMIA_ADMIN):
#     GET  /api/v1/tax-forms/pending         — forms awaiting CPA signature
#     GET  /api/v1/tax-forms/{form_id}       — full form with all lines
#     POST /api/v1/tax-forms/{form_id}/review — submit review + amendments
#     POST /api/v1/tax-forms/{form_id}/sign   — digitally sign a form
#     GET  /api/v1/tax-forms/client/{client_id} — all forms for a client
#
#   CAPA 1 only (EXIMIA_ADMIN):
#     POST /api/v1/tax-forms/generate        — generate forms (normally called by orchestrator)
#
# CPAs CANNOT:
#   - See forms for clients not assigned to them
#   - Re-sign an already signed form
#   - Delete or supersede forms (admin-only)
# =============================================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import Role, TokenData, get_current_user
from ..database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tax-forms", tags=["tax-forms"])

# Roles allowed to read/review/sign tax forms (CAPA 2)
_CPA_ROLES   = {Role.CPA_SENIOR, Role.CPA_PARTNER, Role.EXIMIA_ADMIN}
# EXIMIA_ADMIN only for generation trigger
_ADMIN_ROLES = {Role.EXIMIA_ADMIN}


def _require_cpa(current_user: TokenData) -> TokenData:
    if current_user.role not in _CPA_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tax form access requires CPA_SENIOR, CPA_PARTNER, or EXIMIA_ADMIN role (CAPA 2).",
        )
    return current_user


def _require_admin(current_user: TokenData) -> TokenData:
    if current_user.role not in _ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tax form generation requires EXIMIA_ADMIN role.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class GenerateFormsRequest(BaseModel):
    client_id:         str
    business_name:     str
    ein_pr:            str
    tax_year:          int
    period_start:      str   # YYYY-MM-DD
    period_end:        str   # YYYY-MM-DD
    tax_period:        str   # MONTHLY | QUARTERLY | ANNUAL
    form_types:        List[str]
    fiscal_result_ids: List[str] = Field(default_factory=list)
    fiscal_data:       Dict[str, Any]


class ReviewRequest(BaseModel):
    review_notes:  Optional[str] = None
    lines_amended: List[Dict[str, Any]] = Field(default_factory=list)


class SignRequest(BaseModel):
    cpa_name:             str
    declaration_accepted: bool = Field(description="CPA must explicitly accept the declaration")


# ---------------------------------------------------------------------------
# CAPA 1 — EXIMIA_ADMIN (or orchestrator internal call)
# ---------------------------------------------------------------------------

@router.post(
    "/generate",
    status_code=status.HTTP_201_CREATED,
    summary="[ADMIN] Generate tax forms from fiscal data",
    description="EXIMIA_ADMIN only. Normally called by the ORQUESTADOR agent. "
                "Generates forms for CPA review.",
)
async def generate_forms(
    body: GenerateFormsRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_admin(current_user)

    from tax_form_engine.models import FormGenerationRequest, FormType, TaxPeriod
    from tax_form_engine.engine import TaxFormEngine
    from datetime import date

    try:
        form_types = [FormType(ft) for ft in body.form_types]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid form_type: {exc}")

    try:
        req = FormGenerationRequest(
            client_id=body.client_id,
            business_name=body.business_name,
            ein_pr=body.ein_pr,
            tax_year=body.tax_year,
            period_start=date.fromisoformat(body.period_start),
            period_end=date.fromisoformat(body.period_end),
            tax_period=TaxPeriod(body.tax_period),
            form_types=form_types,
            fiscal_result_ids=body.fiscal_result_ids,
            fiscal_data=body.fiscal_data,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    engine = TaxFormEngine(db=db)
    forms  = await engine.generate(req)
    return [{"form_id": f.form_id, "form_type": f.form_type.value, "form_status": f.form_status.value}
            for f in forms]


# ---------------------------------------------------------------------------
# CAPA 2 — CPA read, review, sign
# ---------------------------------------------------------------------------

@router.get(
    "/pending",
    summary="Forms awaiting this CPA's signature",
    description="Returns forms in PENDING_CPA_SIGNATURE assigned to the calling CPA.",
)
async def get_pending_forms(
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_cpa(current_user)

    from tax_form_engine.engine import TaxFormEngine

    # EXIMIA_ADMIN sees all pending; CPAs see only their own
    cpa_license = current_user.sub if current_user.role != Role.EXIMIA_ADMIN else None

    engine = TaxFormEngine(db=db)
    if cpa_license:
        return await engine.get_pending_signatures(cpa_license)
    # Admin: return all pending across all CPAs
    return await _get_all_pending(db)


@router.get(
    "/client/{client_id}",
    summary="All tax forms for a client",
    description="CPA sees forms for assigned clients only. EXIMIA_ADMIN sees all.",
)
async def get_client_forms(
    client_id: str,
    tax_year: Optional[int] = None,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_cpa(current_user)

    # CPA isolation: ensure CPA is assigned to this client
    if current_user.role != Role.EXIMIA_ADMIN:
        await _assert_cpa_owns_client(current_user.sub, client_id, db)

    from tax_form_engine.engine import TaxFormEngine
    engine = TaxFormEngine(db=db)
    return await engine.get_forms_for_client(client_id, tax_year=tax_year)


@router.get(
    "/{form_id}",
    summary="Full tax form with all lines",
    description="Returns complete form including computed lines and summary. "
                "CPA can only access forms for their assigned clients.",
)
async def get_form(
    form_id: str,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_cpa(current_user)

    row = await _load_form_row(form_id, db)

    # CPA isolation
    if current_user.role != Role.EXIMIA_ADMIN:
        await _assert_cpa_owns_client(current_user.sub, row.client_id, db)

    return _row_to_detail(row)


@router.post(
    "/{form_id}/review",
    summary="Submit CPA review and optional line amendments",
    description=(
        "CPA reviews the form, adds notes, and optionally amends computed lines. "
        "Advances status to PENDING_CPA_SIGNATURE. "
        "Every line amendment requires a justification note."
    ),
)
async def review_form(
    form_id: str,
    body: ReviewRequest,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_cpa(current_user)

    row = await _load_form_row(form_id, db)
    if current_user.role != Role.EXIMIA_ADMIN:
        await _assert_cpa_owns_client(current_user.sub, row.client_id, db)

    # Require justification for each amended line
    for amendment in body.lines_amended:
        if not amendment.get("note"):
            raise HTTPException(
                status_code=400,
                detail=f"Line amendment for line '{amendment.get('line_number')}' requires a 'note' justification.",
            )

    from tax_form_engine.engine import TaxFormEngine
    from tax_form_engine.models import FormReviewRequest

    review_req = FormReviewRequest(
        form_id=form_id,
        cpa_license=current_user.sub,
        review_notes=body.review_notes,
        lines_amended=body.lines_amended,
    )

    engine = TaxFormEngine(db=db)
    try:
        reviewed = await engine.submit_review(review_req)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    logger.info("TAX_FORMS: Form %s reviewed by CPA %s — %d amendments",
                form_id, current_user.sub, len(body.lines_amended))
    return {"form_id": reviewed.form_id, "form_status": reviewed.form_status.value}


@router.post(
    "/{form_id}/sign",
    summary="Digitally sign a tax form",
    description=(
        "CPA digitally signs the form after review. "
        "declaration_accepted must be true — this is the legal attestation. "
        "Signed forms are immutable."
    ),
)
async def sign_form(
    form_id: str,
    body: SignRequest,
    request: Request,
    current_user: TokenData = Depends(get_current_user),
    db=Depends(get_db),
):
    _require_cpa(current_user)

    if not body.declaration_accepted:
        raise HTTPException(
            status_code=400,
            detail="You must accept the declaration to sign a tax form.",
        )

    row = await _load_form_row(form_id, db)
    if current_user.role != Role.EXIMIA_ADMIN:
        await _assert_cpa_owns_client(current_user.sub, row.client_id, db)

    from tax_form_engine.engine import TaxFormEngine
    from tax_form_engine.models import FormSignRequest
    from tax_form_engine.validator import validate_for_signature, FormValidationError

    sign_req = FormSignRequest(
        form_id=form_id,
        cpa_license=current_user.sub,
        cpa_name=body.cpa_name,
        declaration_accepted=body.declaration_accepted,
    )

    engine = TaxFormEngine(db=db)

    # Load full form for validation
    form = await engine._load_form(form_id)
    try:
        validate_for_signature(form)
    except FormValidationError as exc:
        raise HTTPException(status_code=422, detail={"validation_issues": exc.issues})

    ip = request.client.host if request.client else None
    try:
        signed = await engine.sign(sign_req, ip_address=ip)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    logger.info("TAX_FORMS: Form %s SIGNED by CPA %s", form_id, current_user.sub)
    return {
        "form_id":        signed.form_id,
        "form_status":    signed.form_status.value,
        "signature_hash": signed.signature.signature_hash if signed.signature else None,
        "signed_at":      signed.signature.signed_at.isoformat() if signed.signature else None,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_form_row(form_id: str, db):
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available.")
    from sqlalchemy import select
    from api.db.models import AppTaxForm
    result = await db.execute(select(AppTaxForm).where(AppTaxForm.form_id == form_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Tax form '{form_id}' not found.")
    return row


async def _assert_cpa_owns_client(cpa_license: str, client_id: str, db) -> None:
    """Verify the CPA is assigned to this client — enforces data isolation."""
    if db is None:
        return   # no DB, skip in dev mode
    from sqlalchemy import text
    result = await db.execute(
        text("SELECT 1 FROM cpa_client_assignments WHERE cpa_license = :cpa AND client_id = :cid LIMIT 1"),
        {"cpa": cpa_license, "cid": client_id},
    )
    if result.fetchone() is None:
        raise HTTPException(
            status_code=403,
            detail="You are not assigned to this client.",
        )


async def _get_all_pending(db) -> List[Dict[str, Any]]:
    if db is None:
        return []
    from sqlalchemy import select
    from api.db.models import AppTaxForm
    result = await db.execute(
        select(AppTaxForm)
        .where(AppTaxForm.form_status == "PENDING_CPA_SIGNATURE")
        .order_by(AppTaxForm.generated_at)
    )
    return [_row_to_summary(r) for r in result.scalars().all()]


def _row_to_detail(row) -> Dict[str, Any]:
    return {
        "form_id":              row.form_id,
        "form_type":            row.form_type,
        "form_status":          row.form_status,
        "tax_period":           row.tax_period,
        "tax_year":             row.tax_year,
        "period_start":         row.period_start,
        "period_end":           row.period_end,
        "business_name":        row.business_name,
        "ein_pr":               row.ein_pr,
        "assigned_cpa_license": row.assigned_cpa_license,
        "generated_at":         row.generated_at.isoformat() if row.generated_at else None,
        "reviewed_at":          row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_notes":         row.review_notes,
        "lines":                row.lines_json or [],
        "summary":              row.summary_json or {},
        "signature":            row.signature_json,
        "submitted_at":         row.submitted_at.isoformat() if row.submitted_at else None,
        "submission_ref":       row.submission_ref,
    }


def _row_to_summary(row) -> Dict[str, Any]:
    return {
        "form_id":              row.form_id,
        "form_type":            row.form_type,
        "form_status":          row.form_status,
        "tax_period":           row.tax_period,
        "tax_year":             row.tax_year,
        "period_start":         row.period_start,
        "period_end":           row.period_end,
        "business_name":        row.business_name,
        "assigned_cpa_license": row.assigned_cpa_license,
        "generated_at":         row.generated_at.isoformat() if row.generated_at else None,
        "summary":              row.summary_json or {},
    }
