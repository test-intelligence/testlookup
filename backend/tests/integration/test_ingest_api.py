"""
API integration tests for /api/v1/ingest — JSON batch and file upload.

Covers the security fixes committed in 9f1cd7c:
  - Streaming file-size limit (OOM prevention)
  - Tenant isolation (resolve_project_scope enforces membership)
  - project_id string validated as a UUID
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_celery_dispatch():
    """
    Stub the two Celery entry points the ingest router calls so tests don't
    need a running broker. Returns the mocks so callers can assert on them.
    """
    batch_task = AsyncMock()
    batch_task.delay = lambda *a, **kw: type("T", (), {"id": "task-batch-1"})()
    file_task = AsyncMock()
    file_task.delay = lambda *a, **kw: type("T", (), {"id": "task-file-1"})()

    with patch("app.worker.tasks.ingest_uploaded_results", batch_task), \
         patch("app.worker.tasks.ingest_uploaded_file", file_task):
        yield {"batch": batch_task, "file": file_task}


@pytest.fixture(autouse=True)
def mock_upload_storage():
    """Keep API tests independent of a running MinIO service."""
    class _Storage:
        async def put_object(self, key, content, content_type="application/json", bucket=None):
            self.last = (key, content, content_type)

        async def delete_object(self, key, bucket=None):
            return None

        async def get_object_content(self, key, bucket=None):
            return self.last[1]

    with patch("app.db.storage.get_storage_provider", return_value=_Storage()):
        yield


# ── JSON batch ──────────────────────────────────────────────────────────────


async def test_ingest_batch_happy_path(client, auth_as, mock_celery_dispatch):
    """Member of target project → 202 Accepted + run_id + task_id."""
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})

    payload = {
        "project_id": str(project_id),
        "build_number": "b-1",
        "results": [
            {
                "test_name": "t1",
                "suite_name": "suite_a",
                "status": "PASSED",
                "duration_ms": 10,
            }
        ],
    }
    resp = await client.post("/api/v1/ingest", json=payload)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "run_id" in body
    assert body["total_results"] == 1


async def test_ingest_batch_non_member_rejected(client, auth_as, mock_celery_dispatch):
    """
    Regression for the tenant-isolation bypass: a user who is NOT a member
    of the target project must get 403, not 202. Previously the router only
    checked API key project binding, so any JWT holder could ingest into
    any project.
    """
    auth_as(accessible_projects={uuid.uuid4()})  # some OTHER project

    payload = {
        "project_id": str(uuid.uuid4()),  # the victim project
        "build_number": "b-1",
        "results": [
            {"test_name": "t", "suite_name": "s", "status": "PASSED"}
        ],
    }
    resp = await client.post("/api/v1/ingest", json=payload)
    assert resp.status_code == 403


async def test_ingest_batch_admin_bypasses_membership(
    client, auth_as, mock_celery_dispatch
):
    """ADMIN resolves to None accessible set → unrestricted ingestion."""
    auth_as(role=UserRole.ADMIN)
    payload = {
        "project_id": str(uuid.uuid4()),
        "build_number": "b-1",
        "results": [
            {"test_name": "t", "suite_name": "s", "status": "PASSED"}
        ],
    }
    resp = await client.post("/api/v1/ingest", json=payload)
    assert resp.status_code == 202


async def test_ingest_batch_project_scoped_key_must_match(
    client, auth_as, mock_celery_dispatch
):
    """Project-scoped API key targeting a DIFFERENT project → 403."""
    bound = uuid.uuid4()
    other = uuid.uuid4()
    auth_as(accessible_projects={bound, other}, bound_project_id=bound)

    payload = {
        "project_id": str(other),
        "build_number": "b-1",
        "results": [
            {"test_name": "t", "suite_name": "s", "status": "PASSED"}
        ],
    }
    resp = await client.post("/api/v1/ingest", json=payload)
    assert resp.status_code == 403
    assert "api key is restricted" in resp.json()["detail"].lower()


async def test_ingest_batch_invalid_uuid_rejected(
    client, auth_as, mock_celery_dispatch
):
    auth_as(role=UserRole.ADMIN)
    payload = {
        "project_id": "not-a-uuid",
        "build_number": "b-1",
        "results": [
            {"test_name": "t", "suite_name": "s", "status": "PASSED"}
        ],
    }
    resp = await client.post("/api/v1/ingest", json=payload)
    # Pydantic (IngestPayload.project_id: str) accepts it, then
    # _parse_project_uuid rejects with 400.
    assert resp.status_code == 400


# ── File upload ─────────────────────────────────────────────────────────────


_MIN_JUNIT_XML = (
    b'<?xml version="1.0"?>'
    b'<testsuite name="s"><testcase name="t1" classname="c1"/></testsuite>'
)


async def test_ingest_file_happy_path(client, auth_as, mock_celery_dispatch):
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})

    files = {"file": ("results.xml", _MIN_JUNIT_XML, "application/xml")}
    data = {
        "project_id": str(project_id),
        "build_number": "b-42",
        "format": "auto",
    }
    resp = await client.post("/api/v1/ingest/file", files=files, data=data)
    assert resp.status_code == 202, resp.text
    assert resp.json()["run_id"]


async def test_manual_upload_mints_fresh_run_when_build_label_exists(
    client, auth_as, mock_celery_dispatch,
):
    """A manual upload must not resume an unrelated same-label run."""
    project_id = uuid.uuid4()
    existing_run_id = str(uuid.uuid4())
    auth_as(accessible_projects={project_id})

    with patch(
        "app.routers.ingest._resolve_run_id",
        AsyncMock(return_value=existing_run_id),
    ):
        resp = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("results.xml", _MIN_JUNIT_XML, "application/xml")},
            data={"project_id": str(project_id), "build_number": "same-label"},
        )

    assert resp.status_code == 202, resp.text
    assert resp.json()["run_id"] != existing_run_id


async def test_ingest_file_rejects_oversized_payload(
    client, auth_as, mock_celery_dispatch
):
    """
    Regression for OOM bug: a 60 MB upload with a 50 MB cap must be rejected
    with 413. The streaming reader must abort before the full payload is
    consumed, and BEFORE dispatching the Celery task.
    """
    project_id = uuid.uuid4()
    auth_as(accessible_projects={project_id})

    huge = b"\x00" * (60 * 1024 * 1024)  # 60 MB
    files = {"file": ("big.xml", huge, "application/xml")}
    data = {"project_id": str(project_id), "build_number": "b-42"}

    # Patch MAX_FILE_SIZE down so the test stays fast: 4 MB cap, 60 MB upload
    # means the abort fires after a few chunks.
    with patch("app.routers.ingest.MAX_FILE_SIZE", 4 * 1024 * 1024):
        resp = await client.post("/api/v1/ingest/file", files=files, data=data)

    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"].lower()


async def test_ingest_file_non_member_rejected(
    client, auth_as, mock_celery_dispatch
):
    """File upload path enforces tenant isolation just like the JSON path."""
    auth_as(accessible_projects={uuid.uuid4()})

    files = {"file": ("x.xml", _MIN_JUNIT_XML, "application/xml")}
    data = {
        "project_id": str(uuid.uuid4()),  # victim project
        "build_number": "b-1",
    }
    resp = await client.post("/api/v1/ingest/file", files=files, data=data)
    assert resp.status_code == 403


async def test_ingest_file_invalid_uuid_rejected(
    client, auth_as, mock_celery_dispatch
):
    auth_as(role=UserRole.ADMIN)
    files = {"file": ("x.xml", _MIN_JUNIT_XML, "application/xml")}
    data = {"project_id": "garbage", "build_number": "b-1"}
    resp = await client.post("/api/v1/ingest/file", files=files, data=data)
    assert resp.status_code == 400


# ── Upload status endpoint (MRU-5) — IDOR / auth surface ─────────────────────


async def test_upload_status_not_found(client, auth_as):
    """Unknown / expired task_id → 404."""
    auth_as(accessible_projects={uuid.uuid4()})
    with patch("app.services.upload_status.get_status", AsyncMock(return_value=None)):
        resp = await client.get("/api/v1/ingest/uploads/task-x")
    assert resp.status_code == 404


async def test_upload_status_member_ok_and_no_project_leak(client, auth_as):
    """A member of the record's project gets the status; project_id is not leaked."""
    pid = uuid.uuid4()
    auth_as(accessible_projects={pid})
    record = {
        "task_id": "t1", "run_id": "r1", "project_id": str(pid),
        "state": "succeeded", "result": {"total": 3, "passed": 2, "failed": 1},
    }
    with patch("app.services.upload_status.get_status", AsyncMock(return_value=record)):
        resp = await client.get("/api/v1/ingest/uploads/t1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "succeeded"
    assert body["run_id"] == "r1"
    assert body["result"]["total"] == 3
    assert "project_id" not in body  # internal field not exposed


async def test_upload_status_non_member_forbidden(client, auth_as):
    """A guessed task_id for ANOTHER tenant's run → 403 (no IDOR leak)."""
    auth_as(accessible_projects={uuid.uuid4()})  # caller's project
    record = {
        "task_id": "t1", "run_id": "r1", "project_id": str(uuid.uuid4()),  # other project
        "state": "succeeded", "result": {"total": 1},
    }
    with patch("app.services.upload_status.get_status", AsyncMock(return_value=record)):
        resp = await client.get("/api/v1/ingest/uploads/t1")
    assert resp.status_code == 403


# ── Archive (zip) upload routing (MRU-12) ────────────────────────────────────


@pytest.mark.parametrize("requested_format", ["auto", "allure", "cypress"])
async def test_ingest_file_zip_routes_to_archive(client, auth_as, requested_format):
    """A .zip upload is detected by magic bytes and dispatched as file_format=
    'archive' with an object-storage key — REGARDLESS of the requested format (so it is
    never mis-parsed as a single file and never 503s on the cypress gate)."""
    import io
    import zipfile
    from unittest.mock import AsyncMock, patch

    pid = uuid.uuid4()
    auth_as(accessible_projects={pid})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a1-result.json", b'{"uuid":"a1","name":"T","status":"passed"}')
    zip_bytes = buf.getvalue()

    captured: dict = {}

    def _delay(**kw):
        captured.update(kw)
        return type("T", (), {"id": "task-zip-1"})()

    file_task = AsyncMock()
    file_task.delay = _delay
    with patch("app.worker.tasks.ingest_uploaded_file", file_task), \
         patch("app.services.feature_flags.is_enabled", AsyncMock(return_value=False)), \
         patch("app.services.upload_status.set_status", AsyncMock()):
        files = {"file": ("allure.zip", zip_bytes, "application/zip")}
        data = {"project_id": str(pid), "build_number": "b-zip", "format": requested_format}
        resp = await client.post("/api/v1/ingest/file", files=files, data=data)

    assert resp.status_code == 202, resp.text  # never 503, even for format=cypress
    assert captured["file_format"] == "archive"
    assert "file_content" not in captured
    assert captured["file_storage_key"].startswith(f"uploads/{pid}/")
    # cypress/playwright disabled (is_enabled=False) → set passed to the worker to gate
    assert set(captured["disabled_formats"]) == {"cypress", "playwright"}


# ── run_ai flag (MRU-8) — archival happens in the worker, not the router ──────


async def test_ingest_file_run_ai_flag_forwarded(client, auth_as):
    """run_ai=false flows from the form into the worker task kwargs."""
    from unittest.mock import AsyncMock, patch

    pid = uuid.uuid4()
    auth_as(accessible_projects={pid})

    captured: dict = {}
    file_task = AsyncMock()
    file_task.delay = lambda **kw: captured.update(kw) or type("T", (), {"id": "t"})()
    with patch("app.worker.tasks.ingest_uploaded_file", file_task), \
         patch("app.services.upload_status.set_status", AsyncMock()):
        files = {"file": ("x.xml", _MIN_JUNIT_XML, "application/xml")}
        data = {"project_id": str(pid), "build_number": "b", "run_ai": "false"}
        resp = await client.post("/api/v1/ingest/file", files=files, data=data)

    assert resp.status_code == 202, resp.text
    assert captured["run_ai"] is False
    assert "raw_archive_key" not in captured  # router no longer archives


async def test_ingest_file_storage_failure_does_not_enqueue(client, auth_as):
    """A storage outage fails before Celery, avoiding an orphaned queued job."""
    from unittest.mock import AsyncMock, patch

    pid = uuid.uuid4()
    auth_as(accessible_projects={pid})
    task = AsyncMock()
    delay_called = False
    def _unexpected_delay(**kw):
        nonlocal delay_called
        delay_called = True
        raise AssertionError("must not enqueue")
    task.delay = _unexpected_delay

    class _BrokenStorage:
        async def put_object(self, *args, **kwargs):
            raise OSError("storage unavailable")

    with patch("app.worker.tasks.ingest_uploaded_file", task), \
         patch("app.db.storage.get_storage_provider", return_value=_BrokenStorage()):
        resp = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("x.xml", _MIN_JUNIT_XML, "application/xml")},
            data={"project_id": str(pid), "build_number": "storage-down"},
        )

    assert resp.status_code == 503
    assert delay_called is False


async def test_ingest_file_run_ai_defaults_true(client, auth_as):
    from unittest.mock import AsyncMock, patch

    pid = uuid.uuid4()
    auth_as(accessible_projects={pid})
    captured: dict = {}
    file_task = AsyncMock()
    file_task.delay = lambda **kw: captured.update(kw) or type("T", (), {"id": "t"})()
    with patch("app.worker.tasks.ingest_uploaded_file", file_task), \
         patch("app.services.upload_status.set_status", AsyncMock()):
        files = {"file": ("x.xml", _MIN_JUNIT_XML, "application/xml")}
        resp = await client.post("/api/v1/ingest/file", files=files,
                                 data={"project_id": str(pid), "build_number": "b"})
    assert resp.status_code == 202
    assert captured["run_ai"] is True
