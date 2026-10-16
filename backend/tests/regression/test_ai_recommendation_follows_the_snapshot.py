"""``test_runs.ai_recommendation`` moves only with the run's snapshot (W07 FAIL-05, risk R-5).

The Runs list's AI-verdict FILTER reads the column; its PILL reads the
snapshot. They agree only while every write of a snapshot writes the column
too. So, over the AST of ``backend/app``:

* a snapshot row is created (``RunIntelligenceSnapshot(``) and deleted
  (``delete(RunIntelligenceSnapshot)``) ONLY in ``intelligence_snapshot_service.py``;
  no raw SQL there or elsewhere deletes from ``run_intelligence_snapshots``;
* in that module, each writer -- ``save_snapshot``, ``invalidate``,
  ``stage_invalidate`` -- calls ``_set_ai_recommendation``, and ``mark_stale``
  (the snapshot stays servable) does not;
* ``_set_ai_recommendation`` is ``update(TestRun)`` setting ``ai_recommendation``
  AND ``updated_at=TestRun.updated_at`` (live-run recovery selects by
  ``updated_at``; a projection write must not make a run a candidate again);
* nothing else in ``backend/app`` writes ``ai_recommendation``.

Every other removal of a snapshot is the ``ON DELETE CASCADE`` from
``test_runs`` (``run_intelligence_snapshots.run_id``; the project reset deletes
runs), which takes the run, and its column, with it.
"""
from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
SERVICE = APP / "services" / "intelligence_snapshot_service.py"
WRITERS = ("save_snapshot", "invalidate", "stage_invalidate")


def _modules():
    for path in sorted(APP.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_snapshot_rows_are_created_and_deleted_only_by_the_snapshot_service():
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            creates = _name(node.func) == "RunIntelligenceSnapshot"
            deletes = _name(node.func) == "delete" and any(
                _name(arg) == "RunIntelligenceSnapshot" for arg in node.args
            )
            if (creates or deletes) and path != SERVICE:
                offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []


def test_no_raw_sql_deletes_snapshots():
    offenders = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = " ".join(node.value.lower().split())
                if "delete from run_intelligence_snapshots" in text:
                    offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []


def _functions(tree) -> dict[str, ast.AST]:
    return {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _calls(fn, name: str) -> bool:
    return any(isinstance(n, ast.Call) and _name(n.func) == name for n in ast.walk(fn))


def test_each_writer_of_a_snapshot_writes_the_column_and_mark_stale_does_not():
    functions = _functions(ast.parse(SERVICE.read_text(encoding="utf-8")))
    for writer in WRITERS:
        assert _calls(functions[writer], "_set_ai_recommendation"), writer
    assert not _calls(functions["mark_stale"], "_set_ai_recommendation")
    # Every function of the module that adds or deletes a snapshot row is one of the writers.
    for name, fn in functions.items():
        touches = _calls(fn, "RunIntelligenceSnapshot") or any(
            isinstance(n, ast.Call) and _name(n.func) == "delete" for n in ast.walk(fn)
        )
        if touches:
            assert name in WRITERS, name


def test_the_projection_is_an_update_of_test_runs_that_keeps_updated_at():
    fn = _functions(ast.parse(SERVICE.read_text(encoding="utf-8")))["_set_ai_recommendation"]
    updates = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _name(n.func) == "update"]
    assert [_name(u.args[0]) for u in updates] == ["TestRun"]
    (values,) = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and _name(n.func) == "values"]
    keywords = {kw.arg: kw.value for kw in values.keywords}
    assert set(keywords) == {"ai_recommendation", "updated_at"}
    kept = keywords["updated_at"]
    assert isinstance(kept, ast.Attribute) and kept.attr == "updated_at" and _name(kept.value) == "TestRun"


def test_nothing_else_writes_the_column():
    offenders = []
    for path, tree in _modules():
        if path == SERVICE:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "ai_recommendation":
                offenders.append(f"{path.relative_to(APP)}:{node.value.lineno}")
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "ai_recommendation":
                        offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = " ".join(node.value.lower().split())
                if "set ai_recommendation" in text:
                    offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert offenders == []
