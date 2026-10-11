#!/usr/bin/env bash
set -euo pipefail

export PLATFORM_NAME="macOS"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "${SCRIPT_DIR}/local_dev_setup_common.sh" "$@"
