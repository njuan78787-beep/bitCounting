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
# POLICY DRAFT — inmutable, siempre awaiting_confirmation=True al crearse
# ---------------------------------------------------------------------------

class PolicyDraft(BaseModel):
    """
    Borrador de politica generado por el INTERPRETE.

    SIEMPRE tiene awaiting_confirmation=True hasta que el CPA llame a
    confirm_policy(). La politica NO esta activa mientras sea un draft.
    Es inmutable (frozen=True) — si se necesitan cambios, se genera un nuevo draft.
    """

    model_config = ConfigDict(frozen=True)

    draft_id:                     str
    instruction_text:             str
    cpa_license:                  str
    interpreted_as:               str    # Interpretacion en lenguaje humano
    policy_type:                  str    # CLASSIFICATION_OVERRIDE | THRESHOLD_CHANGE |
                                         # EXEMPTION_ADD | ACCOUNT_REMAP
    policy_rules:                 dict   # Reglas legibles por maquina
    examples:                     tuple[dict, ...]  # Exactamente 3 ejemplos
    awaiting_confirmation:        bool   # Siempre True hasta confirm_policy()
    confidence_in_interpretation: Decimal
    alternative_interpretations:  tuple[str, ...]


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
# CONSTANTES Y HELPERS PARA interpret_instruction / confirm_policy
# ---------------------------------------------------------------------------

# Tipos de politica para los metodos nuevos (strings, no Enum)
_ALL_POLICY_TYPES = [
    "THRESHOLD_CHANGE",
    "CLASSIFICATION_OVERRIDE",
    "EXEMPTION_ADD",
    "ACCOUNT_REMAP",
]

_POLICY_DESCRIPTIONS: dict[str, str] = {
    "THRESHOLD_CHANGE":        "Cambio de umbral monetario para clasificacion de transacciones",
    "CLASSIFICATION_OVERRIDE": "Override de clasificacion contable para proveedor o categoria",
    "EXEMPTION_ADD":           "Adicion de exencion fiscal para proveedor o categoria",
    "ACCOUNT_REMAP":           "Reclasificacion de transacciones a cuenta contable diferente",
}

# keywords → policy_type (string)
_POLICY_KEYWORDS: dict[tuple, str] = {
    ("capitaliz", "umbral", "limite", "threshold", "mayores a", "mayor de",
     "menos de", "por encima", "por debajo", "hasta $", "sobre $"): "THRESHOLD_CHANGE",
    ("siempre", "always", "son siempre", "clasifica como", "registrar como",
     "tratar como", "son gastos", "son inventario", "son activos"): "CLASSIFICATION_OVERRIDE",
    ("exento", "exempt", "exenci", "no aplica ivu", "sin ivu",
     "libre de", "exentos de", "exenta de"): "EXEMPTION_ADD",
    ("reclasifica", "reclassify", "mover a", "move to", "reasigna",
     "cambia a", "cambiar a cuenta", "registrar en"): "ACCOUNT_REMAP",
}


def _infer_policy_type(instruction_text: str) -> str:
    """Infiere el tipo de politica desde las palabras clave del texto."""
    text_lower = instruction_text.lower()
    best_score = 0
    best_type = "CLASSIFICATION_OVERRIDE"
    for keywords_tuple, ptype in _POLICY_KEYWORDS.items():
        score = sum(1 for kw in keywords_tuple if kw in text_lower)
        if score > best_score:
            best_score = score
            best_type = ptype
    return best_type


def _build_policy_rules(policy_type: str, instruction_text: str) -> dict[str, Any]:
    """Construye el dict de reglas legible por maquina."""
    text_lower = instruction_text.lower()

    if policy_type == "THRESHOLD_CHANGE":
        amounts = re.findall(r"\$[\d,]+(?:\.\d+)?|\b\d{3,}(?:\.\d+)?\b", instruction_text)
        threshold = amounts[0].replace("$", "").replace(",", "") if amounts else "1000"
        return {
            "type": "THRESHOLD_CHANGE",
            "threshold": threshold,
            "conditions": [{"field": "amount", "operator": "greater_than", "value": threshold}],
            "actions":    [{"type": "CAPITALIZE_ABOVE_THRESHOLD", "target": "1500"}],
            "exceptions": [],
        }

    if policy_type == "CLASSIFICATION_OVERRIDE":
        vendor_match = re.search(
            r"compras? (?:a|de|en) ([A-Za-z][A-Za-z0-9\s]+?)(?:\s+son|\s+siempre|\s+are)",
            instruction_text, re.IGNORECASE,
        )
        vendor = vendor_match.group(1).strip() if vendor_match else "VENDOR_NOT_SPECIFIED"
        target_account, target_name = "1200", "Inventario"
        if any(kw in text_lower for kw in ["gasto", "expense"]):
            target_account, target_name = "5900", "Gastos Varios"
        elif any(kw in text_lower for kw in ["activo", "asset", "equipo"]):
            target_account, target_name = "1500", "Propiedad, Planta y Equipo"
        return {
            "type":       "CLASSIFICATION_OVERRIDE",
            "vendor":     vendor,
            "conditions": [{"field": "vendor", "operator": "equals", "value": vendor}],
            "actions":    [{"type": "SET_ACCOUNT", "account": target_account, "name": target_name}],
            "exceptions": [],
        }

    if policy_type == "EXEMPTION_ADD":
        vendor_match = re.search(
            r"(?:de|para|from|of)\s+([A-Za-z][A-Za-z0-9\s]+?)(?:\s+est[aá]n|\s+are|\s*$)",
            instruction_text, re.IGNORECASE,
        )
        vendor = vendor_match.group(1).strip() if vendor_match else "VENDOR_NOT_SPECIFIED"
        tax_type = "IVU"
        return {
            "type":       "EXEMPTION_ADD",
            "vendor":     vendor,
            "tax_type":   tax_type,
            "conditions": [{"field": "vendor", "operator": "equals", "value": vendor}],
            "actions":    [{"type": "APPLY_EXEMPTION", "tax_type": tax_type, "exempt": True}],
            "exceptions": [],
        }

    if policy_type == "ACCOUNT_REMAP":
        target_account, target_name = "5900", "Gastos Varios"
        if any(kw in text_lower for kw in ["tecnolog", "technology", "tech"]):
            target_account, target_name = "5900", "Gastos de Tecnologia"
        elif any(kw in text_lower for kw in ["viaje", "travel"]):
            target_account, target_name = "5700", "Gastos de Viaje"
        vendor_match = re.search(
            r"(?:compras? de|purchases? from|de)\s+([A-Za-z][A-Za-z0-9\s]+?)(?:\s+como|\s+as|\s+a\s)",
            instruction_text, re.IGNORECASE,
        )
        vendor = vendor_match.group(1).strip() if vendor_match else "VENDOR_NOT_SPECIFIED"
        return {
            "type":       "ACCOUNT_REMAP",
            "vendor":     vendor,
            "conditions": [{"field": "vendor", "operator": "equals", "value": vendor}],
            "actions":    [{"type": "REMAP_TO_ACCOUNT", "account": target_account, "name": target_name}],
            "exceptions": [],
        }

    return {"type": policy_type, "conditions": [], "actions": [], "exceptions": []}


def _make_draft_example(
    policy_type: str,
    policy_rules: dict,
    vendor: str,
    amount: Decimal,
    real: bool,
) -> dict:
    """Construye un ejemplo individual para un PolicyDraft."""
    source = "dato real del cliente" if real else "ejemplo hipotetico"
    action = (policy_rules.get("actions") or [{}])[0]

    if policy_type == "THRESHOLD_CHANGE":
        threshold = Decimal(str(policy_rules.get("threshold", "1000")))
        applies = amount > threshold
        return {
            "source": source, "vendor": vendor, "amount": str(amount), "applies": applies,
            "before": f"Registrado como Gasto — ${amount}",
            "after": (f"Capitalizado como Activo (1500) — ${amount}" if applies
                      else f"Sin cambio — ${amount} <= umbral ${threshold}"),
            "reasoning": f"${amount} {'supera' if applies else 'no supera'} el umbral de ${threshold}.",
        }
    if policy_type == "CLASSIFICATION_OVERRIDE":
        rule_vendor = policy_rules.get("vendor", "")
        applies = bool(rule_vendor) and rule_vendor.lower() in vendor.lower()
        target = action.get("name", "Inventario")
        return {
            "source": source, "vendor": vendor, "amount": str(amount), "applies": applies,
            "before": "Clasificado automaticamente por CLASIFICADOR",
            "after": (f"Forzado a cuenta {action.get('account','1200')} ({target})" if applies
                      else f"Sin cambio — '{vendor}' no coincide con '{rule_vendor}'"),
            "reasoning": (f"'{vendor}' siempre se clasifica como {target}." if applies
                          else f"Override solo aplica a '{rule_vendor}'."),
        }
    if policy_type == "EXEMPTION_ADD":
        rule_vendor = policy_rules.get("vendor", "")
        tax_type = policy_rules.get("tax_type", "IVU")
        applies = bool(rule_vendor) and rule_vendor.lower() in vendor.lower()
        return {
            "source": source, "vendor": vendor, "amount": str(amount), "applies": applies,
            "before": f"{tax_type} calculado sobre ${amount}",
            "after": (f"Exento de {tax_type} — $0" if applies
                      else f"Sin cambio — '{vendor}' no cubierto por exencion"),
            "reasoning": (f"'{vendor}' esta exento de {tax_type}." if applies
                          else f"Exencion aplica solo a '{rule_vendor}'."),
        }
    if policy_type == "ACCOUNT_REMAP":
        rule_vendor = policy_rules.get("vendor", "")
        target = action.get("name", "Gastos Varios")
        applies = bool(rule_vendor) and rule_vendor.lower() in vendor.lower()
        return {
            "source": source, "vendor": vendor, "amount": str(amount), "applies": applies,
            "before": "Clasificado en cuenta original",
            "after": (f"Reclasificado a cuenta {action.get('account','5900')} ({target})" if applies
                      else f"Sin cambio — '{vendor}' no coincide con '{rule_vendor}'"),
            "reasoning": (f"Compras de '{vendor}' se remapean a {target}." if applies
                          else f"Remap aplica solo a '{rule_vendor}'."),
        }
    return {"source": source, "vendor": vendor, "amount": str(amount), "applies": False,
            "reasoning": "Ejemplo generado para politica generica."}


def _generate_examples_for_draft(
    policy_type: str,
    policy_rules: dict,
    instruction_text: str,
    client_examples: list[dict],
) -> tuple[dict, dict, dict]:
    """Genera exactamente 3 ejemplos para el PolicyDraft."""
    examples: list[dict] = []

    if client_examples:
        for raw in client_examples[:3]:
            vendor = str(raw.get("vendor", "Proveedor Ejemplo"))
            amount = Decimal(str(raw.get("amount", "500.00")))
            examples.append(_make_draft_example(policy_type, policy_rules, vendor, amount, real=True))

    hypothetical = [
        ("Costco Wholesale", Decimal("1500.00")),
        ("Amazon Business", Decimal("750.50")),
        ("Acme Corp Services", Decimal("2000.00")),
        ("Office Depot PR", Decimal("300.00")),
        ("Tech Solutions LLC", Decimal("1200.00")),
    ]
    idx = 0
    while len(examples) < 3 and idx < len(hypothetical):
        vendor, amount = hypothetical[idx]
        examples.append(_make_draft_example(policy_type, policy_rules, vendor, amount, real=False))
        idx += 1

    return (examples[0], examples[1], examples[2])


# ---------------------------------------------------------------------------
# AGENTE INTERPRETE
# ---------------------------------------------------------------------------

class InterpreteAgent(BaseAgent):
    """
    INTERPRETE — Convierte instrucciones CPA en lenguaje natural a politicas formales.

    Flujo obligatorio de dos pasos:
      1. interpret_instruction() → genera PolicyDraft con awaiting_confirmation=True
         El CPA revisa los 3 ejemplos del draft.
      2. confirm_policy(draft_id, cpa_license, confirmed=True) → activa la politica.

    La politica NUNCA se activa sin confirmacion explícita del CPA.

    Tambien expone interpret() para compatibilidad con el framework BaseAgent
    (cuando CPAInstruction ya viene con awaiting_confirmation=False).
    """

    def __init__(self) -> None:
        """Inicializa el INTERPRETE con registros vacios."""
        # Drafts pendientes: { draft_id → PolicyDraft }
        self._pending_drafts: dict[str, PolicyDraft] = {}
        # Politicas activadas: { policy_id → dict }
        self._active_policies: dict[str, dict] = {}

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

    # -------------------------------------------------------------------------
    # INTERPRET INSTRUCTION — paso 1 (genera draft, NO activa)
    # -------------------------------------------------------------------------

    def interpret_instruction(
        self,
        instruction_text: str,
        cpa_license: str,
        client_examples: list[dict],
    ) -> PolicyDraft:
        """
        Interpreta la instruccion del CPA y genera un PolicyDraft con 3 ejemplos.

        La politica NO esta activa tras este paso.
        awaiting_confirmation es siempre True en el draft retornado.

        Args:
            instruction_text:  Instruccion del CPA en lenguaje natural.
            cpa_license:       Numero de licencia del CPA.
            client_examples:   Transacciones reales del cliente para ejemplos concretos.
                               Si esta vacia, genera ejemplos hipoteticos.

        Returns:
            PolicyDraft inmutable con awaiting_confirmation=True y exactamente 3 ejemplos.
        """
        if not instruction_text or not instruction_text.strip():
            raise ValueError("instruction_text no puede estar vacio.")
        if not cpa_license or not cpa_license.strip():
            raise ValueError("cpa_license no puede estar vacio.")

        policy_type = _infer_policy_type(instruction_text)
        policy_rules = _build_policy_rules(policy_type, instruction_text)

        interpreted_as = (
            f"Politica tipo {policy_type}: {_POLICY_DESCRIPTIONS.get(policy_type, policy_type)}. "
            f"Instruccion: \"{instruction_text.strip()[:120]}\". "
            f"Condiciones: {len(policy_rules.get('conditions', []))}, "
            f"Acciones: {len(policy_rules.get('actions', []))}."
        )

        ex1, ex2, ex3 = _generate_examples_for_draft(
            policy_type, policy_rules, instruction_text, client_examples
        )

        text_lower = instruction_text.lower()
        matched = sum(
            1 for kws, pt in _POLICY_KEYWORDS.items()
            if pt == policy_type
            for kw in kws
            if kw in text_lower
        )
        confidence = min(
            Decimal("1.00"),
            Decimal("0.65") + Decimal(str(matched)) * Decimal("0.05"),
        )

        # Alternative interpretations (other policy types)
        other_types = [
            pt for pt in _ALL_POLICY_TYPES if pt != policy_type
        ]
        alt_interpretations = tuple(
            f"Alternativa: podria interpretarse como {pt} — "
            f"{_POLICY_DESCRIPTIONS.get(pt, pt)}"
            for pt in other_types[:2]
        )

        draft_id = str(uuid.uuid4())
        draft = PolicyDraft(
            draft_id=draft_id,
            instruction_text=instruction_text,
            cpa_license=cpa_license,
            interpreted_as=interpreted_as,
            policy_type=policy_type,
            policy_rules=policy_rules,
            examples=(ex1, ex2, ex3),
            awaiting_confirmation=True,       # ALWAYS True at creation
            confidence_in_interpretation=confidence,
            alternative_interpretations=alt_interpretations,
        )

        self._pending_drafts[draft_id] = draft

        logger.info(
            "[INTERPRETE] Draft generado: draft_id=%s tipo=%s confidence=%s cpa=%s.",
            draft_id, policy_type, confidence, cpa_license,
        )

        return draft

    # -------------------------------------------------------------------------
    # CONFIRM POLICY — paso 2 (activa o descarta)
    # -------------------------------------------------------------------------

    def confirm_policy(
        self,
        draft_id: str,
        cpa_license: str,
        confirmed: bool,
    ) -> dict:
        """
        Confirma o descarta un PolicyDraft pendiente.

        SOLO este metodo puede activar una politica.
        Una politica NUNCA se activa automaticamente.

        Args:
            draft_id:    ID del draft (generado por interpret_instruction).
            cpa_license: Licencia del CPA. Debe coincidir con la del draft.
            confirmed:   True para activar la politica. False para descartar.

        Returns:
            {"status": "ACTIVATED", "policy_id": str}  si confirmed=True.
            {"status": "DISCARDED"}                     si confirmed=False.

        Raises:
            KeyError:   Si draft_id no existe.
            ValueError: Si cpa_license no coincide con el draft.
        """
        if draft_id not in self._pending_drafts:
            raise KeyError(
                f"Draft '{draft_id}' no encontrado en drafts pendientes. "
                "Use interpret_instruction() primero."
            )

        draft = self._pending_drafts[draft_id]

        if draft.cpa_license != cpa_license:
            raise ValueError(
                f"cpa_license '{cpa_license}' no coincide con el draft "
                f"('{draft.cpa_license}'). Solo el CPA que creo el draft puede confirmarlo."
            )

        del self._pending_drafts[draft_id]

        if not confirmed:
            logger.info("[INTERPRETE] Draft descartado: draft_id=%s.", draft_id)
            return {"status": "DISCARDED"}

        policy_id = str(uuid.uuid4())
        policy_data: dict[str, Any] = {
            "policy_id":      policy_id,
            "draft_id":       draft_id,
            "cpa_license":    cpa_license,
            "policy_type":    draft.policy_type,
            "interpreted_as": draft.interpreted_as,
            "policy_rules":   draft.policy_rules,
            "activated_at":   datetime.now(timezone.utc).isoformat(),
            "client_id":      None,
        }
        self._active_policies[policy_id] = policy_data

        logger.info(
            "[INTERPRETE] Politica ACTIVADA: policy_id=%s tipo=%s cpa=%s.",
            policy_id, draft.policy_type, cpa_license,
        )

        return {"status": "ACTIVATED", "policy_id": policy_id}

    # -------------------------------------------------------------------------
    # GET ACTIVE POLICIES
    # -------------------------------------------------------------------------

    def get_active_policies(self, client_id: Optional[str] = None) -> list[dict]:
        """
        Retorna lista de politicas activas, opcionalmente filtradas por cliente.

        Args:
            client_id: Si se provee, filtra solo politicas para ese cliente.
                       Si es None, retorna todas las politicas activas.

        Returns:
            Lista de dicts con datos de cada politica activa.
        """
        policies = list(self._active_policies.values())
        if client_id is not None:
            policies = [
                p for p in policies
                if p.get("client_id") is None or p.get("client_id") == client_id
            ]
        return policies

    # -------------------------------------------------------------------------
    # INTERPRET — punto de entrada del framework (compatibilidad con BaseAgent)
    # -------------------------------------------------------------------------

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
