# =============================================================================
# tests/test_api.py
# FastAPI endpoint tests for Bit-Counting.
#
# Uses FastAPI TestClient for synchronous testing without a running server.
#
# Test cases:
#   1. GET /health                        → 200
#   2. POST /api/v1/documents/process-text → 200 with status field
#   3. GET /api/v1/cpa/pauses             → 200 list
#   4. POST /api/v1/cpa/approve (LOW)     → approves immediately
#   5. POST /api/v1/cpa/approve (HIGH)    → returns question first
#   6. CPA metrics tracks approval time
# =============================================================================

from __future__ import annotations

import sys
import os

# Ensure project root is in path so imports resolve correctly
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# TEST 1: Health check
# ---------------------------------------------------------------------------

def test_health_check_returns_200():
    """GET /health must return HTTP 200 and status='ok'."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# TEST 2: Document process-text
# ---------------------------------------------------------------------------

def test_process_text_returns_200_with_status():
    """POST /api/v1/documents/process-text must return 200 with a status field."""
    payload = {
        "raw_text": "Invoice from Costco Wholesale PR. Date: 2026-03-15. Amount: $2450.00",
        "client_id": "client-test-001",
        "source_format": "PDF",
    }
    response = client.post("/api/v1/documents/process-text", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert "document_id" in data
    assert "status" in data
    assert data["status"] in ("processing", "processed", "paused", "failed")
    assert isinstance(data["document_id"], str)
    assert len(data["document_id"]) > 0


def test_process_text_with_missing_amount_returns_paused():
    """Text with no numeric amount triggers LOW confidence → PAUSE."""
    payload = {
        "raw_text": "Invoice with no amount mentioned at all",
        "client_id": "client-test-001",
        "source_format": "UNKNOWN",
    }
    response = client.post("/api/v1/documents/process-text", json=payload)
    assert response.status_code == 200

    data = response.json()
    # No digits in text → critical_fields_missing → PAUSE
    assert data["status"] == "paused"
    assert data["centinela_decision"] == "PAUSE"


def test_process_text_with_amount_returns_processed():
    """Text with a clear amount triggers higher confidence → PROCEED."""
    payload = {
        "raw_text": "Dell Technologies invoice. Date: 2026-03-20. Amount: $3200.00",
        "client_id": "client-test-001",
        "source_format": "JSON",
    }
    response = client.post("/api/v1/documents/process-text", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "processed"
    assert data["centinela_decision"] == "PROCEED"
    assert "intake_result" in data
    assert data["intake_result"] is not None


def test_process_text_stores_document_for_status_retrieval():
    """Processed document must be retrievable via GET /status endpoint."""
    payload = {
        "raw_text": "Factura Hacienda PR. Monto: $1250.00. Fecha: 2026-03-31",
        "client_id": "client-test-status",
        "source_format": "PDF",
    }
    post_resp = client.post("/api/v1/documents/process-text", json=payload)
    assert post_resp.status_code == 200
    document_id = post_resp.json()["document_id"]

    status_resp = client.get(f"/api/v1/documents/{document_id}/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["document_id"] == document_id


# ---------------------------------------------------------------------------
# TEST 3: CPA pauses list
# ---------------------------------------------------------------------------

def test_get_pauses_returns_200_list():
    """GET /api/v1/cpa/pauses must return 200 with a list."""
    response = client.get("/api/v1/cpa/pauses")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_get_pauses_items_have_required_fields():
    """Each pause in the list must have the required fields."""
    response = client.get("/api/v1/cpa/pauses")
    assert response.status_code == 200
    pauses = response.json()

    for pause in pauses:
        assert "pause_id" in pause
        assert "trigger_type" in pause
        assert "interpretations" in pause
        assert "status" in pause
        assert pause["status"] == "ACTIVE"  # Default filter is active


def test_get_pauses_with_cpa_license_filter():
    """Pauses can be filtered by CPA license."""
    response = client.get("/api/v1/cpa/pauses?cpa_license=CPA-NONEXISTENT-999")
    assert response.status_code == 200
    # Should return empty list since no pauses are assigned to this license
    data = response.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# TEST 4: CPA approve LOW consequence → approves immediately
# ---------------------------------------------------------------------------

def test_approve_low_consequence_item_immediately():
    """
    LOW consequence items should be approved without requiring any
    friction challenge — just a valid token.
    """
    # LOW consequence item from demo data
    item_id = "review-demo-002"

    payload = {
        "cpa_license": "CPA-PR-12345",
        "cpa_token": "test-token-secure-001",
        "action": "approved",
        "notes": "Reviewed and approved — below capitalization threshold.",
    }
    response = client.post(f"/api/v1/cpa/approve/{item_id}", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["item_id"] == item_id
    assert data["friction_level"] == "LOW"
    assert data["status"] in ("approved", "rejected")


def test_approve_with_invalid_token_returns_401():
    """Approval requests with empty token must be rejected with 401."""
    payload = {
        "cpa_license": "",
        "cpa_token": "",
        "action": "approved",
        "notes": "Invalid token test",
    }
    response = client.post("/api/v1/cpa/approve/review-demo-001", json=payload)
    assert response.status_code == 401


def test_approve_nonexistent_item_returns_404():
    """Approving a non-existent item must return 404."""
    payload = {
        "cpa_license": "CPA-PR-12345",
        "cpa_token": "valid-token-001",
        "action": "approved",
        "notes": "Test",
    }
    response = client.post("/api/v1/cpa/approve/nonexistent-item-xyz", json=payload)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# TEST 5: CPA approve HIGH consequence → returns question first
# ---------------------------------------------------------------------------

def test_approve_high_consequence_returns_question_first():
    """
    HIGH consequence items must return a challenge question on the first call
    (before any challenge_answer is provided).
    """
    item_id = "review-demo-001"  # consequence_level = HIGH

    # First call — no challenge_answer provided
    payload = {
        "cpa_license": "CPA-PR-12345",
        "cpa_token": "test-token-secure-001",
        "action": "approved",
        "notes": "About to approve after reviewing",
    }
    response = client.post(f"/api/v1/cpa/approve/{item_id}", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["friction_level"] == "HIGH"
    assert data["action_required"] == "answer_question"
    assert "question" in data
    assert len(data["question"]) > 10  # Question must be non-trivial


def test_approve_high_consequence_with_wrong_answer_returns_400():
    """
    HIGH consequence items with a wrong challenge answer must return 400.
    """
    item_id = "review-demo-001"

    # Submit with deliberately wrong answer
    payload = {
        "cpa_license": "CPA-PR-99999",
        "cpa_token": "test-token-secure-002",
        "action": "approved",
        "notes": "Test wrong answer",
        "challenge_answer": "THIS_IS_DEFINITELY_WRONG_ANSWER_XYZ",
    }
    response = client.post(f"/api/v1/cpa/approve/{item_id}", json=payload)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# TEST 6: CPA metrics tracks approval time
# ---------------------------------------------------------------------------

def test_cpa_metrics_returns_200():
    """GET /api/v1/cpa/metrics must return 200 with required fields."""
    response = client.get("/api/v1/cpa/metrics?cpa_license=CPA-PR-12345")
    assert response.status_code == 200

    data = response.json()
    assert "cpa_license" in data
    assert "pauses_resolved_today" in data
    assert "avg_response_time_hours" in data
    assert "approval_accuracy_rate" in data
    assert "suspicious_fast_approvals" in data


def test_cpa_metrics_tracks_low_approval():
    """
    After a LOW-level approval, metrics should reflect the approval count.
    Uses a fresh item to avoid interference with other tests.
    """
    import uuid as _uuid

    # Insert a fresh LOW item directly into the review queue
    from api.routes.cpa_dashboard import _review_queue
    fresh_id = f"test-low-{_uuid.uuid4().hex[:8]}"
    _review_queue[fresh_id] = {
        "item_id": fresh_id,
        "item_type": "expense_entry",
        "transaction_id": "txn-test-low",
        "vendor": "Test Vendor",
        "amount": Decimal("100.00"),
        "description": "Test LOW item",
        "consequence_level": "LOW",
        "sla_deadline": "2026-05-01T00:00:00",
        "created_at": "2026-04-01T10:00:00",
        "status": "pending_review",
        "detail": {"account_code": "5900", "account_name": "Gastos General"},
    }

    # Approve it
    cpa_license = f"CPA-TEST-{_uuid.uuid4().hex[:6]}"
    payload = {
        "cpa_license": cpa_license,
        "cpa_token": "valid-test-token",
        "action": "approved",
        "notes": "Test approval for metrics",
    }
    resp = client.post(f"/api/v1/cpa/approve/{fresh_id}", json=payload)
    assert resp.status_code == 200

    # Check metrics
    metrics_resp = client.get(f"/api/v1/cpa/metrics?cpa_license={cpa_license}")
    assert metrics_resp.status_code == 200
    # No suspicious flags expected for a LOW item
    metrics = metrics_resp.json()
    assert metrics["suspicious_fast_approvals"] == 0


def test_fast_high_approval_is_tracked_in_metrics():
    """
    A HIGH-level approval completed instantly should increment
    suspicious_fast_approvals in CPA metrics.
    """
    import uuid as _uuid
    from api.routes.cpa_dashboard import _review_queue
    from decimal import Decimal

    # Insert a HIGH item
    fresh_id = f"test-high-{_uuid.uuid4().hex[:8]}"
    cpa_license = f"CPA-FAST-{_uuid.uuid4().hex[:6]}"
    _review_queue[fresh_id] = {
        "item_id": fresh_id,
        "item_type": "expense_entry",
        "transaction_id": "txn-test-high",
        "vendor": "Expensive Vendor",
        "amount": Decimal("50000.00"),
        "description": "Test HIGH item for fast approval detection",
        "consequence_level": "HIGH",
        "sla_deadline": "2026-05-01T00:00:00",
        "created_at": "2026-04-01T10:00:00",
        "status": "pending_review",
        "detail": {"account_code": "1600", "account_name": "Vehiculos"},
    }

    # First call to get question
    payload1 = {
        "cpa_license": cpa_license,
        "cpa_token": "valid-token",
        "action": "approved",
        "notes": "",
    }
    resp1 = client.post(f"/api/v1/cpa/approve/{fresh_id}", json=payload1)
    assert resp1.status_code == 200
    question_data = resp1.json()
    assert question_data["action_required"] == "answer_question"

    # Immediately answer (simulate < 5 second approval)
    from friction.cpa_vigilance import CPAVigilanceSystem as _CVS
    correct_answer = question_data.get("question", "")  # Use whatever question was asked

    # Get the actual correct answer by generating the challenge
    from api.routes.cpa_dashboard import _vigilance
    challenge = _vigilance.generate_friction_challenge(
        _review_queue[fresh_id], "HIGH"
    )
    correct_answer = challenge["correct_answer"]

    payload2 = {
        "cpa_license": cpa_license,
        "cpa_token": "valid-token",
        "action": "approved",
        "notes": "Approved instantly",
        "challenge_answer": correct_answer,
    }
    resp2 = client.post(f"/api/v1/cpa/approve/{fresh_id}", json=payload2)
    assert resp2.status_code == 200

    # Check metrics — should have at least 1 suspicious approval
    metrics_resp = client.get(f"/api/v1/cpa/metrics?cpa_license={cpa_license}")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    # Fast approval < 5s on HIGH item should be flagged
    assert metrics["suspicious_fast_approvals"] >= 1


# ---------------------------------------------------------------------------
# TEST 7: Additional endpoint coverage
# ---------------------------------------------------------------------------

def test_transactions_list_returns_200():
    """GET /api/v1/transactions must return paginated list."""
    response = client.get("/api/v1/transactions")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)


def test_transactions_filtered_by_client():
    """Transactions can be filtered by client_id."""
    response = client.get("/api/v1/transactions?client_id=client-demo-001")
    assert response.status_code == 200
    data = response.json()
    for item in data["items"]:
        assert item["client_id"] == "client-demo-001"


def test_balance_sheet_returns_200():
    """GET /api/v1/reports/balance-sheet must return 200."""
    response = client.get(
        "/api/v1/reports/balance-sheet?client_id=client-demo-001&as_of_date=2026-03-31"
    )
    assert response.status_code == 200
    data = response.json()
    assert "total_assets" in data
    assert "total_liabilities" in data
    assert "total_equity" in data
    # GAAP: Assets = Liabilities + Equity
    from decimal import Decimal
    total_assets = Decimal(str(data["total_assets"]))
    total_liabilities = Decimal(str(data["total_liabilities"]))
    total_equity = Decimal(str(data["total_equity"]))
    assert total_assets == total_liabilities + total_equity


def test_ivu_summary_returns_200():
    """GET /api/v1/reports/ivu-summary must return IVU data for SC 2915."""
    response = client.get("/api/v1/reports/ivu-summary?client_id=client-demo-001&period=2026-03")
    assert response.status_code == 200
    data = response.json()
    assert "ivu_collected" in data
    assert "ivu_remitted" in data
    assert "ivu_balance" in data
    assert "form_sc2915_ready" in data


def test_normative_pending_updates_returns_200():
    """GET /api/v1/normative/pending-updates must return list."""
    response = client.get("/api/v1/normative/pending-updates")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # All items should have PENDING status
    for update in data:
        assert update["status"] == "pending"


def test_normative_check_now_returns_200():
    """POST /api/v1/normative/check-now must trigger a normative check."""
    response = client.post("/api/v1/normative/check-now")
    assert response.status_code == 200
    data = response.json()
    assert "sources_checked" in data
    assert data["sources_checked"] == 7


def test_document_status_404_for_unknown_id():
    """GET /api/v1/documents/{unknown_id}/status must return 404."""
    response = client.get("/api/v1/documents/nonexistent-document-id-xyz/status")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Decimal import for test helpers
# ---------------------------------------------------------------------------
from decimal import Decimal  # noqa: E402 (imported at bottom for test file clarity)
