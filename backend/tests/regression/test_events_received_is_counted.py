"""A field published to consumers must be written by something.

Found by driving a controlled live-stream session against the homelab: eight
test_result events plus run_start/run_complete, all accepted::

    POST /api/v1/stream/events/batch -> 202 {"accepted": 10, ...}
    GET  /api/v1/stream/sessions/{id} -> {"events_received": 0, ...}
    psql: select events_received from live_sessions -> 0

`events_received` exists in migration 0008, on the ORM model, in
`architecture/DATABASE_SCHEMA.md`, in the API response
(`stream_service.get_session`) and in `frontend/src/types/live-stream.ts`.
Nothing in the product ever incremented it.

What kept it from being noticed: `backend/scripts/seed_dev_data.py` writes a
plausible non-zero value (`sum(tests) * 3`), so demo data shows the field
populated while every real session reports 0. A defect that is invisible in the
data you demo with, and wrong in the data you run with.

Same class as the summary agent's `_fallback_used`, which was read from a key
nothing wrote: **a value reported to consumers that no code path produces.**

The counter now lives on the Redis state hash the ingest path already writes —
one extra pipeline op, no database round trip per batch — and is copied onto
the column when the session closes, so it survives the hash's TTL.

The guards are: the count is incremented on ingest, it is readable *during* the
run (not only after close), it is persisted at close, and — the class ratchet —
every field in the live-session response is produced by something.
"""
from __future__ import annotations

import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
STREAM_SERVICE = BACKEND / "app" / "services" / "stream_service.py"
LIVE_RUN_STATE = BACKEND / "app" / "streams" / "live_run_state.py"


# ── The count is actually produced ──────────────────────────────────────────


def test_the_ingest_path_increments_the_counter():
    """The regression. Nothing wrote this field at all."""
    src = STREAM_SERVICE.read_text(encoding="utf-8")
    body = src.split("async def _persist_event_batch")[1].split("\nasync def ")[0]
    assert re.search(r'hincrby\(\s*state_key,\s*["\']events_received["\']', body), (
        "_persist_event_batch does not increment events_received, so the field "
        "the API publishes stays 0 for every real session"
    )


def test_keepalives_are_not_counted_as_events():
    """`live_heartbeat` exists only to bump last_event_at for the idle reaper.
    The first version of this fix incremented on every accepted event, which
    broke the standing contract that a heartbeat-only batch touches no counter
    at all — and would have inflated the figure with idle noise on any run with
    long gaps between tests."""
    src = STREAM_SERVICE.read_text(encoding="utf-8")
    body = src.split("async def _persist_event_batch")[1].split("\nasync def ")[0]
    assert "live_heartbeat" in body, (
        "_persist_event_batch counts events without excluding keepalives"
    )
    assert re.search(r"hincrby\(\s*state_key,\s*[\"']events_received[\"']", body)
    assert not re.search(
        r'hincrby\(\s*state_key,\s*["\']events_received["\'],\s*accepted\s*\)', body
    ), "counting `accepted` includes heartbeats"


def test_the_counter_is_typed_as_an_int_when_read_back():
    """Redis hashes are strings. Left out of the int projection, the API would
    report "10" where the schema and the frontend type both say number."""
    src = LIVE_RUN_STATE.read_text(encoding="utf-8")
    block = src.split("int_fields")[1].split("}")[0]
    assert "events_received" in block


def test_the_count_is_readable_while_the_run_is_still_going():
    """The column is only written at close. Reading it alone reports 0 for the
    entire duration of the run it describes — which is exactly when a client
    watching ingest progress would be asking."""
    src = STREAM_SERVICE.read_text(encoding="utf-8")
    body = src.split("async def get_session")[1].split("\nasync def ")[0]
    assert "live_stats.get(\"events_received\")" in body, (
        "get_session reads only the persisted column, so an in-flight session "
        "reports 0 events received no matter how many arrived"
    )


def test_the_count_is_persisted_before_redis_expires_it():
    """The Redis hash drops to a 1-hour TTL at completion. Without copying the
    value onto the row, the field goes back to 0 an hour after every run."""
    src = STREAM_SERVICE.read_text(encoding="utf-8")
    body = src.split("async def close_session")[1].split("\nasync def ")[0]
    assert re.search(
        r"session\.events_received\s*=.*state\.get\(\s*[\"']events_received[\"']", body
    ), "close_session never copies the live counter onto the row"


# ── The class ratchet ───────────────────────────────────────────────────────


def _published_fields() -> set[str]:
    """Keys of the dict get_session returns to API clients."""
    src = STREAM_SERVICE.read_text(encoding="utf-8")
    body = src.split("async def get_session")[1].split("\nasync def ")[0]
    ret = body.split("return {")[1]
    return set(re.findall(r'^\s*"([a-z_]+)":', ret, re.MULTILINE))


# Fields sourced directly from the session row or computed in the responder.
# Anything NOT here has to be traceable to a writer.
_ROW_BACKED = {
    "session_id", "run_id", "project_id", "client_name", "machine_id",
    "build_number", "framework", "branch", "status", "total_tests",
    "started_at", "completed_at", "live_stats",
}


def test_every_published_field_has_a_producer():
    """The class. `events_received` was published for as long as the endpoint
    existed and produced by nothing; the only reason it looked fine was seed
    data. Any future field added to this response must be written somewhere in
    app/ other than its own declaration."""
    app_dir = BACKEND / "app"
    orphans = []
    for field in _published_fields() - _ROW_BACKED:
        writers = 0
        for path in app_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            # A write looks like an assignment, an hincrby, or a mapping entry —
            # not merely the name appearing in the response dict.
            if re.search(rf"\.{field}\s*=", text) or re.search(
                rf'hincrby\([^)]*["\']{field}["\']', text
            ):
                writers += 1
        if writers == 0:
            orphans.append(field)
    assert not orphans, (
        "live-session response fields that nothing in app/ ever writes: "
        f"{sorted(orphans)}"
    )


def test_the_orphan_ratchet_can_actually_fail():
    """A checker that found no fields to inspect would pass forever. Pin that
    it sees the response and that the field under test is in scope."""
    fields = _published_fields()
    assert "events_received" in fields, "the response shape moved; ratchet is blind"
    assert len(fields) > 8, f"only parsed {len(fields)} fields — parser is broken"


# ── Seed data must not disguise it again ────────────────────────────────────


def test_seed_data_does_not_invent_an_events_count():
    """`seed_dev_data` wrote `sum(tests) * 3`, which made the field look alive
    in every demo while real sessions reported 0. Seeded values that cannot be
    produced by the real path hide exactly this kind of defect."""
    seed = BACKEND / "scripts" / "seed_dev_data.py"
    if not seed.exists():  # pragma: no cover - layout guard
        pytest.skip("seed_dev_data.py not found")
    text = seed.read_text(encoding="utf-8")
    m = re.search(r"events_received\s*=\s*([^,\n]+)", text)
    if m is None:
        return  # not seeded at all — fine
    expr = m.group(1).strip()
    assert "* 3" not in expr, (
        f"seed writes a fabricated events_received ({expr}); it should reflect "
        "the events the seeded session actually represents"
    )
