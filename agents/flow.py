# =============================================================================
# agents/flow.py
# Coordinador de flujo del sistema Bit-Counting Puerto Rico.
#
# PIPELINE:
#   INTAKE → CENTINELA → CLASIFICADOR → AUDITOR → FISCAL PR → ORQUESTADOR
#
# DISENO:
#   - process_document(): funcion de nivel superior para uso rapido.
#   - FlowCoordinator: clase con estado propio (Orchestrator, Centinela, agentes).
#   - FlowResult: modelo Pydantic frozen con todo el output del pipeline.
#   - Si CENTINELA emite PAUSE, retorna inmediatamente con final_decision="PAUSED".
#   - Si AUDITOR no verifica, marca final_decision="FAILED" (sin pasar a FISCAL PR).
#   - Cada paso se registra en el Orchestrator via log_decision().
# =============================================================================

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from agents import Orchestrator, Centinela, Intake
from agents.clasificador import ClasificadorAgent
from agents.auditor import AuditorAgent
from agents.fiscal_pr import FiscalPRAgent
from agents.messages import (
    AuditorVerification,
    CentinelaDecision,
    CentinelaEvaluation,
    ClassifierOutput,
    FiscalOutput,
    IntakeOutput,
    OrchestratorDecision,
)


# ---------------------------------------------------------------------------
# FlowResult — output inmutable del pipeline completo
# ---------------------------------------------------------------------------

class FlowResult(BaseModel):
    """
    Resultado completo de un ciclo del pipeline de procesamiento.

    Todos los campos son inmutables una vez creados (frozen=True).
    Los campos opcionales son None cuando el paso no fue ejecutado
    (por ejemplo, classifier_output=None si CENTINELA emitio PAUSE).
    """

    model_config = ConfigDict(frozen=True)

    intake_output: IntakeOutput
    centinela_evaluation: CentinelaEvaluation
    classifier_output: Optional[ClassifierOutput]
    auditor_verification: Optional[AuditorVerification]
    fiscal_output: Optional[FiscalOutput]
    final_decision: str                          # "COMPLETED", "PAUSED", "FAILED"
    pause_id: Optional[str]
    log: tuple[OrchestratorDecision, ...]
    total_confidence: Decimal


# ---------------------------------------------------------------------------
# FlowCoordinator — clase principal con estado propio por instancia
# ---------------------------------------------------------------------------

class FlowCoordinator:
    """
    Coordinador del flujo completo de procesamiento de documentos.

    Cada instancia crea su propio Orchestrator, Centinela y agentes.
    Esto garantiza que los logs y pausas de un flujo no interfieran
    con los de otro flujo paralelo.

    Uso tipico:
        coordinator = FlowCoordinator()
        result = coordinator.process(raw_input, source_format="json")
        log = coordinator.get_log()
    """

    def __init__(self) -> None:
        self._orchestrator = Orchestrator()
        self._centinela = Centinela()
        self._intake = Intake()
        self._clasificador = ClasificadorAgent()
        self._auditor = AuditorAgent()
        self._fiscal = FiscalPRAgent()

    def process(
        self,
        raw_input: dict,
        source_format: str = "manual",
    ) -> FlowResult:
        """
        Ejecuta el pipeline completo sobre un documento crudo.

        Args:
            raw_input:     Dict con los campos del documento (vendor, amount, etc.).
            source_format: Formato del documento fuente ("manual", "json", "pdf", etc.).

        Returns:
            FlowResult con todos los outputs del pipeline y el log de decisiones.
        """
        return _run_pipeline(
            raw_input=raw_input,
            source_format=source_format,
            orchestrator=self._orchestrator,
            centinela=self._centinela,
            intake=self._intake,
            clasificador=self._clasificador,
            auditor=self._auditor,
            fiscal=self._fiscal,
        )

    def get_log(self) -> tuple[OrchestratorDecision, ...]:
        """
        Retorna el log inmutable de decisiones del Orchestrator de esta instancia.
        """
        return self._orchestrator.get_decision_log()


# ---------------------------------------------------------------------------
# process_document() — funcion de nivel superior (convenience wrapper)
# ---------------------------------------------------------------------------

def process_document(
    raw_input: dict,
    source_format: str = "manual",
) -> FlowResult:
    """
    Procesa un documento a traves del pipeline completo.

    Crea una instancia fresca de FlowCoordinator por cada llamada,
    garantizando aislamiento completo entre invocaciones.

    Args:
        raw_input:     Dict con los campos del documento.
        source_format: Formato del documento fuente.

    Returns:
        FlowResult con el resultado completo del pipeline.
    """
    coordinator = FlowCoordinator()
    return coordinator.process(raw_input, source_format)


# ---------------------------------------------------------------------------
# _run_pipeline() — implementacion interna del pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    raw_input: dict,
    source_format: str,
    orchestrator: Orchestrator,
    centinela: Centinela,
    intake: Intake,
    clasificador: ClasificadorAgent,
    auditor: AuditorAgent,
    fiscal: FiscalPRAgent,
) -> FlowResult:
    """
    Implementacion del pipeline. Separada para permitir inyeccion de
    dependencias tanto desde FlowCoordinator como desde process_document().
    """

    # -------------------------------------------------------------------------
    # PASO 1: INTAKE — extrae campos del documento crudo
    # -------------------------------------------------------------------------
    intake_output: IntakeOutput = intake.process_document(
        raw_input=raw_input,
        source_format=source_format,
        orchestrator=orchestrator,
    )

    # -------------------------------------------------------------------------
    # PASO 2: CENTINELA — evalua riesgo y decide PROCEED o PAUSE
    # -------------------------------------------------------------------------
    centinela_eval: CentinelaEvaluation = centinela.evaluate(
        intake_output=intake_output,
        client_history=(),
        active_rules=(),
    )

    orchestrator.log_decision(
        agent_name="CENTINELA",
        input_msg=intake_output,
        output_msg=centinela_eval,
        rule_ids=centinela_eval.rule_conflicts,
        confidence=centinela_eval.confidence_at_evaluation,
        requires_cpa_review=(centinela_eval.decision == CentinelaDecision.PAUSE),
    )

    if centinela_eval.decision == CentinelaDecision.PAUSE:
        return FlowResult(
            intake_output=intake_output,
            centinela_evaluation=centinela_eval,
            classifier_output=None,
            auditor_verification=None,
            fiscal_output=None,
            final_decision="PAUSED",
            pause_id=centinela_eval.pause_id,
            log=orchestrator.get_decision_log(),
            total_confidence=centinela_eval.confidence_at_evaluation,
        )

    # -------------------------------------------------------------------------
    # PASO 3: CLASIFICADOR — asigna cuentas contables (partida doble)
    # -------------------------------------------------------------------------
    debit_entry, credit_entry = clasificador.classify(intake_output)

    orchestrator.log_decision(
        agent_name="CLASIFICADOR",
        input_msg=intake_output,
        output_msg=debit_entry,
        rule_ids=(debit_entry.rule_ref,),
        confidence=debit_entry.confidence,
    )

    # -------------------------------------------------------------------------
    # PASO 4: AUDITOR — verificacion cruzada independiente
    # -------------------------------------------------------------------------
    auditor_verification: AuditorVerification = auditor.verify(
        debit=debit_entry,
        credit=credit_entry,
        intake=intake_output,
    )

    orchestrator.log_decision(
        agent_name="AUDITOR",
        input_msg=debit_entry,
        output_msg=auditor_verification,
        rule_ids=(auditor_verification.independent_rule_ref,),
        confidence=Decimal("1.00") if auditor_verification.verified else Decimal("0.00"),
        requires_cpa_review=not auditor_verification.verified or bool(auditor_verification.fraud_flags),
    )

    if not auditor_verification.verified:
        return FlowResult(
            intake_output=intake_output,
            centinela_evaluation=centinela_eval,
            classifier_output=debit_entry,
            auditor_verification=auditor_verification,
            fiscal_output=None,
            final_decision="FAILED",
            pause_id=None,
            log=orchestrator.get_decision_log(),
            total_confidence=_average_confidence(
                centinela_eval.confidence_at_evaluation,
                debit_entry.confidence,
                Decimal("0.00"),
            ),
        )

    # -------------------------------------------------------------------------
    # PASO 5: FISCAL PR — calcula obligaciones tributarias
    # -------------------------------------------------------------------------
    fiscal_output: FiscalOutput = fiscal._process_impl(intake_output)

    orchestrator.log_decision(
        agent_name="FISCAL_PR",
        input_msg=intake_output,
        output_msg=fiscal_output,
        rule_ids=(fiscal_output.rule_ref,),
        confidence=Decimal("0.95"),
    )

    total_confidence = _average_confidence(
        centinela_eval.confidence_at_evaluation,
        debit_entry.confidence,
        Decimal("0.95"),
    )

    return FlowResult(
        intake_output=intake_output,
        centinela_evaluation=centinela_eval,
        classifier_output=debit_entry,
        auditor_verification=auditor_verification,
        fiscal_output=fiscal_output,
        final_decision="COMPLETED",
        pause_id=None,
        log=orchestrator.get_decision_log(),
        total_confidence=total_confidence,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _average_confidence(*values: Decimal) -> Decimal:
    """Promedio de varios scores de confianza. Redondea a 4 decimales."""
    if not values:
        return Decimal("0.00")
    total = sum(values, Decimal("0"))
    return (total / Decimal(len(values))).quantize(Decimal("0.0001"))
