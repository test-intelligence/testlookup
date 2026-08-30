"""A first-run instruction that cannot work is worse than no instruction.

Two classes, both found by walking the shipped runbooks against the code.

**A required env var missing from a committed `.env` template.**
``deploymentsteps.md`` and ``installation.md`` both tell a new self-hoster to
``cp .env.gcp-vm.example .env`` and then ``docker compose up``. That template
defined 32 keys and omitted ``MONGO_PASSWORD`` and ``FLOWER_PASSWORD``, both of
which ``docker-compose.yml`` declares as ``${VAR:?...}``. ``:?`` fires at
**config-parse** time, so Compose aborted before starting a single container:

    error while interpolating services.mongo... MONGO_PASSWORD is required

Nothing after that step in either runbook was reachable. ``.env.example`` had
all five required keys, which is exactly why this went unnoticed -- the common
path worked and only the GCP path was dead.

**The Swagger URL that 404s.** ``main.py`` sets ``docs_url="/api-docs"``, but
``make dev``, ``make dev-lite``, ``make demo``, both local-setup scripts and the
seeder all printed ``http://localhost:8000/docs`` *on success*, handing the user
a 404 as their reward. Six tracked docs repeated it while
``GETTING_STARTED.md`` and ``CONTRIBUTING.md`` had it right -- the docs
contradicted each other, so reading them could not settle it.

Both are checked against the code rather than against a hardcoded list, so a
new required var or a changed ``docs_url`` is caught rather than drifting.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"

#: ``${VAR:?message}`` -- Compose treats these as mandatory and aborts without them.
_REQUIRED_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):\?")


def _required_env_vars() -> set[str]:
    return set(_REQUIRED_RE.findall(COMPOSE.read_text(encoding="utf-8")))


def _committed_env_templates() -> list[Path]:
    """Every ``.env*.example`` a user is told to copy."""
    return sorted(REPO_ROOT.glob(".env*.example"))


def test_the_scan_actually_finds_something():
    """A guard that silently matched nothing would pass forever."""
    required = _required_env_vars()
    assert len(required) >= 3, f"expected several mandatory vars, found {required}"
    assert "MONGO_PASSWORD" in required, (
        "the var whose absence killed the GCP runbook is no longer mandatory -- "
        "confirm that is deliberate before relaxing this test"
    )
    assert len(_committed_env_templates()) >= 2, "no .env templates found to check"


@pytest.mark.parametrize(
    "template", _committed_env_templates(), ids=lambda p: p.name
)
def test_env_template_defines_every_var_compose_demands(template: Path):
    """``cp <template> .env && docker compose up`` must reach a running stack."""
    defined = {
        line.split("=", 1)[0].strip()
        for line in template.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    missing = sorted(_required_env_vars() - defined)
    assert not missing, (
        f"{template.name} omits {missing}, which docker-compose.yml marks "
        "mandatory with `${VAR:?}`. Compose aborts at config-parse time, so "
        "every documented step after the `cp` is unreachable."
    )


# ── The API-docs URL the tooling prints must be the one the app serves ──────

def _served_docs_path() -> str:
    src = (REPO_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'docs_url\s*=\s*["\']([^"\']+)["\']', src)
    assert match, "could not find docs_url in main.py"
    return match.group(1)


def _files_that_advertise_the_api_docs() -> list[Path]:
    candidates = [
        REPO_ROOT / "Makefile",
        REPO_ROOT / "README.md",
        REPO_ROOT / "README_FULL.md",
        REPO_ROOT / "GETTING_STARTED.md",
        REPO_ROOT / "CONTRIBUTING.md",
        REPO_ROOT / "installation.md",
        REPO_ROOT / "deploymentsteps.md",
        REPO_ROOT / "scripts" / "seed_dev_data.py",
        REPO_ROOT / "scripts" / "local_dev_setup_common.sh",
        REPO_ROOT / "scripts" / "local_dev_setup_windows.ps1",
        REPO_ROOT / "backend" / "scripts" / "seed_dev_data.py",
        REPO_ROOT / "user-guide" / "cli-sdk-mcp.md",
    ]
    return [p for p in candidates if p.is_file()]


def test_the_docs_url_scan_reads_real_files():
    assert _served_docs_path().startswith("/")
    found = _files_that_advertise_the_api_docs()
    assert len(found) >= 8, f"expected to scan the runbooks, saw {len(found)}"


def test_nothing_advertises_an_api_docs_url_the_app_does_not_serve():
    """``make dev`` printed a 404 as its success message."""
    served = _served_docs_path()
    offenders: list[str] = []
    pattern = re.compile(r"localhost:8000(/[A-Za-z0-9\-_/]*)")

    for path in _files_that_advertise_the_api_docs():
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for advertised in pattern.findall(line):
                # Only judge paths that are clearly the interactive API docs.
                if advertised.rstrip("/") in ("/docs", "/api-docs", "/swagger", "/redoc"):
                    if advertised.rstrip("/") != served.rstrip("/"):
                        rel = path.relative_to(REPO_ROOT).as_posix()
                        offenders.append(f"{rel}:{i} advertises {advertised}")

    assert not offenders, (
        f"the app serves its API docs at {served}, but these hand the user a "
        "404 -- several of them as a success message:\n  " + "\n  ".join(offenders)
    )
