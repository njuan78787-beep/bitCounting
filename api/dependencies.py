# =============================================================================
# api/dependencies.py
# FastAPI dependencies para autenticación, autorización y rate limiting.
#
# USO EN ENDPOINTS:
#   @router.get("/resource")
#   async def my_endpoint(
#       user: TokenUser = Depends(get_current_user),
#       _rate: None = Depends(rate_limit("transactions:upload")),
#   ):
#       require_permission_check(user, "transactions:read")
#       verify_client_scope(user, request_client_id)
#       ...
#
# GARANTÍAS:
#   - get_current_user: rechaza token sin mfa_verified=True con 403
#   - require_role: rechaza si el rol del usuario no está en la lista permitida
#   - verify_client_scope: rechaza si el usuario intenta acceder a datos de otro cliente
#   - rate_limit: rechaza si el usuario excede los límites definidos en auth.py
# =============================================================================

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import (
    Role,
    TokenUser,
    decode_token,
    token_user_from_payload,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bearer token extractor
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# get_current_user — dependencia principal de autenticación
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> TokenUser:
    """
    Extrae y valida el Bearer token del header Authorization.

    Raises:
        401 — si no hay token o el token es inválido/expirado
        403 — si el token es válido pero MFA no fue completado
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header requerido (Bearer token)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verificar que el token es de tipo access (no refresh)
    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de tipo incorrecto — se requiere access token",
        )

    user = token_user_from_payload(payload)

    # MFA obligatorio — rechazar si mfa_verified=False
    if not user.mfa_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MFA requerido. Complete la verificación en POST /auth/mfa/verify",
        )

    return user


async def get_mfa_pending_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> TokenUser:
    """
    Extrae el usuario de un token con mfa_pending=True.
    Usado exclusivamente por POST /auth/mfa/verify.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {exc}",
        )
    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un access token temporal (del POST /auth/token)",
        )
    user = token_user_from_payload(payload)
    if not user.mfa_pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El token no está en estado MFA pendiente",
        )
    return user


# ---------------------------------------------------------------------------
# require_role — fábrica de dependencias de autorización
# ---------------------------------------------------------------------------

def require_role(*roles: Role):
    """
    Retorna una dependencia FastAPI que verifica que el usuario tiene
    uno de los roles especificados.

    Uso:
        @router.get("/admin/...")
        async def admin_endpoint(
            user: TokenUser = Depends(require_role(Role.EXIMIA_ADMIN)),
        ):
    """
    allowed = frozenset(roles)

    async def _check_role(
        user: TokenUser = Depends(get_current_user),
    ) -> TokenUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Acceso denegado. Rol requerido: "
                    f"{', '.join(r.value for r in allowed)}. "
                    f"Rol actual: {user.role.value}"
                ),
            )
        return user

    return _check_role


def require_permission(permission: str):
    """
    Retorna una dependencia que verifica que el usuario tiene un permiso específico.

    Uso:
        user: TokenUser = Depends(require_permission("normative:activate"))
    """
    async def _check_perm(
        user: TokenUser = Depends(get_current_user),
    ) -> TokenUser:
        if permission not in user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso requerido: {permission}",
            )
        return user

    return _check_perm


# ---------------------------------------------------------------------------
# verify_client_scope — el usuario solo puede acceder a sus propios datos
# ---------------------------------------------------------------------------

def verify_client_scope(user: TokenUser, requested_client_id: str) -> None:
    """
    Verifica que el usuario tiene acceso al client_id solicitado.

    Reglas:
      - EXIMIA_ADMIN: accede a cualquier cliente
      - CPA_SENIOR: accede a cualquier cliente (supervisión global)
      - CPA_PARTNER / CLIENT: solo accede a su propio client_id
      - CLIENT sin client_id asignado: rechazado siempre

    Raises:
        403 — si el usuario no tiene acceso al client_id solicitado
    """
    # EXIMIA_ADMIN y CPA_SENIOR tienen acceso global
    if user.role in (Role.EXIMIA_ADMIN, Role.CPA_SENIOR):
        return

    # Los demás roles solo acceden a su propio cliente
    if user.client_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario sin cliente asignado no puede acceder a datos de clientes",
        )
    if user.client_id != requested_client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: no puede acceder a datos de otro cliente",
        )


# ---------------------------------------------------------------------------
# Rate Limiter — sliding window en-memoria
# ---------------------------------------------------------------------------

class _RateLimiter:
    """
    Rate limiter con ventana deslizante por (user_id, endpoint_key).

    Límites configurados en auth._RATE_LIMITS:
      - "default":             100 req/min
      - "auth:token":          5 req/min
      - "auth:mfa":            5 req/min
      - "transactions:upload": 20 req/min
      - etc.
    """

    # Límites por endpoint_key: (max_requests, window_seconds)
    _LIMITS: dict[str, tuple[int, int]] = {
        "default":              (100, 60),
        "auth:token":           (5,   60),
        "auth:mfa":             (5,   60),
        "auth:refresh":         (20,  60),
        "transactions:upload":  (20,  60),
        "cpa:approve":          (30,  60),
        "admin:write":          (10,  60),
        "reports:read":         (60,  60),
    }

    def __init__(self) -> None:
        # (user_key, endpoint_key) → deque of timestamps
        self._windows: dict[tuple[str, str], deque] = {}

    def reset(self) -> None:
        """Clear all rate-limit windows (useful in tests)."""
        self._windows.clear()

    def check(self, user_key: str, endpoint_key: str) -> None:
        """
        Verifica el rate limit para un usuario y endpoint.

        Raises:
            429 Too Many Requests si se excede el límite.
        """
        max_req, window_sec = self._LIMITS.get(
            endpoint_key, self._LIMITS["default"]
        )

        bucket_key = (user_key, endpoint_key)
        if bucket_key not in self._windows:
            self._windows[bucket_key] = deque()

        window = self._windows[bucket_key]
        now = time.monotonic()
        cutoff = now - window_sec

        # Eliminar timestamps fuera de la ventana
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= max_req:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit excedido para {endpoint_key}: "
                    f"máximo {max_req} requests por {window_sec}s. "
                    "Intente nuevamente más tarde."
                ),
                headers={"Retry-After": str(window_sec)},
            )

        window.append(now)


# Instancia global del rate limiter
_rate_limiter = _RateLimiter()


def rate_limit(endpoint_key: str = "default"):
    """
    Fábrica de dependencias para rate limiting.

    Identifica al usuario por su IP cuando no hay JWT, o por user_id si hay JWT.

    Uso:
        @router.post("/upload")
        async def upload(
            _: None = Depends(rate_limit("transactions:upload")),
            user: TokenUser = Depends(get_current_user),
        ):
    """
    async def _check_rate(request: Request) -> None:
        # Usar user_id del JWT si disponible, si no usar IP
        auth_header = request.headers.get("Authorization", "")
        user_key = request.client.host if request.client else "unknown"
        if auth_header.startswith("Bearer "):
            try:
                payload = decode_token(auth_header[7:])
                user_key = payload.get("sub", user_key)
            except Exception:
                pass  # Token inválido → usar IP
        _rate_limiter.check(user_key, endpoint_key)

    return _check_rate
