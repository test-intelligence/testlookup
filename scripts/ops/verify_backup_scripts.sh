#!/usr/bin/env bash
# ============================================================
# TestLookup — static verification for the ops scripts
# ============================================================
# Dry-run/lint harness for scripts/ops/*.sh — usable on hosts without a
# running stack (and in CI):
#   1. bash -n            syntax check every script
#   2. shellcheck         if installed (skipped with a note otherwise)
#   3. pytest             the manifest/refusal logic unit tests
#                         (backend/tests/test_ops_support.py) when pytest is
#                         importable; skipped with a note otherwise.
#
# For a LIVE round-trip (backup → wipe → restore → verify) against a
# throwaway compose project, see the procedure documented in
# user-guide/administration.md#backup--restore.
# ============================================================

set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$OPS_DIR/../.." && pwd)"
FAILED=0

echo "== bash -n syntax check =="
for script in "$OPS_DIR"/*.sh; do
    if bash -n "$script"; then
        echo "  OK   $(basename "$script")"
    else
        echo "  FAIL $(basename "$script")"
        FAILED=1
    fi
done

echo ""
echo "== shellcheck =="
if command -v shellcheck >/dev/null 2>&1; then
    if shellcheck -x "$OPS_DIR"/*.sh; then
        echo "  OK   shellcheck clean"
    else
        echo "  FAIL shellcheck reported issues"
        FAILED=1
    fi
else
    echo "  SKIP shellcheck not installed"
fi

echo ""
echo "== ops_support.py unit tests =="
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import pytest" >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [ -n "$PY" ]; then
    if ( cd "$REPO_ROOT/backend" && "$PY" -m pytest tests/test_ops_support.py -q ); then
        echo "  OK   pytest passed"
    else
        echo "  FAIL pytest failed"
        FAILED=1
    fi
else
    echo "  SKIP no python with pytest available"
fi

echo ""
if [ "$FAILED" = "0" ]; then
    echo "VERIFY: PASS"
else
    echo "VERIFY: FAIL"
fi
exit "$FAILED"
