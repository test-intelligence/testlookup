"""HYG-06 (a) — the pure live-session audit decision (``services/ws_audit_policy.py``).

Contract §2.2 as revised by the orchestrator (append-only; ``backend.audit-write-discipline`` stays intact):
a connection starting within 60 s of the session's last disconnect is a continuation (exactly 60 s continues,
60.001 s starts a new session); a session gets AT MOST ONE row, inserted at the first disconnect where its
accumulated connected time reaches 5 s (exactly 5 s counts); after that a continuation never writes (``merged``);
a session that never reaches 5 s writes nothing (``short``). Times are built INSIDE each test.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from app.services import ws_audit_policy as policy
from app.services.ws_audit_policy import Decision, decide

MODULE = Path(policy.__file__)


def _session(started_at, ended_at, connected_ms, connections=1, audited=False) -> dict:
    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "connected_ms": connected_ms,
        "connections": connections,
        "audited": audited,
    }


# ── A new session (no previous one, or the previous one is more than 60 s old) ──────────────────────────


def test_no_session_and_a_long_connection_inserts_the_sessions_row():
    s = time.time()
    d = decide(None, s, s + 42.4)
    assert d == Decision("insert", _session(s, s + 42.4, 42_400))
    assert d.outcome == "audited"


def test_no_session_and_a_short_connection_is_metric_only():
    s = time.time()
    d = decide(None, s, s + 3)
    assert d == Decision("short", _session(s, s + 3, 3000))
    assert d.outcome == "short"


def test_exactly_five_seconds_is_audited_and_just_under_is_short():
    s = time.time()
    assert decide(None, s, s + 5).kind == "insert"
    assert decide(None, s, s + 4.999).kind == "short"


def test_a_connection_after_the_window_starts_a_new_session_even_after_an_audited_one():
    end = time.time()
    prev = _session(end - 30, end, 30_000, connections=2, audited=True)
    s = end + 60.001
    assert decide(prev, s, s + 12) == Decision("insert", _session(s, s + 12, 12_000))


def test_a_short_connection_after_the_window_is_metric_only():
    end = time.time()
    prev = _session(end - 30, end, 30_000, connections=2, audited=True)
    s = end + 61
    assert decide(prev, s, s + 2) == Decision("short", _session(s, s + 2, 2000))


# ── A continuation of an audited session never writes ───────────────────────────────────────────────────


@pytest.mark.parametrize("length", [0.5, 4.999, 5, 300])
def test_a_continuation_of_an_audited_session_merges_whatever_its_length(length):
    end = time.time()
    prev = _session(end - 20, end, 20_000, connections=1, audited=True)
    s = end + 10
    d = decide(prev, s, s + length)
    assert d == Decision(
        "merge", _session(end - 20, s + length, 20_000 + round(length * 1000), connections=2, audited=True)
    )
    assert d.outcome == "merged"


def test_a_reconnect_exactly_sixty_seconds_after_the_last_disconnect_continues_the_session():
    end = time.time()
    prev = _session(end - 20, end, 20_000, connections=3, audited=True)
    s = end + policy.RECONNECT_WINDOW_SECONDS
    d = decide(prev, s, s + 1)
    assert d.kind == "merge"
    assert d.session["connections"] == 4
    assert d.session["started_at"] == end - 20


def test_an_overlapping_second_tab_folds_into_the_session_and_keeps_the_widest_span():
    """Two tabs of one user on one project: the second tab's connection started BEFORE the first one ended."""
    now = time.time()
    prev = _session(now - 50, now - 10, 40_000, connections=1, audited=True)
    d = decide(prev, now - 70, now)
    assert d == Decision("merge", _session(now - 70, now, 110_000, connections=2, audited=True))


# ── A continuation of a session without a row yet: accumulated connected time decides ──────────────────


def test_three_seconds_then_four_seconds_inserts_at_the_second_disconnect():
    t = time.time()
    first = decide(None, t, t + 3)
    assert first.kind == "short"
    second = decide(first.session, t + 4, t + 8)
    assert second == Decision("insert", _session(t, t + 8, 7000, connections=2))


def test_a_continuation_still_under_five_seconds_of_connected_time_stays_metric_only():
    end = time.time()
    prev = _session(end - 1, end, 1000, connections=2)
    s = end + 1
    assert decide(prev, s, s + 1) == Decision("short", _session(end - 1, s + 1, 2000, connections=3))


def test_connected_time_not_the_span_decides():
    """Two 2 s connections 50 s apart span 54 s but were connected for 4 s: still short."""
    t = time.time()
    first = decide(None, t, t + 2)
    second = decide(first.session, t + 52, t + 54)
    assert second.kind == "short"
    assert second.session["connected_ms"] == 4000


def test_accumulated_connected_time_is_inclusive_at_five_seconds():
    t = time.time()
    first = decide(None, t, t + 2.5)
    assert decide(first.session, t + 3, t + 5.5).kind == "insert"


# ── Whole sessions, connection by connection ────────────────────────────────────────────────────────────


def _play(connections: list[tuple[float, float]]) -> list[str]:
    """Feed connections through ``decide`` the way live.py does (an insert that commits marks the session)."""
    kinds: list[str] = []
    prev = None
    for s, e in connections:
        d = decide(prev, s, e)
        kinds.append(d.kind)
        prev = dict(d.session)
        if d.kind == "insert":
            prev["audited"] = True
    return kinds


def test_a_long_watch_then_three_refreshes_is_one_insert_and_three_merges():
    t = time.time()
    conns = [(t, t + 30), (t + 31, t + 32), (t + 33, t + 34), (t + 35, t + 90)]
    assert _play(conns) == ["insert", "merge", "merge", "merge"]


def test_three_quick_refreshes_then_a_long_watch_write_one_row_at_the_end():
    t = time.time()
    conns = [(t, t + 1), (t + 2, t + 3), (t + 4, t + 4.5), (t + 5, t + 125)]
    assert _play(conns) == ["short", "short", "short", "insert"]


def test_four_quick_refreshes_under_five_seconds_in_all_write_nothing():
    t = time.time()
    conns = [(t, t + 1), (t + 1.2, t + 2), (t + 2.2, t + 3), (t + 3.2, t + 4)]
    assert _play(conns) == ["short", "short", "short", "short"]


def test_a_failed_insert_leaves_the_session_unaudited_so_the_next_continuation_inserts():
    t = time.time()
    failed = decide(None, t, t + 10)  # the caller's INSERT fails: it keeps audited False
    assert failed.kind == "insert" and failed.session["audited"] is False
    assert decide(failed.session, t + 11, t + 12).kind == "insert"


# ── Stored-state hygiene: Redis holds whatever an older or broken writer put there ──────────────────────

# Each builder takes the last disconnect of a session that, WELL-FORMED, is audited — so the next connection
# would MERGE (write nothing). An insert proves the malformed value was ignored rather than half-read.
_MALFORMED = {
    "str": lambda end: "not-a-dict",
    "list": lambda end: [end - 10, end, 10_000, 1, True],
    "empty": lambda end: {},
    "text-time": lambda end: {"started_at": "soon", "ended_at": end, "connected_ms": 10_000, "connections": 1, "audited": True},
    "no-connected": lambda end: {"started_at": end - 10, "ended_at": end, "connections": 1, "audited": True},
    "zero-connections": lambda end: {"started_at": end - 10, "ended_at": end, "connected_ms": 10_000, "connections": 0, "audited": True},
    "negative-connected": lambda end: {"started_at": end - 10, "ended_at": end, "connected_ms": -1, "connections": 1, "audited": True},
    "nan": lambda end: {"started_at": float("nan"), "ended_at": end, "connected_ms": 10_000, "connections": 1, "audited": True},
    "ends-before-start": lambda end: {"started_at": end + 5, "ended_at": end, "connected_ms": 10_000, "connections": 1, "audited": True},
}


@pytest.mark.parametrize("shape", sorted(_MALFORMED))
def test_a_malformed_stored_session_is_treated_as_no_session(shape):
    end = time.time()
    s = end + 10
    assert decide(_MALFORMED[shape](end), s, s + 10) == Decision("insert", _session(s, s + 10, 10_000))


def test_the_well_formed_control_of_the_malformed_cases_merges():
    end = time.time()
    prev = {"started_at": end - 10, "ended_at": end, "connected_ms": 10_000, "connections": 1, "audited": True}
    assert decide(prev, end + 10, end + 20).kind == "merge"


@pytest.mark.parametrize("audited", ["true", 1, None, "yes"])
def test_only_a_json_true_counts_as_audited(audited):
    end = time.time()
    prev = {"started_at": end - 10, "ended_at": end, "connected_ms": 10_000, "connections": 1, "audited": audited}
    assert decide(prev, end + 1, end + 2).kind == "insert"


def test_a_clock_stepping_backwards_never_yields_a_negative_span():
    s = time.time()
    assert decide(None, s, s - 3) == Decision("short", _session(s, s, 0))


# ── The vocabulary and the module's shape ───────────────────────────────────────────────────────────────


def test_the_constants_are_the_contracts():
    assert policy.WS_AUDIT_ACTION == "ws_session"
    assert len(policy.WS_AUDIT_ACTION) <= 50  # AccessAuditLog.action is String(50)
    assert policy.RECONNECT_WINDOW_SECONDS == 60
    assert policy.SHORT_CONNECTION_SECONDS == 5
    assert policy.WS_OUTCOMES == ("audited", "merged", "short", "audit_failed")
    assert set(policy.OUTCOME_BY_KIND) == set(policy.DECISION_KINDS)
    assert set(policy.OUTCOME_BY_KIND.values()) | {"audit_failed"} == set(policy.WS_OUTCOMES)
    assert policy.SESSION_KEY.format(user_id="u1", project_id="p1") == "testlookup:ws_audit:u1:p1"
    assert policy.RECONNECT_POLICY == "reconnects within 60 s are merged into this session"


def test_the_module_is_pure_no_app_import_no_io_no_commit_no_channel_constant():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "math", "dataclasses", "typing"}, imported
    assert "commit(" not in source
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert not any(name.endswith("_CHANNEL") for name in names)
