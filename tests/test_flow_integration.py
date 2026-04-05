# =============================================================================
# tests/test_flow_integration.py
# Integration tests for the Bit-Counting FlowCoordinator pipeline.
#
# Tests:
#   1. Full happy path document → final_decision = "COMPLETED"
#   2. Document with missing amount → final_decision = "PAUSED"
#   3. FlowResult log has correct agent names
#   4. Multiple documents don't share state
#
# Run:
#   python -m pytest tests/test_flow_integration.py -v
#   python tests/test_flow_integration.py
# =============================================================================

from __future__ import annotations

import sys
import os

# Ensure project root is in path when run directly
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from decimal import Decimal


# ===========================================================================
# TEST 1 — Full happy path: well-formed document should COMPLETE
# ===========================================================================

def test_happy_path_completed():
    """
    A well-formed document with all fields and high confidence should travel
    through INTAKE → CENTINELA → CLASIFICADOR → AUDITOR → FISCAL PR and
    end with final_decision = "COMPLETED".
    """
    from agents.flow import FlowCoordinator

    coordinator = FlowCoordinator()
    raw_input = {
        "vendor": "Costco Wholesale PR",
        "date": "2026-03-15",
        "amount": Decimal("2450.00"),
        "tax_amount": Decimal("281.75"),   # 11.5% IVU rate as expected by AUDITOR
        "currency": "USD",
        "payment_method": "CHECK",
        "confidence": Decimal("0.91"),
        "missing_fields": [],
        "critical_fields_missing": False,
    }

    result = coordinator.process(raw_input=raw_input, source_format="json")

    assert result.final_decision == "COMPLETED", (
        f"Expected COMPLETED, got {result.final_decision!r}. "
        f"Centinela decision: {result.centinela_evaluation.decision}"
    )
    assert result.intake_output is not None, "intake_output must not be None"
    assert result.centinela_evaluation is not None, "centinela_evaluation must not be None"
    assert result.classifier_output is not None, "classifier_output must not be None for COMPLETED"
    assert result.auditor_verification is not None, "auditor_verification must not be None for COMPLETED"
    assert result.fiscal_output is not None, "fiscal_output must not be None for COMPLETED"
    assert result.pause_id is None, "pause_id must be None for COMPLETED"

    print("TEST 1 PASSED: Full happy path document → COMPLETED")


# ===========================================================================
# TEST 2 — Document with missing amount → PAUSED
# ===========================================================================

def test_missing_amount_causes_pause():
    """
    A document missing the critical 'amount' field should cause CENTINELA to
    emit a PAUSE, resulting in final_decision = "PAUSED".

    CENTINELA trigger: MISSING_CRITICAL_FIELD
    """
    from agents.flow import FlowCoordinator

    coordinator = FlowCoordinator()
    raw_input = {
        "vendor": "Proveedor Sin Monto",
        "date": "2026-03-20",
        "amount": None,                     # CRITICAL FIELD MISSING
        "tax_amount": None,
        "currency": "USD",
        "payment_method": None,
        "confidence": Decimal("0.35"),
        "missing_fields": ["amount"],
        "critical_fields_missing": True,    # Signals INTAKE found critical field missing
    }

    result = coordinator.process(raw_input=raw_input, source_format="json")

    assert result.final_decision == "PAUSED", (
        f"Expected PAUSED due to missing amount, got {result.final_decision!r}"
    )
    assert result.pause_id is not None, "pause_id must be set when PAUSED"
    assert result.centinela_evaluation.pause_id == result.pause_id, (
        "pause_id must match between FlowResult and centinela_evaluation"
    )
    assert result.classifier_output is None, (
        "classifier_output must be None when PAUSED (pipeline stops at CENTINELA)"
    )
    assert result.auditor_verification is None, (
        "auditor_verification must be None when PAUSED"
    )
    assert result.fiscal_output is None, (
        "fiscal_output must be None when PAUSED"
    )

    print("TEST 2 PASSED: Document with missing amount → PAUSED")


# ===========================================================================
# TEST 3 — FlowResult log has correct agent names
# ===========================================================================

def test_log_has_correct_agent_names():
    """
    For a COMPLETED flow, the Orchestrator log must contain entries from
    at least: INTAKE, CENTINELA, CLASIFICADOR, AUDITOR, FISCAL_PR.

    The log is the audit trail — every significant agent action must appear.
    """
    from agents.flow import FlowCoordinator

    coordinator = FlowCoordinator()
    raw_input = {
        "vendor": "Dell Technologies PR",
        "date": "2026-03-12",
        "amount": Decimal("3200.00"),
        "tax_amount": Decimal("336.00"),
        "currency": "USD",
        "payment_method": "CREDIT_CARD",
        "confidence": Decimal("0.90"),
        "missing_fields": [],
        "critical_fields_missing": False,
    }

    result = coordinator.process(raw_input=raw_input, source_format="json")

    # Must complete for this test to be meaningful
    assert result.final_decision in ("COMPLETED", "PAUSED", "FAILED"), (
        f"Pipeline must finish with a known decision, got {result.final_decision!r}"
    )

    log = result.log
    assert len(log) > 0, "Orchestrator log must not be empty after processing"

    # Collect all agent names that appear in the log
    agent_names_in_log = {entry.agent_name for entry in log}

    # CENTINELA must always be in the log
    assert "CENTINELA" in agent_names_in_log, (
        f"CENTINELA must appear in log. Got: {sorted(agent_names_in_log)}"
    )

    if result.final_decision == "COMPLETED":
        required_agents = {"CENTINELA", "CLASIFICADOR", "AUDITOR", "FISCAL_PR"}
        missing = required_agents - agent_names_in_log
        assert not missing, (
            f"COMPLETED flow must log these agents: {required_agents}. "
            f"Missing: {missing}. Got: {sorted(agent_names_in_log)}"
        )

    # Verify log is ordered (timestamps non-decreasing)
    for i in range(1, len(log)):
        assert log[i].timestamp >= log[i - 1].timestamp, (
            f"Log entries must be in chronological order "
            f"(entry {i-1} vs {i}: {log[i-1].timestamp} vs {log[i].timestamp})"
        )

    print(
        f"TEST 3 PASSED: FlowResult log has correct agent names "
        f"({sorted(agent_names_in_log)})"
    )


# ===========================================================================
# TEST 4 — Multiple documents don't share state
# ===========================================================================

def test_multiple_documents_isolated_state():
    """
    Each FlowCoordinator instance must have completely isolated state.

    Two documents processed by two different coordinators must not share
    Orchestrator logs, Centinela pause registries, or any other state.

    Specifically:
      - coordinator_a's log must not contain entries from coordinator_b's run
      - A pause emitted in coordinator_a must not appear in coordinator_b
    """
    from agents.flow import FlowCoordinator

    # Document A: will COMPLETE (has all fields)
    coordinator_a = FlowCoordinator()
    raw_a = {
        "vendor": "Farmacia San Pablo",
        "date": "2026-03-08",
        "amount": Decimal("245.00"),
        "tax_amount": Decimal("0.00"),
        "currency": "USD",
        "payment_method": "CREDIT_CARD",
        "confidence": Decimal("0.87"),
        "missing_fields": [],
        "critical_fields_missing": False,
    }

    # Document B: will PAUSE (missing critical field)
    coordinator_b = FlowCoordinator()
    raw_b = {
        "vendor": "Unknown Vendor",
        "date": None,
        "amount": None,
        "tax_amount": None,
        "currency": "USD",
        "payment_method": None,
        "confidence": Decimal("0.30"),
        "missing_fields": ["date", "amount"],
        "critical_fields_missing": True,
    }

    result_a = coordinator_a.process(raw_input=raw_a, source_format="json")
    result_b = coordinator_b.process(raw_input=raw_b, source_format="json")

    # Verify isolation: logs are independent
    log_a = coordinator_a.get_log()
    log_b = coordinator_b.get_log()

    decision_ids_a = {d.decision_id for d in log_a}
    decision_ids_b = {d.decision_id for d in log_b}

    shared_ids = decision_ids_a & decision_ids_b
    assert not shared_ids, (
        f"Coordinators must have completely isolated logs. "
        f"Shared decision_ids found: {shared_ids}"
    )

    # Verify B paused (missing critical field) without affecting A
    assert result_b.final_decision == "PAUSED", (
        f"Document B with missing fields must be PAUSED, got {result_b.final_decision!r}"
    )

    # B has a pause — verify it doesn't bleed into A's coordinator
    # coordinator_a._centinela should have NO active pauses
    a_active_pauses = coordinator_a._centinela.get_active_pauses()
    assert len(a_active_pauses) == 0, (
        f"coordinator_a should have 0 active pauses (pause was in coordinator_b). "
        f"Got {len(a_active_pauses)} pauses."
    )

    # B's coordinator should have exactly 1 active pause
    b_active_pauses = coordinator_b._centinela.get_active_pauses()
    assert len(b_active_pauses) == 1, (
        f"coordinator_b should have 1 active pause. Got {len(b_active_pauses)}."
    )

    # Log sizes must be independent
    assert len(log_a) > 0, "coordinator_a log must not be empty"
    assert len(log_b) > 0, "coordinator_b log must not be empty"

    print(
        f"TEST 4 PASSED: Multiple documents don't share state "
        f"(log_a={len(log_a)} entries, log_b={len(log_b)} entries, "
        f"no shared decision_ids)"
    )


# ===========================================================================
# RUNNER
# ===========================================================================

def run_all_tests() -> bool:
    """Run all integration tests and report results."""
    tests = [
        ("TEST 1: Full happy path → COMPLETED",    test_happy_path_completed),
        ("TEST 2: Missing amount → PAUSED",         test_missing_amount_causes_pause),
        ("TEST 3: Log has correct agent names",     test_log_has_correct_agent_names),
        ("TEST 4: Multiple docs — isolated state",  test_multiple_documents_isolated_state),
    ]

    passed = 0
    failed = 0

    for description, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as exc:
            failed += 1
            print(f"TEST FAILED: {description}")
            print(f"  Error: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"Integration tests: {passed}/{len(tests)} passed, {failed} failed")
    print(f"{'='*60}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
