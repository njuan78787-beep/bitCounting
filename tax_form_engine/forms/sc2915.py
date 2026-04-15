# =============================================================================
# SC2915 — Declaración Mensual/Trimestral de IVU
# Departamento de Hacienda de Puerto Rico
#
# IVU ESTATAL:    10.5% on taxable sales
# IVU MUNICIPAL:  1.0%  on taxable sales (varies by municipality)
# IVU_TOTAL:      11.5% combined
#
# Lines map to official SC2915 form sections as of 2024.
# =============================================================================

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict

from ..models import FormLine, FormType, TaxForm, TaxPeriod


def generate_sc2915(req, fiscal_data: Dict[str, Any]) -> TaxForm:
    """
    Generate a SC2915 IVU declaration form from aggregated fiscal data.

    fiscal_data keys used:
      ivu_estatal_collected  — IVU estatal cobrado a clientes (positivo)
      ivu_municipal_collected — IVU municipal cobrado
      ivu_estatal_credits    — IVU de compras/insumos (crédito, negativo)
      ivu_municipal_credits  — IVU municipal de compras
      taxable_sales          — Base imponible de ventas
      exempt_sales           — Ventas exentas
      total_sales            — Ventas totales
    """
    D = lambda k: _d(fiscal_data, k)

    taxable_sales        = D("taxable_sales")
    exempt_sales         = D("exempt_sales")
    total_sales          = D("total_sales")
    ivu_estatal_col      = D("ivu_estatal_collected")
    ivu_municipal_col    = D("ivu_municipal_collected")
    ivu_estatal_credits  = D("ivu_estatal_credits")
    ivu_municipal_credits= D("ivu_municipal_credits")

    # Net IVU owed = collected − input credits
    net_ivu_estatal   = (ivu_estatal_col - ivu_estatal_credits).quantize(Decimal("0.01"))
    net_ivu_municipal = (ivu_municipal_col - ivu_municipal_credits).quantize(Decimal("0.01"))
    total_ivu_due     = (net_ivu_estatal + net_ivu_municipal).quantize(Decimal("0.01"))

    lines = [
        FormLine(line_number="1",  description="Total de ventas y servicios",               amount=total_sales),
        FormLine(line_number="2",  description="Ventas exentas",                            amount=exempt_sales),
        FormLine(line_number="3",  description="Ventas sujetas a IVU (Línea 1 − Línea 2)", amount=taxable_sales),
        FormLine(line_number="4",  description="IVU estatal cobrado (10.5%)",               amount=ivu_estatal_col,      rule_ref="IVU_ESTATAL_PR_2015_V1"),
        FormLine(line_number="5",  description="IVU municipal cobrado (1.0%)",              amount=ivu_municipal_col,    rule_ref="IVU_MUNICIPAL_PR_2015_V1"),
        FormLine(line_number="6",  description="IVU de compras / crédito de insumos (estatal)", amount=ivu_estatal_credits),
        FormLine(line_number="7",  description="IVU de compras / crédito de insumos (municipal)", amount=ivu_municipal_credits),
        FormLine(line_number="8",  description="IVU estatal neto a pagar (Línea 4 − Línea 6)", amount=net_ivu_estatal),
        FormLine(line_number="9",  description="IVU municipal neto a pagar (Línea 5 − Línea 7)", amount=net_ivu_municipal),
        FormLine(line_number="10", description="TOTAL IVU A PAGAR",                         amount=total_ivu_due),
    ]

    summary = {
        "total_sales":       str(total_sales),
        "taxable_sales":     str(taxable_sales),
        "exempt_sales":      str(exempt_sales),
        "ivu_estatal_due":   str(net_ivu_estatal),
        "ivu_municipal_due": str(net_ivu_municipal),
        "total_due":         str(total_ivu_due),
    }

    return TaxForm(
        form_type=FormType.SC2915,
        tax_period=req.tax_period,
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
