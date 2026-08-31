"""Adoption regression — `scripts/gen-dev-env.sh` produces a working local .env.

Guards the one-command quickstart: the generator must fill every secret that
docker-compose marks required (`${VAR:?...}`) and keep DATABASE_URL / MONGO_URI
passwords in sync with POSTGRES_PASSWORD / MONGO_PASSWORD, so `make quickstart`
starts the stack on the first try with no manual editing.

The script does `cd "$(dirname "$0")/.."` and writes `.env` next to
`.env.example`, so we copy both into an isolated tmp tree (tmp/scripts/...) and
run it there — no side effects on the real repo .env.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment


def _resolve_bash() -> str | None:
    """Return a bash with the utilities required by the generator, or None.

    ``shutil.which("bash")`` was the old check, and it is not enough on
    Windows: it finds the WSL shim at ``System32/bash.exe``, which exists,
    passes the guard, and then dies with ``execvpe(/bin/bash) failed: No such
    file or directory`` when no distro is installed. Presence was verified;
    usability never was — so five tests *errored* locally where they were
    meant to skip. CI (Linux) has a real bash, so CI never showed it.

    Git for Windows ships a working bash even when WSL has no distro, so
    prefer finding one over skipping: a test that runs beats a test that
    politely opts out.
    """
    candidates: list[str] = []
    override = os.environ.get("TL_TEST_BASH")
    if override:
        candidates.append(override)  # explicit choice wins, working or not
    else:
        # Git for Windows ships both: bin/ is a small wrapper, usr/bin/ the
        # real shell. Either passes the probe; list both so a trimmed install
        # still resolves.
        candidates += [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ]
        found = shutil.which("bash")
        if found:
            candidates.append(found)
    for candidate in candidates:
        try:
            probe = subprocess.run(
                [candidate, "-c", (
                    "command -v python3 >/dev/null 2>&1 || "
                    "command -v python >/dev/null 2>&1 || "
                    "command -v python.exe >/dev/null 2>&1 || "
                    "command -v py.exe >/dev/null 2>&1"
                )],
                capture_output=True, timeout=30,
                env=bash_environment(candidate),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


# Resolved once: the probe spawns a process, and these tests spawn enough
# already. None => no usable bash on this machine => skip, don't error.
_BASH = _resolve_bash()


def _require_bash() -> None:
    if _BASH is None:
        pytest.skip(
            "no working bash found (a WSL shim with no distro does not count) — "
            "install Git for Windows or set TL_TEST_BASH to a real bash"
        )


_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "gen-dev-env.sh"
_EXAMPLE = _REPO / ".env.example"

# Secrets docker-compose.yml refuses to start without, plus the app/JWT secrets.
_REQUIRED = [
    "POSTGRES_PASSWORD", "MONGO_PASSWORD", "REDIS_PASSWORD",
    "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
    "APP_SECRET_KEY", "JWT_SECRET_KEY", "WEBHOOK_SECRET", "FLOWER_PASSWORD",
    "GF_SECURITY_ADMIN_PASSWORD",
]


def _is_placeholder(v: str) -> bool:
    lo = v.lower()
    return (not v) or "<" in v or "change-me" in lo or "set-a" in lo or "your-" in lo


def _parse_env(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            env[k] = v
    return env


@pytest.fixture
def generated_env(tmp_path: Path) -> dict[str, str]:
    _require_bash()
    _run_generator(tmp_path)
    out = tmp_path / ".env"
    assert out.exists(), "no .env produced"
    return _parse_env(out.read_text(encoding="utf-8"))


def _run_generator(workdir: Path) -> None:
    """Stage the script + example into ``workdir`` and run it there. The script
    is invoked with a RELATIVE path (cwd=workdir) so its ``dirname "$0"`` logic
    works regardless of OS path style."""
    (workdir / "scripts").mkdir(exist_ok=True)
    shutil.copy(_SCRIPT, workdir / "scripts" / "gen-dev-env.sh")
    shutil.copy(_EXAMPLE, workdir / ".env.example")
    env = bash_environment(_BASH)
    if os.name == "nt":
        # The test runner already has a known-good interpreter; pass its
        # Windows path explicitly because the host may expose stale Python
        # shims ahead of the active installation.
        env["TL_PYTHON_BIN"] = sys.executable.replace("\\", "/")
    res = subprocess.run(
        [_BASH, "scripts/gen-dev-env.sh"],
        cwd=workdir, capture_output=True, text=True, timeout=60,
        env=env,
    )
    assert res.returncode == 0, f"generator failed (rc={res.returncode}): {res.stderr}"


def test_all_required_secrets_filled_no_placeholders(generated_env):
    bad = [k for k in _REQUIRED if k not in generated_env or _is_placeholder(generated_env[k])]
    assert not bad, f"missing/placeholder secrets: {bad}"


def test_connection_strings_synced(generated_env):
    assert generated_env["POSTGRES_PASSWORD"] in generated_env["DATABASE_URL"]
    assert generated_env["MONGO_PASSWORD"] in generated_env["MONGO_URI"]
    # F2 — Redis is the Celery broker + JWT-revocation + authz cache and must
    # not be reachable unauthenticated. The generator must fill a real password
    # and keep REDIS_URL / CELERY_* in sync with it, same as Postgres/Mongo.
    assert generated_env["REDIS_PASSWORD"] in generated_env["REDIS_URL"]
    assert generated_env["REDIS_PASSWORD"] in generated_env["CELERY_BROKER_URL"]
    assert generated_env["REDIS_PASSWORD"] in generated_env["CELERY_RESULT_BACKEND"]


def test_generated_env_restores_dev_convenience(generated_env):
    """gen-dev-env.sh is the LOCAL/DEMO path (`make dev` / `make quickstart`), so
    it must restore the developer affordances that the secure .env.example
    defaults (F1) turn off: dev-login available on APP_ENV=development."""
    assert generated_env["APP_ENV"] == "development"
    assert generated_env["DEV_AUTO_LOGIN_ENABLED"] == "true"


def test_no_stray_carriage_returns_in_values(generated_env):
    assert not [k for k, v in generated_env.items() if "\r" in v]


def test_secrets_look_random(generated_env, tmp_path_factory):
    # A second, independent generation must differ (not a fixed/templated value).
    _require_bash()
    other_dir = tmp_path_factory.mktemp("gen2")
    _run_generator(other_dir)
    other = _parse_env((other_dir / ".env").read_text(encoding="utf-8"))
    assert other["JWT_SECRET_KEY"] != generated_env["JWT_SECRET_KEY"]


def test_idempotent_when_env_exists(tmp_path: Path):
    _require_bash()
    _run_generator(tmp_path)
    first = (tmp_path / ".env").read_text(encoding="utf-8")
    # Re-running must not mutate an existing .env.
    env = bash_environment(_BASH)
    if os.name == "nt":
        env["TL_PYTHON_BIN"] = sys.executable.replace("\\", "/")
    subprocess.run([_BASH, "scripts/gen-dev-env.sh"], cwd=tmp_path, check=True,
                   capture_output=True, timeout=60, env=env)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == first, "second run mutated existing .env"
