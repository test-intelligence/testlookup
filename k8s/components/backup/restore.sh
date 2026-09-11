#!/bin/sh
# TestLookup Kubernetes restore (re-audit M25). One phase per container of a
# job created from the suspended testlookup-restore CronJob. Runbook: README.md.
#
#   restore.sh verify     extract $BACKUP_DIR/$RESTORE_FILE, check every sha256
#   restore.sh postgres   pg_restore --clean --if-exists --single-transaction
#   restore.sh mongo      mongorestore --drop
#   (minio_sync.py restore re-uploads the objects)
set -eu
SCRIPT=restore
. "$(dirname "$0")/lib.sh"

STAGING="${STAGING:-/staging}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"

phase_verify() {
    [ "${RESTORE_CONFIRM:-}" = "yes" ] \
        || die "RESTORE_CONFIRM is not 'yes'. A restore REPLACES the current data; see README.md"
    name="${RESTORE_FILE:-}"
    [ -n "$name" ] || die "RESTORE_FILE is empty: name an archive in the backups volume"
    case "$name" in
        */*|.*) die "RESTORE_FILE must be a bare archive name, got '$name'" ;;
    esac
    archive="$BACKUP_DIR/$name"
    [ -s "$archive" ] || die "no such archive in the backups volume: $name"

    rm -rf "${STAGING:?}"/*
    tar xzf "$archive" -C "$STAGING" || die "cannot extract $name"
    manifest="$STAGING/manifest.json"
    [ -f "$manifest" ] || die "$name has no manifest.json; not a TestLookup backup"
    grep -q '"source": "kubernetes-cronjob"' "$manifest" \
        || die "$name was not written by the Kubernetes backup (restore a Compose archive with 'make restore')"

    sed -n 's/.*"file": "\([^"]*\)".*"sha256": "\([0-9a-f]\{64\}\)".*/\2  \1/p' "$manifest" \
        > "$STAGING/SHA256SUMS"
    [ "$(wc -l < "$STAGING/SHA256SUMS" | tr -d ' ')" = "3" ] \
        || die "manifest.json does not carry a digest for all three components"
    (cd "$STAGING" && sha256sum -c SHA256SUMS) \
        || die "digest mismatch: $name is corrupt or was altered; nothing was restored"
    head=$(sed -n 's/.*"alembic_head": "\([^"]*\)".*/\1/p' "$manifest")
    log "verified $name (alembic head ${head:-unknown}); restoring"
}

phase_postgres() {
    pg_setup
    pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error \
        --dbname="$PG_DBNAME" "$STAGING/postgres.dump" || die "pg_restore failed"
    log "postgres restored"
}

phase_mongo() {
    uri=$(mongo_uri)
    mongorestore --quiet --uri="$uri" --archive="$STAGING/mongo.archive.gz" --gzip --drop \
        || die "mongorestore failed"
    log "mongo restored"
}

case "${1:-}" in
    verify) phase_verify ;;
    postgres) phase_postgres ;;
    mongo) phase_mongo ;;
    *) die "usage: restore.sh verify|postgres|mongo" ;;
esac
