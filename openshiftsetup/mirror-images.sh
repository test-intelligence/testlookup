#!/usr/bin/env bash
# ============================================================================
# mirror-images.sh — seed one Artifactory with every image TestLookup needs
# ----------------------------------------------------------------------------
# Standalone helper for the air-gapped OpenShift deploy. Run it from a host
# that HAS internet + Artifactory access to copy all third-party infra images
# into your Artifactory, so the (internet-less) cluster can pull them. The
# main deploy script does this too (Step 2); this exists for ops who want to
# pre-seed the registry independently, or refresh infra images without a full
# app rebuild.
#
# It mirrors infrastructure images only. The deployment script mirrors the
# complete signed release set, including application images, when it owns the
# release deployment.
#
# Config: reads openshiftsetup/artifactory.env (same file as the deploy
# script) or environment variables. Required: ARTIFACTORY_REGISTRY and the
# release-manifest.json and provenance bundle published with the selected release.
#
# Usage:
#   ./openshiftsetup/mirror-images.sh --manifest release-manifest.json --provenance release.intoto.jsonl
#   ./openshiftsetup/mirror-images.sh --manifest release-manifest.json --provenance release.intoto.jsonl --dry-run
#   INFRA_IMAGES="postgres:16-alpine@sha256:..." ./openshiftsetup/mirror-images.sh --manifest release-manifest.json --provenance release.intoto.jsonl
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="${ARTIFACTORY_ENV_FILE:-$REPO_ROOT/openshiftsetup/artifactory.env}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()   { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }

DRY_RUN=false
MANIFEST_ARG=""
PROVENANCE_ARG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --manifest)
      shift
      [ "$#" -gt 0 ] || error "--manifest requires a path"
      MANIFEST_ARG="$1"
      ;;
    --provenance)
      shift
      [ "$#" -gt 0 ] || error "--provenance requires a path"
      PROVENANCE_ARG="$1"
      ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) error "Unknown flag: $1" ;;
  esac
  shift
done

if [ -f "$CONFIG_FILE" ]; then set -a; . "$CONFIG_FILE"; set +a; fi
[ -n "${ARTIFACTORY_REGISTRY:-}" ] || error "ARTIFACTORY_REGISTRY is required (config or env)."
ARTIFACTORY_HOST="${ARTIFACTORY_REGISTRY%%/*}"

# The catalogue defines the expected names and the authenticated release
# manifest binds each one to the digest CI resolved for this release. An
# explicit INFRA_IMAGES may select a subset, but every selected ref must be an
# exact digest ref from that complete signed inventory.
# shellcheck source=../scripts/release/image-manifest.sh
. "$REPO_ROOT/scripts/release/image-manifest.sh"
RELEASE_MANIFEST="${MANIFEST_ARG:-${RELEASE_MANIFEST:-}}"
RELEASE_PROVENANCE_BUNDLE="${PROVENANCE_ARG:-${RELEASE_PROVENANCE_BUNDLE:-}}"
[ -n "$RELEASE_MANIFEST" ] || error "--manifest release-manifest.json is required."
[ -f "$RELEASE_MANIFEST" ] || error "Release manifest not found: $RELEASE_MANIFEST"
[ -n "$RELEASE_PROVENANCE_BUNDLE" ] || error "--provenance release.intoto.jsonl is required."
[ -f "$RELEASE_PROVENANCE_BUNDLE" ] || error "Release provenance bundle not found: $RELEASE_PROVENANCE_BUNDLE"
command -v gh >/dev/null 2>&1 || error "GitHub CLI (gh) is required to authenticate the release manifest."
gh attestation verify "$RELEASE_MANIFEST" \
  --repo test-intelligence/testlookup \
  --signer-workflow test-intelligence/testlookup/.github/workflows/offline-bundle.yml \
  --source-ref refs/heads/main \
  --bundle "$RELEASE_PROVENANCE_BUNDLE" >/dev/null \
  || error "Release manifest does not have trusted TestLookup release provenance."
command -v python3 >/dev/null 2>&1 || error "python3 is required to validate the release manifest."
python3 "$REPO_ROOT/scripts/release/release_manifest.py" validate \
  --manifest "$RELEASE_MANIFEST" --require-schema-version 2 >/dev/null \
  || error "Release manifest must be the complete authenticated schema-v2 image set."
SIGNED_INFRA_ROWS="$(python3 "$REPO_ROOT/scripts/release/release_manifest.py" refs \
  --manifest "$RELEASE_MANIFEST" --kind infrastructure)" \
  || error "Could not read infrastructure digests from the release manifest."
DESTINATION_INFRA_ROWS="$(python3 "$REPO_ROOT/scripts/release/release_manifest.py" refs \
  --manifest "$RELEASE_MANIFEST" --kind infrastructure \
  --destination-prefix "$ARTIFACTORY_REGISTRY")" \
  || error "Could not materialize infrastructure digests for Artifactory."
SIGNED_INFRA="$(printf '%s\n' "$SIGNED_INFRA_ROWS" | cut -f2-)"
[ -n "$SIGNED_INFRA" ] || error "Release manifest does not contain the complete infrastructure digest set."
CATALOG_INFRA="$(manifest_refs k8s infra core,llm,external)"
for tagged_ref in $CATALOG_INFRA; do
  matches="$(printf '%s\n' "$SIGNED_INFRA" | awk -v prefix="$tagged_ref@sha256:" 'index($0, prefix) == 1 { count += 1 } END { print count + 0 }')"
  [ "$matches" -eq 1 ] || error "Release manifest does not contain one digest for catalogue image $tagged_ref."
done
INFRA_IMAGES="${INFRA_IMAGES:-$(printf '%s\n' "$SIGNED_INFRA" | tr '\n' ' ')}"
declare -A DESTINATION_REFS
while IFS=$'\t' read -r component ref; do
  [ -n "$component" ] && DESTINATION_REFS["$component"]="$ref"
done <<< "$DESTINATION_INFRA_ROWS"
for ref in $INFRA_IMAGES; do
  case "
$SIGNED_INFRA
" in
    *"
$ref
"*) ;;
    *) error "Infrastructure image is not an authenticated release digest: $ref" ;;
  esac
  component="$(manifest_ref_component "$ref")"
  [ -n "${DESTINATION_REFS[$component]:-}" ] \
    || error "No authenticated Artifactory destination was materialized for $component."
done

# --dry-run only prints the plan, so it must work on a host without skopeo
# (that is the whole point of asking for the plan first).
if [ "$DRY_RUN" = false ]; then
  command -v skopeo >/dev/null 2>&1 || error "skopeo is required for digest-preserving registry copies."
fi

if [ "$DRY_RUN" = false ] && [ -n "${ARTIFACTORY_USERNAME:-}" ] && [ -n "${ARTIFACTORY_PASSWORD:-}" ]; then
  printf '%s\n' "$ARTIFACTORY_PASSWORD" \
    | skopeo login "$ARTIFACTORY_HOST" --username "$ARTIFACTORY_USERNAME" --password-stdin \
    || error "skopeo login to $ARTIFACTORY_HOST failed."
fi

log "Mirroring infra images into ${ARTIFACTORY_REGISTRY}"
for ref in $INFRA_IMAGES; do
  component="$(manifest_ref_component "$ref")"
  source_ref="$(manifest_registry_ref "$ref")"
  dest="${DESTINATION_REFS[$component]}"
  if [ "$DRY_RUN" = true ]; then
    echo "  would: skopeo copy --all --preserve-digests docker://$source_ref docker://$dest"
    echo "         skopeo inspect --raw docker://$dest"
    continue
  fi
  log "  $ref  ->  $dest"
  skopeo copy --all --preserve-digests "docker://$source_ref" "docker://$dest" \
    || error "Digest-preserving copy failed for $component."
  skopeo inspect --raw "docker://$dest" >/dev/null \
    || error "Signed digest for $component is missing from destination repository: $dest"
done

log "Done. ${ARTIFACTORY_REGISTRY} now holds the infra images the air-gapped cluster needs."
[ "$DRY_RUN" = true ] && warn "Dry run — nothing was pulled or pushed."
exit 0
