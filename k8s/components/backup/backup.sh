#!/bin/sh
# TestLookup Kubernetes backup (re-audit M25). One phase per container of the
# testlookup-backup CronJob; phases share $STAGING (an emptyDir).
#
#   backup.sh postgres   pg_dump -Fc          -> $STAGING/postgres.dump (+ alembic_head)
#   backup.sh mongo      mongodump --gzip     -> $STAGING/mongo.archive.gz
#   (minio_sync.py dump                       -> $STAGING/minio_data.tar.gz)
#   backup.sh finalize   manifest.json + one archive in $BACKUP_DIR, retention
#
# Every artifact is written to a .tmp name and renamed only when complete, and
# finalize refuses to run unless all three exist and are non-empty: a failed
# phase can never produce an archive that looks like a backup.
set -eu
SCRIPT=backup
. "$(dirname "$0")/lib.sh"

STAGING="${STAGING:-/staging}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
COMPONENTS="postgres.dump mongo.archive.gz minio_data.tar.gz"

phase_postgres() {
    pg_setup
    rm -f "$STAGING/postgres.dump" "$STAGING/postgres.dump.tmp" "$STAGING/alembic_head"
    pg_dump --format=custom --no-owner --dbname="$PG_DBNAME" --file="$STAGING/postgres.dump.tmp" \
        || die "pg_dump failed"
    [ -s "$STAGING/postgres.dump.tmp" ] || die "pg_dump produced an empty file"
    head=$(psql --dbname="$PG_DBNAME" -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null \
        | tr -cd 'A-Za-z0-9_') || head=""
    printf '%s\n' "${head:-unknown}" > "$STAGING/alembic_head"
    mv "$STAGING/postgres.dump.tmp" "$STAGING/postgres.dump"
    log "postgres: $(wc -c < "$STAGING/postgres.dump") bytes, alembic head ${head:-unknown}"
}

phase_mongo() {
    uri=$(mongo_uri)
    rm -f "$STAGING/mongo.archive.gz" "$STAGING/mongo.archive.gz.tmp"
    mongodump --quiet --uri="$uri" --archive="$STAGING/mongo.archive.gz.tmp" --gzip \
        || die "mongodump failed"
    [ -s "$STAGING/mongo.archive.gz.tmp" ] || die "mongodump produced an empty file"
    mv "$STAGING/mongo.archive.gz.tmp" "$STAGING/mongo.archive.gz"
    log "mongo: $(wc -c < "$STAGING/mongo.archive.gz") bytes"
}

sha() { sha256sum "$1" | cut -d' ' -f1; }

phase_finalize() {
    keep="${BACKUP_KEEP:-14}"
    case "$keep" in
        ''|*[!0-9]*|0) die "BACKUP_KEEP must be a positive integer, got '$keep'" ;;
    esac
    for f in $COMPONENTS; do
        [ -s "$STAGING/$f" ] || die "$f is missing or empty: an earlier phase failed; refusing to write a partial backup"
    done

    ts=$(date -u +%Y%m%dT%H%M%SZ)
    head=$(tr -cd 'A-Za-z0-9_' < "$STAGING/alembic_head" 2>/dev/null || true)
    [ -n "$head" ] || head=unknown
    version=$(printf '%s' "${APP_VERSION:-unknown}" | tr -cd 'A-Za-z0-9._+-')
    [ -n "$version" ] || version=unknown

    # One component per line: restore.sh reads the digests back with sed.
    cat > "$STAGING/manifest.json" <<EOF
{
  "schema_version": 2,
  "source": "kubernetes-cronjob",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "app_version": "$version",
  "git_sha": "unknown",
  "alembic_head": "$head",
  "quiesced": "0",
  "components": [
    {"name": "postgres", "file": "postgres.dump", "method": "pg_dump -Fc", "sha256": "$(sha "$STAGING/postgres.dump")"},
    {"name": "mongo", "file": "mongo.archive.gz", "method": "mongodump --archive --gzip", "sha256": "$(sha "$STAGING/mongo.archive.gz")"},
    {"name": "minio", "file": "minio_data.tar.gz", "method": "S3 object export <bucket>/<key> (minio_sync.py)", "sha256": "$(sha "$STAGING/minio_data.tar.gz")"}
  ],
  "excluded": [
    {"name": "redis", "reason": "Celery broker + caches: stale queue entries must not be replayed after a restore"},
    {"name": "chromadb", "reason": "semantic-search vectors: rebuilt from Postgres by the hourly reindex_search beat"},
    {"name": "ollama", "reason": "model blobs: re-pull the model"}
  ]
}
EOF

    mkdir -p "$BACKUP_DIR"
    # Leftovers of a killed run. concurrencyPolicy: Forbid, so none is live.
    rm -f "$BACKUP_DIR"/.testlookup-backup-*.tmp
    tmp="$BACKUP_DIR/.testlookup-backup-$ts.tar.gz.tmp"
    final="$BACKUP_DIR/testlookup-backup-$ts.tar.gz"
    # shellcheck disable=SC2086  # COMPONENTS is a word list on purpose
    tar czf "$tmp" -C "$STAGING" manifest.json $COMPONENTS
    mv "$tmp" "$final"
    log "wrote $final ($(wc -c < "$final") bytes)"

    # Retention: keep the newest $keep archives. Names embed a UTC timestamp,
    # so name order is age order; files that are not archives are left alone.
    n=0
    for old in $(ls -1 "$BACKUP_DIR" | grep -E '^testlookup-backup-[0-9]{8}T[0-9]{6}Z\.tar\.gz$' | sort -r); do
        n=$((n + 1))
        if [ "$n" -gt "$keep" ]; then
            rm -f "$BACKUP_DIR/$old"
            log "retention: removed $old"
        fi
    done
}

case "${1:-}" in
    postgres) phase_postgres ;;
    mongo) phase_mongo ;;
    finalize) phase_finalize ;;
    *) die "usage: backup.sh postgres|mongo|finalize" ;;
esac
