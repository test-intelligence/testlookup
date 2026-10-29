"""TRND-03 expected run cadence — the window math (PURE: no DB, no network, no clock).

A cadence says "a run arrives by <local time> on <days> in <IANA zone>, with
<grace> minutes of slack". Every expected-by instant is a DEADLINE; the WINDOW
of deadline k is ``(deadline[k-1] + grace, deadline[k] + grace]`` — contiguous
and disjoint, so every run counts for exactly one window.

The ONE DST rule (W23a contract §2.2 C-2): a local wall time resolves to the
LATER of the two UTC instants ``fold=0`` / ``fold=1`` give. A wall time that
does not exist (spring forward) therefore resolves with the pre-transition
offset, and one that occurs twice (fall back) to its second occurrence — the
deadline only ever moves LATER, so DST never produces a false "missed".

Cron is parsed with ``celery.schedules.crontab.from_string`` (no new
dependency) and only its expanded minute / hour / day-of-week sets are read;
day-of-month and month must be ``*``. IANA zones come from the ``tzdata``
wheel (a kombu dependency), so no OS tz database is needed.

Nothing here touches the database, the network or ``datetime.now()``: every
instant is passed in (tz-aware), so the tests pin DST with fixed instants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from celery.schedules import crontab

from app.services.run_cadence_vocab import (
    CADENCE_ERRORS,
    CADENCE_MODES,
    GRACE_MAX,
    GRACE_MIN,
    MAX_OCCURRENCES_PER_DAY,
    WEEKDAYS,
)

#: Every supported pattern repeats within a week; the previous / next search
#: looks this many local days around the reference instant (§2.2 C-2).
SEARCH_DAYS = 8

_ERROR_STATUS = {
    "cadence_not_found": 404,
    "duplicate_scope": 409,
    "too_many": 409,
    "not_paused": 409,
}

_DAY_LABEL = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_LABEL = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class CadenceError(Exception):
    """A refused cadence input; ``code`` is a ``CADENCE_ERRORS`` key.

    The routes map it to ``HTTPException(status_code, detail={"code", "message"})``.
    """

    def __init__(self, code: str, message: Optional[str] = None) -> None:
        self.code = code
        self.status_code = _ERROR_STATUS.get(code, 422)
        self.message = message if message is not None else CADENCE_ERRORS[code]
        super().__init__(self.message)

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class CadenceSpec:
    mode: str
    tz: ZoneInfo
    grace: timedelta
    weekdays: frozenset[int]          # Python weekday() numbers (Mon = 0)
    times: tuple[time, ...]           # weekly: (expected_by,); cron: every (hour, minute), sorted


@dataclass(frozen=True)
class Window:
    start: datetime                   # exclusive (UTC)
    end: datetime                     # inclusive = deadline + grace (UTC)
    deadline: datetime                # the expected-by instant, grace excluded (UTC)


# ── zones, weekdays, cron ─────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _zone_names() -> frozenset[str]:
    import zoneinfo

    return frozenset(zoneinfo.available_timezones())


def zone_for(name: str) -> ZoneInfo:
    """An IANA zone by name; anything else (a path, a typo) → ``unknown_timezone``."""
    if not isinstance(name, str) or name not in _zone_names():
        raise CadenceError("unknown_timezone")
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - listed names always load
        raise CadenceError("unknown_timezone") from None


def weekdays_mask(names: Sequence[str]) -> int:
    """``["mon", "fri"]`` → bitmask (bit i = WEEKDAYS[i]); an unknown name → ``weekly_fields``."""
    mask = 0
    for name in names:
        if name not in WEEKDAYS:
            raise CadenceError("weekly_fields")
        mask |= 1 << WEEKDAYS.index(name)
    return mask


def weekdays_names(mask: int) -> list[str]:
    return [name for i, name in enumerate(WEEKDAYS) if mask & (1 << i)]


def normalise_cron(text: str) -> str:
    """Collapse every run of whitespace to ONE space (``from_string`` splits on one space)."""
    return " ".join((text or "").split())


def parse_cron(text: str) -> tuple[frozenset[int], tuple[time, ...]]:
    """``"0 6 * * 1-5"`` → (Python weekdays, the day's sorted times).

    Five fields only; day-of-month and month must be ``*``; at most
    ``MAX_OCCURRENCES_PER_DAY`` times a day. Celery's day-of-week 0 is SUNDAY,
    mapped to Python's ``weekday()`` by ``(dow + 6) % 7``.
    """
    norm = normalise_cron(text)
    fields = norm.split(" ")
    if len(fields) != 5:
        raise CadenceError("bad_cron")
    try:
        parsed = crontab.from_string(norm)
    except Exception:
        raise CadenceError("bad_cron") from None
    if fields[2] != "*" or fields[3] != "*":
        raise CadenceError("cron_day_fields")
    if len(parsed.hour) * len(parsed.minute) > MAX_OCCURRENCES_PER_DAY:
        raise CadenceError("cron_too_frequent")
    times = tuple(sorted(time(h, m) for h in parsed.hour for m in parsed.minute))
    weekdays = frozenset((int(dow) + 6) % 7 for dow in parsed.day_of_week)
    if not times or not weekdays:  # pragma: no cover - celery never expands to empty
        raise CadenceError("bad_cron")
    return weekdays, times


def parse_expected_by(value: object) -> time:
    """``"06:00"`` or a ``time`` → a minute-precision ``time``; anything else → ``weekly_fields``."""
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if isinstance(value, str):
        parts = value.strip().split(":")
        if len(parts) == 2 and all(p.isdigit() and len(p) == 2 for p in parts):
            hour, minute = int(parts[0]), int(parts[1])
            if hour <= 23 and minute <= 59:
                return time(hour, minute)
    raise CadenceError("weekly_fields")


def spec_from(
    mode: str,
    weekdays_mask: Optional[int],
    expected_by: object,
    cron: Optional[str],
    timezone: str,
    grace_minutes: int,
) -> CadenceSpec:
    """Validate one definition and build its spec (raises ``CadenceError``).

    The checks run in the order the sentences read: mode, the mode's fields,
    the cron text, then the zone. ``grace_minutes`` is bounded by the schema
    and the DB CHECK; an out-of-range value here is a programming error.
    """
    if mode not in CADENCE_MODES:
        raise CadenceError("unknown_mode")
    if mode == "weekly":
        if not weekdays_mask or not 1 <= weekdays_mask <= 127 or expected_by is None or cron:
            raise CadenceError("weekly_fields")
        days = frozenset(i for i in range(7) if weekdays_mask & (1 << i))
        times: tuple[time, ...] = (parse_expected_by(expected_by),)
    else:
        if not cron or not cron.strip() or weekdays_mask or expected_by is not None:
            raise CadenceError("cron_fields")
        days, times = parse_cron(cron)
    if not GRACE_MIN <= int(grace_minutes) <= GRACE_MAX:
        raise ValueError("grace_minutes out of range")
    tz = zone_for(timezone)
    return CadenceSpec(mode=mode, tz=tz, grace=timedelta(minutes=int(grace_minutes)),
                       weekdays=days, times=times)


# ── the instants ──────────────────────────────────────────────────────────────


def _utc(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("a cadence instant must be timezone-aware")
    return instant.astimezone(timezone.utc)


def resolve_local(day: date, at: time, tz: ZoneInfo) -> datetime:
    """The UTC instant of ``day`` at wall time ``at`` in ``tz``: the LATER of fold=0 / fold=1.

    Europe/Berlin 2026-03-29 02:30 (does not exist) → 01:30Z; 2026-10-25 02:30
    (occurs twice) → 01:30Z, its second occurrence.
    """
    wall = datetime.combine(day, at.replace(tzinfo=None, fold=0), tzinfo=tz)
    first = wall.astimezone(timezone.utc)
    second = wall.replace(fold=1).astimezone(timezone.utc)
    return max(first, second)


def deadlines_between(spec: CadenceSpec, after: datetime, until: datetime) -> list[datetime]:
    """Every deadline with ``after < d <= until`` (UTC), sorted and DE-DUPLICATED.

    Two wall times that resolve to one instant (a cron at 02:30 and 03:30 on a
    spring-forward day) are ONE occurrence.
    """
    lo, hi = _utc(after), _utc(until)
    if hi <= lo:
        return []
    day = lo.astimezone(spec.tz).date() - timedelta(days=1)
    last = hi.astimezone(spec.tz).date() + timedelta(days=1)
    found: set[datetime] = set()
    while day <= last:
        if day.weekday() in spec.weekdays:
            for at in spec.times:
                instant = resolve_local(day, at, spec.tz)
                if lo < instant <= hi:
                    found.add(instant)
        day += timedelta(days=1)
    return sorted(found)


def previous_deadline(spec: CadenceSpec, before: datetime) -> Optional[datetime]:
    """The latest deadline strictly before ``before`` (searches ≤ 8 local days back)."""
    ref = _utc(before)
    found = [d for d in deadlines_between(spec, ref - timedelta(days=SEARCH_DAYS), ref) if d < ref]
    return found[-1] if found else None


def next_deadline(spec: CadenceSpec, after: datetime) -> Optional[datetime]:
    """The earliest deadline strictly after ``after`` (searches ≤ 8 local days ahead)."""
    ref = _utc(after)
    found = deadlines_between(spec, ref, ref + timedelta(days=SEARCH_DAYS))
    return found[0] if found else None


def windows_for(spec: CadenceSpec, deadlines: Sequence[datetime], *, first_start: datetime) -> list[Window]:
    """Window k = ``(deadline[k-1] + grace, deadline[k] + grace]``; the first starts at ``first_start``.

    The caller passes ``first_start = previous_deadline(spec, deadlines[0]) + grace``
    (``windows_ending_in`` does), so consecutive windows are contiguous and disjoint.
    """
    windows: list[Window] = []
    start = _utc(first_start)
    for deadline in deadlines:
        d = _utc(deadline)
        end = d + spec.grace
        windows.append(Window(start=start, end=end, deadline=d))
        start = end
    return windows


def first_window_start(spec: CadenceSpec, deadline: datetime) -> datetime:
    """``previous_deadline(deadline) + grace``; a week before when nothing precedes it within the search."""
    prev = previous_deadline(spec, deadline)
    if prev is None:  # pragma: no cover - every valid spec repeats within a week
        prev = _utc(deadline) - timedelta(days=7)
    return prev + spec.grace


def windows_ending_in(spec: CadenceSpec, after: datetime, until: datetime) -> list[Window]:
    """The windows whose END (``deadline + grace``) is in ``(after, until]``, oldest first."""
    deadlines = deadlines_between(spec, _utc(after) - spec.grace, _utc(until) - spec.grace)
    if not deadlines:
        return []
    return windows_for(spec, deadlines, first_start=first_window_start(spec, deadlines[0]))


# ── words ─────────────────────────────────────────────────────────────────────


def local_label(deadline: datetime, tz: ZoneInfo) -> str:
    """``"Wed 28 Oct"`` — the deadline's day in the CADENCE's zone (the day the team meant)."""
    local = _utc(deadline).astimezone(tz)
    return f"{_DAY_LABEL[local.weekday()]} {local.day} {_MONTH_LABEL[local.month - 1]}"


def days_label(days: frozenset[int]) -> str:
    """``Every day`` · ``Mon–Fri`` (ONE run of three or more days) · else ``Mon, Wed, Fri``.

    The same rule as the SPA's ``weekdaysLabel`` (``frontend/src/types/runCadence.ts``).
    """
    if len(days) == 7:
        return "Every day"
    ordered = sorted(days)
    contiguous = all(b == a + 1 for a, b in zip(ordered, ordered[1:]))
    if contiguous and len(ordered) >= 3:
        return f"{_DAY_LABEL[ordered[0]]}–{_DAY_LABEL[ordered[-1]]}"
    return ", ".join(_DAY_LABEL[k] for k in ordered)


def _times_label(times: tuple[time, ...]) -> str:
    shown = [t.strftime("%H:%M") for t in times[:4]]
    more = len(times) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def describe(spec: CadenceSpec, *, suite_name: Optional[str] = None) -> str:
    """``RunCadenceRead.summary``: ``"Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min"``.

    The summary names WHEN only; the scope (``suite_name`` or "any run") is
    shown beside it by every reader (the table's first line, the ledger's
    ``{entity_label}``), so it is not repeated here.
    """
    del suite_name  # the scope is rendered separately (see the docstring)
    grace = int(spec.grace.total_seconds() // 60)
    return (f"{days_label(spec.weekdays)} by {_times_label(spec.times)} "
            f"({spec.tz.key}) · grace {grace} min")
