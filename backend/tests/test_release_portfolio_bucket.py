"""W11 REL-03: the release KPI cards' trend, bucketed (pure ``bucket_portfolio``).

Each series is the card's own rule at twelve Monday week ends and now:

* a release created five weeks ago and released two weeks ago is In progress
  at points -5..-3 and Released · 30d from -2 for four points (-2, -1, 0, now);
* a GO decision three weeks ago then a NO_GO last week is Ready at -3..-2 and
  Blocked from -1; a NOT_EVALUATED decision with blocking reasons is Blocked;
  a failed phase is Blocked at NOW only (today's phase state);
* a cancelled release counts nowhere; points before the scope's first release
  are ``None`` (absence), later empty points ``0`` (measured);
* the last point equals the card's own counts over the same rows (the
  ``health.ts`` rules restated below);
* the totals' denominators, CONDITIONAL_GO counted as evaluated and not GO,
  a retired pack is not coverage; the per-week evaluated / GO counts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.release_portfolio_service import bucket_portfolio, portfolio_points

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)      # a Thursday
M0 = datetime(2026, 10, 19, tzinfo=timezone.utc)              # the Monday on or before it
WEEKS = 12
HOUR = timedelta(hours=1)


def wk(n: int) -> datetime:
    """Point -n: the Monday n weeks before M0."""
    return M0 - timedelta(weeks=n)


def idx(n: int) -> int:
    """The series index of point -n (0 is the oldest; WEEKS is NOW)."""
    return WEEKS - 1 - n


NOW_IDX = WEEKS


def rel(rid, status, created, released=None):
    return SimpleNamespace(id=rid, status=status, created_at=created, released_at=released)


def dec(rid, verdict, at, reasons=False):
    return SimpleNamespace(release_id=rid, verdict=verdict, created_at=at, has_reasons=reasons)


def bucket(releases, decisions=(), failed=(), packed=()):
    return bucket_portfolio(releases, decisions, failed, packed, now=NOW, weeks=WEEKS)


def test_the_points_are_mondays_then_now():
    points = portfolio_points(NOW, WEEKS)
    assert len(points) == WEEKS + 1
    assert points[-2] == M0 and points[0] == wk(11)
    assert all(p.weekday() == 0 and p.hour == 0 for p in points[:-1])
    assert points[-1] == NOW


def test_created_five_weeks_ago_released_two_weeks_ago():
    out = bucket([rel("a", "released", wk(5), wk(2) - HOUR)])
    assert out["in_progress"] == [None] * 6 + [1, 1, 1, 0, 0, 0, 0]
    assert out["released_30d"] == [None] * 6 + [0, 0, 0, 1, 1, 1, 1]
    assert out["in_progress"][idx(5)] == 1 and out["in_progress"][idx(2)] == 0
    assert out["approximated"] == ["in_progress", "ready_to_ship", "blocked"]


def test_go_then_no_go_moves_ready_to_blocked():
    out = bucket(
        [rel("a", "in_progress", wk(8))],
        [dec("a", "GO", wk(3) - HOUR), dec("a", "NO_GO", wk(1) - HOUR)],
    )
    assert out["ready_to_ship"][idx(4)] == 0
    assert [out["ready_to_ship"][idx(n)] for n in (3, 2)] == [1, 1]
    assert [out["ready_to_ship"][idx(n)] for n in (1, 0)] == [0, 0]
    assert [out["blocked"][idx(n)] for n in (3, 2)] == [0, 0]
    assert [out["blocked"][idx(n)] for n in (1, 0)] == [1, 1]
    assert out["blocked"][NOW_IDX] == 1
    assert out["in_progress"][idx(8)] == 1 and out["in_progress"][idx(9)] is None


def test_a_cancelled_release_counts_nowhere():
    out = bucket(
        [rel("c", "cancelled", wk(10), wk(1)), rel("a", "in_progress", wk(10))],
        [dec("c", "NO_GO", wk(6))],
        failed={"c"},
    )
    assert out["in_progress"][idx(5)] == 1
    assert out["blocked"][NOW_IDX] == 0
    assert set(out["released_30d"][idx(10):]) == {0}


def test_points_before_the_first_release_are_none_and_later_ones_counts():
    out = bucket([rel("p", "planning", wk(4))])
    for name in ("in_progress", "ready_to_ship", "blocked", "released_30d"):
        assert out[name][:idx(4)] == [None] * idx(4)
        assert out[name][idx(4):] == [0] * (WEEKS + 1 - idx(4))   # measured: 0, not absent


def test_an_empty_scope_is_none_everywhere():
    out = bucket([])
    for name in ("in_progress", "ready_to_ship", "blocked", "released_30d"):
        assert out[name] == [None] * (WEEKS + 1)
    assert out["evaluated_in_week"] == [0] * (WEEKS + 1)
    assert out["totals"]["evaluated"] == 0


def test_not_evaluated_with_reasons_is_blocked():
    out = bucket([rel("a", "in_progress", wk(6))], [dec("a", "NOT_EVALUATED", wk(2) - HOUR, reasons=True)])
    assert out["blocked"][idx(2)] == 1 and out["blocked"][idx(3)] == 0
    assert out["ready_to_ship"][idx(2)] == 0


def test_a_failed_phase_is_blocked_at_now_only():
    out = bucket([rel("a", "in_progress", wk(6))], failed={"a"})
    assert out["blocked"][NOW_IDX] == 1
    assert out["blocked"][idx(0)] == 0


def card_counts(releases, decisions, failed, now):
    """``health.ts`` ``countReleases`` + ``mapping.ts``'s red blockers, restated."""
    from app.core.release_planning import release_stage

    latest = {}
    for d in sorted(decisions, key=lambda d: d.created_at):
        latest[d.release_id] = d
    in_progress = [r for r in releases if release_stage(r.status) == "in_progress"]
    ready = [r for r in in_progress if getattr(latest.get(r.id), "verdict", None) == "GO"]
    blocked = [
        r for r in in_progress
        if getattr(latest.get(r.id), "verdict", None) == "NO_GO"
        or getattr(latest.get(r.id), "has_reasons", False)
        or r.id in failed
    ]
    released = [
        r for r in releases
        if release_stage(r.status) == "released" and r.released_at and r.released_at >= now - timedelta(days=30)
    ]
    return len(in_progress), len(ready), len(blocked), len(released)


def test_the_last_point_is_the_cards_own_count():
    releases = [
        rel("a", "in_progress", wk(9)), rel("b", "active", wk(7)), rel("c", "RUNNING", wk(3)),
        rel("d", "released", wk(9), NOW - timedelta(days=12)), rel("e", "shipped", wk(11), NOW - timedelta(days=45)),
        rel("f", "planning", wk(2)), rel("g", "archived", wk(5)), rel("h", "in-progress", wk(1)),
    ]
    decisions = [
        dec("a", "NO_GO", wk(4)), dec("a", "GO", wk(1)),
        dec("b", "GO", wk(2)), dec("b", "CONDITIONAL_GO", NOW - HOUR, reasons=True),
        dec("c", "NO_GO", wk(0)),
        dec("h", "GO", NOW - HOUR),
    ]
    failed = {"h"}
    out = bucket(releases, decisions, failed)
    last = tuple(out[name][NOW_IDX] for name in ("in_progress", "ready_to_ship", "blocked", "released_30d"))
    assert last == card_counts(releases, decisions, failed, NOW)
    assert last == (4, 2, 3, 1)


def test_the_totals_and_their_denominators():
    releases = [
        rel("go", "in_progress", wk(11)), rel("cond", "in_progress", wk(11)),
        rel("nogo", "in_progress", wk(11)), rel("ne", "in_progress", wk(11)),
        rel("old", "in_progress", wk(11)),
        rel("shipped", "released", wk(11), wk(3)), rel("shipped_unpacked", "released", wk(11), wk(2)),
        rel("shipped_retired", "released", wk(11), wk(1)), rel("ancient", "released", wk(11), NOW - timedelta(weeks=13)),
    ]
    decisions = [
        dec("go", "NO_GO", wk(9)), dec("go", "GO", wk(5)),
        dec("cond", "CONDITIONAL_GO", wk(4)),
        dec("nogo", "NO_GO", wk(2)),
        dec("ne", "NOT_EVALUATED", wk(1)),
        dec("old", "GO", NOW - timedelta(weeks=13)),          # before the window: not evaluated in it
    ]
    out = bucket(releases, decisions, packed={"shipped", "ancient"})   # "shipped_retired"'s pack expired
    assert out["totals"] == {
        "evaluated": 4, "go": 1, "conditional_go": 1, "no_go": 1, "not_evaluated": 1,
        "released": 3, "released_with_pack": 1,
    }


def test_the_weekly_evaluated_and_go_counts():
    out = bucket(
        [rel("a", "in_progress", wk(8)), rel("b", "in_progress", wk(8))],
        [dec("a", "NO_GO", wk(2) - timedelta(days=3)), dec("a", "GO", wk(2) - timedelta(days=1)),
         dec("b", "NO_GO", wk(2) - timedelta(days=2))],
    )
    assert out["evaluated_in_week"][idx(2)] == 2
    assert out["go_in_week"][idx(2)] == 1         # a's latest that week is GO, b's NO_GO
    assert out["evaluated_in_week"][idx(1)] == 0  # nothing decided in the week before -1
    assert out["evaluated_in_week"][idx(3)] == 0


def test_naive_times_are_read_as_utc():
    naive = wk(5).replace(tzinfo=None)
    out = bucket([rel("a", "in_progress", naive)])
    assert out["in_progress"][idx(5)] == 1
