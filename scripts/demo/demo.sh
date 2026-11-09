#!/usr/bin/env bash
# ============================================================================
# TestLookup public read-only demo — one script for the demo VM.
# Runbook: docs/operations/public-demo.md
#
#   scripts/demo/demo.sh setup         first run: .env, build images, seed, start
#   scripts/demo/demo.sh build         (re)build the app images from this checkout
#   scripts/demo/demo.sh reset         wipe all data and re-seed (nightly cron)
#   scripts/demo/demo.sh update        git pull, rebuild, reset
#   scripts/demo/demo.sh status        containers and memory
#   sudo scripts/demo/demo.sh install-cron   nightly reset at 03:00 (VM time)
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
env_value() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
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
  # Images built on this VM by `build`.
  set_env TESTLOOKUP_IMAGE testlookup-demo
  set_env TESTLOOKUP_VERSION local
  # A small VM: fewer worker processes.
  set_env CELERY_CONCURRENCY 2
  set_env CELERY_CHILDREN_CONCURRENCY 1
  [ -n "$(env_value DEMO_USERNAME)" ] || set_env DEMO_USERNAME qa_lead
  grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=' "$ENV_FILE" || set_env CLOUDFLARE_TUNNEL_TOKEN ""
  chmod 600 "$ENV_FILE"
}

cmd_build() {
  log "building $BACKEND_IMAGE (backend/, target production)"
  docker build --target production -t "$BACKEND_IMAGE" backend
  log "building $FRONTEND_IMAGE (frontend/, target production)"
  docker build --target production -t "$FRONTEND_IMAGE" frontend
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
    log "the demo is reachable on this VM only: http://127.0.0.1:3000"
    return 0
  fi
  log "starting cloudflared (public)"
  "${COMPOSE[@]}" --profile public up -d cloudflared
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
  cmd_build
  cmd_reset
  log "setup complete. Next: put the tunnel token in .env (CLOUDFLARE_TUNNEL_TOKEN=...),"
  log "run 'scripts/demo/demo.sh reset', then 'sudo scripts/demo/demo.sh install-cron'."
}

cmd_update() {
  log "update: pulling the latest code"
  git pull --ff-only
  ensure_env
  cmd_build
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
# TestLookup public demo: rebuild all data from seed every night.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * ${owner} ${REPO}/scripts/demo/demo.sh reset >> /var/log/testlookup-demo-reset.log 2>&1
CRON
  chmod 644 /etc/cron.d/testlookup-demo
  touch /var/log/testlookup-demo-reset.log
  chown "$owner" /var/log/testlookup-demo-reset.log
  cat >/etc/logrotate.d/testlookup-demo <<ROTATE
/var/log/testlookup-demo-reset.log {
  weekly
  rotate 4
  compress
  missingok
  notifempty
}
ROTATE
  log "installed /etc/cron.d/testlookup-demo (03:00 daily, as ${owner})"
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    setup)        take_lock; cmd_setup ;;
    build)        take_lock; cmd_build ;;
    reset)        take_lock; cmd_reset ;;
    update)       take_lock; cmd_update ;;
    status)       cmd_status ;;
    install-cron) cmd_install_cron ;;
    *) sed -n '2,15p' "$0"; exit 2 ;;
  esac
}

main "$@"
