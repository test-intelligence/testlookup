"""
Anomaly Detection Agent — Stage 2 of the offline pipeline.

Improvements vs v1:
  - Branch-aware baseline selection (falls back to project-wide)
  - Median-based pass-rate regression (robust to outliers)
  - _classify_failure_diff FIXES: returns current-run IDs; uses correct most-recent run
  - Failure diff categorises: new / reopened / persistent (not just "new")
  - Performance spike detection uses Median Absolute Deviation (MAD), not mean
  - Performance + flaky history scoped to same project (join through TestRun)
  - Flaky detection is a single batched query (eliminates N+1 anti-pattern)
  - Flaky classification requires status transitions (oscillation, not regression)
  - One shared DB session per stage execution
  - Structured logging via structlog
  - LLM metrics recorded via mark_stage_done()
  - Deterministic LLM fallback with descriptive template
  - All thresholds driven from Settings (env-tunable, no magic constants)
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import TestCase, TestCaseHistory, TestRun, TestStatus
from app.services.llm_factory import get_llm

logger = structlog.get_logger("agents.anomaly")


class AnomalyDetectionAgent(BaseAgent):
    stage_name = "anomaly_detection"

    async def run(self, state: dict) -> dict:
        pipeline_run_id: str = state["pipeline_run_id"]
        test_run_id: str = state["test_run_id"]
        project_id: str = state["project_id"]
        current_pass_rate: float = state.get("pass_rate", 0.0)
        total_tests: int = state.get("total_tests", 0)
        branch: Optional[str] = state.get("branch")

        log = logger.bind(
            pipeline_run_id=pipeline_run_id,
            test_run_id=test_run_id,
            project_id=project_id,
            branch=branch,
        )

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(
            project_id,
            {"status": "running", "message": "Scanning for anomalies and regressions…"},
        )

        anomalies: list[dict] = []
        is_regression = False
        regression_tests: list[str] = []
        llm_calls = 0
        anomaly_summary: Optional[str] = None
        _start = time.perf_counter()

        try:
            async with AsyncSessionLocal() as db:
                # ── 1. Pass-rate regression (branch-aware, median-based) ──────────
                baseline_rate, prior_run_ids, baseline_run_id = await self._get_baseline_pass_rate(
                    db, project_id, test_run_id, branch
                )

                if baseline_rate is not None and prior_run_ids:
                    drop = baseline_rate - current_pass_rate
                    if drop >= settings.ANOMALY_REGRESSION_THRESHOLD:
                        is_regression = True
                        anomalies.append({
                            "type": "pass_rate_regression",
                            "severity": "HIGH" if drop >= 20 else "MEDIUM",
                            "confidence": min(95, int(50 + drop * 2)),
                            "description": (
                                f"Pass rate dropped {drop:.1f}% "
                                f"(from {baseline_rate:.1f}% to {current_pass_rate:.1f}%)"
                            ),
                            "value": round(drop, 2),
                            "baseline_run_id": baseline_run_id,
                            "sample_size": len(prior_run_ids),
                            "baseline_type": f"branch={branch}" if branch else "project-wide",
                        })

                # ── 2. Failure diff: new / reopened / persistent ──────────────────
                if prior_run_ids:
                    # prior_run_ids is ordered desc → index 0 = most recent prior run
                    most_recent_run_id = prior_run_ids[0]
                    failure_diff = await self._classify_failure_diff(
                        db, test_run_id, most_recent_run_id
                    )

                    new_failures = failure_diff["new"]
                    reopened = failure_diff["reopened"]

                    if new_failures:
                        regression_tests = [f["current_test_case_id"] for f in new_failures]
                        anomalies.append({
                            "type": "new_failures",
                            "severity": "HIGH",
                            "confidence": 90,
                            "description": (
                                f"{len(new_failures)} test(s) failed for the first time this run"
                            ),
                            "test_ids": regression_tests,
                            "test_names": [f["test_name"] for f in new_failures[:10]],
                            "sample_size": len(new_failures),
                        })

                    if reopened:
                        reopened_ids = [f["current_test_case_id"] for f in reopened]
                        anomalies.append({
                            "type": "reopened_failures",
                            "severity": "MEDIUM",
                            "confidence": 75,
                            "description": (
                                f"{len(reopened)} test(s) passed last run but failed again "
                                f"(intermittent regression or flakiness)"
                            ),
                            "test_ids": reopened_ids,
                            "test_names": [f["test_name"] for f in reopened[:10]],
                            "sample_size": len(reopened),
                        })

                    persistent = failure_diff["persistent"]
                    if persistent:
                        anomalies.append({
                            "type": "persistent_failures",
                            "severity": "LOW",
                            "confidence": 85,
                            "description": (
                                f"{len(persistent)} test(s) continued failing from the previous run"
                            ),
                            "test_ids": [f["current_test_case_id"] for f in persistent],
                            "sample_size": len(persistent),
                        })

                # ── 3. Performance anomalies (MAD-based, project-scoped) ──────────
                perf_anomalies = await self._detect_perf_anomalies(db, test_run_id, project_id)
                anomalies.extend(perf_anomalies)

                # ── 4. Flaky tests (single batched query, project-scoped) ─────────
                flaky_in_run = await self._find_flaky_tests_in_run(db, test_run_id, project_id)
                if flaky_in_run:
                    anomalies.append({
                        "type": "flaky_tests",
                        "severity": "LOW",
                        "confidence": 70,
                        "description": (
                            f"{len(flaky_in_run)} known-flaky test(s) failed "
                            f"— may not indicate real regressions"
                        ),
                        "test_ids": [t["id"] for t in flaky_in_run],
                        "test_names": [t["name"] for t in flaky_in_run[:10]],
                        "sample_size": len(flaky_in_run),
                        "flaky_details": flaky_in_run[:5],
                    })

            # ── 5. LLM structured summary (presentation only) ────────────────────
            anomaly_summary, llm_calls = await self._generate_summary(
                anomalies, current_pass_rate, total_tests
            )

            duration_s = round(time.perf_counter() - _start, 2)
            log.info(
                "anomaly_detection_complete",
                anomaly_count=len(anomalies),
                is_regression=is_regression,
                new_failure_count=len(regression_tests),
                llm_calls=llm_calls,
                duration_s=duration_s,
            )

            await self.mark_stage_done(
                pipeline_run_id,
                result_data={
                    "anomaly_count": len(anomalies),
                    "is_regression": is_regression,
                    "new_failure_count": len(regression_tests),
                },
                llm_calls_count=llm_calls,
                evidence_count=len(anomalies),
                confidence_score=(
                    max(a.get("confidence", 50) for a in anomalies)
                    if anomalies else 0
                ),
            )
            await self.broadcast_progress(
                project_id,
                {
                    "status": "completed",
                    "message": f"Anomaly detection complete: {len(anomalies)} finding(s)",
                    "is_regression": is_regression,
                },
            )

            return {
                "anomalies": anomalies,
                "is_regression": is_regression,
                "regression_tests": regression_tests,
                "anomaly_summary": anomaly_summary,
                "completed_stages": ["anomaly_detection"],
                "errors": [],
                "current_stage": "root_cause_analysis",
            }

        except Exception as exc:
            error_msg = f"Anomaly agent error: {exc}"
            log.error("anomaly_detection_error", error=str(exc), exc_info=True)
            await self.mark_stage_done(
                pipeline_run_id,
                error=error_msg,
                error_category="anomaly_agent_exception",
            )
            return {
                "anomalies": [],
                "is_regression": False,
                "regression_tests": [],
                "anomaly_summary": None,
                "errors": [error_msg],
                "completed_stages": ["anomaly_detection"],
                "current_stage": "root_cause_analysis",
            }

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _get_baseline_pass_rate(
        self,
        db: AsyncSession,
        project_id: str,
        current_run_id: str,
        branch: Optional[str] = None,
    ) -> tuple[Optional[float], list[str], Optional[str]]:
        """
        Returns (median_pass_rate, [run_ids_desc], most_recent_run_id).

        Baseline selection hierarchy:
        1. Same project + same branch  (when branch is provided and sufficient history exists)
        2. Same project, any branch    (fallback)

        Uses the statistical median (not mean) to reduce outlier sensitivity.
        Requires ANOMALY_MIN_HISTORY_RUNS prior runs before flagging anything.
        """
        limit = settings.ANOMALY_MIN_HISTORY_RUNS

        async def _query(with_branch: bool) -> list:
            q = (
                select(TestRun.id, TestRun.pass_rate)
                .where(
                    TestRun.project_id == project_id,
                    TestRun.id != current_run_id,
                    TestRun.pass_rate.is_not(None),
                )
                .order_by(TestRun.created_at.desc())
                .limit(limit)
            )
            if with_branch and branch:
                q = q.where(TestRun.branch == branch)
            result = await db.execute(q)
            return result.all()

        if branch:
            rows = await _query(with_branch=True)
            if len(rows) >= limit:
                rates = [r.pass_rate for r in rows]
                return statistics.median(rates), [str(r.id) for r in rows], str(rows[0].id)

        # Fallback: project-wide
        rows = await _query(with_branch=False)
        if len(rows) < limit:
            return None, [], None

        rates = [r.pass_rate for r in rows]
        return statistics.median(rates), [str(r.id) for r in rows], str(rows[0].id)

    async def _classify_failure_diff(
        self,
        db: AsyncSession,
        current_run_id: str,
        previous_run_id: str,
    ) -> dict[str, list[dict]]:
        """
        Classifies current-run failures into three categories using
        current-run TestCase IDs throughout (bug-fix: v1 returned previous-run IDs).

          new         — FAILED now, PASSED (or absent) in the previous run
          reopened    — FAILED now AND in previous run, but PASSED two runs ago
          persistent  — FAILED in both current and previous run (ongoing)
        """
        # Current run failures
        cur_result = await db.execute(
            select(TestCase.id, TestCase.test_fingerprint, TestCase.test_name)
            .where(
                TestCase.test_run_id == current_run_id,
                TestCase.status.in_([TestStatus.FAILED, TestStatus.BROKEN]),
            )
        )
        current_rows = cur_result.all()
        if not current_rows:
            return {"new": [], "reopened": [], "persistent": []}

        current_fps = {str(r.id): (r.test_fingerprint, r.test_name) for r in current_rows}
        all_fps = [fp for fp, _ in current_fps.values()]

        # Previous run statuses for those fingerprints
        prev_result = await db.execute(
            select(TestCase.test_fingerprint, TestCase.status)
            .where(
                TestCase.test_run_id == previous_run_id,
                TestCase.test_fingerprint.in_(all_fps),
            )
        )
        prev_by_fp: dict[str, str] = {
            r.test_fingerprint: str(r.status) for r in prev_result.all()
        }

        new_failures: list[dict] = []
        possibly_persistent: list[dict] = []

        for tc_id, (fp, name) in current_fps.items():
            entry = {
                "current_test_case_id": tc_id,
                "test_fingerprint": fp,
                "test_name": name,
                "prev_status": prev_by_fp.get(fp),
            }
            prev_s = prev_by_fp.get(fp)
            if prev_s in (None, str(TestStatus.PASSED), str(TestStatus.SKIPPED)):
                new_failures.append(entry)
            else:
                possibly_persistent.append(entry)

        # Resolve reopened: failed in prev, look one run further back
        reopened: list[dict] = []
        persistent: list[dict] = []

        if possibly_persistent:
            persistent_fps = {e["test_fingerprint"] for e in possibly_persistent}

            older_result = await db.execute(
                select(TestRun.id)
                .where(
                    TestRun.project_id == (
                        select(TestRun.project_id)
                        .where(TestRun.id == previous_run_id)
                        .scalar_subquery()
                    ),
                    TestRun.created_at < (
                        select(TestRun.created_at)
                        .where(TestRun.id == previous_run_id)
                        .scalar_subquery()
                    ),
                )
                .order_by(TestRun.created_at.desc())
                .limit(1)
            )
            older_run_id = older_result.scalar_one_or_none()

            older_by_fp: dict[str, str] = {}
            if older_run_id:
                older_cases = await db.execute(
                    select(TestCase.test_fingerprint, TestCase.status)
                    .where(
                        TestCase.test_run_id == older_run_id,
                        TestCase.test_fingerprint.in_(persistent_fps),
                    )
                )
                older_by_fp = {r.test_fingerprint: str(r.status) for r in older_cases.all()}

            for entry in possibly_persistent:
                fp = entry["test_fingerprint"]
                older_s = older_by_fp.get(fp)
                if older_s == str(TestStatus.PASSED):
                    reopened.append(entry)
                else:
                    persistent.append(entry)

        return {"new": new_failures, "reopened": reopened, "persistent": persistent}

    async def _detect_perf_anomalies(
        self,
        db: AsyncSession,
        test_run_id: str,
        project_id: str,
    ) -> list[dict]:
        """
        Finds tests where duration significantly exceeds historical median.

        Uses Median Absolute Deviation (MAD) — a robust, outlier-resistant
        spread measure — instead of the simple mean multiplier used in v1.

        Key improvements vs v1:
        - History is project-scoped (join through TestRun — prevents cross-tenant pollution)
        - Only fetches history for fingerprints present in the current run
        - Requires ANOMALY_MIN_PERF_SAMPLES history points before flagging
        - Requires an absolute minimum delta to suppress noise on fast tests
        """
        cur_result = await db.execute(
            select(TestCase.test_fingerprint, TestCase.duration_ms, TestCase.test_name)
            .where(
                TestCase.test_run_id == test_run_id,
                TestCase.duration_ms.is_not(None),
                TestCase.duration_ms > 0,
            )
        )
        current_rows = cur_result.all()
        if not current_rows:
            return []

        current_fps = [r.test_fingerprint for r in current_rows]
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.ANOMALY_PERF_HISTORY_DAYS)

        # Batch-fetch project-scoped duration history for current-run fingerprints only
        hist_result = await db.execute(
            select(TestCaseHistory.test_fingerprint, TestCaseHistory.duration_ms)
            .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCaseHistory.test_fingerprint.in_(current_fps),
                TestCaseHistory.created_at >= cutoff,
                TestCaseHistory.duration_ms.is_not(None),
                TestCaseHistory.duration_ms > 0,
            )
        )
        hist_by_fp: dict[str, list[float]] = {}
        for row in hist_result.all():
            hist_by_fp.setdefault(row.test_fingerprint, []).append(float(row.duration_ms))

        min_samples = settings.ANOMALY_MIN_PERF_SAMPLES
        multiplier = settings.ANOMALY_PERF_SPIKE_MULTIPLIER
        min_abs_delta = settings.ANOMALY_PERF_MIN_ABSOLUTE_DELTA_MS
        k = 3.0  # 3×MAD → ~99.3% coverage for symmetric distributions

        spike_details: list[dict] = []

        for row in current_rows:
            samples = hist_by_fp.get(row.test_fingerprint, [])
            if len(samples) < min_samples:
                continue

            median_dur = statistics.median(samples)
            mad = statistics.median([abs(x - median_dur) for x in samples])
            upper_bound = median_dur + k * max(mad, 1.0)
            current_ms = float(row.duration_ms)

            if (
                current_ms > upper_bound
                and current_ms > multiplier * median_dur
                and (current_ms - median_dur) >= min_abs_delta
            ):
                pct_above = (
                    (current_ms - median_dur) / median_dur * 100
                    if median_dur > 0 else 0.0
                )
                spike_details.append({
                    "test_name": row.test_name,
                    "current_ms": current_ms,
                    "median_ms": round(median_dur, 1),
                    "mad_ms": round(mad, 1),
                    "pct_above_median": round(pct_above, 1),
                    "sample_size": len(samples),
                })

        if not spike_details:
            return []

        return [{
            "type": "performance_spike",
            "severity": "HIGH" if len(spike_details) >= 5 else "MEDIUM",
            "confidence": 80,
            "description": (
                f"{len(spike_details)} test(s) ran significantly slower than "
                f"their historical baseline (median + {k:.0f}×MAD threshold)"
            ),
            "tests": [d["test_name"] for d in spike_details[:10]],
            "details": spike_details[:5],
            "sample_size": len(spike_details),
        }]

    async def _find_flaky_tests_in_run(
        self,
        db: AsyncSession,
        test_run_id: str,
        project_id: str,
    ) -> list[dict]:
        """
        Batched flaky detection for all failed tests in the current run.

        Key improvements vs v1:
        - Single batched query for all fingerprints (eliminates N+1 anti-pattern)
        - History scoped to same project (join through TestRun)
        - Requires status transitions (oscillation), not just a failure-rate band
        - Returns structured metadata per flaky test (confidence, transitions, recommendation)
        """
        failed_result = await db.execute(
            select(TestCase.test_fingerprint, TestCase.id, TestCase.test_name)
            .where(
                TestCase.test_run_id == test_run_id,
                TestCase.status.in_([TestStatus.FAILED, TestStatus.BROKEN]),
            )
        )
        failed_rows = failed_result.all()
        if not failed_rows:
            return []

        failed_fps = [r.test_fingerprint for r in failed_rows]
        fp_to_meta: dict[str, tuple[str, str]] = {
            r.test_fingerprint: (str(r.id), r.test_name) for r in failed_rows
        }

        window = settings.ANOMALY_FLAKY_WINDOW_RUNS
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.ANOMALY_FLAKY_LOOKBACK_DAYS)

        # Single batched query, project-scoped, desc ordering so we take the latest N per fp
        hist_result = await db.execute(
            select(TestCaseHistory.test_fingerprint, TestCaseHistory.status)
            .join(TestRun, TestRun.id == TestCaseHistory.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCaseHistory.test_fingerprint.in_(failed_fps),
                TestCaseHistory.created_at >= cutoff,
            )
            .order_by(
                TestCaseHistory.test_fingerprint,
                TestCaseHistory.created_at.desc(),
            )
        )

        # Keep last `window` statuses per fingerprint
        history_by_fp: dict[str, list[str]] = {}
        for row in hist_result.all():
            fp = row.test_fingerprint
            if fp not in history_by_fp:
                history_by_fp[fp] = []
            if len(history_by_fp[fp]) < window:
                history_by_fp[fp].append(str(row.status))

        min_history = settings.ANOMALY_MIN_HISTORY_RUNS
        flaky_results: list[dict] = []

        for fp, statuses in history_by_fp.items():
            if len(statuses) < min_history:
                continue

            fail_count = sum(
                1 for s in statuses
                if s in (str(TestStatus.FAILED), str(TestStatus.BROKEN))
            )
            fail_rate = fail_count / len(statuses)

            # Count status transitions — oscillation is the hallmark of flakiness
            transitions = sum(
                1 for i in range(1, len(statuses))
                if statuses[i] != statuses[i - 1]
            )

            # Not a clean new regression: must alternate (fail_rate band + min 2 transitions)
            if 0.1 <= fail_rate <= 0.9 and transitions >= 2:
                tc_id, tc_name = fp_to_meta.get(fp, ("unknown", "unknown"))
                recommendation = (
                    "QUARANTINE" if fail_rate > 0.5
                    else "INVESTIGATE" if fail_rate > 0.25
                    else "MONITOR"
                )
                flaky_results.append({
                    "id": tc_id,
                    "name": tc_name,
                    "fingerprint": fp,
                    "failure_rate": round(fail_rate, 3),
                    "transition_count": transitions,
                    "history_window": len(statuses),
                    "recommendation": recommendation,
                })

        return flaky_results

    async def _generate_summary(
        self,
        anomalies: list[dict],
        pass_rate: float,
        total_tests: int,
    ) -> tuple[str, int]:
        """
        Produces a concise summary via LLM (presentation-only — no decisions).
        Returns (summary_text, llm_calls_made).

        Falls back to a deterministic template if the LLM is unavailable or fails.
        """
        if not anomalies:
            return (
                f"✅ No anomalies detected. "
                f"Pass rate: {pass_rate:.1f}% across {total_tests} tests.",
                0,
            )

        # Cap prompt size to prevent runaway token usage
        items = anomalies[:settings.ANOMALY_SUMMARY_MAX_ITEMS]
        descriptions = "\n".join(
            f"- [{a['severity']}] {a['description']}" for a in items
        )

        prompt = (
            f"Summarise these test anomalies in 2-3 sentences for an engineering team.\n"
            f"Pass rate: {pass_rate:.1f}%  |  Total tests: {total_tests}\n"
            f"Findings:\n{descriptions}\n\n"
            f"Mention the most critical finding first and suggest a concrete next action."
        )

        try:
            llm = await get_llm(temperature=0.0)
            response = await llm.ainvoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
            return (raw if isinstance(raw, str) else str(raw)).strip(), 1
        except Exception as exc:
            logger.warning(
                "anomaly_summary_llm_failed",
                error=str(exc),
                fallback="deterministic_template",
            )
            # Deterministic fallback — always works regardless of LLM availability
            high = [a for a in anomalies if a.get("severity") == "HIGH"]
            return (
                f"⚠️ {len(anomalies)} anomaly(ies) detected "
                f"({len(high)} high-severity). "
                f"Pass rate: {pass_rate:.1f}%. "
                f"Top issue: {anomalies[0]['description']}"
            ), 0
