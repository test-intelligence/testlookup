"""D-52 / D-53: a manual quarantine proposal is measured and names its proposer.

D-52: a manual proposal sends no flip figures, so the Proposed tab read "—" for
Flip rate and Window. ``propose_quarantine`` now measures them from the test's
stored results in the project (the recheck's ``fails / total``).

D-53: the proposer went only to the audit log. ``proposed_by_user_id`` (0260)
holds it; the router serialises ``proposed_by_name`` as full name, else username.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.postgres import FlakyQuarantineRequest, FlakyQuarantineStatus
from app.services import flaky_quarantine_service as svc


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    """Records the added row and every executed statement; answers the status counts."""

    def __init__(self, status_counts):
        self.status_counts = status_counts
        self.added: list = []
        self.statements: list = []
        # Savepoints opened, and whether each ended in a rollback (review of D-52:
        # on Postgres a failed read outside a savepoint aborts the proposal's INSERT).
        self.savepoints: list[str] = []

    def begin_nested(self):
        session = self

        class _Savepoint:
            async def __aenter__(self):
                session.savepoints.append("open")
                return self

            async def __aexit__(self, exc_type, *_a):
                session.savepoints[-1] = "rolled back" if exc_type else "released"
                return False

        return _Savepoint()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, statement, *_a, **_k):
        self.statements.append(statement)
        return _Result(self.status_counts)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        return None

    async def refresh(self, row):
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()

    async def rollback(self):
        return None


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(svc, "_feature_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "_find_live", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "_audit", AsyncMock())
    monkeypatch.setattr("app.services.cache_service.bump_analytics_epoch", AsyncMock())


def _session(monkeypatch, status_counts) -> _Session:
    session = _Session(status_counts)
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: session)
    return session


async def _propose(**overrides):
    kwargs = dict(
        project_id=uuid.uuid4(), test_fingerprint="fp-1", test_name="test_login", suite_name=None,
        detection_method="manual", flip_rate=None, flip_window_size=None,
        actor=SimpleNamespace(id=uuid.uuid4()),
    )
    kwargs.update(overrides)
    return await svc.propose_quarantine(**kwargs), kwargs


# ── D-52 ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_manual_proposal_is_measured_from_the_tests_stored_results(quiet, monkeypatch):
    # 10 stored results: 7 passed, 2 failed, 1 broken (skips are not evidence).
    session = _session(monkeypatch, [("PASSED", 7), ("FAILED", 2), ("BROKEN", 1), ("SKIPPED", 4)])

    row, kwargs = await _propose()

    assert row is not None
    assert row.flip_window_size == 10
    assert row.flip_rate == pytest.approx(0.3)
    assert (row.pass_count, row.fail_count) == (7, 3)
    # The count is scoped to the project (the fingerprint has no project salt).
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "test_runs.project_id" in sql and "test_cases.test_fingerprint" in sql


@pytest.mark.asyncio
async def test_a_manual_proposal_with_no_stored_result_stays_unmeasured(quiet, monkeypatch):
    _session(monkeypatch, [("SKIPPED", 3)])

    row, _ = await _propose()

    # Absence is not health: null, never 0 / 0 %.
    assert (row.flip_rate, row.flip_window_size) == (None, None)


@pytest.mark.asyncio
async def test_figures_the_caller_sent_are_kept_and_detector_rows_are_not_measured(quiet, monkeypatch):
    session = _session(monkeypatch, [("PASSED", 1), ("FAILED", 1)])

    row, _ = await _propose(flip_rate=0.42, flip_window_size=20)
    assert (row.flip_rate, row.flip_window_size) == (0.42, 20)

    row, _ = await _propose(detection_method="pass_fail_ratio", flip_rate=None, flip_window_size=None, actor=None)
    assert (row.flip_rate, row.flip_window_size) == (None, None)
    assert session.statements == []


@pytest.mark.asyncio
async def test_a_failed_measurement_still_writes_the_proposal(quiet, monkeypatch):
    session = _session(monkeypatch, [])

    async def boom(*_a, **_k):
        raise RuntimeError("db hiccup")

    session.execute = boom

    row, _ = await _propose()

    assert row is not None and row.flip_rate is None
    # The failed read was confined to a savepoint, so the INSERT can still commit.
    assert session.savepoints == ["rolled back"]


@pytest.mark.asyncio
async def test_fewer_than_ten_results_leave_a_manual_proposal_unmeasured(quiet, monkeypatch):
    # Review of D-52: one failure in 30 days is not "flip rate 100 %, window 1".
    _session(monkeypatch, [("FAILED", 1)])
    row, _ = await _propose()
    assert (row.flip_rate, row.flip_window_size) == (None, None)

    _session(monkeypatch, [("PASSED", 6), ("FAILED", 3)])
    row, _ = await _propose()
    assert (row.flip_rate, row.flip_window_size) == (None, None)


# ── D-53 ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_manual_proposal_stores_its_proposer(quiet, monkeypatch):
    _session(monkeypatch, [])

    row, kwargs = await _propose()

    assert row.proposed_by_user_id == kwargs["actor"].id


@pytest.mark.asyncio
async def test_a_detected_proposal_has_no_proposer(quiet, monkeypatch):
    _session(monkeypatch, [])

    row, _ = await _propose(detection_method="pass_fail_ratio", flip_rate=0.5, flip_window_size=10, actor=None)

    assert row.proposed_by_user_id is None


@pytest.mark.asyncio
async def test_a_person_proposing_a_detected_row_becomes_its_proposer(quiet, monkeypatch):
    existing = FlakyQuarantineRequest(
        id=uuid.uuid4(), project_id=uuid.uuid4(), test_fingerprint="fp-1",
        status=FlakyQuarantineStatus.DETECTED.value, detection_method="pass_fail_ratio",
        flip_rate=0.4, flip_window_size=15, quarantine_duration_days=14,
    )
    monkeypatch.setattr(svc, "_find_live", AsyncMock(return_value=existing))
    session = _session(monkeypatch, [("PASSED", 1)])
    actor = SimpleNamespace(id=uuid.uuid4())

    row, _ = await _propose(project_id=existing.project_id, actor=actor)

    assert row is existing
    assert row.proposed_by_user_id == actor.id
    # The detector's measurement is kept, not re-measured.
    assert (row.flip_rate, row.flip_window_size) == (0.4, 15)
    assert session.statements == []


@pytest.mark.asyncio
async def test_the_list_names_the_proposer_by_full_name_else_username():
    from app.routers.flaky_quarantine import _with_owner_names

    lead, bare = uuid.uuid4(), uuid.uuid4()

    def row(proposer):
        return FlakyQuarantineRequest(
            id=uuid.uuid4(), project_id=uuid.uuid4(), test_fingerprint="fp", status="PROPOSED",
            detection_method="manual", detected_at=svc.datetime.now(svc.timezone.utc),
            quarantine_duration_days=14, proposed_by_user_id=proposer,
            consecutive_passes=0, ready_to_promote=False,
            created_at=svc.datetime.now(svc.timezone.utc), updated_at=svc.datetime.now(svc.timezone.utc),
        )

    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result([
        SimpleNamespace(id=lead, username="qa.lead", full_name="Quinn Lead"),
        SimpleNamespace(id=bare, username="bare.user", full_name=None),
    ]))

    payloads = await _with_owner_names(db, [row(lead), row(bare), row(None)])

    assert [p.proposed_by_name for p in payloads] == ["Quinn Lead", "bare.user", None]
    assert payloads[0].proposed_by_user_id == lead


@pytest.mark.asyncio
async def test_the_list_names_the_owner_by_full_name_else_username():
    """D-101 sibling: the owner read as the username while the proposer read as the full name."""
    from app.routers.flaky_quarantine import _with_owner_names

    lead, bare = uuid.uuid4(), uuid.uuid4()

    def row(owner):
        return FlakyQuarantineRequest(
            id=uuid.uuid4(), project_id=uuid.uuid4(), test_fingerprint="fp", status="QUARANTINED",
            detection_method="manual", detected_at=svc.datetime.now(svc.timezone.utc),
            quarantine_duration_days=14, owner_user_id=owner,
            consecutive_passes=0, ready_to_promote=False,
            created_at=svc.datetime.now(svc.timezone.utc), updated_at=svc.datetime.now(svc.timezone.utc),
        )

    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result([
        SimpleNamespace(id=lead, username="qa.lead", full_name="Quinn Lead"),
        SimpleNamespace(id=bare, username="bare.user", full_name=None),
    ]))

    payloads = await _with_owner_names(db, [row(lead), row(bare), row(None)])

    assert [p.owner_name for p in payloads] == ["Quinn Lead", "bare.user", None]
