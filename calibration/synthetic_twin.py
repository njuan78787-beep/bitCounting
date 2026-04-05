# =============================================================================
# calibration/synthetic_twin.py
# Synthetic Calibration Twin for Bit-Counting.
#
# PURPOSE:
#   Solves the "circular calibration" problem (Blueprint Problem 1):
#   you cannot calibrate a system using the same data it will later process
#   without a ground-truth reference.
#
#   This module provides:
#     1. CALIBRATION_DATASET — 20 synthetic Puerto Rico transactions where
#        the correct classification is known with certainty (human expert-
#        verified, not derived from the model being calibrated).
#     2. SyntheticCalibrationTwin — runs the CLASIFICADOR agent against the
#        dataset and measures accuracy, precision, and recall per category.
#     3. CalibrationResult — frozen Pydantic model with the full breakdown.
#
# DESIGN:
#   - The calibration twin is run BEFORE production launch.
#   - If accuracy_rate < 0.85 the system is NOT marked ready for production.
#   - Each wrong classification feeds into the ErrorCardSystem (error_cards.py).
#   - The confidence threshold recommended here is used to configure CENTINELA.
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CALIBRATION DATASET
# 20 synthetic PR transactions with known correct classifications.
# Human expert-verified — do NOT modify without CPA sign-off.
# ---------------------------------------------------------------------------

CALIBRATION_DATASET: list[dict[str, Any]] = [
    # 1. Supplies purchase below $2,500 capitalization threshold
    {
        "id": "cal-001",
        "vendor": "Costco Wholesale PR",
        "amount": "2450.00",
        "type": "expense",
        "correct_account": "5900",
        "correct_account_name": "Gastos de Operacion General",
        "is_capital": False,
        "note": "Below $2,500 threshold — expense immediately per PR-CAP-THRESHOLD-2500",
        "category": "general_expense",
    },
    # 2. Computer equipment above $2,500 capitalization threshold
    {
        "id": "cal-002",
        "vendor": "Dell Technologies",
        "amount": "3200.00",
        "type": "expense",
        "correct_account": "1500",
        "correct_account_name": "Equipos de Computacion",
        "is_capital": True,
        "note": "Above $2,500 threshold — capitalize per PR-CAP-THRESHOLD-2500",
        "category": "capital_asset",
    },
    # 3. Hacienda PR tax payment
    {
        "id": "cal-003",
        "vendor": "Hacienda PR",
        "amount": "1250.00",
        "type": "tax_payment",
        "correct_account": "2400",
        "correct_account_name": "Impuestos por Pagar",
        "is_capital": False,
        "note": "Tax liability payment to Hacienda PR",
        "category": "tax_payment",
    },
    # 4. Office rent at exactly $2,500 (boundary — not capital)
    {
        "id": "cal-004",
        "vendor": "Renta Oficina Centro",
        "amount": "2500.00",
        "type": "expense",
        "correct_account": "5200",
        "correct_account_name": "Renta y Arrendamiento",
        "is_capital": False,
        "note": "Rent is NEVER capitalized regardless of amount",
        "category": "rent",
    },
    # 5. Payroll
    {
        "id": "cal-005",
        "vendor": "Nomina Empleados",
        "amount": "15000.00",
        "type": "payroll",
        "correct_account": "5100",
        "correct_account_name": "Sueldos y Salarios",
        "is_capital": False,
        "note": "Payroll expense — always expense, never capitalize",
        "category": "payroll",
    },
    # 6. IVU monthly payment to Hacienda
    {
        "id": "cal-006",
        "vendor": "Hacienda PR — IVU Mensual",
        "amount": "4725.00",
        "type": "tax_payment",
        "correct_account": "2410",
        "correct_account_name": "IVU por Pagar",
        "is_capital": False,
        "note": "Monthly IVU remittance — credit to IVU payable account",
        "category": "ivu_payment",
    },
    # 7. Electric utility
    {
        "id": "cal-007",
        "vendor": "AEE — Autoridad de Energia Electrica",
        "amount": "850.00",
        "type": "expense",
        "correct_account": "5300",
        "correct_account_name": "Servicios Publicos",
        "is_capital": False,
        "note": "Utility expense — always expense regardless of amount",
        "category": "utilities",
    },
    # 8. Professional fees (CPA services)
    {
        "id": "cal-008",
        "vendor": "Firma CPA Consulting Group",
        "amount": "3500.00",
        "type": "expense",
        "correct_account": "5400",
        "correct_account_name": "Servicios Profesionales",
        "is_capital": False,
        "note": "Professional fees — operating expense per GAAP",
        "category": "professional_fees",
    },
    # 9. COGS merchandise purchase
    {
        "id": "cal-009",
        "vendor": "Distribuidor Nacional PR",
        "amount": "8500.00",
        "type": "purchase",
        "correct_account": "5000",
        "correct_account_name": "Costo de Bienes Vendidos",
        "is_capital": False,
        "note": "Inventory purchase for resale — COGS account",
        "category": "cogs",
    },
    # 10. Bank loan principal payment
    {
        "id": "cal-010",
        "vendor": "Banco Popular PR",
        "amount": "5000.00",
        "type": "loan_payment",
        "correct_account": "2100",
        "correct_account_name": "Prestamos por Pagar",
        "is_capital": False,
        "note": "Loan principal repayment — debit liability, not expense",
        "category": "loan_payment",
    },
    # 11. Insurance premium
    {
        "id": "cal-011",
        "vendor": "Triple-S Seguros PR",
        "amount": "1800.00",
        "type": "expense",
        "correct_account": "5500",
        "correct_account_name": "Seguros",
        "is_capital": False,
        "note": "Insurance expense — operating expense",
        "category": "insurance",
    },
    # 12. Advertising / marketing
    {
        "id": "cal-012",
        "vendor": "Agencia Publicidad PR",
        "amount": "2200.00",
        "type": "expense",
        "correct_account": "5600",
        "correct_account_name": "Publicidad y Mercadeo",
        "is_capital": False,
        "note": "Advertising expense — cannot capitalize advertising costs (GAAP ASC 720-35)",
        "category": "advertising",
    },
    # 13. Business travel
    {
        "id": "cal-013",
        "vendor": "JetBlue Airways",
        "amount": "650.00",
        "type": "expense",
        "correct_account": "5700",
        "correct_account_name": "Gastos de Viaje",
        "is_capital": False,
        "note": "Business travel — ordinary business expense",
        "category": "travel",
    },
    # 14. Vehicle above $2,500 (capital asset)
    {
        "id": "cal-014",
        "vendor": "Toyota de Puerto Rico",
        "amount": "25000.00",
        "type": "purchase",
        "correct_account": "1600",
        "correct_account_name": "Vehiculos",
        "is_capital": True,
        "note": "Vehicle purchase — capitalize; subject to Section 179 / PR depreciation rules",
        "category": "capital_asset",
    },
    # 15. Water utility
    {
        "id": "cal-015",
        "vendor": "AAA — Autoridad Acueductos PR",
        "amount": "220.00",
        "type": "expense",
        "correct_account": "5300",
        "correct_account_name": "Servicios Publicos",
        "is_capital": False,
        "note": "Utility expense — water service",
        "category": "utilities",
    },
    # 16. Furniture above $2,500 (capital asset)
    {
        "id": "cal-016",
        "vendor": "Office Depot PR",
        "amount": "4200.00",
        "type": "purchase",
        "correct_account": "1400",
        "correct_account_name": "Mobiliario y Equipo de Oficina",
        "is_capital": True,
        "note": "Furniture above $2,500 threshold — capitalize",
        "category": "capital_asset",
    },
    # 17. Small office supplies (below threshold)
    {
        "id": "cal-017",
        "vendor": "Staples PR",
        "amount": "185.00",
        "type": "expense",
        "correct_account": "5800",
        "correct_account_name": "Materiales y Suministros",
        "is_capital": False,
        "note": "Office supplies — small amount, expense immediately",
        "category": "supplies",
    },
    # 18. Patron municipal license (CRIM)
    {
        "id": "cal-018",
        "vendor": "CRIM — Licencia Municipal",
        "amount": "750.00",
        "type": "tax_payment",
        "correct_account": "5900",
        "correct_account_name": "Gastos de Operacion General",
        "is_capital": False,
        "note": "Municipal license fee — operating expense",
        "category": "general_expense",
    },
    # 19. Loan interest payment (NOT principal — this is expense)
    {
        "id": "cal-019",
        "vendor": "Banco Popular PR — Intereses",
        "amount": "320.00",
        "type": "interest_payment",
        "correct_account": "7100",
        "correct_account_name": "Gastos de Intereses",
        "is_capital": False,
        "note": "Interest expense — debit to interest expense, not loan liability",
        "category": "interest_expense",
    },
    # 20. Software subscription (SaaS — operating expense, not capital)
    {
        "id": "cal-020",
        "vendor": "Microsoft Office 365",
        "amount": "1200.00",
        "type": "expense",
        "correct_account": "5900",
        "correct_account_name": "Gastos de Operacion General",
        "is_capital": False,
        "note": "SaaS subscription — operating expense per ASC 350-40; not capitalized",
        "category": "general_expense",
    },
]


# ---------------------------------------------------------------------------
# CLASIFICADOR PROTOCOL — duck typing for calibration
# ---------------------------------------------------------------------------

class ClasificadorProtocol(Protocol):
    """
    Duck-type interface for any CLASIFICADOR agent implementation.
    The calibration twin calls classify() and expects a dict back.
    """
    def classify(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """
        Classify a transaction and return a dict with at least:
          {"account_code": str, "confidence": Decimal, "is_capital": bool}
        """
        ...


# ---------------------------------------------------------------------------
# CALIBRATION RESULT
# ---------------------------------------------------------------------------

class CalibrationResult(BaseModel):
    """
    Frozen result of a calibration run against the synthetic dataset.

    Fields:
      accuracy_rate                — overall correct / total
      errors_by_category           — dict mapping category → error count
      recommended_confidence_threshold — suggested CENTINELA threshold
      is_ready_for_production      — True only if accuracy_rate >= 0.85
    """
    model_config = ConfigDict(frozen=True)

    calibration_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    total_transactions: int
    correct_classifications: int
    accuracy_rate: Decimal = Field(ge=Decimal("0.00"), le=Decimal("1.00"))
    precision_by_category: dict[str, Decimal]
    recall_by_category: dict[str, Decimal]
    errors_by_category: dict[str, int]
    wrong_classifications: tuple[dict[str, Any], ...]
    recommended_confidence_threshold: Decimal = Field(
        ge=Decimal("0.00"), le=Decimal("1.00")
    )
    calibration_timestamp: datetime
    is_ready_for_production: bool  # True if accuracy_rate >= 0.85


# ---------------------------------------------------------------------------
# SYNTHETIC CALIBRATION TWIN
# ---------------------------------------------------------------------------

class SyntheticCalibrationTwin:
    """
    Calibration system for the CLASIFICADOR agent.

    Usage:
        twin = SyntheticCalibrationTwin()
        result = twin.run_calibration(my_clasificador_agent)
        if not result.is_ready_for_production:
            # review result.wrong_classifications and result.errors_by_category
    """

    PRODUCTION_ACCURACY_THRESHOLD = Decimal("0.85")
    # Confidence threshold recommended when accuracy is high
    HIGH_ACCURACY_CONFIDENCE = Decimal("0.75")
    # Confidence threshold recommended when accuracy is borderline
    BORDERLINE_CONFIDENCE = Decimal("0.80")

    def __init__(self) -> None:
        self._dataset = CALIBRATION_DATASET

    @property
    def dataset_size(self) -> int:
        return len(self._dataset)

    def run_calibration(
        self,
        clasificador_agent: Any,
        *,
        verbose: bool = False,
    ) -> CalibrationResult:
        """
        Run all 20 calibration transactions through the agent and compare
        results against known correct answers.

        Args:
            clasificador_agent: Any object with a classify(dict) -> dict method.
                                 Uses the ClasificadorProtocol duck type.
            verbose:            If True, logs each transaction result.

        Returns:
            CalibrationResult with full accuracy breakdown.
        """
        correct = 0
        wrong_classifications: list[dict[str, Any]] = []

        # category → {"tp": int, "fp": int, "fn": int}
        category_stats: dict[str, dict[str, int]] = {}

        for txn in self._dataset:
            category = txn["category"]
            if category not in category_stats:
                category_stats[category] = {"tp": 0, "fp": 0, "fn": 0}

            try:
                result = clasificador_agent.classify(txn)
            except Exception as exc:
                logger.warning(
                    "Clasificador raised exception on cal-id %s: %s", txn["id"], exc
                )
                result = {"account_code": "ERROR", "confidence": Decimal("0.00"), "is_capital": False}

            predicted_account = str(result.get("account_code", "")).strip()
            expected_account = str(txn["correct_account"]).strip()
            is_correct = predicted_account == expected_account

            if verbose:
                status_symbol = "OK" if is_correct else "WRONG"
                logger.info(
                    "[%s] %s | expected=%s got=%s | vendor=%s amount=%s",
                    status_symbol,
                    txn["id"],
                    expected_account,
                    predicted_account,
                    txn["vendor"],
                    txn["amount"],
                )

            if is_correct:
                correct += 1
                category_stats[category]["tp"] += 1
            else:
                category_stats[category]["fn"] += 1
                # The predicted category (if we can determine it)
                predicted_category = self._account_to_category(predicted_account)
                if predicted_category and predicted_category != category:
                    if predicted_category not in category_stats:
                        category_stats[predicted_category] = {"tp": 0, "fp": 0, "fn": 0}
                    category_stats[predicted_category]["fp"] += 1

                wrong_classifications.append({
                    "cal_id": txn["id"],
                    "vendor": txn["vendor"],
                    "amount": txn["amount"],
                    "expected_account": expected_account,
                    "predicted_account": predicted_account,
                    "category": category,
                    "note": txn["note"],
                })

        total = len(self._dataset)
        accuracy_rate = Decimal(str(correct / total)).quantize(Decimal("0.0001"))

        # Precision and recall per category
        precision_by_category: dict[str, Decimal] = {}
        recall_by_category: dict[str, Decimal] = {}
        errors_by_category: dict[str, int] = {}

        for cat, stats in category_stats.items():
            tp = stats["tp"]
            fp = stats["fp"]
            fn = stats["fn"]
            errors_by_category[cat] = fn

            prec_denom = tp + fp
            precision_by_category[cat] = (
                Decimal(str(tp / prec_denom)).quantize(Decimal("0.0001"))
                if prec_denom > 0 else Decimal("0.0000")
            )
            rec_denom = tp + fn
            recall_by_category[cat] = (
                Decimal(str(tp / rec_denom)).quantize(Decimal("0.0001"))
                if rec_denom > 0 else Decimal("0.0000")
            )

        # Recommend a confidence threshold based on accuracy
        if accuracy_rate >= Decimal("0.90"):
            recommended_threshold = self.HIGH_ACCURACY_CONFIDENCE
        else:
            recommended_threshold = self.BORDERLINE_CONFIDENCE

        is_ready = accuracy_rate >= self.PRODUCTION_ACCURACY_THRESHOLD

        result = CalibrationResult(
            total_transactions=total,
            correct_classifications=correct,
            accuracy_rate=accuracy_rate,
            precision_by_category=precision_by_category,
            recall_by_category=recall_by_category,
            errors_by_category=errors_by_category,
            wrong_classifications=tuple(wrong_classifications),
            recommended_confidence_threshold=recommended_threshold,
            calibration_timestamp=datetime.now(timezone.utc),
            is_ready_for_production=is_ready,
        )

        logger.info(
            "Calibration complete: %d/%d correct (%.1f%%) — ready_for_production=%s",
            correct, total, float(accuracy_rate) * 100, is_ready,
        )
        return result

    @staticmethod
    def _account_to_category(account_code: str) -> Optional[str]:
        """
        Reverse-map an account code to its rough category for F/P calculation.
        """
        mapping = {
            "5900": "general_expense",
            "1500": "capital_asset",
            "1400": "capital_asset",
            "1600": "capital_asset",
            "2400": "tax_payment",
            "2410": "ivu_payment",
            "5200": "rent",
            "5100": "payroll",
            "5300": "utilities",
            "5400": "professional_fees",
            "5000": "cogs",
            "2100": "loan_payment",
            "5500": "insurance",
            "5600": "advertising",
            "5700": "travel",
            "5800": "supplies",
            "7100": "interest_expense",
        }
        return mapping.get(account_code)


# ---------------------------------------------------------------------------
# Convenience functions for use by calibration routes and tests
# ---------------------------------------------------------------------------

def get_synthetic_transactions() -> list[dict]:
    """
    Return the full calibration dataset as a plain list of dicts.

    Each dict contains all fields needed to construct an IntakeOutput and
    verify expected pipeline outcomes:
      - id, vendor, amount, type, correct_account, correct_account_name
      - is_capital, note, category

    Returns:
        List of 20 transaction dicts (copy of CALIBRATION_DATASET).
    """
    return list(CALIBRATION_DATASET)


def run_calibration_suite() -> dict:
    """
    Run a lightweight calibration pass against the ClasificadorAgent.

    Uses the existing SyntheticCalibrationTwin and the real ClasificadorAgent.
    Wraps the result into a simple pass/fail dict for API and test consumption.

    Returns:
        dict with keys:
          - total:   int — number of transactions in the dataset
          - passed:  int — correctly classified
          - failed:  int — misclassified
          - errors:  int — unexpected exceptions during classification
          - details: list[dict] — per-transaction result
          - summary: str — human-readable summary line
    """
    from agents.clasificador import ClasificadorAgent

    twin = SyntheticCalibrationTwin()

    # Adapt ClasificadorAgent to the ClasificadorProtocol duck type.
    # classify() in ClasificadorAgent takes an IntakeOutput and returns a tuple;
    # here we build a minimal adapter that returns the expected dict shape.
    class _AgentAdapter:
        def __init__(self, agent: Any) -> None:
            self._agent = agent

        def classify(self, txn: dict[str, Any]) -> dict[str, Any]:
            """
            Build a minimal IntakeOutput-like dict call and invoke classify().
            Returns {"account_code": str, "confidence": Decimal, "is_capital": bool}.
            """
            import uuid as _uuid
            from datetime import datetime as _dt, timezone as _tz
            from agents.messages import IntakeOutput, SourceFormat

            amount = Decimal(str(txn.get("amount", "0")))
            intake = IntakeOutput(
                message_id=str(_uuid.uuid4()),
                timestamp=_dt.now(_tz.utc),
                source_agent="CALIBRATION",
                target_agent="CENTINELA",
                vendor=txn.get("vendor"),
                date="2026-01-01",
                amount=amount,
                tax_amount=None,
                currency="USD",
                payment_method=None,
                confidence=Decimal("0.90"),
                missing_fields=(),
                critical_fields_missing=False,
                source_format=SourceFormat.JSON,
            )
            try:
                debit, _credit = self._agent.classify(intake)
                is_capital = debit.account_code.startswith("1")
                return {
                    "account_code": debit.account_code,
                    "confidence": debit.confidence,
                    "is_capital": is_capital,
                }
            except Exception as exc:
                return {
                    "account_code": "ERROR",
                    "confidence": Decimal("0.00"),
                    "is_capital": False,
                    "_error": str(exc),
                }

    adapter = _AgentAdapter(ClasificadorAgent())
    result = twin.run_calibration(adapter)

    details = []
    total = result.total_transactions
    passed = result.correct_classifications
    failed = total - passed - sum(
        1 for w in result.wrong_classifications
        if w.get("predicted_account") == "ERROR"
    )
    errors = sum(
        1 for w in result.wrong_classifications
        if w.get("predicted_account") == "ERROR"
    )

    for txn in CALIBRATION_DATASET:
        wrong_match = next(
            (w for w in result.wrong_classifications if w["cal_id"] == txn["id"]),
            None,
        )
        if wrong_match:
            status = "ERROR" if wrong_match["predicted_account"] == "ERROR" else "FAIL"
            reason = (
                f"Expected {wrong_match['expected_account']}, "
                f"got {wrong_match['predicted_account']}"
            )
        else:
            status = "PASS"
            reason = f"Correctly classified as {txn['correct_account']}"

        details.append({
            "txn": f"{txn['id']} {txn['vendor']}",
            "result": status,
            "reason": reason,
        })

    summary = (
        f"Calibration suite: {total} transactions | "
        f"PASSED: {passed} | FAILED: {failed} | ERRORS: {errors} | "
        f"Accuracy: {float(result.accuracy_rate)*100:.1f}% | "
        f"Production-ready: {result.is_ready_for_production}"
    )

    return {
        "total":   total,
        "passed":  passed,
        "failed":  failed,
        "errors":  errors,
        "details": details,
        "summary": summary,
        "accuracy_rate": str(result.accuracy_rate),
        "is_ready_for_production": result.is_ready_for_production,
        "recommended_confidence_threshold": str(result.recommended_confidence_threshold),
    }
