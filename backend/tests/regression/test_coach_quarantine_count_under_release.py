"""S-E-1: "Quarantine candidates" read 0 whenever a release was selected.

The release branch counted ``getattr(e, "quarantine_recommended", False)``, an
attribute ``FlakyCoachEntry`` never had, so every entry fell back to ``False``.
This drives ``get_flaky_coach`` itself (the caches and the release lookup are
stubbed) and asserts the count of the entries that are actually shown.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import test_health_coach_service as svc

pytestmark = pytest.mark.regression


def _row(fp: str, rec: str) -> SimpleNamespace:
    return SimpleNamespace(
        test_fingerprint=fp,
        test_name=f"test_{fp}",
        suite_name="suite",
        failure_rate=0.5,
        total_runs=10,
        failed_runs=5,
        flaky_since=datetime(2026, 11, 1, tzinfo=timezone.utc),
        last_failure_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        quarantine_recommendation=rec,
        stabilization_actions=[],
        impact_score=10.0,
        status_history=["PASSED", "FAILED", "PASSED", "FAILED"],
        flaky_confidence_low=None,
        flaky_confidence_high=None,
        is_flaky_confidence=None,
    )


async def _coach(release_id: str | None, ran: set[str]):
    rows = [
        _row("a", "QUARANTINE"),
        _row("b", "QUARANTINE"),
        _row("c", "QUARANTINE"),  # did not run in the release
        _row("d", "INVESTIGATE"),
    ]
    with patch.object(svc, "_load_flaky_cache", AsyncMock(return_value=rows)), \
            patch.object(svc, "_load_intermittency_signals", AsyncMock(return_value={})), \
            patch.object(svc, "_load_manual_flaky_triage", AsyncMock(return_value=[])), \
            patch.object(svc, "_fingerprints_in_release", AsyncMock(return_value=ran)), \
            patch(
                "app.services.runs_service.failing_step_detail_by_fingerprint",
                AsyncMock(return_value={}),
            ):
        return await svc.get_flaky_coach(uuid.uuid4(), MagicMock(), release_id=release_id)


@pytest.mark.asyncio
async def test_release_filter_counts_the_quarantine_entries_it_shows():
    resp = await _coach("R1", ran={"a", "b", "d"})
    assert [e.test_fingerprint for e in resp.entries] == ["a", "b", "d"]
    assert resp.quarantine_candidates == 2


@pytest.mark.asyncio
async def test_without_a_release_every_quarantine_entry_counts():
    resp = await _coach(None, ran=set())
    assert resp.quarantine_candidates == 3
