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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
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
from app.services.review_envelope import (
    review_envelope_for_pipeline_subject,
    review_envelope_for_run,
)
from app.services.report_distribution_policy import (
    decide_run_distribution,
    record_distribution,
    refusal_detail,
)

logger = logging.getLogger("routers.run_intelligence")


async def _with_review(
    db: Any,
    response: Response,
    run_id: Any,
    payload: Any,
    *,
    ai_generated: bool = True,
    workflow_type: str | None = None,
) -> Any:
    """Attach the E8.3 human-review envelope and headers to a report payload.

    Applied at response time, never stored: a cached intelligence snapshot must
    not freeze a review state that a person may settle a minute later.
    """
    envelope = await review_envelope_for_run(
        db, run_id, workflow_type=workflow_type, ai_generated=ai_generated
    )
    envelope.apply_headers(response)
    if isinstance(payload, dict):
        return {**payload, **envelope.fields()}
    return payload

router = APIRouter(prefix="/api/v1/runs", tags=["Run Intelligence"])


# Run ids with a background refresh already scheduled. A stale snapshot is
# served to every caller until the refresh lands, so without this a popular run
# schedules one recompute per request and they all race to write the same row.
_REFRESH_IN_FLIGHT: set[str] = set()


async def _refresh_snapshot_after_response(run_id: uuid.UUID) -> None:
    """Recompute and store the snapshot for ``run_id``. Best-effort, never raises.

    Scheduled when the GET below serves a STALE snapshot. Without it nothing
    ever cleared the flag: ``get_stale_snapshot`` filters on ``schema_version``
    and not on ``stale``, the handler returns that row before reaching the live
    recompute, and ``save_snapshot`` — the only writer that resets
    ``stale = False`` — was unreachable. There is no TTL, so a QA Lead's
    ``GO -> NO_GO`` override committed to ``release_decisions`` and this
    endpoint kept answering ``GO`` indefinitely.

    Runs after the response is sent, on its own session: the request session is
    closed by then.
    """
    key = str(run_id)
    if key in _REFRESH_IN_FLIGHT:
        return
    _REFRESH_IN_FLIGHT.add(key)
    try:
        from app.db.postgres import AsyncSessionLocal as _AsyncSessionLocal

        mongo = get_mongo_db()
        async with _AsyncSessionLocal() as write_db:
            result = await get_run_intelligence(run_id, write_db, mongo)
            provenance = result.get("provenance") if isinstance(result, dict) else None
            fallback = provenance.get("fallback_used", False) if isinstance(provenance, dict) else False
            await save_snapshot(write_db, run_id, result, fallback_used=fallback)
    except Exception as exc:  # noqa: BLE001 — a failed refresh must not break the served response
        logger.warning(
            "Background intelligence refresh failed for run %s: %s", run_id, exc
        )
    finally:
        _REFRESH_IN_FLIGHT.discard(key)


@router.get("/{run_id}/intelligence")
async def get_run_intelligence_endpoint(
    run_id: uuid.UUID,
    response: Response,
    background: BackgroundTasks,
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
            return await _with_review(db, response, run_id, cached)

        # Try stale snapshot (serves immediately while refresh is recommended)
        stale = (
            await get_stale_snapshot(db, run_id)
            if not include_set and report_version is None
            else None
        )
        if stale and not include_set:
            run_intelligence_requests_total.labels(status="cache_stale").inc()
            # Serving stale is deliberate — it is a latency trade. Scheduling the
            # refresh is what makes it a trade rather than a permanent answer.
            background.add_task(_refresh_snapshot_after_response, run_id)
            if isinstance(stale, dict):
                stale["_snapshot"] = {
                    "cached": True,
                    "stale": True,
                    "refresh_scheduled": True,
                }
            return await _with_review(db, response, run_id, stale)

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

        pipeline_subject = None
        evidence_subject = None
        if isinstance(result, dict):
            pipeline_subject = result.pop("_decision_report_pipeline_run_id", None)
            evidence_subject = result.pop(
                "_decision_report_evidence_bundle_sha256", None
            )
            result["_snapshot"] = {"cached": False, "stale": False}
        # After save_snapshot above: the envelope is never written into the cache.
        if report_version is not None and pipeline_subject is not None:
            envelope = await review_envelope_for_pipeline_subject(
                db,
                pipeline_subject,
                evidence_bundle_sha256=evidence_subject,
            )
            envelope.apply_headers(response)
            return {**result, **envelope.fields()}
        return await _with_review(db, response, run_id, result)
    except ValueError as exc:
        run_intelligence_requests_total.labels(status="failure").inc()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        run_intelligence_duration_seconds.observe(time.monotonic() - start)


@router.get("/{run_id}/decision-reports")
async def list_run_decision_reports(
    run_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    db: Any = Depends(get_db),
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
    # Each immutable version is bound to its producing pipeline. Preserve that
    # exact review, including superseded, rather than inheriting the newest
    # deep pipeline's review state.
    result = []
    first_envelope = None
    for version in versions:
        public_version = dict(version)
        pipeline_subject = public_version.pop("pipeline_run_id", None)
        evidence_subject = public_version.get("evidence_bundle_sha256")
        envelope = await review_envelope_for_pipeline_subject(
            db,
            pipeline_subject,
            evidence_bundle_sha256=evidence_subject,
        )
        if first_envelope is None:
            first_envelope = envelope
        result.append({**public_version, **envelope.fields()})
    if first_envelope is not None:
        first_envelope.apply_headers(response)
    else:
        (await review_envelope_for_run(db, run_id, workflow_type="deep")).apply_headers(
            response
        )
    return result


@router.get("/{run_id}/summary")
async def get_run_summary_by_mode(
    run_id: uuid.UUID,
    response: Response,
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
        # A fallback summary is built deterministically: not AI-generated.
        return await _with_review(
            db, response, run_id, result, ai_generated=not bool(result.get("fallback_used"))
        )
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
    response: Response,
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
        # E8.6: the refreshed payload is the same AI report the GET returns.
        return await _with_review(db, response, run_id, result)
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

    run_data = intelligence.get("run", {})
    project_id = run_data.get("project_id") if isinstance(run_data, dict) else None
    if project_id is None:
        project_id = (
            await db.execute(select(TestRun.project_id).where(TestRun.id == run_id))
        ).scalar_one_or_none()
    distribution = await decide_run_distribution(
        db,
        run_id=run_id,
        project_id=project_id,
        channel="intelligence_export",
    )
    await record_distribution(
        db,
        distribution,
        channel="intelligence_export",
        run_id=run_id,
        project_id=project_id,
        actor=_,
    )
    # The request session rolls back on close unless this audit record is
    # committed. Persist both allowed and refused decisions before returning.
    await db.commit()
    if not distribution.allowed:
        raise HTTPException(status_code=409, detail=refusal_detail(distribution))
    if distribution.watermark:
        # JSON exports cannot rely on a renderer to add the visible DRAFT
        # banner. Carry the same watermark field as HTML/PDF exports so a
        # downloaded pending report never loses its review status.
        report["draft_watermark"] = distribution.watermark

    # E8.6: the export is a downloadable copy of AI report content, so it says
    # whether a person has accepted that content (review envelope, E8.3).
    envelope = await review_envelope_for_run(db, run_id)
    report.update(envelope.fields())

    report_exports_total.labels(format="json", type="intelligence").inc()

    import json
    content = json.dumps(report, indent=2, default=str)
    filename = f"intelligence-report-{run_data.get('build_number', str(run_id)[:8])}.json"

    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
