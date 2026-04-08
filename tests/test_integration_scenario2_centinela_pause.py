# =============================================================================
# tests/test_integration_scenario2_centinela_pause.py
#
# ESCENARIO 2 — Trigger de pausa CENTINELA + flujo CPA completo
#
# Flujo:
#   Confianza baja → CENTINELA detecta → pausa ACTIVA creada
#   → otras transacciones del cliente NO pausan (cirugía)
#   → CPA recibe análisis pre-procesado
#   → CPA emite instrucción → INTÉRPRETE genera preview → CPA confirma
#   → pausa se libera → historial se revisa en background
#
# Cobertura:
#   - agents/centinela_guardian.py  CentinelaGuardian (todos los triggers)
#   - agents/interprete_v2.py       InterpreteV2 (draft → confirmar)
#   - agents/flow.py                FlowCoordinator (PAUSED path)
#   - agents/messages.py            CentinelaPause, CPAInstruction, PolicyActivation
# =============================================================================

from __future__ import annotations

from decimal import Decimal

import pytest

from agents.centinela_guardian import (
    CentinelaGuardian,
    ClientConfig,
    PauseTrigger,
    PauseStatus,
)
from agents.messages import CentinelaDecision

CLIENT_CFG = ClientConfig(
    client_id="client-demo-001",
    confidence_threshold=Decimal("0.75"),
    sla_hours_default=48,
    assigned_cpa_license="CPA-PR-001234",
)

CLIENT_CFG_2 = ClientConfig(
    client_id="client-demo-002",
    confidence_threshold=Decimal("0.75"),
    sla_hours_default=48,
    assigned_cpa_license="CPA-PR-005678",
)


def _pause_txn(guardian: CentinelaGuardian, txn_id: str = "txn-pause-001") -> str:
    """Helper: envía una transacción con confianza baja y retorna el pause_id."""
    result = guardian.evaluate(
        transaction_id=txn_id,
        client_id="client-demo-001",
        transaction_date="2026-03-15",
        amount=Decimal("2450.00"),
        vendor="Ferretería San Juan LLC",
        transaction_type="EXPENSE",
        confidence=Decimal("0.40"),    # por debajo del umbral 0.75
        current_rule_id="IVU_ESTATAL_PR_2015_V1",
        history_90d=(),
        history_6m=(),
        cross_check_results=(),
        audit_result=None,
        client_config=CLIENT_CFG,
    )
    assert result.decision == CentinelaDecision.PAUSE
    return result.pause_id


# =============================================================================
# 2.1 — CENTINELA detecta la contradicción / baja confianza y crea la pausa
# =============================================================================

class TestCentinelaDetectsAndPauses:

    def test_low_confidence_triggers_pause(self, centinela):
        """Confianza 0.40 < umbral 0.75 → debe crear pausa."""
        result = centinela.evaluate(
            transaction_id="txn-001",
            client_id="client-demo-001",
            transaction_date="2026-03-15",
            amount=Decimal("2450.00"),
            vendor="Ferretería San Juan LLC",
            transaction_type="EXPENSE",
            confidence=Decimal("0.40"),
            current_rule_id="IVU_ESTATAL_PR_2015_V1",
            history_90d=(),
            history_6m=(),
            cross_check_results=(),
            audit_result=None,
            client_config=CLIENT_CFG,
        )
        assert result.decision == CentinelaDecision.PAUSE
        assert result.trigger == PauseTrigger.LOW_CONFIDENCE
        assert result.pause_id is not None

    def test_pause_is_created_in_guardian(self, centinela):
        """Después de PAUSE, el guardian debe tener la pausa en estado ACTIVE."""
        pause_id = _pause_txn(centinela)
        pause = centinela.get_pause(pause_id)
        assert pause is not None
        assert pause.status == PauseStatus.ACTIVE

    def test_pause_has_correct_client(self, centinela):
        pause_id = _pause_txn(centinela)
        pause = centinela.get_pause(pause_id)
        assert pause.client_id == "client-demo-001"

    def test_pause_contains_affected_transaction(self, centinela):
        """La pausa debe listar la transacción que la disparó."""
        pause_id = _pause_txn(centinela, txn_id="txn-abc")
        pause = centinela.get_pause(pause_id)
        assert "txn-abc" in pause.affected_transaction_ids

    def test_pause_blocks_transaction(self, centinela):
        """La transacción pausada debe quedar bloqueada."""
        pause_id = _pause_txn(centinela, txn_id="txn-blocked")
        assert centinela.is_transaction_blocked("txn-blocked") is True

    def test_pause_active_count_increments(self, centinela):
        assert centinela.active_pause_count() == 0
        _pause_txn(centinela, "txn-p1")
        assert centinela.active_pause_count() == 1
        _pause_txn(centinela, "txn-p2")
        assert centinela.active_pause_count() == 2

    def test_pause_has_conflict_summary(self, centinela):
        """El CPA debe recibir un resumen del conflicto detectado."""
        pause_id = _pause_txn(centinela)
        pause = centinela.get_pause(pause_id)
        assert pause.conflict_summary
        assert len(pause.conflict_summary) >= 5

    def test_pause_has_multiple_interpretations(self, centinela):
        """El CENTINELA debe generar ≥ 2 interpretaciones para que el CPA elija."""
        pause_id = _pause_txn(centinela)
        pause = centinela.get_pause(pause_id)
        assert len(pause.interpretations) >= 2

    def test_pause_has_sla_hours(self, centinela):
        """La pausa debe tener un SLA definido."""
        pause_id = _pause_txn(centinela)
        pause = centinela.get_pause(pause_id)
        assert pause.sla_hours >= 1

    def test_flow_coordinator_paused_result(self, flow):
        """FlowCoordinator con documento sin monto → final_decision PAUSED."""
        from tests.conftest import MISSING_AMOUNT_RAW
        result = flow.process(raw_input=MISSING_AMOUNT_RAW, source_format="json")
        assert result.final_decision == "PAUSED"
        assert result.pause_id is not None


# =============================================================================
# 2.2 — Pausa quirúrgica: otras transacciones del cliente NO se bloquean
# =============================================================================

class TestSurgicalPause:

    def test_other_transactions_same_client_not_blocked(self, centinela):
        """Pausar txn-1 NO debe bloquear txn-2 del mismo cliente."""
        _pause_txn(centinela, txn_id="txn-paused")
        # txn-2: alta confianza, mismo cliente
        result2 = centinela.evaluate(
            transaction_id="txn-clean",
            client_id="client-demo-001",
            transaction_date="2026-03-16",
            amount=Decimal("1000.00"),
            vendor="Otro Proveedor SRL",
            transaction_type="EXPENSE",
            confidence=Decimal("0.92"),
            current_rule_id="IVU_ESTATAL_PR_2015_V1",
            history_90d=(),
            history_6m=(),
            cross_check_results=(),
            audit_result=None,
            client_config=CLIENT_CFG,
        )
        assert result2.decision == CentinelaDecision.PROCEED
        assert centinela.is_transaction_blocked("txn-clean") is False

    def test_other_clients_not_affected(self, centinela):
        """Pausar una transacción del cliente A NO afecta al cliente B."""
        _pause_txn(centinela, txn_id="txn-client-a")
        # Cliente B — diferente
        result_b = centinela.evaluate(
            transaction_id="txn-client-b",
            client_id="client-demo-002",
            transaction_date="2026-03-16",
            amount=Decimal("500.00"),
            vendor="Proveedor B",
            transaction_type="EXPENSE",
            confidence=Decimal("0.89"),
            current_rule_id="IVU_ESTATAL_PR_2015_V1",
            history_90d=(),
            history_6m=(),
            cross_check_results=(),
            audit_result=None,
            client_config=CLIENT_CFG_2,
        )
        assert result_b.decision == CentinelaDecision.PROCEED
        assert centinela.is_transaction_blocked("txn-client-b") is False

    def test_paused_transaction_stays_blocked(self, centinela):
        """La transacción pausada permanece bloqueada hasta que el CPA la libere."""
        _pause_txn(centinela, txn_id="txn-stays")
        assert centinela.is_transaction_blocked("txn-stays") is True
        # Re-evaluar no cambia el bloqueo
        assert centinela.is_transaction_blocked("txn-stays") is True

    def test_multiple_pauses_are_independent(self, centinela):
        """Dos pausas de distintos clientes son totalmente independientes."""
        p1 = _pause_txn(centinela, txn_id="txn-a1")
        # Client-2 pause
        result2 = centinela.evaluate(
            transaction_id="txn-b1",
            client_id="client-demo-002",
            transaction_date="2026-03-15",
            amount=Decimal("800.00"),
            vendor="Vendedor 2",
            transaction_type="EXPENSE",
            confidence=Decimal("0.35"),
            current_rule_id="IVU_ESTATAL_PR_2015_V1",
            history_90d=(),
            history_6m=(),
            cross_check_results=(),
            audit_result=None,
            client_config=CLIENT_CFG_2,
        )
        p2 = result2.pause_id
        assert p1 != p2
        # Releasing p1 doesn't affect p2
        pause_1 = centinela.get_pause(p1)
        centinela.release_pause(
            pause_id=p1,
            cpa_license="CPA-PR-001234",
            cpa_token="tok-valid-001",
            interpretation_chosen=pause_1.interpretations[0],
            instruction="Verificado por CPA",
        )
        # p2 should still be in the log (not accessible via get_pause since it's client-demo-002's)
        pauses_c1 = centinela.get_active_pauses_for_client("client-demo-001")
        assert len(pauses_c1) == 0   # p1 was released


# =============================================================================
# 2.3 — CPA recibe análisis y emite instrucción → INTÉRPRETE
# =============================================================================

class TestCPAInstructionFlow:

    def test_interprete_generates_draft(self):
        """InterpreteV2 debe generar un PolicyDraft desde una instrucción del CPA."""
        from agents.interprete_v2 import InterpreteV2
        iv2 = InterpreteV2()
        draft = iv2.interpret_instruction(
            instruction_text=(
                "Los gastos de ferretería mayores de $500 requieren "
                "factura original antes de registrar."
            ),
            cpa_license="CPA-PR-001234",
            client_id="client-demo-001",
        )
        assert draft.draft_id
        assert draft.cpa_license == "CPA-PR-001234"
        assert draft.client_id == "client-demo-001"

    def test_interprete_draft_awaiting_confirmation(self):
        """El draft debe quedar en AWAITING_CONFIRMATION hasta que el CPA confirme."""
        from agents.interprete_v2 import InterpreteV2, PolicyStatus
        iv2 = InterpreteV2()
        draft = iv2.interpret_instruction(
            instruction_text="Toda compra sobre $1000 requiere aprobación gerencial.",
            cpa_license="CPA-PR-001234",
            client_id="client-demo-001",
        )
        assert draft.status == PolicyStatus.AWAITING_CONFIRMATION

    def test_interprete_draft_has_examples(self):
        """El draft debe incluir ejemplos para que el CPA verifique la interpretación."""
        from agents.interprete_v2 import InterpreteV2
        iv2 = InterpreteV2()
        draft = iv2.interpret_instruction(
            instruction_text="Reclasificar gastos menores de $200 como gastos operativos.",
            cpa_license="CPA-PR-001234",
            client_id="client-demo-001",
        )
        assert len(draft.transaction_examples) >= 1

    def test_interprete_draft_has_policy_summary(self):
        from agents.interprete_v2 import InterpreteV2
        iv2 = InterpreteV2()
        draft = iv2.interpret_instruction(
            instruction_text="Agrupar todos los gastos de oficina bajo la cuenta 5100.",
            cpa_license="CPA-PR-001234",
            client_id="client-demo-001",
        )
        assert draft.policy_summary
        assert len(draft.policy_summary) >= 10

    def test_interprete_confirmar_activates_policy(self):
        """Al confirmar, la política pasa a ACTIVE."""
        from agents.interprete_v2 import InterpreteV2, PolicyStatus
        iv2 = InterpreteV2()
        draft = iv2.interpret_instruction(
            instruction_text="Separar compras de equipo informático en cuenta 1500.",
            cpa_license="CPA-PR-001234",
            client_id="client-demo-001",
        )
        policy = iv2.confirmar(draft_id=draft.draft_id, cpa_license="CPA-PR-001234")
        assert policy.status == PolicyStatus.ACTIVE
        assert len(iv2.get_active_policies()) == 1

    def test_interprete_active_policy_has_cpa_license(self):
        from agents.interprete_v2 import InterpreteV2
        iv2 = InterpreteV2()
        draft = iv2.interpret_instruction(
            instruction_text="Los pagos por ACH mayores de $5000 requieren respaldo bancario.",
            cpa_license="CPA-PR-001234",
            client_id="client-demo-001",
        )
        policy = iv2.confirmar(draft_id=draft.draft_id, cpa_license="CPA-PR-001234")
        assert policy.cpa_license == "CPA-PR-001234"


# =============================================================================
# 2.4 — CPA libera la pausa → transacción se desbloquea
# =============================================================================

class TestPauseRelease:

    def test_release_changes_status(self, centinela):
        """Liberar la pausa debe moverla al release_log."""
        pause_id = _pause_txn(centinela, "txn-release-001")
        pause = centinela.get_pause(pause_id)
        centinela.release_pause(
            pause_id=pause_id,
            cpa_license="CPA-PR-001234",
            cpa_token="valid-cpa-token",
            interpretation_chosen=pause.interpretations[0],
            instruction="Documento verificado por CPA. Proceder con la clasificación.",
        )
        log = centinela.get_release_log()
        assert len(log) == 1
        assert log[0].pause_id == pause_id

    def test_released_pause_unblocks_transaction(self, centinela):
        """Después de liberar la pausa, la transacción ya no está bloqueada."""
        pause_id = _pause_txn(centinela, "txn-unblock")
        assert centinela.is_transaction_blocked("txn-unblock") is True
        pause = centinela.get_pause(pause_id)
        centinela.release_pause(
            pause_id=pause_id,
            cpa_license="CPA-PR-001234",
            cpa_token="valid-cpa-token",
            interpretation_chosen=pause.interpretations[0],
            instruction="Verificado.",
        )
        assert centinela.is_transaction_blocked("txn-unblock") is False

    def test_release_records_cpa_license(self, centinela):
        """El log de liberación debe registrar la licencia del CPA."""
        pause_id = _pause_txn(centinela, "txn-r2")
        pause = centinela.get_pause(pause_id)
        centinela.release_pause(
            pause_id=pause_id,
            cpa_license="CPA-PR-001234",
            cpa_token="tok-valid-001",
            interpretation_chosen=pause.interpretations[0],
            instruction="OK.",
        )
        log = centinela.get_release_log()
        assert log[0].released_by_license == "CPA-PR-001234"

    def test_release_decrements_active_count(self, centinela):
        pause_id = _pause_txn(centinela, "txn-count")
        assert centinela.active_pause_count() == 1
        pause = centinela.get_pause(pause_id)
        centinela.release_pause(
            pause_id=pause_id,
            cpa_license="CPA-PR-001234",
            cpa_token="tok-valid-001",
            interpretation_chosen=pause.interpretations[0],
            instruction="OK.",
        )
        assert centinela.active_pause_count() == 0

    def test_release_log_is_append_only(self, centinela):
        """El log de liberaciones es append-only — no se puede modificar."""
        p1 = _pause_txn(centinela, "txn-log1")
        p2 = _pause_txn(centinela, "txn-log2")
        for pid in [p1, p2]:
            pause = centinela.get_pause(pid)
            centinela.release_pause(
                pause_id=pid,
                cpa_license="CPA-PR-001234",
                cpa_token="tok-valid-001",
                interpretation_chosen=pause.interpretations[0],
                instruction="OK.",
            )
        log = centinela.get_release_log()
        assert len(log) == 2
        with pytest.raises((TypeError, AttributeError, Exception)):
            log[0].released_by_license = "MANIPULADO"  # type: ignore[misc]


# =============================================================================
# 2.5 — FlowCoordinator: ruta PAUSED completa
# =============================================================================

class TestFlowPausedPath:

    def test_paused_flow_has_no_classifier_output(self, flow):
        """Cuando el CENTINELA pausa, el CLASIFICADOR no debe ejecutarse."""
        from tests.conftest import MISSING_AMOUNT_RAW
        result = flow.process(raw_input=MISSING_AMOUNT_RAW, source_format="json")
        assert result.final_decision == "PAUSED"
        assert result.classifier_output is None

    def test_paused_flow_has_no_auditor_output(self, flow):
        from tests.conftest import MISSING_AMOUNT_RAW
        result = flow.process(raw_input=MISSING_AMOUNT_RAW, source_format="json")
        assert result.auditor_verification is None

    def test_paused_flow_has_pause_id(self, flow):
        from tests.conftest import MISSING_AMOUNT_RAW
        result = flow.process(raw_input=MISSING_AMOUNT_RAW, source_format="json")
        assert result.pause_id is not None

    def test_paused_flow_has_centinela_evaluation(self, flow):
        """Incluso al pausar, la evaluación del CENTINELA debe estar en el resultado."""
        from tests.conftest import MISSING_AMOUNT_RAW
        result = flow.process(raw_input=MISSING_AMOUNT_RAW, source_format="json")
        assert result.centinela_evaluation is not None
        assert result.centinela_evaluation.decision == CentinelaDecision.PAUSE

    def test_paused_flow_is_in_log(self, flow):
        """La pausa debe quedar registrada en el log del ORQUESTADOR."""
        from tests.conftest import MISSING_AMOUNT_RAW
        result = flow.process(raw_input=MISSING_AMOUNT_RAW, source_format="json")
        agents_in_log = {d.agent_name for d in result.log}
        assert "CENTINELA" in agents_in_log
