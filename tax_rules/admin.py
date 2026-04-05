# =============================================================================
# tax_rules/admin.py
# Funciones de administracion con autenticacion separada.
#
# REGLA DE ORO:
#   - Este modulo es el UNICO punto de escritura en la base de reglas.
#   - Requiere autenticacion separada del token de agentes.
#   - Ninguna funcion de agente importa ni llama a este modulo.
#   - El LLM nunca tiene acceso a este modulo en runtime.
#   - Cada escritura genera un log inmutable en la base de datos.
# =============================================================================

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Optional

from .models import TaxRule
from .registry import _build_registry, _INITIAL_RULES, RULES_REGISTRY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AUTENTICACION
# El token de admin se configura via variable de entorno.
# Es diferente e independiente del token de acceso de los agentes.
# ---------------------------------------------------------------------------

_ADMIN_TOKEN_ENV = "BITCOUNTING_TAX_RULES_ADMIN_TOKEN"


class AdminAuthError(PermissionError):
    """Lanzado cuando el token de admin es invalido o no esta configurado."""
    pass


class RuleValidationError(ValueError):
    """Lanzado cuando una regla nueva no pasa las validaciones de integridad."""
    pass


def _require_admin_auth(token: str) -> None:
    """
    Valida el token de administracion.

    Usa hmac.compare_digest para prevenir timing attacks.
    Lanza AdminAuthError si el token es invalido — no retorna ninguna informacion
    sobre el token esperado.
    """
    expected = os.environ.get(_ADMIN_TOKEN_ENV)
    if not expected:
        raise AdminAuthError(
            f"Variable de entorno {_ADMIN_TOKEN_ENV} no configurada. "
            "El modulo de admin no puede operar sin autenticacion configurada."
        )
    if not hmac.compare_digest(token.encode(), expected.encode()):
        logger.warning(
            "ADMIN AUTH FAILED: intento de escritura con token invalido en %s",
            datetime.utcnow().isoformat(),
        )
        raise AdminAuthError("Token de administracion invalido.")
    logger.info("Admin auth: acceso autorizado en %s", datetime.utcnow().isoformat())


# ---------------------------------------------------------------------------
# Registro mutable interno — separado del RULES_REGISTRY publico (inmutable)
# Solo este modulo puede modificarlo.
# ---------------------------------------------------------------------------

_mutable_registry: dict[str, list[TaxRule]] = {
    k: list(v) for k, v in RULES_REGISTRY.items()
}


def _rebuild_public_registry() -> None:
    """
    Reconstruye el registro publico inmutable desde el registro mutable interno.
    Llamado despues de cada operacion de escritura exitosa.
    """
    import tax_rules.registry as reg_module
    new_registry = _build_registry(
        [rule for versions in _mutable_registry.values() for rule in versions]
    )
    # Reemplazar el objeto MappingProxyType en el modulo de registro
    # Esto actualiza RULES_REGISTRY para todos los importadores
    object.__setattr__(reg_module, "RULES_REGISTRY", new_registry)


# ---------------------------------------------------------------------------
# add_rule — agregar regla nueva
# ---------------------------------------------------------------------------

def add_rule(rule: TaxRule, admin_token: str, cpa_license: str) -> str:
    """
    Agrega una regla nueva al registro.

    Requiere:
      - Token de admin valido
      - Licencia CPA del responsable de la adicion
      - La regla debe pasar todas las validaciones de integridad

    Returns:
        rule_id de la regla agregada.

    Raises:
        AdminAuthError: Token invalido.
        RuleValidationError: La regla no pasa validaciones.
    """
    _require_admin_auth(admin_token)
    _validate_new_rule(rule)

    if rule.rule_id in _mutable_registry:
        existing_versions = _mutable_registry[rule.rule_id]
        existing_versions.append(rule)
        existing_versions.sort(key=lambda r: r.effective_date)
    else:
        _mutable_registry[rule.rule_id] = [rule]

    _rebuild_public_registry()

    logger.info(
        "ADMIN ADD_RULE: rule_id='%s' version=%d agregada por CPA '%s' en %s",
        rule.rule_id,
        rule.version,
        cpa_license,
        datetime.utcnow().isoformat(),
    )

    return rule.rule_id


def update_times_applied(rule_id: str, increment: int, admin_token: str) -> None:
    """
    Incrementa el contador times_applied de una regla.
    Unico mecanismo permitido para actualizar un campo de una regla existente.

    Dado que TaxRule es frozen, se reemplaza la instancia con una nueva
    que tiene el contador actualizado.
    """
    _require_admin_auth(admin_token)

    if rule_id not in _mutable_registry:
        raise RuleValidationError(f"Regla '{rule_id}' no encontrada.")

    versions = _mutable_registry[rule_id]
    # Actualizar la version mas reciente
    last = versions[-1]
    updated = last.model_copy(update={"times_applied": last.times_applied + increment})
    versions[-1] = updated
    _rebuild_public_registry()

    logger.info(
        "ADMIN UPDATE_TIMES_APPLIED: rule_id='%s' times_applied ahora=%d",
        rule_id,
        updated.times_applied,
    )


def supersede_rule(
    old_rule_id: str,
    new_rule: TaxRule,
    admin_token: str,
    cpa_license: str,
) -> str:
    """
    Supersede una regla existente con una nueva version.

    La regla antigua recibe expiry_date = new_rule.effective_date - 1 dia.
    La regla nueva debe tener supersedes_rule_id = old_rule_id.
    """
    _require_admin_auth(admin_token)

    if old_rule_id not in _mutable_registry:
        raise RuleValidationError(f"Regla a superseder '{old_rule_id}' no encontrada.")
    if new_rule.supersedes_rule_id != old_rule_id:
        raise RuleValidationError(
            f"La nueva regla debe tener supersedes_rule_id='{old_rule_id}'. "
            f"Recibido: '{new_rule.supersedes_rule_id}'"
        )

    # Cerrar la fecha de expiracion de la regla mas reciente del rule_id anterior
    old_versions = _mutable_registry[old_rule_id]
    last_old = old_versions[-1]

    from datetime import timedelta
    new_expiry = new_rule.effective_date - timedelta(days=1)

    if last_old.expiry_date is None or last_old.expiry_date > new_expiry:
        updated_old = last_old.model_copy(update={"expiry_date": new_expiry})
        old_versions[-1] = updated_old

    # Agregar la nueva regla
    add_rule(new_rule, admin_token, cpa_license)

    logger.info(
        "ADMIN SUPERSEDE_RULE: '%s' supersedida por '%s' efectivo %s. CPA: '%s'",
        old_rule_id,
        new_rule.rule_id,
        new_rule.effective_date,
        cpa_license,
    )

    return new_rule.rule_id


# ---------------------------------------------------------------------------
# Validaciones de integridad para reglas nuevas
# ---------------------------------------------------------------------------

def _validate_new_rule(rule: TaxRule) -> None:
    """
    Valida que una regla nueva cumpla con todos los requisitos del sistema.
    Lanza RuleValidationError si algo no es correcto.
    """
    errors: list[str] = []

    # 1. Metadata completa (Capa 1 del sistema anti-alucinacion)
    if not rule.legal_reference or len(rule.legal_reference) < 20:
        errors.append("legal_reference debe tener al menos 20 caracteres con la referencia exacta.")

    if not rule.source_url or not rule.source_url.startswith("http"):
        errors.append("source_url debe ser una URL valida de una fuente oficial.")

    # 2. Validacion CPA requerida para activar la regla
    if not rule.validated_by_cpa:
        errors.append(
            "validated_by_cpa debe ser True. "
            "Ninguna regla puede entrar al sistema sin validacion CPA."
        )

    # 3. Valores monetarios — no pueden ser float (ya lo garantiza Decimal en el modelo)
    if rule.rate is not None and not isinstance(rule.rate, Decimal):
        errors.append("rate debe ser Decimal, nunca float.")

    # 4. Si supersede otra regla, esa debe existir
    if rule.supersedes_rule_id and rule.supersedes_rule_id not in _mutable_registry:
        errors.append(
            f"supersedes_rule_id='{rule.supersedes_rule_id}' no existe en el registro. "
            "No se puede superseder una regla que no existe."
        )

    # 5. No puede haber dos versiones con el mismo version number
    if rule.rule_id in _mutable_registry:
        existing_versions = [r.version for r in _mutable_registry[rule.rule_id]]
        if rule.version in existing_versions:
            errors.append(
                f"Ya existe version={rule.version} para rule_id='{rule.rule_id}'. "
                "Incrementar el numero de version."
            )

    if errors:
        raise RuleValidationError(
            f"Regla '{rule.rule_id}' fallo validaciones de integridad:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
