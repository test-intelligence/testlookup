"""Shared pytest fixtures."""
import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest


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
