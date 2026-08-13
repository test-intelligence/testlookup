"""LangGraph workflow for the hypothesis-loop Investigator (Agentic plan AI-1).

Graph topology (mirrors the chassis conventions in ``app/agents/workflow.py``:
parallel fan-out, reducer keys, singleton agents, per-stage tracking rows):

                ┌─ hypothesis_infra ────────┐
                ├─ hypothesis_commit ───────┤
    plan ───────┼─ hypothesis_environment ──┼──► investigator_synthesis ─ END
                ├─ hypothesis_known_flaky ──┤
                └─ hypothesis_regression ───┘

* **plan** gathers the deterministic evidence bundle ONCE (small, fast
  queries — baseline selection reused from the PR-comment service, run
  compare, failure/category counts, infra rule-pack keyword matches, the
  known-flaky set, failure clusters, memory recall for top flaky
  offenders) so the five hypothesis nodes are pure consumers.
* **Budgets** — wall-clock deadline snapshot in state (checked between
  nodes), LLM-call/token budgets enforced inside each node's single
  bounded call. Exceeding budgets leaves honest ``inconclusive`` leftovers
  and a budget note in the verdict narrative; the investigation still
  finishes ``completed``.
* **Cancel** — cooperative: the cancel endpoint sets
  ``AgentInvestigation.cancel_requested``; every node checks the flag on
  entry and the runner finalizes ``status="cancelled"``.
* **Ledger (AI-3)** — every terminal outcome (completed / cancelled /
  failed) writes an ``agent_runs`` row + a durable Mongo mirror via
  ``agent_investigation_service.record_agent_run``.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, cast

import structlog
from langgraph.graph import END, StateGraph
from sqlalchemy import select, update

from app.agents.investigator.hypotheses import (
    CommitHypothesisAgent,
    EnvironmentHypothesisAgent,
    InfraHypothesisAgent,
    KnownFlakyHypothesisAgent,
    RegressionHypothesisAgent,
)
from app.agents.investigator.persistence import (
    bounded_accounting_cost,
    bounded_accounting_int,
    is_cancel_requested,
    reconcile_outstanding_budget_ledger,
    set_investigation_status,
)
from app.agents.investigator.state import InvestigationState
from app.agents.investigator.synthesis import SynthesisAgent
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    AgentChildDispatchOutbox,
    AgentInvestigation,
    AgentPipelineRun,
    AgentPolicy,
    AgentStageResult,
    AIAnalysis,
    FailureCluster,
    TestCase,
    TestRun,
)
from app.services.pipeline_event_log import emit_event
from app.services.evidence_sanitizer import sanitize_reference_text

logger = structlog.get_logger("agents.investigator.workflow")

# Prompts this workflow can use — stamped onto the investigation row so any
# verdict traces to exact prompt bytes (AI-F2 convention).
_INVESTIGATOR_PROMPT_IDS = (
    "investigator_hypothesis_weigh",
    "investigator_synthesis_narrative",
)

_INVESTIGATION_STAGES = (
    "investigator_plan",
    "hypothesis_infra",
    "hypothesis_commit",
    "hypothesis_environment",
    "hypothesis_known_flaky",
    "hypothesis_regression",
    "investigator_synthesis",
)

# Bound the failure rows scanned for keywords/categories — the Investigator
# must stay cheap even on pathological runs.
_MAX_FAILURE_ROWS = 200
_MAX_RECALL_OFFENDERS = 3

# Singleton agents (stateless — safe across concurrent investigations).
_infra = InfraHypothesisAgent()
_commit = CommitHypothesisAgent()
_environment = EnvironmentHypothesisAgent()
_known_flaky = KnownFlakyHypothesisAgent()
_regression = RegressionHypothesisAgent()
_synthesis = SynthesisAgent()


# ── Evidence bundle (plan node) ──────────────────────────────────────────────


def _run_dict(run: Optional[TestRun]) -> Optional[dict[str, Any]]:
    if run is None:
        return None
    return {
        "id": str(run.id),
        "build_number": run.build_number,
        "branch": run.branch,
        "commit_hash": run.commit_hash,
        "ci_repo": run.ci_repo,
        "pr_number": run.pr_number,
        "ci_run_url": run.ci_run_url,
        "duration_ms": run.duration_ms,
        "total_tests": int(run.total_tests or 0),
        "failed_tests": int(run.failed_tests or 0),
        "broken_tests": int(run.broken_tests or 0),
        "ocp_namespace": run.ocp_namespace,
        "ocp_node": run.ocp_node,
        "suite_count": len(run.suite_names or []) or None,
    }


def _infra_keyword_hits(failures: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    """Match the run's failure texts against the infra rule pack's keyword
    lists (US-9.4 patterns in ``rules_engine._PATTERNS``). Read-only keyword
    scan — classification itself stays behind ``analysis_router``."""
    try:
        from app.services.rules_engine import _PATTERNS

        infra_keywords = [
            (rule_id, keywords)
            for rule_id, keywords, category, _summary in _PATTERNS
            if category == "INFRASTRUCTURE"
        ]
    except Exception:  # pragma: no cover — rule pack import must not break plan
        return 0, []
    hits: list[dict[str, Any]] = []
    matched = 0
    for failure in failures:
        text = str(failure.get("error_text") or "").lower()
        if not text:
            continue
        for rule_id, keywords in infra_keywords:
            if any(kw in text for kw in keywords):
                matched += 1
                if len(hits) < 5:
                    hits.append({"rule_id": rule_id, "test_name": failure.get("test_name")})
                break
    return matched, hits


async def _gather_bundle(investigation: AgentInvestigation) -> dict[str, Any]:
    """All deterministic evidence in ONE pass (own session). Every sub-fetch
    is individually guarded — a broken source degrades to an empty section,
    the hypotheses then evaluate honestly against what exists."""
    bundle: dict[str, Any] = {
        "run": None,
        "baseline": None,
        "compare": None,
        "failures": [],
        "failure_counts": {},
        "infra_keyword_matches": 0,
        "infra_rule_hits": [],
        "flaky_fingerprints": [],
        "clusters": [],
        "recall_lines": {},
    }
    async with AsyncSessionLocal() as db:
        run = (
            await db.execute(select(TestRun).where(TestRun.id == investigation.run_id))
        ).scalar_one_or_none()
        if run is None:
            return bundle
        bundle["run"] = _run_dict(run)

        scoped_member_ids: set[uuid.UUID] | None = None
        scoped_cluster: FailureCluster | None = None
        if getattr(investigation, "scope_type", "run") == "failure_cluster":
            failure_cluster_id = getattr(investigation, "failure_cluster_id", None)
            parent_pipeline_id = getattr(investigation, "parent_pipeline_run_id", None)
            if not failure_cluster_id or not parent_pipeline_id:
                raise ValueError("cluster investigation is missing durable parent authority")
            scoped_cluster = (
                await db.execute(
                    select(FailureCluster).where(
                        FailureCluster.id == failure_cluster_id,
                        FailureCluster.test_run_id == run.id,
                        FailureCluster.pipeline_run_id == parent_pipeline_id,
                    )
                )
            ).scalar_one_or_none()
            if scoped_cluster is None:
                raise ValueError("cluster investigation authority is unavailable")
            try:
                scoped_member_ids = {
                    uuid.UUID(str(member)) for member in (scoped_cluster.member_test_ids or [])
                }
            except (TypeError, ValueError) as exc:
                raise ValueError("cluster investigation membership is malformed") from exc
            if not scoped_member_ids:
                raise ValueError("cluster investigation membership is empty")
            persisted_members = {
                uuid.UUID(str(member))
                for member in (getattr(investigation, "cluster_member_test_ids", None) or [])
            }
            if persisted_members != scoped_member_ids:
                raise ValueError("cluster investigation membership changed after planning")
            from app.services.agent_planner import compute_cluster_scope_sha256

            observed_scope_hash = compute_cluster_scope_sha256(
                project_id=str(investigation.project_id),
                run_id=str(investigation.run_id),
                parent_pipeline_run_id=str(parent_pipeline_id),
                failure_cluster_id=str(failure_cluster_id),
                member_test_ids=[str(member) for member in scoped_member_ids],
            )
            if observed_scope_hash != getattr(investigation, "cluster_scope_sha256", None):
                raise ValueError("cluster investigation scope hash mismatch")
            bundle["scope"] = {
                "type": "failure_cluster",
                "failure_cluster_id": str(scoped_cluster.id),
                "cluster_id": scoped_cluster.cluster_id,
                "cluster_scope_sha256": observed_scope_hash,
                "member_count": len(scoped_member_ids),
            }

        # Baseline — REUSE the PR-comment selection (main/master first).
        baseline: Optional[TestRun] = None
        try:
            from app.services.github_pr_comment_service import _select_baseline

            baseline = await _select_baseline(db, run)
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_baseline_failed", error_type=type(exc).__name__)
        bundle["baseline"] = _run_dict(baseline)

        # Newly-failed classification — same run_compare the PR comment uses.
        if baseline is not None:
            try:
                from app.services.run_compare_service import compare_runs

                compare = await compare_runs(db, baseline.id, run.id)
                newly_failed = [
                    {
                        "fingerprint": d.get("test_fingerprint"),
                        "test_name": d.get("test_name"),
                        "suite_name": d.get("suite_name"),
                        "status": d.get("right_status"),
                    }
                    for d in compare.get("test_deltas", [])
                    if d.get("classification") == "new_failure"
                ]
                bundle["compare"] = {
                    "baseline_run_id": str(baseline.id),
                    "new_failures": int(compare.get("new_failures") or 0),
                    "fixed": int(compare.get("fixed") or 0),
                    "still_failing": int(compare.get("still_failing") or 0),
                    "newly_failed_tests": newly_failed,
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("investigator_compare_failed", error_type=type(exc).__name__)

        # Failing test rows + their persisted AI categories.
        try:
            rows = (
                await db.execute(
                    select(
                        TestCase.id,
                        TestCase.test_fingerprint,
                        TestCase.test_name,
                        TestCase.suite_name,
                        TestCase.status,
                        TestCase.error_message,
                    )
                    .where(
                        TestCase.test_run_id == run.id,
                        TestCase.status.in_(("FAILED", "BROKEN")),
                        *(
                            (TestCase.id.in_(scoped_member_ids),)
                            if scoped_member_ids is not None else ()
                        ),
                    )
                    .limit(_MAX_FAILURE_ROWS)
                )
            ).all()
            categories: dict[Any, str] = {}
            if rows:
                analysis_rows = (
                    await db.execute(
                        select(AIAnalysis.test_case_id, AIAnalysis.failure_category)
                        .where(AIAnalysis.test_case_id.in_([r.id for r in rows]))
                    )
                ).all()
                categories = {
                    r.test_case_id: str(getattr(r.failure_category, "value", r.failure_category) or "")
                    for r in analysis_rows
                }
            failures = [
                {
                    "test_case_id": str(r.id),
                    "fingerprint": r.test_fingerprint,
                    "test_name": r.test_name,
                    "suite_name": r.suite_name,
                    "status": str(r.status),
                    "category": categories.get(r.id),
                    "error_text": (r.error_message or "")[:2000],
                }
                for r in rows
            ]
            if scoped_member_ids is not None and {r.id for r in rows} != scoped_member_ids:
                raise ValueError("cluster investigation members are stale, foreign, or no longer failing")
            broken = sum(1 for f in failures if str(f["status"]).upper() == "BROKEN")
            suites = {str(f["suite_name"]).strip().lower() for f in failures if f["suite_name"]}
            bundle["failures"] = [
                {k: v for k, v in f.items() if k != "error_text"} for f in failures
            ]
            bundle["failure_counts"] = {
                "total": len(failures),
                "failed": len(failures) - broken,
                "broken": broken,
                "distinct_failing_suites": len(suites),
                "total_suites": len(run.suite_names or []),
            }
            if scoped_member_ids is not None and bundle.get("compare"):
                scoped_fingerprints = {
                    str(f["fingerprint"]) for f in failures if f.get("fingerprint")
                }
                compare = dict(bundle["compare"])
                compare["newly_failed_tests"] = [
                    item for item in (compare.get("newly_failed_tests") or [])
                    if str(item.get("fingerprint") or "") in scoped_fingerprints
                ]
                compare["new_failures"] = len(compare["newly_failed_tests"])
                bundle["compare"] = compare
            matches, hits = _infra_keyword_hits(failures)
            bundle["infra_keyword_matches"] = matches
            bundle["infra_rule_hits"] = hits
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_failure_scan_failed", error_type=type(exc).__name__)
            if scoped_member_ids is not None:
                raise ValueError(
                    "cluster_member_authority_invalid"
                ) from None

        # Known-flaky set — active quarantines ∪ flaky-coach cache (reused).
        try:
            from app.services.github_pr_comment_service import _flaky_fingerprints

            bundle["flaky_fingerprints"] = sorted(
                await _flaky_fingerprints(db, run.project_id)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_flaky_lookup_failed", error_type=type(exc).__name__)

        # Failure clusters (whatever the async AI pipeline has produced).
        try:
            cluster_rows = (
                [scoped_cluster]
                if scoped_cluster is not None
                else (
                    await db.execute(
                        select(FailureCluster).where(FailureCluster.test_run_id == run.id)
                    )
                ).scalars().all()
            )
            bundle["clusters"] = [
                {
                    "failure_cluster_id": str(c.id),
                    "cluster_id": c.cluster_id,
                    "label": c.label,
                    "size": len(c.member_test_ids or []),
                    "member_test_ids": [str(item) for item in (c.member_test_ids or [])],
                }
                for c in cluster_rows if c is not None
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_cluster_lookup_failed", error_type=type(exc).__name__)

        # Memory recall for the top flaky-overlapping offenders (AI-F3 read side).
        try:
            from app.services.memory_recall import (
                format_recall_lines,
                recall_failure_history,
            )

            flaky_set = set(bundle["flaky_fingerprints"])
            newly = (bundle.get("compare") or {}).get("newly_failed_tests")
            target = newly if newly is not None else bundle["failures"]
            offenders = [
                t for t in (target or [])
                if t.get("fingerprint") and str(t["fingerprint"]) in flaky_set
            ][:_MAX_RECALL_OFFENDERS]
            recall_lines: dict[str, list[str]] = {}
            for offender in offenders:
                recall = await recall_failure_history(
                    db,
                    run.project_id,
                    test_fingerprint=str(offender["fingerprint"]),
                    test_name=offender.get("test_name"),
                )
                if recall.get("has_history"):
                    recall_lines[str(offender["fingerprint"])] = format_recall_lines(recall)[:3]
            bundle["recall_lines"] = recall_lines
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_recall_failed", error_type=type(exc).__name__)

    return bundle


# ── Nodes ────────────────────────────────────────────────────────────────────


async def plan_node(state: InvestigationState) -> dict:
    """Gather the evidence bundle once; snapshot the cancel flag."""
    investigation_id = state["investigation_id"]
    pipeline_run_id = state["pipeline_run_id"]
    await emit_event(
        pipeline_run_id, "stage_started", stage_name="investigator_plan",
    )
    cancelled = await is_cancel_requested(investigation_id)
    bundle: dict[str, Any] = {}
    if not cancelled:
        async with AsyncSessionLocal() as db:
            investigation = (
                await db.execute(
                    select(AgentInvestigation).where(
                        AgentInvestigation.id == uuid.UUID(investigation_id)
                    )
                )
            ).scalar_one_or_none()
        if investigation is not None:
            bundle = await _gather_bundle(investigation)
    await _mark_plan_stage_done(pipeline_run_id, bundle, cancelled)
    await emit_event(
        pipeline_run_id,
        "stage_completed",
        stage_name="investigator_plan",
        detail={
            "cancelled": cancelled,
            "baseline_found": bool(bundle.get("baseline")),
            "failures": (bundle.get("failure_counts") or {}).get("total", 0),
            "flaky_set_size": len(bundle.get("flaky_fingerprints") or []),
        },
    )
    return {"bundle": bundle, "cancelled": cancelled, "errors": []}


async def _mark_plan_stage_done(
    pipeline_run_id: str, bundle: dict[str, Any], cancelled: bool
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentStageResult).where(
                    AgentStageResult.pipeline_run_id == pipeline_run_id,
                    AgentStageResult.stage_name == "investigator_plan",
                )
            )
            stage = result.scalar_one_or_none()
            if stage:
                stage.status = "completed"
                stage.completed_at = datetime.now(timezone.utc)
                stage.result_data = {
                    "cancelled": cancelled,
                    "baseline_found": bool(bundle.get("baseline")),
                    "failure_count": (bundle.get("failure_counts") or {}).get("total", 0),
                }
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("plan_stage_write_failed", error_type=type(exc).__name__)


async def infra_node(state: InvestigationState) -> dict:
    return await _infra.run(cast(dict[str, Any], state))


async def commit_node(state: InvestigationState) -> dict:
    return await _commit.run(cast(dict[str, Any], state))


async def environment_node(state: InvestigationState) -> dict:
    return await _environment.run(cast(dict[str, Any], state))


async def known_flaky_node(state: InvestigationState) -> dict:
    return await _known_flaky.run(cast(dict[str, Any], state))


async def regression_node(state: InvestigationState) -> dict:
    return await _regression.run(cast(dict[str, Any], state))


async def synthesis_node(state: InvestigationState) -> dict:
    # If synthesis already completed before a transient failure, reuse its
    # durable verdict and avoid replaying the stable synthesis reservation.
    if "investigator_synthesis" in (state.get("resume_completed_stages") or set()):
        return {"verdict": state.get("resume_verdict"), "errors": []}
    await set_investigation_status(state["investigation_id"], "synthesizing")
    resumed = list(state.get("resume_hypotheses") or [])
    current = list(state.get("hypotheses") or [])
    state_for_synthesis = dict(state)
    state_for_synthesis["hypotheses"] = resumed + current
    return await _synthesis.run(cast(dict[str, Any], state_for_synthesis))


# ── Graph ────────────────────────────────────────────────────────────────────


def _build_graph() -> StateGraph:
    graph = StateGraph(InvestigationState)
    graph.add_node("investigator_plan", plan_node)
    graph.add_node("hypothesis_infra", infra_node)
    graph.add_node("hypothesis_commit", commit_node)
    graph.add_node("hypothesis_environment", environment_node)
    graph.add_node("hypothesis_known_flaky", known_flaky_node)
    graph.add_node("hypothesis_regression", regression_node)
    graph.add_node("investigator_synthesis", synthesis_node)

    graph.set_entry_point("investigator_plan")
    # Parallel fan-out: all five hypotheses run concurrently off the plan.
    for hyp in (
        "hypothesis_infra",
        "hypothesis_commit",
        "hypothesis_environment",
        "hypothesis_known_flaky",
        "hypothesis_regression",
    ):
        graph.add_edge("investigator_plan", hyp)
        graph.add_edge(hyp, "investigator_synthesis")  # fan-in
    graph.add_edge("investigator_synthesis", END)
    return graph


# Compile once at import (compilation is expensive; instance is thread-safe).
_investigator_app = _build_graph().compile()


# ── Runner (Celery entry point) ──────────────────────────────────────────────


async def _create_stage_rows(
    pipeline_run_id: str,
    run_id: str,
    budget: dict[str, Any],
    investigation_id: str,
) -> None:
    from app.services.agent_capability_registry import get_capability
    from app.services.agent_planner import build_workflow_plan

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(AgentPipelineRun.id).where(AgentPipelineRun.id == pipeline_run_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        investigation = (
            await db.execute(
                select(AgentInvestigation).where(
                    AgentInvestigation.id == uuid.UUID(investigation_id)
                )
            )
        ).scalar_one()
        run_budget = {
            "max_llm_calls": int(budget.get("max_llm_calls") or 0),
            "max_tokens": int(budget.get("max_tokens") or 0),
            "max_cost_usd": float(budget.get("max_cost_usd") or 0.0),
            "max_seconds": (
                int(budget["max_seconds"])
                if budget.get("max_seconds") is not None
                else 300
            ),
            "max_retries": 0,
        }
        initial_plan = build_workflow_plan(workflow_type="investigation")
        db.add(AgentPipelineRun(
            id=pipeline_run_id,
            test_run_id=run_id,
            parent_pipeline_run_id=investigation.parent_pipeline_run_id,
            parent_task_id=investigation.parent_task_id,
            spawn_depth=int(investigation.spawn_depth or 0),
            workflow_type="investigation",
            status="running",
            started_at=datetime.now(timezone.utc),
            execution_metadata={
                "initial_workflow_plan": initial_plan,
                "run_budget": run_budget,
                "investigation_id": investigation_id,
                "failure_cluster_id": (
                    str(investigation.failure_cluster_id)
                    if investigation.failure_cluster_id else None
                ),
                "cluster_scope_sha256": investigation.cluster_scope_sha256,
            },
        ))
        for stage in _INVESTIGATION_STAGES:
            capability = get_capability(stage)
            db.add(AgentStageResult(
                pipeline_run_id=pipeline_run_id,
                task_key=stage,
                capability_id=capability.capability_id,
                parent_task_key=(
                    "investigator_plan"
                    if stage.startswith("hypothesis_")
                    else None
                ),
                failure_cluster_id=investigation.failure_cluster_id,
                selected=True,
                required=stage in {"investigator_plan", "investigator_synthesis"},
                dependencies=list(capability.dependencies),
                allocated_budget=run_budget,
                stage_name=stage,
                status="pending",
            ))
        await db.commit()


def _prompt_versions() -> dict[str, str]:
    try:
        from app.services.prompt_registry import prompt_versions_used

        return prompt_versions_used(*_INVESTIGATOR_PROMPT_IDS)
    except Exception:  # pragma: no cover — stamping must never break the run
        return {}


def _registry_digest() -> Optional[str]:
    try:
        from app.services.prompt_registry import registry_digest

        return registry_digest()[:12]
    except Exception:  # pragma: no cover
        return None


def _merge_hypotheses(
    seeded: list[dict[str, Any]], produced: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Final hypotheses in the fixed seeded order; produced results replace
    their pending placeholders, untouched placeholders stay pending."""
    by_id = {h.get("id"): h for h in produced}
    return [by_id.get(entry.get("id"), entry) for entry in seeded]


def _verdict_summary_line(verdict: Optional[dict[str, Any]], status: str) -> str:
    if status == "cancelled":
        return "Investigation cancelled before a verdict was synthesized."
    if status == "failed":
        return "Investigation failed before a verdict was synthesized."
    if not verdict:
        return "Investigation completed without a verdict."
    return (
        f"primary_cause={verdict.get('primary_cause')} "
        f"(confidence {verdict.get('confidence')}) — "
        f"{str(verdict.get('narrative') or '')[:200]}"
    )


def _safe_investigation_error(exc: BaseException) -> tuple[str, str]:
    """Return a bounded public error and a non-secret correlation id."""
    correlation_id = uuid.uuid4().hex[:12]
    safe, _, _ = sanitize_reference_text(str(exc), limit=1200)
    return (
        f"Investigation execution error ({type(exc).__name__}, ref={correlation_id}): {safe}",
        correlation_id,
    )


def _terminal_status(*, error: Optional[str], cancel_requested: bool) -> str:
    if error is not None:
        return "failed"
    if cancel_requested:
        return "cancelled"
    return "completed"


async def _finalize(
    investigation_id: str,
    *,
    pipeline_run_id: str,
    final_state: Optional[dict[str, Any]],
    error: Optional[str],
    wall_seconds: float,
    outbox_failure_reason: Optional[str] = None,
) -> None:
    """Terminal write: investigation row + policy promotion counter + the
    AgentRun ledger entry (AI-3) in one committed unit."""
    from app.services.agent_investigation_service import record_agent_run

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(AgentInvestigation)
                .where(AgentInvestigation.id == uuid.UUID(investigation_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return
        if row.status in {"completed", "cancelled", "failed"}:
            # Idempotent replay: reconcile the stable pipeline projection but
            # never write a second AgentRun or promotion increment.
            existing_pipeline = (
                await db.execute(
                    select(AgentPipelineRun).where(
                        AgentPipelineRun.id == pipeline_run_id
                    )
                )
            ).scalar_one_or_none()
            if existing_pipeline is not None:
                existing_pipeline.status = row.status
                existing_pipeline.completed_at = row.completed_at
                existing_pipeline.error = row.error
                existing_pipeline.execution_metadata = {
                    **dict(existing_pipeline.execution_metadata or {}),
                    "investigation_id": investigation_id,
                    "budget_spend": dict(row.spend or {}),
                }
                await db.commit()
            return

        ledger_spend = reconcile_outstanding_budget_ledger(dict(row.spend or {}))
        state_reasons: list[str] = []
        for hypothesis in (final_state or {}).get("hypotheses") or []:
            reason = hypothesis.get("llm_enrichment_stop_reason") if isinstance(hypothesis, dict) else None
            if reason:
                state_reasons.append(str(reason))
        degradation = ((final_state or {}).get("verdict") or {}).get("degradation") or {}
        if isinstance(degradation, dict) and degradation.get("synthesis_stop_reason"):
            state_reasons.append(str(degradation["synthesis_stop_reason"]))
        budget_stop_reasons = [
            str(item) for item in ledger_spend.get("budget_stop_reasons") or []
        ][-20:]
        for reason in state_reasons:
            if reason not in budget_stop_reasons:
                budget_stop_reasons.append(reason)
        budget_stop_reasons = budget_stop_reasons[-20:]
        ledger_invalid = "budget_ledger_invalid" in budget_stop_reasons
        spend = {
            "llm_calls": max(
                int((final_state or {}).get("spend_llm_calls") or 0),
                bounded_accounting_int(ledger_spend.get("llm_calls")),
            ),
            "tokens": max(
                int((final_state or {}).get("spend_tokens") or 0),
                bounded_accounting_int(ledger_spend.get("tokens")),
            ),
            "cost_usd": max(
                float((final_state or {}).get("spend_cost_usd") or 0.0),
                bounded_accounting_cost(ledger_spend.get("cost_usd")),
            ),
            "seconds": round(wall_seconds, 3),
            "budget_stop_reasons": budget_stop_reasons,
            "last_stop_reason": (
                budget_stop_reasons[-1] if budget_stop_reasons else None
            ),
            "budget_exhausted": bool(
                {
                    "llm_call_budget_exhausted",
                    "token_budget_exhausted",
                    "cost_budget_exhausted",
                    "wall_clock_budget_exhausted",
                }.intersection(budget_stop_reasons)
            ),
            "ledger_version": int(ledger_spend.get("ledger_version") or 2),
            "completed_reservations": dict(
                list(
                    (
                        ledger_spend.get("completed_reservations")
                        if isinstance(ledger_spend.get("completed_reservations"), dict)
                        else {}
                    ).items()
                )[-100:]
            ),
            "reservations": (
                dict(ledger_spend.get("reservations") or {})
                if ledger_invalid and isinstance(ledger_spend.get("reservations"), dict)
                else {}
            ),
            "reserved_llm_calls": (
                bounded_accounting_int(ledger_spend.get("reserved_llm_calls"))
                if ledger_invalid else 0
            ),
            "reserved_tokens": (
                bounded_accounting_int(ledger_spend.get("reserved_tokens"))
                if ledger_invalid else 0
            ),
        }
        status = _terminal_status(error=error, cancel_requested=bool(row.cancel_requested))
        if error is not None:
            row.error = error[:2000]

        if final_state is not None:
            row.hypotheses = _merge_hypotheses(
                list(row.hypotheses or []), list(final_state.get("hypotheses") or [])
            )
            row.verdict = final_state.get("verdict")
            row.model_info = final_state.get("model_info")
        row.spend = spend
        row.prompt_versions = _prompt_versions()
        row.status = status
        row.completed_at = datetime.now(timezone.utc)

        if outbox_failure_reason:
            outbox_rows = (
                await db.execute(
                    select(AgentChildDispatchOutbox)
                    .where(
                        AgentChildDispatchOutbox.investigation_id == row.id,
                        AgentChildDispatchOutbox.status.in_(
                            ("pending", "sending", "sent")
                        ),
                    )
                    .with_for_update()
                )
            ).scalars().all()
            for outbox in outbox_rows:
                outbox.status = "failed"
                outbox.next_attempt_at = None
                outbox.last_error = outbox_failure_reason[:2000]

        pipeline = (
            await db.execute(
                select(AgentPipelineRun).where(
                    AgentPipelineRun.id == pipeline_run_id
                )
            )
        ).scalar_one_or_none()
        if pipeline is not None:
            pipeline.status = status
            pipeline.completed_at = row.completed_at
            pipeline.error = error[:2000] if error else None
            pipeline.execution_metadata = {
                **dict(pipeline.execution_metadata or {}),
                "investigation_id": investigation_id,
                "budget_spend": spend,
            }

        # Promotion counter: completed shadow runs are the evidence base for
        # a later shadow→suggest promotion (AI-3).
        if status == "completed" and row.mode == "shadow":
            policy = (
                await db.execute(
                    select(AgentPolicy).where(
                        AgentPolicy.project_id == row.project_id,
                        AgentPolicy.agent_id == "investigator",
                    )
                )
            ).scalar_one_or_none()
            if policy is None:
                policy = AgentPolicy(
                    project_id=row.project_id,
                    agent_id="investigator",
                    shadow_runs_completed=0,
                )
                db.add(policy)
            policy.shadow_runs_completed = int(policy.shadow_runs_completed or 0) + 1

        verdict = row.verdict or {}
        await record_agent_run(
            db,
            row,
            status=status,
            summary=_verdict_summary_line(row.verdict, status),
            actions_proposed=list(verdict.get("recommended_actions") or []),
            tokens=spend["tokens"],
            cost_usd=spend["cost_usd"],
            duration_ms=int(wall_seconds * 1000),
            prompt_registry_digest=_registry_digest(),
        )
        await db.commit()




async def resume_investigation(investigation_id: str) -> dict[str, Any]:
    """Requeue one failed cluster child under its stable investigation ID.

    Completed hypothesis/synthesis stages remain authoritative; incomplete
    stages receive a new durable attempt and the normal queued claim prevents
    duplicate deliveries from executing concurrently. Provider reservations
    and completed receipts are never cleared or replayed.
    """
    investigation_uuid = uuid.UUID(str(investigation_id))
    pipeline_run_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"testlookup:investigation:{investigation_id}")
    )
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(AgentInvestigation)
            .where(AgentInvestigation.id == investigation_uuid)
            .with_for_update()
        )).scalar_one_or_none()
        if row is None:
            return {"skipped": "not_found"}
        if getattr(row, "scope_type", "run") != "failure_cluster":
            return {"skipped": "resume_scope_unsupported"}
        if row.status not in {"failed", "cancelled"}:
            return {"skipped": f"status_{row.status}"}
        spend = row.spend if isinstance(row.spend, dict) else {}
        reservations = spend.get("reservations")
        if isinstance(reservations, dict) and reservations:
            return {"skipped": "budget_reservations_pending"}

        stages = list((await db.execute(
            select(AgentStageResult)
            .where(AgentStageResult.pipeline_run_id == uuid.UUID(pipeline_run_id))
            .with_for_update()
        )).scalars().all())
        completed_hypotheses: set[str] = set()
        completed_stages: set[str] = set()
        for stage in stages:
            if stage.status != "completed":
                continue
            completed_stages.add(str(stage.stage_name))
            if str(stage.stage_name).startswith("hypothesis_"):
                completed_hypotheses.add(
                    str(stage.stage_name).removeprefix("hypothesis_")
                )
        persisted_hypotheses = [
            dict(item) for item in (row.hypotheses or [])
            if isinstance(item, dict)
            and str(item.get("id")) in completed_hypotheses
        ]
        resume_verdict = (
            dict(row.verdict)
            if "investigator_synthesis" in completed_stages
            and isinstance(row.verdict, dict)
            else None
        )
        for stage in stages:
            if (
                stage.status == "completed"
                and (
                    str(stage.stage_name).startswith("hypothesis_")
                    or (
                        stage.stage_name == "investigator_synthesis"
                        and resume_verdict
                    )
                )
            ):
                continue
            stage.status = "pending"
            stage.started_at = None
            stage.completed_at = None
            stage.error = None
            stage.stop_reason = None
            stage.skipped_reason = None
            stage.execution_path = None
            stage.idempotency_key = None
            stage.result_data = None
            stage.attempt = max(int(stage.attempt or 1) + 1, 1)

        row.status = "queued"
        row.started_at = None
        row.completed_at = None
        row.error = None
        row.cancel_requested = False
        row.cancelled_by = None
        pipeline = (await db.execute(
            select(AgentPipelineRun)
            .where(AgentPipelineRun.id == uuid.UUID(pipeline_run_id))
            .with_for_update()
        )).scalar_one_or_none()
        if pipeline is None:
            return {"skipped": "pipeline_not_found"}
        pipeline.status = "pending"
        pipeline.started_at = None
        pipeline.completed_at = None
        pipeline.error = None
        await db.commit()

    payload = {
        "resume_hypotheses": persisted_hypotheses,
        "resume_completed_hypotheses": completed_hypotheses,
        "resume_completed_stages": completed_stages,
        "resume_verdict": resume_verdict,
    }
    return await run_investigation(
        investigation_id, resume=True, resume_payload=payload
    )

async def run_investigation(
    investigation_id: str,
    *,
    resume: bool = False,
    resume_payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Execute one investigation end-to-end. Celery task entry point
    (``run_agent_investigation`` on the ai_analysis queue)."""
    pipeline_run_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"testlookup:investigation:{investigation_id}")
    )
    async with AsyncSessionLocal() as db:
        started_at = datetime.now(timezone.utc)
        claim = await db.execute(
            update(AgentInvestigation)
            .where(
                AgentInvestigation.id == uuid.UUID(investigation_id),
                AgentInvestigation.status == "queued",
            )
            .values(status="running", started_at=started_at)
            .returning(AgentInvestigation.id)
        )
        if claim.scalar_one_or_none() is None:
            existing_status = (
                await db.execute(
                    select(AgentInvestigation.status).where(
                        AgentInvestigation.id == uuid.UUID(investigation_id)
                    )
                )
            ).scalar_one_or_none()
            if existing_status is None:
                logger.warning("investigation_not_found", investigation_id=investigation_id)
                return {"skipped": "not_found"}
            logger.info(
                "investigation_not_queued",
                investigation_id=investigation_id,
                status=existing_status,
            )
            return {"skipped": f"status_{existing_status}"}
        investigation = (
            await db.execute(
                select(AgentInvestigation).where(
                    AgentInvestigation.id == uuid.UUID(investigation_id)
                )
            )
        ).scalar_one()
        run = (
            await db.execute(
                select(TestRun).where(TestRun.id == investigation.run_id)
            )
        ).scalar_one_or_none()
        if run is None or run.project_id != investigation.project_id:
            await db.commit()
            ownership_pipeline_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"testlookup:investigation:{investigation_id}",
                )
            )
            await _finalize(
                investigation_id,
                pipeline_run_id=ownership_pipeline_id,
                final_state=None,
                error="Investigation run ownership validation failed.",
                wall_seconds=0.0,
            )
            return {"skipped": "run_ownership_invalid"}
        build_number = run.build_number if run else ""
        if resume:
            pipeline = (await db.execute(
                select(AgentPipelineRun).where(
                    AgentPipelineRun.id == uuid.UUID(pipeline_run_id)
                ).with_for_update()
            )).scalar_one_or_none()
            if pipeline is not None:
                pipeline.status = "running"
                pipeline.started_at = started_at
                pipeline.completed_at = None
                pipeline.error = None
        budget = dict(investigation.budget or {})
        mode = investigation.mode
        triggered_by = investigation.triggered_by
        run_id = str(investigation.run_id)
        project_id = str(investigation.project_id)
        await db.commit()


    started = time.monotonic()
    try:
        await _create_stage_rows(
            pipeline_run_id, run_id, budget, investigation_id
        )
        await emit_event(pipeline_run_id, "pipeline_started", detail={
            "workflow_type": "investigation",
            "investigation_id": investigation_id,
            "triggered_by": triggered_by,
            "mode": mode,
        })
    except Exception as exc:
        safe_error, correlation_id = _safe_investigation_error(exc)
        logger.error(
            "investigation_startup_failed",
            investigation_id=investigation_id,
            error_type=type(exc).__name__,
            correlation_id=correlation_id,
        )
        await _finalize(
            investigation_id,
            pipeline_run_id=pipeline_run_id,
            final_state=None,
            error=safe_error,
            wall_seconds=time.monotonic() - started,
        )
        raise RuntimeError(safe_error) from None

    raw_max_seconds = budget.get("max_seconds")
    max_seconds = int(raw_max_seconds) if raw_max_seconds is not None else 300
    initial_state: InvestigationState = {
        "investigation_id": investigation_id,
        "pipeline_run_id": pipeline_run_id,
        "run_id": run_id,
        "project_id": project_id,
        "build_number": build_number,
        "mode": mode,
        "triggered_by": triggered_by,
        "budget": {
            "max_llm_calls": int(budget.get("max_llm_calls") or 0),
            "max_tokens": int(budget.get("max_tokens") or 0),
            "max_cost_usd": float(budget.get("max_cost_usd") or 0.0),
            "max_seconds": max_seconds,
        },
        "deadline_ts": started + max_seconds,
        "bundle": {},
        "cancelled": False,
        "hypotheses": [],
        "spend_llm_calls": 0,
        "spend_tokens": 0,
        "spend_cost_usd": 0.0,
        "errors": [],
        "verdict": None,
        "model_info": None,
        "resume_hypotheses": list((resume_payload or {}).get("resume_hypotheses") or []),
        "resume_completed_hypotheses": set((resume_payload or {}).get("resume_completed_hypotheses") or set()),
        "resume_completed_stages": set((resume_payload or {}).get("resume_completed_stages") or set()),
        "resume_verdict": (resume_payload or {}).get("resume_verdict"),
    }

    try:
        logger.info(
            "investigation_started",
            investigation_id=investigation_id,
            run_id=run_id,
            triggered_by=triggered_by,
            mode=mode,
        )
        final_state = cast(
            dict[str, Any], await cast(Any, _investigator_app).ainvoke(initial_state)
        )
        wall = time.monotonic() - started
        await _finalize(
            investigation_id,
            pipeline_run_id=pipeline_run_id,
            final_state=final_state,
            error=None,
            wall_seconds=wall,
        )
        await emit_event(pipeline_run_id, "pipeline_completed", detail={
            "workflow_type": "investigation",
            "investigation_id": investigation_id,
            "primary_cause": (final_state.get("verdict") or {}).get("primary_cause"),
            "cancelled": bool(final_state.get("cancelled")),
            "spend_llm_calls": final_state.get("spend_llm_calls"),
            "spend_tokens": final_state.get("spend_tokens"),
        })
        logger.info(
            "investigation_finished",
            investigation_id=investigation_id,
            primary_cause=(final_state.get("verdict") or {}).get("primary_cause"),
            seconds=round(wall, 2),
        )
        return final_state
    except Exception as exc:
        wall = time.monotonic() - started
        safe_error, correlation_id = _safe_investigation_error(exc)
        logger.error(
            "investigation_failed",
            investigation_id=investigation_id,
            error_type=type(exc).__name__,
            correlation_id=correlation_id,
        )
        await _finalize(
            investigation_id,
            pipeline_run_id=pipeline_run_id,
            final_state=None,
            error=safe_error,
            wall_seconds=wall,
        )
        await emit_event(pipeline_run_id, "error_occurred", detail={
            "workflow_type": "investigation",
            "investigation_id": investigation_id,
            "error": safe_error[:500],
            "correlation_id": correlation_id,
        })
        raise RuntimeError(safe_error) from None


async def reap_stale_investigations(*, grace_seconds: int = 300) -> dict[str, int]:
    """Fail and reconcile claimed Investigator runs whose execution lease expired.

    ``started_at`` is the durable claim heartbeat for this non-resumable slice.
    The existing ten-minute pipeline reaper calls this function; each row uses
    its own max-seconds budget plus a bounded grace period.
    """
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AgentInvestigation).where(
                    (
                        (
                            AgentInvestigation.status.in_(
                                ("running", "synthesizing")
                            )
                        )
                        & AgentInvestigation.started_at.is_not(None)
                    )
                    | (
                        (AgentInvestigation.status == "queued")
                        & (AgentInvestigation.scope_type == "failure_cluster")
                    ),
                ).limit(100)
            )
        ).scalars().all()
    stale: list[tuple[str, float]] = []
    for row in rows:
        raw_budget = row.budget if isinstance(row.budget, dict) else {}
        raw_max_seconds = raw_budget.get("max_seconds")
        max_seconds = (
            bounded_accounting_int(raw_max_seconds)
            if raw_max_seconds is not None
            else 300
        )
        heartbeat = row.started_at or row.created_at
        if heartbeat is None:
            continue
        age_seconds = max(0.0, (now - heartbeat).total_seconds())
        if age_seconds > max_seconds + max(60, grace_seconds):
            stale.append((str(row.id), age_seconds))
    for stale_id, age_seconds in stale:
        pipeline_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"testlookup:investigation:{stale_id}")
        )
        await _finalize(
            stale_id,
            pipeline_run_id=pipeline_id,
            final_state=None,
            error="Investigation execution lease expired; accounting reconciled by reaper.",
            wall_seconds=age_seconds,
            outbox_failure_reason="investigation_execution_lease_expired",
        )
    return {"checked": len(rows), "reaped": len(stale)}
