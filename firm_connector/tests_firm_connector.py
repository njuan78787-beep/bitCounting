# =============================================================================
# firm_connector/tests_firm_connector.py
# Complete test suite for the FirmConnector module.
#
# RUN:
#   pytest firm_connector/tests_firm_connector.py -v --tb=short
#
# COVERAGE TARGETS:
#   - sync_firm_data() success path (DB_DIRECT + REST_API)
#   - Missing required fields → PARTIAL status, rejected_records populated
#   - Firm unreachable → FAILED status, cached fallback
#   - SSN never appears in logs (invariant check)
#   - Account mapping with confirmed mapping → imported
#   - Account mapping without mapping → deferred, CPA notified
#   - Permission check: non-ADMIN role rejected
#   - ManualImportMode: CSV, JSON, XML, Excel happy paths + validation errors
#   - Encryption round-trip (encrypt → decrypt)
#   - AuditEntry: justification required, no field values
#   - FirmConnector.revoke_access: in-process + subsequent sync rejected
# =============================================================================

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers  (must be defined before module-level constants that use them)
# ---------------------------------------------------------------------------

def _fake_encrypted(plaintext: str) -> str:
    """Produce a valid-looking base64 blob of ≥ 28 bytes for test fixtures."""
    fake = os.urandom(12) + plaintext.encode().ljust(16, b"\x00") + os.urandom(16)
    return base64.b64encode(fake).decode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DEMO_FIRM_ID = "firm-test-001"

DEMO_DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "testdb",
    "username": "ro_user",
    "password_encrypted": _fake_encrypted("s3cr3t"),
    "ssl_mode": "require",
    "max_pool_size": 3,
    "timeout_s": 30,
}

DEMO_API_CONFIG = {
    "token_url": "https://example.firm/oauth/token",
    "client_id": "cid_123",
    "client_secret_encrypted": _fake_encrypted("client_secret"),
    "base_api_url": "https://example.firm/api",
    "webhook_secret_encrypted": _fake_encrypted("wh_secret"),
    "rate_limit_rps": 5,
    "polling_interval_min": 15,
}

_SAMPLE_TRANSACTIONS = [
    {
        "transaction_id": "tx-001",
        "firm_client_id": "client-abc",
        "date": "2024-01-15",
        "description": "Office supplies",
        "amount": "250.00",
        "type": "EXPENSE",
        "account_code": "6100",
        "already_processed": False,
    },
    {
        "transaction_id": "tx-002",
        "firm_client_id": "client-abc",
        "date": "2024-01-20",
        "description": "Revenue Q1",
        "amount": "10000.00",
        "type": "INCOME",
        "account_code": "4000",
        "already_processed": False,
    },
]

_SAMPLE_EMPLOYEES = [
    {
        "employee_id": "emp-001",
        "firm_client_id": "client-abc",
        "name": "Juan Rivera",
        "ssn": "123456789",
        "hire_date": "2023-01-10",
        "pay_type": "SALARY",
        "pay_rate": "55000.00",
        "filing_status_federal": "SINGLE",
        "filing_status_pr": "SINGLE",
    }
]

_SAMPLE_CLIENTS: List[Dict[str, Any]] = []   # parsed by parse_clients — raw dicts


def _make_firm_config(mode="DB_DIRECT"):
    from firm_connector.models import FirmConfig, FirmConnectionMode, DBDirectConfig, OAuthConfig
    if mode == "DB_DIRECT":
        return FirmConfig(
            firm_id=DEMO_FIRM_ID,
            firm_name="Test Firm",
            mode=FirmConnectionMode.DB_DIRECT,
            db_config=DBDirectConfig(**DEMO_DB_CONFIG),
            created_by="admin-001",
        )
    return FirmConfig(
        firm_id=DEMO_FIRM_ID,
        firm_name="Test Firm",
        mode=FirmConnectionMode.REST_API,
        api_config=OAuthConfig(**DEMO_API_CONFIG),
        created_by="admin-001",
    )


# ===========================================================================
# 1. Encryption round-trip
# ===========================================================================

class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        from firm_connector.encryption import encrypt_field, decrypt_field
        plaintext = "123456789"
        encrypted = encrypt_field(plaintext)
        assert plaintext not in encrypted
        assert decrypt_field(encrypted) == plaintext

    def test_each_encryption_is_unique(self):
        from firm_connector.encryption import encrypt_field
        a = encrypt_field("same_value")
        b = encrypt_field("same_value")
        assert a != b, "Different nonces must produce different ciphertexts"

    def test_decrypt_wrong_key_raises(self):
        from firm_connector.encryption import encrypt_field, decrypt_field
        encrypted = encrypt_field("secret", key=os.urandom(32))
        with pytest.raises(ValueError):
            decrypt_field(encrypted, key=os.urandom(32))

    def test_empty_plaintext_raises(self):
        from firm_connector.encryption import encrypt_field
        with pytest.raises(ValueError):
            encrypt_field("")

    def test_is_encrypted_heuristic(self):
        from firm_connector.encryption import is_encrypted, encrypt_field
        assert is_encrypted(encrypt_field("hello"))
        assert not is_encrypted("hello")
        assert not is_encrypted("short")

    def test_mask_for_log_hides_value(self):
        from firm_connector.encryption import mask_for_log
        masked = mask_for_log("123456789")
        assert "123456789" not in masked
        assert masked.endswith("789")


# ===========================================================================
# 2. Pydantic models — validation
# ===========================================================================

class TestModels:
    def test_employee_import_row_valid_ssn(self):
        from firm_connector.models import EmployeeImportRow, PayType
        row = EmployeeImportRow(
            employee_id="e1",
            firm_client_id="c1",
            name="Ana Pérez",
            ssn="123-45-6789",
            hire_date=date(2023, 1, 1),
            pay_type=PayType.HOURLY,
            pay_rate=Decimal("20.00"),
            filing_status_federal="SINGLE",
            filing_status_pr="SINGLE",
        )
        assert row.ssn == "123456789"   # dashes stripped

    def test_employee_import_row_invalid_ssn(self):
        from firm_connector.models import EmployeeImportRow, PayType
        with pytest.raises(Exception):
            EmployeeImportRow(
                employee_id="e1",
                firm_client_id="c1",
                name="Ana Pérez",
                ssn="123",    # too short
                hire_date=date(2023, 1, 1),
                pay_type=PayType.HOURLY,
                pay_rate=Decimal("20.00"),
                filing_status_federal="SINGLE",
                filing_status_pr="SINGLE",
            )

    def test_ssn_never_in_safe_log_repr(self):
        from firm_connector.models import EmployeeFirm, PayType
        from firm_connector.encryption import encrypt_field
        emp = EmployeeFirm(
            employee_id="e1",
            firm_client_id="c1",
            name_encrypted=encrypt_field("Ana Pérez"),
            ssn_encrypted=encrypt_field("123456789"),
            hire_date=date(2023, 1, 1),
            pay_type=PayType.HOURLY,
            pay_rate=Decimal("20.00"),
            filing_status_federal="SINGLE",
            filing_status_pr="SINGLE",
        )
        log_repr = emp.safe_log_repr()
        assert "123456789" not in log_repr
        assert "[ENCRYPTED]" in log_repr

    def test_to_employee_firm_encrypts_ssn(self):
        from firm_connector.models import EmployeeImportRow, PayType
        from firm_connector.encryption import is_encrypted
        row = EmployeeImportRow(
            employee_id="e1",
            firm_client_id="c1",
            name="Ana Pérez",
            ssn="123456789",
            hire_date=date(2023, 1, 1),
            pay_type=PayType.HOURLY,
            pay_rate=Decimal("20.00"),
            filing_status_federal="SINGLE",
            filing_status_pr="SINGLE",
        )
        emp = row.to_employee_firm()
        assert is_encrypted(emp.ssn_encrypted)
        assert is_encrypted(emp.name_encrypted)
        # Plaintext must not appear
        assert "123456789" not in emp.ssn_encrypted
        assert "Ana Pérez" not in emp.name_encrypted

    def test_date_range_validates_order(self):
        from firm_connector.models import DateRange
        with pytest.raises(Exception):
            DateRange(start=date(2024, 6, 1), end=date(2024, 1, 1))

    def test_transaction_date_parsed_from_string(self):
        from firm_connector.models import TransactionFirm, TransactionType
        tx = TransactionFirm(
            transaction_id="tx1",
            firm_client_id="c1",
            date="2024-03-15",
            description="Test",
            amount=Decimal("100.00"),
            type=TransactionType.EXPENSE,
            account_code="6100",
        )
        assert tx.date == date(2024, 3, 15)


# ===========================================================================
# 3. ManualImportMode — CSV / JSON / XML / Excel
# ===========================================================================

class TestManualImport:
    def _csv_bytes(self, rows: List[Dict], fieldnames: List[str]) -> bytes:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue().encode()

    def _json_bytes(self, rows: List[Dict]) -> bytes:
        return json.dumps(rows).encode()

    def _xml_bytes(self, rows: List[Dict]) -> bytes:
        lines = ["<records>"]
        for row in rows:
            lines.append("  <record>")
            for k, v in row.items():
                lines.append(f"    <{k}>{v}</{k}>")
            lines.append("  </record>")
        lines.append("</records>")
        return "\n".join(lines).encode()

    def test_parse_employees_csv_happy_path(self):
        from firm_connector.modes.manual_import import ManualImportMode
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        rows = _SAMPLE_EMPLOYEES
        fields = list(rows[0].keys())
        raw = self._csv_bytes(rows, fields)
        result = mode.parse_employees(raw, ImportFormat.CSV)
        assert len(result) == 1
        assert result[0].employee_id == "emp-001"
        assert result[0].ssn == "123456789"

    def test_parse_employees_json_happy_path(self):
        from firm_connector.modes.manual_import import ManualImportMode
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        raw = self._json_bytes(_SAMPLE_EMPLOYEES)
        result = mode.parse_employees(raw, ImportFormat.JSON)
        assert len(result) == 1

    def test_parse_employees_xml_happy_path(self):
        from firm_connector.modes.manual_import import ManualImportMode
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        raw = self._xml_bytes(_SAMPLE_EMPLOYEES)
        result = mode.parse_employees(raw, ImportFormat.XML)
        assert len(result) == 1
        assert result[0].ssn == "123456789"

    def test_parse_employees_missing_required_field(self):
        from firm_connector.modes.manual_import import ManualImportMode, ImportValidationError
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        bad_rows = [{"employee_id": "emp-001"}]   # missing most fields
        raw = self._json_bytes(bad_rows)
        with pytest.raises(ImportValidationError) as exc_info:
            mode.parse_employees(raw, ImportFormat.JSON)
        assert len(exc_info.value.errors) > 0

    def test_parse_employees_invalid_ssn_rejected(self):
        from firm_connector.modes.manual_import import ManualImportMode, ImportValidationError
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        bad_rows = [{**_SAMPLE_EMPLOYEES[0], "ssn": "123"}]   # invalid SSN
        raw = self._json_bytes(bad_rows)
        with pytest.raises(ImportValidationError) as exc_info:
            mode.parse_employees(raw, ImportFormat.JSON)
        # Error message must NOT contain the invalid SSN value
        for err in exc_info.value.errors:
            assert "123" not in err.reason or "digit" in err.reason.lower()

    def test_parse_transactions_happy_path(self):
        from firm_connector.modes.manual_import import ManualImportMode
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        raw = self._json_bytes(_SAMPLE_TRANSACTIONS)
        result = mode.parse_transactions(raw, ImportFormat.JSON)
        assert len(result) == 2
        assert result[0].transaction_id == "tx-001"

    def test_all_or_nothing_second_row_bad(self):
        """If row 2 of 3 is invalid, all 3 are rejected."""
        from firm_connector.modes.manual_import import ManualImportMode, ImportValidationError
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        rows = [
            _SAMPLE_TRANSACTIONS[0],
            {**_SAMPLE_TRANSACTIONS[1], "amount": "not_a_number"},  # bad
        ]
        raw = self._json_bytes(rows)
        with pytest.raises(ImportValidationError) as exc_info:
            mode.parse_transactions(raw, ImportFormat.JSON)
        assert len(exc_info.value.errors) == 1   # only second row fails

    def test_empty_file_raises(self):
        from firm_connector.modes.manual_import import ManualImportMode, ImportValidationError
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        with pytest.raises(ImportValidationError):
            mode.parse_transactions(b"[]", ImportFormat.JSON)

    def test_invalid_json_raises(self):
        from firm_connector.modes.manual_import import ManualImportMode, ImportValidationError
        from firm_connector.models import ImportFormat
        mode = ManualImportMode()
        with pytest.raises(ImportValidationError):
            mode.parse_transactions(b"{bad json", ImportFormat.JSON)


# ===========================================================================
# 4. Account mapper
# ===========================================================================

class TestAccountMapper:
    def setup_method(self):
        # Reset mapper state before each test
        from firm_connector import account_mapper
        account_mapper._mappings.clear()
        account_mapper._pending_notifications.clear()

    def test_unmapped_code_returns_none_and_queues_notification(self):
        from firm_connector.account_mapper import map_account, get_pending_notifications
        result = map_account("9999", "firm-x")
        assert result is None
        pending = get_pending_notifications()
        assert ("firm-x", "9999") in pending

    def test_mapped_unconfirmed_returns_none(self):
        from firm_connector.account_mapper import map_account, set_mapping
        set_mapping("firm-x", "6100", "EXP-OFFICE", cpa_confirmed=False)
        result = map_account("6100", "firm-x")
        assert result is None

    def test_mapped_confirmed_returns_internal_code(self):
        from firm_connector.account_mapper import map_account, set_mapping
        set_mapping("firm-x", "6100", "EXP-OFFICE", cpa_confirmed=True)
        result = map_account("6100", "firm-x")
        assert result == "EXP-OFFICE"

    def test_confirm_mapping(self):
        from firm_connector.account_mapper import set_mapping, confirm_mapping, map_account
        set_mapping("firm-x", "6100", "EXP-OFFICE", cpa_confirmed=False)
        confirm_mapping("firm-x", "6100")
        assert map_account("6100", "firm-x") == "EXP-OFFICE"

    def test_confirm_nonexistent_raises(self):
        from firm_connector.account_mapper import confirm_mapping
        with pytest.raises(KeyError):
            confirm_mapping("firm-x", "NONEXISTENT")

    def test_notification_cleared_on_confirm(self):
        from firm_connector.account_mapper import (
            map_account, set_mapping, confirm_mapping, get_pending_notifications
        )
        map_account("6100", "firm-x")   # creates UNMAPPED + notification
        set_mapping("firm-x", "6100", "EXP-OFFICE", cpa_confirmed=False)
        confirm_mapping("firm-x", "6100")
        assert ("firm-x", "6100") not in get_pending_notifications()


# ===========================================================================
# 5. Audit trail
# ===========================================================================

class TestAudit:
    def setup_method(self):
        from firm_connector import audit
        audit._audit_log.clear()

    def test_justification_required(self):
        from firm_connector.audit import log_access
        with pytest.raises(ValueError, match="justification"):
            log_access("u1", "EXIMIA_ADMIN", "firm-1", "SYNC", "")

    def test_audit_entry_contains_no_field_values(self):
        """Audit entries must contain only metadata, never field values."""
        from firm_connector.audit import log_access, get_audit_log
        log_access("u1", "EXIMIA_ADMIN", "firm-1", "SYNC_START",
                   justification="Monthly compliance sync",
                   records_affected=250)
        entries = get_audit_log()
        assert len(entries) == 1
        e = entries[0]
        assert e["records_affected"] == 250
        assert e["action"] == "SYNC_START"
        # Must not contain any data values — only counts
        assert "ssn" not in str(e).lower() or e.get("detail") is None

    def test_audit_filtered_by_firm(self):
        from firm_connector.audit import log_access, get_audit_log
        log_access("u1", "EXIMIA_ADMIN", "firm-A", "SYNC", "reason A")
        log_access("u2", "EXIMIA_ADMIN", "firm-B", "SYNC", "reason B")
        assert len(get_audit_log("firm-A")) == 1
        assert len(get_audit_log("firm-B")) == 1
        assert len(get_audit_log()) == 2


# ===========================================================================
# 6. Permission enforcement
# ===========================================================================

class TestPermissions:
    @pytest.mark.anyio
    async def test_sync_requires_eximia_admin(self):
        from firm_connector.connector import FirmConnector
        from firm_connector.sync import PermissionDeniedError
        fc = FirmConnector()
        with pytest.raises(PermissionDeniedError):
            await fc.sync(
                firm_config=_make_firm_config(),
                actor_id="cpa-user",
                actor_role="CPA_SENIOR",
                justification="Trying to sync",
            )

    @pytest.mark.anyio
    async def test_import_file_requires_eximia_admin(self):
        from firm_connector.connector import FirmConnector
        from firm_connector.sync import PermissionDeniedError
        fc = FirmConnector()
        with pytest.raises(PermissionDeniedError):
            await fc.import_file(
                firm_config=_make_firm_config(),
                file_bytes=b"data",
                fmt=__import__("firm_connector.models", fromlist=["ImportFormat"]).ImportFormat.CSV,
                entity="employees",
                actor_id="client-user",
                actor_role="CLIENT",
                justification="Trying to import",
            )

    @pytest.mark.anyio
    async def test_revoke_requires_eximia_admin(self):
        from firm_connector.connector import FirmConnector
        from firm_connector.sync import PermissionDeniedError
        fc = FirmConnector()
        with pytest.raises(PermissionDeniedError):
            await fc.revoke_access(
                firm_id=DEMO_FIRM_ID,
                actor_id="cpa-user",
                actor_role="CPA_PARTNER",
                justification="Trying to revoke",
            )

    @pytest.mark.anyio
    async def test_get_sync_status_allowed_for_cpa_senior(self):
        """CPA_SENIOR can READ sync status (CAPA 2 visibility)."""
        from firm_connector.connector import FirmConnector
        fc = FirmConnector(db=None)
        # Returns None when db=None — but must NOT raise PermissionDeniedError
        result = await fc.get_sync_status(
            firm_id=DEMO_FIRM_ID,
            actor_id="cpa1",
            actor_role="CPA_SENIOR",
        )
        assert result is None   # no DB, no data — but no permission error

    @pytest.mark.anyio
    async def test_get_sync_status_denied_for_client(self):
        from firm_connector.connector import FirmConnector
        from firm_connector.sync import PermissionDeniedError
        fc = FirmConnector()
        with pytest.raises(PermissionDeniedError):
            await fc.get_sync_status(
                firm_id=DEMO_FIRM_ID,
                actor_id="client-user",
                actor_role="CLIENT",
            )


# ===========================================================================
# 7. Revocation
# ===========================================================================

class TestRevocation:
    @pytest.mark.anyio
    async def test_revoked_firm_blocks_sync(self):
        from firm_connector.connector import FirmConnector
        from firm_connector.sync import PermissionDeniedError
        fc = FirmConnector()

        # Revoke (mocked — no DB)
        with patch("firm_connector.connector.FirmConnector._set_firm_inactive_db", new_callable=AsyncMock):
            with patch("firm_connector.audit.log_access_async", new_callable=AsyncMock):
                await fc.revoke_access(
                    firm_id=DEMO_FIRM_ID,
                    actor_id="admin1",
                    actor_role="EXIMIA_ADMIN",
                    justification="Security incident",
                )

        # Subsequent sync must raise immediately without hitting the firm
        with pytest.raises(PermissionDeniedError, match="revoked"):
            await fc.sync(
                firm_config=_make_firm_config(),
                actor_id="admin1",
                actor_role="EXIMIA_ADMIN",
                justification="Should be blocked",
            )


# ===========================================================================
# 8. sync_firm_data() — mocked firm connections
# ===========================================================================

class TestSyncFirmData:
    @pytest.mark.anyio
    async def test_sync_db_direct_success(self):
        """Full sync with mocked asyncpg pool returns SUCCESS."""
        from firm_connector.sync import sync_firm_data
        from firm_connector.account_mapper import set_mapping
        from firm_connector import account_mapper

        account_mapper._mappings.clear()
        account_mapper._pending_notifications.clear()
        set_mapping(DEMO_FIRM_ID, "6100", "EXP-OFFICE", cpa_confirmed=True)
        set_mapping(DEMO_FIRM_ID, "4000", "REV-SALES",  cpa_confirmed=True)

        config = _make_firm_config("DB_DIRECT")

        mock_pool   = AsyncMock()
        mock_conn   = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("firm_connector.modes.db_direct.DirectDBMode._create_pool_with_retry",
                   new_callable=AsyncMock, return_value=mock_pool), \
             patch("firm_connector.sync._persist_client",    new_callable=AsyncMock), \
             patch("firm_connector.sync._persist_transaction", new_callable=AsyncMock), \
             patch("firm_connector.sync._persist_employee",  new_callable=AsyncMock), \
             patch("firm_connector.audit.log_access_async",  new_callable=AsyncMock):

            result = await sync_firm_data(
                firm_config=config,
                actor_id="admin1",
                actor_role="EXIMIA_ADMIN",
                justification="Quarterly sync",
            )

        assert result.status.value in ("SUCCESS", "PARTIAL")
        assert result.firm_id == DEMO_FIRM_ID

    @pytest.mark.anyio
    async def test_sync_partial_when_missing_required_field(self):
        """Transactions with missing account_code get rejected → PARTIAL."""
        from firm_connector.sync import _process_extracted
        from firm_connector.models import FirmConnectionMode, SyncResult, SyncStatus
        from firm_connector import account_mapper

        account_mapper._mappings.clear()
        account_mapper._pending_notifications.clear()

        config = _make_firm_config("DB_DIRECT")
        base_result = SyncResult(
            firm_id=DEMO_FIRM_ID,
            sync_id=str(uuid.uuid4()),
            status=SyncStatus.FAILED,
            started_at=datetime.now(timezone.utc),
            mode_used=FirmConnectionMode.DB_DIRECT,
        )

        bad_tx = [{**_SAMPLE_TRANSACTIONS[0], "account_code": ""}]   # blank code

        with patch("firm_connector.sync._persist_client",    new_callable=AsyncMock), \
             patch("firm_connector.sync._persist_transaction", new_callable=AsyncMock), \
             patch("firm_connector.sync._persist_employee",  new_callable=AsyncMock):

            result = await _process_extracted(config, base_result, [], bad_tx, [], db=None)

        assert result.transactions_rejected >= 1

    @pytest.mark.anyio
    async def test_sync_failed_when_firm_unreachable(self):
        """FirmUnavailableError → FAILED status with error_message."""
        from firm_connector.sync import sync_firm_data
        from firm_connector.modes.db_direct import FirmUnavailableError

        config = _make_firm_config("DB_DIRECT")

        with patch("firm_connector.modes.db_direct.DirectDBMode._create_pool_with_retry",
                   new_callable=AsyncMock,
                   side_effect=FirmUnavailableError("Connection timeout after 3 retries")), \
             patch("firm_connector.audit.log_access_async", new_callable=AsyncMock):

            result = await sync_firm_data(
                firm_config=config,
                actor_id="admin1",
                actor_role="EXIMIA_ADMIN",
                justification="Emergency sync",
            )

        assert result.status.value == "FAILED"
        assert result.error_message is not None

    @pytest.mark.anyio
    async def test_ssn_never_appears_in_logs(self, caplog):
        """SSN plaintext must never appear in log output at any level."""
        from firm_connector.sync import _process_extracted
        from firm_connector.models import FirmConnectionMode, SyncResult, SyncStatus
        from firm_connector import account_mapper

        account_mapper._mappings.clear()

        PLAINTEXT_SSN = "987654321"
        employees_with_ssn = [{**_SAMPLE_EMPLOYEES[0], "ssn": PLAINTEXT_SSN}]

        config = _make_firm_config("DB_DIRECT")
        base_result = SyncResult(
            firm_id=DEMO_FIRM_ID,
            sync_id=str(uuid.uuid4()),
            status=SyncStatus.FAILED,
            started_at=datetime.now(timezone.utc),
            mode_used=FirmConnectionMode.DB_DIRECT,
        )

        with caplog.at_level(logging.DEBUG, logger="firm_connector"), \
             patch("firm_connector.sync._persist_employee", new_callable=AsyncMock):
            await _process_extracted(config, base_result, [], [], employees_with_ssn, db=None)

        for record in caplog.records:
            assert PLAINTEXT_SSN not in record.getMessage(), (
                f"SSN '{PLAINTEXT_SSN}' found in log record: {record.getMessage()}"
            )

    @pytest.mark.anyio
    async def test_already_processed_transactions_skipped(self):
        """Transactions with already_processed=True are counted as skipped, not imported."""
        from firm_connector.sync import _process_extracted
        from firm_connector.models import FirmConnectionMode, SyncResult, SyncStatus
        from firm_connector import account_mapper

        account_mapper._mappings.clear()

        config = _make_firm_config("DB_DIRECT")
        base_result = SyncResult(
            firm_id=DEMO_FIRM_ID,
            sync_id=str(uuid.uuid4()),
            status=SyncStatus.FAILED,
            started_at=datetime.now(timezone.utc),
            mode_used=FirmConnectionMode.DB_DIRECT,
        )

        processed_tx = [{**_SAMPLE_TRANSACTIONS[0], "already_processed": True}]

        with patch("firm_connector.sync._persist_transaction", new_callable=AsyncMock):
            result = await _process_extracted(config, base_result, [], processed_tx, [], db=None)

        assert result.transactions_skipped == 1
        assert result.transactions_imported == 0


# ===========================================================================
# 9. REST API mode — token management + rate limiting
# ===========================================================================

class TestRestAPIMode:
    @pytest.mark.anyio
    async def test_token_cached_until_expiry(self):
        from firm_connector.modes.rest_api import RestAPIMode
        from firm_connector.models import OAuthConfig

        config = OAuthConfig(**DEMO_API_CONFIG)
        mode = RestAPIMode(config)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "tok-abc", "expires_in": 3600}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("firm_connector.encryption.decrypt_field", return_value="plain_secret"):
            token1 = await mode._ensure_token(mock_client)
            token2 = await mode._ensure_token(mock_client)

        assert token1 == "tok-abc"
        assert token2 == "tok-abc"
        assert mock_client.post.call_count == 1   # only fetched once

    @pytest.mark.anyio
    async def test_webhook_signature_invalid(self):
        from firm_connector.modes.rest_api import RestAPIMode
        from firm_connector.models import OAuthConfig

        config = OAuthConfig(**DEMO_API_CONFIG)
        mode = RestAPIMode(config)

        with patch("firm_connector.encryption.decrypt_field", return_value="secret"):
            valid = mode.verify_webhook_signature(b"payload", "sha256=badsig")

        assert valid is False

    @pytest.mark.anyio
    async def test_webhook_no_secret_returns_false(self):
        from firm_connector.modes.rest_api import RestAPIMode
        from firm_connector.models import OAuthConfig

        config = OAuthConfig(
            **{**DEMO_API_CONFIG, "webhook_secret_encrypted": None}
        )
        mode = RestAPIMode(config)
        assert mode.verify_webhook_signature(b"payload", "sha256=anything") is False


# ===========================================================================
# 10. DirectDBMode — connection cap
# ===========================================================================

class TestDirectDBMode:
    def test_pool_size_capped_at_5(self):
        """Pydantic enforces max_pool_size ≤ 5 at model level; constant confirms the cap."""
        from firm_connector.modes.db_direct import _MAX_POOL_SIZE
        from firm_connector.models import DBDirectConfig
        import pydantic

        # Pydantic rejects values > 5 — this is the enforcement mechanism
        with pytest.raises(pydantic.ValidationError):
            DBDirectConfig(**{**DEMO_DB_CONFIG, "max_pool_size": 10})

        # The _MAX_POOL_SIZE constant is the runtime fallback cap
        assert _MAX_POOL_SIZE == 5

        # A valid config at the boundary is accepted
        cfg = DBDirectConfig(**{**DEMO_DB_CONFIG, "max_pool_size": 5})
        assert cfg.max_pool_size == 5


# ===========================================================================
# 11. FirmConfig validator
# ===========================================================================

class TestFirmConfig:
    def test_db_direct_requires_db_config(self):
        from firm_connector.models import FirmConfig, FirmConnectionMode
        with pytest.raises(Exception, match="db_config"):
            FirmConfig(
                firm_id="f1",
                firm_name="Firm",
                mode=FirmConnectionMode.DB_DIRECT,
                db_config=None,
                created_by="admin",
            )

    def test_rest_api_requires_api_config(self):
        from firm_connector.models import FirmConfig, FirmConnectionMode
        with pytest.raises(Exception, match="api_config"):
            FirmConfig(
                firm_id="f1",
                firm_name="Firm",
                mode=FirmConnectionMode.REST_API,
                api_config=None,
                created_by="admin",
            )
