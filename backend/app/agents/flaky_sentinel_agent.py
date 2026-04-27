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
from app.models.postgres import TestCase, TestCaseHistory, TestRun, TestStatus
from app.tools.fetch_build_changes import fetch_build_changes

logger = structlog.get_logger("agents.flaky_sentinel")


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
            await self.mark_stage_done(pipeline_run_id, result_data={"flaky_investigated": 0})
            return {"flaky_findings": []}

        findings = []
        capped_ids = flaky_test_ids[:10]  # Cap at 10 to avoid excessive processing

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

        return {"flaky_findings": findings}

    async def _investigate_flaky_test(
        self, db, tc: TestCase, tc_id: str, project_id: str
    ) -> dict | None:
        # Get last 20 history entries scoped to this project
        hist_result = await db.execute(
            select(TestCaseHistory)
            .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
            .where(
                TestCaseHistory.test_fingerprint == tc.test_fingerprint,
                TestRun.project_id == project_id,
            )
            .order_by(TestCaseHistory.created_at.desc())
            .limit(20)
        )
        history = hist_result.scalars().all()

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

        # Find flakiness onset: first run where status started alternating
        onset_index = 0
        for i in range(1, len(statuses)):
            if statuses[i] != statuses[i - 1]:
                onset_index = i
                break

        flaky_since_build = build_numbers[onset_index] if onset_index < len(build_numbers) else "unknown"
        last_stable_build = build_numbers[onset_index - 1] if onset_index > 0 else "unknown"

        # Compute failure rate
        failed_count = sum(1 for s in statuses if s in (TestStatus.FAILED, TestStatus.BROKEN))
        failure_rate = failed_count / len(statuses)

        # Fetch build changes around onset
        change_summary = "Build change lookup skipped."
        if flaky_since_build != "unknown" and last_stable_build != "unknown":
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

        # Quarantine recommendation
        if failure_rate > 0.5:
            recommendation = "QUARANTINE -- failing more than 50% of the time, blocking CI reliability"
        elif failure_rate > 0.25:
            recommendation = "INVESTIGATE URGENTLY -- high flakiness rate, significant noise source"
        else:
            recommendation = "MONITOR -- low flakiness rate, worth tracking but not yet critical"

        # Tier 1 item 3 — if the flip rate crosses the quarantine floor,
        # propose the test for QA Lead approval via the quarantine service.
        # Threshold matches the acceptance criterion (>= 20% flip rate over
        # 10 runs). The service is idempotent: repeat runs just refresh the
        # existing PROPOSED row. Feature-flag gated, never raises.
        quarantine_request_id: str | None = None
        if failure_rate >= 0.20 and len(statuses) >= 10:
            try:
                import uuid as _uuid
                from app.services.flaky_quarantine_service import propose_quarantine
                from app.models.postgres import TestStatus as _TS
                passes = sum(1 for s in statuses if s == _TS.PASSED)
                fails = sum(1 for s in statuses if s in (_TS.FAILED, _TS.BROKEN))
                # TestCase has no project_id column — derive it from state.
                # state["project_id"] is a str; coerce to UUID for the service contract.
                proj_uuid = project_id if isinstance(project_id, _uuid.UUID) else _uuid.UUID(str(project_id))
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
        }
