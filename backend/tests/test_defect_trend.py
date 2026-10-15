"""DEF-04 unit: the defect trend's post-processing (``analytics_service.build_trend_payload``) and window.

The SQL is pinned on PostgreSQL in ``tests/integration/test_defect_trend_postgres.py``;
this is the pure half: the response keys, the 7-point rolling MTTR (``None``,
never 0, with nothing resolved), the window totals and the last-7-days net.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.services.analytics_service import TREND_MTTR_POINTS, build_trend_payload, trend_window

D0 = date(2026, 10, 1)


def _rows(spec):
    """spec: list of (opened, closed, backlog, p0p1, resolved_n, resolve_days_sum)."""
    return [
        {"day": D0 + timedelta(days=i), "opened": o, "closed": c, "open_backlog": b, "p0p1_open": h,
         "resolved_n": n, "resolve_days_sum": s}
        for i, (o, c, b, h, n, s) in enumerate(spec)
    ]


TOTALS = {"open_total": 3, "p0_open": 1, "p1_open": 1, "closed_without_time": 2}


def test_the_response_has_exactly_the_contract_keys():
    payload = build_trend_payload(_rows([(1, 0, 3, 1, 0, 0)]), TOTALS, 1)
    assert set(payload) == {"points", "totals", "window_days"}
    assert set(payload["points"][0]) == {"date", "opened", "closed", "open_backlog", "p0p1_open", "mttr_days"}
    assert set(payload["totals"]) == {
        "open", "p0_open", "p1_open", "mttr_days", "resolved_in_window", "opened_in_window", "closed_in_window",
        "opened_last_7d", "closed_last_7d", "net_last_7d", "closed_without_time",
    }
    assert payload["points"][0]["date"] == "2026-10-01"
    assert (payload["totals"]["open"], payload["totals"]["closed_without_time"]) == (3, 2)


def test_the_rolling_mttr_is_over_the_trailing_seven_points_and_none_without_a_resolve():
    # Day 0 resolves one in 2 days, day 8 resolves two in 4 + 6 days; nothing else.
    spec = [(0, 0, 0, 0, 0, 0)] * 10
    spec[0] = (0, 1, 0, 0, 1, 2.0)
    spec[8] = (0, 2, 0, 0, 2, 10.0)
    points = build_trend_payload(_rows(spec), TOTALS, 10)["points"]
    assert TREND_MTTR_POINTS == 7
    assert [p["mttr_days"] for p in points[:7]] == [2.0] * 7      # day 0 is in the trailing 7 of days 0..6
    assert points[7]["mttr_days"] is None                          # days 1..7: nothing resolved
    assert points[8]["mttr_days"] == points[9]["mttr_days"] == 5.0


def test_window_totals_and_the_last_seven_days():
    spec = [(2, 1, 0, 0, 1, 1.5)] * 10
    spec[9] = (5, 0, 0, 0, 0, 0)
    totals = build_trend_payload(_rows(spec), TOTALS, 10)["totals"]
    assert totals["opened_in_window"] == 2 * 9 + 5
    assert totals["closed_in_window"] == 9
    assert totals["resolved_in_window"] == 9
    assert totals["mttr_days"] == 1.5
    assert (totals["opened_last_7d"], totals["closed_last_7d"]) == (2 * 6 + 5, 6)
    assert totals["net_last_7d"] == 17 - 6


def test_a_short_window_takes_all_its_points_for_the_last_seven_days():
    totals = build_trend_payload(_rows([(1, 0, 1, 0, 0, 0), (1, 1, 1, 0, 0, 0)]), TOTALS, 2)["totals"]
    assert (totals["opened_last_7d"], totals["closed_last_7d"], totals["net_last_7d"]) == (2, 1, 1)
    assert totals["mttr_days"] is None, "a close that is not RESOLVED has no time to resolve"


def test_decimals_from_the_driver_are_plain_numbers():
    from decimal import Decimal

    rows = _rows([(1, 1, 0, 0, 1, 0)])
    rows[0].update(open_backlog=Decimal(4), resolve_days_sum=Decimal("0.25"))
    point = build_trend_payload(rows, TOTALS, 1)["points"][0]
    assert point["open_backlog"] == 4 and isinstance(point["open_backlog"], int)
    assert point["mttr_days"] == 0.25 and isinstance(point["mttr_days"], float)


def test_the_window_is_days_utc_days_ending_today():
    assert trend_window(30, date(2026, 10, 15)) == (date(2026, 9, 16), date(2026, 10, 15))
    assert trend_window(1, date(2026, 10, 15)) == (date(2026, 10, 15), date(2026, 10, 15))
