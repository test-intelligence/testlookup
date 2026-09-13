"""Test files must not replace core app modules with hand-rolled stubs.

34 test files carried a copy-pasted autouse fixture that did
``m.setitem(sys.modules, "app.core.deps", _make_stub(..., <fixed name list>))``
(and the same for ``app.core.security`` / ``app.db.postgres`` / ``app.db.mongo``
/ ``app.db.redis_client``). A stub built from a fixed name list drifts the
moment a router gains a dependency: run alone, the router is imported fresh
under the stub and dies with ``ImportError: cannot import name
'require_run_access' from 'app.core.deps' (unknown location)``. Inside the full
suite an earlier test has already imported the router with the real module,
so the stub is never consulted and CI stays green. 9 files (40 tests) failed
in isolation this way; earlier fixes only extended the name lists.

The real modules import cleanly with no env vars and no running services
(since #826), so the stubs bought nothing. This guard fails the moment one is
reintroduced, in any of the forms that were used.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

NEVER_STUB = frozenset({
    "app.core.deps",
    "app.core.security",
    "app.db.postgres",
    "app.db.mongo",
    "app.db.redis_client",
})


def _is_sys_modules(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def stubbed_modules(source: str) -> list[tuple[int, str]]:
    """(line, module) for every write of a NEVER_STUB module into sys.modules."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        # monkeypatch.setitem(sys.modules, "app.core.deps", stub)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setitem"
            and len(node.args) >= 2
            and _is_sys_modules(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in NEVER_STUB
        ):
            hits.append((node.lineno, node.args[1].value))
        # sys.modules["app.core.deps"] = stub
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and _is_sys_modules(target.value)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in NEVER_STUB
                ):
                    hits.append((node.lineno, target.slice.value))
    return sorted(hits)


def test_detector_catches_each_stub_form():
    source = (
        "import sys\n"
        "def f(m):\n"
        "    m.setitem(sys.modules, 'app.core.deps', object())\n"
        "    sys.modules['app.db.mongo'] = object()\n"
        "    m.setitem(sys.modules, 'app.services.stream_service', object())\n"
        "    m.setitem(sys.modules, 'bcrypt', object())\n"
    )
    assert stubbed_modules(source) == [(3, "app.core.deps"), (4, "app.db.mongo")]


@pytest.mark.parametrize("module", sorted(NEVER_STUB))
def test_real_module_imports_without_services(module):
    """The premise of the ban: the real module needs no stub to import."""
    assert importlib.import_module(module).__name__ == module


def test_no_test_file_stubs_a_core_app_module():
    offenders = [
        f"{path.relative_to(TESTS_DIR).as_posix()}:{line} stubs {module}"
        for path in sorted(TESTS_DIR.rglob("*.py"))
        if path.name != Path(__file__).name
        for line, module in stubbed_modules(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "Import the real module and monkeypatch the one attribute the test needs "
        "(monkeypatch.setattr('app.db.mongo.get_mongo_db', ...)). A stub with a "
        "fixed name list breaks the file when run alone:\n  " + "\n  ".join(offenders)
    )
