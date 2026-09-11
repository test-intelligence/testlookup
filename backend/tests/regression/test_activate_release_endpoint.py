"""Regression: a QA lead can choose the project's active release.

The active release is where a run lands when nothing else claims it — the
attribution ladder's terminal rung. S0 made it a real, enforced concept: one per
project behind the partial unique index ``ix_releases_project_active``, with
``activated_at`` / ``deactivated_at`` history that ``resolve_active_release_at``
reads.

And it could only ever be chosen FOR the user. ``activate_release`` was written
complete and correct — including the load-bearing flush — with a
``reason="manual"`` default, and its only caller was ``_create_auto_release``.
No route reached it, and ``is_active`` appeared nowhere in the releases router.
The seventh instance of this epic's shape: a finished service function with no
path from a request.

What the endpoint must not do
-----------------------------
Write ``is_active`` itself. The swap contains a flush between the demote and the
promote because SQLAlchemy orders persistent UPDATEs by primary key and
``Release.id`` is a random uuid4 — without it, half of all orderings present two
active rows to a partial unique index, which is checked per statement and cannot
be deferred. Any reimplementation here would route around that.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from fastapi import HTTPException  # noqa: E402

from app.routers import releases as router_mod  # noqa: E402

PROJECT = uuid.uuid4()


class _Rel:
    def __init__(self, *, status="in_progress", is_active=False, name="2.4.0"):
        self.id = uuid.uuid4()
        self.project_id = PROJECT
        self.name = name
        self.status = status
        self.is_active = is_active


class _Session:
    def __init__(self):
        self.added: list = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="qa.lead")


def _patch(mp, release, *, previous=None):
    async def _get(db, release_id):
        return release

    calls = {}

    async def _activate(db, rel, *, reason="manual"):
        calls["reason"] = reason
        calls["release"] = rel
        rel.is_active = True
        return previous

    mp.setattr(router_mod.release_service, "get_release_or_404", _get)
    mp.setattr(router_mod.release_lifecycle_service, "activate_release", _activate)
    return calls


# ── The happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activating_a_release_swaps_it_in_and_reports_what_it_displaced():
    target, previous = _Rel(), _Rel(is_active=True, name="2.3.0")
    db = _Session()

    with pytest.MonkeyPatch.context() as mp:
        calls = _patch(mp, target, previous=previous)
        out = await router_mod.activate_release_endpoint(
            release_id=str(target.id), db=db, current_user=_actor(), __=None
        )

    assert out["is_active"] is True
    assert out["changed"] is True
    assert out["deactivated"] == str(previous.id), (
        "the caller is not told which release lost the flag — that is the half "
        "that explains why attribution moved"
    )
    assert calls["reason"] == "manual", (
        "the activation is recorded as automatic, so the audit trail cannot "
        "distinguish a person's choice from the rotation beat"
    )
    assert db.commits == 1


@pytest.mark.asyncio
async def test_the_swap_is_delegated_rather_than_reimplemented():
    """The endpoint must not set ``is_active`` itself.

    The service's demote-then-flush-then-promote ordering is load-bearing
    against a partial unique index. A router that wrote the flag directly would
    pass this test's happy path and fail in production on roughly half of all
    attempts.
    """
    import inspect

    src = inspect.getsource(router_mod.activate_release_endpoint)
    assert "activate_release(" in src
    assert "is_active = True" not in src, (
        "the endpoint sets the flag itself, bypassing the flush that keeps two "
        "rows from claiming the slot at once"
    )


# ── Refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["released", "cancelled", "archived"])
@pytest.mark.asyncio
async def test_a_finished_release_cannot_be_made_active(status):
    """A finished release must not collect new runs.

    The next unlabelled run would be attributed to something already shipped,
    and the misdated evidence would sit in its gate decision looking legitimate.
    """
    target = _Rel(status=status)
    db = _Session()

    with pytest.MonkeyPatch.context() as mp:
        _patch(mp, target)
        with pytest.raises(HTTPException) as exc:
            await router_mod.activate_release_endpoint(
                release_id=str(target.id), db=db, current_user=_actor(), __=None
            )

    assert exc.value.status_code == 409
    assert status in str(exc.value.detail)
    assert db.commits == 0
    assert not target.is_active


@pytest.mark.asyncio
async def test_the_terminal_list_is_not_restated_here():
    """``TERMINAL_STATUSES`` stays the single source.

    A second copy would drift from the rotation rule that uses it, and the two
    would disagree about whether a release may hold the flag.
    """
    import inspect

    src = inspect.getsource(router_mod.activate_release_endpoint)
    assert "TERMINAL_STATUSES" in src
    for literal in ('"released"', '"cancelled"', '"archived"'):
        assert literal not in src, (
            f"{literal} is hardcoded in the endpoint instead of coming from "
            "TERMINAL_STATUSES"
        )


# ── Idempotency ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activating_the_already_active_release_is_a_no_op():
    """A double-click, or two people acting on the same stale page, must not be
    an error: the requested state is the state."""
    target = _Rel(is_active=True)
    db = _Session()

    with pytest.MonkeyPatch.context() as mp:
        calls = _patch(mp, target)
        out = await router_mod.activate_release_endpoint(
            release_id=str(target.id), db=db, current_user=_actor(), __=None
        )

    assert out["is_active"] is True
    assert out["changed"] is False
    assert "release" not in calls, "the swap ran for a release already active"
    assert db.added == [], "a no-op wrote an audit row"


# ── The audit trail ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_change_is_audited_with_the_actor_and_what_it_displaced():
    """"Who changed attribution for this project, and from what" is exactly the
    question asked afterwards."""
    target, previous = _Rel(), _Rel(is_active=True, name="2.3.0")
    db = _Session()
    actor = _actor()

    with pytest.MonkeyPatch.context() as mp:
        _patch(mp, target, previous=previous)
        await router_mod.activate_release_endpoint(
            release_id=str(target.id), db=db, current_user=actor, __=None
        )

    assert len(db.added) == 1
    row = db.added[0]
    assert row.action == "release.activated"
    assert row.actor_user_id == actor.id, (
        "an activation recorded against nobody answers 'what' and not 'who'"
    )
    assert row.project_id == PROJECT
    assert row.before_value["release_id"] == str(previous.id)
    assert row.after_value["release_id"] == str(target.id)


@pytest.mark.asyncio
async def test_the_first_activation_records_no_predecessor_rather_than_failing():
    """A project whose active release was deleted has none to displace, and
    ``before_value`` is legitimately absent — not an error path."""
    target = _Rel()
    db = _Session()

    with pytest.MonkeyPatch.context() as mp:
        _patch(mp, target, previous=None)
        out = await router_mod.activate_release_endpoint(
            release_id=str(target.id), db=db, current_user=_actor(), __=None
        )

    assert out["deactivated"] is None
    assert db.added[0].before_value is None


# ── Authorization ────────────────────────────────────────────────────────────


def test_the_route_carries_both_guards():
    """Role alone is not enough.

    ``require_role`` gates by ROLE and knows nothing about which project this
    release belongs to, so a QA lead of one project could otherwise redirect
    another project's attribution. The architectural ratchet matches the path
    param, which is what ``require_release_access`` answers.
    """
    import inspect

    src = inspect.getsource(router_mod.activate_release_endpoint)
    # Re-audit N26: QA_LEAD opts a project-bound key in; require_release_access confines it.
    assert "require_role(UserRole.QA_LEAD, allow_project_key=True)" in src
    assert "require_release_access()" in src, (
        "the endpoint gates by role only — a QA lead could activate a release "
        "in a project they have no access to"
    )
