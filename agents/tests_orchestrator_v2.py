# =============================================================================
# agents/tests_orchestrator_v2.py
# Tests for OrchestratorV2 — Redis-backed pipeline coordinator.
# Uses fakeredis for all Redis interactions (no real Redis needed).
# =============================================================================

from __future__ import annotations

import json
import pytest
from decimal import Decimal

import fakeredis

from agents.orchestrator_v2 import (
    OrchestratorV2,
    FlowStatus,
    QueueStats,
    ProcessingJob,
    Q_NORMAL,
    Q_PAUSES,
    Q_CPA,
    Q_BACKGROUND,
    Q_FAILED,
)


# =============================================================================
# MOCK VISION CLIENTS
# =============================================================================

class MockVisionClient:
    def __init__(self, response):
        self.response = response

    def extract(self, image_bytes, source_format):
        return self.response


class BadVisionClient:
    def extract(self, image_bytes, source_format):
        raise RuntimeError("Vision API connection refused")


# Good invoice — all critical fields present
GOOD_INVOICE = {
    "vendor": "Alquileres Caribe LLC",
    "date": "2025-06-15",
    "amount": "1000.00",
    "tax_amount": "105.00",
    "currency": "USD",
    "payment_method": "CHECK",
    "document_type": "INVOICE",
    "line_items": [{"description": "alquiler oficina hato rey", "amount": "1000.00"}],
}

# Missing amount — critical field absent
MISSING_AMOUNT = {
    "vendor": "Test Vendor",
    "date": "2025-06-15",
    "amount": None,
    "tax_amount": None,
    "currency": "USD",
    "payment_method": None,
    "document_type": "INVOICE",
    "line_items": [],
}

# Low confidence — missing vendor, tax_amount, payment_method, line_items
# For a jpg: base 0.75 - 0.05 (vendor) - 0.03 (tax_amount) - 0.02 (payment_method) - 0.02 (line_items) = 0.63 < 0.75 threshold
LOW_CONF = {
    "vendor": None,
    "date": "2025-06-15",
    "amount": "1000.00",
    "tax_amount": None,
    "currency": "USD",
    "payment_method": None,
    "document_type": "INVOICE",
    "line_items": [],
}

# Unclassified item — no keywords match any account
UNCLASSIFIED_INVOICE = {
    "vendor": "Zzzz Corp",
    "date": "2025-06-15",
    "amount": "250.00",
    "tax_amount": "10.00",
    "currency": "USD",
    "payment_method": "CHECK",
    "document_type": "INVOICE",
    "line_items": [{"description": "zzzz completely unknown abc xyz", "amount": "250.00"}],
}

_DUMMY_BYTES = b"fake-document-bytes"


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


@pytest.fixture
def orch(redis_client):
    from agents.core_decisions import AgentDecisionsLog
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=MockVisionClient(GOOD_INVOICE),
    )


@pytest.fixture
def orch_missing(redis_client):
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=MockVisionClient(MISSING_AMOUNT),
    )


@pytest.fixture
def orch_low_conf(redis_client):
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=MockVisionClient(LOW_CONF),
    )


@pytest.fixture
def orch_unclassified(redis_client):
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=MockVisionClient(UNCLASSIFIED_INVOICE),
    )


@pytest.fixture
def orch_bad(redis_client):
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=BadVisionClient(),
    )


# =============================================================================
# TESTS 1-5: Submit / Queue
# =============================================================================

def test_submit_returns_trace_id(orch):
    """1. submit_document returns non-empty string (UUID format)."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    assert isinstance(trace_id, str)
    assert len(trace_id) > 0
    # Should look like a UUID: 8-4-4-4-12
    parts = trace_id.split("-")
    assert len(parts) == 5


def test_submit_adds_to_normal_queue(orch):
    """2. After submit, queue_stats().normal == 1."""
    orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    stats = orch.queue_stats()
    assert stats.normal == 1


def test_submit_creates_trace_meta(orch, redis_client):
    """3. HGET bc:trace:{trace_id}:meta status == 'QUEUED'."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    status = redis_client.hget(f"bc:trace:{trace_id}:meta", "status")
    if isinstance(status, bytes):
        status = status.decode()
    assert status == "QUEUED"


def test_submit_multiple_jobs_queue_grows(orch):
    """4. Submit 3 docs, queue_stats().normal == 3."""
    for i in range(3):
        orch.submit_document(_DUMMY_BYTES, "pdf", f"CLI-{i:03d}")
    stats = orch.queue_stats()
    assert stats.normal == 3


def test_submit_background_task(orch, redis_client):
    """5. submit_background_task adds to background queue."""
    trace_id = orch.submit_background_task("HISTORICAL_REVIEW", {"period": "Q1-2025"}, "CLI-001")
    assert isinstance(trace_id, str) and len(trace_id) > 0
    stats = orch.queue_stats()
    assert stats.background == 1


# =============================================================================
# TESTS 6-12: Happy Path Flow
# =============================================================================

def test_run_one_happy_path_returns_trace_id(orch):
    """6. run_one() returns trace_id."""
    submitted = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    result = orch.run_one()
    assert result == submitted


def test_run_one_happy_path_status_confirmed(orch):
    """7. After run_one(), get_trace()["meta"]["status"] == 'CONFIRMED'."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    trace = orch.get_trace(trace_id)
    assert trace["meta"]["status"] == "CONFIRMED"


def test_run_one_records_all_steps(orch):
    """8. Trace has steps for INTAKE, CENTINELA_PRE, CLASIFICADOR, FISCAL, AUDITOR, CONFIRMED."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    trace = orch.get_trace(trace_id)
    step_names = {s["step_name"] for s in trace["steps"]}
    expected = {"INTAKE", "CENTINELA_PRE", "CLASIFICADOR", "FISCAL", "AUDITOR", "CONFIRMED"}
    assert expected.issubset(step_names), f"Missing steps: {expected - step_names}"


def test_run_one_confirmed_trace_in_confirmed_list(orch, redis_client):
    """9. trace_id appears in bc:confirmed (use LRANGE)."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    confirmed_list = redis_client.lrange("bc:confirmed", 0, -1)
    confirmed_ids = [v.decode() if isinstance(v, bytes) else v for v in confirmed_list]
    assert trace_id in confirmed_ids


def test_run_one_empties_normal_queue(orch):
    """10. After run_one(), queue_stats().normal == 0."""
    orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    stats = orch.queue_stats()
    assert stats.normal == 0


def test_run_one_steps_have_timestamps(orch):
    """11. Every step in trace has non-empty timestamp."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    trace = orch.get_trace(trace_id)
    for step in trace["steps"]:
        assert "timestamp" in step
        assert step["timestamp"]  # non-empty


def test_run_one_steps_are_ordered(orch):
    """12. Steps come in INTAKE→CENTINELA_PRE→CLASIFICADOR→FISCAL→AUDITOR→CONFIRMED order."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    trace = orch.get_trace(trace_id)
    step_names = [s["step_name"] for s in trace["steps"]]
    expected_order = ["INTAKE", "CENTINELA_PRE", "CLASIFICADOR", "FISCAL", "AUDITOR", "CONFIRMED"]
    # All expected steps should appear in order
    idx = 0
    for expected in expected_order:
        found = False
        while idx < len(step_names):
            if step_names[idx] == expected:
                found = True
                idx += 1
                break
            idx += 1
        assert found, f"Step {expected} not found in order. Steps: {step_names}"


# =============================================================================
# TESTS 13-15: Missing Critical Fields → CPA Review
# =============================================================================

def test_missing_amount_goes_to_cpa_review(orch_missing):
    """13. Missing amount → after run_one(), queue_stats().cpa_review == 1."""
    orch_missing.submit_document(_DUMMY_BYTES, "pdf", "CLI-002")
    orch_missing.run_one()
    stats = orch_missing.queue_stats()
    assert stats.cpa_review == 1


def test_missing_amount_trace_status_cpa_review(orch_missing):
    """14. get_trace()["meta"]["status"] == 'CPA_REVIEW'."""
    trace_id = orch_missing.submit_document(_DUMMY_BYTES, "pdf", "CLI-002")
    orch_missing.run_one()
    trace = orch_missing.get_trace(trace_id)
    assert trace["meta"]["status"] == "CPA_REVIEW"


def test_missing_amount_intake_step_paused(orch_missing):
    """15. Trace has INTAKE step with status 'paused'."""
    trace_id = orch_missing.submit_document(_DUMMY_BYTES, "pdf", "CLI-002")
    orch_missing.run_one()
    trace = orch_missing.get_trace(trace_id)
    intake_steps = [s for s in trace["steps"] if s["step_name"] == "INTAKE"]
    assert len(intake_steps) > 0
    assert intake_steps[0]["status"] == "paused"


# =============================================================================
# TESTS 16-18: CENTINELA Pause (Low Confidence)
# =============================================================================

def test_centinela_pause_low_confidence(orch_low_conf):
    """16. Low confidence → queue_stats().pauses == 1 after run_one()."""
    # LOW_CONF: jpg with missing vendor, tax_amount, payment_method, line_items
    # confidence = 0.75 - 0.05 - 0.03 - 0.02 - 0.02 = 0.63 < 0.75 threshold → PAUSE
    orch_low_conf.submit_document(_DUMMY_BYTES, "jpg", "CLI-003")
    orch_low_conf.run_one()
    stats = orch_low_conf.queue_stats()
    assert stats.pauses == 1


def test_centinela_pause_trace_status_paused(orch_low_conf):
    """17. get_trace()["meta"]["status"] == 'PAUSED'."""
    trace_id = orch_low_conf.submit_document(_DUMMY_BYTES, "jpg", "CLI-003")
    orch_low_conf.run_one()
    trace = orch_low_conf.get_trace(trace_id)
    assert trace["meta"]["status"] == "PAUSED"


def test_centinela_pause_step_recorded(orch_low_conf):
    """18. Trace has CENTINELA_PRE step with status 'paused'."""
    trace_id = orch_low_conf.submit_document(_DUMMY_BYTES, "jpg", "CLI-003")
    orch_low_conf.run_one()
    trace = orch_low_conf.get_trace(trace_id)
    centinela_steps = [s for s in trace["steps"] if s["step_name"] == "CENTINELA_PRE"]
    assert len(centinela_steps) > 0
    assert centinela_steps[0]["status"] == "paused"


# =============================================================================
# TESTS 19-20: UNCLASSIFIED → CPA Review
# =============================================================================

def test_unclassified_goes_to_cpa_review(orch_unclassified):
    """19. Unclassified item → queue_stats().cpa_review == 1."""
    orch_unclassified.submit_document(_DUMMY_BYTES, "pdf", "CLI-004")
    orch_unclassified.run_one()
    stats = orch_unclassified.queue_stats()
    assert stats.cpa_review == 1


def test_unclassified_trace_has_clasificador_step(orch_unclassified):
    """20. Trace has CLASIFICADOR step."""
    trace_id = orch_unclassified.submit_document(_DUMMY_BYTES, "pdf", "CLI-004")
    orch_unclassified.run_one()
    trace = orch_unclassified.get_trace(trace_id)
    clasificador_steps = [s for s in trace["steps"] if s["step_name"] == "CLASIFICADOR"]
    assert len(clasificador_steps) > 0


# =============================================================================
# TESTS 21-22: Queue Priority
# =============================================================================

def test_run_one_priority_checks_pauses_first(redis_client):
    """21. run_one_priority() processes pauses item first."""
    # Create two orchestrators sharing the same redis
    orch_normal = OrchestratorV2(redis_client=redis_client, vision_client=MockVisionClient(GOOD_INVOICE))
    orch_paused = OrchestratorV2(redis_client=redis_client, vision_client=MockVisionClient(GOOD_INVOICE))

    # Manually push a job to Q_NORMAL and another to Q_PAUSES
    normal_job = ProcessingJob(client_id="CLI-NORMAL", source_format="pdf", document_b64="dGVzdA==")
    paused_job = ProcessingJob(client_id="CLI-PAUSED", source_format="pdf", document_b64="dGVzdA==")
    redis_client.rpush(Q_NORMAL, normal_job.model_dump_json())
    redis_client.rpush(Q_PAUSES, paused_job.model_dump_json())

    # run_one_priority should pick from Q_PAUSES first
    result_trace = orch_paused.run_one_priority()
    assert result_trace == paused_job.trace_id

    # Pauses queue should now be empty
    stats = orch_paused.queue_stats()
    assert stats.pauses == 0
    # Normal queue should still have 1 item
    assert stats.normal == 1


def test_queue_stats_all_queues(redis_client):
    """22. Submit to all 4 queues manually, check all stats."""
    orch = OrchestratorV2(redis_client=redis_client, vision_client=MockVisionClient(GOOD_INVOICE))

    dummy_job = ProcessingJob(client_id="CLI-X", source_format="pdf", document_b64="dGVzdA==")
    job_json = dummy_job.model_dump_json()

    redis_client.rpush(Q_NORMAL, job_json)
    redis_client.rpush(Q_NORMAL, job_json)
    redis_client.rpush(Q_PAUSES, job_json)
    redis_client.rpush(Q_CPA, job_json)
    redis_client.rpush(Q_BACKGROUND, job_json)
    redis_client.rpush(Q_FAILED, job_json)

    stats = orch.queue_stats()
    assert stats.normal == 2
    assert stats.pauses == 1
    assert stats.cpa_review == 1
    assert stats.background == 1
    assert stats.failed == 1


# =============================================================================
# TESTS 23-28: Retry / Fault Isolation
# =============================================================================

def test_agent_failure_increments_attempt(redis_client):
    """23. Vision failure → retry job in Q_NORMAL with attempt=1."""
    orch = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())
    orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-RETRY")
    orch.run_one()

    # A retry job should be in Q_NORMAL
    stats = orch.queue_stats()
    assert stats.normal == 1

    # Check the retried job has attempt=1
    raw = redis_client.lindex(Q_NORMAL, 0)
    if isinstance(raw, bytes):
        raw = raw.decode()
    retry_job = ProcessingJob.model_validate_json(raw)
    assert retry_job.attempt == 1


def test_agent_failure_does_not_block_other_jobs(redis_client):
    """24. First bad job retries, second good job still completes CONFIRMED."""
    orch_bad_v = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())
    orch_good = OrchestratorV2(redis_client=redis_client, vision_client=MockVisionClient(GOOD_INVOICE))

    # Submit bad job first, then good job to same queue
    orch_bad_v.submit_document(_DUMMY_BYTES, "pdf", "CLI-BAD")
    good_trace_id = orch_good.submit_document(_DUMMY_BYTES, "pdf", "CLI-GOOD")

    # Process bad job (it will retry and push back to normal)
    orch_bad_v.run_one()

    # After bad job retried, queue has: [good_job, retry_of_bad]
    # good_job is at the front (FIFO) — run_one() picks it up directly
    orch_good.run_one()  # process good job

    trace = orch_good.get_trace(good_trace_id)
    assert trace["meta"]["status"] == "CONFIRMED"


def test_max_retries_goes_to_failed(redis_client):
    """25. Job with attempt=MAX_RETRIES and bad agent → queue_stats().failed == 1."""
    orch = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())
    # Inject a job at MAX_RETRIES already
    job = ProcessingJob(
        client_id="CLI-TEST",
        source_format="pdf",
        document_b64="dGVzdA==",
        attempt=OrchestratorV2.MAX_RETRIES,
    )
    redis_client.rpush(Q_NORMAL, job.model_dump_json())
    orch.run_one()

    stats = orch.queue_stats()
    assert stats.failed == 1
    assert stats.normal == 0


def test_max_retries_creates_eximia_alert(redis_client):
    """26. Same setup, get_eximia_alerts() returns 1 alert."""
    orch = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())
    job = ProcessingJob(
        client_id="CLI-TEST",
        source_format="pdf",
        document_b64="dGVzdA==",
        attempt=OrchestratorV2.MAX_RETRIES,
    )
    redis_client.rpush(Q_NORMAL, job.model_dump_json())
    orch.run_one()

    alerts = orch.get_eximia_alerts()
    assert len(alerts) == 1


def test_eximia_alert_has_trace_id(redis_client):
    """27. alert.trace_id matches the submitted job's trace_id."""
    orch = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())
    job = ProcessingJob(
        client_id="CLI-TEST",
        source_format="pdf",
        document_b64="dGVzdA==",
        attempt=OrchestratorV2.MAX_RETRIES,
    )
    redis_client.rpush(Q_NORMAL, job.model_dump_json())
    orch.run_one()

    alerts = orch.get_eximia_alerts()
    assert alerts[0].trace_id == job.trace_id


def test_failed_trace_status_is_failed(redis_client):
    """28. trace meta status == 'FAILED' after exhausting retries."""
    orch = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())
    # First need to init trace meta via submit, then inject at max retries
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-TEST")
    # Pop the auto-submitted job, replace with one at MAX_RETRIES
    redis_client.lpop(Q_NORMAL)
    job = ProcessingJob(
        trace_id=trace_id,
        client_id="CLI-TEST",
        source_format="pdf",
        document_b64="dGVzdA==",
        attempt=OrchestratorV2.MAX_RETRIES,
    )
    redis_client.rpush(Q_NORMAL, job.model_dump_json())
    orch.run_one()

    trace = orch.get_trace(trace_id)
    assert trace["meta"]["status"] == "FAILED"


# =============================================================================
# TESTS 29-31: Trace Reconstruction
# =============================================================================

def test_get_trace_returns_steps_and_meta(orch):
    """29. get_trace() has both 'steps' and 'meta' keys."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    trace = orch.get_trace(trace_id)
    assert "steps" in trace
    assert "meta" in trace


def test_get_trace_meta_has_client_id(orch):
    """30. meta['client_id'] == client_id used in submit."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-SPECIFIC")
    orch.run_one()
    trace = orch.get_trace(trace_id)
    assert trace["meta"]["client_id"] == "CLI-SPECIFIC"


def test_get_trace_steps_are_parseable(orch):
    """31. Each step in steps is a valid JSON dict with required keys."""
    trace_id = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-001")
    orch.run_one()
    trace = orch.get_trace(trace_id)
    required_keys = {"step_name", "timestamp", "status", "detail"}
    for step in trace["steps"]:
        assert isinstance(step, dict), f"Step is not dict: {type(step)}"
        for key in required_keys:
            assert key in step, f"Missing key '{key}' in step: {step}"


# =============================================================================
# TESTS 32-35: Multiple Transactions Isolation
# =============================================================================

def test_two_confirmed_transactions_independent(orch, redis_client):
    """32. Submit and process 2 good docs, both CONFIRMED, bc:confirmed LLEN == 2."""
    trace1 = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-A")
    trace2 = orch.submit_document(_DUMMY_BYTES, "pdf", "CLI-B")
    orch.run_one()
    orch.run_one()

    trace1_data = orch.get_trace(trace1)
    trace2_data = orch.get_trace(trace2)
    assert trace1_data["meta"]["status"] == "CONFIRMED"
    assert trace2_data["meta"]["status"] == "CONFIRMED"
    assert redis_client.llen("bc:confirmed") == 2


def test_failed_txn_does_not_affect_confirmed_txn(redis_client):
    """33. Failing job and good job: good job still CONFIRMED."""
    # Two orchestrators sharing redis, different vision clients
    orch_bad_v = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())
    orch_good = OrchestratorV2(redis_client=redis_client, vision_client=MockVisionClient(GOOD_INVOICE))

    # Submit a bad job at MAX_RETRIES (will go directly to failed)
    bad_job = ProcessingJob(
        client_id="CLI-BAD",
        source_format="pdf",
        document_b64="dGVzdA==",
        attempt=OrchestratorV2.MAX_RETRIES,
    )
    redis_client.rpush(Q_NORMAL, bad_job.model_dump_json())

    # Submit a good job
    good_trace_id = orch_good.submit_document(_DUMMY_BYTES, "pdf", "CLI-GOOD")

    # Process bad job
    orch_bad_v.run_one()

    # Process good job
    orch_good.run_one()

    trace = orch_good.get_trace(good_trace_id)
    assert trace["meta"]["status"] == "CONFIRMED"


def test_queue_stats_after_mixed_run(redis_client):
    """34. After mixed run (1 confirmed, 1 cpa_review, 1 retry), stats reflect correct counts."""
    orch_good = OrchestratorV2(redis_client=redis_client, vision_client=MockVisionClient(GOOD_INVOICE))
    orch_missing = OrchestratorV2(redis_client=redis_client, vision_client=MockVisionClient(MISSING_AMOUNT))
    orch_bad_v = OrchestratorV2(redis_client=redis_client, vision_client=BadVisionClient())

    # Submit and process good job
    orch_good.submit_document(_DUMMY_BYTES, "pdf", "CLI-GOOD")
    orch_good.run_one()

    # Submit and process missing-amount job (→ CPA)
    orch_missing.submit_document(_DUMMY_BYTES, "pdf", "CLI-MISSING")
    orch_missing.run_one()

    # Submit and process bad job (→ RETRYING, goes back to normal)
    orch_bad_v.submit_document(_DUMMY_BYTES, "pdf", "CLI-BAD")
    orch_bad_v.run_one()

    stats = orch_good.queue_stats()
    assert stats.confirmed == 1
    assert stats.cpa_review == 1
    assert stats.normal == 1  # retry job is back in normal queue


def test_run_one_returns_none_on_empty_queue(orch):
    """35. Empty queue, run_one() returns None."""
    result = orch.run_one()
    assert result is None
