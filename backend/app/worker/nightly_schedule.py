"""UTC contracts shared by Beat, workers and schedule monitoring."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class NightlySchedule:
    key: str
    task_name: str
    flag: str
    hour: int
    minute: int


QUARANTINE_MAINTENANCE = NightlySchedule(
    "quarantine_maintenance", "app.worker.tasks.run_flaky_quarantine_maintenance", "flaky_auto_quarantine", 4, 0,
)
PERF_BASELINE_REFRESH = NightlySchedule(
    "perf_baseline_refresh", "app.worker.tasks.refresh_perf_baselines", "perf_regression_detection", 4, 30,
)
NIGHTLY_SCHEDULES = (QUARANTINE_MAINTENANCE, PERF_BASELINE_REFRESH)
NIGHTLY_SUCCESS_GRACE = timedelta(hours=1)
SCHEDULE_HEADER = "testlookup-nightly-schedule"
SLOT_HEADER = "testlookup-nightly-slot"


def latest_due(schedule: NightlySchedule, now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    slot = now.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def stamp_nightly_slot(sender=None, headers=None, **_kwargs) -> None:
    """Use the scheduled slot when publishing so queue delay cannot change it."""
    if not isinstance(headers, dict) or SCHEDULE_HEADER not in headers or SLOT_HEADER in headers:
        return
    for schedule in NIGHTLY_SCHEDULES:
        if sender == schedule.task_name and headers[SCHEDULE_HEADER] == schedule.key:
            headers[SLOT_HEADER] = int(latest_due(schedule, datetime.now(timezone.utc)).timestamp())
            return


def read_nightly_slot(schedule: NightlySchedule, headers: object, now: datetime) -> datetime | None:
    if not isinstance(headers, dict) or headers.get(SCHEDULE_HEADER) != schedule.key:
        return None
    stamp = headers.get(SLOT_HEADER)
    if type(stamp) is not int or not 0 < stamp <= now.timestamp():
        return None
    slot = datetime.fromtimestamp(stamp, timezone.utc)
    return slot if latest_due(schedule, slot) == slot else None
