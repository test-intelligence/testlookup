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
from pathlib import Path

import pytest

# CI (Linux) has a working `bash` on PATH. On a Windows dev box `bash` may
# resolve to the WSL shim; set TL_TEST_BASH to a real bash (e.g. git-bash) to
# run these locally. Unset → plain "bash".
_BASH = os.environ.get("TL_TEST_BASH", "bash")

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "gen-dev-env.sh"
_EXAMPLE = _REPO / ".env.example"

# Secrets docker-compose.yml refuses to start without, plus the app/JWT secrets.
_REQUIRED = [
    "POSTGRES_PASSWORD", "MONGO_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
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
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
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
    res = subprocess.run(
        [_BASH, "scripts/gen-dev-env.sh"],
        cwd=workdir, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"generator failed (rc={res.returncode}): {res.stderr}"


def test_all_required_secrets_filled_no_placeholders(generated_env):
    bad = [k for k in _REQUIRED if k not in generated_env or _is_placeholder(generated_env[k])]
    assert not bad, f"missing/placeholder secrets: {bad}"


def test_connection_strings_synced(generated_env):
    assert generated_env["POSTGRES_PASSWORD"] in generated_env["DATABASE_URL"]
    assert generated_env["MONGO_PASSWORD"] in generated_env["MONGO_URI"]


def test_no_stray_carriage_returns_in_values(generated_env):
    assert not [k for k, v in generated_env.items() if "\r" in v]


def test_secrets_look_random(generated_env, tmp_path_factory):
    # A second, independent generation must differ (not a fixed/templated value).
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    other_dir = tmp_path_factory.mktemp("gen2")
    _run_generator(other_dir)
    other = _parse_env((other_dir / ".env").read_text(encoding="utf-8"))
    assert other["JWT_SECRET_KEY"] != generated_env["JWT_SECRET_KEY"]


def test_idempotent_when_env_exists(tmp_path: Path):
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    _run_generator(tmp_path)
    first = (tmp_path / ".env").read_text(encoding="utf-8")
    # Re-running must not mutate an existing .env.
    subprocess.run([_BASH, "scripts/gen-dev-env.sh"], cwd=tmp_path, check=True,
                   capture_output=True, timeout=60)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == first, "second run mutated existing .env"
