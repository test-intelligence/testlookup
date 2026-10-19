#!/usr/bin/env bash
# ============================================================
# deploy-homelab.sh — Deploy TestLookup to a K3s cluster (single node or more)
# ============================================================
# The public "single-node K3s" path: build the three TestLookup images, push
# them to an in-cluster registry, and apply k8s/overlays/homelab. Works on one
# K3s node (the default) and on a multi-node K3s cluster. Guide:
# docs/operations/deploy-k3s.md.
#
# Prerequisites:
#   - A K3s cluster with the built-in local-path StorageClass and Traefik
#     (homelabsetup/bootstrap-homelab.sh can create one over SSH)
#   - kubectl configured and pointing at the cluster
#   - Docker or Podman on the build machine, trusting the registry below as an
#     insecure (plain-HTTP) registry
#
# Site configuration (NOT part of the repository):
#   Every site-specific value — the host name, the registry address, extra
#   kustomize patches — comes from a local env file. Point
#   TESTLOOKUP_HOMELAB_ENV at it, or keep it at
#   ${XDG_CONFIG_HOME:-$HOME/.config}/testlookup/homelab.env. Variables already
#   set in the environment win over the file. homelabsetup/homelab.env.example
#   lists every variable; with no file at all the defaults below apply
#   (host testlookup.localhost, registry registry.local:30500).
#
# Usage:
#   ./homelabsetup/deploy-homelab.sh [--skip-registry] [--skip-build] [--skip-models]
#
# Flags:
#   --skip-registry  Skip registry deployment (already running)
#   --skip-build     Skip Docker image builds (images already pushed)
#   --skip-models    Skip Ollama model pull (already downloaded)
#   --skip-dns       Skip DNS/hosts file reminder
#   --skip-registry-prune  Keep every old TestLookup image in the registry
#   --keep-builds=N  After a successful deploy, keep the newest N builds of each
#                    TestLookup image in the registry (default 3); older ones
#                    are pruned by homelabsetup/prune-registry.sh
#   --teardown       Delete the entire deployment (keeps PVCs)
#   --teardown-all   Delete namespace + all data (DESTRUCTIVE)
#
# First install: the script generates every secret (database, object store,
# signing keys) and a random password for the initial admin account, prints
# them ONCE and saves them to the credentials file (K3S_CREDENTIALS_FILE).
# Re-runs never regenerate a secret that exists and never change an existing
# admin's password.
# ============================================================

set -euo pipefail

# ── Configuration ───────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE_CONFIG_FILE=""
# shellcheck source=homelabsetup/lib-site-config.sh
. "$REPO_ROOT/homelabsetup/lib-site-config.sh"
load_site_config
# The public host the ingress answers on (plain HTTP). *.localhost resolves to
# the loopback address on most systems; any other name needs a DNS or hosts
# entry pointing at the Traefik LoadBalancer IP (Step 9 prints it).
K3S_HOST="${K3S_HOST:-testlookup.localhost}"
# The registry the cluster pulls TestLookup images from. Must match the
# containerd mirror in /etc/rancher/k3s/registries.yaml on every node.
REGISTRY="${K3S_REGISTRY:-registry.local:30500}"
# Optional second address for the same registry (e.g. <node-ip>:30500), tried
# when the registry host name does not resolve on the build machine.
REGISTRY_FALLBACK="${K3S_REGISTRY_FALLBACK:-}"
# Fixed: k8s/overlays/homelab places every resource in this namespace.
NAMESPACE="testlookup"
# A directory of extra kustomize patches (*.yaml, applied in name order) for
# site-specific settings: an AI provider, resource limits, pinned images. Each
# file is a strategic-merge patch that names the kind, metadata.name and
# metadata.namespace (testlookup) of the resource it changes.
K3S_PATCHES_DIR="${K3S_PATCHES_DIR:-}"
CREDS_FILE="${K3S_CREDENTIALS_FILE:-$REPO_ROOT/homelabsetup/.homelab-credentials}"
# The per-deploy overlay rendered from the site configuration (gitignored).
RENDER_DIR="$REPO_ROOT/homelabsetup/.render"
# Image names the tracked overlay rewrites the app images to; the rendered
# overlay maps them onto ${REGISTRY} and this run's build tag.
OVERLAY_IMAGE_PREFIX="registry.local:30500/testlookup"
LLM_PULL_MODEL="${K3S_LLM_MODEL:-qwen2.5:7b-instruct}"
EMBED_PULL_MODEL="${K3S_EMBEDDING_MODEL:-nomic-embed-text:v1.5}"
BACKEND_POD_SELECTOR="app=testlookup-backend,app.kubernetes.io/component=api"
MINIO_ACCESS="${K3S_S3_ACCESS_KEY:-testlookup_minio}"
# Step 5b re-checks the image authority this long before failing the deploy.
IMAGE_AUTHORITY_TIMEOUT_SECONDS="${IMAGE_AUTHORITY_TIMEOUT_SECONDS:-120}"
IMAGE_AUTHORITY_POLL_SECONDS="${IMAGE_AUTHORITY_POLL_SECONDS:-5}"

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
CONTAINER_ENGINE="${CONTAINER_ENGINE:-docker}"
KUBECTL_INSECURE_SKIP_TLS_VERIFY="${KUBECTL_INSECURE_SKIP_TLS_VERIFY:-false}"
# Bypass the pre-apply K3s containerd mirror check (incident 2026-05-17).
# Default off — the check catches the "every locally-built image
# ImagePullBackOffs because /etc/rancher/k3s/registries.yaml is missing"
# failure mode that hit us 4 times in a row. Set to true to opt out
# (e.g. when running against a non-K3s cluster).
SKIP_MIRROR_CHECK=false
# Registry pruning after a successful deploy (2026-10-18: ~570 builds per image,
# 48.8 GB, had piled up because nothing ever removed an old build).
SKIP_REGISTRY_PRUNE=false
KEEP_BUILDS=3
TEARDOWN=false
TEARDOWN_ALL=false

for arg in "$@"; do
  case $arg in
    --skip-registry)     SKIP_REGISTRY=true ;;
    --skip-build)        SKIP_BUILD=true ;;
    --skip-models)       SKIP_MODELS=true ;;
    --skip-dns)          SKIP_DNS=true ;;
    --skip-mirror-check) SKIP_MIRROR_CHECK=true ;;
    --skip-registry-prune) SKIP_REGISTRY_PRUNE=true ;;
    --keep-builds=*)     KEEP_BUILDS="${arg#*=}" ;;
    --teardown)          TEARDOWN=true ;;
    --teardown-all)      TEARDOWN_ALL=true ;;
    *)                   echo -e "${RED}Unknown flag: $arg${NC}"; exit 1 ;;
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

# Run one command on a node through `kubectl debug node/<node>` (a privileged
# pod with the node's filesystem at /host) and return THAT command's exit code;
# 125 when the check itself could not run. Then delete the debug pod by name.
#
# Without -i, `kubectl debug` returns as soon as the pod is CREATED, with exit 0,
# whatever the command does: the registry-mirror precheck passed on every node
# for that reason alone, and its cleanup (`-l created-by=kubectl-debug`, a label
# these pods do not carry) never matched — 735 dead node-debugger pods had piled
# up by 2026-10-18. So: find the pod this call created, wait for it to finish,
# read the container's exit code, delete exactly that pod.
node_debug_run() {
  local node=$1; shift
  local before after pod phase code
  before="$(kubectl -n default get pods -o name 2>/dev/null | sort)"
  kubectl debug node/"$node" --image=busybox:1.36 --quiet --profile=sysadmin \
    -- "$@" >/dev/null 2>&1 || return 125
  after="$(kubectl -n default get pods -o name 2>/dev/null | sort)"
  pod="$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") \
    | grep "^pod/node-debugger-${node}-" | head -1)"
  [ -n "$pod" ] || return 125
  phase=""
  for _ in $(seq 1 60); do
    phase="$(kubectl -n default get "$pod" -o jsonpath='{.status.phase}' 2>/dev/null)"
    case "$phase" in Succeeded|Failed) break ;; esac
    sleep 2
  done
  code="$(kubectl -n default get "$pod" \
    -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}' 2>/dev/null)"
  kubectl -n default delete "$pod" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
  case "$code" in
    ''|*[!0-9]*) return 125 ;;
  esac
  return "$code"
}

cleanup_generated_build_inputs() {
  local repo_root=$1
  rm -rf -- "$repo_root/backend/__client_sdks_staged"
  rm -f -- \
    "$repo_root/backend/certs/mitm-ca.crt" \
    "$repo_root/frontend/certs/mitm-ca.crt" \
    "$repo_root/mcp/certs/mitm-ca.crt"
}

require_clean_build_inputs() {
  local repo_root=$1 extra_inputs
  git -C "$repo_root" diff --quiet --ignore-submodules -- \
    || { echo "Tracked working-tree changes exist." >&2; return 1; }
  git -C "$repo_root" diff --cached --quiet --ignore-submodules -- \
    || { echo "Staged changes exist." >&2; return 1; }
  extra_inputs="$(git -C "$repo_root" status --porcelain \
    --untracked-files=all --ignored=matching -- \
    backend frontend mcp client)"
  [ -z "$extra_inputs" ] \
    || { echo "Untracked or ignored application build inputs exist." >&2; return 1; }
}

registry_manifest_digest() {
  local registry=$1 image=$2 tag=$3 headers media_type digest
  headers=$(curl -fsSI \
    -H 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' \
    "http://${registry}/v2/testlookup/${image}/manifests/${tag}" 2>/dev/null) \
    || return 1
  headers=$(printf '%s' "$headers" | tr -d '\r')
  media_type=$(printf '%s\n' "$headers" \
    | awk 'tolower($1) == "content-type:" { print tolower($2); exit }' \
    | cut -d';' -f1)
  case "$media_type" in
    application/vnd.oci.image.manifest.v1+json|application/vnd.docker.distribution.manifest.v2+json) ;;
    *) return 1 ;;
  esac
  digest=$(printf '%s\n' "$headers" \
    | awk 'tolower($1) == "docker-content-digest:" { print $2; exit }')
  printf '%s\n' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' || return 1
  printf '%s\n' "$digest"
}

# Every LIVE pod carrying the Deployment's labels must be Ready on the exact
# pushed digest. Two kinds of pod are skipped because they serve nothing:
#   - terminating pods (deletionTimestamp set): the old ReplicaSet going away;
#   - finished pods (phase Succeeded or Failed): a completed Job pod built from
#     the backend image, or an evicted / node-shutdown pod. These stay listed,
#     READY=false, until garbage-collected and are never Service endpoints.
# Anything else, whatever owns it, receives traffic through the Service
# selector, so it must match. On failure IMAGE_AUTHORITY_FAILURE names the pod.
verify_deployment_image() {
  local deployment=$1 expected_image=$2 expected_digest=$3 component pod_rows
  local name phase image ready image_id deleting extra actual_digest active_pods=0
  IMAGE_AUTHORITY_FAILURE="cannot read the app.kubernetes.io/component label of deployment ${deployment}"
  component=$(kubectl -n "$NAMESPACE" get deployment "$deployment" \
    -o jsonpath='{.spec.template.metadata.labels.app\.kubernetes\.io/component}' \
    2>/dev/null) || return 1
  [ -n "$component" ] || return 1
  IMAGE_AUTHORITY_FAILURE="no live pod matches app=${deployment},app.kubernetes.io/component=${component}"
  pod_rows=$(kubectl -n "$NAMESPACE" get pod \
    -l "app=${deployment},app.kubernetes.io/component=${component}" \
    -o custom-columns='NAME:.metadata.name,PHASE:.status.phase,IMAGE:.spec.containers[0].image,READY:.status.containerStatuses[0].ready,IMAGE_ID:.status.containerStatuses[0].imageID,DELETING:.metadata.deletionTimestamp' \
    --no-headers 2>/dev/null) || return 1
  [ -n "$pod_rows" ] || return 1
  while read -r name phase image ready image_id deleting extra; do
    IMAGE_AUTHORITY_FAILURE="unparseable pod row for ${name:-<empty>}"
    [ -z "${extra:-}" ] || return 1
    [ "$deleting" = "<none>" ] || continue
    case "$phase" in Succeeded|Failed) continue ;; esac
    active_pods=$((active_pods + 1))
    IMAGE_AUTHORITY_FAILURE="pod ${name} phase=${phase} ready=${ready} image=${image} imageID=${image_id}"
    [ "$image" = "$expected_image" ] || return 1
    [ "$ready" = "true" ] || return 1
    actual_digest="${image_id##*@}"
    [ "$actual_digest" = "$expected_digest" ] || return 1
  done <<< "$pod_rows"
  IMAGE_AUTHORITY_FAILURE="no live pod matches app=${deployment},app.kubernetes.io/component=${component}"
  [ "$active_pods" -gt 0 ]
}

# Re-run verify_deployment_image for a bounded window. Right after the nine
# rollouts a correct pod can still be briefly un-Ready: the HPA (re-created by
# the apply) adds a replica, or a readiness probe flaps while every Deployment
# restarts at once. A pod that stays wrong still fails once the window closes.
wait_for_deployment_image() {
  local deadline=$((SECONDS + IMAGE_AUTHORITY_TIMEOUT_SECONDS))
  until verify_deployment_image "$@"; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep "$IMAGE_AUTHORITY_POLL_SECONDS"
  done
}

verify_serving_revision() {
  local ingress_ip=$1 expected_revision=$2 host=${3:-${K3S_HOST:-testlookup.localhost}}
  local payload revision
  payload=$(curl -fsS --max-time 10 -H "Host: ${host}" \
    "http://${ingress_ip}/health/version") || return 1
  revision=$(printf '%s\n' "$payload" \
    | sed -n 's/.*"revision"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  [ "$revision" = "$expected_revision" ]
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

# An existing install keeps serving the host it was installed with. If the
# site configuration did not load (or names another host), stop BEFORE anything
# is built or applied rather than silently moving the ingress to a new name.
# Set ALLOW_HOST_CHANGE=true to move it on purpose.
check_existing_host() {
  local wanted=$1 existing
  existing=$(kubectl -n "$NAMESPACE" get ingress testlookup-traefik-ingress \
    -o jsonpath='{.spec.rules[0].host}' 2>/dev/null || true)
  if [ -n "$existing" ] && [ "$existing" != "$wanted" ] \
     && [ "${ALLOW_HOST_CHANGE:-false}" != true ]; then
    echo "[x] This cluster already serves TestLookup on host '${existing}', but this run" >&2
    echo "    would move it to '${wanted}'. Load your site configuration" >&2
    echo "    (TESTLOOKUP_HOMELAB_ENV=<file> with K3S_HOST=${existing}), or set" >&2
    echo "    ALLOW_HOST_CHANGE=true to move the ingress deliberately." >&2
    return 1
  fi
}

# Write the per-deploy overlay: the tracked k8s/overlays/homelab plus this
# run's images, the site host, and any site patches. Nothing tracked is edited.
render_overlay() {
  local render_dir=$1 build_tag=$2 patch name patches_yaml=""
  rm -rf -- "$render_dir"
  mkdir -p "$render_dir"
  cat > "$render_dir/site-ingress-host.yaml" <<EOF
- op: replace
  path: /spec/rules/0/host
  value: ${K3S_HOST}
EOF
  cat > "$render_dir/site-config-host.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: testlookup-config
  namespace: ${NAMESPACE}
data:
  CORS_ORIGINS: "http://${K3S_HOST}"
  PUBLIC_BASE_URL: "http://${K3S_HOST}"
EOF
  if [ -n "$K3S_PATCHES_DIR" ]; then
    local patches_dir="$K3S_PATCHES_DIR"
    # A Windows path (C:\... or C:/...) from the site file, under Git Bash.
    if command -v cygpath >/dev/null 2>&1; then
      patches_dir="$(cygpath -u "$patches_dir")"
    fi
    [ -d "$patches_dir" ] || { echo "[x] K3S_PATCHES_DIR '$K3S_PATCHES_DIR' is not a directory." >&2; return 1; }
    for patch in "$patches_dir"/*.yaml; do
      [ -f "$patch" ] || continue
      name="site-patch-$(basename "$patch")"
      cp -f -- "$patch" "$render_dir/$name"
      patches_yaml="${patches_yaml}  - path: ${name}
"
    done
  fi
  cat > "$render_dir/kustomization.yaml" <<EOF
# Generated by homelabsetup/deploy-homelab.sh for one deploy. Do not edit:
# change the site configuration (TESTLOOKUP_HOMELAB_ENV) instead.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../k8s/overlays/homelab
images:
  - name: ${OVERLAY_IMAGE_PREFIX}/backend
    newName: ${REGISTRY}/testlookup/backend
    newTag: ${build_tag}
  - name: ${OVERLAY_IMAGE_PREFIX}/frontend
    newName: ${REGISTRY}/testlookup/frontend
    newTag: ${build_tag}
  - name: ${OVERLAY_IMAGE_PREFIX}/mcp
    newName: ${REGISTRY}/testlookup/mcp
    newTag: ${build_tag}
patches:
  - path: site-ingress-host.yaml
    target:
      kind: Ingress
      name: testlookup-traefik-ingress
  - path: site-config-host.yaml
${patches_yaml}
EOF
}

# ── TLS-interception CA staging ─────────────────────────────
# An HTTPS-inspecting middlebox on the build host (a corporate proxy or an
# endpoint-security product's TLS scanning) re-signs traffic with a private root CA. The
# host trusts it, but the build containers (python:3.11-slim, node:20-alpine)
# do not, so in-container pip/npm die with:
#   "unable to get local issuer certificate".
# We export that root CA from the host trust store into each build context's
# certs/ dir and flip INSTALL_EXTRA_CA=1 so the Dockerfiles merge it. When no
# interception is detected we leave INSTALL_EXTRA_CA at its 0 default and the
# build uses the public roots unchanged (CI behaviour).
#
# Sources, in order:
#   1. EXTRA_CA_CERT=/path/to/root.crt  — explicit override (any OS).
#   2. Windows host  — auto-detect by probing pypi.org's cert issuer via
#      PowerShell; if it's a private (non-public) CA, export it from the
#      Windows cert store.
CA_BUILD_ARG=()
stage_tls_ca() {
  local staged=""

  if [ -n "${EXTRA_CA_CERT:-}" ] && [ -f "${EXTRA_CA_CERT}" ]; then
    staged="${EXTRA_CA_CERT}"
    log "Using TLS CA from \$EXTRA_CA_CERT: ${EXTRA_CA_CERT}"
  elif command -v powershell.exe >/dev/null 2>&1; then
    # Probe the cert pypi.org presents; if a private middlebox re-signed it,
    # export that issuer's root from the Windows store to ./.tls-mitm-ca.crt.
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
      $ErrorActionPreference = "SilentlyContinue"
      try {
        $tcp = [Net.Sockets.TcpClient]::new("pypi.org", 443)
        $ssl = [Net.Security.SslStream]::new($tcp.GetStream(), $false, ({ $true }))
        $ssl.AuthenticateAsClient("pypi.org")
        $leaf = [Security.Cryptography.X509Certificates.X509Certificate2]$ssl.RemoteCertificate
        $issuer = $leaf.Issuer
        $ssl.Dispose(); $tcp.Close()
      } catch { exit 0 }
      if (-not $issuer) { exit 0 }
      # Public CAs => no interception; nothing to export.
      if ($issuer -match "Let.s Encrypt|DigiCert|Google Trust|Amazon|Sectigo|GlobalSign|ISRG|Microsoft|USERTrust|Baltimore") { exit 0 }
      $ca = Get-ChildItem Cert:\LocalMachine\Root, Cert:\CurrentUser\Root, Cert:\LocalMachine\CA, Cert:\CurrentUser\CA |
            Where-Object { $_.Subject -eq $issuer } | Select-Object -First 1
      if (-not $ca) { exit 0 }
      $pem = "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($ca.RawData, "InsertLineBreaks") + "`n-----END CERTIFICATE-----`n"
      Set-Content -Path ".tls-mitm-ca.crt" -Value $pem -Encoding ascii -NoNewline
    ' >/dev/null 2>&1 || true
    if [ -s "$REPO_ROOT/.tls-mitm-ca.crt" ]; then
      staged="$REPO_ROOT/.tls-mitm-ca.crt"
    fi
  fi

  if [ -z "$staged" ]; then
    log "No TLS-interception CA detected — building against public roots."
    return 0
  fi

  warn "TLS-interception detected — staging host root CA so in-container pip/npm trust it."
  local d
  for d in backend frontend mcp; do
    mkdir -p "$REPO_ROOT/$d/certs"
    cp -f "$staged" "$REPO_ROOT/$d/certs/mitm-ca.crt"
  done
  rm -f "$REPO_ROOT/.tls-mitm-ca.crt"
  CA_BUILD_ARG=(--build-arg INSTALL_EXTRA_CA=1)
  log "Staged interception CA into backend/ frontend/ mcp/ certs/ (INSTALL_EXTRA_CA=1)."
}

# ── Teardown mode ───────────────────────────────────────────
check_command kubectl

# Homelabs commonly use a private/self-signed Kubernetes API certificate.
# Keep the bypass explicit and process-local; never rewrite the user's
# kubeconfig or copy its credentials into the repository. Define this before
# teardown handling so every kubectl path honors the same explicit setting.
if [ "$KUBECTL_INSECURE_SKIP_TLS_VERIFY" = true ]; then
  KUBECTL_EXECUTABLE="$(command -v kubectl)"
  kubectl() {
    "$KUBECTL_EXECUTABLE" --insecure-skip-tls-verify=true "$@"
  }
fi

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

check_command "$CONTAINER_ENGINE"

log "Checking kubectl cluster access..."
kubectl get nodes >/dev/null 2>&1 || error "Cannot reach K3s cluster. Check your kubeconfig."

NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
log "Cluster has $NODE_COUNT node(s)"

log "Checking container engine (${CONTAINER_ENGINE})..."
"$CONTAINER_ENGINE" info >/dev/null 2>&1 || error "$CONTAINER_ENGINE is not running. Start the container engine first."

log "Checking local-path StorageClass..."
kubectl get storageclass local-path >/dev/null 2>&1 || error "local-path StorageClass not found. Is this a K3s cluster?"

if [ -n "$SITE_CONFIG_FILE" ]; then
  log "Site configuration: ${SITE_CONFIG_FILE}"
else
  log "No site configuration file — using the defaults (host ${K3S_HOST}, registry ${REGISTRY})."
fi
check_existing_host "$K3S_HOST" || error "Refusing to change the host of an existing install."

log "All preflight checks passed."

# ── Helper: check registry reachability ─────────────────────
check_registry() {
  # Try the registry name first, then the optional fallback address.
  if curl -sf --max-time 5 "http://${REGISTRY}/v2/_catalog" >/dev/null 2>&1; then
    return 0
  elif [ -n "$REGISTRY_FALLBACK" ] \
       && curl -sf --max-time 5 "http://${REGISTRY_FALLBACK}/v2/_catalog" >/dev/null 2>&1; then
    warn "'${REGISTRY%%:*}' does not resolve here, but the registry answers at ${REGISTRY_FALLBACK}"
    warn "Add a hosts-file entry mapping ${REGISTRY%%:*} to that address."
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
      echo "  2. '${REGISTRY%%:*}' does not resolve on this machine"
      echo "     Fix: add a hosts-file entry mapping it to a node's IP address"
      echo "  3. The container engine does not trust '${REGISTRY}' as an insecure registry"
      echo "     Fix: add it to insecure-registries (Docker) or registries.conf (Podman)"
      echo "  4. A firewall blocks the registry NodePort on the nodes"
      echo ""
      if [ -n "$REGISTRY_FALLBACK" ]; then
        log "Trying the fallback address (${REGISTRY_FALLBACK})..."
        if curl -sf --max-time 5 "http://${REGISTRY_FALLBACK}/v2/_catalog" 2>/dev/null; then
          echo ""
          log "Registry IS reachable via ${REGISTRY_FALLBACK}. The issue is name resolution only."
          warn "Docker push will fail without it. Fix it before continuing."
        else
          warn "Registry is NOT reachable via ${REGISTRY_FALLBACK} either. Check firewall / pod status."
        fi
      fi
      echo ""
      read -p "Press Enter after fixing the issue, or Ctrl+C to abort..."
    fi
  else
    # The registry's data lives on one node (hostPath, created on demand).
    REGISTRY_NODE="${K3S_REGISTRY_NODE:-$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)}"
    [ -n "$REGISTRY_NODE" ] || error "Cannot pick a node for the registry; set K3S_REGISTRY_NODE."
    log "Deploying registry to K3s (data on node ${REGISTRY_NODE})..."
    cat <<EOF | kubectl apply -f -
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
    type: DirectoryOrCreate
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: ["${REGISTRY_NODE}"]
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
        kubernetes.io/hostname: ${REGISTRY_NODE}
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
    echo "  On each node run:"
    echo "    sudo mkdir -p /etc/rancher/k3s"
    echo "    sudo tee /etc/rancher/k3s/registries.yaml <<'YAML'"
    echo "    mirrors:"
    # The NodePort (30500), NOT 5000 (pod-internal): containerd must use the
    # same address the overlay's image references use.
    echo "      \"${REGISTRY}\":"
    echo "        endpoint:"
    echo "          - \"http://<a-node-ip>:${REGISTRY##*:}\""
    echo "    YAML"
    echo "    sudo systemctl restart k3s  # or k3s-agent on agent nodes"
    echo ""
    read -p "Press Enter when registry mirrors are configured on all nodes (or skip if already done)..."
  fi

  # Verify registry is reachable
  if check_registry; then
    log "Registry verified: $(curl -s "http://${REGISTRY}/v2/_catalog" 2>/dev/null || { [ -n "$REGISTRY_FALLBACK" ] && curl -s "http://${REGISTRY_FALLBACK}/v2/_catalog" 2>/dev/null; } || true)"
  else
    warn "Cannot reach the registry at ${REGISTRY}${REGISTRY_FALLBACK:+ or ${REGISTRY_FALLBACK}}."
    warn "Make '${REGISTRY%%:*}' resolve on this machine and trust '${REGISTRY}' as an insecure registry."
    read -p "Press Enter to continue anyway, or Ctrl+C to abort..."
  fi
else
  log "Skipping registry deployment (--skip-registry)"
fi

# ── Step 2: Build and Push Images ──────────────────────────
# One immutable build tag covers all three testlookup images so a single
# deploy is auditable as a single unit. The tag is later substituted into
# kustomization.yaml in place of the BUILD_TAG_PLACEHOLDER literal so
# nothing in the cluster ever runs a :latest tag.
#
# Two modes:
#   * Normal (build path) — generate a fresh ``build-YYYYMMDD-HHMMSS`` tag
#     so this deploy is auditable as a single unit.
#   * ``--skip-build`` — DO NOT invent a fresh tag. The image at that tag
#     wouldn't exist in the registry (we skipped pushing), and every pod
#     would land in ImagePullBackOff (incident 2026-05-17). Instead,
#     resolve the most recent build-* tag that exists for ALL THREE
#     images and reuse it.
if [ "$SKIP_BUILD" = false ]; then
  BUILD_TAG="build-$(date -u +%Y%m%d-%H%M%S)"
  header "Step 2 — Build and Push Container Images"
  log "Build tag for this run: ${BUILD_TAG}"

  cd "$REPO_ROOT"

  # The runtime /health/version response is the authority for which source
  # commit is serving.  A commit label would be misleading if tracked files
  # differ from that commit, so refuse such a build before staging any files.
  # Untracked files under an application build input could also change the
  # image, even though Git has no object for them. Allow unrelated local
  # evidence, but refuse untracked backend/frontend/MCP/client inputs.
  cleanup_generated_build_inputs "$REPO_ROOT"
  require_clean_build_inputs "$REPO_ROOT" \
    || error "Build inputs do not match HEAD. Use a clean worktree before deployment."
  BUILD_REVISION="$(git rev-parse HEAD)"
  BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "Embedding build provenance: revision=${BUILD_REVISION}, built_at=${BUILD_DATE}"

  # Stage a TLS-interception root CA into the build contexts when the host
  # runs an HTTPS-inspecting middlebox (a corporate or endpoint-security proxy), so
  # in-container pip/npm don't fail with "unable to get local issuer
  # certificate". No-op (and INSTALL_EXTRA_CA stays 0) on clean networks.
  stage_tls_ca

  # Determine which registry address works for docker push
  if curl -sf --max-time 3 "http://${REGISTRY}/v2/_catalog" >/dev/null 2>&1; then
    PUSH_REGISTRY="$REGISTRY"
  elif [ -n "$REGISTRY_FALLBACK" ] \
       && curl -sf --max-time 3 "http://${REGISTRY_FALLBACK}/v2/_catalog" >/dev/null 2>&1; then
    PUSH_REGISTRY="$REGISTRY_FALLBACK"
    warn "Using the fallback registry address: ${PUSH_REGISTRY} (make '${REGISTRY%%:*}' resolve for consistency)"
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
  trap 'cleanup_generated_build_inputs "$REPO_ROOT"' EXIT INT TERM

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

  log "Building backend image (${BUILD_TAG})..."
  "$CONTAINER_ENGINE" build -t "${PUSH_REGISTRY}/testlookup/backend:${BUILD_TAG}" \
    ${CA_BUILD_ARG[@]+"${CA_BUILD_ARG[@]}"} \
    --build-arg BUILD_REVISION="$BUILD_REVISION" \
    --build-arg BUILD_DATE="$BUILD_DATE" \
    --target production -f backend/Dockerfile backend/

  log "Building frontend image (${BUILD_TAG}, same-origin relative API URLs)..."
  # --pull guarantees the base node:20-alpine and nginx:alpine layers are
  # refreshed. We intentionally do NOT pass --no-cache so the npm install
  # layer (slow) stays cached when only frontend/src changes — Docker
  # invalidates downstream layers automatically when source files change.
  "$CONTAINER_ENGINE" build --pull -t "${PUSH_REGISTRY}/testlookup/frontend:${BUILD_TAG}" \
    ${CA_BUILD_ARG[@]+"${CA_BUILD_ARG[@]}"} \
    --target production -f frontend/Dockerfile frontend/

  log "Building MCP server image (${BUILD_TAG})..."
  "$CONTAINER_ENGINE" build -t "${PUSH_REGISTRY}/testlookup/mcp:${BUILD_TAG}" \
    ${CA_BUILD_ARG[@]+"${CA_BUILD_ARG[@]}"} \
    -f mcp/Dockerfile mcp/

  cleanup_generated_build_inputs "$REPO_ROOT"
  trap - EXIT INT TERM

  log "Pushing images to registry..."
  "$CONTAINER_ENGINE" push "${PUSH_REGISTRY}/testlookup/backend:${BUILD_TAG}"
  "$CONTAINER_ENGINE" push "${PUSH_REGISTRY}/testlookup/frontend:${BUILD_TAG}"
  "$CONTAINER_ENGINE" push "${PUSH_REGISTRY}/testlookup/mcp:${BUILD_TAG}"

  # Capture the registry manifest digests so every serving application pod can
  # be compared with the exact immutable manifest that was just pushed.
  BACKEND_DIGEST=$(registry_manifest_digest "$PUSH_REGISTRY" backend "$BUILD_TAG") \
    || error "Could not resolve a single-platform backend manifest digest."
  FRONTEND_DIGEST=$(registry_manifest_digest "$PUSH_REGISTRY" frontend "$BUILD_TAG") \
    || error "Could not resolve a single-platform frontend manifest digest."
  MCP_DIGEST=$(registry_manifest_digest "$PUSH_REGISTRY" mcp "$BUILD_TAG") \
    || error "Could not resolve a single-platform MCP manifest digest."
  log "Pushed manifest digests: backend=${BACKEND_DIGEST}, frontend=${FRONTEND_DIGEST}, mcp=${MCP_DIGEST}"

  log "Images pushed. Registry catalog:"
  curl -s "http://${PUSH_REGISTRY}/v2/_catalog" 2>/dev/null || warn "Could not query registry catalog"
else
  log "Skipping image builds (--skip-build) — resolving an existing tag from the registry"
  # Resolve to a tag that ACTUALLY exists in the registry. Without this
  # the deploy would substitute a fresh BUILD_TAG into kustomization.yaml
  # that has no corresponding images, putting every pod in ImagePullBackOff.
  #
  # Strategy:
  #   1. Pick the registry endpoint the same way the build path does.
  #   2. Fetch the tag list for each of the three images.
  #   3. Take the newest tag that appears in ALL THREE catalogs and that
  #      matches the build-YYYYMMDD-HHMMSS shape (skip floating tags).
  #   4. Hard-fail with a helpful message if no common tag exists — the
  #      caller probably wanted a fresh build, or the registry got wiped.
  if curl -sf --max-time 3 "http://${REGISTRY}/v2/_catalog" >/dev/null 2>&1; then
    LOOKUP_REGISTRY="$REGISTRY"
  elif [ -n "$REGISTRY_FALLBACK" ] \
       && curl -sf --max-time 3 "http://${REGISTRY_FALLBACK}/v2/_catalog" >/dev/null 2>&1; then
    LOOKUP_REGISTRY="$REGISTRY_FALLBACK"
  else
    error "Registry not reachable at ${REGISTRY}${REGISTRY_FALLBACK:+ or ${REGISTRY_FALLBACK}}. Cannot resolve a tag for --skip-build. Re-run without --skip-build to push fresh images."
  fi

  # Pure selection step, kept separate from the fetching so it can be
  # exercised without a registry (see
  # backend/tests/regression/test_deploy_tag_resolution.py).
  #
  # ``comm`` requires ASCENDING input. Feeding it ``sort -ru`` made it print
  # "input is not in sorted order" on stderr and **exit 1** — while still
  # emitting the correct answer on stdout. Under this script's
  # ``set -euo pipefail`` that non-zero status killed the command
  # substitution below, so --skip-build aborted every time and the caller's
  # "no common tag" diagnostic was unreachable. Sort ascending for comm,
  # then reverse to pick the newest.
  #
  # ``sed -n '1p'`` rather than ``head -n 1``: head exits after one line and
  # SIGPIPEs sort, which pipefail would also treat as a failure.
  _newest_common_tag() {
    local backend="$1" frontend="$2" mcp="$3" common
    common=$(comm -12 \
      <(printf '%s\n' "$backend"  | sort -u) \
      <(comm -12 \
        <(printf '%s\n' "$frontend" | sort -u) \
        <(printf '%s\n' "$mcp"      | sort -u)))
    [ -n "$common" ] || return 0
    # Tags sort lexicographically === chronologically because the format is
    # fixed-width ``build-YYYYMMDD-HHMMSS``.
    printf '%s\n' "$common" | sort -r | sed -n '1p'
  }

  _fetch_tags() {
    curl -sf --max-time 10 "http://${LOOKUP_REGISTRY}/v2/testlookup/$1/tags/list" 2>/dev/null \
      | sed -e 's/.*"tags":\[//' -e 's/\].*//' -e 's/"//g' -e 's/,/\n/g' \
      | grep -E '^build-[0-9]{8}-[0-9]{6}$' || true
  }

  _existing_common_tag() {
    _newest_common_tag "$(_fetch_tags backend)" "$(_fetch_tags frontend)" "$(_fetch_tags mcp)"
  }

  BUILD_TAG=$(_existing_common_tag)
  if [ -z "$BUILD_TAG" ]; then
    cat >&2 <<EOF
[!] --skip-build asked us to reuse an existing image tag, but no
    build-YYYYMMDD-HHMMSS tag exists for ALL THREE images
    (backend / frontend / mcp) at http://${LOOKUP_REGISTRY}.

    Likely causes:
      * The registry pod's PVC was wiped (cleanup --all, or a
        re-deploy without persistence).
      * The first deploy never completed a build/push round.
      * Tags exist for some images but not others — a partial build.

    Fix: re-run WITHOUT --skip-build so the script builds + pushes fresh
    images:

        ./homelabsetup/deploy-homelab.sh --skip-registry --skip-models
EOF
    exit 1
  fi
  log "Resolved BUILD_TAG=${BUILD_TAG} (newest tag present in all three image catalogs)"
fi

# ── Step 3: Create Namespace ───────────────────────────────
header "Step 3 — Create Namespace"

_ensure_namespace() {
  # Idempotent namespace creation.
  #
  # The previous form was:
  #
  #     if kubectl get namespace "$NS" >/dev/null 2>&1; then
  #       log "already exists"
  #     else
  #       kubectl create namespace "$NS"
  #     fi
  #
  # which conflates "the namespace does not exist" with "I could not reach
  # the API server" — both make `get` exit non-zero. A transient control-plane
  # blip therefore sent us down the else branch, `create` failed with
  # AlreadyExists, and `set -e` aborted the entire deploy after the images had
  # already been built and pushed. Observed twice on this cluster.
  #
  # `apply` is idempotent, so a transient `get` failure is harmless. A genuine
  # API outage still fails loudly, because `apply` itself errors — the fix
  # removes the false negative without introducing a false positive.
  local ns="$1"
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
}

_ensure_namespace "$NAMESPACE"
log "Namespace $NAMESPACE ready."

# ── Step 4: Generate and Apply Secrets ─────────────────────
header "Step 4 — Generate and Apply Secrets"

# Capture the deployment-owned legacy principal before any cleanup. Operators
# that intentionally repurposed it can set KEEP_LEGACY_MCP_SERVICE_ACCOUNT=true.
LEGACY_MCP_USER=$(kubectl -n "$NAMESPACE" get secret testlookup-secrets \
  -o jsonpath='{.data.MCP_USERNAME}' 2>/dev/null | base64 -d 2>/dev/null || echo "")

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
    --from-literal=WEBHOOK_SECRET="${WEBHOOK_SECRET}"

  log "Secrets created."
  echo ""
  echo -e "${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${YELLOW}║  SAVE THESE CREDENTIALS — SHOWN ONLY ONCE          ║${NC}"
  echo -e "${YELLOW}╠══════════════════════════════════════════════════════╣${NC}"
  echo -e "${YELLOW}║${NC}  PostgreSQL password: ${PG_PASSWORD}"
  echo -e "${YELLOW}║${NC}  MinIO access key:    ${MINIO_ACCESS}"
  echo -e "${YELLOW}║${NC}  MinIO secret key:    ${MINIO_SECRET}"
  echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""

  # Save credentials to a local file (gitignored by default location)
  mkdir -p "$(dirname "$CREDS_FILE")"
  ( umask 077; : > "$CREDS_FILE" )
  cat > "$CREDS_FILE" <<CREDS
# TestLookup K3s credentials — generated $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# DO NOT COMMIT THIS FILE
POSTGRES_PASSWORD=${PG_PASSWORD}
MINIO_ACCESS_KEY=${MINIO_ACCESS}
MINIO_SECRET_KEY=${MINIO_SECRET}
DATABASE_URL=${DATABASE_URL}
CREDS
  log "Credentials saved to ${CREDS_FILE} (do NOT commit this file)"
fi

# ── Step 4b: TLS Certificate (skipped — the K3s ingress is HTTP-only) ───
header "Step 4b — TLS Certificate (skipped)"

# The ingress is plain HTTP to avoid self-signed-certificate friction on every
# client (browsers, Java SDK, curl). Terminate TLS in front of Traefik, or add
# a certificate with a site patch, if the cluster is reachable beyond a
# trusted network. The overlay does not reference testlookup-tls-cert.
#
# If the Secret already exists from a prior HTTPS deploy, Step 5 below
# prunes it along with the other orphaned redirect/middleware resources.
# To re-enable HTTPS later, restore middleware-redirect-https.yaml + the
# split ingresses from git history and reinstate the openssl block.
log "Skipping TLS cert generation (ingress is HTTP-only)."

# ── Step 5: Deploy with Kustomize ──────────────────────────
header "Step 5 — Deploy with Kustomize"

# Render the per-deploy overlay: the tracked k8s/overlays/homelab (whose
# image tags stay BUILD_TAG_PLACEHOLDER at rest), this run's immutable build
# tag on ${REGISTRY}, the site host and the site patches. The cluster pulls an
# exact image and never a floating :latest, and no tracked file is edited.
log "Rendering the overlay for ${K3S_HOST} with image tag ${BUILD_TAG}..."
render_overlay "$RENDER_DIR" "$BUILD_TAG" || error "Could not render the deploy overlay."
cleanup_render_dir() { rm -rf -- "$RENDER_DIR"; }
trap cleanup_render_dir EXIT INT TERM

# Hard-fail if the rendered manifests still carry the placeholder — better
# than silently deploying an image tag that won't pull.
if kubectl kustomize "$RENDER_DIR" | grep -q "BUILD_TAG_PLACEHOLDER"; then
  warn "BUILD_TAG_PLACEHOLDER survived the render. Aborting."
  exit 1
fi

# Pre-apply K3s mirror precheck. Without /etc/rancher/k3s/registries.yaml
# on every node, containerd treats ${REGISTRY} as an unknown
# registry, falls back to HTTPS, and ImagePullBackOff hits every pod —
# the only one the bootstrap script handles, the deploy script silently
# assumes. Verify the file exists on each Ready node before applying so
# we surface the actionable fix BEFORE the cluster spends 5+ minutes
# stuck in pull retries. Detects the file via a single ``kubectl debug
# node`` exec per node (no SSH required from the build host).
log "Pre-checking K3s containerd mirror config on every node..."
mirror_missing_nodes=()
mirror_check_skipped=false
node_names="$(kubectl get nodes -o name 2>/dev/null | sed 's|^node/||')"
if [ -z "$node_names" ]; then
  warn "Could not list nodes via kubectl; skipping mirror precheck."
  mirror_check_skipped=true
else
  for node in $node_names; do
    # The grep returns 0 only when the registries.yaml contains a mirror
    # entry for ${REGISTRY} — so we catch both "file missing" and "file
    # present but wrong port" in one check. node_debug_run returns the
    # grep's own exit code (125 = the check itself could not run).
    mirror_rc=0
    node_debug_run "$node" chroot /host grep -qF "$REGISTRY" \
      /etc/rancher/k3s/registries.yaml || mirror_rc=$?
    case "$mirror_rc" in
      0)   ;;
      125) warn "Could not run the mirror check on $node; skipping it for that node." ;;
      *)   mirror_missing_nodes+=("$node") ;;
    esac
  done
fi

if [ "$mirror_check_skipped" = false ] && [ ${#mirror_missing_nodes[@]} -gt 0 ]; then
  cat >&2 <<EOF

══════════════════════════════════════════════════════════════════════
[!] K3s containerd mirror config is missing on:
$(printf "       - %s\n" "${mirror_missing_nodes[@]}")

    Without this, every locally-built TestLookup image
    (${REGISTRY}/testlookup/*) will ImagePullBackOff because
    containerd treats the registry as unknown and tries HTTPS.

    Fix — on every node, write /etc/rancher/k3s/registries.yaml:

        mirrors:
          "${REGISTRY}":
            endpoint:
              - "http://<a-node-ip>:${REGISTRY##*:}"

    then restart k3s (server) or k3s-agent (agents). Or re-run
    ./homelabsetup/bootstrap-homelab.sh (idempotent), which automates it.

    Pass --skip-mirror-check to bypass this guard (NOT recommended).
══════════════════════════════════════════════════════════════════════
EOF
  if [ "${SKIP_MIRROR_CHECK:-false}" = true ]; then
    warn "Continuing anyway because --skip-mirror-check was passed."
  else
    exit 1
  fi
fi

MIGRATION_IMAGE="${REGISTRY}/testlookup/backend:${BUILD_TAG}"
HAD_BACKEND=0
if kubectl -n "$NAMESPACE" get deployment testlookup-backend >/dev/null 2>&1; then
  HAD_BACKEND=1
  log "Migrating with the candidate image before the application rollout..."
  bash "$REPO_ROOT/scripts/run-k8s-migrations.sh" "$NAMESPACE" "$MIGRATION_IMAGE"
fi

log "Applying Kustomize overlay..."
bash "$REPO_ROOT/scripts/prepare-live-fanout-cutover.sh" "$NAMESPACE"
kubectl apply -k "$RENDER_DIR"
if [ "$HAD_BACKEND" -eq 0 ]; then
  log "Fresh install detected; running the migration after datastore creation..."
  bash "$REPO_ROOT/scripts/run-k8s-migrations.sh" "$NAMESPACE" "$MIGRATION_IMAGE"
fi
log "All resources applied and database migrations completed."

# The rendered overlay is per deploy; remove it now (the trap covers the
# failure paths).
cleanup_render_dir
trap - EXIT INT TERM

# ``kubectl apply -k`` only creates/updates resources — it does not delete
# resources that were removed from the manifest. Explicitly remove orphans
# left over from previous HTTPS-enabled deploys, otherwise the old redirect
# Ingress keeps 308'ing http://<host>/ to https://. Idempotent.
log "Pruning HTTPS-era orphans (if present)..."
kubectl -n "$NAMESPACE" delete ingress testlookup-traefik-redirect \
  --ignore-not-found=true >/dev/null
kubectl -n "$NAMESPACE" delete middleware redirect-https \
  --ignore-not-found=true >/dev/null
# The TLS Secret is harmless to leave but cleaner to drop too.
kubectl -n "$NAMESPACE" delete secret testlookup-tls-cert \
  --ignore-not-found=true >/dev/null
log "Orphan prune complete."

# ── Step 5b: Force rollout for app deployments ─────────────
# Each deploy now uses an immutable BUILD_TAG, so kubectl apply -k will
# create a new ReplicaSet automatically when the tag changes. The explicit
# rollout-restart below is kept as a belt-and-suspenders for cases where
# the tag didn't change (e.g., re-running the script without rebuilding).
if [ "$SKIP_BUILD" = false ]; then
  header "Step 5b — Force Fresh Image Pull"

  APP_DEPLOYMENTS=(
    testlookup-backend
    testlookup-frontend
    testlookup-mcp
    testlookup-worker-critical
    testlookup-worker-ingestion
    testlookup-worker-ai
    testlookup-worker-children
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
      if ! kubectl -n "$NAMESPACE" rollout status deployment/"$dep" --timeout=240s; then
        warn "$dep did not become ready in time — check 'kubectl -n $NAMESPACE describe deployment $dep'"
        # Remembered so the run cannot end with an unqualified success banner.
        # A stalled rollout means the PREVIOUS ReplicaSet is still serving, and
        # the ingress health check below will happily pass against it.
        DEPLOY_DEGRADED=true
      fi
    fi
  done
  log "App deployments rolled to image ${BUILD_TAG}."

  # ── Authority check: every Ready application pod on the expected immutable
  # tag must run the exact registry manifest just pushed. Workers and beat use
  # the backend image and therefore must match its digest too.
  APP_IMAGE_AUTHORITIES=(
    "testlookup-backend|backend|${BACKEND_DIGEST}"
    "testlookup-frontend|frontend|${FRONTEND_DIGEST}"
    "testlookup-mcp|mcp|${MCP_DIGEST}"
    "testlookup-worker-critical|backend|${BACKEND_DIGEST}"
    "testlookup-worker-ingestion|backend|${BACKEND_DIGEST}"
    "testlookup-worker-ai|backend|${BACKEND_DIGEST}"
    "testlookup-worker-children|backend|${BACKEND_DIGEST}"
    "testlookup-worker-default|backend|${BACKEND_DIGEST}"
    "testlookup-beat|backend|${BACKEND_DIGEST}"
  )
  for authority in "${APP_IMAGE_AUTHORITIES[@]}"; do
    IFS='|' read -r deployment image digest <<< "$authority"
    expected_image="${REGISTRY}/testlookup/${image}:${BUILD_TAG}"
    wait_for_deployment_image "$deployment" "$expected_image" "$digest" \
      || error "Image authority mismatch for ${deployment}: expected ${expected_image}@${digest}; ${IMAGE_AUTHORITY_FAILURE} (after ${IMAGE_AUTHORITY_TIMEOUT_SECONDS}s)."
  done

  TRAEFIK_IP=$(kubectl -n kube-system get svc traefik \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
  [ -n "$TRAEFIK_IP" ] \
    || error "Cannot verify serving revision: Traefik has no external IP."
  verify_serving_revision "$TRAEFIK_IP" "$BUILD_REVISION" \
    || error "Serving backend revision does not match ${BUILD_REVISION}."
  log "Every application pod digest and the serving backend revision match this build."
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

# ── Converge the Postgres role password to the secret ──────────────────────
# Step 4 regenerates every credential whenever testlookup-secrets is absent,
# but Postgres keeps a persistent PVC and the role password it was FIRST
# initialised with. POSTGRES_PASSWORD in the secret then describes a password
# the database does not have, and every new backend/worker pod dies with
#   asyncpg.exceptions.InvalidPasswordError: password authentication failed
# while the previous ReplicaSet keeps serving — so the deploy looks fine.
# Observed on 2026-08-15: backend CrashLoopBackOff x7, 30 worker failures.
#
# The secret is the source of truth, so make the database agree with it. This
# is idempotent: ALTER USER to the value it already has is a no-op.
#
# NB: pg_hba in this image is `trust` for local/127.0.0.1 and scram-sha-256
# only for remote hosts, so a psql check from inside the pod accepts ANY
# password and proves nothing. Do not "verify" the credential that way.
if kubectl -n "$NAMESPACE" get pod -l app=testlookup-postgres \
     --field-selector=status.phase=Running -o name 2>/dev/null | grep -q . ; then
  PG_POD=$(kubectl -n "$NAMESPACE" get pod -l app=testlookup-postgres \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  SECRET_PG_PW=$(kubectl -n "$NAMESPACE" get secret testlookup-secrets \
    -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
  PG_ROLE=$(kubectl -n "$NAMESPACE" get secret testlookup-secrets \
    -o jsonpath='{.data.DATABASE_URL}' 2>/dev/null | base64 -d 2>/dev/null \
    | sed -E 's|.*://([^:]+):.*|\1|' || echo "testlookup_user")
  if [ -n "$PG_POD" ] && [ -n "$SECRET_PG_PW" ] && [ -n "$PG_ROLE" ]; then
    case "$SECRET_PG_PW" in
      *[!A-Za-z0-9]*)
        warn "POSTGRES_PASSWORD contains characters needing SQL quoting — skipping"
        warn "role convergence. If backend pods report InvalidPasswordError, run"
        warn "ALTER USER by hand."
        ;;
      *)
        log "Converging Postgres role '$PG_ROLE' password to the secret..."
        if kubectl -n "$NAMESPACE" exec -i "$PG_POD" -- \
             env NEWPW="$SECRET_PG_PW" ROLE="$PG_ROLE" sh -c \
             'psql -U "$ROLE" -d "${POSTGRES_DB:-testlookup}" -c "ALTER USER \"$ROLE\" WITH PASSWORD '"'"'"$NEWPW"'"'"';"' \
             >/dev/null 2>&1; then
          log "Postgres role password matches the secret."
        else
          warn "Could not converge the Postgres role password. New backend pods may"
          warn "fail with InvalidPasswordError while the old ReplicaSet keeps serving."
        fi
        ;;
    esac
  fi
fi

# Wait a bit more for the backend after the explicit migration Job completed.
log "Waiting for backend (may take a minute)..."
wait_for_pods "$BACKEND_POD_SELECTOR" 180 || warn "Backend not ready yet. Check: kubectl -n testlookup logs deployment/testlookup-backend"

# ── Step 7: Create object-store buckets ────────────────────
header "Step 7 — Create object-store buckets"

# The bucket setup runs INSIDE the backend pod with the app's own settings
# (MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / bucket names), so
# the build machine needs no S3 client and no port-forward. Idempotent: an
# existing bucket is left as it is.
S3_SETUP_POD=$(kubectl -n "$NAMESPACE" get pod -l "$BACKEND_POD_SELECTOR" \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -z "$S3_SETUP_POD" ]; then
  warn "No running backend pod yet — skipping bucket creation. Run later:"
  warn "  kubectl -n $NAMESPACE exec deploy/testlookup-backend -c backend -- python /app/scripts/s3_setup.py"
elif kubectl -n "$NAMESPACE" exec "$S3_SETUP_POD" -c backend -- \
     python /app/scripts/s3_setup.py; then
  log "Object-store buckets are in place."
else
  warn "Bucket setup did not complete. Re-run it once the object store is up:"
  warn "  kubectl -n $NAMESPACE exec deploy/testlookup-backend -c backend -- python /app/scripts/s3_setup.py"
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

      for model in "$LLM_PULL_MODEL" "$EMBED_PULL_MODEL"; do
        if echo "$EXISTING_MODELS" | awk '{print $1}' | grep -qxF "$model"; then
          log "${model} already present. Skipping pull."
        else
          log "Pulling ${model} (this may take several minutes)..."
          kubectl -n "$NAMESPACE" exec "$OLLAMA_POD" -- ollama pull "$model"
        fi
      done

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

    # Verify the IP in the hosts file matches the CURRENT Traefik IP. When
    # a LoadBalancer pool is recreated the assigned IP can change, and a
    # browser keeps hitting the old one because the hosts file was never
    # updated. This catches that drift and prints the exact fix.
    case "$K3S_HOST" in
      localhost|*.localhost)
        log "${K3S_HOST} resolves to the loopback address; open it on a cluster node, or map it to ${TRAEFIK_IP} elsewhere." ;;
      *)
        HOST_RE="$(printf '%s' "$K3S_HOST" | sed 's/[.]/\\./g')"
        HOSTS_LINE="$(grep -E "^[^#]*[[:space:]]${HOST_RE}([[:space:]]|\$)" "$HOSTS_FILE" 2>/dev/null \
                       | head -1 || true)"
        HOSTS_IP="$(echo "$HOSTS_LINE" | awk '{print $1}')"
        if [ -z "$HOSTS_LINE" ]; then
          echo ""
          warn "${K3S_HOST} is NOT in your hosts file (and may not be in DNS). Add:"
          echo "    ${TRAEFIK_IP} ${K3S_HOST}"
          echo "  to /etc/hosts (Linux/macOS) or %SystemRoot%\\System32\\drivers\\etc\\hosts (Windows)."
          warn "Some browsers upgrade unknown names to HTTPS. Use http:// explicitly."
          echo ""
        elif [ "$HOSTS_IP" != "$TRAEFIK_IP" ]; then
          echo ""
          warn "Hosts file points ${K3S_HOST} at ${HOSTS_IP}, but Traefik is now on ${TRAEFIK_IP}."
          warn "Update that line to: ${TRAEFIK_IP} ${K3S_HOST}"
          echo ""
        else
          log "Hosts file already maps ${K3S_HOST} to ${TRAEFIK_IP} ✓"
        fi ;;
    esac
  else
    warn "Could not determine Traefik IP. Check: kubectl -n kube-system get svc traefik"
  fi
fi

# ── Step 10: Create Initial Admin User ─────────────────────
header "Step 10 — Create Initial Admin User"

# The initial admin account is created ONCE, on a fresh install. Its password
# is ADMIN_PASSWORD when you export one (or set it in the site configuration);
# otherwise a random one is generated here. It is shown once and appended to
# the credentials file. An admin that already exists is never modified: its
# password is not changed and not shown (reset it with scripts/getUser.py).
if [ -f "$CREDS_FILE" ] && grep -q "^ADMIN_PASSWORD=" "$CREDS_FILE"; then
  : "${ADMIN_USERNAME:=$(grep -E '^ADMIN_USERNAME=' "$CREDS_FILE" | cut -d= -f2- | head -n1)}"
  : "${ADMIN_PASSWORD:=$(grep -E '^ADMIN_PASSWORD=' "$CREDS_FILE" | cut -d= -f2- | head -n1)}"
  : "${ADMIN_EMAIL:=$(grep -E '^ADMIN_EMAIL=' "$CREDS_FILE" | cut -d= -f2- | head -n1)}"
fi

ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
ADMIN_FULL_NAME="${ADMIN_FULL_NAME:-TestLookup Admin}"
ADMIN_PASSWORD_GENERATED=false
if [ -z "${ADMIN_PASSWORD:-}" ]; then
  ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '=/+' | head -c 20)"
  ADMIN_PASSWORD_GENERATED=true
fi

log "Waiting for backend to be ready..."
ADMIN_OUTPUT=""
ADMIN_CREATED=false
if wait_for_pods "$BACKEND_POD_SELECTOR" 120; then
  BACKEND_POD=$(kubectl -n "$NAMESPACE" get pod -l "$BACKEND_POD_SELECTOR" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [ -n "$BACKEND_POD" ]; then
    log "Running scripts/createAdmin.py inside backend pod (idempotent)..."
    # Pipe the script over stdin (matches the docstring usage). `env ...` sets
    # the per-invocation values without mutating the pod's environment. The
    # script never prints the password; this script shows it once below.
    if ADMIN_OUTPUT=$(kubectl -n "$NAMESPACE" exec -i "$BACKEND_POD" -- \
        env \
          ADMIN_USERNAME="$ADMIN_USERNAME" \
          ADMIN_PASSWORD="$ADMIN_PASSWORD" \
          ADMIN_EMAIL="$ADMIN_EMAIL" \
          ADMIN_FULL_NAME="$ADMIN_FULL_NAME" \
          ADMIN_PASSWORD_GENERATED="$ADMIN_PASSWORD_GENERATED" \
          python < "$REPO_ROOT/scripts/createAdmin.py" 2>&1); then
      echo "$ADMIN_OUTPUT"
      if echo "$ADMIN_OUTPUT" | grep -q "^admin user created"; then
        ADMIN_CREATED=true
      fi
    else
      warn "createAdmin.py failed inside the backend pod. Output:"
      echo "$ADMIN_OUTPUT"
      warn "Run it manually once the backend is healthy (it generates a password when"
      warn "ADMIN_PASSWORD is unset and prints nothing secret):"
      warn "  kubectl -n $NAMESPACE exec -i deployment/testlookup-backend -- python < scripts/createAdmin.py"
    fi
  else
    warn "Could not find backend pod. Create admin user manually later."
  fi
else
  warn "Backend not ready. Create admin user manually after backend starts:"
  echo "  kubectl -n $NAMESPACE exec -i deployment/testlookup-backend -- python < scripts/createAdmin.py"
fi

if [ "$ADMIN_CREATED" = true ]; then
  # Persist the new admin's credentials beside the other generated secrets.
  if [ ! -f "$CREDS_FILE" ] || ! grep -q "^ADMIN_USERNAME=" "$CREDS_FILE"; then
    mkdir -p "$(dirname "$CREDS_FILE")"
    ( umask 077; touch "$CREDS_FILE" )
    cat >> "$CREDS_FILE" <<CREDS

# Initial admin user (created $(date -u +"%Y-%m-%dT%H:%M:%SZ")).
# Change the password after the first sign-in; this file is not updated then.
ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
ADMIN_EMAIL=${ADMIN_EMAIL}
CREDS
    log "Admin credentials saved to ${CREDS_FILE}"
  fi

  echo ""
  echo -e "${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${YELLOW}║  ADMIN LOGIN — SHOWN ONCE, SAVE IT NOW               ║${NC}"
  echo -e "${YELLOW}╠══════════════════════════════════════════════════════╣${NC}"
  echo -e "${YELLOW}║${NC}  URL:      http://${K3S_HOST}"
  echo -e "${YELLOW}║${NC}  Username: ${ADMIN_USERNAME}"
  echo -e "${YELLOW}║${NC}  Password: ${ADMIN_PASSWORD}"
  echo -e "${YELLOW}║${NC}  Email:    ${ADMIN_EMAIL}"
  echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""
elif echo "$ADMIN_OUTPUT" | grep -q "already exists"; then
  log "An admin account already exists — left unchanged (password not shown)."
  log "To reset it: kubectl -n $NAMESPACE exec -i deployment/testlookup-backend -- \\"
  log "  env ADMIN_PASSWORD='<new password>' python < scripts/getUser.py"
fi

# ── Retire the legacy process-wide MCP principal on upgrade ────────────────
if [ -n "$LEGACY_MCP_USER" ]; then
  if [ "${KEEP_LEGACY_MCP_SERVICE_ACCOUNT:-false}" = "true" ]; then
    warn "Keeping legacy MCP account '$LEGACY_MCP_USER' by operator request."
  else
    if ! kubectl -n "$NAMESPACE" rollout status deployment/testlookup-mcp --timeout=240s; then
      DEPLOY_DEGRADED=true
      warn "MCP replacement not ready; legacy credentials were retained."
      LEGACY_MCP_USER=""
    fi
  fi
fi

if [ -n "$LEGACY_MCP_USER" ] && [ "${KEEP_LEGACY_MCP_SERVICE_ACCOUNT:-false}" != "true" ]; then
    LEGACY_BACKEND_POD=$(kubectl -n "$NAMESPACE" get pod -l "$BACKEND_POD_SELECTOR" \
      --field-selector=status.phase=Running \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
    if [ -z "$LEGACY_BACKEND_POD" ]; then
      DEPLOY_DEGRADED=true
      warn "Cannot retire legacy MCP account: no running backend pod."
    elif kubectl -n "$NAMESPACE" exec -i "$LEGACY_BACKEND_POD" -- \
        env LEGACY_MCP_USERNAME="$LEGACY_MCP_USER" \
        python < "$REPO_ROOT/scripts/retireLegacyMcpServiceAccount.py"; then
      kubectl -n "$NAMESPACE" patch secret testlookup-secrets --type=merge \
        -p='{"data":{"MCP_USERNAME":null,"MCP_PASSWORD":null}}'
      if [ -f "$CREDS_FILE" ]; then
        sed -i '/^MCP_USERNAME=/d;/^MCP_PASSWORD=/d' "$CREDS_FILE"
      fi
      log "Legacy MCP account retired and stored credentials removed."
    else
      DEPLOY_DEGRADED=true
      warn "Legacy MCP account retirement failed; credentials were retained for retry."
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

# ── Pod-level check FIRST ──────────────────────────────────────────────────
# The ingress health check below is answered by whichever backend pod is
# Ready — including one from the ReplicaSet this deploy was replacing. On
# 2026-08-15 the new image crash-looped on a bad DB credential for its whole
# life and the deploy still printed "Health check passed" and exited 0.
# So ask the cluster which pods are actually broken before trusting HTTP.
# Terminating is not broken: it is a pod of the ReplicaSet being REPLACED,
# shutting down gracefully (a Celery worker finishing its task can take
# minutes). On 2026-10-07 an old worker-ai pod still Terminating 80 s after a
# clean rollout marked the deploy DEGRADED; a crash-looping NEW pod is
# CrashLoopBackOff / Error / Pending, which this still catches.
NOT_READY=$(kubectl -n "$NAMESPACE" get pods --no-headers 2>/dev/null \
  | awk '$3 != "Running" && $3 != "Completed" && $3 != "Terminating" { print "  " $1 "  " $3 "  restarts=" $4 }' || echo "")
if [ -n "$NOT_READY" ]; then
  DEPLOY_DEGRADED=true
  warn "Pods not in a healthy state:"
  echo "$NOT_READY"
  warn "A CrashLoopBackOff here usually means the NEW image failed to start while"
  warn "the previous ReplicaSet keeps serving traffic. Check the exact new pod:"
  warn "  kubectl -n $NAMESPACE logs <pod-name> --tail=40"
fi

# Health check
TRAEFIK_IP=$(kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
if [ -n "$TRAEFIK_IP" ]; then
  log "Testing health endpoint via Traefik IP (HTTP, ingress is plain-text)..."
  HEALTH=$(curl -sf --max-time 5 -H "Host: ${K3S_HOST}" "http://${TRAEFIK_IP}/health/live" 2>/dev/null || echo "")
  if [ -n "$HEALTH" ]; then
    if [ "${DEPLOY_DEGRADED:-false}" = true ]; then
      warn "Ingress answered ($HEALTH) — but see the unhealthy pods above. This"
      warn "response may be coming from the ReplicaSet being replaced."
    else
      log "Health check passed: $HEALTH"
    fi
  else
    DEPLOY_DEGRADED=true
    warn "Health check did not respond. Backend may still be starting (running migrations)."
    warn "Check with: kubectl -n testlookup logs deployment/testlookup-backend --tail=30"
  fi
fi

# ── Summary ─────────────────────────────────────────────────
if [ "${DEPLOY_DEGRADED:-false}" = true ]; then
  header "Deployment DEGRADED"
  echo -e "${RED}The deploy finished but the cluster is NOT in the expected state.${NC}"
  echo -e "${RED}Traffic may still be served by the previous ReplicaSet.${NC}"
  echo ""
  echo "  kubectl -n $NAMESPACE get pods"
  echo "  kubectl -n $NAMESPACE logs <failing-pod> --tail=40"
  echo ""
  # Exit non-zero: a caller (or a human skimming the tail) must not read this
  # run as a success. Reporting 0 while the new image never started is how a
  # broken deploy went unnoticed on 2026-08-15.
  exit 1
fi

# ── Step 12: Prune old TestLookup images from the registry ─────────────────
# Only after a deploy that reached this point healthy, and only when this run
# pushed a build (a --skip-build re-run pushed nothing). Keeps the newest
# KEEP_BUILDS builds (the one just deployed among them) so a rollback to the
# previous builds stays possible; other apps' repositories are never touched.
# A failure here only warns: pruning never turns a good deploy into a failure.
if [ "$SKIP_BUILD" = false ] && [ "$SKIP_REGISTRY_PRUNE" = false ]; then
  header "Step 12 — Prune old TestLookup images (keep ${KEEP_BUILDS} builds)"
  prune_rc=0
  bash "$REPO_ROOT/homelabsetup/prune-registry.sh" \
    --registry="$REGISTRY" --keep="$KEEP_BUILDS" || prune_rc=$?
  case "$prune_rc" in
    0) log "Registry pruned." ;;
    3) warn "The registry refuses deletes; old images were kept. Enable them with:"
       warn "  kubectl -n registry set env deploy/registry REGISTRY_STORAGE_DELETE_ENABLED=true" ;;
    *) warn "Registry prune did not complete (exit ${prune_rc}); the deploy itself is fine." ;;
  esac
fi

header "Deployment Complete"

echo -e "${GREEN}TestLookup has been deployed to your K3s cluster.${NC}"
echo ""
echo "  Dashboard:      http://${K3S_HOST}"
echo "  User Docs:      http://${K3S_HOST}/docs"
echo "  API Reference:  http://${K3S_HOST}/api-docs"
echo "  Health Check:   http://${K3S_HOST}/health/live"
echo ""
echo "  Note: the ingress is HTTP-only. If a browser keeps redirecting"
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
