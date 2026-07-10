# shellcheck shell=bash
# ============================================================
# TestLookup ops — shared helpers for backup/restore/upgrade
# ============================================================
# Sourced by backup.sh / restore.sh / preflight.sh / upgrade.sh.
# Everything here talks to the stack through `docker compose`
# (exec/run) so the host needs nothing beyond docker + bash.
#
# Override points (all optional):
#   TL_COMPOSE   full compose invocation, e.g.
#                TL_COMPOSE="docker compose -f docker-compose.release.yml -p testlookup"
#   BACKUP_DIR   where archives land (default: <repo>/backups)
#   TL_API       backend base URL for health checks (default http://localhost:8000)
# ============================================================

set -euo pipefail

# Git Bash (MSYS) rewrites container-side paths like /data into Windows paths
# when calling native executables. Disable that: container paths must be passed
# through verbatim; host paths are converted explicitly via host_path().
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

OPS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$OPS_LIB_DIR/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
TL_API="${TL_API:-http://localhost:8000}"

# App services in dependency order (datastores are NOT in this list).
# seed-init / minio-setup are one-shot containers; stopping them is a no-op.
APP_SERVICES=(backend worker beat flower frontend mcp seed-init minio-setup)
DATASTORE_SERVICES=(postgres mongo redis minio)

compose() {
    # shellcheck disable=SC2086  # TL_COMPOSE is intentionally word-split
    ${TL_COMPOSE:-docker compose} "$@"
}

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Convert a POSIX-ish host path into something `docker run -v` accepts on every
# platform. On Git Bash, /c/Users/... must become C:/Users/... explicitly
# because MSYS path conversion is disabled above.
host_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$1"
    else
        printf '%s\n' "$1"
    fi
}

# Space-separated list of services defined by the active compose file(s).
defined_services() {
    compose config --services 2>/dev/null || true
}

service_defined() {
    defined_services | grep -qx "$1"
}

service_running() {
    compose ps --status running --services 2>/dev/null | grep -qx "$1"
}

require_running() {
    local s
    for s in "$@"; do
        service_running "$s" || die "service '$s' is not running — start the stack first (make dev / docker compose up -d)"
    done
}

# Named volume mounted at <dest> inside <service>'s container.
volume_for() {
    local service="$1" dest="$2" cid
    cid="$(compose ps -q "$service")"
    [ -n "$cid" ] || die "no container found for service '$service'"
    docker inspect -f '{{range .Mounts}}{{if eq .Destination "'"$dest"'"}}{{.Name}}{{end}}{{end}}' "$cid"
}

# Alembic head currently recorded in the database (empty when unavailable —
# e.g. postgres down or a fresh cluster with no alembic_version table yet).
pg_alembic_head() {
    compose exec -T postgres sh -c \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version" 2>/dev/null' \
        2>/dev/null | tr -d '[:space:]' || true
}

# Read a flat string value out of a manifest.json WE wrote (backup.sh emits
# one key per line) — deliberately not a general JSON parser, so restore has
# no host-side python/jq dependency.
manifest_get() {
    local file="$1" key="$2"
    sed -n 's/.*"'"$key"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$file" | head -1
}

# Poll <url> until HTTP 200 or <timeout> seconds elapse.
wait_for_ready() {
    local url="$1" timeout="${2:-300}" waited=0
    until curl -sf "$url" >/dev/null 2>&1; do
        sleep 5
        waited=$((waited + 5))
        if [ "$waited" -ge "$timeout" ]; then
            return 1
        fi
        printf '.'
    done
    printf '\n'
    return 0
}

run_smoke() {
    ( cd "$REPO_ROOT" && { python3 scripts/smoke.py 2>/dev/null || python scripts/smoke.py; } )
}

# Stop the application layer, keep datastores up. Filters against the services
# the active compose file actually defines (release vs dev vs lite differ).
stop_app_services() {
    local to_stop=() s
    for s in "${APP_SERVICES[@]}"; do
        service_defined "$s" && to_stop+=("$s")
    done
    if [ "${#to_stop[@]}" -gt 0 ]; then
        compose stop "${to_stop[@]}"
    fi
}
