"""TRND-03 — the PURE window math of ``run_cadence_schedule`` (W23a contract §2.2 C-2).

Every instant is built INSIDE its test from fixed ``ZoneInfo`` wall times (no
module constant, no parametrize list of datetimes), so a DST edge is pinned
exactly: the ONE rule is "the LATER of the fold=0 / fold=1 instants".
"""

from __future__ import annotations

import zoneinfo
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.services import run_cadence_schedule as sched
from app.services.run_cadence_schedule import CadenceError
from app.services.run_cadence_vocab import CADENCE_ERRORS, WEEKDAYS


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _weekly(days, at="06:00", tz="Europe/Berlin", grace=60):
    return sched.spec_from("weekly", sched.weekdays_mask(days), at, None, tz, grace)


# ── resolve_local: the four DST edges and an ordinary day ─────────────────────


def test_spring_forward_berlin_resolves_with_the_pre_transition_offset():
    tz = zoneinfo.ZoneInfo("Europe/Berlin")
    assert sched.resolve_local(date(2026, 3, 29), time(2, 30), tz) == _utc("2026-03-29T01:30:00+00:00")


def test_fall_back_berlin_resolves_to_the_second_occurrence():
    tz = zoneinfo.ZoneInfo("Europe/Berlin")
    got = sched.resolve_local(date(2026, 10, 25), time(2, 30), tz)
    assert got == _utc("2026-10-25T01:30:00+00:00")
    assert got != _utc("2026-10-25T00:30:00+00:00")


def test_spring_forward_new_york():
    tz = zoneinfo.ZoneInfo("America/New_York")
    assert sched.resolve_local(date(2026, 3, 8), time(2, 30), tz) == _utc("2026-03-08T07:30:00+00:00")


def test_fall_back_new_york():
    tz = zoneinfo.ZoneInfo("America/New_York")
    assert sched.resolve_local(date(2026, 11, 1), time(1, 30), tz) == _utc("2026-11-01T06:30:00+00:00")


def test_ordinary_day_berlin():
    tz = zoneinfo.ZoneInfo("Europe/Berlin")
    got = sched.resolve_local(date(2026, 10, 28), time(6, 0), tz)
    assert got == _utc("2026-10-28T05:00:00+00:00")
    assert got.tzinfo is timezone.utc


# ── deadlines and windows across DST weeks ────────────────────────────────────


def test_weekday_cadence_over_the_fall_back_week():
    spec = _weekly(["mon", "tue", "wed", "thu", "fri"])
    tz = spec.tz
    deadlines = sched.deadlines_between(spec, datetime(2026, 10, 19, 0, 0, tzinfo=tz),
                                        datetime(2026, 10, 30, 23, 59, tzinfo=tz))
    assert len(deadlines) == 10
    first_start = sched.previous_deadline(spec, deadlines[0]) + spec.grace
    windows = sched.windows_for(spec, deadlines, first_start=first_start)
    by_label = {sched.local_label(w.deadline, tz): w.end - w.start for w in windows}
    assert by_label["Mon 26 Oct"] == timedelta(hours=73)          # the weekend + the extra hour
    assert by_label["Mon 19 Oct"] == timedelta(hours=72)          # an ordinary weekend
    others = [v for k, v in by_label.items() if not k.startswith("Mon")]
    assert others == [timedelta(hours=24)] * 8
    # every deadline is 06:00 on the wall clock, CEST before the change and CET after
    assert windows[5].deadline == _utc("2026-10-26T05:00:00+00:00")
    assert windows[4].deadline == _utc("2026-10-23T04:00:00+00:00")


def test_daily_0230_across_spring_forward_yields_one_deadline_a_day():
    spec = _weekly(list(WEEKDAYS), at="02:30")
    tz = spec.tz
    deadlines = sched.deadlines_between(spec, datetime(2026, 3, 27, 0, 0, tzinfo=tz),
                                        datetime(2026, 3, 31, 23, 59, tzinfo=tz))
    assert len(deadlines) == 5
    assert _utc("2026-03-29T01:30:00+00:00") in deadlines


def test_cron_two_times_coincide_on_spring_forward_day():
    spec = sched.spec_from("cron", None, None, "30 2,3 * * *", "Europe/Berlin", 60)
    tz = spec.tz
    on_29 = sched.deadlines_between(spec, datetime(2026, 3, 29, 0, 0, tzinfo=tz),
                                    datetime(2026, 3, 29, 23, 59, tzinfo=tz))
    on_30 = sched.deadlines_between(spec, datetime(2026, 3, 30, 0, 0, tzinfo=tz),
                                    datetime(2026, 3, 30, 23, 59, tzinfo=tz))
    assert on_29 == [_utc("2026-03-29T01:30:00+00:00")]
    assert len(on_30) == 2


def test_daily_0230_across_fall_back_has_one_window_that_day():
    spec = _weekly(list(WEEKDAYS), at="02:30", grace=30)
    tz = spec.tz
    windows = sched.windows_ending_in(spec, datetime(2026, 10, 25, 0, 0, tzinfo=tz),
                                      datetime(2026, 10, 25, 23, 59, tzinfo=tz))
    assert len(windows) == 1
    assert windows[0].end == _utc("2026-10-25T01:30:00+00:00") + timedelta(minutes=30)
    assert windows[0].end - windows[0].start == timedelta(hours=25)


# ── parse_cron ────────────────────────────────────────────────────────────────


def test_parse_cron_weekdays_and_sunday():
    days, times = sched.parse_cron("0 6 * * 1-5")
    assert days == frozenset({0, 1, 2, 3, 4}) and times == (time(6, 0),)
    days, times = sched.parse_cron("0 6 * * 0")
    assert days == frozenset({6})


def test_parse_cron_two_spaces_accepted_and_normalised():
    days, times = sched.parse_cron("0  6 * * 1")
    assert days == frozenset({0}) and times == (time(6, 0),)
    assert sched.normalise_cron("0  6 *\t* 1 ") == "0 6 * * 1"


def test_parse_cron_times_sorted_hour_by_minute():
    _days, times = sched.parse_cron("45,15 7,3 * * *")
    assert times == (time(3, 15), time(3, 45), time(7, 15), time(7, 45))


def _cron_code(text: str) -> str:
    with pytest.raises(CadenceError) as err:
        sched.parse_cron(text)
    assert err.value.status_code == 422
    assert err.value.message == CADENCE_ERRORS[err.value.code]
    return err.value.code


def test_parse_cron_refusals():
    assert _cron_code("0 6 1 * *") == "cron_day_fields"
    assert _cron_code("0 6 * 2 *") == "cron_day_fields"
    assert _cron_code("*/5 * * * *") == "cron_too_frequent"
    assert _cron_code("nonsense") == "bad_cron"
    assert _cron_code("0 6 * * * *") == "bad_cron"
    assert _cron_code("61 6 * * *") == "bad_cron"
    assert _cron_code("") == "bad_cron"


def test_parse_cron_24_times_a_day_is_the_ceiling():
    _days, times = sched.parse_cron("0 * * * *")
    assert len(times) == 24
    assert _cron_code("0,30 0-12 * * *") == "cron_too_frequent"


# ── spec_from validation ──────────────────────────────────────────────────────


def _spec_code(*args) -> str:
    with pytest.raises(CadenceError) as err:
        sched.spec_from(*args)
    return err.value.code


def test_spec_from_refusals():
    assert _spec_code("daily", 1, "06:00", None, "UTC", 60) == "unknown_mode"
    assert _spec_code("weekly", 0, "06:00", None, "UTC", 60) == "weekly_fields"
    assert _spec_code("weekly", 1, None, None, "UTC", 60) == "weekly_fields"
    assert _spec_code("weekly", 1, "06:00", "0 6 * * *", "UTC", 60) == "weekly_fields"
    assert _spec_code("weekly", 1, "6:00", None, "UTC", 60) == "weekly_fields"
    assert _spec_code("cron", None, None, None, "UTC", 60) == "cron_fields"
    assert _spec_code("cron", 1, None, "0 6 * * *", "UTC", 60) == "cron_fields"
    assert _spec_code("cron", None, "06:00", "0 6 * * *", "UTC", 60) == "cron_fields"
    assert _spec_code("weekly", 1, "06:00", None, "Mars/Olympus", 60) == "unknown_timezone"
    assert _spec_code("weekly", 1, "06:00", None, "../etc/passwd", 60) == "unknown_timezone"
    assert _spec_code("weekly", 1, "06:00", None, "/etc/passwd", 60) == "unknown_timezone"


def test_weekdays_mask_round_trip_and_unknown_name():
    for mask in (1, 31, 64, 127, 0b1010101):
        assert sched.weekdays_mask(sched.weekdays_names(mask)) == mask
    assert sched.weekdays_mask(["fri", "mon", "mon"]) == 0b10001
    assert sched.weekdays_names(0b10001) == ["mon", "fri"]
    with pytest.raises(CadenceError) as err:
        sched.weekdays_mask(["mon", "funday"])
    assert err.value.code == "weekly_fields"


# ── windows, grace, labels ────────────────────────────────────────────────────


@pytest.mark.parametrize("grace", [0, 720])
def test_windows_are_contiguous_and_disjoint(grace):
    spec = _weekly(["mon", "wed", "fri"], at="09:15", tz="America/New_York", grace=grace)
    tz = spec.tz
    deadlines = sched.deadlines_between(spec, datetime(2026, 10, 26, 0, 0, tzinfo=tz),
                                        datetime(2026, 11, 13, 0, 0, tzinfo=tz))
    first_start = sched.previous_deadline(spec, deadlines[0]) + timedelta(minutes=grace)
    windows = sched.windows_for(spec, deadlines, first_start=first_start)
    assert windows[0].start == first_start
    for prev, cur in zip(windows, windows[1:]):
        assert cur.start == prev.end
    for w in windows:
        assert w.end == w.deadline + timedelta(minutes=grace) and w.end > w.start
    assert windows == sched.windows_ending_in(spec, deadlines[0] + timedelta(minutes=grace) - timedelta(seconds=1),
                                              deadlines[-1] + timedelta(minutes=grace))


def test_windows_ending_in_is_exclusive_below_and_inclusive_above():
    spec = _weekly(["wed"], at="06:00", grace=60)
    end = _utc("2026-10-28T06:00:00+00:00")             # Wed 06:00 CET + 60 min
    assert len(sched.windows_ending_in(spec, end - timedelta(seconds=1), end)) == 1
    assert sched.windows_ending_in(spec, end, end + timedelta(days=6)) == []


def test_previous_and_next_deadline():
    spec = _weekly(["wed"], at="06:00")
    ref = _utc("2026-10-28T05:00:00+00:00")             # exactly Wed 06:00 CET
    assert sched.previous_deadline(spec, ref) == _utc("2026-10-21T04:00:00+00:00")
    assert sched.next_deadline(spec, ref) == _utc("2026-11-04T05:00:00+00:00")
    assert sched.next_deadline(spec, ref - timedelta(seconds=1)) == ref


def test_local_label_uses_the_zone_day():
    instant = _utc("2026-10-27T23:30:00+00:00")
    assert sched.local_label(instant, zoneinfo.ZoneInfo("Europe/Berlin")) == "Wed 28 Oct"
    assert sched.local_label(instant, zoneinfo.ZoneInfo("America/New_York")) == "Tue 27 Oct"
    assert sched.local_label(_utc("2026-11-01T12:00:00+00:00"), zoneinfo.ZoneInfo("UTC")) == "Sun 1 Nov"


def test_describe():
    assert sched.describe(_weekly(["mon", "tue", "wed", "thu", "fri"]), suite_name="nightly") == \
        "Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min"
    assert sched.describe(_weekly(list(WEEKDAYS), at="23:05", tz="UTC", grace=0), suite_name=None) == \
        "Every day by 23:05 (UTC) · grace 0 min"
    assert sched.describe(_weekly(["mon", "wed", "fri"]), suite_name=None) == \
        "Mon, Wed, Fri by 06:00 (Europe/Berlin) · grace 60 min"
    # the SPA's weekdaysLabel rule: a range only when the WHOLE set is one run of 3+
    assert sched.days_label(frozenset({0, 1, 2, 4})) == "Mon, Tue, Wed, Fri"
    assert sched.days_label(frozenset({5, 6})) == "Sat, Sun"
    cron = sched.spec_from("cron", None, None, "0 1,2,3,4,5 * * 6,0", "UTC", 15)
    assert sched.describe(cron, suite_name=None) == \
        "Sat, Sun by 01:00, 02:00, 03:00, 04:00 and 1 more (UTC) · grace 15 min"


def test_naive_instants_are_refused():
    spec = _weekly(["mon"])
    with pytest.raises(ValueError):
        sched.deadlines_between(spec, datetime(2026, 10, 19), datetime(2026, 10, 26))


# ── the tzdata wheel pin (K-8) ────────────────────────────────────────────────


def test_zones_resolve_from_the_tzdata_wheel_without_an_os_database():
    try:
        zoneinfo.reset_tzpath(to=[])
        assert zoneinfo.TZPATH == ()
        tz = zoneinfo.ZoneInfo.no_cache("Europe/Berlin")
        assert datetime(2026, 7, 1, 12, 0, tzinfo=tz).utcoffset() == timedelta(hours=2)
        assert "Europe/Berlin" in zoneinfo.available_timezones()
    finally:
        zoneinfo.reset_tzpath()
