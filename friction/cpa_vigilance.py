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

    def get_approval_level(self, transaction: dict[str, Any]) -> str:
        """
        Determine the friction level for a transaction dict.

        Rules (in priority order):
          1. If auditor_flagged or fraud_flags present → HIGH
          2. If type is in ALWAYS_HIGH_TYPES (tax_filing, payroll, year_end_entry, etc.) → HIGH
          3. amount > $10,000 → HIGH
          4. $1,000 <= amount <= $10,000 → MEDIUM
          5. amount < $1,000 → LOW

        Args:
            transaction: dict with at least 'amount' (str/Decimal) and
                         optionally 'type', 'consequence_level', 'auditor_flagged'.

        Returns:
            "LOW", "MEDIUM", or "HIGH"
        """
        # Always-HIGH: auditor flags
        if transaction.get("auditor_flagged") or transaction.get("fraud_flags"):
            return "HIGH"

        # Always-HIGH transaction types
        _always_high = frozenset({
            "tax_filing", "payroll", "year_end_entry", "audit_flag",
            "payroll_tax", "quarterly_filing", "annual_filing",
        })
        txn_type = str(
            transaction.get("type", "") or transaction.get("item_type", "")
        ).lower()
        if txn_type in _always_high:
            return "HIGH"

        # Explicit consequence level already set on the item
        explicit = str(transaction.get("consequence_level", "")).upper()
        if explicit in ("LOW", "MEDIUM", "HIGH"):
            return explicit

        # Amount-based
        try:
            amount = Decimal(str(transaction.get("amount", "0")))
        except Exception:
            amount = Decimal("0")

        cfg = self._agent.config
        if amount >= cfg.high_amount_threshold:
            return "HIGH"
        if amount >= cfg.medium_amount_threshold:
            return "MEDIUM"
        return "LOW"

    def generate_random_verification_request(
        self,
        processed_transactions: list[dict[str, Any]],
    ) -> "Optional[dict[str, Any]]":
        """
        Randomly select a correctly-processed transaction to send back to
        the CPA as a verification request (~5% of calls).

        The CPA does NOT know this is a test — the item looks like a normal
        review queue entry.  If the CPA approves a correctly-classified item
        they pass; if they reject it that is a vigilance failure.

        Args:
            processed_transactions: List of successfully processed transaction dicts.

        Returns:
            A verification request dict if triggered, else None.
        """
        import time as _time

        if not processed_transactions:
            return None

        cfg = self._agent.config
        if random.random() >= cfg.spot_check_probability:
            return None

        txn = random.choice(processed_transactions)

        return {
            "item_id": f"verify-{txn.get('transaction_id', 'unknown')}-{int(_time.time())}",
            "item_type": "verification_check",
            "transaction_id": txn.get("transaction_id"),
            "vendor": txn.get("vendor", "Unknown"),
            "amount": txn.get("amount", Decimal("0")),
            "description": (
                "Verificación de clasificación: revise si esta transacción fue "
                "clasificada correctamente por el sistema."
            ),
            "consequence_level": self.get_approval_level(txn),
            "status": "pending_review",
            # Hidden internal flag — NOT exposed to CPA in API response
            "is_verification": True,
            "expected_action": "approved",
        }

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
        Generate a friction challenge appropriate for the given level.

        LOW:
            {"type": "CLICK", "instruction": "Confirmar"}

        MEDIUM:
            {"type": "EXPAND", "instruction": "Expandir detalles primero",
             "detail_key": <item_id or transaction_id>}

        HIGH:
            {"type": "QUESTION", "question": <question>, "correct_answer": <answer>}

        The HIGH question is about the specific transaction so the CPA must
        actually read and understand it before answering.

        Args:
            item:  Transaction/review-item dict.
            level: "LOW", "MEDIUM", or "HIGH".

        Returns:
            Challenge dict with 'type' key and level-specific fields.
        """
        level = level.upper()

        if level == "LOW":
            return {
                "type": "CLICK",
                "instruction": "Confirmar",
            }

        if level == "MEDIUM":
            detail_key = item.get("item_id") or item.get("transaction_id") or "detail"
            return {
                "type": "EXPAND",
                "instruction": "Expandir detalles primero",
                "detail_key": str(detail_key),
            }

        # HIGH — must answer a question about the transaction
        amount = item.get("amount", "?")
        vendor = item.get("vendor", "desconocido")
        account = (
            item.get("account_code")
            or (item.get("detail") or {}).get("account_code", "?")
        )
        account_name = (
            item.get("account_name")
            or (item.get("detail") or {}).get("account_name", "cuenta contable")
        )

        # Deterministic question based on amount
        try:
            amount_dec = Decimal(str(amount))
            seed = int(amount_dec) % 4
        except Exception:
            seed = 0

        if seed == 0:
            question = f"¿Cuál es el monto total de esta transacción de '{vendor}'?"
            correct_answer = str(amount)
        elif seed == 1:
            question = f"¿A qué código de cuenta se clasificó esta transacción de '{vendor}'?"
            correct_answer = str(account)
        elif seed == 2:
            is_capital = False
            try:
                is_capital = Decimal(str(amount)) >= Decimal("2500.00")
            except Exception:
                pass
            question = (
                f"Esta transacción de '{vendor}' por ${amount}: "
                f"¿es gasto operacional o activo de capital?"
            )
            correct_answer = "activo de capital" if is_capital else "gasto operacional"
        else:
            question = f"¿Cuál es el nombre de la cuenta contable de esta transacción de '{vendor}'?"
            correct_answer = str(account_name)

        return {
            "type": "QUESTION",
            "question": question,
            "correct_answer": correct_answer,
            "hint": "Revise los detalles de la transacción antes de responder.",
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
