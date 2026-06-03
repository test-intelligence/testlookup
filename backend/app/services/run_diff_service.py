"""
Run Diff Service — baseline comparison and regression detection.

Standalone service that computes a deterministic diff between the current run
and the most recent prior passing run for the same project.

No LLM calls — uses existing AIAnalysis records + FailureCluster data.

Consumed by:
  - GET /api/v1/runs/{run_id}/baseline-diff   (direct endpoint)
  - run_intelligence_service.get_run_intelligence()  (inline widget)
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RegressionClassification
from app.models.postgres import (
    AIAnalysis,
    FailureCategory,
    FailureCluster,
    LaunchStatus,
    TestCase,
    TestRun,
    TestStatus,
)

logger = logging.getLogger("services.run_diff")

# Same thresholds as RegressionWatchman for consistency
_INFRA_THRESHOLD = 0.5
_FLAKY_MAJORITY = 0.5


def _classify_regression(
    pass_rate_delta: Optional[float],
    new_failures: list[str],
    env_sensitivity: Optional[float],
    flaky_count: int,
    total_failures: int,
) -> str:
    """Run-level regression classification — deterministic, no LLM."""
    if not new_failures and total_failures == 0:
        return RegressionClassification.UNCLASSIFIED
    if flaky_count > 0 and total_failures > 0 and flaky_count / total_failures > _FLAKY_MAJORITY:
        return RegressionClassification.KNOWN_FLAKY
    if env_sensitivity is not None and env_sensitivity > 60:
        return RegressionClassification.ENVIRONMENTAL
    if new_failures and pass_rate_delta is not None and pass_rate_delta < -10:
        return RegressionClassification.NEW_REGRESSION
    if new_failures:
        return RegressionClassification.PRODUCT_BUG
    return RegressionClassification.UNCLASSIFIED


def _classify_cluster(
    cluster: FailureCluster,
    analyses_by_test: dict[str, dict],
) -> str:
    """
    Deterministic cluster classification using FailureCategory from AIAnalysis.
    Option B: reads existing DB records, no LLM calls.
    """
    member_ids: list[str] = cluster.member_test_ids or []
    if not member_ids:
        return RegressionClassification.UNCLASSIFIED

    infra_count = sum(
        1 for mid in member_ids
        if (analyses_by_test.get(mid) or {}).get("failure_category") == FailureCategory.INFRASTRUCTURE.value
    )
    flaky_count = sum(
        1 for mid in member_ids
        if (analyses_by_test.get(mid) or {}).get("is_flaky", False)
    )
    product_bug_count = sum(
        1 for mid in member_ids
        if (analyses_by_test.get(mid) or {}).get("failure_category") == FailureCategory.PRODUCT_BUG.value
    )

    n = len(member_ids)
    if infra_count / n >= _INFRA_THRESHOLD:
        return RegressionClassification.ENVIRONMENTAL
    if flaky_count / n >= _FLAKY_MAJORITY:
        return RegressionClassification.KNOWN_FLAKY
    if product_bug_count > 0:
        return RegressionClassification.PRODUCT_BUG
    return RegressionClassification.NEW_REGRESSION


async def get_baseline_diff(
    run: TestRun,
    db: AsyncSession,
    flaky_count: int = 0,
    env_sensitivity: Optional[float] = None,
) -> Optional[dict]:
    """
    Compute a deterministic diff against the most recent prior passing run.

    Returns None if no baseline exists for this project.
    Returns a plain dict (ready for JSON serialization) including:
      - baseline_run_id, baseline_build_number
      - pass_rate_delta, new_failures, resolved_failures
      - regression_classification (run-level)
      - regression_clusters (per-cluster classification)
      - classified_new_failures (new_failures enriched with classification)
      - suites_impacted_delta, current_suite_count, baseline_suite_count
    """
    # ── Find most recent PASSED run predating this one ────────────────────────
    baseline_result = await db.execute(
        select(TestRun)
        .where(
            TestRun.project_id == run.project_id,
            TestRun.status == LaunchStatus.PASSED,
            TestRun.created_at < run.created_at,
        )
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )
    baseline = baseline_result.scalar_one_or_none()
    if not baseline:
        return None

    # ── Pass-rate delta ───────────────────────────────────────────────────────
    current_rate = run.pass_rate or 0.0
    baseline_rate = baseline.pass_rate or 0.0
    pass_rate_delta = round(current_rate - baseline_rate, 2)

    # ── Current failures (fingerprint → {id, name}) ───────────────────────────
    current_failed_result = await db.execute(
        select(TestCase.id, TestCase.test_fingerprint, TestCase.test_name)
        .where(
            TestCase.test_run_id == run.id,
            TestCase.status.in_(["FAILED", "BROKEN"]),
        )
    )
    current_failed_rows = current_failed_result.all()
    # fingerprint → test_name (for new_failures list)
    current_failed_fps: dict[Any, str] = {
        row.test_fingerprint: row.test_name for row in current_failed_rows
    }
    # test_case_id (str) → test_name (for classified_new_failures)
    current_failed_by_id: dict[str, str] = {
        str(row.id): row.test_name for row in current_failed_rows
    }
    # test_case_id → fingerprint (for new-failure detection)
    current_failed_id_to_fp: dict[str, Any] = {
        str(row.id): row.test_fingerprint for row in current_failed_rows
    }

    # ── Baseline failures (fingerprint + name) ────────────────────────────────
    # Fetch once with both columns and reuse for new-failure detection (set of
    # fingerprints) AND resolved-failures (names) below. Previously this ran two
    # SELECTs with identical WHERE clauses — one projecting fingerprint, one
    # projecting fingerprint+name — a redundant round trip on the same rows.
    baseline_failed_rows = (
        await db.execute(
            select(TestCase.test_fingerprint, TestCase.test_name)
            .where(
                TestCase.test_run_id == baseline.id,
                TestCase.status.in_(["FAILED", "BROKEN"]),
            )
        )
    ).all()
    baseline_failed_fps = {row.test_fingerprint for row in baseline_failed_rows}

    # New failures: in current but NOT in baseline
    new_failures = [
        name for fp, name in current_failed_fps.items()
        if fp not in baseline_failed_fps
    ][:20]
    new_failure_ids = {
        tc_id for tc_id, fp in current_failed_id_to_fp.items()
        if fp not in baseline_failed_fps
    }

    # ── Resolved failures: in baseline (FAILED) now PASSING ───────────────────
    current_passing_result = await db.execute(
        select(TestCase.test_fingerprint)
        .where(
            TestCase.test_run_id == run.id,
            TestCase.status == TestStatus.PASSED,
        )
    )
    current_passing_fps = {row.test_fingerprint for row in current_passing_result.all()}

    # Reuse the single baseline-failures fetch from above (same WHERE clause) —
    # no second query needed.
    resolved_failures = [
        row.test_name for row in baseline_failed_rows
        if row.test_fingerprint in current_passing_fps
    ][:20]

    # ── Run-level regression classification ───────────────────────────────────
    total_failures = len(current_failed_fps)
    regression_class = _classify_regression(
        pass_rate_delta=pass_rate_delta,
        new_failures=new_failures,
        env_sensitivity=env_sensitivity,
        flaky_count=flaky_count,
        total_failures=total_failures,
    )

    # ── Cluster-level regression classification ───────────────────────────────
    regression_clusters, cluster_class_by_test_id = await _classify_failure_clusters(
        run_id=run.id,
        db=db,
    )

    # ── Classified new failures (new_failures enriched with classification) ───
    classified_new_failures = [
        {
            "name": name,
            "classification": cluster_class_by_test_id.get(tc_id, regression_class),
        }
        for tc_id, name in current_failed_by_id.items()
        if tc_id in new_failure_ids
    ][:20]

    # ── Suite impact delta ────────────────────────────────────────────────────
    current_suites_result = await db.execute(
        select(TestCase.suite_name)
        .where(TestCase.test_run_id == run.id, TestCase.status.in_(["FAILED", "BROKEN"]))
        .distinct()
    )
    baseline_suites_result = await db.execute(
        select(TestCase.suite_name)
        .where(TestCase.test_run_id == baseline.id, TestCase.status.in_(["FAILED", "BROKEN"]))
        .distinct()
    )
    current_suites = {row.suite_name for row in current_suites_result.all()}
    baseline_suites = {row.suite_name for row in baseline_suites_result.all()}

    # ── Commit range (when SCM metadata is available) ──────────────────────────
    commit_range = None
    if run.commit_hash and baseline.commit_hash:
        commit_range = {
            "from_commit": baseline.commit_hash[:12] if baseline.commit_hash else None,
            "to_commit": run.commit_hash[:12] if run.commit_hash else None,
            "same_commit": run.commit_hash == baseline.commit_hash,
        }
    elif run.commit_hash:
        commit_range = {
            "from_commit": None,
            "to_commit": run.commit_hash[:12],
            "same_commit": False,
        }

    # ── Config/environment drift ─────────────────────────────────────────────
    config_drift: list[dict] = []
    if run.branch != baseline.branch and (run.branch or baseline.branch):
        config_drift.append({
            "field": "branch",
            "old_value": baseline.branch,
            "new_value": run.branch,
        })
    if run.ocp_namespace != baseline.ocp_namespace and (run.ocp_namespace or baseline.ocp_namespace):
        config_drift.append({
            "field": "ocp_namespace",
            "old_value": baseline.ocp_namespace,
            "new_value": run.ocp_namespace,
        })

    # ── Selection reason ─────────────────────────────────────────────────────
    selection_reason = "latest_passing"

    diff_payload = {
        "baseline_run_id": str(baseline.id),
        "baseline_build_number": baseline.build_number,
        "pass_rate_delta": pass_rate_delta,
        "new_failures": new_failures,
        "resolved_failures": resolved_failures,
        "regression_classification": regression_class,
        "regression_clusters": regression_clusters,
        "classified_new_failures": classified_new_failures,
        "suites_impacted_delta": len(current_suites) - len(baseline_suites),
        "current_suite_count": len(current_suites),
        "baseline_suite_count": len(baseline_suites),
        "commit_range": commit_range,
        "config_drift": config_drift,
        "selection_reason": selection_reason,
    }

    # ── Persist baseline + diff cache (idempotent) on a DEDICATED session ──────
    # CQS split (same pattern as run_intelligence_service._build_and_persist_
    # defect_candidates and test_health_coach): this service is consumed by GET
    # aggregators that keep using the injected ``db`` for further reads AFTER
    # this call — run_intelligence_service.get_run_intelligence reads defect
    # candidates + stage results in steps 7-8, and the /baseline-diff endpoint's
    # get_db owns the request transaction. Committing the injected session here
    # ends the caller's transaction mid-aggregation, and a swallowed commit
    # failure (broad except, no rollback) would leave it in a rollback-required
    # state and 500 the rest of the endpoint. Writing the cache through its own
    # AsyncSessionLocal keeps ``db`` strictly read-only in this service.
    try:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import RunBaseline, RunDiff

        async with AsyncSessionLocal() as write_db:
            existing_baseline = await write_db.execute(
                select(RunBaseline).where(RunBaseline.run_id == run.id)
            )
            if existing_baseline.scalar_one_or_none() is None:
                write_db.add(RunBaseline(
                    run_id=run.id,
                    baseline_run_id=baseline.id,
                    selection_reason=selection_reason,
                    classification=regression_class,
                    baseline_build_number=baseline.build_number,
                    pass_rate_delta=pass_rate_delta,
                    commit_range=commit_range,
                    config_drift=config_drift if config_drift else None,
                ))

            existing_diff = await write_db.execute(
                select(RunDiff).where(RunDiff.run_id == run.id)
            )
            rd = existing_diff.scalar_one_or_none()
            if rd:
                rd.diff_payload = diff_payload
            else:
                write_db.add(RunDiff(
                    run_id=run.id,
                    baseline_run_id=baseline.id,
                    diff_payload=diff_payload,
                ))

            await write_db.commit()
    except Exception as exc:
        logger.warning("Failed to persist baseline/diff for run %s: %s", run.id, exc)

    return diff_payload


async def _classify_failure_clusters(
    run_id: Any,
    db: AsyncSession,
) -> tuple[list[dict], dict[str, str]]:
    """
    Deterministically classify all failure clusters for a run.

    Returns:
      - regression_clusters: list of {cluster_id, label, size, classification}
      - cluster_class_by_test_id: dict mapping test_case_id → classification
        (used to enrich classified_new_failures)
    """
    clusters_result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id)
        .order_by(FailureCluster.size.desc())
        .limit(20)
    )
    clusters = clusters_result.scalars().all()
    if not clusters:
        return [], {}

    # Collect all member test IDs across clusters
    all_member_ids: set[str] = set()
    for c in clusters:
        for mid in (c.member_test_ids or []):
            all_member_ids.add(mid)

    if not all_member_ids:
        return [
            {"cluster_id": c.cluster_id, "label": c.label, "size": c.size,
             "classification": RegressionClassification.UNCLASSIFIED}
            for c in clusters
        ], {}

    # Convert to UUIDs for the query (skip any non-UUID values)
    member_uuids = []
    for mid in all_member_ids:
        try:
            member_uuids.append(_uuid.UUID(mid))
        except (ValueError, AttributeError):
            pass

    # Fetch analyses for all member tests in one query
    analyses_by_test: dict[str, dict] = {}
    if member_uuids:
        analyses_result = await db.execute(
            select(AIAnalysis).where(AIAnalysis.test_case_id.in_(member_uuids))
        )
        for a in analyses_result.scalars().all():
            failure_category = str(getattr(a.failure_category, "value", a.failure_category or "UNKNOWN"))
            analyses_by_test[str(a.test_case_id)] = {
                "failure_category": failure_category,
                "is_flaky": bool(a.is_flaky),
                "confidence_score": a.confidence_score,
            }

    # Classify each cluster and build the test_id → classification map
    regression_clusters: list[dict] = []
    cluster_class_by_test_id: dict[str, str] = {}

    for cluster in clusters:
        classification = _classify_cluster(cluster, analyses_by_test)
        regression_clusters.append({
            "cluster_id": cluster.cluster_id,
            "label": cluster.label,
            "size": cluster.size,
            "classification": classification,
        })
        for mid in (cluster.member_test_ids or []):
            cluster_class_by_test_id[mid] = classification

    return regression_clusters, cluster_class_by_test_id
