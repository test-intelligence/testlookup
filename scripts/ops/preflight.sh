#!/usr/bin/env bash
# ============================================================
# TestLookup — pre-upgrade checks (US-11.2)
# ============================================================
# Read-only. Prints:
#   - current vs target image tags (TAG=vX.Y.Z, default latest)
#   - pending Alembic migrations (DB head vs the code's head)
#   - disk headroom (docker + the host filesystem)
#   - the newest backup on disk + a reminder to `make backup`
#
# Usage:
#   make preflight                 # target = latest
#   make preflight TAG=v0.2.0      # target a specific release tag
# ============================================================

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TAG="${TAG:-${TESTLOOKUP_VERSION:-latest}}"

echo "TestLookup upgrade preflight"
echo "============================"
echo ""

# ── Images: current vs target ──────────────────────────────
echo "-- Images ------------------------------------------------"
echo "Target tag: $TAG   (override with TAG=vX.Y.Z)"
echo ""
if compose ps -q >/dev/null 2>&1 && [ -n "$(compose ps -q 2>/dev/null)" ]; then
    echo "Currently running:"
    compose images 2>/dev/null || warn "could not list running images"
else
    echo "Stack is not running — no current images to compare."
fi
if compose config 2>/dev/null | grep -qE '^[[:space:]]+build:'; then
    echo ""
    echo "NOTE: this compose file BUILDS images from source (dev stack)."
    echo "      'make upgrade' will rebuild from the current checkout;"
    echo "      TAG only applies to the release stack (docker-compose.release.yml)."
fi
echo ""

# ── Migrations: DB head vs code head ───────────────────────
echo "-- Migrations --------------------------------------------"
DB_HEAD=""
if service_running postgres; then
    DB_HEAD="$(pg_alembic_head)"
fi
CODE_HEAD="$(compose run --rm --no-deps -T backend alembic heads 2>/dev/null | awk '{print $1; exit}' || true)"
echo "Database head : ${DB_HEAD:-<unavailable — is postgres running?>}"
echo "Code head     : ${CODE_HEAD:-<unavailable — could not run alembic in the backend image>}"
if [ -n "$DB_HEAD" ] && [ -n "$CODE_HEAD" ]; then
    if [ "$DB_HEAD" = "$CODE_HEAD" ]; then
        echo "Status        : up to date — no pending migrations."
    else
        echo "Status        : PENDING migrations ($DB_HEAD -> $CODE_HEAD):"
        compose run --rm --no-deps -T backend alembic history -r "$DB_HEAD:heads" 2>/dev/null \
            | sed 's/^/    /' || echo "    (could not list the pending range)"
        echo "  'make upgrade' applies these before restarting the app."
    fi
fi
echo ""

# ── Disk headroom ──────────────────────────────────────────
echo "-- Disk --------------------------------------------------"
docker system df 2>/dev/null || warn "docker system df failed"
echo ""
echo "Host filesystem at the repo root:"
df -h "$REPO_ROOT" 2>/dev/null | tail -2 || true
echo ""
echo "Rule of thumb: keep free space > 2x the postgres volume (pg_restore /"
echo "migration table rewrites need working room). Sizing math: user-guide/sizing.md"
echo ""

# ── Backups ────────────────────────────────────────────────
echo "-- Backups -----------------------------------------------"
LATEST_BACKUP="$(ls -1t "$BACKUP_DIR"/testlookup-backup-*.tar.gz 2>/dev/null | head -1 || true)"
if [ -n "$LATEST_BACKUP" ]; then
    echo "Newest backup: $LATEST_BACKUP"
    echo "               ($(du -h "$LATEST_BACKUP" | cut -f1), modified $(date -r "$LATEST_BACKUP" +%Y-%m-%dT%H:%M:%S 2>/dev/null || echo '?'))"
else
    echo "NO BACKUPS FOUND under $BACKUP_DIR"
fi
echo ""
echo ">>> Run 'make backup' BEFORE 'make upgrade'. There is no automatic"
echo ">>> rollback — the pre-upgrade backup is your rollback path."
