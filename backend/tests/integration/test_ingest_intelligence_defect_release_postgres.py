"""One persisted journey from result ingestion to a release gate verdict."""
from __future__ import annotations

import os
import sys
import uuid
import warnings
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
