"""Regression — FIVE ranks, TEN roles, and the docs must say so.

US-13.3 compliance-audit finding 5 alleged stale "four roles" claims; this test
pinned the five-tier answer. Enterprise roles (epic E00, owner decision D-1,
2026-11-26) split two ideas that used to be one:

* the **ranks** -- the authority ladder ``VIEWER < TESTER < QA_ENGINEER <
  QA_LEAD < ADMIN`` that every ``require_role`` compares. Still exactly five.
* the **roles** -- what a person is assigned: the five ranks plus the job roles
  RELEASE_MANAGER, PROJECT_MANAGER, PRODUCT_MANAGER, DEVELOPER and the AGENT
  principal. A job role is not a rung: it counts as VIEWER on any guard that
  names no permission.

If a rank is added, a role is added, or a doc drops one, this file says so.

Scope note: ``docs/`` is gitignored and deliberately not checked here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from app.core.deps import _ROLE_ORDER  # noqa: E402
from app.core.permissions import JOB_ROLES, LADDER, ROLE_BASE_RANK  # noqa: E402
from app.models.postgres import UserRole  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_RANKS = ["VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"]
EXPECTED_ROLES = EXPECTED_RANKS + [
    "RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER", "DEVELOPER", "AGENT",
]

# Tracked files that enumerate the roles for a reader. Each must name every
# rank and every role. ``user-guide/compliance.md`` is the SOC-2 control mapping.
DOCS_THAT_ENUMERATE_THE_HIERARCHY = [
    "README.md",
    "THREAT_MODEL.md",
    "user-guide/compliance.md",
]

_STALE_COUNT_RE = re.compile(
    r"\b(?:four|4)\b[\s\-]*(?:global |user |ordered )*roles?\b", re.IGNORECASE
)


def test_user_role_enum_has_exactly_ten_roles():
    assert [r.value for r in UserRole] == EXPECTED_ROLES


def test_the_ladder_has_exactly_five_ranks():
    assert [r.value for r in LADDER] == EXPECTED_RANKS


def test_deps_role_order_is_the_ladder():
    """``_ROLE_ORDER`` is what ``require_role`` compares against: ranks only."""
    assert [r.value for r in _ROLE_ORDER] == EXPECTED_RANKS


def test_sso_role_order_is_the_ladder():
    from app.services.sso_service import _ROLE_ORDER as SSO_ROLE_ORDER

    assert [r.value for r in SSO_ROLE_ORDER] == EXPECTED_RANKS


def test_every_role_has_a_rank_and_the_new_ones_count_as_viewer():
    assert set(ROLE_BASE_RANK) == set(UserRole)
    for role in LADDER:
        assert ROLE_BASE_RANK[role] is role
    for role in set(UserRole) - set(LADDER):
        assert ROLE_BASE_RANK[role] is UserRole.VIEWER, role


def test_job_roles_are_the_four_human_non_rank_roles():
    assert {r.value for r in JOB_ROLES} == {"RELEASE_MANAGER", "PROJECT_MANAGER", "PRODUCT_MANAGER", "DEVELOPER"}


def test_sso_provisioning_ceiling_accepts_exactly_the_ranks():
    """The provisioning ceiling is a rank: the config validator accepts the five
    ranks and refuses a job role (a typo'd or missing value would silently fall
    back to the ADMIN ceiling)."""
    from app.core.config import Settings

    for role in EXPECTED_RANKS:
        assert Settings._validate_sso_max_provisioned_role(role) == role
    for role in set(EXPECTED_ROLES) - set(EXPECTED_RANKS):
        with pytest.raises(ValueError):
            Settings._validate_sso_max_provisioned_role(role)


@pytest.mark.parametrize("rel", DOCS_THAT_ENUMERATE_THE_HIERARCHY)
def test_tracked_docs_name_every_role(rel):
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} not present in this checkout")
    text = path.read_text(encoding="utf-8", errors="ignore")
    missing = [role for role in EXPECTED_ROLES if role not in text]
    assert not missing, f"{rel} omits role(s): {missing}"


@pytest.mark.parametrize("rel", DOCS_THAT_ENUMERATE_THE_HIERARCHY)
def test_tracked_docs_do_not_claim_four_roles(rel):
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} not present in this checkout")
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits = [m.group(0) for m in _STALE_COUNT_RE.finditer(text)]
    assert not hits, f"{rel} claims a four-role hierarchy: {hits}"
