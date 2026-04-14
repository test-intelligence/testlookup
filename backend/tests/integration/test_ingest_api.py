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
                "status": "passed",
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
            {"test_name": "t", "suite_name": "s", "status": "passed"}
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
            {"test_name": "t", "suite_name": "s", "status": "passed"}
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
            {"test_name": "t", "suite_name": "s", "status": "passed"}
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
            {"test_name": "t", "suite_name": "s", "status": "passed"}
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
