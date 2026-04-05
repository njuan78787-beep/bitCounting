# =============================================================================
# calculation_sandbox/sandbox.py
# Motor de ejecucion restringida para calculos fiscales generados por el LLM.
#
# PRINCIPIO FUNDAMENTAL: ningun calculo aritmético fiscal es ejecutado por el
# LLM directamente. El LLM genera codigo Python; este modulo lo valida y lo
# ejecuta en un namespace totalmente aislado, devolviendo el resultado con
# hash SHA-256 para el audit trail.
#
# GARANTIAS DE DISENO:
#   - Sin acceso a filesystem, red, ni imports peligrosos (garantizado por
#     CodeValidator antes de ejecutar).
#   - Namespace de ejecucion contiene EXCLUSIVAMENTE: math, Decimal, date,
#     y las 5 funciones contables pre-aprobadas.
#   - Todo-o-nada: si el codigo falla en cualquier punto, se retorna el error
#     exacto sin modificar el estado y sin resultados parciales.
#   - SandboxResult es frozen — inmutable una vez generado.
#   - calc_hash = SHA-256(result_value + code + timestamp) para trazabilidad.
# =============================================================================

from __future__ import annotations

import hashlib
import math
import traceback
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .validator import CodeValidator, ValidationError
from .accounting_functions import ACCOUNTING_FUNCTIONS


# ---------------------------------------------------------------------------
# Resultado del sandbox — inmutable, con hash de auditoria
# ---------------------------------------------------------------------------

class SandboxResult(BaseModel):
    """
    Resultado de una ejecucion en el sandbox de calculo.

    Todos los campos son inmutables (frozen=True).
    El calc_hash vincula el resultado con el codigo exacto que lo produjo,
    haciendo imposible alterar el resultado sin invalidar el hash.
    """

    model_config = ConfigDict(frozen=True)

    result_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unico de esta ejecucion para trazabilidad.",
    )
    success: bool = Field(
        description="True si el codigo se ejecuto sin errores; False si fallo.",
    )
    result_value: Any = Field(
        default=None,
        description="Valor retornado por el codigo (ultimo valor de 'result').",
    )
    result_type: str = Field(
        default="NoneType",
        description="Tipo Python del resultado (Decimal, dict, list, etc.).",
    )
    code_executed: str = Field(
        description="Codigo exacto que fue ejecutado (nunca modificado).",
    )
    parameters_used: dict = Field(
        default_factory=dict,
        description="Variables inyectadas en el namespace antes de ejecutar.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Mensaje de error exacto si success=False; None si exitoso.",
    )
    error_type: Optional[str] = Field(
        default=None,
        description="Tipo de excepcion si success=False (ValidationError, etc.).",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Timestamp UTC ISO 8601 del momento de ejecucion.",
    )
    execution_ms: Optional[int] = Field(
        default=None,
        description="Tiempo de ejecucion en milisegundos.",
    )
    calc_hash: str = Field(
        default="",
        description=(
            "SHA-256(result_value + code + timestamp) para audit trail. "
            "Permite verificar que el resultado no fue alterado post-ejecucion."
        ),
    )
    rule_ids_applied: tuple[str, ...] = Field(
        default_factory=tuple,
        description="IDs de reglas fiscales usadas durante el calculo.",
    )


def _compute_hash(result_value: Any, code: str, timestamp: str) -> str:
    """SHA-256 del resultado + codigo + timestamp para audit trail."""
    payload = f"{result_value!r}|{code}|{timestamp}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_rule_ids(result_value: Any) -> tuple[str, ...]:
    """
    Extrae rule_ids de resultados de funciones contables.
    Las funciones retornan dicts con claves 'rule_id*'.
    """
    ids: list[str] = []
    if isinstance(result_value, dict):
        for key, val in result_value.items():
            if "rule_id" in key and isinstance(val, str):
                ids.append(val)
    elif isinstance(result_value, list):
        for item in result_value:
            if isinstance(item, dict):
                ids.extend(_extract_rule_ids(item))
    return tuple(dict.fromkeys(ids))   # deduplicado, orden preservado


def _make_json_safe(value: Any) -> Any:
    """Convierte Decimal y date a tipos serializables para almacenamiento."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Namespace restringido
# ---------------------------------------------------------------------------

def _build_restricted_namespace(parameters: dict) -> dict:
    """
    Construye el namespace de ejecucion con:
    - Solo math, Decimal, date como builtins
    - Las 5 funciones contables pre-aprobadas
    - Los parametros del llamador (variables de entrada)
    - Sin acceso a __builtins__ de Python por defecto
    """
    ns: dict[str, Any] = {
        # Modulos seguros
        "math":     math,
        "Decimal":  Decimal,
        "date":     date,
        "datetime": datetime,
        "ROUND_HALF_UP": ROUND_HALF_UP,
        # Funciones contables pre-aprobadas
        **ACCOUNTING_FUNCTIONS,
        # Variable de resultado — el codigo debe asignar a 'result'
        "result": None,
    }

    # Inyectar parametros del llamador
    for key, val in parameters.items():
        if not isinstance(key, str):
            raise ValueError(f"Las claves de parameters deben ser strings: {key!r}")
        if key.startswith("__"):
            raise ValueError(
                f"Nombre de parametro prohibido (comienza con __): '{key}'"
            )
        ns[key] = val

    return ns


# ---------------------------------------------------------------------------
# Motor principal del sandbox
# ---------------------------------------------------------------------------

class CalculationSandbox:
    """
    Sandbox de ejecucion restringida para calculos fiscales.

    El LLM genera codigo Python que:
    1. Usa las funciones contables pre-aprobadas (calc_ivu, calc_fica, etc.)
    2. Asigna el resultado final a la variable 'result'
    3. No importa nada no permitido, no accede al filesystem ni a la red

    Este sandbox valida el codigo, lo ejecuta en namespace aislado,
    y retorna un SandboxResult inmutable con hash de auditoria.

    Ejemplo de codigo valido que el LLM puede generar:
        base = Decimal("1000.00")
        resultado_ivu = calc_ivu(base, None, date(2025, 3, 15), True)
        result = resultado_ivu

    Uso:
        sandbox = CalculationSandbox()
        output = sandbox.execute(code, parameters={"monto": Decimal("500.00")})
        if output.success:
            print(output.result_value)
    """

    def __init__(self) -> None:
        self._validator = CodeValidator()

    def execute(
        self,
        code: str,
        parameters: Optional[dict] = None,
    ) -> SandboxResult:
        """
        Valida y ejecuta el codigo en el sandbox restringido.

        Args:
            code:       Codigo Python generado por el LLM.
            parameters: Variables a inyectar en el namespace (ej. montos, fechas).

        Returns:
            SandboxResult con success=True y el valor de 'result', o
            SandboxResult con success=False y el error exacto.

        Nota:
            NUNCA lanza excepciones — todos los errores se encapsulan en
            SandboxResult.error para que el llamador pueda decidir como manejarlos.
        """
        if parameters is None:
            parameters = {}

        timestamp = datetime.now(timezone.utc).isoformat()
        t_start = datetime.now(timezone.utc)

        # --- PASO 1: Validacion (sin ejecutar nada) ---
        try:
            self._validator.validate(code)
        except ValidationError as exc:
            return SandboxResult(
                success=False,
                code_executed=code,
                parameters_used=_make_json_safe(parameters),
                error=str(exc),
                error_type="ValidationError",
                timestamp=timestamp,
                execution_ms=0,
                calc_hash=_compute_hash(None, code, timestamp),
            )

        # --- PASO 2: Construccion del namespace restringido ---
        try:
            ns = _build_restricted_namespace(parameters)
        except ValueError as exc:
            return SandboxResult(
                success=False,
                code_executed=code,
                parameters_used=_make_json_safe(parameters),
                error=str(exc),
                error_type="NamespaceError",
                timestamp=timestamp,
                execution_ms=0,
                calc_hash=_compute_hash(None, code, timestamp),
            )

        # --- PASO 3: Ejecucion (todo o nada) ---
        # __builtins__ restringido: solo __import__ para permitir 'from math import ...'
        # El validator ya rechazo cualquier import no permitido antes de llegar aqui.
        # Builtins seguros: solo funciones puras sin I/O ni introspección
        _bi = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
        _safe_builtins = {
            "__import__": _bi["__import__"],   # necesario para 'from math import ...'
            "abs":        _bi["abs"],
            "bool":       _bi["bool"],
            "int":        _bi["int"],
            "float":      _bi["float"],
            "str":        _bi["str"],
            "round":      _bi["round"],
            "min":        _bi["min"],
            "max":        _bi["max"],
            "sum":        _bi["sum"],
            "len":        _bi["len"],
            "range":      _bi["range"],
            "enumerate":  _bi["enumerate"],
            "zip":        _bi["zip"],
            "list":       _bi["list"],
            "dict":       _bi["dict"],
            "tuple":      _bi["tuple"],
            "isinstance": _bi["isinstance"],
            "ValueError": _bi["ValueError"],
            "TypeError":  _bi["TypeError"],
        }
        try:
            exec(code, {"__builtins__": _safe_builtins}, ns)  # noqa: S102 — controlado por el validator
        except Exception as exc:
            t_end = datetime.now(timezone.utc)
            ms = int((t_end - t_start).total_seconds() * 1000)
            tb = traceback.format_exc()
            return SandboxResult(
                success=False,
                code_executed=code,
                parameters_used=_make_json_safe(parameters),
                error=f"{type(exc).__name__}: {exc}\n\n{tb}",
                error_type=type(exc).__name__,
                timestamp=timestamp,
                execution_ms=ms,
                calc_hash=_compute_hash(None, code, timestamp),
            )

        t_end = datetime.now(timezone.utc)
        ms = int((t_end - t_start).total_seconds() * 1000)

        # --- PASO 4: Extraer resultado ---
        result_value = ns.get("result")

        # Si el codigo no asigno 'result', es un error del LLM
        if result_value is None:
            return SandboxResult(
                success=False,
                code_executed=code,
                parameters_used=_make_json_safe(parameters),
                error=(
                    "El codigo no asigno ningun valor a la variable 'result'. "
                    "El codigo debe terminar con: result = <valor_calculado>"
                ),
                error_type="MissingResultVariable",
                timestamp=timestamp,
                execution_ms=ms,
                calc_hash=_compute_hash(None, code, timestamp),
            )

        # --- PASO 5: Construir SandboxResult con hash ---
        rule_ids = _extract_rule_ids(result_value)
        calc_hash = _compute_hash(result_value, code, timestamp)

        return SandboxResult(
            success=True,
            result_value=_make_json_safe(result_value),
            result_type=type(result_value).__name__,
            code_executed=code,
            parameters_used=_make_json_safe(parameters),
            timestamp=timestamp,
            execution_ms=ms,
            calc_hash=calc_hash,
            rule_ids_applied=rule_ids,
        )
