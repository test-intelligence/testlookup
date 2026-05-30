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

# Create non-root user at UID 1000 to match the k8s securityContext (runAsUser: 1000),
# so file ownership of /app lines up with the runtime user in-cluster.
RUN groupadd -r -g 1000 testlookup && useradd -r -u 1000 -g testlookup testlookup

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

# Ensure the SDK staging directory always exists in the builder layer.
#
# Background: the production stage does
# ``COPY --from=builder /app/__client_sdks_staged ./client_sdks``.
# That directory is populated by the caller's CI (``ci.yml`` job
# ``build-images`` step "Stage client SDKs into backend context", and
# ``homelabsetup/deploy-homelab.sh``). When a deploy pipeline skips
# the staging step — e.g. ``deploy-{aks,gke,eks,staging,production}.yml``
# building directly with ``docker build ./backend`` — the path is
# missing and BuildKit fails to compute a cache key for the COPY,
# producing:
#   failed to compute cache key: failed to calculate checksum of ref
#   ...: "/app/__client_sdks_staged": not found
#
# ``mkdir -p`` + a ``.keep`` sentinel guarantees the path exists with
# at least one file so the cross-stage COPY's checksum step always
# succeeds. When SDKs ARE staged, the ``COPY . .`` above brings them
# in first and ``mkdir -p`` is a no-op (the sentinel just rides
# alongside). When SDKs are NOT staged, the directory is empty and
# ``/api/v1/sdk/{lang}`` falls through to its 404 path — the image
# still builds, healthy, and serves every other endpoint.
RUN mkdir -p /app/__client_sdks_staged && \
    touch /app/__client_sdks_staged/.keep

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

RUN groupadd -r -g 1000 testlookup && useradd -r -u 1000 -g testlookup testlookup

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
