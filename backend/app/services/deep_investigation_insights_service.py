"""The three offline reads of the Deep Investigation page (W14 AGT-07).

* :func:`pre_scan` -- a run's failing results grouped by error signature
  (``FAILURE_SIGNATURE_SQL``, the one offline rule the Runs signatures and
  ``GET /analytics/failure-groups`` use), refined by the run's EXISTING current
  cluster snapshot when a deep pipeline already produced one. It never calls
  ChromaDB: the only embedding clustering (``tools/embed_and_cluster.py``)
  UPSERTS into the shared collection and loads model weights in-process, and a
  GET writes nothing. Two statements.
* :func:`deep_history` -- per run of a project, its LATEST deep pipeline with
  the cost, cluster, finding and confidence facts, in ONE statement with
  ``total`` beside the page, then the run-number map.
* :func:`deep_estimate` -- the p50 of the project's latest completed deep
  pipelines (the sample in ONE statement), the Root Cause stage scaled per
  failure analysed, priced ONLY for a local model or a known price (an unpriced
  model is ``priced: false``: ``llm_pricing.estimate_cost`` would count an
  unpriced call that never happened), plus the server's own deep ``gate``. No
  sample -> ``measured: false`` with its reason, never ``0 min`` / ``$0.00``.

Commit-free and read-only: every statement on the caller's session, no row, no
ledger entry, no metric, no Redis key of its own, no model call. Nothing logs.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_ops_vocab import (
    ESTIMATE_LOCAL_NOTE,
    ESTIMATE_NO_ENDPOINT,
    ESTIMATE_SAMPLE_PIPELINES,
    ESTIMATE_UNMEASURED,
    ESTIMATE_UNPRICED,
    PER_FAILURE_STAGES,
    PRESCAN_MEMBERS_CAP,
    PRESCAN_NOTE_CLUSTERS,
    PRESCAN_NOTE_SIGNATURE_ONLY,
)
from app.models.postgres import FailureCluster, TestCase, TestRun
from app.services.analytics_scope import effective_suite_sql
from app.services.cluster_snapshot import current_snapshot_sql, is_current_snapshot
from app.services.deep_run_overrides import (
    FAILING_STATUSES,
    DeepRunConfiguration,
    configuration_read,
    resolve_project_config,
    validate_run_configuration,
)
from app.services.failure_signature import (
    FAILURE_SIGNATURE_PARAMS,
    FAILURE_SIGNATURE_SQL,
    LABEL_LINE_CAP,
    first_line_sql,
    signature_bucket_id,
)
from app.services.workflow_run_state import public_status

__all__ = [
    "ESTIMATE_DETERMINISTIC_NOTE",
    "deep_estimate",
    "deep_history",
    "pre_scan",
]

#: Suites shown per pre-scan group.
PRESCAN_SUITES_CAP = 5
#: The Root Cause stage's ``tier=deterministic`` calls no model: a measured $0.00.
ESTIMATE_DETERMINISTIC_NOTE = "deterministic tier — the Root Cause stage calls no model"
_FAILING_SQL = "tc.status IN (" + ", ".join(f"'{s}'" for s in FAILING_STATUSES) + ")"


# ── pre-scan ─────────────────────────────────────────────────────────────────

_PRESCAN_SQL = f"""
WITH f AS MATERIALIZED (
    SELECT tc.id AS id,
           {FAILURE_SIGNATURE_SQL} AS sig,
           {first_line_sql("tc.error_message", LABEL_LINE_CAP)} AS line,
           {effective_suite_sql("tr", "tc")} AS suite
    FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id
    WHERE tc.test_run_id = :run_id AND {_FAILING_SQL}
), g AS (
    SELECT sig,
           COUNT(*) AS members,
           (array_agg(id ORDER BY id))[1:{PRESCAN_MEMBERS_CAP}] AS member_ids,
           MIN(NULLIF(line, '') COLLATE "C") AS representative,
           (array_agg(DISTINCT suite ORDER BY suite) FILTER (WHERE suite IS NOT NULL))[1:{PRESCAN_SUITES_CAP}]
               AS suites
    FROM f GROUP BY sig
)
SELECT sig, members, member_ids, representative, suites,
       COUNT(*) OVER () AS groups_total,
       SUM(members) OVER () AS failing_results
FROM g
ORDER BY members DESC, sig COLLATE "C"
LIMIT :limit
"""


def _refine(member_ids: list[str], clusters: list[tuple[str, str, set[str]]]) -> tuple[str | None, str | None]:
    """The current cluster holding the most of a group's members (ties: the lower cluster id)."""
    best: tuple[int, str, str] | None = None
    for cluster_id, label, members in clusters:
        overlap = sum(1 for member in member_ids if member in members)
        if overlap and (best is None or overlap > best[0] or (overlap == best[0] and cluster_id < best[1])):
            best = (overlap, cluster_id, label)
    return (best[1], best[2]) if best else (None, None)


async def pre_scan(db: AsyncSession, run: TestRun, *, limit: int) -> dict[str, Any]:
    """A ``DeepPreScanResponse`` body for ``run``: two statements, no ChromaDB."""
    rows = (await db.execute(
        text(_PRESCAN_SQL),
        {**FAILURE_SIGNATURE_PARAMS, "run_id": run.id, "limit": int(limit)},
    )).mappings().all()
    snapshot = (await db.execute(
        select(FailureCluster.cluster_id, FailureCluster.label, FailureCluster.member_test_ids)
        .where(FailureCluster.test_run_id == run.id, is_current_snapshot())
        .order_by(FailureCluster.cluster_id)
    )).all()
    clusters = [
        (str(cluster_id), str(label or ""), {str(m) for m in (members or [])})
        for cluster_id, label, members in snapshot
    ]
    groups: list[dict[str, Any]] = []
    for row in rows:
        member_ids = [str(m) for m in (row["member_ids"] or [])]
        cluster_id, cluster_label = _refine(member_ids, clusters) if clusters else (None, None)
        representative = row["representative"] or None
        groups.append({
            "signature_id": signature_bucket_id(row["sig"]),
            "representative_error": representative[:LABEL_LINE_CAP] if representative else None,
            "members": int(row["members"]),
            "member_result_ids": member_ids,
            "members_truncated": int(row["members"]) > len(member_ids),
            "suites": [str(s) for s in (row["suites"] or [])],
            "cluster_id": cluster_id,
            "cluster_label": cluster_label,
        })
    failing = int(rows[0]["failing_results"]) if rows else 0
    return {
        "run_id": run.id,
        "measured": failing > 0,
        "failing_results": failing,
        "groups_total": int(rows[0]["groups_total"]) if rows else 0,
        "groups": groups,
        "grouped_by": "error_signature",
        "refinement": "clusters" if clusters else "signature_only",
        "refinement_note": PRESCAN_NOTE_CLUSTERS if clusters else PRESCAN_NOTE_SIGNATURE_ONLY,
    }


# ── history ──────────────────────────────────────────────────────────────────

_HISTORY_SQL = f"""
WITH latest AS (
    SELECT DISTINCT ON (p.test_run_id)
           p.id, p.test_run_id, p.status, p.started_at, p.completed_at
    FROM agent_pipeline_runs p
    JOIN test_runs tr ON tr.id = p.test_run_id
    JOIN projects pr ON pr.id = tr.project_id AND pr.is_active IS TRUE
    WHERE tr.project_id = :project_id
      AND p.workflow_type = 'deep'
      AND p.parent_pipeline_run_id IS NULL
    ORDER BY p.test_run_id, p.created_at DESC, p.id DESC
), page AS (
    SELECT l.id, l.test_run_id, l.status, l.started_at, l.completed_at,
           tr.build_number, tr.primary_suite_name AS suite_name, tr.created_at AS run_created_at,
           COALESCE(tr.failed_tests, 0) + COALESCE(tr.broken_tests, 0) AS failures,
           COUNT(*) OVER () AS total
    FROM latest l JOIN test_runs tr ON tr.id = l.test_run_id
    ORDER BY tr.created_at DESC, tr.id DESC
    OFFSET :offset LIMIT :size
)
SELECT pg.*, cost.cost_usd, cost.cost_reported, cl.clusters, fd.findings, fd.avg_confidence
FROM page pg
LEFT JOIN LATERAL (
    SELECT SUM(s.cost_usd) FILTER (WHERE s.cost_usd IS NOT NULL) AS cost_usd,
           COUNT(s.cost_usd) AS cost_reported
    FROM agent_stage_results s WHERE s.pipeline_run_id = pg.id
) cost ON TRUE
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS clusters
    FROM failure_clusters fc
    WHERE fc.test_run_id = pg.test_run_id AND {current_snapshot_sql("fc")}
) cl ON TRUE
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS findings,
           AVG(CASE WHEN f.confidence_score > 1 THEN f.confidence_score / 100.0
                    ELSE f.confidence_score END) AS avg_confidence
    FROM deep_findings f WHERE f.test_run_id = pg.test_run_id
) fd ON TRUE
ORDER BY pg.run_created_at DESC, pg.test_run_id DESC
"""


def _seconds(started: Any, completed: Any) -> Optional[float]:
    """A duration only when BOTH ends are stored (never 0 for "unknown")."""
    if started is None or completed is None:
        return None
    return round(max(0.0, float((completed - started).total_seconds())), 3)


async def deep_history(db: AsyncSession, project_id: uuid.UUID, *, page: int, size: int) -> dict[str, Any]:
    """A ``DeepHistoryResponse`` body: ONE statement, then the run-number map.

    ``project_id`` has already been checked by the route
    (``resolve_project_scope``); only that project's live runs are read.
    """
    from app.services.runs_service import fetch_run_seq_map  # noqa: PLC0415

    rows = (await db.execute(
        text(_HISTORY_SQL),
        {"project_id": project_id, "offset": (int(page) - 1) * int(size), "size": int(size)},
    )).mappings().all()
    seq = await fetch_run_seq_map(db, [row["test_run_id"] for row in rows]) if rows else {}
    items = []
    for row in rows:
        avg = row["avg_confidence"]
        items.append({
            "run_id": row["test_run_id"],
            "run_seq": seq.get(str(row["test_run_id"])),
            "build_number": row["build_number"],
            "suite_name": row["suite_name"],
            "run_created_at": row["run_created_at"],
            "failures": int(row["failures"] or 0),
            "pipeline_run_id": row["id"],
            "status": str(row["status"]),
            "public_status": public_status(row["status"]),
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "duration_seconds": _seconds(row["started_at"], row["completed_at"]),
            "clusters": int(row["clusters"]) if row["clusters"] else None,
            "findings": int(row["findings"] or 0),
            "avg_confidence": round(float(avg), 4) if avg is not None else None,
            "cost_usd": float(row["cost_usd"]) if row["cost_reported"] else None,
        })
    total = int(rows[0]["total"]) if rows else 0
    if not rows and page > 1:
        # Past the last page: the page is empty, the COUNT still answers.
        total = int((await db.execute(
            text(
                "SELECT COUNT(DISTINCT p.test_run_id) FROM agent_pipeline_runs p "
                "JOIN test_runs tr ON tr.id = p.test_run_id "
                "JOIN projects pr ON pr.id = tr.project_id AND pr.is_active IS TRUE "
                "WHERE tr.project_id = :project_id AND p.workflow_type = 'deep' "
                "AND p.parent_pipeline_run_id IS NULL"
            ),
            {"project_id": project_id},
        )).scalar_one())
    return {"project_id": project_id, "total": total, "page": int(page), "size": int(size), "items": items}


# ── estimate ─────────────────────────────────────────────────────────────────

#: ONE statement: the sample (the project's latest completed / passed top-level
#: deep pipelines), the p50 of their whole durations, and per stage the p50 of
#: its duration and tokens -- raw, and per failure the ingestion stage counted.
#: The outer ``LEFT JOIN`` keeps one row when the sample has no timed stage.
_ESTIMATE_SQL = """
WITH sample AS (
    SELECT p.id, p.started_at, p.completed_at
    FROM agent_pipeline_runs p JOIN test_runs tr ON tr.id = p.test_run_id
    WHERE tr.project_id = :project_id
      AND p.workflow_type = 'deep'
      AND p.parent_pipeline_run_id IS NULL
      AND p.status IN ('completed', 'passed')
    ORDER BY p.completed_at DESC NULLS LAST, p.created_at DESC, p.id DESC
    LIMIT :sample_size
), totals AS (
    SELECT COUNT(*) AS sample_pipelines,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
           ) FILTER (WHERE started_at IS NOT NULL AND completed_at IS NOT NULL) AS pipeline_seconds_p50
    FROM sample
), failed AS (
    SELECT s.pipeline_run_id,
           GREATEST(1, COALESCE(CAST(s.result_data ->> 'failed_count' AS integer), 0)) AS failed
    FROM agent_stage_results s JOIN sample ON sample.id = s.pipeline_run_id
    WHERE s.stage_name = 'ingestion'
), timed AS (
    SELECT s.stage_name,
           EXTRACT(EPOCH FROM (s.completed_at - s.started_at)) AS secs,
           s.input_tokens, s.output_tokens,
           COALESCE(f.failed, 1) AS failed
    FROM agent_stage_results s
    JOIN sample ON sample.id = s.pipeline_run_id
    LEFT JOIN failed f ON f.pipeline_run_id = s.pipeline_run_id
    WHERE s.started_at IS NOT NULL AND s.completed_at IS NOT NULL
), per_stage AS (
    SELECT stage_name,
           COUNT(*) AS samples,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY secs) AS secs_p50,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY input_tokens) AS in_p50,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY output_tokens) AS out_p50,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY secs / failed) AS secs_pf_p50,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY input_tokens::float / failed) AS in_pf_p50,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY output_tokens::float / failed) AS out_pf_p50
    FROM timed GROUP BY stage_name
)
SELECT t.sample_pipelines, t.pipeline_seconds_p50, ps.*
FROM totals t LEFT JOIN per_stage ps ON TRUE
ORDER BY ps.stage_name
"""


def _int(value: Any) -> Optional[int]:
    return int(round(float(value))) if value is not None else None


def _stage_rows(rows: list[Any], failures_analysed: int) -> tuple[list[dict[str, Any]], float]:
    """Per stage p50s (the per-failure stages scaled) and the seconds that scaling adds."""
    stages: list[dict[str, Any]] = []
    scaling_delta = 0.0
    for row in rows:
        if row["stage_name"] is None:
            continue
        per_failure = row["stage_name"] in PER_FAILURE_STAGES
        if per_failure:
            secs = float(row["secs_pf_p50"]) * failures_analysed if row["secs_pf_p50"] is not None else None
            tokens_in = row["in_pf_p50"] * failures_analysed if row["in_pf_p50"] is not None else None
            tokens_out = row["out_pf_p50"] * failures_analysed if row["out_pf_p50"] is not None else None
            if secs is not None and row["secs_p50"] is not None:
                scaling_delta += secs - float(row["secs_p50"])
        else:
            secs = float(row["secs_p50"]) if row["secs_p50"] is not None else None
            tokens_in, tokens_out = row["in_p50"], row["out_p50"]
        stages.append({
            "stage_name": str(row["stage_name"]),
            "samples": int(row["samples"] or 0),
            "duration_seconds_p50": round(secs, 3) if secs is not None else None,
            "input_tokens_p50": _int(tokens_in),
            "output_tokens_p50": _int(tokens_out),
            "per_failure": per_failure,
        })
    return stages, scaling_delta


def _gate_read(decision: Any) -> dict[str, Any]:
    return {
        "allowed": bool(decision.allowed),
        "reason_code": decision.reason_code,
        "message": decision.message,
        "analysis_mode": str(decision.analysis_mode),
        "deep_investigation_enabled": bool(decision.deep_investigation_enabled),
    }


def _cost(resolved: Any, *, measured: bool, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    """A ``DeepCostRead`` body for the tightened Root Cause config's endpoint.

    ``estimate_cost`` is called ONLY for a self-hosted provider or a model
    ``resolve_price`` knows: for an unpriced model it logs and counts an
    unpriced call, and a read makes no call (§0.20).
    """
    from app.services.agent_capability_registry import DEFAULT_TIERS  # noqa: PLC0415 (read only)
    from app.services.llm_pricing import (  # noqa: PLC0415
        PRICE_TABLE_UPDATED,
        SELF_HOSTED_PROVIDERS,
        estimate_cost,
        resolve_price,
    )

    out: dict[str, Any] = {
        "priced": False, "local_model": False, "usd": None, "provider": None, "model": None,
        "note": None, "price_table_updated": PRICE_TABLE_UPDATED,
    }
    configured = str(resolved.config.model.tier)
    tier = DEFAULT_TIERS.get("root_cause_analysis", "slm") if configured == "auto" else configured
    if tier == "deterministic":
        return {**out, "priced": True, "usd": 0.0, "note": ESTIMATE_DETERMINISTIC_NOTE}
    endpoint = resolved.endpoints.get(tier)
    if endpoint is None:
        return {**out, "note": ESTIMATE_NO_ENDPOINT.format(tier=tier)}
    provider, model = str(endpoint.provider), str(endpoint.model)
    out.update({"provider": provider, "model": model})
    if provider.strip().lower() in SELF_HOSTED_PROVIDERS:
        # A local model is MEASURED as free, sample or not.
        return {**out, "priced": True, "local_model": True, "usd": 0.0, "note": ESTIMATE_LOCAL_NOTE}
    if resolve_price(provider, model) is None:
        return {**out, "note": ESTIMATE_UNPRICED.format(provider=provider, model=model)}
    if not measured:
        return {**out, "priced": True}  # a known price, but no measured tokens to price
    usd = estimate_cost(provider, model, input_tokens=input_tokens, output_tokens=output_tokens).cost_usd
    return {**out, "priced": True, "usd": round(float(usd), 6)}


async def _failing_results(db: AsyncSession, run: TestRun) -> int:
    return int((await db.execute(
        select(func.count(TestCase.id)).where(
            TestCase.test_run_id == run.id, TestCase.status.in_(FAILING_STATUSES),
        )
    )).scalar_one() or 0)


async def deep_estimate(db: AsyncSession, run: TestRun, *, configuration: DeepRunConfiguration) -> dict[str, Any]:
    """A ``DeepEstimateResponse`` body: the gate, the configuration, the measured p50s."""
    from fastapi import HTTPException, status  # noqa: PLC0415

    from app.core.agent_ops_vocab import ESTIMATE_SELECTED_TOO_MANY  # noqa: PLC0415
    from app.services.ai_config_resolver import get_effective_ai_config  # noqa: PLC0415
    from app.services.deep_investigation_gate import deep_investigation_gate  # noqa: PLC0415

    gate = await deep_investigation_gate(db)
    failing = await _failing_results(db, run)
    if configuration.selected_failures is not None and configuration.selected_failures > failing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ESTIMATE_SELECTED_TOO_MANY.format(n=failing),
        )
    global_ai_config = await get_effective_ai_config(db=db)
    run_overrides, resolved = await validate_run_configuration(
        db, run, configuration, global_ai_config=global_ai_config,
    )
    tightened = bool(run_overrides and run_overrides.get("agent_patches"))
    project = (
        await resolve_project_config(db, run.project_id, global_ai_config=global_ai_config)
        if tightened
        else resolved
    )
    config_read = configuration_read(resolved, project, configuration, failing)

    rows = (await db.execute(
        text(_ESTIMATE_SQL),
        {"project_id": run.project_id, "sample_size": ESTIMATE_SAMPLE_PIPELINES},
    )).mappings().all()
    sample_pipelines = int(rows[0]["sample_pipelines"]) if rows else 0
    measured = sample_pipelines > 0
    stages, scaling_delta = _stage_rows(list(rows), int(config_read["failures_analysed"]))
    input_tokens = sum(stage["input_tokens_p50"] or 0 for stage in stages)
    output_tokens = sum(stage["output_tokens_p50"] or 0 for stage in stages)
    pipeline_seconds = rows[0]["pipeline_seconds_p50"] if rows else None
    minutes = (
        round(max(0.0, float(pipeline_seconds) + scaling_delta) / 60.0, 1)
        if measured and pipeline_seconds is not None
        else None
    )
    tokens = (input_tokens + output_tokens) if measured and any(
        stage["input_tokens_p50"] is not None or stage["output_tokens_p50"] is not None for stage in stages
    ) else None
    return {
        "run_id": run.id,
        "gate": _gate_read(gate),
        "configuration": config_read,
        "measured": measured,
        "reason": None if measured else ESTIMATE_UNMEASURED,
        "sample_pipelines": sample_pipelines,
        "minutes_p50": minutes,
        "tokens_p50": tokens,
        "cost": _cost(resolved, measured=measured, input_tokens=input_tokens, output_tokens=output_tokens),
        "stages": stages,
    }
