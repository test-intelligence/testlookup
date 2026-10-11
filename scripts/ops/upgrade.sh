#!/usr/bin/env bash
# ============================================================
# TestLookup — one-step upgrade (US-11.2)
# ============================================================
# Sequence:
#   1. Warn if there is no recent backup (make backup first!).
#   2. Pull the target images (TAG=vX.Y.Z; release stack) — or rebuild from
#      source when the active compose file builds images (dev stack).
#   3. Bring datastores up and run `alembic upgrade head` explicitly, so a
#      migration failure is loud and happens BEFORE the app is replaced.
#   4. `docker compose up -d` — compose recreates changed containers in
#      dependency order (datastores -> backend -> worker/beat -> frontend/mcp).
#   5. Wait for readiness + run the smoke check.
#   6. On failure: print rollback instructions referencing the pre-upgrade
#      backup. Automatic rollback is deliberately NOT attempted: data
#      migrations are not safely auto-reversible (downgrades can drop
#      backfilled data or fail halfway), so the only trustworthy rollback is
#      restoring the pre-upgrade backup onto the previous image tag.
#
# Usage:
#   make preflight TAG=v0.2.0   # always look before you leap
#   make backup
#   make upgrade TAG=v0.2.0     # release stack (docker-compose.release.yml)
#   make upgrade                # dev stack: rebuild from the current checkout
# ============================================================

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TAG="${TAG:-}"

# Remember where we started for the rollback instructions.
PREV_BACKEND_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$(compose ps -q backend 2>/dev/null)" 2>/dev/null || true)"
LATEST_BACKUP="$(ls -1t "$BACKUP_DIR"/testlookup-backup-*.tar.gz 2>/dev/null | head -1 || true)"

print_rollback() {
    echo "" >&2
    echo "---------------- ROLLBACK INSTRUCTIONS ----------------" >&2
    echo "Automatic rollback is intentionally not attempted: schema migrations" >&2
    echo "are not safely auto-reversible (a downgrade can drop backfilled data" >&2
    echo "or fail halfway). Roll back manually:" >&2
    echo "" >&2
    echo "  1. Stop the app layer:      docker compose stop backend worker beat frontend mcp" >&2
    if [ -n "$PREV_BACKEND_IMAGE" ]; then
        echo "  2. Re-pin the old images:   previous backend image was: $PREV_BACKEND_IMAGE" >&2
        echo "     (release stack: set TESTLOOKUP_VERSION back to that tag in .env)" >&2
    else
        echo "  2. Re-pin the old images (release stack: TESTLOOKUP_VERSION in .env)." >&2
    fi
    if [ -n "$LATEST_BACKUP" ]; then
        echo "  3. Restore the pre-upgrade backup:" >&2
        echo "       make restore FILE=${LATEST_BACKUP#"$REPO_ROOT"/} FORCE=1" >&2
        echo "     (FORCE=1 because the restored data is older than the new code's head)" >&2
    else
        echo "  3. Restore your pre-upgrade backup: make restore FILE=backups/<name>.tar.gz FORCE=1" >&2
        echo "     >>> No backup was found under $BACKUP_DIR — this is why 'make backup'" >&2
        echo "     >>> before 'make upgrade' matters." >&2
    fi
    echo "  4. Start the old stack:     docker compose up -d && make smoke" >&2
    echo "--------------------------------------------------------" >&2
}

fail() {
    echo "" >&2
    echo "UPGRADE VERDICT: FAIL — $*" >&2
    print_rollback
    exit 1
}

# ── 1. Backup guard ────────────────────────────────────────
if [ -z "$LATEST_BACKUP" ]; then
    warn "no backup found under $BACKUP_DIR — strongly consider 'make backup' first."
else
    log "Newest backup: $LATEST_BACKUP"
fi

# ── 2. Pull or rebuild images ──────────────────────────────
if compose config 2>/dev/null | grep -qE '^[[:space:]]+build:'; then
    MODE=build
    log "Dev/source stack detected — rebuilding images from the current checkout..."
    compose build --pull || fail "image build failed (stack untouched — nothing was replaced)"
else
    MODE=pull
    if [ -n "$TAG" ]; then
        export TESTLOOKUP_VERSION="$TAG"
        log "Pulling release images at tag: $TAG"
    else
        log "Pulling release images at the configured tag (TESTLOOKUP_VERSION/latest)..."
    fi
    compose pull || fail "image pull failed (stack untouched — nothing was replaced)"
fi

# ── 3. Migrate first, loudly ───────────────────────────────
log "Starting datastores..."
compose up -d "${DATASTORE_SERVICES[@]}" || fail "datastores failed to start"
log "Waiting for PostgreSQL..."
tries=0
until compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -lt 30 ] || fail "PostgreSQL did not become ready"
    sleep 2
done

DB_HEAD_BEFORE="$(pg_alembic_head)"
log "Applying migrations (alembic upgrade head) — DB head before: ${DB_HEAD_BEFORE:-<none>}..."
compose up --force-recreate --abort-on-container-exit --exit-code-from db-migrate db-migrate || fail "migration failed — the app layer was NOT restarted; the database may need attention before anything else"
DB_HEAD_AFTER="$(pg_alembic_head)"
log "Migrations complete — DB head now: ${DB_HEAD_AFTER:-<none>}"

# ── 4. Roll the application layer ──────────────────────────
log "Recreating services in dependency order (docker compose up -d)..."
compose up -d --remove-orphans || fail "service recreation failed"

# ── 5. Health + smoke ──────────────────────────────────────
log "Waiting for backend readiness at $TL_API/health/ready (up to 300s)..."
wait_for_ready "$TL_API/health/ready" 300 || fail "backend did not become ready within 300s (docker compose logs backend)"

log "Running the smoke check..."
if run_smoke; then
    echo ""
    echo "UPGRADE VERDICT: PASS — stack upgraded and healthy."
    [ "$MODE" = "pull" ] && [ -n "$TAG" ] && echo "Pin it: set TESTLOOKUP_VERSION=$TAG in .env so restarts stay on this version."
else
    fail "smoke check failed after upgrade (docker compose logs backend worker)"
fi
