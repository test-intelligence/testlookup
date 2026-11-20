"""The install and backup scripts do not expose the secrets they handle.

Four gaps in the Compose install path:

* ``scripts/gen-dev-env.sh`` passed every generated secret to Python as a
  command-line argument, which any local user can read in the process list;
* it and ``install.sh`` ignored a failed ``chmod 600 .env``, leaving every
  password in a file other local users might read, with no message;
* ``scripts/ops/backup.sh`` wrote the backup (password hashes, encrypted
  integration secrets) with the caller's umask;
* nothing told the operator that the release frontend is plain HTTP on every
  interface, or that ``INITIAL_ADMIN_PASSWORD`` can be deleted from ``.env``
  after the first sign-in.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "scripts" / "gen-dev-env.sh"
_TIMEOUT_S = 300  # a hang guard, not a speed bound


def _bash() -> str | None:
    candidates = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    candidates.append(shutil.which("bash") or "")
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run([candidate, "-c", "exit 0"], capture_output=True, timeout=60,
                                   env=bash_environment(candidate))
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def _stage(tmp_path: Path) -> tuple[str, dict[str, str]]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    (tmp_path / "scripts").mkdir()
    shutil.copy(GENERATOR, tmp_path / "scripts" / "gen-dev-env.sh")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    (tmp_path / "stubs").mkdir()
    return bash, bash_environment(bash)


def _stub(tmp_path: Path, name: str, body: str) -> None:
    path = tmp_path / "stubs" / name
    path.write_bytes(body.encode())
    path.chmod(0o755)


# ── Secrets never appear on a command line ──────────────────────────────────


def test_no_generated_secret_is_passed_as_a_process_argument(tmp_path):
    bash, env = _stage(tmp_path)
    # Stands in for python: records its arguments, then runs the real one.
    _stub(tmp_path, "recording-python",
          '#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" >> "$ARGUMENT_LOG"\nexec "$REAL_PYTHON" "$@"\n')
    log = tmp_path / "arguments.log"
    env.update(
        TL_PYTHON_BIN=(tmp_path / "stubs" / "recording-python").as_posix(),
        REAL_PYTHON=sys.executable.replace("\\", "/"),
        ARGUMENT_LOG=log.as_posix(),
    )
    result = subprocess.run([bash, "scripts/gen-dev-env.sh", "--release"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=_TIMEOUT_S)
    assert result.returncode == 0, result.stderr

    values = {}
    for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name] = value
    secrets = {name: values[name] for name in (
        "POSTGRES_PASSWORD", "MONGO_PASSWORD", "REDIS_PASSWORD", "MINIO_SECRET_KEY", "FLOWER_PASSWORD",
        "APP_SECRET_KEY", "JWT_SECRET_KEY", "WEBHOOK_SECRET", "GF_SECURITY_ADMIN_PASSWORD",
        "INITIAL_ADMIN_PASSWORD",
    )}
    assert all(len(value) >= 16 for value in secrets.values()), "positive case: every secret was generated"
    arguments = log.read_text(encoding="utf-8")
    assert "REDIS_PASSWORD" in arguments, "the recorder saw nothing, so this check proves nothing"
    exposed = [name for name, value in secrets.items() if value in arguments]
    assert not exposed, f"these secrets were visible in the process list: {exposed}"
    # Positive case: the connection strings still carry the generated passwords.
    assert secrets["REDIS_PASSWORD"] in values["REDIS_URL"]
    assert secrets["POSTGRES_PASSWORD"] in values["DATABASE_URL"]


# ── A failed chmod is an error, not a silent pass ───────────────────────────


def test_the_generator_stops_when_it_cannot_restrict_the_env_file(tmp_path):
    bash, env = _stage(tmp_path)
    # A shell function, read through BASH_ENV, stands in for a chmod that fails
    # (a filesystem without permissions). Not a stub on PATH: Git Bash puts
    # its own /usr/bin first.
    _stub(tmp_path, "failing-chmod.sh", "chmod() { return 1; }\n")
    env["BASH_ENV"] = (tmp_path / "stubs" / "failing-chmod.sh").as_posix()
    env["TL_PYTHON_BIN"] = sys.executable.replace("\\", "/")
    result = subprocess.run([bash, "scripts/gen-dev-env.sh", "--release"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=_TIMEOUT_S)
    assert result.returncode != 0, "a failed chmod 600 was ignored"
    assert "chmod 600 failed" in result.stderr
    assert not (tmp_path / ".env").exists(), "secrets were left in a file that could not be restricted"


@pytest.mark.parametrize("relative", ["scripts/gen-dev-env.sh", "install.sh"])
def test_no_script_ignores_a_failed_chmod_600(relative):
    script = (ROOT / relative).read_text(encoding="utf-8")
    lines = [line.strip() for line in script.splitlines() if re.search(r"\bchmod 600\b", line) and not line.strip().startswith("#")]
    assert lines, f"{relative}: no chmod 600 left, so this check proves nothing"
    swallowed = [line for line in lines if "|| true" in line or "2>/dev/null" in line]
    assert not swallowed, f"{relative}: a failed chmod 600 is ignored: {swallowed}"


# ── The backup is owner-only ────────────────────────────────────────────────


def test_the_backup_is_written_under_a_private_umask():
    script = (ROOT / "scripts/ops/backup.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert re.search(r"^umask 077$", code, re.MULTILINE), "backup.sh does not set umask 077"
    assert code.index("umask 077") < code.index('mkdir -p "$BACKUP_DIR"'), (
        "the umask must be set before the backup directory and the dumps are created"
    )
    assert 'tar czf "$ARCHIVE"' in code, "positive case: the archive is still written"


# ── The operator is told ────────────────────────────────────────────────────


def _final_message(script: str) -> str:
    start = script.index("TestLookup is starting.")
    return script[start:script.index("\nEOF", start)]


def test_the_installer_warns_that_the_web_port_is_plain_http():
    message = _final_message((ROOT / "install.sh").read_text(encoding="utf-8"))
    assert "plain HTTP" in message
    assert "every network interface" in message
    assert "FRONTEND_PUBLISH_ADDR=127.0.0.1" in message
    assert "http://localhost:3000" in message, "positive case: the dashboard address is still printed"


def test_the_installer_says_to_delete_the_first_admin_password():
    message = _final_message((ROOT / "install.sh").read_text(encoding="utf-8"))
    assert re.search(r"delete the INITIAL_ADMIN_PASSWORD line", message)


def test_the_release_compose_header_carries_the_same_warning():
    compose = (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    header = compose[: compose.index("\nservices:")]
    assert "PLAIN HTTP" in header and "FRONTEND_PUBLISH_ADDR=127.0.0.1" in header
    # The default itself is unchanged: every interface unless the operator narrows it.
    # (Host port 3000; the container side is 8080 since nginx runs as a non-root user.)
    assert '"${FRONTEND_PUBLISH_ADDR:+${FRONTEND_PUBLISH_ADDR}:}3000:8080"' in compose


def test_the_compose_guide_closes_the_plain_port_in_the_tls_step():
    guide = (ROOT / "docs/operations/deploy-compose.md").read_text(encoding="utf-8")
    section = guide[guide.index("### Before other people use it"):]
    section = section[: section.index("\n### ", 5)]
    assert "FRONTEND_PUBLISH_ADDR=127.0.0.1" in section
    assert "INITIAL_ADMIN_PASSWORD" in section
    assert "REDIS_PASSWORD" in section
