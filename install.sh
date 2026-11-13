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
#   TL_PROFILE=demo          add a compose profile       (demo = demo projects, the
#                            documented default for an evaluation; or local-llm)
#   TL_NO_UP=1               download + gen .env only, don't `up`
#
# Everything runs from main(), called on the last line: a download cut short
# by the network defines functions but runs nothing.
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/test-intelligence/testlookup"
REF="${TL_REF:-main}"
DIR="${TL_DIR:-testlookup}"
COMPOSE_FILE="docker-compose.release.yml"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

fetch() {  # fetch <remote-path> <local-path>
  local url="${REPO_RAW}/${REF}/$1"
  mkdir -p "$(dirname "$2")"
  $DL "$2" "$url" || die "failed to download $url"
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

  # ── Download the release artifacts ─────────────────────────────────────────
  say "Installing TestLookup into ./$DIR (ref: $REF)"
  mkdir -p "$DIR"
  cd "$DIR"

  fetch "$COMPOSE_FILE"          "$COMPOSE_FILE"
  fetch ".env.example"           ".env.example"
  fetch "scripts/gen-dev-env.sh" "scripts/gen-dev-env.sh"
  fetch "scripts/init-db.sql"    "scripts/init-db.sql"
  chmod +x scripts/gen-dev-env.sh 2>/dev/null || true

  # ── Generate .env (random local secrets) ───────────────────────────────────
  if [ -f .env ]; then
    say "Reusing existing .env"
  else
    say "Generating .env with random secrets"
    bash scripts/gen-dev-env.sh --release || die "could not generate .env"
  fi
  # Secrets only for this user, also for an .env reused from an older install.
  chmod 600 .env 2>/dev/null || true

  # ── Bring the stack up ─────────────────────────────────────────────────────
  PROFILE_ARGS=""
  [ -n "${TL_PROFILE:-}" ] && PROFILE_ARGS="--profile ${TL_PROFILE}"

  if [ "${TL_NO_UP:-0}" = "1" ]; then
    say "TL_NO_UP set — skipping 'up'. Start it yourself with:"
    echo "    cd $DIR && $DC -f $COMPOSE_FILE $PROFILE_ARGS up -d"
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
  TESTLOOKUP_VERSION="${TESTLOOKUP_VERSION:-latest}" $DC -f "$COMPOSE_FILE" $PROFILE_ARGS up -d

  cat <<EOF

TestLookup is starting. Give it a minute for migrations + health checks, then:

  Dashboard : http://localhost:3000
  User docs : http://localhost:3000/docs
  API ref   : http://localhost:8000/api-docs

First sign-in (created on a new install only; you will be asked to change it):
  Username  : admin
  Password  : ${PASSWORD_HINT}

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
