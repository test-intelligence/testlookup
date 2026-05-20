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
# It mirrors INFRA images by default. Pass --with-app to ALSO pull + re-push
# pre-built app images from a source registry (rarely needed — the deploy
# script builds + pushes those itself).
#
# Config: reads openshiftsetup/artifactory.env (same file as the deploy
# script) or environment variables. Required: ARTIFACTORY_REGISTRY.
#
# Usage:
#   ./openshiftsetup/mirror-images.sh                 # mirror infra images
#   ./openshiftsetup/mirror-images.sh --dry-run       # print the plan only
#   INFRA_IMAGES="postgres:16-alpine" ./openshiftsetup/mirror-images.sh
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="${ARTIFACTORY_ENV_FILE:-$REPO_ROOT/openshiftsetup/artifactory.env}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()   { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }

DRY_RUN=false
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=true ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) error "Unknown flag: $a" ;;
  esac
done

if [ -f "$CONFIG_FILE" ]; then set -a; . "$CONFIG_FILE"; set +a; fi
[ -n "${ARTIFACTORY_REGISTRY:-}" ] || error "ARTIFACTORY_REGISTRY is required (config or env)."
ARTIFACTORY_HOST="${ARTIFACTORY_REGISTRY%%/*}"
INFRA_IMAGES="${INFRA_IMAGES:-postgres:16-alpine redis:7-alpine mongo:7 minio/minio:RELEASE.2025-09-07T16-13-09Z chromadb/chroma:0.5.20 ollama/ollama:0.5.4 busybox:1.36}"

command -v docker >/dev/null 2>&1 || error "docker is required."
if [ "$DRY_RUN" = false ]; then
  docker info >/dev/null 2>&1 || error "Docker daemon not reachable."
fi

if [ "$DRY_RUN" = false ] && [ -n "${ARTIFACTORY_USERNAME:-}" ] && [ -n "${ARTIFACTORY_PASSWORD:-}" ]; then
  echo "$ARTIFACTORY_PASSWORD" | docker login "$ARTIFACTORY_HOST" -u "$ARTIFACTORY_USERNAME" --password-stdin \
    || error "docker login to $ARTIFACTORY_HOST failed."
fi

log "Mirroring infra images into ${ARTIFACTORY_REGISTRY}"
for ref in $INFRA_IMAGES; do
  dest="${ARTIFACTORY_REGISTRY}/${ref}"
  if [ "$DRY_RUN" = true ]; then
    echo "  would: docker pull $ref && docker tag $ref $dest && docker push $dest"
    continue
  fi
  log "  $ref  ->  $dest"
  docker pull "$ref"
  docker tag  "$ref" "$dest"
  docker push "$dest"
done

log "Done. ${ARTIFACTORY_REGISTRY} now holds the infra images the air-gapped cluster needs."
[ "$DRY_RUN" = true ] && warn "Dry run — nothing was pulled or pushed."
exit 0
