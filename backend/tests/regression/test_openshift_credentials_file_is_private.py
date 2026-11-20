"""The OpenShift deploy script's credentials file is ignored by git and owner-only.

``openshiftsetup/deploy-openshift-artifactory.sh`` saves the generated
PostgreSQL password and object-store keys to
``openshiftsetup/.openshift-credentials``, inside the checkout. Git did not
ignore that path although the script's log line said it did, so ``git add -A``
staged live credentials; and the file was created with the caller's umask,
usually readable by every local user. The K3s script did both correctly.

The rule: the path is ignored by a rule that names exactly that file, and the
script creates the file under ``umask 077`` (and sets mode 600 on one left by
an earlier run) before it writes a secret into it.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "openshiftsetup" / "deploy-openshift-artifactory.sh"
CREDENTIALS = "openshiftsetup/.openshift-credentials"
START = 'mkdir -p "$(dirname "$CREDS_FILE")"'


def _git(*arguments: str) -> subprocess.CompletedProcess:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True, timeout=120)


def _block() -> str:
    """The lines that create and fill the credentials file."""
    script = SCRIPT.read_text(encoding="utf-8")
    start = script.index(START)
    return script[start:script.index("\nCREDS\n", start) + len("\nCREDS\n")]


def test_the_ignore_file_names_exactly_the_credentials_file():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert CREDENTIALS in lines, f".gitignore has no line {CREDENTIALS!r}"


def test_git_ignores_the_credentials_file_by_that_rule():
    result = _git("check-ignore", "-v", "--no-index", CREDENTIALS)
    assert result.returncode == 0, f"git does not ignore {CREDENTIALS}"
    assert result.stdout.split("\t")[0].endswith(":" + CREDENTIALS), (
        f"ignored, but by a broader pattern than the one file: {result.stdout!r}"
    )


def test_the_rule_does_not_swallow_a_tracked_file():
    """Positive case: every file git tracks beside it is still visible to git."""
    tracked = _git("ls-files", "openshiftsetup", "k3ssetup").stdout.split()
    assert "openshiftsetup/artifactory.env.example" in tracked
    assert "openshiftsetup/deploy-openshift-artifactory.sh" in tracked
    result = _git("check-ignore", "-v", "--no-index", *tracked)
    swallowed = [line for line in result.stdout.splitlines() if line.split("\t")[0].endswith(":" + CREDENTIALS)]
    assert not swallowed, swallowed


def test_the_file_is_created_owner_only_before_a_secret_is_written():
    block = _block()
    create = block.index('( umask 077; : > "$CREDS_FILE" )')
    write = block.index('cat > "$CREDS_FILE"')
    assert create < write
    assert 'chmod 600 "$CREDS_FILE"' in block[create:write], "a file left by an earlier run keeps its old mode"


def test_the_k3s_script_still_does_the_same():
    k3s = (ROOT / "k3ssetup" / "deploy-k3s.sh").read_text(encoding="utf-8")
    assert '( umask 077; : > "$CREDS_FILE" )' in k3s


def test_running_the_block_leaves_an_owner_only_file(tmp_path):
    bash = next((c for c in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash") or "")
                 if c and Path(c).exists()), None)
    if bash is None:
        pytest.skip("bash is not available")
    target = tmp_path / "openshiftsetup" / ".openshift-credentials"
    target.parent.mkdir()
    target.write_text("left by an earlier run\n", encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o644)
    runner = tmp_path / "run.sh"
    runner.write_bytes((
        "set -euo pipefail\numask 022\n"
        'CREDS_FILE="$1"\nPG_PASSWORD=stub-pg\nMINIO_ACCESS=stub-access\nMINIO_SECRET=stub-object-store\n'
        + _block()
    ).encode())
    result = subprocess.run([bash, runner.as_posix(), target.as_posix()], capture_output=True, text=True,
                            timeout=300, env=bash_environment(bash))
    assert result.returncode == 0, result.stderr
    written = target.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=stub-pg" in written and "earlier run" not in written
    if os.name != "nt":  # NTFS has no POSIX permission bits to observe
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, f"mode is {oct(mode)}"


def test_the_log_line_only_claims_what_is_true():
    script = SCRIPT.read_text(encoding="utf-8")
    after = script[script.index("\nCREDS\n", script.index(START)):][:400]
    assert "owner-only" in after and "ignored by git" in after
