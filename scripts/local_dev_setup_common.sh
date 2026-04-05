#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLATFORM_NAME="${PLATFORM_NAME:-Unix}"

LITE_MODE=0
CLEAN_MODE=0
SKIP_MODEL_PULL=0
SKIP_VERIFY=0
DRY_RUN=0

usage() {
  cat <<EOF
TestLookup local developer bootstrap (${PLATFORM_NAME})

Usage:
  $(basename "$0") [options]

Options:
  --clean              Remove existing Docker volumes before starting.
  --lite               Start the lite stack instead of the full stack.
  --skip-model-pull    Do not pull Ollama models after startup.
  --skip-verify        Skip backend/frontend/login verification checks.
  --dry-run            Print commands without executing them.
  -h, --help           Show this help text.

Examples:
  ./$(basename "$0")
  ./$(basename "$0") --clean
  ./$(basename "$0") --lite --skip-model-pull
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN_MODE=1
      ;;
    --lite)
      LITE_MODE=1
      ;;
    --skip-model-pull)
      SKIP_MODEL_PULL=1
      ;;
    --skip-verify)
      SKIP_VERIFY=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

COMPOSE_ARGS=()
STACK_LABEL="full"
if [[ ${LITE_MODE} -eq 1 ]]; then
  COMPOSE_ARGS=(-f docker-compose.dev-lite.yml)
  STACK_LABEL="lite"
fi

run_cmd() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "[dry-run] $*"
    return 0
  fi
  echo "+ $*"
  "$@"
}

run_compose() {
  run_cmd docker compose "${COMPOSE_ARGS[@]}" "$@"
}

require_cmd() {
  local cmd="$1"
  local install_hint="$2"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Missing required command: ${cmd}" >&2
    echo "${install_hint}" >&2
    exit 1
  fi
}

warn_if_placeholder() {
  local env_file="$1"
  if grep -Eq '(<set-|<your-|change-me-)' "${env_file}"; then
    cat <<EOF
WARNING: ${env_file} still contains placeholder secrets.
Before sharing your environment or debugging auth/storage issues, update:
  - POSTGRES_PASSWORD
  - MINIO_SECRET_KEY
  - JWT_SECRET_KEY
  - WEBHOOK_SECRET
EOF
  fi
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"
  local sleep_seconds="${4:-5}"

  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "[dry-run] wait for ${label} at ${url}"
    return 0
  fi

  echo "Waiting for ${label} at ${url}..."
  local try=1
  while (( try <= attempts )); do
    if curl --fail --silent --show-error "${url}" >/dev/null 2>&1; then
      echo "${label} is ready."
      return 0
    fi
    sleep "${sleep_seconds}"
    ((try++))
  done

  echo "Timed out waiting for ${label} at ${url}" >&2
  return 1
}

verify_dev_login() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "[dry-run] verify POST http://localhost:8000/api/v1/auth/dev-login?role=admin"
    return 0
  fi

  local response
  response="$(curl --fail --silent --show-error -X POST 'http://localhost:8000/api/v1/auth/dev-login?role=admin')"
  if [[ "${response}" != *"access_token"* ]]; then
    echo "Dev login verification failed: unexpected response" >&2
    echo "${response}" >&2
    return 1
  fi
  echo "Development quick login is working."
}

main() {
  cd "${REPO_ROOT}"

  if [[ ${DRY_RUN} -eq 0 ]]; then
    require_cmd docker "Install Docker Desktop, Rancher Desktop, OrbStack, or another Docker-compatible runtime first."
    require_cmd curl "Install curl so the script can verify local service health."
    docker compose version >/dev/null
  else
    echo "[dry-run] require docker"
    echo "[dry-run] require curl"
    echo "[dry-run] docker compose version"
  fi

  if [[ ! -f .env ]]; then
    run_cmd cp .env.example .env
    echo "Created .env from .env.example"
  fi

  warn_if_placeholder ".env"

  if [[ ${CLEAN_MODE} -eq 1 ]]; then
    run_compose down -v --remove-orphans
  fi

  run_compose up -d --build

  if [[ ${SKIP_VERIFY} -eq 0 ]]; then
    wait_for_http "http://localhost:8000/health/live" "backend"
    wait_for_http "http://localhost:3000" "frontend"
    verify_dev_login
  fi

  if [[ ${LITE_MODE} -eq 0 && ${SKIP_MODEL_PULL} -eq 0 ]]; then
    run_compose exec ollama ollama pull qwen2.5:7b
    run_compose exec ollama ollama pull nomic-embed-text
  fi

  cat <<EOF

TestLookup local ${STACK_LABEL} developer stack is ready.

Useful URLs:
  - Dashboard:      http://localhost:3000
  - API Docs:       http://localhost:8000/docs
  - Health:         http://localhost:8000/health/details
  - Flower:         http://localhost:5555
  - MinIO Console:  http://localhost:9001
  - MCP SSE:        http://localhost:8002/sse

Helpful follow-up commands:
  - docker compose ps
  - docker compose logs seed-init --tail 200
  - docker compose logs -f backend worker
  - make list-llm

Login:
  - Open the dashboard and use the Quick Login buttons in development mode.
EOF
}

main "$@"
