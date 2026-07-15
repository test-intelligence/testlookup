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
from sqlalchemy import select

from app.agents.investigator.hypotheses import (
    CommitHypothesisAgent,
    EnvironmentHypothesisAgent,
    InfraHypothesisAgent,
    KnownFlakyHypothesisAgent,
    RegressionHypothesisAgent,
)
from app.agents.investigator.persistence import (
    is_cancel_requested,
    set_investigation_status,
)
from app.agents.investigator.state import InvestigationState
from app.agents.investigator.synthesis import SynthesisAgent
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
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

        # Baseline — REUSE the PR-comment selection (main/master first).
        baseline: Optional[TestRun] = None
        try:
            from app.services.github_pr_comment_service import _select_baseline

            baseline = await _select_baseline(db, run)
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_baseline_failed", error=str(exc))
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
                logger.warning("investigator_compare_failed", error=str(exc))

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
                    "fingerprint": r.test_fingerprint,
                    "test_name": r.test_name,
                    "suite_name": r.suite_name,
                    "status": str(r.status),
                    "category": categories.get(r.id),
                    "error_text": (r.error_message or "")[:2000],
                }
                for r in rows
            ]
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
            matches, hits = _infra_keyword_hits(failures)
            bundle["infra_keyword_matches"] = matches
            bundle["infra_rule_hits"] = hits
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_failure_scan_failed", error=str(exc))

        # Known-flaky set — active quarantines ∪ flaky-coach cache (reused).
        try:
            from app.services.github_pr_comment_service import _flaky_fingerprints

            bundle["flaky_fingerprints"] = sorted(
                await _flaky_fingerprints(db, run.project_id)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_flaky_lookup_failed", error=str(exc))

        # Failure clusters (whatever the async AI pipeline has produced).
        try:
            cluster_rows = (
                await db.execute(
                    select(FailureCluster).where(FailureCluster.test_run_id == run.id)
                )
            ).scalars().all()
            bundle["clusters"] = [
                {
                    "cluster_id": c.cluster_id,
                    "label": c.label,
                    "size": len(c.member_test_ids or []),
                }
                for c in cluster_rows
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("investigator_cluster_lookup_failed", error=str(exc))

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
            logger.warning("investigator_recall_failed", error=str(exc))

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
        logger.warning("plan_stage_write_failed", error=str(exc))


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
    await set_investigation_status(state["investigation_id"], "synthesizing")
    return await _synthesis.run(cast(dict[str, Any], state))


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


async def _create_stage_rows(pipeline_run_id: str, run_id: str) -> None:
    async with AsyncSessionLocal() as db:
        db.add(AgentPipelineRun(
            id=pipeline_run_id,
            test_run_id=run_id,
            workflow_type="investigation",
            status="running",
            started_at=datetime.now(timezone.utc),
        ))
        for stage in _INVESTIGATION_STAGES:
            db.add(AgentStageResult(
                pipeline_run_id=pipeline_run_id,
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


async def _finalize(
    investigation_id: str,
    *,
    pipeline_run_id: str,
    final_state: Optional[dict[str, Any]],
    error: Optional[str],
    wall_seconds: float,
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

        spend = {
            "llm_calls": int((final_state or {}).get("spend_llm_calls") or 0),
            "tokens": int((final_state or {}).get("spend_tokens") or 0),
            "cost_usd": float((final_state or {}).get("spend_cost_usd") or 0.0),
            "seconds": round(wall_seconds, 3),
        }
        if error is not None:
            status = "failed"
            row.error = error[:2000]
        elif row.cancel_requested:
            status = "cancelled"
        else:
            status = "completed"

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

    # Mark the backing pipeline-run row terminal (best-effort).
    try:
        async with AsyncSessionLocal() as db:
            pr = (
                await db.execute(
                    select(AgentPipelineRun).where(
                        AgentPipelineRun.id == pipeline_run_id,
                    )
                )
            ).scalar_one_or_none()
            if pr is not None:
                pr.status = "completed" if error is None else "failed"
                pr.completed_at = datetime.now(timezone.utc)
                if error:
                    pr.error = error[:2000]
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("investigator_pipeline_row_finalize_failed", error=str(exc))


async def run_investigation(investigation_id: str) -> dict[str, Any]:
    """Execute one investigation end-to-end. Celery task entry point
    (``run_agent_investigation`` on the ai_analysis queue)."""
    async with AsyncSessionLocal() as db:
        investigation = (
            await db.execute(
                select(AgentInvestigation).where(
                    AgentInvestigation.id == uuid.UUID(investigation_id)
                )
            )
        ).scalar_one_or_none()
        if investigation is None:
            logger.warning("investigation_not_found", investigation_id=investigation_id)
            return {"skipped": "not_found"}
        if investigation.status not in ("queued",):
            logger.info(
                "investigation_not_queued",
                investigation_id=investigation_id,
                status=investigation.status,
            )
            return {"skipped": f"status_{investigation.status}"}
        run = (
            await db.execute(
                select(TestRun).where(TestRun.id == investigation.run_id)
            )
        ).scalar_one_or_none()
        build_number = run.build_number if run else ""
        budget = dict(investigation.budget or {})
        mode = investigation.mode
        triggered_by = investigation.triggered_by
        run_id = str(investigation.run_id)
        project_id = str(investigation.project_id)

    pipeline_run_id = str(uuid.uuid4())
    await _create_stage_rows(pipeline_run_id, run_id)
    await set_investigation_status(
        investigation_id, "running", started_at=datetime.now(timezone.utc)
    )
    await emit_event(pipeline_run_id, "pipeline_started", detail={
        "workflow_type": "investigation",
        "investigation_id": investigation_id,
        "triggered_by": triggered_by,
        "mode": mode,
    })

    max_seconds = int(budget.get("max_seconds") or 300)
    started = time.monotonic()
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
        logger.error(
            "investigation_failed",
            investigation_id=investigation_id,
            error=str(exc),
            exc_info=True,
        )
        await _finalize(
            investigation_id,
            pipeline_run_id=pipeline_run_id,
            final_state=None,
            error=f"Investigation execution error: {exc}",
            wall_seconds=wall,
        )
        await emit_event(pipeline_run_id, "error_occurred", detail={
            "workflow_type": "investigation",
            "investigation_id": investigation_id,
            "error": str(exc)[:500],
        })
        raise
