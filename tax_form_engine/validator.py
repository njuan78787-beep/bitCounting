# =============================================================================
# tax_form_engine/validator.py
# Validates tax form completeness before CPA signature is allowed.
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple

from .models import FormStatus, FormType, TaxForm


class FormValidationError(Exception):
    def __init__(self, issues: List[str]) -> None:
        self.issues = issues
        super().__init__(f"{len(issues)} validation issue(s): {'; '.join(issues)}")


def validate_for_signature(form: TaxForm) -> None:
    """
    Verify a form is complete and ready for CPA digital signature.

    Raises FormValidationError if any required fields are missing or
    if values fail basic sanity checks.
    """
    issues: List[str] = []

    # Must be in PENDING_CPA_SIGNATURE
    if form.form_status != FormStatus.PENDING_CPA_SIGNATURE:
        issues.append(
            f"Form status is {form.form_status.value}; expected PENDING_CPA_SIGNATURE"
        )

    # Must have an assigned CPA
    if not form.assigned_cpa_license:
        issues.append("No CPA assigned to this form")

    # Must have at least one line
    if not form.lines:
        issues.append("Form has no line items")

    # All computed lines must have a value
    for line in form.lines:
        if line.is_computed and line.amount is None and line.text_value is None:
            issues.append(f"Line {line.line_number} ({line.description}) has no value")

    # Form-specific validations
    _validate_form_specific(form, issues)

    if issues:
        raise FormValidationError(issues)


def _validate_form_specific(form: TaxForm, issues: List[str]) -> None:
    """Form-type specific validation rules."""
    summary = form.summary

    if form.form_type == FormType.SC2915:
        _require_non_negative(summary, "total_due", "SC2915 total IVU due", issues)

    elif form.form_type == FormType.F480_20:
        _require_key(summary, "net_taxable", "480.20 net taxable income", issues)
        _require_key(summary, "tax_liability", "480.20 tax liability", issues)

    elif form.form_type == FormType.F941_PR:
        _require_key(summary, "total_taxes", "941-PR total taxes", issues)
        count = summary.get("employee_count", 0)
        if not isinstance(count, int) or count < 0:
            issues.append("941-PR employee_count must be a non-negative integer")

    elif form.form_type == FormType.W2_PR:
        if not summary.get("employee_id"):
            issues.append("W-2PR requires employee_id")
        ssn4 = summary.get("ssn_last4", "")
        if ssn4 and ssn4 != "****" and len(str(ssn4)) != 4:
            issues.append("W-2PR ssn_last4 must be exactly 4 digits or '****'")

    elif form.form_type == FormType.AS2879:
        _require_key(summary, "suta_tax_due", "AS2879 SUTA tax due", issues)

    elif form.form_type in (FormType.F480_6A, FormType.F480_6B):
        _require_key(summary, "total_paid", "480.6 total paid", issues)


def _require_key(summary: dict, key: str, label: str, issues: List[str]) -> None:
    if key not in summary:
        issues.append(f"{label} is missing from form summary")


def _require_non_negative(summary: dict, key: str, label: str, issues: List[str]) -> None:
    val = summary.get(key)
    if val is None:
        issues.append(f"{label} is missing from form summary")
        return
    try:
        if Decimal(str(val)) < 0:
            issues.append(f"{label} cannot be negative ({val})")
    except Exception:
        issues.append(f"{label} has invalid value: {val!r}")
