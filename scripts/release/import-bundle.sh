#!/usr/bin/env bash
# ============================================================================
# import-bundle.sh — load a TestLookup offline bundle on an air-gapped host
# ----------------------------------------------------------------------------
# Ships INSIDE the bundle (scripts/release/import-bundle.sh is the source of
# record). Runs with ZERO internet access: everything it needs is in the
# bundle directory next to it. The only network traffic it can make is the
# optional `--push` to YOUR OWN registry.
#
#   ./import-bundle.sh --verify-only
#   ./import-bundle.sh --registry registry.internal --push
#   ./import-bundle.sh --registry artifactory.example.com/testlookup-docker --push \
#       --storage-class ocs-storagecluster-ceph-rbd \
#       --apps-domain apps.ocp.example.com
#
# --registry is the registry BASE. Images land at <base>/<ref>, so
#   --registry registry.internal  ->  registry.internal/testlookup/backend:<v>
#                                     registry.internal/postgres:16-alpine
# A repository path is allowed (artifactory.example.com/testlookup-docker);
# do NOT append `/testlookup` yourself — the app refs already carry it.
#
# What it does, in order:
#   1. verify checksums.sha256 (refuses to continue on a mismatch)
#   2. `docker load` every images/*.tar listed in images.list
#   3. retag each image into <registry>/… using the bundle's naming contract:
#        infra -> <registry>/<image>:<tag>
#        app   -> <registry>/testlookup/<name>:<version>
#      (identical to openshiftsetup/mirror-images.sh, so the OpenShift overlay
#       and this bundle agree on where images live)
#   4. optionally push them to that registry
#   5. substitute the *_PLACEHOLDER tokens in the rendered k8s manifest — the
#      same mechanism deploy-openshift-artifactory.sh uses — and write a
#      ready-to-apply file
#   6. print the exact next steps for Compose / Kubernetes / OpenShift
#
# Flags:
#   --registry <reg>        target registry base (required to retag/push)
#   --push                  push the retagged images
#   --username/--password   optional registry login before pushing
#   --verify-only           checksums only; load nothing
#   --load-only             load images, skip retag/push
#   --skip-verify           skip checksum verification (NOT recommended)
#   --storage-class <sc>    substitute STORAGE_CLASS_PLACEHOLDER
#   --apps-domain <d>       derive the three route hosts from one domain
#   --route-host-frontend|--route-host-api|--route-host-mcp <host>
#   --output <file>         rendered output (default k8s/testlookup-airgap.rendered.yaml)
#   -h | --help
#
# Container CLI: set DOCKER=podman (or any docker-compatible CLI) if needed.
# ============================================================================
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER="${DOCKER:-docker}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()   { echo -e "${YELLOW}[!]${NC} $1"; }
error()  { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }
header() { echo -e "\n${CYAN}── $1 ─────────────────────────────────${NC}"; }
usage()  { sed -n '2,51p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

REGISTRY=""; PUSH=false; VERIFY_ONLY=false; LOAD_ONLY=false; SKIP_VERIFY=false
REG_USER=""; REG_PASS=""; STORAGE_CLASS=""; APPS_DOMAIN=""
RH_FRONTEND=""; RH_API=""; RH_MCP=""
OUT_FILE="$BUNDLE_DIR/k8s/testlookup-airgap.rendered.yaml"

while [ $# -gt 0 ]; do
  case "$1" in
    --registry)             REGISTRY="${2:?--registry needs a value}"; shift 2 ;;
    --push)                 PUSH=true; shift ;;
    --username)             REG_USER="${2:?}"; shift 2 ;;
    --password)             REG_PASS="${2:?}"; shift 2 ;;
    --verify-only)          VERIFY_ONLY=true; shift ;;
    --load-only)            LOAD_ONLY=true; shift ;;
    --skip-verify)          SKIP_VERIFY=true; shift ;;
    --storage-class)        STORAGE_CLASS="${2:?}"; shift 2 ;;
    --apps-domain)          APPS_DOMAIN="${2:?}"; shift 2 ;;
    --route-host-frontend)  RH_FRONTEND="${2:?}"; shift 2 ;;
    --route-host-api)       RH_API="${2:?}"; shift 2 ;;
    --route-host-mcp)       RH_MCP="${2:?}"; shift 2 ;;
    --output)               OUT_FILE="${2:?}"; shift 2 ;;
    -h|--help)              usage ;;
    *) error "Unknown flag: $1 (try --help)" ;;
  esac
done

[ -f "$BUNDLE_DIR/images.list" ]       || error "images.list not found — run this from inside the unpacked bundle."
[ -f "$BUNDLE_DIR/checksums.sha256" ]  || error "checksums.sha256 not found — this does not look like a TestLookup bundle."

VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$BUNDLE_DIR/MANIFEST.json" 2>/dev/null | head -n1)"
[ -n "$VERSION" ] || VERSION="unknown"
log "Bundle version: $VERSION"

# ── Step 1 — Verify checksums ───────────────────────────────────────────────
header "Step 1 — Verify checksums"
if [ "$SKIP_VERIFY" = true ]; then
  warn "--skip-verify: bundle integrity was NOT checked. Do not do this for a production install."
else
  cd "$BUNDLE_DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c checksums.sha256 --quiet || error "CHECKSUM MISMATCH — the bundle is corrupt or was modified in transit. Do not install it."
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -c checksums.sha256 >/dev/null || error "CHECKSUM MISMATCH — the bundle is corrupt or was modified in transit. Do not install it."
  elif command -v openssl >/dev/null 2>&1; then
    # Manual loop — no `-c` mode in openssl. Same fail-closed semantics.
    bad=0
    while read -r want file; do
      got="$(openssl dgst -sha256 "$file" | awk '{print $NF}')"
      [ "$got" = "$want" ] || { echo "FAILED: $file"; bad=1; }
    done < checksums.sha256
    [ "$bad" -eq 0 ] || error "CHECKSUM MISMATCH — the bundle is corrupt or was modified in transit. Do not install it."
  else
    error "No sha256 tool (sha256sum / shasum / openssl) available. Install one, or re-run with --skip-verify if you accept an unverified bundle."
  fi
  log "All checksums match."
fi

if [ "$VERIFY_ONLY" = true ]; then
  log "--verify-only — nothing loaded."
  exit 0
fi

command -v "$DOCKER" >/dev/null 2>&1 || error "'$DOCKER' not found on PATH. Set DOCKER=<cli> for podman/nerdctl."
"$DOCKER" info >/dev/null 2>&1 || error "'$DOCKER info' failed — the container engine is not reachable."

# ── Step 2 — Load images ────────────────────────────────────────────────────
header "Step 2 — Load images"
LOADED=0
while IFS=$'\t' read -r tar_rel ref; do
  [ -n "${tar_rel:-}" ] || continue
  [ -f "$BUNDLE_DIR/$tar_rel" ] || error "Missing image archive: $tar_rel"
  log "load $ref"
  "$DOCKER" load -i "$BUNDLE_DIR/$tar_rel" >/dev/null || error "docker load failed for $tar_rel"
  LOADED=$((LOADED + 1))
done < "$BUNDLE_DIR/images.list"
log "Loaded $LOADED images. (docker load never contacts a registry — this step is fully offline.)"

if [ "$LOAD_ONLY" = true ]; then
  log "--load-only — images are in the local engine under their original names."
  exit 0
fi

# ── Step 3 — Retag into the local registry namespace ────────────────────────
if [ -z "$REGISTRY" ]; then
  warn "No --registry given: images stay under their original names."
  warn "For Kubernetes/OpenShift you need them in a registry the cluster can reach — re-run with --registry <host>[/<path>] --push."
  exit 0
fi
REGISTRY="${REGISTRY%/}"

header "Step 3 — Retag for $REGISTRY"
RETAGGED=""
while IFS=$'\t' read -r tar_rel ref; do
  [ -n "${ref:-}" ] || continue
  # One rule for both roles — the manifest already names app images
  # `testlookup/<n>:<version>`, so prefixing the registry yields
  #   infra: <reg>/postgres:16-alpine
  #   app:   <reg>/testlookup/backend:<version>
  # which is exactly what mirror-images.sh and the OpenShift overlay expect.
  dest="${REGISTRY}/${ref}"
  "$DOCKER" tag "$ref" "$dest" || error "docker tag failed: $ref -> $dest"
  echo "    $ref  ->  $dest"
  RETAGGED="$RETAGGED $dest"
done < "$BUNDLE_DIR/images.list"

# ── Step 4 — Push ───────────────────────────────────────────────────────────
if [ "$PUSH" = true ]; then
  header "Step 4 — Push to $REGISTRY"
  REG_HOST="${REGISTRY%%/*}"
  if [ -n "$REG_USER" ] && [ -n "$REG_PASS" ]; then
    echo "$REG_PASS" | "$DOCKER" login "$REG_HOST" -u "$REG_USER" --password-stdin \
      || error "login to $REG_HOST failed."
  fi
  for dest in $RETAGGED; do
    log "push $dest"
    "$DOCKER" push "$dest" || error "push failed: $dest (check the registry is reachable and you are logged in)"
  done
else
  warn "Skipping push (--push not given). The images are retagged locally only."
fi

# ── Step 5 — Render the k8s manifests ───────────────────────────────────────
# Same placeholder-substitution contract as
# openshiftsetup/deploy-openshift-artifactory.sh: the committed manifests carry
# tokens that deliberately do not resolve, so an un-rendered apply fails at
# ImagePull instead of silently pulling from the wrong registry.
header "Step 5 — Render k8s manifests"
SRC_YAML="$BUNDLE_DIR/k8s/testlookup-airgap.yaml"
if [ ! -f "$SRC_YAML" ]; then
  warn "k8s/testlookup-airgap.yaml is not in this bundle (MANIFEST.json records why)."
  warn "Render it yourself from the shipped sources: kubectl kustomize k8s/overlay > out.yaml"
else
  if [ -n "$APPS_DOMAIN" ]; then
    RH_FRONTEND="${RH_FRONTEND:-testlookup.${APPS_DOMAIN}}"
    RH_API="${RH_API:-testlookup-api.${APPS_DOMAIN}}"
    RH_MCP="${RH_MCP:-testlookup-mcp.${APPS_DOMAIN}}"
  fi
  mkdir -p "$(dirname "$OUT_FILE")"
  sed -e "s|ARTIFACTORY_REGISTRY_PLACEHOLDER|${REGISTRY}|g" \
      -e "s|APP_TAG_PLACEHOLDER|${VERSION}|g" \
      -e "s|STORAGE_CLASS_PLACEHOLDER|${STORAGE_CLASS}|g" \
      -e "s|ROUTE_HOST_FRONTEND_PLACEHOLDER|${RH_FRONTEND}|g" \
      -e "s|ROUTE_HOST_API_PLACEHOLDER|${RH_API}|g" \
      -e "s|ROUTE_HOST_MCP_PLACEHOLDER|${RH_MCP}|g" \
      "$SRC_YAML" > "$OUT_FILE"

  REMAINING="$(grep -c '_PLACEHOLDER' "$OUT_FILE" || true)"
  if [ "${REMAINING:-0}" -gt 0 ]; then
    warn "$REMAINING placeholder(s) still unresolved in $OUT_FILE"
    grep -n '_PLACEHOLDER' "$OUT_FILE" | head -10
    warn "Pass --storage-class / --apps-domain (or the --route-host-* flags) and re-run, or edit the file before applying."
  else
    log "Rendered: $OUT_FILE (no placeholders left)"
  fi
fi

# ── Step 6 — Next steps ─────────────────────────────────────────────────────
header "Next steps"
cat <<NEXT

The images now live under ${REGISTRY}. Pick your target:

── Docker Compose ─────────────────────────────────────────────────────────
  cd compose
  bash scripts/gen-dev-env.sh           # or copy .env.example and fill it in
  cat >> .env <<'ENV'
  TESTLOOKUP_REGISTRY=${REGISTRY}
  TESTLOOKUP_VERSION=${VERSION}
ENV
  docker compose -f docker-compose.release.yml -f docker-compose.airgap.yml up -d

  The airgap override re-points EVERY image (app + third-party) at
  \$TESTLOOKUP_REGISTRY. Without it, compose would try docker.io and fail.

── Kubernetes ─────────────────────────────────────────────────────────────
  kubectl create namespace testlookup
  kubectl -n testlookup create secret docker-registry local-registry \\
      --docker-server=${REGISTRY%%/*} --docker-username=<user> --docker-password=<pass>
  kubectl -n testlookup patch serviceaccount default \\
      -p '{"imagePullSecrets":[{"name":"local-registry"}]}'
  # create the testlookup-secrets Secret (see user-guide/air-gapped-install.md)
  kubectl apply -f ${OUT_FILE#"$BUNDLE_DIR"/}

── OpenShift ──────────────────────────────────────────────────────────────
  Same as Kubernetes, plus:
  oc adm policy add-scc-to-user anyuid -z default -n testlookup
  The rendered manifests already include the Routes (edge TLS + redirect).
  Re-run this script with --apps-domain <your apps domain> if the route hosts
  above still show a placeholder.

Verify afterwards:  kubectl -n testlookup get pods
Full procedure:     user-guide/air-gapped-install.md
NEXT
