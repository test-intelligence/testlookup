"""W04 (AIC-02 / AIC-03 / AGT-16): what a pipeline freezes survives every persistence point.

A pipeline freezes the Settings > AI confidence threshold and LLM timeout
(``execution_metadata["ai_config_snapshot"]``) and the ``aiq_gap_refinement``
flag (``gap_refinement_settings``) at creation. Each place that writes or
replays ``execution_metadata`` can silently drop a new key; this file reads
every one back against real PostgreSQL:

* P1 ``_create_pipeline_run`` writes both, and the plan is built with the
  frozen threshold and flag;
* P3 ``_mark_pipeline_done`` REBUILDS ``execution_metadata`` from an allowlist
  on success: both keys must survive it (a later degraded-completed retry
  replays them);
* P2 ``_claim_pipeline_resume`` returns both, and ``defect_commander_settings``
  (dropped before W04);
* P4 a same-id resume runs with the FROZEN values although the admin changed
  the setting since;
* P5 a pre-W04 row (no snapshot) resumes with ``origin="resumed_legacy"`` and
  persists what it built, so the next resume replays it.

Then the REAL graph end to end in rules mode (every stage, no LLM: the
pipeline-wide cost decision is pre-checked to ``rules``, so no agent reaches a
provider): triage follows the stored threshold (95 skips it with the recorded
reason, 40 runs it), and the deep gap/refinement stages follow the project flag.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (a database migrated to head, 0209
seeds ``aiq_gap_refinement`` ON) plus the stack's Redis and Mongo.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: The four read-only specialists and the mutating stage reach outside the
#: database (Splunk, Jira, an API spec): off here so the deep run stays local.
_SPECIALISTS_OFF = {
    "contract_validation", "log_intelligence", "regression_watchman",
    "change_ownership", "defect_commander", "async_decision_report_supersession",
}


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _Graph:
    """Stands in for the compiled graph where only persistence is under test."""

    def __init__(self, *, fail: bool = False, during=None) -> None:
        self.states: list[dict] = []
        self.fail = fail
        self.during = during

    async def ainvoke(self, state):
        self.states.append(dict(state))
        if self.during is not None:
            await self.during(state)
        if self.fail:
            raise ValueError("a stage failed")
        return {**state, "completed_stages": ["ingestion"], "errors": []}


@pytest.fixture
async def world(monkeypatch):
    _dsn()
    from app.agents import workflow
    from app.db import postgres as app_pg
    from app.models.postgres import (
        AgentPipelineRun,
        AgentStageResult,
        AppSetting,
        FeatureFlag,
        LaunchStatus,
        Project,
        TestCase,
        TestRun,
    )
    from app.services import feature_flags

    from app.db.mongo import get_mongo_db, reset_mongo_client

    await app_pg.dispose_engine_for_loop()
    # The motor client binds to the loop that created it; each test has its own.
    reset_mongo_client()
    try:
        await get_mongo_db().command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MongoDB is not reachable: {exc}")
    sessions = app_pg.AsyncSessionLocal
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as db:
        prior = await db.get(AppSetting, "ai_config")
        prior_value = dict(prior.value) if prior is not None and prior.value else None
        gap_row = (await db.execute(
            select(FeatureFlag).where(FeatureFlag.key == "aiq_gap_refinement")
        )).scalar_one_or_none()
        gap_prior = (
            (gap_row.enabled_global, gap_row.enabled_projects) if gap_row is not None else None
        )
        db.add(Project(id=project_id, name=f"w04b-{project_id.hex}", slug=f"w04b-{project_id.hex}"))
        await db.flush()
        db.add(TestRun(
            id=run_id, project_id=project_id, build_number=f"w04b-{run_id.hex[:8]}",
            status=LaunchStatus.FAILED, branch="main", primary_suite_name="checkout",
            total_tests=4, passed_tests=1, failed_tests=3, broken_tests=0,
            skipped_tests=0, unknown_tests=0, pass_rate=25.0,
        ))
        await db.flush()
        # "read timeout after 30s" is a rules-engine band of 65: under 95, over 40.
        for i in range(3):
            db.add(TestCase(
                id=uuid.uuid4(), test_run_id=run_id, test_name=f"test_checkout_{i}",
                suite_name="checkout", test_fingerprint=f"w04b{run_id.hex[:6]}{i:03d}",
                status="FAILED", error_message="read timeout after 30s",
            ))
        db.add(TestCase(
            id=uuid.uuid4(), test_run_id=run_id, test_name="test_checkout_ok",
            suite_name="checkout", test_fingerprint=f"w04b{run_id.hex[:6]}ok",
            status="PASSED",
        ))
        await db.commit()

    async def _quiet(*_args, **_kwargs):
        return None

    real_is_enabled = feature_flags.is_enabled

    async def _is_enabled(key, **kwargs):
        if key in _SPECIALISTS_OFF:
            return False
        return await real_is_enabled(key, **kwargs)

    monkeypatch.setattr(feature_flags, "is_enabled", _is_enabled)
    monkeypatch.setattr(workflow, "_persist_memory", _quiet)

    async def store_ai_config(**values: Any) -> None:
        async with sessions() as db:
            row = await db.get(AppSetting, "ai_config")
            if row is None:
                db.add(AppSetting(key="ai_config", value=dict(values)))
            else:
                row.value = {**(row.value or {}), **values}
            await db.commit()
        from app.services.ai_config_resolver import invalidate_ai_config_cache

        await invalidate_ai_config_cache()

    async def set_gap_flag(*, enabled_global: bool, enabled_projects: list[str] | None) -> None:
        async with sessions() as db:
            row = (await db.execute(
                select(FeatureFlag).where(FeatureFlag.key == "aiq_gap_refinement")
            )).scalar_one()
            row.enabled_global = enabled_global
            row.enabled_projects = enabled_projects
            await db.commit()

    try:
        yield {
            "sessions": sessions, "project": str(project_id), "run": str(run_id),
            "models": (AgentPipelineRun, AgentStageResult),
            "store_ai_config": store_ai_config, "set_gap_flag": set_gap_flag,
        }
    finally:
        async with sessions() as db:
            ids = select(AgentPipelineRun.id).where(AgentPipelineRun.test_run_id == run_id)
            await db.execute(delete(AgentStageResult).where(AgentStageResult.pipeline_run_id.in_(ids)))
            await db.execute(delete(AgentPipelineRun).where(AgentPipelineRun.test_run_id == run_id))
            await db.execute(delete(TestRun).where(TestRun.id == run_id))
            await db.execute(delete(Project).where(Project.id == project_id))
            row = await db.get(AppSetting, "ai_config")
            if prior_value is None and row is not None:
                await db.delete(row)
            elif row is not None:
                row.value = prior_value
            if gap_prior is not None:
                flag = (await db.execute(
                    select(FeatureFlag).where(FeatureFlag.key == "aiq_gap_refinement")
                )).scalar_one()
                flag.enabled_global, flag.enabled_projects = gap_prior
            await db.commit()
        from app.services.ai_config_resolver import invalidate_ai_config_cache

        await invalidate_ai_config_cache()
        reset_mongo_client()
        await app_pg.dispose_engine_for_loop()


async def _metadata(world, pipeline_run_id: str) -> dict:
    AgentPipelineRun, _ = world["models"]
    async with world["sessions"]() as db:
        row = await db.get(AgentPipelineRun, uuid.UUID(pipeline_run_id))
        return dict(row.execution_metadata or {}) if row else {}


async def _stages(world, pipeline_run_id: str) -> dict[str, Any]:
    _, AgentStageResult = world["models"]
    async with world["sessions"]() as db:
        rows = (await db.execute(
            select(AgentStageResult).where(AgentStageResult.pipeline_run_id == uuid.UUID(pipeline_run_id))
        )).scalars().all()
        return {row.stage_name: row for row in rows}


def _stage_plan(plan: dict, stage: str) -> dict:
    return next(item for item in plan["stages"] if item["stage"] == stage)


# ── P1: creation ────────────────────────────────────────────────────────────


async def test_creation_freezes_the_snapshot_and_the_gap_flag(world):
    from app.agents import workflow

    await world["store_ai_config"](ai_confidence_threshold=95, ai_timeout_seconds=5)
    deep_id, offline_id = str(uuid.uuid4()), str(uuid.uuid4())
    setup = await workflow._create_pipeline_run(deep_id, world["run"], world["project"], "deep")
    await workflow._create_pipeline_run(offline_id, world["run"], world["project"], "offline")

    deep = await _metadata(world, deep_id)
    snapshot = deep["ai_config_snapshot"]
    assert (snapshot["confidence_threshold"], snapshot["confidence_threshold_source"]) == (95, "ai_config")
    assert (snapshot["llm_timeout_seconds"], snapshot["llm_timeout_source"]) == (5, "ai_config")
    assert snapshot["origin"] == "pipeline_start"
    assert deep["gap_refinement_settings"] == {"enabled": True}       # the 0209 row is ON
    assert setup["ai_config_snapshot"] == snapshot
    assert setup["gap_refinement_settings"] == {"enabled": True}
    plan = deep["initial_workflow_plan"]
    assert plan["triage_threshold"] == 95
    assert plan["specialist_flags"]["gap_refinement"] is True
    assert _stage_plan(plan, "gap_detection")["planned"] is True

    offline = await _metadata(world, offline_id)
    assert offline["gap_refinement_settings"] == {"enabled": False}   # deep only
    assert "gap_refinement" not in offline["initial_workflow_plan"]["specialist_flags"]


async def test_the_gap_flag_off_for_this_project_only(world):
    from app.agents import workflow

    await world["set_gap_flag"](enabled_global=True, enabled_projects=[str(uuid.uuid4())])
    pipeline_id = str(uuid.uuid4())
    await workflow._create_pipeline_run(pipeline_id, world["run"], world["project"], "deep")

    metadata = await _metadata(world, pipeline_id)
    assert metadata["gap_refinement_settings"] == {"enabled": False}
    entry = _stage_plan(metadata["initial_workflow_plan"], "gap_detection")
    assert entry["planned"] is False
    assert entry["rationale"] == "AIQ gap/refinement feature flag is off for this project"


# ── P3 + P2 + P4: completion, claim, same-id resume ─────────────────────────


async def test_completion_claim_and_resume_keep_the_frozen_values(world, monkeypatch):
    from app.agents import workflow

    _, AgentStageResult = world["models"]
    await world["store_ai_config"](ai_confidence_threshold=95, ai_timeout_seconds=5)

    async def one_stage_fails(state):
        """One stage failed inside the graph: the run completes DEGRADED, so it
        stays resumable (the degraded-completed retry P3 exists for)."""
        async with world["sessions"]() as db:
            stage = (await db.execute(
                select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == uuid.UUID(state["pipeline_run_id"]),
                    AgentStageResult.stage_name == "flaky_sentinel",
                )
            )).scalar_one()
            stage.status = "failed"
            stage.started_at = datetime.now(timezone.utc)
            await db.commit()

    graph = _Graph(during=one_stage_fails)
    monkeypatch.setattr(workflow, "_compile_frozen_workflow", lambda _setup: graph)
    pipeline_id = str(uuid.uuid4())
    await workflow.run_deep_pipeline(pipeline_run_id=pipeline_id, create_if_missing=True,
                                     test_run_id=world["run"], project_id=world["project"])
    graph.during = None
    first = graph.states[-1]
    assert (first["confidence_threshold"], first["llm_timeout_seconds"]) == (95, 5)
    assert first["gap_refinement_enabled"] is True

    completed = await _metadata(world, pipeline_id)
    assert completed.get("stage_quality") == "degraded"
    # P3: the success branch rebuilt execution_metadata from its allowlist.
    assert completed["ai_config_snapshot"]["confidence_threshold"] == 95
    assert completed["gap_refinement_settings"] == {"enabled": True}

    # The admin moves both settings after the run started.
    await world["store_ai_config"](ai_confidence_threshold=40, ai_timeout_seconds=300)
    claimed = await workflow._claim_pipeline_resume(pipeline_id)
    assert claimed is not None
    assert claimed["ai_config_snapshot"]["confidence_threshold"] == 95       # P2
    assert claimed["ai_config_snapshot"]["llm_timeout_seconds"] == 5
    assert claimed["gap_refinement_settings"] == {"enabled": True}
    assert claimed["defect_commander_settings"] == {"enabled": False}

    # P4: a same-id resume replays the frozen values and the same plan.
    async with world["sessions"]() as db:
        AgentPipelineRun, _ = world["models"]
        row = await db.get(AgentPipelineRun, uuid.UUID(pipeline_id))
        row.status = "failed"
        await db.commit()
    await workflow.run_deep_pipeline(pipeline_run_id=pipeline_id)
    resumed = graph.states[-1]
    assert (resumed["confidence_threshold"], resumed["llm_timeout_seconds"]) == (95, 5)
    assert resumed["ai_config_snapshot"]["frozen_at"] == first["ai_config_snapshot"]["frozen_at"]
    assert resumed["initial_workflow_plan"]["plan_sha256"] == first["initial_workflow_plan"]["plan_sha256"]


async def test_the_claim_returns_defect_commander_settings(world, monkeypatch):
    """§2.6-4: a resumed deep pipeline kept ``defect_commander_enabled=False``
    although its plan said planned (the key was missing from the claim)."""
    from app.agents import workflow
    from app.services import feature_flags

    wrapped = feature_flags.is_enabled

    async def _on(key, **kwargs):
        if key == "defect_commander":
            return True
        return await wrapped(key, **kwargs)

    monkeypatch.setattr(feature_flags, "is_enabled", _on)
    monkeypatch.setattr(workflow, "_compile_frozen_workflow", lambda _setup: _Graph(fail=True))
    pipeline_id = str(uuid.uuid4())
    with pytest.raises(ValueError):
        await workflow.run_deep_pipeline(pipeline_run_id=pipeline_id, create_if_missing=True,
                                         test_run_id=world["run"], project_id=world["project"])

    claimed = await workflow._claim_pipeline_resume(pipeline_id)
    assert claimed is not None
    assert claimed["defect_commander_settings"] == {"enabled": True}


# ── P5: a pre-W04 row ───────────────────────────────────────────────────────


async def test_a_legacy_row_resumes_as_resumed_legacy_and_persists_it(world, monkeypatch):
    from app.agents import workflow

    AgentPipelineRun, _ = world["models"]
    await world["store_ai_config"](ai_confidence_threshold=70, ai_timeout_seconds=45)
    failing = _Graph(fail=True)
    monkeypatch.setattr(workflow, "_compile_frozen_workflow", lambda _setup: failing)
    pipeline_id = str(uuid.uuid4())
    with pytest.raises(ValueError):
        await workflow.run_offline_pipeline(
            test_run_id=world["run"], project_id=world["project"], build_number="b-1",
            workflow_type="offline", pipeline_run_id=pipeline_id, create_if_missing=True,
        )
    # Strip what a pre-W04 build never wrote.
    async with world["sessions"]() as db:
        row = await db.get(AgentPipelineRun, uuid.UUID(pipeline_id))
        metadata = dict(row.execution_metadata or {})
        metadata.pop("ai_config_snapshot", None)
        metadata.pop("gap_refinement_settings", None)
        row.execution_metadata = metadata
        await db.commit()

    with pytest.raises(ValueError):
        await workflow.run_offline_pipeline(build_number="b-1", workflow_type="offline",
                                            pipeline_run_id=pipeline_id)
    legacy = failing.states[-1]["ai_config_snapshot"]
    assert legacy["origin"] == "resumed_legacy"
    assert (legacy["confidence_threshold"], legacy["llm_timeout_seconds"]) == (70, 45)
    persisted = await _metadata(world, pipeline_id)
    assert persisted["ai_config_snapshot"] == legacy
    assert persisted["gap_refinement_settings"] == {"enabled": False}

    # The NEXT resume replays it, even after the admin moved the setting.
    await world["store_ai_config"](ai_confidence_threshold=99, ai_timeout_seconds=600)
    with pytest.raises(ValueError):
        await workflow.run_offline_pipeline(build_number="b-1", workflow_type="offline",
                                            pipeline_run_id=pipeline_id)
    assert failing.states[-1]["ai_config_snapshot"] == legacy


# ── the real graph, rules mode ──────────────────────────────────────────────


def _route_decisions(metadata: dict) -> list[dict]:
    return [
        d for d in metadata.get("workflow_route_decisions") or []
        if str(d.get("decision_point", "")).startswith("route_after_summary")
    ]


@pytest.mark.parametrize("threshold,triaged", [(95, False), (40, True)])
async def test_the_real_offline_graph_routes_triage_on_the_stored_threshold(world, threshold, triaged):
    from app.agents import workflow

    await world["store_ai_config"](ai_confidence_threshold=threshold, analysis_mode="rules")
    state = await workflow.run_offline_pipeline(
        test_run_id=world["run"], project_id=world["project"], build_number="b-real",
        workflow_type="offline", cost_budget_mode_override="rules",
    )
    pipeline_id = state["pipeline_run_id"]
    metadata = await _metadata(world, pipeline_id)
    stages = await _stages(world, pipeline_id)

    assert metadata["initial_workflow_plan"]["triage_threshold"] == threshold
    assert metadata["ai_config_snapshot"]["confidence_threshold"] == threshold
    assert stages["root_cause_analysis"].status == "completed"
    scores = sorted(int(a.get("confidence_score") or 0) for a in state["analyses"].values())
    assert scores and max(scores) < 95 and min(scores) >= 40, scores
    [decision] = _route_decisions(metadata)
    if triaged:
        assert decision["chosen"] == "triage"
        assert decision["rationale"] == f"{len(scores)} analyses meet confidence ≥ 40"
        assert stages["triage"].status == "completed"
    else:
        assert decision["chosen"] == "end"
        assert decision["rationale"] == "no analyses above confidence threshold 95 — skipping triage"
        assert stages["triage"].status == "skipped"
        assert "triage" not in (state.get("completed_stages") or [])


@pytest.mark.parametrize("gap_on", [True, False])
async def test_the_real_deep_graph_runs_the_gap_stages_when_the_project_flag_is_on(world, gap_on):
    from app.agents import workflow
    from app.agents.decision_report_agent import render_decision_markdown

    await world["store_ai_config"](ai_confidence_threshold=95, analysis_mode="rules")
    if gap_on:
        await world["set_gap_flag"](enabled_global=True, enabled_projects=None)
    else:
        await world["set_gap_flag"](enabled_global=True, enabled_projects=[str(uuid.uuid4())])

    state = await workflow.run_deep_pipeline(
        test_run_id=world["run"], project_id=world["project"], build_number="b-deep",
        cost_budget_mode_override="rules",
    )
    pipeline_id = state["pipeline_run_id"]
    stages = await _stages(world, pipeline_id)
    metadata = await _metadata(world, pipeline_id)
    markdown = render_decision_markdown(state.get("decision_intelligence") or {})

    [decision] = _route_decisions(metadata)
    assert decision["chosen"] == "failure_clustering"
    assert stages["triage"].status == "skipped"
    for stage in ("failure_clustering", "flaky_sentinel", "release_risk", "decision_report"):
        assert stages[stage].status == "completed", (stage, stages[stage].status)
    for stage in ("gap_detection", "report_refinement"):
        row = stages[stage]
        if gap_on:
            assert row.status == "completed", (stage, row.status, row.skipped_reason)
        else:
            assert row.status == "skipped", (stage, row.status)
            assert "feature flag is off for this project" in (row.skipped_reason or "")
    if gap_on:
        failed = len(state["failed_test_ids"])
        assert f"**Analysis coverage:** {failed}/{failed} failures analysed" in markdown
    else:
        assert "**Analysis coverage:** not recorded" in markdown
