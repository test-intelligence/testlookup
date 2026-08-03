#!/usr/bin/env bash
# ============================================================================
# offline-bundle.sh — build the TestLookup air-gapped install bundle
# ----------------------------------------------------------------------------
# Produces ONE tarball that carries everything an air-gapped site needs:
# every container image (`docker save`), the rendered Kubernetes manifests,
# the Compose files, per-image SBOM status, a checksum file, and the
# import script that loads it all on the far side with zero internet.
#
#   make offline-bundle
#   make offline-bundle ARGS="--with-llm --version 0.1.0"
#   ./scripts/release/offline-bundle.sh --help
#
# The image list comes from deploy/images.manifest.txt — the single source of
# truth. Nothing here hardcodes an image tag.
#
# CONTAINER CLI: everything goes through `$DOCKER` (default `docker`) using
# only flags that podman implements identically (build/pull/save/tag/inspect).
# Podman behind a `docker` shim works unchanged; `DOCKER=podman` also works.
#
# Flags:
#   --version <v>       bundle version (default: the VERSION file)
#   --output <dir>      where the tarball lands (default: dist/)
#   --created-at <iso>  ISO-8601 UTC timestamp recorded in MANIFEST.json.
#                       PASS THIS from your release pipeline — see "Determinism".
#   --with-llm          also bundle the optional Ollama + ChromaDB images
#   --skip-build        don't build the app images (must already exist locally)
#   --skip-pull         don't pull infra images (must already exist locally)
#   --skip-k8s-render   don't render the k8s manifests (no kustomize on host)
#   --keep-staging      leave the uncompressed staging tree in place
#   --dry-run           print the plan and exit — builds/pulls/saves nothing
#   -h | --help         this help
#
# Determinism: the bundle contains a `created_at`. It is taken from
# --created-at, else $SOURCE_DATE_EPOCH, else the wall clock (with a warning).
# Two bundles built from the same commit with the same --created-at differ only
# by whatever the container builds themselves make non-reproducible — the
# bundle assembly adds no fresh nondeterminism of its own.
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/release/image-manifest.sh
. "$REPO_ROOT/scripts/release/image-manifest.sh"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()   { echo -e "${YELLOW}[!]${NC} $1"; }
error()  { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }
header() { echo -e "\n${CYAN}── $1 ─────────────────────────────────${NC}"; }
usage()  { sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

DOCKER="${DOCKER:-docker}"

# ── Flags ───────────────────────────────────────────────────────────────────
VERSION=""; OUTPUT_DIR="$REPO_ROOT/dist"; CREATED_AT=""
WITH_LLM=false; SKIP_BUILD=false; SKIP_PULL=false; SKIP_K8S=false
KEEP_STAGING=false; DRY_RUN=false
while [ $# -gt 0 ]; do
  case "$1" in
    --version)         VERSION="${2:?--version needs a value}"; shift 2 ;;
    --output)          OUTPUT_DIR="${2:?--output needs a value}"; shift 2 ;;
    --created-at)      CREATED_AT="${2:?--created-at needs a value}"; shift 2 ;;
    --with-llm)        WITH_LLM=true; shift ;;
    --skip-build)      SKIP_BUILD=true; shift ;;
    --skip-pull)       SKIP_PULL=true; shift ;;
    --skip-k8s-render) SKIP_K8S=true; shift ;;
    --keep-staging)    KEEP_STAGING=true; shift ;;
    --dry-run)         DRY_RUN=true; shift ;;
    -h|--help)         usage ;;
    *) error "Unknown flag: $1 (try --help)" ;;
  esac
done

# ── Version / timestamp ─────────────────────────────────────────────────────
if [ -z "$VERSION" ]; then
  VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null || true)"
  [ -n "$VERSION" ] || error "No --version given and VERSION file is missing/empty."
fi
APP_TAG="$VERSION"; export APP_TAG

if [ -z "$CREATED_AT" ]; then
  if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
    CREATED_AT="$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
                  || date -u -r "$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ)"
    log "created_at from SOURCE_DATE_EPOCH: $CREATED_AT"
  else
    CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    warn "created_at taken from the wall clock ($CREATED_AT) — pass --created-at (or SOURCE_DATE_EPOCH) for a reproducible bundle."
  fi
fi

GIT_SHA="$( (cd "$REPO_ROOT" && git rev-parse HEAD) 2>/dev/null || echo unknown)"
BUNDLE_NAME="testlookup-offline-${VERSION}"
STAGING="$OUTPUT_DIR/$BUNDLE_NAME"
TARBALL="$OUTPUT_DIR/${BUNDLE_NAME}.tar.gz"

BUNDLE_FILTER="core"
[ "$WITH_LLM" = true ] && BUNDLE_FILTER="core,llm"

# ── sha256: hard requirement, never silently degraded ───────────────────────
# A bundle whose checksums are "unavailable" is a bundle nobody can verify on
# arrival, which defeats the entire point of shipping one.
if command -v sha256sum >/dev/null 2>&1;   then SHA_CMD() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1;    then SHA_CMD() { shasum -a 256 "$1" | cut -d' ' -f1; }
elif command -v openssl >/dev/null 2>&1;   then SHA_CMD() { openssl dgst -sha256 "$1" | awk '{print $NF}'; }
else error "Need sha256sum, shasum or openssl to checksum the bundle. Install one and re-run."
fi

# ── Plan ────────────────────────────────────────────────────────────────────
APP_REFS="$(manifest_refs any app "$BUNDLE_FILTER")"
INFRA_REFS="$(manifest_refs any infra "$BUNDLE_FILTER")"
ALL_REFS="$(printf '%s\n%s\n' "$APP_REFS" "$INFRA_REFS" | grep -v '^$')"

header "Plan"
echo "  version      : $VERSION"
echo "  created_at   : $CREATED_AT"
echo "  git sha      : $GIT_SHA"
echo "  container CLI: $DOCKER"
echo "  llm images   : $WITH_LLM"
echo "  output       : $TARBALL"
echo "  images       :"
printf '                 %s\n' $ALL_REFS

if [ "$DRY_RUN" = true ]; then
  warn "--dry-run — nothing built, pulled, saved or written."
  exit 0
fi

command -v "$DOCKER" >/dev/null 2>&1 || error "'$DOCKER' not found on PATH. Set DOCKER=<cli> if you use podman/nerdctl under another name."
"$DOCKER" info >/dev/null 2>&1 || error "'$DOCKER info' failed — the container engine is not reachable."

rm -rf "$STAGING"
mkdir -p "$STAGING/images" "$STAGING/sbom" "$STAGING/compose" "$STAGING/compose/scripts" "$STAGING/k8s"

# ── Step 1 — Build the app images ───────────────────────────────────────────
# All three: the k8s manifests and docker-compose.release.yml both reference
# backend + frontend + mcp. A bundle missing mcp is an incomplete deployment.
if [ "$SKIP_BUILD" = false ]; then
  header "Step 1 — Build app images ($VERSION)"
  # Stage the client SDKs into the backend build context — backend/Dockerfile
  # does `COPY --from=builder /app/__client_sdks_staged ./client_sdks` and the
  # build fails without it (same contract as the homelab/OpenShift deploys).
  STAGED_SDK="$REPO_ROOT/backend/__client_sdks_staged"
  rm -rf "$STAGED_SDK"; mkdir -p "$STAGED_SDK"
  trap 'rm -rf "$STAGED_SDK"' EXIT INT TERM
  SDK_EXCLUDES=( '.pytest_cache' '__pycache__' '*.pyc' '.venv' 'venv' 'node_modules'
                 '.tox' '.mypy_cache' '.ruff_cache' 'target' 'build' '*.egg-info'
                 '.coverage' 'htmlcov' '.DS_Store' )
  if command -v rsync >/dev/null 2>&1; then
    RSYNC_EXCLUDES=(); for e in "${SDK_EXCLUDES[@]}"; do RSYNC_EXCLUDES+=("--exclude=$e"); done
    rsync -a "${RSYNC_EXCLUDES[@]}" "$REPO_ROOT/client/" "$STAGED_SDK/"
  else
    TAR_EXCLUDES=(); for e in "${SDK_EXCLUDES[@]}"; do TAR_EXCLUDES+=("--exclude=$e"); done
    ( cd "$REPO_ROOT/client" && tar "${TAR_EXCLUDES[@]}" -cf - . ) | ( cd "$STAGED_SDK" && tar -xf - )
  fi

  log "backend  -> testlookup/backend:$VERSION"
  "$DOCKER" build -t "testlookup/backend:$VERSION"  --target production -f "$REPO_ROOT/backend/Dockerfile"  "$REPO_ROOT/backend"
  rm -rf "$STAGED_SDK"; trap - EXIT INT TERM

  log "frontend -> testlookup/frontend:$VERSION"
  "$DOCKER" build -t "testlookup/frontend:$VERSION" --target production -f "$REPO_ROOT/frontend/Dockerfile" "$REPO_ROOT/frontend"

  log "mcp      -> testlookup/mcp:$VERSION"
  "$DOCKER" build -t "testlookup/mcp:$VERSION" -f "$REPO_ROOT/mcp/Dockerfile" "$REPO_ROOT/mcp"
else
  header "Step 1 — Build app images (SKIPPED)"
  for ref in $APP_REFS; do
    "$DOCKER" image inspect "$ref" >/dev/null 2>&1 || error "--skip-build but '$ref' is not present locally."
  done
fi

# ── Step 2 — Pull the third-party images ────────────────────────────────────
if [ "$SKIP_PULL" = false ]; then
  header "Step 2 — Pull infra images"
  for ref in $INFRA_REFS; do
    log "pull $ref"
    "$DOCKER" pull "$ref" >/dev/null || error "Failed to pull $ref (the BUILD host needs internet)."
  done
else
  header "Step 2 — Pull infra images (SKIPPED)"
  for ref in $INFRA_REFS; do
    "$DOCKER" image inspect "$ref" >/dev/null 2>&1 || error "--skip-pull but '$ref' is not present locally."
  done
fi

# ── Step 3 — docker save + SBOM ─────────────────────────────────────────────
# One tar per image (rather than one combined archive): it lets the import
# script report progress per image, lets an operator load a single image, and
# lets the CI verifier load ONLY busybox first and then run the checksum
# verification inside a --network none container. The cost is duplicated base
# layers across tars; gzip on the outer tarball claws most of that back.
header "Step 3 — Save images + collect SBOMs"

HAVE_SYFT=false
command -v syft >/dev/null 2>&1 && HAVE_SYFT=true
[ "$HAVE_SYFT" = true ] || warn "syft not found on PATH — see the SBOM note in the bundle README."

: > "$STAGING/images.list"
IMAGE_JSON=""
for ref in $ALL_REFS; do
  slug="$(manifest_ref_slug "$ref")"
  tar_rel="images/${slug}.tar"
  log "save $ref -> $tar_rel"
  "$DOCKER" save -o "$STAGING/$tar_rel" "$ref" || error "docker save failed for $ref"
  [ -s "$STAGING/$tar_rel" ] || error "docker save produced an empty tar for $ref"

  # images.list is what import-bundle.sh reads — plain "<tar>\t<ref>" so the
  # import side needs no JSON parser.
  printf '%s\t%s\n' "$tar_rel" "$ref" >> "$STAGING/images.list"

  size="$(wc -c < "$STAGING/$tar_rel" | tr -d '[:space:]')"
  sha="$(SHA_CMD "$STAGING/$tar_rel")"
  image_id="$("$DOCKER" image inspect --format '{{.Id}}' "$ref" 2>/dev/null || echo unknown)"
  repo_digest="$("$DOCKER" image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$ref" 2>/dev/null || true)"
  [ -n "$repo_digest" ] || repo_digest="none (built locally or pulled without a digest)"

  role="app"; case "$ref" in testlookup/*) role="app" ;; *) role="infra" ;; esac

  # ── SBOM, honestly ────────────────────────────────────────────────────────
  # `docker save` carries image layers + config. It does NOT carry the
  # BuildKit SBOM/provenance attestations that .github/workflows/release.yml
  # produces (`sbom: true`) — those live in the REGISTRY manifest list, not in
  # the saved tar. So:
  #   1. syft on the build host -> a real SPDX SBOM per image (preferred)
  #   2. otherwise -> an explicit "unavailable" record with the reason.
  # We never imply an SBOM exists when it does not.
  if [ "$HAVE_SYFT" = true ]; then
    if syft "${DOCKER}:${ref}" -o spdx-json > "$STAGING/sbom/${slug}.spdx.json" 2>/dev/null \
       || syft "$ref" -o spdx-json > "$STAGING/sbom/${slug}.spdx.json" 2>/dev/null; then
      sbom_json="{\"status\":\"present\",\"tool\":\"syft\",\"format\":\"spdx-json\",\"file\":\"sbom/${slug}.spdx.json\"}"
    else
      rm -f "$STAGING/sbom/${slug}.spdx.json"
      sbom_json="{\"status\":\"unavailable\",\"reason\":\"syft is installed but failed to scan this image\"}"
      warn "syft failed for $ref — recorded as unavailable"
    fi
  else
    sbom_json="{\"status\":\"unavailable\",\"reason\":\"syft was not installed on the build host; docker save does not carry BuildKit SBOM attestations (they live in the registry manifest, see .github/workflows/release.yml)\"}"
  fi

  entry="{\"ref\":\"$ref\",\"role\":\"$role\",\"file\":\"$tar_rel\",\"bytes\":$size,\"sha256\":\"$sha\",\"image_id\":\"$image_id\",\"repo_digest\":\"$repo_digest\",\"sbom\":$sbom_json}"
  if [ -z "$IMAGE_JSON" ]; then IMAGE_JSON="    $entry"; else IMAGE_JSON="$IMAGE_JSON,
    $entry"; fi
done

# ── Step 4 — Compose files ──────────────────────────────────────────────────
header "Step 4 — Compose files"
cp "$REPO_ROOT/docker-compose.release.yml" "$STAGING/compose/"
cp "$REPO_ROOT/docker-compose.airgap.yml"  "$STAGING/compose/"
cp "$REPO_ROOT/.env.example"               "$STAGING/compose/" 2>/dev/null || warn ".env.example not found — operators must supply their own .env"
cp "$REPO_ROOT/scripts/gen-dev-env.sh"     "$STAGING/compose/scripts/" 2>/dev/null || true
cp "$REPO_ROOT/scripts/init-db.sql"        "$STAGING/compose/scripts/"
log "compose/: release + airgap override + .env.example + init-db.sql"

# ── Step 5 — Kubernetes manifests ───────────────────────────────────────────
# The openshift-artifactory overlay is the air-gap-ready one: its images: block
# re-points EVERY image (app + infra) at a single registry placeholder. We
# render it with the placeholders INTACT — import-bundle.sh substitutes them,
# exactly like openshiftsetup/deploy-openshift-artifactory.sh does, so the
# air-gapped side needs no kustomize.
header "Step 5 — Render k8s manifests"
K8S_STATUS='{"status":"unavailable","reason":"not rendered"}'
if [ "$SKIP_K8S" = true ]; then
  K8S_STATUS='{"status":"unavailable","reason":"--skip-k8s-render was passed"}'
  warn "--skip-k8s-render — the bundle ships the raw overlay only."
else
  RENDER_CMD=""
  if command -v kustomize >/dev/null 2>&1; then RENDER_CMD="kustomize build"
  elif command -v kubectl >/dev/null 2>&1;   then RENDER_CMD="kubectl kustomize"
  fi
  if [ -n "$RENDER_CMD" ] && $RENDER_CMD "$REPO_ROOT/k8s/overlays/openshift-artifactory" > "$STAGING/k8s/testlookup-airgap.yaml" 2>/dev/null; then
    log "k8s/testlookup-airgap.yaml rendered with ${RENDER_CMD%% *} (placeholders intact)"
    K8S_STATUS="{\"status\":\"present\",\"file\":\"k8s/testlookup-airgap.yaml\",\"renderer\":\"${RENDER_CMD%% *}\",\"overlay\":\"k8s/overlays/openshift-artifactory\"}"
  else
    rm -f "$STAGING/k8s/testlookup-airgap.yaml"
    warn "No working kustomize/kubectl — shipping the raw overlay only. Render on the far side with 'kubectl apply -k k8s/overlay'."
    K8S_STATUS='{"status":"unavailable","reason":"neither kustomize nor kubectl was available on the build host"}'
  fi
fi
# Always ship the raw overlay + base so operators can customize and re-render.
mkdir -p "$STAGING/k8s/overlay"
cp -r "$REPO_ROOT/k8s/base"                             "$STAGING/k8s/base"
cp -r "$REPO_ROOT/k8s/overlays/openshift-artifactory/." "$STAGING/k8s/overlay/"
# The overlay references ../../base — rewrite to the flattened bundle layout.
if [ -f "$STAGING/k8s/overlay/kustomization.yaml" ]; then
  sed -i.bak 's|- \.\./\.\./base|- ../base|' "$STAGING/k8s/overlay/kustomization.yaml"
  rm -f "$STAGING/k8s/overlay/kustomization.yaml.bak"
fi
find "$STAGING/k8s" -name '*.deploy-bak' -delete 2>/dev/null || true

# ── Step 6 — Import script, manifest, README ────────────────────────────────
header "Step 6 — Import script + MANIFEST.json + README"
cp "$REPO_ROOT/scripts/release/import-bundle.sh" "$STAGING/import-bundle.sh"
chmod +x "$STAGING/import-bundle.sh" 2>/dev/null || true
cp "$REPO_ROOT/deploy/images.manifest.txt" "$STAGING/images.manifest.txt"

SBOM_MODE="unavailable"; [ "$HAVE_SYFT" = true ] && SBOM_MODE="syft"
cat > "$STAGING/MANIFEST.json" <<JSON
{
  "schema_version": 1,
  "bundle": "$BUNDLE_NAME",
  "version": "$VERSION",
  "created_at": "$CREATED_AT",
  "git_sha": "$GIT_SHA",
  "includes_llm_images": $WITH_LLM,
  "app_image_tag": "$VERSION",
  "registry_layout": {
    "infra": "<registry>/<image>:<tag>",
    "app": "<registry>/testlookup/<name>:$VERSION"
  },
  "sbom_mode": "$SBOM_MODE",
  "k8s_manifests": $K8S_STATUS,
  "images": [
$IMAGE_JSON
  ]
}
JSON
python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$STAGING/MANIFEST.json" 2>/dev/null \
  || python -c "import json,sys;json.load(open(sys.argv[1]))" "$STAGING/MANIFEST.json" 2>/dev/null \
  || warn "Could not validate MANIFEST.json (no python on PATH) — check it by hand."

cat > "$STAGING/README.md" <<README
# TestLookup offline install bundle — $VERSION

Created: \`$CREATED_AT\` · source commit: \`$GIT_SHA\`

Everything TestLookup needs to run with **no internet access**: every container
image, the Kubernetes manifests, the Compose files, and the import script.

## Contents

| Path | What |
|---|---|
| \`images/*.tar\` | one \`docker save\` archive per image |
| \`images.list\` | \`<tar>\\t<image ref>\` — what \`import-bundle.sh\` reads |
| \`images.manifest.txt\` | the source-of-truth image manifest this was built from |
| \`MANIFEST.json\` | image list + sha256 + image ids + **per-image SBOM status** |
| \`checksums.sha256\` | checksum of every other file in the bundle |
| \`compose/\` | \`docker-compose.release.yml\` + \`docker-compose.airgap.yml\` + \`.env.example\` |
| \`k8s/testlookup-airgap.yaml\` | rendered manifests, registry/storage placeholders intact |
| \`k8s/base/\`, \`k8s/overlay/\` | the raw Kustomize sources, for customizing |
| \`sbom/\` | SPDX SBOMs, **when the build host had \`syft\`** (see below) |
| \`import-bundle.sh\` | verify → load → retag → (optional) push → next steps |

## Quick start (air-gapped host)

\`\`\`bash
tar xzf ${BUNDLE_NAME}.tar.gz && cd $BUNDLE_NAME
./import-bundle.sh --verify-only                       # checksums only
./import-bundle.sh --registry registry.internal --push # load, retag, push
\`\`\`

Then follow the printed next steps for your target (Compose / Kubernetes /
OpenShift). Full procedure: \`user-guide/air-gapped-install.md\` in the repo.

## SBOM status — read this

This bundle's SBOM mode is **\`$SBOM_MODE\`**.

* \`syft\` — every image has a real SPDX SBOM under \`sbom/\`, generated on the
  build host. \`MANIFEST.json\` points at the file for each image.
* \`unavailable\` — **there are no SBOMs in this bundle.** The published GHCR
  images carry BuildKit SBOM + provenance attestations, but those live in the
  *registry manifest list*, and \`docker save\` does not copy them into the tar.
  \`MANIFEST.json\` records \`"sbom": {"status":"unavailable", ...}\` per image
  rather than pretending otherwise. To get SBOMs, install
  [syft](https://github.com/anchore/syft) on the build host and rebuild the
  bundle, or pull the attestation from the registry while still connected:
  \`docker buildx imagetools inspect <ref> --format '{{json .SBOM}}'\`.

Image \`sha256\` values in \`MANIFEST.json\` are checksums **of the tar files**;
\`image_id\` is the image config digest reported by the container engine.
README

# ── Step 7 — Checksums ──────────────────────────────────────────────────────
header "Step 7 — checksums.sha256"
# The file list is written OUTSIDE the staging tree: a scratch file created
# inside it would be picked up by its own `find` and then checksummed into a
# manifest that references a file the bundle does not ship — which fails
# verification on the customer's machine, not here.
FILELIST="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/testlookup-bundle-$$.list")"
( cd "$STAGING" && find . -type f ! -name 'checksums.sha256' | sed 's|^\./||' | LC_ALL=C sort ) > "$FILELIST"
: > "$STAGING/checksums.sha256"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  printf '%s  %s\n' "$(SHA_CMD "$STAGING/$f")" "$f" >> "$STAGING/checksums.sha256"
done < "$FILELIST"
rm -f "$FILELIST"
log "checksummed $(wc -l < "$STAGING/checksums.sha256" | tr -d '[:space:]') files"

# ── Step 8 — Tarball ────────────────────────────────────────────────────────
header "Step 8 — Tarball"
mkdir -p "$OUTPUT_DIR"
rm -f "$TARBALL"
TAR_FLAGS=( -cf - -C "$OUTPUT_DIR" "$BUNDLE_NAME" )
# GNU tar: stable member order + fixed mtime/ownership. gzip -n drops the
# gzip-header timestamp. Both are no-ops for correctness, so they are
# feature-detected rather than required.
if tar --version 2>/dev/null | grep -qi 'gnu tar'; then
  TAR_FLAGS=( --sort=name --owner=0 --group=0 --numeric-owner --mtime="$CREATED_AT" "${TAR_FLAGS[@]}" )
fi
if command -v gzip >/dev/null 2>&1; then
  tar "${TAR_FLAGS[@]}" | gzip -n -9 > "$TARBALL"
else
  tar -czf "$TARBALL" -C "$OUTPUT_DIR" "$BUNDLE_NAME"
fi
BUNDLE_SHA="$(SHA_CMD "$TARBALL")"
printf '%s  %s\n' "$BUNDLE_SHA" "${BUNDLE_NAME}.tar.gz" > "${TARBALL}.sha256"

[ "$KEEP_STAGING" = true ] || rm -rf "$STAGING"

header "Done"
echo "  bundle : $TARBALL"
echo "  size   : $(du -h "$TARBALL" 2>/dev/null | cut -f1 || wc -c < "$TARBALL") "
echo "  sha256 : $BUNDLE_SHA  (also in ${TARBALL}.sha256)"
echo ""
echo "  Transfer both files to the air-gapped site, then:"
echo "    sha256sum -c ${BUNDLE_NAME}.tar.gz.sha256"
echo "    tar xzf ${BUNDLE_NAME}.tar.gz && cd ${BUNDLE_NAME} && ./import-bundle.sh --help"
