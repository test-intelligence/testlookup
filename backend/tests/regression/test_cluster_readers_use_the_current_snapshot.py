"""Every reader of a run's failure clusters reads its newest snapshot.

A run analysed twice holds one immutable cluster set per pipeline run, so a
query on ``FailureCluster.test_run_id`` alone sees every cluster twice: the
Analysis tab listed duplicates, project metrics double-counted them, and a
lookup by ``cluster_id`` raised ``MultipleResultsFound`` (homelab,
2026-10-14). ``cluster_snapshot.is_current_snapshot()`` (and
``current_snapshot_sql`` for raw SQL) is the fix; the real-SQL behaviour is
pinned in ``tests/integration/test_current_cluster_snapshot_postgres.py``.

This guard keeps it that way: any function in ``app/`` that selects clusters
by run must apply the filter, scope to one pipeline (``pipeline_run_id ==``)
or one row (``FailureCluster.id ==``), or be listed below with its reason.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

SCOPED = (
    "is_current_snapshot(",
    "current_snapshot_sql(",
    "FailureCluster.pipeline_run_id ==",
    "FailureCluster.id ==",
)

# function -> why reading every snapshot of a run is right there.
EXEMPT = {
    "services/defect_jira_service.py::build_prefill":
        "project-wide lookup of the newest row for a cluster id, across runs",
}

_RUN_READ = re.compile(r"FailureCluster\.test_run_id\b|FROM\s+failure_clusters\b", re.IGNORECASE)


def _functions(path: Path):
    source = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, ast.get_source_segment(source, node) or ""


def _unscoped_readers() -> list[str]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel == "services/cluster_snapshot.py":
            continue
        for name, body in _functions(path):
            if not _RUN_READ.search(body):
                continue
            # A nested function is checked on its own; skip the outer copy of its text.
            if any(scope in body for scope in SCOPED) or f"{rel}::{name}" in EXEMPT:
                continue
            found.append(f"{rel}::{name}")
    return found


def test_every_run_cluster_reader_is_scoped():
    unscoped = _unscoped_readers()
    assert unscoped == [], (
        "These functions read a run's failure clusters without the current-snapshot "
        "filter; a re-analysed run would show each cluster twice:\n  " + "\n  ".join(unscoped)
    )


def test_the_exemptions_still_exist():
    present = {f"{p.relative_to(APP).as_posix()}::{name}" for p in APP.rglob("*.py") for name, _ in _functions(p)}
    assert set(EXEMPT) <= present, set(EXEMPT) - present


def test_the_guard_catches_an_unscoped_reader(tmp_path):
    """The guard itself: a reader without the filter is reported."""
    module = tmp_path / "services" / "fake_reader.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "async def list_clusters(db, run_id):\n"
        "    return await db.execute(select(FailureCluster).where(FailureCluster.test_run_id == run_id))\n",
        encoding="utf-8",
    )
    globals()["APP"], saved = tmp_path, APP
    try:
        assert _unscoped_readers() == ["services/fake_reader.py::list_clusters"]
    finally:
        globals()["APP"] = saved
