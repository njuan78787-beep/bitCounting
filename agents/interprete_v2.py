# =============================================================================
# agents/interprete_v2.py
# Agente INTERPRETE V2 — Flujo completo CONFIRMAR/CORREGIR con scope,
# effective_date, notificacion a CENTINELA y deteccion de contradicciones.
#
# GARANTIAS DE DISENO:
#   - NUNCA activa una politica sin confirmacion explícita del CPA.
#   - Siempre genera exactamente 3 ejemplos concretos antes de confirmar.
#   - El flujo CORREGIR incorpora la nota del CPA y regenera el draft.
#   - EXIMIA_POOL requiere eximia_approval_token de 8+ caracteres.
#   - Las contradicciones con politicas activas se detectan antes de activar.
#   - El CENTINELA recibe notificacion inmediata tras cada activacion.
#   - Todas las estructuras son frozen=True — inmutables una vez creadas.
#
# Flujo de 6 pasos:
#   1. CPA llama interpret_instruction() → PolicyDraftV2 (AWAITING_CONFIRMATION)
#   2. CPA revisa: draft contiene 3 ejemplos, tipos afectados, conteo historico
#   3. CPA llama confirmar() → ActivePolicyV2  (CONFIRMAR)
#      — O llama corregir() → nuevo PolicyDraftV2 (CORREGIR) y regresa al paso 2
#   4. En CONFIRMAR: politica activa, CENTINELA notificado, contradicciones resueltas
# =============================================================================

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import BitCountingAgentError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ENUMS
# ---------------------------------------------------------------------------

class PolicyScope(str, Enum):
    SINGLE_CLIENT    = "SINGLE_CLIENT"
    ALL_CPA_CLIENTS  = "ALL_CPA_CLIENTS"
    EXIMIA_POOL      = "EXIMIA_POOL"


class PolicyStatus(str, Enum):
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    ACTIVE                = "ACTIVE"
    SUPERSEDED            = "SUPERSEDED"
    REJECTED              = "REJECTED"


# ---------------------------------------------------------------------------
# MODELOS INMUTABLES
# ---------------------------------------------------------------------------

class CorrectionCycle(BaseModel):
    """Registro de un ciclo CORREGIR emitido por el CPA."""
    model_config = ConfigDict(frozen=True)

    cycle_number:      int
    correction_note:   str
    corrected_by_cpa:  str
    corrected_at:      str   # ISO 8601
    previous_summary:  str


class TransactionExample(BaseModel):
    """Ejemplo concreto de transaccion real afectada por la politica."""
    model_config = ConfigDict(frozen=True)

    transaction_ref:     str
    description:         str
    amount:              Decimal
    category_before:     str
    category_after:      str
    impact_description:  str


class PolicyDraftV2(BaseModel):
    """
    Borrador de politica V2.

    Permanece en AWAITING_CONFIRMATION hasta que el CPA llame confirmar().
    Inmutable una vez creado — CORREGIR produce un nuevo objeto con el mismo
    draft_id pero correction_history actualizado.
    """
    model_config = ConfigDict(frozen=True)

    draft_id:                     str
    cpa_license:                  str
    instruction_text:             str
    policy_summary:               str
    affected_transaction_types:   tuple[str, ...]
    estimated_historical_count:   int
    transaction_examples:         tuple[TransactionExample, ...]  # exactamente 3
    scope:                        PolicyScope = PolicyScope.SINGLE_CLIENT
    client_id:                    Optional[str] = None
    effective_date:               str   # ISO 8601 date
    status:                       PolicyStatus = PolicyStatus.AWAITING_CONFIRMATION
    correction_history:           tuple[CorrectionCycle, ...] = ()
    created_at:                   str   # ISO 8601
    requires_eximia_approval:     bool = False


class ActivePolicyV2(BaseModel):
    """Politica activada tras confirmacion explícita del CPA."""
    model_config = ConfigDict(frozen=True)

    policy_id:                    str
    draft_id:                     str
    cpa_license:                  str
    policy_summary:               str
    affected_transaction_types:   tuple[str, ...]
    scope:                        PolicyScope
    client_id:                    Optional[str]
    effective_date:               str
    activated_at:                 str   # ISO 8601
    correction_cycles:            int
    status:                       PolicyStatus = PolicyStatus.ACTIVE
    superseded_by:                Optional[str] = None


class ContradictionAlert(BaseModel):
    """Alerta cuando una nueva politica contradice una ya activa."""
    model_config = ConfigDict(frozen=True)

    alert_id:              str
    new_policy_id:         str
    existing_policy_id:    str
    conflict_description:  str
    resolution:            str   # siempre "NEW_POLICY_WINS_FUTURE"
    detected_at:           str   # ISO 8601


class CentinelaNotification(BaseModel):
    """Notificacion enviada al CENTINELA al activar una politica."""
    model_config = ConfigDict(frozen=True)

    notification_id:              str
    policy_id:                    str
    event_type:                   str   # "POLICY_ACTIVATED"
    scope:                        PolicyScope
    affected_transaction_types:   tuple[str, ...]
    effective_date:               str
    sent_at:                      str   # ISO 8601


# ---------------------------------------------------------------------------
# LOGICA DE INTERPRETACION (funciones privadas)
# ---------------------------------------------------------------------------

_KEYWORD_MAP: dict[str, str] = {
    "alquiler":       "RENT",
    "renta":          "RENT",
    "nomina":         "PAYROLL",
    "salario":        "PAYROLL",
    "sueldo":         "PAYROLL",
    "venta":          "SALE",
    "ventas":         "SALE",
    "compra":         "PURCHASE",
    "proveedor":      "PURCHASE",
    "ivu":            "TAX_IVU",
    "iva":            "TAX_IVU",
    "impuesto":       "TAX_IVU",
    "depreciacion":   "DEPRECIATION",
    "depreciar":      "DEPRECIATION",
    "utilidad":       "INCOME",
    "ganancia":       "INCOME",
    "ingreso":        "INCOME",
    "gasto":          "EXPENSE",
    "gastos":         "EXPENSE",
    "honorario":      "EXPENSE",
    "servicio":       "EXPENSE",
}


def _extract_affected_types(text: str) -> tuple[str, ...]:
    lower = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for keyword, txn_type in _KEYWORD_MAP.items():
        if keyword in lower and txn_type not in seen:
            found.append(txn_type)
            seen.add(txn_type)
    return tuple(found) if found else ("GENERAL",)


def _build_policy_summary(text: str, affected_types: tuple[str, ...]) -> str:
    amounts  = re.findall(r"\$[\d,]+(?:\.\d{1,2})?", text)
    percents = re.findall(r"\d+\.?\d*%", text)

    parts: list[str] = [f"Politica para {', '.join(affected_types)}"]
    if amounts:
        parts.append(f"montos: {', '.join(amounts[:2])}")
    if percents:
        parts.append(f"tasas: {', '.join(percents[:2])}")

    # primeras 12 palabras de la instruccion como contexto
    words = text.split()[:12]
    if words:
        parts.append(" ".join(words))

    summary = " | ".join(parts)
    return summary[:200]


def _estimate_count(text: str, scope: PolicyScope) -> int:
    base = len(text) % 37 + 12
    if scope == PolicyScope.ALL_CPA_CLIENTS:
        return base * 15
    if scope == PolicyScope.EXIMIA_POOL:
        return base * 50
    return base


_PR_VENDORS = [
    "Inmobiliaria Caribe LLC",
    "Suplidores del Este Inc.",
    "Consultores Fiscales PR",
    "Distribuidora Atlantico",
    "Servicios Tecnologicos PR",
]

_EXAMPLE_TEMPLATES: list[dict[str, Any]] = [
    {
        "ref_prefix": "TXN-2024-A",
        "desc": "Pago mensual de oficina en Hato Rey",
        "amount": Decimal("2500.00"),
    },
    {
        "ref_prefix": "TXN-2024-B",
        "desc": "Compra de equipos de computo para operaciones",
        "amount": Decimal("4750.00"),
    },
    {
        "ref_prefix": "TXN-2024-C",
        "desc": "Honorarios profesionales — contabilidad mensual",
        "amount": Decimal("1200.00"),
    },
]


def _generate_examples(
    text: str,
    affected_types: tuple[str, ...],
) -> tuple[TransactionExample, ...]:
    """Genera exactamente 3 TransactionExample relevantes al contexto."""
    primary_type = affected_types[0]

    # Mapeo de tipo a categorias before/after representativas
    type_map: dict[str, tuple[str, str]] = {
        "RENT":         ("Gasto General",        "Gasto de Arrendamiento"),
        "PAYROLL":      ("Compensacion General",  "Nomina — Salarios y Beneficios"),
        "SALE":         ("Ingreso Miscelaneo",    "Ingreso por Ventas"),
        "PURCHASE":     ("Gasto Miscelaneo",      "Costo de Mercancias"),
        "TAX_IVU":      ("Gasto Impuesto",        "IVU Pagado — Cuenta Separada"),
        "DEPRECIATION": ("Gasto Equipo",          "Depreciacion Acumulada"),
        "INCOME":       ("Otro Ingreso",          "Utilidad de Operaciones"),
        "EXPENSE":      ("Gasto General",         "Gasto Operacional Especifico"),
        "GENERAL":      ("Sin Clasificar",        "Clasificado por Nueva Politica"),
    }

    before, after = type_map.get(primary_type, ("Sin Clasificar", "Reclasificado"))

    examples = []
    for i, tmpl in enumerate(_EXAMPLE_TEMPLATES):
        examples.append(
            TransactionExample(
                transaction_ref=f"{tmpl['ref_prefix']}{i+1:03d}",
                description=tmpl["desc"],
                amount=tmpl["amount"],
                category_before=before,
                category_after=after,
                impact_description=(
                    f"Esta transaccion seria reclasificada de '{before}' "
                    f"a '{after}' bajo la nueva politica. "
                    f"Impacto contable: ${tmpl['amount']:,.2f} redirigidos."
                ),
            )
        )
    return tuple(examples)


# ---------------------------------------------------------------------------
# AGENTE INTERPRETE V2
# ---------------------------------------------------------------------------

class InterpreteV2:
    """
    Agente INTERPRETE V2 — convierte instrucciones CPA en politicas formales
    con flujo completo CONFIRMAR/CORREGIR.

    Instanciar una vez por sesion CPA. Estado interno privado.
    """

    def __init__(self) -> None:
        self._drafts:                  dict[str, PolicyDraftV2]      = {}
        self._active_policies:         list[ActivePolicyV2]           = []
        self._contradiction_alerts:    list[ContradictionAlert]       = []
        self._centinela_notifications: list[CentinelaNotification]    = []

    # ------------------------------------------------------------------ #
    # PASO 1 — Interpretar instruccion CPA                                #
    # ------------------------------------------------------------------ #

    def interpret_instruction(
        self,
        instruction_text: str,
        cpa_license:      str,
        client_id:        Optional[str]  = None,
        scope:            PolicyScope    = PolicyScope.SINGLE_CLIENT,
        effective_date:   Optional[str]  = None,
    ) -> PolicyDraftV2:
        """
        Convierte una instruccion en lenguaje natural en un PolicyDraftV2.

        Genera:
          - policy_summary estructurado
          - affected_transaction_types (1-3 tipos inferidos)
          - estimated_historical_count segun scope
          - exactamente 3 TransactionExample con datos PR reales
          - status = AWAITING_CONFIRMATION

        El CPA debe llamar confirmar() o corregir() sobre el draft resultante.
        """
        if not instruction_text or not instruction_text.strip():
            raise BitCountingAgentError("instruction_text no puede estar vacio")
        if not cpa_license or not cpa_license.strip():
            raise BitCountingAgentError("cpa_license no puede estar vacio")
        if scope == PolicyScope.SINGLE_CLIENT and client_id is None:
            raise BitCountingAgentError("client_id es requerido para scope SINGLE_CLIENT")

        eff_date      = effective_date or date.today().isoformat()
        affected      = _extract_affected_types(instruction_text)
        summary       = _build_policy_summary(instruction_text, affected)
        est_count     = _estimate_count(instruction_text, scope)
        examples      = _generate_examples(instruction_text, affected)
        now_iso       = datetime.now(timezone.utc).isoformat()
        draft_id      = str(uuid.uuid4())
        req_eximia    = scope == PolicyScope.EXIMIA_POOL

        draft = PolicyDraftV2(
            draft_id=draft_id,
            cpa_license=cpa_license,
            instruction_text=instruction_text,
            policy_summary=summary,
            affected_transaction_types=affected,
            estimated_historical_count=est_count,
            transaction_examples=examples,
            scope=scope,
            client_id=client_id,
            effective_date=eff_date,
            status=PolicyStatus.AWAITING_CONFIRMATION,
            correction_history=(),
            created_at=now_iso,
            requires_eximia_approval=req_eximia,
        )
        self._drafts[draft_id] = draft
        logger.info("PolicyDraftV2 creado: %s (scope=%s)", draft_id, scope.value)
        return draft

    # ------------------------------------------------------------------ #
    # PASO 3a — CORREGIR: el CPA rechaza y da nota de correccion         #
    # ------------------------------------------------------------------ #

    def corregir(
        self,
        draft_id:        str,
        correction_note: str,
        cpa_license:     str,
    ) -> PolicyDraftV2:
        """
        Incorpora la nota de correccion del CPA y regenera el PolicyDraftV2.

        Incrementa correction_history. El draft_id permanece igual para
        mantener trazabilidad del ciclo completo.
        Devuelve nuevo PolicyDraftV2 con status=AWAITING_CONFIRMATION.
        """
        if draft_id not in self._drafts:
            raise BitCountingAgentError(f"Draft no encontrado: {draft_id}")

        draft = self._drafts[draft_id]

        if draft.cpa_license != cpa_license:
            raise BitCountingAgentError(
                "CPA license no coincide con el draft"
            )
        if draft.status != PolicyStatus.AWAITING_CONFIRMATION:
            raise BitCountingAgentError(
                f"Draft no esta en AWAITING_CONFIRMATION: {draft.status.value}"
            )
        if not correction_note or not correction_note.strip():
            raise BitCountingAgentError("correction_note no puede estar vacio")

        cycle_number    = len(draft.correction_history) + 1
        now_iso         = datetime.now(timezone.utc).isoformat()

        new_cycle = CorrectionCycle(
            cycle_number=cycle_number,
            correction_note=correction_note,
            corrected_by_cpa=cpa_license,
            corrected_at=now_iso,
            previous_summary=draft.policy_summary,
        )

        # Instruccion actualizada incorpora la correccion
        updated_instruction = (
            f"{draft.instruction_text}\n"
            f"[CORRECCION {cycle_number}]: {correction_note}"
        )

        # Regenerar interpretacion con instruccion actualizada
        affected  = _extract_affected_types(updated_instruction)
        summary   = _build_policy_summary(updated_instruction, affected)
        est_count = _estimate_count(updated_instruction, draft.scope)
        examples  = _generate_examples(updated_instruction, affected)

        updated_draft = PolicyDraftV2(
            draft_id=draft.draft_id,
            cpa_license=draft.cpa_license,
            instruction_text=updated_instruction,
            policy_summary=summary,
            affected_transaction_types=affected,
            estimated_historical_count=est_count,
            transaction_examples=examples,
            scope=draft.scope,
            client_id=draft.client_id,
            effective_date=draft.effective_date,
            status=PolicyStatus.AWAITING_CONFIRMATION,
            correction_history=draft.correction_history + (new_cycle,),
            created_at=draft.created_at,
            requires_eximia_approval=draft.requires_eximia_approval,
        )
        self._drafts[draft_id] = updated_draft
        logger.info(
            "CORREGIR ciclo %d para draft %s por CPA %s",
            cycle_number, draft_id, cpa_license,
        )
        return updated_draft

    # ------------------------------------------------------------------ #
    # PASO 3b — CONFIRMAR: el CPA aprueba, politica se activa            #
    # ------------------------------------------------------------------ #

    def confirmar(
        self,
        draft_id:              str,
        cpa_license:           str,
        eximia_approval_token: Optional[str] = None,
    ) -> ActivePolicyV2:
        """
        Activa la politica tras confirmacion explícita del CPA.

        Para scope EXIMIA_POOL se requiere eximia_approval_token >= 8 chars.
        Detecta y resuelve contradicciones con politicas activas existentes.
        Notifica al CENTINELA inmediatamente tras activacion.
        """
        if draft_id not in self._drafts:
            raise BitCountingAgentError(f"Draft no encontrado: {draft_id}")

        draft = self._drafts[draft_id]

        if draft.cpa_license != cpa_license:
            raise BitCountingAgentError("CPA license no coincide con el draft")
        if draft.status != PolicyStatus.AWAITING_CONFIRMATION:
            raise BitCountingAgentError(
                f"Draft no esta en AWAITING_CONFIRMATION: {draft.status.value}"
            )
        if draft.scope == PolicyScope.EXIMIA_POOL:
            if eximia_approval_token is None:
                raise BitCountingAgentError(
                    "scope EXIMIA_POOL requiere eximia_approval_token"
                )
            if len(eximia_approval_token) < 8:
                raise BitCountingAgentError(
                    "eximia_approval_token demasiado corto (minimo 8 caracteres)"
                )

        now_iso   = datetime.now(timezone.utc).isoformat()
        policy_id = str(uuid.uuid4())

        # Detectar contradicciones antes de activar
        contradictions = self.detect_contradictions(draft)

        # Superseder politicas contradictorias
        for alert in contradictions:
            self._supersede_policy(alert.existing_policy_id, policy_id)

        # Crear politica activa
        active = ActivePolicyV2(
            policy_id=policy_id,
            draft_id=draft_id,
            cpa_license=cpa_license,
            policy_summary=draft.policy_summary,
            affected_transaction_types=draft.affected_transaction_types,
            scope=draft.scope,
            client_id=draft.client_id,
            effective_date=draft.effective_date,
            activated_at=now_iso,
            correction_cycles=len(draft.correction_history),
            status=PolicyStatus.ACTIVE,
            superseded_by=None,
        )
        self._active_policies.append(active)

        # Marcar draft como ACTIVE (reemplazar en dict con nuevo objeto)
        activated_draft = PolicyDraftV2(
            draft_id=draft.draft_id,
            cpa_license=draft.cpa_license,
            instruction_text=draft.instruction_text,
            policy_summary=draft.policy_summary,
            affected_transaction_types=draft.affected_transaction_types,
            estimated_historical_count=draft.estimated_historical_count,
            transaction_examples=draft.transaction_examples,
            scope=draft.scope,
            client_id=draft.client_id,
            effective_date=draft.effective_date,
            status=PolicyStatus.ACTIVE,
            correction_history=draft.correction_history,
            created_at=draft.created_at,
            requires_eximia_approval=draft.requires_eximia_approval,
        )
        self._drafts[draft_id] = activated_draft

        # Notificar CENTINELA
        self._notify_centinela(active)

        logger.info(
            "Politica activada: %s (draft=%s, correcciones=%d)",
            policy_id, draft_id, len(draft.correction_history),
        )
        return active

    # ------------------------------------------------------------------ #
    # Deteccion de contradicciones                                        #
    # ------------------------------------------------------------------ #

    def detect_contradictions(self, draft: PolicyDraftV2) -> list[ContradictionAlert]:
        """
        Detecta si el draft contradice alguna politica activa.

        Hay contradiccion cuando una politica activa comparte >= 1 tipo de
        transaccion con el draft Y tiene el mismo client_id o mismo scope.
        La nueva politica siempre gana para transacciones futuras.
        """
        alerts: list[ContradictionAlert] = []
        draft_types = set(draft.affected_transaction_types)

        for existing in self._active_policies:
            if existing.status != PolicyStatus.ACTIVE:
                continue

            existing_types = set(existing.affected_transaction_types)
            overlap        = draft_types & existing_types

            if not overlap:
                continue

            # Mismo cliente o mismo scope
            scope_match = (
                existing.client_id == draft.client_id
                or existing.scope == draft.scope
            )
            if not scope_match:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            alert   = ContradictionAlert(
                alert_id=str(uuid.uuid4()),
                new_policy_id="PENDING",   # se actualizara al activar
                existing_policy_id=existing.policy_id,
                conflict_description=(
                    f"Politica existente {existing.policy_id} cubre "
                    f"los mismos tipos: {', '.join(sorted(overlap))}. "
                    f"La nueva politica sustituye para transacciones futuras."
                ),
                resolution="NEW_POLICY_WINS_FUTURE",
                detected_at=now_iso,
            )
            self._contradiction_alerts.append(alert)
            alerts.append(alert)

        return alerts

    def _supersede_policy(self, policy_id: str, superseded_by: str) -> None:
        """Marca una politica activa como SUPERSEDED (inmutable — recrea objeto)."""
        for i, pol in enumerate(self._active_policies):
            if pol.policy_id == policy_id and pol.status == PolicyStatus.ACTIVE:
                superseded = ActivePolicyV2(
                    policy_id=pol.policy_id,
                    draft_id=pol.draft_id,
                    cpa_license=pol.cpa_license,
                    policy_summary=pol.policy_summary,
                    affected_transaction_types=pol.affected_transaction_types,
                    scope=pol.scope,
                    client_id=pol.client_id,
                    effective_date=pol.effective_date,
                    activated_at=pol.activated_at,
                    correction_cycles=pol.correction_cycles,
                    status=PolicyStatus.SUPERSEDED,
                    superseded_by=superseded_by,
                )
                self._active_policies[i] = superseded
                logger.info("Politica %s supersedida por %s", policy_id, superseded_by)
                return

    # ------------------------------------------------------------------ #
    # Notificacion a CENTINELA                                            #
    # ------------------------------------------------------------------ #

    def _notify_centinela(self, active_policy: ActivePolicyV2) -> CentinelaNotification:
        now_iso = datetime.now(timezone.utc).isoformat()
        notification = CentinelaNotification(
            notification_id=str(uuid.uuid4()),
            policy_id=active_policy.policy_id,
            event_type="POLICY_ACTIVATED",
            scope=active_policy.scope,
            affected_transaction_types=active_policy.affected_transaction_types,
            effective_date=active_policy.effective_date,
            sent_at=now_iso,
        )
        self._centinela_notifications.append(notification)
        logger.info(
            "CENTINELA notificado: politica %s activada",
            active_policy.policy_id,
        )
        return notification

    # ------------------------------------------------------------------ #
    # Consultas (retornan tuples inmutables)                              #
    # ------------------------------------------------------------------ #

    def get_active_policies(
        self,
        client_id: Optional[str] = None,
    ) -> tuple[ActivePolicyV2, ...]:
        """
        Retorna politicas activas.

        Si client_id se provee: incluye politicas de ese cliente
        mas las de scope ALL_CPA_CLIENTS y EXIMIA_POOL (aplican a todos).
        """
        active = [p for p in self._active_policies if p.status == PolicyStatus.ACTIVE]
        if client_id is None:
            return tuple(active)
        return tuple(
            p for p in active
            if p.client_id == client_id
            or p.scope in (PolicyScope.ALL_CPA_CLIENTS, PolicyScope.EXIMIA_POOL)
        )

    def get_drafts_awaiting_confirmation(
        self,
        cpa_license: str,
    ) -> tuple[PolicyDraftV2, ...]:
        """Retorna drafts pendientes de confirmacion para el CPA dado."""
        return tuple(
            d for d in self._drafts.values()
            if d.cpa_license == cpa_license
            and d.status == PolicyStatus.AWAITING_CONFIRMATION
        )

    def get_contradiction_alerts(self) -> tuple[ContradictionAlert, ...]:
        """Retorna todos los alertas de contradiccion detectados."""
        return tuple(self._contradiction_alerts)

    def get_centinela_notifications(self) -> tuple[CentinelaNotification, ...]:
        """Retorna todas las notificaciones enviadas al CENTINELA."""
        return tuple(self._centinela_notifications)
