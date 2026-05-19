"""Tests for the lazy engine bootstrap in ``app.db.postgres``.

Pins the P2-7 contract: importing the module does NOT construct the
SQLAlchemy engine. The engine builds on first reference (via the lazy
hook or by calling ``get_engine()`` directly), and subsequent references
return the cached instance.

This matters for developer ergonomics — tests that stub the DB client
shouldn't have to prepend env-var blocks to every ``pytest`` invocation
just to satisfy import-time engine construction.
"""
from __future__ import annotations

import ast
import inspect

import pytest

pytest.importorskip("sqlalchemy")


def test_module_body_does_not_call_create_async_engine():
    """Static check: the module body (top-level statements only — NOT
    function bodies) must not call ``create_async_engine``. If a future
    contributor moves the engine build back to module top, this fails.

    Source-level inspection is more reliable than patching
    ``create_async_engine`` and reloading, which leaks the spy into the
    module's local binding."""
    import app.db.postgres as pg

    tree = ast.parse(inspect.getsource(pg))
    top_level_calls: list[str] = []

    for node in tree.body:
        # Module-level Assignment or Expr nodes that contain a Call
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Name) and func.id == "create_async_engine":
                    top_level_calls.append(ast.dump(sub))
                elif isinstance(func, ast.Attribute) and func.attr == "create_async_engine":
                    top_level_calls.append(ast.dump(sub))

    # Function definitions (``def get_engine(): return create_async_engine(...)``)
    # are top-level nodes but their bodies are NOT module-level execution.
    # Filter those out.
    actual_module_calls = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "create_async_engine":
                    actual_module_calls.append(ast.unparse(sub))

    assert not actual_module_calls, (
        "create_async_engine is called at module top level — engine "
        "construction must stay inside get_engine() so importing the "
        "module is side-effect free. Offending calls:\n  "
        + "\n  ".join(actual_module_calls)
    )


def test_get_engine_is_cached_so_repeated_calls_return_same_instance():
    """@lru_cache(maxsize=1) means the engine is built once and reused."""
    from app.db.postgres import get_engine

    engine_a = get_engine()
    engine_b = get_engine()
    assert engine_a is engine_b


def test_get_session_factory_uses_get_engine_and_is_cached():
    """get_session_factory() resolves the engine via get_engine() and
    caches the factory. Two calls return the same factory instance."""
    from app.db.postgres import get_engine, get_session_factory

    factory_a = get_session_factory()
    factory_b = get_session_factory()
    assert factory_a is factory_b
    # Factory binds the cached engine.
    assert factory_a.kw["bind"] is get_engine()


def test_module_level_engine_attribute_resolves_via_lazy_hook():
    """PEP 562 ``__getattr__`` preserves ``from app.db.postgres import engine``
    for backward compatibility. The accessor returns the same cached
    instance as ``get_engine()``."""
    import app.db.postgres as pg
    from app.db.postgres import get_engine

    assert pg.engine is get_engine()


def test_module_level_async_session_local_attribute_resolves_via_lazy_hook():
    """``from app.db.postgres import AsyncSessionLocal`` continues to
    work post-P2-7 — many service + agent files rely on it."""
    import app.db.postgres as pg
    from app.db.postgres import get_session_factory

    assert pg.AsyncSessionLocal is get_session_factory()


def test_unknown_attribute_raises_AttributeError():
    """The lazy hook only handles the two backward-compat names; anything
    else raises AttributeError so typos surface immediately."""
    import app.db.postgres as pg

    with pytest.raises(AttributeError, match="not_a_real_attribute"):
        pg.not_a_real_attribute  # noqa: B018


def test_session_factory_keeps_expire_on_commit_false():
    """``expire_on_commit=False`` is load-bearing for the single-owner
    commit rule (services return ORM objects after flush; callers commit
    later). Pin it so a future refactor doesn't quietly flip it."""
    from app.db.postgres import get_session_factory

    factory = get_session_factory()
    # async_sessionmaker stores its kwargs on .kw
    assert factory.kw["expire_on_commit"] is False
