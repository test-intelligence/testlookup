"""The dev seed dates each test case at its run, never at the moment it ran.

``scripts/seed_dev_data.py`` created every ``TestCase`` without ``created_at``,
so thirty days of seeded results were all dated the minute the seed ran, and
the 7-day "top failing" window counted thirty days of failures: one /failures
row read "7" over "failed 3 of 7 executions" (the UX redesign's browser E2E
pass, 2026-10-08; every seeded install, the homelab demo included). Migration
0199 repairs the rows already written; the Postgres half of these tests is
``tests/integration/test_seeded_test_case_times_postgres.py``.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "scripts" / "seed_dev_data.py"
MIGRATION_0199 = BACKEND / "migrations" / "versions" / "0199_date_seeded_test_cases_at_their_run.py"


def test_every_seeded_test_case_is_dated_at_its_run():
    tree = ast.parse(SEED.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TestCase"
    ]
    assert calls, "the seed creates test cases"
    for call in calls:
        created = [kw.value for kw in call.keywords if kw.arg == "created_at"]
        assert created, "a TestCase without created_at is dated the minute the seed ran"
        assert ast.unparse(created[0]) == "run_start"


def _load_0199():
    spec = importlib.util.spec_from_file_location("migration_0199", MIGRATION_0199)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0199_follows_0198_and_touches_only_the_dev_seed(monkeypatch):
    from alembic import op

    module = _load_0199()
    assert (module.revision, module.down_revision) == ("0199", "0198")
    sent: list = []
    monkeypatch.setattr(op, "execute", sent.append, raising=False)
    module.upgrade()
    assert sent == [module.SQL]
    # Scoped three ways: the seed's marker, the seed's job, rows dated after their run.
    assert "seed_dev_data_v1" in module.SQL
    assert "-regression-pipeline" in module.SQL
    assert "tc.created_at > COALESCE(tr.end_time" in module.SQL
