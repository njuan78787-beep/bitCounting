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
#   /api/v1/documents   — document processing (intake pipeline)
#   /api/v1/transactions — transaction management
#   /api/v1/cpa         — CPA dashboard (pauses, review queue, metrics)
#   /api/v1/reports     — financial reports (balance sheet, P&L, cash flow, IVU)
#   /api/v1/normative   — normative change monitoring
# =============================================================================

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routes import documents, transactions, cpa_dashboard, reports, normative

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
    """Startup and shutdown logic for the Bit-Counting API."""
    logger.info("Bit-Counting API starting — Puerto Rico autonomous accounting system")
    logger.info("Agent pipeline: INTAKE → CENTINELA → CLASIFICADOR → AUDITOR → ORQUESTADOR")
    yield
    logger.info("Bit-Counting API shutting down")


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # React dev server
        "http://localhost:5173",   # Vite dev server
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        # Production origins configured via environment variable in Phase 2
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    "/health",
    tags=["health"],
    summary="API health check",
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

app.include_router(documents.router)
app.include_router(transactions.router)
app.include_router(cpa_dashboard.router)
app.include_router(reports.router)
app.include_router(normative.router)


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
