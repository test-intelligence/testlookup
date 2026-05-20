#!/usr/bin/env bash
# ============================================================================
# deploy-openshift-artifactory.sh
# ----------------------------------------------------------------------------
# Deploy the COMPLETE TestLookup stack to an air-gapped OpenShift cluster that
# can reach ONLY one Artifactory registry (no public internet), over HTTPS.
#
# What it does:
#   1. Builds the 3 app images locally (backend / frontend / mcp) and pushes
#      them to Artifactory under one immutable build tag.
#   2. Mirrors every third-party infra image (postgres, redis, mongo, minio,
#      chromadb, ollama, busybox) into the SAME Artifactory, so the cluster
#      pulls 100% of its images from that one registry.
#   3. Creates the project, an Artifactory image-pull secret, app secrets, and
#      (optionally) grants the anyuid SCC for the stock infra images.
#   4. Renders k8s/overlays/openshift-artifactory with the registry / tag /
#      storage-class / route-host substituted in, and applies it.
#   5. Exposes the app over HTTPS via OpenShift Routes (edge TLS + forced
#      http->https redirect), waits for rollout, seeds MinIO buckets, and
#      creates the initial admin user.
#
# This is the OpenShift/Artifactory sibling of homelabsetup/deploy-homelab.sh.
#
# Prereqs on the BUILD host (which DOES have internet + Artifactory access):
#   - docker, oc (or kubectl), curl, openssl
#   - oc logged in to the target cluster (``oc login ...``)
#   - openshiftsetup/artifactory.env filled in (copy from .env.example)
#
# Usage:
#   cp openshiftsetup/artifactory.env.example openshiftsetup/artifactory.env
#   # edit artifactory.env
#   ./openshiftsetup/deploy-openshift-artifactory.sh [flags]
#
# Flags:
#   --skip-build     Don't rebuild app images; reuse an existing tag
#                    (APP_TAG from config, else newest common build-* in Artifactory)
#   --skip-push      Build images but don't push (local smoke test)
#   --skip-mirror    Don't mirror infra images (already present in Artifactory)
#   --no-anyuid      Don't grant the anyuid SCC (hardened clusters)
#   --skip-admin     Don't create the initial admin user
#   --dry-run        Render the manifests (kustomize build) and exit — no apply
#   --teardown       Delete the application (PVCs preserved)
#   --teardown-all   Delete the whole namespace (DESTRUCTIVE — wipes data)
#   -h | --help      Show this help
# ============================================================================

set -euo pipefail

# ── Paths ───────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY_DIR="$REPO_ROOT/k8s/overlays/openshift-artifactory"
CONFIG_FILE="${ARTIFACTORY_ENV_FILE:-$REPO_ROOT/openshiftsetup/artifactory.env}"
CREDS_FILE="$REPO_ROOT/openshiftsetup/.openshift-credentials"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()   { echo -e "${YELLOW}[!]${NC} $1"; }
error()  { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }
header() { echo -e "\n${CYAN}══════════════════════════════════════════════════${NC}"; echo -e "${CYAN}  $1${NC}"; echo -e "${CYAN}══════════════════════════════════════════════════${NC}\n"; }

usage() { sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# ── Flags ───────────────────────────────────────────────────────────────────
SKIP_BUILD=false; SKIP_PUSH=false; SKIP_MIRROR=false
NO_ANYUID=false; SKIP_ADMIN=false; DRY_RUN=false
TEARDOWN=false; TEARDOWN_ALL=false
for arg in "$@"; do
  case "$arg" in
    --skip-build)   SKIP_BUILD=true ;;
    --skip-push)    SKIP_PUSH=true ;;
    --skip-mirror)  SKIP_MIRROR=true ;;
    --no-anyuid)    NO_ANYUID=true ;;
    --skip-admin)   SKIP_ADMIN=true ;;
    --dry-run)      DRY_RUN=true ;;
    --teardown)     TEARDOWN=true ;;
    --teardown-all) TEARDOWN_ALL=true ;;
    -h|--help)      usage ;;
    *)              error "Unknown flag: $arg (try --help)" ;;
  esac
done

# ── Load config ─────────────────────────────────────────────────────────────
if [ -f "$CONFIG_FILE" ]; then
  log "Loading config from $CONFIG_FILE"
  # shellcheck disable=SC1090
  set -a; . "$CONFIG_FILE"; set +a
else
  warn "Config file $CONFIG_FILE not found — relying on environment variables."
fi

# Defaults
NAMESPACE="${NAMESPACE:-testlookup}"
ARTIFACTORY_PULL_REGISTRY="${ARTIFACTORY_PULL_REGISTRY:-${ARTIFACTORY_REGISTRY:-}}"
ARTIFACTORY_EMAIL="${ARTIFACTORY_EMAIL:-noreply@example.com}"
GRANT_ANYUID="${GRANT_ANYUID:-true}"
STORAGE_CLASS="${STORAGE_CLASS:-}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@2026!}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
ADMIN_FULL_NAME="${ADMIN_FULL_NAME:-TestLookup Admin}"
INFRA_IMAGES="${INFRA_IMAGES:-postgres:16-alpine redis:7-alpine mongo:7 minio/minio:RELEASE.2025-09-07T16-13-09Z chromadb/chroma:0.5.20 ollama/ollama:0.5.4 busybox:1.36}"
PULL_OLLAMA_MODELS="${PULL_OLLAMA_MODELS:-false}"
OLLAMA_MODELS="${OLLAMA_MODELS:-qwen2.5:7b nomic-embed-text}"
PULL_SECRET_NAME="artifactory-pull"
APP_IMAGES=(backend frontend mcp)

# ── Pick the cluster CLI (oc preferred for Route/SCC support) ───────────────
if command -v oc >/dev/null 2>&1; then
  KCLI="oc"
elif command -v kubectl >/dev/null 2>&1; then
  KCLI="kubectl"
  warn "oc not found — falling back to kubectl. Route TLS injection + anyuid SCC need oc; those steps will be skipped/warned."
else
  error "Neither oc nor kubectl found on PATH."
fi

# ── Teardown ────────────────────────────────────────────────────────────────
if [ "$TEARDOWN_ALL" = true ]; then
  header "TEARDOWN (DESTRUCTIVE) — delete namespace $NAMESPACE + all data"
  read -rp "Type 'yes' to confirm: " c; [ "$c" = "yes" ] || { echo "Aborted."; exit 0; }
  "$KCLI" delete namespace "$NAMESPACE" --ignore-not-found
  log "Namespace deleted."; exit 0
fi
if [ "$TEARDOWN" = true ]; then
  header "TEARDOWN — delete application (PVCs preserved)"
  "$KCLI" delete -k "$OVERLAY_DIR" --ignore-not-found || true
  log "Application deleted. PVCs/data preserved."; exit 0
fi

# ── Validate required config ────────────────────────────────────────────────
[ -n "${ARTIFACTORY_REGISTRY:-}" ] || error "ARTIFACTORY_REGISTRY is required (set it in $CONFIG_FILE)."

# Resolve route hosts: explicit values win, else derive from APPS_DOMAIN.
if [ -z "${ROUTE_HOST_FRONTEND:-}" ] || [ -z "${ROUTE_HOST_API:-}" ] || [ -z "${ROUTE_HOST_MCP:-}" ]; then
  [ -n "${APPS_DOMAIN:-}" ] || error "Set APPS_DOMAIN, or all three of ROUTE_HOST_FRONTEND/API/MCP."
  ROUTE_HOST_FRONTEND="${ROUTE_HOST_FRONTEND:-testlookup.${APPS_DOMAIN}}"
  ROUTE_HOST_API="${ROUTE_HOST_API:-testlookup-api.${APPS_DOMAIN}}"
  ROUTE_HOST_MCP="${ROUTE_HOST_MCP:-testlookup-mcp.${APPS_DOMAIN}}"
fi

# ── Preflight ───────────────────────────────────────────────────────────────
header "Step 0 — Preflight"
command -v docker >/dev/null 2>&1 || error "docker is required."
command -v curl   >/dev/null 2>&1 || error "curl is required."
docker info >/dev/null 2>&1 || error "Docker daemon not reachable. Start Docker first."
"$KCLI" whoami >/dev/null 2>&1 || "$KCLI" get nodes >/dev/null 2>&1 || error "Not logged in to a cluster. Run 'oc login ...' first."
log "Cluster reachable as: $("$KCLI" whoami 2>/dev/null || echo 'unknown')"
log "Push registry:  $ARTIFACTORY_REGISTRY"
log "Pull registry:  $ARTIFACTORY_PULL_REGISTRY"
log "Routes (HTTPS): https://$ROUTE_HOST_FRONTEND  |  https://$ROUTE_HOST_API  |  https://$ROUTE_HOST_MCP/sse"

# ── docker login to Artifactory ─────────────────────────────────────────────
ARTIFACTORY_HOST="${ARTIFACTORY_REGISTRY%%/*}"   # strip repo path → host[:port]
if [ -n "${ARTIFACTORY_USERNAME:-}" ] && [ -n "${ARTIFACTORY_PASSWORD:-}" ]; then
  log "docker login $ARTIFACTORY_HOST ..."
  echo "$ARTIFACTORY_PASSWORD" | docker login "$ARTIFACTORY_HOST" -u "$ARTIFACTORY_USERNAME" --password-stdin \
    || error "docker login to $ARTIFACTORY_HOST failed."
else
  warn "ARTIFACTORY_USERNAME/PASSWORD not set — assuming docker is already logged in to $ARTIFACTORY_HOST."
fi

# ── Resolve the build tag ───────────────────────────────────────────────────
# Hits the Artifactory Docker v2 API for an existing tag when --skip-build,
# otherwise mints a fresh immutable one. Fixed-width build-YYYYMMDD-HHMMSS so
# lexical sort == chronological sort.
_artifactory_tags() {  # $1 = repo path under the registry (e.g. testlookup/backend)
  local url="https://${ARTIFACTORY_HOST}/v2/${ARTIFACTORY_REGISTRY#*/}/$1/tags/list"
  curl -sf --max-time 15 -u "${ARTIFACTORY_USERNAME:-}:${ARTIFACTORY_PASSWORD:-}" "$url" 2>/dev/null \
    | sed -e 's/.*"tags":\[//' -e 's/\].*//' -e 's/"//g' -e 's/,/\n/g' \
    | grep -E '^build-[0-9]{8}-[0-9]{6}$' || true
}

if [ "$SKIP_BUILD" = true ]; then
  if [ -n "${APP_TAG:-}" ]; then
    log "Reusing APP_TAG from config: $APP_TAG"
  else
    log "--skip-build: resolving newest common build-* tag from Artifactory..."
    APP_TAG="$(comm -12 \
      <(_artifactory_tags testlookup/backend  | sort -ru) \
      <(comm -12 <(_artifactory_tags testlookup/frontend | sort -ru) \
                 <(_artifactory_tags testlookup/mcp      | sort -ru)) | head -n1)"
    [ -n "$APP_TAG" ] || error "No common build-* tag found for all 3 app images. Set APP_TAG in config or run without --skip-build."
    log "Resolved APP_TAG=$APP_TAG"
  fi
else
  APP_TAG="build-$(date -u +%Y%m%d-%H%M%S)"
fi

# ── Step 1 — Build + push app images ────────────────────────────────────────
if [ "$SKIP_BUILD" = false ]; then
  header "Step 1 — Build app images ($APP_TAG)"
  cd "$REPO_ROOT"

  # Stage client SDKs into the backend build context (same contract as the
  # homelab deploy). Trap cleans the staging dir on any exit.
  STAGED_SDK="$REPO_ROOT/backend/__client_sdks_staged"
  rm -rf "$STAGED_SDK"; mkdir -p "$STAGED_SDK"
  trap 'rm -rf "$STAGED_SDK"' EXIT INT TERM
  SDK_EXCLUDES=( '.pytest_cache' '__pycache__' '*.pyc' '.venv' 'venv' 'node_modules'
                 '.tox' '.mypy_cache' '.ruff_cache' 'target' 'build' '*.egg-info'
                 '.coverage' 'htmlcov' '.DS_Store' )
  if command -v rsync >/dev/null 2>&1; then
    RSYNC_EXCLUDES=(); for e in "${SDK_EXCLUDES[@]}"; do RSYNC_EXCLUDES+=("--exclude=$e"); done
    rsync -a "${RSYNC_EXCLUDES[@]}" "$REPO_ROOT/client/" "$STAGED_SDK/"
  else
    TAR_EXCLUDES=(); for e in "${SDK_EXCLUDES[@]}"; do TAR_EXCLUDES+=("--exclude=$e"); done
    ( cd "$REPO_ROOT/client" && tar "${TAR_EXCLUDES[@]}" -cf - . ) | ( cd "$STAGED_SDK" && tar -xf - )
  fi

  log "Building backend..."
  docker build -t "${ARTIFACTORY_REGISTRY}/testlookup/backend:${APP_TAG}" --target production -f backend/Dockerfile backend/
  rm -rf "$STAGED_SDK"; trap - EXIT INT TERM

  log "Building frontend (same-origin relative API URLs)..."
  docker build --pull -t "${ARTIFACTORY_REGISTRY}/testlookup/frontend:${APP_TAG}" --target production -f frontend/Dockerfile frontend/

  log "Building mcp..."
  docker build -t "${ARTIFACTORY_REGISTRY}/testlookup/mcp:${APP_TAG}" -f mcp/Dockerfile mcp/

  if [ "$SKIP_PUSH" = false ]; then
    log "Pushing app images to Artifactory..."
    for img in "${APP_IMAGES[@]}"; do
      docker push "${ARTIFACTORY_REGISTRY}/testlookup/${img}:${APP_TAG}"
    done
  else
    warn "--skip-push: app images built but not pushed."
  fi
else
  log "Skipping app image build (--skip-build), using tag $APP_TAG"
fi

# ── Step 2 — Mirror infra images into the SAME Artifactory ──────────────────
if [ "$SKIP_MIRROR" = false ]; then
  header "Step 2 — Mirror infra images into Artifactory"
  warn "Pulling upstream infra images — the BUILD host needs internet for this step."
  for ref in $INFRA_IMAGES; do
    dest="${ARTIFACTORY_REGISTRY}/${ref}"
    log "Mirroring ${ref} -> ${dest}"
    docker pull "$ref"
    docker tag  "$ref" "$dest"
    if [ "$SKIP_PUSH" = false ]; then
      docker push "$dest"
    fi
  done
else
  log "Skipping infra image mirror (--skip-mirror)."
fi

# ── Step 3 — Project / namespace ────────────────────────────────────────────
header "Step 3 — Namespace"
if "$KCLI" get namespace "$NAMESPACE" >/dev/null 2>&1; then
  log "Namespace $NAMESPACE exists."
else
  if [ "$KCLI" = "oc" ]; then oc new-project "$NAMESPACE" >/dev/null; else "$KCLI" create namespace "$NAMESPACE"; fi
  log "Namespace $NAMESPACE created."
fi

# ── Step 4 — Image pull secret (cluster auths to Artifactory) ───────────────
header "Step 4 — Artifactory image-pull secret"
if [ -n "${ARTIFACTORY_USERNAME:-}" ] && [ -n "${ARTIFACTORY_PASSWORD:-}" ]; then
  "$KCLI" -n "$NAMESPACE" delete secret "$PULL_SECRET_NAME" --ignore-not-found >/dev/null 2>&1 || true
  "$KCLI" -n "$NAMESPACE" create secret docker-registry "$PULL_SECRET_NAME" \
    --docker-server="$ARTIFACTORY_HOST" \
    --docker-username="$ARTIFACTORY_USERNAME" \
    --docker-password="$ARTIFACTORY_PASSWORD" \
    --docker-email="$ARTIFACTORY_EMAIL"
  # Link to the default SA so every pod pulls with it (covers app + infra).
  if [ "$KCLI" = "oc" ]; then
    oc -n "$NAMESPACE" secrets link default "$PULL_SECRET_NAME" --for=pull
  else
    "$KCLI" -n "$NAMESPACE" patch serviceaccount default \
      -p "{\"imagePullSecrets\":[{\"name\":\"$PULL_SECRET_NAME\"}]}"
  fi
  log "Pull secret $PULL_SECRET_NAME created and linked to the default ServiceAccount."
else
  warn "No Artifactory creds — skipping pull secret. The cluster must already trust $ARTIFACTORY_HOST anonymously."
fi

# ── Step 5 — anyuid SCC (stock infra images need their own UIDs) ────────────
if [ "$NO_ANYUID" = false ] && [ "$GRANT_ANYUID" = "true" ]; then
  header "Step 5 — Grant anyuid SCC to the default ServiceAccount"
  if [ "$KCLI" = "oc" ]; then
    oc adm policy add-scc-to-user anyuid -z default -n "$NAMESPACE" \
      && log "anyuid granted to system:serviceaccount:${NAMESPACE}:default" \
      || warn "Could not grant anyuid (need cluster-admin). Infra pods may fail under restricted-v2."
  else
    warn "kubectl can't manage SCCs. Grant manually: oc adm policy add-scc-to-user anyuid -z default -n $NAMESPACE"
  fi
else
  warn "Skipping anyuid grant. Ensure the infra images tolerate restricted-v2 (random UID) or pre-provision an SCC."
fi

# ── Step 6 — App secrets (random, generated once) ───────────────────────────
header "Step 6 — Application secrets"
if "$KCLI" -n "$NAMESPACE" get secret testlookup-secrets >/dev/null 2>&1; then
  log "Secret testlookup-secrets already exists — keeping it."
else
  APP_SECRET=$(openssl rand -hex 32)
  JWT_SECRET=$(openssl rand -hex 32)
  PG_PASSWORD=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
  MINIO_ACCESS="testlookup_minio"
  MINIO_SECRET=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
  WEBHOOK_SECRET=$(openssl rand -hex 32)
  MCP_USER="mcp_service"
  MCP_PASS=$(openssl rand -base64 12 | tr -d '=/+' | head -c 16)
  DATABASE_URL="postgresql+asyncpg://testlookup_user:${PG_PASSWORD}@testlookup-postgres:5432/testlookup"
  MONGO_URI="mongodb://testlookup-mongo:27017"

  "$KCLI" -n "$NAMESPACE" create secret generic testlookup-secrets \
    --from-literal=APP_SECRET_KEY="${APP_SECRET}" \
    --from-literal=JWT_SECRET_KEY="${JWT_SECRET}" \
    --from-literal=DATABASE_URL="${DATABASE_URL}" \
    --from-literal=POSTGRES_PASSWORD="${PG_PASSWORD}" \
    --from-literal=MINIO_ACCESS_KEY="${MINIO_ACCESS}" \
    --from-literal=MINIO_SECRET_KEY="${MINIO_SECRET}" \
    --from-literal=MONGO_URI="${MONGO_URI}" \
    --from-literal=WEBHOOK_SECRET="${WEBHOOK_SECRET}" \
    --from-literal=MCP_USERNAME="${MCP_USER}" \
    --from-literal=MCP_PASSWORD="${MCP_PASS}"
  log "Secrets created."
  mkdir -p "$(dirname "$CREDS_FILE")"
  cat > "$CREDS_FILE" <<CREDS
# TestLookup OpenShift credentials — generated $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# DO NOT COMMIT
POSTGRES_PASSWORD=${PG_PASSWORD}
MINIO_ACCESS_KEY=${MINIO_ACCESS}
MINIO_SECRET_KEY=${MINIO_SECRET}
MCP_USERNAME=${MCP_USER}
MCP_PASSWORD=${MCP_PASS}
CREDS
  log "Credentials saved to $CREDS_FILE (gitignored)."
fi

# ── Step 7 — Resolve storage class ──────────────────────────────────────────
if [ -z "$STORAGE_CLASS" ]; then
  STORAGE_CLASS="$("$KCLI" get storageclass -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{"\n"}{end}' 2>/dev/null | head -n1)"
  [ -n "$STORAGE_CLASS" ] || error "No default StorageClass found and STORAGE_CLASS not set. Set STORAGE_CLASS in $CONFIG_FILE."
  log "Auto-detected default StorageClass: $STORAGE_CLASS"
else
  log "Using StorageClass: $STORAGE_CLASS"
fi

# ── Step 8 — Substitute placeholders (in place, restored by trap) ───────────
header "Step 8 — Render overlay"
SUBST_FILES=( kustomization.yaml route.yaml infra-postgres.yaml infra-mongo.yaml infra-minio.yaml infra-chromadb.yaml )
restore_overlay() {
  for f in "${SUBST_FILES[@]}"; do
    [ -f "$OVERLAY_DIR/$f.deploy-bak" ] && mv -f "$OVERLAY_DIR/$f.deploy-bak" "$OVERLAY_DIR/$f"
  done
}
for f in "${SUBST_FILES[@]}"; do cp "$OVERLAY_DIR/$f" "$OVERLAY_DIR/$f.deploy-bak"; done
trap restore_overlay EXIT INT TERM

# '|' delimiter — the registry contains '/'.
for f in "${SUBST_FILES[@]}"; do
  sed -i.tmp \
    -e "s|ARTIFACTORY_REGISTRY_PLACEHOLDER|${ARTIFACTORY_PULL_REGISTRY}|g" \
    -e "s|APP_TAG_PLACEHOLDER|${APP_TAG}|g" \
    -e "s|STORAGE_CLASS_PLACEHOLDER|${STORAGE_CLASS}|g" \
    -e "s|ROUTE_HOST_FRONTEND_PLACEHOLDER|${ROUTE_HOST_FRONTEND}|g" \
    -e "s|ROUTE_HOST_API_PLACEHOLDER|${ROUTE_HOST_API}|g" \
    -e "s|ROUTE_HOST_MCP_PLACEHOLDER|${ROUTE_HOST_MCP}|g" \
    "$OVERLAY_DIR/$f"
  rm -f "$OVERLAY_DIR/$f.tmp"
done

if grep -rqE "ARTIFACTORY_REGISTRY_PLACEHOLDER|APP_TAG_PLACEHOLDER|STORAGE_CLASS_PLACEHOLDER|ROUTE_HOST_.*_PLACEHOLDER" "$OVERLAY_DIR"/*.yaml; then
  error "Unsubstituted placeholder remains in the overlay — aborting before apply."
fi

if [ "$DRY_RUN" = true ]; then
  header "DRY RUN — rendered manifests"
  "$KCLI" kustomize "$OVERLAY_DIR" 2>/dev/null || "$KCLI" apply -k "$OVERLAY_DIR" --dry-run=client -o yaml
  restore_overlay; trap - EXIT INT TERM
  log "Dry run complete — nothing applied."
  exit 0
fi

# ── Step 9 — Apply ──────────────────────────────────────────────────────────
header "Step 9 — Apply to cluster"
"$KCLI" apply -k "$OVERLAY_DIR"
log "Manifests applied."
restore_overlay; trap - EXIT INT TERM

# ── Step 10 — Custom Route TLS cert (optional) ──────────────────────────────
if [ -n "${ROUTE_TLS_CERT:-}" ] && [ -n "${ROUTE_TLS_KEY:-}" ]; then
  header "Step 10 — Inject custom TLS cert into Routes"
  if [ "$KCLI" = "oc" ]; then
    [ -f "$ROUTE_TLS_CERT" ] || error "ROUTE_TLS_CERT not found: $ROUTE_TLS_CERT"
    [ -f "$ROUTE_TLS_KEY" ]  || error "ROUTE_TLS_KEY not found: $ROUTE_TLS_KEY"
    # Build a JSON merge patch with python so multiline PEM is escaped safely.
    PATCH_JSON="$(CERT="$ROUTE_TLS_CERT" KEY="$ROUTE_TLS_KEY" CA="${ROUTE_TLS_CACERT:-}" python3 - <<'PY'
import json, os
tls = {"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect",
       "certificate": open(os.environ["CERT"]).read(),
       "key": open(os.environ["KEY"]).read()}
ca = os.environ.get("CA")
if ca:
    tls["caCertificate"] = open(ca).read()
print(json.dumps({"spec": {"tls": tls}}))
PY
)"
    for r in testlookup testlookup-api testlookup-mcp; do
      oc -n "$NAMESPACE" patch route "$r" --type=merge -p "$PATCH_JSON" \
        && log "Patched cert into route/$r" || warn "Failed to patch cert into route/$r"
    done
  else
    warn "Custom Route TLS requires oc. Skipping cert injection."
  fi
else
  log "Using the OpenShift router's default wildcard cert for HTTPS (no custom cert configured)."
fi

# ── Step 11 — Wait for rollout ──────────────────────────────────────────────
header "Step 11 — Wait for infra + backend"
for app in postgres mongo redis minio chromadb; do
  "$KCLI" -n "$NAMESPACE" rollout status deploy/testlookup-$app --timeout=180s \
    || warn "testlookup-$app not ready yet."
done
"$KCLI" -n "$NAMESPACE" rollout status deploy/testlookup-backend --timeout=300s \
  || warn "Backend not ready (it runs DB migrations on start). Check logs."

# ── Step 12 — MinIO buckets ─────────────────────────────────────────────────
header "Step 12 — MinIO buckets"
MINIO_POD="$("$KCLI" -n "$NAMESPACE" get pod -l app=testlookup-minio -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")"
if [ -n "$MINIO_POD" ]; then
  MA="$("$KCLI" -n "$NAMESPACE" get secret testlookup-secrets -o jsonpath='{.data.MINIO_ACCESS_KEY}' | base64 -d 2>/dev/null || echo "")"
  MS="$("$KCLI" -n "$NAMESPACE" get secret testlookup-secrets -o jsonpath='{.data.MINIO_SECRET_KEY}' | base64 -d 2>/dev/null || echo "")"
  # The minio server image ships the `mc` client. Configure an alias against
  # the pod's own endpoint and create the buckets in-cluster (no internet).
  "$KCLI" -n "$NAMESPACE" exec "$MINIO_POD" -- sh -c "
    mc alias set local http://localhost:9000 '$MA' '$MS' >/dev/null 2>&1 &&
    mc mb --ignore-existing local/test-telemetry &&
    mc mb --ignore-existing local/knowledge-docs
  " && log "Buckets ready: test-telemetry, knowledge-docs" \
    || warn "Could not create buckets via mc. The backend will attempt creation on first use."
else
  warn "MinIO pod not found yet — buckets will be created by the backend on first use."
fi

# ── Step 13 — Admin user ────────────────────────────────────────────────────
if [ "$SKIP_ADMIN" = false ]; then
  header "Step 13 — Initial admin user"
  BACKEND_POD="$("$KCLI" -n "$NAMESPACE" get pod -l app=testlookup-backend -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")"
  if [ -n "$BACKEND_POD" ]; then
    "$KCLI" -n "$NAMESPACE" exec -i "$BACKEND_POD" -- env \
        ADMIN_USERNAME="$ADMIN_USERNAME" ADMIN_PASSWORD="$ADMIN_PASSWORD" \
        ADMIN_EMAIL="$ADMIN_EMAIL" ADMIN_FULL_NAME="$ADMIN_FULL_NAME" \
        python < "$REPO_ROOT/scripts/createAdmin.py" \
      && log "Admin user ensured ($ADMIN_USERNAME)" \
      || warn "createAdmin.py failed — run it manually once the backend is healthy."
  else
    warn "Backend pod not found — create the admin user later with scripts/createAdmin.py."
  fi
fi

# ── Step 14 — Ollama models (optional, needs a mirror) ──────────────────────
if [ "$PULL_OLLAMA_MODELS" = "true" ]; then
  header "Step 14 — Ollama models"
  OLLAMA_POD="$("$KCLI" -n "$NAMESPACE" get pod -l app=testlookup-ollama -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")"
  if [ -n "$OLLAMA_POD" ]; then
    for m in $OLLAMA_MODELS; do
      log "Pulling Ollama model $m (requires Ollama to reach a mirror)..."
      "$KCLI" -n "$NAMESPACE" exec "$OLLAMA_POD" -- ollama pull "$m" || warn "Could not pull $m (air-gapped Ollama?)."
    done
  else
    warn "Ollama pod not found."
  fi
fi

# ── Summary ─────────────────────────────────────────────────────────────────
header "Deployment Complete"
echo -e "${GREEN}TestLookup deployed to OpenShift namespace '$NAMESPACE' (images from $ARTIFACTORY_PULL_REGISTRY).${NC}"
echo ""
echo "  Dashboard:  https://${ROUTE_HOST_FRONTEND}"
echo "  API:        https://${ROUTE_HOST_API}/docs"
echo "  MCP (SSE):  https://${ROUTE_HOST_MCP}/sse"
echo "  Admin:      ${ADMIN_USERNAME} / (see ${CREDS_FILE} or your config)"
echo ""
echo "  Routes:   $KCLI -n $NAMESPACE get route"
echo "  Pods:     $KCLI -n $NAMESPACE get pods"
echo "  Re-deploy after a code change:"
echo "    $0            # fresh build tag, mirror skip-able with --skip-mirror"
echo ""
