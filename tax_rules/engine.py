# =============================================================================
# tax_rules/engine.py
# Funciones de consulta del modulo de reglas fiscales.
# SOLO LECTURA — ninguna funcion modifica el registro.
#
# Funciones publicas para agentes:
#   get_rule(rule_id, date)           -> TaxRule o None
#   get_rule_confidence(rule_id)      -> float [0.0, 1.0]
#   check_rule_contradiction(...)     -> ContradictionResult
#   list_active_rules(date)           -> tuple[TaxRule, ...]
#   calculate_ivu(base, date, municipality) -> IVUCalculation
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .constants import (
    ContradictionType,
    CONFIDENCE_WEIGHT_AGE,
    CONFIDENCE_WEIGHT_APPLICATIONS,
    CONFIDENCE_WEIGHT_CPA_VALIDATION,
    CONFIDENCE_WEIGHT_CONTROVERSY,
)
from .models import TaxRule
from .registry import RULES_REGISTRY, _get_all_versions, get_rules_by_tax_type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TIPOS DE RETORNO
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContradictionResult:
    """Resultado del analisis de contradiccion entre dos reglas."""
    has_contradiction: bool
    contradiction_type: ContradictionType
    rule_id_new: str
    rule_id_old: str
    details: str
    affected_date_range: Optional[tuple[date, date]]
    recommended_action: str


@dataclass(frozen=True)
class IVUCalculation:
    """Resultado de un calculo de IVU — siempre con trazabilidad completa."""
    base_amount: Decimal
    ivu_estatal_rate: Decimal
    ivu_municipal_rate: Decimal
    ivu_estatal_amount: Decimal
    ivu_municipal_amount: Decimal
    total_ivu: Decimal
    total_with_ivu: Decimal
    rule_id_estatal: str
    rule_id_municipal: str
    calculation_date: date
    calc_hash: str  # hash del calculo para audit trail


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """Desglose del calculo de confidence para trazabilidad."""
    rule_id: str
    final_score: float
    age_score: float
    application_score: float
    cpa_validation_score: float
    controversy_score: float
    age_days: int
    times_applied: int
    validated_by_cpa: bool
    has_controversy: bool


# ---------------------------------------------------------------------------
# get_rule — consulta por fecha especifica
# ---------------------------------------------------------------------------

def get_rule(rule_id: str, query_date: date) -> Optional[TaxRule]:
    """
    Retorna la regla vigente en la fecha indicada.

    GARANTIA: Nunca retorna la regla mas reciente por defecto.
    Siempre evalua vigencia en la fecha exacta solicitada.

    Args:
        rule_id:    Identificador de la regla.
        query_date: Fecha en la cual se necesita la regla vigente.

    Returns:
        TaxRule si existe una version vigente en esa fecha, None si no.
    """
    versions = _get_all_versions(rule_id)
    if not versions:
        logger.warning("get_rule: rule_id '%s' no encontrado en el registro.", rule_id)
        return None

    # Filtrar solo versiones vigentes en query_date
    active = [v for v in versions if v.is_active_on(query_date)]

    if not active:
        logger.info(
            "get_rule: '%s' no tiene version vigente en %s. "
            "Versiones disponibles: %s",
            rule_id,
            query_date,
            [(v.version, v.effective_date, v.expiry_date) for v in versions],
        )
        return None

    if len(active) > 1:
        # Anomalia — no deberia haber dos versiones activas al mismo tiempo
        # Retornar la de effective_date mas reciente y loguear la anomalia
        logger.error(
            "get_rule: ANOMALIA — '%s' tiene %d versiones activas en %s. "
            "Retornando la mas reciente. Requiere revision manual.",
            rule_id,
            len(active),
            query_date,
        )
        active.sort(key=lambda r: r.effective_date, reverse=True)

    return active[0]


def get_rule_strict(rule_id: str, query_date: date) -> TaxRule:
    """
    Igual que get_rule pero lanza ValueError si no encuentra la regla.
    Usar cuando la regla es absolutamente requerida para continuar el flujo.
    """
    rule = get_rule(rule_id, query_date)
    if rule is None:
        raise ValueError(
            f"Regla requerida '{rule_id}' no encontrada para la fecha {query_date}. "
            "El flujo no puede continuar sin esta regla."
        )
    return rule


def list_active_rules(query_date: date) -> tuple[TaxRule, ...]:
    """Retorna todas las reglas vigentes en la fecha indicada."""
    active = []
    for versions in RULES_REGISTRY.values():
        for rule in versions:
            if rule.is_active_on(query_date):
                active.append(rule)
    return tuple(active)


# ---------------------------------------------------------------------------
# get_rule_confidence — indice de confiabilidad desde metadata
# ---------------------------------------------------------------------------

def get_rule_confidence(rule_id: str, query_date: Optional[date] = None) -> float:
    """
    Calcula el indice de confiabilidad de una regla basado en su metadata.

    El score NO mide la seguridad del modelo LLM — mide evidencia objetiva
    de certeza segun el blueprint de Bit-Counting (Capa 4: Memoria de Fuente).

    Componentes:
      - Antiguedad: reglas mas viejas = mas probadas = mayor confianza
      - Aplicaciones: numero de veces aplicada exitosamente
      - Validacion CPA: si fue validada y hace cuanto
      - Controversia: penalizacion si existe interpretacion alternativa

    Returns:
        float entre 0.0 y 1.0
    """
    if query_date is None:
        query_date = date.today()

    rule = get_rule(rule_id, query_date)
    if rule is None:
        logger.warning("get_rule_confidence: regla '%s' no encontrada.", rule_id)
        return 0.0

    breakdown = _compute_confidence_breakdown(rule, query_date)
    return breakdown.final_score


def get_rule_confidence_breakdown(
    rule_id: str,
    query_date: Optional[date] = None,
) -> Optional[ConfidenceBreakdown]:
    """Retorna el desglose completo del confidence para trazabilidad."""
    if query_date is None:
        query_date = date.today()
    rule = get_rule(rule_id, query_date)
    if rule is None:
        return None
    return _compute_confidence_breakdown(rule, query_date)


def _compute_confidence_breakdown(rule: TaxRule, today: date) -> ConfidenceBreakdown:
    """Calculo interno del confidence score con desglose completo."""

    age_days = (today - rule.effective_date).days

    # --- Componente 1: Antiguedad (0.0 a 1.0) ---
    if age_days > 1095:      # > 3 anos
        age_score = 1.0
    elif age_days > 730:     # 2-3 anos
        age_score = 0.92
    elif age_days > 365:     # 1-2 anos
        age_score = 0.82
    elif age_days > 180:     # 6-12 meses
        age_score = 0.70
    elif age_days > 90:      # 3-6 meses
        age_score = 0.55
    else:                    # < 3 meses — regla muy reciente
        age_score = 0.35

    # --- Componente 2: Aplicaciones exitosas (0.0 a 1.0) ---
    apps = rule.times_applied
    if apps > 10000:
        application_score = 1.0
    elif apps > 1000:
        application_score = 0.95
    elif apps > 100:
        application_score = 0.88
    elif apps > 10:
        application_score = 0.75
    elif apps > 0:
        application_score = 0.60
    else:
        # Sin aplicaciones aun — penalizacion moderada, no critica
        application_score = 0.45

    # --- Componente 3: Validacion CPA (0.0 a 1.0) ---
    if not rule.validated_by_cpa or rule.validation_date is None:
        cpa_validation_score = 0.50  # Penalizacion por falta de validacion CPA
    else:
        validation_age_days = (today - rule.validation_date).days
        if validation_age_days < 180:        # Validado hace menos de 6 meses
            cpa_validation_score = 1.0
        elif validation_age_days < 365:      # 6-12 meses
            cpa_validation_score = 0.92
        elif validation_age_days < 730:      # 1-2 anos
            cpa_validation_score = 0.80
        else:                                # > 2 anos sin re-validar
            cpa_validation_score = 0.65

    # --- Componente 4: Controversia (0.0 a 1.0) ---
    controversy_score = 0.50 if rule.has_controversy else 1.0

    # --- Score final ponderado ---
    final = (
        age_score * CONFIDENCE_WEIGHT_AGE
        + application_score * CONFIDENCE_WEIGHT_APPLICATIONS
        + cpa_validation_score * CONFIDENCE_WEIGHT_CPA_VALIDATION
        + controversy_score * CONFIDENCE_WEIGHT_CONTROVERSY
    )

    # Clamp a [0.0, 1.0] y redondear a 4 decimales
    final = max(0.0, min(1.0, round(final, 4)))

    return ConfidenceBreakdown(
        rule_id=rule.rule_id,
        final_score=final,
        age_score=round(age_score, 4),
        application_score=round(application_score, 4),
        cpa_validation_score=round(cpa_validation_score, 4),
        controversy_score=round(controversy_score, 4),
        age_days=age_days,
        times_applied=rule.times_applied,
        validated_by_cpa=rule.validated_by_cpa,
        has_controversy=rule.has_controversy,
    )


# ---------------------------------------------------------------------------
# check_rule_contradiction — detecta si dos reglas se contradicen
# ---------------------------------------------------------------------------

def check_rule_contradiction(
    rule_id_new: str,
    rule_id_old: str,
    query_date: Optional[date] = None,
) -> ContradictionResult:
    """
    Detecta si dos reglas se contradicen en su ambito de aplicacion.

    Escenarios de contradiccion:
      - RATE_CONFLICT: mismo tax_type, jurisdiccion y periodo — tasas distintas
      - SCOPE_OVERLAP: ambitos solapados con condiciones incompatibles
      - DATE_AMBIGUITY: fechas de vigencia solapadas sin relacion de supersesion
      - EXEMPTION_CONFLICT: exenciones contradictorias en el mismo ambito

    Usa este resultado para trigger de pausa del CENTINELA.
    """
    if query_date is None:
        query_date = date.today()

    rule_new = get_rule(rule_id_new, query_date)
    rule_old = get_rule(rule_id_old, query_date)

    # Si alguna regla no existe, no hay contradiccion
    if rule_new is None and rule_old is None:
        return _no_contradiction(rule_id_new, rule_id_old, "Ninguna de las dos reglas esta vigente en la fecha indicada.")
    if rule_new is None:
        return _no_contradiction(rule_id_new, rule_id_old, f"Regla '{rule_id_new}' no vigente en {query_date}.")
    if rule_old is None:
        return _no_contradiction(rule_id_new, rule_id_old, f"Regla '{rule_id_old}' no vigente en {query_date}.")

    # Si una supersede a la otra, la relacion es intencional — no es contradiccion
    if rule_new.supersedes_rule_id == rule_id_old:
        return _no_contradiction(
            rule_id_new, rule_id_old,
            f"'{rule_id_new}' supersede explicitamente a '{rule_id_old}' — relacion intencional.",
        )
    if rule_old.supersedes_rule_id == rule_id_new:
        return _no_contradiction(
            rule_id_new, rule_id_old,
            f"'{rule_id_old}' supersede explicitamente a '{rule_id_new}' — relacion intencional.",
        )

    # Reglas de diferente tipo o jurisdiccion no se contradicen entre si
    if rule_new.tax_type != rule_old.tax_type:
        return _no_contradiction(
            rule_id_new, rule_id_old,
            f"Tipos distintos: {rule_new.tax_type} vs {rule_old.tax_type} — no hay contradiccion.",
        )
    if rule_new.jurisdiction != rule_old.jurisdiction:
        return _no_contradiction(
            rule_id_new, rule_id_old,
            f"Jurisdicciones distintas: {rule_new.jurisdiction} vs {rule_old.jurisdiction} — no hay contradiccion.",
        )

    # Calcular solapamiento de fechas
    overlap = _compute_date_overlap(rule_new, rule_old)

    if overlap is None:
        return _no_contradiction(
            rule_id_new, rule_id_old,
            "No hay solapamiento de fechas entre las dos reglas.",
        )

    # Hay solapamiento y mismo tipo/jurisdiccion — verificar tipo de contradiccion
    # 1. Conflicto de tasa
    if rule_new.rate is not None and rule_old.rate is not None:
        if rule_new.rate != rule_old.rate:
            return ContradictionResult(
                has_contradiction=True,
                contradiction_type=ContradictionType.RATE_CONFLICT,
                rule_id_new=rule_id_new,
                rule_id_old=rule_id_old,
                details=(
                    f"CONFLICTO DE TASA: '{rule_id_new}' tiene rate={rule_new.rate}% "
                    f"pero '{rule_id_old}' tiene rate={rule_old.rate}% "
                    f"para el mismo tipo {rule_new.tax_type} y jurisdiccion {rule_new.jurisdiction}. "
                    f"Periodo solapado: {overlap[0]} a {overlap[1]}."
                ),
                affected_date_range=overlap,
                recommended_action=(
                    "PAUSA CENTINELA: determinar cual tasa aplica para el periodo solapado. "
                    "Verificar si existe relacion de supersesion no documentada. "
                    "CPA debe confirmar la tasa correcta antes de procesar."
                ),
            )

    # 2. Conflicto de tope salarial (FICA)
    if rule_new.wage_base_limit is not None and rule_old.wage_base_limit is not None:
        if rule_new.wage_base_limit != rule_old.wage_base_limit:
            return ContradictionResult(
                has_contradiction=True,
                contradiction_type=ContradictionType.RATE_CONFLICT,
                rule_id_new=rule_id_new,
                rule_id_old=rule_id_old,
                details=(
                    f"CONFLICTO DE TOPE SALARIAL: '{rule_id_new}' tiene wage_base=${rule_new.wage_base_limit:,} "
                    f"pero '{rule_id_old}' tiene wage_base=${rule_old.wage_base_limit:,} "
                    f"para el mismo periodo. Periodo solapado: {overlap[0]} a {overlap[1]}."
                ),
                affected_date_range=overlap,
                recommended_action=(
                    "PAUSA CENTINELA: aplicar el tope salarial correcto para el ano fiscal. "
                    "Verificar SSA Notice para el ano en cuestion."
                ),
            )

    # 3. Solapamiento de fechas sin relacion de supersesion (ambiguedad)
    return ContradictionResult(
        has_contradiction=True,
        contradiction_type=ContradictionType.DATE_AMBIGUITY,
        rule_id_new=rule_id_new,
        rule_id_old=rule_id_old,
        details=(
            f"AMBIGUEDAD TEMPORAL: '{rule_id_new}' y '{rule_id_old}' tienen fechas solapadas "
            f"({overlap[0]} a {overlap[1]}) para el mismo tipo {rule_new.tax_type} "
            "sin relacion de supersesion documentada."
        ),
        affected_date_range=overlap,
        recommended_action=(
            "Revisar si una regla debe marcar supersedes_rule_id apuntando a la otra. "
            "CPA debe confirmar cual regla aplica en el periodo ambiguo."
        ),
    )


def _no_contradiction(
    rule_id_new: str,
    rule_id_old: str,
    details: str,
) -> ContradictionResult:
    return ContradictionResult(
        has_contradiction=False,
        contradiction_type=ContradictionType.NO_CONTRADICTION,
        rule_id_new=rule_id_new,
        rule_id_old=rule_id_old,
        details=details,
        affected_date_range=None,
        recommended_action="No se requiere accion.",
    )


def _compute_date_overlap(
    r1: TaxRule,
    r2: TaxRule,
) -> Optional[tuple[date, date]]:
    """Calcula el rango de solapamiento de vigencia entre dos reglas."""
    start = max(r1.effective_date, r2.effective_date)

    end1 = r1.expiry_date or date(9999, 12, 31)
    end2 = r2.expiry_date or date(9999, 12, 31)
    end = min(end1, end2)

    if start > end:
        return None
    return (start, end if end.year < 9999 else date(9999, 12, 31))


# ---------------------------------------------------------------------------
# calculate_ivu — calculo de IVU con trazabilidad completa
# El LLM llama a esta funcion — nunca hace la aritmetica el mismo
# Esto implementa Capa 2: Calculo por Codigo del sistema anti-alucinacion
# ---------------------------------------------------------------------------

def calculate_ivu(
    base_amount: Decimal,
    query_date: date,
    include_municipal: bool = True,
) -> IVUCalculation:
    """
    Calcula el IVU (estatal + municipal) sobre un monto base.

    Esta funcion implementa la Capa 2 del sistema anti-alucinacion:
    el LLM genera la llamada a esta funcion — la aritmetica la ejecuta
    el codigo Python verificable, no el modelo.

    Args:
        base_amount:       Monto base antes de IVU. Debe ser Decimal preciso.
        query_date:        Fecha de la transaccion — determina que regla usar.
        include_municipal: Si incluir el 1.0% municipal (default True).

    Returns:
        IVUCalculation con todos los componentes y referencias de reglas.

    Raises:
        ValueError: Si no hay regla de IVU vigente para la fecha indicada.
    """
    import hashlib
    import json

    estatal_rule = get_rule("IVU_ESTATAL_PR_2015_V1", query_date)
    if estatal_rule is None:
        raise ValueError(
            f"No existe regla IVU estatal vigente para la fecha {query_date}. "
            "No se puede calcular el IVU."
        )

    municipal_rule = None
    if include_municipal:
        municipal_rule = get_rule("IVU_MUNICIPAL_PR_2015_V1", query_date)

    # Calculo con Decimal para precision exacta — nunca float
    ivu_estatal_rate = estatal_rule.rate / Decimal("100")
    ivu_estatal_amount = (base_amount * ivu_estatal_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    ivu_municipal_rate = Decimal("0")
    ivu_municipal_amount = Decimal("0")
    rule_id_municipal = "N/A"

    if municipal_rule is not None:
        ivu_municipal_rate = municipal_rule.rate / Decimal("100")
        ivu_municipal_amount = (base_amount * ivu_municipal_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        rule_id_municipal = municipal_rule.rule_id

    total_ivu = ivu_estatal_amount + ivu_municipal_amount
    total_with_ivu = base_amount + total_ivu

    # Hash del calculo para audit trail — permite verificacion posterior
    calc_data = {
        "base": str(base_amount),
        "estatal_rate": str(estatal_rule.rate),
        "municipal_rate": str(municipal_rule.rate if municipal_rule else 0),
        "date": str(query_date),
        "estatal_rule_id": estatal_rule.rule_id,
    }
    calc_hash = hashlib.sha256(
        json.dumps(calc_data, sort_keys=True).encode()
    ).hexdigest()[:16]

    return IVUCalculation(
        base_amount=base_amount,
        ivu_estatal_rate=estatal_rule.rate,
        ivu_municipal_rate=municipal_rule.rate if municipal_rule else Decimal("0"),
        ivu_estatal_amount=ivu_estatal_amount,
        ivu_municipal_amount=ivu_municipal_amount,
        total_ivu=total_ivu,
        total_with_ivu=total_with_ivu,
        rule_id_estatal=estatal_rule.rule_id,
        rule_id_municipal=rule_id_municipal,
        calculation_date=query_date,
        calc_hash=calc_hash,
    )
