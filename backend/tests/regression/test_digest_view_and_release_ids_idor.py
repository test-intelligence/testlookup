"""Regression (W09 contract §0.14): the digest routes stored body ids unchecked.

``POST /api/v1/digests/subscriptions`` stored ``payload.saved_view_id`` as
sent, and ``PATCH /api/v1/digests/subscriptions/{id}`` ``setattr``-ed every
field of ``model_dump(exclude_unset=True)`` -- ``saved_view_id`` and
``scope_value`` included. ``resolve_project_scope`` guarded the body
``project_id`` only, so a member of project A could bind:

* another tenant's saved view (or a teammate's private one) to a digest, and
* another project's release as a ``PER_RELEASE`` digest's scope.

Harmless while nothing read them -- the earlier IDOR test said so of
``saved_view_id`` ("If that changes it needs the same treatment") -- and W09
makes the dispatcher read both: a view's filters now shape the digest's
content, and a release id decides which runs deliver it. So both are checked
where they arrive, against the subscription's project and its owner, with one
422 text whatever the id is (unknown, foreign, unreadable), and the preview's
``saved_view_id`` query parameter by the same rule.

Why the authorization ratchet does not see these: ``SCOPED_IDS`` matches
named path and query ids, and a body field is invisible to it (contract D-10
keeps it that way); this file is the evidence instead.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.models.schemas import DigestSubscriptionUpdate  # noqa: E402
from app.routers import digests as digests_router  # noqa: E402
from app.services import digest_scope_service as dss  # noqa: E402
from tests.digest_scope_fakes import WhereSession, release, subscription, view  # noqa: E402

pytestmark = pytest.mark.regression

_MINE, _THEIRS = uuid.uuid4(), uuid.uuid4()
_ME, _TEAMMATE = uuid.uuid4(), uuid.uuid4()
_THEIR_VIEW = view(project_id=_THEIRS, user_id=_TEAMMATE, is_shared=True, name="Their roadmap")
_PRIVATE_VIEW = view(project_id=_MINE, user_id=_TEAMMATE, is_shared=False, name="Private")
_MY_VIEW = view(project_id=_MINE, user_id=_ME, name="Mine")
_THEIR_RELEASE = release(project_id=_THEIRS, name="their-9.0")
_MY_RELEASE = release(project_id=_MINE, name="2026.10")


def _user():
    user = MagicMock()
    user.id = _ME
    user.role = "QA_ENGINEER"
    user.username = "zzprobe"
    return user


def _db(*subs):
    return WhereSession({
        "saved_views": [_THEIR_VIEW, _PRIVATE_VIEW, _MY_VIEW],
        "releases": [_THEIR_RELEASE, _MY_RELEASE],
        "digest_subscriptions": list(subs),
    })


def _member():
    return patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value={_MINE}))


def _create_payload(saved_view_id):
    payload = MagicMock()
    payload.project_id = _MINE
    payload.saved_view_id = saved_view_id
    payload.name = "zz probe view bind"
    payload.schedule = "WEEKLY"
    payload.channel = "email"
    payload.send_when_unchanged = True
    payload.report_attachment = False
    return payload


class TestCreate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("foreign", [_THEIR_VIEW, _PRIVATE_VIEW], ids=["another-tenants-view", "teammates-private-view"])
    async def test_a_view_the_owner_cannot_read_is_not_bound(self, foreign):
        db = _db()
        with _member(), pytest.raises(HTTPException) as excinfo:
            await digests_router.create_subscription(payload=_create_payload(foreign.id), db=db, current_user=_user())
        assert (excinfo.value.status_code, excinfo.value.detail) == (422, dss.BAD_VIEW)
        assert db.added == [], "a subscription bound to a view its owner cannot read was persisted"

    @pytest.mark.asyncio
    async def test_the_owners_own_view_still_binds(self):
        db = _db()
        with _member():
            sub = await digests_router.create_subscription(payload=_create_payload(_MY_VIEW.id), db=db, current_user=_user())
        assert (sub.saved_view_id, sub.saved_view_name) == (_MY_VIEW.id, "Mine")
        assert db.commits == 1


class TestPatch:
    @pytest.mark.asyncio
    async def test_a_foreign_view_is_not_bound_by_patch(self):
        row = subscription(user_id=_ME, project_id=_MINE, schedule="WEEKLY")
        db = _db(row)
        with _member(), pytest.raises(HTTPException) as excinfo:
            await digests_router.update_subscription(
                sub_id=row.id, payload=DigestSubscriptionUpdate(saved_view_id=_THEIR_VIEW.id), db=db,
                current_user=_user(),
            )
        assert (excinfo.value.status_code, excinfo.value.detail) == (422, dss.BAD_VIEW)
        assert row.saved_view_id is None and db.commits == 0

    @pytest.mark.asyncio
    async def test_a_foreign_release_is_not_set_by_patch(self):
        row = subscription(
            user_id=_ME, project_id=_MINE, schedule="PER_RELEASE", scope_type="release",
            scope_value=str(_MY_RELEASE.id),
        )
        db = _db(row)
        with _member(), pytest.raises(HTTPException) as excinfo:
            await digests_router.update_subscription(
                sub_id=row.id, payload=DigestSubscriptionUpdate(scope_value=str(_THEIR_RELEASE.id)), db=db,
                current_user=_user(),
            )
        assert (excinfo.value.status_code, excinfo.value.detail) == (422, dss.BAD_RELEASE)
        assert row.scope_value == str(_MY_RELEASE.id) and db.commits == 0


class TestPreview:
    @pytest.mark.asyncio
    async def test_a_foreign_view_is_never_previewed(self):
        calls: list = []

        async def _generate(*args, **kwargs):
            calls.append(kwargs)
            return {"period": "weekly", "generated_at": "now"}

        with _member(), patch("app.services.digest_content_service.generate_digest", _generate):
            with pytest.raises(HTTPException) as excinfo:
                await digests_router.preview_digest(
                    project_id=_MINE, period="weekly", saved_view_id=_THEIR_VIEW.id, db=_db(), current_user=_user()
                )
        assert (excinfo.value.status_code, excinfo.value.detail) == (422, dss.BAD_VIEW)
        assert calls == [], "digest content was built over another tenant's view"
