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

# Copy server source
COPY . .

# Run as non-root user (UID 1000 matches the k8s securityContext)
RUN adduser --disabled-password --gecos '' --uid 1000 mcp
USER mcp

EXPOSE 8002

# SSE transport by default when containerised
CMD ["python", "server.py", "--transport", "sse", "--port", "8002"]
