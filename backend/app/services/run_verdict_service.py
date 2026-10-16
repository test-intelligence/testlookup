"""The Runs table's AI verdicts in ONE read (W07 FAIL-04, ``GET /runs/ai-verdicts``).

The column used to ask ``GET /runs/{id}/intelligence`` once per run on screen,
and a cache miss there COMPUTES the run's intelligence and writes a snapshot.
This read computes nothing: it reads each run's stored, servable snapshot and
returns only what ``aiVerdictOf`` reads (``run_verdict.verdict_inputs_of``'s
slim shape), plus the run's live report-review state. EXACTLY two statements
on the request session, nothing else:

1. one ``text()`` statement over ``test_runs`` LEFT JOIN the servable snapshot
   (``schema_version >= CURRENT_SCHEMA_VERSION``), the payload cast to
   ``jsonb`` ONCE per row in a LATERAL (a ``json`` operator would re-parse the
   whole text on every call), every read path answered in SQL;
2. ``review_envelope.report_review_states`` for the runs that have a snapshot
   (skipped when none has one).

No Mongo, no ``get_run_intelligence``, no write, no ``BackgroundTasks``, no
``AsyncSessionLocal`` (``tests/regression/test_run_ai_verdicts_read_only.py``).
A run that is unknown, in a project the caller cannot read, or in a
soft-deleted project is listed in ``unavailable`` and nowhere else: one list,
the same answer for all three, so the shape is no oracle.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from app.services.intelligence_snapshot_service import CURRENT_SCHEMA_VERSION
from app.services.review_envelope import report_review_states
from app.services.run_verdict import MAX_VERDICT_RUN_IDS

__all__ = ["JSONB_PAYLOAD_SQL", "batch_verdict_inputs", "truthy_sql"]

#: A run with no live report review reads as a draft (the per-run fail-closed rule).
_NO_REVIEW_STATE = "pending_review"


def truthy_sql(expr: str) -> str:
    """JavaScript's ``Boolean(x)`` over a ``jsonb`` expression, in SQL (the ONE builder).

    An object or an array (even empty) is true; a string is true unless empty; a
    number unless 0; a boolean is itself; ``null`` and a missing value are false.
    Migration 0214 carries a FROZEN copy of this rule for its backfill.
    """
    return (
        f"(CASE jsonb_typeof({expr}) "
        "WHEN 'object' THEN TRUE WHEN 'array' THEN TRUE "
        f"WHEN 'string' THEN ({expr} #>> '{{}}') <> '' "
        f"WHEN 'number' THEN ({expr} #>> '{{}}')::numeric <> 0 "
        f"WHEN 'boolean' THEN ({expr} #>> '{{}}')::boolean "
        "ELSE FALSE END)"
    )


#: The snapshot's ``json`` payload as ``jsonb``, parsed ONCE per row. A ``json``
#: value may hold the escape ``\u0000`` (a NUL from a Mongo-sourced string)
#: that ``jsonb`` refuses ("unsupported Unicode escape sequence"): one such
#: payload would fail the whole batch. Every ``\u0000`` escape is rewritten to
#: `` `` first -- a same-length escape, so the text stays valid JSON even
#: where the backslash was itself escaped -- which only turns a NUL into a
#: space in some string; no value the verdict reads can be "passed" or "GO"
#: with a NUL in it either way. Migration 0214 freezes the same expression.
JSONB_PAYLOAD_SQL = "CAST(replace(CAST(s.payload AS text), E'\\\\u0000', E'\\\\u0020') AS jsonb)"

_DI = "(j.p #> '{structured_summary,decision_intelligence}')"
_VER = "(j.p #> '{structured_summary,decision_report_verification}')"
_ATT = "(j.p #> '{structured_summary,latest_decision_attempt}')"
_RD = "(j.p -> 'release_decision')"
_TA = "(j.p -> 'top_analyses')"
_IA = "(j.p -> 'intelligence_available')"

# Each read LEAF comes back as the TEXT of its JSON value (``'"passed"'``,
# ``'1'``, ``'["passed"]'``, ``'null'``) and is ``json.loads``-ed: a leaf keeps
# its JSON type, never coerced. A missing path is SQL NULL -> None.
_SELECT = f"""
SELECT r.id AS run_id,
       r.project_id AS project_id,
       s.run_id IS NOT NULL AS snapshot,
       s.stale AS stale,
       s.generated_at AS generated_at,
       EXISTS (
           SELECT 1 FROM agent_pipeline_runs p
           WHERE p.test_run_id = r.id AND p.status IN ('completed', 'passed', 'failed')
       ) AS analysed,
       jsonb_typeof(j.p -> 'structured_summary') AS ss_type,
       jsonb_typeof({_DI}) AS di_type,
       {truthy_sql(_DI)} AS di_truthy,
       ({_DI} #> '{{verification,status}}')::text AS di_v_status,
       ({_DI} #> '{{release_decision,recommendation}}')::text AS di_rec,
       jsonb_typeof({_VER}) AS ver_type,
       {truthy_sql(_VER)} AS ver_truthy,
       ({_VER} -> 'status')::text AS ver_status,
       jsonb_typeof({_ATT}) AS att_type,
       {truthy_sql(_ATT)} AS att_truthy,
       ({_ATT} -> 'status')::text AS att_status,
       ({_ATT} -> 'verification_status')::text AS att_v_status,
       jsonb_typeof({_RD}) AS rd_type,
       {truthy_sql(_RD)} AS rd_truthy,
       ({_RD} -> 'recommendation')::text AS rd_rec,
       {truthy_sql(_IA)} AS ia_truthy,
       jsonb_typeof({_TA}) AS ta_type,
       CASE jsonb_typeof({_TA})
           WHEN 'array' THEN jsonb_array_length({_TA})
           WHEN 'string' THEN length({_TA} #>> '{{}}')
           WHEN 'object' THEN CASE WHEN jsonb_typeof({_TA} -> 'length') = 'number'
                                   THEN ({_TA} ->> 'length')::numeric END
       END AS ta_length
FROM test_runs r
LEFT JOIN run_intelligence_snapshots s
       ON s.run_id = r.id AND s.schema_version >= :current_version
LEFT JOIN LATERAL (SELECT {JSONB_PAYLOAD_SQL} AS p) j ON TRUE
WHERE r.id = ANY(CAST(:ids AS uuid[]))
  AND r.project_id IN (SELECT id FROM projects WHERE is_active IS TRUE)
"""
_ACCESSIBLE = " AND r.project_id = ANY(CAST(:accessible AS uuid[]))"


def _leaf(raw: Any) -> Any:
    """A leaf's JSON text back to its JSON value; SQL NULL (no such path) -> None."""
    return None if raw is None else json.loads(raw)


def _object(kind: Any, truthy: Any, leaves: dict[str, Any]) -> Any:
    """The §1.2 rule: an object -> its leaves; a truthy non-object -> ``[]``; else ``None``."""
    if kind == "object":
        return leaves
    return [] if truthy else None


def _top_analyses(kind: Any, length: Any) -> list[bool] | None:
    if kind in ("array", "string"):
        return [True] if (length or 0) > 0 else []
    if kind == "object" and length is not None and length > 0:
        return [True]
    return None


def _verdict_inputs(row: Any) -> dict[str, Any]:
    """Assemble the slim payload from one row (the SQL twin of ``verdict_inputs_of``)."""
    summary: dict[str, Any] | None = None
    if row["ss_type"] == "object":
        summary = {
            "decision_intelligence": _object(row["di_type"], row["di_truthy"], {
                "verification": {"status": _leaf(row["di_v_status"])},
                "release_decision": {"recommendation": _leaf(row["di_rec"])},
            }),
            "decision_report_verification": _object(row["ver_type"], row["ver_truthy"], {
                "status": _leaf(row["ver_status"]),
            }),
            "latest_decision_attempt": _object(row["att_type"], row["att_truthy"], {
                "status": _leaf(row["att_status"]),
                "verification_status": _leaf(row["att_v_status"]),
            }),
        }
    return {
        "structured_summary": summary,
        "release_decision": _object(row["rd_type"], row["rd_truthy"], {
            "recommendation": _leaf(row["rd_rec"]),
        }),
        "intelligence_available": bool(row["ia_truthy"]),
        "top_analyses": _top_analyses(row["ta_type"], row["ta_length"]),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (None if value is None else str(value))


async def batch_verdict_inputs(
    db: Any,
    run_ids: Sequence[uuid.UUID],
    *,
    accessible: set[uuid.UUID] | None,
) -> dict[str, Any]:
    """``{"items": [RunVerdictItem dicts in the request's order], "unavailable": [ids], "max_ids": 25}``.

    ``accessible`` is ``get_accessible_project_ids``' answer (``None`` = admin, no
    restriction; an EMPTY set short-circuits to "all unavailable" with no
    statement). The router validates the id count first.
    """
    ids = list(dict.fromkeys(i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)) for i in run_ids))
    if accessible is not None and not accessible:
        return {"items": [], "unavailable": [str(i) for i in ids], "max_ids": MAX_VERDICT_RUN_IDS}
    sql = _SELECT
    params: dict[str, Any] = {"ids": [str(i) for i in ids], "current_version": CURRENT_SCHEMA_VERSION}
    if accessible is not None:
        sql += _ACCESSIBLE
        params["accessible"] = sorted(str(p) for p in accessible)
    rows = {row["run_id"]: row for row in (await db.execute(text(sql), params)).mappings().all()}

    with_snapshot = [rid for rid, row in rows.items() if row["snapshot"]]
    states: dict[uuid.UUID, str] = {}
    if with_snapshot:
        states = await report_review_states(
            db,
            with_snapshot,
            project_ids=sorted({rows[rid]["project_id"] for rid in with_snapshot}, key=str),
        )

    items: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for run_id in ids:
        row = rows.get(run_id)
        if row is None:
            unavailable.append(str(run_id))
            continue
        if row["snapshot"]:
            items.append({
                "run_id": str(run_id),
                "snapshot": True,
                "stale": bool(row["stale"]),
                "generated_at": _iso(row["generated_at"]),
                "analysed": bool(row["analysed"]),
                "verdict_inputs": _verdict_inputs(row),
                "review": {"state": states.get(run_id, _NO_REVIEW_STATE)},
            })
        else:
            items.append({
                "run_id": str(run_id),
                "snapshot": False,
                "stale": None,
                "generated_at": None,
                "analysed": bool(row["analysed"]),
                "verdict_inputs": None,
                "review": None,
            })
    return {"items": items, "unavailable": unavailable, "max_ids": MAX_VERDICT_RUN_IDS}
