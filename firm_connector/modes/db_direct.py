# =============================================================================
# firm_connector/modes/db_direct.py
# Mode 1 — Direct database connection to external firm PostgreSQL / MySQL.
#
# DESIGN PRINCIPLES:
#   - Read-only: Bit-Counting NEVER writes to the firm's database
#   - Max 5 concurrent connections (hard cap)
#   - 30-second query/connection timeout
#   - Exponential backoff retry (2s → 4s → 8s, max 3 retries)
#   - On total failure: raises FirmUnavailableError so caller can use cache
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..models import ClientFirm, DBDirectConfig, DateRange, EmployeeImportRow, TransactionFirm

logger = logging.getLogger(__name__)

_MAX_POOL_SIZE   = 5    # absolute cap — never more than 5 connections to the firm
_QUERY_TIMEOUT   = 30   # seconds
_MAX_RETRIES     = 3
_RETRY_BASE_SECS = 2.0


class FirmUnavailableError(Exception):
    """Raised when the firm database cannot be reached after all retries."""


# ---------------------------------------------------------------------------
# Connection pool management
# ---------------------------------------------------------------------------

class DirectDBMode:
    """
    Manages a read-only asyncpg connection pool to an external firm database.

    Usage:
        mode = DirectDBMode(config)
        async with mode.pool_context() as pool:
            clients = await mode.fetch_clients(pool, date_range)
    """

    def __init__(self, config: DBDirectConfig) -> None:
        self._config = config
        self._pool   = None

    @asynccontextmanager
    async def pool_context(self) -> AsyncGenerator[Any, None]:
        """
        Acquire the connection pool with retry logic.
        Yields the asyncpg Pool; disposes it on exit.
        Raises FirmUnavailableError after exhausting retries.
        """
        pool = await self._create_pool_with_retry()
        try:
            yield pool
        finally:
            await pool.close()

    async def _create_pool_with_retry(self) -> Any:
        """Create asyncpg pool with exponential backoff retry."""
        try:
            import asyncpg  # type: ignore[import]
        except ImportError:
            raise RuntimeError("asyncpg is required for DB_DIRECT mode: pip install asyncpg")

        from ..encryption import decrypt_field

        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                password = decrypt_field(self._config.password_encrypted)
                dsn = (
                    f"postgresql://{self._config.username}:{password}"
                    f"@{self._config.host}:{self._config.port}/{self._config.database}"
                )
                pool = await asyncio.wait_for(
                    asyncpg.create_pool(
                        dsn=dsn,
                        min_size=1,
                        max_size=min(self._config.max_pool_size, _MAX_POOL_SIZE),
                        command_timeout=_QUERY_TIMEOUT,
                        ssl="require" if self._config.ssl_mode == "require" else None,
                    ),
                    timeout=_QUERY_TIMEOUT,
                )
                logger.info(
                    "FIRM_DB: Connected to %s (pool max=%d)",
                    self._config.host,
                    min(self._config.max_pool_size, _MAX_POOL_SIZE),
                )
                return pool

            except (OSError, asyncio.TimeoutError, Exception) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_SECS * (2 ** attempt)
                    logger.warning(
                        "FIRM_DB: Connection attempt %d/%d failed (%s). "
                        "Retrying in %.0fs.",
                        attempt + 1, _MAX_RETRIES, type(exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)

        raise FirmUnavailableError(
            f"Cannot connect to firm database at {self._config.host} "
            f"after {_MAX_RETRIES} retries: {last_exc}"
        )

    # -----------------------------------------------------------------------
    # Data fetchers (read-only queries)
    # -----------------------------------------------------------------------

    async def fetch_clients(
        self, pool: Any, date_range: Optional[DateRange] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch all client records from the firm's database.
        The firm table name is expected to match the FirmConnector schema.
        """
        query = """
            SELECT *
            FROM clients_firm
            WHERE ($1::date IS NULL OR updated_at::date >= $1)
              AND ($2::date IS NULL OR updated_at::date <= $2)
        """
        start = date_range.start if date_range else None
        end   = date_range.end   if date_range else None
        try:
            async with pool.acquire() as conn:
                rows = await asyncio.wait_for(
                    conn.fetch(query, start, end),
                    timeout=_QUERY_TIMEOUT,
                )
            result = [dict(r) for r in rows]
            logger.info("FIRM_DB: Fetched %d client records", len(result))
            return result
        except Exception as exc:
            logger.error("FIRM_DB: fetch_clients failed: %s", type(exc).__name__)
            raise FirmUnavailableError(f"fetch_clients error: {exc}") from exc

    async def fetch_transactions(
        self, pool: Any, date_range: Optional[DateRange] = None
    ) -> List[Dict[str, Any]]:
        """Fetch transaction records in the given date range."""
        query = """
            SELECT *
            FROM transactions_firm
            WHERE ($1::date IS NULL OR date >= $1)
              AND ($2::date IS NULL OR date <= $2)
            ORDER BY date ASC
        """
        start = date_range.start if date_range else None
        end   = date_range.end   if date_range else None
        try:
            async with pool.acquire() as conn:
                rows = await asyncio.wait_for(
                    conn.fetch(query, start, end),
                    timeout=_QUERY_TIMEOUT,
                )
            result = [dict(r) for r in rows]
            logger.info("FIRM_DB: Fetched %d transaction records", len(result))
            return result
        except Exception as exc:
            logger.error("FIRM_DB: fetch_transactions failed: %s", type(exc).__name__)
            raise FirmUnavailableError(f"fetch_transactions error: {exc}") from exc

    async def fetch_employees(
        self, pool: Any, date_range: Optional[DateRange] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch employee records from the firm's database.
        SSN values arrive from the firm as plaintext — they are encrypted
        immediately by the caller (sync.py) before any logging or storage.
        """
        query = """
            SELECT *
            FROM employees_firm
            WHERE ($1::date IS NULL OR hire_date >= $1)
               OR ($2::date IS NULL OR termination_date >= $2)
        """
        start = date_range.start if date_range else None
        end   = date_range.end   if date_range else None
        try:
            async with pool.acquire() as conn:
                rows = await asyncio.wait_for(
                    conn.fetch(query, start, end),
                    timeout=_QUERY_TIMEOUT,
                )
            # Return raw dicts — SSN encryption happens in sync.py immediately
            result = [dict(r) for r in rows]
            logger.info(
                "FIRM_DB: Fetched %d employee records (SSN will be encrypted before storage)",
                len(result),
            )
            return result
        except Exception as exc:
            logger.error("FIRM_DB: fetch_employees failed: %s", type(exc).__name__)
            raise FirmUnavailableError(f"fetch_employees error: {exc}") from exc
