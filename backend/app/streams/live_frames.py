"""W25e HYG-06 (E-2) — per-WebSocket frame pacing for ``/ws/live`` (contract §1.2, P-1 … P-7).

Pure: constants and one class, no ``app.*`` import, no I/O; the clock is
injected. ``routers/live.py``'s ``ConnectionManager`` keeps one
``LiveFramePacer`` per registered socket and sends at most one data frame per
``FRAME_INTERVAL_MS``. Mirrored by ``frontend/src/types/liveFrames.ts`` (pinned
by ``tests/test_live_frames_vocab_frontend_parity.py``).

Every event offered to a pacer is accounted for: it is delivered as itself, folded
into a counted entry (``coalesced``; a test result beyond the listed ones is counted
in ``results_dropped``), or replaced by ONE ``reconcile_required`` frame whose
``dropped`` counts it — never silently lost.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

LIVE_PROTOCOL_LEGACY = 1  # a client that asks for nothing: legacy frame types only, paced
LIVE_PROTOCOL_BATCH = 2  # a client that sends "protocol": 2 in its auth frame
LIVE_BATCH_FRAME = "live_batch"
LIVE_RECONCILE_FRAME = "reconcile_required"
FRAME_INTERVAL_MS = 200  # ≤ 5 frames/s per client (D27's ceiling is 10)
RESULTS_PER_FRAME_MAX = 200  # the SPA feed keeps 200 events
ENTRIES_PER_FRAME_MAX = 500  # a batch carries at most 500 entries; the rest wait for the next tick
PENDING_ENTRIES_MAX = 2_000  # protocol 2: more pending entries than this → one reconcile frame
LEGACY_BACKLOG_MAX = 25  # protocol 1: more than 5 s of entries behind → one reconcile frame
COALESCED_EVENT_TYPES = ("live_test_result", "live_warning")  # folded per (type, run_id)
CONTROL_FRAME_TYPES = ("connected", "initial_state", "ping", "pong", "refreshed", "reconcile_required")

_TEST_RESULT = "live_test_result"

SequenceKey = tuple[int, int]


def _parse_sequence(value: Any) -> SequenceKey | None:
    """A Redis stream id ``"<ms>-<seq>"`` as a sortable pair, or None when it is absent or malformed."""
    if not isinstance(value, str):
        return None
    millis, sep, seq = value.partition("-")
    if not sep or not (millis.isascii() and millis.isdigit() and seq.isascii() and seq.isdigit()):
        return None
    return int(millis), int(seq)


class _Entry:
    """One pending entry: a raw event, or the fold of every event of one ``(type, run_id)``."""

    __slots__ = ("event", "count", "seq", "folds", "results", "results_dropped")

    def __init__(self, event: dict[str, Any], seq: SequenceKey | None, folds: bool) -> None:
        self.event = event  # the LATEST folded event; never mutated (one dict is offered to every socket)
        self.count = 1
        self.seq = seq
        self.folds = folds
        self.results: list[dict[str, Any]] = []
        self.results_dropped = 0

    def sequence_text(self) -> str | None:
        if self.seq is None:
            return None
        text: str = self.event["sequence_id"]  # a parsed sequence id is always a str
        return text

    def legacy_frame(self) -> dict[str, Any]:
        """P-6 protocol 1: the event itself, or the latest event plus ``coalesced``."""
        if self.count == 1:
            return self.event
        frame = dict(self.event)
        frame["coalesced"] = self.count
        return frame

    def batch_entry(self) -> dict[str, Any]:
        """One element of a ``live_batch``'s ``events`` (contract §3.2)."""
        if not self.folds:
            return self.event
        entry = dict(self.event)
        entry["coalesced"] = self.count
        if entry.get("type") == _TEST_RESULT:
            entry["results"] = list(self.results)
            entry["results_dropped"] = self.results_dropped
        return entry


class _Reconcile:
    """A queued ``reconcile_required`` frame and what it replaced."""

    __slots__ = ("base", "dropped", "seq", "seq_text")

    def __init__(self, base: dict[str, Any]) -> None:
        self.base = base
        self.dropped = 0
        self.seq: SequenceKey | None = None
        self.seq_text: str | None = None

    def absorb(self, seq: SequenceKey | None, seq_text: Any, dropped: int) -> None:
        self.dropped += dropped
        if seq is not None and (self.seq is None or seq > self.seq):
            self.seq, self.seq_text = seq, seq_text

    def frame(self) -> dict[str, Any]:
        frame = dict(self.base)
        frame["type"] = LIVE_RECONCILE_FRAME
        frame["dropped"] = self.dropped
        if self.seq_text is not None:
            frame["sequence_id"] = self.seq_text
        else:
            # Never a cursor the client cannot order: no dropped event carried one.
            frame.pop("sequence_id", None)
        return frame


class LiveFramePacer:
    """Folds fan-out events for ONE socket and hands out at most one frame per interval.

    ``offer`` is O(1) and never sends; ``take_frame`` returns the frame to send now (and
    stamps the send time before the caller awaits the send, so a concurrent caller
    buffers) or None; ``due_in`` says when to ask again. Folding rules P-1 … P-7 are in
    contract §1.2; the conservation property (every offered data event is delivered as
    1, inside an entry's ``coalesced``, or inside a reconcile's ``dropped``) is pinned by
    ``tests/test_live_frames_pacer.py``.
    """

    __slots__ = ("_protocol", "_interval", "_clock", "_entries", "_serial", "_listed", "_reconcile", "_last_sent")

    def __init__(
        self,
        protocol: int,
        *,
        interval_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Lenient like the handshake: only an int >= 2 selects batches; a future 3 gets this server's best.
        self._protocol = (
            LIVE_PROTOCOL_BATCH
            if type(protocol) is int and protocol >= LIVE_PROTOCOL_BATCH
            else LIVE_PROTOCOL_LEGACY
        )
        # Read at construction time (not bound as a default) so a test can patch the module constant.
        self._interval = FRAME_INTERVAL_MS / 1000 if interval_s is None else float(interval_s)
        self._clock = clock
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()  # last-update order (P-1)
        self._serial = 0  # keys for raw entries (ints never collide with the (type, run_id) tuples)
        self._listed = 0  # test results listed across the pending entries (≤ RESULTS_PER_FRAME_MAX)
        self._reconcile: _Reconcile | None = None
        self._last_sent: float | None = None

    @property
    def protocol(self) -> int:
        return self._protocol

    @property
    def interval_s(self) -> float:
        return self._interval

    @property
    def pending_entries(self) -> int:
        """Pending entries, a queued reconcile frame counting as one (0 ⇔ ``due_in()`` is None)."""
        return len(self._entries) + (1 if self._reconcile is not None else 0)

    # ── folding ──────────────────────────────────────────────────────────────

    def offer(self, event: dict) -> None:
        """Fold ONE fan-out event. O(1); never sends; never raises on the event's shape."""
        is_dict = isinstance(event, dict)
        event_type = event.get("type") if is_dict else None
        seq = _parse_sequence(event.get("sequence_id")) if is_dict else None

        if event_type == LIVE_RECONCILE_FRAME:  # P-3: an upstream reconcile supersedes everything pending
            self._supersede(event, seq)
            return
        if self._reconcile is not None:  # P-4: offered before the reconcile is taken → only counted into it
            self._reconcile.absorb(seq, event.get("sequence_id") if seq is not None else None, 1)
            return

        run_id = event.get("run_id") if is_dict else None
        if (
            seq is not None
            and isinstance(run_id, str)
            and run_id
            and event_type in COALESCED_EVENT_TYPES
        ):
            key: Hashable = (event_type, run_id)
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry(event, seq, folds=True)
                self._entries[key] = entry
            else:
                entry.event = event
                entry.count += 1
                entry.seq = seq
                self._entries.move_to_end(key)  # P-1: an updated entry moves to the end
            if event_type == _TEST_RESULT and self._protocol == LIVE_PROTOCOL_BATCH:
                if self._listed < RESULTS_PER_FRAME_MAX:  # P-2
                    entry.results.append({
                        "test_name": event.get("last_test", ""),
                        "status": event.get("last_status", ""),
                        "sequence_id": event.get("sequence_id"),
                    })
                    self._listed += 1
                else:
                    entry.results_dropped += 1
        else:
            # Any other event — and one without a parsable sequence id (P-7) — is its own entry.
            self._serial += 1
            self._entries[self._serial] = _Entry(event, seq, folds=False)

        limit = PENDING_ENTRIES_MAX if self._protocol == LIVE_PROTOCOL_BATCH else LEGACY_BACKLOG_MAX
        if len(self._entries) > limit:  # P-4
            self._clear_into(_Reconcile({"type": LIVE_RECONCILE_FRAME}))

    def _supersede(self, event: dict, seq: SequenceKey | None) -> None:
        reconcile = self._reconcile
        if reconcile is None:
            reconcile = _Reconcile(dict(event))
            self._clear_into(reconcile)
        else:
            reconcile.base.update(event)
        # The upstream reconcile is not a dropped event, but the cursor must not fall behind it.
        reconcile.absorb(seq, event.get("sequence_id") if seq is not None else None, 0)

    def _clear_into(self, reconcile: _Reconcile) -> None:
        for entry in self._entries.values():
            reconcile.absorb(entry.seq, entry.sequence_text(), entry.count)
        self._entries.clear()
        self._listed = 0
        self._reconcile = reconcile

    # ── pacing ───────────────────────────────────────────────────────────────

    def due_in(self) -> float | None:
        """Seconds until ``take_frame`` can return a frame; None when nothing is pending."""
        if self._reconcile is None and not self._entries:
            return None
        if self._last_sent is None:
            return 0.0
        return max(0.0, self._last_sent + self._interval - self._clock())

    def take_frame(self) -> dict | None:
        """The frame to send NOW (the send time is stamped here), or None (P-5)."""
        if self._reconcile is None and not self._entries:
            return None
        now = self._clock()
        if self._last_sent is not None and now < self._last_sent + self._interval:
            return None
        self._last_sent = now
        if self._reconcile is not None:
            frame = self._reconcile.frame()
            self._reconcile = None
            return frame
        if self._protocol == LIVE_PROTOCOL_BATCH:
            return self._take_batch()
        _key, entry = self._entries.popitem(last=False)  # protocol 1: the OLDEST entry only
        self._listed -= len(entry.results)
        return entry.legacy_frame()

    def _take_batch(self) -> dict:
        if len(self._entries) == 1:
            entry = next(iter(self._entries.values()))
            if entry.count == 1:  # P-6: one lone raw event goes out unchanged (byte-identical to today)
                self._entries.clear()
                self._listed = 0
                return entry.event
        taken = [self._entries.popitem(last=False)[1] for _ in range(min(len(self._entries), ENTRIES_PER_FRAME_MAX))]
        events = [entry.batch_entry() for entry in taken]
        listed = sum(len(entry.results) for entry in taken)
        self._listed -= listed
        frame: dict[str, Any] = {"type": LIVE_BATCH_FRAME, "protocol": LIVE_PROTOCOL_BATCH}
        top = max(((entry.seq, entry.sequence_text()) for entry in taken if entry.seq is not None), default=None)
        if top is not None:  # P-7: the max of its entries (a batch with no sequenced entry carries no cursor)
            frame["sequence_id"] = top[1]
        frame["events"] = events
        frame["counts"] = {
            "events": sum(entry.count for entry in taken),
            "entries": len(taken),
            "results_listed": listed,
            "results_dropped": sum(entry.results_dropped for entry in taken),
        }
        return frame
