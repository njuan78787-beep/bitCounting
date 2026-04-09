# =============================================================================
# Bit-Counting API — Multi-stage Dockerfile
# Python 3.12 · FastAPI · SQLAlchemy async · asyncpg · Redis
#
# Stages:
#   builder  — installs Python dependencies into /opt/venv
#   runtime  — minimal image with only what's needed to run
#
# Build:
#   docker build -t bit-counting-api .
#
# Run:
#   docker run -p 8000:8000 \
#     -e DATABASE_URL=postgresql://... \
#     -e REDIS_URL=redis://redis:6379/0 \
#     -e JWT_SECRET=... \
#     bit-counting-api
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for asyncpg + hiredis C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated virtualenv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Bit-Counting API"
LABEL org.opencontainers.image.description="Autonomous AI Accounting System for Puerto Rico"

# libpq runtime (asyncpg needs it)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN groupadd --gid 1000 appgroup \
 && useradd  --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY --chown=appuser:appgroup . .

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

# Uvicorn: 4 workers, bound to all interfaces
CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--log-level", "info", \
     "--access-log"]
