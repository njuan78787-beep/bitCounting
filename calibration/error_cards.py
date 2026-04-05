# =============================================================================
# calibration/error_cards.py
# Error Card System for Bit-Counting.
#
# PURPOSE:
#   Every classification error the system makes generates a structured
#   ErrorCard.  These cards serve three functions:
#
#   1. LEARNING POOL: Anonymized cards are shared across clients to improve
#      the system's accuracy on similar transactions.
#
#   2. AUDIT TRAIL: Non-anonymized cards are retained per client for CPA
#      review and regulatory compliance.
#
#   3. CALIBRATION FEEDBACK: Wrong calibration runs generate ErrorCards
#      which are fed back into SyntheticCalibrationTwin for iteration.
#
# DESIGN:
#   - ErrorCard is frozen (immutable) — cards cannot be altered after creation.
#   - fiscal_impact_estimate is always in USD Decimal (never abstract).
#   - why_its_wrong must reference the specific rule violated.
#   - context_that_changes_answer documents the conditions under which the
#     correct answer would be different (important for PR edge cases).
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ERROR CARD MODEL
# ---------------------------------------------------------------------------

class ErrorCard(BaseModel):
    """
    Immutable record of a classification error made by the system.

    Once created, an ErrorCard cannot be modified.  The CPA correction
    is embedded at creation time and is part of the permanent record.
    """
    model_config = ConfigDict(frozen=True)

    card_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unique identifier for this error card",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this card was generated",
    )

    # --- What went wrong ---
    what_went_wrong: str = Field(
        min_length=10,
        description="Plain-language description of the error (what the system did wrong)",
    )
    why_its_wrong: str = Field(
        min_length=10,
        description=(
            "Technical explanation with specific rule reference. "
            "Format: 'Violated rule [rule_id]: <explanation>'. "
            "Must cite an actual tax rule or GAAP standard."
        ),
    )
    fiscal_impact_estimate: Decimal = Field(
        description="Estimated monetary impact of this error in USD. Never abstract.",
    )

    # --- Context and learning ---
    context_that_changes_answer: str = Field(
        min_length=10,
        description=(
            "The conditions under which the correct answer would be DIFFERENT. "
            "Example: 'If this were a manufacturing company, IVU exemption PR-6119 would apply.'"
        ),
    )
    documented_exceptions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Known exceptions to the rule that was violated, if any",
    )
    related_card_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="IDs of other ErrorCards that cover similar errors",
    )

    # --- Source trace ---
    agent_decision_id: str = Field(
        description="decision_id from OrchestratorDecision that produced the error",
    )
    transaction_id: str = Field(
        description="ID of the transaction that was misclassified",
    )
    expected_account_code: str = Field(
        description="The correct account code (CPA-verified)",
    )
    actual_account_code: str = Field(
        description="The account code the system incorrectly assigned",
    )
    cpa_correction: str = Field(
        min_length=5,
        description="The CPA's correction and reasoning, verbatim",
    )
    cpa_license: Optional[str] = Field(
        default=None,
        description="License number of the CPA who provided the correction",
    )

    # --- Pool membership ---
    is_anonymized: bool = Field(
        default=False,
        description="True if PII has been removed for pool sharing",
    )
    contributed_to_pool: bool = Field(
        default=False,
        description="True if this card has been shared to the anonymized learning pool",
    )
    anonymized_card_id: Optional[str] = Field(
        default=None,
        description="ID of the anonymized version in the pool (if contributed)",
    )


# ---------------------------------------------------------------------------
# ERROR CARD SYSTEM
# ---------------------------------------------------------------------------

class ErrorCardSystem:
    """
    Manages creation, storage, retrieval, and anonymization of ErrorCards.

    Thread-safety: Phase 1 uses in-memory list. Phase 2 persists to
    the `error_cards` PostgreSQL table (to be added to schema.sql).
    """

    def __init__(self) -> None:
        # card_id -> ErrorCard
        self._cards: dict[str, ErrorCard] = {}
        # Anonymized pool (indexed separately so original IDs are not exposed)
        self._pool: dict[str, ErrorCard] = {}

    # ------------------------------------------------------------------
    # CARD CREATION
    # ------------------------------------------------------------------

    def create_error_card(
        self,
        *,
        agent_decision_id: str,
        transaction_id: str,
        expected_account_code: str,
        actual_account_code: str,
        cpa_correction: str,
        cpa_license: Optional[str] = None,
        vendor: Optional[str] = None,
        amount: Optional[Decimal] = None,
        transaction_type: Optional[str] = None,
        rule_ref: Optional[str] = None,
    ) -> ErrorCard:
        """
        Create an ErrorCard from an agent decision corrected by a CPA.

        The fiscal_impact_estimate is calculated automatically:
          - If amount is provided and accounts differ, the full amount is used
            as a conservative upper bound on impact.
          - If amount is not provided, defaults to $0.00.

        Args:
            agent_decision_id: decision_id from the OrchestratorDecision
            transaction_id:    ID of the misclassified transaction
            expected_account_code: correct account (CPA-verified)
            actual_account_code:   what the system assigned
            cpa_correction:    CPA's documented correction and reasoning
            cpa_license:       CPA license number (optional)
            vendor:            Vendor name for context
            amount:            Transaction amount (for fiscal impact estimation)
            transaction_type:  Type hint for the why_its_wrong message
            rule_ref:          Specific rule that was violated

        Returns:
            Frozen ErrorCard (also stored internally)
        """
        fiscal_impact = amount if amount is not None else Decimal("0.00")
        rule_cited = rule_ref or "UNKNOWN-RULE"
        vendor_str = vendor or "Unknown Vendor"
        txn_type_str = transaction_type or "transaction"

        what_went_wrong = (
            f"System classified {txn_type_str} from '{vendor_str}' as account "
            f"{actual_account_code} but correct classification is {expected_account_code}."
        )

        why_its_wrong = (
            f"Violated rule [{rule_cited}]: The classification of account "
            f"{actual_account_code} is incorrect for this transaction type. "
            f"Expected account {expected_account_code} per the applicable "
            f"accounting rule. CPA correction: {cpa_correction}"
        )

        context_changes = self._infer_context_changes(
            expected_account_code, actual_account_code, amount, transaction_type
        )

        exceptions = self._lookup_exceptions(expected_account_code, rule_cited)

        card = ErrorCard(
            what_went_wrong=what_went_wrong,
            why_its_wrong=why_its_wrong,
            fiscal_impact_estimate=fiscal_impact,
            context_that_changes_answer=context_changes,
            documented_exceptions=tuple(exceptions),
            related_card_ids=tuple(
                self._find_related_cards(expected_account_code, actual_account_code)
            ),
            agent_decision_id=agent_decision_id,
            transaction_id=transaction_id,
            expected_account_code=expected_account_code,
            actual_account_code=actual_account_code,
            cpa_correction=cpa_correction,
            cpa_license=cpa_license,
            is_anonymized=False,
            contributed_to_pool=False,
        )

        self._cards[card.card_id] = card
        logger.info(
            "ErrorCard %s created: txn=%s expected=%s got=%s impact=$%s",
            card.card_id, transaction_id,
            expected_account_code, actual_account_code,
            fiscal_impact,
        )
        return card

    # ------------------------------------------------------------------
    # RETRIEVAL
    # ------------------------------------------------------------------

    def get_card(self, card_id: str) -> Optional[ErrorCard]:
        """Return a specific ErrorCard by ID, or None if not found."""
        return self._cards.get(card_id)

    def get_all_cards(self) -> list[ErrorCard]:
        """Return all ErrorCards (including non-anonymized)."""
        return list(self._cards.values())

    def get_cards_for_learning(self) -> list[ErrorCard]:
        """
        Return anonymized ErrorCards suitable for the shared learning pool.

        Cards are anonymized on first call (lazy anonymization).
        Anonymized cards have vendor names, transaction IDs, and CPA
        license numbers removed.
        """
        for card in self._cards.values():
            if not card.contributed_to_pool:
                anon_card = self._anonymize_card(card)
                self._pool[anon_card.card_id] = anon_card
        return list(self._pool.values())

    def get_pool_size(self) -> int:
        """Return the number of anonymized cards in the pool."""
        return len(self._pool)

    def get_cards_by_account(self, account_code: str) -> list[ErrorCard]:
        """Return all cards where the expected account matches."""
        return [
            c for c in self._cards.values()
            if c.expected_account_code == account_code
        ]

    def get_high_impact_cards(
        self,
        min_impact: Decimal = Decimal("1000.00"),
    ) -> list[ErrorCard]:
        """Return cards with fiscal_impact_estimate >= min_impact, sorted descending."""
        high = [c for c in self._cards.values() if c.fiscal_impact_estimate >= min_impact]
        return sorted(high, key=lambda c: c.fiscal_impact_estimate, reverse=True)

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _anonymize_card(card: ErrorCard) -> ErrorCard:
        """
        Create an anonymized version of an ErrorCard for the learning pool.

        Removes:
          - transaction_id (replaced with deterministic hash)
          - cpa_license
          - vendor names from what_went_wrong text

        Keeps:
          - what_went_wrong (vendor name scrubbed)
          - why_its_wrong (rule references preserved)
          - fiscal_impact_estimate
          - context_that_changes_answer
          - documented_exceptions
          - account codes (needed for learning)
        """
        # Deterministic anonymized ID from original transaction_id
        anon_txn_id = "anon-" + hashlib.sha256(card.transaction_id.encode()).hexdigest()[:12]

        # Scrub vendor names (quoted strings) from what_went_wrong
        anon_what = re.sub(r"'[^']*'", "'[VENDOR ANONYMIZED]'", card.what_went_wrong)

        return ErrorCard(
            card_id=str(uuid.uuid4()),  # New ID for pool card
            created_at=card.created_at,
            what_went_wrong=anon_what,
            why_its_wrong=card.why_its_wrong,
            fiscal_impact_estimate=card.fiscal_impact_estimate,
            context_that_changes_answer=card.context_that_changes_answer,
            documented_exceptions=card.documented_exceptions,
            related_card_ids=card.related_card_ids,
            agent_decision_id=card.agent_decision_id,
            transaction_id=anon_txn_id,
            expected_account_code=card.expected_account_code,
            actual_account_code=card.actual_account_code,
            cpa_correction=card.cpa_correction,
            cpa_license=None,           # Anonymized
            is_anonymized=True,
            contributed_to_pool=True,
            anonymized_card_id=None,
        )

    @staticmethod
    def _infer_context_changes(
        expected: str,
        actual: str,
        amount: Optional[Decimal],
        transaction_type: Optional[str],
    ) -> str:
        """
        Generate a context explanation for when the correct answer would differ.
        """
        # Capital asset context
        if expected in ("1500", "1600", "1400") and amount is not None:
            return (
                f"If the amount were below $2,500 (current: ${amount}), this would be "
                f"expensed to account {actual} instead of capitalized to {expected}. "
                "Also, if this is a repair rather than an improvement, it must be expensed "
                "regardless of amount per GAAP ASC 360."
            )
        # IVU context
        if expected == "2410":
            return (
                "If this payment were for a different tax type (income tax, property tax), "
                "it would map to account 2400 instead of 2410. "
                "Also, if the entity is IVU-exempt (e.g., government entity), no IVU liability exists."
            )
        # Payroll context
        if expected == "5100":
            return (
                "If this were a contractor payment (1099) rather than employee payroll (W-2), "
                "it would map to account 5400 (Servicios Profesionales) instead."
            )
        # Loan principal vs interest
        if expected == "2100":
            return (
                "If this payment included an interest component, that portion must be "
                "separated and posted to account 7100 (Gastos de Intereses). "
                "The principal portion always reduces the liability (2100)."
            )
        # Default
        return (
            f"The correct account ({expected}) applies under standard conditions. "
            "Context that could change this: entity-specific tax elections, "
            "industry-specific GAAP exceptions, or active Act 60 / Act 20 incentives."
        )

    @staticmethod
    def _lookup_exceptions(account_code: str, rule_ref: str) -> list[str]:
        """Return known exceptions for the given account/rule combination."""
        exceptions_db: dict[str, list[str]] = {
            "5900": [
                "Act 60 qualifying businesses may deduct 100% of certain operating expenses",
                "Meals and entertainment: only 50% deductible per IRC §274 (applicable to PR)",
            ],
            "1500": [
                "Section 179 election allows immediate expensing of qualifying assets",
                "Bonus depreciation may apply for assets placed in service after 2017",
            ],
            "2410": [
                "IVU exemption for food (non-prepared), medicine, and qualifying manufacturing inputs",
                "Resellers may claim IVU exemption certificate (certificado de revendedor)",
            ],
            "5100": [
                "Officers of S-Corps must take reasonable compensation subject to payroll taxes",
                "Per-diem allowances within IRS rates are not taxable income",
            ],
        }
        return exceptions_db.get(account_code, [])

    def _find_related_cards(
        self, expected: str, actual: str
    ) -> list[str]:
        """Return card IDs that involve the same expected or actual account."""
        return [
            c.card_id
            for c in self._cards.values()
            if c.expected_account_code == expected or c.actual_account_code == actual
        ][:5]  # Limit to 5 related cards
