"""Shared pytest fixtures."""
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
    """In-memory async Redis-like store for unit tests."""

    def __init__(self):
        self._data: dict[str, object] = {}

    async def get(self, key: str):
        return self._data.get(key)

    async def set(self, key: str, value, ex=None):
        if isinstance(value, str):
            value = value.encode()
        self._data[key] = value
        return True

    async def delete(self, key: str):
        self._data.pop(key, None)
        return 1


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def ns():
    """Convenience namespace factory."""
    return SimpleNamespace
