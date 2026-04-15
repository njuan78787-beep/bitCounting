# =============================================================================
# tax_form_engine/tests_tax_form_engine.py
#
# RUN:  pytest tax_form_engine/tests_tax_form_engine.py -v --tb=short
# =============================================================================

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tax_form_engine.models import (
    FormGenerationRequest,
    FormLine,
    FormReviewRequest,
    FormSignRequest,
    FormStatus,
    FormType,
    TaxForm,
    TaxPeriod,
)
from tax_form_engine.validator import FormValidationError, validate_for_signature


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CLIENT_ID    = "client-test-001"
_CPA_LICENSE  = "CPA-PR-12345"
_EIN_PR       = "660123456"
_PERIOD_START = date(2024, 1, 1)
_PERIOD_END   = date(2024, 12, 31)

_FISCAL_IVU = {
    "total_sales":           "50000.00",
    "taxable_sales":         "45000.00",
    "exempt_sales":          "5000.00",
    "ivu_estatal_collected": "4725.00",     # 45000 * 10.5%
    "ivu_municipal_collected": "450.00",    # 45000 * 1.0%
    "ivu_estatal_credits":   "1050.00",
    "ivu_municipal_credits": "100.00",
}

_FISCAL_CORP = {
    "gross_income":         "200000.00",
    "cost_of_goods_sold":   "80000.00",
    "gross_profit":         "120000.00",
    "operating_expenses":   "30000.00",
    "net_operating_income": "90000.00",
    "other_income":         "5000.00",
    "total_deductions":     "10000.00",
    "net_taxable_income":   "85000.00",
    "prepaid_taxes":        "5000.00",
    "act_60_decree":        False,
}

_FISCAL_941 = {
    "total_wages_tips":         "120000.00",
    "wages_over_ss_cap":        "0.00",
    "wages_over_add_med":       "0.00",
    "fed_income_tax_withheld":  "12000.00",
    "employee_count":           4,
    "month1_tax": "5500.00",
    "month2_tax": "5500.00",
    "month3_tax": "5900.00",
    "deposits_made":            "16800.00",
}

_FISCAL_W2 = {
    "employee_id":          "emp-001",
    "ssn_last4":            "6789",
    "wages_federal":        "55000.00",
    "fed_income_withheld":  "6600.00",
    "ss_wages":             "55000.00",
    "ss_tax_withheld":      "3410.00",
    "medicare_wages":       "55000.00",
    "medicare_tax_withheld": "797.50",
    "pr_wages":             "55000.00",
    "pr_income_tax":        "5500.00",
    "pr_disability_tax":    "0.00",
    "pr_charity":           "0.00",
}

_FISCAL_SUTA = {
    "total_gross_wages":  "120000.00",
    "taxable_wages_suta": "28000.00",    # 4 employees × $7,000
    "suta_rate":          "0.027",
    "employee_count":     4,
    "deposits_made":      "700.00",
}

_FISCAL_480_6A = {
    "dividends_paid":  "10000.00",
    "interest_paid":   "2000.00",
    "rents_paid":      "0.00",
    "royalties_paid":  "0.00",
    "annuities_paid":  "0.00",
    "withholding_rate": "0.10",
    "payee_count":     3,
    "recipients": [
        {"name": "Inversionista A", "ein_or_ssn_last4": "1234", "amount": "6000.00", "type": "dividendo"},
        {"name": "Inversionista B", "ein_or_ssn_last4": "5678", "amount": "6000.00", "type": "dividendo"},
    ],
}


def _make_request(form_types, fiscal_data, tax_period=TaxPeriod.ANNUAL):
    return FormGenerationRequest(
        client_id=_CLIENT_ID,
        business_name="Empresa de Prueba LLC",
        ein_pr=_EIN_PR,
        tax_year=2024,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        tax_period=tax_period,
        form_types=form_types,
        fiscal_result_ids=["fr-001", "fr-002"],
        fiscal_data=fiscal_data,
    )


def _make_pending_sig_form(form_type=FormType.SC2915) -> TaxForm:
    """Build a TaxForm in PENDING_CPA_SIGNATURE status for signing tests."""
    return TaxForm(
        form_id=str(uuid.uuid4()),
        form_type=form_type,
        form_status=FormStatus.PENDING_CPA_SIGNATURE,
        tax_period=TaxPeriod.MONTHLY,
        client_id=_CLIENT_ID,
        business_name="Test Corp",
        ein_pr=_EIN_PR,
        tax_year=2024,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        assigned_cpa_license=_CPA_LICENSE,
        lines=[
            FormLine(line_number="10", description="Total IVU", amount=Decimal("3025.00")),
        ],
        summary={
            "total_due": "3025.00",
            "total_sales": "50000.00",
            "taxable_sales": "45000.00",
            "exempt_sales": "5000.00",
        },
    )


# ===========================================================================
# 1. Form generators — SC2915
# ===========================================================================

class TestSC2915:
    def test_generates_correct_lines(self):
        from tax_form_engine.forms.sc2915 import generate_sc2915
        req = _make_request([FormType.SC2915], _FISCAL_IVU, TaxPeriod.MONTHLY)
        form = generate_sc2915(req, _FISCAL_IVU)
        assert form.form_type == FormType.SC2915
        line_nums = [l.line_number for l in form.lines]
        assert "10" in line_nums

    def test_net_ivu_correct(self):
        from tax_form_engine.forms.sc2915 import generate_sc2915
        req = _make_request([FormType.SC2915], _FISCAL_IVU, TaxPeriod.MONTHLY)
        form = generate_sc2915(req, _FISCAL_IVU)
        total_line = next(l for l in form.lines if l.line_number == "10")
        # net = (4725-1050) + (450-100) = 3675 + 350 = 4025
        assert total_line.amount == Decimal("4025.00")

    def test_summary_populated(self):
        from tax_form_engine.forms.sc2915 import generate_sc2915
        req = _make_request([FormType.SC2915], _FISCAL_IVU, TaxPeriod.MONTHLY)
        form = generate_sc2915(req, _FISCAL_IVU)
        assert "total_due" in form.summary
        assert "taxable_sales" in form.summary


# ===========================================================================
# 2. Form generators — 480.20 (Corporate)
# ===========================================================================

class TestF480_20:
    def test_generates_correct_lines(self):
        from tax_form_engine.forms.f480_20 import generate_480_20
        req = _make_request([FormType.F480_20], _FISCAL_CORP)
        form = generate_480_20(req, _FISCAL_CORP)
        assert form.form_type == FormType.F480_20
        assert len(form.lines) >= 10

    def test_graduated_tax_bracket(self):
        from tax_form_engine.forms.f480_20 import _graduated_tax
        # First $75k at 18.5% = $13,875; next $10k at 21% = $2,100 → total $15,975
        tax = _graduated_tax(Decimal("85000"))
        assert tax == Decimal("15975.00")

    def test_act_60_rate_applies(self):
        from tax_form_engine.forms.f480_20 import generate_480_20
        fiscal = {**_FISCAL_CORP, "act_60_decree": True, "act_60_rate": "0.04"}
        req = _make_request([FormType.F480_20], fiscal)
        form = generate_480_20(req, fiscal)
        tax_line = next(l for l in form.lines if l.line_number == "9")
        # 85000 * 4% = 3400
        assert tax_line.amount == Decimal("3400.00")

    def test_balance_due_computed(self):
        from tax_form_engine.forms.f480_20 import generate_480_20
        req = _make_request([FormType.F480_20], _FISCAL_CORP)
        form = generate_480_20(req, _FISCAL_CORP)
        balance_line = next(l for l in form.lines if l.line_number == "11")
        assert balance_line.amount is not None


# ===========================================================================
# 3. 941-PR quarterly payroll
# ===========================================================================

class TestF941_PR:
    def test_ss_tax_computed(self):
        from tax_form_engine.forms.f941_pr import generate_941_pr
        req = _make_request([FormType.F941_PR], _FISCAL_941, TaxPeriod.QUARTERLY)
        form = generate_941_pr(req, _FISCAL_941)
        ss_line = next(l for l in form.lines if l.line_number == "5a(ii)")
        # 120000 * 12.4% = 14880
        assert ss_line.amount == Decimal("14880.00")

    def test_medicare_computed(self):
        from tax_form_engine.forms.f941_pr import generate_941_pr
        req = _make_request([FormType.F941_PR], _FISCAL_941, TaxPeriod.QUARTERLY)
        form = generate_941_pr(req, _FISCAL_941)
        med_line = next(l for l in form.lines if l.line_number == "5c(ii)")
        # 120000 * 2.9% = 3480
        assert med_line.amount == Decimal("3480.00")

    def test_balance_due_reflects_deposits(self):
        from tax_form_engine.forms.f941_pr import generate_941_pr
        req = _make_request([FormType.F941_PR], _FISCAL_941, TaxPeriod.QUARTERLY)
        form = generate_941_pr(req, _FISCAL_941)
        balance = next(l for l in form.lines if l.line_number == "14")
        assert balance.amount is not None


# ===========================================================================
# 4. W-2PR — SSN safety
# ===========================================================================

class TestW2PR:
    def test_ssn_last4_only(self):
        from tax_form_engine.forms.w2_pr import generate_w2_pr
        req = _make_request([FormType.W2_PR], _FISCAL_W2)
        form = generate_w2_pr(req, _FISCAL_W2)
        ssn_line = next(l for l in form.lines if l.line_number == "SSN4")
        assert ssn_line.text_value == "6789"
        # Full SSN must not appear anywhere in the form
        form_str = str(form.model_dump())
        assert "123456789" not in form_str   # no full SSN

    def test_summary_contains_employee_id(self):
        from tax_form_engine.forms.w2_pr import generate_w2_pr
        req = _make_request([FormType.W2_PR], _FISCAL_W2)
        form = generate_w2_pr(req, _FISCAL_W2)
        assert form.summary["employee_id"] == "emp-001"


# ===========================================================================
# 5. AS2879 SUTA
# ===========================================================================

class TestAS2879:
    def test_suta_computed(self):
        from tax_form_engine.forms.as2879 import generate_as2879
        req = _make_request([FormType.AS2879], _FISCAL_SUTA, TaxPeriod.QUARTERLY)
        form = generate_as2879(req, _FISCAL_SUTA)
        suta_line = next(l for l in form.lines if l.line_number == "5")
        # 28000 * 2.7% = 756
        assert suta_line.amount == Decimal("756.00")


# ===========================================================================
# 6. 480.6A informative
# ===========================================================================

class TestF480_6A:
    def test_total_withheld(self):
        from tax_form_engine.forms.f480_6ab import generate_480_6a
        req = _make_request([FormType.F480_6A], _FISCAL_480_6A)
        form = generate_480_6a(req, _FISCAL_480_6A)
        wh_line = next(l for l in form.lines if l.line_number == "8")
        # (10000 + 2000) * 10% = 1200
        assert wh_line.amount == Decimal("1200.00")

    def test_recipient_lines_added(self):
        from tax_form_engine.forms.f480_6ab import generate_480_6a
        req = _make_request([FormType.F480_6A], _FISCAL_480_6A)
        form = generate_480_6a(req, _FISCAL_480_6A)
        recipient_lines = [l for l in form.lines if l.line_number.startswith("R")]
        assert len(recipient_lines) == 2

    def test_no_full_ssn_in_recipients(self):
        from tax_form_engine.forms.f480_6ab import generate_480_6a
        req = _make_request([FormType.F480_6A], _FISCAL_480_6A)
        form = generate_480_6a(req, _FISCAL_480_6A)
        for line in form.lines:
            if line.text_value:
                assert len(line.text_value.replace("***", "").strip()) <= 4 or "ID:" not in line.text_value


# ===========================================================================
# 7. TaxFormEngine — generate()
# ===========================================================================

class TestTaxFormEngineGenerate:
    @pytest.mark.asyncio
    async def test_generate_sc2915_no_db(self):
        from tax_form_engine.engine import TaxFormEngine
        engine = TaxFormEngine(db=None)
        req = _make_request([FormType.SC2915], _FISCAL_IVU, TaxPeriod.MONTHLY)
        forms = await engine.generate(req)
        assert len(forms) == 1
        assert forms[0].form_type == FormType.SC2915
        assert forms[0].form_status == FormStatus.PENDING_CPA_REVIEW

    @pytest.mark.asyncio
    async def test_generate_multiple_forms(self):
        from tax_form_engine.engine import TaxFormEngine
        engine = TaxFormEngine(db=None)
        req = FormGenerationRequest(
            client_id=_CLIENT_ID,
            business_name="Test Corp",
            ein_pr=_EIN_PR,
            tax_year=2024,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            tax_period=TaxPeriod.ANNUAL,
            form_types=[FormType.F480_20, FormType.F480_6A],
            fiscal_result_ids=["fr-001"],
            fiscal_data={**_FISCAL_CORP, **_FISCAL_480_6A},
        )
        forms = await engine.generate(req)
        assert len(forms) == 2
        types = {f.form_type for f in forms}
        assert FormType.F480_20 in types
        assert FormType.F480_6A in types

    @pytest.mark.asyncio
    async def test_quarterly_form_skipped_for_annual_period(self):
        """941-PR requires QUARTERLY period — skipped when request is ANNUAL."""
        from tax_form_engine.engine import TaxFormEngine
        engine = TaxFormEngine(db=None)
        req = _make_request([FormType.F941_PR], _FISCAL_941, TaxPeriod.ANNUAL)
        forms = await engine.generate(req)
        assert len(forms) == 0   # skipped


# ===========================================================================
# 8. TaxFormEngine — review()
# ===========================================================================

class TestTaxFormEngineReview:
    @pytest.mark.asyncio
    async def test_review_advances_status(self):
        from tax_form_engine.engine import TaxFormEngine, _row_to_tax_form
        engine = TaxFormEngine(db=None)

        # Generate first
        req = _make_request([FormType.SC2915], _FISCAL_IVU, TaxPeriod.MONTHLY)
        forms = await engine.generate(req)
        form = forms[0]

        # Simulate DB: monkey-patch _load_form
        async def mock_load(fid):
            return form.model_copy(update={"form_status": FormStatus.PENDING_CPA_REVIEW})

        engine._load_form = mock_load

        async def mock_persist(f):
            pass

        engine._persist_form = mock_persist

        review = FormReviewRequest(
            form_id=form.form_id,
            cpa_license=_CPA_LICENSE,
            review_notes="Looks good — minor rounding adjustment",
            lines_amended=[],
        )
        reviewed = await engine.submit_review(review)
        assert reviewed.form_status == FormStatus.PENDING_CPA_SIGNATURE
        assert reviewed.assigned_cpa_license == _CPA_LICENSE

    @pytest.mark.asyncio
    async def test_review_wrong_status_raises(self):
        from tax_form_engine.engine import TaxFormEngine

        engine = TaxFormEngine(db=None)

        async def mock_load(fid):
            return _make_pending_sig_form()   # already PENDING_CPA_SIGNATURE

        engine._load_form = mock_load

        with pytest.raises(ValueError, match="PENDING_CPA_REVIEW"):
            await engine.submit_review(FormReviewRequest(
                form_id="any",
                cpa_license=_CPA_LICENSE,
            ))


# ===========================================================================
# 9. TaxFormEngine — sign()
# ===========================================================================

class TestTaxFormEngineSign:
    @pytest.mark.asyncio
    async def test_sign_produces_signature_hash(self):
        from tax_form_engine.engine import TaxFormEngine

        form = _make_pending_sig_form()
        engine = TaxFormEngine(db=None)

        async def mock_load(fid):
            return form

        async def mock_persist(f):
            pass

        engine._load_form  = mock_load
        engine._persist_form = mock_persist

        req = FormSignRequest(
            form_id=form.form_id,
            cpa_license=_CPA_LICENSE,
            cpa_name="Ana García, CPA",
            declaration_accepted=True,
        )
        signed = await engine.sign(req)
        assert signed.form_status == FormStatus.SIGNED
        assert signed.signature is not None
        assert len(signed.signature.signature_hash) == 64   # SHA-256 hex

    @pytest.mark.asyncio
    async def test_sign_wrong_status_raises(self):
        from tax_form_engine.engine import TaxFormEngine

        engine = TaxFormEngine(db=None)

        async def mock_load(fid):
            return _make_pending_sig_form().model_copy(update={"form_status": FormStatus.PENDING_CPA_REVIEW})

        engine._load_form = mock_load

        with pytest.raises(ValueError, match="PENDING_CPA_SIGNATURE"):
            await engine.sign(FormSignRequest(
                form_id="any",
                cpa_license=_CPA_LICENSE,
                cpa_name="Ana García, CPA",
                declaration_accepted=True,
            ))

    @pytest.mark.asyncio
    async def test_sign_wrong_cpa_raises(self):
        from tax_form_engine.engine import TaxFormEngine

        form = _make_pending_sig_form()
        engine = TaxFormEngine(db=None)

        async def mock_load(fid):
            return form

        engine._load_form = mock_load

        with pytest.raises(ValueError, match="assigned to CPA"):
            await engine.sign(FormSignRequest(
                form_id=form.form_id,
                cpa_license="CPA-WRONG-99999",
                cpa_name="Impostor",
                declaration_accepted=True,
            ))

    def test_sign_requires_declaration_accepted(self):
        with pytest.raises(Exception):
            FormSignRequest(
                form_id="f1",
                cpa_license=_CPA_LICENSE,
                cpa_name="Ana García, CPA",
                declaration_accepted=False,
            )


# ===========================================================================
# 10. Validator
# ===========================================================================

class TestValidator:
    def test_valid_form_passes(self):
        form = _make_pending_sig_form()
        validate_for_signature(form)   # must not raise

    def test_wrong_status_fails(self):
        form = _make_pending_sig_form().model_copy(
            update={"form_status": FormStatus.DRAFT}
        )
        with pytest.raises(FormValidationError):
            validate_for_signature(form)

    def test_no_cpa_assigned_fails(self):
        form = _make_pending_sig_form().model_copy(
            update={"assigned_cpa_license": None}
        )
        with pytest.raises(FormValidationError) as exc_info:
            validate_for_signature(form)
        assert any("CPA" in i for i in exc_info.value.issues)

    def test_empty_lines_fails(self):
        form = _make_pending_sig_form().model_copy(update={"lines": []})
        with pytest.raises(FormValidationError) as exc_info:
            validate_for_signature(form)
        assert any("line" in i.lower() for i in exc_info.value.issues)

    def test_w2pr_requires_employee_id(self):
        form = TaxForm(
            form_id=str(uuid.uuid4()),
            form_type=FormType.W2_PR,
            form_status=FormStatus.PENDING_CPA_SIGNATURE,
            tax_period=TaxPeriod.ANNUAL,
            client_id=_CLIENT_ID,
            business_name="Test Corp",
            ein_pr=_EIN_PR,
            tax_year=2024,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
            assigned_cpa_license=_CPA_LICENSE,
            lines=[FormLine(line_number="1", description="Wages", amount=Decimal("50000"))],
            summary={"ssn_last4": "6789"},   # missing employee_id
        )
        with pytest.raises(FormValidationError) as exc_info:
            validate_for_signature(form)
        assert any("employee_id" in i for i in exc_info.value.issues)
