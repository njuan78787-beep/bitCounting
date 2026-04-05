# =============================================================================
# tests/test_calibration.py
# Tests for the Bit-Counting calibration system.
#
# Test cases:
#   1. Synthetic twin dataset has exactly 20 transactions
#   2. CalibrationResult has accuracy_rate between 0 and 1
#   3. ErrorCard creation works (all required fields present)
#   4. is_ready_for_production logic correct (>= 0.85 → True)
# =============================================================================

from __future__ import annotations

import sys
import os
from decimal import Decimal
from typing import Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest

from calibration.synthetic_twin import (
    CALIBRATION_DATASET,
    CalibrationResult,
    SyntheticCalibrationTwin,
)
from calibration.error_cards import ErrorCard, ErrorCardSystem


# ---------------------------------------------------------------------------
# TEST 1: Dataset has exactly 20 transactions
# ---------------------------------------------------------------------------

def test_calibration_dataset_has_20_transactions():
    """CALIBRATION_DATASET must have exactly 20 synthetic transactions."""
    assert len(CALIBRATION_DATASET) == 20


def test_calibration_dataset_all_have_required_fields():
    """Each calibration transaction must have all required fields."""
    required_fields = {
        "id", "vendor", "amount", "type",
        "correct_account", "correct_account_name",
        "is_capital", "note", "category",
    }
    for txn in CALIBRATION_DATASET:
        missing = required_fields - set(txn.keys())
        assert not missing, f"Transaction {txn.get('id')} missing fields: {missing}"


def test_calibration_dataset_ids_are_unique():
    """All calibration transaction IDs must be unique."""
    ids = [txn["id"] for txn in CALIBRATION_DATASET]
    assert len(ids) == len(set(ids)), "Duplicate IDs found in CALIBRATION_DATASET"


def test_calibration_dataset_amounts_are_positive():
    """All transaction amounts must be positive."""
    for txn in CALIBRATION_DATASET:
        amount = Decimal(str(txn["amount"]))
        assert amount > Decimal("0.00"), f"Non-positive amount in {txn['id']}: {amount}"


def test_calibration_dataset_covers_all_categories():
    """Dataset must include a variety of transaction categories."""
    categories = {txn["category"] for txn in CALIBRATION_DATASET}
    # Minimum expected categories for PR accounting calibration
    required_categories = {
        "general_expense", "capital_asset", "tax_payment", "payroll",
        "utilities", "cogs",
    }
    missing = required_categories - categories
    assert not missing, f"Missing categories in dataset: {missing}"


# ---------------------------------------------------------------------------
# TEST 2: CalibrationResult has accuracy_rate between 0 and 1
# ---------------------------------------------------------------------------

class _PerfectClasificador:
    """Stub agent that always returns the correct answer for calibration."""
    def classify(self, txn: dict) -> dict:
        return {
            "account_code": txn["correct_account"],
            "confidence": Decimal("0.95"),
            "is_capital": txn["is_capital"],
        }


class _WorstClasificador:
    """Stub agent that always returns the wrong answer (account 9999)."""
    def classify(self, txn: dict) -> dict:
        return {
            "account_code": "9999",
            "confidence": Decimal("0.10"),
            "is_capital": False,
        }


class _HalfCorrectClasificador:
    """Stub agent that gets even-indexed transactions correct, odd ones wrong."""
    def __init__(self):
        self._index = 0

    def classify(self, txn: dict) -> dict:
        is_correct = (self._index % 2 == 0)
        self._index += 1
        return {
            "account_code": txn["correct_account"] if is_correct else "9999",
            "confidence": Decimal("0.80") if is_correct else Decimal("0.30"),
            "is_capital": txn["is_capital"] if is_correct else False,
        }


def test_calibration_result_accuracy_rate_in_bounds():
    """CalibrationResult.accuracy_rate must be in [0, 1] range."""
    twin = SyntheticCalibrationTwin()

    # Test with worst agent
    result = twin.run_calibration(_WorstClasificador())
    assert Decimal("0.00") <= result.accuracy_rate <= Decimal("1.00")

    # Test with perfect agent
    result_perfect = twin.run_calibration(_PerfectClasificador())
    assert Decimal("0.00") <= result_perfect.accuracy_rate <= Decimal("1.00")


def test_calibration_result_perfect_agent_high_accuracy():
    """A perfect agent must achieve accuracy_rate = 1.0."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_PerfectClasificador())

    assert result.total_transactions == 20
    assert result.correct_classifications == 20
    assert result.accuracy_rate == Decimal("1.0000")


def test_calibration_result_worst_agent_zero_accuracy():
    """An agent that always returns account 9999 must have zero accuracy."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_WorstClasificador())

    assert result.total_transactions == 20
    assert result.correct_classifications == 0
    assert result.accuracy_rate == Decimal("0.0000")
    assert len(result.wrong_classifications) == 20


def test_calibration_result_is_frozen():
    """CalibrationResult must be immutable (frozen Pydantic model)."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_PerfectClasificador())

    with pytest.raises(Exception):  # ValidationError or TypeError for frozen model
        result.accuracy_rate = Decimal("0.50")  # type: ignore


def test_calibration_result_has_required_fields():
    """CalibrationResult must contain all required fields."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_HalfCorrectClasificador())

    assert hasattr(result, "total_transactions")
    assert hasattr(result, "correct_classifications")
    assert hasattr(result, "accuracy_rate")
    assert hasattr(result, "errors_by_category")
    assert hasattr(result, "recommended_confidence_threshold")
    assert hasattr(result, "calibration_timestamp")
    assert hasattr(result, "is_ready_for_production")


def test_calibration_result_wrong_classifications_match_errors():
    """Number of wrong classifications must equal total - correct."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_HalfCorrectClasificador())

    expected_wrong = result.total_transactions - result.correct_classifications
    assert len(result.wrong_classifications) == expected_wrong


def test_calibration_result_recommended_threshold_in_bounds():
    """recommended_confidence_threshold must be between 0 and 1."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_PerfectClasificador())
    assert Decimal("0.00") <= result.recommended_confidence_threshold <= Decimal("1.00")


# ---------------------------------------------------------------------------
# TEST 3: ErrorCard creation works
# ---------------------------------------------------------------------------

def test_error_card_creation_returns_frozen_card():
    """ErrorCard must be creatable with all required fields and be frozen."""
    system = ErrorCardSystem()
    card = system.create_error_card(
        agent_decision_id="decision-test-001",
        transaction_id="txn-test-001",
        expected_account_code="1500",
        actual_account_code="5900",
        cpa_correction="This is a capital asset purchase above $2,500. Must capitalize.",
        cpa_license="CPA-PR-12345",
        vendor="Dell Technologies",
        amount=Decimal("3200.00"),
        transaction_type="purchase",
        rule_ref="PR-CAP-THRESHOLD-2500",
    )

    assert isinstance(card, ErrorCard)
    assert card.expected_account_code == "1500"
    assert card.actual_account_code == "5900"
    assert card.fiscal_impact_estimate == Decimal("3200.00")
    assert card.is_anonymized is False
    assert card.contributed_to_pool is False


def test_error_card_is_frozen():
    """ErrorCard instances must be immutable."""
    system = ErrorCardSystem()
    card = system.create_error_card(
        agent_decision_id="decision-test-002",
        transaction_id="txn-test-002",
        expected_account_code="2410",
        actual_account_code="2400",
        cpa_correction="This is an IVU payment, not a general tax payment. Use account 2410.",
    )

    with pytest.raises(Exception):  # ValidationError or TypeError
        card.expected_account_code = "9999"  # type: ignore


def test_error_card_what_went_wrong_contains_vendor():
    """what_went_wrong must mention the vendor."""
    system = ErrorCardSystem()
    card = system.create_error_card(
        agent_decision_id="decision-test-003",
        transaction_id="txn-test-003",
        expected_account_code="5100",
        actual_account_code="5400",
        cpa_correction="This is a payroll transaction, not professional fees.",
        vendor="Nomina Empleados",
        amount=Decimal("15000.00"),
    )

    assert "Nomina Empleados" in card.what_went_wrong


def test_error_card_why_its_wrong_contains_rule_ref():
    """why_its_wrong must reference the rule that was violated."""
    system = ErrorCardSystem()
    card = system.create_error_card(
        agent_decision_id="decision-test-004",
        transaction_id="txn-test-004",
        expected_account_code="5200",
        actual_account_code="1400",
        cpa_correction="Rent is never capitalized regardless of amount.",
        rule_ref="GAAP-RENT-CANNOT-CAPITALIZE",
    )

    assert "GAAP-RENT-CANNOT-CAPITALIZE" in card.why_its_wrong


def test_error_card_system_stores_and_retrieves():
    """ErrorCards must be retrievable from the system after creation."""
    system = ErrorCardSystem()
    card = system.create_error_card(
        agent_decision_id="decision-test-005",
        transaction_id="txn-test-005",
        expected_account_code="5300",
        actual_account_code="5900",
        cpa_correction="Utility expenses belong to account 5300, not general expenses.",
        vendor="AEE",
        amount=Decimal("850.00"),
    )

    retrieved = system.get_card(card.card_id)
    assert retrieved is not None
    assert retrieved.card_id == card.card_id
    assert retrieved.transaction_id == "txn-test-005"


def test_error_card_get_cards_for_learning_anonymizes():
    """get_cards_for_learning must return anonymized cards."""
    system = ErrorCardSystem()
    system.create_error_card(
        agent_decision_id="decision-test-006",
        transaction_id="txn-001-client-abc",
        expected_account_code="2100",
        actual_account_code="7100",
        cpa_correction="Principal payment reduces liability; interest goes to 7100.",
        cpa_license="CPA-PR-99999",
        vendor="Banco Popular PR",
        amount=Decimal("5000.00"),
    )

    pool = system.get_cards_for_learning()
    assert len(pool) >= 1

    for anon_card in pool:
        assert anon_card.is_anonymized is True
        assert anon_card.cpa_license is None
        assert "txn-001-client-abc" not in anon_card.transaction_id


def test_error_card_zero_amount_defaults_to_zero():
    """ErrorCard created without amount must have fiscal_impact = $0.00."""
    system = ErrorCardSystem()
    card = system.create_error_card(
        agent_decision_id="decision-test-007",
        transaction_id="txn-test-007",
        expected_account_code="5600",
        actual_account_code="5900",
        cpa_correction="Advertising costs belong to account 5600.",
    )

    assert card.fiscal_impact_estimate == Decimal("0.00")


# ---------------------------------------------------------------------------
# TEST 4: is_ready_for_production logic correct (>= 0.85 threshold)
# ---------------------------------------------------------------------------

def test_is_ready_for_production_true_when_high_accuracy():
    """is_ready_for_production must be True if accuracy_rate >= 0.85."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_PerfectClasificador())

    # 100% accuracy → ready
    assert result.accuracy_rate >= Decimal("0.85")
    assert result.is_ready_for_production is True


def test_is_ready_for_production_false_when_low_accuracy():
    """is_ready_for_production must be False if accuracy_rate < 0.85."""
    twin = SyntheticCalibrationTwin()
    result = twin.run_calibration(_WorstClasificador())

    # 0% accuracy → not ready
    assert result.accuracy_rate < Decimal("0.85")
    assert result.is_ready_for_production is False


def test_is_ready_for_production_boundary_at_85_percent():
    """
    Test the exact boundary: 17/20 correct = 0.85 → True.
    16/20 correct = 0.80 → False.
    """
    # We'll use a custom agent that gets exactly 17 correct
    class _SeventeenCorrect:
        def __init__(self):
            self._index = 0

        def classify(self, txn: dict) -> dict:
            # First 17 calls correct, last 3 wrong
            is_correct = self._index < 17
            self._index += 1
            return {
                "account_code": txn["correct_account"] if is_correct else "9999",
                "confidence": Decimal("0.80"),
                "is_capital": txn["is_capital"],
            }

    class _SixteenCorrect:
        def __init__(self):
            self._index = 0

        def classify(self, txn: dict) -> dict:
            is_correct = self._index < 16
            self._index += 1
            return {
                "account_code": txn["correct_account"] if is_correct else "9999",
                "confidence": Decimal("0.80"),
                "is_capital": txn["is_capital"],
            }

    twin = SyntheticCalibrationTwin()

    result_17 = twin.run_calibration(_SeventeenCorrect())
    assert result_17.correct_classifications == 17
    assert result_17.accuracy_rate == Decimal("0.8500")
    assert result_17.is_ready_for_production is True

    result_16 = twin.run_calibration(_SixteenCorrect())
    assert result_16.correct_classifications == 16
    assert result_16.accuracy_rate == Decimal("0.8000")
    assert result_16.is_ready_for_production is False


def test_calibration_twin_dataset_size_property():
    """SyntheticCalibrationTwin.dataset_size must return 20."""
    twin = SyntheticCalibrationTwin()
    assert twin.dataset_size == 20
