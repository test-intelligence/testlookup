"""
Run Intelligence router (thin HTTP layer).

All aggregation logic lives in app.services.run_intelligence_service and
app.services.run_diff_service.

Endpoints:
  GET /api/v1/runs/{run_id}/intelligence
      Unified AI snapshot: summary, clusters, release decision, dimension scores,
      what-changed baseline diff, defect candidates, provenance.

  GET /api/v1/runs/{run_id}/summary?mode=executive|developer|manager
      Mode-specific narrative summary for audience-aware rendering.

  GET /api/v1/runs/{run_id}/baseline-diff
      Standalone baseline diff: new/resolved failures, regression clusters,
      classified failures, suite impact delta.
"""
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from app.core.deps import require_run_access
from app.core.metrics import (
    run_intelligence_duration_seconds,
    run_intelligence_requests_total,
    summary_requests_total,
)
from app.db.postgres import get_db
from app.db.mongo import get_mongo_db
from app.services.run_diff_service import get_baseline_diff
from app.services.run_intelligence_service import get_run_intelligence, get_run_mode_summary
from app.services.intelligence_snapshot_service import get_cached_snapshot, get_stale_snapshot, invalidate, save_snapshot
from app.models.postgres import TestRun

logger = logging.getLogger("routers.run_intelligence")

router = APIRouter(prefix="/api/v1/runs", tags=["Run Intelligence"])


@router.get("/{run_id}/intelligence")
async def get_run_intelligence_endpoint(
    run_id: uuid.UUID,
    include: str = Query(
        default="",
        description="Comma-separated optional expansions: test_cases,evidence,history",
    ),
    report_version: int | None = Query(
        default=None,
        ge=1,
        description="Immutable DecisionReport version to display; omitted means latest",
    ),
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """
    Return a unified Run Intelligence snapshot for the given test run.

    Aggregates:
    - 4-layer structured summary (MongoDB)
    - Failure clusters with criticality levels
    - AI analyses (category breakdown, top findings)
    - Release decision + 7-dimension risk scores
    - What changed since last good run (deterministic baseline diff)
    - Lightweight defect candidates
    - Pipeline stage history with skip context
    - Provenance (schema_version, fallback_used, tools_used_count)
    """
    include_set = {s.strip().lower() for s in include.split(",") if s.strip()}
    mongo = get_mongo_db()
    start = time.monotonic()
    try:
        # Read-through cache: try fresh snapshot first
        cached = (
            await get_cached_snapshot(db, run_id)
            if report_version is None
            else None
        )
        if cached and not include_set and report_version is None:
            run_intelligence_requests_total.labels(status="cache_hit").inc()
            if isinstance(cached, dict):
                cached["_snapshot"] = {"cached": True, "stale": False}
            return cached

        # Try stale snapshot (serves immediately while refresh is recommended)
        stale = (
            await get_stale_snapshot(db, run_id)
            if not include_set and report_version is None
            else None
        )
        if stale and not include_set:
            run_intelligence_requests_total.labels(status="cache_stale").inc()
            if isinstance(stale, dict):
                stale["_snapshot"] = {"cached": True, "stale": True}
            return stale

        # Cache miss — compute live
        result = await get_run_intelligence(
            run_id,
            db,
            mongo,
            include=include_set,
            report_version=report_version,
        )
        run_intelligence_requests_total.labels(status="success").inc()

        # Cache the result for future requests.
        #
        # Item #4 (command/query separation): the snapshot write runs in a
        # **dedicated write session** so this GET handler's own transaction
        # stays read-only. If the write fails, the response still returns
        # successfully — the next GET will just recompute.
        if not include_set and report_version is None:
            try:
                fallback = result.get("provenance", {}).get("fallback_used", False) if isinstance(result.get("provenance"), dict) else False
                from app.db.postgres import AsyncSessionLocal as _AsyncSessionLocal
                async with _AsyncSessionLocal() as write_db:
                    await save_snapshot(write_db, run_id, result, fallback_used=fallback)
            except Exception as cache_err:
                logger.warning("Failed to save intelligence snapshot: %s", cache_err)

        if isinstance(result, dict):
            result["_snapshot"] = {"cached": False, "stale": False}
        return result
    except ValueError as exc:
        run_intelligence_requests_total.labels(status="failure").inc()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        run_intelligence_duration_seconds.observe(time.monotonic() - start)


@router.get("/{run_id}/decision-reports")
async def list_run_decision_reports(
    run_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    _: Any = Depends(require_run_access()),
):
    """List immutable published DecisionReport versions for an authorized run."""
    from app.services.decision_report_service import list_decision_report_versions

    try:
        versions = await list_decision_report_versions(
            get_mongo_db(), str(run_id), limit=limit
        )
    except Exception:
        # Do not turn a transient Mongo outage into an unbounded error surface;
        # callers receive a truthful unavailable response.
        raise HTTPException(status_code=503, detail="decision_report_versions_unavailable") from None
    return versions


@router.get("/{run_id}/summary")
async def get_run_summary_by_mode(
    run_id: uuid.UUID,
    mode: str = Query(
        default="executive",
        description="Summary mode: executive | developer | manager",
    ),
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """
    Return a mode-specific AI-generated summary.

    - executive: concise 3-sentence summary + release impact
    - developer: evidence pack, stack traces, fix recommendations
    - manager:   business impact, criticality, immediate mitigation

    Falls back to a deterministic PostgreSQL-derived summary when the AI pipeline
    has not yet produced a MongoDB document for this run.
    """
    mongo = get_mongo_db()
    try:
        result = await get_run_mode_summary(run_id, mode, db, mongo)
        source = "fallback" if result.get("fallback_used") else "llm"
        summary_requests_total.labels(mode=mode, source=source).inc()
        return result
    except ValueError as exc:
        summary_requests_total.labels(mode=mode, source="error").inc()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/baseline-diff")
async def get_run_baseline_diff(
    run_id: uuid.UUID,
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """
    Return a deterministic baseline diff for the given test run.

    Compares against the most recent prior PASSED run for the same project.
    No LLM calls — all classification is deterministic using existing AI analyses.

    Response includes:
    - pass_rate_delta, new_failures, resolved_failures
    - regression_classification (run-level)
    - regression_clusters (per-cluster: cluster_id, label, size, classification)
    - classified_new_failures (new failures enriched with their cluster classification)
    - suites_impacted_delta, current_suite_count, baseline_suite_count
    """
    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"TestRun {run_id} not found")

    diff = await get_baseline_diff(run=run, db=db)
    if not diff:
        raise HTTPException(
            status_code=404,
            detail="No baseline (prior passing run) found for this project.",
        )
    return diff


@router.post("/{run_id}/intelligence/refresh")
async def refresh_intelligence(
    run_id: uuid.UUID,
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """
    Force-refresh the intelligence snapshot for a run.
    Invalidates the cached snapshot and recomputes from live data.
    Returns the fresh intelligence payload.
    """
    mongo = get_mongo_db()
    # Invalidate existing snapshot
    await invalidate(db, run_id)

    # Recompute live
    try:
        result = await get_run_intelligence(run_id, db, mongo)
        # Save new snapshot in a DEDICATED write session, exactly as the GET
        # handler does. Catching the error is not enough on the request
        # session: a failed flush leaves it in a rolled-back state, so the
        # next use raises PendingRollbackError and the handler 500s — which
        # is how a JSON-serialisation failure in the payload turned a
        # best-effort cache write into a hard failure of this endpoint.
        try:
            fallback = result.get("provenance", {}).get("fallback_used", False) if isinstance(result.get("provenance"), dict) else False
            from app.db.postgres import AsyncSessionLocal as _AsyncSessionLocal
            async with _AsyncSessionLocal() as write_db:
                await save_snapshot(write_db, run_id, result, fallback_used=fallback)
        except Exception as cache_err:
            logger.warning("Failed to save intelligence snapshot on refresh: %s", cache_err)
        if isinstance(result, dict):
            result["_snapshot"] = {"cached": False, "stale": False, "just_refreshed": True}
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/export")
async def export_intelligence_report(
    run_id: uuid.UUID,
    mode: str = Query(default="manager", description="Summary mode: executive | developer | manager"),
    db: Any = Depends(get_db),
    _: Any = Depends(require_run_access()),
):
    """
    Export a customer-facing intelligence report as a structured JSON payload.

    Combines: run summary, structured AI analysis, baseline diff, release decision,
    criticality dimensions, role actions, and provenance — in a single downloadable document.
    """
    from app.core.metrics import report_exports_total

    mongo = get_mongo_db()
    try:
        intelligence = await get_run_intelligence(run_id, db, mongo)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Get mode-specific summary
    mode_summary = None
    try:
        mode_summary = await get_run_mode_summary(run_id, mode, db, mongo)
    except Exception:
        pass

    report = {
        "report_type": "run_intelligence",
        "export_mode": mode,
        "run": intelligence.get("run"),
        "summary": mode_summary.get("markdown_report") if mode_summary else intelligence.get("structured_summary", {}).get("executive_summary"),
        "baseline_diff": intelligence.get("what_changed_since_last_good_run"),
        "release_decision": intelligence.get("release_decision"),
        "failure_clusters": intelligence.get("failure_clusters", [])[:10],
        "dimension_scores": intelligence.get("dimension_scores", []),
        "defect_candidates": intelligence.get("defect_candidates", []),
        "role_actions": intelligence.get("role_actions", {}),
        "provenance": intelligence.get("provenance"),
        "decision_report": intelligence.get("structured_summary", {}).get("decision_report"),
        "decision_report_verification": intelligence.get("structured_summary", {}).get("decision_report_verification"),
        "category_breakdown": intelligence.get("category_breakdown", {}),
    }

    report_exports_total.labels(format="json", type="intelligence").inc()

    import json
    content = json.dumps(report, indent=2, default=str)
    run_data = intelligence.get("run", {})
    filename = f"intelligence-report-{run_data.get('build_number', str(run_id)[:8])}.json"

    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
