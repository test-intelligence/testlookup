"""
Test Health Agent.
Analyzes test code quality for AUTOMATION_DEFECT failures.
Detects anti-patterns: hardcoded waits, empty catch blocks, brittle selectors, missing assertions.

Signal source: uses S3/MinIO stored test attachments or MongoDB source artifacts.
Falls back to error_message ONLY when it contains actual test code snippets
(heuristic: presence of import/class/def/function keywords).
Emits `insufficient_source_context` when no actual source code is available.
"""
import re
import structlog

from app.agents.base import BaseAgent
from app.db.postgres import AsyncSessionLocal
from app.models.agent_contracts import TestHealthAgentOutput, validate_agent_contract
from app.models.postgres import TestCase

logger = structlog.get_logger("agents.test_health")

_ANTIPATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"Thread\.sleep\s*\(|time\.sleep\s*\(", re.I),
     "warning", "Hardcoded sleep detected -- use explicit waits or polling instead"),
    (re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}", re.S),
     "critical", "Empty catch block swallows exceptions -- failures may be silently ignored"),
    (re.compile(r"@Ignore|@pytest\.mark\.skip|@Disabled", re.I),
     "info", "Test is suppressed/ignored -- verify this is intentional"),
    (re.compile(r'By\.xpath\s*\(\s*["\'][^"\']*\d{3,}[^"\']*["\']', re.I),
     "warning", "XPath with long numeric index -- brittle, breaks on DOM changes"),
    (re.compile(r"static\s+(?:volatile\s+)?(?:final\s+)?\w+\s+\w+\s*=", re.I),
     "info", "Static mutable field in test class -- potential shared state between tests"),
]

# Heuristic: if text contains these keywords, it's likely test code (not just an error message)
_CODE_INDICATORS = re.compile(
    r"\b(import\s|from\s\w+\simport|class\s\w+|def\s\w+|function\s|public\s+void\s|@Test|@pytest)",
    re.IGNORECASE,
)


def _looks_like_source_code(text: str) -> bool:
    """Heuristic check: does this text look like actual source code?"""
    if not text or len(text) < 50:
        return False
    return bool(_CODE_INDICATORS.search(text))


def _analyze_source(source_code: str) -> list[dict]:
    violations = []
    for pattern, severity, description in _ANTIPATTERNS:
        matches = pattern.findall(source_code)
        if matches:
            violations.append({
                "pattern": description,
                "severity": severity,
                "occurrences": len(matches),
            })

    has_assertions = bool(re.search(r"assert|expect\(|should\.", source_code, re.I))
    if source_code.strip() and not has_assertions:
        violations.append({
            "pattern": "No assertions found -- test may pass vacuously",
            "severity": "critical",
            "occurrences": 1,
        })

    return violations


class TestHealthAgent(BaseAgent):
    stage_name = "test_health"

    async def run(self, state: dict) -> dict:
        pipeline_run_id: str = state["pipeline_run_id"]
        project_id: str = state["project_id"]
        analyses: dict = state.get("analyses", {})

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(project_id, {"status": "running", "message": "Analyzing test code health..."})

        automation_test_ids = [
            tc_id for tc_id, a in analyses.items()
            if a.get("failure_category") == "AUTOMATION_DEFECT"
        ]

        if not automation_test_ids:
            await self.log_decision(
                pipeline_run_id,
                decision_point="test_health_scope",
                chosen="skip_no_automation_defects",
                rationale=(
                    f"none of {len(analyses)} analysis/analyses was categorised "
                    "AUTOMATION_DEFECT, so there is no test to score"
                ),
                alternatives=["analyze"],
            )
            await self.mark_stage_done(pipeline_run_id, result_data={"analyzed": 0})
            return validate_agent_contract(
                TestHealthAgentOutput,
                {"test_health_findings": []},
                agent_name=self.stage_name,
                confidence=100,
                decision_reason="no_automation_defects_detected",
            )

        findings = []
        insufficient_count = 0
        examined = automation_test_ids[:15]
        # The cap silently drops candidates, and a finding with no source
        # context is a materially weaker one -- both belong in the trail.
        await self.log_decision(
            pipeline_run_id,
            decision_point="test_health_scope",
            chosen="analyze",
            rationale=(
                f"{len(automation_test_ids)} automation defect(s); analysing {len(examined)}"
                + (f" (capped, {len(automation_test_ids) - len(examined)} not examined)"
                   if len(automation_test_ids) > len(examined) else "")
            ),
            context={
                "candidates": len(automation_test_ids),
                "analyzed": len(examined),
                "capped": len(automation_test_ids) > len(examined),
            },
        )
        for tc_id in examined:
            finding = await self._analyze_test(tc_id)
            if finding:
                findings.append(finding)
                if finding.get("source_status") == "insufficient_source_context":
                    insufficient_count += 1

        if insufficient_count:
            await self.log_decision(
                pipeline_run_id,
                decision_point="source_context_availability",
                chosen="scored_without_source",
                rationale=(
                    f"{insufficient_count} of {len(findings)} finding(s) had no readable "
                    "source, so their health score rests on metadata alone"
                ),
                context={"insufficient_source": insufficient_count, "findings": len(findings)},
            )
        await self.mark_stage_done(
            pipeline_run_id,
            result_data={
                "analyzed": len(findings),
                "with_violations": sum(1 for f in findings if f.get("violations")),
                "insufficient_source": insufficient_count,
            },
        )
        await self.broadcast_progress(project_id, {
            "status": "completed",
            "message": f"Test health analyzed {len(findings)} automation defects"
            + (f" ({insufficient_count} lacked source code)" if insufficient_count else ""),
        })

        confidence_scores = [
            int(finding.get("health_score") or 0)
            for finding in findings
            if isinstance(finding.get("health_score"), (int, float))
        ]
        return validate_agent_contract(
            TestHealthAgentOutput,
            {"test_health_findings": findings},
            agent_name=self.stage_name,
            confidence=(
                int(sum(confidence_scores) / len(confidence_scores))
                if confidence_scores else 70
            ),
            evidence_refs=[
                {"type": "test_health_finding", "id": finding.get("test_case_id", "unknown")}
                for finding in findings
            ],
            decision_reason="test_health_analysis_completed",
        )

    async def _analyze_test(self, tc_id: str) -> dict | None:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(select(TestCase).where(TestCase.id == tc_id))
            tc = result.scalar_one_or_none()
            if not tc:
                return None

        # Try to get actual source code from MongoDB attachments
        source_code = await self._fetch_test_source(tc_id)

        # Fallback: use error_message ONLY if it looks like actual source code
        if not source_code and tc.error_message and _looks_like_source_code(tc.error_message):
            source_code = tc.error_message

        if not source_code:
            # No source available — emit explicit insufficient context
            return {
                "test_case_id": tc_id,
                "test_name": tc.test_name,
                "health_score": 70,
                "violations": [],
                "critical_count": 0,
                "warning_count": 0,
                "source_status": "insufficient_source_context",
                "recommendation": (
                    "Source code unavailable for anti-pattern analysis. "
                    "Failure is classified as AUTOMATION_DEFECT based on error characteristics. "
                    "Review test implementation manually."
                ),
            }

        violations = _analyze_source(source_code)
        critical_count = sum(1 for v in violations if v["severity"] == "critical")
        warning_count = sum(1 for v in violations if v["severity"] == "warning")

        if critical_count > 0:
            health_score = max(0, 40 - critical_count * 10)
            recommendation = "CRITICAL: Test has automation defects requiring immediate fix before re-running."
        elif warning_count > 0:
            health_score = max(40, 80 - warning_count * 10)
            recommendation = f"WARNING: {warning_count} test anti-pattern(s) detected. Refactor to improve reliability."
        else:
            health_score = 90
            recommendation = "Test code appears healthy. Failure may be due to environment or test data issues."

        return {
            "test_case_id": tc_id,
            "test_name": tc.test_name,
            "health_score": health_score,
            "violations": violations,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "source_status": "source_available",
            "recommendation": recommendation,
        }

    async def _fetch_test_source(self, tc_id: str) -> str | None:
        """Try to fetch test source code from MongoDB test artifacts."""
        try:
            from app.db.mongo import Collections, get_mongo_db
            mongo_db = get_mongo_db()
            doc = await mongo_db[Collections.AI_ANALYSIS_PAYLOADS].find_one(
                {"test_case_id": tc_id},
                {"analysis.test_source": 1, "analysis.source_code": 1},
            )
            if doc:
                analysis = doc.get("analysis") or {}
                return analysis.get("test_source") or analysis.get("source_code")
        except Exception as exc:
            logger.debug("Test source fetch failed", test_case_id=tc_id, error=str(exc))
        return None
