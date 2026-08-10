"""``GET /api/v1/onboarding/events`` served instance-wide analytics to anyone.

The handler took the authenticated user and **threw it away**::

    async def list_usage_events(
        ...,
        _: User = Depends(get_current_active_user),
    ):
        return await get_usage_events(db, event_name=..., project_id=pid, limit=limit)

``get_usage_events`` applies no scoping either — no project filter at all when
none is named. So any authenticated account read the 500 most recent product
usage events across every project, each carrying another user's ``user_id``,
a ``project_id``, and the raw ``event_payload``.

Every other endpoint in this router uses ``require_project_access()``. This one
alone did not, and could not: it discarded the identity it would have needed.

**Confirmed against the live deployment** with a VIEWER holding zero
memberships, after seeding one event (the table was empty, so an unseeded probe
would have proved nothing — "no data" is not "no leak")::

    GET /api/v1/onboarding/events?limit=100        -> count 1
        event   : zz_probe_event
        user_id : fc89142e-…            (another user)
        project : 2aefa4fa-…            (not a member)
        payload : {"note": "…", "secret_ish": "internal-config-value"}

Fixed as **ADMIN-only**, matching ``/audit-dashboard/export`` — the other
instance-wide analytics export in this codebase. Scoping by membership was the
alternative, but the endpoint is cross-project analytics by nature and
**has no consumer**: no frontend, CLI, MCP or SDK caller references it. Making
it admin-only breaks nothing and matches what it is.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import UserRole  # noqa: E402
from app.routers import onboarding as mod  # noqa: E402

pytestmark = pytest.mark.regression


def _identity_dependency():
    """The Depends(...) that carries the caller's identity, plus its name."""
    for name, param in inspect.signature(mod.list_usage_events).parameters.items():
        default = param.default
        dep = getattr(default, "dependency", None)
        if dep is None:
            continue
        qual = getattr(dep, "__qualname__", "")
        if "require_role" in qual or "current_active_user" in qual:
            return name, dep
    return None, None


def _closure(fn) -> dict:
    return {
        k: c.cell_contents
        for k, c in zip(fn.__code__.co_freevars, fn.__closure__ or ())
    }


def test_listing_usage_events_requires_a_role_guard():
    """Any authenticated account previously read every project's events."""
    name, dep = _identity_dependency()
    assert dep is not None, "the handler has no identity-bearing dependency at all"
    assert "require_role" in dep.__qualname__, (
        "list_usage_events serves instance-wide product usage events — other "
        "users' user_ids, project_ids and raw event payloads — to any "
        "authenticated caller (measured live with a zero-membership VIEWER)"
    )


def test_admin_is_the_level_required():
    """QA_LEAD is not sufficient for instance-wide analytics."""
    _, dep = _identity_dependency()
    assert dep is not None
    role = _closure(dep).get("min_role")
    assert role is not None, "could not read the guard's minimum role"
    assert getattr(role, "value", role) == UserRole.ADMIN.value, (
        f"instance-wide usage analytics requires ADMIN, guard requires {role}"
    )


def test_the_handler_no_longer_discards_its_caller():
    """It bound the user to ``_``, so it could not have scoped anything.

    A guard whose result is thrown away is the failure mode this sweep keeps
    finding — present, called, and unable to affect the outcome.
    """
    name, dep = _identity_dependency()
    assert name != "_", (
        "the caller is bound to `_` and discarded — the handler cannot scope "
        "by an identity it does not keep"
    )
