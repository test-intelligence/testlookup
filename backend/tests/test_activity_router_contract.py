"""Router contract tests for the activity ledger (epic ACT).

Two things are checked here that a handler test cannot see:

1. **Tenant scoping is on the PATH.** The authorization ratchet auto-passes a
   route with no scoped path param — nine IDORs once hid in that blind spot.
   A query-param version of this endpoint would look guarded and not be.
2. **Filter validation rejects rather than silently ignores.** An unknown
   ``event_type`` that quietly matched nothing would look like "no activity"
   forever, which is the #735 failure mode wearing different clothes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.routers import activity as activity_router
from app.services.activity import events as E


def _routes():
    from app.main import app

    return [r for r in app.routes if "activity" in getattr(r, "path", "")]


# ── Scoping shape ────────────────────────────────────────────────────────────


def test_every_project_scoped_activity_route_takes_project_id_on_the_path():
    scoped = [r for r in _routes() if "/projects/" in r.path]
    assert scoped, "no project-scoped activity routes found — did the prefix change?"
    for route in scoped:
        assert "{project_id}" in route.path, (
            f"{route.path} is project-scoped but does not take project_id as a "
            "PATH param, so require_project_access cannot see it"
        )


def test_the_only_unscoped_route_is_the_global_registry():
    """`/activity/event-types` is the event vocabulary, which is global by
    nature. Everything else must be scoped."""
    unscoped = [r.path for r in _routes() if "/projects/" not in r.path]
    assert unscoped == ["/api/v1/activity/event-types"]


def test_literal_segment_routes_are_registered_before_the_uuid_pattern():
    """`/activity/export` must not be swallowed by `/activity/{event_id}`.

    FastAPI matches in declaration order, so a UUID-shaped catch-all declared
    first would make `export` and `entity` unreachable — the router-prefix
    conflict this codebase has hit before.
    """
    paths = [r.path for r in _routes()]
    catch_all = paths.index("/api/v1/projects/{project_id}/activity/{event_id}")
    for literal in (
        "/api/v1/projects/{project_id}/activity/export",
        "/api/v1/projects/{project_id}/activity/entity/{entity_type}/{entity_id}",
    ):
        assert paths.index(literal) < catch_all, f"{literal} is shadowed"


def test_export_requires_a_higher_role_than_reading():
    """Reading is for every project member — that is the epic's whole point.
    Export leaves the building, so it stays QA_LEAD+."""
    import inspect

    read_deps = inspect.signature(activity_router.list_activity).parameters
    export_deps = inspect.signature(activity_router.export_activity).parameters

    read_src = inspect.getsource(activity_router.list_activity)
    export_src = inspect.getsource(activity_router.export_activity)

    assert "require_role" not in read_src, (
        "the feed must be readable by any project member, not just QA_LEAD"
    )
    # Export checks the PROJECT role, not the global one. Composing
    # require_role(QA_LEAD) with require_project_access() was wrong in BOTH
    # directions and shipped that way until exploratory testing caught it: a
    # global lead who was a project VIEWER could export, and a project QA_LEAD
    # who was globally a QA_ENGINEER could not. require_project_access reads
    # only ProjectMember EXISTENCE - never the role column - so it cannot
    # supply the missing half.
    assert "_assert_can_export" in export_src, (
        "export must check the caller's role ON THIS PROJECT"
    )
    assert "require_role(UserRole.QA_LEAD)" not in export_src, (
        "the global role is the wrong gate for a project-scoped export"
    )
    assert "require_project_access" in read_src
    assert "require_project_access" in export_src
    assert read_deps and export_deps


def test_the_export_check_reads_the_project_member_role() -> None:
    """The half require_project_access cannot provide.

    Asserted on the source because the check needs a live session and a
    populated ProjectMember row to exercise; what must not regress is that it
    consults the ROLE column at all.
    """
    import inspect

    src = inspect.getsource(activity_router._assert_can_export)
    assert "ProjectMember.role" in src
    assert "HTTP_403_FORBIDDEN" in src, (
        "an authorization refusal is a 403, not the 400 the suite-review "
        "helper raises for a malformed request"
    )
    assert "UserRole.ADMIN" in src, "instance ADMIN must retain the backstop"


# ── Filter validation ────────────────────────────────────────────────────────


def _filters(**kw):
    base = dict(
        category=None,
        event_type=None,
        actor_id=None,
        actor_type=None,
        entity_type=None,
        entity_id=None,
        release_id=None,
        since=None,
        until=None,
        q=None,
        limit=50,
        cursor=None,
    )
    base.update(kw)
    return activity_router.activity_filters(**base)


def test_filters_build_from_plain_values_not_query_objects():
    """The dependency takes ONE object precisely so a direct call gets real
    values. Twelve bare ``Query(...)`` defaults would hand a test twelve Query
    OBJECTS instead of None — the trap that has broken direct-call tests in
    this repo five separate times."""
    filters = _filters(category=["runs"], limit=10)
    assert filters.categories == ("runs",)
    assert filters.limit == 10
    assert filters.actor_id is None


def test_an_inverted_date_window_is_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(HTTPException) as exc:
        _filters(since=now, until=now - timedelta(days=1))
    assert exc.value.status_code == 422
    assert "earlier" in str(exc.value.detail)


def test_an_unknown_event_type_is_rejected_and_names_the_way_out():
    with pytest.raises(HTTPException) as exc:
        _filters(event_type=["run.exploded"])
    assert exc.value.status_code == 422
    # A bare 422 leaves the caller guessing; point at the registry endpoint.
    assert "event-types" in str(exc.value.detail)


def test_an_unknown_category_is_rejected_and_lists_the_valid_ones():
    with pytest.raises(HTTPException) as exc:
        _filters(category=["nonsense"])
    assert exc.value.status_code == 422
    assert "runs" in str(exc.value.detail)


def test_an_unknown_actor_type_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _filters(actor_type="robot")
    assert exc.value.status_code == 422


@pytest.mark.parametrize("event_type", sorted(E.ACTIVITY_EVENTS))
def test_every_registered_event_is_accepted_as_a_filter(event_type):
    """The filter UI is built from the registry. If any registered name were
    rejected here, the dropdown would offer an option that 422s."""
    assert _filters(event_type=[event_type]).event_types == (event_type,)


@pytest.mark.parametrize("category", E.ACTIVITY_CATEGORIES)
def test_every_registered_category_is_accepted_as_a_filter(category):
    assert _filters(category=[category]).categories == (category,)


@pytest.mark.parametrize("actor_type", E.ACTOR_TYPES)
def test_every_registered_actor_type_is_accepted_as_a_filter(actor_type):
    assert _filters(actor_type=actor_type).actor_type == actor_type


async def test_event_types_endpoint_returns_the_whole_registry():
    payload = await activity_router.list_event_types(current_user=object())
    flat = [
        e["event_type"] for group in payload["events"].values() for e in group
    ]
    assert sorted(flat) == sorted(E.ACTIVITY_EVENTS)
    assert payload["categories"] == list(E.ACTIVITY_CATEGORIES)
    assert payload["actor_types"] == list(E.ACTOR_TYPES)


def test_uuid_typed_path_params_reject_a_non_uuid():
    """`project_id: uuid.UUID` makes FastAPI 422 a malformed id before the
    handler runs, so no query is ever built from unvalidated input.

    Resolved with ``get_type_hints`` rather than read off ``__annotations__``:
    the router uses ``from __future__ import annotations``, so every annotation
    is the STRING "uuid.UUID" and an identity check against the class silently
    fails for a reason that has nothing to do with the contract.
    """
    import typing

    hints = typing.get_type_hints(activity_router.list_activity)
    assert hints["project_id"] is uuid.UUID

    detail_hints = typing.get_type_hints(activity_router.get_activity_event)
    assert detail_hints["project_id"] is uuid.UUID
    assert detail_hints["event_id"] is uuid.UUID
