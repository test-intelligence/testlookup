#!/usr/bin/env bash
# ============================================================================
# TestLookup public demo VM — watchdog. Runs as root from cron every 5
# minutes (installed by `sudo scripts/demo/demo.sh install-cron`).
# Runbook: docs/operations/public-demo.md ("Security").
#
# Checks, cheapest first:
#   1. Miner processes (xmrig, kinsing, stratum URLs, ...). Inside a
#      container: kill them and rebuild every container from its image
#      (demo.sh reset). On the host itself: kill and alert; a host compromise
#      means rebuilding the VM, which this script does not try to do.
#   2. Sustained CPU: the host busier than WATCHDOG_CPU_BUSY_PCT (85) % for
#      WATCHDOG_CPU_STRIKES (3) runs in a row, i.e. 15 minutes. Alert with the
#      top processes, then reset.
#   3. Egress drift: any container other than cloudflared attached to a
#      network with a route out. Alert (the compose files keep it closed).
#   4. Unexpected listening ports on the host, and outbound connections to
#      well-known mining-pool ports. Alert; a pool connection's process is
#      killed.
#
# Alerts go to the log (cron appends stdout to
# /var/log/testlookup-demo-watchdog.log) and, when .env sets DEMO_ALERT_URL,
# are POSTed there as plain text: an ntfy.sh topic URL or a healthchecks.io
# "/fail" URL both work. The same alert repeats at most once an hour, and the
# watchdog resets the demo at most once an hour.
#
#   WATCHDOG_DRY_RUN=1   report only: no kill, no reset
#   WATCHDOG_NO_RESET=1  kill, but do not reset
# ============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO/.env"
STATE_DIR="${WATCHDOG_STATE_DIR:-/var/lib/testlookup-demo-watchdog}"
PROC_ROOT="${WATCHDOG_PROC_ROOT:-/proc}" # tests point this at a fake /proc
CPU_BUSY_PCT="${WATCHDOG_CPU_BUSY_PCT:-85}"
CPU_STRIKES="${WATCHDOG_CPU_STRIKES:-3}"
DRY_RUN="${WATCHDOG_DRY_RUN:-0}"
NO_RESET="${WATCHDOG_NO_RESET:-0}"
EGRESS_CONTAINER="testlookup_cloudflared"

# Command lines a miner gives itself away by. Specific names only: a generic
# word would match a legitimate process and reset the demo for nothing.
MINER_PATTERN='xmrig|xmr-stak|minerd|cpuminer|ccminer|kdevtmpfsi|kinsing|nanominer|lolminer|stratum\+(tcp|ssl|tls)://|--donate-level|cryptonight'
# Pool ports seen in mining malware; the demo itself never dials any of them.
POOL_PORTS=' 3333 4444 5555 6666 7777 14433 14444 45560 45700 '

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

log() { printf '[watchdog %s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

env_value() {
  [ -f "$ENV_FILE" ] || return 0
  { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
}

# alert KEY MESSAGE: log always; send to DEMO_ALERT_URL at most once an hour per KEY.
alert() {
  local key="$1" message="$2" stamp="$STATE_DIR/alert-$1" url now
  log "ALERT [$key] $message"
  now="$(date +%s)"
  if [ -f "$stamp" ] && [ $((now - $(cat "$stamp"))) -lt 3600 ]; then
    return 0
  fi
  printf '%s\n' "$now" >"$stamp"
  url="$(env_value DEMO_ALERT_URL)"
  [ -n "$url" ] || return 0
  curl -fsS -m 10 --retry 2 -H "Title: TestLookup demo watchdog: $key" \
    --data-binary "$(uname -n): $message" "$url" >/dev/null || log "alert delivery to DEMO_ALERT_URL failed"
}

# reset_demo REASON: rebuild every container from its image, at most once an hour.
reset_demo() {
  local reason="$1" stamp="$STATE_DIR/last-reset" now owner
  if [ "$DRY_RUN" = "1" ] || [ "$NO_RESET" = "1" ]; then
    log "would reset the demo ($reason), skipped by WATCHDOG_DRY_RUN/NO_RESET"
    return 0
  fi
  now="$(date +%s)"
  if [ -f "$stamp" ] && [ $((now - $(cat "$stamp"))) -lt 3600 ]; then
    alert reset-suppressed "the demo was reset less than an hour ago; not resetting again ($reason)"
    return 0
  fi
  printf '%s\n' "$now" >"$stamp"
  owner="$(stat -c %U "$REPO")"
  log "resetting the demo: $reason"
  runuser -u "$owner" -- "$REPO/scripts/demo/demo.sh" reset || alert reset-failed "demo.sh reset failed after: $reason"
}

in_container() { grep -qE "docker|containerd" "$PROC_ROOT/$1/cgroup" 2>/dev/null; }

# ── 1. Miner processes ───────────────────────────────────────────────────────
# Take the process list first and match afterwards, so the match never sees
# itself.
processes="$(ps -eo pid=,args= 2>/dev/null || true)"
miners="$(printf '%s\n' "$processes" | grep -Ei "$MINER_PATTERN" | grep -v "^ *$$ " || true)"
if [ -n "$miners" ]; then
  container_hit=0
  while read -r pid args; do
    [ -n "$pid" ] || continue
    where="host"
    if in_container "$pid"; then where="container"; container_hit=1; fi
    alert miner "miner-like process in a $where: pid $pid: ${args:0:200}"
    [ "$DRY_RUN" = "1" ] || kill -9 "$pid" 2>/dev/null || true
    [ "$where" = "host" ] && alert host-compromise "a miner ran on the host itself, outside Docker: rebuild the VM from the runbook and rotate the tunnel token"
  done <<<"$miners"
  [ "$container_hit" = "1" ] && reset_demo "miner process in a container"
fi

# ── 2. Sustained CPU ─────────────────────────────────────────────────────────
read -r _ user nice system idle iowait irq softirq steal _ <"$PROC_ROOT/stat"
total=$((user + nice + system + idle + iowait + irq + softirq + steal))
idle_all=$((idle + iowait))
cpu_state="$STATE_DIR/cpu"
if [ -f "$cpu_state" ]; then
  read -r prev_total prev_idle strikes <"$cpu_state"
  delta_total=$((total - prev_total))
  delta_idle=$((idle_all - prev_idle))
  busy=0
  [ "$delta_total" -gt 0 ] && busy=$(((delta_total - delta_idle) * 100 / delta_total))
  if [ "$busy" -ge "$CPU_BUSY_PCT" ]; then
    strikes=$((strikes + 1))
    log "cpu ${busy}% busy since the last run (strike $strikes of $CPU_STRIKES)"
  else
    strikes=0
  fi
  if [ "$strikes" -ge "$CPU_STRIKES" ]; then
    top="$(ps -eo pcpu=,pid=,comm= --sort=-pcpu | head -5 | tr -s ' ' | paste -sd ';' -)"
    alert cpu "host CPU at or above ${CPU_BUSY_PCT}% for ${strikes} runs in a row; top: $top"
    reset_demo "sustained CPU"
    strikes=0
  fi
else
  strikes=0
fi
printf '%s %s %s\n' "$total" "$idle_all" "$strikes" >"$cpu_state"

# ── 3. Egress drift ──────────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  while read -r name; do
    [ -n "$name" ] && [ "$name" != "$EGRESS_CONTAINER" ] || continue
    for network in $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$name" 2>/dev/null); do
      if [ "$(docker network inspect -f '{{.Internal}}' "$network" 2>/dev/null)" != "true" ]; then
        alert egress "container $name is on network $network, which has a route out (only $EGRESS_CONTAINER may)"
      fi
    done
  done < <(docker ps --format '{{.Names}}' 2>/dev/null || true)
fi

# ── 4. Listeners and pool connections ────────────────────────────────────────
while read -r address; do
  case "$address" in
    0.0.0.0:22 | "[::]:22" | 127.0.0.53%lo:53 | 127.0.0.54:53 | 127.0.0.1:*) ;;
    *) alert listener "unexpected listening socket on the host: $address" ;;
  esac
done < <(ss -ltnH 2>/dev/null | awk '{print $4}' | sort -u)

while read -r remote process; do
  port="${remote##*:}"
  case "$POOL_PORTS" in
    *" $port "*)
      alert pool "outbound connection to mining-pool port $remote: $process"
      pid="$(printf '%s' "$process" | sed -nE 's/.*pid=([0-9]+).*/\1/p' | head -1)"
      [ -n "$pid" ] && [ "$DRY_RUN" != "1" ] && kill -9 "$pid" 2>/dev/null || true
      ;;
  esac
done < <(ss -tnpH state established 2>/dev/null | awk '{print $4, $5}')

exit 0
