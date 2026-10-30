"""W23d AGT-14 — captured HTTP exchanges on a real PostgreSQL + MongoDB (contract §6.3 G-B).

What a fake session / collection cannot show:

1. **The REAL write path redacts.** A JSON batch carrying the §2.1 P-2 real-looking samples goes
   through the router's own model and dump (``IngestPayload(...).model_dump()`` → JSON, the queued
   object-storage bytes), then the batch task's sequence: ``create_run_from_payload`` →
   ``ingest_test_results`` → commit → ``store_batch_http_exchanges``. The Mongo docs carry
   ``[REDACTED]`` where every sample was, and no raw sample appears in the stored JSON, the queued
   bytes, the GET answer or a log line.
2. **The GET through the ASGI app.** A VIEWER member reads the list; a non-member is refused by the
   run guard; another run's case id is a 404.
3. **Cross-run drift in the real stage.** Two runs (A with ``price``, B without) →
   ``workflow.contract_validation_node`` on B's state → one ``schema_drift`` violation on ``price``
   naming A's build, and ``build_decision_intelligence`` carries it.
4. **Retention.** A run past the raw-events cutoff loses its docs; the same under a RUN hold (a real
   ``retention_holds`` row) keeps them; ``perform_run_deletion`` removes them.
5. **The ``full`` export member** of a stored run reads ``[REDACTED]`` and holds no raw sample.

The fixture awaits ``dispose_engine_for_loop()`` before building its engine and after disposing it,
removes every row and doc it made, calls ``close_mongo()`` (this module binds Mongo) and closes Redis
ONLY if the ASGI request path bound the pool. Every instant is built inside its test. Requires
``TESTLOOKUP_POSTGRES_TEST_DSN`` (= ``DATABASE_URL``) at head (0246) and the app's Mongo. To be listed
in the Mongo-backed integration list of ``.github/workflows/ci.yml`` beside
``test_decision_report_supersession_postgres_mongo.py`` (contract §6.4).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    if not os.environ.get("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL must point the app at the same database")
    return value


def _samples() -> dict[str, str]:
    """The §2.1 P-2 REAL-LOOKING samples (non-hex, never a real secret), built here."""
    return {
        "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ3MjNkLXNhbXBsZSJ9.c2lnbmF0dXJlLXNhbXBsZS12YWx1ZS0x",
        "basic": "dzIzZDpzYW1wbGUtcGFzcw==",
        "cookie": "w23dSampleSessionValue",
        "csrf": "w23dcsrfSample",
        "set_cookie": "w23dSidSample",
        "api_key": "tlk_w23d_sample_key_0123456789abcdef",
        "proxy": "w23dProxySampleCred",
        "amz": "w23dAmzSecuritySample",
        "github": "w23dGithubHeaderSample",
        "pat": "ghp_" + "W23dSampleTokenValueKlmnopQrstuvWxyz",   # ghp_ + 36
        "password": "W23dSamplePasswordValue",
        "client_secret": "w23dClientSecretSample",
        "api_key_body": "w23dApiKeyBodySample",
        "email": "w23d.sample@example.test",
        "access_token": "w23dSampleAccessToken123",
        "sig": "w23dSigSample",
        "userinfo": "w23dSamplePass",
        "query_token": "w23dTextTokenSample",
    }


def _no_sample(blob: Any) -> None:
    text_blob = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    leaked = [name for name, value in _samples().items() if value in text_blob]
    assert leaked == [], leaked


def _exchanges(*, price: bool) -> list[dict[str, Any]]:
    s = _samples()
    product: dict[str, Any] = {"id": 7, "name": "lamp", "note": f"see {s['pat']}", "owner": s["email"]}
    if price:
        product["price"] = 9.5
    return [
        {
            "method": "get",
            "url": f"https://w23d:{s['userinfo']}@api.example.test/api/products/7"
                   f"?access_token={s['access_token']}&page=2&sig={s['sig']}",
            "status": 200, "duration_ms": 41,
            "request": {"headers": {
                "Authorization": f"Bearer {s['jwt']}", "Cookie": f"session={s['cookie']}; csrftoken={s['csrf']}",
                "X-API-Key": s["api_key"], "Proxy-Authorization": f"Basic {s['proxy']}",
                "X-Amz-Security-Token": s["amz"], "X-GitHub-Token": s["github"], "Accept": "application/json",
            }},
            "response": {"headers": {"Set-Cookie": f"sid={s['set_cookie']}; HttpOnly", "Content-Type": "application/json"},
                         "body": json.dumps(product)},
        },
        {
            "method": "POST", "url": "https://api.example.test/api/login", "endpoint": "/api/login", "status": 200,
            "request": {"headers": {"Authorization": f"Basic {s['basic']}"},
                        "body": json.dumps({"username": "w23d", "password": s["password"],
                                            "client_secret": s["client_secret"], "apiKey": s["api_key_body"]})},
            "response": {"headers": {"Content-Type": "text/plain"}, "body": f"ok token={s['query_token']}"},
        },
    ]


class _Recorder:
    """Stands in for a module's ``_slog`` (the cached-logger lesson): every event + fields."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def __getattr__(self, level: str):
        return lambda event, **kw: self.lines.append({"level": level, "event": event, **kw})


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import postgres as app_postgres
    from app.db import redis_client
    from app.db.mongo import Collections, close_mongo, get_mongo_db

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    made = SimpleNamespace(projects=[], users=[], runs=[], pipelines=[])
    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, made=made)
    finally:
        try:
            mongo = get_mongo_db()
            runs = [str(r) for r in made.runs]
            await mongo[Collections.REST_API_PAYLOADS].delete_many({"test_run_id": {"$in": runs}})
            await mongo["pipeline_event_log"].delete_many({"pipeline_run_id": {"$in": [str(p) for p in made.pipelines]}})
        finally:
            async with sessions() as db:
                if made.projects:
                    await db.execute(text("DELETE FROM retention_holds WHERE project_id = ANY(:p)"), {"p": made.projects})
                    await db.execute(text("DELETE FROM projects WHERE id = ANY(:p)"), {"p": made.projects})
                if made.users:
                    await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": made.users})
                await db.commit()
            await engine.dispose()
            if redis_client._pool is not None:  # only if the ASGI request path bound it
                await redis_client.close_redis()
            await app_postgres.dispose_engine_for_loop()
            await close_mongo()


async def _world(env) -> SimpleNamespace:
    """A project, a VIEWER member and a VIEWER of nothing, each with an access token."""
    from app.models.postgres import Project, ProjectMember, ProjectRetentionPolicy, User, UserRole
    from app.services.auth_session_tokens import issue_access_jwt

    pid, member, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with env.sessions() as db:
        db.add(Project(id=pid, name=f"gb-http-{pid.hex[:8]}", slug=f"gb-http-{pid.hex[:8]}", is_active=True))
        for uid, name in ((member, "member"), (outsider, "outsider")):
            db.add(User(id=uid, username=f"gb-{name}-{uid.hex[:8]}", email=f"gb-{uid.hex[:8]}@example.test",
                        hashed_password="!unusable", role=UserRole.VIEWER, is_active=True))
        await db.flush()
        db.add(ProjectMember(project_id=pid, user_id=member, role=UserRole.VIEWER))
        db.add(ProjectRetentionPolicy(project_id=pid, enabled=True, raw_events_days=7, runs_days=365,
                                      artifacts_days=365, audit_days=365, keep_last_runs=0))
        await db.commit()
        member_token = await issue_access_jwt(db, str(member), timedelta(minutes=10))
        outsider_token = await issue_access_jwt(db, str(outsider), timedelta(minutes=10))
    env.made.projects.append(pid)
    env.made.users.extend([member, outsider])
    return SimpleNamespace(
        pid=pid,
        member={"Authorization": f"Bearer {member_token}"},
        outsider={"Authorization": f"Bearer {outsider_token}"},
    )


async def _ingest(env, pid: uuid.UUID, build: str, *, price: bool, run_id: str | None = None) -> SimpleNamespace:
    """The router's model + dump, then the batch task's own sequence (no Celery)."""
    from app.models.schemas import IngestPayload
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        ingest_test_results,
        store_batch_http_exchanges,
    )

    model = IngestPayload(project_id=str(pid), build_number=build, run_ai=False, results=[
        {"test_name": "test_product_price", "class_name": "tests.test_products", "status": "FAILED",
         "error_message": "AssertionError: price missing", "http_exchanges": _exchanges(price=price)},
        {"test_name": "test_health", "status": "PASSED"},
    ])
    queued = json.dumps(model.model_dump(mode="json"), separators=(",", ":"))   # routers/ingest.py's object-storage bytes
    payload = json.loads(queued)
    async with env.sessions() as db:
        run = await create_run_from_payload(
            db, project_id=str(pid), build_number=build, ingestion_source="sdk", run_id=run_id,
        )
        await ingest_test_results(db, run, payload["results"])
        await db.commit()
        counts = await store_batch_http_exchanges(db, run, payload["results"])
        case_id = (await db.execute(text(
            "SELECT id FROM test_cases WHERE test_run_id = :r AND test_name = 'test_product_price'"
        ), {"r": run.id})).scalar_one()
    env.made.runs.append(run.id)
    return SimpleNamespace(run_id=run.id, case_id=case_id, queued=queued, counts=counts, build=build)


async def _docs(run_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.db.mongo import Collections, get_mongo_db

    return await get_mongo_db()[Collections.REST_API_PAYLOADS].find(
        {"test_run_id": str(run_id)}, {"_id": 0},
    ).to_list(length=None)


async def _get(headers: dict[str, str], run_id, case_id):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gb-http.test") as client:
        return await client.get(f"/api/v1/runs/{run_id}/tests/{case_id}/http-exchanges", headers=headers)


# ── 1 + 2: the real write path redacts; the GET as member / non-member / another run's case ──


async def test_the_real_batch_path_stores_only_redacted_exchanges_and_the_get_scopes_them(env, monkeypatch):
    from app.services import http_exchange_store, ingestion_pipeline

    store_log, pipeline_log = _Recorder(), _Recorder()
    monkeypatch.setattr(http_exchange_store, "_slog", store_log)
    monkeypatch.setattr(ingestion_pipeline, "logger", pipeline_log)
    world = await _world(env)
    run = await _ingest(env, world.pid, f"gb-http-{uuid.uuid4().hex[:6]}", price=True)

    _no_sample(run.queued)                                     # (a) the queued object-storage bytes
    assert run.counts.stored == 2 and run.counts.write_failed is False
    docs = sorted(await _docs(run.run_id), key=lambda d: d["seq"])
    _no_sample(docs)                                           # (b) the Mongo docs
    product, login = docs
    assert (product["method"], product["endpoint"]) == ("GET", "/api/products/{id}")
    assert (product["project_id"], product["test_case_id"]) == (str(world.pid), str(run.case_id))
    for name in ("Authorization", "Cookie", "X-API-Key", "Proxy-Authorization", "X-Amz-Security-Token", "X-GitHub-Token"):
        assert product["request"]["headers"][name] == "[REDACTED]", name
    assert product["request"]["headers"]["Accept"] == "application/json"
    assert product["response"]["headers"]["Set-Cookie"] == "[REDACTED]"
    assert "page=2" in product["url"] and "[REDACTED]" in product["url"]
    assert product["response_schema_state"] == "parsed" and product["response_schema"]["price"] == "number"
    assert json.loads(login["request"]["body"])["password"] == "[REDACTED]"
    assert login["response_schema_state"] == "not_json"
    assert all(sum(d["redaction_counts"].values()) > 0 for d in docs)

    member = await _get(world.member, run.run_id, run.case_id)  # (c) the GET answer
    assert member.status_code == 200, member.text
    body = member.json()
    assert body["total"] == 2 and [i["seq"] for i in body["items"]] == [0, 1]
    assert body["items"][0]["request"]["headers"]["Authorization"] == "[REDACTED]" and body["items"][0]["redacted"]
    _no_sample(member.text)

    outsider = await _get(world.outsider, run.run_id, run.case_id)
    assert outsider.status_code == 404 and outsider.json()["detail"] == "Test run not found"
    other = await _ingest(env, world.pid, f"gb-http-{uuid.uuid4().hex[:6]}", price=True)
    crossed = await _get(world.member, other.run_id, run.case_id)
    assert crossed.status_code == 404 and crossed.json()["detail"] == "Test case not found"

    lines = store_log.lines + pipeline_log.lines              # (e) every captured log line
    assert any(line["event"] == "http_exchanges_stored" and line["stored"] == 2 for line in lines)
    _no_sample(lines)


async def test_a_re_delivery_of_the_same_build_adds_no_duplicate_docs(env):
    world = await _world(env)
    build = f"gb-redo-{uuid.uuid4().hex[:6]}"
    first = await _ingest(env, world.pid, build, price=True)
    again = await _ingest(env, world.pid, build, price=True, run_id=str(first.run_id))   # a Celery retry
    assert again.run_id == first.run_id and again.counts.stored == 2
    assert len(await _docs(first.run_id)) == 2


# ── 3: the contract stage on captured exchanges, through the real node ───────


async def test_contract_stage_on_captured_exchanges(env):
    from app.agents import workflow
    from app.agents.decision_report_agent import build_decision_intelligence

    world = await _world(env)
    run_a = await _ingest(env, world.pid, f"gb-a-{uuid.uuid4().hex[:6]}", price=True)
    run_b = await _ingest(env, world.pid, f"gb-b-{uuid.uuid4().hex[:6]}", price=False)
    pipeline = uuid.uuid4()
    async with env.sessions() as db:
        await db.execute(text(
            "INSERT INTO agent_pipeline_runs (id, test_run_id, workflow_type, status, spawn_depth, execution_metadata) "
            "VALUES (:id, :run, 'deep', 'running', 0, CAST('{}' AS json))"
        ), {"id": pipeline, "run": run_b.run_id})
        await db.commit()
    env.made.pipelines.append(pipeline)
    state = {
        "pipeline_run_id": str(pipeline), "project_id": str(world.pid), "test_run_id": str(run_b.run_id),
        "failed_test_ids": [str(run_b.case_id)], "contract_agent_enabled": True,
    }
    delta = await workflow.contract_validation_node(state)
    findings = delta["contract_findings"]
    assert findings["status"] == "complete", findings
    (violation,) = findings["violations"]
    assert (violation["violation_type"], violation["field_path"]) == ("schema_drift", "price")
    assert (violation["method"], violation["endpoint"]) == ("GET", "/api/products/{id}")
    assert violation["baseline_build"] == run_a.build
    assert findings["summary"].endswith(f"drift since build {run_a.build}")
    report = build_decision_intelligence({**state, **delta})
    assert [v["field_path"] for v in report["contract_findings"]["violations"]] == ["price"]


# ── 4: retention (raw-events clock, a run hold) and the single-run delete ────


class _Storage:
    """No MinIO in CI: an empty object store that records nothing it is not asked."""

    async def list_objects(self, *_a, **_kw):
        return []

    async def delete_prefix(self, *_a, **_kw):
        return 0

    async def delete_object(self, *_a, **_kw):
        return None


async def _age(env, run_id, created_at: datetime) -> None:
    async with env.sessions() as db:
        await db.execute(text("UPDATE test_runs SET created_at = :at, status = 'FAILED' WHERE id = :id"),
                         {"at": created_at, "id": run_id})
        await db.commit()


async def test_retention_purges_by_run_keeps_a_held_run_and_delete_run_removes_them(env, monkeypatch):
    from app.db.mongo import get_mongo_db
    from app.services import agent_memory_service, analysis_cache_retention, retention_service, semantic_search
    from app.services.run_deletion_service import perform_run_deletion

    async def _caches(project_id, *, cutoff, execute):
        return {"redis": None, "semantic": None}

    async def _search(project_id, *, execute):
        return None

    async def _vectors(project_id, ids):
        return len(ids)

    monkeypatch.setattr(analysis_cache_retention, "purge_project_analysis_caches", _caches)
    monkeypatch.setattr(semantic_search, "purge_project_documents", _search)
    monkeypatch.setattr(agent_memory_service, "purge_memory_vectors", _vectors)
    world = await _world(env)
    now = datetime.now(timezone.utc)
    held = await _ingest(env, world.pid, f"gb-held-{uuid.uuid4().hex[:6]}", price=True)
    aged = await _ingest(env, world.pid, f"gb-aged-{uuid.uuid4().hex[:6]}", price=True)
    fresh = await _ingest(env, world.pid, f"gb-fresh-{uuid.uuid4().hex[:6]}", price=True)
    await _age(env, held.run_id, now - timedelta(days=40))   # older than `aged`: never its baseline
    await _age(env, aged.run_id, now - timedelta(days=30))
    async with env.sessions() as db:
        await db.execute(text(
            "INSERT INTO retention_holds (id, project_id, entity_type, entity_id, entity_label, reason, placed_at) "
            "VALUES (:i, :p, 'run', :e, 'gb label', 'Audit sentinel', :n)"
        ), {"i": uuid.uuid4(), "p": world.pid, "e": held.run_id, "n": now})
        await db.commit()

    async with env.sessions() as db:
        await retention_service.run_purge(db, project_id=world.pid, mode="execute", now=now,
                                          mongo=get_mongo_db(), storage=_Storage())
        await db.commit()
    assert await _docs(aged.run_id) == []
    assert len(await _docs(held.run_id)) == 2
    assert len(await _docs(fresh.run_id)) == 2

    async with env.sessions() as db:
        await perform_run_deletion(
            db, run=SimpleNamespace(id=fresh.run_id, project_id=world.pid, minio_prefix=None),
            mongo=get_mongo_db(), storage=_Storage(), search_index_documents=None, reason="gb integration",
        )
        await db.commit()
    assert await _docs(fresh.run_id) == []
    assert len(await _docs(held.run_id)) == 2


# ── 5: the `full` export member ──────────────────────────────────────────────


async def test_the_full_export_member_reads_redacted(env):
    from app.db.mongo import get_mongo_db
    from app.services.retention_export_content import run_members

    world = await _world(env)
    run = await _ingest(env, world.pid, f"gb-export-{uuid.uuid4().hex[:6]}", price=True)
    scope = SimpleNamespace(tier="full", project_id=world.pid, run_ids=[run.run_id])
    members: dict[str, bytes] = {}
    async with env.sessions() as db:
        async for name, data in run_members(db, scope, mongo=get_mongo_db(), storage=_Storage(), deletion={}):
            members[name] = data
    member = members[f"raw/{run.run_id}/rest_api_payloads.jsonl"].decode("utf-8")
    docs = [json.loads(line) for line in member.splitlines()]
    assert len(docs) == 2
    assert {d["request"]["headers"]["Authorization"] for d in docs} == {"[REDACTED]"}
    _no_sample(member)
