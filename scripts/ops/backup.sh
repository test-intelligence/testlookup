#!/usr/bin/env bash
# ============================================================
# TestLookup — one-command backup (US-11.1)
# ============================================================
# Produces ONE timestamped archive under ./backups/:
#
#   backups/testlookup-backup-<UTC-timestamp>.tar.gz
#     ├── manifest.json        versions, alembic head, component list
#     ├── postgres.dump        pg_dump custom format (all relational data)
#     ├── mongo.archive.gz     mongodump archive (pipeline/audit event logs)
#     └── minio_data.tar.gz    MinIO volume (reports, compliance packs, RAG docs)
#
# Excluded BY DESIGN (rebuildable state — see the restore checklist):
#   - redis    : Celery broker + caches; restoring stale queue entries would
#                replay tasks against a database that no longer matches them.
#   - chromadb : semantic-search vectors; the hourly `reindex_search` beat
#                rebuilds them from Postgres within an hour of a restore.
#   - ollama   : downloaded model blobs; re-pull with `make pull-llm`.
#
# MinIO approach: we tar the named volume through a throwaway busybox
# container instead of `mc mirror`. Rationale: needs zero credentials and no
# network wiring (mc would need the access keys + the compose network name,
# both of which vary per install), captures bucket metadata/policies exactly,
# and works even when the minio image tag changes. Trade-off: an object
# written mid-tar can be captured partially — run backups at a quiet moment,
# or set QUIESCE=1 to stop the app layer for the duration.
#
# Usage:
#   make backup                        # or: bash scripts/ops/backup.sh
#   BACKUP_DIR=/mnt/nas make backup    # custom destination
#   QUIESCE=1 make backup              # stop app containers during backup
#   TL_COMPOSE="docker compose -f docker-compose.release.yml" make backup
# ============================================================

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

QUIESCE="${QUIESCE:-0}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_DIR/testlookup-backup-$TS.tar.gz"
STAGING="$BACKUP_DIR/.staging-$TS-$$"

mkdir -p "$BACKUP_DIR"
mkdir -p "$STAGING"

RESUME_APP=0
cleanup() {
    rm -rf "$STAGING"
    if [ "$RESUME_APP" = "1" ]; then
        log "Restarting application containers (QUIESCE=1)..."
        compose up -d >/dev/null 2>&1 || warn "could not restart app containers — run: docker compose up -d"
    fi
}
trap cleanup EXIT

require_running postgres mongo minio

if [ "$QUIESCE" = "1" ]; then
    log "QUIESCE=1 — stopping application containers for a consistent snapshot..."
    stop_app_services
    RESUME_APP=1
fi

# ── PostgreSQL — pg_dump custom format (compressed, pg_restore-able) ──
log "Backing up PostgreSQL (pg_dump -Fc)..."
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    > "$STAGING/postgres.dump"
[ -s "$STAGING/postgres.dump" ] || die "pg_dump produced an empty file"

# ── MongoDB — mongodump archive (all databases incl. audit streams) ──
log "Backing up MongoDB (mongodump --archive --gzip)..."
compose exec -T mongo sh -c \
    'mongodump --quiet --archive --gzip --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' \
    > "$STAGING/mongo.archive.gz"
[ -s "$STAGING/mongo.archive.gz" ] || die "mongodump produced an empty file"

# ── MinIO — tar the data volume via a throwaway busybox container ──
log "Backing up MinIO object storage (volume tar)..."
MINIO_VOLUME="$(volume_for minio /data)"
[ -n "$MINIO_VOLUME" ] || die "could not resolve the MinIO data volume"
docker run --rm \
    -v "$MINIO_VOLUME":/data:ro \
    -v "$(host_path "$STAGING")":/backup \
    busybox tar czf /backup/minio_data.tar.gz -C /data .
[ -s "$STAGING/minio_data.tar.gz" ] || die "MinIO volume tar produced an empty file"

# ── Manifest ──
log "Writing manifest.json..."
ALEMBIC_HEAD="$(pg_alembic_head)"
[ -n "$ALEMBIC_HEAD" ] || { warn "could not read alembic head from the database"; ALEMBIC_HEAD="unknown"; }
APP_VERSION="$(cat "$REPO_ROOT/VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
# cd instead of `git -C`: MSYS path conversion is disabled in lib.sh, so a
# POSIX-style -C argument would confuse native git on Windows.
GIT_SHA="$( (cd "$REPO_ROOT" && git rev-parse HEAD) 2>/dev/null || echo unknown)"

sha() {  # best-effort content hash per component
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else echo "unavailable"; fi
}

cat > "$STAGING/manifest.json" <<EOF
{
  "schema_version": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "app_version": "${APP_VERSION:-unknown}",
  "git_sha": "$GIT_SHA",
  "alembic_head": "$ALEMBIC_HEAD",
  "quiesced": "$QUIESCE",
  "components": [
    {"name": "postgres", "file": "postgres.dump",     "method": "pg_dump -Fc",              "sha256": "$(sha "$STAGING/postgres.dump")"},
    {"name": "mongo",    "file": "mongo.archive.gz",  "method": "mongodump --archive --gzip", "sha256": "$(sha "$STAGING/mongo.archive.gz")"},
    {"name": "minio",    "file": "minio_data.tar.gz", "method": "volume tar (busybox)",     "sha256": "$(sha "$STAGING/minio_data.tar.gz")"}
  ],
  "excluded": [
    {"name": "redis",    "reason": "Celery broker + caches — stale queue entries must not be replayed after a restore"},
    {"name": "chromadb", "reason": "semantic-search vectors — rebuilt from Postgres by the hourly reindex_search beat"},
    {"name": "ollama",   "reason": "model blobs — re-pull with 'make pull-llm'"}
  ]
}
EOF

# ── Single archive ──
log "Packing $ARCHIVE ..."
tar czf "$ARCHIVE" -C "$STAGING" .

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo ""
echo "Backup complete: $ARCHIVE ($SIZE)"
echo "  app_version=$APP_VERSION git_sha=${GIT_SHA:0:12} alembic_head=$ALEMBIC_HEAD"
echo ""
echo "Restore with:  make restore FILE=${ARCHIVE#"$REPO_ROOT"/}"
