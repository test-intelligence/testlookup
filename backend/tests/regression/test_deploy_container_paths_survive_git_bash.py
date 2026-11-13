"""An in-pod path passed to kubectl must reach the pod unchanged under Git Bash.

Git Bash on Windows rewrites a native program's argument that starts with ``/``
into a Windows path. The K3s deploy's bucket step ran::

    kubectl ... exec <pod> -c backend -- python /app/scripts/s3_setup.py

and on every Windows deploy kubectl received
``C:/Program Files/Git/app/scripts/s3_setup.py``, so the step failed with "can't
open file" and the deploy warned "Bucket setup did not complete" (in the deploy
logs since at least October 2026). The OpenShift deploy had the same call.

``MSYS_NO_PATHCONV=1`` stops the rewrite, and other shells ignore it. The first
test refuses an exec into a pod that passes a ``/`` argument without it; the
second proves the mechanism on Windows.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = (
    REPO / "k3ssetup" / "deploy-k3s.sh",
    REPO / "openshiftsetup" / "deploy-openshift-artifactory.sh",
)
_PRINTS = re.compile(r"^(warn|echo|log|error|info|printf|[A-Z_]+_HINT=)\b")


def _commands(path: Path):
    """(line number, logical command) for each command, continuations joined."""
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        start, text = index + 1, lines[index]
        while text.endswith("\\") and index + 1 < len(lines):
            index += 1
            text = text[:-1] + " " + lines[index].strip()
        index += 1
        stripped = text.strip()
        if stripped and not stripped.startswith("#"):
            yield start, stripped


def _execs_with_a_container_path(path: Path):
    for number, command in _commands(path):
        body = re.sub(r"^(if|elif|then|else|while|until|!)\s+", "", command)
        if _PRINTS.match(body):
            continue  # a printed hint, not a command
        if not re.search(r'(\bkubectl\b|"\$KCLI"|\$KCLI)\s.*\bexec\b', body):
            continue
        _, _, after = body.partition(" -- ")
        if any(token.startswith("/") for token in after.split()):
            yield number, body


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_an_exec_into_a_pod_keeps_its_container_paths(script: Path):
    found = list(_execs_with_a_container_path(script))
    assert found, f"{script.name}: no exec with a container path found, so this check proves nothing"
    missing = [f"{script.name}:{number}" for number, body in found if "MSYS_NO_PATHCONV=1" not in body]
    assert not missing, (
        "Git Bash rewrites a /path argument into C:/Program Files/Git/...; prefix "
        "these with MSYS_NO_PATHCONV=1: " + ", ".join(missing)
    )


def _git_bash() -> str | None:
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if candidate and Path(candidate).exists() and "system32" not in candidate.lower():
            return candidate
    return None


@pytest.mark.skipif(os.name != "nt" or _git_bash() is None, reason="Git Bash on Windows only")
def test_git_bash_rewrites_the_path_unless_told_not_to():
    """The failure and the fix, observed: a native program sees what kubectl saw."""
    python = sys.executable.replace("\\", "/")
    probe = f'"{python}" -c "import sys; print(sys.argv[1])" /app/scripts/s3_setup.py'

    def seen(prefix: str) -> str:
        environment = {k: v for k, v in os.environ.items() if k not in ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL")}
        result = subprocess.run([_git_bash(), "-c", prefix + probe], capture_output=True, text=True,
                                env=environment, timeout=60, check=True)
        return result.stdout.strip()

    assert seen("") != "/app/scripts/s3_setup.py", "Git Bash no longer rewrites paths; the prefix is moot"
    assert seen("MSYS_NO_PATHCONV=1 ") == "/app/scripts/s3_setup.py"
