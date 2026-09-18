"""
Flaky Sentinel Agent.
Investigates the full lifecycle of flaky tests: when flakiness started,
what changed, and whether quarantine is warranted.

Performance: uses bulk query for build numbers (O(1) instead of O(n) per test).
Scoping: history queries scoped by project to prevent cross-project contamination.
"""
import json
import structlog

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.postgres import AsyncSessionLocal
from app.models.agent_contracts import FlakySentinelAgentOutput, validate_agent_contract
from app.models.postgres import TestCase, TestCaseHistory, TestRun, TestStatus
from app.services.flaky_investigator import build_flaky_verdict, cluster_failures
from app.services.flaky_signals import compute_intermittency_signals
from app.services.flaky_statistics import wilson_failure_confidence
from app.services.ml.flaky_confidence import (
    FlakyConfidenceModel,
    build_flaky_feature_vector,
)
from app.services.workflow_step_context import tool_allowed
from app.tools.fetch_build_changes import fetch_build_changes

logger = structlog.get_logger("agents.flaky_sentinel")


def _detect_flaky_onset(statuses: list) -> int | None:
    """Index (oldest-first) of the first status flip that BEGINS a genuinely
    oscillating region — a flip after which the history flips at least once
    more.

    Returns ``None`` when there is no sustained oscillation. The previous
    "first flip wins" heuristic mislabelled a *single permanent transition* as
    the flakiness onset: a test that was broken then fixed
    (``[F,F,F,P,P,P]``) reported the FIX build as ``flaky_since``, and a
    one-off blip (``[P,P,P,P,P,F]``) or a plain regression (``[P,P,P,F,F,F]``)
    was treated as flaky onset. None of those oscillate, so none is a flaky
    onset — the caller reports ``flaky_since = "unknown"`` and skips the
    build-diff lookup rather than pointing at the wrong build.
    """
    n = len(statuses)
    for i in range(1, n):
        if statuses[i] == statuses[i - 1]:
            continue
        # A flip at i is a sustained-flakiness onset only if the suffix from i
        # flips again (real oscillation), not a single permanent transition.
        for j in range(i + 1, n):
            if statuses[j] != statuses[j - 1]:
                return i
    return None


def _reconcile_recommendation(failure_rate: float, verdict: dict) -> str:
    """Derive the human recommendation from the reconciled flaky VERDICT, not
    the raw failure rate alone.

    The verdict (ML + Wilson + intermittency signals) already distinguishes a
    genuine flake from a *persistent regression* (``is_flaky`` is False when
    ``likely_cause_code`` is ``likely_regression``/``insufficient_data`` — see
    ``flaky_investigator.build_flaky_verdict``). The old failure-rate ladder
    ignored that, so a consistently-failing real bug (high failure rate, low
    intermittency) was told to "QUARANTINE" while its own ``verdict.is_flaky``
    said it wasn't flaky — a self-contradiction that also risks *hiding a real
    defect* behind a quarantine. Reconcile the two here.
    """
    if not bool(verdict.get("is_flaky")):
        if verdict.get("likely_cause_code") == "likely_regression":
            return (
                "INVESTIGATE AS BUG -- fails persistently with a consistent "
                "signature; this is a real defect, not flakiness. Fix it rather "
                "than quarantine (quarantining would hide the failure)."
            )
        return (
            "MONITOR -- not enough evidence to confirm flakiness "
            "(insufficient or inconclusive history)."
        )
    # Confirmed flaky: severity by how often it disrupts CI.
    if failure_rate > 0.5:
        return "QUARANTINE -- flaky and failing more than 50% of the time, blocking CI reliability"
    if failure_rate > 0.25:
        return "INVESTIGATE URGENTLY -- flaky with a high flip rate, significant noise source"
    return "MONITOR -- flaky at a low rate, worth tracking but not yet critical"


class FlakySentinelAgent(BaseAgent):
    stage_name = "flaky_sentinel"

    async def run(self, state: dict) -> dict:
        pipeline_run_id: str = state["pipeline_run_id"]
        project_id: str = state["project_id"]
        analyses: dict = state.get("analyses", {})

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(project_id, {"status": "running", "message": "Investigating flaky test lifecycles..."})

        # Identify tests classified as FLAKY
        flaky_test_ids = [
            tc_id for tc_id, analysis in analyses.items()
            if analysis.get("is_flaky") or analysis.get("failure_category") == "FLAKY"
        ]

        if not flaky_test_ids:
            await self.log_decision(
                pipeline_run_id,
                decision_point="flaky_scope",
                chosen="skip_no_candidates",
                rationale=(
                    f"none of {len(analyses)} analysis/analyses was classified FLAKY, "
                    "so there is no lifecycle to investigate"
                ),
                alternatives=["investigate"],
            )
            await self.mark_stage_done(pipeline_run_id, result_data={"flaky_investigated": 0})
            return validate_agent_contract(
                FlakySentinelAgentOutput,
                {"flaky_findings": []},
                agent_name=self.stage_name,
                confidence=100,
                decision_reason="no_flaky_tests_detected",
            )

        findings = []
        capped_ids = flaky_test_ids[:10]  # Cap at 10 to avoid excessive processing
        # The cap silently drops candidates. A bounded result that does not say
        # it was bounded reads as a complete one.
        await self.log_decision(
            pipeline_run_id,
            decision_point="flaky_scope",
            chosen="investigate",
            rationale=(
                f"{len(flaky_test_ids)} flaky candidate(s); investigating "
                f"{len(capped_ids)}"
                + (f" (capped, {len(flaky_test_ids) - len(capped_ids)} not examined)"
                   if len(flaky_test_ids) > len(capped_ids) else "")
            ),
            context={
                "candidates": len(flaky_test_ids),
                "investigated": len(capped_ids),
                "capped": len(flaky_test_ids) > len(capped_ids),
            },
        )

        async with AsyncSessionLocal() as db:
            # Bulk fetch test case details
            tc_result = await db.execute(
                select(TestCase).where(TestCase.id.in_(capped_ids))
            )
            test_cases = {str(tc.id): tc for tc in tc_result.scalars().all()}

            for tc_id in capped_ids:
                tc = test_cases.get(tc_id)
                if not tc:
                    continue
                finding = await self._investigate_flaky_test(db, tc, tc_id, project_id)
                if finding:
                    findings.append(finding)

        await self.mark_stage_done(pipeline_run_id, result_data={"flaky_investigated": len(findings)})
        await self.broadcast_progress(project_id, {
            "status": "completed",
            "message": f"Flaky sentinel investigated {len(findings)} tests",
        })

        return validate_agent_contract(
            FlakySentinelAgentOutput,
            {"flaky_findings": findings},
            agent_name=self.stage_name,
            confidence=85 if findings else 70,
            evidence_refs=[
                {"type": "flaky_finding", "id": finding.get("test_case_id", "unknown")}
                for finding in findings
            ],
            decision_reason="flaky_lifecycle_investigation_completed",
        )

    async def _investigate_flaky_test(
        self, db, tc: TestCase, tc_id: str, project_id: str
    ) -> dict | None:
        # Get last 20 history entries scoped to this project. FLK-P4: join the
        # per-run TestCase meta (error_message / stack_trace / retry_count /
        # is_flaky_run) so the investigator can cluster failures and build a
        # structured, evidence-backed verdict from the same window.
        hist_result = await db.execute(
            select(
                TestCaseHistory.status.label("status"),
                TestCaseHistory.created_at.label("created_at"),
                TestCaseHistory.test_run_id.label("test_run_id"),
                TestCase.error_message.label("error_message"),
                TestCase.stack_trace.label("stack_trace"),
                TestCase.retry_count.label("retry_count"),
                TestCase.is_flaky_run.label("is_flaky_run"),
            )
            .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
            .join(TestCase, TestCase.id == TestCaseHistory.test_case_id, isouter=True)
            .where(
                TestCaseHistory.test_fingerprint == tc.test_fingerprint,
                TestRun.project_id == project_id,
            )
            .order_by(TestCaseHistory.created_at.desc())
            .limit(20)
        )
        history = hist_result.all()  # most-recent-first Row objects

        if len(history) < 3:
            return {
                "test_case_id": tc_id,
                "test_name": tc.test_name,
                "flaky_since": "insufficient history",
                "recommendation": "MONITOR -- insufficient history to determine onset",
            }

        # Bulk fetch build numbers for all history entries in ONE query
        run_ids = [h.test_run_id for h in history]
        build_result = await db.execute(
            select(TestRun.id, TestRun.build_number)
            .where(TestRun.id.in_(run_ids))
        )
        build_map = {str(r.id): r.build_number or "unknown" for r in build_result.all()}

        statuses = [h.status for h in reversed(history)]  # oldest first
        build_numbers = [build_map.get(str(h.test_run_id), "unknown") for h in reversed(history)]

        # Find flakiness onset: the first flip that BEGINS a sustained
        # oscillation (not a one-off transition — a fix/regression/blip is not a
        # flaky onset). None → no sustained onset; report "unknown" and skip the
        # build-diff lookup rather than blaming the wrong build.
        onset_index = _detect_flaky_onset(statuses)
        if onset_index is not None and onset_index < len(build_numbers):
            flaky_since_build = build_numbers[onset_index]
            last_stable_build = build_numbers[onset_index - 1] if onset_index > 0 else "unknown"
        else:
            flaky_since_build = "unknown"
            last_stable_build = "unknown"

        # Compute failure rate
        failed_count = sum(1 for s in statuses if s in (TestStatus.FAILED, TestStatus.BROKEN))
        failure_rate = failed_count / len(statuses)

        # Fetch build changes around onset
        change_summary = "Build change lookup skipped."
        if (
            flaky_since_build != "unknown"
            and last_stable_build != "unknown"
            and tool_allowed("fetch_build_changes")
        ):
            try:
                changes_json = await fetch_build_changes.ainvoke({
                    "params_json": json.dumps({
                        "test_fingerprint": tc.test_fingerprint,
                        "stable_build_number": last_stable_build,
                        "flaky_build_number": flaky_since_build,
                        "project_id": project_id,
                    })
                })
                changes = json.loads(changes_json)
                change_summary = changes.get("change_summary", "No changes found.")
            except Exception as exc:
                logger.debug("Build changes fetch failed", error=str(exc))

        # FLK-P4: confirm + EXPLAIN the verdict. Build the per-run records from
        # the joined window, score intermittency (FLK-P1), the Wilson interval
        # (FLK-P2) and the ML confidence (FLK-P3), cluster the failures, and
        # assemble a structured {is_flaky, confidence, likely_cause, evidence[]}
        # verdict (AIQ-P1/P3). All helpers are pure + never-raise.
        records = [
            {
                "status": h.status,
                "created_at": h.created_at,
                "error_message": h.error_message,
                "stack_trace": h.stack_trace,
                "retry_count": h.retry_count,
                "is_flaky_run": h.is_flaky_run,
            }
            for h in history  # most-recent-first
        ]
        signals = compute_intermittency_signals(records)
        wilson = wilson_failure_confidence(failed_count, len(statuses))
        # Re-audit M14: pick up a version another pod trained (throttled).
        from app.services.ml import model_store

        await model_store.sync_down()
        ml_confidence = FlakyConfidenceModel.predict(
            build_flaky_feature_vector(failure_rate, signals, records)
        )
        clusters = cluster_failures(records)
        verdict = build_flaky_verdict(
            failure_rate,
            signals,
            ml_confidence=ml_confidence,
            wilson=wilson,
            clusters=clusters,
            build_change_summary=change_summary,
        )

        # Recommendation is derived from the reconciled verdict (not the raw
        # failure rate) so it can never contradict ``verdict.is_flaky`` — a
        # consistently-failing real bug is flagged as a bug to fix, not
        # "quarantined as flaky".
        recommendation = _reconcile_recommendation(failure_rate, verdict)

        # FLK-P5: granular step-level surgical attribution from the latest step
        # snapshot — point at the failing step rather than the whole test.
        step_attribution: dict | None = None
        try:
            from app.services.flaky_step_analysis import build_step_attribution
            from app.services.runs_service import failing_step_detail_by_fingerprint
            detail_map = await failing_step_detail_by_fingerprint(
                db, project_id, [tc.test_fingerprint]
            )
            detail = detail_map.get(tc.test_fingerprint)
            if detail:
                attr = build_step_attribution(
                    detail.get("first_failing"),
                    total_steps=detail.get("total_steps", 0),
                    failing_step_count=detail.get("failing_step_count", 0),
                )
                if attr.has_failing_step:
                    step_attribution = attr.to_dict()
        except Exception as exc:  # pragma: no cover — best-effort enrichment
            logger.debug("step attribution failed", error=str(exc))

        # Tier 1 item 3 — if the flip rate crosses the quarantine floor AND the
        # verdict confirms the test is actually flaky, propose it for QA Lead
        # approval via the quarantine service. Gating on ``verdict.is_flaky``
        # keeps this consistent with the recommendation: a persistent regression
        # (high failure rate, not flaky) must be fixed, not quarantined — hiding
        # a real defect behind a quarantine is the wrong action. Thresholds come
        # from the per-project quarantine lifecycle policy (PMF US-5.6);
        # defaults match the original acceptance criterion (>= 20% flip rate
        # over 10 runs). The service is idempotent: repeat runs just refresh
        # the existing PROPOSED row. Feature-flag gated, never raises.
        quarantine_request_id: str | None = None
        import uuid as _uuid
        proj_uuid = (
            project_id if isinstance(project_id, _uuid.UUID)
            else _uuid.UUID(str(project_id))
        )
        flip_rate_floor, min_runs = 0.20, 10
        try:
            from app.services.flaky_quarantine_service import get_lifecycle_policy
            _policy = await get_lifecycle_policy(db, proj_uuid)
            flip_rate_floor = _policy.detection_flip_rate_threshold
            min_runs = _policy.detection_min_runs
        except Exception as exc:  # pragma: no cover — defaults on lookup fault
            logger.debug("lifecycle policy lookup failed", error=str(exc))
        if (
            verdict.get("is_flaky")
            and failure_rate >= flip_rate_floor
            and len(statuses) >= min_runs
        ):
            try:
                from app.services.flaky_quarantine_service import propose_quarantine
                from app.models.postgres import TestStatus as _TS
                passes = sum(1 for s in statuses if s == _TS.PASSED)
                fails = sum(1 for s in statuses if s in (_TS.FAILED, _TS.BROKEN))
                request = await propose_quarantine(
                    project_id=proj_uuid,
                    test_fingerprint=tc.test_fingerprint,
                    test_name=tc.test_name,
                    suite_name=tc.suite_name,
                    detection_method="flaky_sentinel_agent",
                    flip_rate=round(failure_rate, 3),
                    flip_window_size=len(statuses),
                    pass_count=passes,
                    fail_count=fails,
                    rationale={
                        "method": "flaky_sentinel_pass_fail_ratio",
                        "flaky_since_build": flaky_since_build,
                        "last_stable_build": last_stable_build,
                        "sample_history": [str(s) for s in statuses[-10:]],
                        "change_summary": change_summary,
                    },
                )
                if request is not None:
                    quarantine_request_id = str(request.id)
            except Exception as exc:  # pragma: no cover — best-effort
                logger.debug("quarantine proposal failed", error=str(exc))

        return {
            "test_case_id": tc_id,
            "test_name": tc.test_name,
            "test_fingerprint": tc.test_fingerprint,
            "failure_rate": round(failure_rate, 3),
            "history_length": len(statuses),
            "flaky_since_build": flaky_since_build,
            "last_stable_build": last_stable_build,
            "change_summary": change_summary,
            "recommendation": recommendation,
            "status_history": [str(s) for s in statuses[-10:]],
            "quarantine_request_id": quarantine_request_id,
            # FLK-P4 structured verdict: {is_flaky, confidence, likely_cause,
            # likely_cause_code, evidence[], confidence_breakdown}.
            "verdict": verdict,
            "is_flaky": verdict["is_flaky"],
            "likely_cause": verdict["likely_cause"],
            # FLK-P5 granular step-level surgical attribution (None when the test
            # has no captured step snapshot / no failing step).
            "step_attribution": step_attribution,
        }
