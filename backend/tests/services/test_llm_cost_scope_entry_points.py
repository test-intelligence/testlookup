"""Re-audit M13: the entry points that run LLM work charge it to their project.

The per-call reservation (llm_cost_reservation.reserve) only knows whose cap to
hold against through ``cost_budget_scope``. A pipeline or report that does not
set it runs every call unreserved -- the cap would be back to check-then-act
for that path with every test still green. Each test records the scope the
graph (or agent) actually sees when it is invoked.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.llm_cost_reservation import current_cost_scope


class _Stop(Exception):
    """Ends the run once the scope has been observed."""


def _setup(run_id: str, project_id: str, workflow_type: str, stages: list[str]) -> dict:
    return {
        "test_run_id": run_id,
        "project_id": project_id,
        "workflow_type": workflow_type,
        "initial_workflow_plan": {"stages": stages},
        "cluster_child_settings": {},
        "async_decision_report_supersession_enabled": False,
        "contract_agent_settings": {"enabled": False},
        "defect_commander_settings": {"enabled": False},
        "log_intelligence_settings": {"enabled": False},
        "regression_watchman_settings": {"enabled": False},
        "change_ownership_settings": {"enabled": False},
        "resume_attempt": 0,
    }


def _unblocked(monkeypatch, workflow, setup):
    from app.services.llm_cost_budget import CapDecision

    monkeypatch.setattr(workflow, "_create_pipeline_run", AsyncMock(return_value=setup))
    monkeypatch.setattr(workflow, "_load_checkpoint", AsyncMock(return_value=None))
    monkeypatch.setattr(
        workflow,
        "_resolve_analysis_mode_snapshot",
        AsyncMock(return_value={"requested": "llm", "resolved": "llm", "resolution_reason": "configured"}),
    )
    monkeypatch.setattr(workflow, "_persist_execution_context", AsyncMock())
    monkeypatch.setattr(workflow, "_mark_pipeline_done", AsyncMock())
    monkeypatch.setattr(workflow, "emit_event", AsyncMock())
    monkeypatch.setattr(
        "app.services.llm_cost_budget.check_and_apply_cap",
        AsyncMock(return_value=CapDecision(action="UNLIMITED")),
    )


def _recording_graph(seen: list):
    async def ainvoke(state):
        seen.append(current_cost_scope())
        raise _Stop()

    return SimpleNamespace(ainvoke=ainvoke)


@pytest.mark.asyncio
async def test_the_standard_pipeline_graph_runs_inside_its_projects_scope(monkeypatch):
    from app.agents import workflow

    run_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    _unblocked(monkeypatch, workflow, _setup(run_id, project_id, "offline", ["ingestion", "summary"]))
    seen: list = []
    monkeypatch.setattr(workflow, "_offline_app", _recording_graph(seen))

    with pytest.raises(_Stop):
        await workflow.run_offline_pipeline(test_run_id=run_id, project_id=project_id, build_number="1")
    assert seen == [project_id]
    assert current_cost_scope() is None  # and it does not leak out


@pytest.mark.asyncio
async def test_the_deep_pipeline_graph_runs_inside_its_projects_scope(monkeypatch):
    from app.agents import workflow

    run_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    _unblocked(monkeypatch, workflow, _setup(run_id, project_id, "deep", ["ingestion", "failure_clustering"]))
    seen: list = []
    monkeypatch.setattr(workflow, "_deep_app", _recording_graph(seen))

    with pytest.raises(_Stop):
        await workflow.run_deep_pipeline(test_run_id=run_id, project_id=project_id, build_number="1")
    assert seen == [project_id]


@pytest.mark.asyncio
async def test_the_run_compare_report_runs_inside_its_projects_scope(monkeypatch):
    from app.services import run_compare_ai_service as service

    project_id = uuid.uuid4()
    seen: list = []

    class Agent:
        async def generate(self, payload):
            seen.append(current_cost_scope())
            return {"fallback_used": False}

    monkeypatch.setattr(service, "mark_queued", AsyncMock())
    monkeypatch.setattr(service, "_get_row", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "RunCompareAgent", Agent)

    await service.generate_and_save_report(
        SimpleNamespace(flush=AsyncMock()),
        project_id=project_id,
        left_run_id=uuid.uuid4(),
        right_run_id=uuid.uuid4(),
        suite_name=None,
        compare_payload={"scope": "run"},
        cost_budget_prechecked=True,
    )
    assert seen == [str(project_id)]
    assert current_cost_scope() is None


# ── R-B45-1: every other priced entry point that knows its project ───────────


class _ScopeLLM:
    """What get_llm() returns here: records the scope each call runs in."""

    def __init__(self, seen: list, content: str):
        self.seen, self.content = seen, content

    async def ainvoke(self, *args, **kwargs):
        self.seen.append(current_cost_scope())
        return SimpleNamespace(content=self.content, usage_metadata=None, response_metadata={})

    def bind(self, *args, **kwargs):
        return self

    def with_structured_output(self, *args, **kwargs):
        return self


def _get_llm(seen: list, content: str = "{}"):
    async def get_llm(*args, **kwargs):
        return _ScopeLLM(seen, content)

    return get_llm


def test_a_scope_without_a_project_keeps_the_outer_one():
    """A helper that does not know its project must not switch charging off."""
    from app.services.llm_cost_reservation import cost_budget_scope

    with cost_budget_scope("p1"):
        with cost_budget_scope(None):
            assert current_cost_scope() == "p1"
        with cost_budget_scope("p2"):
            assert current_cost_scope() == "p2"
        assert current_cost_scope() == "p1"
    assert current_cost_scope() is None


@pytest.mark.asyncio
async def test_project_chat_and_its_history_compression_run_inside_the_projects_scope(monkeypatch):
    import asyncio

    from app.agents import conversation as conv

    seen: list = []
    compressed: list = []
    monkeypatch.setattr(conv, "get_llm", _get_llm(seen, "the reply"))
    agent = conv.ConversationAgent()

    async def history(session_id):
        return [], ""

    async def nothing(*args, **kwargs):
        return None

    async def no_context(*args, **kwargs):
        return "", []

    async def name(project_id):
        return "Payments"

    async def compress(session_id):
        compressed.append(current_cost_scope())

    monkeypatch.setattr(agent, "_load_history", history)
    monkeypatch.setattr(agent, "_save_message", nothing)
    monkeypatch.setattr(agent, "_touch_session", nothing)
    monkeypatch.setattr(agent, "_retrieve_context", no_context)
    monkeypatch.setattr(agent, "_fetch_bound_report_context", no_context)
    monkeypatch.setattr(agent, "_fetch_project_name", name)
    monkeypatch.setattr(agent, "_tool_loop_enabled", lambda: False)
    monkeypatch.setattr(agent, "_maybe_compress_history", compress)

    project_id = str(uuid.uuid4())
    result = await agent.chat("s-1", "why did checkout fail?", "u-1", project_id=project_id)
    for _ in range(5):
        await asyncio.sleep(0)  # the compression task
    assert result["reply"] == "the reply"
    assert seen == [project_id]
    assert compressed == [project_id]
    assert current_cost_scope() is None

    # An "all projects" chat has no project to charge.
    await agent.chat("s-2", "hello", "u-1", project_id=None)
    assert seen == [project_id, None]


@pytest.mark.asyncio
async def test_the_investigator_graph_runs_inside_its_projects_scope(monkeypatch):
    from app.agents.investigator import workflow as investigator

    project_id = str(uuid.uuid4())
    seen: list = []
    monkeypatch.setattr(investigator, "_investigator_app", _recording_graph(seen))

    with pytest.raises(_Stop):
        await investigator._run_investigator_graph({"project_id": project_id})
    assert seen == [project_id]


@pytest.mark.asyncio
async def test_defect_promotion_writes_its_ticket_inside_the_runs_project_scope(monkeypatch):
    from app.services import defect_promotion_service as promotion

    project_id = uuid.uuid4()
    seen: list = []
    rows = iter([
        SimpleNamespace(member_test_ids=[], cluster_id="c-1"),  # the cluster
        SimpleNamespace(project_id=project_id),                 # its run
        None,                                                   # no deep finding
    ])

    class _Row:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    async def execute(*args, **kwargs):
        return _Row(next(rows))

    async def ticket(cluster, analyses):
        seen.append(current_cost_scope())
        raise _Stop()

    monkeypatch.setattr(promotion, "_generate_jira_content", ticket)
    with pytest.raises(_Stop):
        await promotion.get_defect_candidate(str(uuid.uuid4()), "c-1", SimpleNamespace(execute=execute))
    assert seen == [str(project_id)]


@pytest.mark.asyncio
async def test_the_test_case_ai_tools_run_inside_the_callers_project_scope(monkeypatch):
    from app.services import test_case_ai_agent as ai

    project_id = str(uuid.uuid4())
    seen: list = []
    monkeypatch.setattr(ai, "get_llm", _get_llm(seen, '{"test_cases": []}'))

    await ai.ai_generate_test_cases("login works", project_id=project_id)
    await ai.ai_review_test_case({"title": "t"}, project_id=project_id)
    await ai.ai_analyze_coverage("login works", [], project_id=project_id)
    await ai.ai_generate_strategy("a payments service", project_id=project_id)
    await ai.ai_optimize_plan([], "", project_id=project_id)
    assert seen == [project_id] * 5


@pytest.mark.parametrize("task_name", ["generate_ai_test_cases_task", "generate_ai_strategy_task"])
def test_the_test_case_ai_celery_tasks_run_inside_their_projects_scope(monkeypatch, task_name):
    """They called the async-only tool with ``.invoke``, which raises
    NotImplementedError: they never produced anything. Now they await it,
    charged to the task's project."""
    import app.db.postgres as postgres
    from app.services import test_case_ai_agent as ai
    from app.worker import tasks

    project_id = str(uuid.uuid4())
    seen: list = []
    monkeypatch.setattr(ai, "get_llm", _get_llm(seen, '{"test_cases": []}'))

    def no_database():
        raise _Stop()

    monkeypatch.setattr(postgres, "AsyncSessionLocal", no_database)
    task = getattr(tasks, task_name)
    args = (
        ("checkout requirements", project_id, str(uuid.uuid4()))
        if task_name == "generate_ai_test_cases_task"
        else (project_id, str(uuid.uuid4()), "a payments service")
    )
    try:
        task.run(*args)
    except Exception:  # noqa: BLE001 -- the database is refused on purpose
        pass
    assert seen == [project_id]


@pytest.mark.asyncio
async def test_grounded_generation_runs_inside_its_projects_scope(monkeypatch):
    from app.services import rag_generation_service as rag
    from app.services import test_case_ai_agent as ai

    project_id = uuid.uuid4()
    seen: list = []
    monkeypatch.setattr(ai, "get_llm", _get_llm(seen, '{"test_cases": [{"title": "x"}]}'))

    await rag._call_llm_generate("prompt", None, project_id=project_id)
    assert seen == [str(project_id)]


@pytest.mark.asyncio
async def test_the_faithfulness_judge_runs_inside_the_cases_project_scope(monkeypatch):
    import app.services.llm_factory as factory
    from app.services import rag_faithfulness_service as faithfulness

    project_id = str(uuid.uuid4())
    seen: list = []

    async def enabled(db=None):
        return True

    async def single_prompt():
        return "ollama"

    monkeypatch.setattr(faithfulness, "_feature_enabled", enabled)
    monkeypatch.setattr(faithfulness, "_resolve_backend", single_prompt)
    recording = _get_llm(seen, "SCORE: 0.9\nREASON: grounded")
    monkeypatch.setattr(faithfulness, "get_llm", recording, raising=False)
    monkeypatch.setattr(factory, "get_llm", recording)

    await faithfulness.evaluate("the case", ["the citation"], project_id=project_id)
    assert seen == [project_id]


@pytest.mark.asyncio
async def test_the_retro_narrative_runs_inside_its_projects_scope(monkeypatch):
    import app.services.llm_factory as factory
    from app.core.config import settings
    from app.services import retro_digest_service as retro

    project_id = uuid.uuid4()
    seen: list = []

    async def enabled(db=None):
        return True

    async def digest(db, **kwargs):
        return {"pass_rate": 90, "runs_total": 3, "top_clusters": []}

    async def zero(*args, **kwargs):
        return 0

    monkeypatch.setattr(retro, "_feature_enabled", enabled)
    monkeypatch.setattr(retro, "generate_digest", digest)
    monkeypatch.setattr(retro, "_count_released_flaky", zero)
    monkeypatch.setattr(retro, "_count_new_regressions", zero)
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(factory, "get_llm", _get_llm(seen, "A calm week."))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: "Payments")))

    try:
        await retro.generate_weekly_retro(db, project_id)
    except Exception:  # noqa: BLE001 -- only the narrative's scope is asserted
        pass
    assert seen == [str(project_id)]


@pytest.mark.asyncio
async def test_on_demand_run_summaries_run_inside_the_runs_project_scope(monkeypatch):
    from app.services import run_intelligence_service as intelligence
    from app.services import summary_renderer

    project_id = uuid.uuid4()
    seen: list = []

    async def render(assembled):
        seen.append(current_cost_scope())
        raise _Stop()

    monkeypatch.setattr(summary_renderer, "render_developer_summary", render)
    run = SimpleNamespace(
        project_id=project_id, build_number="1", branch="main",
        total_tests=1, failed_tests=0, pass_rate=100.0,
    )
    result = SimpleNamespace(
        scalar_one_or_none=lambda: run,
        scalars=lambda: SimpleNamespace(all=lambda: []),
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    variant = await intelligence._generate_and_cache_mode_variant(uuid.uuid4(), "developer", {}, None, db)
    assert variant is None  # the renderer "failed"; only its scope matters
    assert seen == [str(project_id)]


@pytest.mark.asyncio
async def test_llm_triage_runs_inside_the_tests_project_scope(monkeypatch):
    from app.services import agent as triage
    from app.services import analysis_router

    project_id = str(uuid.uuid4())
    seen: list = []

    async def run_triage_agent(**kwargs):
        seen.append(current_cost_scope())
        return {"failure_category": "UNKNOWN", "confidence": 0.1}

    monkeypatch.setattr(triage, "run_triage_agent", run_triage_agent)
    await analysis_router._classify_llm({"project_id": project_id, "test_case_id": "t-1"}, None, None)
    assert seen == [project_id]


# ── R-B45-R2-2: the agents the API runs OUTSIDE the pipeline graph ───────────


class _Rows:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.value or []))


class _Session:
    def __init__(self, values):
        self.values = iter(values)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return _Rows(next(self.values))


@pytest.mark.asyncio
async def test_the_on_demand_regression_watchman_runs_inside_the_runs_project_scope(monkeypatch):
    """POST /agents/regression-watch ran its priced classify call unreserved."""
    from app.agents import regression_watchman as watchman

    run_project, passed_project = uuid.uuid4(), str(uuid.uuid4())
    seen: list = []
    cluster = SimpleNamespace(
        cluster_id="c-1", label="l", size=2, member_test_ids=["t-1"], representative_error="boom",
    )
    # run's project, then clusters, then analyses
    monkeypatch.setattr(watchman, "AsyncSessionLocal", lambda: _Session([run_project, [cluster], []]))

    async def classify(self, state):
        seen.append(current_cost_scope())
        return {"c-1": {"classification": "new_regression"}}

    monkeypatch.setattr(watchman._StandaloneWatchman, "_classify", classify)
    result = await watchman.run_regression_watchman(str(uuid.uuid4()), passed_project)
    assert result == {"c-1": {"classification": "new_regression"}}
    # charged to the project the RUN belongs to, read from the run
    assert seen == [str(run_project)]
    assert current_cost_scope() is None


@pytest.mark.asyncio
async def test_the_on_demand_regression_watchman_llm_call_is_reserved(monkeypatch):
    """The call itself, not only the agent method, sees the scope."""
    from app.agents import regression_watchman as watchman
    from app.core.config import settings

    run_project = uuid.uuid4()
    seen: list = []
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(watchman, "get_llm", _get_llm(seen, '{"c-1": {"classification": "new_regression"}}'))
    monkeypatch.setattr(watchman, "AsyncSessionLocal", lambda: _Session([run_project, [], []]))

    async def classify(self, state):
        clusters = [{"cluster_id": "c-1", "label": "l"}]
        return await self._llm_classify({"c-1": {}}, clusters, {})

    monkeypatch.setattr(watchman._StandaloneWatchman, "_classify", classify)
    await watchman.run_regression_watchman(str(uuid.uuid4()), str(uuid.uuid4()))
    assert seen == [str(run_project)]


@pytest.mark.asyncio
async def test_the_on_demand_defect_commander_runs_inside_its_projects_scope(monkeypatch):
    """POST /agents/defect-command writes Jira content with a priced LLM call."""
    from app.agents import defect_commander as commander

    project_id = str(uuid.uuid4())
    seen: list = []

    async def promote(self, state):
        seen.append(current_cost_scope())
        return {"ok": True}

    monkeypatch.setattr(commander._StandaloneCommander, "_promote", promote)
    assert await commander.run_defect_commander("c-1", str(uuid.uuid4()), project_id) == {"ok": True}
    assert seen == [project_id]
    assert current_cost_scope() is None
