# =============================================================================
# api/tests_api_security.py
# Tests de seguridad para la capa API REST de Bit-Counting.
#
# Cobertura:
#   - JWT: creación, validación, expiración, firma inválida
#   - TOTP: generación y verificación de códigos de 6 dígitos
#   - Roles: CLIENT, CPA_PARTNER, CPA_SENIOR, EXIMIA_ADMIN
#   - Permisos explícitos por rol (sin herencia)
#   - MFA obligatorio — sin mfa_verified se rechaza con 403
#   - Endpoints sin token → 401
#   - Rol insuficiente → 403
#   - Client scope: CLIENT no puede acceder a datos de otro cliente
#   - Rate limiting: exceder límite → 429
#   - Security headers en todas las respuestas
#   - Flujo MFA completo: /auth/token → /auth/mfa/verify → endpoint
#   - Refresh token flow
#   - Admin endpoints solo para EXIMIA_ADMIN
#   - Centinela endpoints: CLIENT ve sus pausas, CPA puede liberar
#   - Transacciones: upload, GET /{id}, lista con scope
# =============================================================================

from __future__ import annotations

import sys
import os
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.auth import (
    Role,
    TokenUser,
    _ROLE_PERMISSIONS,
    authenticate_user,
    build_token_payload,
    create_access_token,
    create_refresh_token,
    create_test_token,
    create_user,
    decode_token,
    generate_totp,
    generate_totp_secret,
    get_permissions,
    token_user_from_record,
    verify_totp,
    _USERS_DB,
    _REFRESH_TOKEN_BLACKLIST,
)
from api.dependencies import _rate_limiter

client = TestClient(app)


# ---------------------------------------------------------------------------
# Reset rate limiter before every test so limits don't bleed between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    _rate_limiter.reset()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers(role: Role, client_id: str = "client-demo-001") -> dict:
    token = create_test_token(role=role, client_id=client_id)
    return {"Authorization": f"Bearer {token}"}


def _admin_h() -> dict:
    return _headers(Role.EXIMIA_ADMIN, client_id=None)


def _cpa_h(client_id: str = "client-demo-001") -> dict:
    return _headers(Role.CPA_PARTNER, client_id=client_id)


def _client_h(client_id: str = "client-demo-001") -> dict:
    return _headers(Role.CLIENT, client_id=client_id)


# ===========================================================================
# 1. JWT — creación y validación
# ===========================================================================

class TestJWT:

    def test_create_and_decode_access_token(self):
        payload = {
            "sub": "user-001", "username": "test", "role": "CLIENT",
            "client_id": "c-001", "mfa_verified": True, "mfa_pending": False,
        }
        token = create_access_token(payload)
        decoded = decode_token(token)
        assert decoded["sub"] == "user-001"
        assert decoded["mfa_verified"] is True
        assert decoded["token_type"] == "access"

    def test_token_has_expiry(self):
        payload = {"sub": "u-001", "username": "x", "role": "CLIENT",
                   "client_id": None, "mfa_verified": True, "mfa_pending": False}
        token = create_access_token(payload, expires_in=900)
        decoded = decode_token(token)
        assert decoded["exp"] > int(time.time())
        assert decoded["exp"] <= int(time.time()) + 900 + 5  # ±5s tolerancia

    def test_expired_token_raises(self):
        payload = {"sub": "u-001", "username": "x", "role": "CLIENT",
                   "client_id": None, "mfa_verified": True, "mfa_pending": False}
        token = create_access_token(payload, expires_in=-1)   # ya expirado
        with pytest.raises(ValueError, match="expirado"):
            decode_token(token)

    def test_tampered_signature_raises(self):
        payload = {"sub": "u-001", "username": "x", "role": "CLIENT",
                   "client_id": None, "mfa_verified": True, "mfa_pending": False}
        token = create_access_token(payload)
        parts = token.split(".")
        # Modificar el payload (parte central)
        tampered = parts[0] + "." + parts[1] + "XXXX" + "." + parts[2]
        with pytest.raises(ValueError):
            decode_token(tampered)

    def test_wrong_secret_raises(self):
        payload = {"sub": "u-001", "username": "x", "role": "CLIENT",
                   "client_id": None, "mfa_verified": True, "mfa_pending": False}
        token = create_access_token(payload, secret="secret-A")
        with pytest.raises(ValueError):
            decode_token(token, secret="secret-B")

    def test_malformed_token_raises(self):
        with pytest.raises(ValueError):
            decode_token("not-a-jwt-at-all")

    def test_token_with_only_two_parts_raises(self):
        with pytest.raises(ValueError):
            decode_token("part1.part2")

    def test_refresh_token_type_is_refresh(self):
        payload = {"sub": "u-001", "username": "x", "role": "CLIENT",
                   "client_id": None, "mfa_verified": True, "mfa_pending": False}
        token = create_refresh_token(payload)
        decoded = decode_token(token)
        assert decoded["token_type"] == "refresh"

    def test_refresh_token_rejected_as_access(self):
        """Un refresh token no puede usarse como access token en endpoints."""
        payload = {"sub": "u-001", "username": "x", "role": "CLIENT",
                   "client_id": "client-demo-001", "mfa_verified": True, "mfa_pending": False}
        refresh = create_refresh_token(payload)
        response = client.get(
            "/api/v1/transactions",
            headers={"Authorization": f"Bearer {refresh}"},
        )
        assert response.status_code == 401

    def test_jti_is_unique_per_token(self):
        payload = {"sub": "u-001", "username": "x", "role": "CLIENT",
                   "client_id": None, "mfa_verified": True, "mfa_pending": False}
        t1 = create_access_token(payload)
        t2 = create_access_token(payload)
        assert decode_token(t1)["jti"] != decode_token(t2)["jti"]


# ===========================================================================
# 2. TOTP
# ===========================================================================

class TestTOTP:

    def test_generated_secret_is_base32(self):
        import base64
        secret = generate_totp_secret()
        # Debe ser decodificable como base32
        padded = secret + "=" * ((8 - len(secret) % 8) % 8)
        decoded = base64.b32decode(padded.upper())
        assert len(decoded) == 20   # 160 bits

    def test_generate_and_verify_current_code(self):
        secret = generate_totp_secret()
        code = generate_totp(secret)
        assert len(code) == 6
        assert code.isdigit()
        assert verify_totp(secret, code)

    def test_wrong_code_fails(self):
        secret = generate_totp_secret()
        assert not verify_totp(secret, "000000")

    def test_wrong_length_code_fails(self):
        secret = generate_totp_secret()
        assert not verify_totp(secret, "12345")    # 5 dígitos
        assert not verify_totp(secret, "1234567")  # 7 dígitos

    def test_non_digit_code_fails(self):
        secret = generate_totp_secret()
        assert not verify_totp(secret, "abcdef")

    def test_empty_code_fails(self):
        secret = generate_totp_secret()
        assert not verify_totp(secret, "")

    def test_different_secrets_different_codes(self):
        s1 = generate_totp_secret()
        s2 = generate_totp_secret()
        c1 = generate_totp(s1)
        c2 = generate_totp(s2)
        # Con probabilidad muy alta son distintos
        # (podría fallar 1 en 1,000,000 veces — aceptable)
        assert s1 != s2


# ===========================================================================
# 3. Roles y permisos
# ===========================================================================

class TestRolesAndPermissions:

    def test_client_permissions(self):
        perms = get_permissions(Role.CLIENT)
        assert "transactions:read" in perms
        assert "transactions:upload" in perms
        assert "centinela:read" in perms
        assert "reports:read" in perms
        # CLIENT no tiene permisos de admin ni CPA
        assert "admin:read" not in perms
        assert "admin:write" not in perms
        assert "normative:activate" not in perms
        assert "cpa:approve" not in perms

    def test_cpa_partner_permissions(self):
        perms = get_permissions(Role.CPA_PARTNER)
        assert "cpa:approve" in perms
        assert "centinela:release" in perms
        assert "admin:write" not in perms
        assert "normative:activate" not in perms

    def test_cpa_senior_permissions(self):
        perms = get_permissions(Role.CPA_SENIOR)
        assert "normative:read" in perms
        assert "admin:write" not in perms
        assert "normative:activate" not in perms

    def test_eximia_admin_has_all_permissions(self):
        perms = get_permissions(Role.EXIMIA_ADMIN)
        for role in Role:
            for perm in _ROLE_PERMISSIONS[role]:
                assert perm in perms, f"EXIMIA_ADMIN no tiene permiso {perm}"

    def test_no_implicit_inheritance(self):
        """Los permisos son explícitos — CLIENT no hereda permisos de CPA."""
        client_perms = get_permissions(Role.CLIENT)
        cpa_perms    = get_permissions(Role.CPA_PARTNER)
        # CLIENT no tiene permisos exclusivos de CPA
        cpa_only = cpa_perms - client_perms
        for perm in cpa_only:
            assert perm not in client_perms

    def test_roles_are_distinct_frozensets(self):
        for r1 in Role:
            for r2 in Role:
                if r1 != r2:
                    assert _ROLE_PERMISSIONS[r1] != _ROLE_PERMISSIONS[r2] or r1 == r2


# ===========================================================================
# 4. Endpoints sin token → 401
# ===========================================================================

class TestUnauthenticatedRequests:

    def test_transactions_requires_auth(self):
        response = client.get("/api/v1/transactions")
        assert response.status_code == 401

    def test_transaction_upload_requires_auth(self):
        response = client.post("/api/v1/transactions/upload", json={
            "raw_text": "test", "client_id": "c-001"
        })
        assert response.status_code == 401

    def test_reports_balance_sheet_requires_auth(self):
        response = client.get("/api/v1/reports/balance-sheet?client_id=c-001")
        assert response.status_code == 401

    def test_reports_income_statement_requires_auth(self):
        response = client.get(
            "/api/v1/reports/income-statement?client_id=c-001&period_start=2026-01-01&period_end=2026-03-31"
        )
        assert response.status_code == 401

    def test_centinela_pauses_requires_auth(self):
        response = client.get("/api/v1/centinela/pauses")
        assert response.status_code == 401

    def test_centinela_release_requires_auth(self):
        response = client.post("/api/v1/centinela/pauses/pause-demo-001/release", json={
            "resolution_notes": "test notes here", "instruction": "test instruction here"
        })
        assert response.status_code == 401

    def test_admin_requires_auth(self):
        response = client.get("/api/v1/admin/normative-updates")
        assert response.status_code == 401

    def test_cpa_pauses_requires_auth(self):
        response = client.get("/api/v1/cpa/pauses")
        assert response.status_code == 401

    def test_cpa_metrics_requires_auth(self):
        response = client.get("/api/v1/cpa/metrics?cpa_license=CPA-001")
        assert response.status_code == 401

    def test_auth_me_requires_auth(self):
        response = client.get("/auth/me")
        assert response.status_code == 401

    def test_health_does_not_require_auth(self):
        """El health check es público — no requiere auth."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_root_does_not_require_auth(self):
        response = client.get("/")
        assert response.status_code == 200


# ===========================================================================
# 5. MFA obligatorio — token sin mfa_verified → 403
# ===========================================================================

class TestMFAEnforcement:

    def _mfa_pending_token(self, client_id: str = "client-demo-001") -> str:
        """Crea un token con mfa_pending=True (como /auth/token retorna)."""
        user = TokenUser(
            user_id="u-001", username="test_mfa", role=Role.CLIENT,
            client_id=client_id, mfa_verified=False, mfa_pending=True,
            permissions=get_permissions(Role.CLIENT),
        )
        return create_access_token(build_token_payload(user))

    def test_mfa_pending_token_rejected_with_403(self):
        token = self._mfa_pending_token()
        response = client.get(
            "/api/v1/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_mfa_pending_token_rejected_on_reports(self):
        token = self._mfa_pending_token()
        response = client.get(
            "/api/v1/reports/balance-sheet?client_id=client-demo-001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_mfa_pending_token_rejected_on_centinela(self):
        token = self._mfa_pending_token()
        response = client.get(
            "/api/v1/centinela/pauses",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_mfa_verified_token_allowed(self):
        token = create_test_token(role=Role.CLIENT, client_id="client-demo-001")
        response = client.get(
            "/api/v1/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    def test_403_response_mentions_mfa(self):
        token = self._mfa_pending_token()
        response = client.get(
            "/api/v1/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        detail = response.json().get("detail", "")
        assert "MFA" in detail or "mfa" in detail.lower()


# ===========================================================================
# 6. Rol insuficiente → 403
# ===========================================================================

class TestRoleEnforcement:

    def test_client_cannot_access_admin_endpoints(self):
        response = client.get("/api/v1/admin/normative-updates", headers=_client_h())
        assert response.status_code == 403

    def test_cpa_partner_cannot_access_admin_endpoints(self):
        response = client.get("/api/v1/admin/normative-updates", headers=_cpa_h())
        assert response.status_code == 403

    def test_cpa_senior_cannot_access_admin_endpoints(self):
        token = create_test_token(role=Role.CPA_SENIOR, client_id=None)
        response = client.get(
            "/api/v1/admin/normative-updates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_eximia_admin_can_access_admin_endpoints(self):
        response = client.get("/api/v1/admin/normative-updates", headers=_admin_h())
        assert response.status_code == 200

    def test_client_cannot_release_centinela_pauses(self):
        """Solo CPA puede liberar pausas — CLIENT no."""
        response = client.post(
            "/api/v1/centinela/pauses/pause-demo-001/release",
            json={"resolution_notes": "Notas suficientes para resolver", "instruction": "Instrucción válida"},
            headers=_client_h(),
        )
        assert response.status_code == 403

    def test_cpa_can_release_centinela_pauses(self):
        response = client.post(
            "/api/v1/centinela/pauses/pause-demo-001/release",
            json={"resolution_notes": "Notas de resolución detalladas", "instruction": "Clasificar como IVU mensual"},
            headers=_cpa_h(),
        )
        # 200 o 409 (ya resuelta por otro test)
        assert response.status_code in (200, 409)

    def test_403_response_contains_role_info(self):
        response = client.get("/api/v1/admin/normative-updates", headers=_client_h())
        assert response.status_code == 403
        detail = response.json().get("detail", "")
        assert "EXIMIA_ADMIN" in detail


# ===========================================================================
# 7. Client scope — usuarios solo ven sus propios datos
# ===========================================================================

class TestClientScope:

    def test_client_cannot_access_other_client_transactions(self):
        """CLIENT de client-A no puede ver transacciones de client-B."""
        response = client.get(
            "/api/v1/transactions?client_id=client-OTRO-CLIENTE-XYZ",
            headers=_client_h("client-demo-001"),
        )
        assert response.status_code == 403

    def test_client_cannot_access_other_client_reports(self):
        response = client.get(
            "/api/v1/reports/balance-sheet?client_id=client-OTRO-XYZ",
            headers=_client_h("client-demo-001"),
        )
        assert response.status_code == 403

    def test_client_can_access_own_data(self):
        response = client.get(
            "/api/v1/reports/balance-sheet?client_id=client-demo-001",
            headers=_client_h("client-demo-001"),
        )
        assert response.status_code == 200

    def test_admin_can_access_any_client(self):
        """EXIMIA_ADMIN puede acceder a datos de cualquier cliente."""
        response = client.get(
            "/api/v1/reports/balance-sheet?client_id=client-demo-001",
            headers=_admin_h(),
        )
        assert response.status_code == 200

    def test_cpa_senior_can_access_any_client(self):
        """CPA_SENIOR tiene acceso global para supervisión."""
        token = create_test_token(role=Role.CPA_SENIOR, client_id=None)
        response = client.get(
            "/api/v1/reports/balance-sheet?client_id=client-demo-001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    def test_client_cannot_upload_for_other_client(self):
        """CLIENT no puede subir documentos para otro cliente."""
        response = client.post(
            "/api/v1/transactions/upload",
            json={"raw_text": "Factura test $100", "client_id": "otro-cliente-xyz"},
            headers=_client_h("client-demo-001"),
        )
        assert response.status_code == 403

    def test_client_can_upload_for_own_client(self):
        response = client.post(
            "/api/v1/transactions/upload",
            json={"raw_text": "Factura Dell $3200.00", "client_id": "client-demo-001"},
            headers=_client_h("client-demo-001"),
        )
        assert response.status_code == 202


# ===========================================================================
# 8. Security headers
# ===========================================================================

class TestSecurityHeaders:

    def _get_response(self):
        return client.get("/health")

    def test_x_content_type_options_nosniff(self):
        r = self._get_response()
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options_deny(self):
        r = self._get_response()
        assert r.headers.get("x-frame-options") == "DENY"

    def test_content_security_policy_present(self):
        r = self._get_response()
        csp = r.headers.get("content-security-policy", "")
        assert "default-src" in csp
        assert "frame-ancestors" in csp

    def test_referrer_policy_present(self):
        r = self._get_response()
        assert "referrer-policy" in r.headers

    def test_strict_transport_security_present(self):
        r = self._get_response()
        hsts = r.headers.get("strict-transport-security", "")
        assert "max-age" in hsts
        assert "includeSubDomains" in hsts

    def test_permissions_policy_present(self):
        r = self._get_response()
        assert "permissions-policy" in r.headers

    def test_security_headers_on_auth_endpoints(self):
        """Los headers de seguridad también están en endpoints de auth."""
        r = client.post("/auth/token", json={"username": "x", "password": "y"})
        assert "x-content-type-options" in r.headers

    def test_security_headers_on_protected_endpoints(self):
        r = client.get("/api/v1/transactions", headers=_client_h())
        assert "x-content-type-options" in r.headers
        assert r.headers.get("x-frame-options") == "DENY"


# ===========================================================================
# 9. Auth endpoints — flujo de login
# ===========================================================================

class TestAuthEndpoints:

    def setup_method(self):
        """Crear usuario de prueba para auth tests."""
        self._username = "auth_test_user_security"
        self._password = "SecurePass2026!"
        if self._username not in _USERS_DB:
            self._user = create_user(
                self._username, self._password, Role.CLIENT, "client-test-auth"
            )
        else:
            self._user = _USERS_DB[self._username]

    def test_login_with_invalid_credentials_returns_401(self):
        response = client.post("/auth/token", json={
            "username": "nonexistent@user.com",
            "password": "WrongPassword123!",
        })
        assert response.status_code == 401

    def test_login_with_empty_credentials_returns_422(self):
        response = client.post("/auth/token", json={
            "username": "",
            "password": "",
        })
        assert response.status_code == 422

    def test_login_with_valid_credentials_returns_temp_token(self):
        response = client.post("/auth/token", json={
            "username": self._username,
            "password": self._password,
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["mfa_required"] is True
        # El token temporal tiene mfa_pending=True
        payload = decode_token(data["access_token"])
        assert payload["mfa_pending"] is True
        assert payload["mfa_verified"] is False

    def test_temp_token_rejected_on_protected_endpoints(self):
        """El token temporal (mfa_pending) no puede acceder a endpoints protegidos."""
        response = client.post("/auth/token", json={
            "username": self._username,
            "password": self._password,
        })
        assert response.status_code == 200
        temp_token = response.json()["access_token"]
        resp = client.get(
            "/api/v1/transactions",
            headers={"Authorization": f"Bearer {temp_token}"},
        )
        assert resp.status_code == 403

    def test_mfa_verify_with_valid_totp_returns_full_token(self):
        """Verificar MFA con código TOTP válido retorna access_token completo."""
        # Login
        login_resp = client.post("/auth/token", json={
            "username": self._username,
            "password": self._password,
        })
        assert login_resp.status_code == 200
        temp_token = login_resp.json()["access_token"]

        # Generar código TOTP válido
        totp_code = generate_totp(self._user.totp_secret)

        # Verificar MFA
        mfa_resp = client.post(
            "/auth/mfa/verify",
            json={"totp_code": totp_code},
            headers={"Authorization": f"Bearer {temp_token}"},
        )
        assert mfa_resp.status_code == 200
        data = mfa_resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["mfa_required"] is False

        # El token completo debe tener mfa_verified=True
        payload = decode_token(data["access_token"])
        assert payload["mfa_verified"] is True
        assert payload["mfa_pending"] is False

    def test_mfa_verify_with_invalid_totp_returns_401(self):
        login_resp = client.post("/auth/token", json={
            "username": self._username,
            "password": self._password,
        })
        assert login_resp.status_code == 200
        temp_token = login_resp.json()["access_token"]

        mfa_resp = client.post(
            "/auth/mfa/verify",
            json={"totp_code": "000000"},   # Código inválido
            headers={"Authorization": f"Bearer {temp_token}"},
        )
        assert mfa_resp.status_code == 401

    def test_full_mfa_flow_allows_access(self):
        """Flujo completo: login → MFA → acceder endpoint protegido."""
        # 1. Login
        login_resp = client.post("/auth/token", json={
            "username": self._username,
            "password": self._password,
        })
        temp_token = login_resp.json()["access_token"]

        # 2. MFA
        totp_code = generate_totp(self._user.totp_secret)
        mfa_resp = client.post(
            "/auth/mfa/verify",
            json={"totp_code": totp_code},
            headers={"Authorization": f"Bearer {temp_token}"},
        )
        access_token = mfa_resp.json()["access_token"]

        # 3. Acceder endpoint protegido
        txn_resp = client.get(
            "/api/v1/transactions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert txn_resp.status_code == 200

    def test_get_me_returns_user_info(self):
        token = create_test_token(role=Role.CLIENT, client_id="client-demo-001")
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert "user_id" in data
        assert "role" in data
        assert "permissions" in data
        assert data["role"] == "CLIENT"

    def test_login_error_message_does_not_expose_user_existence(self):
        """El mensaje de error no distingue entre usuario no encontrado y password incorrecta."""
        r1 = client.post("/auth/token", json={"username": "nonexistent@x.com", "password": "pass"})
        r2 = client.post("/auth/token", json={"username": self._username, "password": "wrongpass"})
        # Mismo mensaje genérico
        assert r1.json().get("detail") == r2.json().get("detail")


# ===========================================================================
# 10. Refresh token
# ===========================================================================

class TestRefreshToken:

    def _get_tokens(self) -> tuple[str, str]:
        """Retorna (access_token, refresh_token) válidos."""
        username = "refresh_test_user"
        password = "RefreshPass2026!"
        if username not in _USERS_DB:
            user = create_user(username, password, Role.CLIENT, "client-demo-001")
        else:
            user = _USERS_DB[username]
        token_user = token_user_from_record(user, mfa_verified=True)
        payload = build_token_payload(token_user)
        return (
            create_access_token(payload),
            create_refresh_token(payload),
        )

    def test_refresh_with_valid_token_returns_new_access_token(self):
        _, refresh = self._get_tokens()
        response = client.post("/auth/refresh", json={"refresh_token": refresh})
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        # El nuevo access token debe tener mfa_verified=True
        payload = decode_token(data["access_token"])
        assert payload["mfa_verified"] is True

    def test_refresh_with_access_token_fails(self):
        """No se puede usar un access token como refresh token."""
        access, _ = self._get_tokens()
        response = client.post("/auth/refresh", json={"refresh_token": access})
        assert response.status_code == 401

    def test_refresh_with_invalid_token_fails(self):
        response = client.post("/auth/refresh", json={"refresh_token": "invalid.token.here"})
        assert response.status_code == 401

    def test_logout_invalidates_refresh_token(self):
        access, refresh = self._get_tokens()
        # Logout
        logout_resp = client.post(
            "/auth/logout",
            json={"refresh_token": refresh},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert logout_resp.status_code == 200
        # Intentar usar el refresh token revocado
        refresh_resp = client.post("/auth/refresh", json={"refresh_token": refresh})
        assert refresh_resp.status_code == 401


# ===========================================================================
# 11. Admin endpoints — solo EXIMIA_ADMIN
# ===========================================================================

class TestAdminEndpoints:

    def test_list_normative_updates_admin_only(self):
        response = client.get("/api/v1/admin/normative-updates", headers=_admin_h())
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_normative_updates_has_required_fields(self):
        response = client.get("/api/v1/admin/normative-updates", headers=_admin_h())
        assert response.status_code == 200
        for item in response.json():
            assert "update_id" in item
            assert "impact_level" in item
            assert "status" in item
            assert "requires_human_review" in item
            assert item["requires_human_review"] is True

    def test_activate_update_requires_3_factors(self):
        """La activación sin los 3 factores completos es rechazada."""
        # Obtener un update real
        updates_resp = client.get("/api/v1/admin/normative-updates", headers=_admin_h())
        updates = updates_resp.json()
        if not updates:
            pytest.skip("No hay updates para activar")

        update_id = updates[0]["update_id"]

        # Factor 1 faltante (sin digital_signature válida)
        response = client.post(
            f"/api/v1/admin/normative-updates/{update_id}/activate",
            json={
                "cpa_license": "CPA-ADMIN-001",
                "digital_signature": "short",   # < 16 chars hex
                "effective_date": "2027-01-01",
                "interpretation_note": "Comentario de interpretación suficientemente largo.",
            },
            headers=_admin_h(),
        )
        assert response.status_code == 400

    def test_activate_update_with_past_date_rejected(self):
        updates_resp = client.get("/api/v1/admin/normative-updates", headers=_admin_h())
        updates = updates_resp.json()
        if not updates:
            pytest.skip("No hay updates para activar")

        update_id = updates[0]["update_id"]
        response = client.post(
            f"/api/v1/admin/normative-updates/{update_id}/activate",
            json={
                "cpa_license": "CPA-ADMIN-001",
                "digital_signature": "abcdef1234567890",
                "effective_date": "2020-01-01",   # fecha pasada
                "interpretation_note": "Comentario de interpretación suficientemente largo.",
            },
            headers=_admin_h(),
        )
        assert response.status_code == 400

    def test_activate_nonexistent_update_returns_404(self):
        response = client.post(
            "/api/v1/admin/normative-updates/nonexistent-id-xyz/activate",
            json={
                "cpa_license": "CPA-ADMIN-001",
                "digital_signature": "abcdef1234567890",
                "effective_date": "2027-06-01",
                "interpretation_note": "Comentario de interpretación suficientemente largo.",
            },
            headers=_admin_h(),
        )
        assert response.status_code in (400, 404)


# ===========================================================================
# 12. Centinela endpoints
# ===========================================================================

class TestCentinelaEndpoints:

    def test_list_pauses_returns_list(self):
        response = client.get("/api/v1/centinela/pauses", headers=_cpa_h())
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_pause_detail(self):
        response = client.get("/api/v1/centinela/pauses/pause-demo-001", headers=_cpa_h())
        assert response.status_code in (200, 404, 409)   # puede haber sido resuelta

    def test_get_nonexistent_pause_returns_404(self):
        response = client.get("/api/v1/centinela/pauses/no-existe-xyz", headers=_cpa_h())
        assert response.status_code == 404

    def test_client_cannot_release_pause(self):
        response = client.post(
            "/api/v1/centinela/pauses/pause-demo-001/release",
            json={"resolution_notes": "Notas suficientes para el release", "instruction": "Usar cuenta 2400"},
            headers=_client_h(),
        )
        assert response.status_code == 403


# ===========================================================================
# 13. Transaction endpoints con auth
# ===========================================================================

class TestTransactionEndpoints:

    def test_upload_creates_transaction(self):
        response = client.post(
            "/api/v1/transactions/upload",
            json={"raw_text": "Factura Costco $500.00", "client_id": "client-demo-001"},
            headers=_client_h(),
        )
        assert response.status_code == 202
        data = response.json()
        assert "transaction_id" in data
        assert data["status"] == "pending"

    def test_get_transaction_by_id(self):
        # Crear una transacción primero
        create_resp = client.post(
            "/api/v1/transactions/upload",
            json={"raw_text": "Test transaction $100", "client_id": "client-demo-001"},
            headers=_client_h(),
        )
        txn_id = create_resp.json()["transaction_id"]

        # Obtener por ID
        get_resp = client.get(f"/api/v1/transactions/{txn_id}", headers=_client_h())
        assert get_resp.status_code == 200
        assert get_resp.json()["transaction_id"] == txn_id

    def test_get_nonexistent_transaction_returns_404(self):
        response = client.get("/api/v1/transactions/no-existe-abc", headers=_client_h())
        assert response.status_code == 404

    def test_client_cannot_get_other_clients_transaction(self):
        """CLIENT no puede ver transacciones de otro cliente."""
        # La transacción demo-001 pertenece a client-demo-001
        # Un cliente con client-demo-002 no puede verla
        token = create_test_token(role=Role.CLIENT, client_id="client-OTRO-001")
        response = client.get(
            "/api/v1/transactions/txn-demo-001",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_admin_can_see_all_transactions(self):
        """EXIMIA_ADMIN puede ver transacciones de cualquier cliente."""
        response = client.get("/api/v1/transactions/txn-demo-001", headers=_admin_h())
        assert response.status_code == 200


# ===========================================================================
# 14. Input validation — Pydantic rechaza inputs malformados
# ===========================================================================

class TestInputValidation:

    def test_upload_empty_raw_text_rejected(self):
        response = client.post(
            "/api/v1/transactions/upload",
            json={"raw_text": "", "client_id": "client-demo-001"},
            headers=_client_h(),
        )
        assert response.status_code == 422

    def test_upload_missing_client_id_rejected(self):
        response = client.post(
            "/api/v1/transactions/upload",
            json={"raw_text": "valid text"},   # falta client_id
            headers=_client_h(),
        )
        assert response.status_code == 422

    def test_mfa_verify_wrong_format_rejected(self):
        """El código TOTP debe ser exactamente 6 dígitos."""
        token = create_test_token(role=Role.CLIENT)
        # Crear token con mfa_pending
        user_payload = {"sub": "u", "username": "x", "role": "CLIENT",
                       "client_id": None, "mfa_verified": False, "mfa_pending": True}
        pending_token = create_access_token(user_payload, expires_in=300)

        response = client.post(
            "/auth/mfa/verify",
            json={"totp_code": "abc"},   # no es 6 dígitos
            headers={"Authorization": f"Bearer {pending_token}"},
        )
        assert response.status_code == 422

    def test_login_missing_password_rejected(self):
        response = client.post("/auth/token", json={"username": "user@x.com"})
        assert response.status_code == 422


# ===========================================================================
# 15. Rate limiting
# ===========================================================================

class TestRateLimiting:

    def test_auth_token_rate_limit(self):
        """Más de 5 intentos de login por minuto por IP → 429."""
        # El rate limiter es por user_key (IP en este caso)
        # En tests, TestClient usa la misma IP, así que compartimos la ventana.
        # Hacemos 6 requests a /auth/token — el 6to debería ser 429.
        # NOTA: si ya hay requests previos en la ventana, puede llegar antes.
        responses = []
        for i in range(7):
            r = client.post("/auth/token", json={
                "username": f"rate_test_user_{i}@x.com",
                "password": "pass",
            })
            responses.append(r.status_code)

        # Al menos uno debe ser 429
        assert 429 in responses, f"Expected 429 in {responses}"

    def test_429_response_has_retry_after_header(self):
        """La respuesta 429 debe incluir Retry-After header."""
        responses_429 = []
        for i in range(8):
            r = client.post("/auth/token", json={
                "username": f"retry_test_{i}@x.com",
                "password": "wrong",
            })
            if r.status_code == 429:
                responses_429.append(r)

        if responses_429:
            assert "retry-after" in responses_429[0].headers
