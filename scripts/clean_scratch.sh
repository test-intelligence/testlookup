#!/usr/bin/env bash
#
# Remove pytest scratch roots and tool caches from the working tree.
#
# Why this exists: on 2026-09-20 the main tree held 186 `.pytest*` roots at the
# repo root alone, and homelabsetup/deploy-homelab.sh's require_clean_build_inputs
# refused to deploy from it — 200 entries, so the deploy had to run from a
# throwaway `git worktree add --detach`, a full 3463-file checkout.
#
# The `--basetemp` overrides that produced those 186 roots are gone (every suite
# now uses pytest's own default under the system temp, which it rotates down to
# the last three runs), so this script no longer has an accumulating basetemp to
# chase. It still earns its place: `.pytest_cache`, `__pycache__`, coverage
# output and `.hypothesis` are all written into the tree by an ordinary test run
# and none of them are ever cleaned up, and a repo that predates the basetemp
# change still carries the old roots. repo.no-repo-relative-basetemp is what
# stops them coming back.
#
# Scope is deliberately narrow: scratch and cache output only. Dependency trees
# (node_modules, venvs, .uv-cache) and build outputs (dist, build, target) are
# NEVER touched, and no path containing a git-tracked file is ever removed.
#
#   make clean-scratch              # remove them
#   make clean-scratch DRY_RUN=1    # list what would go, remove nothing
#
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DRY_RUN="${DRY_RUN:-}"

# ── Candidate selection ──────────────────────────────────────────────────────
#
# Pruned outright: `dist`/`build`/`target` are build outputs, and the venv /
# node_modules / .uv-cache trees are expensive to rebuild, so a cache sitting
# inside one of them is not ours to delete. `./.tmp` is pruned by PATH, not by
# name — the repo-root one holds run evidence (h06-*, m08-* …) plus an
# mcp-venv, whereas a component's own `backend/.tmp` is pytest basetemp output
# and is cleaned.
#
# The scratch directories are `-prune`d as well as printed, so find does not
# then descend into a directory that is about to be removed.
#
# NOTE: do not add `-depth` here. It silently disables every `-prune` above.
candidates() {
  find . \
    \( -name .git -o -name .claude -o -name node_modules \
       -o -name .venv -o -name .venv311 -o -name .venv314 \
       -o -name venv -o -name mcp-venv -o -name .uv-cache \
       -o -name dist -o -name build -o -name target \
       -o -name docs -o -name '*.egg-info' \) -prune -o \
    -path ./.tmp -prune -o \
    -type d \( -name '.pytest*' -o -name __pycache__ -o -name .mypy_cache \
       -o -name .ruff_cache -o -name .hypothesis -o -name htmlcov \
       -o -name .tmp \) -prune -print -o \
    -type f \( -name .coverage -o -name '.coverage.*' -o -name coverage.json \
       -o -name coverage.xml -o -name celerybeat-schedule \
       -o -name celerybeat.pid -o -name debug.log \) -print \
    | sed 's|^\./||' \
    | sort -u
}

# ── Tracked-file guard ───────────────────────────────────────────────────────
# Safe by construction: if git tracks anything at or under a candidate, the
# candidate is not scratch and is skipped, whatever its name says.
TRACKED="$(mktemp)"
trap 'rm -f "$TRACKED"' EXIT
git ls-files > "$TRACKED"

is_tracked() {
  local path="$1"
  grep -qxF -- "$path" "$TRACKED" && return 0
  grep -qF -- "${path}/" "$TRACKED" && return 0
  return 1
}

# ── Walk ─────────────────────────────────────────────────────────────────────
removed=0
skipped=0
failed=()

while IFS= read -r path; do
  [ -n "$path" ] || continue
  [ -e "$path" ] || continue          # already gone with a parent

  if is_tracked "$path"; then
    echo "  skip (tracked): $path"
    skipped=$((skipped + 1))
    continue
  fi

  if [ -n "$DRY_RUN" ]; then
    echo "  would remove: $path"
    removed=$((removed + 1))
    continue
  fi

  rm -rf -- "$path" 2>/dev/null || true
  if [ -e "$path" ]; then
    failed+=("$path")
  else
    removed=$((removed + 1))
  fi
done < <(candidates)

# ── Report ───────────────────────────────────────────────────────────────────
if [ -n "$DRY_RUN" ]; then
  echo "clean-scratch: ${removed} path(s) would be removed, ${skipped} skipped as tracked."
  exit 0
fi

echo "clean-scratch: removed ${removed} path(s), skipped ${skipped} as tracked."

if [ ${#failed[@]} -eq 0 ]; then
  exit 0
fi

echo
echo "clean-scratch: ${#failed[@]} path(s) could not be removed:"
printf '  %s\n' "${failed[@]}"

# A containerised test run (compose/podman with the repo bind-mounted) writes
# its scratch as container-root, which is an unresolvable SID on the Windows
# host: the host user holds neither READ_CONTROL nor WRITE_OWNER, so `takeown`
# itself needs elevation. Not hypothetical — that is how all 186 root-level
# dirs got there on 2026-09-20, and why `git status` printed a
# "could not open directory ...: Permission denied" warning for each one.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    quoted=""
    for path in "${failed[@]}"; do
      [ -z "$quoted" ] && quoted="'${path}'" || quoted="${quoted},'${path}'"
    done
    echo
    echo "These are owned by another identity — usually a container that wrote"
    echo "them through the bind mount. Clear them from an ELEVATED PowerShell:"
    echo
    echo "  cd '${REPO_ROOT}'"
    echo "  @(${quoted}) | ForEach-Object { takeown /f \$_ /r /d y *>\$null; icacls \$_ /reset /t /c /q *>\$null; Remove-Item -LiteralPath \$_ -Recurse -Force }"
    ;;
esac
exit 1
