"""Unit tests for the release-council synthesise path.

User-reported bug: ``GET /api/v1/release-readiness/{run_id}`` returned
404 for runs that hadn't been through deep investigation — which is
every run ingested via the live-stream SDK (offline workflow). The
new ``_synthesize_release_council`` derives a deterministic quick-
look decision from the run's aggregates so the /release-gate page
renders something useful instead of an "empty state — go trigger
deep investigation" prompt for routine successful runs.

These tests pin the contract against mock sessions; the full path
through the FastAPI router lives in tests/integration/.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")


class _ScalarResult:
    """Mimic SQLAlchemy execute result with .scalar_one_or_none() + .scalar_one()."""
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def scalar_one(self):
        return self._v


def _run(pass_rate, *, project_id=None, build_number="b-1"):
    """Build a fake TestRun row with the columns the synthesiser reads."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        pass_rate=pass_rate,
        project_id=project_id or uuid.uuid4(),
        build_number=build_number,
    )


def _db_with(run, *, open_defects=0):
    """Mock async session: 1st execute → TestRun lookup, 2nd → open-defects COUNT.

    A third execute is needed for the band-floor lookup added 2026-05-14
    (``_apply_band_floor`` resolves ``ReleaseGatePolicy.rules`` via
    ``_resolve_policy_for_project``). It calls ``.first()`` not
    ``.scalar_one_or_none``, but the helper short-circuits on a falsy row
    so returning ``None`` here is fine — the synth path falls through to
    the legacy ``score_to_recommendation`` verdict that this test pins.
    """
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _ScalarResult(run),
            _ScalarResult(open_defects),
            _PolicyResolveResult(None),    # no project policy → band-floor no-op
            _PolicyResolveResult(None),    # system-default fallback also empty
        ]),
    )


class _PolicyResolveResult:
    """Match the ``.first()`` shape used by ``_resolve_policy_for_project``.

    The helper does ``select(ReleaseGatePolicy.rules)`` and calls ``.first()``;
    a returned ``None`` lets the synth path keep its legacy recommendation
    without a band override.
    """
    def __init__(self, value):
        self._v = value

    def first(self):
        return self._v


@pytest.mark.asyncio
async def test_synth_unknown_run_returns_none():
    """A genuinely-missing run still surfaces as None (caller maps to 404)."""
    from app.services.release_council_service import _synthesize_release_council

    db = SimpleNamespace(execute=AsyncMock(return_value=_ScalarResult(None)))

    result = await _synthesize_release_council(uuid.uuid4(), db)
    assert result is None


@pytest.mark.asyncio
async def test_synth_passed_run_recommends_go():
    """A passed run with no defects + high pass rate should recommend GO."""
    from app.services.release_council_service import _synthesize_release_council

    run = _run(pass_rate=100.0)
    db = _db_with(run, open_defects=0)

    result = await _synthesize_release_council(run.id, db)

    assert result is not None
    assert result.synthesized is True
    assert result.recommendation == "GO"
    assert result.pass_rate == 100.0
    assert result.build_number == "b-1"
    # Synthesised responses have no cluster / defect / override context.
    assert result.cluster_insights == []
    assert result.open_defects_by_component == []
    assert result.override_audit == []
    # input_snapshot keeps the deterministic inputs so an auditor can
    # reproduce why this recommendation was returned.
    assert result.input_snapshot["synthesized"] is True
    assert result.input_snapshot["pass_rate"] == 100.0
    assert result.input_snapshot["open_defects"] == 0


@pytest.mark.asyncio
async def test_synth_low_pass_rate_recommends_no_go():
    """A run below 70% of the configured pass-rate threshold gets the
    hard-floor treatment: NO_GO regardless of composite score."""
    from app.core.config import settings
    from app.services.release_council_service import _synthesize_release_council

    floor = settings.RELEASE_PASS_RATE_THRESHOLD * 0.7
    run = _run(pass_rate=floor - 1.0)
    db = _db_with(run, open_defects=0)

    result = await _synthesize_release_council(run.id, db)

    assert result is not None
    assert result.synthesized is True
    assert result.recommendation == "NO_GO"
    # The hard floor bumps composite to at least 60.
    assert (result.composite_risk or 0) >= 60.0


@pytest.mark.asyncio
async def test_synth_reasoning_mentions_quick_look_and_deep_investigation():
    """The reasoning text guides the user toward Deep Investigation —
    that's what the page's banner relies on for the CTA copy."""
    from app.services.release_council_service import _synthesize_release_council

    run = _run(pass_rate=95.0)
    db = _db_with(run, open_defects=2)

    result = await _synthesize_release_council(run.id, db)

    assert result is not None
    assert result.reasoning is not None
    lowered = result.reasoning.lower()
    assert "quick-look" in lowered
    assert "deep investigation" in lowered
