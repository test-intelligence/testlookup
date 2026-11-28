#!/usr/bin/env bash
# Preload the redistributable optional telemetry images for a tagged release.
# The offline bundle's release path uses --skip-pull, so a fresh runner must
# fetch these existing digest identities before invoking offline-bundle.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/release/image-manifest.sh
. "$REPO_ROOT/scripts/release/image-manifest.sh"

DOCKER="${DOCKER:-docker}"
command -v "$DOCKER" >/dev/null 2>&1 \
  || { echo "telemetry preload: container client '$DOCKER' not found" >&2; exit 1; }

TELEMETRY_REFS="$(manifest_refs telemetry infra telemetry)"
[ -n "$TELEMETRY_REFS" ] \
  || { echo "telemetry preload: no redistributable telemetry images are catalogued" >&2; exit 1; }

while IFS= read -r ref; do
  [ -n "$ref" ] || continue
  [[ "$ref" =~ @sha256:[0-9a-f]{64}$ ]] \
    || { echo "telemetry preload: mutable telemetry reference refused: $ref" >&2; exit 1; }
  tagged_ref="${ref%@sha256:*}"
  previous_id="$("$DOCKER" image inspect --format '{{.Id}}' "$tagged_ref" 2>/dev/null || true)"
  "$DOCKER" pull "$ref"
  source_id="$("$DOCKER" image inspect --format '{{.Id}}' "$ref")" \
    || { echo "telemetry preload: pulled digest is not locally inspectable: $ref" >&2; exit 1; }
  [ -n "$source_id" ] \
    || { echo "telemetry preload: pulled digest has no local image ID: $ref" >&2; exit 1; }
  "$DOCKER" tag "$source_id" "$(manifest_archive_ref "$ref")"
  # busybox is also a signed core-release image. Pulling the pinned telemetry
  # platform digest must not move that already-preloaded release tag.
  if [ -n "$previous_id" ]; then
    "$DOCKER" tag "$previous_id" "$tagged_ref"
  fi
done <<< "$TELEMETRY_REFS"
