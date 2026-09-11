"""R-B45-R2-4: the Jira replay window fails CLOSED.

The dedupe record (sha256 of the signed body) expires; the body's own
``timestamp`` is what bounds a replay after that. The window used to wave
through a body with no numeric timestamp (missing, null, string, NaN), so a
captured delivery could be re-applied once its record expired. A body stamped
ahead of our clock stayed "inside" the window after its record expired too.

The end-to-end behaviour (200 ``applied: false``, the defect unchanged) is in
tests/integration/test_jira_webhook_signature_postgres.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routers import feedback as fb

NOW = 1_800_000_000.0  # seconds
WINDOW = fb.JIRA_REPLAY_WINDOW_SECONDS
SKEW = fb.JIRA_CLOCK_SKEW_SECONDS


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(fb, "time", SimpleNamespace(time=lambda: NOW))


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


@pytest.mark.parametrize("payload", [
    {},
    {"timestamp": None},
    {"timestamp": str(_ms(NOW))},
    {"timestamp": True},
    {"timestamp": float("nan")},
    {"timestamp": float("inf")},
    {"timestamp": float("-inf")},
    {"timestamp": 10 ** 400},                       # float() overflows
    {"timestamp": [_ms(NOW)]},
    {"timestamp": _ms(NOW - WINDOW - 60)},          # too old
    {"timestamp": _ms(NOW + SKEW + 60)},            # stamped in the future
    {"timestamp": _ms(NOW + 365 * 24 * 3600)},
], ids=[
    "missing", "null", "string", "bool", "nan", "inf", "-inf", "huge-int", "list",
    "older-than-window", "future-beyond-skew", "a-year-ahead",
])
def test_a_body_whose_replay_cannot_be_bounded_is_refused(payload):
    assert fb._delivery_outside_window(payload)


@pytest.mark.parametrize("stamp", [
    _ms(NOW), float(_ms(NOW)), _ms(NOW - WINDOW + 60), _ms(NOW + SKEW - 60), _ms(NOW - 5),
], ids=["now-int", "now-float", "near-window-edge", "ahead-within-skew", "just-now"])
def test_a_body_inside_the_window_is_accepted(stamp):
    assert fb._delivery_outside_window({"timestamp": stamp}) is None


def test_the_reasons_say_which_bound_refused():
    assert "no numeric timestamp" in fb._delivery_outside_window({})
    assert "older than the replay window" in fb._delivery_outside_window({"timestamp": _ms(NOW - WINDOW - 1)})
    assert "future" in fb._delivery_outside_window({"timestamp": _ms(NOW + SKEW + 1)})


@pytest.mark.asyncio
async def test_the_dedupe_record_outlives_every_moment_its_body_is_accepted(monkeypatch):
    """A body accepted at arrival must not be acceptable after its record expires."""
    captured: dict = {}

    class Redis:
        async def set(self, key, value, *, nx, ex):
            captured.update(nx=nx, ex=ex)
            return True

    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: Redis())
    assert await fb._claim_delivery(b'{"timestamp": 1}') == (True, fb._JIRA_DELIVERY_KEY + __import__(
        "hashlib").sha256(b'{"timestamp": 1}').hexdigest())
    assert captured["nx"] is True
    ttl = captured["ex"]

    arrival = NOW
    for stamp_s in (arrival - WINDOW, arrival - 60, arrival, arrival + SKEW):
        monkeypatch.setattr(fb, "time", SimpleNamespace(time=lambda: arrival))
        assert fb._delivery_outside_window({"timestamp": _ms(stamp_s)}) is None, stamp_s
        # the first moment the record is gone, the body must be refused
        monkeypatch.setattr(fb, "time", SimpleNamespace(time=lambda: arrival + ttl + 1))
        assert fb._delivery_outside_window({"timestamp": _ms(stamp_s)}), (stamp_s, ttl)
