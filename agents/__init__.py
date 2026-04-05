# =============================================================================
# agents/__init__.py
# API publica del framework de agentes Bit-Counting.
#
# AGENTES DISPONIBLES:
#   Intake      — extrae campos estructurados de documentos crudos (foto/PDF)
#   Centinela   — evalua cada documento y decide PROCEED o PAUSE
#   Orchestrator — coordina el flujo y mantiene el log inmutable de decisiones
#
# TIPOS DE MENSAJES (todos Pydantic frozen=True):
#   BaseAgentMessage  — clase base con message_id, timestamp, source/target
#   IntakeOutput      — output del INTAKE: campos del documento + confidence
#   CentinelaEvaluation — decision PROCEED/PAUSE del CENTINELA
#   CentinelaPause    — pausa irrevocable emitida por el CENTINELA
#   ClassifierOutput  — clasificacion contable del CLASIFICADOR
#   AuditorVerification — verificacion cruzada del AUDITOR
#   OrchestratorDecision — entrada del log inmutable del ORQUESTADOR
#   FiscalOutput      — calculo de obligacion fiscal PR
#   CPAInstruction    — instruccion del CPA al INTERPRETE
#   PolicyActivation  — politica activada por el INTERPRETE
#
# EXCEPCIONES:
#   AgentScopeError    — agente recibio mensaje fuera de su dominio
#   MessageTypeError   — tipo de mensaje invalido (no es BaseAgentMessage)
#   CentinelaBlockError — flujo bloqueado por pausa activa del CENTINELA
#   ImmutableLogError  — intento de modificar el log de decisiones
#
# REGLA DE ORO:
#   Los agentes NO se llaman entre si directamente.
#   Solo el ORQUESTADOR coordina el flujo.
#   Toda comunicacion es via instancias de BaseAgentMessage (nunca strings/dicts).
# =============================================================================

from .exceptions import (
    AgentScopeError,
    BitCountingAgentError,
    CentinelaBlockError,
    ImmutableLogError,
    MessageTypeError,
)
from .messages import (
    AuditorVerification,
    BaseAgentMessage,
    CentinelaDecision,
    CentinelaEvaluation,
    CentinelaPause,
    ClassifierOutput,
    CPAInstruction,
    EntryType,
    FiscalOutput,
    IntakeOutput,
    LineItem,
    OrchestratorDecision,
    PauseStatus,
    PauseTriggerType,
    PolicyActivation,
    SourceFormat,
)
from .base import BaseAgent
from .orchestrator import Orchestrator
from .centinela import Centinela
from .intake import IntakeAgent as Intake

__all__ = [
    # Agentes
    "BaseAgent",
    "Orchestrator",
    "Centinela",
    "Intake",
    # Mensajes
    "BaseAgentMessage",
    "LineItem",
    "IntakeOutput",
    "CentinelaEvaluation",
    "CentinelaPause",
    "ClassifierOutput",
    "AuditorVerification",
    "OrchestratorDecision",
    "FiscalOutput",
    "CPAInstruction",
    "PolicyActivation",
    # Enums
    "CentinelaDecision",
    "PauseStatus",
    "EntryType",
    "PauseTriggerType",
    "SourceFormat",
    # Excepciones
    "BitCountingAgentError",
    "AgentScopeError",
    "MessageTypeError",
    "CentinelaBlockError",
    "ImmutableLogError",
]
