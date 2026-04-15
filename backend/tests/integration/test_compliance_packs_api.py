"""
API integration tests for /api/v1/releases/{id}/compliance-pack and
/api/v1/compliance-packs/{id}/download — Tier 1 item 4.

Covers role guard (QA_LEAD+ for generate), tenant isolation, the 503
path when the feature flag is off, the 409 path when the release has
no linked run, and the download header contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from app.models.postgres import UserRole  # noqa: E402
from app.services import compliance_pack_service as svc  # noqa: E402
from tests.integration.conftest import fake_execute_result  # noqa: E402

pytestmark = pytest.mark.asyncio


def _fake_release(project_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        name="Release 42",
        version="1.2.3",
        description=None,
        status="in_progress",
        planned_date=None,
        released_at=None,
        created_by_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _fake_pack(
    release_id: uuid.UUID,
    project_id: uuid.UUID,
) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        release_id=release_id,
        project_id=project_id,
        test_run_id=uuid.uuid4(),
        minio_key=f"compliance/2026/04/14/{release_id}/pack.zip",
        manifest_sha256="a" * 64,
        file_count=10,
        bytes=4096,
        retention_expires_at=now + timedelta(days=2557),
        generated_at=now,
        generated_by_user_id=None,
        metadata_snapshot={"recommendation": "GO", "risk_score": 10},
        notes=None,
    )


# ── POST /releases/{id}/compliance-pack ────────────────────────────────────


async def test_generate_requires_qa_lead(client, auth_as, override_db, fake_db):
    """QA_ENGINEER gets 403 from the require_role dep."""
    release_id = uuid.uuid4()
    auth_as(role=UserRole.QA_ENGINEER)
    resp = await client.post(
        f"/api/v1/releases/{release_id}/compliance-pack",
        json={},
    )
    assert resp.status_code == 403


async def test_generate_returns_404_when_release_missing(
    client, auth_as, override_db, fake_db,
):
    auth_as(role=UserRole.QA_LEAD)
    fake_db.set_execute_results([fake_execute_result(scalar=None)])
    resp = await client.post(
        f"/api/v1/releases/{uuid.uuid4()}/compliance-pack",
        json={},
    )
    assert resp.status_code == 404


async def test_generate_returns_503_when_flag_disabled(
    client, auth_as, override_db, fake_db,
):
    project_id = uuid.uuid4()
    release = _fake_release(project_id)
    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=release)])

    with patch(
        "app.services.compliance_pack_service.generate_pack",
        AsyncMock(side_effect=svc.CompliancePackDisabledError("off")),
    ):
        resp = await client.post(
            f"/api/v1/releases/{release.id}/compliance-pack",
            json={},
        )
    assert resp.status_code == 503
    assert "off" in resp.json()["detail"]


async def test_generate_returns_409_when_release_has_no_linked_run(
    client, auth_as, override_db, fake_db,
):
    project_id = uuid.uuid4()
    release = _fake_release(project_id)
    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=release)])

    with patch(
        "app.services.compliance_pack_service.generate_pack",
        AsyncMock(side_effect=svc.CompliancePackNotAvailableError("no run")),
    ):
        resp = await client.post(
            f"/api/v1/releases/{release.id}/compliance-pack",
            json={},
        )
    assert resp.status_code == 409


async def test_generate_happy_path(client, auth_as, override_db, fake_db):
    project_id = uuid.uuid4()
    release = _fake_release(project_id)
    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=release)])

    pack = _fake_pack(release.id, project_id)
    with patch(
        "app.services.compliance_pack_service.generate_pack",
        AsyncMock(return_value=pack),
    ):
        resp = await client.post(
            f"/api/v1/releases/{release.id}/compliance-pack",
            json={"notes": "SOX review"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["manifest_sha256"] == "a" * 64
    assert body["bytes"] == 4096


# ── GET /releases/{id}/compliance-packs ────────────────────────────────────


async def test_list_packs_tenant_isolation(
    client, auth_as, override_db, fake_db,
):
    other_project = uuid.uuid4()
    release = _fake_release(other_project)
    auth_as(accessible_projects={uuid.uuid4()})  # not this project
    fake_db.set_execute_results([fake_execute_result(scalar=release)])
    resp = await client.get(f"/api/v1/releases/{release.id}/compliance-packs")
    assert resp.status_code == 403


async def test_list_packs_returns_history_for_admin(
    client, auth_as, override_db, fake_db,
):
    project_id = uuid.uuid4()
    release = _fake_release(project_id)
    auth_as(role=UserRole.ADMIN)
    fake_db.set_execute_results([fake_execute_result(scalar=release)])
    with patch(
        "app.services.compliance_pack_service.list_packs_for_release",
        AsyncMock(return_value=[_fake_pack(release.id, project_id)]),
    ):
        resp = await client.get(f"/api/v1/releases/{release.id}/compliance-packs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ── GET /compliance-packs/{id}/download ────────────────────────────────────


async def test_download_returns_404_when_pack_missing(
    client, auth_as, override_db, fake_db,
):
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.compliance_pack_service.get_pack",
        AsyncMock(return_value=None),
    ):
        resp = await client.get(f"/api/v1/compliance-packs/{uuid.uuid4()}/download")
    assert resp.status_code == 404


async def test_download_sets_manifest_sha_header(
    client, auth_as, override_db, fake_db,
):
    project_id = uuid.uuid4()
    pack = _fake_pack(uuid.uuid4(), project_id)
    auth_as(role=UserRole.ADMIN)
    with patch(
        "app.services.compliance_pack_service.get_pack",
        AsyncMock(return_value=pack),
    ), patch(
        "app.services.compliance_pack_service.load_pack_bytes",
        AsyncMock(return_value=b"fake-zip-bytes"),
    ):
        resp = await client.get(f"/api/v1/compliance-packs/{pack.id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["x-manifest-sha256"] == "a" * 64
    assert resp.headers["x-pack-id"] == str(pack.id)
    assert resp.content == b"fake-zip-bytes"
