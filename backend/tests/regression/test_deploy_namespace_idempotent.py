"""Regression: a transient ``kubectl`` blip aborted the whole deploy.

Step 3 of ``deploy-homelab.sh`` was::

    if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
      log "Namespace $NAMESPACE already exists."
    else
      kubectl create namespace "$NAMESPACE"
      log "Namespace $NAMESPACE created."
    fi

``get`` exits non-zero for two very different reasons — the namespace does not
exist, *or* the API server could not be reached. The guard treats both as
"absent". On this cluster the control plane is intermittently interrupted
(Norton intercepts :6443, producing ``wsarecv: An existing connection was
forcibly closed``), so ``get`` failed spuriously, the else branch ran
``create``, and::

    Error from server (AlreadyExists): namespaces "testlookup" already exists

aborted the run under ``set -euo pipefail`` — **after** the images had been
built and pushed, which is the expensive part. Observed twice:
``deploy11.log`` and ``deploy14.log``, both ending at Step 3 with
``DEPLOY_EXIT=1``.

Fix: ``kubectl create namespace --dry-run=client -o yaml | kubectl apply -f -``.
Idempotent, so a spurious read failure is harmless — while a genuine API
outage still fails loudly, because ``apply`` itself errors. That distinction
is the point of these tests: it would be easy to "fix" this by swallowing
errors (``|| true``), which converts a noisy abort into a silent half-deploy.

The tests drive the extracted ``_ensure_namespace`` with a stubbed ``kubectl``
(a shell function, see ``_run``), so no cluster is involved.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "homelabsetup" / "deploy-homelab.sh"


def _find_bash() -> str | None:
    """Git-for-Windows bash, not the WSL launcher (see the tag-resolution test)."""
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    if found and "system32" in found.lower():
        return None
    return found


BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None or not DEPLOY_SCRIPT.is_file(),
    reason="needs a POSIX bash and homelabsetup/deploy-homelab.sh",
)


def _extract_function(name: str) -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^([ \t]*){re.escape(name)}\(\) \{{$", text, re.M)
    assert match, f"{name}() not found in {DEPLOY_SCRIPT.name} — was it renamed?"
    indent = match.group(1)
    lines = text[match.start():].splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line == f"{indent}}}":
            return "\n".join(lines[: index + 1])
    raise AssertionError(f"no closing brace found for {name}()")


def _run(tmp_path: Path, *, get_ok: bool, apply_ok: bool) -> subprocess.CompletedProcess:
    """Run ``_ensure_namespace`` against a stubbed kubectl.

    ``get_ok=False`` models the transient read failure that caused the aborts.
    ``apply_ok=False`` models a genuinely unreachable API server.

    The stub is a **shell function**, not a script on PATH. PATH shadowing was
    tried first and silently did not work: under Git-for-Windows bash an
    extensionless stub is not treated as executable, so ``command -v kubectl``
    resolved past it to the real binary — and the "passing" tests were quietly
    talking to the live cluster (``Error from server (NotFound)`` proved it).
    ``os.access(path, os.X_OK)`` returns True for any existing file on Windows,
    so it does not catch this. A shell function shadows the external command
    unconditionally and needs no PATH, no chmod, and no production change.
    """
    runner = tmp_path / "run.sh"
    runner.write_text(
        "set -euo pipefail\n"
        + textwrap.dedent(
            f"""\
            kubectl() {{
              case "$1" in
                get)
                  [ "{int(get_ok)}" = "1" ] || {{ echo "connection reset" >&2; return 1; }}
                  echo "namespace/testlookup"; return 0 ;;
                create)
                  echo "apiVersion: v1"; echo "kind: Namespace"; return 0 ;;
                apply)
                  cat >/dev/null
                  [ "{int(apply_ok)}" = "1" ] || {{ echo "Unable to connect to the server" >&2; return 1; }}
                  echo "namespace/testlookup configured"; return 0 ;;
              esac
              return 0
            }}
            """
        )
        + f"{textwrap.dedent(_extract_function('_ensure_namespace'))}\n"
        '_ensure_namespace "testlookup"\n'
        'echo "REACHED-END"\n',
        encoding="utf-8",
        newline="\n",
    )
    assert BASH is not None
    return subprocess.run(
        [BASH, str(runner)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=bash_environment(BASH),
        timeout=60,
    )


class TestATransientReadFailureIsHarmless:
    def test_survives_a_failing_get(self, tmp_path):
        """The exact deploy-aborting condition, twice observed."""
        result = _run(tmp_path, get_ok=False, apply_ok=True)
        assert result.returncode == 0, (
            "a transient kubectl read failure still aborts the deploy after the "
            f"images have been built and pushed. stderr:\n{result.stderr}"
        )
        assert "REACHED-END" in result.stdout

    def test_survives_a_working_get(self, tmp_path):
        """The ordinary path must keep working."""
        result = _run(tmp_path, get_ok=True, apply_ok=True)
        assert result.returncode == 0, result.stderr
        assert "REACHED-END" in result.stdout

    def test_never_calls_bare_create(self, tmp_path):
        """A bare ``create`` is what produced AlreadyExists."""
        src = _extract_function("_ensure_namespace")
        assert "--dry-run=client" in src and "apply -f -" in src, (
            "namespace creation is not idempotent"
        )


class TestARealOutageStillFails:
    """The fix must not become ``|| true`` — a silent half-deploy is worse
    than a loud abort."""

    def test_a_genuinely_unreachable_api_is_not_swallowed(self, tmp_path):
        result = _run(tmp_path, get_ok=False, apply_ok=False)
        assert result.returncode != 0, (
            "an unreachable API server was swallowed; the deploy would carry on "
            "and fail later in a more confusing place"
        )
        assert "REACHED-END" not in result.stdout

    def test_no_error_suppression_crept_in(self, tmp_path):
        src = _extract_function("_ensure_namespace")
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("#")
        )
        assert "|| true" not in code and "2>/dev/null" not in code, (
            "errors are being suppressed, which turns a loud failure into a "
            "silent half-deploy"
        )
