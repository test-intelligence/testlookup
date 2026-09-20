"""A policy that changes the verdict must be named in the response.

TL-2026-09-19-01-008, measured live. Publishing a policy for a project moved a
run at 90.91% from ``GO`` to ``CONDITIONAL_GO`` with ``release_readiness_band:
"orange"`` — and the response still reported ``policy_level: "hardcoded"`` with
a null ``policy_id``.

Those three fields are not lying: they describe the STORED ``ReleaseDecision``
as the release-risk agent wrote it, and it genuinely used hardcoded thresholds.
The band floor runs later, at read time, against whatever policy is active then.
But the field a consumer gates on is ``recommendation``, and that value was a
blend — the stored verdict, downgraded by a policy the response did not name.
"which policy produced this CONDITIONAL_GO" had no answer.

So ``band_policy_*`` reports the read-time policy alongside the stored one.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

BANDS = {"orange_min": 90.0, "yellow_min": 95.0, "green_min": 99.0}
CAPS = {"max_p0_defects": 0, "max_flaky_count": 0, "max_new_failures_24h": 0}
POLICY_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _policy_row(rules, *, policy_id=POLICY_ID, version=3, system=False):
    res = MagicMock()
    # The resolver selects (rules, id, version).
    res.first = MagicMock(return_value=(rules, policy_id, version) if rules else None)
    return res


def _count(n: int):
    res = MagicMock()
    res.scalar = MagicMock(return_value=n)
    return res


class TestTheBandPolicyIsIdentified:
    @pytest.mark.asyncio
    async def test_a_policy_that_downgrades_the_verdict_is_named(self):
        """The exact live scenario: 90.91% -> orange -> GO becomes CONDITIONAL."""
        from app.services.release_council_service import _apply_band_floor

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _policy_row({"pass_rate_bands": BANDS, "hard_caps": CAPS}),
            _count(0),
        ])
        rec, band, _downgrades, band_policy = await _apply_band_floor(
            db, project_id=uuid.uuid4(), recommendation="GO", pass_rate=90.91,
        )

        assert band == "orange"
        assert rec != "GO", "the policy changed the ship decision"
        assert band_policy is not None, (
            "a policy moved the verdict and the response cannot say which one"
        )
        assert band_policy[0] == POLICY_ID
        assert band_policy[1] == 3
        assert band_policy[2] == "project"

    @pytest.mark.asyncio
    async def test_no_policy_means_no_band_and_no_identity(self):
        from app.services.release_council_service import _apply_band_floor

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_policy_row(None), _policy_row(None)])
        rec, band, downgrades, band_policy = await _apply_band_floor(
            db, project_id=uuid.uuid4(), recommendation="GO", pass_rate=90.91,
        )
        assert (rec, band, downgrades, band_policy) == ("GO", None, [], None)

    @pytest.mark.asyncio
    async def test_a_system_default_policy_reports_system(self):
        # Precedence is project-active then system-default; the level must say
        # which one answered, not merely that something did.
        from app.services.release_council_service import _apply_band_floor

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _policy_row(None),                                     # no project policy
            _policy_row({"pass_rate_bands": BANDS, "hard_caps": CAPS}),  # system default
            _count(0),
        ])
        _rec, band, _dg, band_policy = await _apply_band_floor(
            db, project_id=uuid.uuid4(), recommendation="GO", pass_rate=90.91,
        )
        assert band == "orange"
        assert band_policy is not None and band_policy[2] == "system"


class TestOneImplementationOfThePrecedenceRule:
    def test_the_document_resolver_delegates_rather_than_duplicating(self):
        # Two lookups implementing "project-active then system-default" would
        # be free to drift. _resolve_policy_for_project must be a thin wrapper.
        import inspect

        from app.services import metrics_service

        src = inspect.getsource(metrics_service._resolve_policy_for_project)
        assert "_resolve_active_policy_for_project" in src
        assert "select(" not in src, (
            "the document resolver grew its own query; the precedence rule now "
            "exists twice and can drift"
        )


class TestTheResponseCarriesBothProvenances:
    def test_the_schema_separates_stored_from_read_time(self):
        from app.models.schemas import ReleaseCouncilResponse

        fields = ReleaseCouncilResponse.model_fields
        for name in ("policy_id", "policy_version", "policy_level"):
            assert name in fields
        for name in ("band_policy_id", "band_policy_version", "band_policy_level"):
            assert name in fields, f"{name} missing — the band's policy is unnamed"

    def test_band_policy_defaults_to_none(self):
        # An overridden decision skips the band floor entirely, so the response
        # must construct without these being supplied.
        from app.models.schemas import ReleaseCouncilResponse as _R

        defaults = {
            name: field.default
            for name, field in _R.model_fields.items()
            if field.is_required()
        }
        # Build with only the genuinely required fields; the point is that the
        # three band_policy_* fields are NOT among them.
        assert "band_policy_id" not in defaults
        r = _R.model_construct(run_id=str(uuid.uuid4()), recommendation="GO")
        assert r.band_policy_id is None
        assert r.band_policy_version is None
        assert r.band_policy_level is None
