"""Unit tests for the release-council band-floor layer (2026-05-14 feature).

Covers two helpers added to ``release_council_service.py`` so /release-gate
and /overview produce the same colour + verdict for the same project + run:

  * ``_worse_verdict(a, b)`` — pure function. Returns the stricter of two
    recommendations on the GO < CONDITIONAL < NO_GO ladder. Used to layer
    the band-derived verdict over the composite without ever softening.

  * ``_apply_band_floor(db, project_id, recommendation, pass_rate)`` — async,
    returning ``(recommendation, band, downgrades, band_policy)`` where
    ``band_policy`` is ``(policy_id, version, level)`` or ``None``.
    Resolves the active ``ReleaseGatePolicy`` for the project via
    ``metrics_service._resolve_policy_for_project``, counts open CRITICAL
    defects via ``metrics_service.count_open_critical_defects`` (the P0-cap
    input — resolved *internally* so no caller can pass the wrong severity
    set), runs ``classify_with_policy``, and returns ``worse_of(composite,
    band)``. No-ops when no policy is active (no defect count is issued).

The integration into ``_synthesize_release_council`` and ``get_release_council``
is exercised by ``test_release_council_synthesize.py``; here we test the
helpers in isolation so a regression in the worse-of logic doesn't hide
behind the larger synth response shape.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── _worse_verdict — pure function ────────────────────────────────────────


def test_worse_verdict_keeps_no_go_over_anything():
    """NO_GO is the strictest verdict — never softened."""
    from app.services.release_council_service import _worse_verdict
    assert _worse_verdict("NO_GO", "GO") == "NO_GO"
    assert _worse_verdict("GO", "NO_GO") == "NO_GO"
    assert _worse_verdict("NO_GO", "CONDITIONAL") == "NO_GO"
    assert _worse_verdict("CONDITIONAL", "NO_GO") == "NO_GO"
    assert _worse_verdict("NO_GO", "NO_GO") == "NO_GO"


def test_worse_verdict_keeps_conditional_over_go():
    from app.services.release_council_service import _worse_verdict
    assert _worse_verdict("CONDITIONAL", "GO") == "CONDITIONAL"
    assert _worse_verdict("GO", "CONDITIONAL") == "CONDITIONAL"
    assert _worse_verdict("CONDITIONAL", "CONDITIONAL") == "CONDITIONAL"


def test_worse_verdict_go_only_when_both_go():
    from app.services.release_council_service import _worse_verdict
    assert _worse_verdict("GO", "GO") == "GO"


def test_worse_verdict_recognises_canonical_conditional_go():
    """Regression: the recommendation vocabulary is ``CONDITIONAL_GO``
    (score_to_recommendation / persisted agent decision), not the band's
    ``CONDITIONAL``. ``_worse_verdict`` MUST rank it so a green band can't
    soften CONDITIONAL_GO → GO (the old bug returned the band verdict for the
    unrecognised composite, i.e. fail-OPEN)."""
    from app.services.release_council_service import _worse_verdict
    # The bug: CONDITIONAL_GO composite + GO band must NOT become GO.
    assert _worse_verdict("CONDITIONAL_GO", "GO") == "CONDITIONAL_GO"
    assert _worse_verdict("GO", "CONDITIONAL_GO") == "CONDITIONAL_GO"
    # CONDITIONAL_GO and the band's CONDITIONAL are the same severity.
    assert _worse_verdict("CONDITIONAL_GO", "CONDITIONAL") == "CONDITIONAL_GO"
    # NO_GO still wins over CONDITIONAL_GO.
    assert _worse_verdict("CONDITIONAL_GO", "NO_GO") == "NO_GO"


def test_worse_verdict_unknown_input_never_softens():
    """An unknown verdict on one side must NOT be allowed to soften the
    other side. The function returns the known input rather than fall
    through to a softer state."""
    from app.services.release_council_service import _worse_verdict
    assert _worse_verdict("UNKNOWN", "NO_GO") == "NO_GO"
    assert _worse_verdict("NO_GO", "UNKNOWN") == "NO_GO"
    assert _worse_verdict("CONDITIONAL", "UNKNOWN") == "CONDITIONAL"


# ── _apply_band_floor — async, resolves a policy ──────────────────────────


POLICY_ID = "11111111-2222-3333-4444-555555555555"
POLICY_VERSION = 7


def _policy_first_result(rules: dict | None):
    """Match the .first() shape used by ``_resolve_active_policy_for_project``.

    The resolver selects ``(rules, id, version)`` — not just the rules — so the
    band floor can report WHICH policy produced the band. A single-column row
    here raises IndexError rather than failing an assertion, so keep this in
    step with the query.
    """
    res = MagicMock()
    res.first = MagicMock(
        return_value=(rules, POLICY_ID, POLICY_VERSION) if rules is not None else None
    )
    return res


def _critical_count_result(n: int):
    """Match the .scalar() shape used by count_open_critical_defects."""
    res = MagicMock()
    res.scalar = MagicMock(return_value=n)
    return res


@pytest.mark.asyncio
async def test_band_floor_no_project_returns_input_unchanged():
    """Synth path with a run that has no project_id — band-floor must be
    a no-op so the legacy verdict is preserved."""
    from app.services.release_council_service import _apply_band_floor
    db = AsyncMock()
    rec, band, downgrades, band_policy = await _apply_band_floor(
        db, project_id=None, recommendation="GO", pass_rate=99.0,
    )
    assert rec == "GO"
    assert band is None
    assert downgrades == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_band_floor_no_policy_returns_input_unchanged():
    """Project exists but no active ReleaseGatePolicy row — still a no-op,
    and no defect count is issued (the count only happens once a policy
    resolves)."""
    from app.services.release_council_service import _apply_band_floor
    db = AsyncMock()
    # Both lookups (project-scoped → system-default) return None.
    db.execute = AsyncMock(side_effect=[
        _policy_first_result(None),
        _policy_first_result(None),
    ])
    rec, band, downgrades, band_policy = await _apply_band_floor(
        db, project_id=uuid.uuid4(), recommendation="CONDITIONAL",
        pass_rate=92.0,
    )
    assert rec == "CONDITIONAL"
    assert band is None
    assert downgrades == []
    # Policy resolution issued 2 queries; no third (the defect count) fired.
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_band_floor_downgrades_when_band_is_stricter():
    """Composite says GO (pass_rate 88 is below project's NO_GO threshold
    but other dimensions saved it) but the band classifier says NO_GO
    because pass_rate < orange_min. Fail-closed must downgrade to NO_GO."""
    from app.services.release_council_service import _apply_band_floor
    rules = {
        "pass_rate_bands": {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0},
        "hard_caps": {"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    }
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _policy_first_result(rules), _critical_count_result(0),
    ])
    rec, band, downgrades, band_policy = await _apply_band_floor(
        db, project_id=uuid.uuid4(), recommendation="GO",
        pass_rate=88.0,
    )
    assert band == "red"
    assert rec == "NO_GO"
    assert downgrades == []


@pytest.mark.asyncio
async def test_band_floor_never_softens_composite():
    """Composite says NO_GO but pass_rate is excellent (99.5%, band=green).
    Bands must NEVER soften — the composite's NO_GO stands.

    This is the fail-closed invariant: deep investigation found something
    worth blocking (clusters, regressions, defect signals) that a simple
    pass-rate band can't see. Bands can only block, never unblock."""
    from app.services.release_council_service import _apply_band_floor
    rules = {
        "pass_rate_bands": {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0},
        "hard_caps": {"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    }
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _policy_first_result(rules), _critical_count_result(0),
    ])
    rec, band, downgrades, band_policy = await _apply_band_floor(
        db, project_id=uuid.uuid4(), recommendation="NO_GO",
        pass_rate=99.5,
    )
    # Band itself is green, but the final recommendation MUST stay NO_GO.
    assert band == "green"
    assert rec == "NO_GO"


@pytest.mark.asyncio
async def test_band_floor_does_not_soften_conditional_go_in_green_band():
    """Regression for the verdict-vocabulary bug: a CONDITIONAL_GO composite
    (the real value score_to_recommendation / the agent produce) in a green
    band must STAY CONDITIONAL_GO, not be softened to GO. Previously
    CONDITIONAL_GO wasn't in _VERDICT_RANK, so _worse_verdict returned the
    band's GO."""
    from app.services.release_council_service import _apply_band_floor
    rules = {
        "pass_rate_bands": {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0},
        "hard_caps": {"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    }
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _policy_first_result(rules), _critical_count_result(0),
    ])
    rec, band, downgrades, band_policy = await _apply_band_floor(
        db, project_id=uuid.uuid4(), recommendation="CONDITIONAL_GO",
        pass_rate=99.5,
    )
    assert band == "green"
    assert rec == "CONDITIONAL_GO"  # NOT softened to GO


@pytest.mark.asyncio
async def test_band_floor_p0_cap_forces_no_go():
    """An open CRITICAL defect over the hard cap forces NO_GO — even when the
    bare pass-rate would have been green. Combines two fixes: the count comes
    from count_open_critical_defects (B1 — CRITICAL severity, not the dead
    ``severity == "P0"`` filter), and a tripped hard cap is a hard blocker that
    forces NO_GO rather than a one-band nudge (B4)."""
    from app.services.release_council_service import _apply_band_floor
    rules = {
        "pass_rate_bands": {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0},
        "hard_caps": {"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    }
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _policy_first_result(rules), _critical_count_result(2),
    ])
    rec, band, downgrades, band_policy = await _apply_band_floor(
        db, project_id=uuid.uuid4(), recommendation="GO",
        pass_rate=99.5,
    )
    # Hard cap breached → NO_GO / red, overriding the green pass-rate band.
    assert band == "red"
    assert rec == "NO_GO"
    assert any("p0_defects" in d for d in downgrades)


@pytest.mark.asyncio
async def test_band_floor_returns_downgrade_audit_trail():
    """The downgrade list must include human-readable cap identifiers so
    the UI can show "downgraded by P0 defect cap" rather than a numeric
    delta. Empty list when no caps fired."""
    from app.services.release_council_service import _apply_band_floor
    rules = {
        "pass_rate_bands": {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0},
        "hard_caps": {"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0},
    }
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _policy_first_result(rules), _critical_count_result(5),
    ])
    _, _, downgrades, _ = await _apply_band_floor(
        db, project_id=uuid.uuid4(), recommendation="GO",
        pass_rate=99.5,
    )
    # Exactly one cap fired (P0). Format: "p0_defects:5>0".
    assert len(downgrades) == 1
    assert downgrades[0].startswith("p0_defects:")
