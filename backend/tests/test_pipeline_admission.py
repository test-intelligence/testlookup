"""``services/pipeline_admission`` (W14 AGT-12 / AGT-07): the shared in-progress reads.

* ``runs_with_in_progress_pipeline`` is ONE statement selecting ONE column
  (``test_run_id``, DISTINCT) over the requested ids, the workflow type and the
  in-progress statuses, read with ``.scalars().all()``; an empty input answers
  ``set()`` with NO statement; a scripted session whose ``.scalars()`` is a bare
  ``MagicMock`` reads "none in progress" (the shape
  ``tests/regression/test_agents_activity_events.py`` drives, unedited).
* ``latest_in_progress_pipeline`` is ``routers/agents._latest_in_progress_pipeline``'s
  body moved verbatim: the newest non-terminal row for (run, workflow).
* The module never commits, flushes or rolls back.
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import pipeline_admission
from app.services.workflow_run_state import IN_PROGRESS_STATUSES


class _Session:
    """Records every statement; answers each with ``result``."""

    def __init__(self, result) -> None:
        self.statements: list = []
        self.result = result

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return self.result


def _scalars_result(values):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(values)
    return result


def _compiled(statement) -> str:
    return " ".join(str(statement.compile(dialect=postgresql.dialect())).split())


@pytest.mark.asyncio
async def test_an_empty_input_sends_no_statement():
    db = _Session(_scalars_result([]))
    assert await pipeline_admission.runs_with_in_progress_pipeline(db, [], "offline") == set()
    assert db.statements == []


@pytest.mark.asyncio
async def test_one_statement_one_column_scoped_to_the_ids_type_and_in_progress_statuses():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _Session(_scalars_result([a, c]))
    found = await pipeline_admission.runs_with_in_progress_pipeline(db, [a, b, c, a], "deep")
    assert found == {a, c}
    (statement,) = db.statements
    assert len(statement.selected_columns) == 1
    assert statement.selected_columns[0].name == "test_run_id"
    sql = _compiled(statement)
    assert sql.startswith("SELECT DISTINCT agent_pipeline_runs.test_run_id FROM agent_pipeline_runs WHERE")
    assert "agent_pipeline_runs.test_run_id IN" in sql
    assert "agent_pipeline_runs.workflow_type =" in sql
    assert "agent_pipeline_runs.status IN" in sql
    params = statement.compile(dialect=postgresql.dialect()).params
    flat = [v for value in params.values() for v in (value if isinstance(value, (list, tuple)) else [value])]
    assert "deep" in flat and "offline" not in flat
    assert [v for v in flat if isinstance(v, uuid.UUID)] == [a, b, c], "each id once, in the request's order"
    assert set(IN_PROGRESS_STATUSES) <= set(flat)
    assert {"completed", "passed", "failed"}.isdisjoint(flat)


@pytest.mark.asyncio
async def test_a_bare_mock_scalars_reads_none_in_progress():
    result = MagicMock()          # .scalars() is a bare MagicMock: .scalars().all() iterates as empty
    db = _Session(result)
    assert await pipeline_admission.runs_with_in_progress_pipeline(db, [uuid.uuid4()], "offline") == set()


@pytest.mark.asyncio
async def test_latest_in_progress_pipeline_is_the_routers_old_body():
    row = object()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = _Session(result)
    run_id = uuid.uuid4()
    assert await pipeline_admission.latest_in_progress_pipeline(db, run_id, "offline") is row
    (statement,) = db.statements
    sql = _compiled(statement)
    assert sql.startswith("SELECT agent_pipeline_runs.id")
    assert "agent_pipeline_runs.test_run_id =" in sql and "agent_pipeline_runs.workflow_type =" in sql
    assert "agent_pipeline_runs.status IN" in sql
    assert sql.endswith("ORDER BY agent_pipeline_runs.created_at DESC LIMIT %(param_1)s")
    flat = [
        v
        for value in statement.compile(dialect=postgresql.dialect()).params.values()
        for v in (value if isinstance(value, (list, tuple)) else [value])
    ]
    assert run_id in flat and "offline" in flat and set(IN_PROGRESS_STATUSES) <= set(flat)


def test_the_module_is_commit_free():
    tree = ast.parse(Path(pipeline_admission.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint({"commit", "rollback", "flush", "add", "delete"}), called
