# ============================================================
# TestLookup — Backend Dockerfile (Multi-Stage)
# ============================================================

# ── Base stage ───────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    libpq-dev \
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

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r testlookup && useradd -r -g testlookup testlookup

# Copy only production dependencies
COPY --from=base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --from=builder /app/app ./app
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini .
COPY --from=builder /app/scripts ./scripts

# Client SDK files served at /api/v1/sdk/{lang}. Staged into the build
# context as backend/__client_sdks_staged/ by the deploy script (see
# homelabsetup/deploy-homelab.sh). docker-compose dev path is unaffected —
# it targets the `development` stage and uses a host volume mount instead.
COPY --from=builder /app/__client_sdks_staged ./client_sdks

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production

RUN chown -R testlookup:testlookup /app
USER testlookup

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4"]
