#!/usr/bin/env bash
# ============================================================================
# TestLookup — Multi-cloud Kubernetes deployment helper
#
# Usage:
#   scripts/deploy-k8s.sh --cloud=<aws|gcp|azure|self-hosted> [options]
#
# Options:
#   --cloud=<name>         Required. Selects the kustomize overlay.
#   --image-tag=<tag>      Override image tag (default: 0.0.1)
#   --registry=<url>       Override container registry prefix (e.g.
#                          123.dkr.ecr.us-east-1.amazonaws.com).
#   --namespace=<ns>       Override target namespace (default: testlookup).
#   --dry-run              Print rendered manifests without applying.
#   --skip-secrets-check   Don't fail if testlookup-secrets is missing.
#
# Prerequisites (per cloud — see docs/deployment/*.md for full setup):
#   - kubectl + cluster context already configured
#   - testlookup-secrets Secret created in the target namespace, with keys:
#         APP_SECRET_KEY JWT_SECRET_KEY DATABASE_URL POSTGRES_PASSWORD
#         MINIO_ACCESS_KEY MINIO_SECRET_KEY MONGO_URI WEBHOOK_SECRET
#         MCP_USERNAME MCP_PASSWORD
#   - Container images pushed to the registry referenced by the overlay
# ============================================================================

set -euo pipefail

CLOUD=""
IMAGE_TAG=""
REGISTRY=""
NAMESPACE="testlookup"
DRY_RUN=0
SKIP_SECRETS_CHECK=0

print_usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --cloud=*)              CLOUD="${arg#*=}" ;;
    --image-tag=*)          IMAGE_TAG="${arg#*=}" ;;
    --registry=*)           REGISTRY="${arg#*=}" ;;
    --namespace=*)          NAMESPACE="${arg#*=}" ;;
    --dry-run)              DRY_RUN=1 ;;
    --skip-secrets-check)   SKIP_SECRETS_CHECK=1 ;;
    -h|--help)              print_usage; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; print_usage; exit 1 ;;
  esac
done

case "$CLOUD" in
  aws|aws-eks)        OVERLAY="k8s/overlays/aws-eks" ;;
  gcp|gcp-gke|gke)    OVERLAY="k8s/overlays/gcp-gke" ;;
  azure|azure-aks|aks) OVERLAY="k8s/overlays/azure-aks" ;;
  self-hosted|on-prem|k3s|generic) OVERLAY="k8s/overlays/self-hosted" ;;
  "")
    echo "ERROR: --cloud is required" >&2
    print_usage
    exit 1
    ;;
  *)
    echo "ERROR: unknown cloud '$CLOUD'. Valid: aws, gcp, azure, self-hosted" >&2
    exit 1
    ;;
esac

if [[ ! -d "$OVERLAY" ]]; then
  echo "ERROR: overlay directory not found: $OVERLAY" >&2
  echo "       Run this script from the repo root." >&2
  exit 1
fi

# ── Pre-flight checks ────────────────────────────────────────────────────────
command -v kubectl >/dev/null || { echo "ERROR: kubectl not found in PATH"; exit 1; }
command -v kustomize >/dev/null 2>&1 || true   # kubectl has a built-in fallback

CTX="$(kubectl config current-context 2>/dev/null || echo "")"
if [[ -z "$CTX" ]]; then
  echo "ERROR: no current kubectl context. Run 'kubectl config use-context <name>' first." >&2
  exit 1
fi
echo "==> Target cluster: $CTX"
echo "==> Overlay:        $OVERLAY"
echo "==> Namespace:      $NAMESPACE"

# Ensure namespace exists (idempotent)
if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "==> [dry-run] Would create namespace: $NAMESPACE"
  else
    echo "==> Creating namespace: $NAMESPACE"
    kubectl create namespace "$NAMESPACE"
  fi
fi

# Secret check — testlookup-secrets must exist; the deploy will fail at pod
# startup otherwise. See docs/deployment/<cloud>.md for the kubectl-create
# command tailored to each cloud's secret manager integration.
if [[ $SKIP_SECRETS_CHECK -eq 0 && $DRY_RUN -eq 0 ]]; then
  if ! kubectl -n "$NAMESPACE" get secret testlookup-secrets >/dev/null 2>&1; then
    cat >&2 <<EOF
ERROR: Secret testlookup-secrets is missing in namespace '$NAMESPACE'.

       Create it before deploying. Quick template (replace the placeholders):

         kubectl -n $NAMESPACE create secret generic testlookup-secrets \\
           --from-literal=APP_SECRET_KEY=\$(openssl rand -hex 32) \\
           --from-literal=JWT_SECRET_KEY=\$(openssl rand -hex 32) \\
           --from-literal=DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/testlookup' \\
           --from-literal=POSTGRES_PASSWORD='REPLACE' \\
           --from-literal=MINIO_ACCESS_KEY='REPLACE' \\
           --from-literal=MINIO_SECRET_KEY='REPLACE' \\
           --from-literal=MONGO_URI='mongodb://user:pass@host:27017/testlookup_logs?authSource=admin' \\
           --from-literal=WEBHOOK_SECRET=\$(openssl rand -hex 32) \\
           --from-literal=MCP_USERNAME='mcp_service' \\
           --from-literal=MCP_PASSWORD=\$(openssl rand -base64 16 | tr -d '=/+' | head -c 16)

       For cloud-native secret stores (AWS Secrets Manager, GCP Secret Manager,
       Azure Key Vault), see docs/deployment/<cloud>.md.

       Pass --skip-secrets-check to bypass this check (NOT recommended).
EOF
    exit 1
  fi
fi

# ── Optional: override image registry on the fly ─────────────────────────────
TMP_OVERLAY=""
if [[ -n "$REGISTRY" || -n "$IMAGE_TAG" ]]; then
  TMP_OVERLAY="$(mktemp -d)/overlay"
  cp -r "$OVERLAY" "$TMP_OVERLAY"
  pushd "$TMP_OVERLAY" >/dev/null
  for img in testlookup/backend testlookup/frontend testlookup/mcp; do
    new_args=()
    if [[ -n "$REGISTRY" ]]; then
      new_args+=("${img}=${REGISTRY%/}/${img}")
    fi
    if [[ -n "$IMAGE_TAG" ]]; then
      new_args+=(":${IMAGE_TAG}")
    fi
    if [[ -n "$REGISTRY" && -n "$IMAGE_TAG" ]]; then
      kubectl kustomize edit set image "${img}=${REGISTRY%/}/${img}:${IMAGE_TAG}" 2>/dev/null \
        || kustomize edit set image "${img}=${REGISTRY%/}/${img}:${IMAGE_TAG}"
    elif [[ -n "$REGISTRY" ]]; then
      kustomize edit set image "${img}=${REGISTRY%/}/${img}"
    elif [[ -n "$IMAGE_TAG" ]]; then
      kustomize edit set image "${img}:${IMAGE_TAG}"
    fi
  done
  popd >/dev/null
  OVERLAY="$TMP_OVERLAY"
fi

# ── Apply ────────────────────────────────────────────────────────────────────
if [[ $DRY_RUN -eq 1 ]]; then
  echo "==> [dry-run] Rendered manifests:"
  kubectl kustomize "$OVERLAY"
  exit 0
fi

echo "==> Applying overlay..."
kubectl apply -k "$OVERLAY"

echo "==> Waiting for backend rollout..."
kubectl -n "$NAMESPACE" rollout status deployment/testlookup-backend --timeout=300s

echo "==> Waiting for frontend rollout..."
kubectl -n "$NAMESPACE" rollout status deployment/testlookup-frontend --timeout=180s

echo ""
echo "==> Deploy complete."
echo "    Pods:      kubectl -n $NAMESPACE get pods"
echo "    Ingress:   kubectl -n $NAMESPACE get ingress"
echo "    Logs:      kubectl -n $NAMESPACE logs -l app=testlookup-backend --tail=50"

# Cleanup tmp overlay
[[ -n "$TMP_OVERLAY" ]] && rm -rf "$(dirname "$TMP_OVERLAY")"
