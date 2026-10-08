"""A run with no pass rate is not a run at 0%.

A run still in progress has a NULL ``pass_rate`` until ingestion finishes it,
and a finished run that measured no tests has none at all. ``pass_rate or 0``
read both as 0% in prose a person reads: the release gate ("NO GO, pass rate
0.0%"), the chat sidebar ("completed — 1 test failed. Pass rate: 0.0% (10/11
executed)"), global search ("IN_PROGRESS · 0% pass rate") and the run summary
("at a 0.0% pass rate"); the UX redesign's browser E2E pass, 2026-10-08. The
gate and the chat sidebar have their own tests; these pin the other two.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from app.services import global_search_service  # noqa: E402

pytestmark = pytest.mark.regression


def _run(pass_rate, status="IN_PROGRESS"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="viz-3044",
        branch="main",
        status=status,
        pass_rate=pass_rate,
    )


async def _search(runs):
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=runs)))
    ))
    return await global_search_service._search_test_runs(db, "viz", None, None)


@pytest.mark.asyncio
async def test_search_says_a_run_without_a_rate_has_none():
    (hit,) = await _search([_run(None)])
    assert hit["subtitle"] == "main · IN_PROGRESS · no pass rate yet"
    assert hit["metadata"]["pass_rate"] is None


@pytest.mark.asyncio
async def test_search_still_states_a_measured_rate_zero_included():
    hits = await _search([_run(90.9, "PASSED"), _run(0.0, "FAILED")])
    assert [h["subtitle"] for h in hits] == [
        "main · PASSED · 91% pass rate",
        "main · FAILED · 0% pass rate",
    ]


def test_the_run_summary_does_not_print_zero_for_no_rate():
    """``build_run_summary`` reads the DB; the sentence is what changed, so pin
    its source: no ``pass_rate or 0`` left in it."""
    from pathlib import Path

    import app.services.run_summary_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "with no pass rate (no test passed or failed)" in source
    assert 'f"at a {run.pass_rate or 0' not in source
