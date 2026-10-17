"""W09 NTF-04: a digest bound to a saved report view is filtered by it.

``digest_subscriptions.saved_view_id`` was stored and never read: the
dispatcher mailed the whole project whatever view the subscription named, and
any id was accepted (another tenant's included). Now:

* ``validate_subscription`` binds only a report view of the subscription's
  project that its owner may read, for Daily / Weekly only;
* ``resolve_digest_scope`` re-reads the view for its owner at every send and
  preview, re-checks every release against the project and drops the rest;
* ``generate_digest(..., scope=...)`` adds the view's predicates -- and with
  no view emits exactly the statements it emitted before W09;
* the renderers state the scope (``_e`` in HTML, ``slack_text`` in Slack /
  Teams text), and say so when the view was deleted;
* the preview takes ``saved_view_id`` with the same rule.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.routers import digests as digests_router
from app.services import digest_content_service as dcs
from app.services import digest_scope_service as dss
from app.services.digest_scope_service import PROJECT_SCOPE, DigestScope
from tests.digest_scope_fakes import WhereSession, release, view

P, Q = uuid.uuid4(), uuid.uuid4()
OWNER, TEAMMATE = uuid.uuid4(), uuid.uuid4()
R1 = release(project_id=P, name="2026.10")
R2 = release(project_id=P, name="2026.11")
RQ = release(project_id=Q, name="other-tenant-1.0")
HOSTILE = "<img src=x onerror=alert(1)>"


def _views_db(*views):
    return WhereSession({"saved_views": list(views), "releases": [R1, R2, RQ]})


# ── 1. resolve_digest_scope ──────────────────────────────────────────────────


class TestResolve:
    @pytest.mark.asyncio
    async def test_no_view_is_the_project_with_zero_statements(self):
        db = AsyncMock()
        scope = await dss.resolve_digest_scope(
            db, project_id=P, owner_id=OWNER, saved_view_id=None, saved_view_name=None
        )
        assert scope is PROJECT_SCOPE
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_remembered_name_without_an_id_is_a_deleted_view(self):
        db = AsyncMock()
        scope = await dss.resolve_digest_scope(
            db, project_id=P, owner_id=OWNER, saved_view_id=None, saved_view_name="Checkout regressions"
        )
        assert scope == DigestScope(source="saved_view_deleted", view_name="Checkout regressions")
        assert not scope.is_filtered
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "row",
        [
            view(project_id=P, user_id=TEAMMATE, is_shared=False, name="private"),
            view(project_id=P, user_id=OWNER, filters={"page": "trends", "instances": [], "version": 1}),
            view(project_id=P, user_id=OWNER, filters={}),
            view(project_id=Q, user_id=OWNER, is_shared=True),
        ],
        ids=["teammates-unshared", "layout-row", "legacy-empty-row", "another-project"],
    )
    async def test_a_view_failing_the_rule_is_unavailable_and_named_by_the_snapshot(self, row):
        db = _views_db(row)
        scope = await dss.resolve_digest_scope(
            db, project_id=P, owner_id=OWNER, saved_view_id=row.id, saved_view_name="as bound"
        )
        assert scope == DigestScope(source="saved_view_unavailable", view_id=str(row.id), view_name="as bound")
        assert not scope.is_filtered
        # The lookup itself is bound to the subscription's project.
        assert db.statements == [("saved_views", {"id_1": row.id, "project_id_1": P})]

    @pytest.mark.asyncio
    async def test_releases_are_re_checked_against_the_project(self):
        gone = uuid.uuid4()
        row = view(
            project_id=P,
            user_id=TEAMMATE,
            is_shared=True,
            filters={
                "kind": "report_view",
                "page": "trends",
                "window": 3,
                "release_ids": [str(R2.id), str(RQ.id), str(gone), str(R1.id), str(R2.id)],
                "suites": ["  Checkout ", "checkout", "Payments", ""],
            },
        )
        db = _views_db(row)
        scope = await dss.resolve_digest_scope(
            db, project_id=P, owner_id=OWNER, saved_view_id=row.id, saved_view_name="stale"
        )
        assert scope == DigestScope(
            source="saved_view",
            release_ids=(str(R2.id), str(R1.id)),
            release_names=("2026.11", "2026.10"),
            suites=("checkout", "payments"),
            window_days=3,
            view_id=str(row.id),
            view_name="Checkout regressions",
            dropped_release_count=2,
        )
        assert scope.is_filtered
        [(_t1, _), (table, params)] = db.statements
        assert table == "releases" and params["project_id_1"] == P

    @pytest.mark.asyncio
    async def test_a_single_release_id_key_is_read_too(self):
        row = view(project_id=P, user_id=OWNER, filters={"kind": "report_view", "release_id": str(R1.id)})
        scope = await dss.resolve_digest_scope(
            _views_db(row), project_id=P, owner_id=OWNER, saved_view_id=row.id, saved_view_name=None
        )
        assert (scope.release_ids, scope.window_days) == ((str(R1.id),), None)

    @pytest.mark.asyncio
    async def test_a_view_with_no_release_runs_one_statement(self):
        row = view(project_id=P, user_id=OWNER)
        db = _views_db(row)
        scope = await dss.resolve_digest_scope(
            db, project_id=P, owner_id=OWNER, saved_view_id=row.id, saved_view_name=None
        )
        assert (scope.source, scope.window_days, scope.release_ids) == ("saved_view", 7, ())
        assert [table for table, _ in db.statements] == ["saved_views"]


# ── 2. validate_subscription with a view ─────────────────────────────────────


async def _validate(db, *, schedule="WEEKLY", project_id=P, saved_view_id):
    return await dss.validate_subscription(
        db, project_id=project_id, schedule=schedule, scope_type="project", scope_value=None,
        saved_view_id=saved_view_id, owner_id=OWNER,
    )


class TestValidateView:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("schedule", ["PER_RUN", "PER_SUITE", "PER_RELEASE", "WEEKLY_RETRO"])
    async def test_a_view_filters_daily_and_weekly_only(self, schedule):
        row = view(project_id=P, user_id=OWNER)
        with pytest.raises(dss.DigestScopeError) as excinfo:
            await dss.validate_subscription(
                _views_db(row), project_id=P, schedule=schedule, scope_type="suite",
                scope_value=str(R1.id) if schedule == "PER_RELEASE" else "checkout",
                saved_view_id=row.id, owner_id=OWNER,
            )
        assert str(excinfo.value) == dss.VIEW_SCHEDULE

    @pytest.mark.asyncio
    async def test_a_project_less_subscription_cannot_take_a_view(self):
        row = view(project_id=P, user_id=OWNER)
        with pytest.raises(dss.DigestScopeError) as excinfo:
            await _validate(_views_db(row), project_id=None, saved_view_id=row.id)
        assert str(excinfo.value) == dss.VIEW_PROJECT

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "row",
        [
            view(project_id=Q, user_id=OWNER, is_shared=True),
            view(project_id=P, user_id=TEAMMATE, is_shared=False),
            view(project_id=P, user_id=OWNER, filters={"page": "dashboard", "widgets": ["w1"]}),
        ],
        ids=["another-project", "teammates-unshared", "layout-row"],
    )
    async def test_unreadable_views_share_one_text(self, row):
        with pytest.raises(dss.DigestScopeError) as excinfo:
            await _validate(_views_db(row), saved_view_id=row.id)
        assert str(excinfo.value) == dss.BAD_VIEW

    @pytest.mark.asyncio
    async def test_a_shared_report_view_of_the_project_binds_with_its_name(self):
        row = view(project_id=P, user_id=TEAMMATE, is_shared=True, name="Checkout regressions")
        assert await _validate(_views_db(row), schedule="DAILY", saved_view_id=row.id) == (
            "project", None, "Checkout regressions",
        )


# ── 3. generate_digest: no view = the pre-W09 statements ─────────────────────

#: ``generate_digest(<project>, "weekly", since=<7 days ago>)`` over the session
#: below, captured from the code BEFORE W09 (sqlalchemy 2.0.51, the pin in
#: requirements.txt). If a deliberate change to the digest's own SQL moves it,
#: re-capture it from the unfiltered path -- never from a filtered one.
PRE_W09_STATEMENTS = 14
PRE_W09_SHA256 = "4d938a5379d126fca113cfcd14c1e7c9e234ba556eacf072cd1deda137319e90"
_RUN = SimpleNamespace(id=uuid.uuid4(), pass_rate=80.0, status="FAILED", total_tests=10)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return self

    def scalar(self):
        return 0

    def scalar_one_or_none(self):
        return None

    def one(self):
        return SimpleNamespace(active=0, stale=0, ready=0)


class _CompilingSession:
    """Records every statement compiled for Postgres; answers enough rows that
    every branch of the digest runs (one run, one window failure, one pre-window
    failure)."""

    def __init__(self):
        self.sql: list[str] = []
        self.params: list[dict] = []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        text = str(compiled)
        self.sql.append(text)
        self.params.append(dict(compiled.params))
        if text.lstrip().startswith("SELECT test_runs.id"):
            return _Rows([_RUN])
        if "test_fingerprint" in text and "count(test_cases.id)" in text:
            return _Rows([SimpleNamespace(test_fingerprint="fp1", test_name="t1", fail_count=2)])
        if "test_fingerprint" in text and "DISTINCT" in text and "test_cases.status IN" in text:
            return _Rows([("fp0",)])
        return _Rows([])


async def _compile_digest(**kwargs):
    session = _CompilingSession()
    reviews = AsyncMock(return_value={"teams": []})
    headline = AsyncMock(return_value={})
    with patch("app.services.flaky_debt_review.build_flaky_debt_reviews", reviews), patch(
        "app.services.notification_routing.load_team_channels", AsyncMock(return_value={})
    ), patch("app.services.value_metrics_service.compute_headline", headline):
        digest = await dcs.generate_digest(
            session, uuid.UUID(int=1), "weekly", since=datetime.now(timezone.utc) - timedelta(days=7), **kwargs
        )
    return session, digest, reviews, headline


class TestUnfilteredDigestIsUnchanged:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", [{}, {"scope": None}, {"scope": PROJECT_SCOPE}], ids=["omitted", "none", "project"])
    async def test_the_statements_are_the_pre_w09_ones(self, kwargs):
        session, digest, reviews, headline = await _compile_digest(**kwargs)
        joined = "\n----\n".join(session.sql)
        assert (len(session.sql), hashlib.sha256(joined.encode()).hexdigest()) == (
            PRE_W09_STATEMENTS,
            PRE_W09_SHA256,
        )
        assert "scope" not in digest
        reviews.assert_awaited_once()
        headline.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_deleted_view_adds_the_scope_and_no_predicate(self):
        session, digest, reviews, _headline = await _compile_digest(
            scope=DigestScope(source="saved_view_deleted", view_name="Old view")
        )
        assert hashlib.sha256("\n----\n".join(session.sql).encode()).hexdigest() == PRE_W09_SHA256
        assert digest["scope"] == {
            "source": "saved_view_deleted", "view_id": None, "view_name": "Old view", "release_ids": [],
            "release_names": [], "suites": [], "window_days": None, "dropped_release_count": 0,
        }
        reviews.assert_awaited_once()


FILTERED = DigestScope(
    source="saved_view",
    release_ids=(str(R1.id),),
    release_names=("2026.10",),
    suites=("checkout",),
    window_days=3,
    view_id=str(uuid.uuid4()),
    view_name="Checkout regressions",
)


class TestFilteredDigest:
    @pytest.mark.asyncio
    async def test_every_statement_carries_the_views_predicates(self):
        session, digest, reviews, headline = await _compile_digest(scope=FILTERED)
        runs = next(sql for sql in session.sql if sql.lstrip().startswith("SELECT test_runs.id"))
        assert "test_runs.primary_release_id IN" in runs
        assert "EXISTS (SELECT test_cases.id" in runs, "the run suite rule is run_in_suite_scope_clause"
        window_failed = next(sql for sql in session.sql if "count(test_cases.id) AS fail_count" in sql)
        assert "test_runs.primary_release_id IN" in window_failed
        assert "lower(coalesce(CASE WHEN (test_runs.trigger_source" in window_failed, "the effective suite"
        quarantine = [sql for sql in session.sql if "FROM flaky_quarantine_requests" in sql]
        assert len(quarantine) == 2 and all(
            "lower(trim(flaky_quarantine_requests.suite_name))" in sql for sql in quarantine
        )
        assert not any("primary_release_id" in sql for sql in quarantine), "a quarantine is not per release"
        investigations = next(sql for sql in session.sql if "FROM agent_investigations" in sql)
        assert "test_runs.primary_release_id IN" in investigations
        assert digest["scope"]["view_name"] == "Checkout regressions"
        reviews.assert_not_awaited()
        headline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_window_shortens_the_period(self):
        session, digest, _reviews, _headline = await _compile_digest(scope=FILTERED)
        runs_index = next(i for i, sql in enumerate(session.sql) if sql.lstrip().startswith("SELECT test_runs.id"))
        cutoff = session.params[runs_index]["created_at_1"]
        assert abs(datetime.now(timezone.utc) - cutoff - timedelta(days=3)) < timedelta(minutes=1)
        assert digest["changes_since"] == cutoff.isoformat()

    @pytest.mark.asyncio
    async def test_a_longer_window_does_not_widen_or_shorten_the_period(self):
        wide = DigestScope(source="saved_view", suites=("checkout",), window_days=30, view_name="v")
        session, _digest, _r, _h = await _compile_digest(scope=wide)
        runs_index = next(i for i, sql in enumerate(session.sql) if sql.lstrip().startswith("SELECT test_runs.id"))
        age = datetime.now(timezone.utc) - session.params[runs_index]["created_at_1"]
        assert abs(age - timedelta(days=7)) < timedelta(minutes=1)


# ── 4. the renderers ─────────────────────────────────────────────────────────


def _digest(scope=None, **extra):
    digest = {
        "project_name": "Checkout",
        "period": "weekly",
        "generated_at": "2026-10-16T07:00:00+00:00",
        "total_runs": 3,
        "avg_pass_rate": 90.0,
        "pass_rate_trend": None,
        "new_regressions": 0,
        "top_blockers": [],
        "top_clusters": [],
        "flaky_test_count": 0,
        "action_items": ["Quality metrics are stable — no urgent action items"],
    }
    if scope is not None:
        digest["scope"] = dss.scope_info(scope)
    digest.update(extra)
    return digest


class TestRenderers:
    def test_the_scope_lines_in_text_and_html(self):
        scope = DigestScope(
            source="saved_view", release_ids=(str(R1.id),), release_names=("2026.10",), suites=("checkout",),
            window_days=3, view_name="Checkout regressions", dropped_release_count=2,
        )
        text = dcs.render_digest_text(_digest(scope))
        assert text.splitlines()[:3] == [
            "Filtered by view: Checkout regressions — release 2026.10 · suite checkout · last 3 days",
            "2 releases in this view no longer exist in this project and were left out.",
            dcs.FILTERED_SECTIONS_NOTE,
        ]
        html = dcs.render_digest_html(_digest(scope))
        assert "Filtered by view: Checkout regressions — release 2026.10 · suite checkout · last 3 days" in html
        assert "2 releases in this view no longer exist in this project and were left out." in html
        assert "Project-wide sections (flaky debt by team, hours saved) are left out of a filtered digest." in html

    def test_a_whole_project_digest_renders_as_before(self):
        plain = _digest()
        assert dcs.digest_scope_lines(plain) == []
        assert dcs.render_digest_text(_digest(PROJECT_SCOPE)) == dcs.render_digest_text(plain)
        assert dcs.render_digest_html(_digest(PROJECT_SCOPE)) == dcs.render_digest_html(plain)
        assert "data-digest-scope" not in dcs.render_digest_html(plain)

    def test_a_hostile_view_name_is_text_in_html(self):
        scope = DigestScope(source="saved_view", suites=(HOSTILE,), window_days=1, view_name=HOSTILE)
        html = dcs.render_digest_html(_digest(scope))
        assert "<img" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html

    def test_a_channel_ping_in_a_view_name_is_escaped_for_slack(self):
        scope = DigestScope(
            source="saved_view", release_ids=(str(R1.id),), release_names=("<https://evil.example|2026.10>",),
            view_name="<!channel>",
        )
        text = dcs.render_digest_text(_digest(scope))
        first = text.splitlines()[0]
        assert first == "Filtered by view: &lt;!channel&gt; — release &lt;https://evil.example|2026.10&gt;"
        assert "<!channel>" not in text

    def test_deleted_and_unavailable_views_say_so(self):
        deleted = dcs.render_digest_text(_digest(DigestScope(source="saved_view_deleted", view_name="Old view")))
        assert deleted.splitlines()[0] == "View deleted: Old view — this digest covers the whole project."
        gone = dcs.render_digest_html(_digest(DigestScope(source="saved_view_unavailable", view_name="Theirs")))
        assert "View no longer available to you: Theirs — this digest covers the whole project." in gone

    def test_a_zero_change_window_keeps_its_one_liner_and_states_the_view(self):
        scope = DigestScope(source="saved_view", suites=("checkout",), view_name="Checkout")
        text = dcs.render_digest_text(_digest(scope, is_zero_change=True, latest_run_total_tests=12))
        assert text.splitlines() == [
            "No changes since the last digest — 12 tests, pass rate 90.0%.",
            "Filtered by view: Checkout — suite checkout",
            dcs.FILTERED_SECTIONS_NOTE,
        ]

    def test_long_names_are_clipped(self):
        scope = DigestScope(source="saved_view", suites=("checkout",), view_name="v" * 300)
        first = dcs.digest_scope_lines(_digest(scope))[0]
        assert first == f"Filtered by view: {'v' * 119}… — suite checkout"

    @pytest.mark.parametrize("garbage", [None, "saved_view", 3, [], {"source": 7}, {"source": "project"}])
    def test_a_malformed_scope_renders_nothing(self, garbage):
        assert dcs.digest_scope_lines({"scope": garbage, "period": "weekly"}) == []


# ── 5. the preview ───────────────────────────────────────────────────────────


class _GenerateRecorder:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, db, project_id, period, *args, **kwargs):
        self.calls.append({"project_id": project_id, **kwargs})
        scope = kwargs.get("scope")
        digest = {"project_name": "Checkout", "period": period, "generated_at": "2026-10-16T07:00:00+00:00"}
        if scope is not None and scope.source != "project":
            digest["scope"] = dss.scope_info(scope)
        return digest


async def _preview(db, *, project_id, saved_view_id, accessible, user_id=OWNER):
    user = MagicMock()
    user.id = user_id
    recorder = _GenerateRecorder()
    with patch("app.services.digest_content_service.generate_digest", recorder), patch(
        "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=accessible)
    ):
        result = await digests_router.preview_digest(
            project_id=project_id, period="weekly", saved_view_id=saved_view_id, db=db, current_user=user
        )
    return result, recorder


class TestPreview:
    @pytest.mark.asyncio
    async def test_another_projects_view_is_refused_before_any_content(self):
        foreign = view(project_id=Q, user_id=OWNER, is_shared=True)
        recorder = _GenerateRecorder()
        with patch("app.services.digest_content_service.generate_digest", recorder), patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value={P})
        ):
            with pytest.raises(HTTPException) as excinfo:
                user = MagicMock()
                user.id = OWNER
                await digests_router.preview_digest(
                    project_id=P, period="weekly", saved_view_id=foreign.id, db=_views_db(foreign),
                    current_user=user,
                )
        assert (excinfo.value.status_code, excinfo.value.detail) == (422, dss.BAD_VIEW)
        assert recorder.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("accessible", [None, {P}], ids=["admin", "member"])
    async def test_a_view_without_a_project_is_refused(self, accessible):
        row = view(project_id=P, user_id=OWNER)
        with pytest.raises(HTTPException) as excinfo:
            await _preview(_views_db(row), project_id=None, saved_view_id=row.id, accessible=accessible)
        assert (excinfo.value.status_code, excinfo.value.detail) == (422, dss.VIEW_PROJECT)

    @pytest.mark.asyncio
    async def test_a_teammates_private_view_is_refused(self):
        row = view(project_id=P, user_id=TEAMMATE, is_shared=False)
        with pytest.raises(HTTPException) as excinfo:
            await _preview(_views_db(row), project_id=P, saved_view_id=row.id, accessible={P})
        assert (excinfo.value.status_code, excinfo.value.detail) == (422, dss.BAD_VIEW)

    @pytest.mark.asyncio
    async def test_a_readable_view_previews_its_scope(self):
        row = view(
            project_id=P, user_id=TEAMMATE, is_shared=True, name="Checkout regressions",
            filters={"kind": "report_view", "release_ids": [str(R1.id)], "suites": ["checkout"], "window": 7},
        )
        result, recorder = await _preview(_views_db(row), project_id=P, saved_view_id=row.id, accessible={P})
        assert result.scope is not None
        assert (result.scope.source, result.scope.view_name, result.scope.release_names) == (
            "saved_view", "Checkout regressions", ["2026.10"],
        )
        [call] = recorder.calls
        assert call["project_id"] == P and call["scope"].suites == ("checkout",)

    @pytest.mark.asyncio
    async def test_no_view_previews_the_project_with_no_scope(self):
        result, recorder = await _preview(AsyncMock(), project_id=P, saved_view_id=None, accessible={P})
        assert result.scope is None
        assert recorder.calls[0]["scope"] is PROJECT_SCOPE


# ── 6. the scheduled dispatcher ──────────────────────────────────────────────


class _Answer:
    def __init__(self, value):
        self._value = value

    def all(self):
        return self._value

    def first(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self


class _ScriptedSession:
    """Every AsyncSessionLocal() the digest task opens; answers in order."""

    def __init__(self, world):
        self.world = world

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement):
        self.world.statements.append(str(statement))
        if not self.world.answers:
            raise AssertionError(f"unscripted statement: {statement}")
        return _Answer(self.world.answers.pop(0))

    async def commit(self):
        return None

    def add(self, row):
        self.world.added.append(row)


def _dispatch(monkeypatch, *, schedule, saved_view_id, saved_view_name, view_answers):
    import app.db.postgres as postgres
    from app.services.notification import email_service, manager
    from app.worker import tasks

    user_id = OWNER
    world = SimpleNamespace(statements=[], added=[], emails=[], generated=[])
    world.answers = [
        [(uuid.uuid4(), schedule, None, None, True, False, saved_view_id, saved_view_name)],
        (user_id, P, "email"),
        *view_answers,
        SimpleNamespace(id=user_id, role="QA_ENGINEER", email="owner@example.com", is_active=True),
        None,  # the finalising UPDATE
    ]
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _ScriptedSession(world))

    async def _generate(db, project_id, period, *args, **kwargs):
        world.generated.append(kwargs)
        digest = {"project_name": "Checkout", "period": period, "generated_at": "now", "is_zero_change": False}
        scope = kwargs.get("scope")
        if scope is not None and scope.source != "project":
            digest["scope"] = dss.scope_info(scope)
        return digest

    async def _send(**kwargs):
        world.emails.append(kwargs)

    monkeypatch.setattr(dcs, "generate_digest", _generate)
    monkeypatch.setattr(manager, "digest_owner_block_reason", AsyncMock(return_value=None))
    monkeypatch.setattr(email_service, "get_smtp_config", AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(email_service, "send_html_email_with_attachments", _send)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    tasks.dispatch_scheduled_digests.run()
    return world


class TestScheduledDispatcher:
    def test_a_view_filters_the_digest_and_the_log_says_so(self, monkeypatch):
        row = view(
            project_id=P, user_id=OWNER, name="Checkout regressions",
            filters={"kind": "report_view", "release_ids": [str(R1.id), str(RQ.id)], "suites": ["checkout"], "window": 3},
        )
        world = _dispatch(
            monkeypatch, schedule="WEEKLY", saved_view_id=row.id, saved_view_name="Checkout regressions",
            view_answers=[row, [(R1.id, "2026.10")]],
        )
        assert world.answers == [], f"unanswered: {world.answers}"
        [kwargs] = world.generated
        scope = kwargs["scope"]
        assert (scope.source, scope.release_ids, scope.dropped_release_count) == ("saved_view", (str(R1.id),), 1)
        [log] = world.added
        assert log.status == "sent"
        assert log.delivery_metadata == {
            "scope_source": "saved_view", "saved_view_id": str(row.id), "saved_view_name": "Checkout regressions",
        }
        assert log.delivery_key is None, "the relay must never claim a scheduled digest's log row"
        [email] = world.emails
        assert "Filtered by view: Checkout regressions — release 2026.10 · suite checkout · last 3 days" in email["html_body"]

    def test_a_deleted_view_sends_the_whole_project_and_says_so(self, monkeypatch):
        world = _dispatch(
            monkeypatch, schedule="DAILY", saved_view_id=None, saved_view_name="Old view", view_answers=[],
        )
        assert world.answers == []
        assert world.generated[0]["scope"].source == "saved_view_deleted"
        [log] = world.added
        assert log.delivery_metadata == {
            "scope_source": "saved_view_deleted", "saved_view_id": None, "saved_view_name": "Old view",
        }
        assert "View deleted: Old view — this digest covers the whole project." in world.emails[0]["html_body"]

    def test_a_view_its_owner_can_no_longer_read_is_named_by_the_snapshot(self, monkeypatch):
        row = view(project_id=P, user_id=TEAMMATE, is_shared=False, name="Renamed since")
        world = _dispatch(
            monkeypatch, schedule="WEEKLY", saved_view_id=row.id, saved_view_name="As bound", view_answers=[row],
        )
        assert world.answers == []
        assert world.added[0].delivery_metadata["scope_source"] == "saved_view_unavailable"
        html = world.emails[0]["html_body"]
        assert "View no longer available to you: As bound" in html
        assert "Renamed since" not in html

    def test_no_view_runs_no_extra_statement_and_logs_no_metadata(self, monkeypatch):
        world = _dispatch(monkeypatch, schedule="DAILY", saved_view_id=None, saved_view_name=None, view_answers=[])
        assert world.answers == []
        assert len(world.statements) == 4  # discovery, claim, user, the finalising UPDATE
        assert world.generated[0]["scope"] is PROJECT_SCOPE
        assert world.added[0].delivery_metadata is None
