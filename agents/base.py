# =============================================================================
# agents/base.py
# Clase base abstracta para todos los agentes del framework Bit-Counting.
#
# GARANTIAS DE DISENO:
#   - Ningun agente acepta strings, dicts u objetos no tipados.
#   - Cada agente declara allowed_input_types y allowed_output_types en tiempo
#     de definicion de clase — el scope es fijo, no expandible en runtime.
#   - El metodo process() valida tipos de entrada y salida antes y despues
#     de _process_impl(), y registra la decision en el ORQUESTADOR.
#   - _reject_out_of_scope() lanza AgentScopeError si el tipo de mensaje
#     no esta en allowed_input_types del agente.
#
# COMUNICACION ENTRE AGENTES:
#   Los agentes NO se llaman entre si directamente.
#   Solo el ORQUESTADOR coordina el flujo.
#   Un agente genera su output — el ORQUESTADOR decide a quien enviarlo.
# =============================================================================

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .exceptions import AgentScopeError, MessageTypeError
from .messages import BaseAgentMessage, OrchestratorDecision

if TYPE_CHECKING:
    # Evitar importacion circular: Orchestrator importa BaseAgent,
    # BaseAgent referencia Orchestrator solo para type hints.
    from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Clase base abstracta para todos los agentes del sistema Bit-Counting.

    Cada agente especializado debe:
      1. Declarar agent_name (str literal, identidad fija)
      2. Declarar agent_version (semver)
      3. Declarar allowed_input_types (tuple de tipos Pydantic)
      4. Declarar allowed_output_types (tuple de tipos Pydantic)
      5. Implementar _process_impl(message) con la logica real del agente

    El metodo process() es el unico punto de entrada publico.
    Garantiza validacion de tipos, logging y registro en el ORQUESTADOR.

    Ninguna subclase debe sobreescribir process() — solo _process_impl().
    """

    # --- Identidad del agente (deben ser sobreescritos en cada subclase) ---

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Nombre unico del agente. Fijo en tiempo de clase."""
        ...

    @property
    @abstractmethod
    def agent_version(self) -> str:
        """Version del agente en formato semver (ej: '1.0.0')."""
        ...

    @property
    @abstractmethod
    def allowed_input_types(self) -> tuple[type, ...]:
        """
        Tipos de BaseAgentMessage que este agente puede recibir.
        El scope del agente es fijo — no se puede expandir en runtime.
        """
        ...

    @property
    @abstractmethod
    def allowed_output_types(self) -> tuple[type, ...]:
        """
        Tipos de BaseAgentMessage que este agente puede emitir.
        Todo output de _process_impl() debe ser uno de estos tipos.
        """
        ...

    # --- Referencia al ORQUESTADOR (inyectada, no hardcodeada) ---

    _orchestrator: "Orchestrator | None" = None

    def register_orchestrator(self, orchestrator: "Orchestrator") -> None:
        """
        Registra el ORQUESTADOR que recibira los logs de decision.
        Llamado por el ORQUESTADOR al inicializar el sistema.
        """
        self._orchestrator = orchestrator
        logger.debug(
            "Agente '%s' registrado con ORQUESTADOR '%s'.",
            self.agent_name,
            orchestrator.agent_name,
        )

    # --- Punto de entrada publico ---

    def process(self, message: Any) -> BaseAgentMessage:
        """
        Punto de entrada publico para procesar un mensaje.

        Flujo:
          1. Valida que message sea instancia de BaseAgentMessage
             → lanza MessageTypeError si no lo es
          2. Valida que el tipo este en allowed_input_types
             → lanza AgentScopeError si no esta
          3. Ejecuta _process_impl(message)
          4. Valida que el output sea instancia de BaseAgentMessage
             → lanza MessageTypeError si no lo es
          5. Valida que el output sea de tipo permitido en allowed_output_types
             → lanza AgentScopeError si no lo es
          6. Registra la decision en el ORQUESTADOR (si esta registrado)
          7. Retorna el output

        Args:
            message: Debe ser instancia de BaseAgentMessage. Nunca string/dict.

        Returns:
            Instancia de BaseAgentMessage del tipo correcto para este agente.

        Raises:
            MessageTypeError: Si message no es instancia de BaseAgentMessage.
            AgentScopeError: Si el tipo de message no esta en allowed_input_types.
        """
        start_ms = int(time.monotonic() * 1000)

        # --- Paso 1: Validar que es un mensaje tipado ---
        if not isinstance(message, BaseAgentMessage):
            raise MessageTypeError(
                agent_name=self.agent_name,
                received_type=type(message).__name__,
            )

        # --- Paso 2: Validar que el tipo esta en el scope del agente ---
        self._reject_out_of_scope(message)

        logger.info(
            "[%s v%s] Procesando mensaje tipo '%s' (id=%s) de '%s'.",
            self.agent_name,
            self.agent_version,
            type(message).__name__,
            message.message_id,
            message.source_agent,
        )

        # --- Paso 3: Ejecutar la implementacion del agente ---
        output = self._process_impl(message)

        end_ms = int(time.monotonic() * 1000)
        duration_ms = end_ms - start_ms

        # --- Paso 4: Validar que el output es un mensaje tipado ---
        if not isinstance(output, BaseAgentMessage):
            raise MessageTypeError(
                agent_name=self.agent_name,
                received_type=type(output).__name__,
            )

        # --- Paso 5: Validar que el output es del tipo permitido ---
        if not isinstance(output, self.allowed_output_types):
            raise AgentScopeError(
                agent_name=self.agent_name,
                message_type=type(output).__name__,
                allowed_types=tuple(t.__name__ for t in self.allowed_output_types),
            )

        # --- Paso 6: Registrar en el ORQUESTADOR ---
        if self._orchestrator is not None:
            try:
                self._orchestrator.log_decision(
                    agent_name=self.agent_name,
                    input_msg=message,
                    output_msg=output,
                    rule_ids=self._extract_rule_ids(output),
                    confidence=self._extract_confidence(output),
                    duration_ms=duration_ms,
                )
            except Exception as exc:  # noqa: BLE001
                # El fallo de logging NO debe detener el flujo del agente,
                # pero si debe quedar registrado para investigacion.
                logger.error(
                    "[%s] Fallo al registrar decision en ORQUESTADOR: %s",
                    self.agent_name,
                    exc,
                )

        logger.info(
            "[%s] Output generado: tipo='%s', id=%s, duracion=%dms.",
            self.agent_name,
            type(output).__name__,
            output.message_id,
            duration_ms,
        )

        return output

    # --- Implementacion especifica del agente (abstract) ---

    @abstractmethod
    def _process_impl(self, message: BaseAgentMessage) -> BaseAgentMessage:
        """
        Implementacion especifica de la logica del agente.

        Debe retornar una instancia de uno de los tipos en allowed_output_types.
        NUNCA debe retornar strings, dicts u objetos no tipados.
        NUNCA debe llamar a otros agentes directamente — el ORQUESTADOR coordina.

        Args:
            message: Mensaje ya validado. Siempre BaseAgentMessage del tipo correcto.

        Returns:
            BaseAgentMessage del tipo correcto para este agente.
        """
        ...

    # --- Validacion de scope ---

    def _reject_out_of_scope(self, message: BaseAgentMessage) -> None:
        """
        Lanza AgentScopeError si el tipo de mensaje no esta en allowed_input_types.

        Este metodo es la garantia arquitectonica de que ningun agente puede
        opinar fuera de su dominio tecnico. El scope es fijo por diseno.

        Args:
            message: Mensaje tipado a verificar.

        Raises:
            AgentScopeError: Si el tipo de message no esta en allowed_input_types.
        """
        if not isinstance(message, self.allowed_input_types):
            raise AgentScopeError(
                agent_name=self.agent_name,
                message_type=type(message).__name__,
                allowed_types=tuple(t.__name__ for t in self.allowed_input_types),
            )

    # --- Helpers para extraccion de metadatos del output ---

    @staticmethod
    def _extract_rule_ids(output: BaseAgentMessage) -> tuple[str, ...]:
        """
        Extrae rule_ids del output si el tipo los expone.
        Retorna tuple vacia si el tipo no tiene ese campo.
        """
        # ClassifierOutput y FiscalOutput tienen rule_ref
        if hasattr(output, "rule_ref"):
            return (output.rule_ref,)  # type: ignore[attr-defined]
        # AuditorVerification tiene independent_rule_ref
        if hasattr(output, "independent_rule_ref"):
            return (output.independent_rule_ref,)  # type: ignore[attr-defined]
        # OrchestratorDecision ya tiene rule_ids_applied
        if hasattr(output, "rule_ids_applied"):
            return output.rule_ids_applied  # type: ignore[attr-defined]
        return ()

    @staticmethod
    def _extract_confidence(output: BaseAgentMessage) -> Decimal:
        """
        Extrae el confidence score del output si el tipo lo expone.
        Retorna Decimal('1.0') como default si el tipo no tiene ese campo.
        """
        if hasattr(output, "confidence"):
            val = output.confidence  # type: ignore[attr-defined]
            if isinstance(val, Decimal):
                return val
            return Decimal(str(val))
        if hasattr(output, "confidence_at_evaluation"):
            val = output.confidence_at_evaluation  # type: ignore[attr-defined]
            if isinstance(val, Decimal):
                return val
            return Decimal(str(val))
        return Decimal("1.0")

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.agent_name!r}, "
            f"version={self.agent_version!r}, "
            f"inputs={[t.__name__ for t in self.allowed_input_types]}, "
            f"outputs={[t.__name__ for t in self.allowed_output_types]})"
        )
