#!/usr/bin/env bash
# ============================================================================
# TestLookup public read-only demo — one script for the demo VM.
# Runbook: docs/operations/public-demo.md
#
#   scripts/demo/demo.sh setup         first run: .env, images, seed, start
#   scripts/demo/demo.sh build         build the app images from this checkout
#   scripts/demo/demo.sh pull          use the published images instead (small VMs)
#   scripts/demo/demo.sh token         store the Cloudflare tunnel token, start the tunnel
#   scripts/demo/demo.sh reset         wipe all data and re-seed (nightly cron)
#   scripts/demo/demo.sh update        git pull, new images, reset
#   scripts/demo/demo.sh status        containers and memory
#   sudo scripts/demo/demo.sh install-cron   nightly reset at 03:00 (VM time) + watchdog
#   sudo scripts/demo/harden-host.sh         SSH, firewall, fail2ban, kernel, Docker
#
# DEMO_IMAGES in .env picks how setup and update get the app images: `build`
# (the default) or `pull`. On a 4 GB VM, pull: DEMO_IMAGES=pull demo.sh setup
#
# The stack is docker-compose.release.yml + docker-compose.demo.yml. Data is
# disposable: `reset` deletes every volume and rebuilds the sample data, so a
# fresh 30-day history always ends today and nothing a visitor did survives.
# ============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

COMPOSE=(docker compose -f docker-compose.release.yml -f docker-compose.demo.yml)
ENV_FILE="$REPO/.env"
BACKEND_IMAGE="testlookup-demo/backend:local"
FRONTEND_IMAGE="testlookup-demo/frontend:local"

log() { printf '[demo %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

# Read one KEY from .env without sourcing it (a sourced .env runs code).
# A key that is not in .env reads as empty. Without the `|| true`, grep's
# "no match" failed the whole pipeline under pipefail, and an assignment such
# as beat="$(env_value DEMO_HEARTBEAT_URL)" then ended the script: the
# nightly reset died just before "reset: done".
env_value() {
  [ -f "$ENV_FILE" ] || return 0
  { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
}

# Set KEY=VALUE in .env, replacing an existing line or appending one.
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' "$ENV_FILE" >"$tmp"
  else
    cat "$ENV_FILE" >"$tmp"
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
}

# Only one demo.sh run at a time (cron reset vs. a manual update).
take_lock() {
  exec 9>"${TMPDIR:-/tmp}/testlookup-demo.lock"
  if command -v flock >/dev/null 2>&1; then
    flock -n 9 || die "another demo.sh run is in progress"
  fi
}

ensure_env() {
  if [ ! -f "$ENV_FILE" ]; then
    log "generating .env with random secrets (scripts/gen-dev-env.sh --release)"
    bash scripts/gen-dev-env.sh --release
  fi
  # Every published port stays on the VM: Docker-published ports bypass host
  # firewalls such as ufw, and the tunnel is the only intended way in.
  set_env POSTGRES_PUBLISH_ADDR 127.0.0.1
  set_env MINIO_PUBLISH_ADDR 127.0.0.1
  set_env FRONTEND_PUBLISH_ADDR 127.0.0.1
  set_env BACKEND_PUBLISH_ADDR 127.0.0.1
  # The app images, built on this VM (`build`) or pulled and tagged (`pull`).
  set_env TESTLOOKUP_IMAGE testlookup-demo
  set_env TESTLOOKUP_VERSION local
  [ -n "$(env_value DEMO_IMAGES)" ] || set_env DEMO_IMAGES "${DEMO_IMAGES:-build}"
  # A small VM: fewer worker processes.
  set_env CELERY_CONCURRENCY 2
  set_env CELERY_CHILDREN_CONCURRENCY 1
  # ...and the PostgreSQL connection total those processes add up to. The
  # release stack's db-budget-check starts nothing unless the declared total
  # equals what the processes can open:
  #   API 4 x (pool 2 + overflow 1) = 12, worker 2 x (1 + 1) = 4,
  #   worker-children 1 x (1 + 1) = 2, migration 1  ->  19.
  # Change it together with the two concurrencies above.
  set_env PG_COMPOSE_REQUIRED_CONNECTIONS 19
  [ -n "$(env_value DEMO_USERNAME)" ] || set_env DEMO_USERNAME qa_lead
  grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=' "$ENV_FILE" || set_env CLOUDFLARE_TUNNEL_TOKEN ""
  grep -qE '^DEMO_HEARTBEAT_URL=' "$ENV_FILE" || set_env DEMO_HEARTBEAT_URL ""
  chmod 600 "$ENV_FILE"
}

cmd_build() {
  log "building $BACKEND_IMAGE (backend/, target production)"
  docker build --target production -t "$BACKEND_IMAGE" backend
  log "building $FRONTEND_IMAGE (frontend/, target production)"
  docker build --target production -t "$FRONTEND_IMAGE" frontend
}

# The published images, tagged with the local names the stack runs. Building
# the frontend needs more memory than a 4 GB VM has to spare, and the build
# takes most of an hour on two vCPUs; the pull takes a minute. The published
# images are amd64 only: on an Arm VM, build. `latest` follows main, as does
# the checkout `update` pulls; set DEMO_PULL_TAG to pin a release.
cmd_pull() {
  local source tag part
  source="$(env_value DEMO_PULL_IMAGE)"
  source="${source:-ghcr.io/test-intelligence/testlookup}"
  tag="$(env_value DEMO_PULL_TAG)"
  tag="${tag:-latest}"
  for part in backend frontend; do
    log "pulling $source/$part:$tag"
    docker pull "$source/$part:$tag"
    docker tag "$source/$part:$tag" "testlookup-demo/$part:local"
  done
}

cmd_images() {
  case "$(env_value DEMO_IMAGES)" in
    pull) cmd_pull ;;
    build | "") cmd_build ;;
    *) die "DEMO_IMAGES in .env must be build or pull" ;;
  esac
}

wait_for_backend() {
  local deadline=$((SECONDS + 900)) status
  log "waiting for the backend to report healthy (up to 15 min)"
  while [ $SECONDS -lt $deadline ]; do
    status="$(docker inspect -f '{{.State.Health.Status}}' testlookup_backend 2>/dev/null || true)"
    [ "$status" = "healthy" ] && { log "backend healthy"; return 0; }
    sleep 5
  done
  "${COMPOSE[@]}" logs --tail 80 backend db-migrate || true
  die "the backend did not become healthy"
}

start_tunnel() {
  if [ -z "$(env_value CLOUDFLARE_TUNNEL_TOKEN)" ]; then
    log "CLOUDFLARE_TUNNEL_TOKEN is empty: not starting the tunnel (local trial)."
    log "the demo answers inside Docker only; check it with:"
    log "  docker exec testlookup_frontend wget -qO- http://127.0.0.1/api/v1/auth/options"
    return 0
  fi
  log "starting cloudflared (public)"
  "${COMPOSE[@]}" --profile public up -d cloudflared
}

# Read the tunnel token from a hidden prompt and store it in .env only. The
# token never reaches the screen, the shell history or a command line (`ps`):
# it is read with `read -s` and written with shell builtins. Paste either the
# bare token or the whole install command Cloudflare shows; the token is the
# word starting with "eyJ". Copy it in your own browser and paste it here.
# Do not run the install command itself on your computer: it installs a
# connector there, and Cloudflare then sends part of the demo's traffic to it.
cmd_token() {
  [ -f "$ENV_FILE" ] || die "no .env yet: run 'scripts/demo/demo.sh setup' first"
  local line="" word token="" tmp entry
  printf 'Paste the Cloudflare tunnel token (or the whole install command), then press Enter.\n'
  printf 'What you paste stays hidden.\n'
  IFS= read -rs line || true
  printf '\n'
  set -f # split the pasted line into words without expanding * or ?
  for word in $line; do
    case "$word" in eyJ*) token="$word" ;; esac
  done
  set +f
  [ -n "$token" ] || die "no token found (a tunnel token starts with eyJ); nothing changed"
  tmp="$(mktemp)"
  while IFS= read -r entry || [ -n "$entry" ]; do
    case "$entry" in CLOUDFLARE_TUNNEL_TOKEN=*) ;; *) printf '%s\n' "$entry" ;; esac
  done <"$ENV_FILE" >"$tmp"
  printf 'CLOUDFLARE_TUNNEL_TOKEN=%s\n' "$token" >>"$tmp"
  cat "$tmp" >"$ENV_FILE"
  rm -f "$tmp"
  chmod 600 "$ENV_FILE"
  log "stored the tunnel token in .env (${#token} characters)"
  if [ "$(docker inspect -f '{{.State.Health.Status}}' testlookup_backend 2>/dev/null || true)" = "healthy" ]; then
    start_tunnel
  else
    log "the stack is not running yet: 'scripts/demo/demo.sh reset' starts it with the tunnel"
  fi
}

cmd_reset() {
  [ -f "$ENV_FILE" ] || die "no .env yet: run 'scripts/demo/demo.sh setup' first"
  log "reset: taking the demo offline"
  "${COMPOSE[@]}" --profile public --profile demo stop cloudflared 2>/dev/null || true
  log "reset: removing containers and ALL demo data volumes"
  "${COMPOSE[@]}" --profile public --profile demo down --volumes --remove-orphans
  log "reset: starting the stack"
  "${COMPOSE[@]}" up -d
  wait_for_backend
  log "reset: loading the sample data (seed_dev_data.py)"
  "${COMPOSE[@]}" --profile demo run --rm seed-init
  start_tunnel
  docker image prune -f >/dev/null 2>&1 || true
  local beat; beat="$(env_value DEMO_HEARTBEAT_URL)"
  if [ -n "$beat" ]; then
    curl -fsS -m 10 --retry 3 "$beat" >/dev/null || log "heartbeat ping failed (the reset itself succeeded)"
  fi
  log "reset: done"
}

cmd_setup() {
  ensure_env
  cmd_images
  cmd_reset
  log "setup complete. Next: 'scripts/demo/demo.sh token' stores the tunnel token and"
  log "starts the tunnel; then 'sudo scripts/demo/demo.sh install-cron'."
}

cmd_update() {
  log "update: pulling the latest code"
  git pull --ff-only
  ensure_env
  cmd_images
  cmd_reset
}

cmd_status() {
  "${COMPOSE[@]}" --profile public ps
  echo
  docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}'
}

cmd_install_cron() {
  [ "$(id -u)" -eq 0 ] || die "install-cron writes /etc/cron.d: run it with sudo"
  local owner; owner="$(stat -c %U "$REPO")"
  cat >/etc/cron.d/testlookup-demo <<CRON
# TestLookup public demo: rebuild all data from seed every night, and watch
# for miners, runaway CPU and egress drift every 5 minutes (as root: it kills
# processes and reads every socket).
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * ${owner} ${REPO}/scripts/demo/demo.sh reset >> /var/log/testlookup-demo-reset.log 2>&1
*/5 * * * * root ${REPO}/scripts/demo/watchdog.sh >> /var/log/testlookup-demo-watchdog.log 2>&1
CRON
  chmod 644 /etc/cron.d/testlookup-demo
  touch /var/log/testlookup-demo-reset.log /var/log/testlookup-demo-watchdog.log
  chown "$owner" /var/log/testlookup-demo-reset.log
  chmod 600 /var/log/testlookup-demo-watchdog.log
  cat >/etc/logrotate.d/testlookup-demo <<ROTATE
/var/log/testlookup-demo-reset.log /var/log/testlookup-demo-watchdog.log {
  weekly
  rotate 4
  compress
  missingok
  notifempty
}
ROTATE
  log "installed /etc/cron.d/testlookup-demo (reset 03:00 daily as ${owner}; watchdog every 5 min)"
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    setup)        take_lock; cmd_setup ;;
    build)        take_lock; cmd_build ;;
    pull)         take_lock; cmd_pull ;;
    token)        cmd_token ;;
    reset)        take_lock; cmd_reset ;;
    update)       take_lock; cmd_update ;;
    status)       cmd_status ;;
    install-cron) cmd_install_cron ;;
    *) sed -n '2,/^# =====/p' "$0" | sed -n '2,$p'; exit 2 ;;
  esac
}

main "$@"
