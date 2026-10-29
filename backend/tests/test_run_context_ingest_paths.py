"""W23b FAIL-13: every ingest path carries a VALIDATED, REDACTED run context, or refuses it.

The four paths of contract §2.2: ``POST /ingest`` (object storage), ``POST /ingest/file``
(the Celery kwargs, bound against the REAL task signature -- a mocked ``apply_async``
hides Celery's argument check), ``create_run_from_payload`` (create / reuse / resume,
fill-if-null), and the live paths (the ``create_session`` stub, ``_with_ci_context``,
the ``/stream/ingest`` auto-session, ``upsert_test_run``). Secrets are REAL-LOOKING
samples built inside the tests; the word "sentinel" marks the secret part, so no other
string in this module carries it.
"""
from __future__ import annotations

import inspect
import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("asyncpg")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.deps import (  # noqa: E402
    StreamingApiKeyContext,
    get_api_key_context,
    get_current_active_user,
    get_current_user,
    get_current_user_or_api_key,
    get_db,
    get_streaming_api_key_context,
)
from app.main import app  # noqa: E402
from app.services import run_context_service  # noqa: E402
from app.services.run_context_vocab import (  # noqa: E402
    CONTEXT_MAX_BYTES,
    ENV_ALLOWLIST,
    LOCKFILE_NAMES,
    REDACTED,
    RUN_CONTEXT_ERRORS,
)

pytestmark = pytest.mark.asyncio

_JUNIT = (
    b'<?xml version="1.0"?><testsuite name="s" tests="1">'
    b'<testcase name="checkout_flow" classname="pay"/></testsuite>'
)
_ONE_EVENT = [{"event_type": "test_result", "test_name": "checkout_flow", "status": "PASSED"}]


def _secret_env() -> dict[str, str]:
    """REAL-LOOKING samples (S-4) on allowlisted keys; never hex-only."""
    return {
        "BUILD_IMAGE": "ghp_" + "Sentinel" * 5,
        "NODE_ENV": "glpat-" + "SentinelSent1234",
        "CI_SERVER_VERSION": "xoxb-" + "1234-Sentinel",
        "ImageOS": "AKIA" + "SENTINELSENTINEL",
        "ImageVersion": "https://deploy:Hunter2Sentinel@registry.example.com",
        "RUNNER_OS": "Bearer " + "Sentinel" * 4,
        "LC_ALL": "token=SentinelValue1",
        "TZ": "Europe/Berlin",
    }


def _context() -> dict[str, Any]:
    return {
        "v": 1,
        "runtime": {"python": "3.11.9"},
        "dependency_fingerprints": {"poetry.lock": "0f" * 32},
        "env": _secret_env(),
    }


def _no_secret(blob: Any) -> None:
    text = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    assert "sentinel" not in text.lower(), text
    assert "hunter2" not in text.lower(), text


def _refusals() -> list[tuple[str, dict[str, Any], str]]:
    """(name, run_context, the 422 sentence) -- §6.3 G-C's forbidden inputs."""
    images = {f"app{i}": "sha256:" + "0f" * 32 for i in range(9)}
    runtime = {f"tool{i:02d}": "1.0.0" for i in range(17)}
    oversized = {
        "v": 1,
        "image_digests": {f"img{i}/" + "a" * 195 + ":" + "t" * 128: "sha256:" + "0f" * 32 for i in range(8)},
        "runtime": {f"tool{i:02d}": "1." + "0" * 60 for i in range(16)},
        "dependency_fingerprints": {name: "0f" * 32 for name in LOCKFILE_NAMES},
        "env": {key: ("label text " * 19)[:200] for key in ENV_ALLOWLIST},
    }
    # Every section within its own cap; only the whole object is over 8 192 bytes.
    assert len(json.dumps(oversized, separators=(",", ":")).encode()) > CONTEXT_MAX_BYTES
    return [
        ("env-key", {"v": 1, "env": {"HOME": "/home/ci"}},
         RUN_CONTEXT_ERRORS["env_key"].format(keys=", ".join(ENV_ALLOWLIST))),
        ("lockfile-name", {"v": 1, "dependency_fingerprints": {"poetry.lock.bak": "0f" * 32}},
         RUN_CONTEXT_ERRORS["lockfile_name"]),
        ("runtime-17", {"v": 1, "runtime": runtime},
         RUN_CONTEXT_ERRORS["too_many"].format(section="runtime", max=16)),
        ("images-9", {"v": 1, "image_digests": images},
         RUN_CONTEXT_ERRORS["too_many"].format(section="image_digests", max=8)),
        ("too-large", oversized, RUN_CONTEXT_ERRORS["too_large"]),
    ]


class _Storage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(self, key, content, content_type="application/json", bucket=None):
        self.objects[key] = content if isinstance(content, bytes) else str(content).encode()

    async def delete_object(self, key, bucket=None):
        return None

    async def get_object_content(self, key, bucket=None):
        return self.objects[key]


@pytest.fixture
def harness(monkeypatch):
    """The real app, with auth / DB / storage / broker / Redis seams replaced."""
    project_id = uuid.uuid4()
    user = SimpleNamespace(
        id=uuid.uuid4(), username="qa_runner", email="qa@example.com", full_name="QA",
        role="QA_ENGINEER", is_active=True, must_change_password=False,
    )
    storage = _Storage()
    state: dict[str, Any] = {"project_id": project_id, "storage": storage, "file_kwargs": [], "batch_kwargs": []}

    async def _db():
        yield AsyncMock()

    async def _user():
        return user

    async def _api_key_ctx():
        return user, None

    async def _streaming_ctx():
        return StreamingApiKeyContext(
            user=user, project_id=project_id, api_key_id=uuid.uuid4(), api_key_name="ci-key",
        )

    overrides = {
        get_db: _db, get_current_user: _user, get_current_active_user: _user,
        get_current_user_or_api_key: _user, get_api_key_context: _api_key_ctx,
        get_streaming_api_key_context: _streaming_ctx,
    }
    app.dependency_overrides.update(overrides)

    async def _open(*_a, **_kw):
        return None

    from app.worker import tasks

    # Kept BEFORE the patch: the router's real kwargs are bound against it.
    state["real_file_task"] = tasks.ingest_uploaded_file
    state["real_batch_task"] = tasks.ingest_uploaded_results
    file_task = MagicMock()
    file_task.apply_async = lambda *, kwargs, task_id: state["file_kwargs"].append(kwargs) or SimpleNamespace(id=task_id)
    batch_task = MagicMock()
    batch_task.delay = lambda **kw: state["batch_kwargs"].append(kw) or SimpleNamespace(id="task-batch")
    monkeypatch.setattr("app.services.ingestion_backpressure.enforce_redis_memory_backpressure", _open)
    monkeypatch.setattr("app.services.ingestion_rate_limit.enforce_ingest_rate_limit", _open)
    monkeypatch.setattr("app.routers.stream.enforce_redis_memory_backpressure", _open, raising=False)
    monkeypatch.setattr("app.routers.stream.enforce_ingest_rate_limit", _open, raising=False)
    monkeypatch.setattr("app.routers.ingest.resolve_project_scope", AsyncMock())
    monkeypatch.setattr("app.routers.ingest.record_activity", AsyncMock())
    monkeypatch.setattr("app.routers.ingest._resolve_run_id", AsyncMock(return_value=str(uuid.uuid4())))
    monkeypatch.setattr("app.services.upload_status.set_status", AsyncMock())
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)
    monkeypatch.setattr("app.worker.tasks.ingest_uploaded_file", file_task)
    monkeypatch.setattr("app.worker.tasks.ingest_uploaded_results", batch_task)
    try:
        yield state
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)


async def _post(path: str, **kwargs: Any):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post(path, **kwargs)


def _batch(project_id: uuid.UUID, run_context: Any) -> dict[str, Any]:
    return {
        "project_id": str(project_id), "build_number": "b-ctx-1",
        "results": [{"test_name": "checkout_flow", "status": "PASSED"}],
        "run_context": run_context,
    }


def _form(project_id: uuid.UUID, run_context: Any = None) -> dict[str, Any]:
    data = {"project_id": str(project_id), "build_number": "b-ctx-2", "format": "junit"}
    if run_context is not None:
        data["run_context"] = run_context if isinstance(run_context, str) else json.dumps(run_context)
    return data


def _loc_names_run_context(body: dict[str, Any], *prefix: str) -> bool:
    want = ("body", *prefix, "run_context")
    return any(tuple(err["loc"][: len(want)]) == want for err in body["detail"])


# ── POST /api/v1/ingest (JSON) ───────────────────────────────────────────────


async def test_json_ingest_stores_only_the_redacted_context_in_object_storage(harness):
    resp = await _post("/api/v1/ingest", json=_batch(harness["project_id"], _context()))
    assert resp.status_code == 202, resp.text
    (stored,) = harness["storage"].objects.values()
    _no_secret(stored.decode())
    payload = json.loads(stored)
    assert payload["run_context"]["env"]["BUILD_IMAGE"] == REDACTED
    assert payload["run_context"]["env"]["ImageVersion"] == "https://deploy:[REDACTED]@registry.example.com"
    assert payload["run_context"]["env"]["TZ"] == "Europe/Berlin"
    assert payload["run_context"]["runtime"] == {"python": "3.11.9"}
    _no_secret(harness["batch_kwargs"])


@pytest.mark.parametrize("case", _refusals(), ids=lambda c: c[0])
async def test_json_ingest_refuses_a_malformed_context_with_its_sentence(harness, case):
    _name, ctx, sentence = case
    resp = await _post("/api/v1/ingest", json=_batch(harness["project_id"], ctx))
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert _loc_names_run_context(body)
    assert any(err["msg"] == "Value error, " + sentence for err in body["detail"]), body
    assert harness["storage"].objects == {} and harness["batch_kwargs"] == []


# ── POST /api/v1/ingest/file (multipart) ─────────────────────────────────────


class _LogRecorder:
    """Stands in for a module's structlog logger: records (level, event, fields)."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str):
        return lambda event, *args, **kw: self.lines.append((level, str(event), kw))


async def test_no_ingest_log_line_carries_a_context_value(harness, monkeypatch):
    recorder, service_recorder = _LogRecorder(), _LogRecorder()
    monkeypatch.setattr("app.routers.ingest.logger", recorder)
    monkeypatch.setattr(run_context_service, "_slog", service_recorder)
    files = {"file": ("r.xml", _JUNIT, "application/xml")}
    await _post("/api/v1/ingest", json=_batch(harness["project_id"], _context()))
    await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"], _context()))
    await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"], {"v": 1, "env": {"HOME": "x"}}))
    events = [event for _level, event, _kw in recorder.lines]
    assert "batch_ingest_accepted" in events and "file_ingest_accepted" in events
    _no_secret(repr(recorder.lines) + repr(service_recorder.lines))
    assert "Europe/Berlin" not in repr(recorder.lines)  # not even an allowlisted plain value


async def test_file_ingest_queues_the_redacted_context_and_the_real_task_binds_it(harness):
    files = {"file": ("r.xml", _JUNIT, "application/xml")}
    resp = await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"], _context()))
    assert resp.status_code == 202, resp.text
    (kwargs,) = harness["file_kwargs"]
    _no_secret(kwargs)
    assert kwargs["run_context"]["env"]["BUILD_IMAGE"] == REDACTED
    assert kwargs["run_context"]["env"]["TZ"] == "Europe/Berlin"
    assert kwargs["run_context"]["dependency_fingerprints"] == {"poetry.lock": "0f" * 32}
    # The broker argument check a mocked apply_async hides: the REAL task binds
    # exactly the kwargs the router queued (and JSON-serialises them).
    bound = inspect.signature(harness["real_file_task"].run).bind(**kwargs)
    assert bound.arguments["run_context"] == kwargs["run_context"]
    json.dumps(kwargs)


async def test_file_ingest_without_a_context_queues_no_run_context_key(harness):
    files = {"file": ("r.xml", _JUNIT, "application/xml")}
    resp = await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"]))
    assert resp.status_code == 202, resp.text
    (kwargs,) = harness["file_kwargs"]
    assert "run_context" not in kwargs  # a worker that predates the field still binds
    inspect.signature(harness["real_file_task"].run).bind(**kwargs)


@pytest.mark.parametrize("case", _refusals(), ids=lambda c: c[0])
async def test_file_ingest_refuses_an_invalid_context_before_storage_or_broker(harness, case):
    _name, ctx, sentence = case
    files = {"file": ("r.xml", _JUNIT, "application/xml")}
    resp = await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"], ctx))
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == {"code": "invalid_run_context", "message": sentence}
    assert harness["storage"].objects == {} and harness["file_kwargs"] == []


@pytest.mark.parametrize("raw", ["{run_context", "[1, 2]", '"poetry.lock"', "42"])
async def test_file_ingest_refuses_a_value_that_is_not_a_json_object(harness, raw):
    files = {"file": ("r.xml", _JUNIT, "application/xml")}
    resp = await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"], raw))
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == {"code": "bad_json", "message": RUN_CONTEXT_ERRORS["bad_json"]}
    assert harness["storage"].objects == {} and harness["file_kwargs"] == []


async def test_file_ingest_caps_the_form_field_at_16384_characters(harness):
    files = {"file": ("r.xml", _JUNIT, "application/xml")}
    resp = await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"], "x" * 16385))
    assert resp.status_code == 422, resp.text
    assert _loc_names_run_context(resp.json())
    assert harness["file_kwargs"] == []


async def test_file_ingest_strips_a_nul_from_the_context_field(harness):
    raw = json.dumps({"v": 1, "env": {"TZ": "Europe/Berlin"}}).replace("Berlin", "Ber\x00lin")
    files = {"file": ("r.xml", _JUNIT, "application/xml")}
    resp = await _post("/api/v1/ingest/file", files=files, data=_form(harness["project_id"], raw))
    assert resp.status_code == 202, resp.text
    assert harness["file_kwargs"][0]["run_context"]["env"] == {"TZ": "Europe/Berlin"}


# ── POST /api/v1/stream/sessions and /api/v1/stream/ingest (the live routes) ─


@pytest.mark.parametrize("case", _refusals(), ids=lambda c: c[0])
async def test_the_live_routes_refuse_a_malformed_context_with_its_sentence(harness, case):
    _name, ctx, sentence = case
    with patch("app.services.stream_service.create_session", AsyncMock()) as create, \
            patch("app.services.stream_service.ingest_via_api_key", AsyncMock()) as ingest:
        sessions = await _post("/api/v1/stream/sessions", json={
            "project_id": str(harness["project_id"]), "client_name": "ci", "run_context": ctx,
        })
        streamed = await _post("/api/v1/stream/ingest", json={
            "run_id": "ci-run-ctx", "batch_id": "batch-ctx-1", "events": _ONE_EVENT, "meta": {"run_context": ctx},
        })
    assert sessions.status_code == 422, sessions.text
    assert _loc_names_run_context(sessions.json())
    assert any(err["msg"] == "Value error, " + sentence for err in sessions.json()["detail"])
    assert streamed.status_code == 422, streamed.text
    assert _loc_names_run_context(streamed.json(), "meta")
    assert any(err["msg"] == "Value error, " + sentence for err in streamed.json()["detail"])
    create.assert_not_awaited()
    ingest.assert_not_awaited()


# ── The two Celery task bodies ───────────────────────────────────────────────


def _drive_task(task_run, create, **kwargs):
    """Run a task body with its I/O seams replaced; return what create_run_from_payload got."""
    import asyncio
    from contextlib import asynccontextmanager

    from app.services import ingestion_pipeline
    from app.worker import tasks

    run = SimpleNamespace(
        id=uuid.uuid4(), ingestion_attempted_tests=1, ingestion_rejected_tests=0,
        ingestion_complete=True, ingestion_rejection_reasons=None,
    )
    create.return_value = run

    @asynccontextmanager
    async def _session():
        yield AsyncMock()

    with (
        patch.object(tasks, "_run_async", side_effect=asyncio.run),
        patch.object(tasks, "_archive_raw_upload", new=AsyncMock(return_value=None)),
        patch.object(tasks, "reindex_search", MagicMock()),
        patch.object(
            tasks, "_parse_file_to_results_with_timings",
            return_value=([{"test_name": "checkout_flow", "status": "passed"}], {"run_wall_ms": None, "suites": []}),
        ),
        patch.object(ingestion_pipeline, "create_run_from_payload", new=create),
        patch.object(ingestion_pipeline, "ingest_test_results", new=AsyncMock(return_value=1)),
        patch.object(ingestion_pipeline, "finalize_run", new=AsyncMock()),
        patch("app.db.postgres.AsyncSessionLocal", _session),
        patch("app.services.cache_service.bump_analytics_epoch", new=AsyncMock()),
        patch("app.services.upload_status.set_status", new=AsyncMock()),
        patch("app.core.metrics.uploads_total", MagicMock()),
        patch("app.core.metrics.upload_failures_total", MagicMock()),
    ):
        task_run(**kwargs)
    return create.await_args.kwargs


async def test_the_batch_task_hands_the_stored_context_to_create_run(harness):
    import asyncio

    ctx = run_context_service.validated_or_none(_context())
    payload = _batch(harness["project_id"], ctx)
    create = AsyncMock()
    got = await asyncio.to_thread(
        _drive_task, harness["real_batch_task"].run, create, run_id=str(uuid.uuid4()), payload=payload,
    )
    assert got["run_context"] == ctx


async def test_the_file_task_hands_its_run_context_kwarg_to_create_run(harness):
    import asyncio

    ctx = run_context_service.validated_or_none(_context())
    create = AsyncMock()
    got = await asyncio.to_thread(
        _drive_task, harness["real_file_task"].run, create,
        run_id=str(uuid.uuid4()), file_name="r.xml", file_format="junit",
        project_id=str(harness["project_id"]), build_number="b-ctx-3", file_content="<testsuite/>",
        run_context=ctx,
    )
    assert got["run_context"] == ctx


# ── create_run_from_payload: create / reuse / resume, fill-if-null ───────────


def _result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _pipeline_db(*values: Any) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(v) for v in values])
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def _existing_run(run_context: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), ci_provider=None, ci_repo=None, pr_number=None, ci_actor=None,
        ci_run_url=None, ingestion_identity="identity-x", run_context=run_context, build_number="b-1",
    )


async def test_create_run_from_payload_stores_the_revalidated_redacted_context():
    from app.models.postgres import TestRun
    from app.services import ingestion_pipeline

    project_id = uuid.uuid4()
    db = _pipeline_db(SimpleNamespace(id=project_id), None, None, None)
    with patch.object(ingestion_pipeline, "run_is_tombstoned", AsyncMock(return_value=False)):
        run = await ingestion_pipeline.create_run_from_payload(
            db, project_id=str(project_id), build_number="b-new", run_id=str(uuid.uuid4()),
            run_context=_context(),  # RAW: create_run_from_payload re-validates it
        )
    assert isinstance(run, TestRun)
    db.add.assert_called_once_with(run)
    _no_secret(run.run_context)
    assert run.run_context["env"]["BUILD_IMAGE"] == REDACTED
    assert run.run_context["runtime"] == {"python": "3.11.9"}


async def test_create_run_from_payload_without_a_context_leaves_the_column_null():
    from app.services import ingestion_pipeline

    project_id = uuid.uuid4()
    db = _pipeline_db(SimpleNamespace(id=project_id), None, None)
    with patch.object(ingestion_pipeline, "run_is_tombstoned", AsyncMock(return_value=False)):
        run = await ingestion_pipeline.create_run_from_payload(
            db, project_id=str(project_id), build_number="b-none",
        )
    assert run.run_context is None


@pytest.mark.parametrize("stored", [None, {"v": 1, "runtime": {"python": "3.11.8"}}])
async def test_the_reuse_branch_fills_only_a_null_context(stored):
    from app.services import ingestion_pipeline

    project_id = uuid.uuid4()
    existing = _existing_run(stored)
    db = _pipeline_db(SimpleNamespace(id=project_id), existing)
    run = await ingestion_pipeline.create_run_from_payload(
        db, project_id=str(project_id), build_number="b-1", run_context=_context(),
    )
    assert run is existing
    if stored is None:
        assert run.run_context["runtime"] == {"python": "3.11.9"}
        _no_secret(run.run_context)
    else:
        assert run.run_context == stored  # never overwritten


@pytest.mark.parametrize("stored", [None, {"v": 1, "runtime": {"python": "3.11.8"}}])
async def test_the_resume_branch_fills_only_a_null_context(stored):
    from app.services import ingestion_pipeline

    project_id = uuid.uuid4()
    prior = _existing_run(stored)
    db = _pipeline_db(SimpleNamespace(id=project_id), prior)
    run = await ingestion_pipeline.create_run_from_payload(
        db, project_id=str(project_id), build_number="b-1", run_id=str(prior.id), run_context=_context(),
    )
    assert run is prior
    assert run.run_context == (stored or run_context_service.validated_or_none(_context()))


# ── The live paths ───────────────────────────────────────────────────────────


def _forged() -> dict[str, Any]:
    """A context smuggled through the free ``metadata`` dict: never trusted."""
    return {"v": 1, "env": {"HOME": "/home/forged", "BUILD_IMAGE": "ghp_" + "Sentinel" * 5}}


def _nested_db(added: list) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock(side_effect=added.append)
    db.flush = AsyncMock()
    db.begin_nested = MagicMock()
    db.begin_nested.return_value.__aenter__ = AsyncMock(return_value=None)
    db.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
    return db


async def test_create_session_folds_the_typed_context_and_stamps_the_stub():
    from app.models.postgres import LiveSession, TestRun
    from app.models.schemas import LiveSessionCreate
    from app.services.stream_service import create_session

    project = SimpleNamespace(id=uuid.uuid4(), name="Payments")
    payload = LiveSessionCreate.model_validate({
        "project_id": str(project.id), "client_name": "python-sdk", "build_number": "b-live-1",
        "metadata": {"team": "payments", "run_context": _forged()},
        "run_context": _context(),
    })
    added: list = []
    with patch("app.services.stream_service.resolve_project", AsyncMock(return_value=project)), \
            patch("app.services.stream_service.get_redis", return_value=AsyncMock()), \
            patch("app.services.stream_service.run_is_tombstoned", AsyncMock(return_value=False)), \
            patch("app.streams.live_run_state.RedisLiveRunState.start", AsyncMock(return_value=None)):
        await create_session(_nested_db(added), payload)

    (session,) = [row for row in added if isinstance(row, LiveSession)]
    (stub,) = [row for row in added if isinstance(row, TestRun)]
    expected = run_context_service.validated_or_none(_context())
    assert session.extra_metadata == {"team": "payments", "run_context": expected}
    assert stub.run_context == expected
    _no_secret(session.extra_metadata)
    assert "/home/forged" not in json.dumps(session.extra_metadata)


async def test_with_ci_context_drops_a_metadata_run_context_on_both_exits():
    from app.services.stream_service import _with_ci_context

    no_ci = SimpleNamespace(ci_provider=None, ci_repo=None, pr_number=None, ci_actor=None, ci_run_url=None)
    assert _with_ci_context({"team": "payments", "run_context": _forged()}, no_ci) == {"team": "payments"}
    with_ci = SimpleNamespace(
        ci_provider="github_actions", ci_repo="acme/pay", pr_number=None, ci_actor=None, ci_run_url=None,
    )
    merged = _with_ci_context({"run_context": _forged()}, with_ci)
    assert "run_context" not in merged and merged["ci_context"]["ci_repo"] == "acme/pay"


async def test_the_stream_ingest_auto_session_keeps_only_the_typed_context():
    from app.models.postgres import LiveSession
    from app.models.schemas import LiveStreamIngestRequest
    from app.services import stream_service

    project_id = uuid.uuid4()
    request = LiveStreamIngestRequest.model_validate({
        "run_id": "ci-run-ctx", "batch_id": "batch-ctx-1", "events": _ONE_EVENT,
        "meta": {"metadata": {"team": "payments", "run_context": _forged()}, "run_context": _context()},
    })
    added: list = []
    db = _nested_db(added)
    db.get = AsyncMock(return_value=SimpleNamespace(id=project_id))
    db.execute = AsyncMock(return_value=_result(None))
    with patch.object(stream_service, "get_redis", return_value=AsyncMock()), \
            patch("app.streams.live_run_state.RedisLiveRunState.start", AsyncMock(return_value=None)), \
            patch.object(stream_service, "_persist_event_batch", AsyncMock(return_value=0)):
        await stream_service.ingest_via_api_key(db, project_id, "ci-key", request)

    (session,) = [row for row in added if isinstance(row, LiveSession)]
    assert session.extra_metadata == {
        "team": "payments", "run_context": run_context_service.validated_or_none(_context()),
    }
    _no_secret(session.extra_metadata)


def _closing_session(extra: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="b-live-2", branch=None,
        commit_hash=None, suite_name=None, total_tests=1, started_at=None, extra_metadata=extra,
    )


async def test_upsert_test_run_creates_with_the_context():
    from app.models.postgres import TestRun
    from app.services import stream_service

    ctx = run_context_service.validated_or_none(_context())
    added: list = []
    db = _nested_db(added)
    db.execute = AsyncMock(return_value=_result(None))
    with patch.object(stream_service, "run_is_tombstoned", AsyncMock(return_value=False)):
        await stream_service.upsert_test_run(db, _closing_session({"run_context": ctx}), {"passed": 1, "total": 1})
    (run,) = [row for row in added if isinstance(row, TestRun)]
    assert run.run_context == ctx


@pytest.mark.parametrize("stored", [None, {"v": 1, "runtime": {"python": "3.11.8"}}])
async def test_upsert_test_run_fills_only_a_null_context_on_update(stored):
    from app.services import stream_service

    ctx = run_context_service.validated_or_none(_context())
    run = SimpleNamespace(
        status=None, primary_suite_name=None, ci_provider=None, ci_repo=None, pr_number=None,
        ci_actor=None, ci_run_url=None, run_context=stored,
    )
    db = _nested_db([])
    db.execute = AsyncMock(return_value=_result(run))
    await stream_service.upsert_test_run(db, _closing_session({"run_context": ctx}), {"passed": 1, "total": 1})
    assert run.run_context == (stored or ctx)


async def test_a_forged_stored_context_is_dropped_and_logged_without_its_value(monkeypatch):
    """Defence in depth: an extra_metadata copy that skipped the schema never reaches the column."""
    from app.models.postgres import TestRun
    from app.services import stream_service

    lines: list[tuple[str, str, dict[str, Any]]] = []

    class _Recorder:
        def __getattr__(self, level: str):
            return lambda event, **kw: lines.append((level, event, kw))

    monkeypatch.setattr(run_context_service, "_slog", _Recorder())
    added: list = []
    db = _nested_db(added)
    db.execute = AsyncMock(return_value=_result(None))
    session = _closing_session({"run_context": _forged()})
    with patch.object(stream_service, "run_is_tombstoned", AsyncMock(return_value=False)):
        await stream_service.upsert_test_run(db, session, {"passed": 1, "total": 1})
    (run,) = [row for row in added if isinstance(row, TestRun)]
    assert run.run_context is None
    assert [(level, event) for level, event, _ in lines] == [("warning", "run_context_dropped")]
    assert lines[0][2]["run_id"] == str(session.id)
    _no_secret(lines)
    assert "/home/forged" not in repr(lines)
