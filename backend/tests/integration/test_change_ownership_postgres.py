"""Opt-in PostgreSQL authority coverage for the Change/Ownership specialist."""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

from app.agents.change_ownership_agent import ChangeOwnershipAgent  # noqa: E402
from app.models.postgres import (  # noqa: E402
    FailureCluster as _FailureCluster,
    LaunchStatus as _LaunchStatus,
    Project as _Project,
    TestCase as _TestCase,
    TestRun as _TestRun,
    TestStatus as _TestStatus,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def session_factory():
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_change_ownership_re_resolves_project_run_and_failed_members(
    session_factory, monkeypatch
):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    case_id = uuid.uuid4()
    cluster_id = f"pg{uuid.uuid4().hex[:17]}"[:20]
    slug = f"pg-change-{uuid.uuid4().hex}"
    build = f"pg-build-{uuid.uuid4().hex}"

    async with session_factory() as db:
        db.add(_Project(id=project_id, name=slug, slug=slug))
        await db.flush()
        db.add(
            _TestRun(
                id=run_id,
                project_id=project_id,
                build_number=build,
                jenkins_job="pg-change-ownership",
                status=_LaunchStatus.FAILED,
                ingestion_source="unknown",
                total_tests=1,
                failed_tests=1,
            )
        )
        db.add(
            _TestCase(
                id=case_id,
                test_run_id=run_id,
                test_fingerprint=uuid.uuid4().hex[:64],
                test_name="contract-authority-test",
                status=_TestStatus.FAILED,
            )
        )
        await db.flush()
        db.add(
            _FailureCluster(
                test_run_id=run_id,
                cluster_id=cluster_id,
                label="authority cluster",
                member_test_ids=[str(case_id)],
                size=1,
            )
        )
        await db.commit()

    monkeypatch.setattr(
        "app.agents.change_ownership_agent.compute_regression_diff",
        AsyncMock(return_value={"baseline_available": True, "pass_rate_delta": -1}),
    )
    monkeypatch.setattr(
        "app.agents.change_ownership_agent.resolve_cluster_ownership",
        AsyncMock(return_value=type("Owner", (), {"to_dict": lambda self: {"team_name": "qa"}})()),
    )

    try:
        agent = ChangeOwnershipAgent()
        result = await agent.run({"project_id": str(project_id), "test_run_id": str(run_id)})
        assert result["status"] == "complete"
        assert result["ownership_resolutions"][0]["cluster_id"] == cluster_id

        foreign = await agent.run({"project_id": str(uuid.uuid4()), "test_run_id": str(run_id)})
        # The refusal payload itself must leak nothing about the foreign run.
        # Compared key-by-key rather than as a whole-dict equality because the
        # agent also stamps ``agent_contracts`` metadata (required by the
        # agents.contract-metadata ratchet), which is additive and orthogonal
        # to what this test pins.
        assert foreign["status"] == "failed"
        assert foreign["baseline_diff"] == {}
        assert foreign["ownership_resolutions"] == []
        assert foreign["summary"] == "run_scope_not_found"
        contract = foreign["agent_contracts"]["change_ownership"]
        assert contract["fallback_used"] is True
        assert contract["confidence"] == 0

        async with session_factory.begin() as db:
            await db.execute(
                _TestCase.__table__.update()
                .where(_TestCase.id == case_id)
                .values(status=_TestStatus.PASSED)
            )
        stale = await agent.run({"project_id": str(project_id), "test_run_id": str(run_id)})
        assert stale["status"] == "failed"
        assert stale["summary"] == "cluster_member_authority_invalid"
    finally:
        async with session_factory.begin() as db:
            await db.execute(delete(_FailureCluster).where(_FailureCluster.test_run_id == run_id))
            await db.execute(delete(_TestCase).where(_TestCase.test_run_id == run_id))
            await db.execute(delete(_TestRun).where(_TestRun.id == run_id))
            await db.execute(delete(_Project).where(_Project.id == project_id))
