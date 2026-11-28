"""The SPA's role and permission vocabulary equals the backend's (enterprise roles E00-S04).

``frontend/src/types/permissions.ts`` spells out the ``Permission`` union and the role lists the SPA uses for
``can()`` and the role pickers. ``GET /api/v1/me/permissions`` answers with plain strings, so the OpenAPI
document cannot catch a drift: a permission added on one side only would make ``can()`` silently false.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.permissions import EXTRA_ROLE_CHOICES, LADDER, Permission
from app.models.postgres import UserRole

SPA_TYPES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "permissions.ts"


def _source() -> str:
    return SPA_TYPES.read_text(encoding="utf-8")


def _union(name: str) -> list[str]:
    match = re.search(rf"export type {name} =(.*?)(?:\n\n|\nexport )", _source(), re.S)
    assert match, f"{name} not found in {SPA_TYPES}"
    return re.findall(r"'([^']+)'", match.group(1))


def _array(name: str) -> list[str]:
    match = re.search(rf"export const {name}: [^=]+= \[([^\]]*)\]", _source())
    assert match, f"{name} not found in {SPA_TYPES}"
    return re.findall(r"'([^']+)'", match.group(1))


def test_the_spa_permission_union_is_the_backend_catalogue_in_order():
    assert _union("Permission") == [p.value for p in Permission]


def test_the_spa_rank_and_job_role_lists_match():
    assert _union("RankRole") == [r.value for r in LADDER]
    assert _array("RANK_ROLES") == [r.value for r in LADDER]
    assert _union("JobRole") == [r.value for r in EXTRA_ROLE_CHOICES]
    assert _array("JOB_ROLES") == [r.value for r in EXTRA_ROLE_CHOICES]


def test_the_spa_admin_only_grants_match_the_backend():
    """D-17: the SPA leaves out of a QA lead's pickers exactly the roles the server refuses them."""
    from app.core.permissions import ADMIN_ONLY_GRANTS

    assert set(_array("ADMIN_ONLY_GRANTS")) == {r.value for r in ADMIN_ONLY_GRANTS}


def test_every_backend_role_is_an_spa_role():
    spa = set(_union("RankRole")) | set(_union("JobRole")) | {"AGENT"}
    assert spa == {r.value for r in UserRole}
