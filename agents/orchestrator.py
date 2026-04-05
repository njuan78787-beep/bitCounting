# =============================================================================
# agents/orchestrator.py
# Agente ORQUESTADOR del sistema Bit-Counting.
#
# ROL:
#   El ORQUESTADOR es el coordinador central del flujo de procesamiento.
#   Su responsabilidad primaria es el LOG DE DECISIONES: un registro inmutable,
#   append-only, de cada accion tomada por cualquier agente del sistema.
#
# GARANTIAS DE DISENO:
#   - El log es un tuple que se reconstruye con cada append. La lista interna
#     (_decisions_internal) no se expone publicamente — solo se expone via
#     get_decision_log() que retorna un tuple (copia inmutable).
#   - Ninguna operacion de borrado, modificacion o reordenamiento del log
#     esta disponible. Cualquier intento lanza ImmutableLogError.
#   - El ORQUESTADOR recibe las senales de los agentes y decide el agente
#     siguiente segun el protocolo de mensajes del blueprint.
#   - No interpreta el contenido de los mensajes — solo los enruta.
#
# PROTOCOLO DE ENRUTAMIENTO:
#   IntakeOutput      → Centinela.evaluate()
#   CentinelaEvaluation (PROCEED) → Clasificador
#   CentinelaEvaluation (PAUSE)   → emite CentinelaPause → notifica CPA
#   ClassifierOutput  → Auditor
#   AuditorVerification (OK)      → registra en log, cierra flujo
#   AuditorVerification (FAIL)    → emite pausa Centinela
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .base import BaseAgent
from .exceptions import ImmutableLogError
from .messages import (
    AuditorVerification,
    BaseAgentMessage,
    CentinelaEvaluation,
    ClassifierOutput,
    FiscalOutput,
    IntakeOutput,
    OrchestratorDecision,
    PolicyActivation,
)

logger = logging.getLogger(__name__)


class Orchestrator(BaseAgent):
    """
    ORQUESTADOR — coordinador central y guardian del log de decisiones.

    El ORQUESTADOR cumple dos funciones:
      1. LOGGING: registra cada decision de cada agente en un log immutable.
         El log es append-only. Nunca se puede borrar ni modificar una entrada.
      2. ENRUTAMIENTO: recibe senales de los agentes y decide a que agente
         enviar el mensaje siguiente segun el protocolo de mensajes.

    El log en memoria usa el patron tuple-rebuild:
      - La lista interna _decisions_internal es mutable para performance.
      - Se expone UNICAMENTE via get_decision_log() que retorna tuple (inmutable).
      - No hay metodo publico que permita modificar ni eliminar entradas.

    El ORQUESTADOR no puede opinar sobre el contenido de los mensajes.
    Solo puede enrutar y registrar.
    """

    # --- Tipos de mensajes que el ORQUESTADOR puede recibir directamente ---
    # (El ORQUESTADOR tambien puede recibir cualquier mensaje via log_decision)

    @property
    def agent_name(self) -> str:
        return "ORQUESTADOR"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        # El ORQUESTADOR puede recibir outputs de cualquier agente para enrutar
        return (
            IntakeOutput,
            CentinelaEvaluation,
            ClassifierOutput,
            AuditorVerification,
            FiscalOutput,
            PolicyActivation,
        )

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        # El ORQUESTADOR solo emite OrchestratorDecision como output formal
        return (OrchestratorDecision,)

    def __init__(self) -> None:
        """
        Inicializa el ORQUESTADOR con log vacio.

        El log interno es una lista Python mutable para performance de append,
        pero nunca se expone directamente. Solo se expone via get_decision_log()
        que retorna una copia como tuple inmutable.
        """
        # Lista interna mutable — NUNCA exponer directamente
        self._decisions_internal: list[OrchestratorDecision] = []

        # Indice para busqueda rapida por transaction_id
        # { transaction_id -> [decision_id, ...] }
        self._transaction_index: dict[str, list[str]] = {}

        logger.info("ORQUESTADOR inicializado. Log de decisiones vacio.")

        # El ORQUESTADOR se registra a si mismo como su propio orquestador
        # para que sus propias decisiones queden en el log.
        # Esto se hace despues de __init__ para evitar recursion.

    # -------------------------------------------------------------------------
    # LOG DE DECISIONES — API publica de logging
    # -------------------------------------------------------------------------

    def log_decision(
        self,
        agent_name: str,
        input_msg: BaseAgentMessage,
        output_msg: BaseAgentMessage,
        rule_ids: tuple[str, ...],
        confidence: Decimal,
        duration_ms: int | None = None,
        requires_cpa_review: bool = False,
    ) -> OrchestratorDecision:
        """
        Registra una decision en el log inmutable del ORQUESTADOR.

        Esta es la UNICA operacion de escritura sobre el log.
        Es append-only — nunca puede modificar ni borrar entradas existentes.

        Args:
            agent_name:         Nombre del agente que tomo la decision.
            input_msg:          Mensaje de entrada que recibio el agente.
            output_msg:         Mensaje de salida que genero el agente.
            rule_ids:           IDs de reglas fiscales aplicadas.
            confidence:         Score de confianza de la decision.
            duration_ms:        Duracion del procesamiento en ms.
            requires_cpa_review: True si la decision requiere revision CPA.

        Returns:
            OrchestratorDecision creada y agregada al log.
        """
        decision = OrchestratorDecision(
            source_agent="ORQUESTADOR",
            target_agent="LOG",
            decision_id=str(uuid.uuid4()),
            agent_name=agent_name,
            input_snapshot=self._serialize_message(input_msg),
            output_snapshot=self._serialize_message(output_msg),
            rule_ids_applied=rule_ids,
            confidence=confidence,
            requires_cpa_review=requires_cpa_review,
            processing_duration_ms=duration_ms,
        )

        # APPEND-ONLY: solo se agrega, nunca se modifica
        self._decisions_internal.append(decision)

        # Actualizar indice de transacciones
        self._index_decision(decision, input_msg, output_msg)

        logger.debug(
            "Decision registrada: id=%s agente=%s confianza=%.4f total_en_log=%d.",
            decision.decision_id,
            agent_name,
            float(confidence),
            len(self._decisions_internal),
        )

        return decision

    def record_raw(
        self,
        agent_name: str,
        input_snapshot: dict,
        output_snapshot: dict,
        rule_ids_applied: list[str],
        confidence: float,
        requires_cpa_review: bool = False,
    ) -> "OrchestratorDecision":
        """
        Registra una decision usando dicts en lugar de BaseAgentMessage.
        Para uso interno de agentes que no tienen un mensaje tipado de salida
        disponible al momento del logging (ej: INTAKE en su proceso raw).
        """
        from datetime import datetime, timezone
        decision = OrchestratorDecision(
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            source_agent=agent_name,
            target_agent="LOG",
            decision_id=str(uuid.uuid4()),
            agent_name=agent_name,
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            rule_ids_applied=tuple(rule_ids_applied),
            confidence=Decimal(str(round(confidence, 4))),
            requires_cpa_review=requires_cpa_review,
            processing_duration_ms=None,
        )
        self._decisions_internal.append(decision)
        return decision

    def get_decision_log(self) -> tuple[OrchestratorDecision, ...]:
        """
        Retorna el log completo de decisiones como tuple inmutable.

        La inmutabilidad del tuple garantiza que el caller no puede
        modificar el log. El patron tuple() es la barrera de proteccion.

        Returns:
            tuple inmutable de todas las OrchestratorDecision en orden cronologico.
        """
        return tuple(self._decisions_internal)

    def get_decisions_for_transaction(
        self,
        transaction_id: str,
    ) -> tuple[OrchestratorDecision, ...]:
        """
        Retorna todas las decisiones relacionadas con una transaccion especifica.

        Busca en el indice por transaction_id. Si el ID no esta en el indice,
        retorna tuple vacia (no lanza excepcion).

        Args:
            transaction_id: ID de la transaccion a buscar.

        Returns:
            tuple de OrchestratorDecision relacionadas, en orden cronologico.
        """
        decision_ids = self._transaction_index.get(transaction_id, [])
        if not decision_ids:
            return ()

        # Construir un lookup por decision_id para busqueda O(1)
        lookup = {d.decision_id: d for d in self._decisions_internal}
        results = []
        for did in decision_ids:
            if did in lookup:
                results.append(lookup[did])

        return tuple(results)

    def get_log_size(self) -> int:
        """Retorna el numero de decisiones en el log. Util para monitoreo."""
        return len(self._decisions_internal)

    def get_decisions_requiring_cpa_review(self) -> tuple[OrchestratorDecision, ...]:
        """
        Retorna todas las decisiones que requieren revision del CPA.
        Util para generar la cola de trabajo del CPA.
        """
        return tuple(
            d for d in self._decisions_internal if d.requires_cpa_review
        )

    # -------------------------------------------------------------------------
    # OPERACIONES PROHIBIDAS — garantia de inmutabilidad
    # -------------------------------------------------------------------------

    def delete_decision(self, decision_id: str) -> None:
        """
        Operacion prohibida. El log es append-only.

        Raises:
            ImmutableLogError: Siempre. Nunca se puede borrar una decision.
        """
        raise ImmutableLogError(
            f"delete_decision(decision_id={decision_id!r})"
        )

    def modify_decision(self, decision_id: str, **kwargs: Any) -> None:
        """
        Operacion prohibida. El log es append-only.

        Raises:
            ImmutableLogError: Siempre. Nunca se puede modificar una decision.
        """
        raise ImmutableLogError(
            f"modify_decision(decision_id={decision_id!r}, fields={list(kwargs.keys())})"
        )

    def clear_log(self) -> None:
        """
        Operacion prohibida. El log es append-only y no tiene operacion de limpieza.

        Raises:
            ImmutableLogError: Siempre. El log no puede ser vaciado.
        """
        raise ImmutableLogError("clear_log()")

    # -------------------------------------------------------------------------
    # ENRUTAMIENTO DE FLUJO
    # -------------------------------------------------------------------------

    def _process_impl(self, message: BaseAgentMessage) -> OrchestratorDecision:
        """
        Implementacion del ORQUESTADOR como agente.

        El ORQUESTADOR recibe el output de un agente y registra la decision
        de enrutamiento. El enrutamiento real (llamada al agente siguiente)
        lo hace el sistema que invoca al ORQUESTADOR, no el ORQUESTADOR mismo.

        Este metodo registra la decision de enrutamiento en el log y la retorna.
        """
        route_target = self._determine_next_agent(message)
        requires_review = self._requires_cpa_review(message)

        decision = OrchestratorDecision(
            source_agent="ORQUESTADOR",
            target_agent="LOG",
            decision_id=str(uuid.uuid4()),
            agent_name=self.agent_name,
            input_snapshot=self._serialize_message(message),
            output_snapshot={
                "routing_decision": route_target,
                "requires_cpa_review": requires_review,
                "message_type": type(message).__name__,
                "message_id": message.message_id,
            },
            rule_ids_applied=(),
            confidence=self._extract_confidence(message),
            requires_cpa_review=requires_review,
        )

        self._decisions_internal.append(decision)
        self._index_decision(decision, message, message)

        logger.info(
            "[ORQUESTADOR] Mensaje '%s' (id=%s) → ruta '%s'.",
            type(message).__name__,
            message.message_id,
            route_target,
        )

        return decision

    # -------------------------------------------------------------------------
    # HELPERS PRIVADOS
    # -------------------------------------------------------------------------

    def _determine_next_agent(self, message: BaseAgentMessage) -> str:
        """
        Determina el agente siguiente segun el tipo de mensaje recibido.
        Implementa el protocolo de mensajes del blueprint.
        """
        type_to_target: dict[type, str] = {
            IntakeOutput: "CENTINELA",
            CentinelaEvaluation: "CLASIFICADOR_o_PAUSA",  # depende de decision
            ClassifierOutput: "AUDITOR",
            AuditorVerification: "LOG_o_CENTINELA_PAUSA",  # depende de verified
            FiscalOutput: "HACIENDA",
            PolicyActivation: "CENTINELA",
        }

        base_target = type_to_target.get(type(message), "UNKNOWN")

        # Refinar para tipos con logica condicional
        if isinstance(message, CentinelaEvaluation):
            from .messages import CentinelaDecision
            return "CLASIFICADOR" if message.decision == CentinelaDecision.PROCEED else "PAUSA_CPA"

        if isinstance(message, AuditorVerification):
            return "LOG_FINAL" if message.verified else "CENTINELA_PAUSA"

        return base_target

    @staticmethod
    def _requires_cpa_review(message: BaseAgentMessage) -> bool:
        """Determina si la decision requiere revision del CPA."""
        # Cualquier verificacion fallida requiere revision
        if isinstance(message, AuditorVerification):
            return not message.verified or bool(message.fraud_flags)

        # Confidence baja en cualquier output requiere revision
        if hasattr(message, "confidence"):
            from .messages import CentinelaDecision
            from tax_rules import CONFIDENCE_MARK_REVIEW
            conf = float(message.confidence)  # type: ignore[attr-defined]
            if conf < CONFIDENCE_MARK_REVIEW:
                return True

        # Evaluacion del CENTINELA con decision PAUSE
        if isinstance(message, CentinelaEvaluation):
            from .messages import CentinelaDecision
            return message.decision == CentinelaDecision.PAUSE

        return False

    @staticmethod
    def _serialize_message(message: BaseAgentMessage) -> dict[str, Any]:
        """
        Serializa un mensaje Pydantic a dict para almacenamiento en el log.
        Convierte Decimal a str para serialización JSON-safe.
        """
        try:
            raw = message.model_dump()
            return _make_json_safe(raw)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error serializando mensaje '%s': %s", type(message).__name__, exc)
            return {
                "error": f"Serializacion fallida: {exc}",
                "message_type": type(message).__name__,
                "message_id": getattr(message, "message_id", "UNKNOWN"),
            }

    def _index_decision(
        self,
        decision: OrchestratorDecision,
        input_msg: BaseAgentMessage,
        output_msg: BaseAgentMessage,
    ) -> None:
        """
        Actualiza el indice transaction_id → [decision_id, ...].
        Extrae transaction IDs de los mensajes segun el tipo.
        """
        tx_ids: set[str] = set()

        # Extraer IDs de transacciones de los mensajes
        for msg in (input_msg, output_msg):
            # IntakeOutput y mensajes derivados tienen message_id como proxy de transaccion
            tx_ids.add(msg.message_id)

            # AuditorVerification referencia el ClassifierOutput
            if hasattr(msg, "verified_classifier_id"):
                tx_ids.add(msg.verified_classifier_id)  # type: ignore[attr-defined]

            # ClassifierOutput referencia el IntakeOutput
            if hasattr(msg, "classified_intake_id"):
                tx_ids.add(msg.classified_intake_id)  # type: ignore[attr-defined]

            # CentinelaEvaluation referencia el IntakeOutput
            if hasattr(msg, "evaluated_intake_id"):
                tx_ids.add(msg.evaluated_intake_id)  # type: ignore[attr-defined]

            # CentinelaPause tiene affected_transaction_ids
            if hasattr(msg, "affected_transaction_ids"):
                for tx_id in msg.affected_transaction_ids:  # type: ignore[attr-defined]
                    tx_ids.add(tx_id)

        for tx_id in tx_ids:
            if tx_id not in self._transaction_index:
                self._transaction_index[tx_id] = []
            self._transaction_index[tx_id].append(decision.decision_id)


# ---------------------------------------------------------------------------
# Helpers de serializacion
# ---------------------------------------------------------------------------

def _make_json_safe(obj: Any) -> Any:
    """Convierte recursivamente tipos no-JSON-serializable a tipos seguros."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(item) for item in obj]
    return obj
