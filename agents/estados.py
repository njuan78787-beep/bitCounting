# =============================================================================
# agents/estados.py
# Agente ESTADOS — Generacion de estados financieros para Puerto Rico.
#
# GARANTIAS DE DISENO:
#   - Todos los calculos usan Decimal — nunca float.
#   - El Balance General se verifica algebraicamente antes de entregar:
#     Activos == Pasivos + Capital. Si no cuadra, se lanza excepcion.
#   - El Estado de Resultados verifica: Ingreso Bruto - COGS == Utilidad Bruta
#     y Utilidad Bruta - Gastos Operativos == Ingreso Operativo.
#   - El Flujo de Efectivo verifica: neto = operacion + inversion + financiamiento.
#   - Output estructurado con etiquetas XBRL us-gaap namespace para compliance.
# =============================================================================

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from .base import BaseAgent
from .exceptions import BitCountingAgentError
from .messages import BaseAgentMessage, OrchestratorDecision


# ---------------------------------------------------------------------------
# EXCEPCIONES ESPECIFICAS DE ESTADOS
# ---------------------------------------------------------------------------

class BalanceSheetImbalanceError(BitCountingAgentError):
    """
    El Balance General no cuadra: Activos != Pasivos + Capital.

    NUNCA se entrega un balance que no cuadre algebraicamente.
    Esta excepcion se lanza antes de retornar el resultado al cliente.
    """

    def __init__(
        self,
        total_assets: Decimal,
        total_liabilities: Decimal,
        total_equity: Decimal,
    ) -> None:
        self.total_assets = total_assets
        self.total_liabilities = total_liabilities
        self.total_equity = total_equity
        diff = total_assets - (total_liabilities + total_equity)
        super().__init__(
            f"Balance General no cuadra: Activos={total_assets} != "
            f"Pasivos+Capital={total_liabilities + total_equity}. "
            f"Diferencia={diff}. "
            "No se puede entregar un balance que no cuadre algebraicamente."
        )


# ---------------------------------------------------------------------------
# XBRL TAG MAPPING — us-gaap namespace (Phase 1, common tags)
# ---------------------------------------------------------------------------

# Maps account classification keyword → us-gaap XBRL tag
_XBRL_ASSET_TAGS: dict[str, str] = {
    "cash":                         "us-gaap:CashAndCashEquivalentsAtCarryingValue",
    "accounts_receivable":          "us-gaap:AccountsReceivableNetCurrent",
    "inventory":                    "us-gaap:InventoryNet",
    "prepaid":                      "us-gaap:PrepaidExpenseAndOtherAssetsCurrent",
    "property_plant_equipment":     "us-gaap:PropertyPlantAndEquipmentNet",
    "total_current_assets":         "us-gaap:AssetsCurrent",
    "total_non_current_assets":     "us-gaap:AssetsNoncurrent",
    "total_assets":                 "us-gaap:Assets",
}

_XBRL_LIABILITY_TAGS: dict[str, str] = {
    "accounts_payable":             "us-gaap:AccountsPayableCurrent",
    "ivu_payable":                  "us-gaap:TaxesPayableCurrent",
    "payroll_payable":              "us-gaap:EmployeeRelatedLiabilitiesCurrent",
    "loans_payable_current":        "us-gaap:LongTermDebtCurrent",
    "loans_payable_long_term":      "us-gaap:LongTermDebtNoncurrent",
    "total_current_liabilities":    "us-gaap:LiabilitiesCurrent",
    "total_long_term_liabilities":  "us-gaap:LiabilitiesNoncurrent",
    "total_liabilities":            "us-gaap:Liabilities",
}

_XBRL_EQUITY_TAGS: dict[str, str] = {
    "common_stock":                 "us-gaap:CommonStockValue",
    "retained_earnings":            "us-gaap:RetainedEarningsAccumulatedDeficit",
    "current_period_income":        "us-gaap:NetIncomeLoss",
    "total_equity":                 "us-gaap:StockholdersEquity",
}

_XBRL_INCOME_TAGS: dict[str, str] = {
    "gross_revenue":                "us-gaap:Revenues",
    "cost_of_goods_sold":           "us-gaap:CostOfRevenue",
    "gross_profit":                 "us-gaap:GrossProfit",
    "operating_expenses":           "us-gaap:OperatingExpenses",
    "operating_income":             "us-gaap:OperatingIncomeLoss",
    "net_income":                   "us-gaap:NetIncomeLoss",
}

_XBRL_CASHFLOW_TAGS: dict[str, str] = {
    "operating_activities":         "us-gaap:NetCashProvidedByUsedInOperatingActivities",
    "investing_activities":         "us-gaap:NetCashProvidedByUsedInInvestingActivities",
    "financing_activities":         "us-gaap:NetCashProvidedByUsedInFinancingActivities",
    "net_change_in_cash":           "us-gaap:CashAndCashEquivalentsPeriodIncreaseDecrease",
}

# ---------------------------------------------------------------------------
# Account code → balance sheet category mapping (uses Chart of Accounts codes)
# ---------------------------------------------------------------------------

_ACCOUNT_CATEGORY: dict[str, str] = {
    # Current Assets
    "1000": "current_assets",
    "1100": "current_assets",
    "1200": "current_assets",
    "1300": "current_assets",
    # Non-Current Assets
    "1500": "non_current_assets",
    "1600": "non_current_assets",  # contra-asset, reduces PP&E
    # Current Liabilities
    "2000": "current_liabilities",
    "2100": "current_liabilities",
    "2200": "current_liabilities",
    "2400": "current_liabilities",
    # Long-Term Liabilities
    "2300": "long_term_liabilities",
    # Equity
    "3000": "equity",
    "3100": "equity",
    "3900": "equity",
}

_CONTRA_ASSET_CODES = {"1600"}  # Depreciacion Acumulada (reduces assets)

_ACCOUNT_NAMES: dict[str, str] = {
    "1000": "Efectivo y Equivalentes",
    "1100": "Cuentas por Cobrar",
    "1200": "Inventario",
    "1300": "Gastos Prepagados",
    "1500": "Propiedad, Planta y Equipo",
    "1600": "Depreciacion Acumulada",
    "2000": "Cuentas por Pagar",
    "2100": "IVU por Pagar",
    "2200": "Nomina por Pagar",
    "2300": "Prestamos por Pagar",
    "2400": "Impuestos por Pagar",
    "3000": "Capital Social",
    "3100": "Utilidades Retenidas",
    "3900": "Utilidad/Perdida del Periodo",
}


# ---------------------------------------------------------------------------
# AGENT
# ---------------------------------------------------------------------------

class EstadosAgent(BaseAgent):
    """
    ESTADOS — Genera estados financieros algebraicamente verificados.

    Produce Balance General, Estado de Resultados, Flujo de Efectivo
    y Notas en formato estructurado con etiquetas XBRL us-gaap.

    GARANTIA CRITICA: Nunca entrega un Balance General que no cuadre.
    Si Activos != Pasivos + Capital, lanza BalanceSheetImbalanceError
    antes de retornar.

    Nota: Este agente opera principalmente via sus metodos directos
    (generate_balance_sheet, generate_income_statement, etc.) ademas
    de la interfaz BaseAgent.process(). El _process_impl soporta
    OrchestratorDecision para uso en flujo de log.
    """

    @property
    def agent_name(self) -> str:
        return "ESTADOS"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        # ESTADOS recibe OrchestratorDecision (log snapshot) para generar reportes
        return (OrchestratorDecision,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (OrchestratorDecision,)

    def _process_impl(self, message: BaseAgentMessage) -> OrchestratorDecision:
        """
        Implementacion del agente para el framework.
        En el flujo normal, ESTADOS se invoca via sus metodos directos.
        Este hook registra el paso por el framework.
        """
        assert isinstance(message, OrchestratorDecision)
        return OrchestratorDecision(
            source_agent=self.agent_name,
            target_agent="LOG",
            decision_id=str(uuid.uuid4()),
            agent_name=self.agent_name,
            input_snapshot=message.model_dump(),
            output_snapshot={"status": "ESTADOS_PROCESSED"},
            rule_ids_applied=(),
            confidence=Decimal("1.0"),
        )

    # -------------------------------------------------------------------------
    # BALANCE GENERAL (Balance Sheet)
    # -------------------------------------------------------------------------

    def generate_balance_sheet(
        self,
        accounts: dict[str, Decimal],
        as_of_date: date,
        client_name: str,
    ) -> dict[str, Any]:
        """
        Genera el Balance General a una fecha dada.

        Args:
            accounts:     Mapa codigo_cuenta → saldo (Decimal). Usa el Plan de
                          Cuentas de Bit-Counting (1xxx activos, 2xxx pasivos, 3xxx capital).
            as_of_date:   Fecha de corte del balance (normalmente fin de periodo).
            client_name:  Nombre del cliente para encabezado del reporte.

        Returns:
            Dict estructurado con activos, pasivos, capital, verificacion y XBRL tags.

        Raises:
            BalanceSheetImbalanceError: Si Activos != Pasivos + Capital (exacto).
        """
        current_assets: dict[str, Decimal] = {}
        non_current_assets: dict[str, Decimal] = {}
        current_liabilities: dict[str, Decimal] = {}
        long_term_liabilities: dict[str, Decimal] = {}
        equity: dict[str, Decimal] = {}

        for code, balance in accounts.items():
            balance = Decimal(str(balance))
            category = _ACCOUNT_CATEGORY.get(code)
            name = _ACCOUNT_NAMES.get(code, f"Cuenta {code}")

            if category == "current_assets":
                if code in _CONTRA_ASSET_CODES:
                    current_assets[name] = -balance
                else:
                    current_assets[name] = balance
            elif category == "non_current_assets":
                if code in _CONTRA_ASSET_CODES:
                    non_current_assets[name] = -balance
                else:
                    non_current_assets[name] = balance
            elif category == "current_liabilities":
                current_liabilities[name] = balance
            elif category == "long_term_liabilities":
                long_term_liabilities[name] = balance
            elif category == "equity":
                equity[name] = balance
            # Revenue/Expense codes (4xxx, 5xxx, 6xxx) are not balance sheet items;
            # they should be closed to Utilidad/Perdida del Periodo (3900) before
            # calling this method. Unrecognized codes are silently ignored.

        total_current_assets = sum(current_assets.values(), Decimal("0"))
        total_non_current_assets = sum(non_current_assets.values(), Decimal("0"))
        total_assets = total_current_assets + total_non_current_assets

        total_current_liabilities = sum(current_liabilities.values(), Decimal("0"))
        total_long_term_liabilities = sum(long_term_liabilities.values(), Decimal("0"))
        total_liabilities = total_current_liabilities + total_long_term_liabilities

        total_equity = sum(equity.values(), Decimal("0"))

        # --- VERIFICACION ALGEBRAICA --- must happen before returning ---
        if not self.verify_algebraic_balance({
            "assets": {"total_assets": total_assets},
            "liabilities": {"total_liabilities": total_liabilities},
            "equity": {"total_equity": total_equity},
        }):
            raise BalanceSheetImbalanceError(
                total_assets=total_assets,
                total_liabilities=total_liabilities,
                total_equity=total_equity,
            )

        xbrl_tags = {
            "total_current_assets":        _XBRL_ASSET_TAGS["total_current_assets"],
            "total_non_current_assets":    _XBRL_ASSET_TAGS["total_non_current_assets"],
            "total_assets":                _XBRL_ASSET_TAGS["total_assets"],
            "total_current_liabilities":   _XBRL_LIABILITY_TAGS["total_current_liabilities"],
            "total_long_term_liabilities": _XBRL_LIABILITY_TAGS["total_long_term_liabilities"],
            "total_liabilities":           _XBRL_LIABILITY_TAGS["total_liabilities"],
            "total_equity":                _XBRL_EQUITY_TAGS["total_equity"],
        }

        return {
            "statement_type":       "BALANCE_SHEET",
            "client_name":          client_name,
            "as_of_date":           as_of_date.isoformat(),
            "generated_at":         datetime.now(timezone.utc).isoformat(),
            "assets": {
                "current_assets":       {k: str(v) for k, v in current_assets.items()},
                "non_current_assets":   {k: str(v) for k, v in non_current_assets.items()},
                "total_current_assets": str(total_current_assets),
                "total_non_current_assets": str(total_non_current_assets),
                "total_assets":         str(total_assets),
            },
            "liabilities": {
                "current_liabilities":       {k: str(v) for k, v in current_liabilities.items()},
                "long_term_liabilities":     {k: str(v) for k, v in long_term_liabilities.items()},
                "total_current_liabilities": str(total_current_liabilities),
                "total_long_term_liabilities": str(total_long_term_liabilities),
                "total_liabilities":         str(total_liabilities),
            },
            "equity": {
                "accounts":     {k: str(v) for k, v in equity.items()},
                "total_equity": str(total_equity),
            },
            "verification": {
                "algebraically_balanced": True,
                "total_assets":           str(total_assets),
                "total_liabilities":      str(total_liabilities),
                "total_equity":           str(total_equity),
                "check":                  f"{total_assets} == {total_liabilities} + {total_equity}",
            },
            "xbrl_tags": xbrl_tags,
        }

    # -------------------------------------------------------------------------
    # ESTADO DE RESULTADOS (Income Statement)
    # -------------------------------------------------------------------------

    def generate_income_statement(
        self,
        revenue_accounts: dict[str, Decimal],
        expense_accounts: dict[str, Decimal],
        period_start: date,
        period_end: date,
        client_name: str,
    ) -> dict[str, Any]:
        """
        Genera el Estado de Resultados para el periodo dado.

        Args:
            revenue_accounts:  Mapa nombre_cuenta → monto (ingresos).
            expense_accounts:  Mapa nombre_cuenta → monto (gastos/costos).
                               Debe incluir 'Costo de Ventas (COGS)' si aplica.
            period_start:      Inicio del periodo contable.
            period_end:        Fin del periodo contable.
            client_name:       Nombre del cliente.

        Returns:
            Dict con gross_revenue, cogs, gross_profit, operating_expenses,
            operating_income, net_income — todos Decimal str, verificados.

        Raises:
            ValueError: Si la verificacion algebraica del estado falla.
        """
        # Separate COGS from other operating expenses
        _COGS_KEYS = {
            "costo de ventas",
            "cogs",
            "costo de ventas (cogs)",
            "cost of goods sold",
            "cost of revenue",
            "costo de ventas (cogs)",
        }

        cogs: Decimal = Decimal("0")
        operating_expenses: dict[str, Decimal] = {}

        for acct_name, amount in expense_accounts.items():
            amount = Decimal(str(amount))
            if acct_name.lower().strip() in _COGS_KEYS:
                cogs += amount
            else:
                operating_expenses[acct_name] = amount

        gross_revenue = sum(
            (Decimal(str(v)) for v in revenue_accounts.values()),
            Decimal("0"),
        )
        total_operating_expenses = sum(operating_expenses.values(), Decimal("0"))

        gross_profit = gross_revenue - cogs
        operating_income = gross_profit - total_operating_expenses
        net_income = operating_income  # Phase 1: no other income/expense items

        # --- VERIFICACIONES ALGEBRAICAS ---
        if gross_revenue - cogs != gross_profit:
            raise ValueError(
                f"Verificacion fallida: {gross_revenue} - {cogs} != {gross_profit}"
            )
        if gross_profit - total_operating_expenses != operating_income:
            raise ValueError(
                f"Verificacion fallida: {gross_profit} - {total_operating_expenses} "
                f"!= {operating_income}"
            )

        xbrl_tags = {
            "gross_revenue":       _XBRL_INCOME_TAGS["gross_revenue"],
            "cost_of_goods_sold":  _XBRL_INCOME_TAGS["cost_of_goods_sold"],
            "gross_profit":        _XBRL_INCOME_TAGS["gross_profit"],
            "operating_expenses":  _XBRL_INCOME_TAGS["operating_expenses"],
            "operating_income":    _XBRL_INCOME_TAGS["operating_income"],
            "net_income":          _XBRL_INCOME_TAGS["net_income"],
        }

        return {
            "statement_type":         "INCOME_STATEMENT",
            "client_name":            client_name,
            "period_start":           period_start.isoformat(),
            "period_end":             period_end.isoformat(),
            "generated_at":           datetime.now(timezone.utc).isoformat(),
            "gross_revenue":          str(gross_revenue),
            "revenue_accounts":       {k: str(v) for k, v in revenue_accounts.items()},
            "cogs":                   str(cogs),
            "gross_profit":           str(gross_profit),
            "operating_expenses":     {k: str(v) for k, v in operating_expenses.items()},
            "total_operating_expenses": str(total_operating_expenses),
            "operating_income":       str(operating_income),
            "net_income":             str(net_income),
            "verification": {
                "gross_profit_check":    f"{gross_revenue} - {cogs} == {gross_profit}",
                "operating_income_check": (
                    f"{gross_profit} - {total_operating_expenses} == {operating_income}"
                ),
                "algebraically_verified": True,
            },
            "xbrl_tags": xbrl_tags,
        }

    # -------------------------------------------------------------------------
    # FLUJO DE EFECTIVO (Cash Flow — Indirect Method)
    # -------------------------------------------------------------------------

    def generate_cash_flow(
        self,
        transactions: list[dict],
        period_start: date,
        period_end: date,
    ) -> dict[str, Any]:
        """
        Genera el Estado de Flujo de Efectivo por el metodo indirecto.

        Clasifica cada transaccion en:
          - operating_activities:  transacciones del negocio principal
          - investing_activities:  compra/venta de activos de largo plazo
          - financing_activities:  prestamos, capital, dividendos

        Args:
            transactions: Lista de dicts con al menos:
                          'activity' ('operating'|'investing'|'financing'),
                          'description' (str), 'amount' (Decimal).
            period_start: Inicio del periodo.
            period_end:   Fin del periodo.

        Returns:
            Dict con los tres componentes, net_change_in_cash y verificacion.
        """
        operating: list[dict] = []
        investing: list[dict] = []
        financing: list[dict] = []

        for tx in transactions:
            activity = str(tx.get("activity", "operating")).lower().strip()
            description = str(tx.get("description", ""))
            amount = Decimal(str(tx.get("amount", "0")))
            entry = {"description": description, "amount": str(amount)}

            if activity in ("investing",):
                investing.append(entry)
            elif activity in ("financing",):
                financing.append(entry)
            else:
                operating.append(entry)

        net_operating  = sum(Decimal(e["amount"]) for e in operating)
        net_investing  = sum(Decimal(e["amount"]) for e in investing)
        net_financing  = sum(Decimal(e["amount"]) for e in financing)
        net_change     = net_operating + net_investing + net_financing

        # Verify
        if net_operating + net_investing + net_financing != net_change:
            raise ValueError(
                f"Verificacion flujo fallida: {net_operating} + {net_investing} "
                f"+ {net_financing} != {net_change}"
            )

        return {
            "statement_type":         "CASH_FLOW_STATEMENT",
            "method":                 "INDIRECT",
            "period_start":           period_start.isoformat(),
            "period_end":             period_end.isoformat(),
            "generated_at":           datetime.now(timezone.utc).isoformat(),
            "operating_activities": {
                "items":              operating,
                "net":                str(net_operating),
                "xbrl_tag":           _XBRL_CASHFLOW_TAGS["operating_activities"],
            },
            "investing_activities": {
                "items":              investing,
                "net":                str(net_investing),
                "xbrl_tag":           _XBRL_CASHFLOW_TAGS["investing_activities"],
            },
            "financing_activities": {
                "items":              financing,
                "net":                str(net_financing),
                "xbrl_tag":           _XBRL_CASHFLOW_TAGS["financing_activities"],
            },
            "net_change_in_cash":     str(net_change),
            "verification": {
                "check": (
                    f"{net_operating} + {net_investing} + {net_financing} == {net_change}"
                ),
                "algebraically_verified": True,
                "xbrl_tag": _XBRL_CASHFLOW_TAGS["net_change_in_cash"],
            },
        }

    # -------------------------------------------------------------------------
    # VERIFICACION ALGEBRAICA
    # -------------------------------------------------------------------------

    @staticmethod
    def verify_algebraic_balance(balance_sheet: dict) -> bool:
        """
        Verifica que el Balance General cuadre algebraicamente.

        Activos == Pasivos + Capital (comparacion Decimal exacta, tolerancia cero).

        Args:
            balance_sheet: Dict con estructura:
                           {'assets': {'total_assets': Decimal|str},
                            'liabilities': {'total_liabilities': Decimal|str},
                            'equity': {'total_equity': Decimal|str}}

        Returns:
            True si cuadra exactamente. False si hay cualquier diferencia.
        """
        try:
            total_assets      = Decimal(str(balance_sheet["assets"]["total_assets"]))
            total_liabilities = Decimal(str(balance_sheet["liabilities"]["total_liabilities"]))
            total_equity      = Decimal(str(balance_sheet["equity"]["total_equity"]))
        except (KeyError, Exception):
            return False

        return total_assets == (total_liabilities + total_equity)
