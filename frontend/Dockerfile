FROM node:20-alpine AS base
WORKDIR /app

# ── TLS-interception CA support (homelab builds behind an HTTPS-inspecting
#    middlebox: Norton/NordVPN SSL scanning, corporate proxy) ─────────────
# Without this, `npm install` can't verify registry.npmjs.org against the
# public roots and fails with "unable to get local issuer certificate".
# NODE_EXTRA_CA_CERTS *appends* the staged root CA to Node's built-in roots
# (no apk / update-ca-certificates needed on Alpine). deploy-homelab.sh drops
# the CA into frontend/certs/*.crt and builds with INSTALL_EXTRA_CA=1.
# certs/ always exists (committed .keep); empty on CI → the conditional skips
# and npm uses the public roots unchanged.
ARG INSTALL_EXTRA_CA=0
COPY certs/ /opt/extra-ca/
COPY package*.json ./
# npm install works whether or not package-lock.json is committed.
# After running locally once, commit the generated package-lock.json and
# change this line to `RUN npm ci` for faster, reproducible Docker builds.
RUN if [ "$INSTALL_EXTRA_CA" = "1" ] && ls /opt/extra-ca/*.crt >/dev/null 2>&1; then \
      export NODE_EXTRA_CA_CERTS="$(ls /opt/extra-ca/*.crt | head -n1)"; \
      echo "[frontend] trusting interception CA: $NODE_EXTRA_CA_CERTS"; \
    fi; \
    npm install --include=dev --prefer-offline || npm install --include=dev
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
# any ingress, e.g. testlookup.local in k8s). Deployments that need an absolute
# URL (gcp-vm, cloudrun) override this via --build-arg.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine AS production
# Take Alpine's security updates at build time (Trivy on PR #26): util-linux
# fixes CVE-2026-53612/78409 (2.42.3-r0) and CVE-2026-78408 (2.42.3-r1)
# shipped after the nginx:alpine tag was built. The four util-linux CVEs with
# no fix yet are in mount(8), accepted in .trivyignore.yaml.
RUN apk upgrade --no-cache
COPY --from=builder /app/dist /usr/share/nginx/html
# nginx:alpine runs envsubst on /etc/nginx/templates/*.template at container
# start, writing the result to /etc/nginx/conf.d/. This lets the same image
# work in any deploy target by setting BACKEND_UPSTREAM (default backend:8000
# for docker-compose; set to testlookup-backend:8000 in Kubernetes).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV BACKEND_UPSTREAM=backend:8000
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
