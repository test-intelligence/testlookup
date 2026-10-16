"""Failed builds grouped by their real error (W07 FAIL-03, ``GET /runs/failure-signatures``).

The Runs page's "Failure signatures" panel used to key a build on its
``(passed, failed, total)`` counts: two builds with equal counts and different
errors were "one regression", and the same error at different counts was two.
This read returns, per run, its top failure signatures -- ``FAILURE_SIGNATURE_SQL``
over each failing row's message, the one normalisation every other failure
grouping uses (``failure_signature``: "nothing else may spell it again") -- and
the page groups builds by their DOMINANT signature.

Two statements on the request session, nothing else:

1. the requested ids that exist in a live project the caller may read (the rest
   are ``unavailable``: unknown, foreign and soft-deleted answer alike);
2. ONE ``text()`` statement: per run, at most ``SIGNATURE_ROWS_PER_RUN``
   FAILED/BROKEN rows (``ix_test_cases_run_status``, ``ORDER BY id``), grouped
   by signature, the top three per run by count (ties by signature
   ``COLLATE "C"``), with the run's full failing count beside the rows read so
   a capped run says so.

No compute beyond that, no write, no second session
(``tests/regression/test_run_signatures_read_only.py``).
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text

from app.models.postgres import Project, TestRun
from app.services.failure_signature import (
    FAILURE_SIGNATURE_PARAMS,
    FAILURE_SIGNATURE_SQL,
    LABEL_LINE_CAP,
    first_line_sql,
    signature_bucket_id,
)
from app.services.run_verdict import MAX_SIGNATURE_RUN_IDS, SIGNATURE_ROWS_PER_RUN

__all__ = ["LABEL_CHARS", "TOP_SIGNATURES", "batch_run_signatures"]

#: Signatures returned per run.
TOP_SIGNATURES = 3
#: A group's label: its most common raw first line, cut to this.
LABEL_CHARS = 160

_SQL = f"""
WITH ids AS (SELECT unnest(CAST(:ids AS uuid[])) AS run_id),
f AS (
  SELECT ids.run_id, x.sig, x.raw_line, x.test_name, x.fp
  FROM ids CROSS JOIN LATERAL (
    SELECT {FAILURE_SIGNATURE_SQL} AS sig,
           {first_line_sql("tc.error_message", LABEL_LINE_CAP)} AS raw_line,
           tc.test_name AS test_name, tc.test_fingerprint AS fp
    FROM test_cases tc
    WHERE tc.test_run_id = ids.run_id AND tc.status IN ('FAILED', 'BROKEN')
    ORDER BY tc.id
    LIMIT :per_run
  ) x
),
totals AS (
  SELECT tc.test_run_id AS run_id, COUNT(*) AS failing
  FROM test_cases tc
  WHERE tc.test_run_id = ANY(CAST(:ids AS uuid[])) AND tc.status IN ('FAILED', 'BROKEN')
  GROUP BY tc.test_run_id
),
reads AS (SELECT run_id, COUNT(*) AS n FROM f GROUP BY run_id),
g AS (
  SELECT run_id, sig, COUNT(*) AS n, COUNT(DISTINCT fp) AS test_count,
         mode() WITHIN GROUP (ORDER BY raw_line COLLATE "C") AS label,
         (array_agg(DISTINCT test_name COLLATE "C" ORDER BY test_name COLLATE "C"))[1:{TOP_SIGNATURES}] AS tests
  FROM f GROUP BY run_id, sig
),
ranked AS (
  SELECT g.*, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY n DESC, sig COLLATE "C") AS rn
  FROM g
)
SELECT t.run_id, t.failing, COALESCE(rd.n, 0) AS read_rows,
       r.sig, r.n, r.test_count, r.label, r.tests, r.rn
FROM totals t
LEFT JOIN reads rd ON rd.run_id = t.run_id
LEFT JOIN ranked r ON r.run_id = t.run_id AND r.rn <= {TOP_SIGNATURES}
ORDER BY t.run_id, r.rn
"""


def _entry(row: Any) -> dict[str, Any]:
    signature = signature_bucket_id(row["sig"])
    label = "" if not row["sig"] else (row["label"] or "")[:LABEL_CHARS]
    return {
        "signature": signature,
        "label": label,
        "count": int(row["n"]),
        "test_count": int(row["test_count"] or 0),
        "tests": [str(name) for name in (row["tests"] or []) if name is not None],
    }


async def batch_run_signatures(
    db: Any,
    run_ids: Sequence[uuid.UUID],
    *,
    accessible: set[uuid.UUID] | None,
) -> dict[str, Any]:
    """``{"items", "unavailable", "max_ids": 50, "rows_per_run_cap": 500}``; items in the request's order.

    ``accessible`` is ``get_accessible_project_ids``' answer (``None`` = admin;
    an EMPTY set answers "all unavailable" with no statement).
    """
    ids = list(dict.fromkeys(i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)) for i in run_ids))
    envelope: dict[str, Any] = {
        "items": [],
        "unavailable": [],
        "max_ids": MAX_SIGNATURE_RUN_IDS,
        "rows_per_run_cap": SIGNATURE_ROWS_PER_RUN,
    }
    if accessible is not None and not accessible:
        envelope["unavailable"] = [str(i) for i in ids]
        return envelope

    visible = select(TestRun.id).where(
        TestRun.id.in_(ids),
        TestRun.project_id.in_(select(Project.id).where(Project.is_active.is_(True))),
    )
    if accessible is not None:
        visible = visible.where(TestRun.project_id.in_(accessible))
    readable = set((await db.execute(visible)).scalars().all())

    per_run: dict[uuid.UUID, dict[str, Any]] = {}
    if readable:
        params: dict[str, Any] = {
            **FAILURE_SIGNATURE_PARAMS,
            "ids": [str(i) for i in ids if i in readable],
            "per_run": SIGNATURE_ROWS_PER_RUN,
        }
        for row in (await db.execute(text(_SQL), params)).mappings().all():
            item = per_run.setdefault(row["run_id"], {
                "failing_tests": int(row["failing"]),
                "read": int(row["read_rows"]),
                "signatures": [],
            })
            if row["rn"] is not None:
                item["signatures"].append(_entry(row))

    for run_id in ids:
        if run_id not in readable:
            envelope["unavailable"].append(str(run_id))
            continue
        found = per_run.get(run_id)
        failing = found["failing_tests"] if found else 0
        read = found["read"] if found else 0
        signatures = found["signatures"] if found else []
        envelope["items"].append({
            "run_id": str(run_id),
            "failing_tests": failing,
            "read": read,
            "capped": read < failing,
            "dominant": signatures[0]["signature"] if signatures else None,
            "signatures": signatures,
        })
    return envelope
