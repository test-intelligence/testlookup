"""Regression: the chat run card promised an AI analysis nobody had queued.

Measured on the live homelab (2026-08-16). Six runs were ingested, one of them
with ``run_ai=true``. The /chat sidebar showed the analysed run, and beside the
other five::

    ui-5   AI PENDING   just now
      Build ui-5 completed - 3 tests failed. Pass rate: 66.7% (6/9 executed,
      1 skipped). AI analysis is being generated and will appear shortly.

Nothing was being generated. ``ingestion_pipeline.finalize_run`` queues the
agent pipeline only when the ``run_ai`` flag is set; when it is not, it logs
``agent_pipeline_skipped`` and returns. Nothing persists that decision, so the
stub builder in ``chat_service`` — which fires for every run with no summary in
Mongo — cannot tell "queued" from "never asked for" from "failed permanently".
It asserted the most optimistic of the three unconditionally, and for the five
runs above the promise could never come true.

Fix: say what is known. The card states there is no analysis rather than
predicting one, and the badge no longer reads PENDING with a pulsing spinner.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services import chat_service  # noqa: E402

pytestmark = pytest.mark.regression


def _run(build: str, *, passed: int, failed: int, skipped: int, broken: int, pass_rate: float):
    """One ``test_runs`` row, shaped as the stub builder reads it."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number=build,
        pass_rate=pass_rate,
        total_tests=passed + failed + skipped + broken,
        passed_tests=passed,
        failed_tests=failed,
        skipped_tests=skipped,
        broken_tests=broken,
        start_time=datetime(2026, 8, 16, 23, 30, tzinfo=timezone.utc),
    )


async def _summaries_for(runs):
    """Run the service with an empty Mongo, so every run takes the stub path."""
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])

    class _Mongo(dict):
        """Any collection asked for is empty — every run takes the stub path."""

        def __getitem__(self, _key):
            return MagicMock(find=MagicMock(return_value=cursor))

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=runs)))
    ))

    with patch("app.db.mongo.get_mongo_db", return_value=_Mongo()):
        return await chat_service.get_run_summaries(db, None, 5)


@pytest.mark.asyncio
async def test_stub_does_not_promise_an_analysis_that_was_never_queued():
    # ui-5 from the live fixture: 6 passed, 2 failed, 1 broken, 1 skipped.
    runs = [_run("ui-5", passed=6, failed=2, skipped=1, broken=1, pass_rate=66.7)]

    summaries = await _summaries_for(runs)

    assert len(summaries) == 1
    text = summaries[0]["executive_summary"]
    assert summaries[0]["is_stub"] is True
    # The promise, in every phrasing it could survive a careless edit in.
    assert "will appear shortly" not in text
    assert "being generated" not in text
    # It says what it knows instead.
    assert "No AI analysis has been generated for this run." in text


@pytest.mark.asyncio
async def test_the_figures_beside_the_notice_still_match_the_run():
    """The counts were right and must stay right — only the promise was wrong.

    Pass rate is over *executed* tests (total minus skips), and a broken test
    counts as a failure, so ui-5's "3 tests failed" covers 2 failed + 1 broken.
    """
    runs = [_run("ui-5", passed=6, failed=2, skipped=1, broken=1, pass_rate=66.7)]

    text = (await _summaries_for(runs))[0]["executive_summary"]

    assert "**ui-5**" in text
    assert "3 tests failed" in text
    assert "66.7%" in text
    assert "(6/9 executed, 1 skipped)" in text


@pytest.mark.asyncio
async def test_a_clean_run_is_not_described_as_failing():
    runs = [_run("ui-clean", passed=9, failed=0, skipped=1, broken=0, pass_rate=100.0)]

    text = (await _summaries_for(runs))[0]["executive_summary"]

    assert "completed with no failures" in text
    assert "will appear shortly" not in text
