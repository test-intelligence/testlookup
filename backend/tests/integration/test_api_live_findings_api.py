"""Defects the launch QA fixes still had when they were verified live on the homelab (2026-11-25)."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.core.test_library_vocab import AUTOMATION_STATUSES
from app.models.postgres import UserRole
from app.routers import releases
from tests.integration.conftest import fake_execute_result

RELEASE_ID = UUID("66666666-6666-4666-8666-666666666666")
BACKEND = Path(__file__).resolve().parents[2]


# ── L-01: a release that does not exist is not "not evaluated yet" ───────────


@pytest.mark.parametrize("role", [UserRole.ADMIN], ids=["admin-skips-the-guards-lookup"])
async def test_the_gate_of_a_release_that_does_not_exist_is_not_found(client, auth_as, fake_db, monkeypatch, role):
    auth_as(role=role)
    gate = AsyncMock(return_value=None)
    monkeypatch.setattr(releases.release_gate_service, "current_gate", gate)
    fake_db.set_execute_results([fake_execute_result(scalar=None)])

    response = await client.get(f"/api/v1/releases/{RELEASE_ID}/gate")

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Release not found"
    gate.assert_awaited_once()


async def test_a_release_that_exists_but_was_never_evaluated_still_says_so(client, auth_as, fake_db, monkeypatch):
    auth_as(role=UserRole.ADMIN)
    monkeypatch.setattr(releases.release_gate_service, "current_gate", AsyncMock(return_value=None))
    fake_db.set_execute_results([fake_execute_result(scalar=RELEASE_ID)])

    response = await client.get(f"/api/v1/releases/{RELEASE_ID}/gate")

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "This release has not been evaluated yet"


async def test_a_stored_gate_is_returned_unchanged_without_an_existence_query(client, auth_as, monkeypatch):
    auth_as(role=UserRole.ADMIN)
    stored = {"release_id": str(RELEASE_ID), "verdict": "NO_GO", "blocking_reasons": ["checkout is FAILED"]}
    monkeypatch.setattr(releases.release_gate_service, "current_gate", AsyncMock(return_value=stored))
    exists = AsyncMock(return_value=True)
    monkeypatch.setattr(releases, "_release_exists", exists)

    response = await client.get(f"/api/v1/releases/{RELEASE_ID}/gate")

    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "NO_GO"
    exists.assert_not_awaited()


async def test_a_malformed_release_id_names_no_release():
    assert await releases._release_exists(AsyncMock(), "latest") is False


# ── L-03: the demo seed writes only automation statuses the editor offers ────


def test_the_seed_writes_only_known_automation_statuses():
    """24 seeded cases on the homelab held "manual", which no editor can choose again."""
    tree = ast.parse((BACKEND / "scripts" / "seed_dev_data.py").read_text(encoding="utf-8"))
    written = {
        node.value
        for keyword in ast.walk(tree)
        if isinstance(keyword, ast.keyword) and keyword.arg == "automation_status"
        for node in ast.walk(keyword.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert written and written <= set(AUTOMATION_STATUSES), written
