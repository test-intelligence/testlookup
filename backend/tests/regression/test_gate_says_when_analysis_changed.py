"""D-97 (the High finding's last part): the gate says when a stored decision
predates a correction in its run.

Batch 1 made a correction count in the score, but a release decision stored
before the correction still read the old category, and nothing told the
reader. A signed decision must not be recomputed silently, so the read only
reports it: ``GET /release-readiness/{run_id}`` sets
``analysis_changed_since`` / ``analysis_changed_at`` when a person changed a
test's category in the run after the decision read the run's analyses. The
query itself runs on PostgreSQL in
``tests/integration/test_release_decision_analysis_changed_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from app.models.schemas import ReleaseCouncilResponse  # noqa: E402

RUN = uuid.uuid4()
ASSEMBLED = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)
CORRECTED = ASSEMBLED + timedelta(hours=3)


def _council(**kw):
    base = dict(
        run_id=str(RUN), recommendation="GO", risk_score=12,
        input_snapshot={"assembled_at": ASSEMBLED.isoformat()},
        created_at=CREATED, updated_at=CREATED,
    )
    base.update(kw)
    return ReleaseCouncilResponse(**base)


class TestDecisionTime:
    def test_the_evaluation_time_is_the_snapshot_assembly(self):
        from app.services.release_council_service import decision_evaluated_at

        assert decision_evaluated_at(_council()) == ASSEMBLED

    def test_without_a_snapshot_time_the_row_creation(self):
        from app.services.release_council_service import decision_evaluated_at

        assert decision_evaluated_at(_council(input_snapshot={"pass_rate": 91.0})) == CREATED
        assert decision_evaluated_at(_council(input_snapshot={"assembled_at": "not a time"})) == CREATED

    def test_a_naive_time_is_utc(self):
        from app.services.release_council_service import decision_evaluated_at

        naive = _council(input_snapshot={"assembled_at": "2026-11-20T09:00:00"})
        assert decision_evaluated_at(naive) == ASSEMBLED

    def test_no_time_at_all_is_none(self):
        from app.services.release_council_service import decision_evaluated_at

        assert decision_evaluated_at(_council(input_snapshot=None, created_at=None)) is None


class TestTheQuery:
    @pytest.mark.asyncio
    async def test_one_read_only_statement_over_both_records(self):
        from sqlalchemy.dialects import postgresql

        from app.services.release_council_service import analysis_changed_since

        db = SimpleNamespace(execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: CORRECTED)
        ))
        assert await analysis_changed_since(db, RUN, ASSEMBLED) == CORRECTED
        (stmt,), _ = db.execute.await_args
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert sql.lstrip().upper().startswith("SELECT")
        assert "greatest(" in sql
        assert "ai_analysis_versions" in sql and "ai_feedback" in sql
        assert "'human_correction'" in sql and "'bulk_reclassify'" in sql
        assert "'incorrect'" in sql and "corrected_category IS NOT NULL" in sql
        assert sql.count(RUN.hex[:8]) >= 2  # both halves are scoped to the run
        assert sql.count("2026-11-20 09:00:00") == 2  # both halves: after the decision

    @pytest.mark.asyncio
    async def test_with_the_pipeline_the_root_cause_start_is_the_read_time(self):
        """Review F3: the root-cause stage read the analyses and corrections the
        decision scored, minutes before ``assembled_at``. A correction saved in
        between must count, so the comparison is against LEAST(stage start,
        decided_at), and the stage start is the decision's own pipeline's."""
        from sqlalchemy.dialects import postgresql

        from app.services.release_council_service import analysis_changed_since

        pipeline = uuid.uuid4()
        db = SimpleNamespace(execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: CORRECTED)
        ))
        await analysis_changed_since(db, RUN, ASSEMBLED, pipeline)
        (stmt,), _ = db.execute.await_args
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "agent_stage_results" in sql and "'root_cause_analysis'" in sql
        assert sql.count("least(") == 2  # both halves compare against the read time
        assert pipeline.hex[:8] in sql

    @pytest.mark.asyncio
    async def test_without_a_pipeline_the_decision_time_is_used(self):
        from sqlalchemy.dialects import postgresql

        from app.services.release_council_service import analysis_changed_since

        db = SimpleNamespace(execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        ))
        await analysis_changed_since(db, RUN, ASSEMBLED, None)
        (stmt,), _ = db.execute.await_args
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "agent_stage_results" not in sql and "least(" not in sql

    @pytest.mark.asyncio
    async def test_nothing_found_is_none(self):
        from app.services.release_council_service import analysis_changed_since

        db = SimpleNamespace(execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        ))
        assert await analysis_changed_since(db, RUN, ASSEMBLED) is None


async def _route(monkeypatch, council, changed_at, pipeline_run_id=None):
    from app.routers import release_readiness
    from app.services import report_distribution_policy as policy
    from app.services.review_envelope import not_ai_generated

    monkeypatch.setattr(release_readiness, "get_release_council", AsyncMock(return_value=council))
    monkeypatch.setattr(
        policy, "release_review_projection",
        AsyncMock(return_value=(not_ai_generated(), False, False)),
    )
    probe = AsyncMock(return_value=changed_at)
    monkeypatch.setattr(release_readiness, "analysis_changed_since", probe)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(first=lambda: (pipeline_run_id, uuid.uuid4()))),
        commit=AsyncMock(),
    )
    out = await release_readiness.get_release_decision(
        RUN, allow_advisory=False,
        current_user=SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER"), db=db,
    )
    return out, probe


class TestTheGateRead:
    @pytest.mark.asyncio
    async def test_a_correction_after_the_decision_is_reported(self, monkeypatch):
        council = _council()
        out, probe = await _route(monkeypatch, council, CORRECTED)

        assert out.analysis_changed_since is True
        assert out.analysis_changed_at == CORRECTED
        assert probe.await_args.args[1:] == (RUN, ASSEMBLED, None)
        # The stored decision is reported as it is: nothing recomputed.
        assert out.recommendation == "GO" and out.risk_score == 12
        assert council.analysis_changed_since is False

    @pytest.mark.asyncio
    async def test_the_route_passes_the_decisions_pipeline(self, monkeypatch):
        """Review F3: the stored decision's pipeline reaches the query, so its
        root-cause start is the read time."""
        pipeline = uuid.uuid4()
        out, probe = await _route(monkeypatch, _council(), CORRECTED, pipeline_run_id=pipeline)

        assert probe.await_args.args[1:] == (RUN, ASSEMBLED, pipeline)
        assert out.analysis_changed_since is True

    @pytest.mark.asyncio
    async def test_no_later_correction_reports_nothing(self, monkeypatch):
        out, probe = await _route(monkeypatch, _council(), None)

        probe.assert_awaited_once()
        assert out.analysis_changed_since is False and out.analysis_changed_at is None

    @pytest.mark.asyncio
    async def test_a_quick_look_is_not_checked(self, monkeypatch):
        """A quick look is computed now, from the current categories."""
        out, probe = await _route(monkeypatch, _council(synthesized=True, created_at=None), CORRECTED)

        probe.assert_not_awaited()
        assert out.analysis_changed_since is False

    @pytest.mark.asyncio
    async def test_the_json_carries_the_time(self, monkeypatch):
        out, _probe = await _route(monkeypatch, _council(), CORRECTED)

        body = out.model_dump(mode="json")
        assert body["analysis_changed_since"] is True
        assert datetime.fromisoformat(body["analysis_changed_at"]) == CORRECTED


def test_the_response_model_defaults_to_unchanged():
    bare = ReleaseCouncilResponse(run_id=str(RUN), recommendation="GO", risk_score=0)
    assert bare.analysis_changed_since is False and bare.analysis_changed_at is None
