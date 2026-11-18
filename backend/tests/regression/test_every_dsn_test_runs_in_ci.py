"""Every backend test that needs the Postgres DSN really runs in CI's Postgres job.

Tests that need ``TESTLOOKUP_POSTGRES_TEST_DSN`` skip everywhere except the
"Run protected PostgreSQL integration suite" step of ``.github/workflows/ci.yml``,
which passes pytest an explicit file list with ``-m integration``. A test
protects nothing unless all of this holds, so all of it is checked here:

* every test file under ``backend/tests`` that reads the DSN -- directly, or
  through a helper module under ``tests/`` that reads it -- is in that list
  (the older guard, ``test_postgres_integration_tests_are_in_ci.py``, sees only
  ``tests/integration/*_postgres*.py``: GAP-F02's two files outside that
  pattern were never run);
* every listed path exists;
* every test that needs the DSN carries the ``integration`` marker: by location
  under ``tests/integration/`` (``tests/conftest.py`` adds it), by a module
  ``pytestmark``, or on the test itself (``-m integration`` deselects an
  unmarked one silently);
* no listed file ``importorskip``s a package ``backend/requirements-lock.txt``
  does not ship: CI installs the lock, so such a file skips there for good
  (GAP-F01: ``fakeredis``).
"""

from __future__ import annotations

import ast
import re
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"
TESTS = BACKEND / "tests"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LOCK = BACKEND / "requirements-lock.txt"
DSN = "TESTLOOKUP_POSTGRES_TEST_DSN"
STEP = "- name: Run protected PostgreSQL integration suite"

#: Listed files that importorskip a package the lock does not ship. This list
#: may only shrink: a new entry fails the ratchet below, and an entry that no
#: longer offends fails the exact-match check, so fixing one means deleting it.
KNOWN_UNLOCKED_IMPORTORSKIP = {
    "tests/integration/test_chart_data_comparability_postgres.py": "GAP-F01, owner decision pending",
}
KNOWN_UNLOCKED_IMPORTORSKIP_MAX = 1

#: DSN test files that run in another named step of the same job instead of
#: the list (checked: the step must exist and its pytest line must name the file).
RUN_BY_ANOTHER_STEP = {
    "tests/integration/test_ingestion_fingerprint_boundary_postgres.py": "- name: Release matrix",
}


def _require_checkout() -> None:
    if not CI_WORKFLOW.exists():
        pytest.skip("ci.yml not present in this checkout")


def _rel(path: Path) -> str:
    return path.relative_to(BACKEND).as_posix()


@lru_cache(maxsize=None)
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _reads_dsn(node: ast.AST) -> bool:
    """A call with the DSN's name as an argument (``os.getenv``, ``os.environ.get``,
    ``_env``...), or ``os.environ[...]``. A docstring or another dict's key is not a read."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and any(
            isinstance(arg, ast.Constant) and arg.value == DSN
            for arg in [*sub.args, *(k.value for k in sub.keywords)]
        ):
            return True
        if (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.slice, ast.Constant)
            and sub.slice.value == DSN
            and (
                (isinstance(sub.value, ast.Attribute) and sub.value.attr == "environ")
                or (isinstance(sub.value, ast.Name) and sub.value.id == "environ")
            )
        ):
            return True
    return False


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(BACKEND).with_suffix("").parts)


@lru_cache(maxsize=1)
def _dsn_helpers() -> frozenset[str]:
    """Dotted names of non-test modules under tests/ that read the DSN."""
    return frozenset(
        _module_name(path)
        for path in TESTS.rglob("*.py")
        if not path.name.startswith("test_")
        and path.name != "conftest.py"
        and _reads_dsn(_tree(path))
    )


def _imported_helper_names(path: Path) -> set[str] | None:
    """Names a module imports from a DSN helper (``None``: it imports none)."""
    package = _module_name(path).rsplit(".", 1)[0]
    names: set[str] = set()
    found = False
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                base = (
                    package.rsplit(".", node.level - 1)[0]
                    if node.level > 1
                    else package
                )
                module = f"{base}.{module}" if module else base
            if module in _dsn_helpers():
                found = True
                names.update(alias.asname or alias.name for alias in node.names)
            else:
                for alias in node.names:
                    if f"{module}.{alias.name}" in _dsn_helpers():
                        found = True
                        names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _dsn_helpers():
                    found = True
                    names.add((alias.asname or alias.name).split(".")[0])
    return names if found else None


def _needs_dsn(path: Path) -> bool:
    return _reads_dsn(_tree(path)) or _imported_helper_names(path) is not None


@lru_cache(maxsize=1)
def _dsn_test_files() -> tuple[str, ...]:
    return tuple(
        sorted(_rel(path) for path in TESTS.rglob("test_*.py") if _needs_dsn(path))
    )


@lru_cache(maxsize=1)
def _listed() -> tuple[str, ...]:
    source = CI_WORKFLOW.read_text(encoding="utf-8")
    assert source.count(STEP) == 1, f"expected exactly one {STEP!r} step in ci.yml"
    step = source.split(STEP, 1)[1].split("- name:", 1)[0]
    run = step.split("run: >-", 1)[1]
    return tuple(re.findall(r"(?<![\w/])tests/[\w/.-]+\.py", run))


def _is_integration_mark(node: ast.AST) -> bool:
    """``pytest.mark.integration`` (bare or called)."""
    if isinstance(node, ast.Call):
        node = node.func
    return isinstance(node, ast.Attribute) and node.attr == "integration"


def _pytestmark_has_integration(body: list[ast.stmt]) -> bool:
    for stmt in body:
        targets = (
            stmt.targets
            if isinstance(stmt, ast.Assign)
            else [stmt.target]
            if isinstance(stmt, ast.AnnAssign)
            else []
        )
        if (
            any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets)
            and stmt.value is not None
        ):
            if any(_is_integration_mark(sub) for sub in ast.walk(stmt.value)):
                return True
    return False


def _functions(tree: ast.Module):
    """(function, enclosing class or None) for module- and class-level defs."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, None
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield sub, node


def _dsn_tests(path: Path) -> list[tuple[ast.AST, ast.ClassDef | None]]:
    """The test functions of a module that need the DSN: they read it, call a
    function that does, or take a fixture that does (to a fixed point)."""
    tree = _tree(path)
    functions = list(_functions(tree))
    dsn_names: set[str] = set(_imported_helper_names(path) or ())
    changed = True
    while changed:
        changed = False
        for func, _cls in functions:
            if func.name in dsn_names:
                continue
            calls = {
                sub.func.id
                for sub in ast.walk(func)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
            params = {
                a.arg
                for a in [
                    *func.args.posonlyargs,
                    *func.args.args,
                    *func.args.kwonlyargs,
                ]
            }
            used = {sub.id for sub in ast.walk(func) if isinstance(sub, ast.Name)}
            if (
                _reads_dsn(func)
                or calls & dsn_names
                or params & dsn_names
                or used & dsn_names
            ):
                dsn_names.add(func.name)
                changed = True
    return [
        (func, cls)
        for func, cls in functions
        if func.name.startswith("test") and func.name in dsn_names
    ]


def _lock_names() -> set[str]:
    names = set()
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==", line)
        if match:
            names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return names


def _is_importorskip(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "importorskip"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    )


def _env_fallback_guards(func: ast.AST, call: ast.Call) -> bool:
    """The family's fallback shape: the function reads an environment variable
    (``REDIS_URL``) and returns from an ``if`` before the importorskip, so CI,
    which sets the variable, never reaches it."""
    reads_env = any(
        isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr in ("get", "getenv")
        and sub.args
        and isinstance(sub.args[0], ast.Constant)
        and isinstance(sub.args[0].value, str)
        and sub.args[0].value.isupper()
        for sub in ast.walk(func)
    )
    early_return = any(
        isinstance(stmt, ast.If)
        and stmt.lineno < call.lineno
        and any(isinstance(sub, ast.Return) for sub in ast.walk(stmt))
        for stmt in getattr(func, "body", [])
    )
    return reads_env and early_return


def _importorskipped(path: Path) -> list[str]:
    """Modules a file importorskips where nothing lets CI avoid the skip."""
    tree = _tree(path)
    guarded: set[int] = set()
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(func):
                if _is_importorskip(sub) and _env_fallback_guards(func, sub):
                    guarded.add(id(sub))
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if _is_importorskip(node) and id(node) not in guarded
    ]


def _shipped_by_lock(
    module: str, lock: set[str], distributions: dict[str, list[str]]
) -> bool:
    top = module.split(".")[0]
    if top in sys.stdlib_module_names:
        return True
    candidates = distributions.get(top) or [top]
    return any(re.sub(r"[-_.]+", "-", name).lower() in lock for name in candidates)


def test_the_analysis_sees_the_dsn_tests_it_must():
    """If the detection went blind, every check below would pass vacuously."""
    _require_checkout()
    files = _dsn_test_files()
    assert len(files) >= 200, (
        f"only {len(files)} DSN test files found: the detection is broken"
    )
    # A read through a helper module counts (ai_history_seed, defect_w06_world).
    assert "tests.integration.ai_history_seed" in _dsn_helpers(), sorted(_dsn_helpers())
    assert "tests/integration/test_ai_run_versions_mongo.py" in files
    # A key in another dict is not a read: this test only inspects ci.yml.
    assert "tests/test_phase3_ci_workflow.py" not in files
    # No conftest reads the DSN: a fixture there would make tests DSN-bound
    # without their module showing it, and this analysis would not see them.
    readers = [_rel(p) for p in TESTS.rglob("conftest.py") if _reads_dsn(_tree(p))]
    assert readers == [], (
        f"a conftest reads {DSN}; extend this guard to its fixtures: {readers}"
    )
    # Location marking is what makes tests/integration/ files pass -m integration.
    assert '"/tests/integration/" in path' in (TESTS / "conftest.py").read_text(
        encoding="utf-8"
    )


def test_every_test_file_that_needs_the_dsn_is_in_the_postgres_job():
    _require_checkout()
    listed = set(_listed())
    assert len(listed) >= 200, (
        f"the step names too few files to be the right one: {len(listed)}"
    )
    source = CI_WORKFLOW.read_text(encoding="utf-8")
    for rel, step_name in RUN_BY_ANOTHER_STEP.items():
        assert source.count(step_name) == 1, (
            f"{rel}: the step {step_name!r} that runs it is gone"
        )
        step = source.split(step_name, 1)[1].split("- name:", 1)[0]
        assert re.search(r"pytest\b", step) and rel in step, (
            f"{rel} is no longer run by {step_name!r}"
        )
    missing = [
        rel
        for rel in _dsn_test_files()
        if rel not in listed and rel not in RUN_BY_ANOTHER_STEP
    ]
    assert not missing, (
        "these tests need TESTLOOKUP_POSTGRES_TEST_DSN but no CI job runs them; add them to the "
        f"'Run protected PostgreSQL integration suite' step in .github/workflows/ci.yml: {missing}"
    )


def test_every_listed_file_exists():
    _require_checkout()
    absent = [rel for rel in _listed() if not (BACKEND / rel).is_file()]
    assert not absent, f"the Postgres step names files that do not exist: {absent}"


def test_every_dsn_test_carries_the_integration_marker():
    _require_checkout()
    unmarked: list[str] = []
    for rel in _dsn_test_files():
        if rel.startswith("tests/integration/"):
            continue  # marked by location in tests/conftest.py
        path = BACKEND / rel
        tree = _tree(path)
        if _pytestmark_has_integration(tree.body):
            continue
        tests = _dsn_tests(path)
        assert tests, (
            f"{rel} needs the DSN but no test in it was traced to it: extend this guard"
        )
        for func, cls in tests:
            marked = any(_is_integration_mark(d) for d in func.decorator_list)
            if cls is not None:
                marked = marked or any(
                    _is_integration_mark(d) for d in cls.decorator_list
                )
                marked = marked or _pytestmark_has_integration(cls.body)
            if not marked:
                unmarked.append(f"{rel}::{func.name}")
    assert not unmarked, (
        f"-m integration deselects these DSN tests silently; mark them: {unmarked}"
    )


def test_no_listed_file_importorskips_a_package_the_lock_does_not_ship():
    _require_checkout()
    lock = _lock_names()
    assert {"asyncpg", "sqlalchemy", "pytest"} <= lock, "the lock parse is broken"
    distributions = metadata.packages_distributions()
    offenders = {
        rel: sorted(
            m
            for m in _importorskipped(BACKEND / rel)
            if not _shipped_by_lock(m, lock, distributions)
        )
        for rel in _listed()
        if (BACKEND / rel).is_file()
    }
    offenders = {rel: modules for rel, modules in offenders.items() if modules}

    assert len(KNOWN_UNLOCKED_IMPORTORSKIP) <= KNOWN_UNLOCKED_IMPORTORSKIP_MAX, (
        "this list may only shrink"
    )
    new = {
        rel: modules
        for rel, modules in offenders.items()
        if rel not in KNOWN_UNLOCKED_IMPORTORSKIP
    }
    assert not new, (
        "CI installs backend/requirements-lock.txt, so these listed files skip in the Postgres job for good "
        f"(importorskip of a package the lock does not ship): {new}"
    )
    fixed = sorted(set(KNOWN_UNLOCKED_IMPORTORSKIP) - set(offenders))
    assert not fixed, (
        f"no longer an offender, delete it from KNOWN_UNLOCKED_IMPORTORSKIP: {fixed}"
    )
