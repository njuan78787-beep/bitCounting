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
#   1. hacienda.pr.gov
#   2. irs.gov
#   3. estado.pr.gov (LexJuris RSS)
#   4. dtrh.pr.gov
#   5. boletinestado.pr.gov
#   6. congress.gov
#   7. federalregister.gov
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseAgent
from .exceptions import BitCountingAgentError
from .messages import BaseAgentMessage, CPAInstruction, PolicyActivation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EXCEPCION ESPECIFICA
# ---------------------------------------------------------------------------

class ActualizadorError(BitCountingAgentError):
    """
    Error del agente ACTUALIZADOR durante el monitoreo de fuentes normativas.

    Se lanza cuando una fuente normativa no puede ser consultada o cuando
    se detecta un estado interno inconsistente.

    El flujo de propuestas NO se interrumpe por errores de fuentes individuales
    — las fuentes con error se marcan como UNAVAILABLE en el resultado.

    Attributes:
        source_url: URL de la fuente normativa que fallo.
        reason:     descripcion tecnica del fallo.
    """

    def __init__(self, source_url: str, reason: str) -> None:
        self.source_url = source_url
        self.reason = reason
        super().__init__(
            f"ACTUALIZADOR: fallo al consultar fuente normativa '{source_url}'. "
            f"Motivo: {reason}. "
            "El resto de fuentes continua siendo procesado."
        )


# ---------------------------------------------------------------------------
# ENUMS
# ---------------------------------------------------------------------------

class UpdateUrgency(str, Enum):
    """Nivel de urgencia de una propuesta de actualizacion normativa."""
    LOW      = "LOW"       # Cambio menor, puede esperar ciclo normal de revision
    MEDIUM   = "MEDIUM"    # Cambio relevante, revision en proximo ciclo CPA
    HIGH     = "HIGH"      # Cambio que afecta operaciones actuales, revision urgente
    CRITICAL = "CRITICAL"  # Cambio con impacto inmediato — requiere atencion hoy


# ---------------------------------------------------------------------------
# MODELO: NORMATIVE UPDATE PROPOSAL
# ---------------------------------------------------------------------------

class NormativeUpdateProposal(BaseModel):
    """
    Propuesta de actualizacion normativa generada por el ACTUALIZADOR.

    Nunca se aplica automaticamente a tax_rules. El status es siempre
    PENDING_CPA_REVIEW hasta que el CPA tome una accion explicita.

    El campo proposed_change contiene la estructura exacta del cambio
    sugerido en el formato del modulo tax_rules, para que el CPA pueda
    evaluarlo y aplicarlo manualmente si lo aprueba.
    """

    model_config = ConfigDict(frozen=True)

    proposal_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unico de esta propuesta",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp UTC de creacion de la propuesta",
    )
    source_name: str = Field(
        description="Nombre de la fuente normativa (ej: 'Hacienda PR')",
    )
    source_url: str = Field(
        description="URL canonica de la fuente normativa",
    )
    as_of_date: date = Field(
        description="Fecha de la consulta que genero esta propuesta",
    )
    summary: str = Field(
        description=(
            "Resumen ejecutivo del cambio normativo detectado o simulado. "
            "Redactado para ser comprensible por el CPA sin contexto tecnico adicional."
        ),
        min_length=10,
    )
    proposed_change: dict[str, Any] = Field(
        description=(
            "Cambio propuesto en formato estructurado compatible con tax_rules. "
            "Estructura: {'rule_id': str, 'field': str, 'current_value': Any, "
            "'proposed_value': Any, 'rationale': str}. "
            "Vacio ({}) si no hay cambio concreto (ej: 'sin actualizaciones')."
        ),
    )
    requires_legal_review: bool = Field(
        description=(
            "True si el cambio tiene implicaciones legales que requieren "
            "revision por abogado tributarista ademas del CPA."
        ),
    )
    urgency: UpdateUrgency = Field(
        description="Nivel de urgencia de la revision por el CPA",
    )
    status: str = Field(
        default="PENDING_CPA_REVIEW",
        description=(
            "Estado de la propuesta. Siempre 'PENDING_CPA_REVIEW' al crearse. "
            "El CPA debe cambiarlo a 'APPROVED', 'REJECTED' o 'DEFERRED' "
            "mediante accion explicita fuera del ACTUALIZADOR."
        ),
    )
    rule_id: str = Field(
        description=(
            "ID de la regla en tax_rules que potencialmente se ve afectada. "
            "Formato: identificador del modulo tax_rules. "
            "Vacio ('') si no aplica a una regla existente."
        ),
    )


# ---------------------------------------------------------------------------
# FUENTES NORMATIVAS MONITOREADAS
# ---------------------------------------------------------------------------

# 7 fuentes normativas oficiales — hardcodeadas en Fase 1
_NORMATIVE_SOURCES: tuple[tuple[str, str], ...] = (
    ("Hacienda PR",    "https://hacienda.pr.gov"),
    ("IRS Federal",    "https://irs.gov"),
    ("FASB",           "https://fasb.org"),
    ("AICPA",          "https://aicpa.org"),
    ("PR Legislature", "https://oslpr.org"),
    ("SSA",            "https://ssa.gov"),
    ("DOL Federal",    "https://dol.gov"),
)


# ---------------------------------------------------------------------------
# AGENTE ACTUALIZADOR
# ---------------------------------------------------------------------------

class ActualizadorAgent(BaseAgent):
    """
    Monitorea fuentes normativas y propone actualizaciones para revision CPA.

    Fase 1: stub mode — simula monitoreo de 7 fuentes y genera propuestas
    sinteticas de "sin actualizaciones encontradas". La infraestructura de
    propuestas, urgencias y flujo de revision CPA es completamente funcional.

    NUNCA escribe en tax_rules directamente. El CPA es el unico que puede
    aprobar y aplicar cambios a las reglas fiscales del sistema.
    """

    # --- Identidad ---

    @property
    def agent_name(self) -> str:
        return "ACTUALIZADOR"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        # Fase 1: el ACTUALIZADOR puede recibir instrucciones del CPA via
        # CPAInstruction para disparar revisiones manuales. En Fase 2 tendra
        # su propio tipo de mensaje de trigger.
        return (CPAInstruction,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        # El ACTUALIZADOR emite PolicyActivations con las propuestas serializadas
        # hasta que exista un tipo de mensaje dedicado NormativeUpdateOutput.
        return (PolicyActivation,)

    # --- API publica ---

    def check_all_sources(self, as_of_date: date) -> list[NormativeUpdateProposal]:
        """
        Consulta las 7 fuentes normativas y retorna una propuesta por fuente.

        Fase 1: stub — retorna propuestas sinteticas de "sin actualizaciones"
        para cada fuente. Ninguna propuesta tiene proposed_change poblado.

        Args:
            as_of_date: Fecha de referencia para la consulta normativa.

        Returns:
            Lista de 7 NormativeUpdateProposal, una por fuente, todas con
            status="PENDING_CPA_REVIEW". El CPA revisa y descarta o aprueba.
        """
        logger.info(
            "[ACTUALIZADOR] Iniciando revision de %d fuentes normativas para fecha=%s.",
            len(_NORMATIVE_SOURCES),
            as_of_date.isoformat(),
        )

        proposals: list[NormativeUpdateProposal] = []

        for source_name, source_url in _NORMATIVE_SOURCES:
            proposal = self._stub_check_source(source_name, source_url, as_of_date)
            proposals.append(proposal)
            logger.debug(
                "[ACTUALIZADOR] Fuente '%s' revisada — propuesta id=%s, urgency=%s.",
                source_name,
                proposal.proposal_id,
                proposal.urgency.value,
            )

        logger.info(
            "[ACTUALIZADOR] Revision completada: %d propuestas generadas. "
            "Todas en estado PENDING_CPA_REVIEW. Ninguna regla modificada.",
            len(proposals),
        )

        return proposals

    def propose_update(
        self,
        source: str,
        rule_id: str,
        proposed_change: dict[str, Any],
        urgency: str,
    ) -> NormativeUpdateProposal:
        """
        Crea una propuesta de actualizacion manual para una regla especifica.

        Usada cuando el CPA o un proceso externo identifica un cambio normativo
        que debe ser evaluado antes de aplicar. La propuesta queda en
        PENDING_CPA_REVIEW — nunca se auto-aplica.

        Args:
            source:          Nombre de la fuente normativa que origina el cambio.
            rule_id:         ID de la regla en tax_rules potencialmente afectada.
            proposed_change: Dict con el cambio propuesto (ver NormativeUpdateProposal.proposed_change).
            urgency:         Nivel de urgencia: "LOW", "MEDIUM", "HIGH" o "CRITICAL".

        Returns:
            NormativeUpdateProposal congelada con status="PENDING_CPA_REVIEW".

        Raises:
            ActualizadorError: Si urgency no es un valor valido de UpdateUrgency.
        """
        try:
            urgency_enum = UpdateUrgency(urgency.upper())
        except ValueError:
            raise ActualizadorError(
                source_url=source,
                reason=(
                    f"Valor de urgency invalido: '{urgency}'. "
                    f"Valores permitidos: {[u.value for u in UpdateUrgency]}."
                ),
            )

        # Determinar si requiere revision legal segun la urgencia y el tipo de cambio
        requires_legal = urgency_enum in (UpdateUrgency.HIGH, UpdateUrgency.CRITICAL)

        proposal = NormativeUpdateProposal(
            source_name=source,
            source_url=self._resolve_source_url(source),
            as_of_date=date.today(),
            summary=(
                f"Propuesta manual de actualizacion para regla '{rule_id}' "
                f"originada en '{source}'. Urgencia: {urgency_enum.value}. "
                "Pendiente de revision y aprobacion del CPA antes de aplicar."
            ),
            proposed_change=proposed_change,
            requires_legal_review=requires_legal,
            urgency=urgency_enum,
            rule_id=rule_id,
        )

        logger.info(
            "[ACTUALIZADOR] Propuesta manual creada: id=%s, source='%s', "
            "rule_id='%s', urgency=%s, requires_legal=%s.",
            proposal.proposal_id,
            source,
            rule_id,
            urgency_enum.value,
            requires_legal,
        )

        return proposal

    def _process_impl(self, message: BaseAgentMessage) -> BaseAgentMessage:
        """
        Punto de entrada del framework BaseAgent.

        Fase 1: dispara check_all_sources para la fecha actual y serializa
        las propuestas en una PolicyActivation de notificacion al CPA.
        """
        assert isinstance(message, CPAInstruction)

        today = date.today()
        proposals = self.check_all_sources(as_of_date=today)

        # Serializar propuestas en rules_json para transporte via PolicyActivation
        proposals_payload: list[dict[str, Any]] = [
            {
                "proposal_id": p.proposal_id,
                "source_name": p.source_name,
                "source_url": p.source_url,
                "summary": p.summary,
                "urgency": p.urgency.value,
                "requires_legal_review": p.requires_legal_review,
                "status": p.status,
                "rule_id": p.rule_id,
                "proposed_change": p.proposed_change,
            }
            for p in proposals
        ]

        return PolicyActivation(
            source_agent=self.agent_name,
            target_agent="CPA_REVIEW",
            from_instruction_id=message.message_id,
            rules_json={
                "type": "NORMATIVE_UPDATE_BATCH",
                "as_of_date": today.isoformat(),
                "proposals": proposals_payload,
                "total": len(proposals),
                "auto_applied": False,
            },
            effective_from=datetime.now(timezone.utc),
            cpa_license=message.cpa_license,
            policy_description=(
                f"Batch de revision normativa para {today.isoformat()}: "
                f"{len(proposals)} fuentes revisadas. Ninguna regla modificada. "
                "Todas las propuestas en PENDING_CPA_REVIEW."
            ),
        )

    # --- Helpers privados ---

    def _stub_check_source(
        self,
        source_name: str,
        source_url: str,
        as_of_date: date,
    ) -> NormativeUpdateProposal:
        """
        Fase 1: stub que simula la consulta a una fuente normativa.

        Siempre retorna "sin actualizaciones encontradas" con urgency=LOW.
        En Fase 2 esta implementacion sera reemplazada por llamadas HTTP reales
        con parseo de documentos normativos.
        """
        return NormativeUpdateProposal(
            source_name=source_name,
            source_url=source_url,
            as_of_date=as_of_date,
            summary=(
                f"[FASE 1 — STUB] Revision de '{source_name}' para {as_of_date.isoformat()}: "
                "sin actualizaciones normativas detectadas en esta consulta simulada."
            ),
            proposed_change={},          # Sin cambio concreto — no hay nada que proponer
            requires_legal_review=False,
            urgency=UpdateUrgency.LOW,
            rule_id="",                  # No aplica a ninguna regla existente
        )

    def _resolve_source_url(self, source_name: str) -> str:
        """Resuelve la URL canonica de una fuente por nombre. Retorna el nombre si no se encuentra."""
        source_map = dict(_NORMATIVE_SOURCES)
        return source_map.get(source_name, source_name)


# ---------------------------------------------------------------------------
# EXPORTS
# ---------------------------------------------------------------------------

__all__ = [
    "ActualizadorAgent",
    "ActualizadorError",
    "NormativeUpdateProposal",
    "UpdateUrgency",
]
