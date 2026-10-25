"""W14 AGT-08 / AGT-12: the REAL ``_create_pipeline_run`` resolves the project default.

A trigger that names no workflow (the post-ingest outbox, ``/agents/pipelines/
trigger`` without ``workflow_id``, bulk, ``POST /deep-investigate``) runs the
project's default for its base, resolved INSIDE ``_create_pipeline_run`` before
any agent config is frozen. Against real PostgreSQL:

(a) a published default -> ``workflow_ref == "wf.my-fork@1"``,
    ``workflow_default_source == "project_default"``, the fork's steps planned;
(b) no default -> ``offline@1`` / ``builtin``;
(c) a planted unusable row (no published version / another base) -> ``offline@1``
    / ``default_unavailable`` + the recorded sentence, never a failed run;
(d) a named workflow -> ``named`` (the default is not consulted);
and the bulk job id lands in ``execution_metadata`` and survives
``_mark_pipeline_done``'s metadata rebuild.

``_create_pipeline_run`` opens its own ``AsyncSessionLocal``, so the fixture
disposes the app engine before and after (W13's "Event loop is closed").
Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` migrated to head (0230).
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FORK_ID = "wf.my-fork"


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
        AgentPipelineRun,
        AgentStageResult,
        LaunchStatus,
        Project,
        ProjectWorkflowDefault,
        TestRun,
        WorkflowDefinition,
    )
    from app.services import workflow_definition_service as wf

    await app_pg.dispose_engine_for_loop()
    sessions = app_pg.AsyncSessionLocal
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as db:
        db.add(Project(id=project_id, name=f"w14c-{project_id.hex}", slug=f"w14c-{project_id.hex}"))
        await db.flush()
        db.add(TestRun(
            id=run_id, project_id=project_id, build_number=f"w14c-{run_id.hex[:8]}",
            status=LaunchStatus.FAILED, branch="main", primary_suite_name="checkout",
            total_tests=1, passed_tests=0, failed_tests=1, broken_tests=0,
            skipped_tests=0, unknown_tests=0, pass_rate=0.0,
        ))
        await db.commit()

    async def publish_fork(workflow_id: str, base: str) -> int:
        async with sessions() as db:
            row = await wf.fork_definition(
                db, project_id, wf.builtin(base),
                wf.WorkflowForkV1(workflow_id=workflow_id, name=f"Fork {workflow_id}"),
                actor_id=None,
            )
            await wf.publish_definition(db, row)
            version = int(row.version)
            await db.commit()
        return version

    async def add_draft_fork(workflow_id: str, base: str) -> None:
        async with sessions() as db:
            await wf.fork_definition(
                db, project_id, wf.builtin(base),
                wf.WorkflowForkV1(workflow_id=workflow_id, name=f"Draft {workflow_id}"),
                actor_id=None,
            )
            await db.commit()

    async def plant_default(base: str, workflow_id: str, version: int | None = None) -> None:
        # Planted directly: the PUT route refuses every unusable row this file needs.
        async with sessions() as db:
            db.add(ProjectWorkflowDefault(
                project_id=project_id, base=base, workflow_id=workflow_id, workflow_version=version,
            ))
            await db.commit()

    async def metadata(pipeline_run_id: str) -> dict:
        async with sessions() as db:
            row = await db.get(AgentPipelineRun, uuid.UUID(pipeline_run_id))
            return dict(row.execution_metadata or {}) if row else {}

    async def stage_names(pipeline_run_id: str) -> list[str]:
        async with sessions() as db:
            rows = (await db.execute(
                select(AgentStageResult.stage_name)
                .where(AgentStageResult.pipeline_run_id == uuid.UUID(pipeline_run_id))
            )).scalars().all()
            return sorted(rows)

    try:
        yield {
            "project": str(project_id), "run": str(run_id),
            "publish_fork": publish_fork, "add_draft_fork": add_draft_fork,
            "plant_default": plant_default, "metadata": metadata, "stage_names": stage_names,
        }
    finally:
        async with sessions() as db:
            ids = select(AgentPipelineRun.id).where(AgentPipelineRun.test_run_id == run_id)
            await db.execute(delete(AgentStageResult).where(AgentStageResult.pipeline_run_id.in_(ids)))
            await db.execute(delete(AgentPipelineRun).where(AgentPipelineRun.test_run_id == run_id))
            await db.execute(delete(ProjectWorkflowDefault).where(ProjectWorkflowDefault.project_id == project_id))
            await db.execute(delete(WorkflowDefinition).where(WorkflowDefinition.project_id == project_id))
            await db.execute(delete(TestRun).where(TestRun.id == run_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        await app_pg.dispose_engine_for_loop()


async def _create(world, workflow_type: str = "offline", **kwargs) -> tuple[str, dict]:
    from app.agents import workflow

    pipeline_run_id = str(uuid.uuid4())
    setup = await workflow._create_pipeline_run(
        pipeline_run_id, world["run"], world["project"], workflow_type, **kwargs,
    )
    return pipeline_run_id, setup


# ── (a) a usable default ─────────────────────────────────────────────────────


async def test_a_published_default_runs_and_is_frozen_like_a_named_fork(world):
    version = await world["publish_fork"](FORK_ID, "offline")
    await world["plant_default"]("offline", FORK_ID)  # unpinned: the latest published

    by_default, setup = await _create(world)
    meta = await world["metadata"](by_default)
    assert meta["workflow_ref"] == f"{FORK_ID}@{version}" == f"{FORK_ID}@1"
    assert meta["workflow_id"] == FORK_ID
    assert meta["workflow_default_source"] == "project_default"
    assert meta["workflow_default_reason"] is None
    assert setup["workflow_ref"] == f"{FORK_ID}@1"
    assert meta["initial_workflow_plan"]["workflow_ref"] == f"{FORK_ID}@1"

    # The same fork NAMED: the default inherited every gate a named run gets --
    # the same frozen agent configs, the same planned stages, the same rows.
    named, _ = await _create(world, workflow_id=FORK_ID, workflow_version=version)
    named_meta = await world["metadata"](named)
    assert named_meta["workflow_default_source"] == "named"
    assert sorted(meta["resolved_agent_configs"]) == sorted(named_meta["resolved_agent_configs"])
    assert meta["workflow_agent_configs"] == named_meta["workflow_agent_configs"]
    assert [s["stage"] for s in meta["initial_workflow_plan"]["stages"]] == [
        s["stage"] for s in named_meta["initial_workflow_plan"]["stages"]
    ]
    assert await world["stage_names"](by_default) == await world["stage_names"](named)


async def test_a_deep_default_serves_the_deep_base_only(world):
    await world["publish_fork"]("wf.deep-fork", "deep")
    await world["plant_default"]("deep", "wf.deep-fork")

    deep, _ = await _create(world, "deep")
    offline, _ = await _create(world, "offline")
    deep_meta, offline_meta = await world["metadata"](deep), await world["metadata"](offline)
    assert (deep_meta["workflow_ref"], deep_meta["workflow_default_source"]) == (
        "wf.deep-fork@1", "project_default",
    )
    assert (offline_meta["workflow_ref"], offline_meta["workflow_default_source"]) == (
        "offline@1", "builtin",
    )


# ── (b) no default ───────────────────────────────────────────────────────────


async def test_no_default_runs_the_builtin(world):
    pipeline_run_id, _ = await _create(world)
    meta = await world["metadata"](pipeline_run_id)
    assert meta["workflow_ref"] == "offline@1"
    assert meta["workflow_default_source"] == "builtin"
    assert meta["workflow_default_reason"] is None


# ── (c) an unusable default: the built-in runs and says why ─────────────────


async def test_a_default_without_a_published_version_falls_back_honestly(world):
    from app.core.agent_ops_vocab import DEFAULT_FALLBACK_SUFFIX, DEFAULT_UNAVAILABLE_GONE

    await world["add_draft_fork"]("wf.draft-only", "offline")
    await world["plant_default"]("offline", "wf.draft-only")

    pipeline_run_id, setup = await _create(world)
    meta = await world["metadata"](pipeline_run_id)
    assert meta["workflow_ref"] == setup["workflow_ref"] == "offline@1"
    assert meta["workflow_default_source"] == "default_unavailable"
    assert meta["workflow_default_reason"] == (
        DEFAULT_UNAVAILABLE_GONE.format(ref="wf.draft-only")
        + DEFAULT_FALLBACK_SUFFIX.format(base="offline")
    )


async def test_a_default_built_on_another_base_falls_back_honestly(world):
    from app.core.agent_ops_vocab import DEFAULT_FALLBACK_SUFFIX, DEFAULT_UNAVAILABLE_BASE

    await world["publish_fork"]("wf.deep-fork", "deep")
    await world["plant_default"]("offline", "wf.deep-fork", 1)

    pipeline_run_id, _ = await _create(world)
    meta = await world["metadata"](pipeline_run_id)
    assert meta["workflow_ref"] == "offline@1"
    assert meta["workflow_default_source"] == "default_unavailable"
    assert meta["workflow_default_reason"] == (
        DEFAULT_UNAVAILABLE_BASE.format(ref="wf.deep-fork@1", base="offline")
        + DEFAULT_FALLBACK_SUFFIX.format(base="offline")
    )


# ── (d) a named workflow never consults the default ─────────────────────────


async def test_a_named_workflow_ignores_the_default(world):
    await world["publish_fork"](FORK_ID, "offline")
    await world["plant_default"]("offline", FORK_ID)

    pipeline_run_id, _ = await _create(world, workflow_id="offline", workflow_version=1)
    meta = await world["metadata"](pipeline_run_id)
    assert (meta["workflow_ref"], meta["workflow_default_source"]) == ("offline@1", "named")
    assert meta["workflow_default_reason"] is None


async def test_a_rerun_of_a_legacy_row_keeps_the_builtin(world):
    """A retry passes its original's frozen id; a legacy original froze none,
    and its rerun replays the built-in it ran, not a default set since."""
    original, _ = await _create(world)
    await world["publish_fork"](FORK_ID, "offline")
    await world["plant_default"]("offline", FORK_ID)

    rerun, _ = await _create(world, rerun_of=original)
    meta = await world["metadata"](rerun)
    assert (meta["workflow_ref"], meta["workflow_default_source"]) == ("offline@1", "builtin")


# ── AGT-12: the bulk stamp, and the facts outlive the completion rebuild ────


async def test_the_bulk_job_id_lands_and_survives_completion(world):
    """``_mark_pipeline_done`` rebuilds ``execution_metadata`` from an allowlist
    on success; the bulk job counts its finished pipelines by ``bulk_job_id``
    and the timeline reads the workflow's source, so both must survive it."""
    from app.agents import workflow

    await world["add_draft_fork"]("wf.draft-only", "offline")
    await world["plant_default"]("offline", "wf.draft-only")
    job_id = str(uuid.uuid4())
    pipeline_run_id, _ = await _create(world, bulk_job_id=job_id)
    started = await world["metadata"](pipeline_run_id)
    assert started["bulk_job_id"] == job_id
    assert "run_overrides" not in started  # an unconfigured run carries none
    assert "run_overrides" not in started["initial_workflow_plan"]

    await workflow._mark_pipeline_done(
        pipeline_run_id,
        True,
        final_state={
            "pipeline_run_id": pipeline_run_id,
            "test_run_id": world["run"],
            "project_id": world["project"],
            "completed_stages": ["ingestion"],
            "errors": [],
        },
    )
    done = await world["metadata"](pipeline_run_id)
    assert done["bulk_job_id"] == job_id
    assert done["workflow_default_source"] == "default_unavailable"
    assert done["workflow_default_reason"] == started["workflow_default_reason"]


async def test_an_unstamped_run_has_no_bulk_job_id(world):
    pipeline_run_id, _ = await _create(world)
    assert "bulk_job_id" not in await world["metadata"](pipeline_run_id)
