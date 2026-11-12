# Pinned by digest; Dependabot's docker updates for /mcp move it.
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

WORKDIR /app

# Don't write .pyc files — the k8s pod runs with readOnlyRootFilesystem: true,
# so any bytecode write to /app would fail.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── TLS-interception CA support (homelab builds behind an HTTPS-inspecting
#    middlebox: a corporate proxy or endpoint-security TLS scanning) ──
# ca-certificates supplies the OS trust bundle + update-ca-certificates so a
# staged interception root CA can be merged, letting pip reach pypi.org
# through the middlebox. deploy-homelab.sh drops the CA into mcp/certs/*.crt
# and builds with INSTALL_EXTRA_CA=1. certs/ always exists (committed .keep);
# empty on CI → no-op and pip uses the public roots unchanged.
#
# apt-get upgrade takes Debian's security fixes for packages the base image
# already carries. Installing ca-certificates alone never upgrades them, so a
# fix Debian has published (gzip, pcre2, sqlite, perl-base: 12 HIGH/CRITICAL on
# PR #56) stays out of the image until python:3.14-slim is rebuilt upstream.
# The Debian sources are plain http:// with signed Release files, so unlike the
# frontend's apk step this does not need the staged interception CA first.
# OS_PATCH_DAY (CI passes the UTC date) rebuilds this layer at least once a day:
# the CI image scan builds from the GitHub Actions layer cache, which otherwise
# replays an upgrade from before Debian's newest fix (PR #342: zlib CVE-2026-85091,
# fixed in deb13u1, arrived through a CACHED upgrade layer).
ARG OS_PATCH_DAY=unset
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG INSTALL_EXTRA_CA=0
COPY certs/ /usr/local/share/ca-certificates/
RUN if [ "$INSTALL_EXTRA_CA" = "1" ]; then update-ca-certificates; fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Install dependencies from the hashed lock (`make lock-python` writes it from
# requirements.txt, whose ranges used to float per build): every package at
# one reviewed version and sha256, or the build fails.
COPY requirements-lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt

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

# Copy server source
COPY . .

# LICENSE, NOTICE and THIRD_PARTY_NOTICES.md ship with the image (Apache-2.0
# s.4(a)/(d)). mcp/licenses/ is staged from the repository root by
# scripts/generate_third_party_notices.py; CI fails when it is stale.
COPY licenses/ /licenses/
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Run as non-root user (UID 1000 matches the k8s securityContext)
RUN adduser --disabled-password --gecos '' --uid 1000 mcp
USER mcp

EXPOSE 8002

# SSE transport by default when containerised
CMD ["python", "server.py", "--transport", "sse", "--port", "8002"]
