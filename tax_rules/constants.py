# =============================================================================
# tax_rules/constants.py
# Enums, tipos y constantes del modulo de reglas fiscales de PR.
# READ-ONLY para todos los agentes.
# =============================================================================

from enum import Enum


class TaxType(str, Enum):
    IVU_ESTATAL       = "IVU_ESTATAL"
    IVU_MUNICIPAL     = "IVU_MUNICIPAL"
    FICA_SS           = "FICA_SS"
    FICA_MEDICARE     = "FICA_MEDICARE"
    FUTA              = "FUTA"
    SUTA_PR           = "SUTA_PR"
    GAAP_CAPITALIZATION = "GAAP_CAPITALIZATION"
    DEDUCTIBLE_PR     = "DEDUCTIBLE_PR"


class Jurisdiction(str, Enum):
    PR      = "PR"
    FEDERAL = "FEDERAL"
    PR_AND_FEDERAL = "PR_AND_FEDERAL"


class ContradictionType(str, Enum):
    RATE_CONFLICT      = "RATE_CONFLICT"       # mismas condiciones, tasas distintas
    SCOPE_OVERLAP      = "SCOPE_OVERLAP"        # ambitos solapados con resultados distintos
    DATE_AMBIGUITY     = "DATE_AMBIGUITY"       # fechas de vigencia ambiguas o solapadas
    EXEMPTION_CONFLICT = "EXEMPTION_CONFLICT"   # exenciones contradictorias
    NO_CONTRADICTION   = "NO_CONTRADICTION"


# Umbrales de confidence segun el blueprint (Capa 3)
CONFIDENCE_AUTO_PROCESS   = 0.95   # >= 95%  procesamiento automatico + log silencioso
CONFIDENCE_MARK_REVIEW    = 0.80   # 80-94%  procesa pero marca para revision CPA
CONFIDENCE_FORCE_PAUSE    = 0.60   # 60-79%  pausa forzada del CENTINELA
CONFIDENCE_REJECT         = 0.00   # < 60%   rechazado, escala a siguiente CPA

# Pesos del calculo de confidence
CONFIDENCE_WEIGHT_AGE         = 0.25
CONFIDENCE_WEIGHT_APPLICATIONS = 0.30
CONFIDENCE_WEIGHT_CPA_VALIDATION = 0.30
CONFIDENCE_WEIGHT_CONTROVERSY  = 0.15
