# =============================================================================
# calibration/error_cards.py
# ErrorCard system for learning from agent mistakes.
#
# When an agent makes a wrong classification or decision, an ErrorCard is
# created to capture the mistake for future calibration and model improvement.
#
# DESIGN (~80 lines of logic):
#   ErrorCard         — Pydantic model representing a single mistake
#   ErrorCardRegistry — In-memory list with append, query, and resolve methods
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# ERROR CARD MODEL
# ---------------------------------------------------------------------------

class ErrorCard(BaseModel):
    """
    Records a single agent mistake for learning and calibration purposes.

    Once created an ErrorCard is immutable (frozen=True).
    Resolution creates a NEW ErrorCard-like record via the registry.
    """

    model_config = ConfigDict(frozen=True)

    card_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID unique identifier for this error card",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the error was recorded",
    )
    agent_name: str = Field(
        description="Name of the agent that made the error (e.g. CLASIFICADOR, INTAKE)",
    )
    error_type: str = Field(
        description=(
            "Category of error. Examples: WRONG_ACCOUNT, MISSING_FIELD_NOT_DETECTED, "
            "CONFIDENCE_TOO_HIGH, IVU_EXEMPT_MISSED, CAPITALIZATION_THRESHOLD_ERROR"
        ),
    )
    description: str = Field(
        description="Human-readable description of what went wrong",
        min_length=10,
    )
    raw_input_snapshot: dict[str, Any] = Field(
        description="Snapshot of the input that caused the error",
    )
    expected_output: dict[str, Any] = Field(
        description="What the correct output should have been",
    )
    actual_output: dict[str, Any] = Field(
        description="What the agent actually produced",
    )
    resolution: Optional[str] = Field(
        default=None,
        description="How the error was resolved (None if still unresolved)",
    )
    resolved_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the error was resolved",
    )
    resolution_notes: Optional[str] = Field(
        default=None,
        description="CPA or engineer notes on the resolution",
    )


# ---------------------------------------------------------------------------
# ERROR CARD REGISTRY
# ---------------------------------------------------------------------------

class ErrorCardRegistry:
    """
    In-memory registry of ErrorCards.

    Append-only: errors are added but never deleted (for audit trail).
    Resolution marks a card as resolved but does not remove it.
    """

    def __init__(self) -> None:
        self._cards: list[ErrorCard] = []

    def append_card(
        self,
        agent_name: str,
        error_type: str,
        description: str,
        raw_input_snapshot: dict[str, Any],
        expected_output: dict[str, Any],
        actual_output: dict[str, Any],
    ) -> ErrorCard:
        """
        Create and store a new ErrorCard.

        Returns the created ErrorCard (which is also stored internally).
        """
        card = ErrorCard(
            agent_name=agent_name,
            error_type=error_type,
            description=description,
            raw_input_snapshot=raw_input_snapshot,
            expected_output=expected_output,
            actual_output=actual_output,
        )
        self._cards.append(card)
        return card

    def get_unresolved(self) -> list[ErrorCard]:
        """Return all ErrorCards that have not been resolved yet."""
        return [c for c in self._cards if c.resolution is None]

    def get_all(self) -> list[ErrorCard]:
        """Return all ErrorCards in chronological order."""
        return list(self._cards)

    def resolve_card(
        self,
        card_id: str,
        resolution: str,
        resolution_notes: str = "",
    ) -> ErrorCard:
        """
        Mark an ErrorCard as resolved.

        Since ErrorCard is frozen (immutable), this replaces the card in the
        registry with a new instance that has resolution fields populated.

        Args:
            card_id:          UUID of the card to resolve.
            resolution:       Short description of the resolution.
            resolution_notes: Optional detailed notes.

        Returns:
            The updated (resolved) ErrorCard.

        Raises:
            KeyError: If no card with the given card_id is found.
            ValueError: If the card is already resolved.
        """
        for i, card in enumerate(self._cards):
            if card.card_id == card_id:
                if card.resolution is not None:
                    raise ValueError(
                        f"ErrorCard {card_id} is already resolved: {card.resolution!r}"
                    )
                # Replace with a new instance (frozen model — must rebuild)
                resolved = ErrorCard(
                    card_id=card.card_id,
                    timestamp=card.timestamp,
                    agent_name=card.agent_name,
                    error_type=card.error_type,
                    description=card.description,
                    raw_input_snapshot=card.raw_input_snapshot,
                    expected_output=card.expected_output,
                    actual_output=card.actual_output,
                    resolution=resolution,
                    resolved_at=datetime.now(timezone.utc),
                    resolution_notes=resolution_notes or None,
                )
                self._cards[i] = resolved
                return resolved

        raise KeyError(f"ErrorCard with card_id={card_id!r} not found in registry")

    def __len__(self) -> int:
        return len(self._cards)
