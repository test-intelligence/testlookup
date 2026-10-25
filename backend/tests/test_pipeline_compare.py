"""W14 AGT-05: ``GET /agents/pipelines/compare`` and its pure helpers.

The helpers are driven on plain stage objects; the handler is called directly
over a scripted session that records every statement, so "same id -> no
statement" and "the IDOR is a 403" are measured, not assumed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.agent_ops_vocab import COMPARE_CROSS_PROJECT, COMPARE_SAME_ID
from app.services import pipeline_compare_service as svc


def _stage(name, *, pipeline_id=None, start=None, seconds=None, status="completed",
           checksum=None, tokens=None, cost=None, fallback=None, confidence=None,
           path=None, result=None):
    result_data = dict(result or {})
    if checksum is not None:
        result_data["_replay"] = {"output_checksum_sha256": checksum}
    return SimpleNamespace(
        id=uuid.uuid4(),
        pipeline_run_id=pipeline_id,
        stage_name=name,
        status=status,
        started_at=start,
        completed_at=(start + timedelta(seconds=seconds)) if (start and seconds is not None) else None,
        total_tokens=tokens,
        cost_usd=cost,
        fallback_used=fallback,
        confidence_score=confidence,
        execution_path=path,
        result_data=result_data or None,
    )


def _t0():
    return datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


# ── pure helpers ────────────────────────────────────────────────────────────


def test_a_stage_missing_on_one_side_is_a_presence_change():
    t0 = _t0()
    rows = svc.compare_rows(
        [_stage("ingestion", start=t0, checksum="x"), _stage("summary", start=t0 + timedelta(seconds=5), checksum="y")],
        [_stage("ingestion", start=t0, checksum="x")],
    )
    by_name = {r["stage_name"]: r for r in rows}
    assert by_name["summary"]["changed"] == ["presence"]
    assert by_name["summary"]["b"] is None
    assert by_name["summary"]["output_checksum_equal"] is None
    assert by_name["ingestion"]["changed"] == []
    assert svc.compare_output(rows) == "changed"


def test_b_only_stages_follow_a_order_then_b_order():
    t0 = _t0()
    rows = svc.compare_rows(
        [_stage("ingestion", start=t0), _stage("summary", start=t0 + timedelta(seconds=9))],
        [_stage("anomaly", start=t0 + timedelta(seconds=3)), _stage("ingestion", start=t0),
         _stage("triage", start=t0 + timedelta(seconds=4))],
    )
    assert [r["stage_name"] for r in rows] == ["ingestion", "summary", "anomaly", "triage"]


def test_equal_checksums_everywhere_is_identical():
    t0 = _t0()
    a = [_stage("ingestion", start=t0, checksum="c1"), _stage("summary", start=t0 + timedelta(seconds=1), checksum="c2")]
    b = [_stage("ingestion", start=t0, checksum="c1"), _stage("summary", start=t0 + timedelta(seconds=1), checksum="c2")]
    rows = svc.compare_rows(a, b)
    assert all(r["output_checksum_equal"] is True for r in rows)
    assert svc.compare_output(rows) == "identical"


def test_a_differing_checksum_is_changed():
    t0 = _t0()
    rows = svc.compare_rows([_stage("summary", start=t0, checksum="c1")], [_stage("summary", start=t0, checksum="c9")])
    assert rows[0]["output_checksum_equal"] is False
    assert "output_checksum_sha256" in rows[0]["changed"]
    assert svc.compare_output(rows) == "changed"


def test_one_side_without_a_checksum_is_unknown_not_changed():
    t0 = _t0()
    rows = svc.compare_rows([_stage("summary", start=t0, checksum="c1")], [_stage("summary", start=t0)])
    assert rows[0]["output_checksum_equal"] is None
    assert "output_checksum_sha256" not in rows[0]["changed"]
    assert svc.compare_output(rows) == "unknown"


def test_a_reported_zero_cost_stays_zero_and_unknown_stays_none():
    t0 = _t0()
    cell = svc.stage_cell(_stage("summary", start=t0, cost=0.0, tokens=0))
    assert cell["cost_usd"] == 0.0 and cell["cost_usd"] is not None
    assert cell["total_tokens"] == 0
    assert svc.stage_cell(_stage("summary", start=t0))["cost_usd"] is None


def test_duration_is_none_unless_both_ends_are_stored():
    t0 = _t0()
    assert svc.stage_cell(_stage("summary", start=t0, seconds=4))["duration_seconds"] == 4.0
    assert svc.stage_cell(_stage("summary", start=t0))["duration_seconds"] is None
    assert svc.stage_cell(_stage("summary"))["duration_seconds"] is None


def test_deltas_only_when_both_sides_are_known():
    t0 = _t0()
    rows = svc.compare_rows(
        [_stage("summary", start=t0, seconds=10, tokens=100, cost=0.5)],
        [_stage("summary", start=t0, seconds=4, tokens=None, cost=0.75)],
    )
    row = rows[0]
    assert row["duration_delta_seconds"] == -6.0
    assert row["tokens_delta"] is None
    assert row["cost_delta_usd"] == 0.25
    assert "total_tokens" not in row["changed"]          # unknown on one side: not "changed"
    assert {"duration_seconds", "cost_usd"} <= set(row["changed"])


def test_a_repeated_stage_name_compares_its_last_row():
    t0 = _t0()
    a = [_stage("summary", start=t0, status="failed", checksum="old"),
         _stage("summary", start=t0 + timedelta(minutes=1), status="completed", checksum="new")]
    rows = svc.compare_rows(a, [_stage("summary", start=t0, checksum="new")])
    assert len(rows) == 1
    assert rows[0]["a"]["status"] == "completed"
    assert rows[0]["output_checksum_equal"] is True


def _pipeline(*, run_id, status="completed", metadata=None, attempt=1, rerun_of=None):
    t0 = _t0()
    return SimpleNamespace(
        id=uuid.uuid4(), test_run_id=run_id, workflow_type="offline", status=status,
        attempt=attempt, rerun_of=rerun_of, created_at=t0, started_at=t0,
        completed_at=t0 + timedelta(seconds=30), execution_metadata=metadata or {},
    )


def test_build_response_sides_totals_and_release_verdict():
    t0 = _t0()
    run_id = uuid.uuid4()
    a = _pipeline(run_id=run_id, metadata={"workflow_ref": "deep@1", "analysis_mode_resolved": "llm"})
    b = _pipeline(run_id=run_id, attempt=2, rerun_of=a.id)
    stages = [
        _stage("ingestion", pipeline_id=a.id, start=t0, tokens=10, cost=0.0, checksum="c"),
        _stage("release_risk", pipeline_id=a.id, start=t0 + timedelta(seconds=2),
               result={"recommendation": "hold"}, checksum="r"),
        _stage("ingestion", pipeline_id=b.id, start=t0, checksum="c"),
    ]
    body = svc.build_response(a, b, stages)
    assert body["same_test_run"] is True
    assert body["a"]["workflow_ref"] == "deep@1"
    assert body["a"]["analysis_mode_resolved"] == "llm"
    assert body["a"]["release_verdict"] == "hold"
    assert body["b"]["release_verdict"] is None
    assert body["a"]["total_tokens"] == 10 and body["a"]["cost_usd"] == 0.0
    assert body["b"]["total_tokens"] is None and body["b"]["cost_usd"] is None
    assert body["b"]["rerun_of"] == a.id and body["b"]["attempt"] == 2
    assert body["total"] == 2 and body["changed_stages"] == 1
    assert body["output"] == "changed"
    assert body["a"]["stages_truncated"] is False


def test_more_than_the_cap_marks_the_side_truncated():
    t0 = _t0()
    run_id = uuid.uuid4()
    a, b = _pipeline(run_id=run_id), _pipeline(run_id=run_id)
    stages = [
        _stage(f"s{i:03d}", pipeline_id=a.id, start=t0 + timedelta(seconds=i))
        for i in range(svc.MAX_COMPARE_STAGES + 1)
    ]
    body = svc.build_response(a, b, stages)
    assert body["a"]["stages_truncated"] is True
    assert body["b"]["stages_truncated"] is False
    assert body["total"] == svc.MAX_COMPARE_STAGES


# ── the handler over a scripted session ─────────────────────────────────────


class _Session:
    """Answers the two pipeline loads by id, ``db.get(TestRun, …)`` from a map,
    and the ONE stage statement; records every statement."""

    def __init__(self, pipelines, runs, stages=()):
        self.pipelines = {p.id: p for p in pipelines}
        self.runs = runs
        self.stages = list(stages)
        self.statements: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.statements.append(sql)
        if "agent_stage_results" in sql:
            rows = self.stages
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
        wanted = [v for v in stmt.compile().params.values() if isinstance(v, uuid.UUID)]
        found = next((self.pipelines[v] for v in wanted if v in self.pipelines), None)
        return SimpleNamespace(scalar_one_or_none=lambda: found)

    async def get(self, model, key):
        self.statements.append(f"get {model.__name__}")
        return self.runs.get(key)


def _user():
    return SimpleNamespace(id=uuid.uuid4(), role="qa_engineer")


async def _call(db, a, b, *, accessible):
    from app.routers import agents as agents_router

    with patch.object(agents_router, "get_accessible_project_ids", AsyncMock(return_value=accessible)):
        return await agents_router.compare_pipelines(a=a, b=b, db=db, current_user=_user())


@pytest.mark.asyncio
async def test_the_same_id_twice_is_a_422_with_no_statement():
    db = _Session([], {})
    same = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await _call(db, same, same, accessible=None)
    assert exc.value.status_code == 422
    assert exc.value.detail == COMPARE_SAME_ID
    assert db.statements == []


@pytest.mark.asyncio
async def test_an_unknown_a_is_a_404():
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    b = _pipeline(run_id=run.id)
    db = _Session([b], {run.id: run})
    with pytest.raises(HTTPException) as exc:
        await _call(db, uuid.uuid4(), b.id, accessible=None)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Pipeline run not found"


@pytest.mark.asyncio
async def test_a_member_of_p_comparing_with_a_pipeline_of_q_is_a_403():
    p, q = uuid.uuid4(), uuid.uuid4()
    run_p = SimpleNamespace(id=uuid.uuid4(), project_id=p)
    run_q = SimpleNamespace(id=uuid.uuid4(), project_id=q)
    mine, theirs = _pipeline(run_id=run_p.id), _pipeline(run_id=run_q.id)
    db = _Session([mine, theirs], {run_p.id: run_p, run_q.id: run_q})
    for a, b in ((mine.id, theirs.id), (theirs.id, mine.id)):
        with pytest.raises(HTTPException) as exc:
            await _call(db, a, b, accessible={p})
        assert exc.value.status_code == 403
        assert exc.value.detail == "You do not have access to this pipeline run"
    assert not any("agent_stage_results" in s for s in db.statements)


@pytest.mark.asyncio
async def test_an_admin_comparing_across_projects_is_a_422():
    run_p = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    run_q = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    a, b = _pipeline(run_id=run_p.id), _pipeline(run_id=run_q.id)
    db = _Session([a, b], {run_p.id: run_p, run_q.id: run_q})
    with pytest.raises(HTTPException) as exc:
        await _call(db, a.id, b.id, accessible=None)
    assert exc.value.status_code == 422
    assert exc.value.detail == COMPARE_CROSS_PROJECT
    assert not any("agent_stage_results" in s for s in db.statements)


@pytest.mark.asyncio
async def test_a_same_run_pair_answers_200_with_one_stage_statement():
    t0 = _t0()
    project = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project)
    a = _pipeline(run_id=run.id)
    b = _pipeline(run_id=run.id, attempt=2, rerun_of=a.id)
    stages = [
        _stage("ingestion", pipeline_id=a.id, start=t0, checksum="c"),
        _stage("ingestion", pipeline_id=b.id, start=t0, checksum="c"),
    ]
    db = _Session([a, b], {run.id: run}, stages)
    body = await _call(db, a.id, b.id, accessible={project})
    from app.models.schemas import PipelineCompareResponse

    parsed = PipelineCompareResponse.model_validate(body)
    assert parsed.same_test_run is True
    assert parsed.output == "identical"
    assert parsed.b.rerun_of == a.id
    assert sum(1 for s in db.statements if "agent_stage_results" in s) == 1
