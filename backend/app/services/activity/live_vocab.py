"""The activity live tail's, ``/activity/mine``'s and mentions' wire vocabulary (W21d ACT-02).

Constants only, and no ``app.*`` import, so the stream route, the publish
hook (``live.py``), the cross-project read (``mine.py``), mentions
(``mentions.py``) and their tests share one source without an import cycle.
Every vocabulary the client renders or branches on is mirrored by
``frontend/src/types/activityLive.ts`` and pinned by
``tests/test_activity_live_vocab_frontend_parity.py``: change both together.

Enum-like wire fields stay plain ``str``; a request value outside its
vocabulary is a 422 with the one fixed sentence below.
"""
from __future__ import annotations

from typing import Final

# ── the live tail ────────────────────────────────────────────────────────────
SSE_EVENTS: Final[tuple[str, ...]] = ("ready", "activity", "refresh", "bye")
# refresh: the client re-reads page 0 of the feed.
REFRESH_REASONS: Final[tuple[str, ...]] = ("gap", "overflow", "unavailable")
# bye: the server ends the stream; the client reconnects.
BYE_REASONS: Final[tuple[str, ...]] = ("max_age", "token_expired", "unavailable")
# XRANGE (last + COUNT REPLAY_LIMIT, NON-blocking, once a second.
STREAM_POLL_SECONDS: Final[float] = 1.0
# ": heartbeat" comment after this long without a frame.
HEARTBEAT_SECONDS: Final[int] = 15
# A stream lives at most this long, or until the JWT's exp if sooner; the
# reconnect is re-authorized.
MAX_STREAM_SECONDS: Final[int] = 900
# A Last-Event-ID further behind than this, or trimmed away, is refresh(gap).
REPLAY_LIMIT: Final[int] = 200
# The caps are per API process.
MAX_STREAMS_PER_USER: Final[int] = 3
MAX_STREAMS_PER_PROCESS: Final[int] = 200
# A larger row is not published (dropped_oversize); readers see it on the next
# page-0 read.
MAX_PAYLOAD_BYTES: Final[int] = 65_536
# One publish never waits longer (asyncio.wait_for).
PUBLISH_TIMEOUT_SECONDS: Final[float] = 1.0
# A worker task waits at most this for its in-flight publishes.
SETTLE_TIMEOUT_SECONDS: Final[float] = 2.0
# The "retry:" line: the client's first backoff step.
RETRY_MILLISECONDS: Final[int] = 5_000
# detail = {"code": <key>, "message": <sentence>} on the stream route's own
# refusals.
STREAM_ERROR_SENTENCES: Final[dict[str, str]] = {
    "interactive_login_required": (
        "The live activity stream needs a signed-in session; "
        "API keys read GET /api/v1/projects/{project_id}/activity."
    ),
    "invalid_last_event_id": "Last-Event-ID must be a stream id such as 1730000000000-0.",
    "too_many_streams": "You already have 3 live activity views open; close one to open another.",
    "server_busy": "The server has too many live views open; try again in a minute.",
    "live_unavailable": "Live updates are unavailable right now; the feed still refreshes every 30 seconds.",
}

# ── /activity/mine ───────────────────────────────────────────────────────────
# all = mine ∪ involving.
MINE_VIEWS: Final[tuple[str, ...]] = ("all", "mine", "involving")
# Also the PRIORITY when one row matches several parts.
INVOLVEMENTS: Final[tuple[str, ...]] = ("actor", "mentioned", "assigned", "authored")
INVOLVEMENT_LABELS: Final[dict[str, str]] = {
    "actor": "You",
    "mentioned": "Mentioned you",
    "assigned": "Assigned to you",
    "authored": "Your test case",
}
MINE_DEFAULT_DAYS: Final[int] = 7
# 'since' older than this is a 422 (it bounds the COUNT).
MINE_MAX_DAYS: Final[int] = 90
MINE_DEFAULT_LIMIT: Final[int] = 50
MINE_MAX_LIMIT: Final[int] = 100
MINE_ERROR_SENTENCES: Final[dict[str, str]] = {
    "unknown_view": "Unknown view. Valid: all, mine, involving.",
    "window_too_long": "'since' may be at most 90 days ago.",
    # The project feed's own sentence (routers/activity.py).
    "since_after_until": "'since' must be earlier than 'until'.",
}

# ── mentions ─────────────────────────────────────────────────────────────────
MAX_MENTIONS_PER_COMMENT: Final[int] = 10
# NotificationLog.event_type (String(50)); NOT a NotificationEventType value,
# so no notification preference can subscribe to it (no e-mail, no Slack).
MENTION_NOTIFICATION_EVENT: Final[str] = "test_case.mentioned"
# NotificationLog.channel (String(20)); NOT a NotificationChannel value.
IN_APP_CHANNEL: Final[str] = "in_app"
# Clipped to 200 characters (gate_notifications.clip).
MENTION_TITLE: Final[str] = "{author} mentioned you on {case}"
MENTION_BODY_CHARS: Final[int] = 280
