"""W20 ADM-08a: preview the role an IdP user gets from the SSO role mapping, before it is saved.

* the highest mapped role wins; no match falls back to the default role;
* ``SSO_MAX_PROVISIONED_ROLE`` clamps the result and the preview says so;
* the preview is pure: no WARNING audit lines (the real resolver logs elevated grants);
* the route previews an unsaved mapping, else the stored one; a bad role is a 400; ADMIN only.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.postgres import UserRole
from app.services import sso_service

MAPPING = {"qa-leads": "QA_LEAD", "qa": "QA_ENGINEER", "admins": "ADMIN", "typo": "SUPERUSER"}


@pytest.fixture(autouse=True)
def _ceiling(monkeypatch):
    monkeypatch.setattr(sso_service.settings, "SSO_MAX_PROVISIONED_ROLE", "ADMIN")


def test_the_highest_mapped_role_wins_and_names_the_matches():
    out = sso_service.preview_role_from_groups(["qa", "qa-leads", "unknown"], MAPPING, UserRole.VIEWER)
    assert out == {"role": "QA_LEAD", "matched_groups": ["qa", "qa-leads"], "source": "mapping",
                   "ceiling": "ADMIN", "clamped": False}


def test_no_match_or_an_unknown_mapped_role_falls_back_to_the_default():
    assert sso_service.preview_role_from_groups(["other"], MAPPING, UserRole.TESTER)["role"] == "TESTER"
    out = sso_service.preview_role_from_groups(["typo"], MAPPING, UserRole.VIEWER)
    assert (out["role"], out["source"], out["matched_groups"]) == ("VIEWER", "default", [])


def test_the_ceiling_clamps_and_the_preview_says_so(monkeypatch):
    monkeypatch.setattr(sso_service.settings, "SSO_MAX_PROVISIONED_ROLE", "QA_ENGINEER")
    out = sso_service.preview_role_from_groups(["admins"], MAPPING, UserRole.VIEWER)
    assert (out["role"], out["clamped"], out["ceiling"]) == ("QA_ENGINEER", True, "QA_ENGINEER")


def test_the_preview_agrees_with_the_real_resolver():
    for groups in (["qa"], ["qa", "admins"], [], ["nothing"], ["typo", "qa-leads"]):
        want = sso_service.resolve_role_from_groups(groups, MAPPING, UserRole.VIEWER).value
        assert sso_service.preview_role_from_groups(groups, MAPPING, UserRole.VIEWER)["role"] == want, groups


def test_the_preview_writes_no_audit_warning(caplog):
    with caplog.at_level(logging.WARNING):
        sso_service.preview_role_from_groups(["admins"], MAPPING, UserRole.VIEWER)
    assert not [r for r in caplog.records if "elevated role" in r.getMessage()]


def _db(config):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=config)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _call(payload, config):
    from app.routers.sso import preview_sso_role

    return asyncio.run(preview_sso_role(uuid.uuid4(), payload, current_user=SimpleNamespace(), db=_db(config)))


def test_the_route_previews_an_unsaved_mapping_else_the_stored_one():
    from app.models.schemas import SSORolePreviewRequest

    stored = SimpleNamespace(role_mapping={"qa": "QA_ENGINEER"}, default_role="VIEWER")
    assert _call(SSORolePreviewRequest(groups=["qa"]), stored)["role"] == "QA_ENGINEER"
    unsaved = SSORolePreviewRequest(groups=["qa"], role_mapping={"qa": "QA_LEAD"}, default_role="TESTER")
    assert _call(unsaved, stored)["role"] == "QA_LEAD"
    assert _call(SSORolePreviewRequest(groups=[" ", "x"], role_mapping={}, default_role="TESTER"), stored)["role"] == "TESTER"


def test_the_route_refuses_a_bad_role_and_a_missing_config():
    from app.models.schemas import SSORolePreviewRequest

    stored = SimpleNamespace(role_mapping={}, default_role="VIEWER")
    with pytest.raises(HTTPException) as exc:
        _call(SSORolePreviewRequest(groups=["a"], role_mapping={"a": "ROOT"}), stored)
    assert exc.value.status_code == 400 and "ROOT" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        _call(SSORolePreviewRequest(groups=[], default_role="GOD"), stored)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        _call(SSORolePreviewRequest(groups=[]), None)
    assert exc.value.status_code == 404


def test_the_route_is_admin_only():
    import inspect

    from app.routers.sso import preview_sso_role

    dep = inspect.signature(preview_sso_role).parameters["current_user"].default
    assert inspect.getclosurevars(dep.dependency).nonlocals.get("min_role") == UserRole.ADMIN
