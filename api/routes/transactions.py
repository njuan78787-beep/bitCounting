# =============================================================================
# api/routes/transactions.py
# Transaction management endpoints for Bit-Counting.
#
# Endpoints:
#   GET  /api/v1/transactions                          — paginated list
#   POST /api/v1/transactions                          — manual creation
#   GET  /api/v1/transactions/{id}/journal-entries     — double-entry journal
#   GET  /api/v1/transactions/{id}/tax-analysis        — FISCAL PR output
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from ..schemas import (
    JournalEntryResponse,
    TaxAnalysisResponse,
    TransactionCreateRequest,
    TransactionListResponse,
    TransactionResponse,
    TransactionStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])

# ---------------------------------------------------------------------------
# In-memory transaction store (Phase 1)
# ---------------------------------------------------------------------------
_transaction_store: dict[str, dict] = {}


def _make_demo_transactions() -> None:
    """Seed a few demo transactions so the list endpoint is not always empty."""
    demos = [
        {
            "transaction_id": "txn-demo-001",
            "client_id": "client-demo-001",
            "vendor": "Costco Wholesale PR",
            "amount": Decimal("2450.00"),
            "date": "2026-03-15",
            "status": TransactionStatus.PROCESSED,
            "account_code": "5900",
            "account_name": "Gastos de Operacion General",
            "confidence": Decimal("0.91"),
            "created_at": datetime(2026, 3, 15, 10, 0, 0),
        },
        {
            "transaction_id": "txn-demo-002",
            "client_id": "client-demo-001",
            "vendor": "Dell Technologies",
            "amount": Decimal("3200.00"),
            "date": "2026-03-20",
            "status": TransactionStatus.PROCESSED,
            "account_code": "1500",
            "account_name": "Equipos de Computacion",
            "confidence": Decimal("0.88"),
            "created_at": datetime(2026, 3, 20, 14, 30, 0),
        },
        {
            "transaction_id": "txn-demo-003",
            "client_id": "client-demo-001",
            "vendor": "Hacienda PR",
            "amount": Decimal("1250.00"),
            "date": "2026-03-31",
            "status": TransactionStatus.PAUSED,
            "account_code": "2400",
            "account_name": "Impuestos por Pagar",
            "confidence": Decimal("0.55"),
            "created_at": datetime(2026, 3, 31, 9, 0, 0),
        },
    ]
    for demo in demos:
        _transaction_store[demo["transaction_id"]] = demo


_make_demo_transactions()


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=TransactionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List transactions with optional filters",
)
async def list_transactions(
    client_id: Optional[str] = Query(default=None, description="Filter by client UUID"),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by status: pending, processed, paused, rejected",
    ),
    date_from: Optional[str] = Query(
        default=None,
        description="ISO 8601 start date filter (YYYY-MM-DD)",
    ),
    date_to: Optional[str] = Query(
        default=None,
        description="ISO 8601 end date filter (YYYY-MM-DD)",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> TransactionListResponse:
    """
    Return a paginated list of transactions, optionally filtered by client,
    status, and date range.
    """
    items = list(_transaction_store.values())

    if client_id:
        items = [t for t in items if t.get("client_id") == client_id]
    if status_filter:
        items = [t for t in items if t.get("status") == status_filter]
    if date_from:
        items = [t for t in items if (t.get("date") or "") >= date_from]
    if date_to:
        items = [t for t in items if (t.get("date") or "") <= date_to]

    # Sort by created_at descending
    items.sort(key=lambda t: t.get("created_at", datetime.min), reverse=True)

    total = len(items)
    offset = (page - 1) * page_size
    page_items = items[offset : offset + page_size]

    return TransactionListResponse(
        items=[_dict_to_transaction_response(t) for t in page_items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Manually create a transaction",
)
async def create_transaction(
    request: TransactionCreateRequest,
) -> TransactionResponse:
    """
    Manually create a transaction record.  This skips the document intake
    pipeline and is intended for transactions entered directly (e.g., bank
    reconciliation corrections).
    """
    transaction_id = str(uuid.uuid4())
    now = datetime.utcnow()

    record = {
        "transaction_id": transaction_id,
        "client_id": request.client_id,
        "vendor": request.vendor,
        "amount": request.amount,
        "date": request.date,
        "status": TransactionStatus.PENDING,
        "account_code": None,
        "account_name": None,
        "confidence": None,
        "created_at": now,
        "updated_at": now,
    }
    _transaction_store[transaction_id] = record

    logger.info("Manual transaction %s created for client %s", transaction_id, request.client_id)
    return _dict_to_transaction_response(record)


@router.get(
    "/{transaction_id}/journal-entries",
    response_model=list[JournalEntryResponse],
    status_code=status.HTTP_200_OK,
    summary="Get double-entry journal entries for a transaction",
)
async def get_journal_entries(transaction_id: str) -> list[JournalEntryResponse]:
    """
    Return the double-entry journal entries generated by the CLASIFICADOR
    for the specified transaction.
    """
    txn = _transaction_store.get(transaction_id)
    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction '{transaction_id}' not found",
        )

    amount = txn.get("amount", Decimal("0.00"))
    account_code = txn.get("account_code") or "5900"
    account_name = txn.get("account_name") or "Gastos de Operacion General"
    created_at = txn.get("created_at", datetime.utcnow())

    # Phase 1: generate a synthetic balanced journal entry
    entries = [
        JournalEntryResponse(
            entry_id=str(uuid.uuid4()),
            transaction_id=transaction_id,
            account_code=account_code,
            account_name=account_name,
            entry_type="debit",
            amount=amount,
            rule_ref="GAAP-EXPENSE-RECOGNITION",
            reasoning=(
                f"Debit to {account_name} ({account_code}) to record the expense "
                f"of ${amount} per GAAP expense recognition principle."
            ),
            contra_account_code="1000",
            contra_account_name="Banco / Efectivo",
            created_at=created_at,
        ),
        JournalEntryResponse(
            entry_id=str(uuid.uuid4()),
            transaction_id=transaction_id,
            account_code="1000",
            account_name="Banco / Efectivo",
            entry_type="credit",
            amount=amount,
            rule_ref="GAAP-CASH-DISBURSEMENT",
            reasoning=(
                f"Credit to Banco/Efectivo (1000) to record cash disbursement "
                f"of ${amount} corresponding to the debit entry."
            ),
            contra_account_code=account_code,
            contra_account_name=account_name,
            created_at=created_at,
        ),
    ]
    return entries


@router.get(
    "/{transaction_id}/tax-analysis",
    response_model=TaxAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Get FISCAL PR tax analysis for a transaction",
)
async def get_tax_analysis(transaction_id: str) -> TaxAnalysisResponse:
    """
    Return the Puerto Rico tax analysis produced by the FISCAL PR agent
    for the specified transaction, including applicable form reference.
    """
    txn = _transaction_store.get(transaction_id)
    if txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction '{transaction_id}' not found",
        )

    amount = txn.get("amount", Decimal("0.00"))
    txn_date = txn.get("date", "2026-01-01")
    year = txn_date[:4] if txn_date else "2026"

    # Phase 1: synthetic IVU calculation at 10.5% state rate
    ivu_rate = Decimal("0.105")
    taxable_base = amount
    tax_liability = (taxable_base * ivu_rate).quantize(Decimal("0.01"))

    import hashlib
    calc_str = f"{taxable_base}*{ivu_rate}={tax_liability}"
    calc_hash = hashlib.sha256(calc_str.encode()).hexdigest()[:16]

    return TaxAnalysisResponse(
        transaction_id=transaction_id,
        tax_type="IVU",
        tax_liability=tax_liability,
        taxable_base=taxable_base,
        form_id="PLANILLA_MENSUAL_IVU",
        rule_ref="IVU-PR-2021-001",
        rate_version="IVU-PR-2021-001:2021-07-01",
        calc_hash=calc_hash,
        period_from=f"{year}-01-01",
        period_to=f"{year}-12-31",
        exemptions_applied=[],
    )


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _dict_to_transaction_response(t: dict) -> TransactionResponse:
    return TransactionResponse(
        transaction_id=t["transaction_id"],
        client_id=t["client_id"],
        vendor=t.get("vendor"),
        amount=t["amount"],
        date=t.get("date"),
        status=t["status"],
        account_code=t.get("account_code"),
        account_name=t.get("account_name"),
        confidence=t.get("confidence"),
        created_at=t.get("created_at", datetime.utcnow()),
        updated_at=t.get("updated_at"),
    )
