"""
Regression Watchman Agent.

Compares the current test run against the last N healthy/passing baseline runs
and classifies each failure cluster as one of:

  - new_regression        — failed in current run, passed in all recent baselines
  - known_flaky_recurrence — historically flaky, expected to fail intermittently
  - environmental_anomaly — failures correlated with infra/environment signals,
                            not reproducible in isolation

Input consumed from WorkflowState:
  - test_run_id, project_id
  - failure_clusters (from ClusterAgent)
  - analyses (from AnalysisAgent)
  - is_regression, regression_tests (from AnomalyAgent)

Output added to WorkflowState:
  - regression_classification: dict mapping cluster_id → classification + evidence

Standalone endpoint: POST /api/v1/agents/regression-watch
"""
import asyncio
import json
import structlog
from typing import Any

from sqlalchemy import func, select

from app.agents.base import BaseAgent
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import FailureCategory, TestCase, TestRun, TestStatus
from app.services.category_normalizer import normalize_category
from app.services.llm_factory import get_llm
from app.services.llm_json_parser import parse_llm_json
from app.services.prompt_redaction import redact_text

_LLM_CLASSIFY_TIMEOUT = min(90, settings.AI_TIMEOUT_SECONDS)

logger = structlog.get_logger("agents.regression_watchman")

_BASELINE_RUN_COUNT = 10       # Look back this many recent passing runs
_FLAKY_MIN_OCCURRENCES = 2     # A cluster must recur >= 2 times to be "known flaky"
_INFRA_THRESHOLD = 0.5         # If >= 50% of cluster errors are INFRASTRUCTURE, it's env anomaly
_MIN_BASELINE_RUNS = 3         # Minimum baseline runs to trust history verdict
_MIN_FINGERPRINT_OVERLAP = 1   # Minimum fingerprint matches to count as "seen in baseline"


_CLASSIFY_PROMPT = """\
You are a QA regression analyst. For each failure cluster, classify it as one of:
  - "new_regression": first-time failure, not seen in recent baseline runs
  - "known_flaky_recurrence": test has flaked before, not caused by code changes
  - "environmental_anomaly": failure caused by infra/environment, not the application

Cluster data:
{clusters_json}

Historical context:
{history_json}

Respond ONLY with a JSON object mapping cluster_id to a classification:
{{
  "cl_001": {{
    "classification": "new_regression" | "known_flaky_recurrence" | "environmental_anomaly",
    "confidence": 0-100,
    "evidence": "one sentence explaining why"
  }}
}}"""


class RegressionWatchman(BaseAgent):
    stage_name = "regression_watchman"

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state["pipeline_run_id"]
        project_id = state["project_id"]

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(
            project_id,
            {"status": "running", "message": "Classifying regressions vs flaky vs environmental..."},
        )

        try:
            classification = await self._classify(state)
        except Exception as exc:
            logger.error("RegressionWatchman failed", error=str(exc), exc_info=True)
            classification = {}
            await self.mark_stage_done(
                pipeline_run_id,
                error=str(exc),
                error_category="classification_error",
            )
            return {
                "regression_classification": {},
                "completed_stages": ["regression_watchman"],
                "errors": [str(exc)],
                "current_stage": "defect_commander",
            }

        await self.mark_stage_done(
            pipeline_run_id,
            result_data={"classified_clusters": len(classification)},
        )
        await self.broadcast_progress(
            project_id,
            {"status": "completed", "message": f"Classified {len(classification)} failure clusters"},
        )

        return {
            "regression_classification": classification,
            "completed_stages": ["regression_watchman"],
            "errors": [],
            "current_stage": "defect_commander",
        }

    # -- Classification logic --------------------------------------------------

    async def _classify(self, state: dict) -> dict:
        failure_clusters: list[dict] = state.get("failure_clusters", [])
        analyses: dict = state.get("analyses", {})
        test_run_id: str = state["test_run_id"]
        project_id: str = state["project_id"]

        if not failure_clusters:
            return {}

        # Resolve current run branch for branch-aware baseline lookup
        current_branch = await self._resolve_run_branch(test_run_id)

        # Step 1: deterministic pre-classification using postgres history
        history = await self._fetch_cluster_history(
            test_run_id=test_run_id,
            project_id=project_id,
            failure_clusters=failure_clusters,
            branch=current_branch,
        )

        det_results = self._deterministic_classify(failure_clusters, analyses, history)

        # Step 2: LLM refines uncertain cases
        uncertain = {k: v for k, v in det_results.items() if v.get("confidence", 100) < 70}
        if uncertain:
            llm_results = await self._llm_classify(uncertain, failure_clusters, history)
            det_results.update(llm_results)

        return det_results

    def _deterministic_classify(
        self,
        clusters: list[dict],
        analyses: dict,
        history: dict,
    ) -> dict:
        result: dict[str, dict] = {}

        for cluster in clusters:
            cid = cluster.get("cluster_id", cluster.get("id", ""))
            member_ids = cluster.get("member_test_ids", [])

            # Guard: empty or missing cluster
            if not cid or not member_ids:
                result[cid or "unknown"] = {
                    "classification": "new_regression",
                    "confidence": 20,
                    "evidence": "Empty cluster — insufficient data for classification",
                    "reason_code": "EMPTY_CLUSTER",
                }
                continue

            # Normalize category to string value for comparison
            infra_count = 0
            flaky_count = 0
            for mid in member_ids:
                analysis = analyses.get(mid, {})
                raw_cat = analysis.get("failure_category", "")
                cat_str = normalize_category(raw_cat)
                if cat_str == FailureCategory.INFRASTRUCTURE.value:
                    infra_count += 1
                if analysis.get("is_flaky", False):
                    flaky_count += 1

            member_count = max(len(member_ids), 1)
            infra_frac = infra_count / member_count
            flaky_frac = flaky_count / member_count

            # Check history
            hist = history.get(cid, {})
            recent_occurrences = hist.get("recent_occurrences", 0)
            seen_in_baseline = hist.get("seen_in_baseline", False)
            baseline_run_count = hist.get("baseline_run_count", 0)
            low_evidence = baseline_run_count < _MIN_BASELINE_RUNS

            if infra_frac >= _INFRA_THRESHOLD:
                result[cid] = {
                    "classification": "environmental_anomaly",
                    "confidence": min(100, int(infra_frac * 100) + 20),
                    "evidence": f"{infra_count}/{len(member_ids)} failures classified as INFRASTRUCTURE",
                    "reason_code": "INFRA_FRACTION_HIGH",
                }
            elif flaky_frac >= 0.5 and recent_occurrences >= _FLAKY_MIN_OCCURRENCES:
                result[cid] = {
                    "classification": "known_flaky_recurrence",
                    "confidence": min(100, int(flaky_frac * 80) + recent_occurrences * 5),
                    "evidence": f"Cluster has flaky history ({recent_occurrences} prior occurrences)",
                    "reason_code": "FLAKY_HISTORY",
                }
            elif not seen_in_baseline and not low_evidence:
                result[cid] = {
                    "classification": "new_regression",
                    "confidence": 80,
                    "evidence": "Not seen in recent baseline runs — likely new regression",
                    "reason_code": "NOT_IN_BASELINE",
                }
            elif low_evidence:
                # Not enough baseline data to be confident
                result[cid] = {
                    "classification": "new_regression",
                    "confidence": 35,
                    "evidence": f"Insufficient baseline history ({baseline_run_count} runs < {_MIN_BASELINE_RUNS} minimum) — uncertain",
                    "reason_code": "LOW_EVIDENCE",
                }
            else:
                # Uncertain — mark for LLM refinement
                result[cid] = {
                    "classification": "new_regression",
                    "confidence": 45,
                    "evidence": "Deterministic classification inconclusive",
                    "reason_code": "INCONCLUSIVE",
                }

        return result

    async def _llm_classify(
        self,
        uncertain: dict,
        all_clusters: list[dict],
        history: dict,
    ) -> dict:
        cluster_lookup = {c.get("cluster_id", c.get("id", "")): c for c in all_clusters}
        cluster_subset = [cluster_lookup[cid] for cid in uncertain if cid in cluster_lookup]

        prompt = redact_text(_CLASSIFY_PROMPT.format(
            clusters_json=json.dumps(cluster_subset[:10], default=str),
            history_json=json.dumps({k: history.get(k, {}) for k in uncertain}, default=str),
        ))

        try:
            llm = await get_llm(temperature=0.0)
            response = await asyncio.wait_for(
                llm.ainvoke(prompt),
                timeout=_LLM_CLASSIFY_TIMEOUT,
            )
            _raw = response.content if hasattr(response, "content") else str(response)
            content = str(_raw).strip()

            parsed, error = parse_llm_json(
                content,
                context="regression_watchman_classify",
            )
            if error:
                logger.warning("LLM classification parse failed", reason=error)
                return {}
            return parsed
        except asyncio.TimeoutError:
            logger.warning(
                "LLM classification timed out — returning deterministic results",
                timeout=_LLM_CLASSIFY_TIMEOUT,
            )
            return {}
        except Exception as exc:
            logger.warning("LLM classification failed", error=str(exc))

        return {}

    # -- Helpers ---------------------------------------------------------------

    async def _resolve_run_branch(self, test_run_id: str) -> str | None:
        """Look up the branch of the current test run."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(TestRun.branch).where(TestRun.id == test_run_id)
                )
                row = result.first()
                return row.branch if row and row.branch else None
        except Exception:
            return None

    async def _find_baseline_runs(
        self,
        db,
        project_id: str,
        test_run_id: str,
        branch: str | None,
    ) -> list[str]:
        """Find recent passing baseline runs, optionally filtered by branch."""
        q = (
            select(TestRun.id)
            .where(
                TestRun.project_id == project_id,
                TestRun.id != test_run_id,
                TestRun.pass_rate >= settings.RELEASE_PASS_RATE_THRESHOLD * 0.85,
            )
            .order_by(TestRun.created_at.desc())
            .limit(_BASELINE_RUN_COUNT)
        )
        if branch:
            q = q.where(TestRun.branch == branch)
        result = await db.execute(q)
        return [str(r.id) for r in result.all()]

    # -- History fetch (fingerprint-based, branch-aware) -----------------------

    async def _fetch_cluster_history(
        self,
        test_run_id: str,
        project_id: str,
        failure_clusters: list[dict],
        branch: str | None = None,
    ) -> dict:
        """
        For each cluster, check if similar failures appeared in recent baseline runs
        using fingerprint overlap (not ilike on error messages).

        Branch-aware: prefers same-branch baselines, falls back to all-branch.
        Returns dict: cluster_id -> {seen_in_baseline: bool, recent_occurrences: int}
        """
        history: dict[str, dict] = {}

        async with AsyncSessionLocal() as db:
            # Branch-aware baseline lookup: try same branch first, fallback to all
            baseline_run_ids = await self._find_baseline_runs(
                db, project_id, test_run_id, branch
            )

            if not baseline_run_ids and branch:
                # Fallback: try without branch filter
                baseline_run_ids = await self._find_baseline_runs(
                    db, project_id, test_run_id, None
                )

            if not baseline_run_ids:
                return {
                    c.get("cluster_id", c.get("id", "")): {
                        "seen_in_baseline": False,
                        "recent_occurrences": 0,
                        "baseline_run_count": 0,
                    }
                    for c in failure_clusters
                }

            # Collect all member test IDs across clusters for bulk fingerprint lookup
            all_member_ids: list[str] = []
            for cluster in failure_clusters:
                all_member_ids.extend(cluster.get("member_test_ids", []))

            # Bulk fetch fingerprints for all member tests
            fingerprint_map: dict[str, str] = {}
            if all_member_ids:
                fp_result = await db.execute(
                    select(TestCase.id, TestCase.test_fingerprint)
                    .where(TestCase.id.in_(all_member_ids))
                )
                for row in fp_result.all():
                    if row.test_fingerprint:
                        fingerprint_map[str(row.id)] = row.test_fingerprint

            # For each cluster, check fingerprint overlap with baseline failures
            for cluster in failure_clusters:
                cid = cluster.get("cluster_id", cluster.get("id", ""))
                member_ids = cluster.get("member_test_ids", [])

                if not member_ids:
                    history[cid] = {
                        "seen_in_baseline": False,
                        "recent_occurrences": 0,
                        "baseline_run_count": len(baseline_run_ids),
                    }
                    continue

                # Get fingerprints for this cluster's members
                cluster_fingerprints = list({
                    fingerprint_map[mid]
                    for mid in member_ids
                    if mid in fingerprint_map
                })

                if not cluster_fingerprints:
                    history[cid] = {
                        "seen_in_baseline": False,
                        "recent_occurrences": 0,
                        "baseline_run_count": len(baseline_run_ids),
                    }
                    continue

                # Count matching fingerprints in baseline runs (failed/broken)
                overlap_q = await db.execute(
                    select(func.count(func.distinct(TestCase.test_fingerprint)))
                    .where(
                        TestCase.test_run_id.in_(baseline_run_ids),
                        TestCase.status.in_([TestStatus.FAILED.value, TestStatus.BROKEN.value]),
                        TestCase.test_fingerprint.in_(cluster_fingerprints),
                    )
                )
                overlap_count = int(overlap_q.scalar() or 0)

                history[cid] = {
                    "seen_in_baseline": overlap_count >= _MIN_FINGERPRINT_OVERLAP,
                    "recent_occurrences": overlap_count,
                    "baseline_run_count": len(baseline_run_ids),
                    "cluster_fingerprints": len(cluster_fingerprints),
                }

        return history


# -- Standalone runner (for POST /api/v1/agents/regression-watch) --------------

async def run_regression_watchman(
    test_run_id: str,
    project_id: str,
) -> dict:
    """
    Standalone entry point: fetches failure clusters + analyses from DB,
    runs the Regression Watchman, and returns classification results.
    """
    import uuid

    async with AsyncSessionLocal() as db:
        from app.models.postgres import AIAnalysis, FailureCluster

        clusters_result = await db.execute(
            select(FailureCluster).where(FailureCluster.test_run_id == test_run_id).limit(50)
        )
        clusters = [
            {
                "cluster_id": c.cluster_id,
                "label": c.label,
                "size": c.size,
                "member_test_ids": c.member_test_ids or [],
                "representative_error": c.representative_error,
            }
            for c in clusters_result.scalars().all()
        ]

        analyses_result = await db.execute(
            select(AIAnalysis)
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .where(TestCase.test_run_id == test_run_id)
        )
        analyses = {
            str(a.test_case_id): {
                "failure_category": (
                    str(getattr(a.failure_category, "value", a.failure_category or "UNKNOWN"))
                ),
                "is_flaky": a.is_flaky,
                "confidence_score": a.confidence_score,
            }
            for a in analyses_result.scalars().all()
        }

    fake_pipeline_id = str(uuid.uuid4())
    state = {
        "pipeline_run_id": fake_pipeline_id,
        "test_run_id": test_run_id,
        "project_id": project_id,
        "failure_clusters": clusters,
        "analyses": analyses,
    }

    agent = _StandaloneWatchman()
    result = await agent._classify(state)
    return result


class _StandaloneWatchman(RegressionWatchman):
    """Variant that skips DB stage tracking for standalone API use."""

    async def mark_stage_running(self, pipeline_run_id: str) -> None:
        pass

    async def mark_stage_done(
        self,
        pipeline_run_id: str,
        result_data: dict[Any, Any] | None = None,
        error: str | None = None,
        **kwargs: Any,
    ) -> None:
        pass

    async def broadcast_progress(self, project_id: str, payload: dict) -> None:
        pass
