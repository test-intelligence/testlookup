"""The intelligence snapshot must be JSON-encodable, and a failed cache write
must never fail the request.

Found live on the homelab. ``POST /api/v1/runs/{run_id}/intelligence/refresh``
returned **500**:

```
TypeError: Object of type datetime is not JSON serializable
  [SQL: INSERT INTO run_intelligence_snapshots (..., payload, ...)]
...then...
sqlalchemy.exc.PendingRollbackError: This Session's transaction has been rolled
back due to a previous exception during flush.
```

Two distinct defects stacked:

1. **The payload was not JSON-safe.** ``run_intelligence_snapshots.payload`` is
   a JSON column, but the intelligence payload is assembled partly from
   MongoDB, which returns BSON dates as real ``datetime`` objects. Walking a
   live payload found exactly two:
   ``structured_summary.generated_at`` and ``provenance.generated_at``.

2. **The failure was not contained.** The refresh handler wrapped the cache
   write in ``except Exception: pass`` on the **injected request session**.
   Swallowing the exception does not undo the failed flush — the session is
   left rolled back, so the next use of it raises ``PendingRollbackError`` and
   the endpoint 500s. A best-effort write on a shared session cannot be made
   best-effort by catching, only by not sharing the session. The GET handler
   already knew this and used a dedicated write session, with a comment saying
   why; refresh had drifted from it.

Measured blast radius before the fix: **all 56 stored run summaries carried
``generated_at``**, so every run with an AI summary both failed to cache its
snapshot (silently recomputing on every read) and returned 500 on refresh.
The reported run had zero rows in ``run_intelligence_snapshots``.

The guards are the CLASS:
  * anything persisted into the JSON column is coerced to JSON-native types at
    that boundary, whatever field is added later;
  * the cache write never runs on the caller's session, so it cannot poison it.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence_snapshot_service import save_snapshot

RUN_ID = uuid.UUID("bd337e00-38ae-50ee-b2e5-0f21077a711b")

# The live payload shape, including the two fields that actually broke it.
LIVE_SHAPED_PAYLOAD = {
    "run": {"id": str(RUN_ID), "build_number": "sdk-probe-1"},
    "structured_summary": {
        "executive_summary": "4 tests failed",
        "generated_at": datetime(2026, 8, 10, 6, 17, 38, 942000),  # naive, from Mongo
    },
    "provenance": {
        "fallback_used": False,
        "generated_at": datetime(2026, 8, 10, 6, 17, 38, 942000, tzinfo=timezone.utc),
    },
    "category_breakdown": {"PRODUCT_BUG": 2},
}


class _FakeSession:
    """Captures what save_snapshot would persist, without a database."""

    def __init__(self, existing=None):
        self.added = []
        self.committed = False
        self._existing = existing

    async def execute(self, *_a, **_kw):
        res = MagicMock()
        res.scalar_one_or_none = MagicMock(return_value=self._existing)
        return res

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


async def _save(payload, existing=None):
    session = _FakeSession(existing=existing)
    with patch(
        "app.services.intelligence_snapshot_service.attach_memory_reference_manifest",
        new=AsyncMock(side_effect=lambda db, snapshot_id, run_id, payload: payload),
    ):
        await save_snapshot(session, RUN_ID, payload)
    return session


# ── 1. The payload must be JSON-encodable ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_payload_carrying_mongo_datetimes_is_persistable():
    """The regression. This exact shape raised TypeError at flush."""
    session = await _save(LIVE_SHAPED_PAYLOAD)
    assert len(session.added) == 1
    stored = session.added[0].payload
    # The real assertion: the driver would have to serialise this.
    json.dumps(stored)


@pytest.mark.asyncio
async def test_the_two_fields_that_broke_it_survive_as_iso_strings():
    """Encoding must convert, not drop — the timestamps still have to be there
    and still have to mean the same instant."""
    session = await _save(LIVE_SHAPED_PAYLOAD)
    stored = session.added[0].payload
    ss = stored["structured_summary"]["generated_at"]
    pv = stored["provenance"]["generated_at"]
    assert isinstance(ss, str) and ss.startswith("2026-08-10T06:17:38")
    assert isinstance(pv, str) and pv.startswith("2026-08-10T06:17:38")


@pytest.mark.asyncio
async def test_encoding_reaches_arbitrarily_deep_and_covers_other_bson_types():
    """A fix that special-cased the two known keys would pass the tests above
    and break again on the next Mongo-sourced field."""
    payload = {
        "a": {"b": [{"c": {"when": datetime(2026, 1, 2, 3, 4, 5)}}]},
        "id": uuid.uuid4(),
    }
    session = await _save(payload)
    stored = session.added[0].payload
    json.dumps(stored)
    assert stored["a"]["b"][0]["c"]["when"].startswith("2026-01-02T03:04:05")
    assert isinstance(stored["id"], str)


@pytest.mark.asyncio
async def test_the_update_path_is_encoded_too():
    """save_snapshot has two branches — insert and update. Only encoding one
    would leave every re-save of an existing snapshot broken."""
    existing = SimpleNamespace(
        id=uuid.uuid4(), payload=None, schema_version=0,
        fallback_used=False, stale=True, generated_at=None,
    )
    await _save(LIVE_SHAPED_PAYLOAD, existing=existing)
    json.dumps(existing.payload)


@pytest.mark.asyncio
async def test_a_plain_payload_is_unchanged():
    """Encoding must not rewrite data that was already fine."""
    plain = {"run": {"id": "x"}, "counts": {"a": 1}, "list": [1, "two", None, True]}
    session = await _save(plain)
    assert session.added[0].payload == plain


# ── 2. A failed cache write must not fail the request ───────────────────────


@pytest.mark.asyncio
async def test_refresh_survives_a_failing_snapshot_write():
    """Even with the encoder in place, the cache write is best-effort. If it
    fails for any future reason the endpoint must still return its payload."""
    from app.routers.run_intelligence import refresh_intelligence

    computed = {"run": {"id": str(RUN_ID)}, "provenance": {"fallback_used": False}}
    with patch("app.routers.run_intelligence.get_mongo_db", MagicMock()), \
         patch("app.routers.run_intelligence.invalidate", new=AsyncMock(return_value=True)), \
         patch("app.routers.run_intelligence.get_run_intelligence",
               new=AsyncMock(return_value=computed)), \
         patch("app.routers.run_intelligence.save_snapshot",
               new=AsyncMock(side_effect=RuntimeError("flush blew up"))):
        out = await refresh_intelligence(run_id=RUN_ID, db=MagicMock(), _=None)

    assert out["run"]["id"] == str(RUN_ID)
    assert out["_snapshot"]["just_refreshed"] is True


@pytest.mark.asyncio
async def test_the_cache_write_never_runs_on_the_callers_session():
    """The containment guard. Catching the error is not enough on a shared
    session — a failed flush leaves it rolled back and the NEXT use raises
    PendingRollbackError. The write must use a session of its own."""
    from app.routers.run_intelligence import refresh_intelligence

    request_session = MagicMock(name="injected_request_session")
    seen = {}

    async def _capture(db, *_a, **_kw):
        seen["session"] = db

    with patch("app.routers.run_intelligence.get_mongo_db", MagicMock()), \
         patch("app.routers.run_intelligence.invalidate", new=AsyncMock(return_value=True)), \
         patch("app.routers.run_intelligence.get_run_intelligence",
               new=AsyncMock(return_value={"provenance": {"fallback_used": False}})), \
         patch("app.routers.run_intelligence.save_snapshot", new=_capture):
        await refresh_intelligence(run_id=RUN_ID, db=request_session, _=None)

    assert "session" in seen, "save_snapshot was never called"
    assert seen["session"] is not request_session, (
        "the snapshot write ran on the injected request session — a failed "
        "flush there poisons the transaction and 500s the endpoint"
    )
