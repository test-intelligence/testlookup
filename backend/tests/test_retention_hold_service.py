"""W22a ACT-05 — the retention-hold service, without a database.

The Postgres half (the anti-joins against real rows, the lock conflict) lives
in ``tests/integration/test_retention_holds_postgres.py``. Here: validation and
its fixed sentences, the labels, the audit row, ``hold_state``, the list
filters, and the shape every reader and clause builder must keep — each
builder compiles to ``NOT EXISTS`` and never ``NOT IN``, each reader is ONE
statement read with ``.all()``, and the module never records activity.
"""
from __future__ import annotations

import pathlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.postgres import (
    CompliancePack,
    RetentionHold,
    SettingsAuditLog,
    TestRun,
)
from app.services import retention_hold_service as svc
from app.services.retention_vocab import HOLD_ERROR_SENTENCES, PROJECT_HOLD_LABEL


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows))


class _AllOnly:
    """A result that only answers ``.all()`` — what the MagicMock / queue fakes guarantee."""

    def all(self):
        return []


class _DB:
    def __init__(self, *results):
        self._queue = list(results)
        self.statements: list = []
        self.added: list = []
        self.flushes = 0

    async def execute(self, stmt, *_a, **_kw):
        self.statements.append(stmt)
        return self._queue.pop(0) if self._queue else _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1


def _sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect()))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actor(**kw):
    base = {"id": uuid.uuid4(), "full_name": "Quinn Lead", "username": "qlead", "email": "q@example.invalid"}
    base.update(kw)
    return SimpleNamespace(**base)


# ── the coverage trap (§6.5) ─────────────────────────────────────────────────


def test_the_module_never_records_activity():
    """The routes record; the service must not, or every handler that merely
    READS holds (pack retire / delete) would turn "covered" for the
    activity-coverage gate and two real gaps would leave its baseline."""
    source = pathlib.Path(svc.__file__).read_text(encoding="utf-8")
    assert "app.services.activity" not in source
    assert "record_activity(" not in source
    assert ".commit(" not in source, "services flush; the router commits"


# ── the clause builders: NOT EXISTS, never NOT IN ────────────────────────────


def _not_exists_count(sql: str) -> int:
    return len(re.findall(r"NOT \(EXISTS \(", sql))


@pytest.mark.parametrize(
    "name, build, top_level_arms",
    [
        ("project_not_held", lambda p, n: svc.project_not_held(p, now=n), 1),
        ("run_not_held", lambda p, n: svc.run_not_held(TestRun.id, p, now=n), 4),
        (
            "pack_not_held",
            lambda p, n: svc.pack_not_held(CompliancePack.id, CompliancePack.release_id, p, now=n),
            3,
        ),
        ("activity_not_held", lambda p, n: svc.activity_not_held(p, now=n), 6),
    ],
)
def test_every_clause_builder_is_an_anti_join(name, build, top_level_arms):
    sql = _sql(build(uuid.uuid4(), _now()))
    assert _not_exists_count(sql) == top_level_arms, sql
    assert "NOT IN" not in sql.upper(), f"{name} must never compile to NOT IN"
    assert "retention_holds" in sql
    # Expiry needs no job: every arm reads `now`.
    assert "released_at IS NULL" in sql and "expires_at >" in sql


def test_run_not_held_correlates_on_the_candidate_column_only():
    """In a statement over test_runs the arms correlate on test_runs.id and on
    nothing else — each arm reads its own aliases."""
    p = uuid.uuid4()
    sql = _sql(select(TestRun.id).where(TestRun.project_id == p, svc.run_not_held(TestRun.id, p, now=_now())))
    assert sql.count("= test_runs.id") == 3, sql  # run hold, release link, baseline row
    assert "FROM test_runs \nWHERE" in sql, "test_runs must not be re-added inside an arm"


def test_activity_casts_the_uuid_side_never_the_text_side():
    sql = _sql(svc.activity_not_held(uuid.uuid4(), now=_now()))
    assert "CAST(project_activity_events.entity_id" not in sql
    assert "AS VARCHAR) = project_activity_events.entity_id" in sql


def test_live_clause_reads_released_and_expiry():
    sql = _sql(svc.live_clause(_now()))
    assert "retention_holds.released_at IS NULL" in sql
    assert "retention_holds.expires_at IS NULL OR retention_holds.expires_at >" in sql


# ── HoldRef and hold_state ───────────────────────────────────────────────────


def test_hold_ref_sentence_and_json():
    hid, eid = uuid.uuid4(), uuid.uuid4()
    expires = _now() + timedelta(days=3)
    ref = svc.HoldRef(hid, "release", eid, "release 2026.10", "Audit Q4 sentinel", expires)
    assert ref.sentence() == "Held: Audit Q4 sentinel"
    assert ref.as_json() == {
        "hold_id": str(hid),
        "entity_type": "release",
        "entity_id": str(eid),
        "entity_label": "release 2026.10",
        "reason": "Audit Q4 sentinel",
        "expires_at": expires.isoformat(),
    }
    project = svc.HoldRef(hid, "project", None, PROJECT_HOLD_LABEL, "Litigation sentinel", None)
    assert project.as_json()["entity_id"] is None and project.as_json()["expires_at"] is None


def test_holds_honoured_record_is_the_one_job_shape():
    run_ids = [uuid.uuid4() for _ in range(250)]
    release = svc.HoldRef(uuid.uuid4(), "release", uuid.uuid4(), "release 2026.10", "Audit Q4 sentinel", None)
    project = svc.HoldRef(uuid.uuid4(), "project", None, PROJECT_HOLD_LABEL, "Litigation sentinel", None)
    record = svc.holds_honoured_record([release, project, release], run_ids_kept=run_ids + run_ids[:3])
    assert record is not None
    assert set(record) == {"project_held", "holds", "runs_kept", "packs_kept", "run_ids_kept"}
    assert record["project_held"] is True
    assert [h["hold_id"] for h in record["holds"]] == [str(release.id), str(project.id)], "deduped by id"
    assert record["runs_kept"] == 250, "distinct"
    assert record["run_ids_kept"] == sorted(str(r) for r in run_ids)[:200]
    assert record["packs_kept"] == 0
    assert svc.holds_honoured_record([], run_ids_kept=run_ids) is None, "NULL = no hold applied"


def test_hold_state_is_derived_for_now():
    now = _now()
    assert svc.hold_state(SimpleNamespace(released_at=None, expires_at=None), now=now) == "live"
    later = SimpleNamespace(released_at=None, expires_at=now + timedelta(minutes=1))
    assert svc.hold_state(later, now=now) == "live"
    past = SimpleNamespace(released_at=None, expires_at=now - timedelta(minutes=1))
    assert svc.hold_state(past, now=now) == "expired"
    assert svc.hold_state(SimpleNamespace(released_at=None, expires_at=now), now=now) == "expired"
    naive_past = SimpleNamespace(released_at=None, expires_at=(now - timedelta(hours=1)).replace(tzinfo=None))
    assert svc.hold_state(naive_past, now=now) == "expired"
    released = SimpleNamespace(released_at=now - timedelta(days=1), expires_at=now + timedelta(days=9))
    assert svc.hold_state(released, now=now) == "released"


# ── place_hold: validation order and its sentences ───────────────────────────


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"entity_type": "defect", "entity_id": None}, "unknown_entity_type"),
        ({"entity_type": "Project", "entity_id": None}, "unknown_entity_type"),
        ({"entity_type": "project", "entity_id": uuid.UUID(int=7)}, "entity_id_forbidden"),
        ({"entity_type": "release", "entity_id": None}, "entity_id_required"),
        ({"entity_type": "run", "entity_id": None}, "entity_id_required"),
        ({"entity_type": "compliance_pack", "entity_id": None}, "entity_id_required"),
        ({"entity_type": "project", "entity_id": None, "reason": "  ab  "}, "reason_length"),
        ({"entity_type": "project", "entity_id": None, "reason": "x" * 501}, "reason_length"),
    ],
)
@pytest.mark.asyncio
async def test_place_refuses_with_its_fixed_sentence(kwargs, code):
    db = _DB()
    args = {"reason": "Audit Q4 sentinel", "expires_at": None, **kwargs}
    with pytest.raises(svc.HoldValidationError) as caught:
        await svc.place_hold(db, project_id=uuid.uuid4(), actor=_actor(), now=_now(), **args)
    assert caught.value.code == code
    assert caught.value.message == HOLD_ERROR_SENTENCES[code]
    assert db.statements == [] and db.added == [], "validation runs before any read or write"


@pytest.mark.asyncio
async def test_place_refuses_an_expiry_in_the_past_or_too_far():
    now = _now()
    for expires, code in (
        (now - timedelta(seconds=1), "expiry_not_future"),
        (now, "expiry_not_future"),
        (now + timedelta(days=3650, seconds=1), "expiry_too_far"),
    ):
        with pytest.raises(svc.HoldValidationError) as caught:
            await svc.place_hold(
                _DB(), project_id=uuid.uuid4(), entity_type="project", entity_id=None,
                reason="Audit Q4 sentinel", expires_at=expires, actor=_actor(), now=now,
            )
        assert caught.value.code == code


@pytest.mark.asyncio
async def test_place_accepts_the_boundaries_and_makes_a_naive_expiry_utc():
    now = _now()
    project_id = uuid.uuid4()
    db = _DB(_Result([(project_id,)]))
    naive = (now + timedelta(days=3650)).replace(tzinfo=None)
    hold = await svc.place_hold(
        db, project_id=project_id, entity_type="project", entity_id=None,
        reason="  abc  ", expires_at=naive, actor=_actor(), now=now,
    )
    assert hold.reason == "abc", "the trimmed reason is stored"
    assert hold.expires_at.tzinfo is not None and hold.expires_at.utcoffset() == timedelta(0)


# ── place_hold: the lock, the entity inside the project, the label, the audit ──


@pytest.mark.parametrize(
    "entity_type, value, label, table",
    [
        ("release", "2026.10", "release 2026.10", "releases"),
        ("run", "4471", "Build 4471", "test_runs"),
        ("compliance_pack", datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc), "pack of 2026-10-02", "compliance_packs"),
    ],
)
@pytest.mark.asyncio
async def test_place_locks_both_rows_and_snapshots_the_label(entity_type, value, label, table):
    project_id, entity_id, actor = uuid.uuid4(), uuid.uuid4(), _actor()
    db = _DB(_Result([(project_id,)]), _Result([(value,)]))
    now = _now()
    hold = await svc.place_hold(
        db, project_id=project_id, entity_type=entity_type, entity_id=entity_id,
        reason="Audit Q4 sentinel", expires_at=None, actor=actor, now=now,
    )
    project_sql, entity_sql = (_sql(s) for s in db.statements)
    assert "FROM projects" in project_sql and project_sql.rstrip().endswith("FOR SHARE")
    assert f"FROM {table}" in entity_sql and entity_sql.rstrip().endswith("FOR SHARE")
    assert f"{table}.project_id = " in entity_sql, "the entity is resolved INSIDE the path's project"
    assert isinstance(hold, RetentionHold)
    assert (hold.entity_type, hold.entity_id, hold.entity_label) == (entity_type, entity_id, label)
    assert (hold.placed_by_id, hold.placed_by_name, hold.placed_at) == (actor.id, "Quinn Lead", now)
    audit = [o for o in db.added if isinstance(o, SettingsAuditLog)]
    assert len(audit) == 1 and db.flushes == 1
    row = audit[0]
    assert (row.setting_key, row.action) == (f"retention_hold:{project_id}", "hold_placed")
    assert row.changed_fields == {
        "project_id": str(project_id), "hold_id": str(hold.id), "entity_type": entity_type,
        "entity_id": str(entity_id), "entity_label": label, "reason": "Audit Q4 sentinel",
        "expires_at": None,
    }


@pytest.mark.asyncio
async def test_a_project_hold_reads_only_the_project_row():
    project_id = uuid.uuid4()
    db = _DB(_Result([(project_id,)]))
    hold = await svc.place_hold(
        db, project_id=project_id, entity_type="project", entity_id=None,
        reason="Litigation sentinel", expires_at=None, actor=_actor(full_name=None), now=_now(),
    )
    assert len(db.statements) == 1
    assert hold.entity_label == PROJECT_HOLD_LABEL
    assert hold.placed_by_name == "qlead", "full name, else the username — never the e-mail"


@pytest.mark.asyncio
async def test_an_entity_of_another_project_is_not_found_and_nothing_is_staged():
    """Absent and foreign alike: the select carries project_id, so another
    project's release answers no row — the same 404, no existence oracle."""
    db = _DB(_Result([(uuid.uuid4(),)]), _Result([]))
    with pytest.raises(svc.HoldEntityNotFound):
        await svc.place_hold(
            db, project_id=uuid.uuid4(), entity_type="release", entity_id=uuid.uuid4(),
            reason="Audit Q4 sentinel", expires_at=None, actor=_actor(), now=_now(),
        )
    assert db.added == [] and db.flushes == 0


@pytest.mark.asyncio
async def test_a_missing_project_is_not_found():
    db = _DB(_Result([]))
    with pytest.raises(svc.HoldEntityNotFound):
        await svc.place_hold(
            db, project_id=uuid.uuid4(), entity_type="project", entity_id=None,
            reason="Audit Q4 sentinel", expires_at=None, actor=_actor(), now=_now(),
        )
    assert db.added == []


# ── release_hold ─────────────────────────────────────────────────────────────


def _hold_row(project_id, **kw):
    base = {
        "id": uuid.uuid4(), "project_id": project_id, "entity_type": "release",
        "entity_id": uuid.uuid4(), "entity_label": "release 2026.10", "reason": "Audit Q4 sentinel",
        "expires_at": None, "released_at": None, "released_by_id": None,
        "released_by_name": None, "release_reason": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_release_stamps_the_hold_and_adds_its_audit_row():
    project_id, actor, now = uuid.uuid4(), _actor(full_name="Ada Admin"), _now()
    row = _hold_row(project_id)
    db = _DB(_Result([row]))
    out = await svc.release_hold(
        db, project_id=project_id, hold_id=row.id, reason=" Audit closed ", actor=actor, now=now,
    )
    sql = _sql(db.statements[0])
    assert "retention_holds.id = " in sql and "retention_holds.project_id = " in sql
    assert sql.rstrip().endswith("FOR UPDATE")
    assert out is row
    assert (row.released_at, row.released_by_id, row.released_by_name, row.release_reason) == (
        now, actor.id, "Ada Admin", "Audit closed",
    )
    (audit,) = [o for o in db.added if isinstance(o, SettingsAuditLog)]
    assert (audit.setting_key, audit.action) == (f"retention_hold:{project_id}", "hold_released")
    assert audit.changed_fields["release_reason"] == "Audit closed"
    assert audit.changed_fields["hold_id"] == str(row.id)
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_release_of_an_unknown_or_foreign_hold_is_not_found():
    with pytest.raises(svc.HoldNotFound):
        await svc.release_hold(
            _DB(_Result([])), project_id=uuid.uuid4(), hold_id=uuid.uuid4(),
            reason="Audit closed", actor=_actor(), now=_now(),
        )


@pytest.mark.parametrize("state", ["released", "expired"])
@pytest.mark.asyncio
async def test_a_hold_that_is_not_live_cannot_be_released(state):
    now, project_id = _now(), uuid.uuid4()
    if state == "released":
        row = _hold_row(project_id, released_at=now - timedelta(hours=1), release_reason="done before")
    else:
        row = _hold_row(project_id, expires_at=now - timedelta(minutes=1))
    db = _DB(_Result([row]))
    with pytest.raises(svc.HoldNotLive):
        await svc.release_hold(
            db, project_id=project_id, hold_id=row.id, reason="Audit closed", actor=_actor(), now=now,
        )
    assert db.added == [] and db.flushes == 0


@pytest.mark.asyncio
async def test_release_needs_a_reason():
    project_id = uuid.uuid4()
    row = _hold_row(project_id)
    with pytest.raises(svc.HoldValidationError) as caught:
        await svc.release_hold(
            _DB(_Result([row])), project_id=project_id, hold_id=row.id, reason="  ", actor=_actor(), now=_now(),
        )
    assert caught.value.code == "reason_length"
    assert row.released_at is None


# ── list_holds / count_holds_by_state ────────────────────────────────────────


@pytest.mark.parametrize(
    "state, fragment",
    [
        ("live", "retention_holds.released_at IS NULL AND (retention_holds.expires_at IS NULL OR"),
        ("expired", "retention_holds.expires_at IS NOT NULL AND retention_holds.expires_at <="),
        ("released", "retention_holds.released_at IS NOT NULL"),
        ("all", None),
    ],
)
@pytest.mark.asyncio
async def test_list_filters_by_state_and_counts_the_same_filter(state, fragment):
    project_id, entity_id = uuid.uuid4(), uuid.uuid4()
    rows = [_hold_row(project_id), _hold_row(project_id)]
    db = _DB(_Result(rows), _Result(scalar=7))
    items, total = await svc.list_holds(
        db, project_id=project_id, state=state, entity_type="release", entity_id=entity_id,
        limit=2, now=_now(),
    )
    assert items == rows
    assert total == 7, "total is a COUNT of the filter, never len(items)"
    list_sql, count_sql = (_sql(s) for s in db.statements)
    for sql in (list_sql, count_sql):
        assert "retention_holds.project_id = " in sql
        assert "retention_holds.entity_type = " in sql and "retention_holds.entity_id = " in sql
        if fragment:
            assert fragment in sql
        else:
            assert "released_at" not in sql.split("WHERE", 1)[1]
    assert "ORDER BY retention_holds.placed_at DESC, retention_holds.id DESC" in list_sql
    assert "count(*)" in count_sql and "LIMIT" not in count_sql


@pytest.mark.asyncio
async def test_list_refuses_an_unknown_state_or_entity_type():
    for kwargs, code in (({"state": "pending"}, "unknown_state"), ({"entity_type": "defect"}, "unknown_entity_type")):
        db = _DB()
        with pytest.raises(svc.HoldValidationError) as caught:
            await svc.list_holds(db, project_id=uuid.uuid4(), now=_now(), **kwargs)
        assert caught.value.code == code and db.statements == []


@pytest.mark.asyncio
async def test_list_clamps_the_limit():
    db = _DB(_Result([]), _Result(scalar=0))
    await svc.list_holds(db, project_id=uuid.uuid4(), state="all", limit=5000, now=_now())
    assert db.statements[0]._limit_clause.value == 200


@pytest.mark.asyncio
async def test_counts_carry_every_state_from_one_group_by():
    db = _DB(_Result([("live", 2), ("released", 5)]))
    counts = await svc.count_holds_by_state(db, project_id=uuid.uuid4(), now=_now())
    assert counts == {"live": 2, "expired": 0, "released": 5}
    (stmt,) = db.statements
    assert "GROUP BY anon_1.state" in _sql(stmt)


# ── readers: ONE statement each, read with `.all()`; fail closed ─────────────


def _readers(project_id):
    pack = SimpleNamespace(id=uuid.uuid4(), project_id=project_id, release_id=uuid.uuid4())
    now = _now()
    return [
        ("live_holds", lambda db: svc.live_holds(db, project_id, now=now)),
        ("any_live_hold", lambda db: svc.any_live_hold(db, project_id=project_id, now=now)),
        ("holds_covering_run", lambda db: svc.holds_covering_run(db, run_id=uuid.uuid4(), now=now)),
        ("holds_covering_runs", lambda db: svc.holds_covering_runs(
            db, project_id=project_id, run_ids=[uuid.uuid4()], now=now)),
        ("holds_covering_pack", lambda db: svc.holds_covering_pack(db, pack=pack, now=now)),
        ("holds_covering_packs", lambda db: svc.holds_covering_packs(
            db, project_id=project_id, release_id=pack.release_id, pack_ids=[pack.id], now=now)),
        ("holds_covering_release", lambda db: svc.holds_covering_release(db, release_id=uuid.uuid4(), now=now)),
        ("held_run_ids", lambda db: svc.held_run_ids(db, project_id=project_id, now=now)),
        ("pinned_runs_before", lambda db: svc.pinned_runs_before(
            db, project_id=project_id, created_before=now, now=now)),
        ("pinned_expired_packs", lambda db: svc.pinned_expired_packs(db, project_id=project_id, now=now)),
    ]


@pytest.mark.asyncio
async def test_every_reader_is_one_statement_read_with_all():
    """The queue / MagicMock fakes of the existing guards answer `.all()` with
    `[]`; a reader that used `.scalars()` / `.mappings()` would crash them."""
    for name, call in _readers(uuid.uuid4()):
        db = _DB(_AllOnly(), _AllOnly())
        out = await call(db)
        assert len(db.statements) == 1, name
        assert not out or out == {next(iter(out)): []}, name
        sql = _sql(db.statements[0])
        assert "retention_holds" in sql, name
        assert "NOT IN" not in sql.upper(), name


@pytest.mark.asyncio
async def test_a_failing_hold_read_propagates():
    """Fail closed: nothing here turns a statement error into "no holds"."""
    class _Broken(_DB):
        async def execute(self, stmt, *_a, **_kw):
            raise RuntimeError("statement failed")

    for name, call in _readers(uuid.uuid4()):
        with pytest.raises(RuntimeError):
            await call(_Broken())


@pytest.mark.asyncio
async def test_empty_id_lists_issue_no_statement():
    db = _DB()
    now = _now()
    assert await svc.holds_covering_runs(db, project_id=uuid.uuid4(), run_ids=[], now=now) == {}
    assert await svc.holds_covering_packs(
        db, project_id=uuid.uuid4(), release_id=None, pack_ids=[], now=now) == {}
    assert db.statements == []


def _ref_row(entity_type, entity_id, reason="Audit Q4 sentinel"):
    return (uuid.uuid4(), entity_type, entity_id, "label", reason, None)


@pytest.mark.asyncio
async def test_covering_packs_spreads_project_and_release_holds_to_every_pack():
    project_id, release_id = uuid.uuid4(), uuid.uuid4()
    held_pack, free_pack = uuid.uuid4(), uuid.uuid4()
    project_row = _ref_row("project", None, "Litigation sentinel")
    release_row = _ref_row("release", release_id)
    pack_row = _ref_row("compliance_pack", held_pack)
    db = _DB(_Result([project_row, release_row, pack_row]))
    out = await svc.holds_covering_packs(
        db, project_id=project_id, release_id=release_id, pack_ids=[held_pack, free_pack], now=_now(),
    )
    assert [r.id for r in out[held_pack]] == [project_row[0], release_row[0], pack_row[0]]
    assert [r.id for r in out[free_pack]] == [project_row[0], release_row[0]]
    sql = _sql(db.statements[0])
    assert "EXISTS" not in sql, "comparisons / IN only — it must run on any engine"


@pytest.mark.asyncio
async def test_covering_runs_groups_by_run_and_dedupes_holds():
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    hold = _ref_row("project", None)
    db = _DB(_Result([(run_a, *hold), (run_b, *hold), (run_a, *hold)]))
    out = await svc.holds_covering_runs(db, project_id=uuid.uuid4(), run_ids=[run_a, run_b, run_a], now=_now())
    assert set(out) == {run_a, run_b}
    assert [r.sentence() for r in out[run_a]] == ["Held: Audit Q4 sentinel"]


@pytest.mark.asyncio
async def test_pinned_reads_group_by_hold():
    hold_a, hold_b, run_x, run_y = (uuid.uuid4() for _ in range(4))
    db = _DB(_Result([(hold_a, run_x), (hold_a, run_y), (hold_b, run_x)]))
    out = await svc.pinned_runs_before(db, project_id=uuid.uuid4(), created_before=_now(), now=_now())
    assert out == {hold_a: {run_x, run_y}, hold_b: {run_x}}
    sql = _sql(db.statements[0])
    assert "test_runs" in sql and "created_at <" in sql
