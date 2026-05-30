"""Tests for the per-(project, primary_suite_name) run-sequence helper.

Pins the contract behind the ``Run #N`` label that ``/runs``, ``/live``,
and ``/my-failures`` all render:

* The sequence partitions on ``(project_id, lower(trim(coalesce(
  primary_suite_name, ''))))`` so a NULL/empty suite name still gets a
  consistent bucket.
* Ranking order is ``created_at ASC, id ASC`` so equal-timestamp inserts
  don't swap numbers between requests.
* Empty input → empty result, no queries fired.
* Enrichment merges the sequence number into the response dict under
  ``run_seq``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _result(rows):
    """SQLAlchemy ``Result``-like mock for ``.all()``."""
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


@pytest.mark.asyncio
async def test_fetch_run_seq_map_empty_input_short_circuits():
    """No run_ids → no DB queries fired."""
    from app.services.runs_service import fetch_run_seq_map

    db = AsyncMock()
    db.execute = AsyncMock()

    result = await fetch_run_seq_map(db, [])

    assert result == {}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_run_seq_map_returns_per_partition_sequence():
    """End-to-end: stub the two execute() calls and verify the helper
    returns ``{run_id: rn}`` for the requested ids."""
    from app.services.runs_service import fetch_run_seq_map

    project_a = uuid.uuid4()
    run_a1, run_a2, run_a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        # 1: pair lookup — single (project_a, "smoke") partition.
        _result([SimpleNamespace(project_id=project_a, suite_key="smoke")]),
        # 2: ranked window query — three rows, sequence 1..3.
        _result([
            (run_a1, 1),
            (run_a2, 2),
            (run_a3, 3),
        ]),
    ])

    out = await fetch_run_seq_map(db, [run_a1, run_a2, run_a3])
    assert out == {str(run_a1): 1, str(run_a2): 2, str(run_a3): 3}


@pytest.mark.asyncio
async def test_fetch_run_seq_map_handles_multiple_partitions():
    """Two distinct (project, suite) pairs both end up in the result —
    the helper de-duplicates partitions for the WHERE clause but the
    final SELECT covers every requested run_id."""
    from app.services.runs_service import fetch_run_seq_map

    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    run_a, run_b = uuid.uuid4(), uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _result([
            SimpleNamespace(project_id=project_a, suite_key="smoke"),
            SimpleNamespace(project_id=project_b, suite_key="regression"),
        ]),
        _result([
            (run_a, 1),
            (run_b, 1),
        ]),
    ])

    out = await fetch_run_seq_map(db, [run_a, run_b])
    assert out == {str(run_a): 1, str(run_b): 1}


def test_enrich_runs_with_release_merges_run_seq():
    """The enricher attaches ``run_seq`` to the response dict when a
    ``run_seq_map`` is provided. Callers that don't care omit the kwarg
    and the field stays absent from the response."""
    from app.services.runs_service import enrich_runs_with_release

    run_id = uuid.uuid4()
    # Minimal TestRun-shape — serialize_run walks __table__.columns, so
    # the mock needs to expose that. SimpleNamespace works because the
    # enricher accesses run.id and run.project_id directly + iterates
    # the table columns via serialize_run.
    columns = [
        SimpleNamespace(name="id"),
        SimpleNamespace(name="project_id"),
        SimpleNamespace(name="build_number"),
    ]
    table = SimpleNamespace(columns=columns)
    run = SimpleNamespace(
        id=run_id,
        project_id=uuid.uuid4(),
        build_number="b-1",
        __table__=table,
    )

    enriched = enrich_runs_with_release(
        [run], release_map={}, run_seq_map={str(run_id): 7},
    )
    assert enriched[0]["run_seq"] == 7

    # Without a run_seq_map, the field is absent (legacy callers
    # shouldn't see new keys appear in their payload).
    enriched_no_seq = enrich_runs_with_release([run], release_map={})
    assert "run_seq" not in enriched_no_seq[0]
