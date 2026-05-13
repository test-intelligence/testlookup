#!/usr/bin/env bash
# ============================================================
# deploy-homelab.sh — Deploy TestLookup to K3s homelab cluster
# ============================================================
# Prerequisites:
#   - 3-node K3s cluster running (k3s_setup.md steps 1-7, local-path storage)
#   - kubectl configured and pointing to the cluster
#   - Docker Desktop running on dev machine
#   - registry.local in hosts file pointing to 192.168.0.101
#   - Docker Desktop configured with insecure-registries: ["registry.local:30500"]
#
# Usage:
#   ./homelabsetup/deploy-homelab.sh [--skip-registry] [--skip-build] [--skip-models]
#
# Flags:
#   --skip-registry  Skip registry deployment (already running)
#   --skip-build     Skip Docker image builds (images already pushed)
#   --skip-models    Skip Ollama model pull (already downloaded)
#   --skip-dns       Skip DNS/hosts file reminder
#   --teardown       Delete the entire deployment (keeps PVCs)
#   --teardown-all   Delete namespace + all data (DESTRUCTIVE)
# ============================================================

set -euo pipefail

# ── Configuration ───────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGISTRY="registry.local:30500"
NAMESPACE="testlookup"
NODES=("192.168.0.101" "192.168.0.102" "192.168.0.103")
NODE_USER="labadmin"
CONTROL_NODE="${NODES[0]}"
MINIO_ACCESS="testlookup_minio"
MCP_USER="mcp_service"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ── Parse flags ─────────────────────────────────────────────
SKIP_REGISTRY=false
SKIP_BUILD=false
SKIP_MODELS=false
SKIP_DNS=false
TEARDOWN=false
TEARDOWN_ALL=false

for arg in "$@"; do
  case $arg in
    --skip-registry) SKIP_REGISTRY=true ;;
    --skip-build)    SKIP_BUILD=true ;;
    --skip-models)   SKIP_MODELS=true ;;
    --skip-dns)      SKIP_DNS=true ;;
    --teardown)      TEARDOWN=true ;;
    --teardown-all)  TEARDOWN_ALL=true ;;
    *)               echo -e "${RED}Unknown flag: $arg${NC}"; exit 1 ;;
  esac
done

# ── Helper functions ────────────────────────────────────────
log()     { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; exit 1; }
header()  { echo -e "\n${CYAN}══════════════════════════════════════════════════${NC}"; echo -e "${CYAN}  $1${NC}"; echo -e "${CYAN}══════════════════════════════════════════════════${NC}\n"; }

check_command() {
  command -v "$1" >/dev/null 2>&1 || error "$1 is required but not installed."
}

wait_for_pods() {
  local label=$1
  local timeout=${2:-120}
  log "Waiting for pods with label $label (timeout: ${timeout}s)..."
  kubectl -n "$NAMESPACE" wait --for=condition=ready pod -l "$label" --timeout="${timeout}s" 2>/dev/null || {
    warn "Pods with label $label not ready within ${timeout}s. Checking status..."
    kubectl -n "$NAMESPACE" get pods -l "$label"
    return 1
  }
}

# ── Teardown mode ───────────────────────────────────────────
if [ "$TEARDOWN_ALL" = true ]; then
  header "TEARDOWN (DESTRUCTIVE) — Deleting namespace + all data"
  warn "This will delete ALL data including databases!"
  read -p "Type 'yes' to confirm: " confirm
  [ "$confirm" = "yes" ] || { echo "Aborted."; exit 0; }
  kubectl delete namespace "$NAMESPACE" --ignore-not-found
  log "Namespace $NAMESPACE deleted."
  exit 0
fi

if [ "$TEARDOWN" = true ]; then
  header "TEARDOWN — Deleting application (PVCs preserved)"
  kubectl delete -k "$REPO_ROOT/k8s/overlays/homelab" --ignore-not-found || true
  log "Application deleted. PVCs and data preserved."
  exit 0
fi

# ── Preflight checks ───────────────────────────────────────
header "Step 0 — Preflight Checks"

check_command kubectl
check_command docker

log "Checking kubectl cluster access..."
kubectl get nodes >/dev/null 2>&1 || error "Cannot reach K3s cluster. Check your kubeconfig."

NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
log "Cluster has $NODE_COUNT node(s)"

log "Checking Docker Desktop..."
docker info >/dev/null 2>&1 || error "Docker is not running. Start Docker Desktop first."

log "Checking local-path StorageClass..."
kubectl get storageclass local-path >/dev/null 2>&1 || error "local-path StorageClass not found. Is this a K3s cluster?"

log "All preflight checks passed."

# ── Helper: check registry reachability ─────────────────────
check_registry() {
  # Try hostname first, then raw IP
  if curl -sf --max-time 5 "http://registry.local:30500/v2/_catalog" >/dev/null 2>&1; then
    return 0
  elif curl -sf --max-time 5 "http://${CONTROL_NODE}:30500/v2/_catalog" >/dev/null 2>&1; then
    warn "'registry.local' DNS not resolving, but registry is reachable at ${CONTROL_NODE}:30500"
    warn "Add to your hosts file: ${CONTROL_NODE} registry.local"
    return 0
  fi
  return 1
}

# ── Step 1: Registry ───────────────────────────────────────
if [ "$SKIP_REGISTRY" = false ]; then
  header "Step 1 — Deploy Local Container Registry"

  # Check if registry already running
  if kubectl -n registry get deployment registry >/dev/null 2>&1; then
    log "Registry already deployed. Checking health..."
    if check_registry; then
      log "Registry is healthy. Skipping deployment."
    else
      warn "Registry pod exists but is not reachable from this machine."
      log "Checking registry pod status..."
      kubectl -n registry get pods -l app=registry -o wide 2>/dev/null || true
      echo ""
      warn "Possible causes:"
      echo "  1. Registry pod is not Running (check output above)"
      echo "  2. 'registry.local' is not in your hosts file"
      echo "     Fix: Add '${CONTROL_NODE} registry.local' to C:\\Windows\\System32\\drivers\\etc\\hosts"
      echo "  3. Docker Desktop missing insecure-registries config"
      echo "     Fix: Add 'registry.local:30500' to Docker Desktop > Settings > Docker Engine > insecure-registries"
      echo "  4. Firewall blocking port 30500 on the nodes"
      echo "     Fix: On each node: sudo ufw allow 30500/tcp"
      echo ""
      log "Trying direct node IP (${CONTROL_NODE}:30500)..."
      if curl -sf --max-time 5 "http://${CONTROL_NODE}:30500/v2/_catalog" 2>/dev/null; then
        echo ""
        log "Registry IS reachable via IP. The issue is DNS only."
        warn "Add to hosts file: ${CONTROL_NODE} registry.local"
        warn "Docker push will fail without this. Fix it before continuing."
      else
        warn "Registry is NOT reachable via IP either. Check firewall / pod status."
      fi
      echo ""
      read -p "Press Enter after fixing the issue, or Ctrl+C to abort..."
    fi
  else
    log "Creating registry directory on node1..."
    ssh "${NODE_USER}@${CONTROL_NODE}" "sudo mkdir -p /opt/registry" 2>/dev/null || warn "Could not SSH to node1 — ensure /opt/registry exists manually."

    log "Deploying registry to K3s..."
    cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: registry
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: registry-pv
spec:
  capacity:
    storage: 20Gi
  accessModes: [ReadWriteOnce]
  hostPath:
    path: /opt/registry
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: ["k8s-node1"]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: registry-data
  namespace: registry
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ""
  volumeName: registry-pv
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: registry
  namespace: registry
spec:
  replicas: 1
  selector:
    matchLabels:
      app: registry
  template:
    metadata:
      labels:
        app: registry
    spec:
      nodeSelector:
        kubernetes.io/hostname: k8s-node1
      containers:
        - name: registry
          image: registry:2
          ports:
            - containerPort: 5000
          volumeMounts:
            - name: data
              mountPath: /var/lib/registry
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: registry-data
---
apiVersion: v1
kind: Service
metadata:
  name: registry
  namespace: registry
spec:
  type: NodePort
  selector:
    app: registry
  ports:
    - port: 5000
      targetPort: 5000
      nodePort: 30500
EOF

    log "Waiting for registry pod..."
    kubectl -n registry wait --for=condition=ready pod -l app=registry --timeout=60s

    log "Configuring K3s nodes to trust the registry..."
    warn "You must configure registries.yaml on EACH node if not already done:"
    echo "  SSH into each node and run:"
    echo "    sudo mkdir -p /etc/rancher/k3s"
    echo "    sudo tee /etc/rancher/k3s/registries.yaml <<'YAML'"
    echo "    mirrors:"
    echo "      \"registry.local:5000\":"
    echo "        endpoint:"
    echo "          - \"http://192.168.0.101:30500\""
    echo "    YAML"
    echo "    sudo systemctl restart k3s  # or k3s-agent on workers"
    echo ""
    read -p "Press Enter when registry mirrors are configured on all nodes (or skip if already done)..."
  fi

  # Verify registry is reachable
  if check_registry; then
    log "Registry verified: $(curl -s http://registry.local:30500/v2/_catalog 2>/dev/null || curl -s http://${CONTROL_NODE}:30500/v2/_catalog 2>/dev/null)"
  else
    warn "Cannot reach registry at registry.local:30500 or ${CONTROL_NODE}:30500."
    warn "Ensure 'registry.local' is in your hosts file pointing to ${CONTROL_NODE}"
    warn "and Docker Desktop has 'registry.local:30500' in insecure-registries."
    read -p "Press Enter to continue anyway, or Ctrl+C to abort..."
  fi
else
  log "Skipping registry deployment (--skip-registry)"
fi

# ── Step 2: Build and Push Images ──────────────────────────
if [ "$SKIP_BUILD" = false ]; then
  header "Step 2 — Build and Push Container Images"

  cd "$REPO_ROOT"

  # Determine which registry address works for docker push
  if curl -sf --max-time 3 "http://registry.local:30500/v2/_catalog" >/dev/null 2>&1; then
    PUSH_REGISTRY="registry.local:30500"
  elif curl -sf --max-time 3 "http://${CONTROL_NODE}:30500/v2/_catalog" >/dev/null 2>&1; then
    PUSH_REGISTRY="${CONTROL_NODE}:30500"
    warn "Using IP-based registry: ${PUSH_REGISTRY} (add 'registry.local' to hosts for consistency)"
  else
    PUSH_REGISTRY="$REGISTRY"
    warn "Registry not reachable — builds will proceed but push may fail."
  fi

  # Stage client/ into backend/__client_sdks_staged/ so the production
  # Dockerfile can bake it into the image at /app/client_sdks. The SDK
  # download endpoint at /api/v1/sdk/{lang} reads from there. We use a
  # trap so the staging dir is cleaned up even on Ctrl+C / build failure.
  #
  # Plain `cp -r` chokes when pytest/venv cache dirs in client/examples/* have
  # restricted permissions (often left behind by a prior test run inside a
  # container). Use rsync when available, tar-pipe otherwise — both honour
  # the exclude list and never try to read the excluded files. Caches and
  # venvs don't belong in the SDK build context regardless.
  STAGED_SDK="$REPO_ROOT/backend/__client_sdks_staged"
  log "Staging client SDKs into backend build context..."
  rm -rf "$STAGED_SDK"
  mkdir -p "$STAGED_SDK"
  trap 'rm -rf "$STAGED_SDK"' EXIT INT TERM

  SDK_EXCLUDES=(
    '.pytest_cache' '__pycache__' '*.pyc' '.venv' 'venv'
    'node_modules' '.tox' '.mypy_cache' '.ruff_cache' 'target' 'build'
    '*.egg-info' '.coverage' 'htmlcov' '.DS_Store'
  )
  if command -v rsync >/dev/null 2>&1; then
    RSYNC_EXCLUDES=()
    for e in "${SDK_EXCLUDES[@]}"; do RSYNC_EXCLUDES+=("--exclude=$e"); done
    rsync -a "${RSYNC_EXCLUDES[@]}" "$REPO_ROOT/client/" "$STAGED_SDK/"
  else
    TAR_EXCLUDES=()
    for e in "${SDK_EXCLUDES[@]}"; do TAR_EXCLUDES+=("--exclude=$e"); done
    ( cd "$REPO_ROOT/client" && tar "${TAR_EXCLUDES[@]}" -cf - . ) \
      | ( cd "$STAGED_SDK" && tar -xf - )
  fi

  log "Building backend image..."
  docker build -t "${PUSH_REGISTRY}/testlookup/backend:latest" \
    --target production -f backend/Dockerfile backend/

  rm -rf "$STAGED_SDK"
  trap - EXIT INT TERM

  log "Building frontend image (uses same-origin relative API URLs)..."
  # --pull guarantees the base node:20-alpine and nginx:alpine layers are
  # refreshed. We intentionally do NOT pass --no-cache so the npm install
  # layer (slow) stays cached when only frontend/src changes — Docker
  # invalidates downstream layers automatically when source files change.
  docker build --pull -t "${PUSH_REGISTRY}/testlookup/frontend:latest" \
    --target production -f frontend/Dockerfile frontend/

  # Tag and push a content-addressable build tag in addition to :latest so
  # the cluster has a way to verify the image it pulled really matches what
  # we just built. Useful when triaging "I don't see my UI changes".
  FRONTEND_BUILD_TAG="build-$(date -u +%Y%m%d-%H%M%S)"
  docker tag  "${PUSH_REGISTRY}/testlookup/frontend:latest" \
              "${PUSH_REGISTRY}/testlookup/frontend:${FRONTEND_BUILD_TAG}"
  log "Frontend build tagged ${FRONTEND_BUILD_TAG}"

  log "Building MCP server image..."
  docker build -t "${PUSH_REGISTRY}/testlookup/mcp:latest" \
    -f mcp/Dockerfile mcp/

  log "Pushing images to registry..."
  docker push "${PUSH_REGISTRY}/testlookup/backend:latest"
  docker push "${PUSH_REGISTRY}/testlookup/frontend:latest"
  docker push "${PUSH_REGISTRY}/testlookup/mcp:latest"

  # Push the dated frontend tag too so we can verify in-cluster which build is live.
  docker push "${PUSH_REGISTRY}/testlookup/frontend:${FRONTEND_BUILD_TAG}"

  # Capture the digest of the freshly pushed :latest so we can compare it
  # against the digest the pod actually runs after the rollout finishes.
  FRONTEND_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' \
    "${PUSH_REGISTRY}/testlookup/frontend:latest" 2>/dev/null \
    | sed 's/.*@//' || echo "")
  if [ -n "$FRONTEND_DIGEST" ]; then
    log "Frontend image digest just pushed: ${FRONTEND_DIGEST}"
  fi

  log "Images pushed. Registry catalog:"
  curl -s "http://${PUSH_REGISTRY}/v2/_catalog" 2>/dev/null || warn "Could not query registry catalog"
else
  log "Skipping image builds (--skip-build)"
fi

# ── Step 3: Create Namespace ───────────────────────────────
header "Step 3 — Create Namespace"

if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  log "Namespace $NAMESPACE already exists."
else
  kubectl create namespace "$NAMESPACE"
  log "Namespace $NAMESPACE created."
fi

# ── Step 4: Generate and Apply Secrets ─────────────────────
header "Step 4 — Generate and Apply Secrets"

if kubectl -n "$NAMESPACE" get secret testlookup-secrets >/dev/null 2>&1; then
  log "Secret testlookup-secrets already exists. Skipping generation."
  warn "To regenerate: kubectl -n $NAMESPACE delete secret testlookup-secrets"
else
  log "Generating random credentials..."

  APP_SECRET=$(openssl rand -hex 32)
  JWT_SECRET=$(openssl rand -hex 32)
  PG_PASSWORD=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
  MINIO_SECRET=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
  WEBHOOK_SECRET=$(openssl rand -hex 32)
  MCP_PASS=$(openssl rand -base64 12 | tr -d '=/+' | head -c 16)

  DATABASE_URL="postgresql+asyncpg://testlookup_user:${PG_PASSWORD}@testlookup-postgres:5432/testlookup"
  MONGO_URI="mongodb://testlookup-mongo:27017"

  kubectl -n "$NAMESPACE" create secret generic testlookup-secrets \
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
  echo ""
  echo -e "${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${YELLOW}║  SAVE THESE CREDENTIALS — SHOWN ONLY ONCE          ║${NC}"
  echo -e "${YELLOW}╠══════════════════════════════════════════════════════╣${NC}"
  echo -e "${YELLOW}║${NC}  PostgreSQL password: ${PG_PASSWORD}"
  echo -e "${YELLOW}║${NC}  MinIO access key:    ${MINIO_ACCESS}"
  echo -e "${YELLOW}║${NC}  MinIO secret key:    ${MINIO_SECRET}"
  echo -e "${YELLOW}║${NC}  MCP username:        ${MCP_USER}"
  echo -e "${YELLOW}║${NC}  MCP password:        ${MCP_PASS}"
  echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""

  # Save credentials to a local file (gitignored)
  CREDS_FILE="$REPO_ROOT/homelabsetup/.homelab-credentials"
  cat > "$CREDS_FILE" <<CREDS
# TestLookup Homelab Credentials — generated $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# DO NOT COMMIT THIS FILE
POSTGRES_PASSWORD=${PG_PASSWORD}
MINIO_ACCESS_KEY=${MINIO_ACCESS}
MINIO_SECRET_KEY=${MINIO_SECRET}
MCP_USERNAME=${MCP_USER}
MCP_PASSWORD=${MCP_PASS}
DATABASE_URL=${DATABASE_URL}
CREDS
  log "Credentials saved to homelabsetup/.homelab-credentials (do NOT commit this file)"
fi

# ── Step 4b: TLS Certificate (skipped — homelab ingress is HTTP-only) ───
header "Step 4b — TLS Certificate (skipped)"

# The homelab ingress was switched to HTTP-only on 2026-05-10 to remove the
# self-signed-cert friction every client (browsers, Java SDK, curl) was hitting.
# The kustomize overlay no longer references testlookup-tls-cert.
#
# If the Secret already exists from a prior HTTPS deploy, Step 5 below
# prunes it along with the other orphaned redirect/middleware resources.
# To re-enable HTTPS later, restore middleware-redirect-https.yaml + the
# split ingresses from git history and reinstate the openssl block.
log "Skipping TLS cert generation (ingress is HTTP-only)."

# ── Step 5: Deploy with Kustomize ──────────────────────────
header "Step 5 — Deploy with Kustomize"

log "Applying Kustomize overlay..."
kubectl apply -k "$REPO_ROOT/k8s/overlays/homelab"
log "All resources applied."

# ``kubectl apply -k`` only creates/updates resources — it does not delete
# resources that were removed from the manifest. Explicitly remove orphans
# left over from previous HTTPS-enabled deploys, otherwise the old redirect
# Ingress keeps 308'ing http://testlookup.local/ → https://. Idempotent.
log "Pruning HTTPS-era orphans (if present)..."
kubectl -n "$NAMESPACE" delete ingress testlookup-traefik-redirect \
  --ignore-not-found=true >/dev/null
kubectl -n "$NAMESPACE" delete middleware redirect-https \
  --ignore-not-found=true >/dev/null
# The TLS Secret is harmless to leave but cleaner to drop too.
kubectl -n "$NAMESPACE" delete secret testlookup-tls-cert \
  --ignore-not-found=true >/dev/null
log "Orphan prune complete."

# ── Step 5b: Force fresh :latest pull on app deployments ───
# K3s containerd caches :latest aggressively. Even with imagePullPolicy=Always
# (set by the homelab overlay), an unchanged Deployment spec means kubectl
# apply doesn't roll. Trigger rollouts explicitly so the new image content
# actually lands on the nodes. Skipped when --skip-build is passed because no
# new image content exists.
if [ "$SKIP_BUILD" = false ]; then
  header "Step 5b — Force Fresh Image Pull"

  APP_DEPLOYMENTS=(
    testlookup-backend
    testlookup-frontend
    testlookup-mcp
    testlookup-worker-critical
    testlookup-worker-ingestion
    testlookup-worker-ai
    testlookup-worker-default
    testlookup-beat
  )

  log "Restarting app deployments to pull the latest image..."
  for dep in "${APP_DEPLOYMENTS[@]}"; do
    if kubectl -n "$NAMESPACE" get deployment "$dep" >/dev/null 2>&1; then
      kubectl -n "$NAMESPACE" rollout restart deployment/"$dep" >/dev/null
    else
      warn "Deployment $dep not found yet — will start fresh on first reconcile."
    fi
  done

  log "Waiting for rollouts to complete (timeout 240s each)..."
  for dep in "${APP_DEPLOYMENTS[@]}"; do
    if kubectl -n "$NAMESPACE" get deployment "$dep" >/dev/null 2>&1; then
      kubectl -n "$NAMESPACE" rollout status deployment/"$dep" --timeout=240s \
        || warn "$dep did not become ready in time — check 'kubectl -n $NAMESPACE describe deployment $dep'"
    fi
  done
  log "App deployments rolled to fresh :latest content."

  # ── Sanity check: digest of the frontend pod matches the one we just pushed.
  # Catches the "K3s containerd kept the cached :latest" failure mode early —
  # without this, a silent cache hit looks like a successful deploy.
  if [ -n "${FRONTEND_DIGEST:-}" ]; then
    POD_DIGEST=$(kubectl -n "$NAMESPACE" get pod -l app=testlookup-frontend \
      -o jsonpath='{.items[0].status.containerStatuses[0].imageID}' 2>/dev/null \
      | sed 's/.*@//' || echo "")
    if [ -z "$POD_DIGEST" ]; then
      warn "Could not read frontend pod imageID — skipping digest verification."
    elif [ "$POD_DIGEST" = "$FRONTEND_DIGEST" ]; then
      log "Frontend pod is running the just-pushed image (digest match)."
    else
      warn "Frontend pod digest does NOT match the pushed image!"
      warn "  Pushed: $FRONTEND_DIGEST"
      warn "  Pod:    $POD_DIGEST"
      warn "K3s likely served a cached :latest. Force re-pull with:"
      warn "  kubectl -n $NAMESPACE delete pod -l app=testlookup-frontend"
      warn "Or use the dated tag instead of :latest:"
      warn "  kubectl -n $NAMESPACE set image deployment/testlookup-frontend \\"
      warn "    frontend=registry.local:5000/testlookup/frontend:${FRONTEND_BUILD_TAG}"
    fi
  fi
else
  log "Skipping rollout-restart (--skip-build): keeping current images."
fi

# ── Step 6: Wait for Infrastructure ────────────────────────
header "Step 6 — Wait for Infrastructure Pods"

log "Waiting for infrastructure pods to become ready..."

INFRA_LABELS=(
  "app=testlookup-postgres"
  "app=testlookup-mongo"
  "app=testlookup-redis"
  "app=testlookup-minio"
  "app=testlookup-chromadb"
)

INFRA_OK=true
for label in "${INFRA_LABELS[@]}"; do
  wait_for_pods "$label" 120 || INFRA_OK=false
done

if [ "$INFRA_OK" = true ]; then
  log "All infrastructure pods are ready."
else
  warn "Some infrastructure pods are not ready. Continuing — they may still be starting."
fi

# Wait a bit more for the backend (depends on DB migrations)
log "Waiting for backend (runs DB migrations on startup — may take a minute)..."
wait_for_pods "app=testlookup-backend" 180 || warn "Backend not ready yet. Check: kubectl -n testlookup logs deployment/testlookup-backend"

# ── Step 7: Create MinIO Buckets ───────────────────────────
header "Step 7 — Create MinIO Buckets"

# Read MinIO secret from cluster
MINIO_SECRET_VAL=$(kubectl -n "$NAMESPACE" get secret testlookup-secrets -o jsonpath='{.data.MINIO_SECRET_KEY}' | base64 -d 2>/dev/null || echo "")

if [ -z "$MINIO_SECRET_VAL" ]; then
  warn "Could not read MinIO secret from cluster. Skipping bucket creation."
  warn "Create buckets manually: mc mb homelab/test-telemetry && mc mb homelab/knowledge-docs"
else
  # Check if mc is available
  if command -v mc >/dev/null 2>&1; then
    log "Starting MinIO port-forward..."
    kubectl -n "$NAMESPACE" port-forward svc/testlookup-minio 9000:9000 &
    PF_PID=$!
    sleep 3

    log "Configuring MinIO client..."
    mc alias set homelab http://localhost:9000 "$MINIO_ACCESS" "$MINIO_SECRET_VAL" --api S3v4 2>/dev/null || true

    log "Creating buckets..."
    mc mb homelab/test-telemetry --ignore-existing 2>/dev/null || warn "Could not create test-telemetry bucket"
    mc mb homelab/knowledge-docs --ignore-existing 2>/dev/null || warn "Could not create knowledge-docs bucket"

    log "Buckets:"
    mc ls homelab/ 2>/dev/null || true

    kill $PF_PID 2>/dev/null || true
    wait $PF_PID 2>/dev/null || true
  else
    warn "MinIO client (mc) not found. Install it to create buckets:"
    echo "  Windows: winget install MinIO.Client"
    echo "  Linux:   wget https://dl.min.io/client/mc/release/linux-amd64/mc && chmod +x mc && sudo mv mc /usr/local/bin/"
    echo ""
    echo "Then run:"
    echo "  kubectl -n testlookup port-forward svc/testlookup-minio 9000:9000 &"
    echo "  mc alias set homelab http://localhost:9000 $MINIO_ACCESS <secret>"
    echo "  mc mb homelab/test-telemetry"
    echo "  mc mb homelab/knowledge-docs"
  fi
fi

# ── Step 8: Pull Ollama Models ─────────────────────────────
if [ "$SKIP_MODELS" = false ]; then
  header "Step 8 — Pull Ollama Models"

  log "Waiting for Ollama pod..."
  wait_for_pods "app=testlookup-ollama" 120 || { warn "Ollama pod not ready. Skipping model pull."; SKIP_MODELS=true; }

  if [ "$SKIP_MODELS" = false ]; then
    OLLAMA_POD=$(kubectl -n "$NAMESPACE" get pod -l app=testlookup-ollama -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

    if [ -n "$OLLAMA_POD" ]; then
      # Check if models already exist
      EXISTING_MODELS=$(kubectl -n "$NAMESPACE" exec "$OLLAMA_POD" -- ollama list 2>/dev/null || echo "")

      if echo "$EXISTING_MODELS" | grep -q "qwen2.5:7b"; then
        log "qwen2.5:7b already present. Skipping pull."
      else
        log "Pulling qwen2.5:7b (this may take several minutes)..."
        kubectl -n "$NAMESPACE" exec "$OLLAMA_POD" -- ollama pull qwen2.5:7b
      fi

      if echo "$EXISTING_MODELS" | grep -q "nomic-embed-text"; then
        log "nomic-embed-text already present. Skipping pull."
      else
        log "Pulling nomic-embed-text..."
        kubectl -n "$NAMESPACE" exec "$OLLAMA_POD" -- ollama pull nomic-embed-text
      fi

      log "Installed models:"
      kubectl -n "$NAMESPACE" exec "$OLLAMA_POD" -- ollama list
    else
      warn "Could not find Ollama pod. Pull models manually later."
    fi
  fi
else
  log "Skipping Ollama model pull (--skip-models)"
fi

# ── Step 9: DNS Configuration ──────────────────────────────
if [ "$SKIP_DNS" = false ]; then
  header "Step 9 — DNS Configuration"

  TRAEFIK_IP=$(kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

  if [ -n "$TRAEFIK_IP" ]; then
    log "Traefik external IP: $TRAEFIK_IP"

    # Check hosts file (Windows and Linux paths)
    HOSTS_FILE="/etc/hosts"
    [ -f "/c/Windows/System32/drivers/etc/hosts" ] && HOSTS_FILE="/c/Windows/System32/drivers/etc/hosts"

    if grep -q "testlookup.local" "$HOSTS_FILE" 2>/dev/null; then
      log "testlookup.local already in hosts file."
    else
      echo ""
      warn "Add this to your hosts file:"
      echo ""
      echo "  Windows (Admin PowerShell):"
      echo "    Add-Content C:\\Windows\\System32\\drivers\\etc\\hosts \"$TRAEFIK_IP testlookup.local\""
      echo "    ipconfig /flushdns"
      echo ""
      echo "  Linux/Mac:"
      echo "    echo \"$TRAEFIK_IP testlookup.local\" | sudo tee -a /etc/hosts"
      echo ""
      warn "Note: Some browsers auto-upgrade .local to HTTPS. Use http:// explicitly."
      echo ""
    fi
  else
    warn "Could not determine Traefik IP. Check: kubectl -n kube-system get svc traefik"
  fi
fi

# ── Step 10: Create Initial Admin User ─────────────────────
header "Step 10 — Create Initial Admin User"

# Defaults — override by exporting ADMIN_USERNAME/ADMIN_PASSWORD/ADMIN_EMAIL
# before invoking this script. On first deploy these are written to the
# .homelab-credentials file; subsequent deploys read from that file so the
# banner stays accurate even after re-runs.
CREDS_FILE="$REPO_ROOT/homelabsetup/.homelab-credentials"

if [ -f "$CREDS_FILE" ] && grep -q "^ADMIN_PASSWORD=" "$CREDS_FILE"; then
  : "${ADMIN_USERNAME:=$(grep -E '^ADMIN_USERNAME=' "$CREDS_FILE" | cut -d= -f2- | head -n1)}"
  : "${ADMIN_PASSWORD:=$(grep -E '^ADMIN_PASSWORD=' "$CREDS_FILE" | cut -d= -f2- | head -n1)}"
  : "${ADMIN_EMAIL:=$(grep -E '^ADMIN_EMAIL=' "$CREDS_FILE" | cut -d= -f2- | head -n1)}"
fi

ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin@2026!}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@testlookup.local}"
ADMIN_FULL_NAME="${ADMIN_FULL_NAME:-TestLookup Admin}"

log "Waiting for backend to be ready..."
ADMIN_OUTPUT=""
ADMIN_RAN=false
if wait_for_pods "app=testlookup-backend" 120; then
  BACKEND_POD=$(kubectl -n "$NAMESPACE" get pod -l app=testlookup-backend -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [ -n "$BACKEND_POD" ]; then
    log "Running scripts/createAdmin.py inside backend pod (idempotent)..."
    # Pipe the script over stdin (matches the docstring usage). `env ...` sets
    # the per-invocation overrides so the script picks up our values without
    # mutating the pod's environment.
    if ADMIN_OUTPUT=$(kubectl -n "$NAMESPACE" exec -i "$BACKEND_POD" -- \
        env \
          ADMIN_USERNAME="$ADMIN_USERNAME" \
          ADMIN_PASSWORD="$ADMIN_PASSWORD" \
          ADMIN_EMAIL="$ADMIN_EMAIL" \
          ADMIN_FULL_NAME="$ADMIN_FULL_NAME" \
          python < "$REPO_ROOT/scripts/createAdmin.py" 2>&1); then
      ADMIN_RAN=true
      echo "$ADMIN_OUTPUT"
    else
      warn "createAdmin.py failed inside the backend pod. Output:"
      echo "$ADMIN_OUTPUT"
      warn "Run manually once the backend is healthy:"
      warn "  kubectl -n $NAMESPACE exec -i deployment/testlookup-backend -- \\"
      warn "    env ADMIN_PASSWORD='$ADMIN_PASSWORD' python < scripts/createAdmin.py"
    fi
  else
    warn "Could not find backend pod. Create admin user manually later."
  fi
else
  warn "Backend not ready. Create admin user manually after backend starts:"
  echo "  kubectl -n testlookup exec -i deployment/testlookup-backend -- python < scripts/createAdmin.py"
fi

if [ "$ADMIN_RAN" = true ]; then
  # Persist creds so future re-runs can read them back. We only write the
  # admin block once — never overwrite a password the operator may have
  # rotated via getUser.py.
  if [ -f "$CREDS_FILE" ] && ! grep -q "^ADMIN_USERNAME=" "$CREDS_FILE"; then
    cat >> "$CREDS_FILE" <<CREDS

# Initial admin user — printed on every deploy banner.
# Rotate the password via scripts/getUser.py and update this file by hand.
ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
ADMIN_EMAIL=${ADMIN_EMAIL}
CREDS
    log "Admin credentials appended to homelabsetup/.homelab-credentials"
  fi

  echo ""
  echo -e "${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${YELLOW}║  ADMIN LOGIN — TestLookup Dashboard                ║${NC}"
  echo -e "${YELLOW}╠══════════════════════════════════════════════════════╣${NC}"
  echo -e "${YELLOW}║${NC}  URL:      http://testlookup.local"
  echo -e "${YELLOW}║${NC}  Username: ${ADMIN_USERNAME}"
  echo -e "${YELLOW}║${NC}  Password: ${ADMIN_PASSWORD}"
  echo -e "${YELLOW}║${NC}  Email:    ${ADMIN_EMAIL}"
  echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""

  if echo "$ADMIN_OUTPUT" | grep -q "already exists"; then
    warn "Admin user already existed — the password above is what the deploy"
    warn "script believes is current (from .homelab-credentials or env). If it"
    warn "was rotated out-of-band, reset it with:"
    warn "  kubectl -n $NAMESPACE exec -i deployment/testlookup-backend -- \\"
    warn "    env ADMIN_PASSWORD='$ADMIN_PASSWORD' python < scripts/getUser.py"
  fi
fi

# ── Step 11: Final Verification ────────────────────────────
header "Step 11 — Verification"

log "Pod status:"
echo ""
kubectl -n "$NAMESPACE" get pods -o wide 2>/dev/null || true
echo ""

log "Services:"
echo ""
kubectl -n "$NAMESPACE" get svc 2>/dev/null || true
echo ""

log "Ingress:"
echo ""
kubectl -n "$NAMESPACE" get ingress 2>/dev/null || true
echo ""

log "PVCs (local-path storage):"
echo ""
kubectl -n "$NAMESPACE" get pvc 2>/dev/null || true
echo ""

# Health check
TRAEFIK_IP=$(kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
if [ -n "$TRAEFIK_IP" ]; then
  log "Testing health endpoint via Traefik IP (HTTP, ingress is plain-text)..."
  HEALTH=$(curl -sf --max-time 5 -H "Host: testlookup.local" "http://${TRAEFIK_IP}/health/live" 2>/dev/null || echo "")
  if [ -n "$HEALTH" ]; then
    log "Health check passed: $HEALTH"
  else
    warn "Health check did not respond. Backend may still be starting (running migrations)."
    warn "Check with: kubectl -n testlookup logs deployment/testlookup-backend --tail=30"
  fi
fi

# ── Summary ─────────────────────────────────────────────────
header "Deployment Complete"

echo -e "${GREEN}TestLookup has been deployed to your K3s homelab cluster.${NC}"
echo ""
echo "  Dashboard:      http://testlookup.local"
echo "  API Docs:       http://testlookup.local/docs"
echo "  Health Check:   http://testlookup.local/health/live"
echo ""
echo "  Note: ingress is HTTP-only on the homelab. If a browser keeps redirecting"
echo "        to https://, clear the site's HSTS cache (chrome://net-internals/#hsts)."
echo ""
echo "Useful commands:"
echo "  kubectl -n testlookup get pods           # Check pod status"
echo "  kubectl -n testlookup logs -f deploy/testlookup-backend  # Stream backend logs"
echo "  kubectl -n testlookup top pods            # Resource usage"
echo ""
echo "To update after code changes:"
echo "  $0 --skip-registry --skip-models"
echo ""
