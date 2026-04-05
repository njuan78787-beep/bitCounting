# =============================================================================
# api/routes/documents.py
# Document processing endpoints for Bit-Counting.
#
# Handles document intake: file uploads (multipart) and raw-text submissions.
# Phase 1: processing is synchronous — INTAKE and CENTINELA run in-request.
#
# Endpoints:
#   POST /api/v1/documents/process        — multipart file + metadata
#   POST /api/v1/documents/process-text   — JSON raw text
#   GET  /api/v1/documents/{id}/status    — poll status
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile, File, status

from ..schemas import (
    DocumentProcessRequest,
    DocumentProcessResponse,
    DocumentStatus,
    DocumentStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# ---------------------------------------------------------------------------
# In-memory document store (Phase 1 — replaced by PostgreSQL in Phase 2)
# ---------------------------------------------------------------------------
# Maps document_id → DocumentProcessResponse dict
_document_store: dict[str, dict] = {}


def _run_intake_simulation(raw_text: str, client_id: str, source_format: str) -> dict:
    """
    Phase 1 stub: simulates INTAKE agent processing.

    In production this calls agents.intake.Intake.process() and then
    agents.centinela.Centinela.evaluate().  For now it returns a synthetic
    result so that the API surface is fully testable end-to-end.
    """
    # Minimal heuristic: if the text looks like it has an amount, confidence is higher
    has_amount = any(ch.isdigit() for ch in raw_text)
    confidence = Decimal("0.82") if has_amount else Decimal("0.45")
    critical_missing = not has_amount

    intake_result = {
        "vendor": _extract_hint(raw_text, "vendor") or "Unknown Vendor",
        "amount": _extract_hint(raw_text, "amount"),
        "date": _extract_hint(raw_text, "date"),
        "confidence": str(confidence),
        "source_format": source_format,
        "critical_fields_missing": critical_missing,
        "missing_fields": (["amount", "date"] if critical_missing else []),
    }

    # CENTINELA decision
    if critical_missing or confidence < Decimal("0.60"):
        centinela_decision = "PAUSE"
    else:
        centinela_decision = "PROCEED"

    return {
        "intake_result": intake_result,
        "centinela_decision": centinela_decision,
    }


def _extract_hint(text: str, field: str) -> Optional[str]:
    """Very naive field extraction for Phase 1 stub."""
    import re
    if field == "amount":
        m = re.search(r"\$?([\d,]+\.?\d*)", text)
        return m.group(1).replace(",", "") if m else None
    if field == "date":
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        return m.group(1) if m else None
    if field == "vendor":
        # Return first capitalized word cluster as vendor hint
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
    file: UploadFile = File(..., description="Document file (PDF, image, CSV, etc.)"),
    client_id: str = Form(..., description="UUID of the client"),
    source_format: str = Form(default="UNKNOWN", description="Source format hint"),
) -> DocumentProcessResponse:
    """
    Accept a multipart file upload and run it through the INTAKE → CENTINELA
    pipeline.  The file content is read as bytes and decoded to text for Phase 1.
    Binary formats (PDF, images) will use OCR in Phase 2.
    """
    document_id = str(uuid.uuid4())

    try:
        raw_bytes = await file.read()
        # Phase 1: treat as UTF-8 text; Phase 2 adds OCR/PDF parsing
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = raw_bytes.decode("latin-1", errors="replace")

        processing = _run_intake_simulation(raw_text, client_id, source_format)
        centinela_decision = processing["centinela_decision"]
        doc_status = (
            DocumentStatus.PAUSED if centinela_decision == "PAUSE"
            else DocumentStatus.PROCESSED
        )

        pause_id = str(uuid.uuid4()) if centinela_decision == "PAUSE" else None

        response = DocumentProcessResponse(
            document_id=document_id,
            status=doc_status,
            intake_result=processing["intake_result"],
            centinela_decision=centinela_decision,
            pause_id=pause_id,
        )

        # Persist in store
        _document_store[document_id] = response.model_dump()
        logger.info(
            "Document %s processed for client %s — decision=%s",
            document_id, client_id, centinela_decision,
        )
        return response

    except Exception as exc:
        logger.exception("Error processing document %s: %s", document_id, exc)
        error_response = DocumentProcessResponse(
            document_id=document_id,
            status=DocumentStatus.FAILED,
            intake_result={"error": str(exc)},
        )
        _document_store[document_id] = error_response.model_dump()
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
) -> DocumentProcessResponse:
    """
    Accept raw text (JSON body) and run it through the INTAKE → CENTINELA
    pipeline.  Useful for CSV, XML, JSON sources already converted to text.
    """
    document_id = str(uuid.uuid4())

    processing = _run_intake_simulation(
        request.raw_text, request.client_id, request.source_format
    )
    centinela_decision = processing["centinela_decision"]
    doc_status = (
        DocumentStatus.PAUSED if centinela_decision == "PAUSE"
        else DocumentStatus.PROCESSED
    )
    pause_id = str(uuid.uuid4()) if centinela_decision == "PAUSE" else None

    response = DocumentProcessResponse(
        document_id=document_id,
        status=doc_status,
        intake_result=processing["intake_result"],
        centinela_decision=centinela_decision,
        pause_id=pause_id,
    )
    _document_store[document_id] = response.model_dump()
    logger.info(
        "Text document %s processed for client %s — decision=%s",
        document_id, request.client_id, centinela_decision,
    )
    return response


@router.get(
    "/{document_id}/status",
    response_model=DocumentStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get processing status for a document",
)
async def get_document_status(document_id: str) -> DocumentStatusResponse:
    """
    Poll the processing status and results for a previously submitted document.
    """
    stored = _document_store.get(document_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found",
        )

    return DocumentStatusResponse(
        document_id=stored["document_id"],
        status=stored["status"],
        intake_result=stored.get("intake_result"),
        centinela_decision=stored.get("centinela_decision"),
        pause_id=stored.get("pause_id"),
        processing_log=[
            f"Document submitted at {stored.get('created_at', 'unknown')}",
            f"INTAKE completed — confidence: {stored.get('intake_result', {}).get('confidence', 'N/A')}",
            f"CENTINELA decision: {stored.get('centinela_decision', 'N/A')}",
        ],
        created_at=stored.get("created_at"),
        updated_at=stored.get("created_at"),
    )
