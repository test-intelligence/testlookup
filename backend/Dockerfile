# ============================================================
# TestLookup — Backend Dockerfile (Multi-Stage)
# ============================================================

# ── Base stage ───────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
# procps provides `pgrep`, used by the Celery beat liveness probe (k8s/base/worker-deployments.yaml)
# ca-certificates provides the OS trust bundle (/etc/ssl/certs/ca-certificates.crt) +
# update-ca-certificates, used below to trust a TLS-interception root CA when present.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gcc \
    g++ \
    libpq-dev \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user at UID 1000 to match the k8s securityContext (runAsUser: 1000),
# so file ownership of /app lines up with the runtime user in-cluster.
RUN groupadd -r -g 1000 testlookup && useradd -r -u 1000 -g testlookup testlookup

# ── TLS-interception CA support (homelab builds behind an HTTPS-inspecting
#    middlebox: Norton/NordVPN SSL scanning, corporate proxy) ─────────────
# When the host re-signs TLS with a private root CA, in-container pip can't
# verify pypi.org against the public roots and dies with
#   "unable to get local issuer certificate".
# deploy-homelab.sh exports that root CA into backend/certs/*.crt and builds
# with --build-arg INSTALL_EXTRA_CA=1, which merges it into the OS bundle.
# certs/ always exists (committed .keep). On CI the dir holds only .keep,
# INSTALL_EXTRA_CA defaults to 0, and pip uses the public roots unchanged.
ARG INSTALL_EXTRA_CA=0
COPY certs/ /usr/local/share/ca-certificates/
RUN if [ "$INSTALL_EXTRA_CA" = "1" ]; then update-ca-certificates; fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

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

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]

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
# apt-get upgrade takes Debian's security fixes for packages the base image
# already carries; installing alone never upgrades them (see mcp/Dockerfile and
# scripts/test_ci_security.py::test_the_debian_runtime_images_take_security_updates).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libpq-dev \
    procps \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r -g 1000 testlookup && useradd -r -u 1000 -g testlookup testlookup

# Trust the same TLS-interception CA at runtime so outbound HTTPS (webhooks,
# Jira, hosted LLM/eval) works through the middlebox. No-op when certs/ holds
# only the .keep sentinel / INSTALL_EXTRA_CA=0. See the base stage for detail.
ARG INSTALL_EXTRA_CA=0
COPY certs/ /usr/local/share/ca-certificates/
RUN if [ "$INSTALL_EXTRA_CA" = "1" ]; then update-ca-certificates; fi
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Copy only production dependencies
COPY --from=base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --from=builder /app/app ./app
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini .
COPY --from=builder /app/gunicorn_conf.py ./gunicorn_conf.py
COPY --from=builder /app/scripts ./scripts

# Client SDK files served at /api/v1/sdk/{lang}. Staged into the build
# context as backend/__client_sdks_staged/ by the deploy script (see
# homelabsetup/deploy-homelab.sh). docker-compose dev path is unaffected —
# it targets the `development` stage and uses a host volume mount instead.
COPY --from=builder /app/__client_sdks_staged ./client_sdks

# No packaging toolchain in the runtime image (Trivy on PR #26). pip,
# setuptools and wheel are build tools: nothing at runtime imports
# pkg_resources, setuptools or distutils (the only third-party importers are
# guarded fallbacks, passlib.pwd which nothing imports, and numpy/cffi build
# helpers). Removing them clears setuptools CVE-2025-47273, the wheel 0.45.1
# and jaraco.context 5.3.0 vendored inside setuptools, and the msgpack 1.1.2 /
# setuptools 70.3.0 that pip vendors (pip/_vendor/vendor.txt). Deleted by
# path (a copied site-packages can hold two pip dist-infos), then proven gone.
RUN rm -rf /usr/local/lib/python3.11/site-packages/pip \
        /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.11/site-packages/setuptools \
        /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
        /usr/local/lib/python3.11/site-packages/pkg_resources \
        /usr/local/lib/python3.11/site-packages/_distutils_hack \
        /usr/local/lib/python3.11/site-packages/distutils-precedence.pth \
        /usr/local/lib/python3.11/site-packages/wheel \
        /usr/local/lib/python3.11/site-packages/wheel-*.dist-info \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 /usr/local/bin/wheel \
    && python -c "import importlib.util, sys; left = [m for m in ('pip', 'setuptools', 'pkg_resources', 'wheel', '_distutils_hack') if importlib.util.find_spec(m)]; sys.exit('build tooling still importable: %s' % left if left else 0)"

# Build provenance — the commit SHA + build timestamp this image was built
# from. Wired from CI so a running container can self-report its
# origin via GET /health/details. Empty by default (local builds) → reported
# as "unknown".
ARG BUILD_REVISION=""
ARG BUILD_DATE=""

# ChromaDB's bundled ONNX embedder downloads its local model under HOME. The
# Kubernetes runtime user has no writable /home/<user>, while /tmp is writable.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    HOME=/tmp \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc \
    BUILD_REVISION=$BUILD_REVISION \
    BUILD_DATE=$BUILD_DATE

RUN mkdir -p "$PROMETHEUS_MULTIPROC_DIR" && \
    chown -R testlookup:testlookup /app "$PROMETHEUS_MULTIPROC_DIR"
USER testlookup

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["sh", "-c", "rm -f \"$PROMETHEUS_MULTIPROC_DIR\"/*.db && exec gunicorn -c gunicorn_conf.py app.main:app"]
