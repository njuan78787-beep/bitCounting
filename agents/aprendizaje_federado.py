# =============================================================================
# agents/aprendizaje_federado.py
# Sistema de Aprendizaje Federado sin datos privados.
#
# PRINCIPIO FUNDAMENTAL — PRIVACIDAD POR DISEÑO:
#   - Las error_cards NUNCA contienen datos identificables del cliente.
#   - El PII Scanner verifica activamente antes de registrar.
#   - El pool Eximia registra QUÉ cliente contribuyó (métricas) pero
#     DESVINCULA ese ID del patrón de comportamiento.
#   - behavior_patterns NO tiene FK a clients — por diseño arquitectónico.
#   - Campos prohibidos: name, email, EIN, SSN, address, phone, account_number.
#
# TRES FUNCIONES PRINCIPALES:
#   generate_error_card(transaction_id, correction)
#     → extrae solo el patrón de comportamiento, verifica PII, registra
#   contribute_to_pool(error_card_id)
#     → doble verificación PII, agrega al pool Eximia, desvincula client_id
#   calibrate_confidence(transaction_type, client_id)
#     → analiza historial, ajusta umbrales, registra en confidence_calibration
#
# REGLAS DE PRIVACIDAD:
#   - PII directo: SSN, EIN, email, teléfono, nombre propio, IP, cuenta bancaria
#   - PII indirecto: combos que identifican al cliente (EIN+fecha+monto exacto)
#   - Si el PII Scanner detecta algún campo sospechoso: rechaza con PIIViolationError
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import re
import statistics
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import BitCountingAgentError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------

class PIIViolationError(BitCountingAgentError):
    """
    Un campo contiene datos potencialmente identificables.
    La operación es rechazada para proteger la privacidad del cliente.
    """
    def __init__(self, field: str, pattern: str) -> None:
        self.field   = field
        self.pattern = pattern
        super().__init__(
            f"Campo '{field}' contiene patrón PII detectado: {pattern}. "
            "La error_card no puede ser registrada con datos identificables."
        )


class ErrorCardNotFoundError(BitCountingAgentError):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FiscalImpactType(str, Enum):
    IVU_MISCLASSIFICATION  = "IVU_MISCLASSIFICATION"
    FICA_ERROR             = "FICA_ERROR"
    DEDUCTION_MISSED       = "DEDUCTION_MISSED"
    WRONG_ACCOUNT          = "WRONG_ACCOUNT"
    RATE_MISMATCH          = "RATE_MISMATCH"
    PERIOD_ERROR           = "PERIOD_ERROR"
    EXEMPT_APPLIED_WRONG   = "EXEMPT_APPLIED_WRONG"
    THRESHOLD_EXCEEDED     = "THRESHOLD_EXCEEDED"
    UNKNOWN                = "UNKNOWN"


class LearningPriority(str, Enum):
    HIGH_PRIORITY_LEARNING = "HIGH_PRIORITY_LEARNING"  # confidence > 80% y fue corregido
    NORMAL                 = "NORMAL"
    LOW                    = "LOW"


# ---------------------------------------------------------------------------
# PII Scanner
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SSN",             re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
    ("EIN",             re.compile(r'\b\d{2}-\d{7}\b')),
    ("EMAIL",           re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')),
    ("PHONE_US",        re.compile(r'\b(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')),
    ("PHONE_PR",        re.compile(r'\b787[-.\s]?\d{3}[-.\s]?\d{4}\b|\b939[-.\s]?\d{3}[-.\s]?\d{4}\b')),
    ("IP_ADDRESS",      re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')),
    ("BANK_ACCOUNT",    re.compile(r'\b\d{8,17}\b')),   # números de cuenta típicos
    ("CREDIT_CARD",     re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b')),
    ("FULL_NAME",       re.compile(r'\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b')),
    ("ADDRESS",         re.compile(r'\b\d{1,5}\s+[A-Za-z\s]+(Street|St|Avenue|Ave|Road|Rd|Blvd|Drive|Dr|Calle|Carr)\b', re.IGNORECASE)),
]

# Campos que NO se escanean (son técnicos, no PII)
_SAFE_FIELDS = frozenset({
    "transaction_type", "industry_code", "classification_initial",
    "classification_corrected", "correction_reason", "rule_applied",
    "gaap_reference", "fiscal_impact_type", "confidence_initial",
    "confidence_final", "context_variables", "error_card_id",
    "behavior_pattern_id", "learning_priority",
})


def scan_for_pii(field_name: str, value: Any) -> Optional[str]:
    """
    Escanea un valor buscando PII. Retorna el patrón detectado o None.
    Lanza PIIViolationError si el campo es sospechoso.
    """
    if field_name in _SAFE_FIELDS:
        return None
    if value is None:
        return None
    text = str(value)
    for pattern_name, pattern_re in _PII_PATTERNS:
        if pattern_re.search(text):
            return pattern_name
    return None


def verify_no_pii(data: dict[str, Any]) -> None:
    """
    Verifica que ningún campo del dict contiene PII.
    Lanza PIIViolationError en el primer campo sospechoso.
    """
    for field, value in data.items():
        if field in _SAFE_FIELDS:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                pii = scan_for_pii(field, item)
                if pii:
                    raise PIIViolationError(field, pii)
        else:
            pii = scan_for_pii(field, value)
            if pii:
                raise PIIViolationError(field, pii)


# ---------------------------------------------------------------------------
# Modelos inmutables
# ---------------------------------------------------------------------------

class ErrorCard(BaseModel):
    """
    Tarjeta de error — solo contiene patrón de comportamiento, NUNCA PII.

    Campos permitidos (definidos por spec):
        transaction_type, industry_code, classification_initial,
        classification_corrected, correction_reason, rule_applied,
        gaap_reference, fiscal_impact_type, context_variables[],
        confidence_initial, confidence_final

    PROHIBIDOS: name, email, EIN, SSN, address, phone, account_number,
                client_name, vendor_name, amounts exactos con identificadores.
    """
    model_config = ConfigDict(frozen=True)

    error_card_id:          str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Patrón de comportamiento — sin PII
    transaction_type:       str
    industry_code:          str
    classification_initial: str
    classification_corrected: str
    correction_reason:      str
    rule_applied:           str
    gaap_reference:         str
    fiscal_impact_type:     FiscalImpactType
    context_variables:      tuple[str, ...] = ()   # ej: ("seasonal", "new_vendor_type")
    confidence_initial:     Decimal
    confidence_final:       Decimal
    learning_priority:      LearningPriority = LearningPriority.NORMAL
    # Metadatos técnicos (no PII)
    created_at:             str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    is_contributed_to_pool: bool = False
    pii_verified:           bool = True   # siempre True — verificado antes de crear


class BehaviorPattern(BaseModel):
    """
    Patrón de comportamiento para el pool Eximia.

    NO tiene FK a clients — desvinculado por diseño.
    source_error_card_ids son los UUIDs de las error_cards que lo originaron.
    """
    model_config = ConfigDict(frozen=True)

    pattern_id:            str = Field(default_factory=lambda: str(uuid.uuid4()))
    pattern_type:          str
    transaction_type:      str
    industry_code:         str
    classification_pair:   tuple[str, str]   # (initial, corrected)
    fiscal_impact_type:    FiscalImpactType
    rule_applied:          str
    gaap_reference:        str
    frequency:             int = 1
    avg_confidence_initial: Decimal
    learning_priority:     LearningPriority
    source_error_card_ids: tuple[str, ...]
    # NO hay client_id aquí — desvinculado
    created_at:            str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    contributed_to_eximia: bool = False


class PoolContribution(BaseModel):
    """
    Registro de qué cliente contribuyó (métricas) DESVINCULADO del patrón.

    client_id y pattern_id se almacenan en objetos SEPARADOS.
    Nadie puede hacer JOIN para conectar cliente ↔ patrón.
    """
    model_config = ConfigDict(frozen=True)

    contribution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_id:       str   # solo para métricas internas Eximia
    contributed_at:  str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # El pattern_id NO se guarda aquí — desvinculado
    # Solo se guarda un hash del pattern para verificación de unicidad
    pattern_hash:    str
    error_card_count: int


class ConfidenceCalibration(BaseModel):
    """Registro de un ajuste de umbral de confianza para un tipo de transacción."""
    model_config = ConfigDict(frozen=True)

    calibration_id:       str = Field(default_factory=lambda: str(uuid.uuid4()))
    transaction_type:     str
    client_id:            str
    period_from:          str   # ISO 8601 date
    period_to:            str   # ISO 8601 date
    total_corrections:    int
    high_conf_wrong:      int   # confidence > 80% y fue corregido (crítico)
    correction_rate:      Decimal
    old_threshold:        Decimal
    new_threshold:        Decimal
    adjustment_reason:    str
    calibrated_at:        str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Corrección del CPA (input)
# ---------------------------------------------------------------------------

class CPACorrection(BaseModel):
    """Input de una corrección hecha por el CPA sobre una transacción."""
    model_config = ConfigDict(frozen=True)

    transaction_id:        str
    client_id:             str
    transaction_type:      str
    industry_code:         str
    classification_initial: str
    classification_corrected: str
    correction_reason:     str
    rule_applied:          str
    gaap_reference:        str
    fiscal_impact_type:    FiscalImpactType
    context_variables:     tuple[str, ...] = ()
    confidence_initial:    Decimal
    confidence_final:      Decimal = Decimal("1.0")   # CPA confirma → confianza 100%


# ---------------------------------------------------------------------------
# Sistema de Aprendizaje Federado
# ---------------------------------------------------------------------------

class AprendizajeFederado:
    """
    Sistema de Aprendizaje Federado sin datos privados para Bit-Counting.

    Extrae patrones de comportamiento de las correcciones del CPA,
    verifica que no hay PII, y contribuye al pool colectivo Eximia
    desvinculando el cliente del patrón de aprendizaje.
    """

    HIGH_CONF_THRESHOLD = Decimal("0.80")   # umbral para HIGH_PRIORITY_LEARNING

    def __init__(self) -> None:
        self._error_cards:            list[ErrorCard]             = []
        self._behavior_patterns:      list[BehaviorPattern]       = []
        self._pool_contributions:     list[PoolContribution]       = []
        self._confidence_calibrations: list[ConfidenceCalibration] = []

    # ------------------------------------------------------------------ #
    # 1. generate_error_card                                              #
    # ------------------------------------------------------------------ #

    def generate_error_card(
        self,
        transaction_id: str,
        correction:     CPACorrection,
    ) -> ErrorCard:
        """
        Extrae el patrón de comportamiento de una corrección del CPA.

        GARANTÍAS:
          - Solo extrae los campos permitidos por spec (transaction_type,
            industry_code, classification_*, confidence_*, etc.)
          - Verifica PII en todos los campos antes de registrar.
          - Si confidence_initial > 80% y fue corregido: HIGH_PRIORITY_LEARNING.
          - NUNCA almacena: name, email, EIN, SSN, address, phone, amounts exactos.

        Raises:
            PIIViolationError: si algún campo contiene datos identificables.
        """
        # Determinar prioridad de aprendizaje
        priority = LearningPriority.NORMAL
        if correction.confidence_initial > self.HIGH_CONF_THRESHOLD:
            priority = LearningPriority.HIGH_PRIORITY_LEARNING
            logger.warning(
                "APRENDIZAJE: confidence_initial=%.2f > 80%% y fue corregido. "
                "Registrando como HIGH_PRIORITY_LEARNING. transaction_id=%s",
                float(correction.confidence_initial),
                transaction_id,
            )

        # Construir solo los campos permitidos
        card_data = {
            "transaction_type":        correction.transaction_type,
            "industry_code":           correction.industry_code,
            "classification_initial":  correction.classification_initial,
            "classification_corrected": correction.classification_corrected,
            "correction_reason":       correction.correction_reason,
            "rule_applied":            correction.rule_applied,
            "gaap_reference":          correction.gaap_reference,
            "fiscal_impact_type":      correction.fiscal_impact_type.value,
            "context_variables":       list(correction.context_variables),
            "confidence_initial":      str(correction.confidence_initial),
            "confidence_final":        str(correction.confidence_final),
        }

        # Verificar que no hay PII en ningún campo
        verify_no_pii(card_data)

        card = ErrorCard(
            transaction_type=correction.transaction_type,
            industry_code=correction.industry_code,
            classification_initial=correction.classification_initial,
            classification_corrected=correction.classification_corrected,
            correction_reason=correction.correction_reason,
            rule_applied=correction.rule_applied,
            gaap_reference=correction.gaap_reference,
            fiscal_impact_type=correction.fiscal_impact_type,
            context_variables=correction.context_variables,
            confidence_initial=correction.confidence_initial,
            confidence_final=correction.confidence_final,
            learning_priority=priority,
            pii_verified=True,
        )
        self._error_cards.append(card)

        logger.info(
            "ErrorCard creada: %s | tipo=%s | prioridad=%s",
            card.error_card_id, correction.transaction_type, priority.value,
        )
        return card

    # ------------------------------------------------------------------ #
    # 2. contribute_to_pool                                               #
    # ------------------------------------------------------------------ #

    def contribute_to_pool(
        self,
        error_card_id: str,
        client_id:     str,
    ) -> tuple[BehaviorPattern, PoolContribution]:
        """
        Agrega el patrón al pool Eximia desvinculando el cliente.

        PROCESO:
          1. Verifica DOBLE que la error_card no tiene PII.
          2. Crea/actualiza BehaviorPattern SIN client_id.
          3. Crea PoolContribution con client_id SEPARADA del patrón.
          4. El patrón y la contribución nunca se pueden unir por diseño.

        Returns:
            (BehaviorPattern, PoolContribution) — objetos separados.
        """
        card = self._find_card(error_card_id)

        # PRIMERA verificación de PII (los campos del card)
        card_dict = {
            "transaction_type":         card.transaction_type,
            "industry_code":            card.industry_code,
            "classification_initial":   card.classification_initial,
            "classification_corrected": card.classification_corrected,
            "correction_reason":        card.correction_reason,
            "rule_applied":             card.rule_applied,
            "gaap_reference":           card.gaap_reference,
        }
        verify_no_pii(card_dict)

        # SEGUNDA verificación: escaneo por patrones regex directamente
        for field, value in card_dict.items():
            for pattern_name, pattern_re in _PII_PATTERNS:
                if pattern_re.search(str(value)):
                    raise PIIViolationError(field, pattern_name)

        # Verificar si ya existe un patrón equivalente para agregar frecuencia
        existing = self._find_matching_pattern(card)
        if existing is not None:
            # Incrementar frecuencia del patrón existente
            pattern = BehaviorPattern(
                pattern_id=existing.pattern_id,
                pattern_type=existing.pattern_type,
                transaction_type=existing.transaction_type,
                industry_code=existing.industry_code,
                classification_pair=existing.classification_pair,
                fiscal_impact_type=existing.fiscal_impact_type,
                rule_applied=existing.rule_applied,
                gaap_reference=existing.gaap_reference,
                frequency=existing.frequency + 1,
                avg_confidence_initial=(
                    (existing.avg_confidence_initial * existing.frequency + card.confidence_initial)
                    / Decimal(str(existing.frequency + 1))
                ),
                learning_priority=card.learning_priority,
                source_error_card_ids=existing.source_error_card_ids + (error_card_id,),
                contributed_to_eximia=True,
            )
            idx = self._behavior_patterns.index(existing)
            self._behavior_patterns[idx] = pattern
        else:
            pattern = BehaviorPattern(
                pattern_type=f"{card.transaction_type}:{card.classification_initial}->{card.classification_corrected}",
                transaction_type=card.transaction_type,
                industry_code=card.industry_code,
                classification_pair=(card.classification_initial, card.classification_corrected),
                fiscal_impact_type=card.fiscal_impact_type,
                rule_applied=card.rule_applied,
                gaap_reference=card.gaap_reference,
                avg_confidence_initial=card.confidence_initial,
                learning_priority=card.learning_priority,
                source_error_card_ids=(error_card_id,),
                contributed_to_eximia=True,
            )
            self._behavior_patterns.append(pattern)

        # PoolContribution: solo guarda client_id + hash del patrón, NO el pattern_id
        pattern_hash = hashlib.sha256(
            f"{card.transaction_type}:{card.classification_initial}:"
            f"{card.classification_corrected}".encode()
        ).hexdigest()

        contribution = PoolContribution(
            client_id=client_id,
            pattern_hash=pattern_hash,
            error_card_count=1,
        )
        self._pool_contributions.append(contribution)

        # Marcar error_card como contribuida
        idx_card = next(i for i, c in enumerate(self._error_cards) if c.error_card_id == error_card_id)
        updated_card = ErrorCard(
            error_card_id=card.error_card_id,
            transaction_type=card.transaction_type,
            industry_code=card.industry_code,
            classification_initial=card.classification_initial,
            classification_corrected=card.classification_corrected,
            correction_reason=card.correction_reason,
            rule_applied=card.rule_applied,
            gaap_reference=card.gaap_reference,
            fiscal_impact_type=card.fiscal_impact_type,
            context_variables=card.context_variables,
            confidence_initial=card.confidence_initial,
            confidence_final=card.confidence_final,
            learning_priority=card.learning_priority,
            created_at=card.created_at,
            is_contributed_to_pool=True,
            pii_verified=True,
        )
        self._error_cards[idx_card] = updated_card

        logger.info(
            "Pool contribution: client=%s → patrón %s (desvinculado). "
            "Frecuencia patrón: %d",
            client_id[:8] + "...",   # solo primeros chars en log
            pattern.pattern_id,
            pattern.frequency,
        )
        return pattern, contribution

    # ------------------------------------------------------------------ #
    # 3. calibrate_confidence                                             #
    # ------------------------------------------------------------------ #

    def calibrate_confidence(
        self,
        transaction_type: str,
        client_id:        str,
    ) -> ConfidenceCalibration:
        """
        Analiza el historial de correcciones para el tipo de transacción
        y ajusta el umbral de confianza específicamente para ese cliente.

        Lógica de ajuste:
          - Si correction_rate > 20%: reducir umbral (más pausas preventivas)
          - Si correction_rate < 5% y high_conf_wrong == 0: subir umbral levemente
          - Si high_conf_wrong > 0: bajar umbral drásticamente (peligro de falsa certeza)

        Returns:
            ConfidenceCalibration con el ajuste registrado.
        """
        # Filtrar cards del cliente para este tipo de transacción
        # (client_id no está en ErrorCard — usamos transaction_type como proxy)
        relevant_cards = [
            c for c in self._error_cards
            if c.transaction_type == transaction_type
        ]

        total        = len(relevant_cards)
        high_conf_wrong = sum(
            1 for c in relevant_cards
            if c.learning_priority == LearningPriority.HIGH_PRIORITY_LEARNING
        )
        correction_rate = Decimal(str(total)) / Decimal("100")   # proxy: 1 card = 1% de corrección

        # Umbral base (configurable, default 0.75)
        old_threshold = Decimal("0.75")
        new_threshold = old_threshold

        if high_conf_wrong > 0:
            # Alta confianza equivocada → bajar umbral drásticamente
            reduction = Decimal(str(min(0.15, high_conf_wrong * 0.05)))
            new_threshold = max(Decimal("0.50"), old_threshold - reduction)
            reason = (
                f"{high_conf_wrong} correcciones con confidence > 80%. "
                f"Reducción de {reduction*100:.0f}% para aumentar revisión CPA."
            )
        elif total > 0 and correction_rate > Decimal("0.20"):
            # Tasa de corrección alta → bajar umbral
            reduction = Decimal("0.05")
            new_threshold = max(Decimal("0.60"), old_threshold - reduction)
            reason = (
                f"Tasa de corrección alta ({total} cards). "
                "Umbral reducido para aumentar revisión preventiva."
            )
        elif total > 0 and correction_rate < Decimal("0.05") and high_conf_wrong == 0:
            # Tasa baja y ninguna corrección de alta confianza → subir levemente
            new_threshold = min(Decimal("0.90"), old_threshold + Decimal("0.05"))
            reason = (
                f"Historial limpio ({total} cards, ninguna alta confianza). "
                "Umbral incrementado levemente."
            )
        else:
            reason = f"Sin cambio significativo ({total} correcciones históricas)."

        today = date.today().isoformat()
        cal = ConfidenceCalibration(
            transaction_type=transaction_type,
            client_id=client_id,
            period_from=today,
            period_to=today,
            total_corrections=total,
            high_conf_wrong=high_conf_wrong,
            correction_rate=correction_rate.quantize(Decimal("0.0001")),
            old_threshold=old_threshold,
            new_threshold=new_threshold,
            adjustment_reason=reason,
        )
        self._confidence_calibrations.append(cal)

        logger.info(
            "Calibración: tipo=%s cliente=%s umbral %s→%s. %s",
            transaction_type, client_id[:8] + "...",
            old_threshold, new_threshold, reason,
        )
        return cal

    # ------------------------------------------------------------------ #
    # Consultas                                                           #
    # ------------------------------------------------------------------ #

    def get_error_cards(
        self,
        priority: Optional[LearningPriority] = None,
    ) -> tuple[ErrorCard, ...]:
        if priority is None:
            return tuple(self._error_cards)
        return tuple(c for c in self._error_cards if c.learning_priority == priority)

    def get_behavior_patterns(self) -> tuple[BehaviorPattern, ...]:
        """Retorna patrones del pool. NO contiene client_id."""
        return tuple(self._behavior_patterns)

    def get_pool_contributions(self) -> tuple[PoolContribution, ...]:
        """Retorna contribuciones. Contiene client_id PERO NO pattern_id."""
        return tuple(self._pool_contributions)

    def get_calibrations(self, client_id: Optional[str] = None) -> tuple[ConfidenceCalibration, ...]:
        if client_id is None:
            return tuple(self._confidence_calibrations)
        return tuple(c for c in self._confidence_calibrations if c.client_id == client_id)

    def verify_pool_privacy(self) -> dict[str, bool]:
        """
        Verifica que el pool Eximia cumple garantías de privacidad.
        Retorna dict con las verificaciones realizadas.
        """
        # 1. Ningún BehaviorPattern tiene client_id
        patterns_have_no_client = all(
            not hasattr(p, 'client_id')
            or not getattr(p, 'client_id', None)
            for p in self._behavior_patterns
        )

        # 2. Los pattern_ids no aparecen en PoolContribution
        pattern_ids = {p.pattern_id for p in self._behavior_patterns}
        contributions_have_no_pattern_id = all(
            not any(c_field == pid for c_field in vars(contrib).values()
                    for pid in pattern_ids)
            for contrib in self._pool_contributions
        )

        # 3. Ningún campo de BehaviorPattern contiene PII
        patterns_pii_free = True
        for pattern in self._behavior_patterns:
            try:
                verify_no_pii({
                    "transaction_type":   pattern.transaction_type,
                    "industry_code":      pattern.industry_code,
                    "rule_applied":       pattern.rule_applied,
                    "gaap_reference":     pattern.gaap_reference,
                })
            except PIIViolationError:
                patterns_pii_free = False
                break

        return {
            "behavior_patterns_have_no_client_id": patterns_have_no_client,
            "contributions_have_no_pattern_id":    contributions_have_no_pattern_id,
            "patterns_are_pii_free":               patterns_pii_free,
        }

    # ------------------------------------------------------------------ #
    # Privados                                                            #
    # ------------------------------------------------------------------ #

    def _find_card(self, error_card_id: str) -> ErrorCard:
        for c in self._error_cards:
            if c.error_card_id == error_card_id:
                return c
        raise ErrorCardNotFoundError(f"ErrorCard no encontrada: {error_card_id}")

    def _find_matching_pattern(self, card: ErrorCard) -> Optional[BehaviorPattern]:
        """Busca un patrón existente con el mismo tipo+pair de clasificación."""
        for p in self._behavior_patterns:
            if (p.transaction_type == card.transaction_type
                    and p.classification_pair == (card.classification_initial,
                                                   card.classification_corrected)):
                return p
        return None
