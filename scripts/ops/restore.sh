#!/usr/bin/env bash
# ============================================================
# TestLookup — one-command restore (US-11.1)
# ============================================================
# Restores an archive produced by scripts/ops/backup.sh:
#   1. Validates the archive + manifest.
#   2. Migration-safety check: REFUSES to restore when the deployed code's
#      alembic head is NEWER than the backup's (or when ancestry can't be
#      verified) unless FORCE=1 — see the refusal text below for why.
#   3. Stops the application containers (datastores stay up).
#   4. Restores PostgreSQL (drop/recreate + pg_restore), MongoDB
#      (mongorestore --drop), and the MinIO volume; flushes Redis
#      (broker/caches are rebuildable and stale queue entries are dangerous).
#   5. Starts the stack (the backend runs `alembic upgrade head` on boot),
#      waits for readiness, runs the smoke check, prints a verdict +
#      post-restore checklist (ChromaDB reindex, etc).
#
# Usage:
#   make restore FILE=backups/testlookup-backup-<ts>.tar.gz
#   make restore FILE=... FORCE=1          # override the migration refusal
#   make restore FILE=... CONFIRM=yes      # skip the interactive prompt
#
# Test/advanced escape hatches (used by the verification harness):
#   TL_RESTORE_SKIP_HEAD_CHECK=1   skip the migration-ancestry check
#   TL_RESTORE_NO_START=1          restore datastores but do not start the app
#   SKIP_REDIS_FLUSH=1             keep Redis contents (not recommended)
# ============================================================

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FILE="${1:-${FILE:-}}"
FORCE="${FORCE:-0}"
CONFIRM="${CONFIRM:-}"

[ -n "$FILE" ] || die "usage: make restore FILE=backups/<name>.tar.gz"
# Resolve relative to the repo root so `make restore FILE=backups/x.tar.gz` works anywhere.
case "$FILE" in
    /*|[A-Za-z]:*) : ;;
    *) FILE="$REPO_ROOT/$FILE" ;;
esac
[ -f "$FILE" ] || die "backup archive not found: $FILE"

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/tl-restore-XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

log "Extracting $(basename "$FILE") ..."
tar xzf "$FILE" -C "$STAGING"

MANIFEST="$STAGING/manifest.json"
[ -f "$MANIFEST" ] || die "archive has no manifest.json — not a TestLookup backup?"
for component in postgres.dump mongo.archive.gz minio_data.tar.gz; do
    [ -s "$STAGING/$component" ] || die "archive is missing component: $component"
done

BACKUP_HEAD="$(manifest_get "$MANIFEST" alembic_head)"
BACKUP_CREATED="$(manifest_get "$MANIFEST" created_at)"
BACKUP_VERSION="$(manifest_get "$MANIFEST" app_version)"
log "Backup: created=$BACKUP_CREATED app_version=$BACKUP_VERSION alembic_head=$BACKUP_HEAD"

require_running postgres mongo minio

# ── Migration-safety check ─────────────────────────────────
# Classified INSIDE the backend container against the deployed migration
# scripts (backend/scripts/ops_support.py — pure alembic ScriptDirectory read,
# no DB needed):
#   equal                    backup head == deployed code head  → safe
#   stack_newer              backup head is an ANCESTOR of the code head
#   backup_newer_or_unknown  backup head is not in the deployed migration tree
if [ "${TL_RESTORE_SKIP_HEAD_CHECK:-0}" = "1" ]; then
    warn "TL_RESTORE_SKIP_HEAD_CHECK=1 — skipping the migration-ancestry check"
elif [ -z "$BACKUP_HEAD" ] || [ "$BACKUP_HEAD" = "unknown" ]; then
    if [ "$FORCE" != "1" ]; then
        die "the backup manifest carries no alembic head, so schema compatibility cannot be verified. Re-run with FORCE=1 to restore anyway."
    fi
    warn "backup has no alembic head — proceeding because FORCE=1"
else
    VERDICT="$(compose run --rm --no-deps -T backend python /app/scripts/ops_support.py compare-head "$BACKUP_HEAD" 2>/dev/null | tail -1 || true)"
    case "$VERDICT" in
        equal)
            log "Migration check: backup matches the deployed schema head ($BACKUP_HEAD)."
            ;;
        stack_newer)
            if [ "$FORCE" != "1" ]; then
                echo "" >&2
                echo "REFUSING to restore: the deployed code's migrations are NEWER than this backup ($BACKUP_HEAD)." >&2
                echo "" >&2
                echo "Restoring would roll your data back in time: everything written since the" >&2
                echo "backup is lost, and on next start alembic will re-apply every migration" >&2
                echo "after $BACKUP_HEAD — any data those migrations backfilled from post-backup" >&2
                echo "state is gone. This is usually an accident (restoring an old archive onto" >&2
                echo "an upgraded install)." >&2
                echo "" >&2
                echo "If this rollback is intentional, re-run with FORCE=1:" >&2
                echo "  make restore FILE=$FILE FORCE=1" >&2
                exit 1
            fi
            warn "deployed migrations are newer than the backup — proceeding because FORCE=1 (alembic will upgrade the restored data on next start)"
            ;;
        backup_newer_or_unknown)
            if [ "$FORCE" != "1" ]; then
                echo "" >&2
                echo "REFUSING to restore: backup head '$BACKUP_HEAD' is not in the deployed" >&2
                echo "code's migration history — the backup was taken from a NEWER (or unrelated)" >&2
                echo "TestLookup version. The current code would not understand the restored schema." >&2
                echo "" >&2
                echo "Upgrade first (make upgrade TAG=<version-the-backup-came-from>), then restore." >&2
                echo "Or re-run with FORCE=1 if you know what you are doing." >&2
                exit 1
            fi
            warn "backup head is unknown to the deployed code — proceeding because FORCE=1"
            ;;
        *)
            if [ "$FORCE" != "1" ]; then
                die "could not verify migration ancestry (backend image lacks ops_support.py, or compose run failed). Failing safe — re-run with FORCE=1 to restore anyway."
            fi
            warn "migration ancestry unverified — proceeding because FORCE=1"
            ;;
    esac
fi

# ── Confirmation (this REPLACES all current data) ──────────
if [ "$CONFIRM" != "yes" ]; then
    if [ -t 0 ]; then
        printf 'This will REPLACE all PostgreSQL/MongoDB/MinIO data with the backup. Continue? [y/N] '
        read -r answer
        case "$answer" in y|Y|yes|YES) : ;; *) die "aborted — no changes made" ;; esac
    else
        die "refusing to run non-interactively without CONFIRM=yes (this replaces all data)"
    fi
fi

# ── Stop the application layer ─────────────────────────────
log "Stopping application containers (datastores stay up)..."
stop_app_services

# ── PostgreSQL ─────────────────────────────────────────────
log "Restoring PostgreSQL (drop/recreate + pg_restore)..."
compose exec -T postgres sh -c '
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '"'"'$POSTGRES_DB'"'"' AND pid <> pg_backend_pid();" \
        -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";" \
        -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";" >/dev/null
'
compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --role "$POSTGRES_USER"' \
    < "$STAGING/postgres.dump"
RESTORED_HEAD="$(pg_alembic_head)"
log "PostgreSQL restored (alembic head now: ${RESTORED_HEAD:-<none>})."

# ── MongoDB ────────────────────────────────────────────────
log "Restoring MongoDB (mongorestore --drop)..."
compose exec -T mongo sh -c \
    'mongorestore --quiet --archive --gzip --drop --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' \
    < "$STAGING/mongo.archive.gz"

# ── MinIO (volume replace — service stopped for consistency) ──
log "Restoring MinIO object storage..."
MINIO_VOLUME="$(volume_for minio /data)"
[ -n "$MINIO_VOLUME" ] || die "could not resolve the MinIO data volume"
compose stop minio >/dev/null
docker run --rm \
    -v "$MINIO_VOLUME":/data \
    -v "$(host_path "$STAGING")":/backup:ro \
    busybox sh -c 'rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar xzf /backup/minio_data.tar.gz -C /data'
compose up -d minio >/dev/null

# ── Redis (excluded from backup by design — flush stale state) ──
if [ "${SKIP_REDIS_FLUSH:-0}" = "1" ]; then
    warn "SKIP_REDIS_FLUSH=1 — Redis keeps pre-restore queues/caches (may reference data that no longer exists)"
elif service_running redis; then
    log "Flushing Redis (broker/caches — stale entries must not outlive the restore)..."
    compose exec -T redis sh -c 'redis-cli ${REDIS_PASSWORD:+-a "$REDIS_PASSWORD"} FLUSHALL' >/dev/null
fi

# ── Restart + verify ───────────────────────────────────────
if [ "${TL_RESTORE_NO_START:-0}" = "1" ]; then
    warn "TL_RESTORE_NO_START=1 — datastores restored; application NOT started."
else
    log "Starting the stack (backend applies pending migrations on boot)..."
    compose up -d
    log "Waiting for backend readiness at $TL_API/health/ready (up to 300s)..."
    if wait_for_ready "$TL_API/health/ready" 300; then
        if run_smoke; then
            echo ""
            echo "RESTORE VERDICT: PASS — stack restored and healthy."
        else
            echo ""
            echo "RESTORE VERDICT: DEGRADED — data restored but the smoke check failed."
            echo "Inspect: docker compose logs backend worker"
        fi
    else
        echo ""
        echo "RESTORE VERDICT: FAIL — backend did not become ready within 300s."
        echo "Inspect: docker compose logs backend"
    fi
fi

# ── Post-restore checklist ─────────────────────────────────
cat <<'EOF'

Post-restore checklist:
  1. ChromaDB (semantic search) was NOT restored — it rebuilds automatically
     via the hourly reindex beat. To rebuild immediately:
       docker compose exec backend python -c "from app.worker.tasks import reindex_search; reindex_search.apply_async(kwargs={'full': True})"
  2. Redis was flushed: in-flight Celery jobs from before the restore are gone.
     Re-trigger any analyses you were waiting on from the UI.
  3. If you restored onto a different host, keep the SAME .env secrets the
     backup's install used only for MinIO service accounts stored in the data
     volume; root credentials for Postgres/Mongo/MinIO come from your .env.
  4. Ollama models are not part of the backup — `make pull-llm` if you use the
     local-LLM profile.
  5. Verify the dashboard (runs, failures, settings) before pointing CI back
     at this instance.
EOF
