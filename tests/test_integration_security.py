# =============================================================================
# tests/test_integration_security.py
# Tests de seguridad e integración para Bit-Counting.
#
# Verifica:
#   - CLIENT A no puede ver datos de CLIENT B (scope isolation)
#   - CPA sin MFA verificado no puede aprobar transacciones
#   - SQL injection no afecta el sistema (inputs sanitizados vía Pydantic)
#   - EXIMIA_ADMIN accede a recursos de cualquier cliente
#   - Tokens inválidos o expirados son rechazados
#   - Rate limiting básico funciona
#   - Campos críticos del log son inmutables (no UPDATE/DELETE)
# =============================================================================

from __future__ import annotations

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from api.main import app
from api.auth import Role, create_test_token


client = TestClient(app)


# =============================================================================
# Helpers de autenticación
# =============================================================================

def _client_headers(client_id: str = "client-demo-001") -> dict:
    token = create_test_token(role=Role.CLIENT, client_id=client_id)
    return {"Authorization": f"Bearer {token}"}


def _cpa_headers(client_id: str = "client-demo-001") -> dict:
    token = create_test_token(role=Role.CPA_PARTNER, client_id=client_id)
    return {"Authorization": f"Bearer {token}"}


def _admin_headers() -> dict:
    token = create_test_token(role=Role.EXIMIA_ADMIN, client_id=None)
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# TestClientScopeIsolation
# =============================================================================

class TestClientScopeIsolation:
    """Cliente A no puede acceder a datos de Cliente B."""

    def test_client_a_can_read_own_transactions(self):
        resp = client.get(
            "/api/v1/transactions",
            params={"client_id": "client-demo-001"},
            headers=_client_headers("client-demo-001"),
        )
        assert resp.status_code == 200

    def test_client_a_cannot_read_client_b_transactions(self):
        """CLIENT con client_id A intentando leer de B → 403 Forbidden."""
        resp = client.get(
            "/api/v1/transactions",
            params={"client_id": "client-demo-002"},
            headers=_client_headers("client-demo-001"),
        )
        assert resp.status_code == 403

    def test_client_a_cannot_upload_for_client_b(self):
        """CLIENT A no puede subir documentos en nombre de CLIENT B."""
        payload = {
            "raw_text": "Factura de prueba",
            "client_id": "client-demo-002",   # distinto al token
            "source_format": "JSON",
        }
        resp = client.post(
            "/api/v1/transactions/upload",
            json=payload,
            headers=_client_headers("client-demo-001"),
        )
        assert resp.status_code == 403

    def test_admin_can_read_any_client_transactions(self):
        """EXIMIA_ADMIN puede leer cualquier cliente sin restricción de scope."""
        resp = client.get(
            "/api/v1/transactions",
            params={"client_id": "client-demo-001"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_cpa_can_read_assigned_client(self):
        """CPA_PARTNER puede leer datos del cliente que tiene asignado."""
        resp = client.get(
            "/api/v1/transactions",
            params={"client_id": "client-demo-001"},
            headers=_cpa_headers("client-demo-001"),
        )
        assert resp.status_code == 200

    def test_scope_check_on_transaction_detail(self, api_client, client_headers, client_b_headers):
        """GET /transactions/{txn-demo-001} con token de CLIENT B → 403."""
        # txn-demo-001 pertenece a client-demo-001; token B no puede verla
        resp = api_client.get("/api/v1/transactions/txn-demo-001", headers=client_b_headers)
        assert resp.status_code == 403


# =============================================================================
# TestMFAEnforcement
# =============================================================================

class TestMFAEnforcement:
    """CPA sin MFA verificado no puede realizar acciones protegidas."""

    def test_cpa_without_mfa_cannot_approve(self, api_client, no_mfa_headers):
        """Token con mfa_verified=False → 401 o 403 en aprobación."""
        resp = api_client.post(
            "/api/v1/cpa/approve/txn-demo-001",
            json={"action": "APPROVE", "cpa_license": "CPA-PR-001234"},
            headers=no_mfa_headers,
        )
        assert resp.status_code in (401, 403)

    def test_cpa_without_mfa_cannot_access_review_queue(self, api_client, no_mfa_headers):
        """MFA pendiente → acceso a cola de revisión denegado."""
        resp = api_client.get("/api/v1/cpa/review-queue", headers=no_mfa_headers)
        assert resp.status_code in (401, 403)

    def test_cpa_without_mfa_cannot_list_pauses(self, api_client, no_mfa_headers):
        """Listar pausas requiere MFA completo (get_current_user checa mfa_verified)."""
        resp = api_client.get("/api/v1/cpa/pauses", headers=no_mfa_headers)
        assert resp.status_code == 403

    def test_valid_cpa_token_can_access_review_queue(self, api_client, cpa_headers):
        """Token CPA con MFA verificado → acceso a cola concedido."""
        resp = api_client.get("/api/v1/cpa/review-queue", headers=cpa_headers)
        assert resp.status_code == 200


# =============================================================================
# TestInvalidTokens
# =============================================================================

class TestInvalidTokens:
    """Tokens inválidos o ausentes son rechazados con 401."""

    def test_no_token_returns_401(self, api_client):
        resp = api_client.get("/api/v1/transactions")
        assert resp.status_code == 401

    def test_invalid_bearer_token_returns_401(self, api_client):
        resp = api_client.get(
            "/api/v1/transactions",
            headers={"Authorization": "Bearer INVALID_TOKEN_GARBAGE"},
        )
        assert resp.status_code == 401

    def test_malformed_authorization_header_returns_401(self, api_client):
        resp = api_client.get(
            "/api/v1/transactions",
            headers={"Authorization": "NotBearer abc123"},
        )
        assert resp.status_code == 401

    def test_empty_authorization_header_returns_401(self, api_client):
        resp = api_client.get(
            "/api/v1/transactions",
            headers={"Authorization": ""},
        )
        assert resp.status_code == 401

    def test_health_endpoint_does_not_require_auth(self, api_client):
        """GET /health es público — no necesita token."""
        resp = api_client.get("/health")
        assert resp.status_code == 200


# =============================================================================
# TestSQLInjectionRejection
# =============================================================================

class TestSQLInjectionRejection:
    """SQL injection en inputs no afecta el sistema — validado por Pydantic."""

    def test_sql_injection_in_client_id_param_is_handled(self, api_client, client_headers):
        """Un SQL injection en client_id es rechazado o ignorado sin exception 500."""
        sql_injection = "client-demo-001'; DROP TABLE transactions; --"
        resp = api_client.get(
            "/api/v1/transactions",
            params={"client_id": sql_injection},
            headers=client_headers,
        )
        # Debe retornar 403 (scope violation) o 422 (validation error) — NUNCA 500
        assert resp.status_code in (400, 403, 422)

    def test_sql_injection_in_upload_raw_text(self, api_client, client_headers):
        """SQL injection embebido en raw_text no ejecuta SQL — es solo texto."""
        payload = {
            "raw_text": "'; SELECT * FROM users; --",
            "client_id": "client-demo-001",
            "source_format": "JSON",
        }
        resp = api_client.post(
            "/api/v1/transactions/upload",
            json=payload,
            headers=client_headers,
        )
        # El endpoint acepta el texto como dato, no como query SQL
        assert resp.status_code in (200, 202, 400, 422)
        assert resp.status_code != 500

    def test_xss_payload_in_vendor_field_does_not_cause_500(self, api_client, client_headers):
        """XSS en campos de texto no rompe el servidor."""
        payload = {
            "raw_text": "<script>alert('xss')</script>",
            "client_id": "client-demo-001",
            "source_format": "JSON",
        }
        resp = api_client.post(
            "/api/v1/transactions/upload",
            json=payload,
            headers=client_headers,
        )
        assert resp.status_code != 500

    def test_extremely_long_input_does_not_crash(self, api_client, client_headers):
        """Input muy largo no produce error 500 — el servidor valida o trunca."""
        payload = {
            "raw_text": "A" * 10_000,
            "client_id": "client-demo-001",
            "source_format": "JSON",
        }
        resp = api_client.post(
            "/api/v1/transactions/upload",
            json=payload,
            headers=client_headers,
        )
        assert resp.status_code != 500

    def test_null_bytes_in_text_do_not_crash(self, api_client, client_headers):
        """Bytes nulos en el payload no producen error 500."""
        payload = {
            "raw_text": "factura\x00test",
            "client_id": "client-demo-001",
            "source_format": "JSON",
        }
        resp = api_client.post(
            "/api/v1/transactions/upload",
            json=payload,
            headers=client_headers,
        )
        assert resp.status_code != 500


# =============================================================================
# TestRoleBasedAccess
# =============================================================================

class TestRoleBasedAccess:
    """Roles tienen acceso solo a sus recursos permitidos."""

    def test_client_cannot_access_admin_normative_updates(self, api_client, client_headers):
        """CLIENT no puede acceder a endpoints de administración normativa."""
        resp = api_client.get("/api/v1/admin/normative-updates", headers=client_headers)
        assert resp.status_code in (401, 403)

    def test_client_cannot_activate_normative_update(self, api_client, client_headers):
        """CLIENT no puede activar actualizaciones normativas (solo ADMIN)."""
        resp = api_client.post(
            "/api/v1/admin/normative-updates/update-001/activate",
            json={"cpa_license": "CPA-001", "digital_sig": "abc123", "effective_date": "2026-05-01"},
            headers=client_headers,
        )
        assert resp.status_code in (401, 403, 422)

    def test_cpa_cannot_access_admin_normative(self, api_client, cpa_headers):
        """CPA_PARTNER no puede acceder a administración de actualizaciones normativas."""
        resp = api_client.get("/api/v1/admin/normative-updates", headers=cpa_headers)
        assert resp.status_code in (401, 403)

    def test_admin_can_access_admin_normative_updates(self, api_client, admin_headers):
        """EXIMIA_ADMIN sí puede acceder a endpoints de administración normativa."""
        resp = api_client.get("/api/v1/admin/normative-updates", headers=admin_headers)
        assert resp.status_code in (200, 404)  # 404 si no hay datos, pero no 403

    def test_client_cannot_approve_normative_updates(self, api_client, client_headers):
        """Aprobación de actualizaciones normativas es solo para CPA/Admin."""
        resp = api_client.post(
            "/api/v1/normative/updates/test-001/approve",
            json={"update_id": "test-001", "cpa_license": "CPA-001"},
            headers=client_headers,
        )
        assert resp.status_code in (401, 403, 422)


# =============================================================================
# TestAgentLogImmutability
# =============================================================================

class TestAgentLogImmutability:
    """El log de decisiones del agente es inmutable — no UPDATE/DELETE."""

    def test_orchestrator_log_entries_are_frozen(self):
        """Las entradas del log del Orchestrator son Pydantic frozen models."""
        from agents.flow import process_document
        from tests.conftest import CLEAN_INVOICE_RAW

        result = process_document(CLEAN_INVOICE_RAW, source_format="manual")
        assert len(result.log) > 0

        # Intentar modificar una entrada debe fallar (Pydantic frozen=True)
        entry = result.log[0]
        with pytest.raises(Exception):
            entry.agent_name = "MODIFIED"

    def test_flow_result_is_frozen(self):
        """FlowResult es frozen — no se puede alterar el estado final."""
        from agents.flow import process_document
        from tests.conftest import CLEAN_INVOICE_RAW

        result = process_document(CLEAN_INVOICE_RAW, source_format="manual")
        with pytest.raises(Exception):
            result.final_decision = "HACKED"

    def test_intake_output_is_frozen(self):
        """IntakeOutput es frozen — los datos extraídos no se pueden alterar."""
        from agents.flow import process_document
        from tests.conftest import CLEAN_INVOICE_RAW

        result = process_document(CLEAN_INVOICE_RAW, source_format="manual")
        with pytest.raises(Exception):
            result.intake_output.amount = Decimal("9999.00")

    def test_auditor_verification_is_frozen(self):
        """AuditorVerification es frozen — las verificaciones no se pueden alterar."""
        from agents.flow import process_document
        from tests.conftest import CLEAN_INVOICE_RAW

        result = process_document(CLEAN_INVOICE_RAW, source_format="manual")
        if result.auditor_verification:
            with pytest.raises(Exception):
                result.auditor_verification.verified = False

    def test_fiscal_output_is_frozen(self):
        """FiscalOutput es frozen — la obligación tributaria no se puede alterar."""
        from agents.flow import process_document
        from tests.conftest import CLEAN_INVOICE_RAW

        result = process_document(CLEAN_INVOICE_RAW, source_format="manual")
        if result.fiscal_output:
            with pytest.raises(Exception):
                result.fiscal_output.tax_liability = Decimal("0.00")

    def test_orchestrator_log_is_tuple_not_list(self):
        """El log del Orchestrator es una tupla (inmutable), no una lista."""
        from agents.flow import process_document
        from tests.conftest import CLEAN_INVOICE_RAW

        result = process_document(CLEAN_INVOICE_RAW, source_format="manual")
        assert isinstance(result.log, tuple), "result.log debe ser tuple, no list"

    def test_cannot_append_to_log_tuple(self):
        """No se puede agregar entradas al log después de creado."""
        from agents.flow import process_document
        from tests.conftest import CLEAN_INVOICE_RAW

        result = process_document(CLEAN_INVOICE_RAW, source_format="manual")
        with pytest.raises(AttributeError):
            result.log.append("FAKE_ENTRY")  # type: ignore[attr-defined]


# =============================================================================
# TestSecurityHeaders
# =============================================================================

class TestSecurityHeaders:
    """Los headers de seguridad están presentes en todas las respuestas."""

    def test_health_response_has_security_headers(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        # Al menos uno de los headers de seguridad debe estar presente
        security_headers = [
            "x-content-type-options",
            "x-frame-options",
            "x-xss-protection",
            "strict-transport-security",
            "content-security-policy",
        ]
        resp_headers_lower = {k.lower(): v for k, v in resp.headers.items()}
        present = [h for h in security_headers if h in resp_headers_lower]
        assert len(present) >= 1, (
            f"Ningún header de seguridad encontrado. Headers presentes: {list(resp_headers_lower.keys())}"
        )

    def test_process_time_header_present(self, api_client):
        """El header X-Process-Time-Ms indica el tiempo de procesamiento."""
        resp = api_client.get("/health")
        assert "x-process-time-ms" in {k.lower() for k in resp.headers}
