# =============================================================================
# W2_PR — Formulario 499R-2 / W-2PR
# Departamento de Hacienda de Puerto Rico + IRS
#
# One form per employee per tax year.
# SSN is stored ENCRYPTED — only ssn_last4 used for display/printing.
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

from ..models import FormLine, FormType, TaxForm, TaxPeriod


def generate_w2_pr(req, fiscal_data: Dict[str, Any]) -> TaxForm:
    """
    Generate a W-2PR / 499R-2 for a single employee.

    fiscal_data keys:
      employee_id          — Bit-Counting employee identifier (not SSN)
      ssn_last4            — last 4 digits of SSN only (display use)
      employee_name        — encrypted name resolved at render time
      wages_federal        — Box 1: Federal taxable wages
      fed_income_withheld  — Box 2: Federal income tax withheld
      ss_wages             — Box 3: Social Security wages
      ss_tax_withheld      — Box 4: SS employee portion
      medicare_wages       — Box 5: Medicare wages
      medicare_tax_withheld— Box 6: Medicare employee portion
      pr_wages             — Box 16/Caja A: PR taxable wages
      pr_income_tax        — Box 17/Caja B: PR income tax withheld
      pr_disability_tax    — Caja C: PR disability insurance withheld
      pr_charity           — Caja D: Charitable contributions withheld
    """
    D = lambda k: _d(fiscal_data, k)

    lines = [
        FormLine(line_number="Box1",  description="Salarios federales sujetos a retención",        amount=D("wages_federal")),
        FormLine(line_number="Box2",  description="Contribución federal sobre ingresos retenida",  amount=D("fed_income_withheld")),
        FormLine(line_number="Box3",  description="Salarios de Seguro Social",                     amount=D("ss_wages")),
        FormLine(line_number="Box4",  description="SS retenido (empleado 6.2%)",                   amount=D("ss_tax_withheld"),  rule_ref="FICA_SS_2024_V1"),
        FormLine(line_number="Box5",  description="Salarios de Medicare",                          amount=D("medicare_wages")),
        FormLine(line_number="Box6",  description="Medicare retenido (empleado 1.45%)",            amount=D("medicare_tax_withheld"), rule_ref="FICA_MEDICARE_2024_V1"),
        FormLine(line_number="CajaA", description="Salarios sujetos a contribución PR",            amount=D("pr_wages")),
        FormLine(line_number="CajaB", description="Contribución sobre ingresos PR retenida",       amount=D("pr_income_tax")),
        FormLine(line_number="CajaC", description="Seguro por incapacidad (SINOT) retenido",       amount=D("pr_disability_tax")),
        FormLine(line_number="CajaD", description="Contribuciones a organizaciones de caridad",    amount=D("pr_charity")),
        FormLine(line_number="EmpID", description="Identificador de empleado (sistema)",           text_value=str(fiscal_data.get("employee_id", "")), is_computed=True),
        FormLine(line_number="SSN4",  description="Últimos 4 dígitos SSN (solo display)",          text_value=str(fiscal_data.get("ssn_last4", "****")), is_computed=True),
    ]

    summary = {
        "employee_id":         fiscal_data.get("employee_id"),
        "ssn_last4":           fiscal_data.get("ssn_last4", "****"),
        "federal_wages":       str(D("wages_federal")),
        "pr_wages":            str(D("pr_wages")),
        "total_tax_withheld":  str(D("fed_income_withheld") + D("pr_income_tax")),
        "ss_total":            str(D("ss_tax_withheld")),
        "medicare_total":      str(D("medicare_tax_withheld")),
    }

    return TaxForm(
        form_type=FormType.W2_PR,
        tax_period=TaxPeriod.ANNUAL,
        client_id=req.client_id,
        business_name=req.business_name,
        ein_pr=req.ein_pr,
        tax_year=req.tax_year,
        period_start=req.period_start,
        period_end=req.period_end,
        fiscal_result_ids=list(req.fiscal_result_ids),
        lines=lines,
        summary=summary,
    )


def _d(data: Dict[str, Any], key: str) -> Decimal:
    return Decimal(str(data.get(key, "0"))).quantize(Decimal("0.01"))
