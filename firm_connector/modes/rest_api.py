# =============================================================================
# firm_connector/modes/rest_api.py
# Mode 2 — OAuth 2.0 REST API connection to an external firm's web API.
#
# FEATURES:
#   - Client-credentials OAuth 2.0 with automatic token refresh
#   - Rate limit compliance: never exceeds the firm's declared RPS ceiling
#   - Webhook receiver for real-time updates (registered via API)
#   - Polling fallback every 15 minutes when webhooks are unavailable
#   - Exponential backoff on 429 / 5xx responses
# =============================================================================

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..models import DateRange, OAuthConfig

logger = logging.getLogger(__name__)

_MAX_RETRIES     = 3
_RETRY_BASE_SECS = 2.0
_REQUEST_TIMEOUT = 30   # seconds


class RestAPIAuthError(Exception):
    """OAuth token cannot be obtained or refreshed."""


class RestAPIRateLimitError(Exception):
    """Rate limit exceeded and max retries exhausted."""


class RestAPIMode:
    """
    HTTP client for an external firm REST API with OAuth 2.0.

    Usage:
        mode = RestAPIMode(config)
        async with mode.client_context() as client:
            clients = await mode.fetch_clients(client, date_range)
    """

    def __init__(self, config: OAuthConfig) -> None:
        self._config       = config
        self._access_token: Optional[str]   = None
        self._token_expiry: Optional[float] = None   # Unix timestamp
        self._last_request: float           = 0.0    # monotonic

    # -----------------------------------------------------------------------
    # Token management
    # -----------------------------------------------------------------------

    async def _ensure_token(self, client) -> str:
        """Return a valid access token, refreshing if necessary."""
        now = time.time()
        if self._access_token and self._token_expiry and now < self._token_expiry - 30:
            return self._access_token

        from ..encryption import decrypt_field
        secret = decrypt_field(self._config.client_secret_encrypted)

        resp = await client.post(
            self._config.token_url,
            data={
                "grant_type":    "client_credentials",
                "client_id":     self._config.client_id,
                "client_secret": secret,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RestAPIAuthError(
                f"OAuth token request failed: HTTP {resp.status_code}"
            )
        payload = resp.json()
        self._access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expiry = time.time() + expires_in
        logger.info("FIRM_API: OAuth token obtained (expires in %ds)", expires_in)
        return self._access_token

    # -----------------------------------------------------------------------
    # Rate limiting
    # -----------------------------------------------------------------------

    async def _rate_limited_request(self, client, method: str, url: str, **kwargs) -> Any:
        """
        Execute an HTTP request honoring the firm's rate limit.
        Backs off exponentially on 429 responses.
        """
        min_interval = 1.0 / max(self._config.rate_limit_rps, 1)
        last_exc: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES + 1):
            # Enforce inter-request delay
            elapsed = time.monotonic() - self._last_request
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)

            token = await self._ensure_token(client)
            headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}

            try:
                resp = await client.request(
                    method, url, headers=headers, timeout=_REQUEST_TIMEOUT, **kwargs
                )
                self._last_request = time.monotonic()
            except Exception as exc:
                last_exc = exc
                delay = _RETRY_BASE_SECS * (2 ** attempt)
                logger.warning(
                    "FIRM_API: Request %s %s failed (%s). Retry in %.0fs.",
                    method, url, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", _RETRY_BASE_SECS * (2 ** attempt)))
                logger.warning("FIRM_API: 429 Too Many Requests — sleeping %ds", retry_after)
                await asyncio.sleep(retry_after)
                continue

            if resp.status_code >= 500:
                delay = _RETRY_BASE_SECS * (2 ** attempt)
                logger.warning(
                    "FIRM_API: %d server error — retrying in %.0fs.", resp.status_code, delay
                )
                await asyncio.sleep(delay)
                continue

            resp.raise_for_status()
            return resp

        raise RestAPIRateLimitError(
            f"Max retries ({_MAX_RETRIES}) exhausted for {method} {url}. "
            f"Last error: {last_exc}"
        )

    # -----------------------------------------------------------------------
    # Data fetchers
    # -----------------------------------------------------------------------

    @asynccontextmanager
    async def client_context(self) -> AsyncGenerator[Any, None]:
        """Yield an httpx.AsyncClient for use in fetch_* methods."""
        try:
            import httpx  # type: ignore[import]
        except ImportError:
            raise RuntimeError("httpx is required for REST_API mode: pip install httpx")
        async with httpx.AsyncClient() as client:
            yield client

    async def fetch_clients(
        self, client, date_range: Optional[DateRange] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {}
        if date_range:
            params["updated_from"] = date_range.start.isoformat()
            params["updated_to"]   = date_range.end.isoformat()

        resp = await self._rate_limited_request(
            client, "GET",
            f"{self._config.base_api_url}/clients",
            params=params,
        )
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        logger.info("FIRM_API: Fetched %d client records", len(items))
        return items

    async def fetch_transactions(
        self, client, date_range: Optional[DateRange] = None
    ) -> List[Dict[str, Any]]:
        params: Dict[str, str] = {}
        if date_range:
            params["date_from"] = date_range.start.isoformat()
            params["date_to"]   = date_range.end.isoformat()

        resp = await self._rate_limited_request(
            client, "GET",
            f"{self._config.base_api_url}/transactions",
            params=params,
        )
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        logger.info("FIRM_API: Fetched %d transaction records", len(items))
        return items

    async def fetch_employees(
        self, client, date_range: Optional[DateRange] = None
    ) -> List[Dict[str, Any]]:
        """
        Employees endpoint — SSN arrives in plaintext from the firm API.
        Encryption happens in sync.py immediately on receipt.
        """
        params: Dict[str, str] = {}
        if date_range:
            params["active_as_of"] = date_range.end.isoformat()

        resp = await self._rate_limited_request(
            client, "GET",
            f"{self._config.base_api_url}/employees",
            params=params,
        )
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        logger.info(
            "FIRM_API: Fetched %d employee records (SSN will be encrypted before storage)",
            len(items),
        )
        return items

    # -----------------------------------------------------------------------
    # Webhook validation
    # -----------------------------------------------------------------------

    def verify_webhook_signature(self, payload_bytes: bytes, signature_header: str) -> bool:
        """
        Verify an incoming webhook signature using HMAC-SHA256.
        Returns False if the signature is invalid or the secret is not configured.
        """
        if not self._config.webhook_secret_encrypted:
            logger.warning("FIRM_API: Webhook received but no secret configured — rejecting")
            return False

        from ..encryption import decrypt_field
        try:
            secret = decrypt_field(self._config.webhook_secret_encrypted).encode()
            expected = "sha256=" + hmac.new(secret, payload_bytes, sha256).hexdigest()
            return hmac.compare_digest(expected, signature_header)
        except Exception as exc:
            logger.error("FIRM_API: Webhook signature check failed: %s", type(exc).__name__)
            return False


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

async def start_polling_loop(
    mode: RestAPIMode,
    firm_id: str,
    on_data_received,   # callable(firm_id, raw_data: dict) -> None
    interval_minutes: int = 15,
) -> None:
    """
    Background polling loop: every `interval_minutes` minutes, fetch
    new data from the firm API and call on_data_received.
    Runs until cancelled.
    """
    logger.info(
        "FIRM_API: Starting polling loop for firm=%s every %d min",
        firm_id, interval_minutes,
    )
    while True:
        try:
            async with mode.client_context() as client:
                transactions = await mode.fetch_transactions(client)
            await on_data_received(firm_id, {"transactions": transactions})
        except Exception as exc:
            logger.warning("FIRM_API: Polling error for firm=%s: %s", firm_id, exc)
        await asyncio.sleep(interval_minutes * 60)
