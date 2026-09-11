# shellcheck shell=sh
# Shared helpers for backup.sh / restore.sh. POSIX sh: postgres:16-alpine
# runs busybox ash and mongo:7 runs dash.

log() { printf '%s %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SCRIPT:-backup}" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Sets PG_DBNAME (a libpq connection string or a database name) and, when
# only the POSTGRES_* pieces are configured, the libpq PG* variables.
# DATABASE_URL is SQLAlchemy's `postgresql+asyncpg://...`; libpq wants
# `postgresql://...`, so the driver suffix is dropped.
pg_setup() {
    if [ -n "${DATABASE_URL:-}" ]; then
        PG_DBNAME=$(printf '%s' "$DATABASE_URL" | sed 's#^postgresql+[A-Za-z0-9]*://#postgresql://#')
    else
        PGHOST="${POSTGRES_HOST:?neither DATABASE_URL nor POSTGRES_HOST is set}"
        PGPORT="${POSTGRES_PORT:-5432}"
        PGUSER="${POSTGRES_USER:?POSTGRES_USER is not set}"
        PGPASSWORD="${POSTGRES_PASSWORD:-}"
        export PGHOST PGPORT PGUSER PGPASSWORD
        PG_DBNAME="${POSTGRES_DB:?POSTGRES_DB is not set}"
    fi
}

mongo_uri() {
    if [ -n "${MONGO_URI:-}" ]; then
        printf '%s' "$MONGO_URI"
    else
        printf 'mongodb://%s:%s' "${MONGO_HOST:?neither MONGO_URI nor MONGO_HOST is set}" "${MONGO_PORT:-27017}"
    fi
}

# Wait until a store accepts connections before touching it (re-audit M25,
# found live on the homelab). k3s enforces NetworkPolicy with kube-router,
# which programs a NEW pod's label-matched allow rules asynchronously after the
# pod starts; until then base default-deny REJECTs its traffic and pg_dump
# failed in its first second with "Connection refused" (twice, backoffLimit 1).
# Calico and others have similar startup windows. Namespace-wide rules (DNS)
# work at once, which is why name resolution succeeded.
#   wait_for <store> <readiness command...>
# Polls every BACKUP_WAIT_INTERVAL s (default 2) until the command succeeds,
# and dies once BACKUP_WAIT_SECONDS (default 120) have passed.
wait_for() {
    store=$1
    shift
    limit="${BACKUP_WAIT_SECONDS:-120}"
    interval="${BACKUP_WAIT_INTERVAL:-2}"
    case "$limit" in ''|*[!0-9]*) die "BACKUP_WAIT_SECONDS must be a whole number of seconds, got '$limit'" ;; esac
    case "$interval" in ''|*[!0-9]*|0) die "BACKUP_WAIT_INTERVAL must be a positive integer, got '$interval'" ;; esac
    start=$(date +%s)
    while :; do
        if "$@" >/dev/null 2>&1; then
            log "$store: accepting connections after $(( $(date +%s) - start ))s"
            return 0
        fi
        elapsed=$(( $(date +%s) - start ))
        if [ "$elapsed" -ge "$limit" ]; then
            die "$store: not reachable after ${elapsed}s (BACKUP_WAIT_SECONDS=$limit); check the NetworkPolicies and the store"
        fi
        log "$store: not accepting connections yet (${elapsed}s elapsed); retrying in ${interval}s"
        sleep "$interval"
    done
}

# Readiness probes, against exactly the target the dump/restore will use.
postgres_ready() { pg_isready -d "$PG_DBNAME" -t 2; }
mongo_ready() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 5 mongosh --quiet "$1" --eval 'db.adminCommand({ ping: 1 }).ok'
    else
        mongosh --quiet "$1" --eval 'db.adminCommand({ ping: 1 }).ok'
    fi
}
