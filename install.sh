#!/usr/bin/env bash
# install.sh — one-command remote install for TestLookup (no git clone needed).
#
#   curl -fsSL https://raw.githubusercontent.com/anandtopu/testlookup/main/install.sh | bash
#
# It downloads the pinned-image release stack, generates a local .env with
# random secrets, and brings the stack up with `docker compose`. Nothing is
# built from source — it pulls pre-built images from GHCR.
#
# Environment overrides:
#   TL_DIR=<path>            install directory          (default ./testlookup)
#   TL_REF=<branch|sha|tag>  raw-file ref to download   (default main)
#   TESTLOOKUP_VERSION=<tag> image tag to run           (default latest)
#   TL_PROFILE=demo          add a compose profile       (e.g. demo / local-llm)
#   TL_NO_UP=1               download + gen .env only, don't `up`
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/anandtopu/testlookup"
REF="${TL_REF:-main}"
DIR="${TL_DIR:-testlookup}"
COMPOSE_FILE="docker-compose.release.yml"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
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

fetch() {  # fetch <remote-path> <local-path>
  local url="${REPO_RAW}/${REF}/$1"
  mkdir -p "$(dirname "$2")"
  $DL "$2" "$url" || die "failed to download $url"
}

# ── Download the release artifacts ───────────────────────────────────────────
say "Installing TestLookup into ./$DIR (ref: $REF)"
mkdir -p "$DIR"
cd "$DIR"

fetch "$COMPOSE_FILE"          "$COMPOSE_FILE"
fetch ".env.example"           ".env.example"
fetch "scripts/gen-dev-env.sh" "scripts/gen-dev-env.sh"
fetch "scripts/init-db.sql"    "scripts/init-db.sql"
chmod +x scripts/gen-dev-env.sh 2>/dev/null || true

# ── Generate .env (random local secrets) ─────────────────────────────────────
if [ -f .env ]; then
  say "Reusing existing .env"
else
  say "Generating .env with random local secrets"
  bash scripts/gen-dev-env.sh || die "could not generate .env"
fi

# ── Bring the stack up ───────────────────────────────────────────────────────
PROFILE_ARGS=""
[ -n "${TL_PROFILE:-}" ] && PROFILE_ARGS="--profile ${TL_PROFILE}"

if [ "${TL_NO_UP:-0}" = "1" ]; then
  say "TL_NO_UP set — skipping 'up'. Start it yourself with:"
  echo "    cd $DIR && $DC -f $COMPOSE_FILE $PROFILE_ARGS up -d"
  exit 0
fi

say "Pulling images and starting the stack (first boot pulls a few GB)…"
# shellcheck disable=SC2086
TESTLOOKUP_VERSION="${TESTLOOKUP_VERSION:-latest}" $DC -f "$COMPOSE_FILE" $PROFILE_ARGS up -d

cat <<EOF

TestLookup is starting. Give it a minute for migrations + health checks, then:

  Dashboard : http://localhost:3000
  API docs  : http://localhost:8000/docs

Useful commands (run from ./$DIR):
  $DC -f $COMPOSE_FILE ps           # service status
  $DC -f $COMPOSE_FILE logs -f      # follow logs
  $DC -f $COMPOSE_FILE down         # stop (keeps data)

Want demo data? Re-run with: TL_PROFILE=demo curl ... | bash
EOF
