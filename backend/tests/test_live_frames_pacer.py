"""W25e HYG-06 (E-2) T-B1 — ``streams/live_frames.LiveFramePacer`` (contract §1.2 P-1 … P-7, §2.2).

Pure: a fake clock, no event loop, no socket. The pacer is what turns the fan-out's one-notification-per-test
flood (W25a: 367 frames/s per client at p95) into at most one frame per 200 ms per socket, so these tests pin
both halves of the promise: the RATE (≤ 5 frames in every sliding 1 s window) and the ACCOUNTING (every offered
data event comes out as itself, inside an entry's ``coalesced``, or inside a reconcile's ``dropped`` — the
conservation property, over randomly seeded streams for both protocols).
"""
from __future__ import annotations

import json
import random

import pytest

from app.streams import live_frames
from app.streams.live_frames import (
    ENTRIES_PER_FRAME_MAX,
    LEGACY_BACKLOG_MAX,
    LIVE_BATCH_FRAME,
    LIVE_PROTOCOL_BATCH,
    LIVE_PROTOCOL_LEGACY,
    LIVE_RECONCILE_FRAME,
    PENDING_ENTRIES_MAX,
    RESULTS_PER_FRAME_MAX,
    LiveFramePacer,
)

INTERVAL = 0.2
BASE_MS = 1_700_000_000_000
PROJECT = "proj-zz-sentinel"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Feed:
    """Builds fan-out notifications with strictly increasing Redis stream ids, like the per-project stream."""

    def __init__(self) -> None:
        self.n = 0

    def seq(self) -> str:
        self.n += 1
        return f"{BASE_MS + self.n // 3}-{self.n % 3}"

    def result(self, run: str, *, status: str = "PASSED", seq: bool = True) -> dict:
        event = {
            "type": "live_test_result",
            "run_id": run,
            "project_id": PROJECT,
            "passed": self.n,
            "failed": 0,
            "total": 500,
            "pass_rate": 100.0,
            "status": "running",
            "last_test": f"zz_case_{self.n}",
            "last_status": status,
        }
        if seq:
            event["sequence_id"] = self.seq()
        return event

    def warning(self, run: str) -> dict:
        return {"type": "live_warning", "run_id": run, "message": f"High failure rate {self.n}", "sequence_id": self.seq()}

    def raw(self, kind: str = "live_run_started", run: str = "run-zz-raw") -> dict:
        return {"type": kind, "run_id": run, "build_number": "b-zz", "sequence_id": self.seq()}


def _pacer(protocol: int, clock: _Clock) -> LiveFramePacer:
    return LiveFramePacer(protocol, interval_s=INTERVAL, clock=clock)


def _seq_key(value: str) -> tuple[int, int]:
    millis, seq = value.split("-")
    return int(millis), int(seq)


def _delivered(frame: dict) -> int:
    """How many offered data events one frame accounts for (contract §1.2 conservation)."""
    if frame.get("type") == LIVE_BATCH_FRAME:
        return sum(entry.get("coalesced", 1) for entry in frame["events"])
    if frame.get("type") == LIVE_RECONCILE_FRAME:
        return frame["dropped"]
    return frame.get("coalesced", 1)


# ── P-6: a lone event is the event itself ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
@pytest.mark.parametrize("kind", ["raw", "result"])
def test_one_lone_event_is_returned_unchanged(protocol, kind):
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(protocol, clock)
    event = feed.raw() if kind == "raw" else feed.result("run-a")
    snapshot = json.dumps(event)

    pacer.offer(event)
    frame = pacer.take_frame()

    assert frame == event and frame is event, "the first frame after an idle interval is the event, unchanged"
    assert json.dumps(frame) == snapshot, "byte-identical to today's frame"
    assert pacer.take_frame() is None and pacer.due_in() is None and pacer.pending_entries == 0


# ── P-5: at most one frame per interval ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
def test_a_thousand_offers_in_one_second_give_at_most_five_frames_in_every_one_second_window(protocol):
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(protocol, clock)
    frame_times: list[float] = []
    frames: list[dict] = []
    offered = 0
    for tick in range(200):  # poll every 10 ms for 2 s; offers in the first second
        clock.now = tick / 100
        if tick < 100:
            for k in range(10):
                which = (tick * 10 + k) % 7
                event = feed.raw() if which == 6 else feed.result(f"run-{which}")
                pacer.offer(event)
                offered += 1
        frame = pacer.take_frame()
        if frame is not None:
            frame_times.append(clock.now)
            frames.append(frame)
    while pacer.due_in() is not None:  # drain at the same 10 ms polling
        clock.now += 0.01
        frame = pacer.take_frame()
        if frame is not None:
            frame_times.append(clock.now)
            frames.append(frame)

    assert offered == 1_000
    assert len([t for t in frame_times if t < 1.0]) == 5, "the pacer still delivers every interval"
    for start in frame_times:
        in_window = [t for t in frame_times if start <= t < start + 1.0]
        assert len(in_window) <= 5, f"{len(in_window)} frames in the 1 s window starting at {start}"
    gaps = [b - a for a, b in zip(frame_times, frame_times[1:])]
    assert min(gaps) >= INTERVAL - 1e-9
    assert sum(_delivered(f) for f in frames) == offered
    if protocol == LIVE_PROTOCOL_LEGACY:
        assert all(f["type"] != LIVE_BATCH_FRAME for f in frames)


# ── P-1 / P-2: folding ────────────────────────────────────────────────────────────────────────────────


def test_37_results_of_one_run_inside_one_interval_fold_into_one_entry():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_BATCH, clock)
    pacer.offer(feed.raw())
    assert pacer.take_frame() is not None  # t = 0: the slot is now used
    results = []
    for i in range(37):
        clock.now = 0.05 + i * 0.002
        results.append(feed.result("run-a", status="FAILED" if i % 5 == 0 else "PASSED"))
        pacer.offer(results[-1])
    assert pacer.take_frame() is None, "inside the interval nothing goes out"
    assert pacer.due_in() == pytest.approx(INTERVAL - clock.now)

    clock.now = INTERVAL
    frame = pacer.take_frame()

    assert frame["type"] == LIVE_BATCH_FRAME and frame["protocol"] == LIVE_PROTOCOL_BATCH
    (entry,) = frame["events"]
    assert entry["coalesced"] == 37
    assert entry["results"] == [
        {"test_name": r["last_test"], "status": r["last_status"], "sequence_id": r["sequence_id"]} for r in results
    ]
    assert entry["results_dropped"] == 0
    assert entry["sequence_id"] == results[-1]["sequence_id"] == frame["sequence_id"]
    assert entry["passed"] == results[-1]["passed"], "the latest absolute state wins"
    assert frame["counts"] == {"events": 37, "entries": 1, "results_listed": 37, "results_dropped": 0}


def test_300_results_over_3_runs_list_200_and_count_the_other_100():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_BATCH, clock)
    offered = [feed.result(f"run-{i % 3}") for i in range(300)]
    for event in offered:
        pacer.offer(event)

    frame = pacer.take_frame()

    entries = frame["events"]
    assert len(entries) == 3 and all(e["coalesced"] == 100 for e in entries)
    listed = [item for e in entries for item in e["results"]]
    assert len(listed) == RESULTS_PER_FRAME_MAX == 200
    assert sum(e["results_dropped"] for e in entries) == 100
    assert sorted(item["sequence_id"] for item in listed) == sorted(
        e["sequence_id"] for e in offered[:200]
    ), "the first 200 in arrival order are the listed ones"
    assert frame["counts"] == {"events": 300, "entries": 3, "results_listed": 200, "results_dropped": 100}

    clock.now = INTERVAL  # the next frame has its own 200-result budget
    for i in range(10):
        pacer.offer(feed.result("run-0"))
    (entry,) = pacer.take_frame()["events"]
    assert len(entry["results"]) == 10 and entry["results_dropped"] == 0


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
def test_an_updated_entry_moves_behind_a_later_run(protocol):
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(protocol, clock)
    pacer.offer(feed.raw())
    pacer.take_frame()
    first_a = feed.result("run-a")
    pacer.offer(first_a)
    pacer.offer(feed.result("run-b"))
    last_a = feed.result("run-a")
    pacer.offer(last_a)

    clock.now = INTERVAL
    frame = pacer.take_frame()
    if protocol == LIVE_PROTOCOL_BATCH:
        assert [e["run_id"] for e in frame["events"]] == ["run-b", "run-a"]
        assert frame["events"][1]["coalesced"] == 2
    else:
        assert frame["run_id"] == "run-b" and "coalesced" not in frame
        clock.now = 2 * INTERVAL
        second = pacer.take_frame()
        assert second["run_id"] == "run-a" and second["coalesced"] == 2
        assert second["sequence_id"] == last_a["sequence_id"]


# ── P-3 / P-4: reconcile ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
def test_an_upstream_reconcile_supersedes_everything_pending(protocol):
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(protocol, clock)
    pacer.offer(feed.raw())
    pacer.take_frame()
    pending = [feed.result("run-a") for _ in range(5)] + [feed.warning("run-b") for _ in range(3)] + [feed.raw()]
    for event in pending:
        pacer.offer(event)
    upstream = {"type": LIVE_RECONCILE_FRAME}
    pacer.offer(upstream)
    late = feed.result("run-c")
    pacer.offer(late)  # offered before the reconcile is taken: only counted into it

    assert pacer.pending_entries == 1
    clock.now = INTERVAL
    frame = pacer.take_frame()

    assert frame == {"type": LIVE_RECONCILE_FRAME, "dropped": 10, "sequence_id": late["sequence_id"]}
    assert upstream == {"type": LIVE_RECONCILE_FRAME}, "the offered dict is never mutated"
    clock.now = 2 * INTERVAL
    assert pacer.take_frame() is None and pacer.due_in() is None


def test_an_upstream_reconcile_with_nothing_pending_carries_no_cursor():
    clock = _Clock()
    pacer = _pacer(LIVE_PROTOCOL_BATCH, clock)
    pacer.offer({"type": LIVE_RECONCILE_FRAME})
    assert pacer.take_frame() == {"type": LIVE_RECONCILE_FRAME, "dropped": 0}


def test_2001_distinct_runs_pending_overflow_into_one_reconcile_frame():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_BATCH, clock)
    events = [feed.result(f"run-{i}") for i in range(PENDING_ENTRIES_MAX + 1)]
    for event in events[:-1]:
        pacer.offer(event)
    assert pacer.pending_entries == PENDING_ENTRIES_MAX, "exactly the maximum is still a batch"

    pacer.offer(events[-1])
    assert pacer.pending_entries == 1

    frame = pacer.take_frame()
    assert frame == {
        "type": LIVE_RECONCILE_FRAME,
        "dropped": PENDING_ENTRIES_MAX + 1,
        "sequence_id": events[-1]["sequence_id"],
    }
    clock.now = INTERVAL
    assert pacer.take_frame() is None, "ONE reconcile frame"


def test_protocol_1_keeps_legacy_shapes_and_reconciles_after_25_entries():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_LEGACY, clock)
    pacer.offer(feed.raw())
    pacer.take_frame()
    tens = [feed.result("run-a") for _ in range(10)]
    for event in tens:
        pacer.offer(event)
    clock.now = INTERVAL
    frame = pacer.take_frame()
    assert frame["type"] == "live_test_result" and frame["coalesced"] == 10
    assert "results" not in frame and "results_dropped" not in frame
    assert {k: v for k, v in frame.items() if k != "coalesced"} == tens[-1]

    for i in range(LEGACY_BACKLOG_MAX):  # 25 entries behind: still paced legacy frames
        pacer.offer(feed.result(f"run-{i}"))
    assert pacer.pending_entries == LEGACY_BACKLOG_MAX
    behind = feed.result("run-26")
    pacer.offer(behind)  # the 26th
    assert pacer.pending_entries == 1
    clock.now = 2 * INTERVAL
    assert pacer.take_frame() == {
        "type": LIVE_RECONCILE_FRAME,
        "dropped": LEGACY_BACKLOG_MAX + 1,
        "sequence_id": behind["sequence_id"],
    }


def test_protocol_1_never_yields_a_live_batch():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_LEGACY, clock)
    types = []
    for step in range(60):
        clock.now = step * 0.05
        for run in ("run-a", "run-b", "run-c"):
            pacer.offer(feed.result(run))
        pacer.offer(feed.warning("run-a"))
        frame = pacer.take_frame()
        if frame is not None:
            types.append(frame["type"])
    assert types and LIVE_BATCH_FRAME not in types
    assert set(types) <= {"live_test_result", "live_warning", LIVE_RECONCILE_FRAME}


# ── P-6 entry cap, P-7 cursors ────────────────────────────────────────────────────────────────────────


def test_a_batch_carries_at_most_500_entries_and_the_rest_wait():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_BATCH, clock)
    events = [feed.result(f"run-{i}") for i in range(600)]
    for event in events:
        pacer.offer(event)

    first = pacer.take_frame()
    assert len(first["events"]) == ENTRIES_PER_FRAME_MAX
    assert first["sequence_id"] == events[ENTRIES_PER_FRAME_MAX - 1]["sequence_id"]
    assert pacer.pending_entries == 100
    clock.now = INTERVAL
    second = pacer.take_frame()
    assert len(second["events"]) == 100 and second["sequence_id"] == events[-1]["sequence_id"]
    assert first["counts"]["results_listed"] + second["counts"]["results_listed"] == RESULTS_PER_FRAME_MAX


def test_an_event_without_a_parsable_sequence_id_is_its_own_entry_and_never_moves_the_max():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_BATCH, clock)
    first = feed.result("run-a")
    unsequenced = feed.result("run-a", seq=False)
    bad = {**feed.result("run-a", seq=False), "sequence_id": "not-a-cursor"}
    pacer.offer(first)
    pacer.offer(unsequenced)
    pacer.offer(bad)

    frame = pacer.take_frame()
    assert [e.get("coalesced") for e in frame["events"]] == [1, None, None]
    assert frame["events"][1] is unsequenced and frame["events"][2] is bad
    assert frame["sequence_id"] == first["sequence_id"]

    clock.now = INTERVAL
    pacer.offer(unsequenced)
    pacer.offer({"type": "pipeline_progress"})
    frame = pacer.take_frame()
    assert frame["type"] == LIVE_BATCH_FRAME and "sequence_id" not in frame, "no cursor the client cannot order"


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
def test_top_level_sequence_ids_strictly_increase(protocol):
    rng = random.Random(7)
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(protocol, clock)
    seen: list[str] = []
    for _ in range(200):
        clock.now += rng.choice((0.0, 0.01, 0.05, 0.12, 0.25))
        roll = rng.random()
        run = f"run-{rng.randrange(6)}"
        if roll < 0.6:
            event = feed.result(run)
        elif roll < 0.75:
            event = feed.warning(run)
        elif roll < 0.92:
            event = feed.raw(rng.choice(("live_run_started", "live_run_complete", "pipeline_progress")), run)
        elif roll < 0.97:
            event = feed.result(run, seq=False)
        else:
            event = {"type": LIVE_RECONCILE_FRAME}
        pacer.offer(event)
        frame = pacer.take_frame()
        if frame is not None and "sequence_id" in frame:
            seen.append(frame["sequence_id"])
    for _ in range(60):  # drain
        clock.now += INTERVAL
        frame = pacer.take_frame()
        if frame is not None and "sequence_id" in frame:
            seen.append(frame["sequence_id"])

    assert len(seen) > 10
    keys = [_seq_key(s) for s in seen]
    assert all(a < b for a, b in zip(keys, keys[1:])), "a client cursor must never see an older id"


# ── conservation: nothing is silent ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
@pytest.mark.parametrize("seed", range(12))
def test_every_offered_event_is_delivered_folded_or_counted(protocol, seed):
    rng = random.Random(seed)
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(protocol, clock)
    runs = rng.randint(1, 40)
    offered = 0
    snapshots: list[tuple[dict, str]] = []
    frames: list[dict] = []
    for _ in range(rng.randint(500, 3000)):
        clock.now += rng.random() * 0.03
        roll = rng.random()
        run = f"run-{rng.randrange(runs)}"
        if roll < 0.02:
            event = {"type": LIVE_RECONCILE_FRAME}  # upstream: not a data event, never counted as one
        else:
            offered += 1
            if roll < 0.62:
                event = feed.result(run)
            elif roll < 0.72:
                event = feed.warning(run)
            elif roll < 0.95:
                event = feed.raw(rng.choice(("live_run_started", "live_run_complete", "run_completed")), run)
            else:
                event = feed.result(run, seq=False)
        snapshots.append((event, json.dumps(event, sort_keys=True)))
        pacer.offer(event)
        if rng.random() < 0.3:
            frame = pacer.take_frame()
            if frame is not None:
                frames.append(frame)
    while pacer.due_in() is not None:
        clock.now += INTERVAL
        frame = pacer.take_frame()
        if frame is not None:
            frames.append(frame)

    assert sum(_delivered(frame) for frame in frames) == offered
    for frame in frames:
        if frame["type"] == LIVE_BATCH_FRAME:
            assert frame["counts"]["events"] == _delivered(frame)
            assert frame["counts"]["entries"] == len(frame["events"]) <= ENTRIES_PER_FRAME_MAX
            assert frame["counts"]["results_listed"] <= RESULTS_PER_FRAME_MAX
            for entry in frame["events"]:
                if entry["type"] == "live_test_result" and "coalesced" in entry:
                    assert len(entry["results"]) + entry["results_dropped"] == entry["coalesced"]
        if protocol == LIVE_PROTOCOL_LEGACY:
            assert frame["type"] != LIVE_BATCH_FRAME
    assert all(json.dumps(event, sort_keys=True) == text for event, text in snapshots), (
        "an offered dict is shared by every socket's pacer and must never be mutated"
    )


# ── construction ──────────────────────────────────────────────────────────────────────────────────────


def test_the_interval_is_read_from_the_module_constant_at_construction(monkeypatch):
    assert LiveFramePacer(LIVE_PROTOCOL_BATCH).interval_s == pytest.approx(0.2)
    monkeypatch.setattr(live_frames, "FRAME_INTERVAL_MS", 50)
    assert LiveFramePacer(LIVE_PROTOCOL_BATCH).interval_s == pytest.approx(0.05)
    assert LiveFramePacer(LIVE_PROTOCOL_BATCH, interval_s=1.5).interval_s == 1.5


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(2, LIVE_PROTOCOL_BATCH), (3, LIVE_PROTOCOL_BATCH), (1, LIVE_PROTOCOL_LEGACY), (0, LIVE_PROTOCOL_LEGACY),
     (True, LIVE_PROTOCOL_LEGACY), ("2", LIVE_PROTOCOL_LEGACY), (None, LIVE_PROTOCOL_LEGACY)],
)
def test_the_protocol_is_read_leniently(requested, expected):
    assert LiveFramePacer(requested).protocol == expected


def test_due_in_and_pending_entries_follow_the_interval():
    clock, feed = _Clock(), _Feed()
    pacer = _pacer(LIVE_PROTOCOL_BATCH, clock)
    assert pacer.due_in() is None and pacer.pending_entries == 0
    pacer.offer(feed.raw())
    assert pacer.due_in() == 0.0 and pacer.pending_entries == 1
    pacer.take_frame()
    assert pacer.due_in() is None
    clock.now = 0.15
    pacer.offer(feed.raw())
    assert pacer.due_in() == pytest.approx(0.05)
    clock.now = 0.3
    assert pacer.due_in() == 0.0


@pytest.mark.parametrize("protocol", [LIVE_PROTOCOL_LEGACY, LIVE_PROTOCOL_BATCH])
def test_offer_never_raises_on_the_events_shape(protocol):
    clock = _Clock()
    pacer = _pacer(protocol, clock)
    odd = [
        {},
        {"type": None},
        {"type": ["live_test_result"], "run_id": "run-a", "sequence_id": "1-0"},
        {"type": "live_test_result", "run_id": ["unhashable"], "sequence_id": "1-1"},
        {"type": "live_test_result", "run_id": "run-a", "sequence_id": 7},
        {"type": "live_warning", "run_id": "", "sequence_id": "1-2"},
    ]
    for event in odd:
        pacer.offer(event)
    delivered = 0
    for step in range(40):
        clock.now = step * INTERVAL
        frame = pacer.take_frame()
        if frame is not None:
            delivered += _delivered(frame)
    assert delivered == len(odd)
