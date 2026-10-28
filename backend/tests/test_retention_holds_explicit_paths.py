"""W22a ACT-05 — every EXPLICIT deletion path refuses what a live hold covers (H-6 … H-14).

Each path is driven with the REAL hold service and the real
``citation_blockers`` against a statement-routing fake session: a statement
over ``retention_holds`` answers the hold rows the test scripts, everything
else answers what the path needs. Each refusal is a 409 with ``Held: <reason>``
FIRST, and nothing irreversible is awaited (storage, the deleting service, the
job queue, the attempt row). The Postgres half lives in
``tests/integration/test_retention_holds_postgres.py``.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.services import retention_hold_service
from app.services.retention_vocab import FLOOR_NOTE_EXPLICIT

REASON = "Audit Q4 sentinel"
HELD = f"Held: {REASON}"


def _hold_row(entity_type="release", entity_id=None, reason=REASON, hold_id=None):
    return (hold_id or uuid.uuid4(), entity_type, entity_id or uuid.uuid4(), "release 2026.10", reason, None)


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

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows))


class _DB:
    """Answers by the statement's text: the FIRST rule whose fragment it contains wins."""

    def __init__(self, journal=None, *, holds=(), rules=(), get=None):
        self.journal = journal if journal is not None else []
        self.rules = [("retention_holds", _Result(list(holds)))] + list(rules)
        self.sql: list[str] = []
        self.added: list = []
        self._get = get

    async def execute(self, stmt, *_a, **_kw):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        self.sql.append(sql)
        for fragment, result in self.rules:
            if fragment in sql:
                return result
        return _Result()

    async def scalar(self, *_a, **_kw):
        return None

    async def get(self, *_a, **_kw):
        return self._get

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.journal.append(("flush", ""))

    async def commit(self):
        self.journal.append(("commit", ""))

    async def refresh(self, *_a, **_kw):
        return None

    async def delete(self, obj):
        self.journal.append(("delete", ""))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    @property
    def hold_reads(self) -> int:
        return sum("retention_holds" in s for s in self.sql)


class _Mongo:
    def __getitem__(self, _name):
        class _C:
            async def count_documents(self, _q):
                return 0

        return _C()


def _run(**kw):
    base = {"id": uuid.uuid4(), "project_id": uuid.uuid4(), "status": "PASSED", "minio_prefix": None, "build_number": "4471"}
    base.update(kw)
    return SimpleNamespace(**base)


# ── H-6: the single-run DELETE route ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_h6_the_delete_route_names_the_hold_first_and_queues_nothing(mocker):
    from app.routers.runs import delete_run

    run = _run()
    db = _DB(holds=[_hold_row()], rules=[
        ("FROM release_test_run_links", _Result(["release-1"])),
        ("FROM compliance_packs", _Result([])),
        ("FROM test_runs", _Result(scalar=run)),
    ])
    mocker.patch("app.db.mongo.get_mongo_db", mocker.Mock(return_value=_Mongo()))
    mocker.patch("app.services.run_tombstone_service.run_is_tombstoned", AsyncMock(return_value=False))
    open_job = mocker.patch("app.services.deletion_job_service.open_job", AsyncMock())
    task = mocker.patch("app.worker.tasks.delete_run_everywhere")

    with pytest.raises(HTTPException) as caught:
        await delete_run(
            run_id=run.id, body=SimpleNamespace(confirm=True, reason="cleanup"), db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()), _=SimpleNamespace(),
        )

    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert detail["run_id"] == str(run.id)
    assert detail["blockers"][0] == HELD
    assert detail["blockers"][1].startswith("linked to 1 release(s)")
    open_job.assert_not_awaited()
    task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_h6_an_unheld_run_is_accepted_with_the_warnings_and_the_floor_note(mocker):
    from app.routers.runs import delete_run
    from app.services.retention_references import ReferenceReport

    run = _run()
    db = _DB(rules=[("FROM test_runs", _Result(scalar=run))])
    mocker.patch("app.db.mongo.get_mongo_db", mocker.Mock(return_value=_Mongo()))
    mocker.patch("app.services.run_tombstone_service.run_is_tombstoned", AsyncMock(return_value=False))
    mocker.patch("app.services.deletion_job_service.open_job", AsyncMock(return_value=uuid.uuid4()))
    mocker.patch("app.worker.tasks.delete_run_everywhere")
    item = {"table": "run_baselines", "column": "baseline_run_id", "count": 3, "label": "3 runs lose their baseline link"}
    refs = mocker.patch(
        "app.services.retention_references.references_broken",
        AsyncMock(return_value=ReferenceReport(True, [item])),
    )

    response = await delete_run(
        run_id=run.id, body=SimpleNamespace(confirm=True, reason="cleanup"), db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()), _=SimpleNamespace(),
    )

    assert refs.await_args.args == (db, [run.id]), "counted on the REQUEST session for this run"
    assert [r.label for r in response.references_broken] == ["3 runs lose their baseline link"]
    assert response.references_measured is True
    assert response.floor_applies is False and response.floor_note == FLOOR_NOTE_EXPLICIT


@pytest.mark.asyncio
async def test_citation_blockers_fails_closed_when_the_holds_cannot_be_read(mocker):
    """No route deletes a run whose holds could not be read."""
    from app.services import run_deletion_service

    mocker.patch.object(
        retention_hold_service, "holds_covering_run", AsyncMock(side_effect=RuntimeError("statement failed"))
    )
    blockers = await run_deletion_service.citation_blockers(_DB(), run_id=uuid.uuid4(), mongo=_Mongo())
    assert blockers == [run_deletion_service.HOLDS_UNREADABLE]


@pytest.mark.asyncio
async def test_citation_blockers_names_every_distinct_hold_once_and_first():
    from app.services import run_deletion_service

    same = uuid.uuid4()
    db = _DB(
        holds=[_hold_row(hold_id=same), _hold_row(hold_id=same), _hold_row("run", reason="Litigation sentinel")],
        rules=[("FROM compliance_packs", _Result(["pack-a"]))],
    )
    blockers = await run_deletion_service.citation_blockers(db, run_id=uuid.uuid4(), mongo=_Mongo())
    assert blockers[:2] == [HELD, "Held: Litigation sentinel"]
    assert blockers[2].startswith("cited by 1 compliance pack(s)")
    assert "retention_holds" in db.sql[-1], "the hold read is the LAST statement"


# ── H-7: the single-run worker ───────────────────────────────────────────────


def _worker_stubs(monkeypatch, mocker, factory):
    from app.db import mongo as mongo_module
    from app.db import postgres as postgres_module
    from app.db import storage as storage_module
    from app.services import deletion_job_service, run_deletion_service, semantic_search
    from app.worker import tasks

    monkeypatch.setattr(postgres_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(mongo_module, "get_mongo_db", lambda: _Mongo())
    monkeypatch.setattr(storage_module, "get_storage_provider", lambda: object())
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    mocker.patch("app.services.cache_service.bump_analytics_epoch", AsyncMock())
    return SimpleNamespace(
        close=mocker.patch.object(deletion_job_service, "close_job", AsyncMock(return_value=True)),
        purge=mocker.patch.object(semantic_search, "purge_run_documents", AsyncMock(return_value=0)),
        delete=mocker.patch.object(
            run_deletion_service, "perform_run_deletion", AsyncMock(return_value={"postgres": {"runs": 1}})
        ),
    )


def _reference_report():
    from app.services.retention_references import ReferenceReport

    item = {"table": "run_baselines", "column": "baseline_run_id", "count": 3, "label": "3 runs lose their baseline link"}
    return ReferenceReport(True, [item])


def test_h7_a_hold_placed_after_the_202_fails_the_job_with_holds_honoured(monkeypatch, mocker):
    from app.services import deletion_job_service, run_deletion_service
    from app.worker import tasks

    run, hold_id = _run(), uuid.uuid4()
    db = _DB(
        holds=[(hold_id, "project", None, "the whole project", REASON, None)],
        rules=[("FROM test_runs", _Result(scalar=run))],
    )
    stubs = _worker_stubs(monkeypatch, mocker, lambda: db)
    job_id = uuid.uuid4()

    with pytest.raises(run_deletion_service.RunDeletionBlocked):
        tasks.delete_run_everywhere.run(str(run.id), str(job_id))

    stubs.purge.assert_not_awaited()
    stubs.delete.assert_not_awaited()
    assert ("commit", "") not in db.journal
    kwargs = stubs.close.await_args.kwargs
    assert kwargs["status"] == deletion_job_service.FAILED
    assert kwargs["error"].startswith(f"run {run.id} is protected: {HELD}")
    assert kwargs["holds_honoured"] == {
        "project_held": True,
        "holds": [{
            "hold_id": str(hold_id), "entity_type": "project", "entity_id": None,
            "entity_label": "the whole project", "reason": REASON, "expires_at": None,
        }],
        "runs_kept": 1,
        "packs_kept": 0,
        "run_ids_kept": [str(run.id)],
    }
    assert db.hold_reads == 2, "citation_blockers' read, then the structured refs once"


def test_h7_an_unheld_run_records_the_links_it_broke(monkeypatch, mocker):
    from app.services import deletion_job_service
    from app.worker import tasks

    run = _run()
    db = _DB(rules=[("FROM test_runs", _Result(scalar=run))])
    stubs = _worker_stubs(monkeypatch, mocker, lambda: db)
    refs = mocker.patch(
        "app.services.retention_references.references_broken", AsyncMock(return_value=_reference_report())
    )

    tasks.delete_run_everywhere.run(str(run.id), str(uuid.uuid4()))

    assert refs.await_args.args == (db, [run.id]), "on the worker's own session, before the delete"
    stubs.delete.assert_awaited_once()
    kwargs = stubs.close.await_args.kwargs
    assert kwargs["status"] == deletion_job_service.COMPLETED
    assert kwargs["references_broken"] == {"measured": True, "total": 3, "run_baselines.baseline_run_id": 3}
    assert "holds_honoured" not in kwargs, "NULL = no hold applied"
    assert db.hold_reads == 1, "no structured read without a Held: blocker"


# ── H-8: criteria deletion — the preview and the worker ──────────────────────


class _QueuedHoldsDB(_DB):
    """Each hold read pops the next scripted answer (per-run, in run order)."""

    def __init__(self, *hold_answers, **kw):
        super().__init__(**kw)
        self._hold_answers = list(hold_answers)

    async def execute(self, stmt, *_a, **_kw):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        if "retention_holds" in sql:
            self.sql.append(sql)
            return _Result(self._hold_answers.pop(0) if self._hold_answers else [])
        return await super().execute(stmt)


@pytest.mark.asyncio
async def test_h8_the_criteria_preview_names_the_hold_first_and_freezes_only_the_rest(mocker):
    from app.models.postgres import LaunchStatus
    from app.routers.retention import preview_criteria_deletion

    held_run, free_run = uuid.uuid4(), uuid.uuid4()
    mocker.patch("app.services.deletion_criteria.foreign_run_ids", AsyncMock(return_value=[]))
    mocker.patch(
        "app.services.deletion_criteria.resolve_criteria_candidates",
        AsyncMock(return_value=[held_run, free_run]),
    )
    mocker.patch("app.db.mongo.get_mongo_db", mocker.Mock(return_value=_Mongo()))
    frozen = mocker.patch(
        "app.services.deletion_job_service.stage_frozen_candidate_set", mocker.Mock(return_value=uuid.uuid4())
    )
    refs = mocker.patch(
        "app.services.retention_references.references_broken", AsyncMock(return_value=_reference_report())
    )
    # The held run is ALSO still executing: the hold is named first anyway.
    rows = _Result([(held_run, LaunchStatus.IN_PROGRESS, None), (free_run, LaunchStatus.PASSED, None)])
    db = _QueuedHoldsDB([_hold_row()], [], rules=[("FROM test_runs", rows)])

    response = await preview_criteria_deletion(
        project_id=uuid.uuid4(), criteria=SimpleNamespace(run_ids=None, model_dump=lambda **_k: {}),
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=SimpleNamespace(),
    )

    assert response.blocked == [{"run_id": str(held_run), "reasons": [HELD, "run is still executing"]}]
    assert response.run_ids == [free_run]
    assert frozen.call_args.kwargs["run_ids"] == [free_run]
    assert refs.await_args.args == (db, [free_run]), "the warnings cover the deletable runs only"
    assert [r.label for r in response.references_broken] == ["3 runs lose their baseline link"]
    assert response.floor_applies is False and response.floor_note == FLOOR_NOTE_EXPLICIT


def test_h8_the_worker_refuses_a_run_held_after_the_preview_and_records_it(monkeypatch, mocker):
    from app.services import deletion_job_service
    from app.worker import tasks

    project_id = uuid.uuid4()
    held, free = _run(project_id=project_id), _run(project_id=project_id)
    hold = _hold_row()
    start = _DB()
    held_db = _DB(holds=[hold], rules=[("FROM test_runs", _Result(scalar=held))])
    free_db = _DB(rules=[("FROM test_runs", _Result(scalar=free))])
    sessions = [start, held_db, free_db]
    stubs = _worker_stubs(monkeypatch, mocker, lambda: sessions.pop(0))
    mocker.patch.object(
        deletion_job_service, "start_frozen_set", AsyncMock(return_value=[held.id, free.id])
    )
    refs = mocker.patch(
        "app.services.retention_references.references_broken", AsyncMock(return_value=_reference_report())
    )

    result = tasks.execute_criteria_deletion_task.run(str(uuid.uuid4()), str(project_id), None)

    assert (result["deleted"], result["failed"]) == (1, 1)
    assert refs.await_count == 1 and refs.await_args.args == (start, [held.id, free.id]), (
        "counted ONCE, on the start session, before the per-run loop"
    )
    stubs.delete.assert_awaited_once()
    assert stubs.delete.await_args.kwargs["run"] is free
    kwargs = stubs.close.await_args.kwargs
    assert kwargs["status"] == deletion_job_service.outcome_status(requested=2, deleted=1)
    assert kwargs["holds_honoured"]["run_ids_kept"] == [str(held.id)]
    assert kwargs["holds_honoured"]["holds"] == [retention_hold_service.HoldRef.from_row(hold).as_json()]
    assert kwargs["holds_honoured"]["project_held"] is False
    assert kwargs["references_broken"]["total"] == 3
    assert HELD in kwargs["error"]


# ── H-9 / H-10: compliance-pack delete and retire; the per-release pack list ─


def _pack(*, expired=True, release_id=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), release_id=release_id or uuid.uuid4(),
        minio_key="compliance/pack.zip",
        retention_expires_at=now - timedelta(days=1) if expired else now + timedelta(days=365),
    )


@pytest.fixture
def pack_route(mocker):
    from app.routers import compliance_packs as route

    mocker.patch.object(route, "resolve_project_scope", AsyncMock(return_value=(None, None)))
    storage = SimpleNamespace(delete_object=AsyncMock())
    mocker.patch("app.db.storage.get_storage_provider", mocker.Mock(return_value=storage))
    return SimpleNamespace(route=route, storage=storage)


@pytest.mark.asyncio
async def test_h9_a_held_pack_is_not_deleted_and_the_hold_comes_before_the_window(pack_route, mocker):
    pack = _pack(expired=False)
    mocker.patch("app.services.compliance_pack_lifecycle.get_pack_for_write", AsyncMock(return_value=pack))
    db = _DB(holds=[_hold_row("compliance_pack", pack.id)])

    with pytest.raises(HTTPException) as caught:
        await pack_route.route.delete_compliance_pack(
            pack_id=pack.id, current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
        )

    assert caught.value.status_code == 409
    blockers = caught.value.detail["blockers"]
    assert blockers[0] == HELD and blockers[1].startswith("retention window runs to")
    pack_route.storage.delete_object.assert_not_awaited()
    assert ("delete", "") not in db.journal and ("commit", "") not in db.journal
    hold_sql = next(s for s in db.sql if "retention_holds" in s)
    assert "retention_holds.project_id = " in hold_sql


@pytest.mark.asyncio
async def test_h9_an_expired_unheld_pack_is_still_deleted(pack_route, mocker):
    pack = _pack(expired=True)
    mocker.patch("app.services.compliance_pack_lifecycle.get_pack_for_write", AsyncMock(return_value=pack))
    db = _DB()

    await pack_route.route.delete_compliance_pack(pack_id=pack.id, current_user=SimpleNamespace(id=uuid.uuid4()), db=db)

    pack_route.storage.delete_object.assert_awaited_once()
    assert ("delete", "") in db.journal and ("commit", "") in db.journal


@pytest.mark.asyncio
async def test_h10_a_held_pack_is_not_retired_and_the_hold_comes_before_the_confirmation(pack_route, mocker):
    """A WRONG confirmation id still gets the hold's 409, not the 422: the hold
    check runs first, and the pack's window is untouched."""
    pack = _pack(expired=False)
    window = pack.retention_expires_at
    mocker.patch("app.services.compliance_pack_lifecycle.get_pack_for_write", AsyncMock(return_value=pack))
    db = _DB(holds=[_hold_row("release", pack.release_id)])

    with pytest.raises(HTTPException) as caught:
        await pack_route.route.retire_compliance_pack(
            pack_id=pack.id, body=SimpleNamespace(confirmation_id="not-the-id", reason="early"),
            current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == {"blockers": [HELD]}
    assert pack.retention_expires_at == window
    assert ("commit", "") not in db.journal


@pytest.mark.asyncio
async def test_the_per_release_pack_list_carries_each_packs_holds(mocker):
    from app.routers.compliance_packs import list_compliance_packs

    release_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    packs = [
        SimpleNamespace(
            id=uuid.uuid4(), release_id=release_id, project_id=uuid.uuid4(), test_run_id=None,
            minio_key="k", manifest_sha256="a" * 64, file_count=1, bytes=10,
            retention_expires_at=now + timedelta(days=9), generated_at=now, generated_by_user_id=None,
            metadata_snapshot=None, notes=None, signing_key_id=None,
        )
        for _ in range(2)
    ]
    mocker.patch("app.services.compliance_pack_service.list_packs_for_release", AsyncMock(return_value=packs))
    own = _hold_row("compliance_pack", packs[0].id)
    db = _DB(holds=[own])

    out = await list_compliance_packs(release_id=release_id, current_user=None, db=db, _=None)

    assert [[h.reason for h in p.holds] for p in out] == [[REASON], []]
    assert db.hold_reads == 1, "ONE read for the whole list"


# ── H-11: the policy purge's job record ──────────────────────────────────────


@pytest.mark.parametrize("produced", [True, False])
@pytest.mark.asyncio
async def test_h11_the_sweep_passes_what_the_purge_kept_and_broke(produced, monkeypatch, mocker):
    import app.db.postgres as pg
    from app.services import deletion_job_service, retention_service
    from app.worker.tasks import _retention_purge_sweep

    project_id = uuid.uuid4()
    honoured = {"project_held": True, "holds": [], "runs_kept": 4, "packs_kept": 0, "run_ids_kept": []}
    broken = {"measured": True, "total": 2, "run_baselines.baseline_run_id": 2}

    async def _purge(db, *, project_id, mode):
        out = {"counts": {"postgres": {"runs": 0}}, "cutoffs": {}}
        if produced:
            out.update(holds_honoured=honoured, references_job=broken)
        else:
            out.update(holds_honoured=None, references_job=None)
        return out

    monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(retention_service, "run_purge", _purge)
    mocker.patch.object(deletion_job_service, "open_job", AsyncMock(return_value=uuid.uuid4()))
    close = mocker.patch.object(deletion_job_service, "close_job", AsyncMock(return_value=True))
    mocker.patch("app.services.cache_service.bump_analytics_epoch", AsyncMock())

    await _retention_purge_sweep(str(project_id))

    kwargs = close.await_args.kwargs
    if produced:
        assert kwargs["holds_honoured"] == honoured and kwargs["references_broken"] == broken
    else:
        assert "holds_honoured" not in kwargs and "references_broken" not in kwargs


# ── H-12: project reset ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_h12_a_held_project_refuses_its_reset_before_the_attempt_row(mocker):
    from app.routers import projects as route

    project = SimpleNamespace(id=uuid.uuid4(), name="Payments", is_active=True)
    db = _DB(holds=[(uuid.uuid4(), "project", None, "the whole project", REASON, None)], get=project)
    recorder = mocker.patch.object(route, "record_activity", AsyncMock())
    service = mocker.patch.object(route, "reset_project", AsyncMock())

    with pytest.raises(HTTPException) as caught:
        await route.reset_project_data(
            project_id=project.id,
            payload=SimpleNamespace(confirmation_name="Payments", mode="runs"),
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4(), full_name="Ada", email=None),
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == {"blockers": [HELD]}
    recorder.assert_not_awaited()
    service.assert_not_awaited()


@pytest.mark.asyncio
async def test_h12_the_service_refuses_under_its_lock_before_any_count_or_delete():
    """A hold committed between the route's pre-check and the service's lock is
    still caught: the service reads holds again, after the confirmation check."""
    from app.services.project_reset_service import ResetBlockedByHold, reset_project

    project = SimpleNamespace(id=uuid.uuid4(), name="Payments", is_active=True)
    db = _DB(
        holds=[_hold_row("run"), _hold_row("release", reason="Litigation sentinel")],
        rules=[("FROM projects", _Result(scalar=project))],
    )

    with pytest.raises(ResetBlockedByHold) as caught:
        await reset_project(db, project_id=project.id, mode="full", confirmation_name="Payments")

    assert caught.value.reasons == [HELD, "Held: Litigation sentinel"]
    assert len(db.sql) == 2, "the project lock, then the hold read — no count, no delete"
    assert "FOR UPDATE" in db.sql[0]
    assert ("commit", "") not in db.journal and db.added == []


# ── H-13 / H-14: release delete and run unlink; the release detail ───────────


@pytest.fixture
def release_route(mocker):
    from app.routers import releases as route
    from app.services import release_service

    return SimpleNamespace(
        route=route,
        doomed=mocker.patch.object(
            release_service, "get_release_or_404",
            AsyncMock(return_value=SimpleNamespace(project_id=uuid.uuid4(), name="2026.10")),
        ),
        delete=mocker.patch.object(release_service, "delete_release", AsyncMock()),
        unlink=mocker.patch.object(release_service, "unlink_test_run", AsyncMock()),
        recorder=mocker.patch.object(route, "record_activity", AsyncMock()),
        bump=mocker.patch("app.services.cache_service.bump_analytics_epoch", AsyncMock()),
    )


@pytest.mark.asyncio
async def test_h13_a_held_release_is_not_deleted(release_route):
    release_id = str(uuid.uuid4())
    db = _DB(holds=[_hold_row("release", uuid.UUID(release_id))])

    with pytest.raises(HTTPException) as caught:
        await release_route.route.delete_release(
            release_id, db=db, current_user=SimpleNamespace(id=uuid.uuid4()), __=None,
        )

    assert caught.value.status_code == 409 and caught.value.detail == {"blockers": [HELD]}
    release_route.delete.assert_not_awaited()
    release_route.recorder.assert_not_awaited()
    release_route.bump.assert_not_awaited()
    hold_sql = next(s for s in db.sql if "retention_holds" in s)
    assert "FROM releases" in hold_sql, "the project arm reads the release's project in SQL"


@pytest.mark.asyncio
async def test_h14_a_run_is_not_unlinked_from_a_held_release(release_route):
    db = _DB(holds=[(uuid.uuid4(), "project", None, "the whole project", REASON, None)])

    with pytest.raises(HTTPException) as caught:
        await release_route.route.unlink_test_run(
            str(uuid.uuid4()), str(uuid.uuid4()), db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()), __=None, ___=None,
        )

    assert caught.value.status_code == 409 and caught.value.detail == {"blockers": [HELD]}
    release_route.unlink.assert_not_awaited()
    assert ("commit", "") not in db.journal


@pytest.mark.asyncio
async def test_the_release_detail_carries_its_live_holds(release_route, mocker):
    from app.services import release_service

    release_id = str(uuid.uuid4())
    mocker.patch.object(release_service, "get_release_details", AsyncMock(return_value={"id": release_id}))
    hold = _hold_row("release", uuid.UUID(release_id))
    db = _DB(holds=[hold])

    out = await release_route.route.get_release(release_id, db=db, _=None)

    assert out["id"] == release_id
    assert out["holds"] == [retention_hold_service.HoldRef.from_row(hold).as_json()]

