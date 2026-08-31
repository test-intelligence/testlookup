"""Regression tests for integration-health metric/DB consistency."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.core import metrics  # noqa: E402
from app.db import postgres  # noqa: E402
from app.services import integration_probe_service  # noqa: E402

pytestmark = pytest.mark.regression


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _CommitFailingSession:
    def __init__(self) -> None:
        self.health = SimpleNamespace(
            status="healthy",
            last_checked_at=None,
            message="previous",
            response_ms=12,
            consecutive_failures=0,
            last_success_at=None,
        )

    async def __aenter__(self) -> _CommitFailingSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(self.health)

    def add(self, _value: object) -> None:
        return None

    async def commit(self) -> None:
        raise RuntimeError("database commit rejected")


class _RecordingGauge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float | None]] = []
        self.provider = ""

    def labels(self, *, provider: str) -> _RecordingGauge:
        self.provider = provider
        return self

    def set(self, value: float) -> None:
        self.calls.append(("set", self.provider, value))

    def remove(self, provider: str) -> None:
        self.calls.append(("remove", provider, None))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["healthy", "skipped"])
async def test_prometheus_health_is_not_changed_when_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    """Dashboards must not advertise a health state the API never committed."""
    db = _CommitFailingSession()
    gauge = _RecordingGauge()
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(metrics, "integration_health_gauge", gauge)

    result = integration_probe_service.ProbeResult(
        "jira", status, response_ms=7, message="new verdict"
    )
    with pytest.raises(RuntimeError, match="database commit rejected"):
        await integration_probe_service.persist_probe_results([result])

    assert gauge.calls == [], (
        "Prometheus changed before the matching database transaction committed"
    )
