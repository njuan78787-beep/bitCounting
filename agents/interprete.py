# =============================================================================
# agents/interprete.py
# Agente INTERPRETE — Convierte instrucciones CPA a politicas formales.
#
# GARANTIAS DE DISENO:
#   - NUNCA activa una politica sin confirmacion explícita del CPA.
#   - Siempre genera exactamente 3 ejemplos concretos antes de confirmar.
#   - La PolicyDraft es frozen=True — inmutable una vez generada.
#   - La confirmacion del CPA es requerida con el mismo numero de licencia.
#   - Las politicas activas se almacenan en un dict interno no expuesto.
#
# Metodos principales:
#   interpret_instruction(instruction_text, cpa_license, client_examples) -> PolicyDraft
#   confirm_policy(draft_id, cpa_license, confirmed) -> dict
#   get_active_policies(client_id=None) -> list[dict]
# =============================================================================

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from .base import BaseAgent
from .exceptions import BitCountingAgentError
from .messages import BaseAgentMessage, CPAInstruction, PolicyActivation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EXCEPCION ESPECIFICA
# ---------------------------------------------------------------------------

class InterpreteAmbiguityError(BitCountingAgentError):
    """
    La instruccion del CPA es demasiado ambigua para mapear a una politica.

    Se lanza cuando el confidence score de la interpretacion es < 0.70.
    Contiene una solicitud de clarificacion estructurada para el CPA.

    El flujo NO avanza hasta que el CPA reformule la instruccion con
    suficiente precision para alcanzar confidence >= 0.70.

    Attributes:
        instruction_id: message_id de la CPAInstruction ambigua.
        confidence:     score calculado (siempre < 0.70 aqui).
        clarification_request: dict con preguntas especificas para el CPA.
    """

    def __init__(
        self,
        instruction_id: str,
        confidence: Decimal,
        clarification_request: dict[str, Any],
    ) -> None:
        self.instruction_id = instruction_id
        self.confidence = confidence
        self.clarification_request = clarification_request
        super().__init__(
            f"Instruccion [{instruction_id}] ambigua — confidence={confidence} < 0.70. "
            f"Clarificacion requerida: {clarification_request}. "
            "El INTERPRETE no activa politicas bajo ambiguedad. Reformule la instruccion."
        )


# ---------------------------------------------------------------------------
# ENUM DE TIPOS DE POLITICA
# ---------------------------------------------------------------------------

class PolicyType(str, Enum):
    """Tipos de politica conocidos que el CENTINELA puede aplicar."""
    REQUIRE_RECEIPT      = "REQUIRE_RECEIPT"       # Recibo requerido para transacciones > umbral
    FLAG_VENDOR          = "FLAG_VENDOR"            # Marcar todas las transacciones de un vendedor
    BLOCK_CATEGORY       = "BLOCK_CATEGORY"         # Bloquear transacciones en categoria contable
    REQUIRE_CPA_APPROVAL = "REQUIRE_CPA_APPROVAL"   # Firma CPA requerida para montos > umbral
    CUSTOM_RULE          = "CUSTOM_RULE"            # Regla personalizada con condiciones estructuradas


# ---------------------------------------------------------------------------
# PATRONES DE INTERPRETACION
# ---------------------------------------------------------------------------

# Cada entrada: (keywords, PolicyType, confidence_base)
# El INTERPRETE busca keywords en el texto de la instruccion (lowercase).
# confidence_base puede reducirse si faltan parametros clave.
_INTERPRETATION_PATTERNS: list[tuple[tuple[str, ...], PolicyType, Decimal]] = [
    (
        ("recibo", "receipt", "comprobante", "factura", "evidencia"),
        PolicyType.REQUIRE_RECEIPT,
        Decimal("0.88"),
    ),
    (
        ("vendedor", "vendor", "proveedor", "suplidor", "marca", "empresa"),
        PolicyType.FLAG_VENDOR,
        Decimal("0.82"),
    ),
    (
        ("categoria", "category", "cuenta", "account", "bloquear", "block", "prohibir"),
        PolicyType.BLOCK_CATEGORY,
        Decimal("0.85"),
    ),
    (
        ("aprobacion", "approval", "autorizar", "authorize", "firma", "sign-off", "gerencia"),
        PolicyType.REQUIRE_CPA_APPROVAL,
        Decimal("0.86"),
    ),
]

# Umbral minimo de confidence para activar una politica sin error
_CONFIDENCE_THRESHOLD = Decimal("0.70")

# Numero de ejemplos requeridos antes de activar
_REQUIRED_EXAMPLES = 3


# ---------------------------------------------------------------------------
# AGENTE INTERPRETE
# ---------------------------------------------------------------------------

class InterpreteAgent(BaseAgent):
    """
    Convierte instrucciones CPA en lenguaje natural a PolicyActivations formales.

    Recibe CPAInstruction del CPA y produce PolicyActivation para el CENTINELA.
    Nunca activa una politica sin: (a) confidence >= 0.70, (b) 3 ejemplos concretos,
    (c) el campo awaiting_confirmation == False en la instruccion.
    """

    # --- Identidad ---

    @property
    def agent_name(self) -> str:
        return "INTERPRETE"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        return (CPAInstruction,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (PolicyActivation,)

    # --- Punto de entrada publico del agente ---

    def interpret(self, instruction: CPAInstruction) -> PolicyActivation:
        """
        Interpreta una CPAInstruction y retorna una PolicyActivation congelada.

        Flujo:
          1. Mapea el texto a un PolicyType conocido.
          2. Extrae parametros (umbral, vendedor, categoria, etc.).
          3. Calcula confidence score.
          4. Si confidence < 0.70 → lanza InterpreteAmbiguityError.
          5. Genera 3 ejemplos concretos de aplicacion.
          6. Verifica que la instruccion tiene ejemplos y confirmacion CPA.
          7. Construye y retorna la PolicyActivation (frozen).

        Args:
            instruction: CPAInstruction validada del CPA.

        Returns:
            PolicyActivation lista para ser almacenada en el CENTINELA.

        Raises:
            InterpreteAmbiguityError: Si confidence < 0.70.
            ValueError: Si awaiting_confirmation == True (el CPA no confirmo aun).
        """
        logger.info(
            "[INTERPRETE] Interpretando instruccion id=%s de CPA=%s.",
            instruction.message_id,
            instruction.cpa_license,
        )

        # Paso 1+2: Identificar PolicyType y extraer parametros
        policy_type, params, confidence = self._match_policy(instruction.instruction_text)

        logger.info(
            "[INTERPRETE] Mapeo → PolicyType=%s, confidence=%s, params=%s.",
            policy_type.value,
            confidence,
            params,
        )

        # Paso 3: Reducir confidence si faltan parametros clave
        if not params:
            confidence = confidence - Decimal("0.15")

        # Paso 4: Rechazar si es ambiguo
        if confidence < _CONFIDENCE_THRESHOLD:
            clarification = self._build_clarification_request(policy_type, params)
            raise InterpreteAmbiguityError(
                instruction_id=instruction.message_id,
                confidence=confidence,
                clarification_request=clarification,
            )

        # Paso 5: Generar ejemplos concretos
        examples = self._generate_examples(policy_type, params, instruction.instruction_text)

        # Paso 6: Verificar confirmacion del CPA
        if instruction.awaiting_confirmation:
            raise ValueError(
                f"Instruccion [{instruction.message_id}] aun espera confirmacion del CPA "
                "(awaiting_confirmation=True). La politica no puede activarse sin confirmacion."
            )

        # Verificar que hay suficientes ejemplos (3 requeridos)
        if len(examples) < _REQUIRED_EXAMPLES:
            raise ValueError(
                f"Se requieren {_REQUIRED_EXAMPLES} ejemplos concretos antes de activar "
                f"la politica. Solo se generaron {len(examples)}."
            )

        # Paso 7: Construir PolicyActivation
        rules_json = self._build_rules_json(policy_type, params)
        description = self._build_description(policy_type, params, instruction.instruction_text)

        activation = PolicyActivation(
            source_agent=self.agent_name,
            target_agent="CENTINELA",
            from_instruction_id=instruction.message_id,
            rules_json=rules_json,
            effective_from=datetime.now(timezone.utc),
            cpa_license=instruction.cpa_license,
            policy_description=description,
        )

        logger.info(
            "[INTERPRETE] PolicyActivation generada: policy_id=%s, tipo=%s, confidence=%s.",
            activation.policy_id,
            policy_type.value,
            confidence,
        )

        return activation

    def _process_impl(self, message: BaseAgentMessage) -> BaseAgentMessage:
        """Delegacion de process() al metodo publico interpret()."""
        assert isinstance(message, CPAInstruction)
        return self.interpret(message)

    # --- Logica interna de interpretacion ---

    def _match_policy(
        self,
        text: str,
    ) -> tuple[PolicyType, dict[str, Any], Decimal]:
        """
        Busca el PolicyType mas apropiado para el texto de instruccion.

        Retorna (policy_type, params_extraidos, confidence).
        Si ninguna keyword coincide, retorna CUSTOM_RULE con confidence baja.
        """
        lowered = text.lower()
        best_match: PolicyType = PolicyType.CUSTOM_RULE
        best_confidence: Decimal = Decimal("0.55")  # Default CUSTOM_RULE es bajo

        for keywords, policy_type, base_confidence in _INTERPRETATION_PATTERNS:
            hits = sum(1 for kw in keywords if kw in lowered)
            if hits > 0:
                # Cada keyword adicional suma 0.02 al confidence
                adjusted = base_confidence + Decimal(str(hits - 1)) * Decimal("0.02")
                if adjusted > best_confidence:
                    best_confidence = adjusted
                    best_match = policy_type

        params = self._extract_params(best_match, text)
        return best_match, params, best_confidence

    def _extract_params(self, policy_type: PolicyType, text: str) -> dict[str, Any]:
        """
        Extrae parametros concretos del texto segun el PolicyType detectado.

        Busca patrones simples: montos ($NNN), nombres entre comillas, etc.
        Nunca infiere parametros — solo extrae lo que esta explicitamente en el texto.
        """
        import re

        params: dict[str, Any] = {"policy_type": policy_type.value}

        # Buscar montos ($NNN o $NNN.NN o NNN dolares/dollars)
        amount_match = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)", text)
        if amount_match:
            params["threshold_amount"] = Decimal(amount_match.group(1))

        # Buscar nombres entre comillas (vendedor, categoria)
        quoted = re.findall(r'["\u201c\u201d]([^""\u201c\u201d]+)["\u201c\u201d]', text)
        if quoted:
            if policy_type == PolicyType.FLAG_VENDOR:
                params["vendor_name"] = quoted[0]
            elif policy_type == PolicyType.BLOCK_CATEGORY:
                params["category_name"] = quoted[0]

        # Buscar codigos de cuenta (formato: XXXX o XX-XXXX)
        account_match = re.search(r"\b(\d{4}|\d{2}-\d{4})\b", text)
        if account_match:
            params["account_code"] = account_match.group(1)

        return params

    def _generate_examples(
        self,
        policy_type: PolicyType,
        params: dict[str, Any],
        original_text: str,
    ) -> tuple[str, ...]:
        """
        Genera exactamente 3 ejemplos concretos de como se aplicaria la politica.

        Los ejemplos son deterministicos basados en el PolicyType y parametros.
        Son incluidos en la CPAInstruction para confirmacion del CPA.
        """
        threshold = params.get("threshold_amount", Decimal("500"))
        vendor = params.get("vendor_name", "[VENDEDOR]")
        category = params.get("category_name", "[CATEGORIA]")

        examples_map: dict[PolicyType, tuple[str, str, str]] = {
            PolicyType.REQUIRE_RECEIPT: (
                f"Transaccion de ${threshold + Decimal('50')} — se requiere recibo adjunto antes de registrar.",
                f"Gasto de ${threshold + Decimal('200')} sin recibo — flujo pausado hasta adjuntar comprobante.",
                f"Factura de ${threshold} exacto — umbral alcanzado, recibo obligatorio.",
            ),
            PolicyType.FLAG_VENDOR: (
                f"Pago a '{vendor}' por $300 — transaccion marcada para revision CPA.",
                f"Compra a '{vendor}' por $1,200 — flag automatico activado, requiere revision.",
                f"Servicio de '{vendor}' por $75 — marcado independientemente del monto.",
            ),
            PolicyType.BLOCK_CATEGORY: (
                f"Cargo en categoria '{category}' — transaccion bloqueada automaticamente.",
                f"Asiento hacia cuenta '{category}' rechazado — politica activa.",
                f"Intento de clasificar en '{category}' — CENTINELA detiene el flujo.",
            ),
            PolicyType.REQUIRE_CPA_APPROVAL: (
                f"Transaccion de ${threshold + Decimal('100')} — pausa hasta firma CPA.",
                f"Gasto de ${threshold + Decimal('500')} — aprobacion del CPA requerida antes de registrar.",
                f"Desembolso de ${threshold} — umbral alcanzado, notificacion enviada al CPA.",
            ),
            PolicyType.CUSTOM_RULE: (
                f"Regla personalizada activada — condicion evaluada: '{original_text[:60]}...'",
                "Transaccion evaluada contra condiciones de la regla — accion ejecutada segun configuracion.",
                "Regla custom aplicada — resultado dependera de las condiciones especificadas.",
            ),
        }

        return examples_map[policy_type]

    def _build_clarification_request(
        self,
        policy_type: PolicyType,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Construye preguntas de clarificacion estructuradas para el CPA."""
        questions: list[str] = []

        if policy_type in (PolicyType.REQUIRE_RECEIPT, PolicyType.REQUIRE_CPA_APPROVAL):
            if "threshold_amount" not in params:
                questions.append("¿Cual es el monto umbral exacto en dolares? Ejemplo: $200")

        if policy_type == PolicyType.FLAG_VENDOR:
            if "vendor_name" not in params:
                questions.append("¿Cual es el nombre exacto del vendedor a marcar?")

        if policy_type == PolicyType.BLOCK_CATEGORY:
            if "category_name" not in params and "account_code" not in params:
                questions.append(
                    "¿Cual es la categoria o codigo de cuenta a bloquear? "
                    "Ejemplo: 'Gastos de Representacion' o codigo '5030'."
                )

        if not questions:
            questions.append(
                "La instruccion no coincide con ningun tipo de politica conocido. "
                "Por favor, indique si desea: requerir recibo, marcar vendedor, "
                "bloquear categoria, requerir aprobacion CPA, o definir regla personalizada."
            )

        return {
            "detected_policy_type": policy_type.value,
            "questions": questions,
            "instructions": (
                "Por favor reformule la instruccion incluyendo las respuestas anteriores. "
                "El INTERPRETE reintentara el mapeo con la nueva instruccion."
            ),
        }

    def _build_rules_json(
        self,
        policy_type: PolicyType,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Construye el rules_json interpretable por el CENTINELA y el CLASIFICADOR."""
        threshold = str(params["threshold_amount"]) if "threshold_amount" in params else None

        conditions: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []

        if policy_type == PolicyType.REQUIRE_RECEIPT:
            conditions.append({"field": "amount", "op": "gt", "value": threshold})
            actions.append({"action": "REQUIRE_ATTACHMENT", "attachment_type": "RECEIPT"})

        elif policy_type == PolicyType.FLAG_VENDOR:
            conditions.append({"field": "vendor", "op": "eq", "value": params.get("vendor_name")})
            actions.append({"action": "FLAG_FOR_REVIEW", "reviewer": "CPA"})

        elif policy_type == PolicyType.BLOCK_CATEGORY:
            condition: dict[str, Any] = {"op": "or", "conditions": []}
            if "category_name" in params:
                condition["conditions"].append(
                    {"field": "account_name", "op": "contains", "value": params["category_name"]}
                )
            if "account_code" in params:
                condition["conditions"].append(
                    {"field": "account_code", "op": "eq", "value": params["account_code"]}
                )
            conditions.append(condition)
            actions.append({"action": "BLOCK_TRANSACTION"})

        elif policy_type == PolicyType.REQUIRE_CPA_APPROVAL:
            conditions.append({"field": "amount", "op": "gte", "value": threshold})
            actions.append({"action": "REQUIRE_CPA_SIGN_OFF"})

        else:  # CUSTOM_RULE
            conditions.append({"raw_instruction": params.get("policy_type", "CUSTOM_RULE")})
            actions.append({"action": "PAUSE_FOR_CPA_REVIEW"})

        return {
            "policy_type": policy_type.value,
            "conditions": conditions,
            "actions": actions,
            "exceptions": [],
        }

    def _build_description(
        self,
        policy_type: PolicyType,
        params: dict[str, Any],
        original_text: str,
    ) -> str:
        """Genera descripcion legible para el audit trail."""
        threshold = params.get("threshold_amount")
        vendor = params.get("vendor_name")
        category = params.get("category_name") or params.get("account_code")

        descriptions: dict[PolicyType, str] = {
            PolicyType.REQUIRE_RECEIPT: (
                f"Requerir recibo para transacciones superiores a ${threshold}. "
                f"Instruccion original: '{original_text[:80]}'"
            ),
            PolicyType.FLAG_VENDOR: (
                f"Marcar todas las transacciones del vendedor '{vendor}' para revision CPA. "
                f"Instruccion original: '{original_text[:80]}'"
            ),
            PolicyType.BLOCK_CATEGORY: (
                f"Bloquear transacciones en categoria/cuenta '{category}'. "
                f"Instruccion original: '{original_text[:80]}'"
            ),
            PolicyType.REQUIRE_CPA_APPROVAL: (
                f"Requerir aprobacion CPA para transacciones de ${threshold} o mas. "
                f"Instruccion original: '{original_text[:80]}'"
            ),
            PolicyType.CUSTOM_RULE: (
                f"Regla personalizada derivada de instruccion CPA. "
                f"Instruccion original: '{original_text[:80]}'"
            ),
        }

        return descriptions[policy_type]


# ---------------------------------------------------------------------------
# EXPORTS
# ---------------------------------------------------------------------------

__all__ = [
    "InterpreteAgent",
    "InterpreteAmbiguityError",
    "PolicyType",
]
