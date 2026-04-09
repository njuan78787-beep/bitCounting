# =============================================================================
# api/routes/documents.py
# Document processing endpoints for Bit-Counting.
#
# Storage: PostgreSQL (app_documents) via SQLAlchemy async.
#
# Endpoints:
#   POST /api/v1/documents/process        — multipart file upload
#   POST /api/v1/documents/process-text   — JSON raw text
#   GET  /api/v1/documents/{id}/status    — poll processing status
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..db.models import AppDocument
from ..schemas import (
    DocumentProcessRequest,
    DocumentProcessResponse,
    DocumentStatus,
    DocumentStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# INTAKE simulation (Phase 1 stub)
# ---------------------------------------------------------------------------

def _run_intake_simulation(raw_text: str, client_id: str, source_format: str) -> dict:
    """Phase 1 stub: simulates INTAKE → CENTINELA pipeline."""
    has_amount = any(ch.isdigit() for ch in raw_text)
    confidence = Decimal("0.82") if has_amount else Decimal("0.45")
    critical_missing = not has_amount

    intake_result = {
        "vendor":                _extract_hint(raw_text, "vendor") or "Unknown Vendor",
        "amount":                _extract_hint(raw_text, "amount"),
        "date":                  _extract_hint(raw_text, "date"),
        "confidence":            str(confidence),
        "source_format":         source_format,
        "critical_fields_missing": critical_missing,
        "missing_fields":        (["amount", "date"] if critical_missing else []),
    }

    centinela_decision = "PAUSE" if (critical_missing or confidence < Decimal("0.60")) else "PROCEED"
    return {"intake_result": intake_result, "centinela_decision": centinela_decision}


def _extract_hint(text: str, field: str) -> Optional[str]:
    import re
    if field == "amount":
        m = re.search(r"\$?([\d,]+\.?\d*)", text)
        return m.group(1).replace(",", "") if m else None
    if field == "date":
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        return m.group(1) if m else None
    if field == "vendor":
        m = re.search(r"([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*)", text)
        return m.group(1) if m else None
    return None


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@router.post(
    "/process",
    response_model=DocumentProcessResponse,
    status_code=status.HTTP_200_OK,
    summary="Process an uploaded document file",
)
async def process_document_file(
    file:          UploadFile = File(..., description="Document file (PDF, image, CSV, etc.)"),
    client_id:     str        = Form(..., description="UUID of the client"),
    source_format: str        = Form(default="UNKNOWN", description="Source format hint"),
    db:            AsyncSession = Depends(get_db),
) -> DocumentProcessResponse:
    document_id = str(uuid.uuid4())
    now = datetime.utcnow()

    try:
        raw_bytes = await file.read()
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("latin-1", errors="replace")

        processing = _run_intake_simulation(raw_text, client_id, source_format)
        centinela_decision = processing["centinela_decision"]
        doc_status = DocumentStatus.PAUSED if centinela_decision == "PAUSE" else DocumentStatus.PROCESSED
        pause_id   = str(uuid.uuid4()) if centinela_decision == "PAUSE" else None

        row = AppDocument(
            document_id=document_id,
            client_id=client_id,
            status=doc_status.value,
            centinela_decision=centinela_decision,
            pause_id=pause_id,
            intake_result=processing["intake_result"],
        )
        db.add(row)
        await db.commit()

        logger.info("Document %s processed for client %s — decision=%s", document_id, client_id, centinela_decision)
        return DocumentProcessResponse(
            document_id=document_id,
            status=doc_status,
            intake_result=processing["intake_result"],
            centinela_decision=centinela_decision,
            pause_id=pause_id,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error processing document %s: %s", document_id, exc)
        row = AppDocument(
            document_id=document_id,
            client_id=client_id,
            status=DocumentStatus.FAILED.value,
            intake_result={"error": str(exc)},
        )
        db.add(row)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document processing failed: {exc}",
        ) from exc


@router.post(
    "/process-text",
    response_model=DocumentProcessResponse,
    status_code=status.HTTP_200_OK,
    summary="Process raw text content as a document",
)
async def process_document_text(
    request: DocumentProcessRequest,
    db:      AsyncSession = Depends(get_db),
) -> DocumentProcessResponse:
    document_id = str(uuid.uuid4())

    processing = _run_intake_simulation(request.raw_text, request.client_id, request.source_format)
    centinela_decision = processing["centinela_decision"]
    doc_status = DocumentStatus.PAUSED if centinela_decision == "PAUSE" else DocumentStatus.PROCESSED
    pause_id   = str(uuid.uuid4()) if centinela_decision == "PAUSE" else None

    row = AppDocument(
        document_id=document_id,
        client_id=request.client_id,
        status=doc_status.value,
        centinela_decision=centinela_decision,
        pause_id=pause_id,
        intake_result=processing["intake_result"],
    )
    db.add(row)
    await db.commit()

    logger.info("Text document %s processed for client %s — decision=%s",
                document_id, request.client_id, centinela_decision)
    return DocumentProcessResponse(
        document_id=document_id,
        status=doc_status,
        intake_result=processing["intake_result"],
        centinela_decision=centinela_decision,
        pause_id=pause_id,
    )


@router.get(
    "/{document_id}/status",
    response_model=DocumentStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get processing status for a document",
)
async def get_document_status(
    document_id: str,
    db:          AsyncSession = Depends(get_db),
) -> DocumentStatusResponse:
    result = await db.execute(
        select(AppDocument).where(AppDocument.document_id == document_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found",
        )

    confidence = (row.intake_result or {}).get("confidence", "N/A")
    created_str = row.created_at.isoformat() if row.created_at else "unknown"

    return DocumentStatusResponse(
        document_id=row.document_id,
        status=row.status,
        intake_result=row.intake_result,
        centinela_decision=row.centinela_decision,
        pause_id=row.pause_id,
        processing_log=[
            f"Document submitted at {created_str}",
            f"INTAKE completed — confidence: {confidence}",
            f"CENTINELA decision: {row.centinela_decision or 'N/A'}",
        ],
        created_at=created_str,
        updated_at=created_str,
    )
