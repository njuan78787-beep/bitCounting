# =============================================================================
# calculation_sandbox/validator.py
# Validador de codigo Python antes de ejecutar en el sandbox.
#
# GARANTIAS DE DISENO:
#   - Analiza el AST del codigo — no ejecuta nada durante la validacion.
#   - Lista blanca de imports permitidos: math, decimal, datetime.
#   - Detecta acceso a __builtins__, __globals__, __import__, eval, exec.
#   - Detecta loops sin limite de iteraciones (while True sin break).
#   - Si cualquier validacion falla, lanza ValidationError con detalle exacto.
#   - El sandbox NUNCA ejecuta codigo que no pase la validacion.
# =============================================================================

from __future__ import annotations

import ast
from typing import Optional


class ValidationError(Exception):
    """
    El codigo generado por el LLM no pasa la validacion de seguridad.

    Atributos:
        reason:    Descripcion exacta del problema detectado.
        line:      Numero de linea donde se detecto el problema (None si N/A).
        node_type: Tipo de nodo AST que disparo la validacion (None si N/A).
    """

    def __init__(
        self,
        reason: str,
        line: Optional[int] = None,
        node_type: Optional[str] = None,
    ) -> None:
        self.reason = reason
        self.line = line
        self.node_type = node_type
        detail = f"[linea {line}] " if line else ""
        super().__init__(f"ValidationError: {detail}{reason}")


# ---------------------------------------------------------------------------
# Configuracion de la lista blanca
# ---------------------------------------------------------------------------

# Modulos que el codigo generado puede importar
_ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "math",
    "decimal",
    "datetime",
})

# Nombres que el codigo generado NUNCA puede referenciar
_FORBIDDEN_NAMES: frozenset[str] = frozenset({
    "__builtins__",
    "__globals__",
    "__locals__",
    "__import__",
    "__class__",
    "__base__",
    "__subclasses__",
    "__dict__",
    "__code__",
    "__closure__",
    "__module__",
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "print",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
    "type",
    "object",
    "super",
    "__loader__",
    "__spec__",
    "__file__",
    "__path__",
    "breakpoint",
    "exit",
    "quit",
})

# Atributos de objetos que no pueden ser accedidos
_FORBIDDEN_ATTRS: frozenset[str] = frozenset({
    "__class__",
    "__dict__",
    "__globals__",
    "__builtins__",
    "__code__",
    "__closure__",
    "__subclasses__",
    "__mro__",
    "__bases__",
    "__base__",
})

# Limite de iteraciones para while loops (sin break explicito)
_MAX_LOOP_BODY_STATEMENTS = 50


# ---------------------------------------------------------------------------
# Visitante AST principal
# ---------------------------------------------------------------------------

class _SecurityVisitor(ast.NodeVisitor):
    """
    Recorre el AST y rechaza cualquier construccion peligrosa.
    Lanza ValidationError en el primer problema encontrado.
    """

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top_level = alias.name.split(".")[0]
            if top_level not in _ALLOWED_IMPORTS:
                raise ValidationError(
                    reason=(
                        f"Import no permitido: '{alias.name}'. "
                        f"Solo se permiten: {sorted(_ALLOWED_IMPORTS)}"
                    ),
                    line=node.lineno,
                    node_type="Import",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        top_level = module.split(".")[0]
        if top_level not in _ALLOWED_IMPORTS:
            raise ValidationError(
                reason=(
                    f"Import no permitido: 'from {module} import ...'. "
                    f"Solo se permiten: {sorted(_ALLOWED_IMPORTS)}"
                ),
                line=node.lineno,
                node_type="ImportFrom",
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            raise ValidationError(
                reason=f"Referencia prohibida: '{node.id}'.",
                line=node.lineno,
                node_type="Name",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FORBIDDEN_ATTRS:
            raise ValidationError(
                reason=f"Acceso a atributo prohibido: '.{node.attr}'.",
                line=node.lineno,
                node_type="Attribute",
            )
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        """
        Detecta while loops potencialmente infinitos.

        Un while loop es seguro si:
        - Su condicion no es literalmente True, o
        - Tiene un break explicito en el cuerpo.
        """
        is_infinite_condition = (
            isinstance(node.test, ast.Constant) and node.test.value is True
        )
        if is_infinite_condition:
            has_break = any(
                isinstance(stmt, ast.Break)
                for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            )
            if not has_break:
                raise ValidationError(
                    reason=(
                        "Loop potencialmente infinito detectado: "
                        "'while True' sin 'break' explicito."
                    ),
                    line=node.lineno,
                    node_type="While",
                )
        if len(node.body) > _MAX_LOOP_BODY_STATEMENTS:
            raise ValidationError(
                reason=(
                    f"Cuerpo del while loop excede {_MAX_LOOP_BODY_STATEMENTS} "
                    f"sentencias ({len(node.body)} encontradas). "
                    "Refactorice el calculo en funciones separadas."
                ),
                line=node.lineno,
                node_type="While",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Detecta llamadas a funciones peligrosas."""
        # eval("..."), exec("..."), compile("...")
        if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_NAMES:
            raise ValidationError(
                reason=f"Llamada prohibida: '{node.func.id}(...)'.",
                line=node.lineno,
                node_type="Call",
            )
        # objeto.__class__(...) o similar
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in _FORBIDDEN_ATTRS:
                raise ValidationError(
                    reason=f"Llamada a metodo prohibido: '.{node.func.attr}(...)'.",
                    line=node.lineno,
                    node_type="Call",
                )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

class CodeValidator:
    """
    Valida codigo Python generado por el LLM antes de ejecutarlo en el sandbox.

    Uso:
        validator = CodeValidator()
        validator.validate(code_string)   # lanza ValidationError si hay problema
    """

    def validate(self, code: str) -> ast.Module:
        """
        Valida el codigo y retorna el AST parseado si es seguro.

        Args:
            code: Codigo Python como string (generado por el LLM).

        Returns:
            ast.Module — el arbol AST del codigo validado.

        Raises:
            ValidationError: Si el codigo contiene alguna construccion prohibida.
        """
        if not code or not code.strip():
            raise ValidationError(reason="El codigo esta vacio.")

        # Limite de longitud — el LLM no deberia generar calculos enormes
        if len(code) > 8_000:
            raise ValidationError(
                reason=(
                    f"Codigo excede el limite de 8,000 caracteres "
                    f"({len(code)} encontrados). "
                    "Divida el calculo en llamadas separadas al sandbox."
                )
            )

        # Parseo — si falla, el codigo tiene errores de sintaxis
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise ValidationError(
                reason=f"Error de sintaxis Python: {exc.msg}",
                line=exc.lineno,
                node_type="SyntaxError",
            ) from exc

        # Recorrido de seguridad
        visitor = _SecurityVisitor()
        visitor.visit(tree)

        return tree
