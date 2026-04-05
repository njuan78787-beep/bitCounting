# =============================================================================
# agents/exceptions.py
# Excepciones del framework de agentes Bit-Counting.
#
# GARANTIA DE DISENO:
#   - AgentScopeError: ningún agente puede opinar fuera de su dominio técnico.
#   - MessageTypeError: la comunicación entre agentes es exclusivamente via
#     instancias tipadas de Pydantic — nunca strings o dicts crudos.
#   - CentinelaBlockError: solo el CENTINELA puede bloquear el flujo, y solo
#     el CPA puede liberarlo. Ningún otro agente puede saltarse esta pausa.
#   - ImmutableLogError: el log del ORQUESTADOR es append-only — nunca se
#     puede modificar ni eliminar una decisión ya registrada.
# =============================================================================

from __future__ import annotations


class BitCountingAgentError(Exception):
    """Excepción base para todos los errores del framework de agentes."""


class AgentScopeError(BitCountingAgentError):
    """
    Un agente intentó opinar o procesar algo fuera de su dominio técnico.

    El scope de cada agente es fijo y no puede expandirse en runtime.
    Si un agente recibe un mensaje que no le corresponde, debe rechazarlo
    con esta excepción en lugar de intentar procesarlo.

    Ejemplo: el CLASIFICADOR recibiendo un mensaje de pausa del CENTINELA.
    """

    def __init__(
        self,
        agent_name: str,
        message_type: str,
        allowed_types: tuple[str, ...],
    ) -> None:
        self.agent_name = agent_name
        self.message_type = message_type
        self.allowed_types = allowed_types
        super().__init__(
            f"Agente '{agent_name}' rechazó mensaje de tipo '{message_type}'. "
            f"Tipos permitidos: {allowed_types}. "
            "El scope del agente es fijo — no puede procesar mensajes fuera de su dominio."
        )


class MessageTypeError(BitCountingAgentError):
    """
    Tipo de mensaje inválido para un agente específico.

    Se lanza cuando un agente recibe un objeto que no es una instancia de
    los tipos Pydantic tipados definidos en agents/messages.py.
    Los agentes NUNCA aceptan strings, dicts u objetos no tipados.

    Diferencia con AgentScopeError:
      - MessageTypeError: el objeto no es un mensaje tipado válido (ej: dict crudo).
      - AgentScopeError: el objeto ES un mensaje tipado, pero no es del tipo
        que ese agente particular puede recibir.
    """

    def __init__(self, agent_name: str, received_type: str) -> None:
        self.agent_name = agent_name
        self.received_type = received_type
        super().__init__(
            f"Agente '{agent_name}' recibió objeto de tipo '{received_type}'. "
            "Los agentes solo aceptan instancias de BaseAgentMessage (Pydantic frozen). "
            "Nunca pasar strings, dicts o tipos no tipados a un agente."
        )


class CentinelaBlockError(BitCountingAgentError):
    """
    El flujo de procesamiento está bloqueado por una pausa activa del CENTINELA.

    Una pausa del CENTINELA es irrevocable por cualquier agente del sistema.
    Solo el CPA humano puede liberarla mediante release_pause() con sus
    credenciales. Ningún otro agente puede bypasear esta restricción.

    Attributes:
        pause_id: UUID de la pausa activa que bloquea el flujo.
        affected_transaction_ids: transacciones bloqueadas por esta pausa.
        reason: motivo técnico que generó la pausa.
    """

    def __init__(
        self,
        pause_id: str,
        affected_transaction_ids: list[str],
        reason: str,
    ) -> None:
        self.pause_id = pause_id
        self.affected_transaction_ids = affected_transaction_ids
        self.reason = reason
        super().__init__(
            f"Flujo bloqueado por pausa CENTINELA [{pause_id}]. "
            f"Transacciones afectadas: {affected_transaction_ids}. "
            f"Motivo: {reason}. "
            "Solo el CPA puede liberar esta pausa mediante release_pause() con token válido."
        )


class ImmutableLogError(BitCountingAgentError):
    """
    Intento de modificar o eliminar el log de decisiones del ORQUESTADOR.

    El log del ORQUESTADOR es append-only por diseño arquitectónico.
    Una vez registrada una decisión, no puede ser modificada, eliminada
    ni reordenada. Cualquier intento de hacerlo lanza esta excepción.

    Esto garantiza la inmutabilidad del audit trail para cumplimiento
    con regulaciones contables y fiscales de Puerto Rico.
    """

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"Operación prohibida sobre el log de decisiones: '{operation}'. "
            "El log del ORQUESTADOR es append-only — las decisiones son inmutables. "
            "El audit trail nunca puede ser modificado ni eliminado."
        )
