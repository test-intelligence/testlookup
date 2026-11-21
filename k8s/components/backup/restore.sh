#!/bin/sh
# TestLookup Kubernetes restore (re-audit M25). One phase per container of a
# job created from the suspended testlookup-restore CronJob. Runbook: README.md.
#
#   restore.sh verify     check $BACKUP_DIR/$RESTORE_FILE member by member and
#                         digest by digest; only then stage it for the others
#   restore.sh postgres   pg_restore --clean --if-exists --single-transaction
#   restore.sh mongo      mongorestore --drop
#   (minio_sync.py restore re-uploads the objects)
set -eu
SCRIPT=restore
. "$(dirname "$0")/lib.sh"

STAGING="${STAGING:-/staging}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
# The whole of an archive backup.sh writes: nothing more, nothing less.
MEMBERS="manifest.json postgres.dump mongo.archive.gz minio_data.tar.gz"
# Where verify unpacks. The later phases read $STAGING/<file>, never this.
QUARANTINE=".verify"

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

    # Nothing of the last attempt is left for the later phases to pick up.
    rm -rf "${STAGING:?}"/* "$STAGING/$QUARANTINE"

    # 1. The archive is judged by its listing, before a byte of it is written
    #    (security review 2026-11-20, TL-07; the Compose restore does the same
    #    in backend/scripts/ops_support.py). It must hold exactly the four
    #    members backup.sh writes, each once, each a regular file. A path, a
    #    link, a device, a fifth member or a missing one refuses the restore.
    if [ -n "${RESTORE_MAX_ARCHIVE_BYTES:-}" ]; then
        case "$RESTORE_MAX_ARCHIVE_BYTES" in
            *[!0-9]*) die "RESTORE_MAX_ARCHIVE_BYTES must be a whole number of bytes" ;;
        esac
        size=$(wc -c < "$archive" | tr -d ' ')
        [ "$size" -le "$RESTORE_MAX_ARCHIVE_BYTES" ] \
            || die "$name is $size bytes, over RESTORE_MAX_ARCHIVE_BYTES ($RESTORE_MAX_ARCHIVE_BYTES); nothing was restored"
    fi
    names=$(tar tzf "$archive") || die "$name is not a readable gzip tar archive; nothing was restored"
    types=$(tar tzvf "$archive" | cut -c1 | sort -u | tr -d '\n') \
        || die "$name is not a readable gzip tar archive; nothing was restored"
    [ "$types" = "-" ] \
        || die "$name holds a member that is not a regular file (a link, directory or device); nothing was restored"
    [ "$(printf '%s\n' "$names" | sort)" = "$(printf '%s\n' $MEMBERS | sort)" ] \
        || die "$name does not hold exactly manifest.json and the three component files (found: $(printf '%s' "$names" | tr '\n' ' ')); not a TestLookup Kubernetes backup (a Compose archive is restored with 'make restore'); nothing was restored"

    # 2. Extract those four names, and only those, into a directory no later
    #    phase reads.
    stage="$STAGING/$QUARANTINE"
    mkdir "$stage" || die "cannot create $stage"
    # A refusal below leaves nothing behind, the unpacked copy included.
    trap 'rm -rf "$STAGING/$QUARANTINE"' EXIT
    # shellcheck disable=SC2086  # MEMBERS is a word list on purpose
    tar xzf "$archive" -C "$stage" $MEMBERS || die "cannot extract $name"
    for f in $MEMBERS; do
        [ -f "$stage/$f" ] && [ ! -L "$stage/$f" ] && [ -s "$stage/$f" ] \
            || die "$f in $name is missing, empty or not a regular file; nothing was restored"
    done

    # 3. The manifest must be one this script understands, and must name each
    #    file the later phases restore, with that file's digest. backup.sh
    #    writes one component per line.
    manifest="$stage/manifest.json"
    [ "$(wc -c < "$manifest" | tr -d ' ')" -le 65536 ] || die "manifest.json in $name is too large to be a manifest"
    grep -q '"source": "kubernetes-cronjob"' "$manifest" \
        || die "$name was not written by the Kubernetes backup (restore a Compose archive with 'make restore')"
    grep -q '^  "schema_version": 2,$' "$manifest" \
        || die "$name has a manifest schema this restore does not support (it reads schema_version 2)"
    [ "$(grep -c '"file":' "$manifest")" = "3" ] \
        || die "manifest.json in $name does not list exactly three components"
    for pair in postgres:postgres.dump mongo:mongo.archive.gz minio:minio_data.tar.gz; do
        component=${pair%%:*}
        file=${pair#*:}
        pattern=$(printf '%s' "$file" | sed 's/\./\\./g')
        want=$(sed -n "s/^ *{\"name\": \"$component\", \"file\": \"$pattern\", .*\"sha256\": \"\([0-9a-f]\{64\}\)\"},\{0,1\}\$/\1/p" "$manifest")
        [ "$(printf '%s' "$want" | wc -c | tr -d ' ')" = "64" ] \
            || die "manifest.json in $name carries no single sha256 for $component ($file); nothing was restored"
        got=$(sha256sum "$stage/$file" | cut -d' ' -f1)
        [ "$got" = "$want" ] \
            || die "digest mismatch for $file: $name is corrupt or was altered; nothing was restored"
    done

    # 4. Only now do the files the postgres, mongo and minio phases read exist.
    for f in $MEMBERS; do
        mv "$stage/$f" "$STAGING/$f" || die "cannot stage $f"
    done
    rmdir "$stage"
    head=$(sed -n 's/.*"alembic_head": "\([A-Za-z0-9_]*\)".*/\1/p' "$STAGING/manifest.json")
    log "verified $name (alembic head ${head:-unknown}); restoring"
}

phase_postgres() {
    pg_setup
    wait_for postgres postgres_ready
    pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error \
        --dbname="$PG_DBNAME" "$STAGING/postgres.dump" || die "pg_restore failed"
    log "postgres restored"
}

phase_mongo() {
    uri=$(mongo_uri)
    wait_for mongo mongo_ready "$uri"
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
