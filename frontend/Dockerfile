# Pinned by digest; Dependabot's docker updates for /frontend move it.
FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS base
WORKDIR /app

# ── TLS-interception CA support (reference K3s install builds behind an HTTPS-inspecting
#    middlebox: a corporate proxy or endpoint-security TLS scanning) ──
# Without this, `npm ci` can't verify registry.npmjs.org against the
# public roots and fails with "unable to get local issuer certificate".
# NODE_EXTRA_CA_CERTS *appends* the staged root CA to Node's built-in roots
# (no apk / update-ca-certificates needed on Alpine). deploy-k3s.sh drops
# the CA into frontend/certs/*.crt and builds with INSTALL_EXTRA_CA=1.
# certs/ always exists (committed .keep); empty on CI → the conditional skips
# and npm uses the public roots unchanged.
ARG INSTALL_EXTRA_CA=0
COPY certs/ /opt/extra-ca/
COPY package*.json ./
# npm ci installs exactly what package-lock.json records, including the
# platform-specific optional binaries (rolldown's linux-musl binding). Plain
# `npm install` can skip those (npm/cli#4828) and break `vite build`.
RUN if [ "$INSTALL_EXTRA_CA" = "1" ] && ls /opt/extra-ca/*.crt >/dev/null 2>&1; then \
      export NODE_EXTRA_CA_CERTS="$(ls /opt/extra-ca/*.crt | head -n1)"; \
      echo "[frontend] trusting interception CA: $NODE_EXTRA_CA_CERTS"; \
    fi; \
    npm ci --include=dev
RUN test -x node_modules/.bin/tsc && test -x node_modules/.bin/vite

# Development stage
FROM base AS development
ENV NODE_ENV=development
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# Build stage — Vite handles TypeScript compilation internally via esbuild
FROM base AS builder
# Default to empty so the bundle uses same-origin relative URLs (works behind
# any ingress, e.g. testlookup.localhost on K3s). Deployments that need an absolute
# URL (gcp-vm, cloudrun) override this via --build-arg.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
COPY . .
RUN npm run build

# Production stage
# Pinned by version and digest (it was the floating nginx:alpine, so every
# build could start from a different nginx). This is the image nginx:alpine
# pointed at on 2026-11-12 (mainline 1.31.8 on Alpine 3.24). Dependabot's
# docker updates for /frontend move the tag and digest.
FROM nginx:1.31.8-alpine3.24@sha256:713fc2ebfd92e8217db451477182ee91944b123d2b1ee9fd5a8c6e91ca6c3a11 AS production
# Take Alpine's security updates at build time (Trivy on PR #26). Alpine 3.24
# ships util-linux 2.42.3-r1, which fixes the seven util-linux CVEs that
# .trivyignore.yaml used to accept (secdb: CVE-2026-53612/53613/53614/76642/
# 78409/78410 in 2.42.3-r0, CVE-2026-78408 in 2.42.3-r1).
#
# apk reaches the mirror over HTTPS, so behind a TLS-intercepting middlebox it
# needs the staged interception CA exactly like the node stage above
# (R-B45-T-2): deploy-k3s.sh stages it in certs/*.crt with
# INSTALL_EXTRA_CA=1. It is appended to the system bundle apk verifies
# against, BEFORE apk touches the network. Empty certs/ (CI) = no-op.
ARG INSTALL_EXTRA_CA=0
COPY certs/ /opt/extra-ca/
# OS_PATCH_DAY (CI passes the UTC date) rebuilds this layer at least once a day, so
# the CI scan's layer cache cannot replay an upgrade older than Alpine's newest fix.
ARG OS_PATCH_DAY=unset
RUN if [ "$INSTALL_EXTRA_CA" = "1" ] && ls /opt/extra-ca/*.crt >/dev/null 2>&1; then \
      cat /opt/extra-ca/*.crt >> /etc/ssl/certs/ca-certificates.crt; \
      echo "[frontend] trusting interception CA for apk"; \
    fi; \
    apk upgrade --no-cache
COPY --from=builder /app/dist /usr/share/nginx/html
# LICENSE, NOTICE and the third-party notices travel with the image (Apache-2.0
# s.4(a)/(d) and the notice conditions of the bundled JavaScript). dist/ also
# serves them at /LICENSE, /NOTICE and /THIRD_PARTY_LICENSES.txt, written by
# scripts/generate-third-party-licenses.mjs at the end of `npm run build`;
# licenses/ is staged by scripts/generate_third_party_notices.py (repo root).
COPY --from=builder /app/licenses /licenses
LABEL org.opencontainers.image.licenses="Apache-2.0"
# nginx:alpine runs envsubst on /etc/nginx/templates/*.template at container
# start, writing the result to /etc/nginx/conf.d/. This lets the same image
# work in any deploy target by setting BACKEND_UPSTREAM (default backend:8000
# for docker-compose; set to testlookup-backend:8000 in Kubernetes).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV BACKEND_UPSTREAM=backend:8000
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
