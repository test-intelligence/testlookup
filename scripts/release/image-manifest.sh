#!/usr/bin/env bash
# ============================================================================
# image-manifest.sh — sourceable reader for deploy/images.manifest.txt
# ----------------------------------------------------------------------------
# The manifest is the single source of truth for every container image
# TestLookup deploys. This helper is the shell-side parser; the CI drift guard
# (scripts/release/check_image_drift.py) is the Python-side one.
#
# Usage (source, don't execute):
#   . "$REPO_ROOT/scripts/release/image-manifest.sh"
#   manifest_refs k8s infra core          # -> one ref per line
#   manifest_refs compose any core,llm
#   manifest_refs any app any             # app refs still carry @APP_TAG
#
#   manifest_refs <surface> <role> <bundle>
#     surface  compose | k8s | any
#     role     app | infra | any
#     bundle   comma list (core,llm,excluded) | any
#
# Set APP_TAG before calling to have `@APP_TAG` substituted in app refs.
# Running this file directly prints the whole manifest (a quick sanity check).
# ============================================================================

# Resolve the manifest relative to this file so callers in any directory work.
_IMAGE_MANIFEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
IMAGE_MANIFEST_FILE="${IMAGE_MANIFEST_FILE:-$(cd "$_IMAGE_MANIFEST_DIR/../.." && pwd)/deploy/images.manifest.txt}"

manifest_refs() {
  local want_surface="${1:-any}" want_role="${2:-any}" want_bundle="${3:-any}"

  if [ ! -f "$IMAGE_MANIFEST_FILE" ]; then
    echo "image-manifest.sh: manifest not found: $IMAGE_MANIFEST_FILE" >&2
    return 1
  fi

  # awk over the 4 columns. Comma lists are matched with a delimiter-padded
  # substring test so `core` never matches `core-extra`.
  awk -v ws="$want_surface" -v wr="$want_role" -v wb="$want_bundle" -v tag="${APP_TAG:-}" '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      ref = $1; role = $2; surfaces = $3; bundle = $4
      if (ref == "" || role == "" || surfaces == "" || bundle == "") next

      if (wr != "any" && role != wr) next
      if (ws != "any" && index("," surfaces ",", "," ws ",") == 0) next
      if (wb != "any") {
        n = split(wb, wants, ",")
        ok = 0
        for (i = 1; i <= n; i++) if (bundle == wants[i]) ok = 1
        if (!ok) next
      }
      if (tag != "") sub(/@APP_TAG$/, tag, ref)
      print ref
    }
  ' "$IMAGE_MANIFEST_FILE"
}

# Strip the tag from a ref: minio/minio:RELEASE.x -> minio/minio
manifest_ref_name() { printf '%s\n' "${1%:*}"; }

# Turn a ref into a filesystem-safe basename for images/<name>.tar
manifest_ref_slug() { printf '%s\n' "$1" | tr '/:' '__' | tr -cd 'A-Za-z0-9._-'; }

# Direct execution -> dump the parsed manifest (debug aid).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  echo "manifest: $IMAGE_MANIFEST_FILE"
  echo "-- all --";            manifest_refs any any any
  echo "-- bundle core --";    manifest_refs any any core
  echo "-- k8s infra core --"; manifest_refs k8s infra core
fi
