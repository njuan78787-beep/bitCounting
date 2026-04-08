# =============================================================================
# api/middleware.py
# Middleware de Bit-Counting: logging de acceso + cabeceras de seguridad.
#
# MIDDLEWARES:
#
#   AccessLogMiddleware
#     Registra cada request con: user_id, método, path, status, response_time_ms.
#     El user_id se extrae del JWT sin validarlo (solo decode), para no duplicar
#     la validación que ya hace get_current_user. Si no hay JWT, user_id="anon".
#     NUNCA registra passwords, tokens, ni datos de payload del body.
#
#   SecurityHeadersMiddleware
#     Agrega cabeceras de seguridad HTTP a todas las respuestas:
#       - Strict-Transport-Security (HSTS): fuerza HTTPS por 1 año
#       - X-Content-Type-Options: nosniff
#       - X-Frame-Options: DENY
#       - Content-Security-Policy: restricto
#       - Referrer-Policy: strict-origin-when-cross-origin
#       - Permissions-Policy: deshabilita APIs innecesarias
#       - X-XSS-Protection: 1; mode=block (navegadores legacy)
#
# REGISTRO EN main.py:
#   app.add_middleware(SecurityHeadersMiddleware)
#   app.add_middleware(AccessLogMiddleware)
# =============================================================================

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("bitcounting.access")


# ---------------------------------------------------------------------------
# Utilidad: extraer user_id del JWT sin validar la firma
# (Solo para logging — la validación real la hace get_current_user)
# ---------------------------------------------------------------------------

def _extract_user_id_unsafe(authorization: Optional[str]) -> str:
    """
    Extrae el user_id (sub) del JWT sin verificar firma.
    Usado solo para logging — no confiar en este valor para autorización.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return "anon"
    try:
        token = authorization[7:]
        parts = token.split(".")
        if len(parts) != 3:
            return "anon"
        # Decodificar payload (índice 1) sin verificar firma
        p_b64 = parts[1]
        pad = 4 - len(p_b64) % 4
        if pad != 4:
            p_b64 += "=" * pad
        payload = json.loads(base64.urlsafe_b64decode(p_b64))
        user_id = payload.get("sub", "anon")
        # Solo tomar los primeros 8 chars para privacidad en logs
        return str(user_id)[:8] + "..." if len(str(user_id)) > 8 else str(user_id)
    except Exception:
        return "anon"


# ---------------------------------------------------------------------------
# Middleware 1: Logging de acceso
# ---------------------------------------------------------------------------

class AccessLogMiddleware(BaseHTTPMiddleware):
    """
    Registra cada request HTTP con:
      - user_id (de JWT, parcial para privacidad)
      - método HTTP
      - path (sin query string)
      - código de respuesta
      - response_time_ms
      - IP del cliente

    Nunca registra:
      - Passwords ni tokens completos
      - Body del request (puede contener datos sensibles)
      - Query strings (pueden contener parámetros sensibles)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()

        # Extraer info del request ANTES de ejecutar (para logs de error también)
        method   = request.method
        path     = request.url.path          # sin query string
        user_id  = _extract_user_id_unsafe(request.headers.get("Authorization"))
        client_ip = request.client.host if request.client else "unknown"

        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - start) * 1000

            logger.info(
                "ACCESS user=%s %s %s -> %d (%.1fms) ip=%s",
                user_id,
                method,
                path,
                response.status_code,
                elapsed_ms,
                client_ip,
            )
            # Agregar header de tiempo de proceso
            response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
            return response

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "ACCESS ERROR user=%s %s %s -> 500 (%.1fms) ip=%s error=%s",
                user_id, method, path, elapsed_ms, client_ip,
                type(exc).__name__,
            )
            raise


# ---------------------------------------------------------------------------
# Middleware 2: Cabeceras de seguridad HTTP
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = {
    # Fuerza HTTPS por 1 año, incluye subdominios
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",

    # Previene MIME-type sniffing
    "X-Content-Type-Options": "nosniff",

    # Previene clickjacking — nunca embeder en frames
    "X-Frame-Options": "DENY",

    # CSP restrictivo:
    #   - default-src 'none': bloquea todo por defecto
    #   - script-src 'self': solo scripts del mismo origen
    #   - connect-src 'self': solo conexiones al mismo origen
    #   - frame-ancestors 'none': no puede ser embebido en ningún frame
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),

    # Controla la información de referrer enviada con requests
    "Referrer-Policy": "strict-origin-when-cross-origin",

    # Deshabilita APIs de hardware innecesarias
    "Permissions-Policy": (
        "accelerometer=(), "
        "camera=(), "
        "geolocation=(), "
        "gyroscope=(), "
        "magnetometer=(), "
        "microphone=(), "
        "payment=(), "
        "usb=()"
    ),

    # Para navegadores legacy
    "X-XSS-Protection": "1; mode=block",

    # Identifica el sistema sin revelar tecnología interna
    "X-Powered-By": "Bit-Counting/1.0",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Agrega cabeceras de seguridad HTTP a todas las respuestas.

    Las cabeceras están definidas en _SECURITY_HEADERS.
    No sobrescribe cabeceras que el endpoint haya establecido explícitamente.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            if header not in response.headers:
                response.headers[header] = value
        return response
