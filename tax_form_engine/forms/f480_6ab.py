# =============================================================================
# 480.6A — Declaración Informativa — Ingresos Pasivos
# 480.6B — Declaración Informativa — Servicios / Contratistas Independientes
# Departamento de Hacienda de Puerto Rico
#
# 480.6A covers: dividends, interest income, rent, royalties, annuities
# 480.6B covers: payments to independent contractors for services (≥$500/year)
#
# These are informative returns — no tax due directly on these forms.
# They are submitted to Hacienda and a copy to each payee.
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from ..models import FormLine, FormType, TaxForm, TaxPeriod


def generate_480_6a(req, fiscal_data: Dict[str, Any]) -> TaxForm:
    """
    Generate 480.6A — Declaración Informativa de Ingresos Pasivos.

    fiscal_data keys:
      dividends_paid       — dividends paid to shareholders
      interest_paid        — interest paid on loans / bonds
      rents_paid           — rent payments made to landlords
      royalties_paid       — royalty payments
      annuities_paid       — annuity payments
      withholding_rate     — applicable withholding rate (e.g. 0.10)
      payee_count          — number of payees
      recipients           — list of {name, ein_or_ssn_last4, amount, type}
    """
    D = lambda k: _d(fiscal_data, k)

    dividends   = D("dividends_paid")
    interest    = D("interest_paid")
    rents       = D("rents_paid")
    royalties   = D("royalties_paid")
    annuities   = D("annuities_paid")
    total_paid  = (dividends + interest + rents + royalties + annuities).quantize(Decimal("0.01"))
    wh_rate     = Decimal(str(fiscal_data.get("withholding_rate", "0.10")))
    total_withheld = (total_paid * wh_rate).quantize(Decimal("0.01"))

    lines = [
        FormLine(line_number="1",  description="Dividendos pagados",                    amount=dividends),
        FormLine(line_number="2",  description="Intereses pagados",                     amount=interest),
        FormLine(line_number="3",  description="Arrendamientos pagados",                amount=rents),
        FormLine(line_number="4",  description="Regalías pagadas",                     amount=royalties),
        FormLine(line_number="5",  description="Anualidades pagadas",                   amount=annuities),
        FormLine(line_number="6",  description="Total de pagos informados",             amount=total_paid),
        FormLine(line_number="7",  description="Tasa de retención aplicada",            text_value=f"{wh_rate * 100:.1f}%"),
        FormLine(line_number="8",  description="Total de contribución retenida",        amount=total_withheld, rule_ref="WITHHOLDING_PR_2024_V1"),
        FormLine(line_number="9",  description="Número de beneficiarios",              text_value=str(fiscal_data.get("payee_count", 0))),
    ]

    # Recipient detail lines (one per payee — no full SSN, last 4 only)
    for i, r in enumerate(fiscal_data.get("recipients", []), start=1):
        lines.append(FormLine(
            line_number=f"R{i:03d}",
            description=f"Beneficiario: {r.get('name', 'N/A')} — {r.get('type', '')}",
            amount=_d(r, "amount"),
            text_value=f"ID: ***{str(r.get('ein_or_ssn_last4', '????'))[-4:]}",
            is_computed=True,
        ))

    summary = {
        "total_paid":        str(total_paid),
        "total_withheld":    str(total_withheld),
        "withholding_rate":  str(wh_rate),
        "payee_count":       fiscal_data.get("payee_count", 0),
    }

    return TaxForm(
        form_type=FormType.F480_6A,
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


def generate_480_6b(req, fiscal_data: Dict[str, Any]) -> TaxForm:
    """
    Generate 480.6B — Declaración Informativa de Servicios / Contratistas.

    Reports payments of $500+ to independent contractors.

    fiscal_data keys:
      total_services_paid   — total fees paid to contractors
      withholding_rate      — applicable withholding rate
      total_withheld        — total PR tax withheld from contractors
      contractor_count      — number of contractors
      contractors           — list of {name, ein_or_ssn_last4, amount, service_type}
    """
    D = lambda k: _d(fiscal_data, k)

    total_paid  = D("total_services_paid")
    wh_rate     = Decimal(str(fiscal_data.get("withholding_rate", "0.10")))
    withheld    = D("total_withheld") or (total_paid * wh_rate).quantize(Decimal("0.01"))

    lines = [
        FormLine(line_number="1",  description="Total de pagos a contratistas independientes", amount=total_paid),
        FormLine(line_number="2",  description="Tasa de retención",                           text_value=f"{wh_rate * 100:.1f}%"),
        FormLine(line_number="3",  description="Total de contribución retenida a contratistas", amount=withheld, rule_ref="WITHHOLDING_SERVICES_PR_2024_V1"),
        FormLine(line_number="4",  description="Número de contratistas reportados",           text_value=str(fiscal_data.get("contractor_count", 0))),
    ]

    for i, c in enumerate(fiscal_data.get("contractors", []), start=1):
        lines.append(FormLine(
            line_number=f"C{i:03d}",
            description=f"Contratista: {c.get('name', 'N/A')} — {c.get('service_type', '')}",
            amount=_d(c, "amount"),
            text_value=f"ID: ***{str(c.get('ein_or_ssn_last4', '????'))[-4:]}",
            is_computed=True,
        ))

    summary = {
        "total_paid":       str(total_paid),
        "total_withheld":   str(withheld),
        "withholding_rate": str(wh_rate),
        "contractor_count": fiscal_data.get("contractor_count", 0),
    }

    return TaxForm(
        form_type=FormType.F480_6B,
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
