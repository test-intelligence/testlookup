# ============================================================
# TestLookup — Backend Dockerfile (Multi-Stage)
# ============================================================

# ── Base stage ───────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
# procps provides `pgrep`, used by the Celery beat liveness probe (k8s/base/worker-deployments.yaml)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    libpq-dev \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r testlookup && useradd -r -g testlookup testlookup

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Development stage ────────────────────────────────────────
FROM base AS development

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=development

# Install development tools
RUN pip install --no-cache-dir watchfiles

COPY . .

RUN chown -R testlookup:testlookup /app
USER testlookup

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]

# ── Production build stage ───────────────────────────────────
FROM base AS builder

COPY . .

# Compile Python files for faster startup
RUN python -m compileall -q app/

# ── Production stage ─────────────────────────────────────────
FROM python:3.11-slim AS production

WORKDIR /app

# procps provides `pgrep`, used by the Celery beat liveness probe (k8s/base/worker-deployments.yaml)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    procps \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r testlookup && useradd -r -g testlookup testlookup

# Copy only production dependencies
COPY --from=base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --from=builder /app/app ./app
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production

RUN chown -R testlookup:testlookup /app
USER testlookup

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
