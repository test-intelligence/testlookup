"""Run pieces of the OpenShift deploy script against a recording stand-in for ``oc``.

Nothing here talks to a cluster. The script keeps its secret handling in one
marked block of functions; a test sources that block in bash with ``KCLI``
pointing at a stub that writes every argument list and every stdin it receives
to files, and answers ``get secret`` from a directory the test fills.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "openshiftsetup" / "deploy-openshift-artifactory.sh"
BLOCK_START = "# >>> secret helpers"
BLOCK_END = "# <<< secret helpers"

# The stand-in for ``oc``. FAKE_DIR is the scenario's directory:
#   argv.log       one line per call (the arguments, space-joined)
#   stdin.N        what call N received on stdin (create/apply -f -, exec -i)
#   patch.N.json   the file call N passed with --patch-file
#   secret/KEY     the base64 value ``get secret ... jsonpath={.data.KEY}`` answers with
#   has-mongo, has-mongo-pvc   their presence answers the two ``get`` probes
# FAKE_EXEC_REPLY is what an ``exec -i`` prints.
FAKE_OC = r"""#!/usr/bin/env bash
n=$(( $(cat "$FAKE_DIR/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAKE_DIR/count"
printf '%s\n' "$*" >> "$FAKE_DIR/argv.log"
case " $* " in
  *" get secret "*jsonpath=*)
    all="$*"; key="${all##*jsonpath=\{.data.}"; key="${key%%\}*}"
    [ -f "$FAKE_DIR/secret/$key" ] && cat "$FAKE_DIR/secret/$key"
    exit 0 ;;
  *" get secret "*) [ -d "$FAKE_DIR/secret" ]; exit $? ;;
  *" patch secret "*)
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--patch-file" ]; then
        cp "$a" "$FAKE_DIR/patch.$n.json"
        mkdir -p "$FAKE_DIR/secret"
        tr ',{}' '\n\n\n' < "$a" | sed -n 's/^"\([A-Za-z_.]*\)":"\(.*\)"$/\1 \2/p' \
          | while read -r k v; do printf '%s' "$v" > "$FAKE_DIR/secret/$k"; done
      fi
      prev="$a"
    done
    exit 0 ;;
  *" get deployment testlookup-mongo "*) [ -f "$FAKE_DIR/has-mongo" ]; exit $? ;;
  *" get pvc mongo-data "*) [ -f "$FAKE_DIR/has-mongo-pvc" ]; exit $? ;;
  *" exec -i "*) cat > "$FAKE_DIR/stdin.$n"; printf '%s\n' "${FAKE_EXEC_REPLY:-}"; exit 0 ;;
  *" -f - "*) cat > "$FAKE_DIR/stdin.$n"; exit 0 ;;
esac
exit 0
"""

PRELUDE = r"""set -euo pipefail
WORK="$1"
KCLI="$WORK/oc"
NAMESPACE=testlookup
CREDS_FILE="$WORK/openshiftsetup/.openshift-credentials"
log()   { echo "LOG: $1"; }
warn()  { echo "WARN: $1"; }
error() { echo "ERROR: $1" >&2; exit 1; }
# Generated values are obviously fake and differ per call.
openssl() { echo "stubvalue${RANDOM}x${RANDOM}x${RANDOM}"; }
scenario() { export FAKE_DIR="$WORK/$1"; mkdir -p "$FAKE_DIR"; CREDS_FILE="$FAKE_DIR/.openshift-credentials"; }
"""


def helper_block() -> str:
    """The script's secret-handling functions, cut out between the two markers."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert BLOCK_START in script and BLOCK_END in script, (
        f"{SCRIPT.name} has no '{BLOCK_START}' ... '{BLOCK_END}' block"
    )
    return script[script.index(BLOCK_START):script.index(BLOCK_END)]


def run_scenarios(work: Path, body: str, variables: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Source the helper block and run ``body`` (bash) in ONE process.
    ``variables`` are added to the environment of that process only."""
    bash = next((c for c in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash") or "")
                 if c and Path(c).exists()), None)
    if bash is None:
        pytest.skip("bash is not available")
    (work / "oc").write_bytes(FAKE_OC.encode())
    (work / "oc").chmod(0o755)
    runner = work / "run.sh"
    runner.write_bytes((PRELUDE + helper_block() + "\n" + body).encode())
    return subprocess.run([bash, runner.as_posix(), work.as_posix()], capture_output=True, text=True,
                          timeout=600, env={**bash_environment(bash), **(variables or {})})


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def credential(creds_file: Path, key: str) -> str:
    """The value the script saved for ``key`` in its credentials file ('' when absent)."""
    for line in read(creds_file).splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""
