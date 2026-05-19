"""Phase H carry-over backend test gaps for ``stream_service`` (the two
items listed in `docs/BACKLOG.md` § "Phase H follow-ups — Test gaps").

1. ``LiveSessionCreate.suite_name`` round-trips into
   ``TestRun.primary_suite_name`` via ``upsert_test_run``. The mapping
   has been in place since the May redesign but only end-to-end
   coverage existed; pin the contract at the service-function level so
   a future refactor of ``upsert_test_run`` can't silently drop it.

2. ``stream_service.close_session`` honours the project-scoped API-key
   binding — when ``bound_project_id`` doesn't match the session's
   project, the call returns 403 *before* mutating session state.
   Regression coverage for the May 2026 bug where X-API-Key callers
   were getting a spurious 401 because the auth happened at the route
   level instead of inside the service (CLAUDE.md pitfall #2).

These tests stub the DB session and Redis just deeply enough to reach
the assertions. The wider create/persist flow is covered elsewhere.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


# ── upsert_test_run: suite_name → TestRun.primary_suite_name ────────────────


@pytest.mark.asyncio
async def test_upsert_test_run_creates_run_with_suite_name_from_session():
    """First-time upsert (no existing TestRun) materialises a row with
    ``primary_suite_name`` lifted from the LiveSession + ``suite_names``
    seeded as a single-element list."""
    from app.services.stream_service import upsert_test_run

    session_id = uuid.uuid4()
    run_id = str(uuid.uuid4())
    project_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id=session_id,
        run_id=run_id,
        project_id=project_id,
        build_number="build-42",
        branch="main",
        commit_hash="deadbeef",
        total_tests=3,
        started_at=started,
        suite_name="Smoke Suite",
        release_name=None,
    )

    # No existing TestRun — first execute returns scalar_one_or_none() = None
    # so the create branch fires.
    no_run = MagicMock()
    no_run.scalar_one_or_none = MagicMock(return_value=None)
    db = MagicMock()
    db.execute = AsyncMock(return_value=no_run)
    added: list = []
    db.add = lambda obj: added.append(obj)

    await upsert_test_run(db, session, state={"passed": 2, "failed": 1, "total": 3})

    # One TestRun added, carrying the SDK-supplied suite name in BOTH the
    # singular ``primary_suite_name`` field AND the ``suite_names`` list
    # (the list is what aggregate-page filters pivot on).
    assert len(added) == 1
    run = added[0]
    assert run.primary_suite_name == "Smoke Suite"
    assert run.suite_names == ["Smoke Suite"]


@pytest.mark.asyncio
async def test_upsert_test_run_does_not_overwrite_existing_primary_suite_name():
    """When the TestRun already has a ``primary_suite_name`` (per-event
    aggregation got there first), a later upsert with a different
    SDK-supplied suite must NOT clobber it. The contract is documented
    inline as "Only overwrite suite when SDK provided one — preserve any
    value already populated by per-event aggregation."
    """
    from app.services.stream_service import upsert_test_run

    session_id = uuid.uuid4()
    run_id = str(uuid.uuid4())
    project_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id=session_id,
        run_id=run_id,
        project_id=project_id,
        build_number="build-42",
        branch="main",
        commit_hash="deadbeef",
        total_tests=3,
        started_at=started,
        suite_name="SDK Supplied",
        release_name=None,
    )

    existing_run = SimpleNamespace(
        primary_suite_name="Already Aggregated",
        suite_names=["Already Aggregated"],
        status=None,
        total_tests=None,
        passed_tests=None,
        failed_tests=None,
        skipped_tests=None,
        broken_tests=None,
        pass_rate=None,
        end_time=None,
    )
    has_run = MagicMock()
    has_run.scalar_one_or_none = MagicMock(return_value=existing_run)
    db = MagicMock()
    db.execute = AsyncMock(return_value=has_run)
    db.add = MagicMock()

    await upsert_test_run(db, session, state={"passed": 2, "failed": 1, "total": 3})

    # The pre-existing aggregate value is preserved.
    assert existing_run.primary_suite_name == "Already Aggregated"
    assert existing_run.suite_names == ["Already Aggregated"]
    # And no new TestRun was inserted on top.
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_test_run_seeds_suite_when_existing_row_missing_it():
    """If the per-event aggregator hasn't filled in a suite yet (NULL),
    a later SDK-supplied value DOES seed the row — otherwise the
    "live-stream gap" returns and ``/suites`` shows nothing while
    ``/runs`` shows everything."""
    from app.services.stream_service import upsert_test_run

    session = SimpleNamespace(
        id=uuid.uuid4(),
        run_id=str(uuid.uuid4()),
        project_id=uuid.uuid4(),
        build_number="build-42",
        branch="main",
        commit_hash="deadbeef",
        total_tests=3,
        started_at=datetime.now(timezone.utc),
        suite_name="Late Arriver",
        release_name=None,
    )

    existing_run = SimpleNamespace(
        primary_suite_name=None,
        suite_names=None,
        status=None,
        total_tests=None,
        passed_tests=None,
        failed_tests=None,
        skipped_tests=None,
        broken_tests=None,
        pass_rate=None,
        end_time=None,
    )
    has_run = MagicMock()
    has_run.scalar_one_or_none = MagicMock(return_value=existing_run)
    db = MagicMock()
    db.execute = AsyncMock(return_value=has_run)
    db.add = MagicMock()

    await upsert_test_run(db, session, state={"passed": 2, "failed": 1, "total": 3})

    assert existing_run.primary_suite_name == "Late Arriver"
    assert existing_run.suite_names == ["Late Arriver"]


# ── close_session: X-API-Key project binding ─────────────────────────────────


@pytest.mark.asyncio
async def test_close_session_403s_when_api_key_bound_to_different_project():
    """A project-scoped API key (``bound_project_id`` set) targeting a
    session in a different project must hit 403 BEFORE any state
    mutation. Regression: the May 2026 fix moved this check from the
    route layer into the service so X-API-Key calls stop getting a
    spurious 401."""
    from app.services.stream_service import close_session

    session_id = str(uuid.uuid4())
    session_uuid = uuid.UUID(session_id)
    session_project = uuid.uuid4()
    bound_project = uuid.uuid4()  # different — must trigger 403
    assert session_project != bound_project

    session_row = SimpleNamespace(
        id=session_uuid,
        run_id=session_id,
        project_id=session_project,
        status="active",
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=session_row)
    # Any DB write would be a regression — the guard must short-circuit.
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await close_session(db, session_id, bound_project_id=bound_project)

    assert exc_info.value.status_code == 403
    # No mutation: status untouched, no writes, no Redis side effects.
    assert session_row.status == "active"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_session_skips_already_completed_for_matching_project(monkeypatch):
    """When the bound project matches AND the session is already
    ``completed``, the idempotency guard returns cleanly without touching
    anything else. This double-duty test pins (a) the bound-project gate
    does NOT raise on match, and (b) the idempotency guard works — all
    without driving the full Redis + Celery fall-through path that would
    otherwise need a broker.
    """
    from app.services.stream_service import close_session

    session_id = str(uuid.uuid4())
    session_uuid = uuid.UUID(session_id)
    project_id = uuid.uuid4()

    completed_session = SimpleNamespace(
        id=session_uuid,
        run_id=session_id,
        project_id=project_id,
        # ``status == "completed"`` short-circuits before any of the
        # downstream Redis / Celery work — keeps the test fast and
        # broker-free while still exercising the bound-project gate.
        status="completed",
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=completed_session)
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    # Matching bound project + completed status → returns None cleanly,
    # no exception. The bound-project gate did NOT fire (else we'd see a
    # 403); the idempotency guard short-circuited everything downstream.
    result = await close_session(
        db, session_id, bound_project_id=project_id,
    )
    assert result is None
    db.flush.assert_not_awaited()
    db.add.assert_not_called()
