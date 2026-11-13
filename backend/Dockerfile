# ============================================================
# TestLookup — Backend Dockerfile (Multi-Stage)
# ============================================================

# ── Base stage ───────────────────────────────────────────────
# Pinned by digest, here and in the production stage, so every build starts
# from the same base; Dependabot's docker updates for /backend move both.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS base

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

# ── TLS-interception CA support (reference K3s install builds behind an HTTPS-inspecting
#    middlebox: a corporate proxy or endpoint-security TLS scanning) ──
# When the host re-signs TLS with a private root CA, in-container pip can't
# verify pypi.org against the public roots and dies with
#   "unable to get local issuer certificate".
# deploy-k3s.sh exports that root CA into backend/certs/*.crt and builds
# with --build-arg INSTALL_EXTRA_CA=1, which merges it into the OS bundle.
# certs/ always exists (committed .keep). On CI the dir holds only .keep,
# INSTALL_EXTRA_CA defaults to 0, and pip uses the public roots unchanged.
ARG INSTALL_EXTRA_CA=0
COPY certs/ /usr/local/share/ca-certificates/
RUN if [ "$INSTALL_EXTRA_CA" = "1" ]; then update-ca-certificates; fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Install Python dependencies from the hashed lock (`make lock-python` writes
# it from requirements.txt): all ~220 packages, transitive ones included, at
# one reviewed version and sha256. A package the lock does not list, or a
# release file whose hash changed, fails the build instead of installing.
# requirements.txt is copied too: requirements-ragas.txt includes it.
COPY requirements.txt requirements-lock.txt ./
# The base image's pip installs the lock; an unpinned `pip install --upgrade
# pip` fetched whatever pip was newest at build time.
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt

# Optional: the Ragas RAG-faithfulness evaluator (Settings > AI Configuration).
# Off by default: it adds 26 packages (pandas, pyarrow, datasets; ~360 MB) and
# moves fsspec, jiter, openai and rich to lower versions (requirements-ragas.txt says
# why). --build-arg INSTALL_RAGAS=1 adds it. Without it, choosing Ragas runs
# the single-prompt judge and the settings page says why. Copied after the
# main install so editing it keeps that layer.
ARG INSTALL_RAGAS=0
COPY requirements-ragas.txt .
RUN if [ "$INSTALL_RAGAS" = "1" ]; then pip install --no-cache-dir -r requirements-ragas.txt; fi

# ── Development stage ────────────────────────────────────────
FROM base AS development

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=development

# uvicorn's --reload uses watchfiles, which the lock already installs.

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
# ``k3ssetup/deploy-k3s.sh``). When a build skips the staging
# step — e.g. a plain ``docker build ./backend`` — the path is
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
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS production

WORKDIR /app

# procps provides `pgrep`, used by the Celery beat liveness probe (k8s/base/worker-deployments.yaml)
# apt-get upgrade takes Debian's security fixes for packages the base image
# already carries; installing alone never upgrades them (see mcp/Dockerfile and
# scripts/test_ci_security.py::test_the_debian_runtime_images_take_security_updates).
# OS_PATCH_DAY (CI passes the UTC date) rebuilds this layer at least once a day, so
# the CI scan's layer cache cannot replay an upgrade older than Debian's newest fix.
ARG OS_PATCH_DAY=unset
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
COPY --from=base /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --from=builder /app/app ./app
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/alembic.ini .
COPY --from=builder /app/gunicorn_conf.py ./gunicorn_conf.py
COPY --from=builder /app/scripts ./scripts

# LICENSE, NOTICE and THIRD_PARTY_NOTICES.md ship with the image (Apache-2.0
# s.4(a)/(d)). backend/licenses/ is staged from the repository root by
# scripts/generate_third_party_notices.py; CI fails when it is stale.
COPY --from=builder /app/licenses /licenses
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Client SDK files served at /api/v1/sdk/{lang}. Staged into the build
# context as backend/__client_sdks_staged/ by the deploy script (see
# k3ssetup/deploy-k3s.sh). docker-compose dev path is unaffected —
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
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.14/site-packages/setuptools \
        /usr/local/lib/python3.14/site-packages/setuptools-*.dist-info \
        /usr/local/lib/python3.14/site-packages/pkg_resources \
        /usr/local/lib/python3.14/site-packages/_distutils_hack \
        /usr/local/lib/python3.14/site-packages/distutils-precedence.pth \
        /usr/local/lib/python3.14/site-packages/wheel \
        /usr/local/lib/python3.14/site-packages/wheel-*.dist-info \
        /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 /usr/local/bin/wheel \
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
