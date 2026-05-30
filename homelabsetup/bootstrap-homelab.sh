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

# Pinned K3s version so re-running bootstrap against an existing cluster
# doesn't silently track ``https://get.k3s.io`` 's "stable" channel and
# upgrade only the workers (or only the server) — producing a kubelet/
# apiserver version skew + the port-already-in-use failure we saw
# 2026-05-18 when v1.35.4 binaries were installed alongside running
# v1.34.6 kube-proxy shims. Override per-deploy via ``K3S_VERSION`` env
# var when you genuinely want to roll the whole cluster forward.
K3S_VERSION="${K3S_VERSION:-v1.34.6+k3s1}"
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
# When the passwordless-sudo precheck finds nodes that still require a
# password, ``--configure-sudo`` auto-writes /etc/sudoers.d/<user> on
# each via one interactive ssh -t per node. Default off so the precheck
# stays a read-only diagnostic that never mutates remote /etc/.
CONFIGURE_SUDO=false

for arg in "$@"; do
  case $arg in
    --deploy)          DO_DEPLOY=true ;;
    --skip-prereqs)    SKIP_PREREQS=true ;;
    --teardown)        TEARDOWN=true ;;
    --configure-sudo)  CONFIGURE_SUDO=true ;;
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

# ssh that tries key auth first; falls back to sshpass+password (or
# interactive ssh when sshpass is missing — e.g. Windows Git Bash).
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

  if command -v sshpass >/dev/null 2>&1; then
    local pass; pass="$(get_password "$idx")"
    SSHPASS="$pass" sshpass -e ssh \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
      -o ConnectTimeout=10 \
      "${NODE_USER}@${ip}" "$@"
  else
    # Interactive — ssh prompts for the password normally. distribute_keys
    # should have run before any other nssh call, so reaching this branch
    # post-key-distribution means the key push didn't persist.
    warn "nssh: sshpass missing, will prompt for password for ${NODE_USER}@${ip}"
    ssh \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
      -o ConnectTimeout=10 \
      "${NODE_USER}@${ip}" "$@"
  fi
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

  if command -v sshpass >/dev/null 2>&1; then
    local pass; pass="$(get_password "$idx")"
    SSHPASS="$pass" sshpass -e scp \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
      "$src" "${NODE_USER}@${ip}:$dst"
  else
    warn "nscp: sshpass missing, will prompt for password for ${NODE_USER}@${ip}"
    scp \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
      "$src" "${NODE_USER}@${ip}:$dst"
  fi
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

  # Probe whether any node still needs a one-time password-auth key push.
  # When every node already accepts the key, no password helper is needed
  # at all — including for ``--skip-prereqs`` quick re-runs.
  local need_password_auth=false
  for i in 0 1 2; do
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 \
            "${NODE_USER}@${NODES_IP[$i]}" "true" 2>/dev/null; then
      need_password_auth=true
    fi
  done
  if $need_password_auth; then
    # ``sshpass`` is the preferred path (silent + automatable); when it's
    # missing we fall back to interactive ssh which prompts for the
    # password normally. The fallback works fine on Windows Git Bash
    # (where sshpass isn't packaged) — the user just types the password
    # once per node during ``distribute_keys``. After that, every
    # subsequent step uses the now-authorized key.
    if command -v sshpass >/dev/null 2>&1; then
      log "sshpass available — first-time key push will be non-interactive."
    else
      warn "sshpass not installed — will prompt for the node password interactively"
      warn "during the one-time key push (3 prompts, one per node)."
      warn "Install sshpass to skip the prompts: 'apt install sshpass' / 'brew install sshpass'."
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
    # Inline command — same for both code paths. ``grep -qxF`` makes the
    # append idempotent so re-running distribute_keys after a partial
    # success won't add a duplicate authorized_keys line.
    local remote_cmd="mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
       (grep -qxF '$pub_key' ~/.ssh/authorized_keys 2>/dev/null || \
        echo '$pub_key' >> ~/.ssh/authorized_keys) && \
       chmod 600 ~/.ssh/authorized_keys"

    if command -v sshpass >/dev/null 2>&1; then
      # Non-interactive path — preferred when sshpass is available.
      local pass; pass="$(get_password "$i")"
      SSHPASS="$pass" sshpass -e ssh \
        -o StrictHostKeyChecking=accept-new \
        -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
        "${NODE_USER}@${ip}" "$remote_cmd"
    else
      # Interactive fallback — works on Windows Git Bash where sshpass
      # isn't packaged. ssh will prompt for the password normally; the
      # user types it once per node during this one-time bootstrap.
      warn "  → ssh will prompt for the password for ${NODE_USER}@${ip}"
      ssh \
        -o StrictHostKeyChecking=accept-new \
        -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
        "${NODE_USER}@${ip}" "$remote_cmd"
    fi
  done
}

# ── Passwordless-sudo precheck ──────────────────────────────
# K3s automation needs ``sudo`` without a password prompt on each node.
# Without it, the K3s installer fails mid-install on a TTY-less ssh
# session ("sudo: a terminal is required to read the password"). Detect
# the gap up front and print a one-line fix per node — much faster than
# a partial install + cleanup loop.
ensure_passwordless_sudo() {
  header "Verifying passwordless sudo on each node"

  local missing=()
  for i in 0 1 2; do
    if nssh "$i" "sudo -n true" 2>/dev/null; then
      log "${NODES_NAME[$i]} (${NODES_IP[$i]}): passwordless sudo ✓"
    else
      missing+=("$i")
      warn "${NODES_NAME[$i]} (${NODES_IP[$i]}): sudo requires a password"
    fi
  done

  if [ ${#missing[@]} -eq 0 ]; then
    return 0
  fi

  # Auto-remediate when the user opted in (``--configure-sudo``); otherwise
  # print the manual one-liner. The auto path needs one interactive ssh
  # per missing node so the user can type the sudo password once each.
  if $CONFIGURE_SUDO; then
    log "Auto-configuring passwordless sudo (one prompt per missing node)..."
    for i in "${missing[@]}"; do
      local ip="${NODES_IP[$i]}"
      warn "  → ssh -t will prompt for ${NODE_USER}@${ip}'s sudo password"
      # ``ssh -t`` allocates a TTY so the remote ``sudo`` can prompt.
      # The single-quoted heredoc keeps ${NODE_USER} expanded locally so
      # the remote sees a literal username (no remote interpolation).
      ssh -t \
        -o StrictHostKeyChecking=accept-new \
        -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
        "${NODE_USER}@${ip}" \
        "echo '${NODE_USER} ALL=(ALL) NOPASSWD: ALL' \
           | sudo tee /etc/sudoers.d/${NODE_USER} >/dev/null && \
         sudo chmod 440 /etc/sudoers.d/${NODE_USER} && \
         sudo -n true && echo 'NOPASSWD configured for ${NODE_USER}'" \
        || error "Failed to configure passwordless sudo on ${NODES_NAME[$i]}."
    done
    # Re-check now that we've configured it.
    for i in "${missing[@]}"; do
      nssh "$i" "sudo -n true" 2>/dev/null \
        || error "Passwordless sudo still failing on ${NODES_NAME[$i]} after configure step."
    done
    log "All nodes now have passwordless sudo."
    return 0
  fi

  cat >&2 <<EOF

══════════════════════════════════════════════════════════════════════
[!] K3s installation requires passwordless sudo for ${NODE_USER} on
    every node. The following nodes are missing it:

$(for i in "${missing[@]}"; do echo "       - ${NODES_NAME[$i]} (${NODES_IP[$i]})"; done)

    Fix — re-run bootstrap with ``--configure-sudo`` to auto-write
    /etc/sudoers.d/${NODE_USER} on each missing node (you'll be prompted
    once per node for ${NODE_USER}'s sudo password):

        ./homelabsetup/bootstrap-homelab.sh --configure-sudo

    Or do it manually, once per missing node:

        ssh -t ${NODE_USER}@<node-ip> "echo '${NODE_USER} ALL=(ALL) NOPASSWD: ALL' \\
          | sudo tee /etc/sudoers.d/${NODE_USER} && \\
          sudo chmod 440 /etc/sudoers.d/${NODE_USER}"

    Then re-run ./homelabsetup/bootstrap-homelab.sh
══════════════════════════════════════════════════════════════════════
EOF
  exit 1
}

# ── K3s install ─────────────────────────────────────────────
k3s_install_server() {
  header "Installing K3s control plane on ${NODE1_NAME} ($NODE1_IP)"
  if nssh 0 "command -v k3s >/dev/null 2>&1 && sudo systemctl is-active --quiet k3s"; then
    log "K3s server already running on ${NODE1_NAME}"
    return
  fi

  # --disable servicelb because we use MetalLB. Traefik stays.
  # INSTALL_K3S_VERSION pins us to the same release as the existing
  # cluster so we don't get a partial upgrade when re-running bootstrap.
  nssh 0 "curl -sfL https://get.k3s.io | \
    INSTALL_K3S_VERSION='${K3S_VERSION}' \
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
      INSTALL_K3S_VERSION='${K3S_VERSION}' \
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

  # The IPAddressPool admission webhook is stricter than ``kubectl apply``
  # — it rejects re-apply when ANY pool already owns the CIDR, even the
  # same pool name (re-asserting the CIDR is interpreted as "overlap with
  # already defined CIDR"). Three cases to handle idempotently:
  #
  #   1. ``homelab-pool`` exists with the desired CIDR → no-op.
  #   2. ``homelab-pool`` exists with a DIFFERENT CIDR → in-place edit
  #      via ``kubectl patch``; the webhook accepts a mutation to the
  #      same pool's address list.
  #   3. ``homelab-pool`` doesn't exist → create normally. If a
  #      DIFFERENT pool already claims the CIDR (left over from an
  #      earlier deploy under a different name), warn + bail so the
  #      user can clean it up explicitly rather than silently masking it.
  local pool_exists
  pool_exists=$(kubectl -n metallb-system get ipaddresspool homelab-pool -o name 2>/dev/null || true)

  if [ -n "$pool_exists" ]; then
    local current
    current=$(kubectl -n metallb-system get ipaddresspool homelab-pool \
                -o jsonpath='{.spec.addresses[0]}' 2>/dev/null || echo "")
    if [ "$current" = "$METALLB_RANGE" ]; then
      log "IPAddressPool 'homelab-pool' already configured with ${METALLB_RANGE}"
    else
      log "Patching IPAddressPool 'homelab-pool' (${current} → ${METALLB_RANGE})"
      kubectl -n metallb-system patch ipaddresspool homelab-pool \
        --type=merge -p "{\"spec\":{\"addresses\":[\"${METALLB_RANGE}\"]}}"
    fi
  else
    # Surface a clear error when a DIFFERENT pool already claims the
    # range — otherwise the user gets the cryptic webhook overlap message
    # from before. Listing the conflicting pools by name makes cleanup
    # obvious.
    local conflicting
    conflicting=$(kubectl -n metallb-system get ipaddresspool \
                    -o jsonpath="{range .items[?(@.spec.addresses[0]=='${METALLB_RANGE}')]}{.metadata.name}{'\n'}{end}" \
                    2>/dev/null | grep -v '^homelab-pool$' || true)
    if [ -n "$conflicting" ]; then
      cat >&2 <<EOF
[!] Another MetalLB IPAddressPool already claims ${METALLB_RANGE}:

$(echo "$conflicting" | sed 's/^/       - /')

    Resolve before continuing. Either:
      * Delete the conflicting pool(s):
          kubectl -n metallb-system delete ipaddresspool <name>
      * Or change METALLB_RANGE at the top of bootstrap-homelab.sh.
EOF
      exit 1
    fi
    log "Creating IPAddressPool 'homelab-pool' (${METALLB_RANGE})"
    kubectl apply -f - <<EOF
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: homelab-pool
  namespace: metallb-system
spec:
  addresses:
    - ${METALLB_RANGE}
EOF
  fi

  # L2Advertisement is idempotent under ``kubectl apply`` — the webhook
  # doesn't have the same overlap quirk because advertisements point at
  # pool names (no CIDR comparison).
  kubectl apply -f - <<EOF
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

  # Track which nodes actually need a restart so we can wait between the
  # control-plane and agent restarts. Restarting k3s on node1 takes the
  # API server down briefly; if we restart k3s-agent on node2/node3
  # before the server is back, the agent fails to register and systemd
  # exits with a non-zero code ("Job for k3s-agent.service failed").
  local server_restarted=false
  local agents_to_restart=()
  for i in 0 1 2; do
    local name="${NODES_NAME[$i]}"
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

    if [[ $i -eq 0 ]]; then
      # Restart the control plane FIRST and wait for the API to come
      # back before touching any worker. ``k3s kubectl get nodes``
      # returns 200 once the server is reachable; we poll up to 120s
      # which is plenty even on a slow restart.
      log "$name: restarting k3s (control plane) to pick up mirror config"
      nssh "$i" "sudo systemctl restart k3s"
      server_restarted=true
    else
      # Defer agent restarts until after the server is healthy.
      agents_to_restart+=("$i")
    fi
  done

  if $server_restarted; then
    log "Waiting for K3s API to recover after control-plane restart..."
    # Poll the API server. ``kubectl --request-timeout=5s get --raw=/healthz``
    # is cheaper than ``kubectl get nodes`` (no list traversal) and the
    # 200 response means the API is serving — at which point agents can
    # rejoin cleanly. 120s ceiling matches the existing post-loop wait.
    local waited=0
    while [ $waited -lt 120 ]; do
      if kubectl --request-timeout=5s get --raw=/healthz >/dev/null 2>&1; then
        log "K3s API is back up after ${waited}s."
        break
      fi
      sleep 3
      waited=$((waited + 3))
    done
    if [ $waited -ge 120 ]; then
      error "K3s API did not recover within 120s after restarting node1. Check 'sudo journalctl -u k3s -n 50' on ${NODE1_NAME}."
    fi
  fi

  # Now safe to restart the agents — the server is reachable.
  for i in "${agents_to_restart[@]}"; do
    local name="${NODES_NAME[$i]}"
    log "$name: restarting k3s-agent to pick up mirror config"
    # Run k3s-killall.sh first. ``systemctl restart`` does NOT reliably
    # tear down the orphaned containerd-shim-runc-v2 processes from the
    # previous k3s-agent process — they keep their socket binds on ports
    # 10249 (metrics) + 10256 (healthz), so the new k3s-agent fails with
    # ``bind: address already in use`` and systemd marks the unit failed.
    # Incident 2026-05-18: a version-skewed reinstall left v1.34.6
    # shims running while the v1.35.4 binary tried to take over the
    # same ports. k3s-killall.sh kills everything cleanly before the
    # fresh start. Idempotent — safe to call when nothing's running.
    nssh "$i" "sudo /usr/local/bin/k3s-killall.sh 2>/dev/null || true"
    # ``reset-failed`` clears any lingering failed-unit state from
    # earlier restart attempts so systemctl start doesn't refuse with
    # "start-limit-hit". Idempotent on a healthy unit.
    nssh "$i" "sudo systemctl reset-failed k3s-agent 2>/dev/null || true"
    nssh "$i" "sudo systemctl start k3s-agent"
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
  ensure_passwordless_sudo
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
