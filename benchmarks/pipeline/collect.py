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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregate import build_baseline  # noqa: E402

_RUNS_SQL = """
    SELECT id, workflow_type, status, started_at, completed_at
    FROM agent_pipeline_runs
    WHERE started_at IS NOT NULL
      AND started_at >= now() - make_interval(days => $1)
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


async def collect(
    dsn: str, *, days: int, workflow_type: Optional[str], limit: int
) -> dict[str, Any]:
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - environment guard
        raise SystemExit(
            "asyncpg is required. Run this inside the backend container "
            "(make shell-backend) or pip install asyncpg."
        )

    conn = await asyncpg.connect(_normalize_dsn(dsn))
    try:
        run_rows = await conn.fetch(_RUNS_SQL, days, workflow_type, limit)
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
        "workflow_type": row["workflow_type"],
        "status": row["status"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "stages": stages_by_run.get(row["id"], []),
    } for row in run_rows]

    baseline = build_baseline(runs, window={
        "days": days,
        "workflow_type": workflow_type or "all",
        "limit": limit,
    })
    baseline["generated_at"] = datetime.now(timezone.utc).isoformat()
    return baseline


def render(baseline: dict[str, Any]) -> str:
    """A terminal summary — the JSON is the artifact, this is for reading."""
    lines = [
        f"AI pipeline baseline — {baseline.get('runs_observed', 0)} run(s) "
        f"over {baseline.get('window', {}).get('days')} day(s)",
    ]
    if not baseline.get("sufficient_samples"):
        lines.append(
            "  ! FEW SAMPLES — percentiles below are arithmetic, not evidence."
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
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.database_url:
        print("error: --database-url or DATABASE_URL is required", file=sys.stderr)
        return 2

    baseline = asyncio.run(collect(
        args.database_url,
        days=args.days,
        workflow_type=args.workflow_type,
        limit=args.limit,
    ))

    if baseline.get("runs_observed", 0) == 0:
        print(
            f"error: no pipeline runs in the last {args.days} day(s)"
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
