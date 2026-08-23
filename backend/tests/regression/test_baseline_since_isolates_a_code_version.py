"""Regression guard: `--since` can isolate one code version.

Why it exists
-------------
The collector's window was ``started_at >= now() - make_interval(days => N)``,
so the finest slice available was **one day**. On 2026-08-23 three fixes
deployed hours apart, and a "post-fix" baseline taken with ``--days 1`` would
have been **304 of 354 runs pre-fix** — 86% of it measuring the code the
baseline was supposed to exclude.

A baseline that silently blends code versions is worse than no baseline: it
looks measured, and the number it produces gets quoted.

What is guarded
---------------
* ``--since`` parses ISO timestamps, with or without ``Z``;
* a naive timestamp is treated as UTC rather than local time, so the same
  command means the same window on a laptop and in a container;
* a malformed value is rejected rather than silently ignored — falling back to
  ``--days`` would produce exactly the blended baseline this prevents;
* the chosen bound is recorded in the artifact, so a committed file says what
  window it covered;
* ``--days`` still works untouched when ``--since`` is absent.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "pipeline"
sys.path.insert(0, str(_BENCH))

import collect as collect_mod  # noqa: E402


# ── Parsing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("2026-08-23T06:05:00Z", datetime(2026, 8, 23, 6, 5, tzinfo=timezone.utc)),
    ("2026-08-23T06:05:00+00:00", datetime(2026, 8, 23, 6, 5, tzinfo=timezone.utc)),
    ("2026-08-23T06:05:00", datetime(2026, 8, 23, 6, 5, tzinfo=timezone.utc)),
])
def test_since_accepts_iso_timestamps_and_defaults_to_utc(raw, expected, monkeypatch):
    captured = {}

    async def _fake_collect(dsn, **kwargs):
        captured.update(kwargs)
        return {"runs_observed": 1, "window": {}}

    monkeypatch.setattr(collect_mod, "collect", _fake_collect)
    rc = collect_mod.main(["--database-url", "postgresql://x/y", "--since", raw])

    assert rc == 0
    assert captured["since"] == expected


def test_a_malformed_since_is_rejected_not_ignored(monkeypatch, capsys):
    """Falling back to --days would produce the blended baseline silently."""
    called = False

    async def _fake_collect(dsn, **kwargs):
        nonlocal called
        called = True
        return {"runs_observed": 1, "window": {}}

    monkeypatch.setattr(collect_mod, "collect", _fake_collect)
    rc = collect_mod.main(["--database-url", "postgresql://x/y", "--since", "yesterday"])

    assert rc == 2
    assert not called, "a bad --since must not fall through to a --days window"
    assert "not an ISO timestamp" in capsys.readouterr().err


# ── The window actually used ─────────────────────────────────────────────────


class _FakeConn:
    """Captures the bound actually handed to Postgres."""

    def __init__(self, sink):
        self.sink = sink

    async def fetch(self, sql, *args):
        self.sink.append((sql, args))
        return []

    async def close(self):
        return None


def _fake_asyncpg(sink):
    import types

    mod = types.SimpleNamespace()

    async def _connect(_dsn):
        return _FakeConn(sink)

    mod.connect = _connect
    return mod


@pytest.mark.asyncio
async def test_the_bound_handed_to_postgres_is_the_since_value(monkeypatch):
    """Not the flag — the actual first SQL parameter."""
    sink: list = []
    monkeypatch.setitem(sys.modules, "asyncpg", _fake_asyncpg(sink))
    since = datetime(2026, 8, 23, 6, 5, tzinfo=timezone.utc)

    await collect_mod.collect(
        "postgresql://x/y", days=30, workflow_type=None, limit=10, since=since,
    )

    assert sink, "no query was issued"
    _sql, args = sink[0]
    assert args[0] == since, (
        "the --days window would have been used instead of the explicit bound"
    )


@pytest.mark.asyncio
async def test_without_since_the_bound_comes_from_days(monkeypatch):
    sink: list = []
    monkeypatch.setitem(sys.modules, "asyncpg", _fake_asyncpg(sink))

    await collect_mod.collect(
        "postgresql://x/y", days=7, workflow_type=None, limit=10,
    )

    _sql, args = sink[0]
    bound = args[0]
    age_days = (datetime.now(timezone.utc) - bound).total_seconds() / 86400
    assert 6.9 < age_days < 7.1, f"expected a ~7-day bound, got {age_days}"


def test_the_artifact_records_the_bound_it_used():
    """A committed baseline must say what window it covered."""
    import inspect

    src = inspect.getsource(collect_mod.collect)
    assert '"since": since.isoformat() if since else None' in src, (
        "the window block must record --since or the file cannot be interpreted"
    )
    assert "cutoff = since or (" in src, "since must override the days bound"


def test_the_sql_takes_an_explicit_lower_bound():
    """A day-count in SQL cannot express a mid-day boundary."""
    assert "started_at >= $1" in collect_mod._RUNS_SQL
    assert "make_interval" not in collect_mod._RUNS_SQL


def test_days_still_works_when_since_is_absent(monkeypatch):
    captured = {}

    async def _fake_collect(dsn, **kwargs):
        captured.update(kwargs)
        return {"runs_observed": 1, "window": {}}

    monkeypatch.setattr(collect_mod, "collect", _fake_collect)
    rc = collect_mod.main(["--database-url", "postgresql://x/y", "--days", "7"])

    assert rc == 0
    assert captured["since"] is None
    assert captured["days"] == 7
