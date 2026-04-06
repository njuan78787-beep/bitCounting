# =============================================================================
# agents/independent_auditor.py
# AUDITOR — Agente de verificacion completamente independiente.
#
# PRINCIPIOS FUNDAMENTALES:
#   - Nunca lanza excepciones al sistema exterior. Todo error queda
#     encapsulado en AuditResult. El caller SIEMPRE recibe un resultado.
#   - Opera por cola separada — ningún otro agente le envia mensajes directo.
#   - Solo escribe en su audit_log interno — nunca modifica transacciones.
#   - Verifica tasas contra la fecha de la transaccion, nunca contra hoy.
#   - Tolerancia algebraica: exactamente $0.00 — un centavo rompe el balance.
# =============================================================================

from __future__ import annotations

import math
import statistics
import time
import uuid
from collections import deque
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tax_rules import get_rule


# =============================================================================
# MODELOS DE SALIDA — todos frozen
# =============================================================================

class AuditSeverity(str, Enum):
    LOW      = "LOW"       # Informativo — no bloquea el flujo
    MEDIUM   = "MEDIUM"    # Requiere revision CPA antes de cerrar el periodo
    HIGH     = "HIGH"      # Bloquea el asiento hasta resolucion
    CRITICAL = "CRITICAL"  # Detiene todo el flujo — notifica CPA inmediatamente


class AuditAlert(BaseModel):
    """Alerta individual generada por una verificacion fallida."""

    model_config = ConfigDict(frozen=True)

    alert_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    check_name: str = Field(description="Nombre del check que genero la alerta.")
    severity:   AuditSeverity
    description: str = Field(description="Descripcion exacta del problema detectado.")
    expected:   Optional[str] = Field(
        default=None,
        description="Valor esperado segun las reglas vigentes.",
    )
    actual:     Optional[str] = Field(
        default=None,
        description="Valor encontrado en la transaccion.",
    )
    rule_ref:   Optional[str] = Field(
        default=None,
        description="ID de la regla fiscal que define el valor esperado.",
    )


class AuditResult(BaseModel):
    """
    Resultado completo de una auditoria. Siempre se retorna — nunca se lanza
    una excepcion al sistema exterior.

    Si ocurre un error interno inesperado del auditor, passed=False y
    alerts contiene una alerta CRITICAL describiendo el error del auditor.
    """

    model_config = ConfigDict(frozen=True)

    audit_id:           str = Field(default_factory=lambda: str(uuid.uuid4()))
    transaction_ref:    str = Field(description="Referencia de la transaccion auditada.")
    passed:             bool
    checks_performed:   tuple[str, ...]
    alerts:             tuple[AuditAlert, ...]
    computation_time_ms: int
    auditor_version:    str = "2.0.0"
    timestamp:          str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    summary:            str = Field(description="Resumen en una linea del resultado.")


# =============================================================================
# REQUEST — estructura de entrada a la cola del AUDITOR
# =============================================================================

class AuditRequest(BaseModel):
    """Solicitud de auditoria que se encola para procesamiento."""

    model_config = ConfigDict(frozen=True)

    request_id:      str = Field(default_factory=lambda: str(uuid.uuid4()))
    transaction_ref: str = Field(description="Referencia unica de la transaccion.")
    transaction_date: str = Field(
        description="Fecha ISO 8601 de la transaccion (YYYY-MM-DD)."
    )

    # --- Asientos contables (partida doble o multiple) ---
    # Cada entrada: {"account_code": str, "entry_type": "debit"|"credit",
    #                "amount": Decimal, "description": str}
    journal_entries: tuple[dict, ...]

    # --- IVU ---
    ivu_base_amount:    Optional[Decimal] = None
    ivu_reported_amount: Optional[Decimal] = None
    ivu_exempt:         bool = False
    ivu_exempt_reason:  Optional[str] = None

    # --- Nomina ---
    is_payroll:       bool = False
    gross_pay:        Optional[Decimal] = None
    net_pay:          Optional[Decimal] = None
    deductions:       tuple[dict, ...] = ()   # [{"name": str, "amount": Decimal}]

    # --- Contexto para anomalias ---
    vendor:              Optional[str] = None
    vendor_is_new:       bool = False
    client_history_amounts: tuple[Decimal, ...] = ()

    # --- Tasa fiscal aplicada (para verificacion por fecha) ---
    rate_rule_id:    Optional[str] = None
    rate_applied:    Optional[Decimal] = None   # porcentaje: 10.5, 6.2, etc.


# =============================================================================
# DIAS NO LABORABLES PR — tabla expandida 2024-2026
# =============================================================================

_PR_NON_WORKING: frozenset[date] = frozenset([
    # 2024
    date(2024, 1, 1),  date(2024, 1, 6),  date(2024, 1, 15),
    date(2024, 2, 19), date(2024, 3, 22), date(2024, 3, 29),
    date(2024, 5, 27), date(2024, 7, 4),  date(2024, 7, 25),
    date(2024, 9, 2),  date(2024, 11, 19),date(2024, 11, 28),
    date(2024, 12, 25),
    # 2025
    date(2025, 1, 1),  date(2025, 1, 6),  date(2025, 1, 20),
    date(2025, 2, 17), date(2025, 3, 22), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 7, 4),  date(2025, 7, 25),
    date(2025, 9, 1),  date(2025, 11, 19),date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),  date(2026, 1, 6),  date(2026, 1, 19),
    date(2026, 2, 16), date(2026, 3, 22), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 7, 4),  date(2026, 7, 25),
    date(2026, 9, 7),  date(2026, 11, 19),date(2026, 11, 26),
    date(2026, 12, 25),
])

_IVU_TOLERANCE    = Decimal("0.01")
_ANOMALY_STD_DEVS = 2.0    # umbral en desviaciones estandar


# =============================================================================
# INDEPENDENT AUDITOR
# =============================================================================

class IndependentAuditor:
    """
    Agente AUDITOR completamente independiente.

    Opera por cola interna — ningún agente puede inyectarle requests directo
    sin pasar por submit(). Sus resultados van al audit_log append-only.
    No modifica transacciones. Captura todas las excepciones internamente.

    Uso:
        auditor = IndependentAuditor()
        req = AuditRequest(...)
        auditor.submit(req)
        result = auditor.process_next()   # None si la cola esta vacia
    """

    AUDITOR_VERSION = "2.0.0"

    def __init__(self) -> None:
        self._queue: deque[AuditRequest] = deque()
        self._audit_log: list[AuditResult] = []   # append-only interno

    # ------------------------------------------------------------------
    # Cola de entrada
    # ------------------------------------------------------------------

    def submit(self, request: AuditRequest) -> None:
        """Encola una solicitud de auditoria. Thread-safe para uso single-threaded."""
        if not isinstance(request, AuditRequest):
            raise TypeError(
                f"IndependentAuditor.submit() solo acepta AuditRequest. "
                f"Recibido: {type(request).__name__}"
            )
        self._queue.append(request)

    def queue_depth(self) -> int:
        """Cantidad de solicitudes pendientes en la cola."""
        return len(self._queue)

    def process_next(self) -> Optional[AuditResult]:
        """
        Procesa la siguiente solicitud de la cola.

        Returns:
            AuditResult si habia algo en la cola, None si estaba vacia.
        """
        if not self._queue:
            return None
        request = self._queue.popleft()
        result = self._run_audit(request)
        self._audit_log.append(result)
        return result

    def process_all(self) -> tuple[AuditResult, ...]:
        """Procesa todas las solicitudes en la cola y retorna los resultados."""
        results: list[AuditResult] = []
        while self._queue:
            r = self.process_next()
            if r is not None:
                results.append(r)
        return tuple(results)

    def get_audit_log(self) -> tuple[AuditResult, ...]:
        """Retorna el log de auditoria completo — inmutable (copia en tuple)."""
        return tuple(self._audit_log)

    # ------------------------------------------------------------------
    # Motor de auditoria — NUNCA lanza excepciones al exterior
    # ------------------------------------------------------------------

    def _run_audit(self, req: AuditRequest) -> AuditResult:
        t_start = time.monotonic()
        checks_performed: list[str] = []
        alerts: list[AuditAlert] = []

        try:
            # 1. Balance algebraico
            self._check_algebraic_balance(req, checks_performed, alerts)

            # 2. IVU contra documento original
            self._check_ivu(req, checks_performed, alerts)

            # 3. Nomina: gross == net + deducciones
            self._check_payroll_balance(req, checks_performed, alerts)

            # 4. Dias no laborables PR
            self._check_non_working_day(req, checks_performed, alerts)

            # 5. Vendor nuevo + monto atipico (>2 desv. estandar)
            self._check_new_vendor_anomaly(req, checks_performed, alerts)

            # 6. Tasa aplicada vs. tasa vigente en la fecha de la transaccion
            self._check_rate_by_date(req, checks_performed, alerts)

        except Exception as exc:
            # Captura de cualquier error inesperado del propio auditor
            alerts.append(AuditAlert(
                check_name="AUDITOR_INTERNAL_ERROR",
                severity=AuditSeverity.CRITICAL,
                description=(
                    f"Error interno del AUDITOR — esto es un bug del sistema, "
                    f"no de la transaccion: {type(exc).__name__}: {exc}"
                ),
            ))

        ms = int((time.monotonic() - t_start) * 1000)
        passed = all(a.severity not in (AuditSeverity.HIGH, AuditSeverity.CRITICAL)
                     for a in alerts)
        n_alerts = len(alerts)
        summary = (
            "OK" if n_alerts == 0
            else f"{n_alerts} alerta(s): "
                 + ", ".join(f"{a.severity.value}:{a.check_name}" for a in alerts)
        )

        return AuditResult(
            transaction_ref=req.transaction_ref,
            passed=passed,
            checks_performed=tuple(checks_performed),
            alerts=tuple(alerts),
            computation_time_ms=ms,
            auditor_version=self.AUDITOR_VERSION,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # CHECK 1 — Balance algebraico (tolerancia exacta $0.00)
    # ------------------------------------------------------------------

    def _check_algebraic_balance(
        self,
        req: AuditRequest,
        checks: list[str],
        alerts: list[AuditAlert],
    ) -> None:
        check = "algebraic_balance"
        checks.append(check)

        if not req.journal_entries:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.HIGH,
                description="El asiento no tiene entradas contables.",
            ))
            return

        total_debit  = Decimal("0.00")
        total_credit = Decimal("0.00")

        for entry in req.journal_entries:
            try:
                amt = Decimal(str(entry.get("amount", "0")))
            except Exception:
                alerts.append(AuditAlert(
                    check_name=check,
                    severity=AuditSeverity.CRITICAL,
                    description=f"Monto no parseable en entrada: {entry}",
                ))
                return

            etype = str(entry.get("entry_type", "")).lower()
            if etype == "debit":
                total_debit  += amt
            elif etype == "credit":
                total_credit += amt
            else:
                alerts.append(AuditAlert(
                    check_name=check,
                    severity=AuditSeverity.HIGH,
                    description=(
                        f"entry_type invalido '{entry.get('entry_type')}' en cuenta "
                        f"'{entry.get('account_code')}'. Solo 'debit' o 'credit'."
                    ),
                ))
                return

        diff = abs(total_debit - total_credit)

        if diff != Decimal("0.00"):
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.CRITICAL,
                description=(
                    f"BALANCE ROTO: suma debitos ${total_debit} != "
                    f"suma creditos ${total_credit}. "
                    f"Diferencia: ${diff}. Tolerancia: exactamente $0.00."
                ),
                expected=str(total_debit),
                actual=str(total_credit),
            ))

    # ------------------------------------------------------------------
    # CHECK 2 — IVU calculado vs. reportado en documento
    # ------------------------------------------------------------------

    def _check_ivu(
        self,
        req: AuditRequest,
        checks: list[str],
        alerts: list[AuditAlert],
    ) -> None:
        check = "ivu_verification"

        if req.ivu_base_amount is None and req.ivu_reported_amount is None:
            checks.append(f"{check}:not_applicable")
            return

        checks.append(check)

        if req.ivu_exempt:
            if req.ivu_reported_amount is not None and req.ivu_reported_amount != Decimal("0.00"):
                alerts.append(AuditAlert(
                    check_name=check,
                    severity=AuditSeverity.HIGH,
                    description=(
                        f"Transaccion marcada como exenta de IVU pero reporta "
                        f"IVU = ${req.ivu_reported_amount}. "
                        f"Razon de exencion: {req.ivu_exempt_reason or 'no especificada'}."
                    ),
                    expected="0.00",
                    actual=str(req.ivu_reported_amount),
                ))
            return

        if req.ivu_base_amount is None or req.ivu_reported_amount is None:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.MEDIUM,
                description=(
                    "Faltan campos para verificar IVU: se requiere tanto "
                    "ivu_base_amount como ivu_reported_amount."
                ),
            ))
            return

        # Obtener tasa vigente en la FECHA DE LA TRANSACCION
        try:
            txn_date = date.fromisoformat(req.transaction_date)
        except ValueError:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.HIGH,
                description=f"Fecha invalida para lookup de tasa: '{req.transaction_date}'",
            ))
            return

        ivu_estatal = get_rule("IVU_ESTATAL_PR_2015_V1", txn_date)
        ivu_munic   = get_rule("IVU_MUNICIPAL_PR_2015_V1", txn_date)

        if ivu_estatal is None:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.CRITICAL,
                description=(
                    f"No se encontro regla IVU estatal vigente en {txn_date}. "
                    "No es posible verificar el calculo."
                ),
            ))
            return

        # Tasas en porcentaje → convertir a decimal
        rate_estatal = ivu_estatal.rate / Decimal("100")
        rate_munic   = (ivu_munic.rate / Decimal("100")) if ivu_munic else Decimal("0.00")
        total_rate   = rate_estatal + rate_munic

        expected_ivu = (req.ivu_base_amount * total_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        diff = abs(req.ivu_reported_amount - expected_ivu)

        if diff > _IVU_TOLERANCE:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.HIGH,
                description=(
                    f"IVU INCORRECTO: reportado ${req.ivu_reported_amount}, "
                    f"esperado ${expected_ivu} "
                    f"({total_rate * 100:.1f}% de base ${req.ivu_base_amount}). "
                    f"Diferencia: ${diff} (tolerancia: ±${_IVU_TOLERANCE}). "
                    f"Tasa vigente en {txn_date}: estatal {ivu_estatal.rate}% + "
                    f"municipal {ivu_munic.rate if ivu_munic else 0}%."
                ),
                expected=str(expected_ivu),
                actual=str(req.ivu_reported_amount),
                rule_ref=ivu_estatal.rule_id,
            ))

    # ------------------------------------------------------------------
    # CHECK 3 — Nomina: gross == net + sum(deducciones)
    # ------------------------------------------------------------------

    def _check_payroll_balance(
        self,
        req: AuditRequest,
        checks: list[str],
        alerts: list[AuditAlert],
    ) -> None:
        check = "payroll_balance"

        if not req.is_payroll:
            checks.append(f"{check}:not_applicable")
            return

        checks.append(check)

        if req.gross_pay is None or req.net_pay is None:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.HIGH,
                description=(
                    "Transaccion marcada como nomina pero falta gross_pay o net_pay."
                ),
            ))
            return

        total_deductions = Decimal("0.00")
        for ded in req.deductions:
            try:
                total_deductions += Decimal(str(ded.get("amount", "0")))
            except Exception as e:
                alerts.append(AuditAlert(
                    check_name=check,
                    severity=AuditSeverity.CRITICAL,
                    description=f"Deduccion con monto invalido: {ded} — {e}",
                ))
                return

        expected_net = req.gross_pay - total_deductions
        diff = abs(req.net_pay - expected_net)

        if diff != Decimal("0.00"):
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.CRITICAL,
                description=(
                    f"NOMINA DESBALANCEADA: gross ${req.gross_pay} - "
                    f"deducciones ${total_deductions} = ${expected_net}, "
                    f"pero net_pay reportado es ${req.net_pay}. "
                    f"Diferencia: ${diff}. Tolerancia: exactamente $0.00."
                ),
                expected=str(expected_net),
                actual=str(req.net_pay),
            ))

    # ------------------------------------------------------------------
    # CHECK 4 — Dia no laborable PR
    # ------------------------------------------------------------------

    def _check_non_working_day(
        self,
        req: AuditRequest,
        checks: list[str],
        alerts: list[AuditAlert],
    ) -> None:
        check = "non_working_day"
        checks.append(check)

        try:
            txn_date = date.fromisoformat(req.transaction_date)
        except ValueError:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.HIGH,
                description=f"Fecha no parseable: '{req.transaction_date}'",
            ))
            return

        if txn_date in _PR_NON_WORKING:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.LOW,
                description=(
                    f"FERIADO PR: la transaccion tiene fecha {req.transaction_date}, "
                    "que es un dia no laborable en Puerto Rico. "
                    "Verificar si la fecha es correcta o si corresponde al proximo dia habil."
                ),
                actual=req.transaction_date,
            ))

    # ------------------------------------------------------------------
    # CHECK 5 — Vendor nuevo + monto atipico (>2 desviaciones estandar)
    # ------------------------------------------------------------------

    def _check_new_vendor_anomaly(
        self,
        req: AuditRequest,
        checks: list[str],
        alerts: list[AuditAlert],
    ) -> None:
        check = "new_vendor_anomaly"

        if not req.vendor_is_new:
            checks.append(f"{check}:vendor_known")
            return

        checks.append(check)

        # Calcular el monto total del asiento actual
        txn_amount = Decimal("0.00")
        for entry in req.journal_entries:
            etype = str(entry.get("entry_type", "")).lower()
            if etype == "debit":
                try:
                    txn_amount += Decimal(str(entry.get("amount", "0")))
                except Exception:
                    pass

        if not req.client_history_amounts or len(req.client_history_amounts) < 3:
            # Sin historial suficiente — alertar solo si monto es grande
            if txn_amount > Decimal("5000.00"):
                alerts.append(AuditAlert(
                    check_name=check,
                    severity=AuditSeverity.MEDIUM,
                    description=(
                        f"VENDOR NUEVO: '{req.vendor}' no tiene historial. "
                        f"Monto ${txn_amount} supera $5,000. "
                        "Se requiere verificacion adicional (sin historial para calcular desv. estandar)."
                    ),
                    actual=str(txn_amount),
                    rule_ref="VENDOR_NEW_HIGH_AMOUNT",
                ))
            return

        # Calcular media y desviacion estandar del historial
        history = [float(a) for a in req.client_history_amounts]
        mean_val = statistics.mean(history)
        stdev_val = statistics.stdev(history)

        if stdev_val == 0:
            checks.append(f"{check}:stdev_zero_skip")
            return

        z_score = (float(txn_amount) - mean_val) / stdev_val

        if z_score > _ANOMALY_STD_DEVS:
            severity = (
                AuditSeverity.CRITICAL if z_score > 4.0
                else AuditSeverity.HIGH if z_score > 3.0
                else AuditSeverity.MEDIUM
            )
            alerts.append(AuditAlert(
                check_name=check,
                severity=severity,
                description=(
                    f"ANOMALIA ESTADISTICA: vendor nuevo '{req.vendor}' con monto "
                    f"${txn_amount} — {z_score:.1f} desviaciones estandar sobre "
                    f"la media historica del cliente (media=${mean_val:.2f}, "
                    f"stdev=${stdev_val:.2f}). Umbral: {_ANOMALY_STD_DEVS} desv. estandar."
                ),
                expected=f"<= ${mean_val + _ANOMALY_STD_DEVS * stdev_val:.2f}",
                actual=str(txn_amount),
            ))

    # ------------------------------------------------------------------
    # CHECK 6 — Tasa aplicada vs. tasa vigente en fecha de transaccion
    # ------------------------------------------------------------------

    def _check_rate_by_date(
        self,
        req: AuditRequest,
        checks: list[str],
        alerts: list[AuditAlert],
    ) -> None:
        check = "rate_by_transaction_date"

        if req.rate_rule_id is None or req.rate_applied is None:
            checks.append(f"{check}:not_applicable")
            return

        checks.append(check)

        try:
            txn_date = date.fromisoformat(req.transaction_date)
        except ValueError:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.HIGH,
                description=f"Fecha invalida para verificacion de tasa: '{req.transaction_date}'",
            ))
            return

        rule = get_rule(req.rate_rule_id, txn_date)

        if rule is None:
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.CRITICAL,
                description=(
                    f"Regla '{req.rate_rule_id}' NO estaba vigente en {txn_date}. "
                    "La tasa aplicada corresponde a una fecha incorrecta."
                ),
                rule_ref=req.rate_rule_id,
            ))
            return

        # La regla tiene campos rate, rate_employee, rate_employer
        # Buscar la tasa que corresponde al contexto
        correct_rate: Optional[Decimal] = None
        if rule.rate is not None:
            correct_rate = rule.rate
        elif rule.rate_employee is not None:
            correct_rate = rule.rate_employee

        if correct_rate is None:
            checks.append(f"{check}:rule_has_no_rate_field")
            return

        # Comparar (ambas en porcentaje)
        diff = abs(req.rate_applied - correct_rate)

        if diff > Decimal("0.001"):
            alerts.append(AuditAlert(
                check_name=check,
                severity=AuditSeverity.HIGH,
                description=(
                    f"TASA INCORRECTA: se aplico {req.rate_applied}% pero la regla "
                    f"'{req.rate_rule_id}' vigente en {txn_date} establece {correct_rate}%. "
                    "La tasa debe corresponder a la fecha de la transaccion, "
                    "no a la fecha actual."
                ),
                expected=str(correct_rate),
                actual=str(req.rate_applied),
                rule_ref=req.rate_rule_id,
            ))
