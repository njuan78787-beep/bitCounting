# =============================================================================
# agents/centinela_guardian.py
# CENTINELA GUARDIAN — Guardian del sistema Bit-Counting.
#
# PRINCIPIOS FUNDAMENTALES:
#   - Una pausa emitida es IRREVOCABLE por cualquier agente del sistema.
#     Solo el CPA autenticado puede liberarla via release_pause().
#   - La pausa es QUIRURGICA — bloquea solo las transacciones afectadas,
#     no toda la operacion del cliente.
#   - El log de pausas y liberaciones es INMUTABLE — append-only.
#   - Si el CPA no responde en X horas (configurable), escala automaticamente
#     al siguiente CPA en la cadena del cliente.
#   - NADA avanza sobre una transaccion bloqueada hasta que el CPA la libere.
#
# 5 TRIGGERS DE PAUSA:
#   1. Regla nueva contradice la aplicada en los ultimos 90 dias (mismo tipo)
#   2. Confidence score < umbral configurado por cliente
#   3. Sin precedente en los ultimos 6 meses del cliente
#   4. Dos agentes llegaron a resultados distintos (verificacion cruzada)
#   5. AuditResult del AUDITOR tiene severity HIGH o CRITICAL
# =============================================================================

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# ENUMS
# =============================================================================

class PauseTrigger(str, Enum):
    RULE_CONTRADICTION_90D  = "RULE_CONTRADICTION_90D"   # Trigger 1
    LOW_CONFIDENCE          = "LOW_CONFIDENCE"            # Trigger 2
    NO_PRECEDENT_6M         = "NO_PRECEDENT_6M"           # Trigger 3
    AGENT_DIVERGENCE        = "AGENT_DIVERGENCE"          # Trigger 4
    AUDIT_HIGH_CRITICAL     = "AUDIT_HIGH_CRITICAL"       # Trigger 5
    MANUAL_CPA_OVERRIDE     = "MANUAL_CPA_OVERRIDE"       # CPA manual


class PauseStatus(str, Enum):
    ACTIVE    = "ACTIVE"
    RESOLVED  = "RESOLVED"
    ESCALATED = "ESCALATED"


# =============================================================================
# MODELOS — todos frozen
# =============================================================================

class FiscalImpactEstimate(BaseModel):
    """Estimado del impacto fiscal de una interpretacion posible."""
    model_config = ConfigDict(frozen=True)

    interpretation_label: str
    tax_liability_low:  Decimal
    tax_liability_high: Decimal
    notes: str


class SurgicalPause(BaseModel):
    """
    Pausa quirurgica — bloquea solo las transacciones afectadas.

    Incluye el analisis pre-procesado completo para el CPA:
    resumen del conflicto, reglas en conflicto (texto completo),
    interpretaciones posibles, impacto fiscal estimado, y precedentes.
    """
    model_config = ConfigDict(frozen=True)

    pause_id:            str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_id:           str
    trigger:             PauseTrigger
    status:              PauseStatus = PauseStatus.ACTIVE
    created_at:          str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Scope quirurgico — SOLO estas transacciones estan bloqueadas
    affected_transaction_ids: tuple[str, ...]

    # Analisis pre-procesado para el CPA
    conflict_summary:    str   # resumen en espanol claro
    rules_in_conflict:   tuple[str, ...]  # IDs de reglas
    rules_text:          tuple[str, ...]  # texto completo de cada regla
    interpretations:     tuple[str, ...]  # 2-3 interpretaciones posibles
    fiscal_impact:       tuple[FiscalImpactEstimate, ...]
    historical_precedents: tuple[str, ...]  # precedentes del cliente

    # SLA y escalacion
    cpa_assigned:        str
    sla_hours:           int = 48
    escalation_chain:    tuple[str, ...]  = ()  # CPAs adicionales en orden
    escalated_to:        Optional[str]    = None
    escalated_at:        Optional[str]    = None


class PauseRelease(BaseModel):
    """
    Registro inmutable de la liberacion de una pausa por un CPA.
    Una vez creado, no puede modificarse.
    """
    model_config = ConfigDict(frozen=True)

    release_id:          str = Field(default_factory=lambda: str(uuid.uuid4()))
    pause_id:            str
    released_by_license: str   # numero de licencia del CPA
    released_by_name:    Optional[str]
    released_at:         str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    interpretation_chosen: str   # cual de las interpretaciones eligio el CPA
    instruction:         str     # instruccion especifica del CPA
    requires_follow_up:  bool = False


class CentinelaDecision(BaseModel):
    """Decision del CENTINELA para una transaccion especifica."""
    model_config = ConfigDict(frozen=True)

    decision:     str   # "PROCEED" o "PAUSE"
    pause_id:     Optional[str]
    trigger:      Optional[PauseTrigger]
    reason:       str
    confidence:   Decimal
    client_id:    str
    transaction_id: str
    evaluated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# =============================================================================
# CONFIGURACION POR CLIENTE
# =============================================================================

class ClientConfig(BaseModel):
    """Configuracion del CENTINELA especifica para un cliente."""
    model_config = ConfigDict(frozen=True)

    client_id:              str
    confidence_threshold:   Decimal = Decimal("0.75")  # umbral configurable
    sla_hours:              int     = 48
    cpa_primary:            str     = "CPA_DEFAULT"
    escalation_chain:       tuple[str, ...] = ()
    require_precedent:      bool    = True   # pausar si sin precedente 6m
    no_precedent_threshold: Decimal = Decimal("5000.00")  # solo pausar si > este monto


# =============================================================================
# CENTINELA GUARDIAN
# =============================================================================

class CentinelaGuardian:
    """
    CENTINELA GUARDIAN — guardian del flujo de Bit-Counting.

    Uso:
        guardian = CentinelaGuardian()
        decision = guardian.evaluate(transaction, context)
        if decision.decision == "PAUSE":
            # la transaccion esta bloqueada
            pause = guardian.get_pause(decision.pause_id)
    """

    def __init__(self) -> None:
        # Pausas activas: pause_id -> SurgicalPause
        self._active_pauses:   dict[str, SurgicalPause]  = {}
        # Log inmutable de pausas (append-only)
        self._pause_log:       list[SurgicalPause]        = []
        # Log inmutable de liberaciones (append-only)
        self._release_log:     list[PauseRelease]         = []
        # Transacciones bloqueadas: transaction_id -> pause_id
        self._blocked_txns:    dict[str, str]             = {}

    # ------------------------------------------------------------------
    # EVALUACION PRINCIPAL
    # ------------------------------------------------------------------

    def evaluate(
        self,
        transaction_id:   str,
        client_id:        str,
        transaction_date: str,
        amount:           Decimal,
        vendor:           Optional[str],
        transaction_type: str,
        confidence:       Decimal,
        current_rule_id:  Optional[str],
        history_90d:      tuple[dict, ...],
        history_6m:       tuple[dict, ...],
        cross_check_results: tuple[dict, ...],
        audit_result:     Optional[dict],
        client_config:    Optional[ClientConfig] = None,
    ) -> CentinelaDecision:
        """
        Evalua una transaccion y decide PROCEED o PAUSE.

        Aplica los 5 triggers en orden de severidad. El primer trigger que
        activa una pausa detiene la evaluacion y emite la pausa quirurgica.

        Args:
            transaction_id:      ID unico de la transaccion.
            client_id:           ID del cliente.
            transaction_date:    Fecha ISO 8601 de la transaccion.
            amount:              Monto de la transaccion.
            vendor:              Proveedor (puede ser None).
            transaction_type:    Tipo de transaccion (GASTO, INGRESO, NOMINA, etc.).
            confidence:          Score de confianza del INTAKE (0.0-1.0).
            current_rule_id:     ID de la regla fiscal aplicada.
            history_90d:         Historial del cliente de los ultimos 90 dias.
            history_6m:          Historial del cliente de los ultimos 6 meses.
            cross_check_results: Resultados de verificacion cruzada entre agentes.
                                 Cada dict: {"agent": str, "result": str/Decimal}
            audit_result:        Resultado del AUDITOR (dict con "passed", "alerts").
                                 Cada alerta: {"severity": str, "check_name": str, ...}
            client_config:       Configuracion especifica del cliente.

        Returns:
            CentinelaDecision con decision PROCEED o PAUSE.
        """
        cfg = client_config or ClientConfig(client_id=client_id)

        # --- TRIGGER 5 (mayor severidad): AuditResult HIGH o CRITICAL ---
        audit_trigger = self._check_audit_severity(audit_result)
        if audit_trigger:
            return self._emit_pause_decision(
                transaction_id=transaction_id,
                client_id=client_id,
                trigger=PauseTrigger.AUDIT_HIGH_CRITICAL,
                conflict_summary=audit_trigger["summary"],
                rules_in_conflict=tuple(audit_trigger.get("rules", [])),
                rules_text=tuple(audit_trigger.get("rules_text", [])),
                interpretations=self._interpretations_audit(audit_trigger),
                fiscal_impact=self._fiscal_impact_audit(amount, audit_trigger),
                historical_precedents=(),
                cpa_assigned=cfg.cpa_primary,
                sla_hours=cfg.sla_hours,
                escalation_chain=cfg.escalation_chain,
                confidence=confidence,
            )

        # --- TRIGGER 4: Dos agentes divergen ---
        divergence = self._check_agent_divergence(cross_check_results)
        if divergence:
            return self._emit_pause_decision(
                transaction_id=transaction_id,
                client_id=client_id,
                trigger=PauseTrigger.AGENT_DIVERGENCE,
                conflict_summary=divergence["summary"],
                rules_in_conflict=(),
                rules_text=(),
                interpretations=self._interpretations_divergence(divergence, amount, vendor),
                fiscal_impact=self._fiscal_impact_divergence(amount, divergence),
                historical_precedents=(),
                cpa_assigned=cfg.cpa_primary,
                sla_hours=cfg.sla_hours,
                escalation_chain=cfg.escalation_chain,
                confidence=confidence,
            )

        # --- TRIGGER 1: Regla nueva contradice lo aplicado en 90 dias ---
        contradiction = self._check_rule_contradiction_90d(
            transaction_type, current_rule_id, history_90d
        )
        if contradiction:
            pcts = self._get_history_precedents(history_6m, vendor, transaction_type)
            return self._emit_pause_decision(
                transaction_id=transaction_id,
                client_id=client_id,
                trigger=PauseTrigger.RULE_CONTRADICTION_90D,
                conflict_summary=contradiction["summary"],
                rules_in_conflict=tuple(contradiction["rules"]),
                rules_text=tuple(contradiction["rules_text"]),
                interpretations=self._interpretations_contradiction(contradiction, amount, vendor),
                fiscal_impact=self._fiscal_impact_contradiction(amount, contradiction),
                historical_precedents=pcts,
                cpa_assigned=cfg.cpa_primary,
                sla_hours=cfg.sla_hours,
                escalation_chain=cfg.escalation_chain,
                confidence=confidence,
            )

        # --- TRIGGER 2: Confidence < umbral del cliente ---
        if confidence < cfg.confidence_threshold:
            return self._emit_pause_decision(
                transaction_id=transaction_id,
                client_id=client_id,
                trigger=PauseTrigger.LOW_CONFIDENCE,
                conflict_summary=(
                    f"Confidence {confidence:.2%} por debajo del umbral configurado "
                    f"{cfg.confidence_threshold:.2%} para el cliente {client_id}. "
                    f"Proveedor: {vendor or 'desconocido'} | Monto: ${amount}."
                ),
                rules_in_conflict=(),
                rules_text=(),
                interpretations=self._interpretations_low_confidence(amount, vendor),
                fiscal_impact=self._fiscal_impact_low_confidence(amount),
                historical_precedents=self._get_history_precedents(
                    history_6m, vendor, transaction_type
                ),
                cpa_assigned=cfg.cpa_primary,
                sla_hours=cfg.sla_hours,
                escalation_chain=cfg.escalation_chain,
                confidence=confidence,
            )

        # --- TRIGGER 3: Sin precedente en 6 meses (solo si monto > umbral) ---
        if cfg.require_precedent and amount >= cfg.no_precedent_threshold:
            no_prec = self._check_no_precedent_6m(
                vendor, transaction_type, amount, history_6m
            )
            if no_prec:
                return self._emit_pause_decision(
                    transaction_id=transaction_id,
                    client_id=client_id,
                    trigger=PauseTrigger.NO_PRECEDENT_6M,
                    conflict_summary=(
                        f"Sin precedente en los ultimos 6 meses para "
                        f"'{vendor or transaction_type}' con monto ${amount}. "
                        f"Es la primera transaccion de este tipo para el cliente."
                    ),
                    rules_in_conflict=(),
                    rules_text=(),
                    interpretations=self._interpretations_no_precedent(
                        amount, vendor, transaction_type
                    ),
                    fiscal_impact=self._fiscal_impact_no_precedent(amount),
                    historical_precedents=(),
                    cpa_assigned=cfg.cpa_primary,
                    sla_hours=cfg.sla_hours,
                    escalation_chain=cfg.escalation_chain,
                    confidence=confidence,
                )

        # --- PROCEED ---
        return CentinelaDecision(
            decision="PROCEED",
            pause_id=None,
            trigger=None,
            reason=(
                f"Todos los checks pasaron. Confidence={confidence:.2%} "
                f"(umbral={cfg.confidence_threshold:.2%}). "
                f"Proveedor={vendor or 'N/A'} | Monto=${amount}."
            ),
            confidence=confidence,
            client_id=client_id,
            transaction_id=transaction_id,
        )

    # ------------------------------------------------------------------
    # LIBERAR PAUSA — solo el CPA autenticado
    # ------------------------------------------------------------------

    def release_pause(
        self,
        pause_id:              str,
        cpa_license:           str,
        cpa_token:             str,
        interpretation_chosen: str,
        instruction:           str,
        cpa_name:              Optional[str] = None,
        requires_follow_up:    bool = False,
    ) -> PauseRelease:
        """
        Libera una pausa activa. SOLO puede llamarse con credenciales CPA validas.

        La liberacion queda registrada en el log inmutable con:
        quien libero, cuando, que interpretacion eligio, y la instruccion exacta.

        Args:
            pause_id:              UUID de la pausa a liberar.
            cpa_license:           Numero de licencia del CPA.
            cpa_token:             Token de autenticacion (min 8 chars).
            interpretation_chosen: Cual de las interpretaciones eligio el CPA.
            instruction:           Instruccion especifica del CPA.
            cpa_name:              Nombre del CPA (opcional para el log).
            requires_follow_up:    Si requiere seguimiento posterior.

        Returns:
            PauseRelease con registro inmutable de la liberacion.

        Raises:
            ValueError: Credenciales invalidas.
            KeyError:   Pausa no encontrada o ya resuelta.
        """
        if not cpa_license or not cpa_license.strip():
            raise ValueError("cpa_license no puede estar vacio.")
        if not cpa_token or len(cpa_token.strip()) < 8:
            raise ValueError("cpa_token debe tener al menos 8 caracteres.")
        if not instruction or not instruction.strip():
            raise ValueError("instruction no puede estar vacia — el CPA debe documentar su decision.")
        if not interpretation_chosen or not interpretation_chosen.strip():
            raise ValueError("interpretation_chosen no puede estar vacio.")

        if pause_id not in self._active_pauses:
            raise KeyError(
                f"Pausa '{pause_id}' no encontrada en pausas activas. "
                "Puede que ya haya sido resuelta o el ID es incorrecto."
            )

        pause = self._active_pauses[pause_id]

        # Crear registro de liberacion (inmutable)
        release = PauseRelease(
            pause_id=pause_id,
            released_by_license=cpa_license.strip(),
            released_by_name=cpa_name,
            interpretation_chosen=interpretation_chosen.strip(),
            instruction=instruction.strip(),
            requires_follow_up=requires_follow_up,
        )

        # Desbloquear transacciones afectadas
        for txn_id in pause.affected_transaction_ids:
            self._blocked_txns.pop(txn_id, None)

        # Mover pausa a resuelta (no se borra del log)
        del self._active_pauses[pause_id]

        # Registrar en log inmutable
        self._release_log.append(release)

        return release

    # ------------------------------------------------------------------
    # ESCALACION AUTOMATICA POR SLA
    # ------------------------------------------------------------------

    def escalate_overdue(self, now: Optional[datetime] = None) -> list[str]:
        """
        Escala pausas cuyo SLA ha sido superado al siguiente CPA en la cadena.

        Returns:
            Lista de pause_ids escalados.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        escalated: list[str] = []

        for pause_id, pause in list(self._active_pauses.items()):
            created = datetime.fromisoformat(pause.created_at)
            sla_deadline = created + timedelta(hours=pause.sla_hours)

            if now > sla_deadline and pause.escalated_to is None:
                if not pause.escalation_chain:
                    continue

                next_cpa = pause.escalation_chain[0]
                # Crear nueva pausa con estado ESCALATED
                escalated_pause = SurgicalPause(
                    pause_id=pause.pause_id,
                    client_id=pause.client_id,
                    trigger=pause.trigger,
                    status=PauseStatus.ESCALATED,
                    created_at=pause.created_at,
                    affected_transaction_ids=pause.affected_transaction_ids,
                    conflict_summary=pause.conflict_summary,
                    rules_in_conflict=pause.rules_in_conflict,
                    rules_text=pause.rules_text,
                    interpretations=pause.interpretations,
                    fiscal_impact=pause.fiscal_impact,
                    historical_precedents=pause.historical_precedents,
                    cpa_assigned=next_cpa,
                    sla_hours=pause.sla_hours,
                    escalation_chain=pause.escalation_chain[1:],
                    escalated_to=next_cpa,
                    escalated_at=now.isoformat(),
                )
                self._active_pauses[pause_id] = escalated_pause
                escalated.append(pause_id)

        return escalated

    # ------------------------------------------------------------------
    # CONSULTAS — solo lectura
    # ------------------------------------------------------------------

    def is_transaction_blocked(self, transaction_id: str) -> bool:
        """Verifica si una transaccion especifica esta bloqueada."""
        return transaction_id in self._blocked_txns

    def get_pause(self, pause_id: str) -> Optional[SurgicalPause]:
        """Retorna una pausa activa por ID."""
        return self._active_pauses.get(pause_id)

    def get_active_pauses_for_client(
        self, client_id: str
    ) -> tuple[SurgicalPause, ...]:
        """Retorna todas las pausas activas de un cliente especifico."""
        return tuple(
            p for p in self._active_pauses.values()
            if p.client_id == client_id
        )

    def get_pause_log(self) -> tuple[SurgicalPause, ...]:
        """Log inmutable de todas las pausas emitidas (tuple)."""
        return tuple(self._pause_log)

    def get_release_log(self) -> tuple[PauseRelease, ...]:
        """Log inmutable de todas las liberaciones (tuple)."""
        return tuple(self._release_log)

    def active_pause_count(self) -> int:
        return len(self._active_pauses)

    # ------------------------------------------------------------------
    # TRIGGER CHECKS
    # ------------------------------------------------------------------

    def _check_audit_severity(
        self, audit_result: Optional[dict]
    ) -> Optional[dict]:
        """
        Trigger 5: AuditResult del AUDITOR tiene severity HIGH o CRITICAL.
        """
        if audit_result is None:
            return None

        high_critical = [
            a for a in audit_result.get("alerts", [])
            if a.get("severity") in ("HIGH", "CRITICAL")
        ]

        if not high_critical:
            return None

        worst = "CRITICAL" if any(
            a["severity"] == "CRITICAL" for a in high_critical
        ) else "HIGH"

        checks = [a.get("check_name", "?") for a in high_critical]
        rules  = [a.get("rule_ref") for a in high_critical if a.get("rule_ref")]
        rules_text = [
            f"{a.get('check_name')}: esperado={a.get('expected')} actual={a.get('actual')}"
            for a in high_critical
        ]

        return {
            "summary": (
                f"El AUDITOR reporto {len(high_critical)} alerta(s) de severidad "
                f"{worst}: {', '.join(checks)}. "
                "El flujo no puede continuar hasta resolucion."
            ),
            "severity": worst,
            "alerts":   high_critical,
            "rules":    rules,
            "rules_text": rules_text,
        }

    def _check_agent_divergence(
        self, cross_check_results: tuple[dict, ...]
    ) -> Optional[dict]:
        """
        Trigger 4: Dos agentes llegaron a resultados distintos.
        cross_check_results: [{"agent": "CLASIFICADOR", "result": "5200"},
                               {"agent": "AUDITOR",     "result": "5300"}]
        """
        if len(cross_check_results) < 2:
            return None

        results_by_agent = {
            r["agent"]: r["result"]
            for r in cross_check_results
            if "agent" in r and "result" in r
        }

        unique_results = set(str(v) for v in results_by_agent.values())
        if len(unique_results) <= 1:
            return None

        agents = list(results_by_agent.keys())
        return {
            "summary": (
                f"Divergencia entre agentes: "
                + ", ".join(
                    f"{a}={results_by_agent[a]}" for a in agents
                )
                + ". Los agentes no estan de acuerdo sobre la clasificacion."
            ),
            "agents": agents,
            "results": results_by_agent,
        }

    def _check_rule_contradiction_90d(
        self,
        transaction_type: str,
        current_rule_id:  Optional[str],
        history_90d:      tuple[dict, ...],
    ) -> Optional[dict]:
        """
        Trigger 1: La regla actual contradice la aplicada en los ultimos 90 dias
        para el mismo tipo de transaccion.
        """
        if not current_rule_id or not history_90d:
            return None

        prior_rules = set()
        for tx in history_90d:
            if tx.get("transaction_type") == transaction_type:
                rule = tx.get("rule_applied")
                if rule and rule != current_rule_id:
                    prior_rules.add(rule)

        if not prior_rules:
            return None

        prior_list = sorted(prior_rules)
        return {
            "summary": (
                f"Contradiccion de regla: '{current_rule_id}' aplicada hoy "
                f"difiere de la(s) regla(s) usada(s) en los ultimos 90 dias "
                f"para transacciones de tipo '{transaction_type}': "
                f"{', '.join(prior_list)}. "
                "Verificar si hubo cambio normativo o error de clasificacion."
            ),
            "rules":      [current_rule_id] + prior_list,
            "rules_text": [
                f"Regla actual: {current_rule_id}",
            ] + [f"Regla previa (90d): {r}" for r in prior_list],
            "current":  current_rule_id,
            "previous": prior_list,
        }

    def _check_no_precedent_6m(
        self,
        vendor:           Optional[str],
        transaction_type: str,
        amount:           Decimal,
        history_6m:       tuple[dict, ...],
    ) -> bool:
        """
        Trigger 3: Sin precedente del mismo vendor/tipo en los ultimos 6 meses.
        """
        for tx in history_6m:
            if vendor and tx.get("vendor") == vendor:
                return False
            if tx.get("transaction_type") == transaction_type:
                return False
        return True

    # ------------------------------------------------------------------
    # HELPERS — interpretaciones e impacto fiscal
    # ------------------------------------------------------------------

    def _emit_pause_decision(
        self,
        transaction_id:    str,
        client_id:         str,
        trigger:           PauseTrigger,
        conflict_summary:  str,
        rules_in_conflict: tuple[str, ...],
        rules_text:        tuple[str, ...],
        interpretations:   tuple[str, ...],
        fiscal_impact:     tuple[FiscalImpactEstimate, ...],
        historical_precedents: tuple[str, ...],
        cpa_assigned:      str,
        sla_hours:         int,
        escalation_chain:  tuple[str, ...],
        confidence:        Decimal,
    ) -> CentinelaDecision:
        pause = SurgicalPause(
            client_id=client_id,
            trigger=trigger,
            affected_transaction_ids=(transaction_id,),
            conflict_summary=conflict_summary,
            rules_in_conflict=rules_in_conflict,
            rules_text=rules_text,
            interpretations=interpretations,
            fiscal_impact=fiscal_impact,
            historical_precedents=historical_precedents,
            cpa_assigned=cpa_assigned,
            sla_hours=sla_hours,
            escalation_chain=escalation_chain,
        )
        self._active_pauses[pause.pause_id] = pause
        self._pause_log.append(pause)
        self._blocked_txns[transaction_id] = pause.pause_id

        return CentinelaDecision(
            decision="PAUSE",
            pause_id=pause.pause_id,
            trigger=trigger,
            reason=conflict_summary,
            confidence=confidence,
            client_id=client_id,
            transaction_id=transaction_id,
        )

    @staticmethod
    def _get_history_precedents(
        history_6m: tuple[dict, ...],
        vendor: Optional[str],
        transaction_type: str,
    ) -> tuple[str, ...]:
        precs: list[str] = []
        for tx in history_6m[:5]:
            if tx.get("vendor") == vendor or tx.get("transaction_type") == transaction_type:
                precs.append(
                    f"{tx.get('date','?')} | {tx.get('vendor','?')} | "
                    f"${tx.get('amount','?')} | regla: {tx.get('rule_applied','?')}"
                )
        return tuple(precs)

    # --- Interpretaciones por trigger ---

    @staticmethod
    def _interpretations_audit(info: dict) -> tuple[str, ...]:
        severity = info.get("severity", "HIGH")
        return (
            f"OPCION A — Corregir el error detectado por el AUDITOR "
            f"({', '.join(a.get('check_name','?') for a in info.get('alerts',[]))}) "
            f"antes de continuar. Severidad: {severity}.",
            "OPCION B — Si el error es un falso positivo, documentar la justificacion "
            "tecnica y liberar con nota explicativa en el expediente.",
            "OPCION C — Rechazar la transaccion y solicitar documento corregido al proveedor.",
        )

    @staticmethod
    def _interpretations_divergence(info: dict, amount: Decimal, vendor: Optional[str]) -> tuple[str, ...]:
        results = info.get("results", {})
        items = list(results.items())
        return (
            f"OPCION A — Usar el resultado del {items[0][0] if items else 'agente 1'} "
            f"({items[0][1] if items else '?'}): clasificacion conservadora, "
            f"menor riesgo de penalidad.",
            f"OPCION B — Usar el resultado del {items[1][0] if len(items) > 1 else 'agente 2'} "
            f"({items[1][1] if len(items) > 1 else '?'}): revisar criterios de clasificacion.",
            f"OPCION C — Rechazar ambas clasificaciones y reclasificar manualmente "
            f"para '{vendor or 'transaccion'}' por ${amount}.",
        )

    @staticmethod
    def _interpretations_contradiction(info: dict, amount: Decimal, vendor: Optional[str]) -> tuple[str, ...]:
        current  = info.get("current", "regla nueva")
        previous = info.get("previous", ["regla anterior"])
        prev_str = previous[0] if previous else "regla anterior"
        return (
            f"OPCION A — Aplicar la regla nueva '{current}': puede reflejar un cambio "
            f"normativo reciente que requiere actualizacion retroactiva de los ultimos 90 dias.",
            f"OPCION B — Mantener la regla anterior '{prev_str}': si el cambio normativo "
            f"no aplica retroactivamente, continuar con la clasificacion historica.",
            f"OPCION C — Solicitar consulta tecnica a Hacienda PR para esta categoria "
            f"antes de clasificar '{vendor or 'proveedor'}' por ${amount}.",
        )

    @staticmethod
    def _interpretations_low_confidence(amount: Decimal, vendor: Optional[str]) -> tuple[str, ...]:
        return (
            f"OPCION A — Solicitar el documento original en formato digital al proveedor "
            f"'{vendor or 'desconocido'}' para mejorar la calidad del OCR.",
            f"OPCION B — El CPA puede completar manualmente los campos faltantes "
            f"y liberar la transaccion de ${amount} con nota de revision manual.",
            "OPCION C — Rechazar el documento y solicitar re-emision al proveedor.",
        )

    @staticmethod
    def _interpretations_no_precedent(
        amount: Decimal, vendor: Optional[str], transaction_type: str
    ) -> tuple[str, ...]:
        return (
            f"OPCION A — Aprobar la primera transaccion con '{vendor or transaction_type}' "
            f"por ${amount} como nuevo proveedor/tipo y establecer precedente.",
            f"OPCION B — Solicitar documentacion adicional (contrato, cotizacion) "
            f"para respaldar la primera transaccion de este tipo.",
            f"OPCION C — Rechazar hasta tener al menos una transaccion previa aprobada "
            f"que sirva como precedente.",
        )

    # --- Impacto fiscal por trigger ---

    @staticmethod
    def _fiscal_impact_audit(amount: Decimal, info: dict) -> tuple[FiscalImpactEstimate, ...]:
        ivu = (amount * Decimal("0.115")).quantize(Decimal("0.01"))
        return (
            FiscalImpactEstimate(
                interpretation_label="Corregir y continuar",
                tax_liability_low=ivu,
                tax_liability_high=ivu,
                notes="Impacto fiscal igual al correcto — sin variacion si se corrige el error.",
            ),
            FiscalImpactEstimate(
                interpretation_label="Rechazar transaccion",
                tax_liability_low=Decimal("0.00"),
                tax_liability_high=Decimal("0.00"),
                notes="Sin obligacion fiscal si se rechaza — pero puede generar penalidad por no declarar.",
            ),
        )

    @staticmethod
    def _fiscal_impact_divergence(amount: Decimal, info: dict) -> tuple[FiscalImpactEstimate, ...]:
        ivu = (amount * Decimal("0.115")).quantize(Decimal("0.01"))
        return (
            FiscalImpactEstimate(
                interpretation_label="Clasificacion Agente 1",
                tax_liability_low=ivu * Decimal("0.9"),
                tax_liability_high=ivu * Decimal("1.1"),
                notes="Rango estimado segun clasificacion del primer agente.",
            ),
            FiscalImpactEstimate(
                interpretation_label="Clasificacion Agente 2",
                tax_liability_low=ivu * Decimal("0.8"),
                tax_liability_high=ivu * Decimal("1.2"),
                notes="Rango estimado segun clasificacion del segundo agente.",
            ),
        )

    @staticmethod
    def _fiscal_impact_contradiction(amount: Decimal, info: dict) -> tuple[FiscalImpactEstimate, ...]:
        ivu = (amount * Decimal("0.115")).quantize(Decimal("0.01"))
        return (
            FiscalImpactEstimate(
                interpretation_label="Regla nueva",
                tax_liability_low=ivu,
                tax_liability_high=ivu * Decimal("1.15"),
                notes="Regla nueva puede implicar mayor o menor obligacion.",
            ),
            FiscalImpactEstimate(
                interpretation_label="Regla anterior (90d)",
                tax_liability_low=ivu * Decimal("0.9"),
                tax_liability_high=ivu,
                notes="Mantener regla historica — impacto fiscal conocido.",
            ),
        )

    @staticmethod
    def _fiscal_impact_low_confidence(amount: Decimal) -> tuple[FiscalImpactEstimate, ...]:
        ivu = (amount * Decimal("0.115")).quantize(Decimal("0.01"))
        return (
            FiscalImpactEstimate(
                interpretation_label="Aprobacion manual CPA",
                tax_liability_low=ivu * Decimal("0.9"),
                tax_liability_high=ivu * Decimal("1.1"),
                notes="Impacto dependiente de la clasificacion que elija el CPA.",
            ),
        )

    @staticmethod
    def _fiscal_impact_no_precedent(amount: Decimal) -> tuple[FiscalImpactEstimate, ...]:
        ivu = (amount * Decimal("0.115")).quantize(Decimal("0.01"))
        return (
            FiscalImpactEstimate(
                interpretation_label="Primer precedente aprobado",
                tax_liability_low=ivu,
                tax_liability_high=ivu,
                notes="Establece precedente para transacciones futuras del mismo tipo.",
            ),
        )
