"""W22b ACT-03 E-7 / E-8 — collect, acknowledge and execute-with-export, through the real guards.

A mini app mounts ``routers/retention.py`` alone (``test_retention_hold_routes``'
pattern): ``require_role`` / ``require_project_access`` run for real; only
``get_current_active_user`` and ``get_db`` are overridden. The ``get_db``
override commits on success and rolls back on an exception like the real one,
and the fake session's rollback restores the job row -- so "a refusal leaves
the job ``previewed``" and "a 502 records nothing" are asserted, not assumed.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.core.deps import (  # noqa: E402
    CREDENTIAL_KIND_API_KEY,
    CREDENTIAL_KIND_JWT,
    _bind_api_key_project,
    _bind_credential_kind,
    get_current_active_user,
    get_db,
)
from app.models.postgres import SettingsAuditLog  # noqa: E402
from app.routers import retention as route  # noqa: E402
from app.services.retention_vocab import EXPORT_ERROR_SENTENCES  # noqa: E402

KEY = "exports/2026/10/28/project/secret-location-sentinel.zip"
MANIFEST = "ab" * 32
ARCHIVE = "cd" * 32


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows))


def _job(project_id, **kw):
    now = datetime.now(timezone.utc)
    run_ids = [str(uuid.uuid4()) for _ in range(3)]
    base = dict(
        id=uuid.uuid4(), project_id=project_id, job_kind="criteria", status="awaiting_collection",
        criteria={"statuses": ["FAILED"]}, counts=None, bytes_reclaimed=None, holds_honoured=None,
        references_broken=None, error=None, requested_at=now - timedelta(hours=1),
        started_at=now - timedelta(minutes=50), finished_at=None, requested_by_id=uuid.uuid4(),
        resolved_run_ids=run_ids, candidate_hash="h", export_tier="evidence", export_target="collect",
        export_bytes=12, export_manifest_sha256=MANIFEST, export_archive_sha256=ARCHIVE,
        export_location=KEY, export_verified_at=now - timedelta(minutes=40),
        export_meta={"run_count": 3, "file_count": 7, "objects_not_archived": 0, "signing": {"state": "unsigned"},
                     "run_ids_excluded": [], "missing_run_ids": [], "estimates": {"tiers": []}},
        collect_expires_at=now + timedelta(hours=71), collected_at=None, acknowledged_at=None,
        acknowledged_by_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Session:
    """Routes by statement text; the job answers only for ITS project."""

    def __init__(self, journal, *, project_id, job=None, project_name="Payments sentinel"):
        self.journal = journal
        self.project_id = project_id
        self.job = job
        self.project = SimpleNamespace(id=project_id, name=project_name)
        self.added: list = []
        self._snapshot = copy.copy(job.__dict__) if job is not None else None

    async def execute(self, stmt, *_a, **_kw):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        if "FROM deletion_jobs" in sql:
            job = self.job
            wanted = {v for v in compiled.params.values() if isinstance(v, uuid.UUID)}
            if job is None or job.id not in wanted:
                return _Result()
            if "deletion_jobs.project_id = %(" in sql and job.project_id not in wanted:
                return _Result()          # another project's job: the same miss as no job
            return _Result([job], scalar=job)
        if "FROM projects" in sql:
            return _Result([(self.project_id,)], scalar=self.project)
        return _Result()

    async def scalar(self, *_a, **_kw):
        return 1

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.journal.append(("flush", ""))

    async def commit(self):
        self.journal.append(("commit", ""))

    async def refresh(self, obj, *_a, **_kw):
        self.journal.append(("refresh", ""))

    async def rollback(self):
        self.journal.append(("rollback", ""))
        self.added.clear()
        if self.job is not None and self._snapshot is not None:
            self.job.__dict__.clear()
            self.job.__dict__.update(copy.copy(self._snapshot))


def _user(role, *, credential=CREDENTIAL_KIND_JWT, bound_project=None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, full_name="Ada Admin", username="ada",
                           email="ada@example.invalid", is_active=True)
    if bound_project is not None:
        _bind_api_key_project(user, bound_project)
    if credential is not None:
        _bind_credential_kind(user, credential)
    return user


def _client(session, user) -> AsyncClient:
    app = FastAPI()
    app.include_router(route.router)

    async def _db():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    async def _current():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = _current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


class _Storage:
    def __init__(self, data=b"PK-archive-bytes", fail=False):
        self.data, self.fail, self.keys = data, fail, []

    async def stream_object(self, key, bucket=None):
        self.keys.append((bucket, key))
        if self.fail:
            raise ConnectionError("object store down")
        yield self.data[:5]
        yield self.data[5:]


@pytest.fixture
def journal():
    return []


@pytest.fixture
def storage(monkeypatch):
    store = _Storage()
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: store)
    return store


@pytest.fixture
def recorded(monkeypatch, journal):
    calls: list[dict] = []

    async def _record(db, **kwargs):
        calls.append({"db": db, **kwargs})
        journal.append(("activity", kwargs["event_type"]))

    monkeypatch.setattr(route, "record_activity", _record)
    return calls


@pytest.fixture
def dispatched(monkeypatch, journal):
    from app.worker import tasks

    calls: list[tuple] = []

    def _delay(*args):
        calls.append(args)
        journal.append(("dispatch", ""))

    monkeypatch.setattr(tasks.execute_criteria_deletion_task, "delay", _delay)
    return calls


def _url(project_id, job_id, action):
    return f"/api/v1/projects/{project_id}/deletion/jobs/{job_id}/{action}"


def _detail(code):
    return {"code": code, "message": EXPORT_ERROR_SENTENCES[code]}


# ── who may collect / acknowledge ─────────────────────────────────────────────


@pytest.mark.parametrize("action", ["collect", "acknowledge"])
@pytest.mark.parametrize("role", ["VIEWER", "QA_LEAD"])
@pytest.mark.asyncio
async def test_below_admin_is_refused(action, role, journal, storage, recorded, dispatched):
    project_id = uuid.uuid4()
    job = _job(project_id)
    session = _Session(journal, project_id=project_id, job=job)
    async with _client(session, _user(role)) as client:
        resp = await client.post(_url(project_id, job.id, action), json={"manifest_sha256": MANIFEST})
    assert resp.status_code == 403 and resp.json()["detail"] == "Requires at least ADMIN role"
    assert job.collected_at is None and job.status == "awaiting_collection"


@pytest.mark.parametrize("action", ["collect", "acknowledge"])
@pytest.mark.asyncio
async def test_a_project_bound_key_and_a_user_key_are_refused(action, journal, storage, recorded, dispatched):
    project_id = uuid.uuid4()
    job = _job(project_id)
    bound = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY, bound_project=project_id)
    async with _client(_Session(journal, project_id=project_id, job=job), bound) as client:
        resp = await client.post(_url(project_id, job.id, action), json={"manifest_sha256": MANIFEST})
    assert resp.status_code == 403
    user_key = _user("ADMIN", credential=CREDENTIAL_KIND_API_KEY)
    async with _client(_Session(journal, project_id=project_id, job=job), user_key) as client:
        resp = await client.post(_url(project_id, job.id, action), json={"manifest_sha256": MANIFEST})
    assert resp.status_code == 403 and resp.json()["detail"] == _detail("interactive_login_required")
    assert storage.keys == [] and recorded == [] and dispatched == []


@pytest.mark.parametrize("action", ["collect", "acknowledge"])
@pytest.mark.asyncio
async def test_another_projects_job_is_the_unknown_id_404(action, journal, storage, recorded, dispatched):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    foreign = _job(theirs)
    answers = []
    for job_id in (foreign.id, uuid.uuid4()):
        async with _client(_Session(journal, project_id=mine, job=foreign), _user("ADMIN")) as client:
            resp = await client.post(_url(mine, job_id, action), json={"manifest_sha256": MANIFEST})
        answers.append((resp.status_code, resp.json()))
    assert answers[0] == answers[1] == (404, {"detail": _detail("job_not_found")})
    assert str(theirs) not in str(answers[0])
    assert foreign.collected_at is None and foreign.status == "awaiting_collection"


# ── collect ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_streams_the_archive_with_both_digests_and_no_key(journal, storage, recorded):
    project_id = uuid.uuid4()
    job = _job(project_id)
    session = _Session(journal, project_id=project_id, job=job)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(_url(project_id, job.id, "collect"))
    assert resp.status_code == 200, resp.text
    assert resp.content == storage.data
    assert resp.headers["x-manifest-sha256"] == MANIFEST
    assert resp.headers["x-archive-sha256"] == ARCHIVE
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"].startswith(
        f'attachment; filename="testlookup-deletion-archive-{str(job.id)[:8]}-'
    )
    assert all("secret-location-sentinel" not in value for value in resp.headers.values())
    assert storage.keys == [("compliance-packs", KEY)]
    assert job.collected_at is not None and ("commit", "") in journal
    (audit,) = [o for o in session.added if isinstance(o, SettingsAuditLog)]
    assert (audit.setting_key, audit.action) == (f"deletion_export:{project_id}", "export_collected")
    assert KEY not in str(audit.changed_fields)
    assert recorded == [], "a download is not a ledger event (# activity: none)"


@pytest.mark.asyncio
async def test_a_failed_first_chunk_is_a_502_and_records_nothing(journal, monkeypatch):
    project_id = uuid.uuid4()
    job = _job(project_id)
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: _Storage(fail=True))
    session = _Session(journal, project_id=project_id, job=job)
    async with _client(session, _user("ADMIN")) as client:
        resp = await client.post(_url(project_id, job.id, "collect"))
    assert resp.status_code == 502 and resp.json()["detail"] == _detail("archive_unreadable")
    assert ("commit", "") not in journal and ("rollback", "") in journal
    assert job.collected_at is None, "the stamp rolled back with the request"
    assert session.added == []


def _state(name):
    """Built inside the test (a window is never a module constant)."""
    if name == "past_window":
        return {"collect_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
    return {"status": name}


@pytest.mark.parametrize("state, code", [
    ("queued", "not_awaiting"),
    ("completed", "not_awaiting"),
    ("expired", "collect_expired"),
    ("past_window", "collect_expired"),
])
@pytest.mark.asyncio
async def test_collect_refusals(state, code, journal, storage):
    project_id = uuid.uuid4()
    job = _job(project_id, **_state(state))
    async with _client(_Session(journal, project_id=project_id, job=job), _user("ADMIN")) as client:
        resp = await client.post(_url(project_id, job.id, "collect"))
    assert resp.status_code == 409 and resp.json()["detail"] == _detail(code)
    assert storage.keys == []


# ── acknowledge ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acknowledge_queues_the_job_with_its_ledger_row_before_the_commit(journal, recorded, dispatched):
    project_id = uuid.uuid4()
    job = _job(project_id, collected_at=datetime.now(timezone.utc) - timedelta(minutes=5),
               export_meta={"run_count": 2, "run_ids_excluded": ["held-run"], "signing": {"state": "signed"}})
    session = _Session(journal, project_id=project_id, job=job)
    user = _user("ADMIN")
    async with _client(session, user) as client:
        resp = await client.post(_url(project_id, job.id, "acknowledge"), json={"manifest_sha256": MANIFEST.upper()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "queued" and body["acknowledged_at"]
    assert body["export_summary"] == {"run_count": 2, "file_count": None, "objects_not_archived": None,
                                      "missing_runs": 0, "excluded_runs": 1, "signing_state": "signed"}
    assert "export_location" not in body and KEY not in resp.text
    assert job.acknowledged_by_id == user.id
    events = [e for e in journal if e[0] in ("activity", "commit", "refresh", "dispatch")]
    assert events[:4] == [("activity", "retention.export_acknowledged"), ("commit", ""), ("refresh", ""),
                          ("dispatch", "")]
    (call,) = recorded
    assert call["db"] is session and call["context"]["count"] == 2, "3 frozen runs - 1 excluded"
    (audit,) = [o for o in session.added if isinstance(o, SettingsAuditLog)]
    assert (audit.setting_key, audit.action) == (f"deletion_export:{project_id}", "export_acknowledged")
    assert dispatched == [(str(job.id), str(project_id), str(job.requested_by_id))]


@pytest.mark.parametrize("state, code", [
    ("expired", "collect_expired"),
    ("past_window", "collect_expired"),
    ("queued", "not_awaiting"),
    ("not_collected", "not_collected"),
    ("wrong_digest", "manifest_mismatch"),
])
@pytest.mark.asyncio
async def test_acknowledge_refusals(state, code, journal, recorded, dispatched):
    project_id = uuid.uuid4()
    collected = {"collected_at": datetime.now(timezone.utc) - timedelta(minutes=5)}
    overrides = {} if state == "not_collected" else collected
    if state not in ("not_collected", "wrong_digest"):
        overrides.update(_state(state))
    job = _job(project_id, **overrides)
    digest = "ef" * 32 if state == "wrong_digest" else MANIFEST
    async with _client(_Session(journal, project_id=project_id, job=job), _user("ADMIN")) as client:
        resp = await client.post(_url(project_id, job.id, "acknowledge"), json={"manifest_sha256": digest})
    assert resp.status_code == 409 and resp.json()["detail"] == _detail(code)
    assert job.acknowledged_at is None and recorded == [] and dispatched == []


@pytest.mark.asyncio
async def test_acknowledge_rejects_a_malformed_digest_at_the_schema(journal, recorded, dispatched):
    project_id = uuid.uuid4()
    job = _job(project_id, collected_at=datetime.now(timezone.utc))
    async with _client(_Session(journal, project_id=project_id, job=job), _user("ADMIN")) as client:
        resp = await client.post(_url(project_id, job.id, "acknowledge"), json={"manifest_sha256": "xyz"})
    assert resp.status_code == 422 and job.status == "awaiting_collection"


# ── execute with an export ────────────────────────────────────────────────────


def _previewed(project_id, *, estimate_tiers=()):
    from app.services.deletion_job_service import candidate_hash

    run_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    return _job(project_id, status="previewed", export_tier=None, export_target=None,
                export_location=None, export_verified_at=None, collect_expires_at=None,
                export_meta={"estimates": {"tiers": list(estimate_tiers)}},
                candidate_hash=candidate_hash(run_ids), resolved_run_ids=run_ids)


@pytest.mark.parametrize("choice, status, code", [
    ({"tier": "everything", "target": "collect"}, 422, "unknown_tier"),
    ({"tier": "summary", "target": "external"}, 422, "unknown_target"),
    ({"tier": "full", "target": "retain"}, 409, "over_cap"),
])
@pytest.mark.asyncio
async def test_execute_refusals_leave_the_job_previewed(choice, status, code, journal, dispatched):
    from app.services.deletion_job_service import candidate_hash

    project_id = uuid.uuid4()
    job = _previewed(project_id, estimate_tiers=[
        {"tier": "full", "archive_bytes": 10**12, "measured": True, "over_cap": True},
    ])
    job.candidate_hash = candidate_hash(job.resolved_run_ids)
    session = _Session(journal, project_id=project_id, job=job)
    body = {"job_id": str(job.id), "confirmation_name": "Payments sentinel", "export": choice}
    async with _client(session, _user("ADMIN", credential=None)) as client:
        resp = await client.post(f"/api/v1/projects/{project_id}/deletion/execute", json=body)
    assert resp.status_code == status, resp.text
    assert resp.json()["detail"] == _detail(code)
    assert job.status == "previewed" and job.export_tier is None, "the claim rolled back"
    assert ("commit", "") not in journal and dispatched == []


@pytest.mark.asyncio
async def test_execute_with_an_export_queues_it_and_echoes_the_choice(journal, dispatched):
    from app.services.deletion_job_service import candidate_hash

    project_id = uuid.uuid4()
    job = _previewed(project_id)
    job.candidate_hash = candidate_hash(job.resolved_run_ids)
    session = _Session(journal, project_id=project_id, job=job)
    body = {"job_id": str(job.id), "confirmation_name": "Payments sentinel",
            "export": {"tier": "evidence", "target": "collect"}}
    async with _client(session, _user("ADMIN", credential=None)) as client:
        resp = await client.post(f"/api/v1/projects/{project_id}/deletion/execute", json=body)
    assert resp.status_code == 202, resp.text
    assert resp.json()["export_tier"] == "evidence" and resp.json()["export_target"] == "collect"
    assert (job.status, job.export_tier, job.export_target) == ("queued", "evidence", "collect")
    (audit,) = [o for o in session.added if isinstance(o, SettingsAuditLog)]
    assert audit.action == "export_requested" and audit.changed_fields["run_count"] == 2
    assert len(dispatched) == 1


# ── the criteria preview carries (and freezes) the estimate ─────────────────


@pytest.mark.asyncio
async def test_the_preview_estimates_the_deletable_runs_and_freezes_the_estimate(mocker):
    from unittest.mock import AsyncMock

    from app.models.postgres import LaunchStatus
    from app.services import retention_export_service

    project_id, ok_run, busy_run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    estimate = {
        "run_count": 1, "exportable": True, "refusal": None, "reclaim_bytes": 900,
        "reclaim_object_bytes": 800, "reclaim_mongo_bytes": 100, "reclaim_measured": True,
        "tiers": [{"tier": t, "archive_bytes": 10, "measured": True, "over_cap": False}
                  for t in ("summary", "evidence", "full")],
        "max_bytes": 536_870_912, "collect_ttl_hours": 72,
    }
    estimated = mocker.patch.object(retention_export_service, "estimate_export", AsyncMock(return_value=estimate))
    storage = object()
    mocker.patch("app.db.storage.get_storage_provider", mocker.Mock(return_value=storage))
    mocker.patch("app.db.mongo.get_mongo_db", mocker.Mock(return_value={}))
    mocker.patch("app.services.deletion_criteria.foreign_run_ids", AsyncMock(return_value=[]))
    mocker.patch("app.services.deletion_criteria.resolve_criteria_candidates",
                 AsyncMock(return_value=[ok_run, busy_run]))
    mocker.patch("app.services.run_deletion_service.citation_blockers", AsyncMock(return_value=[]))
    frozen = mocker.patch("app.services.deletion_job_service.stage_frozen_candidate_set",
                          mocker.Mock(return_value=uuid.uuid4()))

    class _DB:
        async def execute(self, *_a, **_kw):
            return _Result([(ok_run, LaunchStatus.PASSED, f"uploads/{project_id}/{ok_run}/"),
                            (busy_run, LaunchStatus.IN_PROGRESS, None)])

        async def commit(self):
            return None

    response = await route.preview_criteria_deletion(
        project_id=project_id, criteria=SimpleNamespace(run_ids=None, model_dump=lambda **_k: {}),
        db=_DB(), current_user=SimpleNamespace(id=uuid.uuid4()), _=SimpleNamespace(),
    )

    kwargs = estimated.await_args.kwargs
    assert kwargs["run_ids"] == [ok_run], "estimated over the DELETABLE runs only"
    assert kwargs["prefixes"] == {ok_run: f"uploads/{project_id}/{ok_run}/"} and kwargs["storage"] is storage
    assert frozen.call_args.kwargs["export_estimates"] == estimate
    assert response.export_estimate.reclaim_bytes == 900
    assert response.export_estimate.note == "Archive, not a backup — it cannot be re-imported."
