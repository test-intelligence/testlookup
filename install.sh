#!/usr/bin/env bash
# install.sh — one-command remote install for TestLookup (no git clone needed).
#
#   curl -fsSL https://raw.githubusercontent.com/test-intelligence/testlookup/main/install.sh | bash
#
# It downloads the pinned-image release stack, generates a local .env with
# random secrets, and brings the stack up with `docker compose`. Nothing is
# built from source — it pulls pre-built images from GHCR.
#
# Environment overrides:
#   TL_DIR=<path>            install directory          (default ./testlookup)
#   TL_REF=<branch|sha|tag>  raw-file ref to download   (default main)
#   TESTLOOKUP_VERSION=<tag> image tag to run           (default latest)
#                            A release tag (vX.Y.Z) in either one installs that
#                            release: the other follows it unless you set it
#                            too, and the downloaded files are checked against
#                            the release's SHA256SUMS before anything runs.
#   TL_PROFILE=demo          add a compose profile       (demo = demo projects, the
#                            documented default for an evaluation; or local-llm)
#   TL_NO_UP=1               download + gen .env only, don't `up`
#   TL_RAW_BASE=<url>        where the files come from (a mirror; default
#                            https://raw.githubusercontent.com/test-intelligence/testlookup)
#   TL_RELEASE_BASE=<url>    where a release's SHA256SUMS comes from (default
#                            https://github.com/test-intelligence/testlookup/releases/download)
#
# The checksum step protects the files this script downloads. It cannot check
# this script itself: for that, download install.sh and SHA256SUMS from the
# release page and compare them before running it.
#
# Everything runs from main(), called on the last line: a download cut short
# by the network defines functions but runs nothing.
set -euo pipefail

REPO_RAW="${TL_RAW_BASE:-https://raw.githubusercontent.com/test-intelligence/testlookup}"
RELEASE_DOWNLOADS="${TL_RELEASE_BASE:-https://github.com/test-intelligence/testlookup/releases/download}"
REF="${TL_REF:-main}"
DIR="${TL_DIR:-testlookup}"
COMPOSE_FILE="docker-compose.release.yml"
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

is_release_tag() {  # vX.Y.Z, the form the release workflow accepts
  [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
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
  $DL "$sums" "$url" || die "could not download the checksums for $REF ($url). Nothing was installed or run. Check that $REF is a published release with a SHA256SUMS file; to install the unreleased tip without a checksum, set TL_REF=main."
  for path in "${FILES[@]}"; do
    want="$(tr -d '\r' < "$sums" | awk -v p="$path" '$2 == p || $2 == ("*" p) { print $1; exit }')"
    [ -n "$want" ] || die "the checksums for $REF do not list $path. Nothing was installed or run."
    got="$(sha256_of "$stage/$path")" \
      || die "need sha256sum, shasum or openssl to check the download. Nothing was installed or run."
    [ "$got" = "$want" ] || die "checksum mismatch for $path from $REF (expected $want, got $got). The download is not the released file. Nothing was installed or run."
  done
  say "Checked ${#FILES[@]} downloaded files against the $REF SHA256SUMS"
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
  if [ -z "${TL_REF:-}" ] && is_release_tag "${TESTLOOKUP_VERSION:-}"; then
    REF="$TESTLOOKUP_VERSION"
  fi
  if [ -n "${TESTLOOKUP_VERSION:-}" ]; then
    IMAGE_VERSION="$TESTLOOKUP_VERSION"
  elif is_release_tag "$REF"; then
    IMAGE_VERSION="$REF"
  else
    IMAGE_VERSION="latest"
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
  else
    warn "no checksum is available for an untagged ref ($REF): the downloaded files are not verified. Install a release with TL_REF=vX.Y.Z to check them against its SHA256SUMS."
  fi
  for path in "${FILES[@]}"; do
    mkdir -p "$(dirname "$path")"
    cp "$STAGE/$path" "$path" || die "could not write $path"
  done
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

  if [ "${TL_NO_UP:-0}" = "1" ]; then
    say "TL_NO_UP set — skipping 'up'. Start it yourself with:"
    echo "    cd $DIR && TESTLOOKUP_VERSION=$IMAGE_VERSION $DC -f $COMPOSE_FILE $PROFILE_ARGS up -d"
    exit 0
  fi

  # The first admin's password lives in .env (INITIAL_ADMIN_PASSWORD). Say
  # where, never the value: a terminal's scrollback, a CI log or a shared
  # screen keeps what is printed (OSS review 2026-11-12, M-14). An .env reused
  # from an earlier install keeps its admin.
  if grep -q '^INITIAL_ADMIN_PASSWORD=.' .env; then
    PASSWORD_HINT="in .env (readable by you only): grep '^INITIAL_ADMIN_PASSWORD=' .env"
  else
    PASSWORD_HINT="generated at first start: $DC -f $COMPOSE_FILE logs db-migrate"
  fi

  say "Pulling images and starting the stack (first boot pulls a few GB)…"
  # shellcheck disable=SC2086
  TESTLOOKUP_VERSION="$IMAGE_VERSION" $DC -f "$COMPOSE_FILE" $PROFILE_ARGS up -d

  cat <<EOF

TestLookup is starting. Give it a minute for migrations + health checks, then:

  Dashboard : http://localhost:3000
  User docs : http://localhost:3000/docs
  API ref   : http://localhost:8000/api-docs

First sign-in (created on a new install only; you will be asked to change it):
  Username  : admin
  Password  : ${PASSWORD_HINT}

After that first sign-in, delete the INITIAL_ADMIN_PASSWORD line from
./$DIR/.env: it is only used to create the admin on a new database, and
leaving it there keeps an unused password in a file several services read.

WARNING: plain HTTP, reachable from your network
  Port 3000 is published on every network interface of this host and is not
  encrypted. Anyone on the same network can open it, and can read the
  passwords, session tokens and API keys sent to it.
    This machine only : add FRONTEND_PUBLISH_ADDR=127.0.0.1 to ./$DIR/.env, then
                        $DC -f $COMPOSE_FILE up -d
    Other people      : put a TLS-terminating reverse proxy in front of port
                        3000 and keep FRONTEND_PUBLISH_ADDR=127.0.0.1, see
                        "Before other people use it" in docs/operations/deploy-compose.md

Useful commands (run from ./$DIR):
  $DC -f $COMPOSE_FILE ps           # service status
  $DC -f $COMPOSE_FILE logs -f      # follow logs
  $DC -f $COMPOSE_FILE down         # stop (keeps data)

EOF
  if [ "${TL_PROFILE:-}" = "demo" ]; then
    echo "Demo projects load in the background once the database is ready:"
    echo "  $DC -f $COMPOSE_FILE logs -f seed-init"
  else
    echo "Want demo data? Re-run with: curl -fsSL <install.sh URL> | TL_PROFILE=demo bash"
  fi
}

main "$@"
