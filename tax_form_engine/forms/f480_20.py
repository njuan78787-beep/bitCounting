# =============================================================================
# 480.20 — Planilla de Contribución sobre Ingresos de Corporaciones
# Departamento de Hacienda de Puerto Rico
#
# Corporate income tax rates (2024):
#   First $75,000     : 18.5%
#   $75,001–$150,000  : 21%
#   $150,001–$250,000 : 25%
#   Over $250,000     : 28%
#   Act 60 decree holders: special negotiated rates
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

from ..models import FormLine, FormType, TaxForm, TaxPeriod


# Corporate tax brackets (taxable net income)
_BRACKETS = [
    (Decimal("75000"),   Decimal("0.185")),
    (Decimal("75000"),   Decimal("0.21")),
    (Decimal("100000"),  Decimal("0.25")),
    (None,               Decimal("0.28")),
]


def generate_480_20(req, fiscal_data: Dict[str, Any]) -> TaxForm:
    """
    Generate 480.20 Corporate Income Tax Return.

    fiscal_data keys:
      gross_income         — total gross income
      cost_of_goods_sold   — COGS
      gross_profit         — gross_income − COGS
      operating_expenses   — deductible operating expenses
      net_operating_income — gross_profit − operating_expenses
      other_income         — dividends, interest, other
      total_deductions     — depreciation, NOL, etc.
      net_taxable_income   — final taxable base
      act_60_decree        — bool — Act 60 negotiated rate applies
      act_60_rate          — Decimal — negotiated rate if Act 60
      prepaid_taxes        — withholdings / estimated payments
    """
    D = lambda k: _d(fiscal_data, k)

    gross_income       = D("gross_income")
    cogs               = D("cost_of_goods_sold")
    gross_profit       = D("gross_profit") or (gross_income - cogs)
    operating_expenses = D("operating_expenses")
    net_op_income      = D("net_operating_income") or (gross_profit - operating_expenses)
    other_income       = D("other_income")
    total_deductions   = D("total_deductions")
    net_taxable        = D("net_taxable_income") or (net_op_income + other_income - total_deductions)
    prepaid_taxes      = D("prepaid_taxes")

    act_60 = bool(fiscal_data.get("act_60_decree", False))
    act_60_rate = Decimal(str(fiscal_data.get("act_60_rate", "0")))

    if act_60 and act_60_rate > 0:
        tax_liability = (net_taxable * act_60_rate).quantize(Decimal("0.01"))
        rate_note = f"Act 60 decree rate: {act_60_rate * 100:.2f}%"
    else:
        tax_liability = _graduated_tax(net_taxable)
        rate_note = "Graduated rate schedule (Section 1022 PRIRC)"

    balance_due = (tax_liability - prepaid_taxes).quantize(Decimal("0.01"))

    lines = [
        FormLine(line_number="1",    description="Ingresos brutos totales",                   amount=gross_income),
        FormLine(line_number="2",    description="Costo de ventas y gastos directos",          amount=cogs),
        FormLine(line_number="3",    description="Ganancia bruta (Línea 1 − Línea 2)",         amount=gross_profit),
        FormLine(line_number="4",    description="Gastos de operación deducibles",             amount=operating_expenses),
        FormLine(line_number="5",    description="Ingresos netos de operación",                amount=net_op_income),
        FormLine(line_number="6",    description="Otros ingresos (dividendos, intereses)",     amount=other_income),
        FormLine(line_number="7",    description="Deducciones adicionales (depreciación, NOL)",amount=total_deductions),
        FormLine(line_number="8",    description="Ingresos netos sujetos a contribución",      amount=net_taxable),
        FormLine(line_number="9",    description="Contribución sobre ingresos computada",      amount=tax_liability, note=rate_note, rule_ref="CORP_INCOME_TAX_PR_2024_V1"),
        FormLine(line_number="10",   description="Pagos estimados y retenciones",              amount=prepaid_taxes),
        FormLine(line_number="11",   description="BALANCE A PAGAR / (CRÉDITO)",                amount=balance_due),
    ]

    summary = {
        "gross_income":    str(gross_income),
        "net_taxable":     str(net_taxable),
        "tax_liability":   str(tax_liability),
        "prepaid_taxes":   str(prepaid_taxes),
        "balance_due":     str(balance_due),
        "act_60_applied":  act_60,
    }

    return TaxForm(
        form_type=FormType.F480_20,
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


def _graduated_tax(taxable: Decimal) -> Decimal:
    """Apply graduated corporate tax brackets to net taxable income."""
    tax = Decimal("0")
    remaining = max(taxable, Decimal("0"))
    for bracket_size, rate in _BRACKETS:
        if remaining <= 0:
            break
        if bracket_size is None:
            taxable_in_bracket = remaining
        else:
            taxable_in_bracket = min(remaining, bracket_size)
        tax += (taxable_in_bracket * rate).quantize(Decimal("0.01"))
        remaining -= taxable_in_bracket
    return tax


def _d(data: Dict[str, Any], key: str) -> Decimal:
    return Decimal(str(data.get(key, "0"))).quantize(Decimal("0.01"))
