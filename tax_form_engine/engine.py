# =============================================================================
# tax_form_engine/engine.py
# TaxFormEngine — generates Puerto Rico tax forms from FiscalResultV2 outputs.
#
# DESIGN:
#   - Pure internal module: no user-facing interface here.
#   - Input:  FiscalResultV2 aggregated by the ORQUESTADOR.
#   - Output: List[TaxForm] persisted to DB, status=PENDING_CPA_REVIEW.
#   - CPA reviews and signs via CAPA 2 (api/routes/tax_forms.py).
#
# FORM SELECTION:
#   The engine selects which forms to generate based on the fiscal data
#   present in the request. The orchestrator passes pre-aggregated data;
#   the engine does NOT recompute — it structures into official form layout.
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .models import (
    CPASignature,
    FormGenerationRequest,
    FormReviewRequest,
    FormSignRequest,
    FormStatus,
    FormType,
    TaxForm,
    TaxPeriod,
)
from .forms import (
    generate_sc2915,
    generate_480_20,
    generate_941_pr,
    generate_w2_pr,
    generate_as2879,
    generate_480_6a,
    generate_480_6b,
)

logger = logging.getLogger(__name__)

# Map FormType → generator function
_GENERATORS = {
    FormType.SC2915:  generate_sc2915,
    FormType.F480_20: generate_480_20,
    FormType.F941_PR: generate_941_pr,
    FormType.W2_PR:   generate_w2_pr,
    FormType.AS2879:  generate_as2879,
    FormType.F480_6A: generate_480_6a,
    FormType.F480_6B: generate_480_6b,
}

# Forms that require annual tax period
_ANNUAL_ONLY = {FormType.F480_20, FormType.W2_PR, FormType.F480_6A, FormType.F480_6B}
# Forms that require quarterly tax period
_QUARTERLY_ONLY = {FormType.F941_PR, FormType.AS2879}


class TaxFormEngine:
    """
    Generates, reviews, and signs Puerto Rico tax forms.

    Usage (orchestrator → engine → CAPA 2):

        engine = TaxFormEngine(db=session)
        forms  = await engine.generate(request)   # status: PENDING_CPA_REVIEW
        # CPA reviews via CAPA 2 API, then:
        signed = await engine.sign(sign_request)  # status: SIGNED
    """

    def __init__(self, db=None) -> None:
        self._db = db

    # -----------------------------------------------------------------------
    # Generation
    # -----------------------------------------------------------------------

    async def generate(self, request: FormGenerationRequest) -> List[TaxForm]:
        """
        Generate all requested tax forms from aggregated fiscal data.

        Returns forms with status=PENDING_CPA_REVIEW.
        Each form is persisted to DB if db session is available.
        """
        forms: List[TaxForm] = []

        for form_type in request.form_types:
            gen_fn = _GENERATORS.get(form_type)
            if gen_fn is None:
                logger.warning("TAX_FORM_ENGINE: No generator for %s — skipped", form_type)
                continue

            # Period validation
            if form_type in _ANNUAL_ONLY and request.tax_period != TaxPeriod.ANNUAL:
                logger.warning(
                    "TAX_FORM_ENGINE: %s requires ANNUAL period — skipped (got %s)",
                    form_type, request.tax_period,
                )
                continue
            if form_type in _QUARTERLY_ONLY and request.tax_period != TaxPeriod.QUARTERLY:
                logger.warning(
                    "TAX_FORM_ENGINE: %s requires QUARTERLY period — skipped (got %s)",
                    form_type, request.tax_period,
                )
                continue

            try:
                form = gen_fn(request, request.fiscal_data)
                # Advance to PENDING_CPA_REVIEW
                form = _set_status(form, FormStatus.PENDING_CPA_REVIEW)
                forms.append(form)
                logger.info(
                    "TAX_FORM_ENGINE: Generated %s form_id=%s client=%s period=%s–%s",
                    form_type.value, form.form_id, request.client_id,
                    request.period_start, request.period_end,
                )
            except Exception as exc:
                logger.error(
                    "TAX_FORM_ENGINE: Error generating %s for client=%s: %s",
                    form_type, request.client_id, type(exc).__name__,
                )
                raise

        if self._db is not None:
            for form in forms:
                await self._persist_form(form)

        return forms

    # -----------------------------------------------------------------------
    # CPA Review
    # -----------------------------------------------------------------------

    async def submit_review(self, review: FormReviewRequest) -> TaxForm:
        """
        Record CPA review notes and any line amendments.
        Advances form to PENDING_CPA_SIGNATURE.

        Raises:
            ValueError: If form is not in PENDING_CPA_REVIEW status.
        """
        form = await self._load_form(review.form_id)
        if form.form_status != FormStatus.PENDING_CPA_REVIEW:
            raise ValueError(
                f"Form {review.form_id} is in status {form.form_status.value}, "
                "expected PENDING_CPA_REVIEW"
            )

        # Apply any CPA line amendments
        updated_lines = list(form.lines)
        for amendment in review.lines_amended:
            line_number = amendment.get("line_number")
            for i, line in enumerate(updated_lines):
                if line.line_number == line_number:
                    updated_lines[i] = line.model_copy(update={
                        "amount":    Decimal(str(amendment["amount"])) if "amount" in amendment else line.amount,
                        "note":      amendment.get("note", line.note),
                        "is_computed": False,   # CPA manually overrode
                    })
                    break

        reviewed_form = form.model_copy(update={
            "form_status":         FormStatus.PENDING_CPA_SIGNATURE,
            "assigned_cpa_license": review.cpa_license,
            "reviewed_at":         datetime.now(timezone.utc),
            "review_notes":        review.review_notes,
            "lines":               updated_lines,
        })

        if self._db is not None:
            await self._persist_form(reviewed_form)

        logger.info(
            "TAX_FORM_ENGINE: Form %s reviewed by CPA %s — %d lines amended",
            review.form_id, review.cpa_license, len(review.lines_amended),
        )
        return reviewed_form

    # -----------------------------------------------------------------------
    # CPA Digital Signature
    # -----------------------------------------------------------------------

    async def sign(self, sign_req: FormSignRequest, ip_address: Optional[str] = None) -> TaxForm:
        """
        Apply CPA digital signature to a reviewed form.
        Advances form to SIGNED (immutable after this point).

        Raises:
            ValueError: If form is not in PENDING_CPA_SIGNATURE status.
            ValueError: If signing CPA does not match reviewing CPA.
        """
        form = await self._load_form(sign_req.form_id)
        if form.form_status != FormStatus.PENDING_CPA_SIGNATURE:
            raise ValueError(
                f"Form {sign_req.form_id} is in status {form.form_status.value}, "
                "expected PENDING_CPA_SIGNATURE"
            )

        if form.assigned_cpa_license and form.assigned_cpa_license != sign_req.cpa_license:
            raise ValueError(
                f"Form was assigned to CPA {form.assigned_cpa_license}, "
                f"cannot be signed by {sign_req.cpa_license}"
            )

        signed_at = datetime.now(timezone.utc)
        sig_hash  = _signature_hash(sign_req.form_id, sign_req.cpa_license, signed_at)

        signature = CPASignature(
            cpa_license=sign_req.cpa_license,
            cpa_name=sign_req.cpa_name,
            signed_at=signed_at,
            signature_hash=sig_hash,
            ip_address=ip_address,
        )

        signed_form = form.model_copy(update={
            "form_status": FormStatus.SIGNED,
            "signature":   signature,
        })

        if self._db is not None:
            await self._persist_form(signed_form)

        logger.info(
            "TAX_FORM_ENGINE: Form %s SIGNED by CPA %s at %s hash=%s",
            sign_req.form_id, sign_req.cpa_license,
            signed_at.isoformat(), sig_hash[:16],
        )
        return signed_form

    # -----------------------------------------------------------------------
    # Query helpers
    # -----------------------------------------------------------------------

    async def get_forms_for_client(
        self,
        client_id: str,
        tax_year: Optional[int] = None,
        status: Optional[FormStatus] = None,
    ) -> List[Dict[str, Any]]:
        """Return form summaries for a client (CAPA 2 use)."""
        if self._db is None:
            return []
        from sqlalchemy import select
        from api.db.models import AppTaxForm
        q = select(AppTaxForm).where(AppTaxForm.client_id == client_id)
        if tax_year:
            q = q.where(AppTaxForm.tax_year == tax_year)
        if status:
            q = q.where(AppTaxForm.form_status == status.value)
        result = await self._db.execute(q.order_by(AppTaxForm.generated_at.desc()))
        rows = result.scalars().all()
        return [_row_to_summary(r) for r in rows]

    async def get_pending_signatures(self, cpa_license: str) -> List[Dict[str, Any]]:
        """
        Return forms awaiting this CPA's signature (CAPA 2 queue).
        Respects CPA-to-client assignment — a CPA only sees their own clients.
        """
        if self._db is None:
            return []
        from sqlalchemy import select
        from api.db.models import AppTaxForm
        result = await self._db.execute(
            select(AppTaxForm).where(
                AppTaxForm.assigned_cpa_license == cpa_license,
                AppTaxForm.form_status == FormStatus.PENDING_CPA_SIGNATURE.value,
            ).order_by(AppTaxForm.generated_at)
        )
        rows = result.scalars().all()
        return [_row_to_summary(r) for r in rows]

    # -----------------------------------------------------------------------
    # DB persistence
    # -----------------------------------------------------------------------

    async def _persist_form(self, form: TaxForm) -> None:
        try:
            from sqlalchemy import select
            from api.db.models import AppTaxForm
            existing = await self._db.execute(
                select(AppTaxForm).where(AppTaxForm.form_id == form.form_id)
            )
            row = existing.scalar_one_or_none()
            if row is None:
                row = AppTaxForm(form_id=form.form_id)
                self._db.add(row)

            row.form_type             = form.form_type.value
            row.form_status           = form.form_status.value
            row.tax_period            = form.tax_period.value
            row.client_id             = form.client_id
            row.business_name         = form.business_name
            row.ein_pr                = form.ein_pr
            row.tax_year              = form.tax_year
            row.period_start          = form.period_start.isoformat()
            row.period_end            = form.period_end.isoformat()
            row.fiscal_result_ids     = form.fiscal_result_ids
            row.generated_at          = form.generated_at
            row.lines_json            = [ln.model_dump() for ln in form.lines]
            row.summary_json          = form.summary
            row.assigned_cpa_license  = form.assigned_cpa_license
            row.reviewed_at           = form.reviewed_at
            row.review_notes          = form.review_notes
            row.signature_json        = form.signature.model_dump() if form.signature else None
            row.submitted_at          = form.submitted_at
            row.submission_ref        = form.submission_ref
            await self._db.commit()
        except Exception as exc:
            await self._db.rollback()
            logger.error("TAX_FORM_ENGINE: DB persist failed for form %s: %s",
                         form.form_id, type(exc).__name__)
            raise

    async def _load_form(self, form_id: str) -> TaxForm:
        if self._db is None:
            raise ValueError(f"No DB session — cannot load form {form_id}")
        from sqlalchemy import select
        from api.db.models import AppTaxForm
        result = await self._db.execute(
            select(AppTaxForm).where(AppTaxForm.form_id == form_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Form {form_id} not found")
        return _row_to_tax_form(row)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _set_status(form: TaxForm, status: FormStatus) -> TaxForm:
    return form.model_copy(update={"form_status": status})


def _signature_hash(form_id: str, cpa_license: str, signed_at: datetime) -> str:
    payload = f"{form_id}:{cpa_license}:{signed_at.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()


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
        "reviewed_at":          row.reviewed_at.isoformat() if row.reviewed_at else None,
        "summary":              row.summary_json or {},
    }


def _row_to_tax_form(row) -> TaxForm:
    from .models import FormLine
    lines = [FormLine(**ln) for ln in (row.lines_json or [])]
    sig = None
    if row.signature_json:
        sig = CPASignature(**row.signature_json)
    from datetime import date
    return TaxForm(
        form_id=row.form_id,
        form_type=FormType(row.form_type),
        form_status=FormStatus(row.form_status),
        tax_period=TaxPeriod(row.tax_period),
        client_id=row.client_id,
        business_name=row.business_name,
        ein_pr=row.ein_pr,
        tax_year=row.tax_year,
        period_start=date.fromisoformat(row.period_start),
        period_end=date.fromisoformat(row.period_end),
        fiscal_result_ids=row.fiscal_result_ids or [],
        generated_at=row.generated_at,
        lines=lines,
        summary=row.summary_json or {},
        assigned_cpa_license=row.assigned_cpa_license,
        reviewed_at=row.reviewed_at,
        review_notes=row.review_notes,
        signature=sig,
        submitted_at=row.submitted_at,
        submission_ref=row.submission_ref,
    )
