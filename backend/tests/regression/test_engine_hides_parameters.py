"""A production engine must keep bound parameters out of its error text.

Re-audit H3, code review of the Q2 fix. Tracebacks are rendered to text and
redacted before they are logged, and a SQLAlchemy ``DBAPIError`` renders the
failing statement's bound parameters into its own text:
``[parameters: (1, '$2b$12$...')]``. Redaction recognises a secret by a marker
(``password=``, ``Bearer``, ``://user:pass@``); a bare positional value has
none, so a password hash, an API-key hash or a session-token hash in an INSERT
or UPDATE reached the log. With ``hide_parameters`` SQLAlchemy writes
``[SQL parameters hidden due to hide_parameters=True]`` instead.

Outside production the values stay visible: they are how a failing statement
gets debugged.

The real-PostgreSQL proof -- a failing INSERT, and the traceback the logging
pipeline writes for it -- is
``tests/integration/test_engine_hides_parameters_postgres.py``.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")

_APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"
_ENGINE_BUILDERS = frozenset({
    "create_engine",
    "create_async_engine",
    "engine_from_config",
    "async_engine_from_config",
})


@pytest.fixture
def engine_under(monkeypatch):
    """``get_engine()`` itself, built under a given APP_ENV, dropped after."""
    from app.core.config import settings
    from app.db import postgres

    def build(app_env: str):
        monkeypatch.setattr(settings, "APP_ENV", app_env)
        postgres.get_session_factory.cache_clear()
        postgres.get_engine.cache_clear()
        return postgres.get_engine()

    yield build
    postgres.get_session_factory.cache_clear()
    postgres.get_engine.cache_clear()


def test_a_production_engine_hides_bound_parameters(engine_under):
    assert engine_under("production").sync_engine.hide_parameters is True


@pytest.mark.parametrize("app_env", ["development", "staging"])
def test_other_environments_keep_them_for_debugging(engine_under, app_env):
    assert engine_under(app_env).sync_engine.hide_parameters is False


def test_every_engine_built_in_app_decides_on_hide_parameters():
    """The flag is set where the engine is built, so a second engine built
    anywhere in ``app/`` without it would put its parameters in the log again."""
    builders: list[str] = []
    offenders: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in _ENGINE_BUILDERS:
                continue
            where = f"{path.relative_to(_APP_ROOT).as_posix()}:{node.lineno}"
            builders.append(where)
            if not any(keyword.arg == "hide_parameters" for keyword in node.keywords):
                offenders.append(where)

    assert builders, "the scan found no engine builder at all; it is looking in the wrong place"
    assert not offenders, (
        "an engine is built without hide_parameters, so its bound parameters "
        f"reach the error text and the log: {offenders}"
    )
