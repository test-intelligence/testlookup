"""W05 REL-10: a phase-gate override or skip reaches the activity ledger, with its reason.

Before W05 ``_enforce_phase_gate`` recorded an override / skip in
``access_audit_logs`` only. ``/activity`` and the release page's "Recent
activity" read the activity ledger (``project_activity_events``), where
``release.phase_gate_overridden`` / ``release.phase_skipped`` were registered and
emitted by nobody, so the decision was recorded and still invisible to the
people it concerned. And both were ``attempt`` mode: an emitter would have
opened a SECOND database session inside the request.

What these pin:

1. a NO_GO phase completed with ``gate_override_reason`` stages ONE access-audit
   row (fields unchanged) and ONE ledger row whose summary ends with the reason
   and whose context carries the verdict; a skip with ``skip_reason`` the same
   with ``release.phase_skipped``; a GO phase and the flag-off path stage
   nothing and spend no extra statement;
2. a refused 409 stages nothing (it never reached the audit);
3. both rows are ``outcome`` mode: no second session is opened;
4. against a real PostgreSQL, a request that rolls back leaves no ledger row
   (and one that commits leaves exactly one).
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.models.postgres import AccessAuditLog, ProjectActivityEvent  # noqa: E402
from app.routers.releases import PhaseUpdate  # noqa: E402
from app.services import release_phase_gate_service  # noqa: E402
from app.services import release_service as svc  # noqa: E402

RELEASE = uuid.uuid4()
PROJECT = uuid.uuid4()


def _audits(db):
    return [row for row in db.added if isinstance(row, AccessAuditLog)]


#: W21 ACT-01: every real phase change also stages a ``release.phase_updated``
#: row (and ``release.phase_advanced`` into in_progress). These tests are about
#: the GATE's rows, so the CRUD rows are set aside here and asserted in
#: ``tests/integration/test_activity_ledger_slice_a_postgres.py``.
_PHASE_CRUD_EVENTS = {"release.phase_updated", "release.phase_advanced"}


def _gate_rows(db):
    return [
        row for row in db.added
        if not (isinstance(row, ProjectActivityEvent) and row.event_type in _PHASE_CRUD_EVENTS)
    ]


def _ledger(db):
    return [row for row in _gate_rows(db) if isinstance(row, ProjectActivityEvent)]


class _Phase:
    def __init__(self, name="Regression", status="in_progress"):
        self.id = uuid.uuid4()
        self.name = name
        self.status = status


class _Session:
    """The project lookup, the all-done COUNT and ``db.get(Release)``; counts
    every statement and records every staged row."""

    def __init__(self):
        self.added: list = []
        self.executed = 0
        self.gets = 0

    async def execute(self, stmt=None, *a, **kw):
        self.executed += 1

        class _R:
            def scalar(self_inner):
                return 0  # the all-done COUNT: nothing left incomplete

            def scalar_one_or_none(self_inner):
                return PROJECT  # ``_project_of_release``

        return _R()

    async def get(self, model, pk):
        self.gets += 1
        return SimpleNamespace(id=pk, name="2026.11", project_id=PROJECT)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="anand", full_name="Anand Goud")


@pytest.fixture
def gate(monkeypatch):
    """Enforcement on, a scripted phase verdict, and a fixed phase row."""
    state = SimpleNamespace(enabled=True, verdict="NO_GO",
                            reasons=["checkout-e2e::test_pay is FAILED"], phase=_Phase())

    async def _flag(**_kw):
        return state.enabled

    async def _evaluate(db, release_id, phase_id, record=True, created_by_id=None):
        assert record is False, "enforcement must preview the phase gate, never record it"
        return {"verdict": state.verdict, "blocking_reasons": list(state.reasons)}

    async def _get_phase(db, release_id, phase_id):
        return state.phase

    monkeypatch.setattr(svc, "_enforcement_enabled", _flag)
    monkeypatch.setattr(release_phase_gate_service, "evaluate_phase", _evaluate)
    monkeypatch.setattr(svc, "get_phase_or_404", _get_phase)
    return state


@pytest.fixture
def no_second_session(monkeypatch):
    """Any attempt-mode ledger write opens ``AsyncSessionLocal``; record it.

    The attempt writer swallows its own failures (a ledger outage must never
    fail the mutation), so raising alone would prove nothing: the list is what
    the tests read.
    """
    opened: list[str] = []

    def _opened(*_a, **_kw):
        opened.append("AsyncSessionLocal")
        raise AssertionError("a second database session was opened inside the request")

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _opened, raising=False)
    return opened


async def _update(db, phase, actor, **body):
    return await svc.update_phase(db, str(RELEASE), str(phase.id), PhaseUpdate(**body), actor=actor)


# ── 1. What an override and a skip stage ─────────────────────────────────────


@pytest.mark.asyncio
async def test_an_override_stages_one_audit_row_and_one_ledger_row_with_the_reason(gate, no_second_session):
    db, actor = _Session(), _actor()

    phase, _ = await _update(db, gate.phase, actor, status="completed",
                             gate_override_reason="  Customer waiver CAB-142  ")

    assert phase.status == "completed"
    # The access-audit row is exactly what it was before W05.
    (audit,) = _audits(db)
    assert audit.action == "release.phase_gate_overridden"
    assert audit.actor_user_id == actor.id and audit.project_id == PROJECT
    assert audit.before_value == {"phase_id": str(gate.phase.id), "status": "in_progress"}
    assert audit.after_value == {
        "verdict": "NO_GO",
        "blocking_reasons": ["checkout-e2e::test_pay is FAILED"],
        "reason": "Customer waiver CAB-142",
    }
    # And the ledger row the feed reads, in the contract's words.
    (event,) = _ledger(db)
    assert event.event_type == "release.phase_gate_overridden"
    assert event.summary == "Phase gate Regression overridden on release 2026.11: Customer waiver CAB-142"
    assert event.context == {"phase": "Regression", "reason": "Customer waiver CAB-142", "verdict": "NO_GO"}
    assert event.category == "release"
    assert (event.project_id, event.release_id, event.entity_id) == (PROJECT, RELEASE, str(RELEASE))
    assert event.entity_label == "2026.11"
    assert (event.actor_type, event.actor_id, event.actor_name) == ("user", actor.id, "Anand Goud")
    assert no_second_session == []


@pytest.mark.asyncio
async def test_a_skip_with_a_reason_stages_a_phase_skipped_ledger_row(gate, no_second_session):
    db, actor = _Session(), _actor()
    gate.phase = _Phase(name="Soak")

    phase, _ = await _update(db, gate.phase, actor, status="skipped",
                             skip_reason="vendor environment down")

    assert phase.status == "skipped"
    (audit,) = _audits(db)
    assert audit.action == "release.phase_skipped"
    assert audit.after_value == {"reason": "vendor environment down"}
    (event,) = _ledger(db)
    assert event.event_type == "release.phase_skipped"
    assert event.summary == "Phase Soak skipped on release 2026.11: vendor environment down"
    assert event.context == {"phase": "Soak", "reason": "vendor environment down"}
    assert event.release_id == RELEASE
    assert no_second_session == []


@pytest.mark.asyncio
async def test_a_go_phase_stages_nothing_and_reads_no_release(gate):
    gate.verdict = "GO"
    db = _Session()

    phase, _ = await _update(db, gate.phase, _actor(), status="completed")

    assert phase.status == "completed"
    assert _gate_rows(db) == []
    # W21 ACT-01: the one read is the phase_updated row's ``db.get(Release)``
    # (an identity-map hit in a real request), never a gate-event read.
    assert db.gets == 1
    # The project lookup and the all-done COUNT, nothing more.
    assert db.executed == 2


@pytest.mark.asyncio
async def test_with_enforcement_off_nothing_is_staged_and_no_statement_is_added(gate):
    gate.enabled = False
    db = _Session()

    phase, _ = await _update(db, gate.phase, _actor(), status="skipped",
                             skip_reason="vendor environment down")

    assert phase.status == "skipped"
    assert _gate_rows(db) == []
    assert db.gets == 1  # W21 ACT-01: the phase_updated row's label, as above
    assert db.executed == 1, "the flag-off path must spend only the all-done COUNT"


@pytest.mark.asyncio
async def test_an_internal_caller_with_no_actor_records_no_ledger_row(gate):
    """No actor = nobody to attribute it to; the access-audit row (actor
    "system") is still written, as it always was."""
    db = _Session()

    await _update(db, gate.phase, None, status="completed", gate_override_reason="automation")

    assert len(_audits(db)) == 1
    assert _ledger(db) == []


# ── 2. A refusal stages nothing ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refused_override_stages_no_row(gate):
    db = _Session()

    with pytest.raises(HTTPException) as exc:
        await _update(db, gate.phase, _actor(), status="completed")

    assert exc.value.status_code == 409
    assert _gate_rows(db) == [], "a refused completion reached the audit or the ledger"
    assert gate.phase.status == "in_progress"


@pytest.mark.asyncio
async def test_a_refused_skip_stages_no_row(gate):
    db = _Session()

    with pytest.raises(HTTPException) as exc:
        await _update(db, gate.phase, _actor(), status="skipped")

    assert exc.value.status_code == 409
    assert _gate_rows(db) == []


# ── 3. Outcome mode, through the router too ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_router_commits_the_ledger_row_with_the_phase_change(gate, no_second_session, monkeypatch):
    """The handler's ONE commit carries the phase change, the audit row and the
    ledger row together: the row is on the request session BEFORE the commit,
    and nothing else was opened."""
    from app.routers import releases as router_mod

    monkeypatch.setattr(router_mod, "serialize_model", lambda row: {"id": str(row.id)})
    db = _Session()
    staged_at_commit: list[int] = []

    async def _commit():
        staged_at_commit.append(len(_ledger(db)))

    async def _refresh(_obj):
        return None

    db.commit, db.refresh = _commit, _refresh

    out = await router_mod.update_phase(
        release_id=str(RELEASE), phase_id=str(gate.phase.id),
        body=PhaseUpdate(status="completed", gate_override_reason="Customer waiver CAB-142"),
        db=db, current_user=_actor(), __=None,
    )

    assert out["all_phases_completed"] is True
    assert staged_at_commit == [1], "the ledger row was not on the request session at commit"
    assert no_second_session == []


# ── 4. A rolled-back request leaves no ledger row (real PostgreSQL) ──────────


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def pg_db():
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                factory = async_sessionmaker(
                    bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
                )
                async with factory() as session:
                    yield session
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_rolled_back_request_leaves_no_ledger_row_and_a_committed_one_leaves_one(
    pg_db, monkeypatch, no_second_session
):
    from sqlalchemy import func, select

    from app.models.postgres import Project, Release, ReleasePhase, User, UserRole

    db = pg_db
    token = uuid.uuid4().hex[:10]
    user = User(id=uuid.uuid4(), email=f"lead-{token}@example.test", username=f"lead-{token}",
                full_name=f"Lead {token}", hashed_password="x", role=UserRole.QA_LEAD)
    project = Project(id=uuid.uuid4(), name=f"P {token}", slug=f"p-{token}")
    db.add_all([user, project])
    await db.flush()
    release = Release(id=uuid.uuid4(), project_id=project.id, name="2026.11", status="in_progress")
    db.add(release)
    await db.flush()
    phase = ReleasePhase(id=uuid.uuid4(), release_id=release.id, name="Regression",
                         phase_type="qa_testing", status="in_progress", order_index=0)
    db.add(phase)
    await db.commit()  # the fixture rows survive the rollback below (a SAVEPOINT release)
    # Plain values: a rollback expires every ORM object, and touching one then
    # would lazy-load outside the async context.
    release_id, phase_id = release.id, phase.id
    actor = SimpleNamespace(id=user.id, username=user.username, full_name=user.full_name)

    async def _flag(**_kw):
        return True

    async def _evaluate(_db, release_id, phase_id, record=True, created_by_id=None):
        return {"verdict": "NO_GO", "blocking_reasons": ["checkout-e2e::test_pay is FAILED"]}

    monkeypatch.setattr(svc, "_enforcement_enabled", _flag)
    monkeypatch.setattr(release_phase_gate_service, "evaluate_phase", _evaluate)

    async def _events() -> int:
        return (await db.execute(
            select(func.count(ProjectActivityEvent.id)).where(
                ProjectActivityEvent.release_id == release_id,
                ProjectActivityEvent.event_type == "release.phase_gate_overridden",
            )
        )).scalar_one()

    body = PhaseUpdate(status="completed", gate_override_reason="Customer waiver CAB-142")

    # The request fails after the gate let it through (anything between the
    # staging and the commit): get_db rolls the session back.
    await svc.update_phase(db, str(release_id), str(phase_id), body, actor=actor)
    await db.rollback()
    assert await _events() == 0, "an outcome row outlived the rollback of its request"

    # The same request, committed: exactly one row, rendered from the real data.
    await svc.update_phase(db, str(release_id), str(phase_id), body, actor=actor)
    await db.commit()
    assert await _events() == 1
    row = (await db.execute(
        select(ProjectActivityEvent).where(
            ProjectActivityEvent.release_id == release_id,
            # W21 ACT-01: the same change also leaves a release.phase_updated row.
            ProjectActivityEvent.event_type == "release.phase_gate_overridden",
        )
    )).scalar_one()
    assert row.summary == "Phase gate Regression overridden on release 2026.11: Customer waiver CAB-142"
    assert row.context["verdict"] == "NO_GO"
    assert row.actor_id == actor.id
    assert no_second_session == []
