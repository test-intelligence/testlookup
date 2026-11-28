#!/usr/bin/env bash
# install.sh — authenticated release install for TestLookup (no git clone needed).
#
# Download this file from a versioned GitHub release and verify its immutable
# release-asset digest and signed provenance before running it. README.md
# documents that bootstrap sequence.
#
# It downloads the pinned-image release stack, verifies the release's signed
# image manifest, generates a local .env with random secrets, and brings the
# stack up with `docker compose`. Nothing is built from source — published
# releases pull the exact image digests CI built and attested.
#
# Environment overrides:
#   TL_DIR=<path>            install directory          (default ./testlookup)
#   TL_REF=<branch|sha|tag>  raw-file ref to download   (default: the latest
#                            published release)
#   TESTLOOKUP_VERSION=<tag> release to run             (default: that release)
#                            A release tag (vX.Y.Z) in either one installs that
#                            release: the other follows it unless you set it
#                            too. Published releases require GitHub CLI (`gh`):
#                            downloaded files and exact image digests are
#                            authenticated before anything runs.
#                            With neither set, the installer asks GitHub which
#                            release is the latest and installs that one, files
#                            and images together, checked the same way. The
#                            unreleased tip is TL_REF=main: nothing vouches for
#                            those files and the images are the moving `latest`.
#   TL_LATEST_RELEASE_URL=<url>  where the latest release is looked up (a
#                            mirror; default the GitHub API's releases/latest)
#   TL_PROFILE=demo          add a compose profile       (demo = demo projects, the
#                            documented default for an evaluation; or local-llm)
#   TL_NO_UP=1               download + gen .env only, don't `up`
#   TL_RAW_BASE=<url>        where the files come from (a mirror; default
#                            https://raw.githubusercontent.com/test-intelligence/testlookup)
#   TL_RELEASE_BASE=<url>    where release verification assets come from (default
#                            https://github.com/test-intelligence/testlookup/releases/download)
#
# The checksum step protects the files this script downloads. The documented
# bootstrap separately authenticates this script's versioned release asset
# before Bash interprets it.
#
# Everything runs from main(), called on the last line: a download cut short
# by the network defines functions but runs nothing.
set -euo pipefail

REPO_RAW="${TL_RAW_BASE:-https://raw.githubusercontent.com/test-intelligence/testlookup}"
RELEASE_DOWNLOADS="${TL_RELEASE_BASE:-https://github.com/test-intelligence/testlookup/releases/download}"
LATEST_RELEASE_URL="${TL_LATEST_RELEASE_URL:-https://api.github.com/repos/test-intelligence/testlookup/releases/latest}"
# Empty until main() decides: a release tag you named, or the latest release.
REF="${TL_REF:-}"
DIR="${TL_DIR:-testlookup}"
COMPOSE_FILE="docker-compose.release.yml"
IMAGE_MANIFEST="release-manifest.json"
IMAGE_OVERRIDE_FILE="docker-compose.images.yml"
ATTESTATION_REPOSITORY="test-intelligence/testlookup"
RELEASE_SIGNER="test-intelligence/testlookup/.github/workflows/offline-bundle.yml"
IMAGE_SIGNER="test-intelligence/testlookup/.github/workflows/ci.yml"
# Every file this script downloads. The release's SHA256SUMS lists each one.
FILES=("$COMPOSE_FILE" ".env.example" "scripts/gen-dev-env.sh" "scripts/init-db.sql")

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

fetch() {  # fetch <remote-path> <local-path>
  local url="${REPO_RAW}/${REF}/$1"
  mkdir -p "$(dirname "$2")"
  $DL "$2" "$url" || die "failed to download $url"
}

fetch_release_asset() {  # fetch_release_asset <asset-name> <local-path>
  local url="${RELEASE_DOWNLOADS}/${REF}/$1"
  mkdir -p "$(dirname "$2")"
  $DL "$2" "$url"
}

is_release_tag() {  # vX.Y.Z, the form the release workflow accepts
  [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

# latest_release_tag -> the tag (vX.Y.Z) of the latest published release.
# Fails when the lookup fails or the answer is not a release tag: the caller
# stops, it does not fall back to an unreleased branch.
latest_release_tag() {
  local answer tag
  answer="$(mktemp)" || return 1
  $DL "$answer" "$LATEST_RELEASE_URL" 2>/dev/null || { rm -f "$answer"; return 1; }
  tag="$(tr -d '\r' < "$answer" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  rm -f "$answer"
  is_release_tag "$tag" || return 1
  printf '%s\n' "$tag"
}

sha256_of() {  # sha256_of <file> -> hex digest; fails when no tool is installed
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    return 1
  fi
}

# verify_release_files <dir>: compare every downloaded file in <dir> with the
# release's published SHA256SUMS. Any failure stops the install: a missing
# sums file, a file the sums do not list, a missing checksum tool, a mismatch.
verify_release_files() {
  local stage="$1" sums="$1/SHA256SUMS" url="${RELEASE_DOWNLOADS}/${REF}/SHA256SUMS"
  local path want got
  fetch_release_asset "SHA256SUMS" "$sums" \
    || die "could not download the checksums for $REF ($url). Nothing was installed or run. Check that $REF is a published release with a SHA256SUMS file; to install the unreleased tip without a checksum, set TL_REF=main."
  for path in "${FILES[@]}"; do
    want="$(tr -d '\r' < "$sums" | awk -v p="$path" '$2 == p || $2 == ("*" p) { print $1; exit }')"
    [ -n "$want" ] || die "the checksums for $REF do not list $path. Nothing was installed or run."
    got="$(sha256_of "$stage/$path")" \
      || die "need sha256sum, shasum or openssl to check the download. Nothing was installed or run."
    [ "$got" = "$want" ] || die "checksum mismatch for $path from $REF (expected $want, got $got). The download is not the released file. Nothing was installed or run."
  done
  say "Checked ${#FILES[@]} downloaded files against the $REF SHA256SUMS"
}

# Read one scalar from the canonical JSON emitted by release_manifest.py.
# The signed manifest is still treated as untrusted input: every extracted
# value is checked below before it reaches gh, Compose, or a generated file.
manifest_scalar() {  # manifest_scalar <manifest> <field>
  awk -v key="\"$2\":" '
    $1 == key {
      count += 1
      value = $0
      sub(/^[^:]+:[[:space:]]*"/, "", value)
      sub(/",?[[:space:]]*$/, "", value)
    }
    END {
      if (count != 1 || value == "") exit 1
      print value
    }
  ' "$1"
}

manifest_image_ref() {  # manifest_image_ref <manifest> <component>
  awk -v component="\"$2\":" '
    $1 == component && $2 == "{" { component_count += 1; inside = 1; next }
    inside && $1 == "\"source_ref\":" {
      ref_count += 1
      value = $0
      sub(/^[^:]+:[[:space:]]*"/, "", value)
      sub(/",?[[:space:]]*$/, "", value)
      inside = 0
    }
    inside && $0 ~ /^    },?[[:space:]]*$/ { inside = 0 }
    END {
      if (component_count != 1 || ref_count != 1 || value == "") exit 1
      print value
    }
  ' "$1"
}

verify_release_provenance() {  # verify_release_provenance <staging-dir>
  local stage="$1" manifest="$1/$IMAGE_MANIFEST"
  local bundle_name="testlookup-offline-${REF#v}.intoto.jsonl"
  local bundle="$1/$bundle_name" manifest_version source_sha component ref ref_count expected digest

  fetch_release_asset "$IMAGE_MANIFEST" "$manifest" \
    || die "could not download the authenticated image manifest for $REF. Nothing was installed or run."
  fetch_release_asset "$bundle_name" "$bundle" \
    || die "could not download the signed provenance bundle for $REF. Nothing was installed or run."

  gh attestation verify "$stage/SHA256SUMS" \
    --repo "$ATTESTATION_REPOSITORY" \
    --signer-workflow "$RELEASE_SIGNER" \
    --source-ref refs/heads/main \
    --bundle "$bundle" >/dev/null \
    || die "the $REF SHA256SUMS does not have trusted TestLookup release provenance. Nothing was installed or run."
  gh attestation verify "$manifest" \
    --repo "$ATTESTATION_REPOSITORY" \
    --signer-workflow "$RELEASE_SIGNER" \
    --source-ref refs/heads/main \
    --bundle "$bundle" >/dev/null \
    || die "the $REF image manifest does not have trusted TestLookup release provenance. Nothing was installed or run."

  manifest_version="$(manifest_scalar "$manifest" version)" \
    || die "the authenticated image manifest has no single version. Nothing was installed or run."
  [ "$manifest_version" = "$REF" ] \
    || die "the authenticated image manifest is for $manifest_version, not $REF. Nothing was installed or run."
  source_sha="$(manifest_scalar "$manifest" source_sha)" \
    || die "the authenticated image manifest has no single source SHA. Nothing was installed or run."
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] \
    || die "the authenticated image manifest has an invalid source SHA. Nothing was installed or run."
  ref_count="$(awk '$1 == "\"source_ref\":" { count += 1 } END { print count + 0 }' "$manifest")"
  [ "$ref_count" -eq 10 ] \
    || die "the authenticated image manifest must contain exactly ten application and infrastructure image references. Nothing was installed or run."

  for component in backend frontend mcp postgres valkey mongo versitygw busybox ollama chroma; do
    ref="$(manifest_image_ref "$manifest" "$component")" \
      || die "the authenticated image manifest has no single $component reference. Nothing was installed or run."
    case "$component" in
      backend|frontend|mcp) expected="ghcr.io/test-intelligence/testlookup/$component" ;;
      postgres) expected="postgres:16-alpine" ;;
      valkey) expected="valkey/valkey:8.1-alpine" ;;
      mongo) expected="mongo:7" ;;
      versitygw) expected="ghcr.io/versity/versitygw:v1.8.0" ;;
      busybox) expected="busybox:1.36" ;;
      ollama) expected="ollama/ollama:0.40.10" ;;
      chroma) expected="chromadb/chroma:1.5.9" ;;
    esac
    digest="${ref##*@}"
    [ "${ref%@*}" = "$expected" ] && [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
      || die "the authenticated image manifest has an invalid $component digest reference. Nothing was installed or run."
    if [[ " backend frontend mcp " == *" $component "* ]]; then
      gh attestation verify "oci://$ref" \
        --repo "$ATTESTATION_REPOSITORY" \
        --signer-workflow "$IMAGE_SIGNER" \
        --source-ref refs/heads/main \
        --source-digest "$source_sha" >/dev/null \
        || die "the $component digest in the $REF manifest does not have trusted CI provenance for $source_sha. Nothing was installed or run."
    fi
    case "$component" in
      backend) BACKEND_IMAGE_REF="$ref" ;;
      frontend) FRONTEND_IMAGE_REF="$ref" ;;
      mcp) MCP_IMAGE_REF="$ref" ;;
      postgres) POSTGRES_IMAGE_REF="$ref" ;;
      valkey) VALKEY_IMAGE_REF="$ref" ;;
      mongo) MONGO_IMAGE_REF="$ref" ;;
      versitygw) VERSITYGW_IMAGE_REF="$ref" ;;
      busybox) BUSYBOX_IMAGE_REF="$ref" ;;
      ollama) OLLAMA_IMAGE_REF="$ref" ;;
      chroma) CHROMA_IMAGE_REF="$ref" ;;
    esac
  done
  say "Authenticated the $REF manifest and all ten release image digests"
}

write_release_image_override() {  # write_release_image_override <path>
  local destination="$1" prefix="${TESTLOOKUP_IMAGE:-}"
  if [ -n "$prefix" ]; then
    prefix="${prefix%/}"
    [[ "$prefix" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]*$ ]] \
      && [[ "$prefix" != *"//"* && "$prefix" != *"://"* && "$prefix" != *"@"* ]] \
      || die "TESTLOOKUP_IMAGE must be a registry/namespace without whitespace, a tag, or a digest. Nothing was installed or run."
    BACKEND_IMAGE_REF="$prefix/backend@${BACKEND_IMAGE_REF##*@}"
    FRONTEND_IMAGE_REF="$prefix/frontend@${FRONTEND_IMAGE_REF##*@}"
    MCP_IMAGE_REF="$prefix/mcp@${MCP_IMAGE_REF##*@}"
  fi

  cat > "$destination" <<EOF
# Generated by install.sh from the authenticated $REF release manifest.
# Keep this override in every Compose command so all release images stay pinned.
services:
  postgres:
    image: $POSTGRES_IMAGE_REF
  mongo:
    image: $MONGO_IMAGE_REF
  redis:
    image: $VALKEY_IMAGE_REF
  minio:
    image: $VERSITYGW_IMAGE_REF
  ollama:
    image: $OLLAMA_IMAGE_REF
  chromadb:
    image: $CHROMA_IMAGE_REF
  db-budget-check:
    image: $BACKEND_IMAGE_REF
  minio-setup:
    image: $BACKEND_IMAGE_REF
  db-migrate:
    image: $BACKEND_IMAGE_REF
  backend:
    image: $BACKEND_IMAGE_REF
  worker:
    image: $BACKEND_IMAGE_REF
  worker-children:
    image: $BACKEND_IMAGE_REF
  beat:
    image: $BACKEND_IMAGE_REF
  seed-init:
    image: $BACKEND_IMAGE_REF
  frontend:
    image: $FRONTEND_IMAGE_REF
  mcp:
    image: $MCP_IMAGE_REF
EOF
}

main() {
  # ── Preflight ──────────────────────────────────────────────────────────────
  command -v docker >/dev/null 2>&1 || die "docker is required but not found. Install Docker Desktop / Engine first: https://docs.docker.com/get-docker/"
  if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
  else
    die "Docker Compose v2 is required (got neither 'docker compose' nor 'docker-compose')."
  fi

  DL=""
  if command -v curl >/dev/null 2>&1; then DL="curl -fsSL -o"; fi
  if [ -z "$DL" ] && command -v wget >/dev/null 2>&1; then DL="wget -qO"; fi
  [ -n "$DL" ] || die "need curl or wget to download files."

  # ── Which release ──────────────────────────────────────────────────────────
  # A release tag in TESTLOOKUP_VERSION alone also selects that release's
  # files, and a release tag in TL_REF alone also selects its images, so the
  # Compose file and the images it starts come from the same release.
  #
  # With neither set, install the latest published release, not the tip of
  # main: a release's files and exact image digests are authenticated, while
  # main changes under you and `latest` is whatever was
  # pushed last (security review 2026-11-20, TL-06). When the lookup fails the
  # install stops and says how to name a release; it never falls back to main
  # by itself. An image tag that is not a release (TESTLOOKUP_VERSION=<sha>)
  # keeps the files of main, as before, and says they are not verified.
  if [ -z "${TL_REF:-}" ] && is_release_tag "${TESTLOOKUP_VERSION:-}"; then
    REF="$TESTLOOKUP_VERSION"
  elif [ -z "${TL_REF:-}" ] && [ -z "${TESTLOOKUP_VERSION:-}" ]; then
    REF="$(latest_release_tag)" || die "could not find the latest TestLookup release ($LATEST_RELEASE_URL). Nothing was installed or run. Name a release yourself, for example TL_REF=v0.1.0 (the list is at https://github.com/test-intelligence/testlookup/releases), or set TL_REF=main to install the unreleased tip, which is not checked against any checksum."
    say "Latest release: $REF"
  elif [ -z "${TL_REF:-}" ]; then
    REF="main"
  fi
  if [ -n "${TESTLOOKUP_VERSION:-}" ]; then
    IMAGE_VERSION="$TESTLOOKUP_VERSION"
  elif is_release_tag "$REF"; then
    IMAGE_VERSION="$REF"
  else
    IMAGE_VERSION="latest"
  fi
  if is_release_tag "$REF"; then
    [ -z "${TESTLOOKUP_VERSION:-}" ] || [ "$TESTLOOKUP_VERSION" = "$REF" ] \
      || die "TESTLOOKUP_VERSION ($TESTLOOKUP_VERSION) must match the release files in TL_REF ($REF). Nothing was installed or run."
    command -v gh >/dev/null 2>&1 \
      || die "GitHub CLI (gh) is required to authenticate a published release. Install it from https://cli.github.com/ and run this command again. Nothing was installed or run."
  fi

  # ── Download the release artifacts ─────────────────────────────────────────
  say "Installing TestLookup into ./$DIR (ref: $REF, images: $IMAGE_VERSION)"
  mkdir -p "$DIR"
  cd "$DIR"

  # Download to a private staging directory and check there: an existing
  # install is not touched, and nothing downloaded runs, until the check passes.
  STAGE="$(mktemp -d)" || die "could not create a temporary directory"
  trap 'rm -rf "$STAGE"' EXIT
  local path
  for path in "${FILES[@]}"; do
    fetch "$path" "$STAGE/$path"
  done
  if is_release_tag "$REF"; then
    verify_release_files "$STAGE"
    verify_release_provenance "$STAGE"
    write_release_image_override "$STAGE/$IMAGE_OVERRIDE_FILE"
  else
    warn "no checksum is available for an untagged ref ($REF): the downloaded files are not verified. Install a release with TL_REF=vX.Y.Z to check them against its SHA256SUMS."
  fi
  for path in "${FILES[@]}"; do
    mkdir -p "$(dirname "$path")"
    cp "$STAGE/$path" "$path" || die "could not write $path"
  done
  IMAGE_OVERRIDE=""
  if is_release_tag "$REF"; then
    cp "$STAGE/$IMAGE_OVERRIDE_FILE" "$IMAGE_OVERRIDE_FILE" \
      || die "could not write $IMAGE_OVERRIDE_FILE"
    IMAGE_OVERRIDE="$IMAGE_OVERRIDE_FILE"
  fi
  chmod +x scripts/gen-dev-env.sh 2>/dev/null || true

  # ── Generate .env (random local secrets) ───────────────────────────────────
  if [ -f .env ]; then
    say "Reusing existing .env"
  else
    say "Generating .env with random secrets"
    bash scripts/gen-dev-env.sh --release || die "could not generate .env"
  fi
  # Secrets only for this user, also for an .env reused from an older install.
  # A failed chmod stops the install: .env holds every password and signing key.
  chmod 600 .env || die "could not make .env readable by you only (chmod 600 failed). It holds every generated password: fix its permissions, or move the install to a filesystem that keeps them, and run this again."

  # ── Bring the stack up ─────────────────────────────────────────────────────
  PROFILE_ARGS=""
  [ -n "${TL_PROFILE:-}" ] && PROFILE_ARGS="--profile ${TL_PROFILE}"
  COMPOSE_ARGS="-f $COMPOSE_FILE"
  [ -n "$IMAGE_OVERRIDE" ] && COMPOSE_ARGS="$COMPOSE_ARGS -f $IMAGE_OVERRIDE"

  if [ "${TL_NO_UP:-0}" = "1" ]; then
    say "TL_NO_UP set — skipping 'up'. Start it yourself with:"
    if [ -n "$IMAGE_OVERRIDE" ]; then
      echo "    cd $DIR && $DC $COMPOSE_ARGS $PROFILE_ARGS up -d"
    else
      echo "    cd $DIR && TESTLOOKUP_VERSION=$IMAGE_VERSION $DC $COMPOSE_ARGS $PROFILE_ARGS up -d"
    fi
    exit 0
  fi

  # The first admin's password lives in .env (INITIAL_ADMIN_PASSWORD). Say
  # where, never the value: a terminal's scrollback, a CI log or a shared
  # screen keeps what is printed (OSS review 2026-11-12, M-14). An .env reused
  # from an earlier install keeps its admin.
  if grep -q '^INITIAL_ADMIN_PASSWORD=.' .env; then
    PASSWORD_HINT="in .env (readable by you only): grep '^INITIAL_ADMIN_PASSWORD=' .env"
  else
    PASSWORD_HINT="not set (.env has no INITIAL_ADMIN_PASSWORD), so no admin was created. Create one, with a password shown once in your terminal: $DC $COMPOSE_ARGS exec backend python /app/scripts/create_admin.py"
  fi

  say "Pulling images and starting the stack (first boot pulls a few GB)…"
  # shellcheck disable=SC2086
  if [ -n "$IMAGE_OVERRIDE" ]; then
    $DC $COMPOSE_ARGS $PROFILE_ARGS up -d
  else
    TESTLOOKUP_VERSION="$IMAGE_VERSION" $DC $COMPOSE_ARGS $PROFILE_ARGS up -d
  fi

  cat <<EOF

TestLookup is starting. Give it a minute for migrations + health checks, then:

  Dashboard : http://localhost:3000
  User docs : http://localhost:3000/docs
  API ref   : http://localhost:3000/api-docs

First sign-in (created on a new install only; you will be asked to change it):
  Username  : admin
  Password  : ${PASSWORD_HINT}

After that first sign-in, delete the INITIAL_ADMIN_PASSWORD line from
./$DIR/.env: it is only used to create the admin on a new database, and
leaving it there keeps an unused password in a file several services read.

LOCAL HTTP ONLY
  Port 3000 is not encrypted, but it is bound to 127.0.0.1 by default so
  machines on the network cannot connect to it directly.
    This machine only : open http://localhost:3000 as shown above
    Other people      : keep the loopback binding and put a TLS-terminating
                        reverse proxy on this host in front of port 3000; see
                        "Before other people use it" in docs/operations/deploy-compose.md

Useful commands (run from ./$DIR):
  $DC $COMPOSE_ARGS ps           # service status
  $DC $COMPOSE_ARGS logs -f      # follow logs
  $DC $COMPOSE_ARGS down         # stop (keeps data)

EOF
  if [ "${TL_PROFILE:-}" = "demo" ]; then
    echo "Demo projects load in the background once the database is ready:"
    echo "  $DC $COMPOSE_ARGS logs -f seed-init"
  else
    echo "Want demo data? Re-run the authenticated release installer with TL_PROFILE=demo."
  fi
}

main "$@"
