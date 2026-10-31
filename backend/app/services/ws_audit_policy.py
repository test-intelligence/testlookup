"""HYG-06 (a) — the live WebSocket audit policy: AT MOST ONE ``ws_session`` row per session (D27 a), append-only.

Before W25a every live connection wrote two ``access_audit_logs`` rows (``ws_connect`` after the handshake,
``ws_disconnect`` in ``finally``), so the first page of ``/settings/audit`` was connection churn. Now a
connection writes nothing when it opens; when it ends, ``routers/live.py::_record_ws_session`` asks
:func:`decide` what the connection means for the user's *session* on that project (kept in Redis):

=====================================  ===========================================  =====================
previous session (Redis)               this connection ``[s, e]``                   kind -> outcome
=====================================  ===========================================  =====================
none, or ``s > prev.ended_at + 60``    lasted ``>= 5 s``                            insert -> audited
none, or ``s > prev.ended_at + 60``    lasted ``< 5 s``                             short -> short
``s <= prev.ended_at + 60``, audited   any                                          merge -> merged
``s <= prev.ended_at + 60``, no row    accumulated connected time reaches ``5 s``   insert -> audited
``s <= prev.ended_at + 60``, no row    accumulated connected time still ``< 5 s``   short -> short
=====================================  ===========================================  =====================

A connection that starts within 60 s of the session's last disconnect is a CONTINUATION (exactly 60 s
continues, 60.001 s starts a new session); otherwise it starts a NEW session. A session gets AT MOST ONE row:
an INSERT at the first disconnect where its accumulated connected time reaches 5 s (exactly 5 s counts), with
that disconnect's timing. After that, continuations never write: each is counted ``merged`` and only moves the
session's last disconnect and accumulated time in Redis. Audit rows are append-only
(``backend.audit-write-discipline``), so the row is NEVER updated and its detail claims no reconnect count and no
total duration — it cannot know them when it is written. Two tabs of one user on one project overlap, so they
fold into one session too.

Constants + ONE pure function: no session, no Redis, no commit, no I/O at import (a ``STAGE_ONLY_SERVICES``
module). Times are epoch seconds (``time.time()``); connected time is kept in whole milliseconds so sums never
drift across the 5 s boundary. The session dict kept in Redis is ``{"started_at": float, "ended_at": float,
"connected_ms": int, "connections": int, "audited": bool}``; for an ``insert`` the decision's ``audited`` is
``False`` and the caller sets it once the INSERT has committed (a failed write leaves the next continuation to
try again).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

WS_AUDIT_ACTION = "ws_session"  # the ONE access_audit_logs action a live session writes (replaces ws_connect / ws_disconnect)
RECONNECT_WINDOW_SECONDS = 60  # D27 (a): a connection starting <= 60 s after the session's last disconnect continues it
SHORT_CONNECTION_SECONDS = 5  # D27 (a): a session with less connected time than this is metric-only
WS_OUTCOMES = ("audited", "merged", "short", "audit_failed")  # the metric's `outcome` label values
SESSION_KEY = "testlookup:ws_audit:{user_id}:{project_id}"  # Redis, JSON, TTL = RECONNECT_WINDOW_SECONDS
RECONNECT_POLICY = "reconnects within 60 s are merged into this session"  # a fixed fact in the row, never a count
# NOT named *_CHANNEL (test_review_gate_inline_vocab_parity.py); the audit detail keeps {"channel": "live"} as a literal.

DECISION_KINDS = ("insert", "merge", "short")
OUTCOME_BY_KIND = {"insert": "audited", "merge": "merged", "short": "short"}
_SHORT_MS = SHORT_CONNECTION_SECONDS * 1000


@dataclass(frozen=True)
class Decision:
    """What one finished connection does to its session.

    ``kind`` is one of :data:`DECISION_KINDS`; ``session`` is the session state to keep (see the module
    docstring). ``audit_failed`` is not a decision: it is the caller's outcome when the INSERT it was told to
    make fails.
    """

    kind: str
    session: dict[str, Any]

    @property
    def outcome(self) -> str:
        return OUTCOME_BY_KIND[self.kind]


def _previous(prev: object) -> tuple[float, float, int, int, bool] | None:
    """The usable part of a stored session, or ``None`` when it is absent or malformed.

    Redis holds whatever an older (or a broken) writer put there: anything that is not a well-formed session
    is treated as no session, so the connection starts a new one instead of raising into the socket.
    """
    if not isinstance(prev, dict):
        return None
    try:
        started = float(prev["started_at"])
        ended = float(prev["ended_at"])
        connected_ms = int(prev["connected_ms"])
        connections = int(prev["connections"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(started) and math.isfinite(ended)) or ended < started:
        return None
    if connections < 1 or connected_ms < 0:
        return None
    return started, ended, connected_ms, connections, prev.get("audited") is True


def decide(prev: dict[str, Any] | None, started_at: float, ended_at: float) -> Decision:
    """The pure §2.2 decision (orchestrator's append-only revision) for one finished connection.

    The window is compared as ``s <= last_end + 60`` (not as a difference) so a boundary built the same way by
    the caller compares exactly, whatever the float rounding of an epoch.
    """
    ended_at = max(ended_at, started_at)  # a clock stepping backwards never yields a negative span
    connected_ms = round((ended_at - started_at) * 1000)
    previous = _previous(prev)
    if previous is not None and started_at <= previous[1] + RECONNECT_WINDOW_SECONDS:
        prev_started, prev_ended, prev_connected_ms, prev_connections, audited = previous
        session: dict[str, Any] = {
            "started_at": min(prev_started, started_at),
            "ended_at": max(prev_ended, ended_at),
            "connected_ms": prev_connected_ms + connected_ms,
            "connections": prev_connections + 1,
            "audited": audited,
        }
        if audited:
            return Decision("merge", session)  # a continuation never writes
    else:
        session = {
            "started_at": started_at,
            "ended_at": ended_at,
            "connected_ms": connected_ms,
            "connections": 1,
            "audited": False,
        }
    if session["connected_ms"] >= _SHORT_MS:
        return Decision("insert", session)
    return Decision("short", session)
