# =============================================================================
# api/auth.py
# Capa de autenticación y autorización de Bit-Counting.
#
# DISEÑO DE SEGURIDAD:
#   - JWT HS256 implementado con stdlib (hmac + hashlib + base64) — sin deps externas
#   - TOTP RFC 6238 con stdlib (struct + hmac + hashlib) — compatible con Google Authenticator
#   - Access token: 15 minutos de vida
#   - Refresh token: 7 días de vida
#   - MFA obligatorio: todo access token válido requiere mfa_verified=True
#   - Roles explícitos sin herencia: CLIENT, CPA_PARTNER, CPA_SENIOR, EXIMIA_ADMIN
#   - Permisos por rol definidos en frozensets — no se pueden asumir implícitamente
#
# FLUJO DE AUTENTICACIÓN:
#   1. POST /auth/token  →  {temp_token (mfa_pending=True), mfa_required=True}
#   2. POST /auth/mfa/verify  →  {access_token (mfa_verified=True), refresh_token}
#   3. Todos los endpoints protegidos requieren access_token con mfa_verified=True
#   4. POST /auth/refresh  →  nuevo access_token con mfa_verified=True si refresh válido
#
# IMPORTANTE — PRODUCCIÓN:
#   JWT_SECRET debe ser una variable de entorno (≥32 chars, random).
#   La _USERS_DB en-memoria se reemplaza con la tabla cpa_partners en Phase 3.
# =============================================================================

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# En producción: JWT_SECRET debe ser env var de mínimo 32 caracteres aleatorios.
# NUNCA usar este valor por defecto en producción.
JWT_SECRET: str = os.environ.get(
    "JWT_SECRET",
    "BIT-COUNTING-DEV-SECRET-CHANGE-IN-PRODUCTION-2026-MIN32CHARS",
)

ACCESS_TOKEN_EXPIRE_SECONDS:  int = 15 * 60          # 15 minutos
REFRESH_TOKEN_EXPIRE_SECONDS: int = 7 * 24 * 3600    # 7 días
TOTP_WINDOW:                  int = 1                 # ±1 período de 30s


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class Role(str, Enum):
    CLIENT       = "CLIENT"
    CPA_PARTNER  = "CPA_PARTNER"
    CPA_SENIOR   = "CPA_SENIOR"
    EXIMIA_ADMIN = "EXIMIA_ADMIN"


# Permisos exactos por rol — NO hay herencia implícita.
# Si no está en el frozenset, el rol no tiene el permiso.
_ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.CLIENT: frozenset({
        "transactions:read",
        "transactions:upload",
        "centinela:read",
        "reports:read",
    }),
    Role.CPA_PARTNER: frozenset({
        "transactions:read",
        "transactions:upload",
        "centinela:read",
        "centinela:release",
        "cpa:instructions",
        "cpa:review",
        "cpa:approve",
        "reports:read",
    }),
    Role.CPA_SENIOR: frozenset({
        "transactions:read",
        "transactions:upload",
        "centinela:read",
        "centinela:release",
        "cpa:instructions",
        "cpa:review",
        "cpa:approve",
        "reports:read",
        "normative:read",
    }),
    Role.EXIMIA_ADMIN: frozenset({
        "transactions:read",
        "transactions:upload",
        "centinela:read",
        "centinela:release",
        "cpa:instructions",
        "cpa:review",
        "cpa:approve",
        "reports:read",
        "normative:read",
        "normative:activate",
        "admin:read",
        "admin:write",
    }),
}


def get_permissions(role: Role) -> frozenset[str]:
    """Retorna el frozenset de permisos para un rol. Nunca hereda de otro rol."""
    return _ROLE_PERMISSIONS[role]


def has_permission(role: Role, permission: str) -> bool:
    return permission in _ROLE_PERMISSIONS[role]


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class TokenUser(BaseModel):
    """Payload del usuario extraído del JWT — inmutable."""
    model_config = ConfigDict(frozen=True)

    user_id:      str
    username:     str
    role:         Role
    client_id:    Optional[str]   # None para EXIMIA_ADMIN y roles CPA sin cliente asignado
    mfa_verified: bool = False    # True solo después de verificar TOTP
    mfa_pending:  bool = False    # True durante el flujo MFA (entre /token y /mfa/verify)
    permissions:  frozenset[str] = frozenset()


class UserRecord(BaseModel):
    """Registro de usuario en la base de datos (en-memoria Phase 1)."""
    model_config = ConfigDict(frozen=True)

    user_id:       str
    username:      str
    password_hash: str              # SHA-256 hex
    role:          Role
    client_id:     Optional[str] = None
    totp_secret:   str              # base32 TOTP secret
    mfa_enabled:   bool = True
    is_active:     bool = True


# ---------------------------------------------------------------------------
# Base de datos de usuarios en-memoria (Phase 1)
# ---------------------------------------------------------------------------

_USERS_DB: dict[str, UserRecord] = {}    # username → UserRecord
_REFRESH_TOKEN_BLACKLIST: set[str] = set()  # jti de refresh tokens revocados


# ---------------------------------------------------------------------------
# Utilidades JWT — HS256 con stdlib
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign_jwt(signing_input: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return _b64url_encode(sig)


def create_access_token(
    payload: dict,
    secret:     str = JWT_SECRET,
    expires_in: int = ACCESS_TOKEN_EXPIRE_SECONDS,
) -> str:
    """
    Crea un JWT HS256 con los campos del payload más exp, iat, jti.

    El payload debe incluir: user_id, username, role, client_id,
    mfa_verified, mfa_pending.
    """
    header  = {"alg": "HS256", "typ": "JWT"}
    now     = int(time.time())
    full_payload = {
        **payload,
        "exp": now + expires_in,
        "iat": now,
        "jti": secrets.token_hex(16),
        "token_type": "access",
    }
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p_b64 = _b64url_encode(json.dumps(full_payload, separators=(",", ":")).encode())
    signing = f"{h_b64}.{p_b64}"
    return f"{signing}.{_sign_jwt(signing, secret)}"


def create_refresh_token(
    payload: dict,
    secret:     str = JWT_SECRET,
    expires_in: int = REFRESH_TOKEN_EXPIRE_SECONDS,
) -> str:
    """Crea un refresh token JWT con token_type='refresh'."""
    header      = {"alg": "HS256", "typ": "JWT"}
    now         = int(time.time())
    full_payload = {
        **payload,
        "exp": now + expires_in,
        "iat": now,
        "jti": secrets.token_hex(16),
        "token_type": "refresh",
    }
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p_b64 = _b64url_encode(json.dumps(full_payload, separators=(",", ":")).encode())
    signing = f"{h_b64}.{p_b64}"
    return f"{signing}.{_sign_jwt(signing, secret)}"


def decode_token(token: str, secret: str = JWT_SECRET) -> dict:
    """
    Decodifica y valida un JWT.

    Raises:
        ValueError: si la firma es inválida, el token expiró, o el formato es incorrecto.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Formato de token inválido")

    h_b64, p_b64, sig_b64 = parts
    signing = f"{h_b64}.{p_b64}"

    # Verificar firma con compare_digest (timing-safe)
    expected = hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest()
    try:
        actual = _b64url_decode(sig_b64)
    except Exception:
        raise ValueError("Firma de token inválida")
    if not hmac.compare_digest(expected, actual):
        raise ValueError("Firma de token inválida")

    # Decodificar payload
    try:
        payload = json.loads(_b64url_decode(p_b64))
    except Exception:
        raise ValueError("Payload de token inválido")

    # Verificar expiración
    if payload.get("exp", 0) < int(time.time()):
        raise ValueError("Token expirado")

    return payload


# ---------------------------------------------------------------------------
# Utilidades TOTP — RFC 6238 con stdlib
# ---------------------------------------------------------------------------

def generate_totp_secret() -> str:
    """Genera un secreto TOTP aleatorio en base32 (compatible con Google Authenticator)."""
    raw = secrets.token_bytes(20)   # 160 bits
    return base64.b32encode(raw).decode().rstrip("=")


def _totp_code(secret_b32: str, counter: int) -> str:
    """Genera el código TOTP de 6 dígitos para un counter dado."""
    # Padding de base32
    pad = (8 - len(secret_b32) % 8) % 8
    padded = secret_b32.upper() + "=" * pad
    try:
        secret = base64.b32decode(padded)
    except Exception:
        raise ValueError("TOTP secret inválido (base32 malformado)")

    msg = struct.pack(">Q", counter)
    h   = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code   = struct.unpack(">I", h[offset: offset + 4])[0] & 0x7FFF_FFFF
    return str(code % 1_000_000).zfill(6)


def generate_totp(secret_b32: str) -> str:
    """Genera el código TOTP actual (útil para tests)."""
    return _totp_code(secret_b32, int(time.time()) // 30)


def verify_totp(secret_b32: str, token: str, window: int = TOTP_WINDOW) -> bool:
    """
    Verifica un código TOTP con ventana de ±window períodos (30s cada uno).

    Permite desfase de reloj de hasta 30s * window.
    """
    if not token or not token.isdigit() or len(token) != 6:
        return False
    counter = int(time.time()) // 30
    for delta in range(-window, window + 1):
        if _totp_code(secret_b32, counter + delta) == token:
            return True
    return False


# ---------------------------------------------------------------------------
# Gestión de usuarios
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """Hash SHA-256 de la contraseña. Phase 3 usará bcrypt/argon2."""
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(
    username:  str,
    password:  str,
    role:      Role,
    client_id: Optional[str] = None,
) -> UserRecord:
    """
    Crea un usuario en la base de datos en-memoria.

    En Phase 3, persiste en la tabla cpa_partners / clients.
    """
    record = UserRecord(
        user_id=str(uuid.uuid4()),
        username=username,
        password_hash=_hash_password(password),
        role=role,
        client_id=client_id,
        totp_secret=generate_totp_secret(),
        mfa_enabled=True,
        is_active=True,
    )
    _USERS_DB[username] = record
    logger.info("Usuario creado: %s (rol=%s)", username, role.value)
    return record


def get_user(username: str) -> Optional[UserRecord]:
    return _USERS_DB.get(username)


def authenticate_user(username: str, password: str) -> Optional[UserRecord]:
    """
    Autentica un usuario con username + password.
    Retorna UserRecord si válido, None si no.

    Usa compare_digest para resistir timing attacks.
    """
    user = _USERS_DB.get(username)
    if user is None or not user.is_active:
        # Siempre hacer el hash para resistir timing attacks de enumeración de usuarios
        _hash_password(password)
        return None
    if hmac.compare_digest(user.password_hash, _hash_password(password)):
        return user
    return None


def token_user_from_record(
    user:         UserRecord,
    mfa_verified: bool = False,
    mfa_pending:  bool = False,
) -> TokenUser:
    """Convierte un UserRecord en un TokenUser con sus permisos."""
    return TokenUser(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        client_id=user.client_id,
        mfa_verified=mfa_verified,
        mfa_pending=mfa_pending,
        permissions=get_permissions(user.role),
    )


def build_token_payload(user: TokenUser) -> dict:
    """Construye el dict de payload para incluir en el JWT."""
    return {
        "sub":          user.user_id,
        "username":     user.username,
        "role":         user.role.value,
        "client_id":    user.client_id,
        "mfa_verified": user.mfa_verified,
        "mfa_pending":  user.mfa_pending,
    }


def token_user_from_payload(payload: dict) -> TokenUser:
    """Reconstruye un TokenUser desde el payload del JWT decodificado."""
    role = Role(payload["role"])
    return TokenUser(
        user_id=payload["sub"],
        username=payload["username"],
        role=role,
        client_id=payload.get("client_id"),
        mfa_verified=payload.get("mfa_verified", False),
        mfa_pending=payload.get("mfa_pending", False),
        permissions=get_permissions(role),
    )


# ---------------------------------------------------------------------------
# Refresh token — blacklist en-memoria
# ---------------------------------------------------------------------------

def revoke_refresh_token(jti: str) -> None:
    """Revoca un refresh token por su jti (logout)."""
    _REFRESH_TOKEN_BLACKLIST.add(jti)


def is_refresh_token_revoked(jti: str) -> bool:
    return jti in _REFRESH_TOKEN_BLACKLIST


# ---------------------------------------------------------------------------
# Helper para tests — crea un token válido sin pasar por el flujo MFA
# ---------------------------------------------------------------------------

def create_test_token(
    user_id:   str = "test-user-001",
    username:  str = "test_user",
    role:      Role = Role.CLIENT,
    client_id: Optional[str] = "client-demo-001",
) -> str:
    """
    Crea un access token válido para tests sin requerir credenciales reales.

    SOLO para uso en tests — nunca exponer este endpoint en producción.
    """
    user = TokenUser(
        user_id=user_id,
        username=username,
        role=role,
        client_id=client_id,
        mfa_verified=True,
        mfa_pending=False,
        permissions=get_permissions(role),
    )
    return create_access_token(build_token_payload(user))


# ---------------------------------------------------------------------------
# Seed de usuarios demo (Phase 1 — en-memoria)
# ---------------------------------------------------------------------------

def _seed_demo_users() -> None:
    """Crea usuarios demo si la base de datos está vacía."""
    if _USERS_DB:
        return

    create_user("admin@eximia.pr", "Admin2026!Secure", Role.EXIMIA_ADMIN)
    create_user("cpa_senior@eximia.pr", "CPA2026!Senior", Role.CPA_SENIOR)
    create_user("cpa_partner@eximia.pr", "CPA2026!Partner", Role.CPA_PARTNER,
                client_id="client-demo-001")
    create_user("cliente@empresa.pr", "Client2026!Demo", Role.CLIENT,
                client_id="client-demo-001")
    logger.info("Usuarios demo creados (Phase 1 en-memoria)")


_seed_demo_users()
