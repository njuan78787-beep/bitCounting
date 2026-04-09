# =============================================================================
# api/routes/transactions.py
# Transaction management endpoints for Bit-Counting.
#
# Storage: PostgreSQL (app_transactions) via SQLAlchemy async.
#          Falls back to in-memory dict when DATABASE_URL is not set.
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import TokenUser, Role
from ..database import get_db
from ..dependencies import get_current_user, rate_limit, verify_client_scope
from ..db.models import AppTransaction
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


class DocumentUploadRequest(BaseModel):
    raw_text:      str = Field(min_length=1, description="Texto del documento")
    client_id:     str = Field(min_length=1, description="UUID del cliente")
    source_format: str = Field(default="UNKNOWN", description="PDF, CSV, JSON, etc.")


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=TransactionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Subir documento e iniciar flujo ORQUESTADOR",
)
async def upload_document(
    body:  DocumentUploadRequest,
    user:  TokenUser = Depends(get_current_user),
    _rate: None      = Depends(rate_limit("transactions:upload")),
    db:    AsyncSession = Depends(get_db),
) -> TransactionResponse:
    verify_client_scope(user, body.client_id)

    transaction_id = str(uuid.uuid4())
    now = datetime.utcnow()

    row = AppTransaction(
        transaction_id=transaction_id,
        client_id=body.client_id,
        vendor=None,
        amount=Decimal("0.00"),
        date=now.date().isoformat(),
        status="pending",
        source_format=body.source_format,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    logger.info("UPLOAD: transacción %s iniciada para cliente %s", transaction_id, body.client_id)
    return _row_to_response(row)


@router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    status_code=status.HTTP_200_OK,
    summary="Estado y trace completo de una transacción",
)
async def get_transaction(
    transaction_id: str,
    user:           TokenUser = Depends(get_current_user),
    db:             AsyncSession = Depends(get_db),
) -> TransactionResponse:
    row = await _get_or_404(db, transaction_id)
    verify_client_scope(user, row.client_id)
    return _row_to_response(row)


@router.get(
    "",
    response_model=TransactionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List transactions with optional filters",
)
async def list_transactions(
    client_id: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> TransactionListResponse:
    filters = []

    # Scope: CLIENT only sees their own data
    if client_id:
        verify_client_scope(user, client_id)
        filters.append(AppTransaction.client_id == client_id)
    elif user.role == Role.CLIENT and user.client_id:
        filters.append(AppTransaction.client_id == user.client_id)

    if status_filter:
        filters.append(AppTransaction.status == status_filter)
    if date_from:
        filters.append(AppTransaction.date >= date_from)
    if date_to:
        filters.append(AppTransaction.date <= date_to)

    stmt = select(AppTransaction)
    if filters:
        stmt = stmt.where(and_(*filters))
    stmt = stmt.order_by(AppTransaction.created_at.desc())

    result = await db.execute(stmt)
    all_rows = result.scalars().all()
    total = len(all_rows)

    offset = (page - 1) * page_size
    page_rows = all_rows[offset: offset + page_size]

    return TransactionListResponse(
        items=[_row_to_response(r) for r in page_rows],
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
    user:    TokenUser = Depends(get_current_user),
    db:      AsyncSession = Depends(get_db),
) -> TransactionResponse:
    verify_client_scope(user, request.client_id)

    row = AppTransaction(
        transaction_id=str(uuid.uuid4()),
        client_id=request.client_id,
        vendor=request.vendor,
        amount=request.amount,
        date=request.date,
        status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    logger.info("Manual transaction %s created for client %s", row.transaction_id, request.client_id)
    return _row_to_response(row)


@router.get(
    "/{transaction_id}/journal-entries",
    response_model=list[JournalEntryResponse],
    status_code=status.HTTP_200_OK,
    summary="Get double-entry journal entries for a transaction",
)
async def get_journal_entries(
    transaction_id: str,
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> list[JournalEntryResponse]:
    row = await _get_or_404(db, transaction_id)
    verify_client_scope(user, row.client_id)

    amount       = row.amount or Decimal("0.00")
    account_code = row.account_code or "5900"
    account_name = row.account_name or "Gastos de Operacion General"
    created_at   = row.created_at or datetime.utcnow()

    return [
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


@router.get(
    "/{transaction_id}/tax-analysis",
    response_model=TaxAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Get FISCAL PR tax analysis for a transaction",
)
async def get_tax_analysis(
    transaction_id: str,
    user: TokenUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_db),
) -> TaxAnalysisResponse:
    row = await _get_or_404(db, transaction_id)
    verify_client_scope(user, row.client_id)

    import hashlib
    amount       = row.amount or Decimal("0.00")
    txn_date     = row.date or "2026-01-01"
    year         = txn_date[:4]
    ivu_rate     = Decimal("0.105")
    tax_liability = (amount * ivu_rate).quantize(Decimal("0.01"))
    calc_str     = f"{amount}*{ivu_rate}={tax_liability}"
    calc_hash    = hashlib.sha256(calc_str.encode()).hexdigest()[:16]

    return TaxAnalysisResponse(
        transaction_id=transaction_id,
        tax_type="IVU",
        tax_liability=tax_liability,
        taxable_base=amount,
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

async def _get_or_404(db: AsyncSession, transaction_id: str) -> AppTransaction:
    result = await db.execute(
        select(AppTransaction).where(AppTransaction.transaction_id == transaction_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transacción '{transaction_id}' no encontrada",
        )
    return row


def _row_to_response(r: AppTransaction) -> TransactionResponse:
    # Map DB status strings to TransactionStatus enum values
    _STATUS_MAP = {
        "pending":     TransactionStatus.PENDING,
        "processed":   TransactionStatus.PROCESSED,
        "paused":      TransactionStatus.PAUSED,
        "rejected":    TransactionStatus.REJECTED,
        "posted":      TransactionStatus.PROCESSED,
        "under_review": TransactionStatus.PAUSED,
        "voided":      TransactionStatus.REJECTED,
    }
    txn_status = _STATUS_MAP.get(r.status, TransactionStatus.PENDING)

    return TransactionResponse(
        transaction_id=r.transaction_id,
        client_id=r.client_id,
        vendor=r.vendor,
        amount=r.amount or Decimal("0.00"),
        date=r.date,
        status=txn_status,
        account_code=r.account_code,
        account_name=r.account_name,
        confidence=r.confidence,
        created_at=r.created_at or datetime.utcnow(),
        updated_at=r.updated_at,
    )
