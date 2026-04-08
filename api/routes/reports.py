# =============================================================================
# api/routes/reports.py
# Financial report endpoints for Bit-Counting.
#
# Generates balance sheet, income statement, cash flow, and IVU summary.
# Phase 1: returns synthetic structured reports.
# Phase 2: aggregates from journal_entries and transactions tables.
#
# Endpoints:
#   GET /api/v1/reports/balance-sheet
#   GET /api/v1/reports/income-statement
#   GET /api/v1/reports/cash-flow
#   GET /api/v1/reports/ivu-summary
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import TokenUser
from ..dependencies import get_current_user, verify_client_scope
from ..schemas import (
    BalanceSheetResponse,
    CashFlowResponse,
    IncomeStatementResponse,
    IVUSummaryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@router.get(
    "/balance-sheet",
    response_model=BalanceSheetResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate balance sheet as of a given date",
)
async def get_balance_sheet(
    client_id: str = Query(..., description="Client UUID"),
    as_of_date: Optional[str] = Query(
        default=None,
        description="ISO 8601 date (YYYY-MM-DD). Defaults to today.",
    ),
    user: TokenUser = Depends(get_current_user),
) -> BalanceSheetResponse:
    """
    Return a balance sheet for the specified client as of the given date.
    Follows the GAAP accounting equation: Assets = Liabilities + Equity.
    """
    _require_client(client_id)
    verify_client_scope(user, client_id)
    effective_date = as_of_date or datetime.utcnow().date().isoformat()

    # Phase 1: synthetic balanced balance sheet
    assets = {
        "current_assets": {
            "cash_and_equivalents": Decimal("45000.00"),
            "accounts_receivable": Decimal("18500.00"),
            "inventory": Decimal("12000.00"),
            "prepaid_expenses": Decimal("2500.00"),
            "total_current_assets": Decimal("78000.00"),
        },
        "non_current_assets": {
            "property_plant_equipment": Decimal("95000.00"),
            "accumulated_depreciation": Decimal("-22000.00"),
            "intangible_assets": Decimal("5000.00"),
            "total_non_current_assets": Decimal("78000.00"),
        },
    }
    total_assets = Decimal("156000.00")

    liabilities = {
        "current_liabilities": {
            "accounts_payable": Decimal("14000.00"),
            "accrued_expenses": Decimal("8000.00"),
            "ivu_payable": Decimal("3500.00"),
            "current_portion_long_term_debt": Decimal("5000.00"),
            "total_current_liabilities": Decimal("30500.00"),
        },
        "long_term_liabilities": {
            "long_term_debt": Decimal("45000.00"),
            "deferred_tax_liability": Decimal("2500.00"),
            "total_long_term_liabilities": Decimal("47500.00"),
        },
    }
    total_liabilities = Decimal("78000.00")

    equity = {
        "common_stock": Decimal("50000.00"),
        "retained_earnings": Decimal("28000.00"),
        "total_equity": Decimal("78000.00"),
    }
    total_equity = Decimal("78000.00")

    # Verify accounting equation
    assert total_assets == total_liabilities + total_equity, "Balance sheet does not balance"

    return BalanceSheetResponse(
        client_id=client_id,
        as_of_date=effective_date,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        generated_at=datetime.utcnow(),
    )


@router.get(
    "/income-statement",
    response_model=IncomeStatementResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate income statement for a period",
)
async def get_income_statement(
    client_id: str = Query(..., description="Client UUID"),
    period_start: str = Query(..., description="ISO 8601 start date"),
    period_end: str = Query(..., description="ISO 8601 end date"),
    user: TokenUser = Depends(get_current_user),
) -> IncomeStatementResponse:
    """
    Return a profit & loss statement for the specified client and period.
    """
    _require_client(client_id)
    verify_client_scope(user, client_id)
    _validate_period(period_start, period_end)

    revenue = {
        "service_revenue": Decimal("85000.00"),
        "product_sales": Decimal("42000.00"),
        "other_revenue": Decimal("3000.00"),
        "total_revenue": Decimal("130000.00"),
    }

    cost_of_goods_sold = {
        "materials": Decimal("28000.00"),
        "direct_labor": Decimal("18000.00"),
        "overhead": Decimal("6000.00"),
        "total_cogs": Decimal("52000.00"),
    }
    gross_profit = Decimal("78000.00")

    operating_expenses = {
        "salaries_and_wages": Decimal("35000.00"),
        "rent": Decimal("12000.00"),
        "utilities": Decimal("3500.00"),
        "insurance": Decimal("2000.00"),
        "advertising": Decimal("4500.00"),
        "professional_fees": Decimal("6000.00"),
        "depreciation": Decimal("4400.00"),
        "total_operating_expenses": Decimal("67400.00"),
    }
    operating_income = Decimal("10600.00")

    other_income_expense = {
        "interest_income": Decimal("200.00"),
        "interest_expense": Decimal("-1800.00"),
        "total_other": Decimal("-1600.00"),
    }
    net_income = Decimal("9000.00")

    return IncomeStatementResponse(
        client_id=client_id,
        period_start=period_start,
        period_end=period_end,
        revenue=revenue,
        cost_of_goods_sold=cost_of_goods_sold,
        gross_profit=gross_profit,
        operating_expenses=operating_expenses,
        operating_income=operating_income,
        other_income_expense=other_income_expense,
        net_income=net_income,
        generated_at=datetime.utcnow(),
    )


@router.get(
    "/cash-flow",
    response_model=CashFlowResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate cash flow statement for a period",
)
async def get_cash_flow(
    client_id: str = Query(..., description="Client UUID"),
    period_start: str = Query(..., description="ISO 8601 start date"),
    period_end: str = Query(..., description="ISO 8601 end date"),
    user: TokenUser = Depends(get_current_user),
) -> CashFlowResponse:
    """
    Return a cash flow statement (indirect method) for the specified period.
    """
    _require_client(client_id)
    verify_client_scope(user, client_id)
    _validate_period(period_start, period_end)

    operating_activities = {
        "net_income": Decimal("9000.00"),
        "adjustments": {
            "depreciation": Decimal("4400.00"),
            "change_in_accounts_receivable": Decimal("-2500.00"),
            "change_in_inventory": Decimal("-1200.00"),
            "change_in_accounts_payable": Decimal("1800.00"),
            "change_in_accrued_expenses": Decimal("500.00"),
        },
        "net_cash_from_operations": Decimal("12000.00"),
    }

    investing_activities = {
        "purchase_of_equipment": Decimal("-8500.00"),
        "proceeds_from_asset_sale": Decimal("0.00"),
        "net_cash_from_investing": Decimal("-8500.00"),
    }

    financing_activities = {
        "loan_repayments": Decimal("-5000.00"),
        "owner_distributions": Decimal("-3000.00"),
        "net_cash_from_financing": Decimal("-8000.00"),
    }

    net_change = Decimal("-4500.00")
    beginning_cash = Decimal("49500.00")
    ending_cash = Decimal("45000.00")

    return CashFlowResponse(
        client_id=client_id,
        period_start=period_start,
        period_end=period_end,
        operating_activities=operating_activities,
        investing_activities=investing_activities,
        financing_activities=financing_activities,
        net_change_in_cash=net_change,
        beginning_cash=beginning_cash,
        ending_cash=ending_cash,
        generated_at=datetime.utcnow(),
    )


@router.get(
    "/ivu-summary",
    response_model=IVUSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="IVU collected vs remitted summary for SC 2915",
)
async def get_ivu_summary(
    client_id: str = Query(..., description="Client UUID"),
    period: str = Query(..., description="Period in YYYY-MM format"),
    user: TokenUser = Depends(get_current_user),
) -> IVUSummaryResponse:
    """
    Return a summary of IVU (Puerto Rico sales tax) collected from customers
    vs. remitted to Hacienda PR for the specified period.
    Used to prepare Form SC 2915 (Planilla Mensual de IVU).
    """
    _require_client(client_id)
    verify_client_scope(user, client_id)

    # Validate period format
    if not _is_valid_period(period):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid period format '{period}'. Expected YYYY-MM.",
        )

    # Phase 1: synthetic IVU data
    ivu_collected = Decimal("4725.00")
    ivu_remitted = Decimal("4725.00")
    ivu_balance = ivu_collected - ivu_remitted

    return IVUSummaryResponse(
        client_id=client_id,
        period=period,
        ivu_collected=ivu_collected,
        ivu_remitted=ivu_remitted,
        ivu_balance=ivu_balance,
        ivu_rate_municipal=Decimal("0.01"),
        ivu_rate_state=Decimal("0.105"),
        transactions_count=47,
        form_sc2915_ready=(ivu_balance == Decimal("0.00")),
        generated_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# SUMMARY AND CONFIDENCE DISTRIBUTION ENDPOINTS (spec-required)
# ---------------------------------------------------------------------------

@router.get(
    "/summary",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Aggregate stats: total docs processed, pauses, decisions",
)
async def get_summary() -> dict:
    """
    Return aggregate system statistics:
      - Total documents processed
      - Active and resolved pauses
      - Total Orchestrator decisions
      - System uptime info
    """
    from api.routes.cpa_dashboard import _pauses, _orchestrator_singleton
    from api.routes.documents import _document_store

    total_docs = len(_document_store)
    docs_processed = sum(
        1 for d in _document_store.values()
        if d.get("status") in ("processed", "paused")
    )
    active_pauses = sum(1 for p in _pauses.values() if p.get("status") == "ACTIVE")
    resolved_pauses = sum(1 for p in _pauses.values() if p.get("status") == "RESOLVED")
    total_decisions = _orchestrator_singleton.get_log_size()
    cpa_review_decisions = len(
        _orchestrator_singleton.get_decisions_requiring_cpa_review()
    )

    return {
        "total_documents_submitted": total_docs,
        "total_documents_processed": docs_processed,
        "active_pauses":             active_pauses,
        "resolved_pauses":           resolved_pauses,
        "total_pauses":              active_pauses + resolved_pauses,
        "total_orchestrator_decisions": total_decisions,
        "decisions_requiring_cpa_review": cpa_review_decisions,
        "generated_at":              datetime.utcnow().isoformat(),
    }


@router.get(
    "/confidence",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Confidence distribution stats across all processed documents",
)
async def get_confidence_distribution() -> dict:
    """
    Return confidence distribution statistics for processed documents.

    Buckets:
      - very_high:  confidence >= 0.90
      - high:       0.80 <= confidence < 0.90
      - medium:     0.70 <= confidence < 0.80
      - low:        0.60 <= confidence < 0.70
      - very_low:   confidence < 0.60  (these should all be PAUSED)
    """
    from api.routes.documents import _document_store

    buckets: dict[str, int] = {
        "very_high": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "very_low": 0,
    }
    confidences: list[float] = []

    for doc in _document_store.values():
        intake = doc.get("intake_result") or {}
        conf_str = intake.get("confidence")
        if conf_str is None:
            continue
        try:
            conf = float(conf_str)
        except (ValueError, TypeError):
            continue

        confidences.append(conf)
        if conf >= 0.90:
            buckets["very_high"] += 1
        elif conf >= 0.80:
            buckets["high"] += 1
        elif conf >= 0.70:
            buckets["medium"] += 1
        elif conf >= 0.60:
            buckets["low"] += 1
        else:
            buckets["very_low"] += 1

    total = len(confidences)
    avg_confidence = sum(confidences) / total if total else 0.0
    min_confidence = min(confidences) if confidences else 0.0
    max_confidence = max(confidences) if confidences else 0.0

    return {
        "total_documents_with_confidence": total,
        "average_confidence":  round(avg_confidence, 4),
        "min_confidence":      round(min_confidence, 4),
        "max_confidence":      round(max_confidence, 4),
        "distribution":        buckets,
        "distribution_pct": {
            k: round(v / total * 100, 1) if total else 0.0
            for k, v in buckets.items()
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _require_client(client_id: str) -> None:
    """Phase 1 stub: accepts any non-empty client_id."""
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_id is required",
        )


def _validate_period(period_start: str, period_end: str) -> None:
    """Validate that period_start <= period_end."""
    if period_start > period_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"period_start ({period_start}) must be <= period_end ({period_end})",
        )


def _is_valid_period(period: str) -> bool:
    """Check YYYY-MM format."""
    import re
    return bool(re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period))
