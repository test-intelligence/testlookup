"""Shared pytest fixtures."""
import os
import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "true")
os.environ.setdefault("OTEL_ENABLED", "false")

# ``Settings.DATABASE_URL`` is the one field with no usable default (""), and
# ``app/db/postgres.py`` parses it at *import* time. Unset, the whole suite dies
# at collection with ``ArgumentError: Could not parse SQLAlchemy URL from string
# ''`` — six files, 26 failures, none of them about the code under test. CI
# exports a URL in the workflow, so CI never saw it; only a local run without
# the Docker stack did, which made "the suite is green" mean different things in
# the two places.
#
# ``setdefault``, so a real URL (CI, Docker, ``tests/integration/``) still wins.
# The host is deliberately ``.invalid`` — an RFC 6761 TLD guaranteed never to
# resolve. Unit tests only need this to *parse*; pointing it at localhost would
# risk a stray test opening a real connection to a developer's own database.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://testlookup:testlookup@db.invalid:5432/testlookup_test",
)


def _make_celery_fail_fast_without_a_broker() -> None:
    """A missing Redis must fail the dispatch, not retry it forever.

    ``backend/CLAUDE.md`` says the local suite does not need live services,
    and every call site that dispatches a Celery task wraps it in
    ``try/except`` on that basis. Celery's Redis **result backend** breaks
    the assumption: ``apply_async`` calls ``ResultConsumer.on_task_call``,
    which enters ``kombu.utils.functional.retry_over_time`` and reconnects
    with no retry ceiling. Nothing is ever raised, so the ``except`` never
    runs and the test blocks at zero CPU indefinitely.

    That is exactly how ``test_release_link_canonical_uuid`` hung the whole
    suite at ~18% on a machine with no local Redis — a hang, not a failure,
    so it produced no output to diagnose.

    The hang is specifically ``ResultConsumer.consume_from`` subscribing to the
    result pubsub channel. Nothing in the unit suite asserts on that
    subscription, so making it a no-op costs nothing and lets ``apply_async``
    fall through to the broker publish, which *does* raise promptly and is
    caught by the handlers that already wrap every dispatch.

    Tuning ``retry_policy`` instead does not work — verified, not assumed. With
    the flat transport options bounded the run still hung, because celery reads
    a *nested* ``retry_policy`` key and the result consumer holds its own
    connection either way.

    This only changes behaviour when there is no broker to talk to; with Redis
    up, the consumer has nothing to retry and nothing here matters.
    """
    try:
        from celery.backends.redis import ResultConsumer
    except Exception:  # pragma: no cover - celery not installed locally
        return

    ResultConsumer.consume_from = lambda self, task_id: None


_make_celery_fail_fast_without_a_broker()


def pytest_collection_modifyitems(config, items):
    """Apply coarse-grained markers from test location/name."""
    integration_marker = pytest.mark.integration
    live_marker = pytest.mark.live
    for item in items:
        path = str(item.path).replace("\\", "/")
        if "/tests/integration/" in path:
            item.add_marker(integration_marker)
        if path.endswith("/tests/test_performance_budgets_live.py"):
            item.add_marker(integration_marker)
            item.add_marker(live_marker)


@dataclass
class FakeExecuteResult:
    """Small helper that mimics SQLAlchemy execute results."""

    scalar_value: object = None
    one_value: object = None
    all_value: object = None
    fetchall_value: object = None

    def scalar(self):
        return self.scalar_value

    def scalar_one_or_none(self):
        return self.scalar_value

    def one(self):
        return self.one_value

    def all(self):
        return self.all_value or []

    def fetchall(self):
        return self.fetchall_value or []


class FakeRedis:
    """In-memory async Redis-like store for unit tests.

    P5-1: Now tracks TTL via ``_expiry`` dict so that ``set(..., ex=N)``
    and ``setex()`` behave correctly — expired keys return None from
    ``get()`` instead of silently succeeding.
    """

    def __init__(self):
        self._data: dict[str, object] = {}
        self._expiry: dict[str, float] = {}

    def _is_expired(self, key: str) -> bool:
        if key in self._expiry and time.monotonic() > self._expiry[key]:
            self._data.pop(key, None)
            self._expiry.pop(key, None)
            return True
        return False

    async def get(self, key: str):
        if self._is_expired(key):
            return None
        return self._data.get(key)

    async def set(self, key: str, value, ex=None):
        if isinstance(value, str):
            value = value.encode()
        self._data[key] = value
        if ex is not None:
            self._expiry[key] = time.monotonic() + ex
        elif key in self._expiry:
            del self._expiry[key]
        return True

    async def setex(self, key: str, ttl: int, value):
        """SET with mandatory TTL (Redis SETEX command)."""
        return await self.set(key, value, ex=ttl)

    async def expire(self, key: str, seconds: int) -> bool:
        """Set or refresh TTL on an existing key."""
        if key in self._data:
            self._expiry[key] = time.monotonic() + seconds
            return True
        return False

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if self._data.pop(key, None) is not None:
                count += 1
            self._expiry.pop(key, None)
        return count

    async def exists(self, key: str) -> int:
        if self._is_expired(key):
            return 0
        return 1 if key in self._data else 0

    async def xlen(self, stream: str) -> int:
        """Return length of a stream (stub: always 0)."""
        return 0

    async def scan(self, cursor: int = 0, match: str = "*", count: int = 100):
        """SCAN stub — returns all matching keys in a single pass."""
        import fnmatch
        matched = [k for k in self._data if fnmatch.fnmatch(k, match)]
        return (0, matched)

    def pipeline(self):
        """Return a pipeline stub that collects commands."""
        return _FakePipeline(self)


class _FakePipeline:
    """Minimal pipeline mock for FakeRedis."""

    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops: list = []

    def xadd(self, stream, fields, maxlen=None, approximate=True):
        self._ops.append(("xadd", stream, fields))
        return self

    async def execute(self):
        return [f"msg-{i}" for i in range(len(self._ops))]


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def ns():
    """Convenience namespace factory."""
    return SimpleNamespace
