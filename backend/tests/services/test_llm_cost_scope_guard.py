"""R-B45-R2-2 guard: every get_llm() caller is charged to a project, or is a
reviewed no-project caller.

The per-call reservation only holds a call against a cap when a
``cost_budget_scope`` is in force. ``POST /agents/regression-watch`` ran a
priced call with none while every test was green, because each entry-point
test pins one path and nothing listed the paths. This walks the AST of
``backend/app`` for every call to ``llm_factory.get_llm`` (any import alias)
and requires each caller to be:

* lexically inside ``with cost_budget_scope(...)``; or
* in :data:`SCOPED`, naming the entry-point test(s) that prove the scope is in
  force when it runs; or
* in :data:`NO_PROJECT`, with the reason there is no project to charge.

A new caller fails until someone decides which. Agents the API runs outside
the pipeline graph (the ``_Standalone*`` runners) must open the scope
themselves: that is the class of the miss.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"

_ENTRY = "tests/services/test_llm_cost_scope_entry_points.py"
_PIPELINE = (
    f"{_ENTRY}::test_the_standard_pipeline_graph_runs_inside_its_projects_scope",
    f"{_ENTRY}::test_the_deep_pipeline_graph_runs_inside_its_projects_scope",
)
_TRIAGE = f"{_ENTRY}::test_llm_triage_runs_inside_the_tests_project_scope"
_CHAT = f"{_ENTRY}::test_project_chat_and_its_history_compression_run_inside_the_projects_scope"
_INVESTIGATOR = f"{_ENTRY}::test_the_investigator_graph_runs_inside_its_projects_scope"
_PROMOTION = (
    f"{_ENTRY}::test_defect_promotion_writes_its_ticket_inside_the_runs_project_scope",
    f"{_ENTRY}::test_the_on_demand_defect_commander_runs_inside_its_projects_scope",
)
_TC_AI = (
    f"{_ENTRY}::test_the_test_case_ai_tools_run_inside_the_callers_project_scope",
    f"{_ENTRY}::test_the_test_case_ai_celery_tasks_run_inside_their_projects_scope",
)

#: caller (path relative to app/ :: qualname) -> the tests that pin its scope.
SCOPED: dict[str, tuple[str, ...]] = {
    "agents/anomaly_agent.py::AnomalyDetectionAgent._generate_summary": _PIPELINE,
    "agents/release_risk_agent.py::ReleaseRiskAgent._get_llm_reasoning": _PIPELINE,
    "agents/summary_agent.py::SummaryAgent._generate_tiered_report": _PIPELINE,
    "agents/summary_agent.py::SummaryAgent._generate_structured_report": _PIPELINE,
    "agents/regression_watchman.py::RegressionWatchman._llm_classify": _PIPELINE + (
        f"{_ENTRY}::test_the_on_demand_regression_watchman_runs_inside_the_runs_project_scope",
        f"{_ENTRY}::test_the_on_demand_regression_watchman_llm_call_is_reserved",
    ),
    # Chat with a project is charged; an "all projects" chat has no project
    # (asserted in the same test).
    "agents/conversation.py::ConversationAgent._chat": (_CHAT,),
    "agents/conversation.py::ConversationAgent._run_tool_loop": (_CHAT,),
    "agents/conversation.py::ConversationAgent._maybe_compress_history": (_CHAT,),
    "agents/fixer/pipeline.py::generate_candidate_patch": (
        "tests/test_fixer_pipeline_e2e.py::test_patch_generation_runs_inside_the_projects_llm_cost_scope",
    ),
    "agents/investigator/hypotheses.py::HypothesisAgent._weigh_with_llm": (_INVESTIGATOR,),
    "agents/investigator/synthesis.py::SynthesisAgent._narrative_with_llm": (_INVESTIGATOR,),
    "agents/run_compare_agent.py::RunCompareAgent.generate": (
        f"{_ENTRY}::test_the_run_compare_report_runs_inside_its_projects_scope",
    ),
    "services/agent.py::run_triage_agent": (_TRIAGE,) + _PIPELINE,
    # FastClassifier is the fast path inside run_triage_agent and the graph's
    # analysis agent.
    "services/training/classifier.py::FastClassifier.classify_with_outcome": (_TRIAGE,) + _PIPELINE,
    "services/defect_promotion_service.py::get_llm": _PROMOTION,
    "services/defect_promotion_service.py::_generate_jira_content": _PROMOTION,
    "services/rag_faithfulness_service.py::_evaluate_via_ollama": (
        f"{_ENTRY}::test_the_faithfulness_judge_runs_inside_the_cases_project_scope",
    ),
    "services/retro_digest_service.py::_compose_narrative": (
        f"{_ENTRY}::test_the_retro_narrative_runs_inside_its_projects_scope",
    ),
    "services/summary_renderer.py::render_developer_summary": (
        f"{_ENTRY}::test_on_demand_run_summaries_run_inside_the_runs_project_scope",
    ),
    "services/summary_renderer.py::render_manager_summary": (
        f"{_ENTRY}::test_on_demand_run_summaries_run_inside_the_runs_project_scope",
    ),
    "services/test_case_ai_agent.py::generate_test_cases_tool": _TC_AI,
    "services/test_case_ai_agent.py::review_test_quality_tool": _TC_AI,
    "services/test_case_ai_agent.py::analyze_coverage_gaps_tool": _TC_AI,
    "services/test_case_ai_agent.py::generate_test_strategy_tool": _TC_AI,
    "services/test_case_ai_agent.py::optimize_test_plan_tool": _TC_AI,
}

#: caller -> why there is no project to charge. Reviewed; keep it short.
NO_PROJECT: dict[str, str] = {
    "services/training/evaluator.py::ModelEvaluator._run_classifier": (
        "the admin training evaluator scores a candidate model on the golden "
        "set; it belongs to no project and has no project cap"
    ),
    "services/prompt_eval_recordings.py::_record_with_llm": (
        "the M16 prompt-eval recorder CLI, run by a developer against a "
        "configured model; no project"
    ),
}


def _is_scope(node: ast.AST) -> bool:
    if not isinstance(node, (ast.With, ast.AsyncWith)):
        return False
    for item in node.items:
        call = item.context_expr
        if isinstance(call, ast.Call):
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if name == "cost_budget_scope":
                return True
    return False


def _get_llm_callers() -> dict[str, bool]:
    """caller -> is every call in it lexically inside a cost_budget_scope."""
    callers: dict[str, bool] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {"get_llm"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("llm_factory"):
                aliases |= {a.asname or a.name for a in node.names if a.name == "get_llm"}
        rel = path.relative_to(APP).as_posix()

        def visit(node: ast.AST, qual: list[str], scoped: bool) -> None:
            for child in ast.iter_child_nodes(node):
                child_qual, child_scoped = qual, scoped or _is_scope(child)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    child_qual = qual + [child.name]
                if isinstance(child, ast.Call):
                    func = child.func
                    name = func.id if isinstance(func, ast.Name) else (
                        func.attr if isinstance(func, ast.Attribute) else None
                    )
                    if name in aliases:
                        key = f"{rel}::{'.'.join(qual) or '<module>'}"
                        callers[key] = callers.get(key, True) and scoped
                visit(child, child_qual, child_scoped)

        visit(tree, [], False)
    return callers


def _test_exists(ref: str) -> bool:
    file, _, name = ref.partition("::")
    path = BACKEND / file
    if not path.exists():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in ast.walk(tree)
    )


def test_the_walk_finds_the_callers_it_must() -> None:
    callers = _get_llm_callers()
    # control: an aliased import and a method are both seen
    assert "services/defect_promotion_service.py::get_llm" in callers
    assert "agents/regression_watchman.py::RegressionWatchman._llm_classify" in callers
    assert len(callers) >= 20


def test_every_get_llm_caller_is_scoped_or_a_reviewed_no_project_caller() -> None:
    callers = _get_llm_callers()
    unreviewed = sorted(
        caller for caller, lexically_scoped in callers.items()
        if not lexically_scoped and caller not in SCOPED and caller not in NO_PROJECT
    )
    assert not unreviewed, (
        "get_llm() callers with no cost_budget_scope and no review. Open the scope "
        "at the entry point (and pin it in test_llm_cost_scope_entry_points.py), "
        f"or add a NO_PROJECT reason: {unreviewed}"
    )


def test_the_registry_names_no_caller_that_is_gone() -> None:
    callers = _get_llm_callers()
    stale = sorted((set(SCOPED) | set(NO_PROJECT)) - set(callers))
    assert not stale, stale
    assert not set(SCOPED) & set(NO_PROJECT)


def test_every_scoped_caller_names_tests_that_exist() -> None:
    for caller, refs in SCOPED.items():
        assert refs, caller
        missing = [ref for ref in refs if not _test_exists(ref)]
        assert not missing, (caller, missing)


def test_every_no_project_reason_is_a_reason() -> None:
    assert all(len(reason.split()) >= 8 for reason in NO_PROJECT.values())


def _standalone_runners() -> list[tuple[str, ast.AST]]:
    """Module functions that run a ``_Standalone*`` agent outside the graph."""
    runners = []
    for path in sorted((APP / "agents").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id.startswith("_Standalone")
                for n in ast.walk(fn)
            ):
                runners.append((f"{path.relative_to(APP).as_posix()}::{fn.name}", fn))
    return runners


def test_every_standalone_agent_runner_opens_its_own_cost_scope() -> None:
    """An agent run outside the pipeline graph has no scope unless it opens one.

    Both known runners (Regression Watchman, Defect Commander) make priced
    calls; the scope must wrap the agent's work in the runner itself so every
    caller of it is charged.
    """
    runners = _standalone_runners()
    names = {name for name, _ in runners}
    assert {
        "agents/regression_watchman.py::run_regression_watchman",
        "agents/defect_commander.py::run_defect_commander",
    } <= names
    for name, fn in runners:
        scoped_calls = [
            call for w in ast.walk(fn) if _is_scope(w)
            for call in ast.walk(w)
            if isinstance(call, ast.Await)
        ]
        assert scoped_calls, f"{name} runs an agent outside the graph with no cost_budget_scope around it"
