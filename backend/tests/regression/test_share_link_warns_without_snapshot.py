"""Minting a share link must say when the link will 404 on arrival.

UAT-004. ``POST /api/v1/reports/runs/{run_id}/share`` returned **201** with a
valid-looking ``share_url`` for a run with no intelligence snapshot. Following
that URL returns 404 — *"No intelligence snapshot found for run … Trigger deep
investigation first."* — so a user could copy the link, send it to a
stakeholder, and the recipient hit a dead page with nothing having been said at
creation time.

**Why a warning and not a 409.** The link *self-heals*: once deep investigation
runs, the same token resolves. That is the measured property that got UAT-004
scored MINOR rather than MAJOR, and refusing to create the link would break the
legitimate order of operations — mint the link, kick off the investigation,
send it once it lands. So creation still returns 201; it just stops being
silent.

``snapshot_ready`` is ``Optional[bool]`` and ``None`` means **not evaluated**,
not "ready". The list endpoint does not run the check, and defaulting it to
``True`` there would publish a value nobody measured — the same dishonesty the
confidence-gate work exists to remove.

The precondition checked at creation is exactly the one
``report_composition_service.compose_report`` applies to the recipient: a
snapshot row AND a non-empty payload. Anything narrower would warn on links
that work, or stay silent on links that do not — so both halves are pinned
below.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import reports  # noqa: E402

pytestmark = pytest.mark.regression

RUN_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Answers the two SELECTs the endpoint issues, keyed on the table the
    statement actually names rather than on call order — order-keyed fakes
    silently answer the wrong question the moment a query is added."""

    def __init__(self, project_id, snapshot_payload):
        self._project_id = project_id
        self._snapshot_payload = snapshot_payload
        self.saw_snapshot_query = False

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "run_intelligence_snapshots" in sql:
            self.saw_snapshot_query = True
            return _Scalar(self._snapshot_payload)
        return _Scalar(self._project_id)

    def add(self, _obj):
        return None

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


class _Link:
    id = uuid.uuid4()
    report_layout = "executive"
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    created_by_name = "QA Lead"
    access_count = 0
    is_revoked = False
    created_at = datetime.now(timezone.utc)


class _Created:
    link = _Link()
    raw_token = "tok-abc123"


async def _call(monkeypatch, *, snapshot_payload):
    async def _create_share_link(**_kw):
        return _Created()

    monkeypatch.setattr(
        "app.services.share_link_service.create_share_link", _create_share_link
    )
    monkeypatch.setattr(reports, "_stage_audit", lambda *a, **k: None)

    db = _FakeSession(PROJECT_ID, snapshot_payload)
    resp = await reports.create_share_link_endpoint(
        run_id=RUN_ID,
        body=reports.CreateShareLinkRequest(layout="executive", expiry_days=7),
        db=db,
        current_user=object(),
    )
    return resp, db


@pytest.mark.asyncio
async def test_a_link_for_a_run_without_a_snapshot_carries_a_warning(monkeypatch):
    """The measured bug: 201 and a valid-looking URL, said nothing."""
    resp, db = await _call(monkeypatch, snapshot_payload=None)

    assert db.saw_snapshot_query, "creation never checked whether a snapshot exists"
    assert resp.snapshot_ready is False
    assert resp.warning, (
        "a share link was minted for a run with no intelligence snapshot and "
        "the response said nothing — the recipient gets a 404"
    )
    assert "deep investigation" in resp.warning.lower(), (
        "the warning must name the remedy, not just report a problem"
    )


@pytest.mark.asyncio
async def test_an_empty_payload_counts_as_not_ready(monkeypatch):
    """``compose_report`` rejects ``not snapshot.payload``, so a row with an
    empty payload 404s exactly like a missing row. Checking only for the row's
    existence would stay silent on a link that does not work."""
    for empty in ({}, [], ""):
        resp, _ = await _call(monkeypatch, snapshot_payload=empty)
        assert resp.snapshot_ready is False, f"empty payload {empty!r} read as ready"
        assert resp.warning


@pytest.mark.asyncio
async def test_a_link_for_a_run_with_a_snapshot_is_silent(monkeypatch):
    """The warning must not cry wolf — most links are fine."""
    resp, _ = await _call(monkeypatch, snapshot_payload={"summary": "..."})

    assert resp.snapshot_ready is True
    assert resp.warning is None


@pytest.mark.asyncio
async def test_creation_still_succeeds_and_returns_a_usable_link(monkeypatch):
    """A warning, NOT a refusal. The link self-heals once investigation runs,
    so blocking creation would break minting a link ahead of the run."""
    resp, _ = await _call(monkeypatch, snapshot_payload=None)

    assert resp.token == "tok-abc123"
    assert resp.share_url and resp.share_url.endswith("tok-abc123")
    assert resp.id == str(_Link.id)


def test_not_evaluated_is_distinguishable_from_ready():
    """``None`` must remain the default, so a response that never ran the
    check (the list endpoint) cannot be read as 'ready'."""
    field = reports.ShareLinkResponse.model_fields["snapshot_ready"]
    assert field.default is None, (
        "snapshot_ready defaults to something other than None — a list "
        "response would then claim a readiness nobody measured"
    )
