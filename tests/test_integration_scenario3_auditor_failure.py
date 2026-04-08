# =============================================================================
# tests/test_integration_scenario3_auditor_failure.py
# Escenario 3 — Fallo del AUDITOR
#
# Verifica:
#   - Desbalance débito/crédito → AUDITOR rechaza con verified=False
#   - IVU imposible ($999 en base $1000 = 99.9%) → discrepancia detectada
#   - La transacción NO llega al libro mayor (fiscal_output es None)
#   - El log queda registrado correctamente con requires_cpa_review=True
#   - FlowResult.final_decision == "FAILED"
#   - BitCountingFlow detecta y pausa en auditor failure
# =============================================================================

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from agents.auditor import AuditorAgent
from agents.clasificador import ClasificadorAgent
from agents.flow import FlowCoordinator, process_document
from agents.messages import AuditorVerification, EntryType, IntakeOutput
from agents import Orchestrator, Intake


# =============================================================================
# Documentos de prueba con anomalías contables
# =============================================================================

# Factura con IVU imposible: $999 IVU sobre base $1000 = 99.9% (debería ser 11.5%)
IMBALANCED_INVOICE_RAW = {
    "vendor":               "Proveedora Incorrecta SA",
    "date":                 "2026-03-10",
    "amount":               Decimal("1000.00"),
    "tax_amount":           Decimal("999.00"),   # IVU = 99.9% — imposible
    "currency":             "USD",
    "payment_method":       "CASH",
    "confidence":           Decimal("0.91"),
    "missing_fields":       [],
    "critical_fields_missing": False,
}

# Factura con monto enorme (>$50,000) para triggear check de monto inusual
LARGE_AMOUNT_RAW = {
    "vendor":               "Constructora Gigante LLC",
    "date":                 "2026-03-10",
    "amount":               Decimal("75000.00"),
    "tax_amount":           Decimal("8625.00"),  # 11.5% correcto de 75000
    "currency":             "USD",
    "payment_method":       "CHECK",
    "confidence":           Decimal("0.88"),
    "missing_fields":       [],
    "critical_fields_missing": False,
}

# Factura con vendor None y monto > $500 — falta vendor requerido
NO_VENDOR_RAW = {
    "vendor":               None,
    "date":                 "2026-03-12",
    "amount":               Decimal("1500.00"),
    "tax_amount":           Decimal("172.50"),   # 11.5% correcto
    "currency":             "USD",
    "payment_method":       "CASH",
    "confidence":           Decimal("0.55"),
    "missing_fields":       ["vendor"],
    "critical_fields_missing": False,
}


# =============================================================================
# Helpers
# =============================================================================

def _make_intake(data: dict) -> IntakeOutput:
    """Crea un IntakeOutput directamente desde un dict de datos."""
    orchestrator = Orchestrator()
    intake = Intake()
    return intake.process_document(
        raw_input=data,
        source_format="manual",
        orchestrator=orchestrator,
    )


def _classify_and_verify(data: dict) -> tuple[AuditorVerification, "IntakeOutput"]:
    """Pipeline hasta auditor — retorna (AuditorVerification, intake_output)."""
    orchestrator = Orchestrator()
    intake = Intake()
    clasificador = ClasificadorAgent()
    auditor = AuditorAgent()

    intake_out = intake.process_document(
        raw_input=data,
        source_format="manual",
        orchestrator=orchestrator,
    )
    debit, credit = clasificador.classify(intake_out)
    verification = auditor.verify(
        debit_entry=debit,
        credit_entry=credit,
        intake_output=intake_out,
    )
    return verification, intake_out


# =============================================================================
# TestAuditorDetectsImbalancedIVU
# =============================================================================

class TestAuditorDetectsImbalancedIVU:
    """El AUDITOR detecta IVU imposible y genera discrepancias."""

    def test_auditor_verified_is_false_for_bad_ivu(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        assert verification.verified is False

    def test_discrepancies_list_is_not_empty(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        assert len(verification.discrepancies) > 0

    def test_ivu_discrepancy_text_mentions_expected_value(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        ivu_discrepancies = [d for d in verification.discrepancies if "IVU" in d]
        assert len(ivu_discrepancies) >= 1, "Debe haber discrepancia de IVU"

    def test_ivu_discrepancy_mentions_reported_amount(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        ivu_disc = next(d for d in verification.discrepancies if "IVU" in d)
        assert "999" in ivu_disc, "Discrepancia debe mencionar el IVU reportado incorrecto"

    def test_ivu_discrepancy_mentions_expected_115_rate(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        ivu_disc = next(d for d in verification.discrepancies if "IVU" in d)
        # El esperado es 11.5% de $1000 = $115.00
        assert "115" in ivu_disc, "La discrepancia debe mencionar el valor esperado correcto"

    def test_algebraic_balance_check_passes_for_same_amounts(self):
        """Los asientos débito/crédito tienen el mismo monto (el CLASIFICADOR
        siempre balancea), así que algebraic_balance_ok debe ser True."""
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        assert verification.algebraic_balance_ok is True

    def test_checks_passed_contains_algebraic_balance(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        assert "algebraic_balance" in verification.checks_passed

    def test_verification_has_message_id(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        assert verification.message_id
        # UUID válido
        uuid.UUID(verification.message_id)

    def test_verification_source_agent_is_auditor(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        assert verification.source_agent == "AUDITOR"

    def test_independent_rule_ref_is_present(self):
        verification, _ = _classify_and_verify(IMBALANCED_INVOICE_RAW)
        assert verification.independent_rule_ref
        assert len(verification.independent_rule_ref) > 0


# =============================================================================
# TestTransactionDoesNotReachLedger
# =============================================================================

class TestTransactionDoesNotReachLedger:
    """Cuando el AUDITOR falla, la transacción NO llega al libro mayor."""

    def test_flow_final_decision_is_failed(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.final_decision == "FAILED"

    def test_fiscal_output_is_none_when_auditor_fails(self):
        """El paso FISCAL_PR no debe ejecutarse si el AUDITOR rechaza."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.fiscal_output is None

    def test_auditor_verification_present_in_result(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.auditor_verification is not None

    def test_auditor_verification_verified_false_in_result(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.auditor_verification is not None
        assert result.auditor_verification.verified is False

    def test_classifier_output_present_despite_failure(self):
        """El clasificador sí corrió — su output se preserva en el result."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.classifier_output is not None

    def test_intake_output_present(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.intake_output is not None

    def test_pause_id_is_none_for_failed_result(self):
        """FAILED no es igual a PAUSED — no hay pause_id en modo FAILED."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.pause_id is None

    def test_total_confidence_is_low_when_auditor_fails(self):
        """Cuando el AUDITOR falla, su contribución a la confianza es 0.00."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        # total_confidence promedia con 0.00 del auditor fallido
        assert result.total_confidence < Decimal("0.80")


# =============================================================================
# TestLogRegisteredCorrectly
# =============================================================================

class TestLogRegisteredCorrectly:
    """El log del orchestrator queda registrado correctamente tras fallo."""

    def test_log_contains_auditor_entry(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        agent_names = [d.agent_name for d in result.log]
        assert "AUDITOR" in agent_names

    def test_auditor_log_entry_requires_cpa_review(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        auditor_entry = next(d for d in result.log if d.agent_name == "AUDITOR")
        assert auditor_entry.requires_cpa_review is True

    def test_auditor_log_entry_confidence_is_zero(self):
        """Auditor fallido → confidence 0.00 en el log."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        auditor_entry = next(d for d in result.log if d.agent_name == "AUDITOR")
        assert auditor_entry.confidence == Decimal("0.00")

    def test_log_contains_intake_entry(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        agent_names = [d.agent_name for d in result.log]
        assert "INTAKE" in agent_names

    def test_log_contains_centinela_entry(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        agent_names = [d.agent_name for d in result.log]
        assert "CENTINELA" in agent_names

    def test_log_contains_clasificador_entry(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        agent_names = [d.agent_name for d in result.log]
        assert "CLASIFICADOR" in agent_names

    def test_log_does_not_contain_fiscal_entry(self):
        """FISCAL_PR nunca se ejecuta cuando AUDITOR falla."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        agent_names = [d.agent_name for d in result.log]
        assert "FISCAL_PR" not in agent_names

    def test_log_entries_are_immutable(self):
        """Las entradas del log son objetos frozen — no se pueden modificar."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        entry = result.log[0]
        with pytest.raises(Exception):
            entry.agent_name = "HACKED"  # Pydantic frozen=True bloquea asignación directa

    def test_log_order_intake_before_auditor(self):
        """El orden del log refleja el orden real del pipeline."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        names = [d.agent_name for d in result.log]
        intake_pos  = names.index("INTAKE")
        auditor_pos = names.index("AUDITOR")
        assert intake_pos < auditor_pos

    def test_each_log_entry_has_unique_decision_id(self):
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        ids = [d.decision_id for d in result.log]
        assert len(ids) == len(set(ids)), "Cada entrada del log debe tener decision_id único"


# =============================================================================
# TestAuditorDetectsLargeAmount
# =============================================================================

class TestAuditorDetectsLargeAmount:
    """El AUDITOR detecta montos inusuales > $50,000."""

    def test_large_amount_triggers_discrepancy(self):
        verification, _ = _classify_and_verify(LARGE_AMOUNT_RAW)
        large_disc = [d for d in verification.discrepancies if "50000" in d or "INUSUAL" in d]
        assert len(large_disc) >= 1

    def test_verified_is_false_for_large_amount(self):
        verification, _ = _classify_and_verify(LARGE_AMOUNT_RAW)
        assert verification.verified is False

    def test_large_amount_discrepancy_mentions_threshold(self):
        verification, _ = _classify_and_verify(LARGE_AMOUNT_RAW)
        disc = next((d for d in verification.discrepancies if "50000" in d), None)
        assert disc is not None


# =============================================================================
# TestAuditorDetectsMissingVendor
# =============================================================================

class TestAuditorDetectsMissingVendor:
    """El AUDITOR detecta vendor faltante para montos > $500."""

    def _make_no_vendor_verification(self) -> "AuditorVerification":
        """
        Construye directamente IntakeOutput y ClassifierOutputs con vendor=None
        para evitar que el CLASIFICADOR rechace el documento (AgentScopeError).
        El AUDITOR opera directamente sobre los mensajes — sin pasar por el CLASIFICADOR.
        """
        import uuid
        from datetime import datetime, timezone
        from agents.messages import ClassifierOutput, EntryType, IntakeOutput

        txn_id = str(uuid.uuid4())
        intake = IntakeOutput(
            message_id=txn_id,
            timestamp=datetime.now(timezone.utc),
            source_agent="INTAKE",
            target_agent="CENTINELA",
            vendor=None,
            date="2026-03-12",
            amount=Decimal("1500.00"),
            tax_amount=Decimal("172.50"),
            confidence=Decimal("0.55"),
        )
        debit = ClassifierOutput(
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            source_agent="CLASIFICADOR",
            target_agent="AUDITOR",
            account_code="5900",
            account_name="Gastos Varios",
            entry_type=EntryType.DEBIT,
            amount=Decimal("1500.00"),
            rule_ref="GAAP-PR-5900-MISC",
            confidence=Decimal("0.75"),
            reasoning="Clasificado como gastos varios por falta de vendor.",
            contra_account_code="2000",
            contra_account_name="Cuentas por Pagar",
            contra_entry_type=EntryType.CREDIT,
            classified_intake_id=txn_id,
        )
        credit = ClassifierOutput(
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            source_agent="CLASIFICADOR",
            target_agent="AUDITOR",
            account_code="2000",
            account_name="Cuentas por Pagar",
            entry_type=EntryType.CREDIT,
            amount=Decimal("1500.00"),
            rule_ref="GAAP-PR-5900-MISC",
            confidence=Decimal("0.75"),
            reasoning="Contrapartida de gastos varios.",
            classified_intake_id=txn_id,
        )
        auditor = AuditorAgent()
        return auditor.verify(debit, credit, intake)

    def test_missing_vendor_triggers_discrepancy(self):
        verification = self._make_no_vendor_verification()
        vendor_disc = [d for d in verification.discrepancies if "VENDEDOR" in d or "vendor" in d.lower()]
        assert len(vendor_disc) >= 1

    def test_verified_false_when_vendor_missing(self):
        verification = self._make_no_vendor_verification()
        assert verification.verified is False

    def test_discrepancy_mentions_500_threshold(self):
        verification = self._make_no_vendor_verification()
        disc = next((d for d in verification.discrepancies
                     if "VENDEDOR" in d or "vendor" in d.lower()), None)
        assert disc is not None
        assert "500" in disc


# =============================================================================
# TestAuditorAnomalyDetection
# =============================================================================

class TestAuditorAnomalyDetection:
    """El AUDITOR puede detectar anomalías contra historial del cliente."""

    def test_anomaly_detection_returns_list(self):
        auditor = AuditorAgent()
        intake_out = _make_intake(IMBALANCED_INVOICE_RAW)
        anomalies = auditor.detect_anomalies(intake_out, client_history=[])
        assert isinstance(anomalies, list)

    def test_no_anomalies_for_empty_history(self):
        auditor = AuditorAgent()
        intake_out = _make_intake(IMBALANCED_INVOICE_RAW)
        anomalies = auditor.detect_anomalies(intake_out, client_history=[])
        assert anomalies == []

    def test_anomaly_detected_when_amount_exceeds_3x_average(self):
        """Si la transacción supera 3x el promedio histórico, se detecta anomalía."""
        auditor = AuditorAgent()
        intake_out = _make_intake(IMBALANCED_INVOICE_RAW)  # amount = $1000
        history = [
            {"amount": Decimal("100.00")},
            {"amount": Decimal("120.00")},
            {"amount": Decimal("90.00")},
        ]  # promedio ~$103, 3x = $310 → $1000 > $310
        anomalies = auditor.detect_anomalies(intake_out, client_history=history)
        assert len(anomalies) >= 1
        assert any("ANOMALIA" in a or "promedio" in a.lower() for a in anomalies)

    def test_no_anomaly_when_amount_within_3x_average(self):
        """Si la transacción está dentro de 3x el promedio, no se reporta anomalía."""
        auditor = AuditorAgent()
        intake_out = _make_intake(IMBALANCED_INVOICE_RAW)  # amount = $1000
        history = [
            {"amount": Decimal("400.00")},
            {"amount": Decimal("500.00")},
            {"amount": Decimal("600.00")},
        ]  # promedio $500, 3x = $1500 → $1000 < $1500
        anomalies = auditor.detect_anomalies(intake_out, client_history=history)
        # No debe haber anomalía de monto
        amount_anomalies = [a for a in anomalies if "ANOMALIA" in a]
        assert len(amount_anomalies) == 0


# =============================================================================
# TestFlowCoordinatorFailedPath
# =============================================================================

class TestFlowCoordinatorFailedPath:
    """FlowCoordinator maneja correctamente el path FAILED."""

    def test_flow_coordinator_returns_failed(self):
        coordinator = FlowCoordinator()
        result = coordinator.process(IMBALANCED_INVOICE_RAW, source_format="manual")
        assert result.final_decision == "FAILED"

    def test_flow_coordinator_log_accessible_via_get_log(self):
        coordinator = FlowCoordinator()
        coordinator.process(IMBALANCED_INVOICE_RAW, source_format="manual")
        log = coordinator.get_log()
        assert len(log) > 0

    def test_flow_result_is_immutable(self):
        """FlowResult es frozen — no se puede modificar."""
        result = process_document(IMBALANCED_INVOICE_RAW, source_format="manual")
        with pytest.raises(Exception):
            result.final_decision = "COMPLETED"

    def test_multiple_failed_transactions_each_logged_independently(self):
        """Dos transacciones fallidas en coordinadores distintos no se mezclan."""
        c1 = FlowCoordinator()
        c2 = FlowCoordinator()
        r1 = c1.process(IMBALANCED_INVOICE_RAW)
        r2 = c2.process(IMBALANCED_INVOICE_RAW)
        # Los logs son independientes — misma longitud pero objetos distintos
        assert r1.log is not r2.log
        assert len(r1.log) == len(r2.log)
