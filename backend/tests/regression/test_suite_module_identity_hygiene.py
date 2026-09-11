"""Tests must not leave the module table different from how they found it (E2).

Re-audit E2: some tests passed alone and in the full suite but failed in other
subsets. Each failure was one test file changing ``sys.modules`` for everyone
after it:

* ``services/test_batch1_pure_services.py`` installed a stub
  ``defusedxml.ElementTree`` (no ``iterparse``) whenever it happened to be
  collected first, and never removed it: XML exports failed later with
  "cannot import name 'iterparse' ... (unknown location)".
* ``test_analysis_agent.py`` swapped the classifier module for a mock and
  then POPPED it. The next import built a second copy, so a test holding the
  first one patched a module its code no longer ran.
* ``services/test_batch5_all_projects.py`` re-imported three services (one
  under fake ORM models) and left the fresh copies in ``sys.modules`` and on
  the parent package.

Each polluter's test body is run here directly, then the module table is
checked. The victims were hardened too, but that alone would let a polluter
come back unnoticed.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, TESTS / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identity(name: str):
    module = importlib.import_module(name)
    parent_name, _, child = name.rpartition(".")
    return module, getattr(sys.modules[parent_name], child)


def test_batch1_does_not_stub_an_installed_defusedxml() -> None:
    pytest.importorskip("defusedxml")
    real = importlib.import_module("defusedxml.ElementTree")
    saved = {k: sys.modules.pop(k) for k in ("defusedxml", "defusedxml.ElementTree")}
    try:
        _load("services/test_batch1_pure_services.py", "_e2_batch1")
        after = importlib.import_module("defusedxml.ElementTree")
        assert hasattr(after, "iterparse"), "a stub defusedxml.ElementTree was left in sys.modules"
        assert after.__spec__ is not None and after.__spec__.origin, "defusedxml.ElementTree is a stub"
    finally:
        sys.modules.update(saved)
    assert importlib.import_module("defusedxml.ElementTree") is real


def test_analysis_agent_fallback_tests_put_the_classifier_module_back() -> None:
    name = "app.services.training.classifier"
    before = _identity(name)
    module = _load("test_analysis_agent.py", "_e2_analysis_agent")
    suite = module.TestProgressiveFallback()
    for test in ("test_tier1_fast_classifier_success",
                 "test_tier2_pattern_match_on_classifier_failure",
                 "test_tier3_generic_when_no_pattern"):
        asyncio.run(getattr(suite, test)())
        assert sys.modules.get(name) is before[0], f"{test} left a different {name} in sys.modules"
    assert _identity(name) == before


@pytest.mark.parametrize("test_name,module_name", [
    ("test_list_project_runs_no_filter_builds_correct_query", "app.services.runs_service"),
    ("test_analytics_service_accepts_none_project_id", "app.services.analytics_service"),
    ("test_metrics_service_accepts_none_project_id", "app.services.metrics_service"),
    ("test_flaky_tests_sql_omits_project_filter_when_none", "app.services.analytics_service"),
])
def test_batch5_fresh_imports_are_not_left_behind(test_name: str, module_name: str) -> None:
    before = _identity(module_name)
    module = _load("services/test_batch5_all_projects.py", "_e2_batch5")
    getattr(module, test_name)()
    after = _identity(module_name)
    assert after[0] is before[0], f"{test_name} left a fresh {module_name} in sys.modules"
    assert after[1] is before[1], f"{test_name} left a fresh {module_name} on its package"


# ── imports under a fake models module (coordinator's batch, re-audit E2) ────
#
# services/test_batch2, _batch3 and _batch4 imported a service INSIDE
# ``patch.dict(sys.modules, {"app.models.postgres": <hand-listed fake>})``.
# If an earlier test had cached the real module the fake was ignored; alone,
# the service was imported under the fake and died on the first name the list
# had drifted from (Project, TestAttachment, DigestSubscription): passes in
# full order, fails alone. The pattern itself is what is refused.


def _is_sys_modules_dict_patch(call) -> bool:
    import ast

    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "dict"):
        return False
    if not call.args:
        return False
    target = call.args[0]
    names_sys_modules = (
        (isinstance(target, ast.Constant) and target.value == "sys.modules")
        or (isinstance(target, ast.Attribute) and target.attr == "modules"
            and getattr(target.value, "id", "") == "sys")
    )
    fakes_models = len(call.args) > 1 and isinstance(call.args[1], ast.Dict) and any(
        isinstance(k, ast.Constant) and k.value == "app.models.postgres" for k in call.args[1].keys
    )
    return names_sys_modules and fakes_models


def test_no_test_imports_the_app_under_a_fake_models_module() -> None:
    import ast

    offenders = []
    for path in sorted(TESTS.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            if not any(isinstance(item.context_expr, ast.Call)
                       and _is_sys_modules_dict_patch(item.context_expr) for item in node.items):
                continue
            for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                module = None
                if isinstance(inner, ast.ImportFrom):
                    module = inner.module or ""
                elif isinstance(inner, ast.Import):
                    module = inner.names[0].name
                if module and (module == "app" or module.startswith("app.")):
                    offenders.append(f"{path.relative_to(TESTS).as_posix()}:{inner.lineno} imports {module}")
    assert not offenders, (
        "import the real module at module level instead of inside a fake "
        "app.models.postgres window:\n" + "\n".join(offenders)
    )


# ── the Celery drill's worker module (coordinator's batch, re-audit E2) ──────
#
# tests/regression/celery_visibility_worker.py configures the SHARED Celery app
# for the disposable broker drill (visibility_timeout=4). A pytest run that
# named it by path imported it, and every later test saw that config:
# test_child_broker_delivery_contract failed on visibility_timeout == 3600.


def test_importing_the_celery_drill_worker_leaves_the_shared_app_alone(monkeypatch) -> None:
    pytest.importorskip("celery")
    from app.worker.celery_app import celery_app

    monkeypatch.delenv("VISIBILITY_WORKER_NAME", raising=False)
    before_opts = dict(celery_app.conf.broker_transport_options or {})
    before_queues = tuple(celery_app.conf.task_queues or ())
    _load("regression/celery_visibility_worker.py", "_e2_visibility_worker")
    assert dict(celery_app.conf.broker_transport_options or {}) == before_opts
    assert tuple(celery_app.conf.task_queues or ()) == before_queues


def test_the_drill_worker_process_still_gets_its_settings(monkeypatch) -> None:
    pytest.importorskip("celery")
    from app.worker.celery_app import celery_app

    saved_opts = celery_app.conf.broker_transport_options
    saved_queues = celery_app.conf.task_queues
    monkeypatch.setenv("VISIBILITY_WORKER_NAME", "ci-visibility-test")
    try:
        _load("regression/celery_visibility_worker.py", "_e2_visibility_worker_on")
        assert celery_app.conf.broker_transport_options["visibility_timeout"] == 4
        assert any(q.name == "ci_visibility_drill" for q in celery_app.conf.task_queues)
    finally:
        celery_app.conf.broker_transport_options = saved_opts
        celery_app.conf.task_queues = saved_queues
