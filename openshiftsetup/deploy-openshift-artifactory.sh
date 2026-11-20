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
#   2. Mirrors every third-party infra image (postgres, valkey, mongo,
#      versitygw, chromadb, ollama, busybox — the list comes from
#      deploy/images.manifest.txt) into the SAME Artifactory, so the cluster
#      pulls 100% of its images from that one registry. MongoDB is SSPL-1.0:
#      it is pulled from its upstream registry here, on your host.
#   3. Creates the project, an Artifactory image-pull secret, app secrets
#      (with a generated Redis password; MongoDB authentication is opt-in,
#      ENABLE_MONGO_AUTH), and grants two SCCs: anyuid to the one
#      ServiceAccount of the stock infra images (optional), nonroot-v2 to the
#      application's accounts.
#   4. Renders k8s/overlays/openshift-artifactory with the registry / tag /
#      storage-class / route-host substituted in, and applies it.
#   5. Exposes the app over HTTPS via OpenShift Routes (edge TLS + forced
#      http->https redirect), waits for rollout, creates the S3 buckets
#      (scripts/s3_setup.py in the backend pod), and creates the initial admin
#      user — with a generated password unless ADMIN_PASSWORD is set; a
#      generated one is printed ONCE in the final summary.
#
# This is the OpenShift/Artifactory sibling of k3ssetup/deploy-k3s.sh.
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
#   --no-anyuid      Don't grant the anyuid SCC to the data stores' account
#                    (hardened clusters that pre-provision an SCC)
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

usage() { sed -n '2,53p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

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
GRANT_NONROOT="${GRANT_NONROOT:-true}"
REVOKE_DEFAULT_ANYUID="${REVOKE_DEFAULT_ANYUID:-false}"
# MongoDB authentication is opt-in: see ensure_mongo_credentials below and the
# procedure in artifactory.env.example. Redis always gets a password.
ENABLE_MONGO_AUTH="${ENABLE_MONGO_AUTH:-false}"
MONGO_AUTH_JUST_ENABLED=false
STORAGE_CLASS="${STORAGE_CLASS:-}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
# No default password, ever: when ADMIN_PASSWORD is unset, Step 13 generates a
# random one (same recipe as the other secrets) and the final summary prints it
# ONCE — and only if that run actually created the admin.
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
ADMIN_PASSWORD_GENERATED=false
ADMIN_CREATED=false
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
ADMIN_FULL_NAME="${ADMIN_FULL_NAME:-TestLookup Admin}"
# Derived from deploy/images.manifest.txt — see mirror-images.sh for the
# rationale (a hardcoded copy here is how the list drifted from k8s before).
# `external` rows (MongoDB, SSPL-1.0) are mirrored too: this host pulls them
# from upstream; only the offline bundle leaves them out.
# shellcheck source=../scripts/release/image-manifest.sh
. "$REPO_ROOT/scripts/release/image-manifest.sh"
INFRA_IMAGES="${INFRA_IMAGES:-$(manifest_refs k8s infra core,llm,external | tr '\n' ' ')}"
PULL_OLLAMA_MODELS="${PULL_OLLAMA_MODELS:-false}"
OLLAMA_MODELS="${OLLAMA_MODELS:-qwen2.5:7b-instruct nomic-embed-text:v1.5}"
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

# ── Secret handling ─────────────────────────────────────────────────────────
# Every process's arguments are readable by other local users while it runs,
# so a secret value is never an argument of oc/kubectl/curl here: a Secret is
# sent as a manifest on stdin, keys are added from an owner-only temporary
# file, and passwords are piped. `printf` is a shell builtin (no process).
# The block between the two markers is sourced on its own by the tests
# (backend/tests/regression/openshift_deploy_harness.py): keep it to function
# definitions that use only $KCLI, $NAMESPACE, $CREDS_FILE and log/warn/error.
# >>> secret helpers
b64() { printf '%s' "$1" | base64 | tr -d '\r\n'; }

# Escape a value for a JSON string (also the quoting curl's -K file uses).
json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

# Same recipe as the PostgreSQL and object-store passwords: letters and digits
# only, so the value needs no escaping inside a connection URL.
generate_password() { openssl rand -base64 16 | tr -d '=/+' | head -c 24; }

# create_secret NAME TYPE KEY VALUE [KEY VALUE ...] — manifest on stdin.
create_secret() {
  local name="$1" type="$2"; shift 2
  {
    printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: %s\ntype: %s\ndata:\n' "$name" "$type"
    while [ "$#" -ge 2 ]; do
      printf '  "%s": %s\n' "$1" "$(b64 "$2")"; shift 2
    done
  } | "$KCLI" -n "$NAMESPACE" create -f -
}

# merge_secret_keys NAME KEY VALUE [KEY VALUE ...] — adds or replaces keys of
# an existing Secret and leaves its other keys alone.
merge_secret_keys() {
  local name="$1" patch sep="" status=0; shift
  patch="$(umask 077; mktemp)"
  {
    printf '{"data":{'
    while [ "$#" -ge 2 ]; do
      printf '%s"%s":"%s"' "$sep" "$1" "$(b64 "$2")"; sep=","; shift 2
    done
    printf '}}\n'
  } > "$patch"
  "$KCLI" -n "$NAMESPACE" patch secret "$name" --type=merge --patch-file "$patch" || status=$?
  rm -f "$patch"
  return "$status"
}

# The value of one key of testlookup-secrets ('' when the key or the Secret is absent).
secret_value() {
  "$KCLI" -n "$NAMESPACE" get secret testlookup-secrets -o "jsonpath={.data.$1}" 2>/dev/null \
    | base64 -d 2>/dev/null || true
}

# dockerconfig_json HOST USERNAME PASSWORD EMAIL — the body of an image-pull Secret.
dockerconfig_json() {
  printf '{"auths":{"%s":{"username":"%s","password":"%s","email":"%s","auth":"%s"}}}' \
    "$(json_escape "$1")" "$(json_escape "$2")" "$(json_escape "$3")" "$(json_escape "$4")" \
    "$(b64 "$2:$3")"
}

# save_credential KEY VALUE — one line of the owner-only credentials file; an
# earlier line for the same key is replaced.
save_credential() {
  local kept
  mkdir -p "${CREDS_FILE%/*}"
  ( umask 077; touch "$CREDS_FILE" )
  chmod 600 "$CREDS_FILE"
  kept="$(grep -v "^$1=" "$CREDS_FILE" || true)"
  {
    [ -z "$kept" ] || printf '%s\n' "$kept"
    printf '%s=%s\n' "$1" "$2"
  } > "$CREDS_FILE"
}

# The bundled Redis (Valkey) requires a password. It is generated once and kept
# in testlookup-secrets with the three URLs that carry it; the overlay points
# Valkey (--requirepass) and every client at those keys. An install made before
# the password existed gets it on its next deploy; later runs reuse it.
ensure_redis_credentials() {
  local password authority
  if [ -n "$(secret_value REDIS_PASSWORD)" ]; then
    log "Redis password already in testlookup-secrets — keeping it."
    return 0
  fi
  password="$(generate_password)"
  authority=":${password}@testlookup-redis:6379"
  merge_secret_keys testlookup-secrets \
    REDIS_PASSWORD "$password" \
    REDIS_URL "redis://${authority}/0" \
    CELERY_BROKER_URL "redis://${authority}/0" \
    CELERY_RESULT_BACKEND "redis://${authority}/1"
  save_credential REDIS_PASSWORD "$password"
  log "Redis password generated and added to testlookup-secrets (also in $CREDS_FILE)."
}

# MongoDB authentication: OFF unless ENABLE_MONGO_AUTH=true, and once it is on
# it stays on (the credentials in the Secret are reused).
#
# The mongo image turns authentication on as soon as it is given a root user,
# but it creates that user only on an EMPTY data directory. So on an install
# that already has data, the user is created in the running, still open server
# FIRST; only after the server confirmed it do the credentials go into the
# Secret. If the user cannot be created, nothing is changed and the script
# stops: a Secret with credentials for a user that does not exist would lock
# the application out of MongoDB.
# Sets MONGO_AUTH_JUST_ENABLED=true when this run switched it on.
ensure_mongo_credentials() {
  local user="testlookup_root" password authority reply
  MONGO_AUTH_JUST_ENABLED=false
  if [ -n "$(secret_value MONGO_ROOT_PASSWORD)" ]; then
    log "MongoDB authentication is on — keeping its credentials."
    return 0
  fi
  if [ "${ENABLE_MONGO_AUTH:-false}" != "true" ]; then
    warn "MongoDB runs WITHOUT authentication (a NetworkPolicy is its only protection). Set ENABLE_MONGO_AUTH=\"true\" to switch it on — see openshiftsetup/artifactory.env.example."
    return 0
  fi
  password="$(generate_password)"
  if "$KCLI" -n "$NAMESPACE" get deployment testlookup-mongo >/dev/null 2>&1; then
    log "Creating the MongoDB root user in the running server before authentication is switched on..."
    # The statement is piped to mongosh inside the pod; it prints one marker.
    reply="$(printf '%s\n' "try { const a = db.getSiblingDB(\"admin\"); if (a.getUser(\"${user}\") === null) { a.createUser({user: \"${user}\", pwd: \"${password}\", roles: [{role: \"root\", db: \"admin\"}]}); } else { a.updateUser(\"${user}\", {pwd: \"${password}\"}); } print(\"TESTLOOKUP_ROOT_USER_READY\"); } catch (e) { print(\"TESTLOOKUP_ROOT_USER_FAILED\"); }" \
      | "$KCLI" -n "$NAMESPACE" exec -i deployment/testlookup-mongo -- mongosh --quiet 2>&1 || true)"
    case "$reply" in
      *TESTLOOKUP_ROOT_USER_READY*) log "MongoDB root user '${user}' is ready." ;;
      *) error "MongoDB did not confirm the root user, so authentication was NOT switched on and nothing was changed. Check that the testlookup-mongo pod is Running, then run again." ;;
    esac
  elif "$KCLI" -n "$NAMESPACE" get pvc mongo-data >/dev/null 2>&1; then
    error "ENABLE_MONGO_AUTH=true, but MongoDB has a data volume (mongo-data) and no running server to create the root user in. Deploy once with ENABLE_MONGO_AUTH=\"false\" so MongoDB is running, then run again with \"true\"."
  fi
  authority="${user}:${password}@testlookup-mongo:27017"
  merge_secret_keys testlookup-secrets \
    MONGO_ROOT_USERNAME "$user" \
    MONGO_ROOT_PASSWORD "$password" \
    MONGO_URI "mongodb://${authority}/?authSource=admin"
  save_credential MONGO_ROOT_USERNAME "$user"
  save_credential MONGO_ROOT_PASSWORD "$password"
  MONGO_AUTH_JUST_ENABLED=true
  log "MongoDB root credentials added to testlookup-secrets (also in $CREDS_FILE)."
}

# Security context constraints. `anyuid` (any UID, root included) goes to ONE
# ServiceAccount, used only by the stock data-store images and Ollama. The
# application's own pods state a fixed non-zero UID, which `nonroot-v2` admits
# and `restricted-v2` does not: that one goes to testlookup-sa (API, workers)
# and to `default` (frontend, MCP, backup jobs, and the migration Job, which
# names no account). `default` is never given anyuid.
# ANYUID_SA must match k8s/overlays/openshift-artifactory/serviceaccount-anyuid.yaml.
ANYUID_SA="testlookup-anyuid"
grant_scc() {  # SCC SERVICEACCOUNT
  "$KCLI" adm policy add-scc-to-user "$1" -z "$2" -n "$NAMESPACE" \
    && log "$1 granted to system:serviceaccount:${NAMESPACE}:$2" \
    || warn "Could not grant $1 to $2 (needs cluster-admin). Its pods may be refused under restricted-v2."
}
grant_sccs() {
  local account
  if [ "$GRANT_NONROOT" = "true" ]; then
    for account in default testlookup-sa; do grant_scc nonroot-v2 "$account"; done
  else
    warn "Skipping the nonroot-v2 grant. The application pods run as UID 1000 / 101: pre-provision an SCC that admits them."
  fi
  if [ "$NO_ANYUID" = false ] && [ "$GRANT_ANYUID" = "true" ]; then
    grant_scc anyuid "$ANYUID_SA"
  else
    warn "Skipping the anyuid grant. Ensure the infra images tolerate restricted-v2 (random UID) or pre-provision an SCC for the $ANYUID_SA ServiceAccount."
  fi
}
# <<< secret helpers

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
  # The credentials go to curl as a config file on stdin (-K -), not as -u.
  printf 'user = "%s"\n' "$(json_escape "${ARTIFACTORY_USERNAME:-}:${ARTIFACTORY_PASSWORD:-}")" \
    | curl -sf --max-time 15 -K - "$url" 2>/dev/null \
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
  # reference K3s install deploy). Trap cleans the staging dir on any exit.
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
  create_secret "$PULL_SECRET_NAME" kubernetes.io/dockerconfigjson .dockerconfigjson \
    "$(dockerconfig_json "$ARTIFACTORY_HOST" "$ARTIFACTORY_USERNAME" "$ARTIFACTORY_PASSWORD" "$ARTIFACTORY_EMAIL")"
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

# ── Step 5 — SCCs: anyuid for the data stores' account, nonroot-v2 for the app ─
header "Step 5 — Security context constraints"
if [ "$KCLI" = "oc" ]; then
  grant_sccs
  # An install made by an older version of this script gave anyuid to the
  # `default` ServiceAccount, where every pod without an account of its own
  # could use it. Nothing needs it there any more.
  if oc -n "$NAMESPACE" get rolebinding system:openshift:scc:anyuid \
       -o jsonpath='{range .subjects[*]}{.name}{"\n"}{end}' 2>/dev/null | grep -qx default; then
    if [ "$REVOKE_DEFAULT_ANYUID" = "true" ]; then
      log "The default ServiceAccount still has anyuid from an older install — it is removed after the rollout (REVOKE_DEFAULT_ANYUID=true)."
    else
      warn "The default ServiceAccount still has the anyuid SCC from an older install. Once this deploy is healthy, remove it: oc adm policy remove-scc-from-user anyuid -z default -n $NAMESPACE   (or run once with REVOKE_DEFAULT_ANYUID=true)"
    fi
  fi
else
  warn "kubectl can't manage SCCs. Grant manually: oc adm policy add-scc-to-user anyuid -z $ANYUID_SA -n $NAMESPACE ; oc adm policy add-scc-to-user nonroot-v2 -z default -n $NAMESPACE ; oc adm policy add-scc-to-user nonroot-v2 -z testlookup-sa -n $NAMESPACE"
fi

# ── Step 6 — App secrets (random, generated once) ───────────────────────────
header "Step 6 — Application secrets"
LEGACY_MCP_USER=$("$KCLI" -n "$NAMESPACE" get secret testlookup-secrets \
  -o jsonpath='{.data.MCP_USERNAME}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
if "$KCLI" -n "$NAMESPACE" get secret testlookup-secrets >/dev/null 2>&1; then
  log "Secret testlookup-secrets already exists — keeping it."
else
  APP_SECRET=$(openssl rand -hex 32)
  JWT_SECRET=$(openssl rand -hex 32)
  PG_PASSWORD=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
  MINIO_ACCESS="testlookup_minio"
  MINIO_SECRET=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
  WEBHOOK_SECRET=$(openssl rand -hex 32)
  DATABASE_URL="postgresql+asyncpg://testlookup_user:${PG_PASSWORD}@testlookup-postgres:5432/testlookup"
  MONGO_URI="mongodb://testlookup-mongo:27017"

  create_secret testlookup-secrets Opaque \
    APP_SECRET_KEY "${APP_SECRET}" \
    JWT_SECRET_KEY "${JWT_SECRET}" \
    DATABASE_URL "${DATABASE_URL}" \
    POSTGRES_PASSWORD "${PG_PASSWORD}" \
    MINIO_ACCESS_KEY "${MINIO_ACCESS}" \
    MINIO_SECRET_KEY "${MINIO_SECRET}" \
    MONGO_URI "${MONGO_URI}" \
    WEBHOOK_SECRET "${WEBHOOK_SECRET}"
  log "Secrets created."
  mkdir -p "$(dirname "$CREDS_FILE")"
  # Owner-only before any secret is written: create it under umask 077, and
  # set the mode again for a file an earlier run left with the default umask.
  ( umask 077; : > "$CREDS_FILE" )
  chmod 600 "$CREDS_FILE"
  cat > "$CREDS_FILE" <<CREDS
# TestLookup OpenShift credentials — generated $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# DO NOT COMMIT
POSTGRES_PASSWORD=${PG_PASSWORD}
MINIO_ACCESS_KEY=${MINIO_ACCESS}
MINIO_SECRET_KEY=${MINIO_SECRET}
CREDS
  log "Credentials saved to $CREDS_FILE (owner-only, mode 600; ignored by git)."
fi

# MongoDB credentials (opt-in) are settled here, early: when the root user
# cannot be created the script stops before anything else has changed. Pods
# that restart between here and the apply keep working, because the user
# already exists in the still open server. Not on --dry-run, which must change
# nothing. (The Redis password is added in Step 9, right before the apply.)
if [ "$DRY_RUN" = false ]; then
  ensure_mongo_credentials
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
MIGRATION_IMAGE="${ARTIFACTORY_PULL_REGISTRY}/testlookup/backend:${APP_TAG}"
HAD_BACKEND=0
if "$KCLI" -n "$NAMESPACE" get deployment testlookup-backend >/dev/null 2>&1; then
  HAD_BACKEND=1
  log "Migrating with the candidate image before the application rollout..."
  KCLI="$KCLI" bash "$REPO_ROOT/scripts/run-k8s-migrations.sh" "$NAMESPACE" "$MIGRATION_IMAGE"
fi
KCLI="$KCLI" bash "$REPO_ROOT/scripts/prepare-live-fanout-cutover.sh" "$NAMESPACE"
# The Redis password goes into the Secret right before the manifests that make
# Valkey require it. Every pod also loads the Secret with envFrom, so a pod of
# the OLD release that restarts after this line already sends the password; on
# an upgrade the still password-less Valkey refuses that until the apply below
# replaces it. Keeping the two steps adjacent keeps that window short.
ensure_redis_credentials
"$KCLI" apply -k "$OVERLAY_DIR"
if [ "$HAD_BACKEND" -eq 0 ]; then
  log "Fresh install detected; migrating after datastore creation..."
  KCLI="$KCLI" bash "$REPO_ROOT/scripts/run-k8s-migrations.sh" "$NAMESPACE" "$MIGRATION_IMAGE"
fi
log "Manifests applied and database migrations completed."
restore_overlay; trap - EXIT INT TERM

# `apply -k` never deletes. The single /health/ Route of earlier versions is
# replaced by one Route per published endpoint (route.yaml); left in place it
# would keep publishing the whole prefix.
"$KCLI" -n "$NAMESPACE" delete route testlookup-api-health --ignore-not-found >/dev/null 2>&1 \
  || warn "Could not delete the retired Route testlookup-api-health — delete it by hand."

# MongoDB reads its root user, and the application its MONGO_URI, from the
# Secret at pod start. When this run switched authentication on for an install
# that was already running, restart MongoDB first and then its clients (a
# first install starts every pod with the new Secret anyway).
if [ "$MONGO_AUTH_JUST_ENABLED" = true ] && [ "$HAD_BACKEND" -eq 1 ]; then
  log "MongoDB authentication was switched on in this run — restarting MongoDB, then its clients..."
  "$KCLI" -n "$NAMESPACE" rollout restart deployment/testlookup-mongo
  "$KCLI" -n "$NAMESPACE" rollout status deployment/testlookup-mongo --timeout=180s \
    || warn "testlookup-mongo not ready yet."
  for app in backend worker-critical worker-ingestion worker-ai worker-default worker-children beat; do
    "$KCLI" -n "$NAMESPACE" rollout restart "deployment/testlookup-$app" \
      || warn "Could not restart testlookup-$app — restart it by hand so it reads the new MONGO_URI."
  done
fi

# ── Step 10 — Custom Route TLS cert (optional) ──────────────────────────────
if [ -n "${ROUTE_TLS_CERT:-}" ] && [ -n "${ROUTE_TLS_KEY:-}" ]; then
  header "Step 10 — Inject custom TLS cert into Routes"
  if [ "$KCLI" = "oc" ]; then
    [ -f "$ROUTE_TLS_CERT" ] || error "ROUTE_TLS_CERT not found: $ROUTE_TLS_CERT"
    [ -f "$ROUTE_TLS_KEY" ]  || error "ROUTE_TLS_KEY not found: $ROUTE_TLS_KEY"
    # Build a JSON merge patch with python so multiline PEM is escaped safely.
    # The patch holds the private key, so it goes into an owner-only temporary
    # file passed with --patch-file, never into an argument.
    ROUTE_PATCH_FILE="$(umask 077; mktemp)"
    CERT="$ROUTE_TLS_CERT" KEY="$ROUTE_TLS_KEY" CA="${ROUTE_TLS_CACERT:-}" python3 - > "$ROUTE_PATCH_FILE" <<'PY' \
      || { rm -f "$ROUTE_PATCH_FILE"; error "Could not build the Route TLS patch (python3 is required for a custom certificate)."; }
import json, os
tls = {"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect",
       "certificate": open(os.environ["CERT"]).read(),
       "key": open(os.environ["KEY"]).read()}
ca = os.environ.get("CA")
if ca:
    tls["caCertificate"] = open(ca).read()
print(json.dumps({"spec": {"tls": tls}}))
PY
    # Every Route of the app, so a new path-scoped API Route is never missed.
    for r in $(oc -n "$NAMESPACE" get route -l app.kubernetes.io/name=testlookup -o name); do
      oc -n "$NAMESPACE" patch route "$r" --type=merge --patch-file "$ROUTE_PATCH_FILE" \
        && log "Patched cert into route/$r" || warn "Failed to patch cert into route/$r"
    done
    rm -f "$ROUTE_PATCH_FILE"
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
  || warn "Backend not ready after the migration Job completed. Check logs."
"$KCLI" -n "$NAMESPACE" rollout status deploy/testlookup-mcp --timeout=300s \
  || error "MCP replacement not ready; legacy credentials were retained."

# The new pods are up under their own SCC grants, so the grant an older
# install left on the default ServiceAccount can go (opt-in, see Step 5).
if [ "$REVOKE_DEFAULT_ANYUID" = "true" ] && [ "$KCLI" = "oc" ]; then
  oc adm policy remove-scc-from-user anyuid -z default -n "$NAMESPACE" \
    && log "anyuid removed from system:serviceaccount:${NAMESPACE}:default" \
    || warn "Could not remove anyuid from the default ServiceAccount (needs cluster-admin)."
fi

# Retire only the deployment-managed principal captured from the old Secret.
if [ -n "$LEGACY_MCP_USER" ]; then
  if [ "${KEEP_LEGACY_MCP_SERVICE_ACCOUNT:-false}" = "true" ]; then
    warn "Keeping legacy MCP account '$LEGACY_MCP_USER' by operator request."
  else
    LEGACY_BACKEND_POD=$("$KCLI" -n "$NAMESPACE" get pod \
      -l app=testlookup-backend --field-selector=status.phase=Running \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [ -z "$LEGACY_BACKEND_POD" ]; then
      error "Cannot retire legacy MCP account: no running backend pod."
    fi
    "$KCLI" -n "$NAMESPACE" exec -i "$LEGACY_BACKEND_POD" -- \
      env LEGACY_MCP_USERNAME="$LEGACY_MCP_USER" \
      python < "$REPO_ROOT/scripts/retireLegacyMcpServiceAccount.py" \
      || error "Legacy MCP account retirement failed; Secret keys were retained."
    "$KCLI" -n "$NAMESPACE" patch secret testlookup-secrets --type=merge \
      -p='{"data":{"MCP_USERNAME":null,"MCP_PASSWORD":null}}'
    if [ -f "$CREDS_FILE" ]; then
      sed -i '/^MCP_USERNAME=/d;/^MCP_PASSWORD=/d' "$CREDS_FILE"
    fi
    log "Legacy MCP account retired and stored credentials removed."
  fi
fi

# ── Step 12 — S3 buckets ────────────────────────────────────────────────────
# scripts/s3_setup.py ships in the backend image (/app/scripts) and reads the
# pod's own MINIO_* settings: it waits for testlookup-minio and creates every
# bucket the backend writes to (idempotent; no client image, no internet).
# The command is ONE `sh -c` string: Git Bash on Windows rewrites an argument
# that starts with / into a Windows path the pod does not have. Not
# MSYS_NO_PATHCONV=1: it also stops Git Bash converting a /c/... KUBECONFIG.
header "Step 12 — S3 buckets"
S3_SETUP_HINT="$KCLI -n $NAMESPACE exec deploy/testlookup-backend -- sh -c 'python /app/scripts/s3_setup.py'"
BACKEND_POD="$("$KCLI" -n "$NAMESPACE" get pod -l app=testlookup-backend --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")"
if [ -n "$BACKEND_POD" ]; then
  "$KCLI" -n "$NAMESPACE" exec "$BACKEND_POD" -- sh -c "python /app/scripts/s3_setup.py" \
    && log "S3 buckets ready." \
    || warn "Bucket setup failed — fix the cause, then re-run: $S3_SETUP_HINT"
else
  warn "No running backend pod yet — create the buckets later with: $S3_SETUP_HINT"
fi

# ── Step 13 — Admin user ────────────────────────────────────────────────────
if [ "$SKIP_ADMIN" = false ]; then
  header "Step 13 — Initial admin user"
  if [ -z "$ADMIN_PASSWORD" ]; then
    ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -d '=/+' | head -c 20)"
    ADMIN_PASSWORD_GENERATED=true
  fi
  BACKEND_POD="$("$KCLI" -n "$NAMESPACE" get pod -l app=testlookup-backend -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")"
  if [ -n "$BACKEND_POD" ]; then
    # The image's create_admin.py is idempotent (an existing admin is left
    # untouched). Its output is filtered so the password is shown once, in the
    # summary below. ADMIN_PASSWORD_GENERATED makes a generated password
    # one the admin must change at first sign-in. The password is piped to the
    # pod and read there from stdin: an env argument would show it in this
    # host's process list.
    if ADMIN_OUT=$(printf '%s\n' "$ADMIN_PASSWORD" | "$KCLI" -n "$NAMESPACE" exec -i "$BACKEND_POD" -- env \
          ADMIN_USERNAME="$ADMIN_USERNAME" \
          ADMIN_EMAIL="$ADMIN_EMAIL" ADMIN_FULL_NAME="$ADMIN_FULL_NAME" \
          ADMIN_PASSWORD_GENERATED="${ADMIN_PASSWORD_GENERATED:-false}" \
          sh -c 'IFS= read -r ADMIN_PASSWORD && export ADMIN_PASSWORD && exec "$@"' read-admin-password \
          sh -c "python /app/scripts/create_admin.py" 2>&1); then
      printf '%s\n' "$ADMIN_OUT" | grep -iv 'password:' || true
      if printf '%s\n' "$ADMIN_OUT" | grep -q 'admin user created'; then
        ADMIN_CREATED=true
        log "Admin user created ($ADMIN_USERNAME)"
      else
        log "Admin user already exists ($ADMIN_USERNAME) — its password was NOT changed."
      fi
    else
      printf '%s\n' "$ADMIN_OUT" | grep -iv 'password:' || true
      warn "create_admin.py failed — run it manually once the backend is healthy:"
      warn "  $KCLI -n $NAMESPACE exec deployment/testlookup-backend -- sh -c 'python /app/scripts/create_admin.py'"
    fi
  else
    warn "Backend pod not found — create the admin user later: $KCLI -n $NAMESPACE exec deployment/testlookup-backend -- sh -c 'python /app/scripts/create_admin.py'"
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
echo "  API:        https://${ROUTE_HOST_API}/api-docs"
echo "  MCP (SSE):  https://${ROUTE_HOST_MCP}/sse"
if [ "$ADMIN_CREATED" = true ] && [ "$ADMIN_PASSWORD_GENERATED" = true ]; then
  echo "  Admin:      ${ADMIN_USERNAME}"
  echo "  Password:   ${ADMIN_PASSWORD}"
  echo -e "  ${YELLOW}Generated for this install and shown only once — store it in your password manager now.${NC}"
elif [ "$ADMIN_CREATED" = true ]; then
  echo "  Admin:      ${ADMIN_USERNAME} (password: the ADMIN_PASSWORD you supplied)"
else
  echo "  Admin:      ${ADMIN_USERNAME} (unchanged; reset a lost password with scripts/getUser.py)"
fi
echo ""
echo "  Routes:   $KCLI -n $NAMESPACE get route"
echo "  Pods:     $KCLI -n $NAMESPACE get pods"
echo "  Re-deploy after a code change:"
echo "    $0            # fresh build tag, mirror skip-able with --skip-mirror"
echo ""
