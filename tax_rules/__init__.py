# =============================================================================
# tax_rules/__init__.py
# API publica del modulo de reglas fiscales de Puerto Rico.
#
# PARA AGENTES — solo estas funciones estan disponibles:
#   get_rule(rule_id, date)
#   get_rule_strict(rule_id, date)
#   get_rule_confidence(rule_id, date)
#   get_rule_confidence_breakdown(rule_id, date)
#   check_rule_contradiction(rule_id_new, rule_id_old, date)
#   list_active_rules(date)
#   calculate_ivu(base_amount, date, include_municipal)
#   get_all_rule_ids()
#   get_rules_by_tax_type(tax_type)
#
# PARA ADMIN — importar directamente tax_rules.admin (requiere token separado)
#
# LO QUE LOS AGENTES NO PUEDEN HACER:
#   - Modificar ninguna TaxRule (frozen=True)
#   - Escribir en el registro (MappingProxyType)
#   - Importar tax_rules.admin
#   - Llamar funciones prefijadas con _ (convencion de privacidad)
# =============================================================================

from .engine import (
    calculate_ivu,
    check_rule_contradiction,
    get_rule,
    get_rule_confidence,
    get_rule_confidence_breakdown,
    get_rule_strict,
    list_active_rules,
    ContradictionResult,
    ConfidenceBreakdown,
    IVUCalculation,
)
from .models import TaxRule
from .constants import (
    TaxType,
    Jurisdiction,
    ContradictionType,
    CONFIDENCE_AUTO_PROCESS,
    CONFIDENCE_MARK_REVIEW,
    CONFIDENCE_FORCE_PAUSE,
    CONFIDENCE_REJECT,
)
from .registry import get_all_rule_ids, get_rules_by_tax_type

__all__ = [
    # Funciones de consulta
    "get_rule",
    "get_rule_strict",
    "get_rule_confidence",
    "get_rule_confidence_breakdown",
    "check_rule_contradiction",
    "list_active_rules",
    "calculate_ivu",
    "get_all_rule_ids",
    "get_rules_by_tax_type",
    # Tipos de retorno
    "TaxRule",
    "ContradictionResult",
    "ConfidenceBreakdown",
    "IVUCalculation",
    # Enums y constantes
    "TaxType",
    "Jurisdiction",
    "ContradictionType",
    "CONFIDENCE_AUTO_PROCESS",
    "CONFIDENCE_MARK_REVIEW",
    "CONFIDENCE_FORCE_PAUSE",
    "CONFIDENCE_REJECT",
]

# admin.py NO se exporta aqui intencionalmente.
# Los agentes no deben tener acceso a funciones de escritura.
