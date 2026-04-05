# =============================================================================
# agents/messages.py
# Tipos de mensaje del framework de agentes Bit-Counting.
#
# GARANTIA DE DISENO:
#   - Todos los mensajes son Pydantic frozen=True — inmutables una vez creados.
#   - La comunicacion entre agentes es EXCLUSIVAMENTE via estas instancias tipadas.
#   - Texto libre entre agentes esta arquitectonicamente prohibido.
#   - Cada mensaje incluye: message_id (UUID), timestamp, source_agent,
#     target_agent, schema_version para trazabilidad completa.
#
# Jerarquia:
#   BaseAgentMessage
#     ├── IntakeOutput          (INTAKE → CENTINELA)
#     ├── CentinelaEvaluation   (CENTINELA → CLASIFICADOR o PAUSA)
#     ├── CentinelaPause        (CENTINELA → CPA notification)
#     ├── ClassifierOutput      (CLASIFICADOR → AUDITOR)
#     ├── AuditorVerification   (AUDITOR → ORQUESTADOR)
#     ├── OrchestratorDecision  (ORQUESTADOR → log inmutable)
#     ├── FiscalOutput          (FISCAL PR → HACIENDA)
#     ├── CPAInstruction        (CPA → INTERPRETE)
#     └── PolicyActivation      (INTERPRETE → CENTINELA)
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# ENUMS DE ESTADO
# ---------------------------------------------------------------------------

class CentinelaDecision(str, Enum):
    """Decision del agente CENTINELA sobre si continuar o pausar el flujo."""
    PROCEED = "PROCEED"
    PAUSE   = "PAUSE"


class PauseStatus(str, Enum):
    """Estado de una pausa emitida por el CENTINELA."""
    ACTIVE   = "ACTIVE"
    RESOLVED = "RESOLVED"


class EntryType(str, Enum):
    """Tipo de asiento contable segun la partida doble."""
    DEBIT  = "debit"
    CREDIT = "credit"


class PauseTriggerType(str, Enum):
    """Tipo de evento que disparo la pausa del CENTINELA."""
    LOW_CONFIDENCE             = "LOW_CONFIDENCE"
    RULE_CONTROVERSY           = "RULE_CONTROVERSY"
    NO_PRECEDENT               = "NO_PRECEDENT"
    PENDING_REGULATORY_CHANGE  = "PENDING_REGULATORY_CHANGE"
    AGENT_RESULT_DIVERGENCE    = "AGENT_RESULT_DIVERGENCE"
    MISSING_CRITICAL_FIELD     = "MISSING_CRITICAL_FIELD"
    MANUAL_CPA_OVERRIDE        = "MANUAL_CPA_OVERRIDE"


class SourceFormat(str, Enum):
    """Formato del documento fuente procesado por INTAKE."""
    PDF       = "PDF"
    IMAGE_JPG = "IMAGE_JPG"
    IMAGE_PNG = "IMAGE_PNG"
    IMAGE_TIFF = "IMAGE_TIFF"
    CSV       = "CSV"
    XML       = "XML"
    JSON      = "JSON"
    UNKNOWN   = "UNKNOWN"


# ---------------------------------------------------------------------------
# CLASE BASE
# ---------------------------------------------------------------------------

class BaseAgentMessage(BaseModel):
    """
    Clase base para todos los mensajes del framework de agentes.

    Todo intercambio de informacion entre agentes DEBE ser una instancia
    de esta clase o sus subclases. Nunca strings, dicts u objetos crudos.

    Todos los campos de identidad son auto-generados si no se proveen.
    """

    model_config = ConfigDict(frozen=True)

    message_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unico del mensaje para trazabilidad completa",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp UTC de creacion del mensaje",
    )
    source_agent: str = Field(
        description="Nombre del agente que genero este mensaje",
    )
    target_agent: str = Field(
        description="Nombre del agente destinatario de este mensaje",
    )
    schema_version: str = Field(
        default="1.0",
        description="Version del schema de mensajes para compatibilidad",
    )


# ---------------------------------------------------------------------------
# INTAKE OUTPUT
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    """Un item de linea extraido de un documento por el agente INTAKE."""

    model_config = ConfigDict(frozen=True)

    description: str = Field(description="Descripcion del producto o servicio")
    quantity: Optional[Decimal] = Field(default=None, description="Cantidad de unidades")
    unit_price: Optional[Decimal] = Field(default=None, description="Precio unitario en la moneda del documento")
    amount: Decimal = Field(description="Monto total de este item (cantidad * precio unitario)")
    tax_amount: Optional[Decimal] = Field(default=None, description="IVU u otro impuesto aplicado a este item")
    account_hint: Optional[str] = Field(
        default=None,
        description="Sugerencia de cuenta contable basada en la descripcion (pre-clasificacion)",
    )


class IntakeOutput(BaseAgentMessage):
    """
    Output del agente INTAKE tras procesar un documento (foto, PDF, etc.).

    El INTAKE extrae campos estructurados del documento crudo.
    NUNCA infiere campos faltantes — los marca en missing_fields.
    Si hay campos criticos faltantes (amount, date), el flujo se detiene
    y se emite una pausa al CENTINELA.

    Siempre va dirigido al CENTINELA para evaluacion de riesgo.
    """

    # --- Campos del documento extraidos ---
    vendor: Optional[str] = Field(
        default=None,
        description="Nombre o razon social del vendedor/proveedor",
    )
    date: Optional[str] = Field(
        default=None,
        description="Fecha del documento en formato ISO 8601 (YYYY-MM-DD)",
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="Monto total del documento antes de impuestos. Siempre Decimal.",
    )
    tax_amount: Optional[Decimal] = Field(
        default=None,
        description="Monto de IVU u otro impuesto del documento. Siempre Decimal.",
    )
    currency: str = Field(
        default="USD",
        description="Codigo de moneda ISO 4217. Puerto Rico opera en USD.",
    )
    line_items: tuple[LineItem, ...] = Field(
        default_factory=tuple,
        description="Items de linea extraidos del documento. Tuple inmutable.",
    )
    payment_method: Optional[str] = Field(
        default=None,
        description="Metodo de pago identificado: CASH, CHECK, CREDIT_CARD, ACH, WIRE, etc.",
    )

    # --- Metadatos de calidad ---
    confidence: Decimal = Field(
        ge=Decimal("0.00"),
        le=Decimal("1.00"),
        description="Score de confianza [0.00, 1.00] calculado desde completitud y calidad de campos",
    )
    missing_fields: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Campos no encontrados en el documento. Nunca se infieren.",
    )
    critical_fields_missing: bool = Field(
        default=False,
        description="True si faltan campos criticos (amount, date) que impiden continuar",
    )
    source_format: SourceFormat = Field(
        default=SourceFormat.UNKNOWN,
        description="Formato del documento original procesado",
    )
    raw_text_excerpt: Optional[str] = Field(
        default=None,
        description="Extracto del texto crudo del OCR para trazabilidad (max 500 chars)",
    )


# ---------------------------------------------------------------------------
# CENTINELA EVALUATION
# ---------------------------------------------------------------------------

class CentinelaEvaluation(BaseAgentMessage):
    """
    Resultado de la evaluacion del agente CENTINELA sobre un IntakeOutput.

    El CENTINELA decide si el flujo puede continuar (PROCEED) o debe
    pausarse (PAUSE) para revision del CPA.

    PROCEED → el mensaje siguiente va al CLASIFICADOR.
    PAUSE   → se emite CentinelaPause y se notifica al CPA.
    """

    decision: CentinelaDecision = Field(
        description="PROCEED si el flujo puede continuar; PAUSE si se detiene para revision CPA",
    )
    reason: str = Field(
        description="Explicacion tecnica de la decision. Referencia triggers especificos.",
    )
    pause_id: Optional[str] = Field(
        default=None,
        description="UUID de la CentinelaPause emitida. Presente solo si decision=PAUSE.",
    )
    confidence_at_evaluation: Decimal = Field(
        ge=Decimal("0.00"),
        le=Decimal("1.00"),
        description="Confidence del IntakeOutput evaluado al momento de la decision",
    )
    rule_conflicts: tuple[str, ...] = Field(
        default_factory=tuple,
        description="IDs de reglas en conflicto detectadas durante la evaluacion",
    )
    precedent_found: bool = Field(
        description="True si existe precedente en el historial del cliente para esta transaccion",
    )
    evaluated_intake_id: str = Field(
        description="message_id del IntakeOutput evaluado. Para trazabilidad.",
    )


# ---------------------------------------------------------------------------
# CENTINELA PAUSE
# ---------------------------------------------------------------------------

class CentinelaPause(BaseAgentMessage):
    """
    Pausa irrevocable emitida por el CENTINELA.

    Una vez emitida, NINGUN agente puede liberar esta pausa.
    Solo el CPA humano puede liberarla mediante Centinela.release_pause()
    con su numero de licencia y token de autenticacion valido.

    La pausa bloquea el procesamiento de las transacciones afectadas
    hasta que el CPA tome una decision documentada.

    El campo pre_processed_analysis contiene el analisis completo del
    CENTINELA para que el CPA no tenga que volver a revisar desde cero.
    """

    pause_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unico de esta pausa. Inmutable una vez emitida.",
    )
    trigger_type: PauseTriggerType = Field(
        description="Tipo de evento que origino esta pausa",
    )
    affected_transaction_ids: tuple[str, ...] = Field(
        description="IDs de las transacciones bloqueadas por esta pausa",
    )
    conflicting_rules: tuple[str, ...] = Field(
        default_factory=tuple,
        description="IDs de reglas fiscales en conflicto que motivaron la pausa",
    )
    interpretations: tuple[str, ...] = Field(
        description="2-3 interpretaciones posibles del caso. El CPA elige una.",
        min_length=2,
    )
    pre_processed_analysis: str = Field(
        description=(
            "Analisis completo pre-procesado para el CPA: hechos del caso, "
            "reglas relevantes, precedentes encontrados, riesgo estimado. "
            "El CPA recibe esto — no tiene que volver a analizar desde cero."
        ),
    )
    sla_hours: int = Field(
        default=48,
        ge=1,
        description="SLA maximo en horas para que el CPA resuelva la pausa",
    )
    status: PauseStatus = Field(
        default=PauseStatus.ACTIVE,
        description="Estado actual de la pausa. ACTIVE = bloqueando flujo.",
    )


# ---------------------------------------------------------------------------
# CLASSIFIER OUTPUT
# ---------------------------------------------------------------------------

class ClassifierOutput(BaseAgentMessage):
    """
    Output del agente CLASIFICADOR.

    El CLASIFICADOR asigna cuentas contables y genera el asiento de
    partida doble para cada transaccion, referenciando la regla fiscal
    o contable aplicada.

    Output va siempre al AUDITOR para verificacion cruzada independiente.
    """

    account_code: str = Field(
        description=(
            "Codigo de cuenta contable segun el Plan de Cuentas de Bit-Counting. "
            "Formato: XXXX o XX-XXXX. Ejemplo: '5010' para Gastos de Operacion."
        ),
    )
    account_name: str = Field(
        description="Nombre legible de la cuenta contable asignada",
    )
    entry_type: EntryType = Field(
        description="Tipo de asiento: debit (debe) o credit (haber)",
    )
    amount: Decimal = Field(
        description="Monto del asiento contable. Siempre Decimal, nunca float.",
    )
    rule_ref: str = Field(
        description=(
            "Referencia a la regla contable o fiscal aplicada para esta clasificacion. "
            "Formato: rule_id del registro de tax_rules o codigo GAAP/IFRS."
        ),
    )
    confidence: Decimal = Field(
        ge=Decimal("0.00"),
        le=Decimal("1.00"),
        description="Score de confianza de la clasificacion [0.00, 1.00]",
    )
    reasoning: str = Field(
        description=(
            "Razonamiento tecnico de la clasificacion: por que esta cuenta, "
            "que regla aplica, que condiciones se cumplieron. "
            "Minimo 1 oracion completa — no puede estar vacio."
        ),
        min_length=10,
    )
    contra_account_code: Optional[str] = Field(
        default=None,
        description="Codigo de la cuenta de contrapartida para completar el asiento de partida doble",
    )
    contra_account_name: Optional[str] = Field(
        default=None,
        description="Nombre de la cuenta de contrapartida",
    )
    contra_entry_type: Optional[EntryType] = Field(
        default=None,
        description="Tipo de asiento de la contrapartida (opuesto a entry_type)",
    )
    classified_intake_id: str = Field(
        description="message_id del IntakeOutput que se esta clasificando. Para trazabilidad.",
    )


# ---------------------------------------------------------------------------
# AUDITOR VERIFICATION
# ---------------------------------------------------------------------------

class AuditorVerification(BaseAgentMessage):
    """
    Output del agente AUDITOR tras verificacion cruzada independiente.

    El AUDITOR verifica el ClassifierOutput de forma independiente,
    sin acceso al razonamiento interno del CLASIFICADOR.
    Su funcion es detectar discrepancias, errores algebraicos y
    senales de fraude antes de que el ORQUESTADOR registre la decision.

    Output va al ORQUESTADOR.
    """

    verified: bool = Field(
        description="True si la verificacion pasa todos los checks. False si hay discrepancias.",
    )
    checks_passed: tuple[str, ...] = Field(
        description="Lista de verificaciones completadas exitosamente",
    )
    discrepancies: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Lista de discrepancias encontradas vs el ClassifierOutput",
    )
    fraud_flags: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Senales de alerta de fraude detectadas (vacía si no hay)",
    )
    algebraic_balance_ok: bool = Field(
        description="True si el asiento de partida doble esta algebraicamente balanceado (debe = haber)",
    )
    independent_account_code: str = Field(
        description="Codigo de cuenta al que llego el AUDITOR independientemente",
    )
    independent_rule_ref: str = Field(
        description="Regla que el AUDITOR aplico independientemente para verificar",
    )
    verified_classifier_id: str = Field(
        description="message_id del ClassifierOutput verificado. Para trazabilidad.",
    )


# ---------------------------------------------------------------------------
# ORCHESTRATOR DECISION
# ---------------------------------------------------------------------------

class OrchestratorDecision(BaseAgentMessage):
    """
    Log inmutable de una decision del ORQUESTADOR.

    El ORQUESTADOR genera una OrchestratorDecision por cada accion
    significativa del sistema: cada vez que un agente procesa un mensaje,
    cada vez que el CENTINELA evalua, cada vez que se registra un asiento.

    Este log es APPEND-ONLY — una vez registrada una decision, no puede
    ser modificada, eliminada ni reordenada. Es el audit trail del sistema.

    decision_id es diferente de message_id — es el identificador persistente
    de esta decision en el log.
    """

    decision_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unico de esta decision en el log del ORQUESTADOR",
    )
    agent_name: str = Field(
        description="Nombre del agente cuya accion se esta registrando",
    )
    input_snapshot: dict[str, Any] = Field(
        description=(
            "Snapshot del mensaje de entrada en el momento del procesamiento. "
            "Se serializa a dict para persistencia — el original puede ser cualquier BaseAgentMessage."
        ),
    )
    output_snapshot: dict[str, Any] = Field(
        description="Snapshot del mensaje de salida generado por el agente",
    )
    rule_ids_applied: tuple[str, ...] = Field(
        default_factory=tuple,
        description="IDs de reglas fiscales o contables aplicadas en esta decision",
    )
    confidence: Decimal = Field(
        ge=Decimal("0.00"),
        le=Decimal("1.00"),
        description="Score de confianza del agente al tomar esta decision",
    )
    requires_cpa_review: bool = Field(
        default=False,
        description="True si esta decision requiere revision del CPA antes de finalizar",
    )
    processing_duration_ms: Optional[int] = Field(
        default=None,
        description="Duracion del procesamiento en milisegundos para monitoreo de performance",
    )


# ---------------------------------------------------------------------------
# FISCAL OUTPUT
# ---------------------------------------------------------------------------

class FiscalOutput(BaseAgentMessage):
    """
    Output del agente FISCAL PR.

    El FISCAL PR calcula las obligaciones tributarias del cliente
    con el Departamento de Hacienda de Puerto Rico.
    Cada calculo incluye el codigo Python que lo ejecuto para
    auditabilidad completa — nunca se confía en el resultado del LLM.

    Output va al agente HACIENDA para preparacion de formularios.
    """

    tax_type: str = Field(
        description="Tipo de impuesto calculado. Ver TaxType enum en tax_rules.constants.",
    )
    tax_liability: Decimal = Field(
        description="Monto de obligacion tributaria calculada. Siempre Decimal.",
    )
    form_id: str = Field(
        description=(
            "Identificador del formulario de Hacienda PR aplicable. "
            "Ejemplos: 'SC-2644', 'SC-2765', 'AS-2970-1', 'PLANILLA_MENSUAL_IVU'."
        ),
    )
    rule_ref: str = Field(
        description="rule_id de la regla fiscal de tax_rules aplicada para este calculo",
    )
    rate_version: str = Field(
        description=(
            "Version de la tasa aplicada. Formato: 'rule_id:version' o "
            "'rule_id:effective_date'. Permite reproducir el calculo exacto."
        ),
    )
    calc_hash: str = Field(
        description=(
            "Hash SHA-256 (16 chars) del calculo para verificacion de integridad. "
            "Generado por calculate_ivu() u otras funciones del modulo tax_rules."
        ),
    )
    calculation_code: str = Field(
        description=(
            "El codigo Python exacto que se ejecuto para obtener tax_liability. "
            "Implementa la Capa 2 del sistema anti-alucinacion: el LLM genera "
            "la llamada, Python ejecuta la aritmetica. Nunca calculos inline del LLM."
        ),
        min_length=20,
    )
    taxable_base: Decimal = Field(
        description="Monto base sobre el cual se calculo el impuesto",
    )
    period_from: str = Field(
        description="Inicio del periodo fiscal cubierto (ISO 8601: YYYY-MM-DD)",
    )
    period_to: str = Field(
        description="Fin del periodo fiscal cubierto (ISO 8601: YYYY-MM-DD)",
    )
    exemptions_applied: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Exenciones aplicadas al calculo, si las hay",
    )


# ---------------------------------------------------------------------------
# CPA INSTRUCTION
# ---------------------------------------------------------------------------

class CPAInstruction(BaseAgentMessage):
    """
    Instruccion del CPA humano al agente INTERPRETE.

    El CPA puede dar instrucciones en lenguaje natural que el INTERPRETE
    convierte a politicas formales del sistema.

    El flujo requiere confirmacion explícita del CPA antes de activar
    la politica — el INTERPRETE nunca actua sin confirmacion.

    CRITICO: Este mensaje puede provenir de fuera del sistema (interfaz CPA).
    El INTERPRETE debe validar cpa_license antes de procesar.
    """

    instruction_text: str = Field(
        description=(
            "Instruccion del CPA en lenguaje natural. "
            "El INTERPRETE la convierte a reglas formales del sistema. "
            "Ejemplo: 'Los gastos de representacion sobre $200 siempre requieren "
            "aprobacion previa de gerencia antes de registrar.'"
        ),
        min_length=10,
    )
    cpa_license: str = Field(
        description=(
            "Numero de licencia del CPA que emite la instruccion. "
            "Debe coincidir con un registro activo en cpa_partners."
        ),
    )
    policy_draft: Optional[str] = Field(
        default=None,
        description=(
            "Draft de politica generado por el INTERPRETE tras analizar la instruccion. "
            "Se completa despues de la primera pasada del INTERPRETE. "
            "None en la instruccion inicial del CPA."
        ),
    )
    examples: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "3 ejemplos concretos generados por el INTERPRETE que ilustran "
            "como aplicaria la politica. El CPA revisa estos ejemplos para "
            "confirmar que el INTERPRETE entendio correctamente su intencion."
        ),
    )
    awaiting_confirmation: bool = Field(
        default=True,
        description=(
            "True = el INTERPRETE espera confirmacion del CPA antes de activar. "
            "False = el CPA ya confirmo y la politica puede activarse."
        ),
    )
    resolves_pause_id: Optional[str] = Field(
        default=None,
        description="Si esta instruccion resuelve una pausa activa, su pause_id. Opcional.",
    )


# ---------------------------------------------------------------------------
# POLICY ACTIVATION
# ---------------------------------------------------------------------------

class PolicyActivation(BaseAgentMessage):
    """
    Politica activada por el INTERPRETE tras confirmacion del CPA.

    Una PolicyActivation convierte la instruccion informal del CPA en
    reglas formales del sistema que el CENTINELA puede aplicar.

    Una vez activada, la politica:
      1. Se almacena en la memoria del CENTINELA
      2. Puede liberar pausas que estaban esperando esta clarificacion
      3. Se aplica a transacciones futuras del mismo tipo

    El policy_id es inmutable y sirve como referencia para audit trail.
    """

    policy_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unico de esta politica activada",
    )
    from_instruction_id: str = Field(
        description="message_id del CPAInstruction que origino esta politica",
    )
    rules_json: dict[str, Any] = Field(
        description=(
            "Representacion JSON de las reglas derivadas de la instruccion del CPA. "
            "Estructura: {'conditions': [...], 'actions': [...], 'exceptions': [...]}. "
            "Este JSON es interpretable por el CENTINELA y el CLASIFICADOR."
        ),
    )
    effective_from: datetime = Field(
        description="Fecha y hora UTC desde la cual esta politica esta activa",
    )
    applied_to_pauses: tuple[str, ...] = Field(
        default_factory=tuple,
        description="pause_ids de las pausas liberadas al activar esta politica",
    )
    cpa_license: str = Field(
        description="Numero de licencia del CPA que confirmo la activacion de esta politica",
    )
    policy_description: str = Field(
        description="Descripcion legible de la politica para el audit trail",
        min_length=10,
    )


# ---------------------------------------------------------------------------
# EXPORTS
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "CentinelaDecision",
    "PauseStatus",
    "EntryType",
    "PauseTriggerType",
    "SourceFormat",
    # Base
    "BaseAgentMessage",
    # Mensajes
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
]
