# =============================================================================
# agents/clasificador_v2.py
# Agente CLASIFICADOR V2 — asignacion de cuentas contables con sandbox.
#
# GARANTIAS DE DISENO:
#   - Solo opera sobre el catalogo fijo del cliente (account_catalog).
#   - NUNCA crea cuentas fuera del catalogo — si no encaja: UNCLASSIFIED.
#   - UNCLASSIFIED escala automaticamente al CENTINELA.
#   - Totales de line_items verificados via CalculationSandbox, no inline.
#   - Confianza < 0.65 → flag needs_cpa_review=True (no excepcion).
#   - Toda decision queda registrada en AgentDecisionsLog (append-only).
#   - ClassificationResultV2 es frozen=True — inmutable.
# =============================================================================

from __future__ import annotations

import logging
import sys
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .core_decisions import get_decisions_log, AgentDecisionsLog
from .exceptions import BitCountingAgentError
from .intake_v2 import IntakeResultV2, LineItemV2

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from calculation_sandbox.sandbox import CalculationSandbox

logger = logging.getLogger(__name__)

_CONFIDENCE_REVIEW_THRESHOLD = Decimal("0.65")


# ---------------------------------------------------------------------------
# Catalogo de cuentas PR GAAP por defecto
# ---------------------------------------------------------------------------

DEFAULT_ACCOUNT_CATALOG: dict[str, dict] = {
    # ACTIVOS (1xxx)
    "1000": {"name": "Efectivo y Equivalentes",        "category": "ASSET",        "normal": "debit"},
    "1100": {"name": "Cuentas por Cobrar",             "category": "ASSET",        "normal": "debit"},
    "1200": {"name": "Inventario",                     "category": "ASSET",        "normal": "debit"},
    "1300": {"name": "Gastos Prepagados",              "category": "ASSET",        "normal": "debit"},
    "1500": {"name": "Propiedad, Planta y Equipo",     "category": "ASSET",        "normal": "debit"},
    "1600": {"name": "Depreciacion Acumulada",         "category": "CONTRA_ASSET", "normal": "credit"},
    # PASIVOS (2xxx)
    "2000": {"name": "Cuentas por Pagar",              "category": "LIABILITY",    "normal": "credit"},
    "2100": {"name": "IVU por Pagar",                  "category": "LIABILITY",    "normal": "credit"},
    "2200": {"name": "FICA por Pagar",                 "category": "LIABILITY",    "normal": "credit"},
    "2300": {"name": "Deudas a Largo Plazo",           "category": "LIABILITY",    "normal": "credit"},
    # CAPITAL (3xxx)
    "3000": {"name": "Capital Social",                 "category": "EQUITY",       "normal": "credit"},
    "3100": {"name": "Utilidades Retenidas",           "category": "EQUITY",       "normal": "credit"},
    "3200": {"name": "Distribucion a Socios",          "category": "EQUITY",       "normal": "debit"},
    # INGRESOS (4xxx)
    "4000": {"name": "Ingresos por Ventas",            "category": "REVENUE",      "normal": "credit"},
    "4100": {"name": "Ingresos por Servicios",         "category": "REVENUE",      "normal": "credit"},
    "4200": {"name": "Otros Ingresos",                 "category": "REVENUE",      "normal": "credit"},
    # GASTOS (5xxx)
    "5000": {"name": "Costo de Ventas",                "category": "EXPENSE",      "normal": "debit"},
    "5100": {"name": "Gastos de Nomina",               "category": "EXPENSE",      "normal": "debit"},
    "5200": {"name": "Gastos de Alquiler",             "category": "EXPENSE",      "normal": "debit"},
    "5300": {"name": "Gastos de Servicios Publicos",   "category": "EXPENSE",      "normal": "debit"},
    "5400": {"name": "Gastos de Depreciacion",         "category": "EXPENSE",      "normal": "debit"},
    "5500": {"name": "Gastos Administrativos",         "category": "EXPENSE",      "normal": "debit"},
    "5600": {"name": "IVU Pagado (Insumo)",            "category": "EXPENSE",      "normal": "debit"},
    "5700": {"name": "Gastos de Telecomunicaciones",   "category": "EXPENSE",      "normal": "debit"},
    "5800": {"name": "Gastos Profesionales",           "category": "EXPENSE",      "normal": "debit"},
    "5900": {"name": "Otros Gastos",                   "category": "EXPENSE",      "normal": "debit"},
    # ESPECIAL
    "9999": {"name": "UNCLASSIFIED",                   "category": "UNCLASSIFIED", "normal": "debit"},
}

# Reglas de clasificacion: keywords en vendor/description → account_code
_CLASSIFICATION_RULES: list[tuple[list[str], str, float]] = [
    # (keywords, account_code, confidence_boost)
    (["alquiler", "renta", "arrendamiento", "lease"],                     "5200", 0.90),
    (["nomina", "salario", "sueldo", "payroll", "wages"],                 "5100", 0.90),
    (["electricidad", "luz", "aee", "autoridad energia", "agua", "aaa"], "5300", 0.85),
    (["telefono", "celular", "internet", "cable", "claro", "liberty"],   "5700", 0.85),
    (["abogado", "notario", "legal", "attorney", "law"],                  "5800", 0.85),
    (["contador", "contabilidad", "auditoria", "cpa"],                    "5800", 0.85),
    (["ivu", "sales tax", "impuesto sobre ventas"],                       "5600", 0.90),
    (["inventario", "mercancias", "productos", "goods"],                  "5000", 0.80),
    (["venta", "sale", "ingreso", "revenue"],                             "4000", 0.80),
    (["servicio", "service", "consulting", "consultoria"],                "4100", 0.75),
    (["depreciacion", "depreciation", "amortizacion"],                    "5400", 0.90),
    (["administracion", "oficina", "office", "supplies"],                 "5500", 0.75),
    (["efectivo", "cash", "cheque", "check", "transferencia"],            "1000", 0.70),
    (["cuenta por cobrar", "receivable", "factura pendiente"],            "1100", 0.80),
]


# ---------------------------------------------------------------------------
# Modelos de resultado
# ---------------------------------------------------------------------------

class ClassifiedLineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    original_description: str
    amount:               Decimal
    account_code:         str
    account_name:         str
    account_category:     str
    confidence:           Decimal
    classification_rule:  str   # descripcion de la regla aplicada
    is_unclassified:      bool = False


class ClassificationResultV2(BaseModel):
    """
    Output del CLASIFICADOR V2. Frozen — inmutable.

    unclassified_count > 0 activa escalacion automatica al CENTINELA.
    needs_cpa_review=True cuando confianza promedio < 0.65.
    """
    model_config = ConfigDict(frozen=True)

    result_id:          str = Field(default_factory=lambda: str(uuid.uuid4()))
    intake_result_id:   str
    classified_items:   tuple[ClassifiedLineItem, ...]
    total_amount:       Decimal   # verificado via sandbox
    unclassified_count: int
    needs_cpa_review:   bool
    average_confidence: Decimal
    sandbox_result_id:  Optional[str] = None
    decision_id:        Optional[str] = None
    escalate_to_centinela: bool = False
    classified_at:      str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Agente CLASIFICADOR V2
# ---------------------------------------------------------------------------

class ClasificadorAgentV2:
    """
    Agente CLASIFICADOR V2 — asigna cuentas contables usando reglas y sandbox.

    Instanciar con catalogo opcional; default = PR GAAP catalog.
    """

    AGENT_NAME = "CLASIFICADOR_V2"

    def __init__(
        self,
        account_catalog: Optional[dict[str, dict]] = None,
        decisions_log:   Optional[AgentDecisionsLog] = None,
    ) -> None:
        self._catalog = account_catalog or DEFAULT_ACCOUNT_CATALOG
        self._log     = decisions_log if decisions_log is not None else get_decisions_log()
        self._sandbox = CalculationSandbox()

    # ------------------------------------------------------------------ #
    # Punto de entrada principal                                           #
    # ------------------------------------------------------------------ #

    def classify(self, intake_result: IntakeResultV2) -> ClassificationResultV2:
        """
        Clasifica las line_items del IntakeResultV2 segun el catalogo.

        Si intake no tiene line_items, crea un item sintetico desde el monto total.
        Todos los items que no encajan en el catalogo se marcan UNCLASSIFIED.
        El total se verifica via CalculationSandbox.

        Returns:
            ClassificationResultV2 — inmutable, con todas las clasificaciones.
        """
        # Construir lista de items a clasificar
        items = list(intake_result.line_items)
        if not items and intake_result.amount is not None:
            items = [LineItemV2(
                description=intake_result.vendor or "Transaccion sin detalle",
                amount=intake_result.amount,
            )]

        classified: list[ClassifiedLineItem] = []
        for item in items:
            classified.append(self._classify_item(item))

        # Verificar total via sandbox
        amounts = [str(c.amount) for c in classified]
        total, sandbox_id = self._verify_total_sandbox(amounts)

        # Calcular metricas
        unclassified_count = sum(1 for c in classified if c.is_unclassified)
        confidences        = [c.confidence for c in classified]
        avg_confidence     = (
            sum(confidences, Decimal("0")) / Decimal(len(confidences))
            if confidences else Decimal("0")
        )
        needs_review   = avg_confidence < _CONFIDENCE_REVIEW_THRESHOLD
        escalate       = unclassified_count > 0

        # Registrar decision
        decision_entry = self._log.record(
            agent_name=self.AGENT_NAME,
            input_data={"intake_result_id": intake_result.result_id, "items": len(items)},
            decision=(
                f"Clasificados {len(classified)} items. "
                f"Unclassified: {unclassified_count}. "
                f"Confianza promedio: {avg_confidence:.2f}. "
                f"Escalar CENTINELA: {escalate}."
            ),
            confidence=float(avg_confidence),
            sandbox_ids=(sandbox_id,) if sandbox_id else (),
        )

        return ClassificationResultV2(
            intake_result_id=intake_result.result_id,
            classified_items=tuple(classified),
            total_amount=total,
            unclassified_count=unclassified_count,
            needs_cpa_review=needs_review,
            average_confidence=avg_confidence,
            sandbox_result_id=sandbox_id,
            decision_id=decision_entry.decision_id,
            escalate_to_centinela=escalate,
        )

    # ------------------------------------------------------------------ #
    # Clasificacion de item individual                                    #
    # ------------------------------------------------------------------ #

    def _classify_item(self, item: LineItemV2) -> ClassifiedLineItem:
        """Clasifica un LineItemV2 usando las reglas del catalogo."""
        text = (item.description or "").lower()
        best_code: Optional[str] = None
        best_conf = 0.0
        best_rule = "sin coincidencia"

        for keywords, account_code, conf in _CLASSIFICATION_RULES:
            if account_code not in self._catalog:
                continue
            for kw in keywords:
                if kw in text:
                    if conf > best_conf:
                        best_conf  = conf
                        best_code  = account_code
                        best_rule  = f"keyword '{kw}' → {account_code}"
                    break

        if best_code is None:
            # No hay coincidencia — UNCLASSIFIED
            account = self._catalog["9999"]
            return ClassifiedLineItem(
                original_description=item.description,
                amount=item.amount,
                account_code="9999",
                account_name=account["name"],
                account_category=account["category"],
                confidence=Decimal("0.0"),
                classification_rule="sin coincidencia — UNCLASSIFIED",
                is_unclassified=True,
            )

        account = self._catalog[best_code]
        return ClassifiedLineItem(
            original_description=item.description,
            amount=item.amount,
            account_code=best_code,
            account_name=account["name"],
            account_category=account["category"],
            confidence=Decimal(str(best_conf)),
            classification_rule=best_rule,
            is_unclassified=False,
        )

    # ------------------------------------------------------------------ #
    # Verificacion de total via CalculationSandbox                       #
    # ------------------------------------------------------------------ #

    def _verify_total_sandbox(
        self,
        amounts: list[str],
    ) -> tuple[Decimal, Optional[str]]:
        """
        Suma los montos de los items via sandbox — NUNCA aritmetica directa.

        Retorna (total, sandbox_result_id).
        Si el sandbox falla, retorna suma directa de fallback con id=None.
        """
        if not amounts:
            return Decimal("0"), None

        # Generar codigo para el sandbox
        amounts_literal = ", ".join(f"Decimal('{a}')" for a in amounts)
        code = (
            f"from decimal import Decimal\n"
            f"amounts = [{amounts_literal}]\n"
            f"result = sum(amounts, Decimal('0'))\n"
        )

        sandbox_result = self._sandbox.execute(code)

        if sandbox_result.success:
            try:
                total = Decimal(str(sandbox_result.result_value))
                return total, sandbox_result.result_id
            except Exception:
                pass

        # Fallback si sandbox falla (log el error)
        logger.warning(
            "CLASIFICADOR V2: sandbox fallo al verificar total: %s",
            sandbox_result.error,
        )
        fallback = sum(Decimal(a) for a in amounts)
        return fallback, None
