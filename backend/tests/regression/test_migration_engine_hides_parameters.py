"""The migration engine keeps bound parameters out of its error text in production (re-audit H3).

``app/db/postgres.get_engine`` sets ``hide_parameters`` in production, and a
test holds every engine built under ``app/`` to it. ``migrations/env.py`` builds
its own engine outside ``app/``, and a failed migration statement is logged too.
"""
from __future__ import annotations

import ast
import pathlib

ENV = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "env.py"


def test_the_migration_engine_passes_hide_parameters():
    tree = ast.parse(ENV.read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "async_engine_from_config"
    ]
    assert len(calls) == 1, "migrations/env.py should build exactly one engine"
    keywords = {kw.arg: kw for kw in calls[0].keywords}
    assert "hide_parameters" in keywords, "the migration engine logs bound parameters"
    value = keywords["hide_parameters"].value
    assert not (isinstance(value, ast.Constant) and value.value is False)


def test_hide_parameters_follows_the_environment(monkeypatch):
    import importlib.util

    from app.core.config import settings

    source = ENV.read_text(encoding="utf-8")
    tree = ast.parse(source)
    [func] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_hide_parameters"]
    namespace: dict = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(ENV), "exec"), namespace)
    assert importlib.util.find_spec("app.core.config")

    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert namespace["_hide_parameters"]() is True
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert namespace["_hide_parameters"]() is False
