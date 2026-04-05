# =============================================================================
# agents/actualizador.py
# Agente ACTUALIZADOR del sistema Bit-Counting.
#
# ROL:
#   Monitorea 7 fuentes normativas diariamente. Detecta cambios.
#   Genera update drafts. TODOS los cambios requieren aprobacion humana
#   antes de activarse — nunca se auto-aplican.
#
# GARANTIAS DE DISENO:
#   - requires_human_review es siempre True en todo MonitorResult.
#   - NUNCA auto-aplica cambios a tax_rules. Solo genera drafts.
#   - Los 7 stubs retornan no-change por defecto (Fase 1).
#   - Todas las actualizaciones quedan en PENDING_REVIEW.
#
# FUENTES MONITOREADAS (7):
#   1. hacienda.pr.gov     — circulares, reglamentos, formularios
#   2. irs.gov             — Publication 15, Revenue Rulings, Notices
#   3. estado.pr.gov       — leyes aprobadas (LexJuris RSS)
#   4. dtrh.pr.gov         — tasas SUTA, reglamentos laborales
#   5. boletinestado.pr.gov — Boletin Oficial
#   6. congress.gov        — legislacion federal con impacto en PR
#   7. federalregister.gov — regulaciones federales
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from .base import BaseAgent
from .exceptions import BitCountingAgentError
from .messages import BaseAgentMessage, CPAInstruction, PolicyActivation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EXCEPCION ESPECIFICA
# ---------------------------------------------------------------------------

class ActualizadorError(BitCountingAgentError):
    """Error del agente ACTUALIZADOR durante el monitoreo de fuentes normativas."""

    def __init__(self, source_url: str, reason: str) -> None:
        self.source_url = source_url
        self.reason = reason
        super().__init__(
            f"ACTUALIZADOR: fallo al consultar fuente normativa '{source_url}'. "
            f"Motivo: {reason}. "
            "El resto de fuentes continua siendo procesado."
        )


# ---------------------------------------------------------------------------
# MONITOR RESULT — inmutable, requires_human_review SIEMPRE True
# ---------------------------------------------------------------------------

class MonitorResult(BaseModel):
    """
    Resultado de monitorear una fuente normativa.

    GARANTIA CRITICA: requires_human_review es SIEMPRE True.
    El sistema nunca auto-aplica cambios normativos.
    Toda actualizacion requiere aprobacion humana explicita.
    """

    model_config = ConfigDict(frozen=True)

    source_name:          str
    source_url:           str
    checked_at:           datetime
    change_detected:      bool
    change_type:          str   # rate_change | new_rule | rule_removal |
                                # rule_modification | deadline_change |
                                # form_update | no_change
    description:          str
    urgency:              str   # low | normal | high | critical
    affected_rule_ids:    tuple[str, ...]
    draft_update:         Optional[dict]
    requires_human_review: bool   # SIEMPRE True — invariante del sistema


# ---------------------------------------------------------------------------
# LAS 7 FUENTES NORMATIVAS (spec exacta)
# ---------------------------------------------------------------------------

_SOURCES: tuple[tuple[str, str, str], ...] = (
    (
        "hacienda.pr.gov",
        "https://hacienda.pr.gov",
        "Departamento de Hacienda PR — circulares, reglamentos, formularios",
    ),
    (
        "irs.gov",
        "https://irs.gov",
        "IRS Federal — Publication 15, Revenue Rulings, Notices",
    ),
    (
        "estado.pr.gov",
        "https://estado.pr.gov",
        "Estado PR — leyes aprobadas (LexJuris RSS)",
    ),
    (
        "dtrh.pr.gov",
        "https://dtrh.pr.gov",
        "DTRH PR — tasas SUTA, reglamentos laborales",
    ),
    (
        "boletinestado.pr.gov",
        "https://boletinestado.pr.gov",
        "Boletin Oficial del Estado Libre Asociado de PR",
    ),
    (
        "congress.gov",
        "https://congress.gov",
        "Congreso Federal — legislacion federal con impacto en PR",
    ),
    (
        "federalregister.gov",
        "https://federalregister.gov",
        "Federal Register — regulaciones federales",
    ),
)

_SOURCE_NAMES: frozenset[str] = frozenset(s[0] for s in _SOURCES)


# ---------------------------------------------------------------------------
# AGENTE ACTUALIZADOR
# ---------------------------------------------------------------------------

class ActualizadorAgent(BaseAgent):
    """
    ACTUALIZADOR — Monitorea 7 fuentes normativas y genera update drafts.

    GARANTIA CRITICA: Nunca auto-aplica cambios. Toda actualizacion requiere
    aprobacion humana. requires_human_review es siempre True en MonitorResult.

    Fase 1: stubs retornan no-change por defecto.
    Fase 3: HTTP fetch + diff contra version cacheada.
    """

    def __init__(self) -> None:
        """Inicializa el ACTUALIZADOR con lista de updates pendientes vacia."""
        # Updates pendientes de revision humana
        self._pending_updates: list[dict] = []

    # --- Identidad ---

    @property
    def agent_name(self) -> str:
        return "ACTUALIZADOR"

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
    # CHECK ALL SOURCES
    # -------------------------------------------------------------------------

    def check_all_sources(
        self,
        last_check_date: Optional[date] = None,
    ) -> list[MonitorResult]:
        """
        Verifica las 7 fuentes normativas y retorna un MonitorResult por fuente.

        Fase 1: stubs retornan no-change por defecto.
        requires_human_review es siempre True en todos los resultados.
        Nunca auto-aplica cambios.

        Args:
            last_check_date: Fecha del ultimo chequeo para calcular delta.
                             Si es None, usa hoy como referencia.

        Returns:
            Lista de 7 MonitorResult, uno por fuente. Todos tienen
            requires_human_review=True.
        """
        results: list[MonitorResult] = []
        for source_name, _url, _desc in _SOURCES:
            result = self.check_source(source_name)
            results.append(result)
            logger.debug(
                "[ACTUALIZADOR] Fuente '%s' verificada — change_detected=%s urgency=%s.",
                source_name, result.change_detected, result.urgency,
            )

        logger.info(
            "[ACTUALIZADOR] Chequeo completo: %d fuentes. "
            "Ninguna regla modificada automaticamente. "
            "requires_human_review=True en todos los resultados.",
            len(results),
        )
        return results

    # -------------------------------------------------------------------------
    # CHECK SOURCE (single)
    # -------------------------------------------------------------------------

    def check_source(self, source_name: str) -> MonitorResult:
        """
        Verifica una sola fuente normativa.

        Fase 1: stub — siempre retorna no-change.
        Fase 3: HTTP fetch + diff contra version cacheada.

        Args:
            source_name: Nombre de la fuente (ej: 'hacienda.pr.gov').

        Returns:
            MonitorResult con requires_human_review=True siempre.
        """
        source_url = next(
            (url for nm, url, _ in _SOURCES if nm == source_name),
            f"https://{source_name}",
        )

        # Fase 1: stub — siempre no-change
        return MonitorResult(
            source_name=source_name,
            source_url=source_url,
            checked_at=datetime.now(timezone.utc),
            change_detected=False,
            change_type="no_change",
            description=(
                f"[FASE 1 STUB] Fuente '{source_name}' verificada. "
                "Sin cambios normativos detectados en esta consulta simulada. "
                "En Fase 3 se implementara HTTP fetch y diff real contra cache."
            ),
            urgency="low",
            affected_rule_ids=(),
            draft_update=None,
            requires_human_review=True,   # SIEMPRE True — invariante del sistema
        )

    # -------------------------------------------------------------------------
    # GENERATE UPDATE DRAFT
    # -------------------------------------------------------------------------

    def generate_update_draft(
        self,
        monitor_result: MonitorResult,
        existing_rule_id: str,
    ) -> dict:
        """
        Crea un update draft estructurado desde un MonitorResult con cambio.

        Status siempre "PENDING_REVIEW" — nunca "APPROVED".
        El draft se almacena en la lista interna de pendientes.

        Args:
            monitor_result:   MonitorResult del chequeo de fuente.
            existing_rule_id: ID de la regla en tax_rules potencialmente afectada.

        Returns:
            Dict con: draft_id, status="PENDING_REVIEW", old_text, new_text,
            diff, estimated_affected_clients, fiscal_impact_estimate,
            requires_human_review=True.
        """
        draft_id = str(uuid.uuid4())
        old_text = (
            f"Regla '{existing_rule_id}' — version actual en tax_rules. "
            "Consultar tax_rules/registry.py para el valor actual."
        )
        new_text = (
            f"Cambio propuesto desde '{monitor_result.source_name}' "
            f"({monitor_result.change_type}): {monitor_result.description}"
        )
        diff = (
            f"- {old_text}\n"
            f"+ {new_text}\n"
            f"  tipo_cambio: {monitor_result.change_type}\n"
            f"  urgencia: {monitor_result.urgency}\n"
            f"  fuente_url: {monitor_result.source_url}"
        )

        draft: dict[str, Any] = {
            "draft_id":                    draft_id,
            "status":                      "PENDING_REVIEW",  # NUNCA "APPROVED"
            "requires_human_review":       True,              # SIEMPRE True
            "source_name":                 monitor_result.source_name,
            "source_url":                  monitor_result.source_url,
            "existing_rule_id":            existing_rule_id,
            "change_type":                 monitor_result.change_type,
            "urgency":                     monitor_result.urgency,
            "old_text":                    old_text,
            "new_text":                    new_text,
            "diff":                        diff,
            "description":                 monitor_result.description,
            "affected_rule_ids":           list(monitor_result.affected_rule_ids),
            "estimated_affected_clients":  "UNKNOWN — requiere analisis manual del CPA",
            "fiscal_impact_estimate":      "UNKNOWN — requiere analisis manual del CPA",
            "created_at":                  datetime.now(timezone.utc).isoformat(),
            "approved_by":                 None,
            "approved_at":                 None,
        }

        self._pending_updates.append(draft)

        logger.info(
            "[ACTUALIZADOR] Draft creado: draft_id=%s status=PENDING_REVIEW "
            "source='%s' rule='%s'.",
            draft_id, monitor_result.source_name, existing_rule_id,
        )

        return draft

    # -------------------------------------------------------------------------
    # GET PENDING UPDATES
    # -------------------------------------------------------------------------

    def get_pending_updates(self) -> list[dict]:
        """
        Retorna todos los update drafts pendientes de revision humana.

        Returns:
            Lista de dicts con status='PENDING_REVIEW'.
            Todos tienen requires_human_review=True.
        """
        return [d for d in self._pending_updates if d["status"] == "PENDING_REVIEW"]

    # -------------------------------------------------------------------------
    # FRAMEWORK — _process_impl
    # -------------------------------------------------------------------------

    def _process_impl(self, message: BaseAgentMessage) -> PolicyActivation:
        """
        Punto de entrada del framework BaseAgent.
        Dispara check_all_sources y encapsula resultados en PolicyActivation.
        """
        assert isinstance(message, CPAInstruction)

        results = self.check_all_sources()

        results_payload = [
            {
                "source_name":          r.source_name,
                "source_url":           r.source_url,
                "checked_at":           r.checked_at.isoformat(),
                "change_detected":      r.change_detected,
                "change_type":          r.change_type,
                "description":          r.description,
                "urgency":              r.urgency,
                "requires_human_review": r.requires_human_review,
            }
            for r in results
        ]

        return PolicyActivation(
            source_agent=self.agent_name,
            target_agent="CPA_REVIEW",
            from_instruction_id=message.message_id,
            rules_json={
                "type":                     "NORMATIVE_MONITOR_BATCH",
                "sources_checked":          len(results),
                "results":                  results_payload,
                "auto_applied":             False,
                "all_pending_human_review": True,
            },
            effective_from=datetime.now(timezone.utc),
            cpa_license=message.cpa_license,
            policy_description=(
                f"Monitor normativo: {len(results)} fuentes verificadas. "
                "Ningun cambio aplicado automaticamente. "
                "requires_human_review=True en todos los resultados."
            ),
        )


# ---------------------------------------------------------------------------
# EXPORTS
# ---------------------------------------------------------------------------

__all__ = [
    "ActualizadorAgent",
    "ActualizadorError",
    "MonitorResult",
]
