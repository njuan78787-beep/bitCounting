# =============================================================================
# 941-PR — Planilla para la Declaración Federal TRIMESTRAL del Patrono
# IRS Form 941-PR (Puerto Rico version)
#
# Covers:
#   - FICA Social Security (employer 6.2% + employee 6.2% = 12.4% on wages up to $168,600)
#   - FICA Medicare (employer 1.45% + employee 1.45% = 2.9%, no wage cap)
#   - Federal income tax withheld from Puerto Rico employees
#   - Additional Medicare Tax (0.9% on wages > $200k — employee only)
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

from ..models import FormLine, FormType, TaxForm, TaxPeriod

_SS_WAGE_CAP      = Decimal("168600")    # 2024 Social Security wage base
_SS_RATE_COMBINED = Decimal("0.124")     # 6.2% employer + 6.2% employee
_MEDICARE_RATE    = Decimal("0.029")     # 1.45% + 1.45%
_ADD_MEDICARE     = Decimal("0.009")     # Additional 0.9% (employee only, >$200k)


def generate_941_pr(req, fiscal_data: Dict[str, Any]) -> TaxForm:
    """
    Generate IRS 941-PR quarterly payroll tax return.

    fiscal_data keys:
      total_wages_tips      — total wages and tips subject to FICA
      wages_over_ss_cap     — wages exceeding Social Security wage base
      wages_over_add_med    — wages subject to Additional Medicare (>$200k)
      fed_income_tax_withheld — federal income tax withheld
      employee_count        — number of employees during quarter
      month1_tax / month2_tax / month3_tax — monthly tax liability breakdown
      deposits_made         — total federal tax deposits made in quarter
    """
    D = lambda k: _d(fiscal_data, k)

    total_wages         = D("total_wages_tips")
    wages_over_ss_cap   = D("wages_over_ss_cap")
    wages_over_add_med  = D("wages_over_add_med")
    fed_income_withheld = D("fed_income_tax_withheld")
    deposits_made       = D("deposits_made")

    ss_wages         = max(total_wages - wages_over_ss_cap, Decimal("0"))
    ss_tax           = (ss_wages * _SS_RATE_COMBINED).quantize(Decimal("0.01"))
    medicare_tax     = (total_wages * _MEDICARE_RATE).quantize(Decimal("0.01"))
    add_medicare_tax = (wages_over_add_med * _ADD_MEDICARE).quantize(Decimal("0.01"))

    total_taxes     = (fed_income_withheld + ss_tax + medicare_tax + add_medicare_tax).quantize(Decimal("0.01"))
    balance_due     = (total_taxes - deposits_made).quantize(Decimal("0.01"))

    month1 = D("month1_tax")
    month2 = D("month2_tax")
    month3 = D("month3_tax")

    lines = [
        FormLine(line_number="1",    description="Número de empleados durante el trimestre", text_value=str(fiscal_data.get("employee_count", 0))),
        FormLine(line_number="2",    description="Salarios, propinas y otras remuneraciones",         amount=total_wages),
        FormLine(line_number="3",    description="Contribución sobre ingresos retenida de salarios",  amount=fed_income_withheld),
        FormLine(line_number="5a",   description="Salarios sujetos a SS (hasta límite anual)",        amount=ss_wages),
        FormLine(line_number="5a(ii)",description="Contribución SS — patrono y empleado (12.4%)",     amount=ss_tax, rule_ref="FICA_SS_2024_V1"),
        FormLine(line_number="5c",   description="Salarios sujetos a Medicare",                       amount=total_wages),
        FormLine(line_number="5c(ii)",description="Contribución Medicare — patrono y empleado (2.9%)",amount=medicare_tax, rule_ref="FICA_MEDICARE_2024_V1"),
        FormLine(line_number="5d",   description="Salarios sujetos a Medicare Adicional (>$200k)",   amount=wages_over_add_med),
        FormLine(line_number="5d(ii)",description="Medicare Adicional (0.9%)",                        amount=add_medicare_tax),
        FormLine(line_number="6",    description="Total de contribuciones (Líneas 3+5a+5c+5d)",       amount=total_taxes),
        FormLine(line_number="13",   description="Depósitos federales realizados en el trimestre",    amount=deposits_made),
        FormLine(line_number="14",   description="BALANCE A DEPOSITAR / (CRÉDITO)",                   amount=balance_due),
        FormLine(line_number="M1",   description="Responsabilidad tributaria — Mes 1",               amount=month1),
        FormLine(line_number="M2",   description="Responsabilidad tributaria — Mes 2",               amount=month2),
        FormLine(line_number="M3",   description="Responsabilidad tributaria — Mes 3",               amount=month3),
    ]

    summary = {
        "total_wages":          str(total_wages),
        "ss_tax":               str(ss_tax),
        "medicare_tax":         str(medicare_tax),
        "additional_medicare":  str(add_medicare_tax),
        "fed_income_withheld":  str(fed_income_withheld),
        "total_taxes":          str(total_taxes),
        "deposits_made":        str(deposits_made),
        "balance_due":          str(balance_due),
        "employee_count":       fiscal_data.get("employee_count", 0),
    }

    return TaxForm(
        form_type=FormType.F941_PR,
        tax_period=TaxPeriod.QUARTERLY,
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
