"""W14 AGT-07: a deep run configuration is frozen by the REAL ``_create_pipeline_run``.

Against real PostgreSQL (no model is called; ``get_llm`` is not mocked -- nothing
reaches it):

* the frozen Root Cause config (``resolved_agent_configs``) carries the
  tightened cap / tier / tools; ``plan_sha256`` differs from the same call
  without them (the configuration is in the plan hash, §0.23);
  ``execution_metadata.run_overrides`` is set and ``run_overrides_status`` is
  ``applied``;
* the real ``IngestionAgent.run`` on that state keeps only the subset (order
  kept) and records ``failed_count_full``; the fingerprint covers the FULL run;
* ``AnalysisAgent._max_failures_analyzed(state)`` returns the tightened cap;
* ``_claim_pipeline_resume`` returns the same ``run_overrides``;
* a project config tightened below the patch BEFORE the start wins:
  ``run_overrides_status: "superseded"`` + reasons, the project's value frozen.

``_create_pipeline_run`` opens its own ``AsyncSessionLocal``: the fixture
disposes the app engine before and after.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select, update

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

RCA = "agent.root_cause_analysis.v1"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world():
    _dsn()
    from app.db import postgres as app_pg
    from app.models.postgres import (
        AgentConfig,
        AgentPipelineRun,
        AgentStageResult,
        LaunchStatus,
        Project,
        TestCase,
        TestRun,
    )

    await app_pg.dispose_engine_for_loop()
    sessions = app_pg.AsyncSessionLocal
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    failing = [uuid.UUID(int=(run_id.int & ~0xFFFF) | i) for i in range(1, 7)]  # ordered ids
    async with sessions() as db:
        db.add(Project(id=project_id, name=f"w14c-o-{project_id.hex}", slug=f"w14c-o-{project_id.hex}"))
        await db.flush()
        db.add(TestRun(
            id=run_id, project_id=project_id, build_number=f"w14c-{run_id.hex[:8]}",
            status=LaunchStatus.FAILED, branch="main", primary_suite_name="checkout",
            total_tests=7, passed_tests=1, failed_tests=6, broken_tests=0,
            skipped_tests=0, unknown_tests=0, pass_rate=14.3,
        ))
        await db.flush()
        for i, case_id in enumerate(failing):
            db.add(TestCase(
                id=case_id, test_run_id=run_id, test_name=f"test_pay_{i}", suite_name="checkout",
                test_fingerprint=f"w14c{run_id.hex[:6]}{i:03d}", status="FAILED",
                error_message="read timeout after 30s",
            ))
        db.add(TestCase(
            id=uuid.uuid4(), test_run_id=run_id, test_name="test_pay_ok", suite_name="checkout",
            test_fingerprint=f"w14c{run_id.hex[:6]}ok", status="PASSED",
        ))
        await db.commit()

    async def put_rca(**changes) -> None:
        from app.services.agent_config_service import AgentConfigV1, default_config, put_config

        doc = default_config(RCA).model_dump()
        for path, value in changes.items():
            section, key = path.split(".")
            doc[section][key] = value
        async with sessions() as db:
            await put_config(db, project_id, AgentConfigV1.model_validate(doc), updated_by=None)
            await db.commit()

    async def metadata(pipeline_run_id: str) -> dict:
        async with sessions() as db:
            row = await db.get(AgentPipelineRun, uuid.UUID(pipeline_run_id))
            return dict(row.execution_metadata or {}) if row else {}

    async def stage(pipeline_run_id: str, name: str):
        async with sessions() as db:
            return (await db.execute(select(AgentStageResult).where(
                AgentStageResult.pipeline_run_id == uuid.UUID(pipeline_run_id),
                AgentStageResult.stage_name == name,
            ))).scalar_one()

    async def fail(pipeline_run_id: str) -> None:
        async with sessions() as db:
            await db.execute(update(AgentPipelineRun).where(
                AgentPipelineRun.id == uuid.UUID(pipeline_run_id)
            ).values(status="failed"))
            await db.commit()

    try:
        yield {
            "project": str(project_id), "run": str(run_id), "failing": [str(i) for i in failing],
            "put_rca": put_rca, "metadata": metadata, "stage": stage, "fail": fail,
        }
    finally:
        async with sessions() as db:
            ids = select(AgentPipelineRun.id).where(AgentPipelineRun.test_run_id == run_id)
            await db.execute(delete(AgentStageResult).where(AgentStageResult.pipeline_run_id.in_(ids)))
            await db.execute(delete(AgentPipelineRun).where(AgentPipelineRun.test_run_id == run_id))
            await db.execute(delete(AgentConfig).where(AgentConfig.project_id == project_id))
            await db.execute(delete(TestCase).where(TestCase.test_run_id == run_id))
            await db.execute(delete(TestRun).where(TestRun.id == run_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        await app_pg.dispose_engine_for_loop()
        # The agents these tests drive open the cached Redis / Mongo clients on
        # this test's loop; drain them here, as the worker's loop runner does,
        # or the next module (test_every_get_route_answers_without_500_postgres
        # in ci.yml's order) meets "Event loop is closed".
        from app.db.mongo import close_mongo
        from app.db.redis_client import close_redis

        await close_redis()
        await close_mongo()


def _overrides(world, *, failure_ids, cap=None, tier=None, tools=None) -> dict:
    patch: dict = {}
    if tier is not None:
        patch["model"] = {"tier": tier}
    if cap is not None:
        patch["thresholds"] = {"max_failures_analyzed": cap}
    if tools is not None:
        patch["tools"] = {"allowlist": sorted(tools)}
    return {"failure_ids": failure_ids, "agent_patches": {RCA: patch} if patch else {}}


async def _create(world, **kwargs) -> tuple[str, dict]:
    from app.agents import workflow

    pipeline_run_id = str(uuid.uuid4())
    setup = await workflow._create_pipeline_run(
        pipeline_run_id, world["run"], world["project"], "deep", **kwargs,
    )
    return pipeline_run_id, setup


def _initial_state(world, pipeline_run_id: str, setup: dict) -> dict:
    """What run_deep_pipeline hands the graph (the keys the two agents read)."""
    return {
        "pipeline_run_id": pipeline_run_id,
        "test_run_id": world["run"],
        "project_id": world["project"],
        "run_overrides": setup.get("run_overrides"),
        "resolved_agent_configs": setup["resolved_agent_configs"],
        "_fencing_token": None,
    }


async def test_the_configuration_is_frozen_hashed_and_applied(world):
    from app.agents.analysis_agent import AnalysisAgent
    from app.agents.ingestion_agent import IngestionAgent
    from app.services.agent_config_service import default_config

    await world["put_rca"](**{"model.tier": "llm"})
    tools = list(default_config(RCA).tools.allowlist)[:1]
    subset = [world["failing"][4], world["failing"][1]]   # the run's order is kept, not this one
    overrides = _overrides(world, failure_ids=subset, cap=1, tier="slm", tools=tools)

    plain_id, plain = await _create(world)
    tight_id, tight = await _create(world, run_overrides=overrides)

    meta = await world["metadata"](tight_id)
    assert meta["run_overrides"] == overrides and tight["run_overrides"] == overrides
    assert meta["run_overrides_status"] == "applied"
    assert "run_overrides_reasons" not in meta
    assert meta["initial_workflow_plan"]["run_overrides"] == overrides
    assert meta["workflow_plan_sha256"] != (await world["metadata"](plain_id))["workflow_plan_sha256"]
    assert "run_overrides" not in (await world["metadata"](plain_id))
    frozen = meta["resolved_agent_configs"][RCA]["config"]
    assert frozen["thresholds"]["max_failures_analyzed"] == 1
    assert frozen["model"]["tier"] == "slm"
    assert frozen["tools"]["allowlist"] == sorted(tools)
    plain_frozen = plain["resolved_agent_configs"][RCA]["config"]
    assert (plain_frozen["model"]["tier"], plain_frozen["thresholds"]["max_failures_analyzed"]) == ("llm", 50)

    state = _initial_state(world, tight_id, tight)
    assert AnalysisAgent._max_failures_analyzed(state) == 1
    out = await IngestionAgent().run(state)
    assert out["failed_test_ids"] == [world["failing"][1], world["failing"][4]]
    ingestion = await world["stage"](tight_id, "ingestion")
    assert ingestion.result_data["failed_count"] == 2
    assert ingestion.result_data["failed_count_full"] == 6
    [decision] = [d for d in (ingestion.decision_log or []) if d.get("decision_point") == "ingestion_scope"]
    assert decision["chosen"] == "subset"
    assert (decision["context"]["failed_count"], decision["context"]["failed_count_full"]) == (2, 6)

    # The fingerprint is the run's, not the selection's.
    plain_out = await IngestionAgent().run(_initial_state(world, plain_id, plain))
    assert len(plain_out["failed_test_ids"]) == 6
    assert out["run_input_fingerprint"] == plain_out["run_input_fingerprint"]


async def test_a_resume_replays_the_same_configuration(world):
    from app.agents import workflow

    overrides = _overrides(world, failure_ids=world["failing"][:2], cap=3)
    pipeline_run_id, _ = await _create(world, run_overrides=overrides)
    await world["fail"](pipeline_run_id)

    claimed = await workflow._claim_pipeline_resume(pipeline_run_id)
    assert claimed is not None, "the configured pipeline must stay resumable"
    assert claimed["run_overrides"] == overrides


async def test_a_project_tightened_since_acceptance_wins(world):
    await world["put_rca"](**{"thresholds.max_failures_analyzed": 2})
    overrides = _overrides(world, failure_ids=world["failing"][:3], cap=5)

    pipeline_run_id, setup = await _create(world, run_overrides=overrides)

    meta = await world["metadata"](pipeline_run_id)
    assert meta["run_overrides_status"] == "superseded"
    assert any("max_failures_analyzed=5" in reason for reason in meta["run_overrides_reasons"])
    assert meta["resolved_agent_configs"][RCA]["config"]["thresholds"]["max_failures_analyzed"] == 2
    # The failure subset still applies.
    assert setup["run_overrides"]["failure_ids"] == world["failing"][:3]
