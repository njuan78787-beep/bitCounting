# =============================================================================
# api/main.py
# Bit-Counting FastAPI application entry point.
#
# Autonomous AI Accounting System for Puerto Rico.
# Supervised by licensed CPAs; every decision is traceable.
#
# Architecture:
#   - INTAKE → CENTINELA → CLASIFICADOR → AUDITOR → ORQUESTADOR
#   - FISCAL PR handles tax calculations (IVU, patentes, planillas)
#   - CPA dashboard with friction prevents rubber-stamp approvals
#   - Normative monitor watches 7 PR regulatory sources
#
# Routers:
#   /auth               — autenticación JWT + MFA TOTP
#   /api/v1/documents   — document processing (intake pipeline)
#   /api/v1/transactions — transaction management
#   /api/v1/centinela   — pausas CENTINELA (separado del CPA dashboard)
#   /api/v1/cpa         — CPA dashboard (review queue, instrucciones, métricas)
#   /api/v1/reports     — financial reports (balance sheet, P&L, cash flow, IVU)
#   /api/v1/normative   — normative change monitoring
#   /api/v1/admin       — administración (solo EXIMIA_ADMIN)
#   /api/v1/firm-connector — FirmConnector: sync externo (CAPA 1 — EXIMIA_ADMIN)
#
# SEGURIDAD:
#   - SecurityHeadersMiddleware: HSTS, CSP, X-Frame-Options, etc.
#   - AccessLogMiddleware: user_id, endpoint, response_time_ms en cada request
#   - CORS restringido a orígenes conocidos (env var en producción)
# =============================================================================

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .middleware import AccessLogMiddleware, SecurityHeadersMiddleware
from .routes import documents, transactions, cpa_dashboard, reports, normative
from .routes import auth_router, centinela, admin, firm_connector as firm_connector_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# APPLICATION LIFECYCLE
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Startup: initialize DB (create tables + seed demo data) and Redis.
    Shutdown: close all connections gracefully.
    """
    logger.info("Bit-Counting API starting — Puerto Rico autonomous accounting system")
    logger.info("Agent pipeline: INTAKE → CENTINELA → CLASIFICADOR → AUDITOR → ORQUESTADOR")

    # 1. Redis (non-blocking — falls back to in-memory if unavailable)
    from .database import init_redis
    await init_redis()

    # 2. Database: create tables
    from .database import create_tables, _session_factory
    await create_tables()

    # 3. Seed demo data (idempotent — no-ops if rows already exist)
    if _session_factory is not None:
        from .db.seed import run_seed
        async with _session_factory() as db:
            try:
                await run_seed(db)
            except Exception as exc:
                logger.warning("Seed failed (DB may not be ready yet): %s", exc)

    yield

    logger.info("Bit-Counting API shutting down")
    from .database import close_connections
    await close_connections()


# ---------------------------------------------------------------------------
# APPLICATION INSTANCE
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Bit-Counting API",
    description=(
        "Autonomous AI Accounting System for Puerto Rico. "
        "Provides document processing, transaction management, CPA supervision, "
        "financial reports, and Puerto Rico tax compliance (IVU, Hacienda PR)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS MIDDLEWARE — allows React dashboard to communicate with the API
# ---------------------------------------------------------------------------

# Parse ALLOWED_ORIGINS from env var (comma-separated list of origins).
# Defaults to local dev origins if not set.
_DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

def _parse_allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    if not raw:
        return _DEFAULT_ORIGINS
    extras = [o.strip() for o in raw.split(",") if o.strip()]
    return _DEFAULT_ORIGINS + extras


# ---------------------------------------------------------------------------
# SECURITY MIDDLEWARES (se aplican en orden inverso al que se registran)
# ---------------------------------------------------------------------------

# 1. Security headers en todas las respuestas
app.add_middleware(SecurityHeadersMiddleware)

# 2. Access log: user_id, endpoint, response_time_ms
app.add_middleware(AccessLogMiddleware)

# 3. CORS — default to localhost dev; production adds origins via ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Response-Time-Ms", "X-Process-Time-Ms"],
)


# ---------------------------------------------------------------------------
# REQUEST TIMING MIDDLEWARE
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_process_time_header(request: Request, call_next) -> Response:
    """Add X-Process-Time header to all responses for monitoring."""
    start_time = time.perf_counter()
    response = await call_next(request)
    process_time_ms = (time.perf_counter() - start_time) * 1000
    response.headers["X-Process-Time-Ms"] = f"{process_time_ms:.2f}"
    return response


# ---------------------------------------------------------------------------
# GLOBAL EXCEPTION HANDLER
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler — never expose internal details to the client."""
    logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred. The Bit-Counting team has been notified.",
        },
    )


# ---------------------------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------------------------

@app.get(
    "/",
    tags=["health"],
    summary="Root health check",
    response_description="Service status, system name, and version",
)
async def root_health_check() -> dict:
    """
    Root health check endpoint as specified in the API contract.
    Returns HTTP 200 with system identification.
    """
    return {
        "status": "ok",
        "system": "bit-counting",
        "version": "1.0.0",
    }


@app.get(
    "/health",
    tags=["health"],
    summary="Detailed API health check",
    response_description="Service status and version",
)
async def health_check() -> dict:
    """
    Health check endpoint.
    Returns HTTP 200 if the API is running and accepting requests.
    Used by load balancers and monitoring tools.
    """
    return {
        "status": "ok",
        "version": "1.0.0",
        "service": "bit-counting-api",
        "description": "Autonomous AI Accounting System for Puerto Rico",
    }


# ---------------------------------------------------------------------------
# BACKGROUND TASK DEMO ENDPOINT
# ---------------------------------------------------------------------------

def _log_background_task(task_name: str, payload: dict) -> None:
    """Example background task — logs async processing requests."""
    logger.info("Background task '%s' completed with payload: %s", task_name, payload)


@app.post(
    "/api/v1/background-demo",
    tags=["health"],
    summary="Demo endpoint showing background task capability",
    include_in_schema=False,  # Hidden from public docs
)
async def background_demo(background_tasks: BackgroundTasks) -> dict:
    """
    Demonstrates FastAPI BackgroundTasks for async processing.
    Used in Phase 2 for non-blocking document ingestion and report generation.
    """
    background_tasks.add_task(
        _log_background_task,
        "demo_task",
        {"timestamp": time.time()},
    )
    return {"message": "Background task queued", "status": "accepted"}


# ---------------------------------------------------------------------------
# ROUTER REGISTRATION
# ---------------------------------------------------------------------------

app.include_router(auth_router.router)
app.include_router(documents.router)
app.include_router(transactions.router)
app.include_router(centinela.router)
app.include_router(cpa_dashboard.router)
app.include_router(reports.router)
app.include_router(normative.router)
app.include_router(admin.router)
app.include_router(firm_connector_router.router)   # CAPA 1 — EXIMIA_ADMIN only


# ---------------------------------------------------------------------------
# ENTRY POINT (for direct execution during development)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
