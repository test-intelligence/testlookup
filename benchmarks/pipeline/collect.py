"""Collect an AI-pipeline baseline from telemetry the pipeline already writes.

Reads ``agent_pipeline_runs`` + ``agent_stage_results`` over a time window and
emits the aggregate document defined by ``aggregate.build_baseline``.

Deliberately raw SQL over asyncpg rather than the app's ORM: this must run
against any environment that has pipeline data -- a homelab, a dev compose
stack, a restored dump -- without importing ``app`` (whose engine is built at
import time) or matching its dependency set.

    python benchmarks/pipeline/collect.py --days 30 --output benchmarks/results/pipeline_baseline.json

An empty window is an ERROR, not an empty baseline. A file full of nulls looks
exactly like a measured result once it is committed, and the entire point of
this harness is that later work can be compared against something real.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregate import build_baseline  # noqa: E402

_RUNS_SQL = """
    SELECT id, test_run_id, workflow_type, status, started_at, completed_at
    FROM agent_pipeline_runs
    WHERE started_at IS NOT NULL
      AND started_at >= $1
      AND ($2::text IS NULL OR workflow_type = $2)
    ORDER BY started_at DESC
    LIMIT $3
"""

_STAGES_SQL = """
    SELECT pipeline_run_id, stage_name, status, started_at, completed_at,
           input_tokens, output_tokens, total_tokens, llm_calls_count, cost_usd,
           fallback_used, fallback_reason, error_category, analysis_mode,
           execution_path,
           result_data, decision_log
    FROM agent_stage_results
    WHERE pipeline_run_id = ANY($1::uuid[])
"""


def _json_field(value: Any) -> Any:
    """asyncpg hands JSON columns back as text unless a codec is registered."""
    if isinstance(value, (dict, list)) or value is None:
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _normalize_dsn(url: str) -> str:
    """Strip the SQLAlchemy driver suffix — asyncpg wants a plain postgres DSN."""
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )


async def _fetch_grounding(
    mongo_uri: str, test_run_ids: list[str], *, mongo_db: Optional[str] = None
) -> tuple[list[dict], list[dict]]:
    """Read published decision reports + run summaries for the same runs.

    Returns ``(reports, summaries)``. The two stores answer different grounding
    questions: reports carry typed claims with an evidence list, summaries
    carry the generated narrative whose citations come from verbatim matching.

    Only PUBLISHED reports count. A rejected or superseded attempt is not what
    a reader was shown, and scoring it would flatter or damn the baseline with
    output nobody acted on.
    """
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:  # pragma: no cover - environment guard
        raise SystemExit(
            "motor is required for --mongo-uri. Run inside the backend "
            "container (make shell-backend) or drop the flag to skip grounding."
        )

    client = AsyncIOMotorClient(mongo_uri)
    try:
        # The deployed MONGO_URI carries no database path (it is
        # ``mongodb://host:27017``), so get_default_database() raises. The app
        # keeps the name in a separate setting -- MONGO_DB, default
        # "testlookup_logs" -- and this collector has to resolve it the same
        # way rather than assuming the URI is self-describing.
        db = client[mongo_db] if mongo_db else client.get_default_database()
        report_docs = await db["decision_reports"].find(
            {"test_run_id": {"$in": test_run_ids}, "status": "published"},
            {"_id": 0, "test_run_id": 1, "report_version": 1, "decision_intelligence": 1},
        ).to_list(length=len(test_run_ids) * 5)
        summary_docs = await db["run_summaries"].find(
            {"test_run_id": {"$in": test_run_ids}},
            {"_id": 0, "test_run_id": 1, "layer1_executive_summary": 1,
             "layer2_incident_view": 1, "layer3_evidence_pack": 1,
             "layer4_action_plan": 1},
        ).to_list(length=len(test_run_ids))
    finally:
        client.close()

    # Keep only the newest published version per run — older versions were
    # superseded, and counting them would weight noisy runs more heavily.
    latest: dict[str, dict] = {}
    for doc in sorted(report_docs, key=lambda d: d.get("report_version") or 0, reverse=True):
        run_id = str(doc.get("test_run_id") or "")
        if run_id and run_id not in latest:
            latest[run_id] = dict(doc.get("decision_intelligence") or {})
    return list(latest.values()), summary_docs


async def collect(
    dsn: str,
    *,
    days: int,
    workflow_type: Optional[str],
    limit: int,
    mongo_uri: Optional[str] = None,
    mongo_db: Optional[str] = None,
    since: Optional[datetime] = None,
) -> dict[str, Any]:
    """Aggregate recorded pipeline telemetry into a baseline.

    ``since`` overrides the ``days`` window with an explicit lower bound. It
    exists because ``--days 1`` is too coarse to separate code versions when
    several deploys land in one day: a "post-fix" baseline taken that way was
    86% pre-fix runs, which is worse than no baseline at all.
    """
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - environment guard
        raise SystemExit(
            "asyncpg is required. Run this inside the backend container "
            "(make shell-backend) or pip install asyncpg."
        )

    conn = await asyncpg.connect(_normalize_dsn(dsn))
    try:
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=days))
        run_rows = await conn.fetch(_RUNS_SQL, cutoff, workflow_type, limit)
        run_ids = [r["id"] for r in run_rows]
        stage_rows = await conn.fetch(_STAGES_SQL, run_ids) if run_ids else []
    finally:
        await conn.close()

    stages_by_run: dict[Any, list[dict]] = {}
    for row in stage_rows:
        stages_by_run.setdefault(row["pipeline_run_id"], []).append({
            "stage_name": row["stage_name"],
            "status": row["status"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "total_tokens": row["total_tokens"],
            "llm_calls_count": row["llm_calls_count"],
            "cost_usd": row["cost_usd"],
            "fallback_used": row["fallback_used"],
            "fallback_reason": row["fallback_reason"],
            "error_category": row["error_category"],
            "analysis_mode": row["analysis_mode"],
            "execution_path": row["execution_path"],
            "result_data": _json_field(row["result_data"]),
            "decision_log": _json_field(row["decision_log"]),
        })

    runs = [{
        "pipeline_run_id": str(row["id"]),
        "test_run_id": str(row["test_run_id"]),
        "workflow_type": row["workflow_type"],
        "status": row["status"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "stages": stages_by_run.get(row["id"], []),
    } for row in run_rows]

    reports = summaries = None
    grounding_error: Optional[str] = None
    if mongo_uri and runs:
        # Grounding documents are keyed by test_run_id, not pipeline_run_id.
        test_run_ids = sorted({r["test_run_id"] for r in runs})
        try:
            reports, summaries = await _fetch_grounding(
                mongo_uri, test_run_ids, mongo_db=mongo_db
            )
        except Exception as exc:  # noqa: BLE001
            # Grounding is an ENRICHMENT. Letting its failure propagate threw
            # away a complete set of already-collected Postgres measurements --
            # observed against the homelab, where the URI carried no database
            # name. An optional metric must never cost the primary one.
            reports = summaries = None
            grounding_error = f"{type(exc).__name__}: {exc}"[:200]
            print(f"warning: grounding skipped — {grounding_error}", file=sys.stderr)

    baseline = build_baseline(
        runs,
        window={
            "days": days,
            "since": since.isoformat() if since else None,
            "workflow_type": workflow_type or "all",
            "limit": limit,
            "grounding_source": "mongo" if mongo_uri else None,
            **({"grounding_error": grounding_error} if grounding_error else {}),
        },
        reports=reports,
        summaries=summaries,
    )
    baseline["generated_at"] = datetime.now(timezone.utc).isoformat()
    return baseline


def render(baseline: dict[str, Any]) -> str:
    """A terminal summary — the JSON is the artifact, this is for reading."""
    lines = [
        f"AI pipeline baseline — {baseline.get('runs_observed', 0)} run(s) "
        + (
            # Saying "over 30 day(s)" when an explicit bound was given is how a
            # narrow, version-isolated baseline gets read as a monthly one.
            f"since {baseline.get('window', {}).get('since')}"
            if baseline.get("window", {}).get("since")
            else f"over {baseline.get('window', {}).get('days')} day(s)"
        ),
    ]
    if not baseline.get("sufficient_samples"):
        lines.append(
            "  ! FEW SAMPLES — percentiles below are arithmetic, not evidence."
        )
    # Printed before the tables, because it changes how the degraded column
    # should be read. Learned the hard way: the first real baseline reported
    # 86% degraded when 747 of 765 failures were one hour, three weeks back.
    temporal = baseline.get("temporal") or {}
    if temporal.get("degradation_is_concentrated"):
        lines.append(f"  ! {temporal.get('concentration_note')}")
        off_peak = temporal.get("degraded_rate_excluding_peak_day")
        if off_peak is not None:
            lines.append(
                f"  ! Excluding {temporal.get('peak_degraded_day')}, the degraded "
                f"rate is {off_peak:.1%}."
            )
    lines.append("")
    # Note for editors: this file must parse on Python 3.11 (what CI runs), so
    # no nested same-quote f-strings — every cell is formatted before interpolation.
    def _num_cell(dist: Any, key: str, digits: int = 1) -> str:
        value = (dist or {}).get(key)
        return "-" if value is None else format(value, f".{digits}f")

    def _pct_cell(value: Any) -> str:
        return "-" if value is None else format(value, ".0%")

    lines.append(f"  {'BAND':<8} {'RUNS':>5} {'E2E p50':>10} {'E2E p95':>10} "
                 f"{'TOK p50':>10} {'USD p50':>9} {'DEGRADED':>9}")
    for name, band in (baseline.get("bands") or {}).items():
        e2e = band.get("end_to_end_seconds")
        lines.append(
            f"  {name:<8} {band.get('runs', 0):>5} "
            f"{_num_cell(e2e, 'p50'):>10} "
            f"{_num_cell(e2e, 'p95'):>10} "
            f"{_num_cell(band.get('tokens_per_run'), 'p50', 0):>10} "
            f"{_num_cell(band.get('cost_per_run_usd'), 'p50', 4):>9} "
            f"{_pct_cell(band.get('degraded_rate')):>9}"
        )
    lines.append("")
    lines.append(f"  {'STAGE':<30} {'RUNS':>5} {'p50 s':>8} {'p95 s':>8} "
                 f"{'FALLBACK':>9} {'PARSE FAIL':>11}")
    for name, stage in (baseline.get("stages") or {}).items():
        latency = stage.get("latency_seconds") or {}
        lines.append(
            f"  {name:<30} {stage.get('observations', 0):>5} "
            f"{_num_cell(latency, 'p50'):>8} "
            f"{_num_cell(latency, 'p95'):>8} "
            f"{_pct_cell(stage.get('fallback_rate')):>9} "
            f"{stage.get('parse_failures', 0):>11}"
        )
    grounding = baseline.get("grounding") or {}
    claims = grounding.get("claims")
    narrative = grounding.get("narrative")
    if claims or narrative:
        lines.append("")
        lines.append("  GROUNDING")
    if claims:
        lines.append(
            f"    claims: {claims.get('claims_total', 0)} across "
            f"{claims.get('reports', 0)} published report(s); "
            f"with evidence {_pct_cell(claims.get('claims_with_evidence_rate'))}"
        )
        shared = claims.get("shared_evidence_bundle_rate")
        lines.append(
            f"    reports where EVERY claim shares one evidence set: "
            f"{claims.get('reports_sharing_one_evidence_set', 0)}"
            f"/{claims.get('multi_claim_reports', 0)} ({_pct_cell(shared)})"
            "   <- finding F-16"
        )
    if narrative:
        lines.append(
            f"    narrative: {narrative.get('summaries_with_any_citation', 0)}"
            f"/{narrative.get('summaries', 0)} summaries carry any citation "
            f"({_pct_cell(narrative.get('citation_rate'))}); "
            f"{narrative.get('citations_total', 0)} citation(s) total"
        )
        lines.append(
            f"    layers that CAN be cited: {', '.join(narrative.get('citable_layers') or [])} "
            f"— uncitable by construction: "
            f"{', '.join(narrative.get('uncitable_layers') or [])}   <- finding F-3"
        )
    not_measured = baseline.get("not_measured") or []
    if not_measured:
        lines.append("")
        lines.append("  NOT MEASURED (absent from this baseline on purpose):")
        for item in not_measured:
            lines.append(f"    - {item.get('metric')}: {item.get('why')}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--workflow-type", default=None,
                        help="offline | deep | live (default: all)")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI"),
                        help="read the report store for grounding metrics "
                             "(claim evidence coverage, narrative citations)")
    parser.add_argument("--mongo-db", default=os.environ.get("MONGO_DB", "testlookup_logs"),
                        help="database name; the deployed MONGO_URI carries none")
    parser.add_argument("--since", default=None,
                        help="ISO timestamp lower bound, overriding --days. Use "
                             "this to isolate one code version: --days 1 cannot "
                             "separate deploys that land in the same day")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.database_url:
        print("error: --database-url or DATABASE_URL is required", file=sys.stderr)
        return 2

    since = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        except ValueError:
            print(f"error: --since is not an ISO timestamp: {args.since}", file=sys.stderr)
            return 2
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    baseline = asyncio.run(collect(
        args.database_url,
        days=args.days,
        workflow_type=args.workflow_type,
        limit=args.limit,
        mongo_uri=args.mongo_uri,
        mongo_db=args.mongo_db,
        since=since,
    ))

    if baseline.get("runs_observed", 0) == 0:
        print(
            (f"error: no pipeline runs since {args.since}" if args.since
             else f"error: no pipeline runs in the last {args.days} day(s)")
            + (f" for workflow_type={args.workflow_type}" if args.workflow_type else "")
            + ".\n"
            "       Refusing to write an empty baseline: a file of nulls is\n"
            "       indistinguishable from a measured result once committed.\n"
            "       Ingest a run with failures, let the pipeline finish, retry.",
            file=sys.stderr,
        )
        return 1

    print(render(baseline))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(baseline, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
