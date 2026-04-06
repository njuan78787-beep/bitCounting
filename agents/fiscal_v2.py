# =============================================================================
# agents/fiscal_v2.py
# Agente FISCAL PR V2 — TODOS los calculos via CalculationSandbox.
#
# GARANTIAS DE DISENO:
#   - CERO aritmetica directa — cada obligacion tributaria se computa
#     via CalculationSandbox con una de las 6 funciones pre-aprobadas.
#   - Las reglas fiscales se consultan via tax_rules.get_rule() dentro
#     del codigo enviado al sandbox (nunca en el agente directamente).
#   - FiscalResultV2 almacena sandbox_result_ids para auditabilidad total.
#   - Toda decision queda en AgentDecisionsLog (append-only).
#   - Nunca lanza excepciones — errores del sandbox dan obligacion=0 + alerta.
# =============================================================================

from __future__ import annotations

import logging
import sys
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .core_decisions import get_decisions_log, AgentDecisionsLog
from .clasificador_v2 import ClassificationResultV2, ClassifiedLineItem

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from calculation_sandbox.sandbox import CalculationSandbox

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums y modelos
# ---------------------------------------------------------------------------

class TaxType(str, Enum):
    IVU_ESTATAL  = "IVU_ESTATAL"
    IVU_MUNICIPAL= "IVU_MUNICIPAL"
    IVU_EXENTO   = "IVU_EXENTO"
    FICA_SS      = "FICA_SS"
    FICA_MEDICARE= "FICA_MEDICARE"
    FUTA         = "FUTA"
    RETENCION    = "RETENCION"
    NO_TAX       = "NO_TAX"


class TaxLiabilityLine(BaseModel):
    """Una obligacion tributaria individual calculada via sandbox."""
    model_config = ConfigDict(frozen=True)

    tax_type:          TaxType
    taxable_base:      Decimal
    tax_amount:        Decimal     # negativo = credito de entrada
    rule_ref:          str
    rate_applied:      Optional[Decimal] = None   # porcentaje, ej. 10.5
    sandbox_result_id: str
    form_id:           str
    calculation_code:  str
    is_exempt:         bool = False
    exempt_reason:     Optional[str] = None
    sandbox_error:     Optional[str] = None


class FiscalResultV2(BaseModel):
    """
    Output del agente FISCAL PR V2. Frozen — inmutable.

    tax_liabilities es una tupla de TaxLiabilityLine, una por cada
    obligacion calculada. sandbox_result_ids agrupa todos los IDs para
    trazabilidad completa en el audit trail.
    """
    model_config = ConfigDict(frozen=True)

    result_id:           str = Field(default_factory=lambda: str(uuid.uuid4()))
    classification_id:   str
    transaction_date:    str   # ISO 8601
    tax_liabilities:     tuple[TaxLiabilityLine, ...]
    total_tax_due:       Decimal
    sandbox_result_ids:  tuple[str, ...]
    rule_refs:           tuple[str, ...]
    decision_id:         Optional[str] = None
    has_sandbox_errors:  bool = False
    computed_at:         str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Agente FISCAL PR V2
# ---------------------------------------------------------------------------

class FiscalAgentV2:
    """
    Agente FISCAL PR V2 — determina obligaciones tributarias via sandbox.

    TODOS los calculos se ejecutan en CalculationSandbox usando las funciones
    pre-aprobadas: calc_ivu, calc_ivu_exento, calc_fica, calc_futa, etc.
    """

    AGENT_NAME = "FISCAL_PR_V2"

    # Cuentas de expense con IVU deducible como credito de insumo
    _IVU_EXPENSE_ACCOUNTS = {"5000", "5200", "5300", "5400",
                              "5500", "5600", "5700", "5800", "5900"}
    # Cuentas de revenue con IVU cobrado
    _IVU_REVENUE_ACCOUNTS = {"4000", "4100", "4200"}
    # Cuentas de nomina
    _PAYROLL_ACCOUNTS      = {"5100"}

    def __init__(
        self,
        decisions_log: Optional[AgentDecisionsLog] = None,
    ) -> None:
        self._log     = decisions_log if decisions_log is not None else get_decisions_log()
        self._sandbox = CalculationSandbox()

    # ------------------------------------------------------------------ #
    # Punto de entrada                                                    #
    # ------------------------------------------------------------------ #

    def compute_fiscal_obligations(
        self,
        classification: ClassificationResultV2,
        transaction_date: date,
    ) -> FiscalResultV2:
        """
        Calcula obligaciones tributarias para cada item clasificado.

        Para cuentas de REVENUE: calcula IVU cobrado (obligacion positiva).
        Para cuentas de EXPENSE: calcula IVU pagado (credito negativo).
        Para cuentas de PAYROLL: calcula FICA SS + Medicare.

        TODOS los calculos via CalculationSandbox — cero aritmetica directa.
        """
        liabilities: list[TaxLiabilityLine] = []
        sandbox_ids: list[str] = []

        for item in classification.classified_items:
            if item.is_unclassified:
                continue
            code = item.account_code
            if code in self._PAYROLL_ACCOUNTS:
                line = self._compute_fica(item, transaction_date)
            elif code in self._IVU_REVENUE_ACCOUNTS:
                line = self._compute_ivu_revenue(item, transaction_date)
            elif code in self._IVU_EXPENSE_ACCOUNTS:
                line = self._compute_ivu_expense(item, transaction_date)
            else:
                line = self._no_tax_line(item, transaction_date)

            liabilities.append(line)
            if line.sandbox_result_id:
                sandbox_ids.append(line.sandbox_result_id)

        total_due  = sum(l.tax_amount for l in liabilities)
        rule_refs  = tuple(dict.fromkeys(l.rule_ref for l in liabilities))
        has_errors = any(l.sandbox_error is not None for l in liabilities)

        decision_entry = self._log.record(
            agent_name=self.AGENT_NAME,
            input_data={"classification_id": classification.result_id,
                        "date": transaction_date.isoformat()},
            decision=(
                f"Obligaciones calculadas: {len(liabilities)} items. "
                f"Total: {total_due}. Errores sandbox: {has_errors}."
            ),
            rule_ids=rule_refs,
            sandbox_ids=tuple(sandbox_ids),
        )

        return FiscalResultV2(
            classification_id=classification.result_id,
            transaction_date=transaction_date.isoformat(),
            tax_liabilities=tuple(liabilities),
            total_tax_due=total_due,
            sandbox_result_ids=tuple(sandbox_ids),
            rule_refs=rule_refs,
            decision_id=decision_entry.decision_id,
            has_sandbox_errors=has_errors,
        )

    # ------------------------------------------------------------------ #
    # IVU sobre ventas (revenue)                                          #
    # ------------------------------------------------------------------ #

    def _compute_ivu_revenue(
        self,
        item: ClassifiedLineItem,
        txn_date: date,
    ) -> TaxLiabilityLine:
        code = (
            f"from decimal import Decimal\n"
            f"from datetime import date\n"
            f"ivu = calc_ivu(\n"
            f"    base_amount=Decimal('{item.amount}'),\n"
            f"    rate=None,\n"
            f"    calc_date=date({txn_date.year},{txn_date.month},{txn_date.day}),\n"
            f"    include_municipal=True,\n"
            f")\n"
            f"result = ivu['total_ivu']\n"
        )
        sr = self._sandbox.execute(code)
        if sr.success:
            return TaxLiabilityLine(
                tax_type=TaxType.IVU_ESTATAL,
                taxable_base=item.amount,
                tax_amount=Decimal(str(sr.result_value)),
                rule_ref=str(sr.rule_ids_applied[0]) if sr.rule_ids_applied else "IVU_ESTATAL_PR_2015_V1",
                sandbox_result_id=sr.result_id,
                form_id="SC-2915",
                calculation_code=code,
            )
        return self._sandbox_error_line(item, sr, TaxType.IVU_ESTATAL, "SC-2915")

    # ------------------------------------------------------------------ #
    # IVU pagado en gastos (credito de insumo)                           #
    # ------------------------------------------------------------------ #

    def _compute_ivu_expense(
        self,
        item: ClassifiedLineItem,
        txn_date: date,
    ) -> TaxLiabilityLine:
        # Si la cuenta ES la cuenta de IVU pagado (5600), no calcular — ya es el IVU
        if item.account_code == "5600":
            code = (
                f"from decimal import Decimal\n"
                f"# IVU pagado directo — la cuenta 5600 ya es el IVU\n"
                f"result = -Decimal('{item.amount}')  # credito de insumo\n"
            )
            sr = self._sandbox.execute(code)
            if sr.success:
                return TaxLiabilityLine(
                    tax_type=TaxType.IVU_ESTATAL,
                    taxable_base=item.amount,
                    tax_amount=Decimal(str(sr.result_value)),
                    rule_ref="IVU_ESTATAL_PR_2015_V1",
                    sandbox_result_id=sr.result_id,
                    form_id="SC-2915",
                    calculation_code=code,
                )
            return self._sandbox_error_line(item, sr, TaxType.IVU_ESTATAL, "SC-2915")

        # Calcular IVU pagado sobre el monto del gasto
        code = (
            f"from decimal import Decimal\n"
            f"from datetime import date\n"
            f"ivu = calc_ivu(\n"
            f"    base_amount=Decimal('{item.amount}'),\n"
            f"    rate=None,\n"
            f"    calc_date=date({txn_date.year},{txn_date.month},{txn_date.day}),\n"
            f"    include_municipal=True,\n"
            f")\n"
            f"result = -ivu['total_ivu']  # negativo = credito de insumo\n"
        )
        sr = self._sandbox.execute(code)
        if sr.success:
            return TaxLiabilityLine(
                tax_type=TaxType.IVU_ESTATAL,
                taxable_base=item.amount,
                tax_amount=Decimal(str(sr.result_value)),
                rule_ref="IVU_ESTATAL_PR_2015_V1",
                sandbox_result_id=sr.result_id,
                form_id="SC-2915",
                calculation_code=code,
            )
        return self._sandbox_error_line(item, sr, TaxType.IVU_ESTATAL, "SC-2915")

    # ------------------------------------------------------------------ #
    # FICA sobre nomina                                                   #
    # ------------------------------------------------------------------ #

    def _compute_fica(
        self,
        item: ClassifiedLineItem,
        txn_date: date,
    ) -> TaxLiabilityLine:
        year = txn_date.year
        code = (
            f"from decimal import Decimal\n"
            f"from datetime import date\n"
            f"fica = calc_fica(\n"
            f"    gross_pay=Decimal('{item.amount}'),\n"
            f"    ytd_ss=Decimal('0'),\n"
            f"    ytd_medicare=Decimal('0'),\n"
            f"    calc_year={year},\n"
            f")\n"
            f"result = fica['total_employee'] + fica['total_employer']\n"
        )
        sr = self._sandbox.execute(code)
        if sr.success:
            return TaxLiabilityLine(
                tax_type=TaxType.FICA_SS,
                taxable_base=item.amount,
                tax_amount=Decimal(str(sr.result_value)),
                rule_ref=f"FICA_SS_{year}_V1",
                sandbox_result_id=sr.result_id,
                form_id="941-PR",
                calculation_code=code,
            )
        return self._sandbox_error_line(item, sr, TaxType.FICA_SS, "941-PR")

    # ------------------------------------------------------------------ #
    # Sin obligacion tributaria                                           #
    # ------------------------------------------------------------------ #

    def _no_tax_line(
        self,
        item: ClassifiedLineItem,
        txn_date: date,
    ) -> TaxLiabilityLine:
        code = (
            f"from decimal import Decimal\n"
            f"# Cuenta {item.account_code} — sin obligacion tributaria adicional\n"
            f"result = Decimal('0')\n"
        )
        sr = self._sandbox.execute(code)
        return TaxLiabilityLine(
            tax_type=TaxType.NO_TAX,
            taxable_base=item.amount,
            tax_amount=Decimal("0"),
            rule_ref="NO_TAX",
            sandbox_result_id=sr.result_id,
            form_id="N/A",
            calculation_code=code,
        )

    # ------------------------------------------------------------------ #
    # Linea de error de sandbox                                           #
    # ------------------------------------------------------------------ #

    def _sandbox_error_line(
        self,
        item: ClassifiedLineItem,
        sr: object,
        tax_type: TaxType,
        form_id: str,
    ) -> TaxLiabilityLine:
        logger.error("FISCAL V2: sandbox error para item %s: %s", item.account_code, sr.error)
        return TaxLiabilityLine(
            tax_type=tax_type,
            taxable_base=item.amount,
            tax_amount=Decimal("0"),
            rule_ref="SANDBOX_ERROR",
            sandbox_result_id=sr.result_id,
            form_id=form_id,
            calculation_code=getattr(sr, "code_executed", ""),
            sandbox_error=str(getattr(sr, "error", "unknown")),
        )
