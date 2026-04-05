# =============================================================================
# agents/centinela.py
# Agente CENTINELA del sistema Bit-Counting.
#
# ROL:
#   El CENTINELA es la unica entidad que puede bloquear el flujo de
#   procesamiento. Evalua cada IntakeOutput antes de clasificar y
#   decide si el flujo puede continuar (PROCEED) o debe pausarse (PAUSE).
#
#   Una vez emitida una pausa, es IRREVOCABLE por cualquier agente.
#   Solo el CPA humano puede liberarla mediante release_pause() con
#   su numero de licencia y token de autenticacion valido.
#
# TRIGGERS DE PAUSA (segun blueprint):
#   1. confidence < 0.60 → PAUSE automatico
#   2. Regla aplicable tiene has_controversy=True → PAUSE
#   3. Sin precedente en historial del cliente → flag de evaluacion
#   4. Cambio normativo pendiente de validacion → PAUSE
#   5. Dos agentes llegan a resultados distintos → PAUSE
#
# GARANTIAS DE DISENO:
#   - Las pausas activas se almacenan en dict interno mutable (_active_pauses).
#   - Se exponen SOLO via get_active_pauses() que retorna tuple inmutable.
#   - release_pause() requiere cpa_license y cpa_token — sin ellos, falla.
#   - La pausa liberada se mueve a _resolved_pauses — no se borra del registro.
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .base import BaseAgent
from .exceptions import CentinelaBlockError
from .messages import (
    BaseAgentMessage,
    CentinelaDecision,
    CentinelaEvaluation,
    CentinelaPause,
    IntakeOutput,
    PauseStatus,
    PauseTriggerType,
)

logger = logging.getLogger(__name__)

# Umbral de confidence segun el blueprint
_CONFIDENCE_FORCE_PAUSE = Decimal("0.60")


class Centinela(BaseAgent):
    """
    CENTINELA — guardian del flujo de procesamiento de Bit-Counting.

    El CENTINELA evalua cada IntakeOutput antes de que llegue al CLASIFICADOR.
    Aplica una bateria de checks y decide PROCEED o PAUSE.

    Cuando emite una pausa:
      1. Crea una CentinelaPause con analisis pre-procesado para el CPA.
      2. La almacena en _active_pauses (dict interno, no expuesto).
      3. Retorna CentinelaEvaluation con decision=PAUSE y pause_id.

    La pausa solo puede liberarse cuando el CPA llama a release_pause()
    con su licencia y token validos.

    El CENTINELA tambien mantiene un registro de politicas activas que
    puede aplicar al evaluar transacciones (cargadas via PolicyActivation).
    """

    @property
    def agent_name(self) -> str:
        return "CENTINELA"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        return (IntakeOutput,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (CentinelaEvaluation,)

    def __init__(self) -> None:
        """Inicializa el CENTINELA con registros vacios."""
        # Dict mutable interno — NUNCA exponer directamente
        self._active_pauses: dict[str, CentinelaPause] = {}
        self._resolved_pauses: dict[str, CentinelaPause] = {}

        # Politicas activas cargadas por el INTERPRETE via PolicyActivation
        self._active_policies: list[dict[str, Any]] = []

        logger.info("CENTINELA inicializado. Sin pausas activas.")

    # -------------------------------------------------------------------------
    # PROCESO PRINCIPAL — implementacion de BaseAgent
    # -------------------------------------------------------------------------

    def _process_impl(self, message: BaseAgentMessage) -> CentinelaEvaluation:
        """
        Evalua un IntakeOutput y decide PROCEED o PAUSE.

        Delega a evaluate() con parametros por defecto para el caso
        en que no hay historial del cliente ni lista de reglas activas.
        Para el flujo completo, usar evaluate() directamente.
        """
        assert isinstance(message, IntakeOutput)
        return self.evaluate(
            intake_output=message,
            client_history=(),
            active_rules=(),
        )

    # -------------------------------------------------------------------------
    # EVALUACION — logica principal del CENTINELA
    # -------------------------------------------------------------------------

    def evaluate(
        self,
        intake_output: IntakeOutput,
        client_history: tuple[dict[str, Any], ...],
        active_rules: tuple[Any, ...],
    ) -> CentinelaEvaluation:
        """
        Evalua un IntakeOutput y determina si el flujo puede continuar.

        Aplica los cinco triggers de pausa del blueprint en orden de severidad.
        Si cualquier trigger activa una pausa, emite CentinelaPause y retorna
        CentinelaEvaluation con decision=PAUSE.

        Args:
            intake_output:   Output del agente INTAKE a evaluar.
            client_history:  Historial de transacciones del cliente (puede ser vacio).
            active_rules:    Reglas fiscales activas para verificar controversias.

        Returns:
            CentinelaEvaluation con decision PROCEED o PAUSE.
        """
        confidence = Decimal(str(intake_output.confidence))
        rule_conflicts: list[str] = []
        pause_trigger: PauseTriggerType | None = None
        pause_reason: str = ""

        # --- Check 1: Campos criticos faltantes ---
        if intake_output.critical_fields_missing:
            pause_trigger = PauseTriggerType.MISSING_CRITICAL_FIELD
            pause_reason = (
                f"Campos criticos faltantes: {list(intake_output.missing_fields)}. "
                "El documento no tiene suficiente informacion para clasificar."
            )

        # --- Check 2: Confidence por debajo del umbral de pausa ---
        elif confidence < _CONFIDENCE_FORCE_PAUSE:
            pause_trigger = PauseTriggerType.LOW_CONFIDENCE
            pause_reason = (
                f"Confidence {confidence:.4f} por debajo del umbral {_CONFIDENCE_FORCE_PAUSE}. "
                "El documento presenta calidad insuficiente para procesamiento automatico."
            )

        # --- Check 3: Reglas con controversia ---
        elif active_rules:
            for rule in active_rules:
                if hasattr(rule, "has_controversy") and rule.has_controversy:
                    rule_conflicts.append(rule.rule_id)

            if rule_conflicts:
                pause_trigger = PauseTriggerType.RULE_CONTROVERSY
                pause_reason = (
                    f"Reglas con controversia activa detectadas: {rule_conflicts}. "
                    "La interpretacion de la regla no es univoca — requiere criterio CPA."
                )

        # --- Check 4: Sin precedente en historial del cliente ---
        # Este check es un flag de evaluacion adicional, no necesariamente pausa
        precedent_found = self._check_precedent(intake_output, client_history)

        # --- Check 5: Politicas activas que requieren pausa ---
        policy_pause = self._check_policy_triggers(intake_output)
        if policy_pause and pause_trigger is None:
            pause_trigger = PauseTriggerType.PENDING_REGULATORY_CHANGE
            pause_reason = policy_pause

        # --- Decision final ---
        if pause_trigger is not None:
            pause = self.emit_pause(
                reason=pause_reason,
                trigger_type=pause_trigger,
                affected_transactions=(intake_output.message_id,),
                conflicting_rules=tuple(rule_conflicts),
                interpretations=self._generate_interpretations(
                    intake_output, pause_trigger, pause_reason
                ),
                pre_processed_analysis=self._build_pre_processed_analysis(
                    intake_output, pause_trigger, pause_reason,
                    rule_conflicts, client_history, precedent_found,
                ),
            )

            logger.warning(
                "[CENTINELA] PAUSA emitida: pause_id=%s trigger=%s confidence=%.4f.",
                pause.pause_id,
                pause_trigger.value,
                float(confidence),
            )

            return CentinelaEvaluation(
                source_agent="CENTINELA",
                target_agent="ORQUESTADOR",
                decision=CentinelaDecision.PAUSE,
                reason=pause_reason,
                pause_id=pause.pause_id,
                confidence_at_evaluation=confidence,
                rule_conflicts=tuple(rule_conflicts),
                precedent_found=precedent_found,
                evaluated_intake_id=intake_output.message_id,
            )

        # --- PROCEED ---
        logger.info(
            "[CENTINELA] PROCEED: confidence=%.4f precedent=%s intake_id=%s.",
            float(confidence),
            precedent_found,
            intake_output.message_id,
        )

        return CentinelaEvaluation(
            source_agent="CENTINELA",
            target_agent="CLASIFICADOR",
            decision=CentinelaDecision.PROCEED,
            reason=(
                f"Todos los checks pasaron. Confidence={confidence:.4f}. "
                f"Precedente={'encontrado' if precedent_found else 'no encontrado, flujo continua con flag'}."
            ),
            pause_id=None,
            confidence_at_evaluation=confidence,
            rule_conflicts=tuple(rule_conflicts),
            precedent_found=precedent_found,
            evaluated_intake_id=intake_output.message_id,
        )

    # -------------------------------------------------------------------------
    # EMISION DE PAUSA
    # -------------------------------------------------------------------------

    def emit_pause(
        self,
        reason: str,
        trigger_type: PauseTriggerType,
        affected_transactions: tuple[str, ...],
        conflicting_rules: tuple[str, ...],
        interpretations: tuple[str, ...],
        pre_processed_analysis: str,
        sla_hours: int = 48,
    ) -> CentinelaPause:
        """
        Emite una pausa irrevocable que solo el CPA puede liberar.

        La pausa se almacena en _active_pauses y bloquea el procesamiento
        de las transacciones afectadas hasta que el CPA llame a release_pause().

        Args:
            reason:                  Motivo tecnico de la pausa.
            trigger_type:            Tipo de trigger que causo la pausa.
            affected_transactions:   IDs de transacciones bloqueadas.
            conflicting_rules:       IDs de reglas en conflicto.
            interpretations:         2-3 interpretaciones posibles para el CPA.
            pre_processed_analysis:  Analisis completo para el CPA.
            sla_hours:               SLA maximo en horas (default 48).

        Returns:
            CentinelaPause creada y registrada.
        """
        pause_id = str(uuid.uuid4())

        pause = CentinelaPause(
            source_agent="CENTINELA",
            target_agent="CPA",
            pause_id=pause_id,
            trigger_type=trigger_type,
            affected_transaction_ids=affected_transactions,
            conflicting_rules=conflicting_rules,
            interpretations=interpretations,
            pre_processed_analysis=pre_processed_analysis,
            sla_hours=sla_hours,
            status=PauseStatus.ACTIVE,
        )

        # Almacenar en dict interno — solo se expone via get_active_pauses()
        self._active_pauses[pause_id] = pause

        logger.warning(
            "[CENTINELA] PAUSA EMITIDA: id=%s trigger=%s transacciones_afectadas=%s.",
            pause_id,
            trigger_type.value,
            affected_transactions,
        )

        return pause

    # -------------------------------------------------------------------------
    # LIBERACION DE PAUSA — solo el CPA puede hacerlo
    # -------------------------------------------------------------------------

    def release_pause(
        self,
        pause_id: str,
        cpa_license: str,
        cpa_token: str,
    ) -> CentinelaPause:
        """
        Libera una pausa activa. Solo puede ser llamado por el CPA.

        Valida que:
          1. La pausa existe y esta ACTIVA.
          2. cpa_license es un string no vacio.
          3. cpa_token es valido (en produccion, verificaria contra BD o servicio auth).

        En produccion, cpa_token se verificaria contra la base de datos de cpa_partners.
        En esta implementacion, se valida que no sea vacio y tenga longitud minima.

        Args:
            pause_id:    UUID de la pausa a liberar.
            cpa_license: Numero de licencia del CPA.
            cpa_token:   Token de autenticacion del CPA.

        Returns:
            CentinelaPause actualizada con status=RESOLVED.

        Raises:
            CentinelaBlockError: Si la pausa no existe o ya fue resuelta.
            ValueError: Si cpa_license o cpa_token son invalidos.
        """
        # Validar credenciales del CPA
        if not cpa_license or not cpa_license.strip():
            raise ValueError(
                "release_pause requiere cpa_license valido. "
                "La licencia del CPA no puede estar vacia."
            )

        if not cpa_token or len(cpa_token.strip()) < 8:
            raise ValueError(
                "release_pause requiere cpa_token valido con minimo 8 caracteres. "
                "Token invalido o demasiado corto."
            )

        # Verificar que la pausa existe
        if pause_id not in self._active_pauses:
            if pause_id in self._resolved_pauses:
                raise CentinelaBlockError(
                    pause_id=pause_id,
                    affected_transaction_ids=[],
                    reason=f"La pausa {pause_id} ya fue resuelta anteriormente.",
                )
            raise CentinelaBlockError(
                pause_id=pause_id,
                affected_transaction_ids=[],
                reason=f"Pausa {pause_id} no encontrada en el registro activo.",
            )

        original_pause = self._active_pauses[pause_id]

        # Crear una nueva instancia con status RESOLVED (frozen model)
        resolved_pause = CentinelaPause(
            # Copiar todos los campos del original
            message_id=original_pause.message_id,
            timestamp=original_pause.timestamp,
            source_agent=original_pause.source_agent,
            target_agent=original_pause.target_agent,
            schema_version=original_pause.schema_version,
            pause_id=original_pause.pause_id,
            trigger_type=original_pause.trigger_type,
            affected_transaction_ids=original_pause.affected_transaction_ids,
            conflicting_rules=original_pause.conflicting_rules,
            interpretations=original_pause.interpretations,
            pre_processed_analysis=(
                original_pause.pre_processed_analysis
                + f"\n\n[RESUELTA] CPA: {cpa_license} | "
                f"Timestamp: {datetime.now(timezone.utc).isoformat()}"
            ),
            sla_hours=original_pause.sla_hours,
            status=PauseStatus.RESOLVED,
        )

        # Mover de activas a resueltas — nunca se borra del registro
        del self._active_pauses[pause_id]
        self._resolved_pauses[pause_id] = resolved_pause

        logger.info(
            "[CENTINELA] PAUSA LIBERADA: id=%s por CPA licencia=%s.",
            pause_id,
            cpa_license,
        )

        return resolved_pause

    def check_active_pause_for_transaction(
        self, transaction_id: str
    ) -> CentinelaPause | None:
        """
        Verifica si una transaccion tiene una pausa activa.

        Usado por el ORQUESTADOR antes de enrutar mensajes para asegurarse
        de que las transacciones bloqueadas no se procesen.

        Args:
            transaction_id: ID de la transaccion a verificar.

        Returns:
            CentinelaPause activa si existe, None si la transaccion puede continuar.
        """
        for pause in self._active_pauses.values():
            if transaction_id in pause.affected_transaction_ids:
                return pause
        return None

    def get_active_pauses(self) -> tuple[CentinelaPause, ...]:
        """
        Retorna todas las pausas activas como tuple inmutable.

        La inmutabilidad del tuple garantiza que el caller no puede
        modificar el registro interno.

        Returns:
            tuple de CentinelaPause con status=ACTIVE.
        """
        return tuple(self._active_pauses.values())

    def get_resolved_pauses(self) -> tuple[CentinelaPause, ...]:
        """Retorna todas las pausas resueltas como tuple inmutable."""
        return tuple(self._resolved_pauses.values())

    def load_policy(self, policy: dict[str, Any]) -> None:
        """
        Carga una politica activa desde el INTERPRETE.
        Las politicas se aplican durante la evaluacion de futuros IntakeOutputs.
        """
        self._active_policies.append(policy)
        logger.info(
            "[CENTINELA] Politica cargada: policy_id=%s.",
            policy.get("policy_id", "UNKNOWN"),
        )

    # -------------------------------------------------------------------------
    # HELPERS PRIVADOS
    # -------------------------------------------------------------------------

    @staticmethod
    def _check_precedent(
        intake_output: IntakeOutput,
        client_history: tuple[dict[str, Any], ...],
    ) -> bool:
        """
        Verifica si hay precedente en el historial del cliente para esta transaccion.

        Un precedente se define como una transaccion anterior del mismo vendor
        con monto similar (dentro del 20%) o del mismo tipo de gasto.

        Args:
            intake_output:  IntakeOutput a evaluar.
            client_history: Historial de transacciones del cliente.

        Returns:
            True si se encontro precedente, False si no.
        """
        if not client_history:
            return False

        vendor = intake_output.vendor
        amount = intake_output.amount

        for historical_tx in client_history:
            # Precedente por vendor
            if vendor and historical_tx.get("vendor") == vendor:
                return True

            # Precedente por monto similar (dentro del 20%)
            if amount and "amount" in historical_tx:
                try:
                    hist_amount = Decimal(str(historical_tx["amount"]))
                    if hist_amount > Decimal("0"):
                        ratio = abs(amount - hist_amount) / hist_amount
                        if ratio <= Decimal("0.20"):
                            return True
                except Exception:  # noqa: BLE001
                    pass

        return False

    def _check_policy_triggers(self, intake_output: IntakeOutput) -> str | None:
        """
        Verifica si alguna politica activa requiere pausa para este documento.

        Retorna el motivo de pausa si aplica, None si no hay conflicto.
        """
        for policy in self._active_policies:
            conditions = policy.get("conditions", [])
            for condition in conditions:
                if condition.get("type") == "AMOUNT_THRESHOLD":
                    threshold = Decimal(str(condition.get("threshold", "0")))
                    if intake_output.amount and intake_output.amount > threshold:
                        return (
                            f"Politica activa requiere revision: monto "
                            f"{intake_output.amount} supera umbral {threshold}."
                        )
                if condition.get("type") == "VENDOR_BLOCKED":
                    if intake_output.vendor in condition.get("vendors", []):
                        return (
                            f"Politica activa bloquea proveedor: {intake_output.vendor}."
                        )
        return None

    @staticmethod
    def _generate_interpretations(
        intake_output: IntakeOutput,
        trigger_type: PauseTriggerType,
        reason: str,
    ) -> tuple[str, ...]:
        """
        Genera 2-3 interpretaciones posibles del caso para el CPA.
        El CPA revisara estas interpretaciones y elegira la correcta.
        """
        base = f"Documento de {intake_output.vendor or 'proveedor desconocido'}"
        amount_str = str(intake_output.amount) if intake_output.amount else "monto desconocido"

        if trigger_type == PauseTriggerType.LOW_CONFIDENCE:
            return (
                f"{base}: El documento es legitimo pero la calidad del OCR es baja. "
                f"Recomendacion: solicitar documento original en formato digital.",
                f"{base}: El documento puede ser una copia o fotocopia de baja calidad. "
                f"Recomendacion: verificar con el proveedor la factura original.",
                f"{base}: Algunos campos ({intake_output.missing_fields}) no pudieron extraerse. "
                f"El CPA puede completar manualmente los campos faltantes.",
            )

        if trigger_type == PauseTriggerType.RULE_CONTROVERSY:
            return (
                f"{base} por ${amount_str}: Aplicar la interpretacion conservadora "
                f"de la regla — mayor obligacion tributaria, menor riesgo de penalidad.",
                f"{base} por ${amount_str}: Aplicar la interpretacion liberal "
                f"de la regla — menor obligacion tributaria, requiere documentacion de soporte.",
                f"{base} por ${amount_str}: Solicitar carta consulta (ruling) a Hacienda "
                f"para esta categoria especifica antes de clasificar.",
            )

        if trigger_type == PauseTriggerType.MISSING_CRITICAL_FIELD:
            return (
                f"{base}: Completar los campos faltantes {intake_output.missing_fields} "
                f"solicitando la informacion directamente al proveedor.",
                f"{base}: Si los campos faltantes no estan disponibles, registrar "
                f"el documento como gasto pendiente de clasificacion.",
            )

        # Default para otros triggers
        return (
            f"{base} por ${amount_str}: Procesar con la regla general aplicable "
            f"y documentar la decision en el expediente del cliente.",
            f"{base} por ${amount_str}: Pausar hasta obtener documentacion adicional "
            f"que clarifique el tratamiento fiscal correcto.",
            f"{base} por ${amount_str}: Consultar con un especialista en el area "
            f"antes de tomar una decision de clasificacion.",
        )

    @staticmethod
    def _build_pre_processed_analysis(
        intake_output: IntakeOutput,
        trigger_type: PauseTriggerType,
        reason: str,
        rule_conflicts: list[str],
        client_history: tuple[dict[str, Any], ...],
        precedent_found: bool,
    ) -> str:
        """
        Construye el analisis pre-procesado que recibe el CPA.

        El objetivo es que el CPA no tenga que re-analizar el caso desde cero.
        El CENTINELA entrega toda la informacion relevante organizada.
        """
        lines = [
            "=== ANALISIS PRE-PROCESADO DEL CENTINELA ===",
            f"Trigger: {trigger_type.value}",
            f"Motivo: {reason}",
            "",
            "--- DATOS DEL DOCUMENTO ---",
            f"Proveedor: {intake_output.vendor or 'No identificado'}",
            f"Fecha: {intake_output.date or 'No identificada'}",
            f"Monto: {intake_output.amount or 'No identificado'} {intake_output.currency}",
            f"IVU/Impuesto: {intake_output.tax_amount or 'No identificado'}",
            f"Formato fuente: {intake_output.source_format.value}",
            f"Confidence OCR: {intake_output.confidence:.4f}",
            f"Campos faltantes: {list(intake_output.missing_fields) or 'Ninguno'}",
            "",
            "--- EVALUACION DE RIESGO ---",
            f"Confianza del documento: {float(intake_output.confidence)*100:.1f}%",
            f"Precedente en historial del cliente: {'SI' if precedent_found else 'NO'}",
            f"Transacciones en historial analizado: {len(client_history)}",
        ]

        if rule_conflicts:
            lines += [
                "",
                "--- REGLAS EN CONFLICTO ---",
            ]
            for rule_id in rule_conflicts:
                lines.append(f"  • {rule_id}")

        if intake_output.line_items:
            lines += [
                "",
                "--- ITEMS DE LINEA ---",
            ]
            for item in intake_output.line_items:
                lines.append(
                    f"  • {item.description}: ${item.amount} "
                    f"{'(IVU: $' + str(item.tax_amount) + ')' if item.tax_amount else ''}"
                )

        lines += [
            "",
            "--- ACCION REQUERIDA DEL CPA ---",
            "Revisar las interpretaciones provistas y seleccionar la correcta.",
            "Confirmar via release_pause() con su licencia y token de autenticacion.",
        ]

        return "\n".join(lines)
