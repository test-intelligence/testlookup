"""W10 INT-07: the outbound webhook circuit breaker counts TERMINAL deliveries, once each.

The real ``webhook_service.deliver`` posts to the shared contract fake's
receiver (``/hooks/ok`` 200, ``/hooks/fail`` 500, ``/hooks/reject`` 400)
through ``httpx.ASGITransport``, or to an ``httpx.MockTransport`` that times
out. The sessions are faked at ``AsyncSessionLocal`` (as
``tests/services/test_webhook_service.py`` does) by an in-memory world that
executes the real statements: the token-fenced delivery CAS, the subscription
UPDATE with its ``+ 1`` expressions, the breaker's guarded ``UPDATE ...
RETURNING``, the activity row the ledger stages and the notification INSERT.
Nothing between ``deliver`` and those statements is patched.

What these pin (decision D-9, contract §2.5 test 1):

* threshold 10: nine terminal failures keep the subscription enabled; the
  tenth pauses it (``circuit_open``) and the trip is returned once;
* a success in between resets the run to 0;
* a delivery retried until its budget is spent counts ONCE (at ``DLQ``); a
  read timeout with retries left counts 0; an egress refusal counts; a gated
  (offline / flag off) delivery and a disabled subscription count 0;
* the trip stages exactly one ``webhook.circuit_opened`` activity row and one
  email delivery per recipient (the last editor and the project's QA leads,
  de-duplicated, active only); ``webhook_circuit_opened_total`` rises by 1
  AFTER the commit, and a failing commit leaves no row and no increment;
* a paused subscription's next delivery says so and does not trip again;
* ``OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS``: a listed host skips the
  public-only check and goes through the shared client; an unlisted one is
  refused exactly as before.
"""
from __future__ import annotations

import operator
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import elements

from app.core import metrics
from app.core.config import settings
from app.models.postgres import ProjectActivityEvent, WebhookSubscription
from app.services import webhook_breaker
from app.services import webhook_service as svc
from tests.fakes.fake_integrations import FakeState, create_app

RECEIVER = "https://hooks.fake.test"


def _value(current, expression):
    """Evaluate one SET value of a real UPDATE against the in-memory row."""
    if isinstance(expression, elements.BindParameter):
        return expression.value
    if isinstance(expression, elements.Null):
        return None
    if isinstance(expression, elements.BinaryExpression) and expression.operator is operator.add:
        return (current or 0) + expression.right.value
    raise AssertionError(f"unexpected SET expression {expression!r}")


def _set_values(stmt):
    """(attribute, SET expression) pairs of a real UPDATE."""
    return [(getattr(key, "key", key), expression) for key, expression in stmt._values.items()]


class _Result:
    def __init__(self, value=None, rows=None, rowcount=0):
        self._value, self._rows, self.rowcount = value, rows or [], rowcount

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _World:
    def __init__(self):
        self.project_id = uuid.uuid4()
        self.deliveries: dict[uuid.UUID, SimpleNamespace] = {}
        self.users: dict[uuid.UUID, SimpleNamespace] = {}
        self.members: list[tuple[uuid.UUID, str]] = []
        self.activity: list[ProjectActivityEvent] = []
        self.notifications: list[dict] = []
        self.events: list[str] = []
        self.trips: list = []
        self.fail_trip_commit = False
        editor = self.user("lee@example.test", "QA_LEAD")
        self.user("kim@example.test", "QA_LEAD")
        self.user("gone@example.test", "QA_LEAD", active=False)
        self.user("eng@example.test", "QA_ENGINEER")
        self.sub = SimpleNamespace(
            id=uuid.uuid4(), project_id=self.project_id, name="release hook", enabled=True,
            has_secret=False, target_url=f"{RECEIVER}/hooks/fail", max_retries=0,
            last_delivered_at=None, last_failure_at=None, last_error=None, failure_count=0,
            total_delivered=0, consecutive_failures=0, breaker_threshold=10,
            disabled_reason=None, disabled_at=None, updated_at=None, updated_by_user_id=editor,
        )

    def user(self, email, role, active=True):
        uid = uuid.uuid4()
        self.users[uid] = SimpleNamespace(id=uid, email=email, is_active=active)
        self.members.append((uid, role))
        return uid

    def delivery(self):
        row = SimpleNamespace(
            id=uuid.uuid4(), subscription_id=self.sub.id, run_id=None, event_type="run.completed",
            event_payload={"run_id": str(uuid.uuid4())}, status="PENDING", attempt_count=0,
            dispatch_attempts=0, dispatch_token=None, dispatch_lease_expires_at=None,
            next_dispatch_at=None, error=None, http_status=None, response_preview=None, delivered_at=None,
        )
        self.deliveries[row.id] = row
        return row

    def session(self):
        return _Session(self)

    def recipients(self):
        """The email address each staged notification goes to (its route snapshot)."""
        return sorted(n["delivery_metadata"]["_durable_explicit_route"]["target"] for n in self.notifications)


class _Session:
    def __init__(self, world: _World):
        self.world = world
        self.staged: list = []

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, params = str(compiled), compiled.params
        table = getattr(getattr(stmt, "table", None), "name", None)
        if sql.startswith("UPDATE webhook_deliveries"):
            row = self.world.deliveries[params["id_1"]]
            if row.status != params["status_1"] or row.dispatch_token != params["dispatch_token_1"]:
                return _Result(rowcount=0)
            for key, expression in _set_values(stmt):
                setattr(row, key, _value(getattr(row, key, None), expression))
            return _Result(rowcount=1)
        if sql.startswith("UPDATE webhook_subscriptions"):
            sub = self.world.sub
            if stmt._returning:  # the breaker's guarded trip
                if not (sub.enabled and sub.consecutive_failures >= sub.breaker_threshold):
                    return _Result(None)
                for key, expression in _set_values(stmt):
                    setattr(sub, key, _value(None, expression))
                return _Result(sub)
            for key, expression in _set_values(stmt):
                setattr(sub, key, _value(getattr(sub, key, None), expression))
            return _Result(rowcount=1)
        if sql.startswith("INSERT INTO notification_logs"):
            for values in stmt._multi_values[0]:
                self.staged.append({col.key: value for col, value in values.items()})
            return _Result(rowcount=1)
        if "FROM webhook_deliveries" in sql:
            return _Result(self.world.deliveries.get(params["id_1"]))
        if "FROM webhook_subscriptions" in sql:
            return _Result(self.world.sub if params["id_1"] == self.world.sub.id else None)
        if "JOIN project_members" in sql:
            assert params["project_id_1"] == self.world.project_id and params["role_1"] == "QA_LEAD"
            leads = {uid for uid, role in self.world.members if role == "QA_LEAD"}
            rows = [(u.id, u.email) for u in self.world.users.values() if u.id in leads and u.is_active]
            return _Result(rows=sorted(rows, key=lambda r: r[1]))
        if "FROM users" in sql:
            user = self.world.users.get(params["id_1"])
            return _Result(rows=[(user.id, user.email)] if user and user.is_active else [])
        raise AssertionError(f"unexpected statement on {table}: {sql}")

    def add(self, obj):
        self.staged.append(obj)

    async def flush(self):
        return None

    def begin_nested(self):
        session = self

        class _Savepoint:
            async def __aenter__(self):
                self.mark = len(session.staged)
                return self

            async def __aexit__(self, exc_type, *_exc):
                if exc_type is not None:
                    del session.staged[self.mark:]
                return False

        return _Savepoint()

    async def commit(self):
        if self.world.fail_trip_commit and any(isinstance(o, ProjectActivityEvent) for o in self.staged):
            self.staged = []
            raise RuntimeError("database commit rejected")
        for obj in self.staged:
            if isinstance(obj, ProjectActivityEvent):
                self.world.activity.append(obj)
            else:
                self.world.notifications.append(obj)
        self.staged = []
        self.world.events.append("commit")

    async def rollback(self):
        self.staged = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Counter:
    def __init__(self, events):
        self.events = events
        self.count = 0

    def inc(self, amount=1):
        self.count += amount
        self.events.append("inc")


@pytest.fixture
def world(monkeypatch):
    world = _World()
    state = FakeState()
    world.fake = state
    world.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)))
    world.counter = _Counter(world.events)
    original_trip = webhook_breaker.trip_if_due

    async def recording_trip(db, subscription_id, *, now):
        trip = await original_trip(db, subscription_id, now=now)
        if trip is not None:
            world.trips.append(trip)
        return trip

    monkeypatch.setattr(svc, "AsyncSessionLocal", world.session)
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "_is_safe_public_url", lambda url: (True, "public target"))
    monkeypatch.setattr(svc, "get_public_http_client", lambda: world.client)
    monkeypatch.setattr(metrics, "webhook_circuit_opened_total", world.counter)
    monkeypatch.setattr(webhook_breaker, "trip_if_due", recording_trip)
    monkeypatch.setattr(settings, "OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS", "")
    return world


async def _deliver_until_settled(world, row) -> str:
    """Run the worker's attempts for one delivery until it is no longer PENDING."""
    for _ in range(12):
        await svc.deliver(row.id)
        if row.status != "PENDING":
            return row.status
        row.next_dispatch_at = None  # the back-off has passed
    raise AssertionError("the delivery never settled")


async def _fail(world, path="/hooks/fail"):
    world.sub.target_url = f"{RECEIVER}{path}"
    return await _deliver_until_settled(world, world.delivery())


@pytest.mark.asyncio
async def test_the_tenth_terminal_failure_pauses_the_subscription_once(world):
    for n in range(9):
        assert await _fail(world) == "DLQ"
        assert world.sub.consecutive_failures == n + 1
    assert world.sub.enabled is True and world.trips == []
    assert world.sub.failure_count == 9

    assert await _fail(world, "/hooks/reject") == "FAILED"  # a 4xx ends FAILED and counts too

    assert world.sub.enabled is False
    assert world.sub.disabled_reason == "circuit_open" and world.sub.disabled_at is not None
    assert len(world.trips) == 1
    trip = world.trips[0]
    assert (trip.subscription_id, trip.consecutive_failures, trip.breaker_threshold) == (world.sub.id, 10, 10)
    assert trip.name == "release hook" and trip.last_error.startswith("HTTP 400")

    # The trip's activity row and notifications, committed with the pause.
    assert [(a.event_type, a.entity_id, a.entity_label, a.actor_type) for a in world.activity] == [
        ("webhook.circuit_opened", str(world.sub.id), "release hook", "system"),
    ]
    assert world.activity[0].context["count"] == 10 and world.activity[0].context["threshold"] == 10
    assert world.activity[0].summary == "Webhook release hook paused after 10 consecutive failures"
    assert world.recipients() == ["kim@example.test", "lee@example.test"]  # editor + leads, once, active
    for note in world.notifications:
        assert (note["channel"], note["event_type"], note["run_id"]) == ("email", "webhook.circuit_opened", None)
        assert note["title"] == "Webhook release hook paused after 10 consecutive failures"
        assert note["body"].endswith(webhook_breaker.REENABLE_HINT)
    # Counted after the commit, once.
    assert world.counter.count == 1
    assert world.events[-1] == "inc" and world.events[-2] == "commit"


@pytest.mark.asyncio
async def test_a_paused_subscription_says_so_and_does_not_trip_again(world):
    world.sub.breaker_threshold = 2
    await _fail(world)
    await _fail(world)
    assert world.sub.disabled_reason == "circuit_open"

    row = world.delivery()
    result = await svc.deliver(row.id)

    assert result == {"error": "subscription paused after 2 consecutive failures"}
    assert row.status == "FAILED" and row.error == "subscription paused after 2 consecutive failures"
    assert world.sub.consecutive_failures == 2, "a delivery refused for a paused subscription counted"
    assert len(world.trips) == 1 and world.counter.count == 1
    assert world.fake.hooks[-1]["path"] == "/hooks/fail" and len(world.fake.hooks) == 2


@pytest.mark.asyncio
async def test_a_success_resets_the_run(world):
    for _ in range(5):
        await _fail(world)
    assert world.sub.consecutive_failures == 5
    world.sub.target_url = f"{RECEIVER}/hooks/ok"
    assert await _deliver_until_settled(world, world.delivery()) == "SUCCESS"
    assert world.sub.consecutive_failures == 0
    for _ in range(9):
        await _fail(world)
    assert world.sub.enabled is True and world.trips == []
    assert world.sub.failure_count == 14, "failure_count keeps counting attempts"


@pytest.mark.asyncio
async def test_a_delivery_retried_until_its_budget_is_spent_counts_once(world):
    world.sub.max_retries = 5
    world.sub.target_url = f"{RECEIVER}/hooks/fail"
    row = world.delivery()

    for attempt in range(1, 5):
        await svc.deliver(row.id)
        assert (row.status, world.sub.consecutive_failures) == ("PENDING", 0), attempt
        row.next_dispatch_at = None
    await svc.deliver(row.id)

    assert row.status == "DLQ" and row.attempt_count == 5
    assert world.sub.consecutive_failures == 1, "a retried delivery counted more than once"
    assert world.sub.failure_count == 5
    assert len([h for h in world.fake.hooks if h["delivery_id"] == str(row.id)]) == 5


@pytest.mark.asyncio
async def test_a_read_timeout_with_retries_left_is_not_a_failure_yet(world, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    monkeypatch.setattr(svc, "get_public_http_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(timeout)))
    world.sub.max_retries = 3
    row = world.delivery()

    result = await svc.deliver(row.id)

    assert result["retry"] is True and row.status == "PENDING"
    assert world.sub.consecutive_failures == 0 and world.sub.failure_count == 1


@pytest.mark.asyncio
async def test_an_egress_refusal_and_a_missing_signing_secret_count_and_trip(world, monkeypatch):
    from app.services import secret_service

    world.sub.breaker_threshold = 2
    monkeypatch.setattr(svc, "_is_safe_public_url", lambda url: (False, "resolves to non-public address 10.0.0.9"))
    refused = world.delivery()

    result = await svc.deliver(refused.id)

    assert result["error"] == "blocked_unsafe_target" and refused.status == "FAILED"
    assert world.sub.consecutive_failures == 1 and world.sub.enabled is True

    monkeypatch.setattr(svc, "_is_safe_public_url", lambda url: (True, "public target"))
    monkeypatch.setattr(secret_service, "read_secret", AsyncMock(return_value=None))
    world.sub.has_secret = True
    unsigned = world.delivery()

    assert await svc.deliver(unsigned.id) == {"error": "signing_secret_unavailable"}
    assert unsigned.status == "FAILED" and world.sub.consecutive_failures == 2
    assert world.sub.disabled_reason == "circuit_open" and len(world.trips) == 1
    assert world.fake.requests == [], "nothing was sent: a refused target and an unsigned body never leave"


@pytest.mark.asyncio
async def test_gated_and_disabled_outcomes_do_not_count(world, monkeypatch):
    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=False))
    gated = world.delivery()
    assert await svc.deliver(gated.id) == {"error": "gated"}
    assert gated.status == "FAILED" and gated.error == "webhooks disabled or offline mode"

    monkeypatch.setattr(svc, "_post_allowed", AsyncMock(return_value=True))
    world.sub.enabled, world.sub.disabled_reason = False, "manual"
    disabled = world.delivery()
    assert await svc.deliver(disabled.id) == {"error": "subscription missing or disabled"}

    assert world.sub.consecutive_failures == 0 and world.sub.failure_count == 0
    assert world.trips == [] and world.fake.requests == []


@pytest.mark.asyncio
async def test_a_failing_commit_leaves_no_trip_row_and_no_increment(world):
    world.sub.breaker_threshold = 1
    world.fail_trip_commit = True

    with pytest.raises(RuntimeError, match="database commit rejected"):
        await _fail(world)

    assert world.activity == [] and world.notifications == []
    assert world.counter.count == 0, "the dashboard showed a trip the database never committed"


@pytest.mark.asyncio
async def test_a_notification_failure_never_fails_the_bookkeeping(world, monkeypatch):
    from app.services.notification import manager

    monkeypatch.setattr(manager, "stage_explicit_notification_deliveries",
                        AsyncMock(side_effect=RuntimeError("notification_logs unavailable")))
    world.sub.breaker_threshold = 1

    assert await _fail(world) == "DLQ"

    assert world.sub.disabled_reason == "circuit_open"
    assert world.activity == [], "the savepoint kept a half-staged activity row"
    assert world.counter.count == 1


@pytest.mark.asyncio
async def test_a_listed_private_host_is_reached_through_the_shared_client(world, monkeypatch):
    shared = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(world.fake)))
    public = AsyncMock(side_effect=AssertionError("the public-only client was used for a listed host"))
    refuse = AsyncMock(side_effect=AssertionError("the public-only check ran for a listed host"))
    monkeypatch.setattr(settings, "OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS", " 10.0.0.5. , other.test")
    monkeypatch.setattr(svc, "get_http_client", lambda: shared)
    monkeypatch.setattr(svc, "get_public_http_client", public)
    monkeypatch.setattr(svc, "_is_safe_public_url", refuse)
    world.sub.target_url = "http://10.0.0.5:18093/hooks/ok"
    row = world.delivery()

    assert await svc.deliver(row.id) == {"status": "SUCCESS", "http_status": 200}
    assert [h["path"] for h in world.fake.hooks] == ["/hooks/ok"]
    await svc._assert_safe_target_url("http://10.0.0.5:18093/hooks/ok")  # no 422


@pytest.mark.asyncio
async def test_an_unlisted_host_is_refused_exactly_as_before(world, monkeypatch):
    from fastapi import HTTPException

    from app.services import url_safety

    monkeypatch.setattr(svc, "_is_safe_public_url", url_safety.is_safe_public_url)  # the real check
    monkeypatch.setattr(settings, "OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS", "10.0.0.5")
    for url in ("http://10.0.0.50/hooks/ok", "http://sub.10.0.0.5/hooks/ok", "http://127.0.0.1/hooks/ok"):
        assert svc._private_subscription_host_listed(url) is False
        with pytest.raises(HTTPException) as exc:
            await svc._assert_safe_target_url(url)
        assert exc.value.status_code == 422 and exc.value.detail.startswith("Webhook target URL rejected: ")
    with pytest.raises(HTTPException):
        await svc._assert_safe_target_url("ftp://10.0.0.5/hooks/ok")


def test_the_breaker_values_are_the_counting_rule():
    now = __import__("datetime").datetime(2026, 10, 23)
    terminal = webhook_breaker.terminal_failure_values("x" * 3000, now)
    retry = webhook_breaker.retry_failure_values("boom", now)
    assert set(terminal) == {"last_failure_at", "last_error", "failure_count", "consecutive_failures"}
    assert len(terminal["last_error"]) == 2000
    assert "consecutive_failures" not in retry
    assert str(terminal["consecutive_failures"].compile()) == "webhook_subscriptions.consecutive_failures + :consecutive_failures_1"
    assert webhook_breaker.SUCCESS_RESET == {"consecutive_failures": 0}
    assert webhook_breaker.reenable_values() == {"consecutive_failures": 0, "disabled_reason": None, "disabled_at": None}
    assert webhook_breaker.DEFAULT_BREAKER_THRESHOLD == WebhookSubscription.__table__.c.breaker_threshold.default.arg
