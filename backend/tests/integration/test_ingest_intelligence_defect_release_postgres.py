"""One persisted journey from result ingestion to a release gate verdict."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _EmptyCursor:
    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self, *, length: int):
        assert length > 0
        return []


class _EmptyCollection:
    async def find_one(self, *_args, **_kwargs):
        return None

    def find(self, *_args, **_kwargs):
        return _EmptyCursor()


class _EmptyMongo:
    def __getitem__(self, _name):
        return _EmptyCollection()


async def test_ingest_intelligence_promotion_and_gate_share_one_release_axis() -> None:
    """A promoted blocker discovered from a release run must block that release."""
    from app.models.postgres import (
        Defect,
        DefectCandidate,
        FailureCluster,
        LaunchStatus,
        LinkSource,
        Project,
        Release,
        ReleaseGateDecision,
        ReleaseTestRunLink,
        TestCase,
        TestRun,
    )
    from app.services import defect_promotion_service
    from app.services.ingestion import _update_run_aggregates
    from app.services.ingestion_pipeline import ingest_test_results
    from app.services.release_gate_service import evaluate_release
    from app.services.run_intelligence_service import get_run_intelligence

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    release_id = uuid.uuid4()
    run_id = uuid.uuid4()
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as db:
            db.add(Project(id=project_id, name=f"Journey {token}", slug=f"journey-{token}"))
            db.add(
                Release(
                    id=release_id,
                    project_id=project_id,
                    name=f"2026.9-{token[:8]}",
                    status="in_progress",
                )
            )
            run = TestRun(
                id=run_id,
                project_id=project_id,
                primary_release_id=release_id,
                build_number=f"journey-{token}",
                jenkins_job="coverage-integration",
                branch="main",
                status=LaunchStatus.IN_PROGRESS,
            )
            db.add(run)
            # These models expose only scalar foreign keys, so SQLAlchemy has
            # no relationship edge from the link to the pending TestRun. Flush
            # the parent rows before inserting the explicit release link.
            await db.flush()
            db.add(
                ReleaseTestRunLink(
                    release_id=release_id,
                    test_run_id=run_id,
                    project_id=project_id,
                    link_source=LinkSource.EXPLICIT_CLIENT.value,
                    is_primary=True,
                )
            )
            await db.flush()

            accepted = await ingest_test_results(
                db,
                run,
                [
                    {
                        "test_name": f"checkout_{index}",
                        "class_name": "CheckoutJourney",
                        "suite_name": "checkout",
                        "status": "failed" if index == 4 else "passed",
                        "error_message": "payment declined" if index == 4 else None,
                    }
                    for index in range(5)
                ],
            )
            await db.flush()
            await _update_run_aggregates(db, run_id)
            failed_case = (
                await db.execute(
                    select(TestCase).where(
                        TestCase.test_run_id == run_id,
                        TestCase.status == "FAILED",
                    )
                )
            ).scalar_one()
            cluster = FailureCluster(
                test_run_id=run_id,
                cluster_id="cl_checkout",
                label="Checkout payment failure",
                representative_error="payment declined",
                member_test_ids=[str(failed_case.id)],
                size=1,
                cohesion_score=1.0,
            )
            db.add(cluster)
            db.add(
                DefectCandidate(
                    run_id=run_id,
                    cluster_id="cl_checkout",
                    severity="HIGH",
                    title="Checkout payment failure",
                    member_count=1,
                    composite_score=0.8,
                    status="pending",
                )
            )
            await db.commit()

        async with sessions() as db:
            intelligence = await get_run_intelligence(run_id, db, _EmptyMongo())
            assert accepted == 5
            assert intelligence["run"]["total_tests"] == 5
            assert intelligence["run"]["failed_tests"] == 1
            assert [item["cluster_id"] for item in intelligence["failure_clusters"]] == [
                "cl_checkout"
            ]
            assert intelligence["defect_candidates"][0]["status"] == "pending"

            with patch.object(
                defect_promotion_service,
                "_resolve_defect_owner_from_memory",
                AsyncMock(return_value=None),
            ), patch.object(
                defect_promotion_service,
                "_find_duplicate_semantic",
                AsyncMock(return_value=(None, False)),
            ), patch.object(
                defect_promotion_service,
                "check_defect_promotion_policy",
                AsyncMock(
                    return_value={
                        "initial_status": "approved",
                        "requires_approval": False,
                        "policy_reasons": [],
                    }
                ),
            ):
                promoted = await defect_promotion_service.promote_cluster(
                    str(run_id),
                    "cl_checkout",
                    str(project_id),
                    {
                        "title": "Checkout payment failure",
                        "description": "Persisted release blocker",
                        "severity": "HIGH",
                    },
                    db,
                )
            await db.flush()

            defect = await db.get(Defect, uuid.UUID(promoted["defect_id"]))
            assert defect is not None
            assert defect.release_id == release_id

            gate = await evaluate_release(db, release_id, record=True)
            await db.commit()

            assert gate["verdict"] == "NO_GO"
            assert gate["scorecard"]["denominator"] == 5
            assert gate["scorecard"]["evidence_count"] == 5
            assert gate["defects"]["blocking_count"] == 1
            assert gate["defects"]["blocking"][0]["id"] == promoted["defect_id"]
            decision = await db.get(
                ReleaseGateDecision, uuid.UUID(gate["decision_id"])
            )
            assert decision is not None
            assert decision.is_current is True
            assert decision.verdict == "NO_GO"
            assert decision.run_ids == [str(run_id)]
    finally:
        primary_error = sys.exception()
        try:
            async with sessions() as db:
                await db.execute(delete(Project).where(Project.id == project_id))
                await db.commit()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            warnings.warn(
                f"journey cleanup failed after primary failure: {cleanup_error!r}",
                RuntimeWarning,
                stacklevel=1,
            )
        finally:
            await engine.dispose()


async def test_compliance_history_excludes_phase_gate_rows() -> None:
    """A release export must not present phase verdicts as release history."""
    from app.models.postgres import Project, Release, ReleasePhase
    from app.services import compliance_pack_service
    from app.services.release_gate_decision_service import record_decision

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    release_id = uuid.uuid4()
    phase_id = uuid.uuid4()
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as db:
            db.add(
                Project(
                    id=project_id,
                    name=f"History {token}",
                    slug=f"history-{token}",
                )
            )
            db.add(
                Release(
                    id=release_id,
                    project_id=project_id,
                    name=f"history-{token[:8]}",
                    status="in_progress",
                )
            )
            db.add(
                ReleasePhase(
                    id=phase_id,
                    release_id=release_id,
                    name="UAT",
                    phase_type="uat",
                )
            )
            await db.flush()

            await record_decision(
                db, release_id, "NO_GO", denominator=5, evidence_count=5
            )
            await record_decision(
                db, release_id, "GO", denominator=6, evidence_count=6
            )
            await record_decision(
                db,
                release_id,
                "NOT_EVALUATED",
                phase_id=phase_id,
                denominator=0,
                evidence_count=0,
            )
            await db.commit()

        async with sessions() as db:
            exported = await compliance_pack_service._gather_release_gate(db, release_id)

        assert exported["current"]["verdict"] == "GO"
        assert len(exported["history"]) == 2
        assert {row["verdict"] for row in exported["history"]} == {"GO", "NO_GO"}
        assert all(row["phase_id"] is None for row in exported["history"])
    finally:
        primary_error = sys.exception()
        try:
            async with sessions() as db:
                await db.execute(delete(Project).where(Project.id == project_id))
                await db.commit()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            warnings.warn(
                f"history cleanup failed after primary failure: {cleanup_error!r}",
                RuntimeWarning,
                stacklevel=1,
            )
        finally:
            await engine.dispose()


async def test_concurrent_release_overrides_preserve_both_audit_entries() -> None:
    """Two QA decisions must serialize instead of losing one audit record."""
    from app.models.postgres import Project, ReleaseDecision, TestRun
    from app.services import policy_evaluator_service
    from app.services.release_council_service import apply_override

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def no_policy_with_overlap(*_args, **_kwargs):
        # Without a row lock, this gives both transactions time to read the
        # same pre-override JSON audit list before either flushes its update.
        await asyncio.sleep(0.2)
        return None, "hardcoded"

    async def override(recommendation: str, reason: str) -> None:
        async with sessions() as db:
            result = await apply_override(
                run_id,
                recommendation,
                reason,
                SimpleNamespace(id=None, username=f"qa-{recommendation.lower()}"),
                db,
            )
            assert result is not None
            await db.commit()

    try:
        async with sessions() as db:
            db.add(Project(id=project_id, name=f"Override {token}", slug=f"override-{token}"))
            db.add(
                TestRun(
                    id=run_id,
                    project_id=project_id,
                    build_number=f"override-{token}",
                    jenkins_job="coverage-integration",
                    status="completed",
                )
            )
            await db.flush()
            db.add(
                ReleaseDecision(
                    test_run_id=run_id,
                    recommendation="NO_GO",
                    risk_score=80,
                    override_audit=[],
                )
            )
            await db.commit()

        with patch.object(
            policy_evaluator_service,
            "resolve_effective_policy",
            no_policy_with_overlap,
        ):
            await asyncio.gather(
                override("GO", "first independent QA decision"),
                override("CONDITIONAL_GO", "second independent QA decision"),
            )

        async with sessions() as db:
            decision = (
                await db.execute(
                    select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
                )
            ).scalar_one()
            assert decision.original_recommendation == "NO_GO"
            assert len(decision.override_audit or []) == 2
            assert {entry["reason"] for entry in decision.override_audit or []} == {
                "first independent QA decision",
                "second independent QA decision",
            }
    finally:
        primary_error = sys.exception()
        try:
            async with sessions() as db:
                await db.execute(delete(Project).where(Project.id == project_id))
                await db.commit()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            warnings.warn(
                f"override-race cleanup failed after primary failure: {cleanup_error!r}",
                RuntimeWarning,
                stacklevel=1,
            )
        finally:
            await engine.dispose()


async def test_agent_recompute_preserves_human_override() -> None:
    """A later agent pass may refresh facts but must not erase the QA verdict."""
    from app.agents import release_risk_agent
    from app.models.postgres import AgentPipelineRun, Project, ReleaseDecision, TestRun

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as db:
            db.add(Project(id=project_id, name=f"Recompute {token}", slug=f"recompute-{token}"))
            db.add(
                TestRun(
                    id=run_id,
                    project_id=project_id,
                    build_number=f"recompute-{token}",
                    jenkins_job="coverage-integration",
                    status="completed",
                )
            )
            await db.flush()
            db.add(
                AgentPipelineRun(
                    id=pipeline_id,
                    test_run_id=run_id,
                    workflow_type="deep",
                    status="completed",
                )
            )
            db.add(
                ReleaseDecision(
                    test_run_id=run_id,
                    pipeline_run_id=pipeline_id,
                    recommendation="GO",
                    risk_score=80,
                    human_override="QA accepted the documented residual risk",
                    original_recommendation="NO_GO",
                    original_risk_score=80,
                    override_audit=[{"reason": "QA accepted the documented residual risk"}],
                )
            )
            await db.commit()

        with patch.object(release_risk_agent, "AsyncSessionLocal", sessions), patch(
            "app.services.release_decision_webhook.emit_release_decided",
            AsyncMock(),
        ):
            await release_risk_agent.ReleaseRiskAgent()._persist_decision(
                str(run_id),
                str(pipeline_id),
                {
                    "recommendation": "CONDITIONAL_GO",
                    "risk_score": 45,
                    "blocking_issues": [],
                    "conditions_for_go": ["Recheck after deployment"],
                    "reasoning": "Fresh agent evidence is less severe.",
                    "dimension_scores": {"user_impact": 20.0},
                    "composite_risk": 45.0,
                    "score_model_version": 1,
                    "policy_id": None,
                    "policy_evaluation": None,
                },
                {"pass_rate": 95.0},
            )

        async with sessions() as db:
            decision = (
                await db.execute(
                    select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
                )
            ).scalar_one()
            assert decision.recommendation == "GO"
            assert decision.risk_score == 45
            assert decision.human_override == "QA accepted the documented residual risk"
            assert decision.original_recommendation == "NO_GO"
            assert decision.original_risk_score == 80
            assert decision.override_audit == [
                {"reason": "QA accepted the documented residual risk"}
            ]
            assert decision.reasoning == "Fresh agent evidence is less severe."
            assert decision.input_snapshot == {"pass_rate": 95.0}
    finally:
        primary_error = sys.exception()
        try:
            async with sessions() as db:
                await db.execute(delete(Project).where(Project.id == project_id))
                await db.commit()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            warnings.warn(
                f"recompute cleanup failed after primary failure: {cleanup_error!r}",
                RuntimeWarning,
                stacklevel=1,
            )
        finally:
            await engine.dispose()


async def test_override_wins_when_agent_recompute_waits_on_its_row_lock() -> None:
    """An override committed first must survive a waiting agent recomputation."""
    from app.agents import release_risk_agent
    from app.models.postgres import AgentPipelineRun, Project, ReleaseDecision, TestRun
    from app.services import policy_evaluator_service
    from app.services.release_council_service import apply_override

    token = uuid.uuid4().hex
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    engine = create_async_engine(_dsn(), pool_size=3, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    override_holds_lock = asyncio.Event()
    release_override = asyncio.Event()

    async def pause_after_override_lock(*_args, **_kwargs):
        # apply_override resolves policy only after SELECT FOR UPDATE, so this
        # pause proves recomputation waits behind the human transaction.
        override_holds_lock.set()
        await release_override.wait()
        return None, "hardcoded"

    async def override() -> None:
        async with sessions() as db:
            result = await apply_override(
                run_id,
                "GO",
                "QA accepted the documented residual risk",
                SimpleNamespace(id=None, username="qa-lead"),
                db,
            )
            assert result is not None
            await db.commit()

    decision = {
        "recommendation": "CONDITIONAL_GO",
        "risk_score": 45,
        "blocking_issues": [],
        "conditions_for_go": ["Recheck after deployment"],
        "reasoning": "Fresh agent evidence is less severe.",
        "dimension_scores": {"user_impact": 20.0},
        "composite_risk": 45.0,
        "score_model_version": 1,
        "policy_id": None,
        "policy_evaluation": None,
    }

    try:
        async with sessions() as db:
            db.add(Project(id=project_id, name=f"Race {token}", slug=f"race-{token}"))
            db.add(
                TestRun(
                    id=run_id,
                    project_id=project_id,
                    build_number=f"race-{token}",
                    jenkins_job="coverage-integration",
                    status="completed",
                )
            )
            await db.flush()
            db.add(
                AgentPipelineRun(
                    id=pipeline_id,
                    test_run_id=run_id,
                    workflow_type="deep",
                    status="completed",
                )
            )
            db.add(
                ReleaseDecision(
                    test_run_id=run_id,
                    pipeline_run_id=pipeline_id,
                    recommendation="NO_GO",
                    risk_score=80,
                    override_audit=[],
                )
            )
            await db.commit()

        with patch.object(
            policy_evaluator_service,
            "resolve_effective_policy",
            pause_after_override_lock,
        ), patch.object(release_risk_agent, "AsyncSessionLocal", sessions), patch(
            "app.services.release_decision_webhook.emit_release_decided",
            AsyncMock(),
        ):
            override_task = asyncio.create_task(override())
            await asyncio.wait_for(override_holds_lock.wait(), timeout=2)
            recompute_task = asyncio.create_task(
                release_risk_agent.ReleaseRiskAgent()._persist_decision(
                    str(run_id), str(pipeline_id), decision, {"pass_rate": 95.0}
                )
            )
            await asyncio.sleep(0.1)
            assert not recompute_task.done()
            release_override.set()
            await asyncio.gather(override_task, recompute_task)

        async with sessions() as db:
            stored = (
                await db.execute(
                    select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
                )
            ).scalar_one()
            assert stored.recommendation == "GO"
            assert stored.risk_score == 45
            assert stored.human_override == "QA accepted the documented residual risk"
            assert stored.original_recommendation == "NO_GO"
            assert stored.original_risk_score == 80
            assert len(stored.override_audit or []) == 1
            assert stored.override_audit[0]["before_recommendation"] == "NO_GO"
            assert stored.override_audit[0]["before_risk_score"] == 80
            assert stored.override_audit[0]["after_recommendation"] == "GO"
            assert stored.reasoning == "Fresh agent evidence is less severe."
    finally:
        release_override.set()
        primary_error = sys.exception()
        try:
            async with sessions() as db:
                await db.execute(delete(Project).where(Project.id == project_id))
                await db.commit()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            warnings.warn(
                f"override/recompute race cleanup failed after primary failure: {cleanup_error!r}",
                RuntimeWarning,
                stacklevel=1,
            )
        finally:
            await engine.dispose()
