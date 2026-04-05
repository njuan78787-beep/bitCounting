# =============================================================================
# friction/cpa_vigilance.py
# CPA vigilance and friction system for Bit-Counting.
#
# DESIGN:
#   FrictionLevel  — LOW / MEDIUM / HIGH
#   FrictionConfig — Pydantic model with configurable thresholds
#   FrictionDecision — frozen Pydantic: friction_level, requires_review, reason
#   VigileAgent    — evaluates transactions and decides friction level
#   CPAVigilanceSystem — tracks approval times, detects suspicious patterns,
#                        and wraps VigileAgent for use by the API routes.
#
# FRICTION RULES:
#   HIGH:   amount > $10,000  OR  confidence < 0.70
#   MEDIUM: amount > $2,500   OR  confidence < 0.80
#   LOW:    default
#   RANDOM: ~5% of PROCEED transactions get flagged for spot check
# =============================================================================

from __future__ import annotations

import random
import time
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# ENUMS
# ---------------------------------------------------------------------------

class FrictionLevel(str, Enum):
    """Level of friction applied to a CPA approval action."""
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

class FrictionConfig(BaseModel):
    """
    Thresholds that drive friction level assignments.

    All amounts are in USD (Puerto Rico operates in USD).
    Confidence scores are [0.00, 1.00].
    """

    model_config = ConfigDict(frozen=False)

    # Amount thresholds
    high_amount_threshold: Decimal = Field(
        default=Decimal("10000.00"),
        description="Transactions above this amount get HIGH friction",
    )
    medium_amount_threshold: Decimal = Field(
        default=Decimal("2500.00"),
        description="Transactions above this amount get MEDIUM friction (if not HIGH)",
    )

    # Confidence thresholds
    high_confidence_floor: Decimal = Field(
        default=Decimal("0.70"),
        description="Confidence below this triggers HIGH friction",
    )
    medium_confidence_floor: Decimal = Field(
        default=Decimal("0.80"),
        description="Confidence below this triggers MEDIUM friction (if not HIGH)",
    )

    # Random spot-check probability for PROCEED transactions
    spot_check_probability: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Probability that a PROCEED transaction gets a random spot check",
    )

    # Suspicious fast-approval threshold (seconds)
    fast_approval_threshold_seconds: int = Field(
        default=5,
        ge=1,
        description="Approvals completed faster than this on HIGH items are flagged",
    )


# ---------------------------------------------------------------------------
# FRICTION DECISION
# ---------------------------------------------------------------------------

class FrictionDecision(BaseModel):
    """
    Result of a VigileAgent friction evaluation.

    Immutable once created — used as a value object throughout the system.
    """

    model_config = ConfigDict(frozen=True)

    friction_level: FrictionLevel = Field(
        description="Friction level assigned: LOW, MEDIUM, or HIGH",
    )
    requires_review: bool = Field(
        description="True if this item must be reviewed by a CPA before approval",
    )
    reason: str = Field(
        description="Human-readable explanation of why this friction level was assigned",
    )
    is_random_spot_check: bool = Field(
        default=False,
        description="True if this was a random spot-check (5% sampling)",
    )


# ---------------------------------------------------------------------------
# VIGILE AGENT
# ---------------------------------------------------------------------------

class VigileAgent:
    """
    Evaluates a transaction and assigns a friction level for CPA approval.

    Rules applied in priority order:
      1. HIGH:   amount > $10,000  OR  confidence < 0.70
      2. MEDIUM: amount > $2,500   OR  confidence < 0.80
      3. Random spot-check: ~5% of LOW transactions get flagged
      4. LOW:    default (no friction beyond normal confirmation)
    """

    def __init__(self, config: Optional[FrictionConfig] = None) -> None:
        self._config = config or FrictionConfig()

    @property
    def config(self) -> FrictionConfig:
        return self._config

    def should_flag(
        self,
        transaction_amount: Decimal,
        vendor: str,
        confidence: Decimal,
    ) -> FrictionDecision:
        """
        Evaluate a transaction and return the appropriate FrictionDecision.

        Args:
            transaction_amount: Total amount of the transaction in USD.
            vendor:             Vendor/payee name.
            confidence:         Confidence score from INTAKE [0.00, 1.00].

        Returns:
            FrictionDecision with friction_level, requires_review, and reason.
        """
        cfg = self._config

        # --- HIGH: amount or confidence triggers high friction ---
        if transaction_amount > cfg.high_amount_threshold:
            return FrictionDecision(
                friction_level=FrictionLevel.HIGH,
                requires_review=True,
                reason=(
                    f"Monto ${transaction_amount:,.2f} supera el umbral de alta fricción "
                    f"(${cfg.high_amount_threshold:,.2f}). Requiere revisión detallada del CPA."
                ),
            )

        if confidence < cfg.high_confidence_floor:
            return FrictionDecision(
                friction_level=FrictionLevel.HIGH,
                requires_review=True,
                reason=(
                    f"Confianza {float(confidence):.2%} por debajo del umbral HIGH "
                    f"({float(cfg.high_confidence_floor):.2%}). "
                    f"Calidad del documento insuficiente para aprobación automática."
                ),
            )

        # --- MEDIUM: amount or confidence triggers medium friction ---
        if transaction_amount > cfg.medium_amount_threshold:
            return FrictionDecision(
                friction_level=FrictionLevel.MEDIUM,
                requires_review=True,
                reason=(
                    f"Monto ${transaction_amount:,.2f} supera el umbral de capitalización "
                    f"(${cfg.medium_amount_threshold:,.2f}). El CPA debe confirmar la clasificación."
                ),
            )

        if confidence < cfg.medium_confidence_floor:
            return FrictionDecision(
                friction_level=FrictionLevel.MEDIUM,
                requires_review=True,
                reason=(
                    f"Confianza {float(confidence):.2%} por debajo del umbral MEDIUM "
                    f"({float(cfg.medium_confidence_floor):.2%}). "
                    f"Requiere confirmación del CPA antes de registrar."
                ),
            )

        # --- Random spot-check for ~5% of PROCEED transactions ---
        if random.random() < cfg.spot_check_probability:
            return FrictionDecision(
                friction_level=FrictionLevel.LOW,
                requires_review=True,
                reason=(
                    f"Verificación aleatoria de control de calidad ({cfg.spot_check_probability:.0%} "
                    f"de transacciones). Proveedor: {vendor}. "
                    f"El CPA debe confirmar que la clasificación automática es correcta."
                ),
                is_random_spot_check=True,
            )

        # --- LOW: default ---
        return FrictionDecision(
            friction_level=FrictionLevel.LOW,
            requires_review=False,
            reason=(
                f"Transacción de ${transaction_amount:,.2f} con confianza "
                f"{float(confidence):.2%}. Dentro de los parámetros normales."
            ),
        )


# ---------------------------------------------------------------------------
# CPA VIGILANCE SYSTEM
# ---------------------------------------------------------------------------

class CPAVigilanceSystem:
    """
    Tracks CPA approval behavior and detects vigilance concerns.

    Wraps VigileAgent and maintains per-CPA metrics:
      - Approval times per item per level
      - Count of suspicious fast approvals (< 5s on HIGH items)
      - Random verification accuracy

    Used by api/routes/cpa_dashboard.py as a singleton.
    """

    def __init__(self, config: Optional[FrictionConfig] = None) -> None:
        self._agent = VigileAgent(config)
        # { cpa_license → [{"item_id": str, "elapsed_s": int, "level": str, "ts": float}] }
        self._approval_log: dict[str, list[dict[str, Any]]] = {}
        # { cpa_license → int }  count of suspicious fast approvals
        self._suspicious_counts: dict[str, int] = {}

    # -------------------------------------------------------------------------
    # Public API used by routes
    # -------------------------------------------------------------------------

    def evaluate(
        self,
        transaction_amount: Decimal,
        vendor: str,
        confidence: Decimal,
    ) -> FrictionDecision:
        """Delegate to VigileAgent.should_flag()."""
        return self._agent.should_flag(transaction_amount, vendor, confidence)

    def track_approval_time(
        self,
        cpa_license: str,
        item_id: str,
        elapsed_seconds: int,
        level: str,
    ) -> None:
        """
        Record how long a CPA took to approve/reject an item.

        If level is HIGH and elapsed < fast_approval_threshold_seconds,
        increment the suspicious counter for this CPA.
        """
        entry: dict[str, Any] = {
            "item_id": item_id,
            "elapsed_s": elapsed_seconds,
            "level": level,
            "ts": time.time(),
        }
        if cpa_license not in self._approval_log:
            self._approval_log[cpa_license] = []
        self._approval_log[cpa_license].append(entry)

        threshold = self._agent.config.fast_approval_threshold_seconds
        if level == "HIGH" and elapsed_seconds < threshold:
            self._suspicious_counts[cpa_license] = (
                self._suspicious_counts.get(cpa_license, 0) + 1
            )

    def generate_friction_challenge(
        self,
        item: dict[str, Any],
        level: str,
    ) -> dict[str, str]:
        """
        Generate a challenge question for HIGH-friction items.

        The CPA must answer the question correctly before approving.
        Returns a dict with 'question' and 'correct_answer' keys.
        """
        amount = item.get("amount", "?")
        vendor = item.get("vendor", "desconocido")
        account = item.get("detail", {}).get("account_code", "?")

        question = (
            f"Para la transacción de ${amount} del proveedor '{vendor}': "
            f"¿Cuál es el código de cuenta asignado?"
        )
        correct_answer = str(account)

        return {
            "question": question,
            "correct_answer": correct_answer,
            "hint": "Revise la sección 'detail' de la transacción.",
        }

    def get_cpa_vigilance_metrics(self, cpa_license: str) -> dict[str, Any]:
        """
        Return vigilance metrics for a specific CPA license.

        Returns dict with:
          - total_approvals: int
          - avg_approval_time_by_level: dict mapping level → avg seconds
          - suspicious_fast_approvals: int
          - random_verification_accuracy: float (placeholder: 1.0 in Phase 1)
        """
        log = self._approval_log.get(cpa_license, [])

        total = len(log)
        if total == 0:
            return {
                "total_approvals": 0,
                "avg_approval_time_by_level": {"ALL": 0},
                "suspicious_fast_approvals": 0,
                "random_verification_accuracy": 1.0,
            }

        by_level: dict[str, list[int]] = {}
        for entry in log:
            lv = entry["level"]
            by_level.setdefault(lv, []).append(entry["elapsed_s"])
        by_level.setdefault("ALL", [e["elapsed_s"] for e in log])

        avg_by_level = {
            lv: int(sum(times) / len(times)) if times else 0
            for lv, times in by_level.items()
        }

        return {
            "total_approvals": total,
            "avg_approval_time_by_level": avg_by_level,
            "suspicious_fast_approvals": self._suspicious_counts.get(cpa_license, 0),
            "random_verification_accuracy": 1.0,  # Phase 2: computed from error cards
        }
