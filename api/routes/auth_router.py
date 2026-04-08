# =============================================================================
# api/routes/auth_router.py
# Endpoints de autenticación para Bit-Counting.
#
# FLUJO COMPLETO:
#   1. POST /auth/token
#      → Credenciales válidas: {temp_token (mfa_pending=True), mfa_required=True}
#      → Credenciales inválidas: 401
#
#   2. POST /auth/mfa/verify  (con temp_token como Bearer)
#      → Código TOTP válido: {access_token (mfa_verified=True), refresh_token}
#      → Código TOTP inválido: 401
#
#   3. POST /auth/refresh  (con refresh_token)
#      → Refresh válido: {access_token nuevo (mfa_verified=True), mismo refresh}
#      → Refresh inválido/revocado: 401
#
#   4. POST /auth/logout  (con access_token)
#      → Revoca el refresh_token del usuario
#
# RATE LIMITING:
#   - /auth/token:      5 intentos por minuto por IP
#   - /auth/mfa/verify: 5 intentos por minuto por IP
#   - /auth/refresh:    20 refreshes por minuto
#
# SEGURIDAD:
#   - Los errores de login NO diferencian entre usuario no encontrado y
#     contraseña incorrecta (previene enumeración de usuarios).
#   - El tiempo de respuesta es constante independientemente del resultado.
# =============================================================================

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import (
    Role,
    TokenUser,
    authenticate_user,
    build_token_payload,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_user,
    is_refresh_token_revoked,
    revoke_refresh_token,
    token_user_from_payload,
    token_user_from_record,
    verify_totp,
    ACCESS_TOKEN_EXPIRE_SECONDS,
)
from ..dependencies import (
    get_current_user,
    get_mfa_pending_user,
    rate_limit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas de request/response
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class MFAVerifyRequest(BaseModel):
    totp_code: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="Código TOTP de 6 dígitos (Google Authenticator)",
    )


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token:  str
    token_type:    str = "bearer"
    expires_in:    int = ACCESS_TOKEN_EXPIRE_SECONDS
    refresh_token: Optional[str] = None
    mfa_required:  bool = False


# ---------------------------------------------------------------------------
# POST /auth/token — Step 1: autenticación con credenciales
# ---------------------------------------------------------------------------

@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtener token temporal (Step 1 del flujo MFA)",
)
async def login(
    body:    LoginRequest,
    _rate:   None = Depends(rate_limit("auth:token")),
) -> TokenResponse:
    """
    Autentica con username + password.

    Si las credenciales son válidas, retorna un **token temporal** con
    `mfa_pending=True`. Este token solo sirve para POST /auth/mfa/verify.

    El token temporal NO tiene acceso a ningún endpoint protegido.
    """
    user = authenticate_user(body.username, body.password)
    if user is None:
        # Mensaje genérico para prevenir enumeración de usuarios
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
        )

    # Crear token temporal con mfa_pending=True
    token_user = token_user_from_record(user, mfa_verified=False, mfa_pending=True)
    payload = build_token_payload(token_user)

    # Token temporal corto: 5 minutos para completar MFA
    temp_token = create_access_token(payload, expires_in=5 * 60)

    logger.info("LOGIN OK user=%s — MFA pendiente", body.username)
    return TokenResponse(
        access_token=temp_token,
        mfa_required=True,
        refresh_token=None,   # No se emite refresh hasta completar MFA
    )


# ---------------------------------------------------------------------------
# POST /auth/mfa/verify — Step 2: verificación TOTP
# ---------------------------------------------------------------------------

@router.post(
    "/mfa/verify",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Verificar código TOTP (Step 2 del flujo MFA)",
)
async def verify_mfa(
    body:     MFAVerifyRequest,
    user:     TokenUser = Depends(get_mfa_pending_user),
    _rate:    None = Depends(rate_limit("auth:mfa")),
) -> TokenResponse:
    """
    Verifica el código TOTP de 6 dígitos.

    Requiere el **token temporal** del POST /auth/token como Bearer.

    Si el código es válido:
      - Retorna `access_token` completo con `mfa_verified=True`
      - Retorna `refresh_token` para renovar el access token
    """
    # Buscar el usuario en la DB para obtener el TOTP secret
    db_user = get_user(user.username)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado",
        )

    if not db_user.mfa_enabled:
        # Si MFA está deshabilitado (no permitido en producción), emitir token directamente
        logger.warning("MFA deshabilitado para usuario %s — no permitido en producción", user.username)

    if not verify_totp(db_user.totp_secret, body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Código TOTP inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # MFA verificado — emitir access token completo y refresh token
    verified_user = token_user_from_record(db_user, mfa_verified=True, mfa_pending=False)
    payload = build_token_payload(verified_user)

    access_token  = create_access_token(payload)
    refresh_token = create_refresh_token(payload)

    logger.info("MFA OK user=%s rol=%s", user.username, user.role.value)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        mfa_required=False,
    )


# ---------------------------------------------------------------------------
# POST /auth/refresh — renovar access token con refresh token
# ---------------------------------------------------------------------------

@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Renovar access token con refresh token",
)
async def refresh_token(
    body:  RefreshRequest,
    _rate: None = Depends(rate_limit("auth:refresh")),
) -> TokenResponse:
    """
    Renueva el access token usando un refresh token válido.

    El nuevo access token tendrá `mfa_verified=True` porque el refresh
    token solo se emite tras completar el flujo MFA.
    """
    try:
        payload = decode_token(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Refresh token inválido: {exc}",
        )

    if payload.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token proporcionado no es un refresh token",
        )

    # Verificar que no está revocado
    jti = payload.get("jti", "")
    if is_refresh_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revocado — inicie sesión nuevamente",
        )

    # Verificar que el usuario sigue activo
    db_user = get_user(payload.get("username", ""))
    if db_user is None or not db_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inactivo o no encontrado",
        )

    # Emitir nuevo access token (mfa_verified=True heredado del refresh)
    verified_user = token_user_from_record(db_user, mfa_verified=True, mfa_pending=False)
    new_payload    = build_token_payload(verified_user)
    new_access     = create_access_token(new_payload)

    logger.info("REFRESH OK user=%s", payload.get("username"))
    return TokenResponse(
        access_token=new_access,
        refresh_token=body.refresh_token,   # El mismo refresh token
        mfa_required=False,
    )


# ---------------------------------------------------------------------------
# POST /auth/logout — revocar refresh token
# ---------------------------------------------------------------------------

@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Cerrar sesión y revocar refresh token",
)
async def logout(
    body: RefreshRequest,
    user: TokenUser = Depends(get_current_user),
) -> dict:
    """
    Revoca el refresh token proporcionado.

    Requiere access token válido (con mfa_verified=True) como Bearer.
    """
    try:
        payload = decode_token(body.refresh_token)
        jti = payload.get("jti", "")
        if jti:
            revoke_refresh_token(jti)
    except ValueError:
        pass   # Si el refresh ya expiró, logout igual es válido

    logger.info("LOGOUT user=%s", user.username)
    return {"message": "Sesión cerrada exitosamente"}


# ---------------------------------------------------------------------------
# GET /auth/me — información del usuario autenticado
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    summary="Obtener información del usuario autenticado",
)
async def get_me(
    user: TokenUser = Depends(get_current_user),
) -> dict:
    """
    Retorna la información del usuario autenticado extraída del JWT.
    No hace query a la base de datos.
    """
    return {
        "user_id":   user.user_id,
        "username":  user.username,
        "role":      user.role.value,
        "client_id": user.client_id,
        "permissions": sorted(user.permissions),
    }
