"""W11 GATE-08b (D15): digests carry the RELEASE-level verdict.

Which release: a Daily / Weekly digest uses the one release its saved view
names, else the project's ACTIVE release; a PER_RELEASE subscription (event
driven) its own ``scope_value``; a project-less digest none. The verdict is the
release's CURRENT release-level decision (``is_current`` and no phase) -- never
recomputed and never the run-level ``ReleaseDecision``, which keeps its own
line. A release never evaluated reads "not evaluated yet": absence is not GO.

``generate_digest`` reads it only when the caller asks
(``with_release_verdict=True``): the default path keeps the pinned pre-W09
statements (``tests/test_digest_scope.py``, unedited).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.models.schemas import DigestContentResponse
from app.services import digest_content_service as dcs
from app.services.digest_scope_service import DigestScope
from tests.test_digest_scope import (
    PRE_W09_SHA256,
    PRE_W09_STATEMENTS,
    _CompilingSession,
    _dispatch,
)

P = uuid.UUID(int=1)  # the project _compile_digest's pin uses
Q = uuid.UUID(int=2)
DECIDED = datetime(2026, 10, 21, 14, 5, tzinfo=timezone.utc)
HOSTILE = "<img src=x onerror=alert(1)>"

VERDICT_SQL = (
    "SELECT releases.id, releases.name, release_gate_decisions.verdict, "
    "release_gate_decisions.created_at, release_gate_decisions.blocking_reasons "
    "FROM releases LEFT OUTER JOIN release_gate_decisions ON "
    "release_gate_decisions.release_id = releases.id AND release_gate_decisions.is_current IS true "
    "AND release_gate_decisions.phase_id IS NULL WHERE releases.project_id = %(project_id_1)s::UUID "
)
#: How the fakes recognise the statement (its WHERE is what the tests judge).
VERDICT_FROM = VERDICT_SQL.split(" ON ")[0]


def _release(name, *, project_id=P, active=False, verdict=None, reasons=None, decided=DECIDED):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, name=name, is_active=active,
        verdict=verdict, decided=decided if verdict else None, reasons=reasons,
    )


ACTIVE = _release("2026.11", active=True, verdict="NO_GO", reasons=["P0 open", "pass rate 81%"])
OTHER = _release("2026.10", verdict="GO", reasons=[])
NEVER = _release("2026.12")
FOREIGN = _release("other-tenant-1.0", project_id=Q, active=True, verdict="GO", reasons=[])


class _First:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


def _answer_verdict(releases, compiled):
    """Evaluate the verdict statement's predicates against ``releases``."""
    params = dict(compiled.params)
    # A statement that lost its project predicate is answered without one, so
    # the test sees the foreign row it would leak.
    project = params.get("project_id_1")
    wanted = params.get("id_1")
    rows = [
        r for r in releases
        if (project is None or r.project_id == project)
        and (r.id == wanted if wanted is not None else r.is_active)
    ]
    if not rows:
        return _First(None)
    r = rows[0]
    return _First((r.id, r.name, r.verdict, r.decided, r.reasons))


class _VerdictSession:
    """Answers only the release verdict statement; records every statement."""

    def __init__(self, releases=(ACTIVE, OTHER, NEVER, FOREIGN)):
        self.releases = list(releases)
        self.sql: list[str] = []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = " ".join(str(compiled).split())
        self.sql.append(sql)
        assert sql.startswith(VERDICT_FROM), f"unexpected statement: {sql}"
        return _answer_verdict(self.releases, compiled)


class _DigestSession(_CompilingSession):
    """The pin's session, plus an answer for the release verdict statement."""

    def __init__(self, releases=(ACTIVE, OTHER, NEVER, FOREIGN)):
        super().__init__()
        self.releases = list(releases)

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        if " ".join(str(compiled).split()).startswith(VERDICT_FROM):
            self.sql.append(str(compiled))
            self.params.append(dict(compiled.params))
            return _answer_verdict(self.releases, compiled)
        return await super().execute(stmt)


async def _digest(session, project_id=P, **kwargs):
    with patch(
        "app.services.flaky_debt_review.build_flaky_debt_reviews", AsyncMock(return_value={"teams": []})
    ), patch(
        "app.services.notification_routing.load_team_channels", AsyncMock(return_value={})
    ), patch("app.services.value_metrics_service.compute_headline", AsyncMock(return_value={})):
        return await dcs.generate_digest(
            session, project_id, "weekly", since=datetime.now(timezone.utc) - timedelta(days=7), **kwargs
        )


def _verdict_statements(session) -> list[str]:
    return [s for s in session.sql if " ".join(s.split()).startswith(VERDICT_FROM)]


# ── 1. digest_release_verdict ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_active_release_with_a_current_no_go():
    db = _VerdictSession()
    rv = await dcs.digest_release_verdict(db, project_id=P, release_id=None)
    assert rv == {
        "release_id": str(ACTIVE.id),
        "release_name": "2026.11",
        "source": "active",
        "verdict": "NO_GO",
        "decided_at": "2026-10-21T14:05:00+00:00",
        "blocker_count": 2,
    }
    assert len(db.sql) == 1
    assert db.sql[0] == VERDICT_SQL + "AND releases.is_active IS true LIMIT %(param_1)s"


@pytest.mark.asyncio
async def test_a_named_release_of_the_project_is_the_subscriptions():
    db = _VerdictSession()
    rv = await dcs.digest_release_verdict(db, project_id=P, release_id=str(OTHER.id))
    assert (rv["release_id"], rv["source"], rv["verdict"], rv["blocker_count"]) == (
        str(OTHER.id), "subscription", "GO", 0,
    )
    assert db.sql[0] == VERDICT_SQL + "AND releases.id = %(id_1)s::UUID LIMIT %(param_1)s"


@pytest.mark.asyncio
async def test_a_foreign_release_id_reads_nothing_of_it():
    """The project predicate confines the id: another tenant's release is None."""
    db = _VerdictSession()
    assert await dcs.digest_release_verdict(db, project_id=P, release_id=FOREIGN.id) is None
    assert len(db.sql) == 1


@pytest.mark.asyncio
async def test_an_id_that_is_not_a_uuid_runs_no_statement():
    db = _VerdictSession()
    assert await dcs.digest_release_verdict(db, project_id=P, release_id="unattributed") is None
    assert await dcs.digest_release_verdict(db, project_id=P, release_id="") is None
    assert db.sql == []


@pytest.mark.asyncio
async def test_a_project_with_no_active_release_has_no_verdict():
    db = _VerdictSession(releases=[OTHER])
    assert await dcs.digest_release_verdict(db, project_id=P, release_id=None) is None


@pytest.mark.asyncio
async def test_a_never_evaluated_release_is_none_not_go():
    db = _VerdictSession()
    rv = await dcs.digest_release_verdict(db, project_id=P, release_id=NEVER.id)
    assert (rv["verdict"], rv["decided_at"], rv["blocker_count"]) == (None, None, 0)
    assert dcs.release_verdict_line({**rv, "source": "active"}) == (
        "Release 2026.12 — not evaluated yet · the project's active release"
    )


@pytest.mark.asyncio
async def test_a_fault_is_none_and_one_warning(caplog):
    class _Broken:
        async def execute(self, stmt):
            raise RuntimeError("connection reset")

    with caplog.at_level(logging.WARNING, logger="services.digest_content"):
        assert await dcs.digest_release_verdict(_Broken(), project_id=P, release_id=None) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "RuntimeError" in warnings[0].getMessage()


# ── generate_digest(with_release_verdict=...) ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"with_release_verdict": False}], ids=["omitted", "false"])
async def test_without_the_flag_the_statements_are_the_pinned_ones(kwargs):
    session = _DigestSession()
    digest = await _digest(session, **kwargs)
    joined = "\n----\n".join(session.sql)
    assert (len(session.sql), hashlib.sha256(joined.encode()).hexdigest()) == (
        PRE_W09_STATEMENTS, PRE_W09_SHA256,
    )
    assert "release_verdict" not in digest


@pytest.mark.asyncio
async def test_with_the_flag_one_statement_after_all_the_others():
    session = _DigestSession()
    digest = await _digest(session, with_release_verdict=True)
    assert len(session.sql) == PRE_W09_STATEMENTS + 1
    before = "\n----\n".join(session.sql[:PRE_W09_STATEMENTS])
    assert hashlib.sha256(before.encode()).hexdigest() == PRE_W09_SHA256
    assert _verdict_statements(session) == [session.sql[-1]]
    assert digest["release_verdict"]["source"] == "active"
    assert digest["release_verdict"]["release_name"] == "2026.11"
    # The preview's response model types it.
    assert DigestContentResponse(**digest).release_verdict.verdict == "NO_GO"


@pytest.mark.asyncio
async def test_a_view_naming_one_release_uses_it():
    scope = DigestScope(source="saved_view", release_ids=(str(OTHER.id),), release_names=("2026.10",))
    session = _DigestSession()
    digest = await _digest(session, scope=scope, with_release_verdict=True)
    rv = digest["release_verdict"]
    assert (rv["release_id"], rv["source"]) == (str(OTHER.id), "subscription")


@pytest.mark.asyncio
async def test_a_view_naming_two_releases_uses_the_active_one():
    scope = DigestScope(
        source="saved_view", release_ids=(str(OTHER.id), str(NEVER.id)), release_names=("2026.10", "2026.12"),
    )
    digest = await _digest(_DigestSession(), scope=scope, with_release_verdict=True)
    assert (digest["release_verdict"]["release_id"], digest["release_verdict"]["source"]) == (
        str(ACTIVE.id), "active",
    )


@pytest.mark.asyncio
async def test_no_such_release_sets_the_key_to_none():
    digest = await _digest(_DigestSession(releases=[]), with_release_verdict=True)
    assert "release_verdict" in digest and digest["release_verdict"] is None


@pytest.mark.asyncio
async def test_a_project_less_digest_reads_no_release():
    session = _DigestSession()
    digest = await _digest(session, project_id=None, with_release_verdict=True)
    assert _verdict_statements(session) == []
    assert "release_verdict" not in digest


# ── the line and the renderers ───────────────────────────────────────────────

RV = {
    "release_id": str(ACTIVE.id), "release_name": "2026.11", "source": "active",
    "verdict": "NO_GO", "decided_at": "2026-10-21T14:05:00+00:00", "blocker_count": 2,
}
LINE = "Release 2026.11 — No-Go (2 blockers) · decided 2026-10-21 14:05 UTC · the project's active release"


def test_the_line_names_the_release_its_verdict_and_its_source():
    assert dcs.release_verdict_line(RV) == LINE
    assert dcs.release_verdict_line({**RV, "verdict": "GO", "blocker_count": 0, "source": "subscription"}) == (
        "Release 2026.11 — Go · decided 2026-10-21 14:05 UTC · this digest's release"
    )
    assert dcs.release_verdict_line({**RV, "blocker_count": 1}).startswith("Release 2026.11 — No-Go (1 blocker) ·")
    assert dcs.release_verdict_line({**RV, "verdict": "NOT_EVALUATED", "blocker_count": 0}).startswith(
        "Release 2026.11 — Not evaluated (too little evidence) · decided"
    )
    assert dcs.DIGEST_RELEASE_SOURCES == ("subscription", "active")
    assert dcs.NEVER_EVALUATED_TEXT == "not evaluated yet"


def _base_digest(**extra):
    digest = {
        "project_name": "Checkout", "period": "weekly", "generated_at": "2026-10-23T08:00:00+00:00",
        "total_runs": 3, "avg_pass_rate": 90.0, "pass_rate_trend": 1.0, "flaky_test_count": 0,
        "action_items": ["Quality metrics are stable — no urgent action items"],
        "scope": {
            "source": "saved_view", "view_id": "v", "view_name": "Checkout regressions",
            "release_ids": [str(ACTIVE.id)], "release_names": ["2026.11"], "suites": [],
            "window_days": None, "dropped_release_count": 0,
        },
    }
    digest.update(extra)
    return digest


def test_the_text_line_comes_first_after_the_scope_lines():
    lines = dcs.render_digest_text(_base_digest(release_verdict=RV)).split("\n")
    scope = dcs.digest_scope_lines(_base_digest())
    assert lines[: len(scope)] == [dcs.slack_text(line) for line in scope]
    assert lines[len(scope)] == LINE


def test_the_zero_change_one_liner_keeps_its_place():
    lines = dcs.render_digest_text(_base_digest(release_verdict=RV, is_zero_change=True)).split("\n")
    assert lines[0].startswith("No changes since the last digest")
    assert lines[-1] == LINE


def test_html_carries_the_line_after_the_scope_block():
    html = dcs.render_digest_html(_base_digest(release_verdict=RV))
    assert html.index("data-digest-scope") < html.index("data-digest-release-verdict") < html.index("Avg Pass Rate")
    assert "Release 2026.11 — No-Go (2 blockers)" in html
    zero = dcs.render_digest_html(_base_digest(release_verdict=RV, is_zero_change=True))
    assert zero.index("No changes since the last digest") < zero.index("data-digest-release-verdict")


def test_a_hostile_release_name_is_escaped_in_html_and_for_slack():
    rv = {**RV, "release_name": HOSTILE}
    html = dcs.render_digest_html(_base_digest(release_verdict=rv))
    assert HOSTILE not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    text = dcs.render_digest_text(_base_digest(release_verdict={**RV, "release_name": "<!channel>"}))
    assert "<!channel>" not in text
    assert "Release &lt;!channel&gt; — No-Go" in text


@pytest.mark.parametrize("value", ["absent", None])
def test_no_verdict_renders_exactly_as_before(value):
    plain = _base_digest()
    other = _base_digest() if value == "absent" else _base_digest(release_verdict=None)
    assert dcs.render_digest_text(other) == dcs.render_digest_text(plain)
    assert dcs.render_digest_html(other) == dcs.render_digest_html(plain)
    assert "data-digest-release-verdict" not in dcs.render_digest_html(other)


# ── 2. the dispatchers ───────────────────────────────────────────────────────


@pytest.mark.parametrize("schedule", ["DAILY", "WEEKLY"])
def test_the_scheduled_dispatcher_asks_for_the_verdict(monkeypatch, schedule):
    world = _dispatch(monkeypatch, schedule=schedule, saved_view_id=None, saved_view_name=None, view_answers=[])
    assert world.answers == []
    [kwargs] = world.generated
    assert kwargs["with_release_verdict"] is True
    # The fake builds the digest, so the dispatcher's own statements are unchanged.
    assert len(world.statements) == 4


RUN = uuid.uuid4()
PROJECT = uuid.uuid4()
REL_A = _release("2026.11", project_id=PROJECT, active=True, verdict="NO_GO", reasons=["P0 open", "flaky"])
REL_B = _release("2026.10", project_id=PROJECT, verdict="GO", reasons=[])


class _TaskSession:
    """Every AsyncSessionLocal() the AI-summary task opens."""

    def __init__(self, world):
        self.world = world

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = " ".join(str(compiled).split())
        self.world.statements.append(sql)
        if sql.startswith(VERDICT_FROM):
            return _answer_verdict([REL_A, REL_B, FOREIGN], compiled)
        if "FROM digest_subscriptions" in sql:
            return SimpleNamespace(all=lambda: list(self.world.subs))
        if "FROM release_test_run_links" in sql:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [REL_A.id]))
        if "FROM projects" in sql:
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(name="Checkout"))
        return SimpleNamespace(scalar_one_or_none=lambda: self.world.run)

    async def commit(self):
        pass


def _sub(schedule, scope_value=None):
    return SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=PROJECT, trigger_filter="all",
        schedule=schedule, scope_type="release" if schedule == "PER_RELEASE" else "project",
        scope_value=scope_value,
    )


def _drive_ai_summary(monkeypatch, subs):
    from app.services.notification import manager
    from app.worker import tasks

    world = SimpleNamespace(
        statements=[], subs=[(sub, f"user{i}@example.com") for i, sub in enumerate(subs)],
        run=SimpleNamespace(id=RUN, project_id=PROJECT, pass_rate=80.0, total_tests=10, failed_tests=2,
                            primary_suite_name=None, primary_release_id=REL_A.id),
    )

    @asynccontextmanager
    async def _factory():
        yield _TaskSession(world)

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr(
        "app.services.report_distribution_policy.gate_ai_summary_text",
        AsyncMock(side_effect=lambda db, **kw: (kw["summary_text"], None)),
    )
    digests = AsyncMock()
    monkeypatch.setattr(manager, "dispatch_ai_summary_notifications", AsyncMock())
    monkeypatch.setattr(manager, "stage_explicit_notification_deliveries", digests)
    monkeypatch.setattr(tasks, "run_matches_digest_scope", lambda _sub, _run: True)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    tasks.dispatch_ai_summary_email.run(
        str(RUN), str(PROJECT), "42", None, None, "Deterministic summary of build 42.", None, False,
    )
    return world, digests.await_args.kwargs["deliveries"]


def test_a_per_release_delivery_carries_its_releases_verdict(monkeypatch):
    world, deliveries = _drive_ai_summary(monkeypatch, [_sub("PER_RELEASE", str(REL_B.id))])
    [delivery] = deliveries
    rv = delivery["metadata"]["release_verdict"]
    assert (rv["release_id"], rv["release_name"], rv["source"], rv["verdict"]) == (
        str(REL_B.id), "2026.10", "subscription", "GO",
    )
    # The body is the summary as gated: untouched by the verdict.
    assert delivery["body"] == "Deterministic summary of build 42."
    assert len([s for s in world.statements if s.startswith(VERDICT_FROM)]) == 1


def test_two_subscriptions_of_one_release_read_it_once(monkeypatch):
    world, deliveries = _drive_ai_summary(
        monkeypatch, [_sub("PER_RELEASE", str(REL_A.id)), _sub("PER_RELEASE", str(REL_A.id))],
    )
    assert [d["metadata"]["release_verdict"]["release_name"] for d in deliveries] == ["2026.11", "2026.11"]
    assert len([s for s in world.statements if s.startswith(VERDICT_FROM)]) == 1


def test_other_event_driven_digests_read_no_verdict(monkeypatch):
    world, deliveries = _drive_ai_summary(monkeypatch, [_sub("PER_RUN")])
    assert "release_verdict" not in deliveries[0]["metadata"]
    assert not any(s.startswith(VERDICT_FROM) for s in world.statements)


def test_a_per_release_subscription_of_a_foreign_release_carries_nothing(monkeypatch):
    world, deliveries = _drive_ai_summary(monkeypatch, [_sub("PER_RELEASE", str(FOREIGN.id))])
    assert "release_verdict" not in deliveries[0]["metadata"]
    assert len([s for s in world.statements if s.startswith(VERDICT_FROM)]) == 1
