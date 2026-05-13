#!/usr/bin/env bash
# ============================================================
# bootstrap-homelab.sh — One-shot K3s cluster bootstrap
# ============================================================
# Goes from "fresh Linux nodes with SSH access" to "kubectl-ready
# 3-node K3s cluster with MetalLB + cert-manager," then hands off
# to deploy-homelab.sh for the application install.
#
# Targets the standard TestLookup homelab layout:
#   k8s-node1  192.168.0.101  control-plane + worker
#   k8s-node2  192.168.0.102  worker
#   k8s-node3  192.168.0.103  worker
#   SSH user:  labadmin
#
# Credentials (NEVER hardcoded):
#   - First, the script tries SSH key auth (idempotent — re-runs are free).
#   - If key auth fails, it falls back to password auth via sshpass.
#   - Passwords are read from env vars HOMELAB_NODE{1,2,3}_PASS, or
#     prompted interactively (read -s) if unset. Never written to disk.
#
# Usage:
#   ./homelabsetup/bootstrap-homelab.sh                 # bootstrap only
#   ./homelabsetup/bootstrap-homelab.sh --deploy        # bootstrap + app deploy
#   ./homelabsetup/bootstrap-homelab.sh --skip-prereqs  # K3s only, no MetalLB/cert-manager
#   ./homelabsetup/bootstrap-homelab.sh --teardown      # uninstall K3s from all nodes
# ============================================================

set -euo pipefail

# ── Configuration ───────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NODE_USER="labadmin"
NODE1_IP="192.168.0.101"
NODE2_IP="192.168.0.102"
NODE3_IP="192.168.0.103"
NODE1_NAME="k8s-node1"
NODE2_NAME="k8s-node2"
NODE3_NAME="k8s-node3"
NODES_IP=("$NODE1_IP" "$NODE2_IP" "$NODE3_IP")
NODES_NAME=("$NODE1_NAME" "$NODE2_NAME" "$NODE3_NAME")

METALLB_RANGE="192.168.0.200-192.168.0.220"
METALLB_VERSION="v0.14.8"
CERT_MANAGER_VERSION="v1.16.1"

REGISTRY_HOST="registry.local"
REGISTRY_NODEPORT="30500"
REGISTRY_IMAGES=("testlookup/backend" "testlookup/frontend" "testlookup/mcp")
REGISTRY_TAG="latest"

LOCAL_KUBECONFIG="$HOME/.kube/config-testlookup-homelab"
SSH_KEY="$HOME/.ssh/id_ed25519"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

# ── Flags ───────────────────────────────────────────────────
DO_DEPLOY=false
SKIP_PREREQS=false
TEARDOWN=false

for arg in "$@"; do
  case $arg in
    --deploy)        DO_DEPLOY=true ;;
    --skip-prereqs)  SKIP_PREREQS=true ;;
    --teardown)      TEARDOWN=true ;;
    -h|--help)
      sed -n '2,28p' "$0"; exit 0 ;;
    *) echo -e "${RED}Unknown flag: $arg${NC}"; exit 1 ;;
  esac
done

# ── Helpers ─────────────────────────────────────────────────
log()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()   { echo -e "${YELLOW}[!]${NC} $*"; }
error()  { echo -e "${RED}[x]${NC} $*"; exit 1; }
header() { echo -e "\n${CYAN}━━━ $* ━━━${NC}\n"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || error "$1 is required but not installed."
}

# Index 0/1/2 → password env var → fallback to prompt
get_password() {
  local idx="$1"
  local var="HOMELAB_NODE$((idx + 1))_PASS"
  local val="${!var:-}"
  if [[ -z "$val" ]]; then
    read -rsp "Password for ${NODE_USER}@${NODES_IP[$idx]}: " val
    echo
    [[ -n "$val" ]] || error "Empty password for ${NODES_IP[$idx]}."
  fi
  printf '%s' "$val"
}

# ssh that tries key auth first; falls back to sshpass+password if needed.
# Args: <node_idx> <command...>
nssh() {
  local idx="$1"; shift
  local ip="${NODES_IP[$idx]}"
  local opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new \
              -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
              -o ConnectTimeout=10)

  if ssh "${opts[@]}" "${NODE_USER}@${ip}" "true" 2>/dev/null; then
    ssh "${opts[@]}" "${NODE_USER}@${ip}" "$@"
    return $?
  fi

  require_cmd sshpass
  local pass; pass="$(get_password "$idx")"
  SSHPASS="$pass" sshpass -e ssh \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    -o ConnectTimeout=10 \
    "${NODE_USER}@${ip}" "$@"
}

nscp() {
  local idx="$1"; local src="$2"; local dst="$3"
  local ip="${NODES_IP[$idx]}"
  local opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new \
              -o UserKnownHostsFile="$HOME/.ssh/known_hosts")

  if ssh "${opts[@]}" "${NODE_USER}@${ip}" "true" 2>/dev/null; then
    scp "${opts[@]}" "$src" "${NODE_USER}@${ip}:$dst"
    return $?
  fi

  require_cmd sshpass
  local pass; pass="$(get_password "$idx")"
  SSHPASS="$pass" sshpass -e scp \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    "$src" "${NODE_USER}@${ip}:$dst"
}

# ── Preflight ───────────────────────────────────────────────
preflight() {
  header "Preflight"
  require_cmd ssh
  require_cmd scp
  require_cmd kubectl
  require_cmd curl

  if [[ ! -f "$SSH_KEY" ]]; then
    log "Generating SSH key at $SSH_KEY"
    mkdir -p "$(dirname "$SSH_KEY")"
    ssh-keygen -t ed25519 -N "" -f "$SSH_KEY" -C "testlookup-homelab"
  else
    log "Using existing SSH key: $SSH_KEY"
  fi

  # Probe sshpass only if any node still needs password auth.
  local need_sshpass=false
  for i in 0 1 2; do
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 \
            "${NODE_USER}@${NODES_IP[$i]}" "true" 2>/dev/null; then
      need_sshpass=true
    fi
  done
  if $need_sshpass; then
    if ! command -v sshpass >/dev/null 2>&1; then
      error "sshpass is required for first-time setup. Install:
  Debian/Ubuntu/WSL:  sudo apt install sshpass
  macOS (brew):       brew install esolitos/ipa/sshpass"
    fi
  fi
}

# ── SSH key distribution ────────────────────────────────────
distribute_keys() {
  header "Distributing SSH keys"
  local pub_key; pub_key="$(cat "${SSH_KEY}.pub")"

  for i in 0 1 2; do
    local ip="${NODES_IP[$i]}"
    if ssh -o BatchMode=yes -o ConnectTimeout=5 \
           "${NODE_USER}@${ip}" "true" 2>/dev/null; then
      log "Key already authorized on ${NODES_NAME[$i]} ($ip)"
      continue
    fi

    log "Pushing key to ${NODES_NAME[$i]} ($ip) — using password auth"
    require_cmd sshpass
    local pass; pass="$(get_password "$i")"
    SSHPASS="$pass" sshpass -e ssh \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
      "${NODE_USER}@${ip}" \
      "mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
       grep -qxF '$pub_key' ~/.ssh/authorized_keys 2>/dev/null || \
       echo '$pub_key' >> ~/.ssh/authorized_keys && \
       chmod 600 ~/.ssh/authorized_keys"
  done
}

# ── K3s install ─────────────────────────────────────────────
k3s_install_server() {
  header "Installing K3s control plane on ${NODE1_NAME} ($NODE1_IP)"
  if nssh 0 "command -v k3s >/dev/null 2>&1 && sudo systemctl is-active --quiet k3s"; then
    log "K3s server already running on ${NODE1_NAME}"
    return
  fi

  # --disable servicelb because we use MetalLB. Traefik stays.
  nssh 0 "curl -sfL https://get.k3s.io | \
    INSTALL_K3S_EXEC='server --disable=servicelb --node-name=${NODE1_NAME} --tls-san=${NODE1_IP}' \
    sh -"

  # Wait for API server.
  local attempts=0
  until nssh 0 "sudo k3s kubectl get nodes >/dev/null 2>&1"; do
    attempts=$((attempts + 1))
    [[ $attempts -gt 30 ]] && error "K3s API server didn't come up on ${NODE1_NAME}"
    sleep 2
  done
  log "K3s server up on ${NODE1_NAME}"
}

k3s_install_agents() {
  header "Joining workers to cluster"
  local token
  token="$(nssh 0 "sudo cat /var/lib/rancher/k3s/server/node-token")"
  [[ -n "$token" ]] || error "Could not read K3s join token from ${NODE1_NAME}"

  for i in 1 2; do
    local ip="${NODES_IP[$i]}"
    local name="${NODES_NAME[$i]}"
    if nssh "$i" "command -v k3s >/dev/null 2>&1 && sudo systemctl is-active --quiet k3s-agent"; then
      log "K3s agent already running on ${name}"
      continue
    fi
    log "Installing K3s agent on ${name} ($ip)"
    nssh "$i" "curl -sfL https://get.k3s.io | \
      K3S_URL=https://${NODE1_IP}:6443 \
      K3S_TOKEN='${token}' \
      INSTALL_K3S_EXEC='agent --node-name=${name}' \
      sh -"
  done

  log "Waiting 15s for agents to register..."
  sleep 15
}

fetch_kubeconfig() {
  header "Fetching kubeconfig"
  mkdir -p "$(dirname "$LOCAL_KUBECONFIG")"
  nssh 0 "sudo cat /etc/rancher/k3s/k3s.yaml" \
    | sed "s|server: https://127.0.0.1:6443|server: https://${NODE1_IP}:6443|" \
    > "$LOCAL_KUBECONFIG"
  chmod 600 "$LOCAL_KUBECONFIG"
  export KUBECONFIG="$LOCAL_KUBECONFIG"
  log "Kubeconfig saved to $LOCAL_KUBECONFIG"
  log "Cluster nodes:"
  kubectl get nodes -o wide
}

# ── Cluster prerequisites ───────────────────────────────────
install_metallb() {
  header "Installing MetalLB ($METALLB_VERSION) — pool $METALLB_RANGE"
  if kubectl get ns metallb-system >/dev/null 2>&1; then
    log "metallb-system namespace already exists"
  else
    kubectl apply -f "https://raw.githubusercontent.com/metallb/metallb/${METALLB_VERSION}/config/manifests/metallb-native.yaml"
  fi

  log "Waiting for MetalLB controller to be ready..."
  kubectl -n metallb-system wait --for=condition=Available \
    deployment/controller --timeout=180s

  kubectl apply -f - <<EOF
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: homelab-pool
  namespace: metallb-system
spec:
  addresses:
    - ${METALLB_RANGE}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: homelab-l2
  namespace: metallb-system
spec:
  ipAddressPools:
    - homelab-pool
EOF
}

verify_docker_insecure_registry() {
  header "Verifying Docker daemon trusts ${REGISTRY_HOST}:${REGISTRY_NODEPORT}"
  # Without this, 'docker push' speaks HTTPS to a plain-HTTP registry and fails
  # mid-push with: "http: server gave HTTP response to HTTPS client"

  if ! command -v docker >/dev/null 2>&1; then
    error "docker CLI not found — needed to build and push images."
  fi
  if ! docker info >/dev/null 2>&1; then
    error "Docker daemon is not reachable. Start Docker Desktop (or your Docker daemon)."
  fi

  local entries
  entries=$(docker info --format '{{range .RegistryConfig.InsecureRegistryCIDRs}}{{.}} {{end}}{{range $k, $v := .RegistryConfig.IndexConfigs}}{{if not $v.Secure}}{{$k}} {{end}}{{end}}' 2>/dev/null || true)

  for needle in "${REGISTRY_HOST}:${REGISTRY_NODEPORT}" "${NODE1_IP}:${REGISTRY_NODEPORT}"; do
    if echo "$entries" | grep -qF "$needle"; then
      log "Docker trusts $needle as insecure registry ✓"
      return 0
    fi
  done

  # Detect the platform so we can give the right remediation.
  local platform="unknown"
  if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
    platform="wsl"
  elif [[ "$(uname -s)" == "Darwin" ]]; then
    platform="macos"
  elif [[ -f /etc/docker/daemon.json ]] || systemctl list-unit-files docker.service >/dev/null 2>&1; then
    platform="linux"
  fi

  echo
  warn "Docker is NOT configured to trust ${REGISTRY_HOST}:${REGISTRY_NODEPORT} as an insecure registry."
  warn "Without this, 'docker push' will fail with 'http: server gave HTTP response to HTTPS client'."
  echo

  case "$platform" in
    wsl|macos)
      cat <<EOF
Fix (Docker Desktop on Windows/macOS):
  1. Open Docker Desktop → Settings → Docker Engine
  2. Add "registry.local:30500" to the JSON, e.g.:
       {
         "insecure-registries": ["registry.local:30500", "${NODE1_IP}:30500"]
       }
  3. Click "Apply & Restart"
  4. Also ensure your hosts file maps ${REGISTRY_HOST} → ${NODE1_IP}:
       Windows: C:\\Windows\\System32\\drivers\\etc\\hosts
       macOS:   /etc/hosts
       Add line: ${NODE1_IP} ${REGISTRY_HOST}
  5. Re-run this script.
EOF
      ;;
    linux)
      cat <<EOF
Fix (native Linux Docker):
  sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "insecure-registries": ["${REGISTRY_HOST}:${REGISTRY_NODEPORT}", "${NODE1_IP}:${REGISTRY_NODEPORT}"]
}
JSON
  sudo systemctl restart docker
  # Then add to /etc/hosts:
  echo "${NODE1_IP} ${REGISTRY_HOST}" | sudo tee -a /etc/hosts
EOF
      ;;
    *)
      cat <<EOF
Fix: Configure your Docker daemon to allow insecure HTTP push to:
       ${REGISTRY_HOST}:${REGISTRY_NODEPORT}
       ${NODE1_IP}:${REGISTRY_NODEPORT}
     and ensure ${REGISTRY_HOST} resolves to ${NODE1_IP}.
EOF
      ;;
  esac
  echo
  error "Aborting before build to save you a long failed push. Fix the above and re-run."
}

configure_registry_mirrors() {
  header "Configuring K3s registry mirror on all nodes"
  # Without this, kubelet on the workers can't pull from registry.local:30500
  # because it's an insecure HTTP registry. deploy-homelab.sh would normally
  # ask the user to do this by hand on each node.

  local mirror_config
  mirror_config=$(cat <<EOF
mirrors:
  "${REGISTRY_HOST}:${REGISTRY_NODEPORT}":
    endpoint:
      - "http://${NODE1_IP}:${REGISTRY_NODEPORT}"
  "${NODE1_IP}:${REGISTRY_NODEPORT}":
    endpoint:
      - "http://${NODE1_IP}:${REGISTRY_NODEPORT}"
EOF
)

  for i in 0 1 2; do
    local name="${NODES_NAME[$i]}"
    local svc="k3s"
    [[ $i -gt 0 ]] && svc="k3s-agent"

    # Compare against existing config — only restart k3s if it actually changes,
    # restarts are disruptive on agents already running workloads.
    local current
    current=$(nssh "$i" "sudo cat /etc/rancher/k3s/registries.yaml 2>/dev/null || true")
    if [[ "$current" == "$mirror_config" ]]; then
      log "$name: registries.yaml already up to date"
      continue
    fi

    log "$name: writing /etc/rancher/k3s/registries.yaml"
    nssh "$i" "sudo mkdir -p /etc/rancher/k3s && sudo tee /etc/rancher/k3s/registries.yaml >/dev/null <<'YAML'
${mirror_config}
YAML"
    log "$name: restarting $svc to pick up mirror config"
    nssh "$i" "sudo systemctl restart $svc"
  done

  log "Waiting 10s for nodes to recover..."
  sleep 10
  kubectl wait --for=condition=Ready node --all --timeout=120s
}

# Confirms the registry's /v2/_catalog lists every required image with the
# expected tag. Run AFTER deploy-homelab.sh has built+pushed.
verify_pushed_images() {
  header "Verifying images in registry"

  local registry_url
  if curl -sf --max-time 5 "http://${REGISTRY_HOST}:${REGISTRY_NODEPORT}/v2/_catalog" >/dev/null 2>&1; then
    registry_url="http://${REGISTRY_HOST}:${REGISTRY_NODEPORT}"
  elif curl -sf --max-time 5 "http://${NODE1_IP}:${REGISTRY_NODEPORT}/v2/_catalog" >/dev/null 2>&1; then
    registry_url="http://${NODE1_IP}:${REGISTRY_NODEPORT}"
    warn "Using IP-based registry URL: ${registry_url}"
    warn "Add '${NODE1_IP} ${REGISTRY_HOST}' to your hosts file for cleaner output"
  else
    error "Registry not reachable at ${REGISTRY_HOST}:${REGISTRY_NODEPORT} or ${NODE1_IP}:${REGISTRY_NODEPORT}.
    Was --skip-registry passed? Or did the registry pod fail to start?"
  fi

  local catalog
  catalog=$(curl -sf --max-time 10 "${registry_url}/v2/_catalog") \
    || error "Could not query ${registry_url}/v2/_catalog"
  log "Registry catalog: $catalog"

  local missing=()
  for img in "${REGISTRY_IMAGES[@]}"; do
    local tags_url="${registry_url}/v2/${img}/tags/list"
    local tags
    tags=$(curl -sf --max-time 10 "$tags_url" 2>/dev/null || echo "")
    if [[ -z "$tags" ]] || ! echo "$tags" | grep -q "\"${REGISTRY_TAG}\""; then
      missing+=("$img:${REGISTRY_TAG}")
      warn "  ✗ $img:${REGISTRY_TAG} NOT FOUND"
    else
      log "  ✓ $img:${REGISTRY_TAG} present"
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    error "Missing images in registry: ${missing[*]}
    Common causes:
      - Docker push failed silently (check 'docker push' output above)
      - Docker Desktop missing 'registry.local:30500' in insecure-registries
      - Wrong registry hostname — image was tagged for a different registry
    Re-run deploy-homelab.sh manually to retry the build/push step."
  fi

  log "All ${#REGISTRY_IMAGES[@]} images verified in registry."
}

install_cert_manager() {
  header "Installing cert-manager ($CERT_MANAGER_VERSION)"
  if kubectl get ns cert-manager >/dev/null 2>&1; then
    log "cert-manager namespace already exists"
    return
  fi
  kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml"
  kubectl -n cert-manager wait --for=condition=Available \
    deployment/cert-manager deployment/cert-manager-webhook deployment/cert-manager-cainjector \
    --timeout=240s
}

# ── Teardown ────────────────────────────────────────────────
teardown() {
  header "Tearing down K3s on all nodes"
  warn "This uninstalls K3s but leaves the OS untouched."
  for i in 1 2; do
    log "Uninstalling agent on ${NODES_NAME[$i]}"
    nssh "$i" "if [[ -x /usr/local/bin/k3s-agent-uninstall.sh ]]; then sudo /usr/local/bin/k3s-agent-uninstall.sh; fi" || true
  done
  log "Uninstalling server on ${NODES_NAME[0]}"
  nssh 0 "if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then sudo /usr/local/bin/k3s-uninstall.sh; fi" || true
  log "Done. Local kubeconfig at $LOCAL_KUBECONFIG was NOT removed."
}

# ── Main ────────────────────────────────────────────────────
main() {
  if $TEARDOWN; then
    preflight
    distribute_keys
    teardown
    exit 0
  fi

  preflight
  distribute_keys
  k3s_install_server
  k3s_install_agents
  fetch_kubeconfig

  if ! $SKIP_PREREQS; then
    install_metallb
    install_cert_manager
  fi

  configure_registry_mirrors

  header "Cluster bootstrap complete"
  cat <<EOF

Cluster is ready. To use kubectl from this shell:

  export KUBECONFIG="$LOCAL_KUBECONFIG"
  kubectl get nodes

To make it permanent, add the export above to your ~/.bashrc or ~/.zshrc.

EOF

  if $DO_DEPLOY; then
    verify_docker_insecure_registry
    header "Handing off to deploy-homelab.sh"
    export KUBECONFIG="$LOCAL_KUBECONFIG"
    "$REPO_ROOT/homelabsetup/deploy-homelab.sh"
    verify_pushed_images
  else
    echo "Next step (when ready):"
    echo "  KUBECONFIG=$LOCAL_KUBECONFIG ./homelabsetup/deploy-homelab.sh"
    echo "Or re-run this script with --deploy to do everything in one shot."
  fi
}

main "$@"
