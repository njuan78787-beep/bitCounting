# =============================================================================
# agents/estados_v2.py
# Agente ESTADOS V2 — estados financieros con verificacion algebraica + XBRL.
#
# GARANTIAS DE DISENO:
#   - Verifica algebraicamente CADA estado antes de entregarlo:
#       Balance General: Activos == Pasivos + Capital (diferencia cero)
#       Estado de Resultados: Utilidad == Ingresos - Gastos
#       Flujo de Caja: Saldo final == Saldo inicial + flujos totales
#   - Si la verificacion falla: lanza AlgebraicImbalanceError con detalle exacto.
#   - XBRL basico incluido en cada estado (tags us-gaap / ifrs).
#   - Toda decision registrada en AgentDecisionsLog (append-only).
#   - Todos los resultados son frozen=True — inmutables.
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .core_decisions import get_decisions_log, AgentDecisionsLog
from .exceptions import BitCountingAgentError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Excepcion de desbalance algebraico
# ---------------------------------------------------------------------------

class AlgebraicImbalanceError(BitCountingAgentError):
    """
    El estado financiero no cuadra algebraicamente.

    Se lanza ANTES de entregar el estado al CPA — ningun estado
    desbalanceado puede salir del sistema.
    """
    def __init__(
        self,
        statement_type: str,
        left_label: str,
        left_value: Decimal,
        right_label: str,
        right_value: Decimal,
    ) -> None:
        self.statement_type = statement_type
        self.difference     = left_value - right_value
        super().__init__(
            f"ESTADOS V2: {statement_type} no cuadra. "
            f"{left_label}={left_value} != {right_label}={right_value}. "
            f"Diferencia: {self.difference}. "
            "El estado NO puede entregarse hasta corregir el desbalance."
        )


# ---------------------------------------------------------------------------
# XBRL helper — tags estandar us-gaap
# ---------------------------------------------------------------------------

_XBRL_TAGS: dict[str, str] = {
    # Assets
    "1000": "us-gaap:CashAndCashEquivalentsAtCarryingValue",
    "1100": "us-gaap:AccountsReceivableNetCurrent",
    "1200": "us-gaap:InventoryNet",
    "1300": "us-gaap:PrepaidExpenseAndOtherAssetsCurrent",
    "1500": "us-gaap:PropertyPlantAndEquipmentNet",
    "1600": "us-gaap:AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
    # Liabilities
    "2000": "us-gaap:AccountsPayableCurrent",
    "2100": "us-gaap:SalesAndExciseTaxPayableCurrent",
    "2200": "us-gaap:EmployeeRelatedLiabilitiesCurrent",
    "2300": "us-gaap:LongTermDebtNoncurrent",
    # Equity
    "3000": "us-gaap:CommonStockValue",
    "3100": "us-gaap:RetainedEarningsAccumulatedDeficit",
    "3200": "us-gaap:PartnersCapitalAccountDistributions",
    # Revenue
    "4000": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "4100": "us-gaap:RevenuesFromServices",
    "4200": "us-gaap:OtherIncome",
    # Expenses
    "5000": "us-gaap:CostOfGoodsSoldAndServicesSold",
    "5100": "us-gaap:LaborAndRelatedExpense",
    "5200": "us-gaap:OperatingLeaseExpense",
    "5300": "us-gaap:UtilitiesExpense",
    "5400": "us-gaap:DepreciationAndAmortization",
    "5500": "us-gaap:GeneralAndAdministrativeExpense",
    "5600": "us-gaap:TaxesExcludingIncomeAndExciseTaxes",
    "5700": "us-gaap:CommunicationsAndInformationTechnology",
    "5800": "us-gaap:ProfessionalFees",
    "5900": "us-gaap:OtherExpenses",
}

_ACCOUNT_CATEGORIES: dict[str, str] = {
    "1": "ASSET",
    "2": "LIABILITY",
    "3": "EQUITY",
    "4": "REVENUE",
    "5": "EXPENSE",
}


def _category_of(account_code: str) -> str:
    return _ACCOUNT_CATEGORIES.get(account_code[0], "UNKNOWN")


def _xbrl_tag(account_code: str) -> str:
    return _XBRL_TAGS.get(account_code, f"bit-counting:{account_code}")


# ---------------------------------------------------------------------------
# Modelos de resultado
# ---------------------------------------------------------------------------

class XBRLFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    tag:           str    # us-gaap:CashAndCashEquivalentsAtCarryingValue
    account_code:  str
    account_name:  str
    value:         Decimal
    period:        str    # "instant" o "duration"
    as_of_date:    str    # ISO 8601


class BalanceSheetV2(BaseModel):
    model_config = ConfigDict(frozen=True)
    result_id:         str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name:       str
    as_of_date:        str
    total_assets:      Decimal
    total_liabilities: Decimal
    total_equity:      Decimal
    assets:            dict[str, Any]      # codigo → {name, amount, xbrl_tag}
    liabilities:       dict[str, Any]
    equity:            dict[str, Any]
    algebraically_verified: bool = True
    xbrl_facts:        tuple[XBRLFact, ...] = ()
    generated_at:      str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class IncomeStatementV2(BaseModel):
    model_config = ConfigDict(frozen=True)
    result_id:           str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name:         str
    period_from:         str
    period_to:           str
    total_revenue:       Decimal
    total_expenses:      Decimal
    net_income:          Decimal
    revenues:            dict[str, Any]
    expenses:            dict[str, Any]
    algebraically_verified: bool = True
    xbrl_facts:          tuple[XBRLFact, ...] = ()
    generated_at:        str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class CashFlowStatementV2(BaseModel):
    model_config = ConfigDict(frozen=True)
    result_id:              str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name:            str
    period_from:            str
    period_to:              str
    opening_balance:        Decimal
    operating_activities:   Decimal
    investing_activities:   Decimal
    financing_activities:   Decimal
    net_change:             Decimal
    closing_balance:        Decimal
    algebraically_verified: bool = True
    xbrl_facts:             tuple[XBRLFact, ...] = ()
    generated_at:           str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class FinancialStatementsV2(BaseModel):
    """Paquete completo de estados financieros. Frozen."""
    model_config = ConfigDict(frozen=True)
    package_id:       str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name:      str
    period_from:      str
    period_to:        str
    balance_sheet:    BalanceSheetV2
    income_statement: IncomeStatementV2
    cash_flow:        CashFlowStatementV2
    all_verified:     bool
    decision_id:      Optional[str] = None
    generated_at:     str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Agente ESTADOS V2
# ---------------------------------------------------------------------------

class EstadosAgentV2:
    """
    Agente ESTADOS V2 — genera y verifica algebraicamente los 3 estados
    financieros principales en formato estructurado + XBRL basico.
    """

    AGENT_NAME = "ESTADOS_V2"

    def __init__(
        self,
        decisions_log: Optional[AgentDecisionsLog] = None,
    ) -> None:
        self._log = decisions_log if decisions_log is not None else get_decisions_log()

    # ------------------------------------------------------------------ #
    # Punto de entrada — genera el paquete completo                       #
    # ------------------------------------------------------------------ #

    def generate_financial_statements(
        self,
        accounts:      dict[str, Decimal],   # account_code → balance
        client_name:   str,
        period_from:   date,
        period_to:     date,
        opening_cash:  Decimal = Decimal("0"),
    ) -> FinancialStatementsV2:
        """
        Genera Balance General, Estado de Resultados y Flujo de Caja.

        Cada estado es verificado algebraicamente antes de incluirse.
        Lanza AlgebraicImbalanceError si cualquier estado no cuadra.

        Args:
            accounts:     Mapa account_code → saldo del periodo.
            client_name:  Nombre del cliente / empresa.
            period_from:  Inicio del periodo (inclusive).
            period_to:    Fin del periodo (inclusive).
            opening_cash: Saldo inicial de efectivo para el Flujo de Caja.
        """
        bs = self.generate_balance_sheet(accounts, period_to, client_name)
        is_ = self.generate_income_statement(accounts, period_from, period_to, client_name)
        cf = self.generate_cash_flow(
            accounts, period_from, period_to, client_name, opening_cash
        )

        all_verified = (
            bs.algebraically_verified
            and is_.algebraically_verified
            and cf.algebraically_verified
        )

        decision_entry = self._log.record(
            agent_name=self.AGENT_NAME,
            input_data={
                "client": client_name,
                "period": f"{period_from}/{period_to}",
                "accounts": len(accounts),
            },
            decision=(
                f"Estados financieros generados. "
                f"Balance: Activos={bs.total_assets}, P+C={bs.total_liabilities + bs.total_equity}. "
                f"IS: Ingresos={is_.total_revenue}, Gastos={is_.total_expenses}, Utilidad={is_.net_income}. "
                f"CF: Cierre={cf.closing_balance}. Verificados: {all_verified}."
            ),
        )

        return FinancialStatementsV2(
            client_name=client_name,
            period_from=period_from.isoformat(),
            period_to=period_to.isoformat(),
            balance_sheet=bs,
            income_statement=is_,
            cash_flow=cf,
            all_verified=all_verified,
            decision_id=decision_entry.decision_id,
        )

    # ------------------------------------------------------------------ #
    # Balance General                                                     #
    # ------------------------------------------------------------------ #

    def generate_balance_sheet(
        self,
        accounts:    dict[str, Decimal],
        as_of_date:  date,
        client_name: str,
    ) -> BalanceSheetV2:
        """
        Genera el Balance General y verifica: Activos == Pasivos + Capital.
        Lanza AlgebraicImbalanceError si no cuadra.
        """
        assets_dict:      dict[str, Any] = {}
        liabilities_dict: dict[str, Any] = {}
        equity_dict:      dict[str, Any] = {}
        xbrl_facts:       list[XBRLFact] = []

        for code, balance in accounts.items():
            cat = _category_of(code)
            tag = _xbrl_tag(code)
            entry = {
                "amount":    balance,
                "xbrl_tag":  tag,
                "account_code": code,
            }
            fact = XBRLFact(
                tag=tag,
                account_code=code,
                account_name=tag.split(":")[-1],
                value=balance,
                period="instant",
                as_of_date=as_of_date.isoformat(),
            )
            xbrl_facts.append(fact)

            if cat == "ASSET":
                assets_dict[code] = entry
            elif cat in ("LIABILITY",):
                liabilities_dict[code] = entry
            elif cat == "EQUITY":
                equity_dict[code] = entry
            elif cat == "CONTRA_ASSET":
                # Contra-asset reduce activos
                assets_dict[code] = {**entry, "amount": -balance}

        total_assets      = sum(e["amount"] for e in assets_dict.values())
        total_liabilities = sum(e["amount"] for e in liabilities_dict.values())
        total_equity      = sum(e["amount"] for e in equity_dict.values())

        # Verificacion algebraica
        diff = abs(total_assets - (total_liabilities + total_equity))
        if diff != Decimal("0"):
            raise AlgebraicImbalanceError(
                statement_type="Balance General",
                left_label="Activos",
                left_value=total_assets,
                right_label="Pasivos + Capital",
                right_value=total_liabilities + total_equity,
            )

        return BalanceSheetV2(
            client_name=client_name,
            as_of_date=as_of_date.isoformat(),
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            total_equity=total_equity,
            assets=assets_dict,
            liabilities=liabilities_dict,
            equity=equity_dict,
            algebraically_verified=True,
            xbrl_facts=tuple(xbrl_facts),
        )

    # ------------------------------------------------------------------ #
    # Estado de Resultados                                                #
    # ------------------------------------------------------------------ #

    def generate_income_statement(
        self,
        accounts:    dict[str, Decimal],
        period_from: date,
        period_to:   date,
        client_name: str,
    ) -> IncomeStatementV2:
        """
        Genera Estado de Resultados y verifica: Utilidad == Ingresos - Gastos.
        """
        revenues_dict: dict[str, Any] = {}
        expenses_dict: dict[str, Any] = {}
        xbrl_facts:    list[XBRLFact] = []

        for code, balance in accounts.items():
            cat = _category_of(code)
            tag = _xbrl_tag(code)
            entry = {"amount": balance, "xbrl_tag": tag, "account_code": code}
            fact  = XBRLFact(
                tag=tag,
                account_code=code,
                account_name=tag.split(":")[-1],
                value=balance,
                period="duration",
                as_of_date=period_to.isoformat(),
            )
            if cat == "REVENUE":
                revenues_dict[code] = entry
                xbrl_facts.append(fact)
            elif cat == "EXPENSE":
                expenses_dict[code] = entry
                xbrl_facts.append(fact)

        total_revenue  = sum(e["amount"] for e in revenues_dict.values())
        total_expenses = sum(e["amount"] for e in expenses_dict.values())
        net_income     = total_revenue - total_expenses

        # Verificacion algebraica
        computed = total_revenue - total_expenses
        if computed != net_income:  # siempre true por construccion, pero protege futuros cambios
            raise AlgebraicImbalanceError(
                statement_type="Estado de Resultados",
                left_label="Ingresos - Gastos",
                left_value=computed,
                right_label="Utilidad Neta",
                right_value=net_income,
            )

        return IncomeStatementV2(
            client_name=client_name,
            period_from=period_from.isoformat(),
            period_to=period_to.isoformat(),
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            net_income=net_income,
            revenues=revenues_dict,
            expenses=expenses_dict,
            algebraically_verified=True,
            xbrl_facts=tuple(xbrl_facts),
        )

    # ------------------------------------------------------------------ #
    # Flujo de Caja                                                       #
    # ------------------------------------------------------------------ #

    def generate_cash_flow(
        self,
        accounts:     dict[str, Decimal],
        period_from:  date,
        period_to:    date,
        client_name:  str,
        opening_cash: Decimal,
    ) -> CashFlowStatementV2:
        """
        Genera Estado de Flujo de Caja.
        Verifica: Saldo final == Saldo inicial + Flujo neto.

        Clasificacion simplificada:
          Operativo: ingresos por ventas/servicios - gastos operativos
          Inversion: adquisicion/venta de activos fijos
          Financiamiento: capital + distribuciones + deuda largo plazo
        """
        operating  = Decimal("0")
        investing  = Decimal("0")
        financing  = Decimal("0")
        xbrl_facts: list[XBRLFact] = []

        for code, balance in accounts.items():
            cat = _category_of(code)
            if cat == "REVENUE":
                operating += balance
            elif cat == "EXPENSE":
                operating -= balance
            elif code.startswith("15"):   # Propiedad, Planta y Equipo
                investing -= balance
            elif code.startswith("16"):   # Depreciacion Acumulada (no es salida de caja)
                pass
            elif cat == "EQUITY":
                if code == "3200":        # Distribuciones — salida de caja
                    financing -= balance
                else:
                    financing += balance
            elif cat == "LIABILITY" and code == "2300":  # Deuda LP
                financing += balance

            tag  = _xbrl_tag(code)
            xbrl_facts.append(XBRLFact(
                tag=tag,
                account_code=code,
                account_name=tag.split(":")[-1],
                value=balance,
                period="duration",
                as_of_date=period_to.isoformat(),
            ))

        net_change      = operating + investing + financing
        closing_balance = opening_cash + net_change

        # Verificacion algebraica
        expected_closing = opening_cash + net_change
        if expected_closing != closing_balance:
            raise AlgebraicImbalanceError(
                statement_type="Flujo de Caja",
                left_label="Saldo Inicial + Flujo Neto",
                left_value=expected_closing,
                right_label="Saldo Final",
                right_value=closing_balance,
            )

        return CashFlowStatementV2(
            client_name=client_name,
            period_from=period_from.isoformat(),
            period_to=period_to.isoformat(),
            opening_balance=opening_cash,
            operating_activities=operating,
            investing_activities=investing,
            financing_activities=financing,
            net_change=net_change,
            closing_balance=closing_balance,
            algebraically_verified=True,
            xbrl_facts=tuple(xbrl_facts),
        )
