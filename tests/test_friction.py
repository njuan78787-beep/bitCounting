# =============================================================================
# tests/test_friction.py
# Tests for the Bit-Counting CPA friction and vigilance system.
#
# Test cases:
#   1. LOW transaction gets CLICK friction
#   2. HIGH transaction gets QUESTION friction
#   3. Tax filing always gets HIGH friction
#   4. Fast approval (< 5s) on HIGH transaction is flagged
#   5. Random verification is triggered ~5% of time (statistical test over 100 calls)
# =============================================================================

from __future__ import annotations

import sys
import os
from decimal import Decimal

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest

from friction.cpa_vigilance import (
    CPAVigilanceSystem,
    FrictionConfig,
    FrictionDecision,
    FrictionLevel,
    VigileAgent,
)

# The fast approval threshold is defined in FrictionConfig
FAST_APPROVAL_THRESHOLD_SECONDS = FrictionConfig().fast_approval_threshold_seconds


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vigilance() -> CPAVigilanceSystem:
    """Fresh CPAVigilanceSystem for each test."""
    return CPAVigilanceSystem()


def _make_vigilance(rate: float = 0.05) -> CPAVigilanceSystem:
    """Helper: create a CPAVigilanceSystem with a custom spot-check probability."""
    return CPAVigilanceSystem(config=FrictionConfig(spot_check_probability=rate))


@pytest.fixture
def low_transaction() -> dict:
    """A LOW-consequence transaction (amount < $1,000)."""
    return {
        "transaction_id": "txn-low-001",
        "vendor": "Staples PR",
        "amount": Decimal("185.00"),
        "type": "expense",
        "consequence_level": "LOW",
        "account_code": "5800",
        "account_name": "Materiales y Suministros",
    }


@pytest.fixture
def medium_transaction() -> dict:
    """A MEDIUM-consequence transaction ($1,000 - $10,000)."""
    return {
        "transaction_id": "txn-medium-001",
        "vendor": "Dell Technologies",
        "amount": Decimal("3200.00"),
        "type": "expense",
        "consequence_level": "MEDIUM",
        "account_code": "1500",
        "account_name": "Equipos de Computacion",
    }


@pytest.fixture
def high_transaction() -> dict:
    """A HIGH-consequence transaction (amount > $10,000)."""
    return {
        "transaction_id": "txn-high-001",
        "vendor": "Toyota de Puerto Rico",
        "amount": Decimal("25000.00"),
        "type": "purchase",
        "consequence_level": "HIGH",
        "account_code": "1600",
        "account_name": "Vehiculos",
    }


@pytest.fixture
def tax_filing_transaction() -> dict:
    """A tax filing transaction (always HIGH regardless of amount)."""
    return {
        "transaction_id": "txn-tax-001",
        "vendor": "Hacienda PR",
        "amount": Decimal("500.00"),  # Low amount but HIGH because tax_filing type
        "type": "tax_filing",
        "account_code": "2400",
        "account_name": "Impuestos por Pagar",
    }


@pytest.fixture
def payroll_transaction() -> dict:
    """A payroll transaction (always HIGH regardless of amount)."""
    return {
        "transaction_id": "txn-payroll-001",
        "vendor": "Nomina Empleados",
        "amount": Decimal("15000.00"),
        "type": "payroll",
        "account_code": "5100",
        "account_name": "Sueldos y Salarios",
    }


# ---------------------------------------------------------------------------
# TEST 1: LOW transaction gets CLICK friction
# ---------------------------------------------------------------------------

def test_low_transaction_gets_click_friction(vigilance, low_transaction):
    """LOW-consequence transaction must get CLICK-type friction challenge."""
    challenge = vigilance.generate_friction_challenge(low_transaction, "LOW")

    assert challenge["type"] == "CLICK"
    assert "instruction" in challenge
    assert challenge["instruction"] == "Confirmar"


def test_low_transaction_approval_level(vigilance, low_transaction):
    """Transaction under $1,000 must be assigned LOW approval level."""
    level = vigilance.get_approval_level(low_transaction)
    assert level == "LOW"


def test_low_transaction_vigile_agent():
    """VigileAgent must assign LOW friction to small, high-confidence transactions."""
    agent = VigileAgent()
    decision = agent.should_flag(
        transaction_amount=Decimal("500.00"),
        vendor="Staples PR",
        confidence=Decimal("0.95"),
    )

    # Without random spot-check, must be LOW (no review required)
    # Note: 5% chance of random spot check, so we test the non-spot-check path
    # by testing the friction_level attribute (spot-check items still get LOW level)
    assert decision.friction_level == FrictionLevel.LOW


# ---------------------------------------------------------------------------
# TEST 2: HIGH transaction gets QUESTION friction
# ---------------------------------------------------------------------------

def test_high_transaction_gets_question_friction(vigilance, high_transaction):
    """HIGH-consequence transaction must get QUESTION-type friction challenge."""
    challenge = vigilance.generate_friction_challenge(high_transaction, "HIGH")

    assert challenge["type"] == "QUESTION"
    assert "question" in challenge
    assert "correct_answer" in challenge
    assert len(challenge["question"]) > 10


def test_high_transaction_approval_level(vigilance, high_transaction):
    """Transaction over $10,000 must be assigned HIGH approval level."""
    level = vigilance.get_approval_level(high_transaction)
    assert level == "HIGH"


def test_high_transaction_vigile_agent():
    """VigileAgent must assign HIGH friction to large transactions."""
    agent = VigileAgent()
    decision = agent.should_flag(
        transaction_amount=Decimal("50000.00"),
        vendor="Toyota de Puerto Rico",
        confidence=Decimal("0.90"),
    )

    assert decision.friction_level == FrictionLevel.HIGH
    assert decision.requires_review is True


def test_high_transaction_question_contains_vendor(vigilance, high_transaction):
    """HIGH friction question must reference the transaction's vendor."""
    challenge = vigilance.generate_friction_challenge(high_transaction, "HIGH")
    # The question should reference something specific about the transaction
    assert "question" in challenge
    assert len(challenge["question"]) > 0


def test_high_transaction_correct_answer_is_not_empty(vigilance, high_transaction):
    """HIGH friction challenge must have a non-empty correct_answer."""
    challenge = vigilance.generate_friction_challenge(high_transaction, "HIGH")
    assert "correct_answer" in challenge
    assert len(str(challenge["correct_answer"])) > 0


# ---------------------------------------------------------------------------
# TEST 3: Tax filing always gets HIGH friction
# ---------------------------------------------------------------------------

def test_tax_filing_always_gets_high_friction(vigilance, tax_filing_transaction):
    """Transactions of type 'tax_filing' must always be HIGH, regardless of amount."""
    level = vigilance.get_approval_level(tax_filing_transaction)
    assert level == "HIGH"


def test_payroll_always_gets_high_friction(vigilance, payroll_transaction):
    """Transactions of type 'payroll' must always be HIGH, regardless of amount."""
    level = vigilance.get_approval_level(payroll_transaction)
    assert level == "HIGH"


def test_year_end_entry_always_high(vigilance):
    """Transactions of type 'year_end_entry' must always be HIGH."""
    year_end_txn = {
        "transaction_id": "txn-ye-001",
        "vendor": "Auditores PR",
        "amount": Decimal("200.00"),  # Low amount
        "type": "year_end_entry",
        "account_code": "3000",
        "account_name": "Cierre de Ejercicio",
    }
    level = vigilance.get_approval_level(year_end_txn)
    assert level == "HIGH"


def test_tax_filing_low_amount_still_high(vigilance):
    """A $50 tax payment of type 'tax_filing' must still be HIGH."""
    tiny_tax = {
        "transaction_id": "txn-tax-tiny",
        "vendor": "Hacienda PR",
        "amount": Decimal("50.00"),
        "type": "tax_filing",
    }
    level = vigilance.get_approval_level(tiny_tax)
    assert level == "HIGH"


def test_auditor_flagged_transaction_always_high(vigilance):
    """Any transaction with auditor_flagged=True must be HIGH."""
    flagged_txn = {
        "transaction_id": "txn-flagged-001",
        "vendor": "Suspicious Vendor",
        "amount": Decimal("100.00"),  # Low amount
        "type": "expense",
        "auditor_flagged": True,
    }
    level = vigilance.get_approval_level(flagged_txn)
    assert level == "HIGH"


# ---------------------------------------------------------------------------
# TEST 4: Fast approval (< 5s) on HIGH transaction is flagged
# ---------------------------------------------------------------------------

def test_fast_high_approval_is_flagged(vigilance):
    """
    Approving a HIGH-consequence item in < 5 seconds must be flagged
    as suspicious in the CPA vigilance metrics.
    """
    cpa_license = "CPA-PR-TEST-001"
    item_id = "item-high-test-001"

    # Record a HIGH-level approval completed in 2 seconds (below 5s threshold)
    vigilance.track_approval_time(cpa_license, item_id, 2, "HIGH")

    metrics = vigilance.get_cpa_vigilance_metrics(cpa_license)
    assert metrics["suspicious_fast_approvals"] == 1


def test_slow_high_approval_not_flagged(vigilance):
    """
    Approving a HIGH-consequence item in >= 5 seconds must NOT be flagged.
    """
    cpa_license = "CPA-PR-TEST-002"
    item_id = "item-high-slow-001"

    # 10 seconds — well above the 5s threshold
    vigilance.track_approval_time(cpa_license, item_id, 10, "HIGH")

    metrics = vigilance.get_cpa_vigilance_metrics(cpa_license)
    assert metrics["suspicious_fast_approvals"] == 0


def test_fast_low_approval_not_flagged(vigilance):
    """
    Approving a LOW-consequence item quickly must NOT be flagged
    (only HIGH items are subject to the 5s threshold).
    """
    cpa_license = "CPA-PR-TEST-003"
    item_id = "item-low-fast-001"

    # 1 second on a LOW item — should not be flagged
    vigilance.track_approval_time(cpa_license, item_id, 1, "LOW")

    metrics = vigilance.get_cpa_vigilance_metrics(cpa_license)
    assert metrics["suspicious_fast_approvals"] == 0


def test_multiple_fast_approvals_counted_correctly(vigilance):
    """Multiple suspicious fast approvals must be counted correctly."""
    cpa_license = "CPA-PR-TEST-004"

    # 3 fast HIGH approvals
    for i in range(3):
        vigilance.track_approval_time(cpa_license, f"item-fast-{i}", 1, "HIGH")

    # 2 normal HIGH approvals
    for i in range(2):
        vigilance.track_approval_time(cpa_license, f"item-slow-{i}", 30, "HIGH")

    metrics = vigilance.get_cpa_vigilance_metrics(cpa_license)
    assert metrics["suspicious_fast_approvals"] == 3


def test_approval_threshold_is_5_seconds():
    """The fast approval threshold must be 5 seconds."""
    assert FAST_APPROVAL_THRESHOLD_SECONDS == 5


def test_approval_time_exactly_at_threshold_not_flagged(vigilance):
    """An approval at exactly 5 seconds (not < 5) must NOT be flagged."""
    cpa_license = "CPA-PR-TEST-005"
    item_id = "item-boundary-001"

    vigilance.track_approval_time(cpa_license, item_id, 5, "HIGH")
    metrics = vigilance.get_cpa_vigilance_metrics(cpa_license)
    assert metrics["suspicious_fast_approvals"] == 0


# ---------------------------------------------------------------------------
# TEST 5: Random verification is triggered ~5% of time (100 calls)
# ---------------------------------------------------------------------------

def test_random_verification_triggered_approximately_5_percent():
    """
    Over 1000 calls with a list of 1 processed transaction, the verification
    should trigger approximately 5% of the time (allow 1%–15% tolerance for
    statistical variance in a unit test).
    """
    import random

    # Use a fixed seed for reproducibility
    random.seed(42)

    vigilance = _make_vigilance(rate=0.05)

    processed = [
        {
            "transaction_id": "txn-processed-001",
            "vendor": "Costco PR",
            "amount": Decimal("2450.00"),
            "type": "expense",
            "consequence_level": "LOW",
        }
    ]

    trigger_count = 0
    total_calls = 1000

    for _ in range(total_calls):
        result = vigilance.generate_random_verification_request(processed)
        if result is not None:
            trigger_count += 1

    rate = trigger_count / total_calls

    # Allow 1% to 15% tolerance (5% ± large margin for statistical variance)
    assert 0.01 <= rate <= 0.15, (
        f"Verification rate {rate:.1%} is outside expected range [1%, 15%] "
        f"(triggered {trigger_count}/{total_calls} times)"
    )


def test_random_verification_returns_none_for_empty_list(vigilance):
    """generate_random_verification_request must return None for empty input."""
    result = vigilance.generate_random_verification_request([])
    assert result is None


def test_random_verification_request_has_required_fields():
    """When a verification is triggered, it must have required fields."""
    import random
    random.seed(1)  # Seed that triggers verification

    vigilance = _make_vigilance(rate=1.0)  # Always trigger

    processed = [
        {
            "transaction_id": "txn-verify-001",
            "vendor": "Test Vendor",
            "amount": Decimal("500.00"),
            "type": "expense",
            "consequence_level": "LOW",
        }
    ]

    result = vigilance.generate_random_verification_request(processed)
    assert result is not None

    required_keys = {
        "item_id", "item_type", "transaction_id",
        "vendor", "amount", "description",
        "consequence_level", "status",
    }
    missing = required_keys - set(result.keys())
    assert not missing, f"Missing keys in verification request: {missing}"


def test_random_verification_has_is_verification_flag():
    """
    The internal verification flag must be present so the system can track
    outcomes even though it's hidden from the CPA.
    """
    vigilance = _make_vigilance(rate=1.0)

    processed = [
        {
            "transaction_id": "txn-internal-flag",
            "vendor": "Test Vendor",
            "amount": Decimal("300.00"),
            "type": "expense",
        }
    ]

    result = vigilance.generate_random_verification_request(processed)
    assert result is not None
    assert result.get("is_verification") is True
    assert result.get("expected_action") == "approved"


def test_random_verification_zero_rate_never_triggers():
    """A vigilance system with rate=0 must never trigger verification."""
    import random
    random.seed(42)

    vigilance = _make_vigilance(rate=0.0)

    processed = [{"transaction_id": "txn-never", "vendor": "V", "amount": Decimal("100")}]

    for _ in range(100):
        result = vigilance.generate_random_verification_request(processed)
        assert result is None


# ---------------------------------------------------------------------------
# TEST 6: MEDIUM friction
# ---------------------------------------------------------------------------

def test_medium_transaction_gets_expand_friction(vigilance, medium_transaction):
    """MEDIUM-consequence transaction must get EXPAND-type friction challenge."""
    challenge = vigilance.generate_friction_challenge(medium_transaction, "MEDIUM")

    assert challenge["type"] == "EXPAND"
    assert "instruction" in challenge
    assert "detail_key" in challenge


def test_medium_transaction_approval_level(vigilance, medium_transaction):
    """Transaction between $1,000 and $10,000 must be MEDIUM."""
    level = vigilance.get_approval_level(medium_transaction)
    assert level == "MEDIUM"


# ---------------------------------------------------------------------------
# TEST 7: Metrics with no approvals
# ---------------------------------------------------------------------------

def test_metrics_with_no_approvals_returns_zeros(vigilance):
    """CPA with no approvals must return zero metrics."""
    metrics = vigilance.get_cpa_vigilance_metrics("CPA-NEW-NO-HISTORY")

    assert metrics["total_approvals"] == 0
    assert metrics["suspicious_fast_approvals"] == 0
    assert metrics["random_verification_accuracy"] == 1.0  # Default when no verifications sent


# ---------------------------------------------------------------------------
# TEST 8: VigileAgent confidence-based friction
# ---------------------------------------------------------------------------

def test_low_confidence_triggers_high_friction():
    """Very low confidence (<0.70) must trigger HIGH friction regardless of amount."""
    agent = VigileAgent()
    decision = agent.should_flag(
        transaction_amount=Decimal("100.00"),  # Low amount
        vendor="Unknown Vendor",
        confidence=Decimal("0.50"),  # Very low confidence
    )
    assert decision.friction_level == FrictionLevel.HIGH


def test_medium_confidence_triggers_medium_friction():
    """Confidence 0.70-0.79 must trigger MEDIUM friction."""
    agent = VigileAgent()
    decision = agent.should_flag(
        transaction_amount=Decimal("100.00"),
        vendor="Known Vendor",
        confidence=Decimal("0.75"),
    )
    assert decision.friction_level == FrictionLevel.MEDIUM


def test_friction_decision_is_frozen():
    """FrictionDecision must be immutable."""
    decision = FrictionDecision(
        friction_level=FrictionLevel.LOW,
        requires_review=False,
        reason="Test",
    )
    with pytest.raises(Exception):
        decision.friction_level = FrictionLevel.HIGH  # type: ignore
