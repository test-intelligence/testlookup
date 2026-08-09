"""The access check must not sit in the branch that cannot leak.

Third recurrence of one bug. Digests (F-033) and chat (F-040) were each fixed
in isolation; sweeping the whole router surface afterwards found **six more**
handlers with the identical shape::

    if not project_id:
        accessible = await get_accessible_project_ids(db, current_user)
        if accessible is not None:
            return []
    ...                                  # <- runs with the caller's project_id,
                                         #    unguarded

Two were confirmed against the live deployment with a VIEWER holding **zero
memberships**, controls first (0 projects visible, empty on the guarded
branch):

``GET /api/v1/release-gate-policies?project_id=<theirs>``
    Returned the other tenant's policy including its full rule document —
    ``go_threshold 20.0, no_go_threshold 55.0, pass_rate_minimum 90.0``.

``GET /api/v1/test-management/plans?project_id=<theirs>``
    ``total: 1`` with the plan name.

The other four (saved views, managed cases, strategies, audit log, plus four
export endpoints) share the shape but had no data on that deployment to prove
it with, so they are fixed on the strength of the shared code path rather than
an individual repro. That distinction is recorded rather than glossed.

**Why it kept recurring, and why a gate now exists.** Grepping for the guard
finds it — ``get_accessible_project_ids`` *is* imported and *is* called. The
architectural authorization ratchet does not apply — it matches routers whose
*path* declares ``{project_id}``, and here the id is a query parameter. Neither
signal distinguishes a guard that runs from one that cannot. So placement got
its own guard: ``backend.project-scope-guard-placement``.

The gate deliberately tolerates a compensating check on the other path —
``runs.py`` guards the ``if`` branch by membership set and the ``else`` branch
with ``_require_accessible_project``, which is correct and must not be flagged.
"""
from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

pytestmark = pytest.mark.regression


_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()


def _user(role="VIEWER"):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = role
    user.username = "zz_sweep_probe"
    return user


class TestReleaseGatePoliciesRefusesAnotherTenant:
    """Live-confirmed: leaked a policy's full threshold document."""

    @pytest.mark.asyncio
    async def test_non_member_naming_a_project_is_denied(self):
        from app.routers import release_gate_policies as mod

        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await mod.list_policies(
                    project_id=_THEIRS, is_active=None,
                    current_user=_user(), db=AsyncMock(),
                )
        assert excinfo.value.status_code == 403, (
            "a non-member listed another tenant's release-gate policies, "
            "including go/no-go thresholds and pass-rate minimums"
        )


class TestTestPlansRefusesAnotherTenant:
    """Live-confirmed: total 1, with the plan name."""

    @pytest.mark.asyncio
    async def test_non_member_naming_a_project_is_denied(self):
        from app.routers import test_management_plans as mod

        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await mod.list_plans(
                    project_id=_THEIRS, page=1, size=20, status=None,
                    db=AsyncMock(), current_user=_user(),
                )
        assert excinfo.value.status_code == 403


class TestTheShapeIsGoneFromEveryRouter:
    """Structural backstop mirroring the CI gate.

    Behavioural tests cover the two handlers that had data to leak; this covers
    the rest of the class, including handlers added later.
    """

    def test_no_access_check_hides_in_a_not_project_id_branch(self):
        from pathlib import Path

        import app.routers as routers_pkg

        routers = Path(routers_pkg.__file__).parent
        guards = ("get_accessible_project_ids", "resolve_project_scope")
        offenders: list[str] = []

        for path in sorted(routers.glob("*.py")):
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            for i, line in enumerate(lines):
                if not re.match(
                    r"if (not project_id|project_id is None)\s*:", line.strip()
                ):
                    continue
                indent = len(line) - len(line.lstrip())
                body, rest = [], []
                j = i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break
                    body.append(nxt)
                    j += 1
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.startswith("@router.") or re.match(r"^(async )?def ", nxt):
                        break
                    rest.append(nxt)
                    j += 1
                if not any(g in "\n".join(body) for g in guards):
                    continue
                # A compensating check on the other path is correct (runs.py).
                if any(
                    g in "\n".join(rest)
                    for g in (*guards, "_require_accessible_project")
                ):
                    continue
                offenders.append(f"{path.name}:{i + 1}")

        assert not offenders, (
            "access check sits in the branch that cannot leak: "
            + ", ".join(offenders)
        )
