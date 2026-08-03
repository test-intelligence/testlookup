"""Regression — the role hierarchy is FIVE tiers, and the docs must say so.

US-13.3 compliance-audit finding 5 alleged stale "four roles" claims. A sweep
of the tracked tree found none — ``TESTER`` (added between ``VIEWER`` and
``QA_ENGINEER``) is present in every enumeration. This test pins that result so
the claim cannot silently go stale again: if a sixth tier is added, or a doc
drops one, the pinned files stop matching ``UserRole`` and CI says so.

Scope note: ``docs/`` is gitignored and deliberately not checked here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from app.core.deps import _ROLE_ORDER  # noqa: E402
from app.models.postgres import UserRole  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_ORDER = ["VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"]

# Tracked files that enumerate the hierarchy for a reader. Each must name
# every tier — a four-name list here is the exact staleness the audit looked
# for. ``user-guide/compliance.md`` is the SOC-2 control mapping.
DOCS_THAT_ENUMERATE_THE_HIERARCHY = [
    "README.md",
    "README_FULL.md",
    "THREAT_MODEL.md",
    "UserGuides/TESTLOOKUP_USER_GUIDE.md",
    "user-guide/compliance.md",
]

_STALE_COUNT_RE = re.compile(
    r"\b(?:four|4)\b[\s\-]*(?:global |user |ordered )*roles?\b", re.IGNORECASE
)


def test_user_role_enum_has_exactly_five_tiers():
    assert [r.value for r in UserRole] == EXPECTED_ORDER


def test_deps_role_order_matches_the_enum():
    """``_ROLE_ORDER`` is what ``require_role`` actually compares against."""
    assert [r.value for r in _ROLE_ORDER] == EXPECTED_ORDER


def test_sso_role_order_matches_the_enum():
    from app.services.sso_service import _ROLE_ORDER as SSO_ROLE_ORDER

    assert [r.value for r in SSO_ROLE_ORDER] == EXPECTED_ORDER


def test_sso_provisioning_ceiling_accepts_every_tier():
    """The config validator's allow-list must not lag the enum — a typo'd or
    missing role silently falls back to the ADMIN ceiling."""
    from app.core.config import Settings

    for role in EXPECTED_ORDER:
        assert Settings._validate_sso_max_provisioned_role(role) == role


@pytest.mark.parametrize("rel", DOCS_THAT_ENUMERATE_THE_HIERARCHY)
def test_tracked_docs_name_every_role_tier(rel):
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} not present in this checkout")
    text = path.read_text(encoding="utf-8", errors="ignore")
    missing = [tier for tier in EXPECTED_ORDER if tier not in text]
    assert not missing, f"{rel} omits role tier(s): {missing}"


@pytest.mark.parametrize("rel", DOCS_THAT_ENUMERATE_THE_HIERARCHY)
def test_tracked_docs_do_not_claim_four_roles(rel):
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} not present in this checkout")
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits = [m.group(0) for m in _STALE_COUNT_RE.finditer(text)]
    assert not hits, f"{rel} claims a four-role hierarchy: {hits}"
