"""The outbox-driven AI pipeline starts, and a setup failure never strands it (re-audit N27).

Found on the homelab, on the first AI pipeline the outbox ever published (N23
had kept every one from being published at all):

* ``run_agent_pipeline`` calls ``run_offline_pipeline(pipeline_run_id=...,
  create_if_missing=True)``, which reads ``pipeline_setup["test_run_id"]``;
  ``_create_pipeline_run`` returned no such key, so the pipeline died with
  KeyError 30 ms in;
* that happened before the pipeline's failure handler, so its record stayed
  ``running`` and every retry raised ``pipeline_not_resumable``.

The only test of that path replaced ``run_offline_pipeline`` with a mock. These
run the real setup against real PostgreSQL; only the LangGraph graph and the
two post-run side effects (events, memory) are faked.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` / ``DATABASE_URL`` pointing at a
database migrated to head.
"""
from __future__ import annotations

import ast
import inspect
import os
import textwrap
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Graph:
    """Stands in for the compiled LangGraph app: returns the state it was given."""

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, state):
        self.calls += 1
        return {**state, "completed_stages": ["ingestion"], "errors": []}


@pytest.fixture
async def world(monkeypatch):
    from app.agents import workflow
    from app.models.postgres import (
        AgentPipelineRun,
        AgentStageResult,
        Project,
        TestRun,
    )

    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    # The app engine may still hold connections an EARLIER test file opened on
    # its own, now closed, event loop: in CI's order this test then failed with
    # "Event loop is closed" (QA of N27). Start clean, as well as end clean.
    from app.db import postgres as app_postgres

    await app_postgres.dispose_engine_for_loop()
    async with sessions() as db:
        db.add(Project(id=project_id, name=f"n27-{project_id.hex}", slug=f"n27-{project_id.hex}"))
        await db.flush()
        db.add(TestRun(
            id=run_id, project_id=project_id, build_number=f"n27-{run_id.hex[:8]}",
            status="FAILED", total_tests=3, passed_tests=2, failed_tests=1,
            skipped_tests=0, broken_tests=0, unknown_tests=0,
        ))
        await db.commit()

    graph = _Graph()

    async def _quiet(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workflow, "_compile_frozen_workflow", lambda _setup: graph)
    monkeypatch.setattr(workflow, "emit_event", _quiet)
    monkeypatch.setattr(workflow, "_persist_memory", _quiet)
    try:
        yield {
            "sessions": sessions, "project": str(project_id), "run": str(run_id),
            "graph": graph, "models": (AgentPipelineRun, AgentStageResult),
        }
    finally:
        async with sessions() as db:
            ids = select(AgentPipelineRun.id).where(AgentPipelineRun.test_run_id == run_id)
            await db.execute(delete(AgentStageResult).where(AgentStageResult.pipeline_run_id.in_(ids)))
            await db.execute(delete(AgentPipelineRun).where(AgentPipelineRun.test_run_id == run_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            await db.commit()
        await engine.dispose()
        # The pipeline opened the app's own engine on this test's event loop;
        # the next test runs on a new loop, where it could not be used.
        from app.db import postgres

        await postgres.dispose_engine_for_loop()


async def _status(world, pipeline_run_id: str) -> str | None:
    AgentPipelineRun, _ = world["models"]
    async with world["sessions"]() as db:
        row = await db.get(AgentPipelineRun, uuid.UUID(pipeline_run_id))
    return row.status if row else None


def _keys_read(function) -> set[str]:
    """Every ``pipeline_setup["..."]`` a pipeline function reads by subscript."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    return {
        node.slice.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "pipeline_setup"
        and isinstance(node.slice, ast.Constant)
    }


async def test_a_created_setup_has_every_key_the_pipelines_read(world):
    """The shape is taken from the real function, never written out by hand: a
    hand-written shape is how the missing key hid behind a passing test."""
    from app.agents import workflow

    read = _keys_read(workflow.run_offline_pipeline) | _keys_read(workflow.run_deep_pipeline)
    assert {"test_run_id", "project_id", "initial_workflow_plan"} <= read, read
    pipeline_run_id = str(uuid.uuid4())
    setup = await workflow._create_pipeline_run(pipeline_run_id, world["run"], world["project"], "offline")
    assert setup["attempt"] == 1
    # analysis_mode_resolution is read only on the resume path, where the
    # claim supplies it.
    missing = sorted(read - set(setup) - {"analysis_mode_resolution"})
    assert not missing, f"the pipelines read keys the create helper does not return: {missing}"


async def test_an_outbox_driven_pipeline_starts_and_completes(world):
    from app.agents import workflow

    pipeline_run_id = str(uuid.uuid4())
    state = await workflow.run_offline_pipeline(
        test_run_id=world["run"], project_id=world["project"], build_number="b-1",
        workflow_type="offline", pipeline_run_id=pipeline_run_id, create_if_missing=True,
    )

    assert world["graph"].calls == 1, "the pipeline never reached its graph"
    assert state["test_run_id"] == world["run"]
    assert state["_attempt"] == 1
    assert await _status(world, pipeline_run_id) == "completed"


async def test_a_setup_failure_fails_the_pipeline_and_the_retry_resumes_it(world, monkeypatch):
    from app.agents import workflow

    pipeline_run_id = str(uuid.uuid4())
    real = workflow._resolve_analysis_mode_snapshot

    async def _broken(*_args, **_kwargs):
        raise RuntimeError("the AI config could not be read")

    monkeypatch.setattr(workflow, "_resolve_analysis_mode_snapshot", _broken)
    with pytest.raises(RuntimeError):
        await workflow.run_offline_pipeline(
            test_run_id=world["run"], project_id=world["project"], build_number="b-1",
            workflow_type="offline", pipeline_run_id=pipeline_run_id, create_if_missing=True,
        )
    assert await _status(world, pipeline_run_id) == "failed", (
        "a setup failure left the pipeline running, so no retry could ever resume it"
    )

    monkeypatch.setattr(workflow, "_resolve_analysis_mode_snapshot", real)
    await workflow.run_offline_pipeline(
        test_run_id=world["run"], project_id=world["project"], build_number="b-1",
        workflow_type="offline", pipeline_run_id=pipeline_run_id, create_if_missing=True,
    )
    assert await _status(world, pipeline_run_id) == "completed"


async def test_a_graph_failure_marks_the_pipeline_failed(world, monkeypatch):
    """The other failure path (re-audit N28): marking a pipeline failed without
    a final state raised UnboundLocalError, so every failed pipeline stayed
    'running' and its real error was replaced by that one."""
    from app.agents import workflow

    class _Failing:
        async def ainvoke(self, _state):
            raise ValueError("a stage failed")

    monkeypatch.setattr(workflow, "_compile_frozen_workflow", lambda _setup: _Failing())
    pipeline_run_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="a stage failed"):
        await workflow.run_offline_pipeline(
            test_run_id=world["run"], project_id=world["project"], build_number="b-1",
            workflow_type="offline", pipeline_run_id=pipeline_run_id, create_if_missing=True,
        )
    assert await _status(world, pipeline_run_id) == "failed"


async def test_a_pipeline_whose_stages_ran_is_not_resumed_without_its_snapshot(world):
    """Only a pipeline that failed during setup may resume with a fresh mode.
    Once a stage has run, the stored routing is the replay authority, and a
    row without it stays closed, as it always did."""
    from datetime import datetime, timezone

    from app.agents import workflow

    AgentPipelineRun, AgentStageResult = world["models"]
    pipeline_run_id = str(uuid.uuid4())
    await workflow._create_pipeline_run(pipeline_run_id, world["run"], world["project"], "offline")
    async with world["sessions"]() as db:
        stage = (await db.execute(
            select(AgentStageResult).where(AgentStageResult.pipeline_run_id == uuid.UUID(pipeline_run_id))
        )).scalars().first()
        stage.status = "failed"
        stage.started_at = datetime.now(timezone.utc)
        pipeline = await db.get(AgentPipelineRun, uuid.UUID(pipeline_run_id))
        pipeline.status = "failed"
        await db.commit()

    assert await workflow._claim_pipeline_resume(pipeline_run_id) is None
    assert await _status(world, pipeline_run_id) == "failed", "the refused claim changed the pipeline"


async def test_a_deep_pipeline_that_fails_during_setup_is_failed_not_stranded(world, monkeypatch):
    """The same guard on the deep path (QA of N27): a direct deep run that
    failed during setup stayed 'running' for the 30-minute reaper."""
    from app.agents import workflow

    AgentPipelineRun, _ = world["models"]

    async def _broken(*_args, **_kwargs):
        raise RuntimeError("the AI config could not be read")

    monkeypatch.setattr(workflow, "_resolve_analysis_mode_snapshot", _broken)
    with pytest.raises(RuntimeError):
        await workflow.run_deep_pipeline(
            test_run_id=world["run"], project_id=world["project"], build_number="b-1",
        )
    async with world["sessions"]() as db:
        statuses = (await db.execute(
            select(AgentPipelineRun.status).where(
                AgentPipelineRun.test_run_id == uuid.UUID(world["run"]),
                AgentPipelineRun.workflow_type == "deep",
            )
        )).scalars().all()
    assert statuses == ["failed"], statuses


async def test_a_log_call_that_raises_does_not_strand_the_pipeline(world, monkeypatch):
    """QA reproduced it with a non-UTF-8 stdout: the setup handler logged first,
    the log call raised, and the pipeline was never marked failed."""
    from types import SimpleNamespace

    from app.agents import workflow

    async def _broken(*_args, **_kwargs):
        raise RuntimeError("setup failed")

    def _error(event, *_args, **_kwargs):
        if event == "pipeline_setup_failed":
            raise UnicodeEncodeError("cp1252", "x", 0, 1, "cannot encode")

    def _quiet(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workflow, "_resolve_analysis_mode_snapshot", _broken)
    monkeypatch.setattr(
        workflow, "logger",
        SimpleNamespace(error=_error, info=_quiet, warning=_quiet, debug=_quiet, exception=_quiet),
    )
    pipeline_run_id = str(uuid.uuid4())
    with pytest.raises(RuntimeError, match="setup failed"):
        await workflow.run_offline_pipeline(
            test_run_id=world["run"], project_id=world["project"], build_number="b-1",
            workflow_type="offline", pipeline_run_id=pipeline_run_id, create_if_missing=True,
        )
    assert await _status(world, pipeline_run_id) == "failed"
