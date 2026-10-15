"""W05 REL-01 / REL-02 against a real PostgreSQL: the release list, detail and
create/update payloads carry the CURRENT recorded gate verdict and the owner.

What only a real database can prove, and the unit tests next door cannot:

* the embedded ``gate`` is the CURRENT RELEASE-LEVEL decision only (history rows
  and phase rows ignored), a never-evaluated release reads ``null``, and a row
  whose ``blocking_reasons`` / ``run_ids`` hold the JSON scalar ``null`` (what
  SQLAlchemy's ``JSON`` stores for ``None``) does not raise;
* the list spends the SAME number of statements for 1 release as for 20, and
  resolves every owner on the page in ONE statement (it polls every 30 s);
* tenant scope is unchanged: another project's release never appears;
* an owner is validated against the release's project on create AND update
  (422, one text for every ineligible id), ``null`` clears it, and the change is
  a ``release.updated`` ledger row carrying NAMES;
* the create writes ``created_by_id`` and defaults the owner to an interactive
  human only;
* deleting the owner's user row leaves the release with no owner
  (``ON DELETE SET NULL``).

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head
(0211+); skips otherwise. Every test runs inside one outer transaction that is
rolled back (the routers' ``commit()`` releases a SAVEPOINT), so nothing is
left behind.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine):
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


def _as_jwt(user):
    from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind

    return _bind_credential_kind(user, CREDENTIAL_KIND_JWT)


def _as_api_key(user):
    from app.core.deps import CREDENTIAL_KIND_API_KEY, _bind_credential_kind

    return _bind_credential_kind(user, CREDENTIAL_KIND_API_KEY)


@pytest.fixture
async def world(db):
    """Project A: lead, priya, a service account, a synthetic QA lead and a
    deactivated user. Project B: outsider. Admin: member of neither."""
    from app.models.postgres import Project, ProjectMember, User, UserRole

    # Letters only. The token is part of each full name, and the ledger's
    # write-time redaction reads a run of digits as a phone number: a hex token
    # that happened to be all digits turned "Priya Nair 4388381508" into
    # "Priya Nair [REDACTED_PHONE]" in the owner diff, on an unlucky run.
    token = uuid.uuid4().hex[:10].translate(str.maketrans("0123456789", "ghijklmnop"))

    def user(name, role, *, active=True, service=False, synthetic=False, full=None):
        return User(
            id=uuid.uuid4(),
            email=f"{name}-{token}@example.test",
            username=f"{name}-{token}",
            full_name=full if full is not None else f"{name.title()} {token}",
            hashed_password="x",
            role=role,
            is_active=active,
            is_service_account=service,
            is_synthetic=synthetic,
        )

    people = {
        "lead": user("lead", UserRole.QA_LEAD),
        "priya": user("priya", UserRole.QA_ENGINEER, full=f"Priya Nair {token}"),
        "svc": user("svc", UserRole.QA_LEAD, service=True),
        "synth": user("synth", UserRole.QA_LEAD, synthetic=True),
        "gone": user("gone", UserRole.QA_ENGINEER, active=False),
        "outsider": user("outsider", UserRole.QA_LEAD),
        "admin": user("admin", UserRole.ADMIN),
    }
    a = Project(id=uuid.uuid4(), name=f"A {token}", slug=f"a-{token}")
    b = Project(id=uuid.uuid4(), name=f"B {token}", slug=f"b-{token}")
    db.add_all([*people.values(), a, b])
    await db.flush()
    for name in ("lead", "priya", "svc", "synth", "gone"):
        db.add(ProjectMember(id=uuid.uuid4(), project_id=a.id, user_id=people[name].id,
                             role=UserRole.QA_ENGINEER.value))
    db.add(ProjectMember(id=uuid.uuid4(), project_id=b.id, user_id=people["outsider"].id,
                         role=UserRole.QA_LEAD.value))
    await db.flush()
    return {**people, "a": a, "b": b, "token": token}


async def _release(db, project, name, *, owner=None):
    from app.models.postgres import Release

    rel = Release(id=uuid.uuid4(), project_id=project.id, name=name, status="in_progress",
                  owner_user_id=getattr(owner, "id", None))
    db.add(rel)
    await db.flush()
    return rel


async def _decision(db, release, verdict, *, current=True, phase=None, reasons=None,
                    run_ids=None, evidence=10, denominator=12, rollup=None):
    from app.models.postgres import ReleaseGateDecision

    row = ReleaseGateDecision(
        id=uuid.uuid4(), release_id=release.id, phase_id=getattr(phase, "id", None),
        is_current=current, verdict=verdict, denominator=denominator, evidence_count=evidence,
        run_ids=run_ids, status_rollup=rollup, blocking_reasons=reasons, ingestion_complete=True,
    )
    db.add(row)
    await db.flush()
    return row


async def _list(db, project_id=None, accessible=None):
    from app.services import release_service

    return await release_service.list_releases(
        db, str(project_id) if project_id else None, None, accessible_project_ids=accessible,
    )


def _by_id(body):
    return {item["id"]: item for item in body["items"]}


# ── REL-01: the embedded verdict ─────────────────────────────────────────────


async def test_the_list_embeds_the_current_release_level_decision_only(db, world) -> None:
    from app.models.postgres import ReleasePhase

    judged = await _release(db, world["a"], "2026.11")
    never = await _release(db, world["a"], "2026.12")
    legacy = await _release(db, world["a"], "2026.10")
    phase = ReleasePhase(id=uuid.uuid4(), release_id=judged.id, name="Soak", phase_type="qa_testing",
                         status="in_progress", order_index=0)
    db.add(phase)
    await db.flush()
    # History (superseded GO), the phase's own current verdict (GO), and the
    # release's current verdict (NO_GO): only the last may reach the card.
    await _decision(db, judged, "GO", current=False, reasons=[])
    await _decision(db, judged, "GO", phase=phase, reasons=[])
    await _decision(
        db, judged, "NO_GO", run_ids=[str(uuid.uuid4()) for _ in range(3)],
        reasons=["checkout-e2e::9f2c41d07ab3e815 is FAILED", "defect PROJ-1 is open"],
        rollup={"PASSED": 8, "FAILED": 2}, evidence=10, denominator=12,
    )
    # A legacy row: JSON ``null`` in both arrays and no rollup.
    await _decision(db, legacy, "NOT_EVALUATED", reasons=None, run_ids=None, evidence=0)

    items = _by_id(await _list(db, world["a"].id))

    gate = items[str(judged.id)]["gate"]
    assert gate["verdict"] == "NO_GO", "a history or phase row reached the card"
    assert gate["run_count"] == 3
    assert gate["blocking_total"] == 2
    assert gate["blocking_reasons"][1] == "defect PROJ-1 is open"
    assert gate["pass_rate"] == 80.0
    assert gate["evidence_count"] == 10 and gate["denominator"] == 12
    assert items[str(never.id)]["gate"] is None, "a never-evaluated release must read null"
    legacy_gate = items[str(legacy.id)]["gate"]
    assert legacy_gate["blocking_total"] == 0 and legacy_gate["run_count"] == 0
    assert legacy_gate["blocking_reasons"] == []
    assert legacy_gate["pass_rate"] is None


async def test_a_huge_decision_embeds_twenty_reasons_and_the_true_total(db, world) -> None:
    rel = await _release(db, world["a"], "2026.13")
    await _decision(db, rel, "NO_GO", reasons=[f"suite::{i:016x} is FAILED" for i in range(5000)])

    gate = _by_id(await _list(db, world["a"].id))[str(rel.id)]["gate"]

    assert len(gate["blocking_reasons"]) == 20
    assert gate["blocking_total"] == 5000


async def test_the_gate_route_names_tests_inside_the_releases_project_only(db, world) -> None:
    """``GET /releases/{id}/gate`` keeps the stored reasons and adds display
    strings beside them: a fingerprint is named only from THIS project's test
    rows (another tenant's name for it never leaks), anything that does not
    resolve passes through, and a verdict with no reason carries no extra key."""
    from datetime import datetime, timezone

    from app.models.postgres import TestCase, TestRun
    from app.routers import releases as rel_router

    tag, now = world["token"], datetime.now(timezone.utc)
    mine_run, foreign_run = uuid.uuid4(), uuid.uuid4()
    db.add_all([
        TestRun(id=mine_run, project_id=world["a"].id, build_number=f"b-{tag}", status="FAILED",
                total_tests=1, failed_tests=1, created_at=now, start_time=now),
        TestRun(id=foreign_run, project_id=world["b"].id, build_number=f"bf-{tag}", status="FAILED",
                total_tests=1, failed_tests=1, created_at=now, start_time=now),
    ])
    await db.flush()
    db.add_all([
        TestCase(test_run_id=mine_run, test_fingerprint=f"{tag}-pay", test_name="test_pay",
                 suite_name="checkout-e2e", status="FAILED"),
        TestCase(test_run_id=foreign_run, test_fingerprint=f"{tag}-ghost", test_name="foreign_secret",
                 suite_name="x", status="FAILED"),
    ])
    await db.flush()
    judged = await _release(db, world["a"], "2026.11")
    raw = [f"checkout-e2e::{tag}-pay is FAILED", f"checkout-e2e::{tag}-ghost is FAILED", "defect PROJ-1 is open"]
    await _decision(db, judged, "NO_GO", reasons=raw)

    gate = await rel_router.get_release_gate(release_id=str(judged.id), db=db, _=None)

    assert gate["blocking_reasons"] == raw, "the stored reasons are the record"
    assert gate["blocking_reasons_display"] == [
        "test_pay (checkout-e2e) is FAILED",
        f"checkout-e2e::{tag}-ghost is FAILED",
        "defect PROJ-1 is open",
    ]
    assert gate["blocking_total"] == 3

    clean = await _release(db, world["a"], "2026.12")
    await _decision(db, clean, "GO", reasons=[], rollup={"PASSED": 9}, evidence=9)
    passed = await rel_router.get_release_gate(release_id=str(clean.id), db=db, _=None)
    assert "blocking_reasons_display" not in passed


async def test_another_tenants_release_never_appears(db, world) -> None:
    mine = await _release(db, world["a"], "mine")
    theirs = await _release(db, world["b"], "theirs")
    await _decision(db, theirs, "GO", reasons=[])

    listed = _by_id(await _list(db, None, accessible={world["a"].id}))

    assert str(mine.id) in listed
    assert str(theirs.id) not in listed


# ── The list's cost does not grow with the page ──────────────────────────────


async def _count_statements(engine, coro):
    seen: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        result = await coro
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
    return result, seen


async def test_one_release_and_twenty_cost_the_same_statements(db, engine, world) -> None:
    from app.models.postgres import Project

    token = world["token"]
    one = Project(id=uuid.uuid4(), name=f"One {token}", slug=f"one-{token}")
    many = Project(id=uuid.uuid4(), name=f"Many {token}", slug=f"many-{token}")
    db.add_all([one, many])
    await db.flush()
    for project, n in ((one, 1), (many, 20)):
        for i in range(n):
            # Every release owned, every one judged with a reason that names a
            # test: the batch takes its most expensive path in both projects.
            rel = await _release(db, project, f"r{i}", owner=world["priya" if i % 2 else "lead"])
            await _decision(db, rel, "NO_GO", reasons=[f"suite::{i:016x} is FAILED"])

    small, small_sql = await _count_statements(engine, _list(db, one.id))
    large, large_sql = await _count_statements(engine, _list(db, many.id))

    assert len(small["items"]) == 1 and len(large["items"]) == 20
    assert len(small_sql) == len(large_sql), (small_sql, large_sql)
    owner_reads = [s for s in large_sql if "FROM users" in s]
    assert len(owner_reads) == 1, "the owners of a page must resolve in ONE statement"
    names = {item["owner"]["name"] for item in large["items"]}
    assert names == {world["lead"].full_name, world["priya"].full_name}


# ── REL-02: owners on read ───────────────────────────────────────────────────


async def test_a_robot_or_departed_owner_reads_as_no_owner(db, world) -> None:
    kept = await _release(db, world["a"], "kept", owner=world["priya"])
    robot = await _release(db, world["a"], "robot", owner=world["svc"])
    synthetic = await _release(db, world["a"], "synthetic", owner=world["synth"])
    departed = await _release(db, world["a"], "departed", owner=world["gone"])

    items = _by_id(await _list(db, world["a"].id))

    assert items[str(kept.id)]["owner"] == {"id": str(world["priya"].id), "name": world["priya"].full_name}
    for rel in (robot, synthetic, departed):
        assert items[str(rel.id)]["owner"] is None, rel.name
        # The column still holds the id: only the READING says "No owner".
        assert items[str(rel.id)]["owner_user_id"] is not None


async def test_the_detail_carries_the_gate_and_the_owner(db, world) -> None:
    from app.services import release_service

    rel = await _release(db, world["a"], "2026.11", owner=world["priya"])
    await _decision(db, rel, "GO", reasons=[], rollup={"PASSED": 9}, evidence=9)

    detail = await release_service.get_release_details(db, str(rel.id))

    assert detail["gate"]["verdict"] == "GO"
    assert detail["gate"]["pass_rate"] == 100.0
    assert detail["owner"]["name"] == world["priya"].full_name


async def test_deleting_the_owner_user_leaves_no_owner(db, world) -> None:
    from sqlalchemy import delete

    from app.models.postgres import Release, User, UserRole

    temp = User(id=uuid.uuid4(), email=f"temp-{world['token']}@example.test",
                username=f"temp-{world['token']}", hashed_password="x", role=UserRole.ADMIN)
    db.add(temp)
    await db.flush()
    rel = await _release(db, world["a"], "orphaned", owner=temp)
    rel_id, temp_id = rel.id, temp.id
    assert rel.owner_user_id == temp_id

    await db.execute(delete(User).where(User.id == temp_id))
    await db.flush()

    stored = (await db.execute(select(Release.owner_user_id).where(Release.id == rel_id))).scalar_one()
    assert stored is None


# ── REL-02: create ───────────────────────────────────────────────────────────


async def _create(db, actor, project, **body):
    from app.routers import releases as rel_router

    payload = rel_router.ReleaseIn(project_id=str(project.id), name=f"rel-{uuid.uuid4().hex[:6]}", **body)
    return await rel_router.create_release(body=payload, db=db, current_user=actor)


async def test_a_human_creator_becomes_the_owner_and_the_creator(db, world) -> None:
    from app.models.postgres import Release

    lead = _as_jwt(world["lead"])
    created = await _create(db, lead, world["a"])

    assert created["owner"] == {"id": str(lead.id), "name": lead.full_name}
    assert created["gate"] is None
    row = await db.get(Release, uuid.UUID(created["id"]))
    assert row.created_by_id == lead.id and row.owner_user_id == lead.id


@pytest.mark.parametrize("kind", ["api_key", "service_account"])
async def test_a_machine_creator_owns_nothing_but_is_still_recorded(db, world, kind) -> None:
    from app.models.postgres import Release

    actor = _as_api_key(world["lead"]) if kind == "api_key" else _as_jwt(world["svc"])
    created = await _create(db, actor, world["a"])

    assert created["owner"] is None
    row = await db.get(Release, uuid.UUID(created["id"]))
    assert row.owner_user_id is None
    assert row.created_by_id == actor.id, "created_by_id must be written whoever created it"


async def test_an_explicit_owner_and_an_explicit_no_owner_are_respected(db, world) -> None:
    lead = _as_jwt(world["lead"])
    chosen = await _create(db, lead, world["a"], owner_user_id=str(world["priya"].id))
    nobody = await _create(db, lead, world["a"], owner_user_id=None)

    assert chosen["owner"]["id"] == str(world["priya"].id)
    assert nobody["owner"] is None


def _ineligible(world):
    return {
        "another project's user": str(world["outsider"].id),
        "an inactive user": str(world["gone"].id),
        "a service account": str(world["svc"].id),
        "a synthetic account": str(world["synth"].id),
        "an unknown id": str(uuid.uuid4()),
        "a malformed id": "not-a-uuid",
    }


async def test_every_ineligible_owner_is_one_422_on_create_and_update(db, world) -> None:
    from app.routers import releases as rel_router
    from app.services.release_owner_service import OWNER_INELIGIBLE

    lead = _as_jwt(world["lead"])
    rel = await _release(db, world["a"], "target", owner=world["priya"])
    for case, owner_id in _ineligible(world).items():
        with pytest.raises(HTTPException) as on_create:
            await _create(db, lead, world["a"], owner_user_id=owner_id)
        assert (on_create.value.status_code, on_create.value.detail) == (422, OWNER_INELIGIBLE), case

        with pytest.raises(HTTPException) as on_update:
            await rel_router.update_release(
                release_id=str(rel.id), body=rel_router.ReleaseUpdate(owner_user_id=owner_id),
                db=db, current_user=lead, __=None,
            )
        assert (on_update.value.status_code, on_update.value.detail) == (422, OWNER_INELIGIBLE), case
    assert rel.owner_user_id == world["priya"].id, "a refused owner changed the row"


async def test_an_admin_who_is_not_a_member_may_own_a_release(db, world) -> None:
    created = await _create(db, _as_jwt(world["lead"]), world["a"], owner_user_id=str(world["admin"].id))

    assert created["owner"]["id"] == str(world["admin"].id)


# ── REL-02: update, and the ledger row ───────────────────────────────────────


async def _owner_events(db, release_id):
    from app.models.postgres import ProjectActivityEvent

    return list((await db.execute(
        select(ProjectActivityEvent).where(
            ProjectActivityEvent.release_id == release_id,
            ProjectActivityEvent.event_type == "release.updated",
        )
    )).scalars().all())


async def test_setting_clearing_and_resending_the_owner(db, world) -> None:
    from app.routers import releases as rel_router

    lead = _as_jwt(world["lead"])
    rel = await _release(db, world["a"], "2026.11")

    async def put(**body):
        return await rel_router.update_release(
            release_id=str(rel.id), body=rel_router.ReleaseUpdate(**body),
            db=db, current_user=lead, __=None,
        )

    await put(owner_user_id=str(world["priya"].id))
    (set_row,) = await _owner_events(db, rel.id)
    assert set_row.diff == {
        "before": {"owner": "No owner"},
        "after": {"owner": world["priya"].full_name},
        "changed_fields": ["owner"],
    }
    assert set_row.summary.endswith("changed: owner")

    # Re-sending the current owner is not a change.
    await put(owner_user_id=str(world["priya"].id))
    assert len(await _owner_events(db, rel.id)) == 1

    # ``null`` clears (``exclude_none`` alone could never carry it).
    cleared = await put(owner_user_id=None)
    assert cleared["owner_user_id"] is None
    rows = await _owner_events(db, rel.id)
    assert len(rows) == 2
    clear_row = next(r for r in rows if r.id != set_row.id)
    assert clear_row.diff["changed_fields"] == ["owner"]
    assert clear_row.diff["before"] == {"owner": world["priya"].full_name}
    assert clear_row.diff["after"] == {"owner": "No owner"}
    assert str(world["priya"].id) not in str(clear_row.diff), "the ledger carries names, never ids"


async def test_an_update_that_omits_the_owner_keeps_it(db, world) -> None:
    from app.routers import releases as rel_router

    rel = await _release(db, world["a"], "2026.11", owner=world["priya"])
    await rel_router.update_release(
        release_id=str(rel.id), body=rel_router.ReleaseUpdate(description="notes"),
        db=db, current_user=_as_jwt(world["lead"]), __=None,
    )
    assert rel.owner_user_id == world["priya"].id
