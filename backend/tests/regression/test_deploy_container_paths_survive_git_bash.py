"""An in-pod path passed to kubectl must reach the pod unchanged under Git Bash.

Git Bash on Windows rewrites a native program's argument that starts with ``/``
into a Windows path. The K3s deploy's bucket step ran::

    kubectl ... exec <pod> -c backend -- python /app/scripts/s3_setup.py

and on every Windows deploy kubectl received
``C:/Program Files/Git/app/scripts/s3_setup.py``, so the step failed with "can't
open file" and the deploy warned "Bucket setup did not complete" (in the deploy
logs since at least October 2026). The OpenShift deploy had the same call.

The first fix prefixed the call with ``MSYS_NO_PATHCONV=1``. That keeps the
argument, but it also stops Git Bash converting environment variables, so a
``KUBECONFIG=/c/...`` reached kubectl unreadable and the next deploy failed with
``localhost:8080`` instead (2026-11-13). The command now goes inside one
``sh -c`` string, as the registry-mirror check already does: an argument that
does not start with ``/`` passes through unchanged, and KUBECONFIG is still
converted.

The static tests refuse a ``/`` argument after ``--`` in any exec into a pod,
including the re-run hints the scripts print (a user pastes those into the same
shell), and refuse ``MSYS_NO_PATHCONV``. The last test observes the mechanism
on Windows.
"""
from __future__ import annotations

import json
import os
import re
import shlex
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
IN_POD_SCRIPT = "/app/scripts/s3_setup.py"
_EXEC = re.compile(r'(?:\bkubectl\b|"?\$KCLI"?)\s[^#]*?\bexec\b')
_OPERATORS = {";", "&&", "||", "|", "&"}


def _commands(path: Path):
    """(line number, logical line) for each non-comment line, continuations joined."""
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


def _words_after_double_dash(line: str) -> list[str] | None:
    """The words an exec passes into the pod, as the shell would split them.

    A printed hint ends with the closing quote of its ``warn "..."`` or
    ``X_HINT="..."``; that quote is dropped and the rest is split the same way.
    """
    match = _EXEC.search(line)
    if not match:
        return None
    _, dashes, after = line[match.end():].partition(" -- ")
    if not dashes:
        return None
    for candidate in (after, after[:-1] if after.endswith('"') else None):
        if candidate is None:
            continue
        try:
            lexer = shlex.shlex(candidate, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            words = list(lexer)
        except ValueError:
            continue
        return _arguments(words)
    return after.split()


def _arguments(words: list[str]) -> list[str]:
    """Words up to the first shell operator, without redirections (``2>/dev/null`` is local)."""
    kept, skip_next = [], False
    for word in words:
        if word in _OPERATORS:
            break
        if skip_next:
            skip_next = False
        elif set(word) <= set("<>&"):
            skip_next = True  # the redirection's target stays on this machine
        else:
            kept.append(word)
    return kept


def _execs_into_a_pod(path: Path):
    for number, line in _commands(path):
        words = _words_after_double_dash(line)
        if words is not None:
            yield number, line, words


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_an_exec_into_a_pod_passes_no_slash_argument(script: Path):
    execs = list(_execs_into_a_pod(script))
    bucket_steps = [number for number, line, _ in execs if IN_POD_SCRIPT in line]
    assert len(bucket_steps) >= 2, (
        f"{script.name}: expected the bucket-setup exec and its printed re-run hint, found "
        f"{bucket_steps}; this check would prove nothing"
    )
    bad = [f"{script.name}:{number} {words}" for number, _, words in execs
           if any(word.startswith("/") for word in words)]
    assert not bad, (
        "Git Bash rewrites an argument that starts with / into C:/Program Files/Git/...; "
        "put the in-pod command inside one sh -c string: " + "; ".join(bad)
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_deploy_command_turns_off_git_bash_conversion(script: Path):
    found = [f"{script.name}:{number}" for number, line in _commands(script) if "MSYS_NO_PATHCONV" in line]
    assert not found, (
        "MSYS_NO_PATHCONV=1 also stops Git Bash converting KUBECONFIG=/c/..., so kubectl "
        "falls back to localhost:8080; use one sh -c string instead: " + ", ".join(found)
    )


def _git_bash() -> str | None:
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if candidate and Path(candidate).exists() and "system32" not in candidate.lower():
            return candidate
    return None


@pytest.mark.skipif(os.name != "nt" or _git_bash() is None, reason="Git Bash on Windows only")
def test_git_bash_keeps_a_sh_c_command_and_converts_kubeconfig(tmp_path: Path):
    """What a native kubectl receives from Git Bash, observed with a stand-in program."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'kubeconfig': os.environ.get('KUBECONFIG')}))\n",
        encoding="utf-8",
    )
    native = f'"{sys.executable.replace(chr(92), "/")}" "{probe.as_posix()}"'

    def seen(command: str) -> dict:
        environment = {k: v for k, v in os.environ.items()
                       if k not in ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL", "KUBECONFIG")}
        script = f"export KUBECONFIG=/c/Users/example/kubeconfig; {command}"
        result = subprocess.run([_git_bash(), "-c", script], capture_output=True, text=True,
                                env=environment, timeout=60, check=True)
        return json.loads(result.stdout.strip().splitlines()[-1])

    bare = seen(f"{native} python {IN_POD_SCRIPT}")
    assert bare["argv"][-1] != IN_POD_SCRIPT, "Git Bash no longer rewrites paths; this test is moot"

    fixed = seen(f'{native} sh -c "python {IN_POD_SCRIPT}"')
    assert fixed["argv"] == ["sh", "-c", f"python {IN_POD_SCRIPT}"]
    assert not fixed["kubeconfig"].startswith("/c/"), "KUBECONFIG must still be converted for kubectl"

    no_pathconv = seen(f"MSYS_NO_PATHCONV=1 {native} python {IN_POD_SCRIPT}")
    assert no_pathconv["argv"][-1] == IN_POD_SCRIPT
    assert no_pathconv["kubeconfig"] == "/c/Users/example/kubeconfig", (
        "MSYS_NO_PATHCONV=1 now converts KUBECONFIG; the reason for refusing it is gone"
    )
