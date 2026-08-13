"""
Ingestion Agent — Stage 1 of the offline pipeline.

Validates that the test run data is fully ingested, extracts the list of
failed/broken tests, and enriches the workflow state with run metadata.
"""
import structlog

from sqlalchemy import select

from app.agents.base import BaseAgent
from app.db.postgres import AsyncSessionLocal
from app.models.agent_contracts import IngestionAgentOutput, validate_agent_contract
from app.models.postgres import TestCase, TestRun, TestStatus

logger = structlog.get_logger("agents.ingestion")

_FAILED_STATUSES = {TestStatus.FAILED, TestStatus.BROKEN}


class IngestionAgent(BaseAgent):
    stage_name = "ingestion"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state["pipeline_run_id"]
        test_run_id = state["test_run_id"]

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(
            state["project_id"],
            {"status": "running", "message": "Validating ingested test data…"},
        )

        try:
            run_data, failed_ids = await self._extract_run_data(test_run_id)
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={"total_tests": run_data["total_tests"], "failed_count": len(failed_ids)},
            )
            await self.broadcast_progress(
                state["project_id"],
                {
                    "status": "completed",
                    "message": f"Ingestion validated: {run_data['total_tests']} tests, {len(failed_ids)} failures",
                },
            )
            output = {
                "test_run_data": run_data,
                "branch": run_data.get("branch"),
                "failed_test_ids": failed_ids,
                "total_tests": run_data["total_tests"],
                "pass_rate": run_data.get("pass_rate") or 0.0,
                "ingestion_enriched": True,
                "completed_stages": ["ingestion"],
                "errors": [],
                "current_stage": "anomaly_detection",
            }
            return validate_agent_contract(
                IngestionAgentOutput,
                output,
                agent_name=self.stage_name,
                confidence=100,
                decision_reason="Validated persisted test run facts and failed test IDs",
            )
        except Exception as exc:
            error_msg = f"Ingestion agent error: {exc}"
            logger.error(error_msg, exc_info=True)
            await self.mark_stage_done(pipeline_run_id, error=error_msg)
            output = {
                "ingestion_enriched": False,
                "branch": None,
                "failed_test_ids": [],
                "total_tests": 0,
                "pass_rate": 0.0,
                "errors": [error_msg],
                "completed_stages": ["ingestion"],
                "current_stage": "anomaly_detection",
            }
            return validate_agent_contract(
                IngestionAgentOutput,
                output,
                agent_name=self.stage_name,
                fallback_used=True,
                confidence=0,
                decision_reason=error_msg,
            )

    async def _extract_run_data(self, test_run_id: str) -> tuple[dict, list[str]]:
        """Fetch test run metadata and IDs of all failed/broken tests."""
        async with AsyncSessionLocal() as db:
            run_result = await db.execute(
                select(TestRun).where(TestRun.id == test_run_id)
            )
            run = run_result.scalar_one_or_none()
            if not run:
                raise ValueError(f"TestRun {test_run_id} not found")

            # Get failed test IDs
            cases_result = await db.execute(
                select(TestCase.id, TestCase.test_name, TestCase.class_name,
                       TestCase.suite_name, TestCase.severity, TestCase.error_message)
                .where(
                    TestCase.test_run_id == test_run_id,
                    TestCase.status.in_([s.value for s in _FAILED_STATUSES]),
                )
            )
            failed_rows = cases_result.all()

            run_data = {
                "id": str(run.id),
                "build_number": run.build_number,
                "branch": run.branch,
                "jenkins_job": run.jenkins_job,
                "total_tests": run.total_tests,
                "passed_tests": run.passed_tests,
                "failed_tests": run.failed_tests,
                "skipped_tests": run.skipped_tests,
                "broken_tests": run.broken_tests,
                "unknown_tests": run.unknown_tests,
                "pass_rate": run.pass_rate,
                "duration_ms": run.duration_ms,
                "status": getattr(run.status, "value", run.status) or "UNKNOWN",
            }
            failed_ids = [str(row.id) for row in failed_rows]

            return run_data, failed_ids
