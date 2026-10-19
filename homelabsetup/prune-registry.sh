#!/usr/bin/env bash
# ============================================================
# Prune old TestLookup images from the homelab registry
# ============================================================
# Every deploy pushes a new immutable build-YYYYMMDD-HHMMSS tag of
# testlookup/backend, testlookup/frontend and testlookup/mcp (~1 GB of new
# layers) and nothing ever removed the old ones: on 2026-10-18 the registry
# held ~570 builds per repository, 48.8 GB on one node's disk.
#
# For each of those three repositories this keeps the newest N dated build
# tags, `latest`, and every manifest digest one of them shares (a DELETE by
# digest removes every tag on that digest, so a digest a kept tag points to is
# never deleted). Every other manifest is deleted, then the registry's
# garbage collector frees the unreferenced layers. No other repository is
# read or touched, and `garbage-collect` runs WITHOUT --delete-untagged:
# other apps on the cluster deploy by digest, with no tag.
#
# Usage:
#   ./homelabsetup/prune-registry.sh [--registry=host:port] [--keep=N] [--dry-run] [--no-gc]
#
# Exit codes: 0 done (or nothing to prune), 2 bad flag, 3 the registry refuses
# deletes (registry:2 needs REGISTRY_STORAGE_DELETE_ENABLED=true), 4 the
# registry is unreachable. deploy-homelab.sh runs this after a successful
# deploy and only warns on a non-zero exit: pruning never fails a deploy.
# ============================================================
set -u

REGISTRY="${K3S_REGISTRY:-registry.local:30500}"
KEEP=3
DRY_RUN=false
GC=true
KUBECTL="${KUBECTL:-kubectl}"
REGISTRY_NAMESPACE="${REGISTRY_NAMESPACE:-registry}"
REPOS=(testlookup/backend testlookup/frontend testlookup/mcp)
ACCEPT="application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"

for arg in "$@"; do
  case $arg in
    --registry=*) REGISTRY="${arg#*=}" ;;
    --keep=*)     KEEP="${arg#*=}" ;;
    --dry-run)    DRY_RUN=true ;;
    --no-gc)      GC=false ;;
    *)            echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done
case "$KEEP" in
  ''|*[!0-9]*) echo "--keep must be a whole number (got '$KEEP')" >&2; exit 2 ;;
esac
if [ "$KEEP" -lt 1 ]; then
  echo "--keep must be at least 1: the running build is always kept" >&2
  exit 2
fi

BASE="http://${REGISTRY}/v2"

# Tags of one repository, one per line ("tags": null -> nothing).
tags_of() {
  curl -sf --max-time 30 "$BASE/$1/tags/list?n=10000" \
    | tr -d '\n' \
    | sed -n 's/.*"tags":[[:space:]]*\[\([^]]*\)\].*/\1/p' \
    | tr ',' '\n' | tr -d '" \t' | grep -v '^$' || true
}

# The manifest digest one tag points to (empty when it cannot be read).
digest_of() {
  curl -sfI --max-time 30 -H "Accept: $ACCEPT" "$BASE/$1/manifests/$2" \
    | tr -d '\r' \
    | awk -F': ' 'tolower($1) == "docker-content-digest" { print $2 }'
}

if ! curl -sf --max-time 15 -o /dev/null "$BASE/"; then
  echo "registry not reachable at $REGISTRY" >&2
  exit 4
fi

deleted_total=0
for repo in "${REPOS[@]}"; do
  all_tags="$(tags_of "$repo")"
  if [ -z "$all_tags" ]; then
    echo "$repo: no tags"
    continue
  fi
  keep_tags="$( {
    printf '%s\n' "$all_tags" | grep -E '^build-[0-9]{8}-[0-9]{6}$' | sort | tail -n "$KEEP"
    printf '%s\n' "$all_tags" | grep -x 'latest'
  } | sort -u)"

  # "<tag> <digest>" for every tag whose manifest can be read.
  pairs=""
  while IFS= read -r tag; do
    digest="$(digest_of "$repo" "$tag")"
    [ -n "$digest" ] && pairs+="$tag $digest"$'\n'
  done <<< "$all_tags"

  keep_digests="$(printf '%s' "$pairs" | awk 'NR==FNR { keep[$1] = 1; next } ($1 in keep) { print $2 }' \
    <(printf '%s\n' "$keep_tags") - | sort -u)"
  delete_digests="$(printf '%s' "$pairs" | awk '{ print $2 }' | sort -u \
    | grep -vxF -f <(printf '%s\n' "${keep_digests:-__none__}") || true)"

  n_delete=0
  [ -n "$delete_digests" ] && n_delete="$(printf '%s\n' "$delete_digests" | wc -l | tr -d ' ')"
  echo "$repo: $(printf '%s\n' "$all_tags" | wc -l | tr -d ' ') tags; keep $(printf '%s' "$keep_tags" | tr '\n' ' '); delete $n_delete manifest(s)"
  [ "$DRY_RUN" = true ] && continue

  while IFS= read -r digest; do
    [ -n "$digest" ] || continue
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X DELETE "$BASE/$repo/manifests/$digest")"
    case "$code" in
      202) deleted_total=$((deleted_total + 1)) ;;
      404) ;;  # already gone (another tag's DELETE removed it)
      405)
        echo "the registry refuses deletes (set REGISTRY_STORAGE_DELETE_ENABLED=true on deploy/registry)" >&2
        exit 3
        ;;
      *) echo "  DELETE $repo@${digest:0:19} -> HTTP $code" >&2 ;;
    esac
  done <<< "$delete_digests"
done

if [ "$DRY_RUN" = true ]; then
  echo "dry run: nothing deleted"
  exit 0
fi
echo "deleted $deleted_total manifest(s)"

if [ "$GC" = true ] && [ "$deleted_total" -gt 0 ]; then
  # No --delete-untagged: other apps' images are referenced by digest only.
  # One `sh -c` string, not a separate /etc/... argument: Git Bash rewrites an
  # argument starting with / into a Windows path before the native kubectl
  # sees it, and the 2026-10-19 deploy's collection failed that way.
  if "$KUBECTL" -n "$REGISTRY_NAMESPACE" exec deploy/registry -- \
       sh -c "registry garbage-collect /etc/docker/registry/config.yml" >/dev/null 2>&1; then
    echo "registry garbage collection done"
  else
    echo "registry garbage collection failed (run: $KUBECTL -n $REGISTRY_NAMESPACE exec deploy/registry -- registry garbage-collect /etc/docker/registry/config.yml)" >&2
  fi
fi
exit 0
