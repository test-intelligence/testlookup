"""W09 NTF-03: a "Per Release" digest names a release of its project, and delivers.

Before W09 a ``PER_RELEASE`` / ``PER_SUITE`` subscription with no scope was
accepted with a 201 and never delivered (``run_matches_digest_scope`` refuses
an unscoped narrowing row), and any ``scope_value`` was stored -- another
project's release id included. The router now judges the MERGED state through
``digest_scope_service.validate_subscription`` after ``resolve_project_scope``,
answers one fixed 422 text per refusal, and decorates rows with
``scope_label`` / ``scope_problem``. Delivery matches a run linked to the
release by a secondary link too, read with one statement and only when a
``PER_RELEASE`` subscription is met.

The handlers are called directly (the IDOR regression's shape) over
``tests/digest_scope_fakes.WhereSession``, which evaluates each statement's
bound parameters -- so a dropped ``project_id`` predicate changes its answer.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.schemas import DigestSubscriptionCreate, DigestSubscriptionUpdate
from app.routers import digests as digests_router
from app.services import digest_scope_service as dss
from app.worker import tasks
from tests.digest_scope_fakes import WhereSession, release, subscription

P, Q = uuid.uuid4(), uuid.uuid4()
R1 = release(project_id=P, name="2026.10")
R2 = release(project_id=P, name="2026.11")
RQ = release(project_id=Q, name="other-tenant-1.0")


def _user(role="QA_ENGINEER", user_id=None):
    user = MagicMock()
    user.id = user_id or uuid.uuid4()
    user.role = role
    user.username = "digest-owner"
    return user


def _membership(accessible):
    """``None`` = ADMIN (unrestricted); a set = a non-admin's projects."""
    return patch("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible))


def _db(*subs):
    return WhereSession({"releases": [R1, R2, RQ], "saved_views": [], "digest_subscriptions": list(subs)})


async def _create(body: dict, *, accessible=frozenset({P}), role="QA_ENGINEER", db=None):
    db = db or _db()
    with _membership(None if accessible is None else set(accessible)):
        sub = await digests_router.create_subscription(
            payload=DigestSubscriptionCreate(**body), db=db, current_user=_user(role)
        )
    return sub, db


async def _refused(coro) -> tuple[int, str]:
    with pytest.raises(HTTPException) as excinfo:
        await coro
    return excinfo.value.status_code, excinfo.value.detail


# ── 1. create PER_RELEASE ────────────────────────────────────────────────────


class TestPerReleaseCreate:
    @pytest.mark.asyncio
    async def test_no_release_is_refused_with_the_fixed_text(self):
        assert await _refused(
            _create({"name": "rel", "schedule": "PER_RELEASE", "project_id": str(P)})
        ) == (422, dss.NEEDS_RELEASE)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value",
        [str(RQ.id), "unattributed", "Unattributed", "not-a-uuid", str(uuid.uuid4())],
        ids=["another-projects-release", "sentinel", "sentinel-cased", "malformed", "unknown"],
    )
    async def test_foreign_unknown_malformed_and_the_sentinel_share_one_text(self, value):
        db = _db()
        status, detail = await _refused(
            _create(
                {"name": "rel", "schedule": "PER_RELEASE", "project_id": str(P), "scope_value": value},
                db=db,
            )
        )
        assert (status, detail) == (422, dss.BAD_RELEASE)
        assert db.added == [], "a refused subscription was persisted"

    @pytest.mark.asyncio
    async def test_a_project_less_admin_subscription_is_told_to_pick_a_project(self):
        assert await _refused(
            _create(
                {"name": "rel", "schedule": "PER_RELEASE", "scope_value": str(R1.id)},
                accessible=None,
                role="ADMIN",
            )
        ) == (422, dss.RELEASE_PROJECT)

    @pytest.mark.asyncio
    async def test_a_release_of_the_project_is_stored_as_a_release_scope(self):
        sub, db = await _create(
            {
                "name": "rel",
                "schedule": "PER_RELEASE",
                "project_id": str(P),
                # The body said "project"; the stored scope is what the schedule means.
                "scope_type": "project",
                "scope_value": f"  {str(R1.id).upper()} ",
            }
        )
        [row] = db.added
        assert row is sub
        assert (row.scope_type, row.scope_value) == ("release", str(R1.id))
        assert row.saved_view_name is None
        assert (sub.scope_label, sub.scope_problem) == ("2026.10", None)
        # The release lookup is bound to THIS project: the statement carried both ids.
        [lookup] = [params for table, params in db.statements[:1] if table == "releases"]
        assert set(lookup.values()) == {R1.id, P}
        assert db.commits == 1


# ── 2. create PER_SUITE ──────────────────────────────────────────────────────


class TestPerSuiteCreate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [None, "", "   "])
    async def test_a_blank_suite_is_refused(self, value):
        body = {"name": "suite", "schedule": "PER_SUITE", "project_id": str(P), "scope_value": value}
        assert await _refused(_create(body)) == (422, dss.NEEDS_SUITE)

    @pytest.mark.asyncio
    async def test_the_suite_is_stored_trimmed(self):
        sub, db = await _create(
            {"name": "suite", "schedule": "PER_SUITE", "project_id": str(P), "scope_value": "  checkout "}
        )
        assert (sub.scope_type, sub.scope_value) == ("suite", "checkout")
        assert (sub.scope_label, sub.scope_problem) == ("checkout", None)
        assert db.statements == [], "a suite needs no lookup (a suite not yet seen is legitimate)"


# ── 3. no statement without a release or a view ──────────────────────────────


class TestNoStatementWithoutAScope:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("schedule", ["DAILY", "WEEKLY", "PER_RUN", "WEEKLY_RETRO"])
    async def test_the_validator_runs_nothing_for_mock_scope_fields(self, schedule):
        """The IDOR regression drives create with MagicMock payloads and an
        AsyncMock db; the validator must not touch the db for them."""
        db = AsyncMock()
        stored = await dss.validate_subscription(
            db,
            project_id=P,
            schedule=schedule,
            scope_type=(scope_type := MagicMock()),
            scope_value=(scope_value := MagicMock()),
            saved_view_id=None,
            owner_id=uuid.uuid4(),
        )
        assert stored == (scope_type, scope_value, None)
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_whole_create_handler_runs_nothing_either(self):
        payload = MagicMock()
        payload.project_id = P
        payload.saved_view_id = None
        payload.name = "daily"
        payload.schedule = "DAILY"
        payload.channel = "email"
        payload.send_when_unchanged = True
        payload.report_attachment = False
        db = AsyncMock()
        db.add = MagicMock()
        with _membership({P}):
            await digests_router.create_subscription(payload=payload, db=db, current_user=_user())
        assert db.add.called
        db.execute.assert_not_awaited()


# ── 4. PATCH ─────────────────────────────────────────────────────────────────


async def _patch(sub, body: dict, *, owner, accessible=frozenset({P}), db=None):
    db = db or _db(sub)
    with _membership(None if accessible is None else set(accessible)):
        out = await digests_router.update_subscription(
            sub_id=sub.id, payload=DigestSubscriptionUpdate(**body), db=db, current_user=owner
        )
    return out, db


class TestPatch:
    @pytest.mark.asyncio
    async def test_renaming_a_legacy_unscoped_row_is_not_re_judged(self):
        owner = _user()
        legacy = subscription(user_id=owner.id, project_id=P, schedule="PER_SUITE", scope_type="project")
        out, db = await _patch(legacy, {"name": "renamed"}, owner=owner)
        assert out.name == "renamed"
        assert out.scope_problem == "unscoped", "the row still says it cannot deliver"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_a_foreign_release_is_refused_and_the_row_is_untouched(self):
        owner = _user()
        row = subscription(
            user_id=owner.id, project_id=P, schedule="PER_RELEASE", scope_type="release", scope_value=str(R1.id)
        )
        db = _db(row)
        assert await _refused(
            _patch(row, {"scope_value": str(RQ.id)}, owner=owner, db=db)
        ) == (422, dss.BAD_RELEASE)
        assert row.scope_value == str(R1.id)
        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_a_member_who_left_the_project_gets_403_before_any_lookup(self):
        owner = _user()
        row = subscription(user_id=owner.id, project_id=P, schedule="PER_RELEASE", scope_type="release")
        db = _db(row)
        status, _detail = await _refused(
            _patch(row, {"scope_value": str(R1.id)}, owner=owner, accessible=frozenset(), db=db)
        )
        assert status == 403
        assert [table for table, _ in db.statements] == ["digest_subscriptions"], (
            "a former member's PATCH looked a release up: the 422 / 200 split would "
            "tell them which releases exist in a project they no longer read"
        )
        assert row.scope_value is None

    @pytest.mark.asyncio
    async def test_choosing_the_release_on_an_unscoped_row_fixes_it(self):
        owner = _user()
        row = subscription(user_id=owner.id, project_id=P, schedule="PER_RELEASE", scope_type="project")
        out, db = await _patch(row, {"scope_type": "release", "scope_value": str(R2.id)}, owner=owner)
        assert (out.scope_type, out.scope_value) == ("release", str(R2.id))
        assert (out.scope_label, out.scope_problem) == ("2026.11", None)
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_a_deleted_views_snapshot_survives_other_edits_and_clears_on_unbind(self):
        owner = _user()
        row = subscription(
            user_id=owner.id, project_id=P, schedule="WEEKLY", saved_view_id=None, saved_view_name="Old view"
        )
        out, _db_ = await _patch(row, {"schedule": "DAILY"}, owner=owner)
        assert (out.saved_view_id, out.saved_view_name, out.scope_problem) == (None, "Old view", "saved_view_deleted")
        out, _db_ = await _patch(row, {"saved_view_id": None}, owner=owner)
        assert (out.saved_view_id, out.saved_view_name, out.scope_problem) == (None, None, None)

    @pytest.mark.asyncio
    async def test_the_merged_state_is_judged(self):
        """A PATCH of only ``schedule`` takes the stored scope: a PER_SUITE row
        with no suite moved to PER_RELEASE still has no release."""
        owner = _user()
        row = subscription(user_id=owner.id, project_id=P, schedule="PER_SUITE", scope_value="checkout")
        assert await _refused(
            _patch(row, {"schedule": "PER_RELEASE"}, owner=owner)
        ) == (422, dss.BAD_RELEASE)


# ── 5. decorate_subscriptions ────────────────────────────────────────────────


class TestDecorate:
    @pytest.mark.asyncio
    async def test_labels_and_problems_in_at_most_two_statements(self):
        rows = [
            subscription(project_id=P, schedule="PER_RELEASE", scope_value=str(R1.id)),
            subscription(project_id=P, schedule="PER_RELEASE", scope_value=str(RQ.id)),  # foreign
            subscription(project_id=P, schedule="PER_RELEASE", scope_value=str(uuid.uuid4())),  # gone
            subscription(project_id=P, schedule="PER_RELEASE", scope_value=None),
            subscription(project_id=P, schedule="PER_SUITE", scope_value=" checkout "),
            subscription(project_id=P, schedule="PER_SUITE", scope_value=""),
            subscription(project_id=P, schedule="DAILY", saved_view_id=None, saved_view_name="Old view"),
            subscription(project_id=P, schedule="WEEKLY"),
        ]
        db = _db()
        await dss.decorate_subscriptions(db, rows)
        assert [(row.scope_label, row.scope_problem) for row in rows] == [
            ("2026.10", None),
            (None, "release_missing"),
            (None, "release_missing"),
            (None, "unscoped"),
            ("checkout", None),
            (None, "unscoped"),
            (None, "saved_view_deleted"),
            (None, None),
        ]
        assert [table for table, _ in db.statements] == ["releases"]
        assert db.savepoints == 1

    @pytest.mark.asyncio
    async def test_a_list_without_a_release_or_a_view_runs_no_statement(self):
        rows = [
            subscription(schedule="DAILY"),
            subscription(schedule="PER_SUITE", scope_value="checkout"),
            subscription(schedule="PER_RELEASE", scope_value=None),
            subscription(schedule="PER_RUN"),
        ]
        db = AsyncMock()
        await dss.decorate_subscriptions(db, rows)
        db.execute.assert_not_awaited()
        db.begin_nested.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failing_lookup_leaves_both_attributes_none(self, monkeypatch):
        # structlog's capture_logs cannot see a logger another test already
        # bound under the cached configuration: record the module's own.
        logs: list[dict] = []
        monkeypatch.setattr(
            dss, "logger", SimpleNamespace(warning=lambda event, **kw: logs.append({"event": event, **kw}))
        )
        rows = [subscription(project_id=P, schedule="PER_RELEASE", scope_value=str(R1.id))]
        out = await dss.decorate_subscriptions(WhereSession(fail=RuntimeError("db down")), rows)
        assert out is rows
        assert (rows[0].scope_label, rows[0].scope_problem) == (None, None)
        assert logs == [{"event": "digest_scope_labels_failed", "error_type": "RuntimeError"}]


# ── 6. run_matches_digest_scope over DigestRunScope ──────────────────────────


class TestRunMatches:
    def _sub(self, value):
        return SimpleNamespace(schedule="PER_RELEASE", scope_value=value)

    def test_a_run_linked_to_the_release_by_a_secondary_link_matches(self):
        run = tasks.DigestRunScope("checkout", str(R2.id), frozenset({str(R1.id)}))
        assert tasks.run_matches_digest_scope(self._sub(str(R1.id)), run) is True

    def test_the_primary_release_matches(self):
        run = tasks.DigestRunScope("checkout", R1.id, frozenset())
        assert tasks.run_matches_digest_scope(self._sub(str(R1.id)), run) is True

    def test_another_release_does_not(self):
        run = tasks.DigestRunScope("checkout", R2.id, frozenset({str(RQ.id)}))
        assert tasks.run_matches_digest_scope(self._sub(str(R1.id)), run) is False

    @pytest.mark.parametrize("value", [None, "", "  "])
    def test_an_unscoped_row_still_matches_nothing(self, value):
        run = tasks.DigestRunScope("checkout", R1.id, frozenset({str(R1.id)}))
        assert tasks.run_matches_digest_scope(self._sub(value), run) is False

    def test_the_signature_stays_two_positional_arguments(self):
        import inspect

        assert list(inspect.signature(tasks.run_matches_digest_scope).parameters) == ["sub", "run"]


# ── 7. the event-driven loop reads the links once, and only for PER_RELEASE ──


class _CountingSession:
    def __init__(self, world):
        self.world = world

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, stmt):
        sql = str(stmt)
        world = self.world
        world.sql.append(sql)
        if "FROM release_test_run_links" in sql:
            world.link_reads += 1
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(world.links)))
        if "FROM digest_subscriptions" in sql:
            return SimpleNamespace(all=lambda: [(sub, "owner@example.com") for sub in world.subs])
        if "FROM projects" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(name="Checkout"))
        return SimpleNamespace(scalar_one_or_none=lambda: world.run)

    async def commit(self):
        return None


def _drive_loop(monkeypatch, subs, links=()):
    from app.services.notification import manager

    run_id = uuid.uuid4()
    world = SimpleNamespace(
        subs=subs,
        links=[uuid.UUID(str(link)) for link in links],
        sql=[],
        link_reads=0,
        run=SimpleNamespace(
            id=run_id, project_id=P, pass_rate=50.0, total_tests=10, failed_tests=5,
            primary_suite_name="checkout", primary_release_id=R2.id,
        ),
    )

    @asynccontextmanager
    async def _factory():
        yield _CountingSession(world)

    staged = AsyncMock()
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr(manager, "dispatch_ai_summary_notifications", AsyncMock())
    monkeypatch.setattr(manager, "stage_explicit_notification_deliveries", staged)
    monkeypatch.setattr(
        "app.services.report_distribution_policy.gate_ai_summary_text",
        AsyncMock(side_effect=lambda _db, **kw: (kw["summary_text"], None)),
    )
    monkeypatch.setattr("app.services.report_distribution_policy.gate_enforced", lambda: False)
    monkeypatch.setattr("app.services.auto_tagging_service.auto_tag_after_analysis", AsyncMock())
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    tasks.dispatch_ai_summary_email.run(
        str(run_id), str(P), "42", None, None, "deterministic summary", None, False
    )
    deliveries = staged.await_args.kwargs["deliveries"]
    return world, {delivery["digest_subscription_id"] for delivery in deliveries}


def _event_sub(schedule, value=None, project_id=P):
    return SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=project_id, schedule=schedule,
        scope_value=value, trigger_filter="all",
    )


class TestTheEventDrivenLoop:
    def test_the_link_statement_runs_once_for_any_number_of_per_release_rows(self, monkeypatch):
        linked = _event_sub("PER_RELEASE", str(R1.id))  # R1 only by a secondary link
        primary = _event_sub("PER_RELEASE", str(R2.id))  # the run's primary release
        other = _event_sub("PER_RELEASE", str(RQ.id))
        per_run = _event_sub("PER_RUN")
        world, delivered = _drive_loop(monkeypatch, [linked, primary, other, per_run], links=[R1.id, R2.id])
        assert world.link_reads == 1
        assert delivered == {str(linked.id), str(primary.id), str(per_run.id)}

    def test_no_per_release_row_means_no_link_statement(self, monkeypatch):
        subs = [_event_sub("PER_RUN"), _event_sub("PER_SUITE", "checkout")]
        world, delivered = _drive_loop(monkeypatch, subs, links=[R1.id])
        assert world.link_reads == 0
        assert not any("release_test_run_links" in sql for sql in world.sql)
        assert delivered == {str(sub.id) for sub in subs}

    def test_another_projects_per_release_row_reads_no_links(self, monkeypatch):
        world, delivered = _drive_loop(monkeypatch, [_event_sub("PER_RELEASE", str(R1.id), project_id=Q)])
        assert world.link_reads == 0
        assert delivered == set()
