# =============================================================================
# tests/test_integration_scenario1_clean_transaction.py
#
# ESCENARIO 1 — Transacción limpia: factura de materiales en Puerto Rico
#
# Flujo completo:
#   Factura subida → INTAKE extrae campos → CENTINELA aprueba sin pausa
#   → CLASIFICADOR asigna cuenta → FISCAL PR calcula IVU correcto (11.5 %)
#   → AUDITOR pasa todas las verificaciones → queda en libro mayor
#   → trace completo es reconstruible
#
# Cobertura:
#   - agents/flow.py          FlowCoordinator.process()
#   - agents/intake_v2.py     IntakeAgentV2
#   - agents/centinela.py     Centinela (via FlowCoordinator)
#   - agents/clasificador_v2.py ClasificadorAgentV2
#   - agents/fiscal_v2.py     FiscalAgentV2 / FISCAL PR
#   - agents/auditor.py       AuditorAgent
#   - agents/messages.py      Toda la jerarquía de mensajes
#   - tax_rules/engine.py     calculate_ivu()
#   - agents/core_decisions.py AgentDecisionsLog (trace)
# =============================================================================

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from agents.messages import (
    CentinelaDecision,
    EntryType,
    SourceFormat,
)

# IVU Puerto Rico: 10.5 % estatal + 1.0 % municipal = 11.5 % total
IVU_RATE_PR = Decimal("0.115")

# Tolerancia para comparaciones monetarias (centavos)
MONEY_TOL = Decimal("0.01")


# =============================================================================
# 1.1 — Extracción de campos por INTAKE
# =============================================================================

class TestIntakeExtraction:

    def test_intake_extracts_vendor(self, flow, CLEAN_INVOICE_RAW=None):
        """INTAKE debe extraer el nombre del proveedor correctamente."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output is not None
        assert result.intake_output.vendor == "Ferretería San Juan LLC"

    def test_intake_extracts_amount(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.amount == Decimal("2450.00")

    def test_intake_extracts_tax_amount(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.tax_amount == Decimal("281.75")

    def test_intake_extracts_date(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.date == "2026-03-15"

    def test_intake_extracts_payment_method(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.payment_method == "CHECK"

    def test_intake_no_critical_fields_missing(self, flow):
        """Con documento completo, critical_fields_missing debe ser False."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.critical_fields_missing is False

    def test_intake_confidence_above_threshold(self, flow):
        """Documento completo debe producir confidence ≥ 0.75."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.confidence >= Decimal("0.75"), (
            f"Confidence {result.intake_output.confidence} demasiado baja para documento completo"
        )

    def test_intake_message_has_uuid(self, flow):
        """El message_id debe ser un UUID válido."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        assert uuid_pattern.match(result.intake_output.message_id)

    def test_intake_source_agent_correct(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.source_agent == "INTAKE"

    def test_intake_target_is_centinela(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.intake_output.target_agent == "CENTINELA"


# =============================================================================
# 1.2 — CENTINELA aprueba sin pausa
# =============================================================================

class TestCentinelaApprovesCleanTransaction:

    def test_centinela_decision_is_proceed(self, flow):
        """Factura limpia → CENTINELA debe emitir PROCEED."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.centinela_evaluation is not None
        assert result.centinela_evaluation.decision == CentinelaDecision.PROCEED

    def test_no_pause_id_on_clean_transaction(self, flow):
        """PROCEED → pause_id debe ser None."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.pause_id is None
        assert result.centinela_evaluation.pause_id is None

    def test_centinela_preserves_evaluated_intake_id(self, flow):
        """El CENTINELA debe referenciar el message_id del INTAKE."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert (
            result.centinela_evaluation.evaluated_intake_id
            == result.intake_output.message_id
        )

    def test_centinela_source_agent(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.centinela_evaluation.source_agent == "CENTINELA"


# =============================================================================
# 1.3 — CLASIFICADOR asigna cuenta correcta
# =============================================================================

class TestClasificadorAssignsAccount:

    def test_classifier_output_present(self, flow):
        """Transacción PROCEED → ClassifierOutput debe existir."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.classifier_output is not None

    def test_classifier_account_code_is_expense(self, flow):
        """Materiales de construcción → cuenta de gastos (5xxx)."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        code = result.classifier_output.account_code
        assert code.startswith("5"), (
            f"Cuenta {code!r} no es una cuenta de gastos (debería comenzar con 5)"
        )

    def test_classifier_entry_type_is_debit(self, flow):
        """Gasto → el asiento principal debe ser DEBIT."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.classifier_output.entry_type == EntryType.DEBIT

    def test_classifier_amount_matches_invoice(self, flow):
        """El monto clasificado debe coincidir con el monto de la factura."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        diff = abs(result.classifier_output.amount - Decimal("2450.00"))
        assert diff <= MONEY_TOL

    def test_classifier_has_contra_account(self, flow):
        """Partida doble: debe existir cuenta de contrapartida (crédito)."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.classifier_output.contra_account_code is not None
        assert result.classifier_output.contra_entry_type == EntryType.CREDIT

    def test_classifier_reasoning_not_empty(self, flow):
        """El razonamiento no puede estar vacío — mínimo 10 chars."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert len(result.classifier_output.reasoning) >= 10

    def test_classifier_references_intake_id(self, flow):
        """El CLASIFICADOR debe referenciar el intake_id para trazabilidad."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert (
            result.classifier_output.classified_intake_id
            == result.intake_output.message_id
        )

    def test_classifier_confidence_positive(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.classifier_output.confidence > Decimal("0")


# =============================================================================
# 1.4 — FISCAL PR calcula IVU correcto (11.5 %)
# =============================================================================

class TestFiscalPRCalculatesCorrectIVU:

    def test_fiscal_output_present(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.fiscal_output is not None

    def test_fiscal_ivu_matches_document(self, flow):
        """IVU calculado debe coincidir con el reportado en la factura."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        diff = abs(result.fiscal_output.tax_liability - Decimal("281.75"))
        assert diff <= MONEY_TOL, (
            f"IVU esperado $281.75, calculado {result.fiscal_output.tax_liability}"
        )

    def test_fiscal_ivu_rate_is_11_5_percent(self, flow):
        """Verificar que la tasa efectiva es exactamente 11.5 % (10.5 + 1.0)."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        fo = result.fiscal_output
        effective_rate = fo.tax_liability / fo.taxable_base
        diff = abs(effective_rate - IVU_RATE_PR)
        assert diff <= Decimal("0.001"), (
            f"Tasa IVU efectiva {effective_rate:.4f} ≠ 11.5 % esperado"
        )

    def test_fiscal_form_is_sc_2915(self, flow):
        """Formulario para IVU en PR debe ser SC-2915."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.fiscal_output.form_id == "SC-2915"

    def test_fiscal_rule_ref_is_ivu_estatal(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert "IVU_ESTATAL_PR" in result.fiscal_output.rule_ref

    def test_fiscal_calc_hash_present(self, flow):
        """El hash de cálculo debe estar presente (integridad)."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.fiscal_output.calc_hash
        assert len(result.fiscal_output.calc_hash) >= 8

    def test_fiscal_calculation_code_is_python(self, flow):
        """El campo calculation_code debe contener código Python ejecutable."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        code = result.fiscal_output.calculation_code
        assert "calculate_ivu" in code or "Decimal" in code, (
            "calculation_code debe referenciar calculate_ivu o Decimal"
        )

    def test_fiscal_taxable_base_correct(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        diff = abs(result.fiscal_output.taxable_base - Decimal("2450.00"))
        assert diff <= MONEY_TOL

    def test_fiscal_ivu_engine_direct():
        """Test directo del motor de IVU — sin agentes."""
        from tax_rules.engine import calculate_ivu
        from datetime import date

        ivu = calculate_ivu(Decimal("2450.00"), date(2026, 3, 15))
        assert ivu.ivu_estatal_amount == Decimal("257.25")   # 10.5 %
        assert ivu.ivu_municipal_amount == Decimal("24.50")  # 1.0 %
        assert ivu.total_ivu == Decimal("281.75")            # 11.5 %
        assert ivu.calc_hash  # hash presente para trazabilidad

    test_fiscal_ivu_engine_direct = staticmethod(test_fiscal_ivu_engine_direct)


# =============================================================================
# 1.5 — AUDITOR pasa todas las verificaciones
# =============================================================================

class TestAuditorPassesAllChecks:

    def test_auditor_verified_true(self, flow):
        """Con factura correcta, AUDITOR.verified debe ser True."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.auditor_verification is not None
        assert result.auditor_verification.verified is True

    def test_auditor_no_discrepancies(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert len(result.auditor_verification.discrepancies) == 0, (
            f"Discrepancias inesperadas: {result.auditor_verification.discrepancies}"
        )

    def test_auditor_no_fraud_flags(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert len(result.auditor_verification.fraud_flags) == 0

    def test_auditor_algebraic_balance_ok(self, flow):
        """Débito = Crédito — balance algebraico correcto."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.auditor_verification.algebraic_balance_ok is True

    def test_auditor_checks_passed_not_empty(self, flow):
        """Debe reportar al menos una verificación superada."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert len(result.auditor_verification.checks_passed) >= 1

    def test_auditor_account_code_matches_classifier(self, flow):
        """AUDITOR debe llegar independientemente a la misma cuenta que CLASIFICADOR."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert (
            result.auditor_verification.independent_account_code
            == result.classifier_output.account_code
        )

    def test_auditor_references_classifier_id(self, flow):
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert (
            result.auditor_verification.verified_classifier_id
            == result.classifier_output.message_id
        )


# =============================================================================
# 1.6 — Libro mayor y decisión final
# =============================================================================

class TestBookEntryAndFinalDecision:

    def test_final_decision_is_completed(self, flow):
        """Flujo limpio → final_decision debe ser COMPLETED."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.final_decision == "COMPLETED", (
            f"final_decision={result.final_decision!r}, centinela={result.centinela_evaluation.decision}"
        )

    def test_total_confidence_above_threshold(self, flow):
        """La confianza total debe superar 0.75 para transacción completada."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        assert result.total_confidence >= Decimal("0.75")


# =============================================================================
# 1.7 — Trazabilidad: trace completo reconstruible
# =============================================================================

class TestTraceIsFullyReconstructible:

    def test_log_has_all_agent_steps(self, flow):
        """El log debe contener decisiones de INTAKE, CENTINELA, CLASIFICADOR,
        AUDITOR y FISCAL_PR — al menos 4 pasos."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        agents_in_log = {d.agent_name for d in result.log}
        required = {"INTAKE", "CENTINELA", "CLASIFICADOR", "AUDITOR"}
        assert required.issubset(agents_in_log), (
            f"Agentes faltantes en el log: {required - agents_in_log}"
        )

    def test_log_entries_are_immutable(self, flow):
        """Los OrchestratorDecision son frozen — no se pueden modificar."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        d = result.log[0]
        with pytest.raises(Exception):   # Pydantic frozen raises ValidationError
            d.agent_name = "MANIPULADO"  # type: ignore[misc]

    def test_log_entries_have_unique_decision_ids(self, flow):
        """Cada decisión en el log debe tener un decision_id único."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        ids = [d.decision_id for d in result.log]
        assert len(ids) == len(set(ids)), "decision_ids duplicados en el log"

    def test_trace_ids_form_chain(self, flow):
        """Los message_ids deben formar una cadena de referencias coherente."""
        from tests.conftest import CLEAN_INVOICE_RAW
        result = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        # CENTINELA debe referenciar el IntakeOutput
        assert (
            result.centinela_evaluation.evaluated_intake_id
            == result.intake_output.message_id
        )
        # AUDITOR debe referenciar el ClassifierOutput
        assert (
            result.auditor_verification.verified_classifier_id
            == result.classifier_output.message_id
        )

    def test_two_documents_dont_share_state(self, flow):
        """Dos documentos procesados en el mismo FlowCoordinator no comparten estado."""
        from tests.conftest import CLEAN_INVOICE_RAW, LOW_CONFIDENCE_RAW
        r1 = flow.process(raw_input=CLEAN_INVOICE_RAW, source_format="json")
        r2 = flow.process(raw_input=CLEAN_INVOICE_RAW.copy(), source_format="json")
        assert r1.intake_output.message_id != r2.intake_output.message_id
        assert r1.intake_output.timestamp != r2.intake_output.timestamp

    def test_orchestrator_trace_complete(self, orchestrator, redis_client):
        """OrchestratorV2: el trace almacenado en Redis es reconstruible."""
        from tests.conftest import CLEAN_INVOICE_BYTES
        from agents.orchestrator_v2 import Q_NORMAL

        trace_id = orchestrator.submit_document(
            CLEAN_INVOICE_BYTES, "image_jpg", "client-demo-001"
        )
        for _ in range(10):
            if not orchestrator.run_one(Q_NORMAL):
                break

        trace = orchestrator.get_trace(trace_id)
        assert "steps" in trace
        step_names = [s["step_name"] for s in trace["steps"]]
        assert "INTAKE" in step_names, f"INTAKE no encontrado en trace: {step_names}"

    def test_orchestrator_trace_id_is_uuid(self, orchestrator):
        from tests.conftest import CLEAN_INVOICE_BYTES
        trace_id = orchestrator.submit_document(
            CLEAN_INVOICE_BYTES, "image_jpg", "client-demo-001"
        )
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
        assert uuid_pattern.match(trace_id), f"trace_id no es UUID v4: {trace_id!r}"
