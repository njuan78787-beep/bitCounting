# =============================================================================
# calculation_sandbox/__init__.py
# Sandbox de calculo aritmetico para Bit-Counting.
#
# PRINCIPIO FUNDAMENTAL: ningun calculo fiscal es ejecutado por el LLM.
# El LLM genera codigo Python; este sandbox lo valida, lo ejecuta en entorno
# restringido, y devuelve el resultado con hash de auditoria.
# =============================================================================

from .sandbox import CalculationSandbox, SandboxResult
from .validator import CodeValidator, ValidationError
from .accounting_functions import ACCOUNTING_FUNCTIONS

__all__ = [
    "CalculationSandbox",
    "SandboxResult",
    "CodeValidator",
    "ValidationError",
    "ACCOUNTING_FUNCTIONS",
]
