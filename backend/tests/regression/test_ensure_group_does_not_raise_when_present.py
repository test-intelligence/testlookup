"""Regression: a routine startup condition must not reach observability as an error.

Bug (homelab, 2026-08-16, E-017): ``LiveEventStreamConsumer._ensure_group``
called ``XGROUP CREATE`` unconditionally and swallowed the resulting
``BUSYGROUP Consumer Group name already exists``. The consumer group lives in
Redis and survives pod restarts, so on every deploy after the first this fired
once per uvicorn worker — four times per backend pod.

The application handled it correctly, but the OpenTelemetry Redis
instrumentation records the exception on the span *before* this code catches
it, so each one was exported as an ERROR-level record with a full stack trace.
A condition the code considers entirely normal therefore reached operators as
errors: a log scan of a completely healthy deployment reported four failures
per pod. Error logs that are routinely wrong train people to stop reading them.

The guard is the CLASS — the expected path must not raise at all:

  * when the group already exists, XGROUP CREATE must not be called;
  * BUSYGROUP must still be tolerated, because two workers starting at once is
    a genuine race and the check-then-create is not atomic.

Asserting on the *call* rather than on log output is deliberate. The exception
never reached our logger even when the bug was live — it was recorded by the
tracing layer — so a test that watched ``caplog`` would have passed throughout.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class _FakeRedis:
    """Records what was actually issued to Redis."""

    def __init__(self, *, stream_exists: bool, groups: list[str] | None = None,
                 create_raises: Exception | None = None):
        self._stream_exists = stream_exists
        self._groups = groups or []
        self._create_raises = create_raises
        self.create_calls = 0
        self.xinfo_calls = 0

    async def exists(self, _key):
        return 1 if self._stream_exists else 0

    async def xinfo_groups(self, _key):
        if not self._stream_exists:
            raise RuntimeError("ERR no such key")
        self.xinfo_calls += 1
        return [{"name": name} for name in self._groups]

    async def xgroup_create(self, *_args, **_kwargs):
        self.create_calls += 1
        if self._create_raises is not None:
            raise self._create_raises


async def _ensure(fake) -> None:
    from app.streams.live_consumer import LiveEventStreamConsumer

    with patch("app.streams.live_consumer.get_redis", return_value=fake):
        await LiveEventStreamConsumer()._ensure_group()


@pytest.mark.asyncio
async def test_existing_group_is_not_recreated():
    """The regression itself: no XGROUP CREATE when the group is already there."""
    from app.streams.live_consumer import LIVE_GROUP

    fake = _FakeRedis(stream_exists=True, groups=[LIVE_GROUP])
    await _ensure(fake)

    assert fake.xinfo_calls == 1, "did not inspect the existing groups"
    assert fake.create_calls == 0, (
        "XGROUP CREATE was issued for a group that already exists — this is the "
        "call that raises BUSYGROUP and gets recorded as an ERROR span on every "
        "pod start"
    )


@pytest.mark.asyncio
async def test_group_is_created_when_the_stream_has_none():
    """The check must not become a blanket skip — a missing group is still created."""
    fake = _FakeRedis(stream_exists=True, groups=["some-other-group"])
    await _ensure(fake)

    assert fake.create_calls == 1, "an absent consumer group was never created"


@pytest.mark.asyncio
async def test_missing_stream_creates_without_probing_groups():
    """XINFO GROUPS raises on a missing key, so it must be gated behind EXISTS —
    otherwise the fix would simply trade one guaranteed exception for another."""
    fake = _FakeRedis(stream_exists=False)
    await _ensure(fake)

    assert fake.xinfo_calls == 0, (
        "XINFO GROUPS was issued against a stream that does not exist; that "
        "raises, which re-creates the error-span problem on a first-ever boot"
    )
    assert fake.create_calls == 1, "the stream/group was not created via mkstream"


@pytest.mark.asyncio
async def test_busygroup_race_is_still_tolerated():
    """check-then-create is not atomic; two workers can still collide. That must
    stay silent rather than propagating."""
    fake = _FakeRedis(
        stream_exists=True,
        groups=[],
        create_raises=RuntimeError("BUSYGROUP Consumer Group name already exists"),
    )
    await _ensure(fake)  # must not raise

    assert fake.create_calls == 1


@pytest.mark.asyncio
async def test_a_real_creation_failure_is_still_reported(caplog):
    """Tolerating BUSYGROUP must not turn into tolerating everything."""
    fake = _FakeRedis(
        stream_exists=True,
        groups=[],
        create_raises=RuntimeError("READONLY You can't write against a replica"),
    )
    with caplog.at_level("ERROR"):
        await _ensure(fake)

    assert any("Failed to create consumer group" in r.message for r in caplog.records), (
        "a genuine XGROUP CREATE failure was swallowed silently"
    )
