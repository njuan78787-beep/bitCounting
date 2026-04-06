# =============================================================================
# agents/core_decisions.py
# Log de decisiones append-only compartido por todos los agentes core V2.
#
# GARANTIAS DE DISENO:
#   - Solo operacion: APPEND. Nunca se modifica ni elimina una entrada.
#   - Cada entrada es un AgentDecision frozen (Pydantic).
#   - El log es un singleton por proceso — todos los agentes comparten la
#     misma instancia accesible via get_decisions_log().
#   - input_hash = SHA-256(str(input_snapshot)) para evitar almacenar datos
#     sensibles del cliente directamente en el log.
# =============================================================================

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentDecision(BaseModel):
    """Una decision registrada por un agente core V2. Inmutable."""
    model_config = ConfigDict(frozen=True)

    decision_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_name:    str
    timestamp:     str  # ISO 8601
    input_hash:    str  # SHA-256 del input snapshot
    decision:      str  # descripcion de la decision tomada
    rule_ids:      tuple[str, ...] = ()
    confidence:    Optional[float] = None
    sandbox_ids:   tuple[str, ...] = ()  # SandboxResult.result_id usados
    notes:         str = ""


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


class AgentDecisionsLog:
    """
    Log de decisiones append-only para agentes core V2.

    Solo soporta append y consulta — nunca modificacion ni eliminacion.
    Implementacion de singleton via get_decisions_log().
    """

    def __init__(self) -> None:
        self._entries: list[AgentDecision] = []

    def record(
        self,
        agent_name:    str,
        input_data:    Any,
        decision:      str,
        rule_ids:      tuple[str, ...] = (),
        confidence:    Optional[float] = None,
        sandbox_ids:   tuple[str, ...] = (),
        notes:         str = "",
    ) -> AgentDecision:
        """
        Registra una decision. Retorna la entrada creada.
        NUNCA lanza excepciones — si falla, retorna una entrada de error.
        """
        try:
            input_hash = _sha256(str(input_data))
            entry = AgentDecision(
                agent_name=agent_name,
                timestamp=datetime.now(timezone.utc).isoformat(),
                input_hash=input_hash,
                decision=decision,
                rule_ids=rule_ids,
                confidence=confidence,
                sandbox_ids=sandbox_ids,
                notes=notes,
            )
        except Exception as exc:
            entry = AgentDecision(
                agent_name=agent_name,
                timestamp=datetime.now(timezone.utc).isoformat(),
                input_hash="error",
                decision=f"DECISION_LOG_ERROR: {exc}",
            )
        self._entries.append(entry)
        return entry

    def get_decisions(
        self,
        agent_name: Optional[str] = None,
    ) -> tuple[AgentDecision, ...]:
        """Retorna todas las decisiones, opcionalmente filtradas por agente."""
        if agent_name is None:
            return tuple(self._entries)
        return tuple(e for e in self._entries if e.agent_name == agent_name)

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Singleton global (uno por proceso)
# ---------------------------------------------------------------------------

_GLOBAL_LOG = AgentDecisionsLog()


def get_decisions_log() -> AgentDecisionsLog:
    """Retorna el log global de decisiones (singleton por proceso)."""
    return _GLOBAL_LOG


def reset_decisions_log() -> None:
    """Solo para tests — reinicia el log global."""
    global _GLOBAL_LOG
    _GLOBAL_LOG = AgentDecisionsLog()
