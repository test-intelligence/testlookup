"""Every setting the backend reads is explained in the configuration reference.

``docs/operations/configuration.md`` is the operator-facing reference: what
each setting does, its default, when to change it, and whether it is a secret
or checked in production. ``docs/reference/configuration.md`` is generated from
source and lists only names, types and defaults; before the hand-written page
existed, 133 of the 256 ``Settings`` fields were documented nowhere else.

The failure this guards against is silent: a new ``Settings`` field, a new key
in ``.env.example`` or a new ``os.getenv`` read ships, and the reference no
longer covers it. Each test names the missing setting so the fix is obvious.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE = REPO_ROOT / "docs" / "operations" / "configuration.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
BACKEND_APP = REPO_ROOT / "backend" / "app"

#: ``os.getenv("NAME"``, ``os.environ.get("NAME"`` and ``os.environ["NAME"]``.
_GETENV_RE = re.compile(
    r"""os\.(?:getenv|environ\.get)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]"""
)

#: An uncommented ``KEY=value`` line in ``.env.example``.
_ENV_KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def _reference_text() -> str:
    assert REFERENCE.is_file(), f"missing {REFERENCE.relative_to(REPO_ROOT)}"
    return REFERENCE.read_text(encoding="utf-8")


def _documented(name: str, text: str) -> bool:
    """The name appears as a code span, e.g. ``LLM_MODEL``, in the reference."""
    return f"`{name}`" in text


def _undocumented(names: set[str], text: str) -> list[str]:
    return sorted(name for name in names if not _documented(name, text))


def test_every_settings_field_is_in_the_reference():
    fields = set(Settings.model_fields)
    # A Settings class that lost its fields would make this test vacuous.
    assert len(fields) > 200, f"expected the full Settings class, found {len(fields)} fields"
    missing = _undocumented(fields, _reference_text())
    assert not missing, (
        "docs/operations/configuration.md does not mention these Settings fields "
        "(backend/app/core/config.py). Add a row for each:\n  " + "\n  ".join(missing)
    )


def test_every_env_example_key_is_in_the_reference():
    keys = set(_ENV_KEY_RE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    assert len(keys) > 50, f"expected the .env.example keys, found {len(keys)}"
    missing = _undocumented(keys, _reference_text())
    assert not missing, (
        "docs/operations/configuration.md does not mention these .env.example keys. "
        "Document each one (say so when only Docker Compose reads it):\n  "
        + "\n  ".join(missing)
    )


def test_every_environment_read_outside_settings_is_in_the_reference():
    names: set[str] = set()
    for path in BACKEND_APP.rglob("*.py"):
        for match in _GETENV_RE.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            names.add(match.group(1) or match.group(2))
    # The scan must find the known direct reads, or the regex has rotted.
    assert {"MIGRATION_LOCK_WAIT_SECONDS", "PROMETHEUS_MULTIPROC_DIR", "SDK_PATH"} <= names, names
    missing = _undocumented(names, _reference_text())
    assert not missing, (
        "backend/app reads these environment variables directly (os.getenv / "
        "os.environ) but docs/operations/configuration.md does not mention them:\n  "
        + "\n  ".join(missing)
    )
