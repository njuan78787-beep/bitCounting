# =============================================================================
# agents/clasificador.py
# Agente CLASIFICADOR — asigna codigos contables segun el Plan de Cuentas.
#
# GARANTIAS DE DISENO:
#   - Solo opera sobre el catalogo fijo de cuentas (Phase 1).
#   - NUNCA crea cuentas nuevas — lanza AgentScopeError si no puede clasificar.
#   - Partida doble siempre balanceada: debito == credito (Decimal exacto).
#   - Confianza < 0.65 → flag para revision CPA (no excepcion).
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .base import BaseAgent
from .exceptions import AgentScopeError
from .messages import ClassifierOutput, EntryType, IntakeOutput

# ---------------------------------------------------------------------------
# PLAN DE CUENTAS — Puerto Rico GAAP (Phase 1, hardcoded)
# ---------------------------------------------------------------------------

_CHART_OF_ACCOUNTS: dict[str, dict] = {
    # ASSETS (1xxx)
    "1000": {"code": "1000", "name": "Efectivo y Equivalentes",        "category": "ASSET",     "normal": "debit"},
    "1100": {"code": "1100", "name": "Cuentas por Cobrar",             "category": "ASSET",     "normal": "debit"},
    "1200": {"code": "1200", "name": "Inventario",                     "category": "ASSET",     "normal": "debit"},
    "1300": {"code": "1300", "name": "Gastos Prepagados",              "category": "ASSET",     "normal": "debit"},
    "1500": {"code": "1500", "name": "Propiedad, Planta y Equipo",     "category": "ASSET",     "normal": "debit"},
    "1600": {"code": "1600", "name": "Depreciacion Acumulada",         "category": "CONTRA_ASSET", "normal": "credit"},
    # LIABILITIES (2xxx)
    "2000": {"code": "2000", "name": "Cuentas por Pagar",             "category": "LIABILITY", "normal": "credit"},
    "2100": {"code": "2100", "name": "IVU por Pagar",                 "category": "LIABILITY", "normal": "credit"},
    "2200": {"code": "2200", "name": "Nomina por Pagar",              "category": "LIABILITY", "normal": "credit"},
    "2300": {"code": "2300", "name": "Prestamos por Pagar",           "category": "LIABILITY", "normal": "credit"},
    "2400": {"code": "2400", "name": "Impuestos por Pagar",           "category": "LIABILITY", "normal": "credit"},
    # EQUITY (3xxx)
    "3000": {"code": "3000", "name": "Capital Social",                "category": "EQUITY",    "normal": "credit"},
    "3100": {"code": "3100", "name": "Utilidades Retenidas",          "category": "EQUITY",    "normal": "credit"},
    "3900": {"code": "3900", "name": "Utilidad/Perdida del Periodo",  "category": "EQUITY",    "normal": "credit"},
    # REVENUE (4xxx)
    "4000": {"code": "4000", "name": "Ingresos por Ventas",           "category": "REVENUE",   "normal": "credit"},
    "4100": {"code": "4100", "name": "Ingresos por Servicios",        "category": "REVENUE",   "normal": "credit"},
    "4900": {"code": "4900", "name": "Otros Ingresos",                "category": "REVENUE",   "normal": "credit"},
    # EXPENSES (5xxx / 6xxx)
    "5000": {"code": "5000", "name": "Costo de Ventas (COGS)",        "category": "EXPENSE",   "normal": "debit"},
    "5100": {"code": "5100", "name": "Gastos de Nomina",              "category": "EXPENSE",   "normal": "debit"},
    "5200": {"code": "5200", "name": "Renta",                         "category": "EXPENSE",   "normal": "debit"},
    "5300": {"code": "5300", "name": "Utilidades (electricidad, agua, etc.)", "category": "EXPENSE", "normal": "debit"},
    "5400": {"code": "5400", "name": "Publicidad y Mercadeo",         "category": "EXPENSE",   "normal": "debit"},
    "5500": {"code": "5500", "name": "Seguros",                       "category": "EXPENSE",   "normal": "debit"},
    "5600": {"code": "5600", "name": "Depreciacion",                  "category": "EXPENSE",   "normal": "debit"},
    "5700": {"code": "5700", "name": "Gastos de Viaje",               "category": "EXPENSE",   "normal": "debit"},
    "5800": {"code": "5800", "name": "Honorarios Profesionales",      "category": "EXPENSE",   "normal": "debit"},
    "5900": {"code": "5900", "name": "Gastos Varios",                 "category": "EXPENSE",   "normal": "debit"},
    "6000": {"code": "6000", "name": "IVU Pagado (compras con IVU)",  "category": "EXPENSE",   "normal": "debit"},
}

# ---------------------------------------------------------------------------
# KEYWORD MAPS — Phase 1 classification rules
# ---------------------------------------------------------------------------

# Maps (debit_code, credit_code, confidence_bonus, rule_ref, keyword_list)
# Each tuple: (debit_account, credit_account, extra_confidence, rule_ref, [keywords])
# credit_account may be overridden by payment_method logic later.

_EXPENSE_RULES: list[tuple[str, str, Decimal, str, list[str]]] = [
    # Renta / alquiler
    ("5200", "2000", Decimal("0.10"), "GAAP-PR-5200-RENT",
     ["renta", "alquiler", "lease", "arrendamiento", "rent"]),
    # Nomina / payroll
    ("5100", "2200", Decimal("0.10"), "GAAP-PR-5100-PAYROLL",
     ["nomina", "payroll", "salario", "sueldo", "adp", "paychex", "dtrh", "wage"]),
    # Utilidades
    ("5300", "2000", Decimal("0.08"), "GAAP-PR-5300-UTILITIES",
     ["aee", "electricidad", "electric", "agua", "prasa", "utility", "utilities",
      "luma", "lumra", "luz", "internet", "claro", "liberty", "at&t", "sprint"]),
    # Publicidad
    ("5400", "2000", Decimal("0.08"), "GAAP-PR-5400-ADVERTISING",
     ["publicidad", "mercadeo", "marketing", "advertising", "meta", "facebook",
      "google ads", "instagram", "ads"]),
    # Seguros
    ("5500", "2000", Decimal("0.10"), "GAAP-PR-5500-INSURANCE",
     ["seguro", "insurance", "triple-s", "triple s", "humana", "aetna",
      "first medical", "mmm", "prima"]),
    # Depreciacion
    ("5600", "1600", Decimal("0.10"), "GAAP-PR-5600-DEPRECIATION",
     ["depreciacion", "depreciation", "amortizacion", "amortization"]),
    # Viaje
    ("5700", "2000", Decimal("0.07"), "GAAP-PR-5700-TRAVEL",
     ["viaje", "travel", "hotel", "aerolinea", "airline", "uber", "taxi",
      "lyft", "airbnb", "hospedaje"]),
    # Honorarios profesionales
    ("5800", "2000", Decimal("0.10"), "GAAP-PR-5800-PROFESSIONAL",
     ["honorario", "honorarios", "profesional", "consultor", "consulting",
      "abogado", "lawyer", "attorney", "contador", "accountant", "cpa",
      "arquitecto", "engineer", "ingeniero"]),
    # Impuestos / gobierno → Impuestos por Pagar / Efectivo
    ("2400", "1000", Decimal("0.12"), "GAAP-PR-2400-TAX-PAYMENT",
     ["hacienda", "irs", "dtrh", "impuesto", "tax payment", "contribucion",
      "internal revenue", "departamento hacienda", "futa", "suta", "planilla"]),
    # IVU pagado en compras
    ("6000", "1000", Decimal("0.10"), "PR-IVU-6000-PAID",
     ["ivu pagado", "sales tax paid", "ivu compra"]),
]

_REVENUE_RULES: list[tuple[str, str, Decimal, str, list[str]]] = [
    ("1000", "4000", Decimal("0.10"), "GAAP-PR-4000-SALES",
     ["venta", "sale", "ingreso venta", "revenue"]),
    ("1000", "4100", Decimal("0.10"), "GAAP-PR-4100-SERVICES",
     ["servicio", "service", "consultoria", "consulting revenue", "honorario cobrado"]),
    ("1000", "4900", Decimal("0.08"), "GAAP-PR-4900-OTHER-INCOME",
     ["otro ingreso", "other income", "miscelaneo", "miscellaneous income"]),
]

_ASSET_RULES: list[tuple[str, str, Decimal, str, list[str]]] = [
    # Compra de inventario
    ("1200", "2000", Decimal("0.10"), "GAAP-PR-1200-INVENTORY",
     ["inventario", "inventory", "mercancia", "merchandise", "producto",
      "compra inventario", "stock", "goods purchased"]),
    # Compra de activo fijo
    ("1500", "2000", Decimal("0.10"), "GAAP-PR-1500-FIXED-ASSET",
     ["equipo", "equipment", "maquinaria", "machinery", "mobiliario",
      "furniture", "computadora", "computer", "vehiculo", "vehicle",
      "compra activo", "fixed asset"]),
    # Gastos prepagados
    ("1300", "1000", Decimal("0.08"), "GAAP-PR-1300-PREPAID",
     ["prepago", "prepaid", "anticipo", "deposito garantia", "deposit"]),
    # IVU por pagar (collected from customers)
    ("4000", "2100", Decimal("0.12"), "PR-IVU-2100-COLLECTED",
     ["ivu cobrado", "ivu por pagar", "sales tax collected", "ivu clientes"]),
]

_ALL_RULES = _EXPENSE_RULES + _REVENUE_RULES + _ASSET_RULES

# ---------------------------------------------------------------------------
# Capitalization threshold (GAAP rule: items > $2,500 should be capitalized)
# ---------------------------------------------------------------------------
_CAPITALIZATION_THRESHOLD = Decimal("2500.00")
_FRAUD_HIGH_AMOUNT = Decimal("50000.00")


def get_chart_of_accounts() -> dict[str, dict]:
    """Returns a copy of the full Chart of Accounts catalog."""
    return dict(_CHART_OF_ACCOUNTS)


def get_account(code: str) -> dict:
    """
    Returns account details for the given code.

    Raises:
        KeyError: If the code does not exist in the Chart of Accounts.
    """
    if code not in _CHART_OF_ACCOUNTS:
        raise KeyError(
            f"Cuenta '{code}' no existe en el Plan de Cuentas de Bit-Counting. "
            "CLASIFICADOR solo puede usar cuentas del catalogo fijo."
        )
    return dict(_CHART_OF_ACCOUNTS[code])


# ---------------------------------------------------------------------------
# CLASIFICADOR AGENT
# ---------------------------------------------------------------------------

class ClasificadorAgent(BaseAgent):
    """
    Agente CLASIFICADOR: asigna codigos contables a cada transaccion.

    Opera exclusivamente sobre el catalogo fijo de cuentas (Phase 1).
    NUNCA crea categorias nuevas — lanza AgentScopeError si la transaccion
    no puede clasificarse dentro del catalogo existente.

    Genera partida doble balanceada: un asiento DEBIT y un asiento CREDIT
    por cada transaccion. La suma debito == suma credito siempre (Decimal exacto).
    """

    @property
    def agent_name(self) -> str:
        return "CLASIFICADOR"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        return (IntakeOutput,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (ClassifierOutput,)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self,
        intake_output: IntakeOutput,
        client_context: dict | None = None,
    ) -> tuple[ClassifierOutput, ClassifierOutput]:
        """
        Clasifica una transaccion y retorna (debit_entry, credit_entry).

        La partida doble es siempre balanceada: debit.amount == credit.amount.
        Si confianza < 0.65, el campo reasoning incluye un flag de revision CPA.
        Si la transaccion no puede clasificarse con el catalogo existente, lanza
        AgentScopeError.

        Args:
            intake_output: Output estructurado del agente INTAKE.
            client_context: Contexto opcional del cliente (historial, industria).

        Returns:
            Tuple (ClassifierOutput debit, ClassifierOutput credit).

        Raises:
            AgentScopeError: Si la transaccion no puede clasificarse con el
                             catalogo fijo de cuentas existente.
        """
        vendor = (intake_output.vendor or "").strip()
        amount = intake_output.amount or Decimal("0.00")
        vendor_lower = vendor.lower()

        # --- 1. Match keyword rules ---
        matched_rule = self._match_rule(vendor_lower, amount, client_context)

        if matched_rule is None:
            raise AgentScopeError(
                agent_name=self.agent_name,
                message_type="IntakeOutput",
                allowed_types=tuple(_CHART_OF_ACCOUNTS.keys()),
            )

        debit_code, credit_code, confidence_bonus, rule_ref = matched_rule

        # --- 2. Override credit account based on payment_method ---
        credit_code = self._resolve_credit_by_payment(
            credit_code, intake_output.payment_method
        )

        # --- 3. Check capitalization threshold (GAAP) ---
        reasoning_notes: list[str] = []
        if amount > _CAPITALIZATION_THRESHOLD:
            reasoning_notes.append(
                f"NOTA: Monto ${amount} supera el umbral de capitalizacion GAAP de "
                f"${_CAPITALIZATION_THRESHOLD}. Se sugiere evaluar si debe activarse "
                "como Propiedad, Planta y Equipo (1500) en lugar de gastos."
            )

        # --- 4. Calculate confidence ---
        base_confidence = Decimal("0.75")
        confidence = min(Decimal("1.00"), base_confidence + confidence_bonus)

        # Penalize if vendor is empty
        if not vendor:
            confidence -= Decimal("0.10")
            reasoning_notes.append("ADVERTENCIA: Vendedor no identificado.")

        # Penalize if amount is zero or very low
        if amount <= Decimal("0.00"):
            confidence -= Decimal("0.10")
            reasoning_notes.append("ADVERTENCIA: Monto cero o negativo.")

        # Flag for CPA review if confidence is low
        cpa_flag = ""
        if confidence < Decimal("0.65"):
            cpa_flag = " [FLAG: Confianza baja — requiere revision CPA antes de registrar.]"

        confidence = max(Decimal("0.00"), confidence)

        # --- 5. Build reasoning string ---
        debit_acct = _CHART_OF_ACCOUNTS[debit_code]
        credit_acct = _CHART_OF_ACCOUNTS[credit_code]

        reasoning_base = (
            f"Transaccion clasificada mediante coincidencia de palabras clave para "
            f"vendedor '{vendor or '(sin vendedor)'}'. "
            f"Regla aplicada: {rule_ref}. "
            f"Asiento DEBITO: {debit_code} ({debit_acct['name']}), "
            f"Asiento CREDITO: {credit_code} ({credit_acct['name']}). "
            f"Monto: ${amount}."
        )
        if reasoning_notes:
            reasoning_base += " " + " ".join(reasoning_notes)
        reasoning_base += cpa_flag

        ts = datetime.now(timezone.utc)

        # --- 6. Build debit ClassifierOutput ---
        debit_entry = ClassifierOutput(
            message_id=str(uuid.uuid4()),
            timestamp=ts,
            source_agent=self.agent_name,
            target_agent="AUDITOR",
            account_code=debit_code,
            account_name=debit_acct["name"],
            entry_type=EntryType.DEBIT,
            amount=amount,
            rule_ref=rule_ref,
            confidence=confidence,
            reasoning=reasoning_base,
            contra_account_code=credit_code,
            contra_account_name=credit_acct["name"],
            contra_entry_type=EntryType.CREDIT,
            classified_intake_id=intake_output.message_id,
        )

        # --- 7. Build credit ClassifierOutput ---
        credit_entry = ClassifierOutput(
            message_id=str(uuid.uuid4()),
            timestamp=ts,
            source_agent=self.agent_name,
            target_agent="AUDITOR",
            account_code=credit_code,
            account_name=credit_acct["name"],
            entry_type=EntryType.CREDIT,
            amount=amount,
            rule_ref=rule_ref,
            confidence=confidence,
            reasoning=reasoning_base,
            contra_account_code=debit_code,
            contra_account_name=debit_acct["name"],
            contra_entry_type=EntryType.DEBIT,
            classified_intake_id=intake_output.message_id,
        )

        return debit_entry, credit_entry

    # ------------------------------------------------------------------
    # BaseAgent._process_impl — wraps classify() for framework compatibility
    # ------------------------------------------------------------------

    def _process_impl(self, message: IntakeOutput) -> ClassifierOutput:
        """
        Framework entry point. Runs classify() and returns the debit entry.
        The credit entry is accessible via the contra_* fields of the debit entry.
        """
        debit_entry, _credit_entry = self.classify(message)
        return debit_entry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_rule(
        self,
        vendor_lower: str,
        amount: Decimal,
        client_context: dict | None,
    ) -> tuple[str, str, Decimal, str] | None:
        """
        Returns (debit_code, credit_code, confidence_bonus, rule_ref) for the
        best matching rule, or None if no rule matches.

        Scoring: total character length of all matched keywords for a rule.
        Longer keyword matches win over shorter ones — this gives priority to
        more specific multi-word keywords (e.g. "ivu cobrado") over single
        general words (e.g. "venta"). On ties, the rule with more individual
        keyword hits wins. On further ties, the first rule in list order wins.
        """
        best: tuple[str, str, Decimal, str] | None = None
        best_char_score = 0
        best_count_score = 0

        for debit_code, credit_code, confidence_bonus, rule_ref, keywords in _ALL_RULES:
            matched = [kw for kw in keywords if kw in vendor_lower]
            count_score = len(matched)
            if count_score == 0:
                continue
            char_score = sum(len(kw) for kw in matched)
            if (char_score > best_char_score) or (
                char_score == best_char_score and count_score > best_count_score
            ):
                best_char_score = char_score
                best_count_score = count_score
                best = (debit_code, credit_code, confidence_bonus, rule_ref)

        # If no keyword matched but we have at least some vendor text,
        # fall back to Gastos Varios (5900) only for non-revenue transactions.
        if best is None and vendor_lower:
            best = ("5900", "2000", Decimal("0.00"), "GAAP-PR-5900-MISC")

        return best

    def _resolve_credit_by_payment(
        self,
        default_credit: str,
        payment_method: str | None,
    ) -> str:
        """
        Overrides the default credit account based on payment method.

        - CASH / CHECK → credit 1000 (Efectivo) — only for payable/AP accounts
        - CREDIT_CARD / ACH / WIRE → credit 2000 (Cuentas por Pagar)
        - None / unknown → keep default

        Liability accounts (2100–2400) are NEVER overridden by payment method
        because they represent specific tax/payroll liabilities, not cash flows.
        """
        if payment_method is None:
            return default_credit

        pm = payment_method.upper()

        # If default is already Efectivo (1000) or a specific liability/equity/revenue
        # account, never override — those accounts are determined by accounting rules,
        # not by the payment method.
        # 2100 = IVU por Pagar, 2200 = Nomina por Pagar, 2300 = Prestamos,
        # 2400 = Impuestos por Pagar, 1600 = Depreciacion Acumulada
        _no_override = {"1000", "1600", "2100", "2200", "2300", "2400",
                        "3000", "3100", "3900", "4000", "4100", "4900"}
        if default_credit in _no_override:
            return default_credit

        if pm in ("CASH", "CHECK"):
            return "1000"
        if pm in ("CREDIT_CARD", "ACH", "WIRE", "CREDIT CARD"):
            return "2000"

        return default_credit
