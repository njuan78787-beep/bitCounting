# =============================================================================
# AS2879 — Planilla para la Declaración y Pago de Contribución
#           por Desempleo del Estado (SUTA)
# Departamento del Trabajo y Recursos Humanos (DTRH) de Puerto Rico
#
# SUTA rates vary by employer experience rating (0.1% – 5.4%)
# Taxable wage base: first $7,000 per employee per year (2024)
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

from ..models import FormLine, FormType, TaxForm, TaxPeriod

_SUTA_WAGE_BASE = Decimal("7000")    # taxable wage base per employee


def generate_as2879(req, fiscal_data: Dict[str, Any]) -> TaxForm:
    """
    Generate AS2879 Puerto Rico SUTA (unemployment tax) return.

    fiscal_data keys:
      total_gross_wages     — total wages paid this quarter
      taxable_wages_suta    — wages up to $7,000 per employee (pre-computed)
      suta_rate             — employer experience rate (decimal, e.g. 0.027)
      employee_count        — employees covered this quarter
      suta_tax_due          — computed SUTA liability
      prior_credit          — any credit from prior quarter
      deposits_made         — SUTA deposits already made
    """
    D = lambda k: _d(fiscal_data, k)

    total_gross   = D("total_gross_wages")
    taxable_wages = D("taxable_wages_suta")
    suta_rate     = Decimal(str(fiscal_data.get("suta_rate", "0.027")))
    computed_tax  = (taxable_wages * suta_rate).quantize(Decimal("0.01"))
    # Use provided value if available (sandbox-computed), else use our computed
    suta_tax_due  = D("suta_tax_due") or computed_tax
    prior_credit  = D("prior_credit")
    deposits      = D("deposits_made")
    balance_due   = (suta_tax_due - prior_credit - deposits).quantize(Decimal("0.01"))

    lines = [
        FormLine(line_number="1",  description="Total de salarios brutos pagados en el trimestre",  amount=total_gross),
        FormLine(line_number="2",  description="Salarios exentos (>$7,000 por empleado)",           amount=total_gross - taxable_wages),
        FormLine(line_number="3",  description="Salarios sujetos a contribución por desempleo",     amount=taxable_wages),
        FormLine(line_number="4",  description="Tasa de contribución aplicable",                    text_value=f"{suta_rate * 100:.2f}%", is_computed=True),
        FormLine(line_number="5",  description="Contribución de desempleo (Línea 3 × Línea 4)",    amount=suta_tax_due, rule_ref="SUTA_DTRH_PR_2024_V1"),
        FormLine(line_number="6",  description="Crédito de trimestre anterior",                     amount=prior_credit),
        FormLine(line_number="7",  description="Pagos realizados en el trimestre",                  amount=deposits),
        FormLine(line_number="8",  description="BALANCE A PAGAR / (CRÉDITO)",                       amount=balance_due),
        FormLine(line_number="9",  description="Número de empleados cubiertos",                     text_value=str(fiscal_data.get("employee_count", 0))),
    ]

    summary = {
        "total_gross_wages": str(total_gross),
        "taxable_wages":     str(taxable_wages),
        "suta_rate":         str(suta_rate),
        "suta_tax_due":      str(suta_tax_due),
        "balance_due":       str(balance_due),
        "employee_count":    fiscal_data.get("employee_count", 0),
    }

    return TaxForm(
        form_type=FormType.AS2879,
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
