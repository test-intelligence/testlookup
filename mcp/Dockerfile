FROM python:3.11-slim

WORKDIR /app

# Don't write .pyc files — the k8s pod runs with readOnlyRootFilesystem: true,
# so any bytecode write to /app would fail.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── TLS-interception CA support (homelab builds behind an HTTPS-inspecting
#    middlebox: Norton/NordVPN SSL scanning, corporate proxy) ─────────────
# ca-certificates supplies the OS trust bundle + update-ca-certificates so a
# staged interception root CA can be merged, letting pip reach pypi.org
# through the middlebox. deploy-homelab.sh drops the CA into mcp/certs/*.crt
# and builds with INSTALL_EXTRA_CA=1. certs/ always exists (committed .keep);
# empty on CI → no-op and pip uses the public roots unchanged.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG INSTALL_EXTRA_CA=0
COPY certs/ /usr/local/share/ca-certificates/
RUN if [ "$INSTALL_EXTRA_CA" = "1" ]; then update-ca-certificates; fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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

# Copy server source
COPY . .

# Run as non-root user (UID 1000 matches the k8s securityContext)
RUN adduser --disabled-password --gecos '' --uid 1000 mcp
USER mcp

EXPOSE 8002

# SSE transport by default when containerised
CMD ["python", "server.py", "--transport", "sse", "--port", "8002"]
