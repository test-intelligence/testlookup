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
