"""W34 STRAT-13 — the drop-folder watcher's pure rules and its schedule entry.

The sweep's glue runs on fakes in ``test_drop_folder_watcher_sweep.py`` and on
PostgreSQL in ``integration/test_drop_folder_watcher_postgres.py``; this module
pins the decisions those build on: which prefixes a project has, which folder
keys are refused, when a folder is HEADed, how a HEAD is classified against the
baseline, how the stored baseline is read, and the beat entry / route / limits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from celery.schedules import crontab

from app.services import drop_folder_watcher as dfw

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def test_each_project_has_its_id_and_its_slug_prefix_once() -> None:
    a, b = uuid.UUID(int=1), uuid.UUID(int=2)
    assert dfw.project_prefixes([(a, "auth-service"), (b, None), (a, "auth-service")]) == [
        f"{a}/runs/", "auth-service/runs/", f"{b}/runs/",
    ]
    # a slug equal to the id string is one prefix, and a blank slug is none
    assert dfw.project_prefixes([(a, str(a)), (b, "  ")]) == [f"{a}/runs/", f"{b}/runs/"]


@pytest.mark.parametrize("prefix", [
    "auth-service/runs/7/", "0b6f0b5e-2a49-4d55-9c39-2f7a5a1f7c10/runs/build-2026.11.05_1/",
])
def test_a_plain_build_folder_has_no_key_problem(prefix: str) -> None:
    assert dfw.key_problem(prefix) is None
    assert dfw.sentinel_key_for(prefix) == f"{prefix}upload_complete.json"
    assert dfw.segment_of(prefix) == prefix.split("/")[0]


@pytest.mark.parametrize("prefix, reason", [
    ("p/runs/../", "'..' segment"),
    ("p/runs/./", "'.' segment"),
    ("p/runs//", "empty segment"),
    ("p/runs/a\\b/", "backslash"),
    ("p/runs/a\x00b/", "control character"),
    ("p/other/7/", "is not <project>/runs/<build>/"),
    ("p/runs/7/x/", "is not <project>/runs/<build>/"),
    ("p/runs/7", "is not <project>/runs/<build>/"),
])
def test_a_folder_whose_key_the_webhook_would_refuse_is_refused(prefix: str, reason: str) -> None:
    problem = dfw.key_problem(prefix)
    assert problem is not None and reason in problem


def test_a_folder_is_headed_when_new_or_pending_under_a_day() -> None:
    assert dfw.needs_head(None, NOW)
    fresh = dfw.KnownBuild(status=dfw.STATUS_PENDING, first_seen_at=NOW - timedelta(hours=23, minutes=59))
    stale = dfw.KnownBuild(status=dfw.STATUS_PENDING, first_seen_at=NOW - timedelta(hours=24))
    assert dfw.needs_head(fresh, NOW) and not dfw.needs_head(stale, NOW)
    for status in (dfw.STATUS_QUEUED, dfw.STATUS_BASELINE, dfw.STATUS_ABANDONED, dfw.STATUS_REFUSED):
        assert not dfw.needs_head(dfw.KnownBuild(status=status, first_seen_at=NOW), NOW), status


def test_new_folders_come_first_then_pending_ones_least_recently_checked_first() -> None:
    def known(status: str, checked_minutes_ago: int, first_hours_ago: int = 1) -> dfw.KnownBuild:
        return dfw.KnownBuild(status=status, first_seen_at=NOW - timedelta(hours=first_hours_ago),
                              last_checked_at=NOW - timedelta(minutes=checked_minutes_ago))

    listed = ["p/runs/1/", "p/runs/2/", "p/runs/3/", "p/runs/4/", "p/runs/5/", "p/runs/6/", "p/runs/7/"]
    rows = {
        "p/runs/1/": known(dfw.STATUS_PENDING, 1),
        "p/runs/2/": known(dfw.STATUS_QUEUED, 1),
        "p/runs/4/": known(dfw.STATUS_PENDING, 9),
        "p/runs/5/": known(dfw.STATUS_PENDING, 5, first_hours_ago=25),     # past the window: not a candidate
        "p/runs/6/": known(dfw.STATUS_PENDING, 3),
    }
    assert dfw.candidates(listed, rows, NOW) == ["p/runs/3/", "p/runs/7/", "p/runs/4/", "p/runs/6/", "p/runs/1/"]


def test_a_head_is_pending_baseline_or_queued_against_the_baseline() -> None:
    def head(at: datetime) -> SimpleNamespace:
        return SimpleNamespace(last_modified=at)

    assert dfw.classify(None, NOW) == dfw.STATUS_PENDING
    assert dfw.classify(head(NOW - timedelta(seconds=1)), NOW) == dfw.STATUS_BASELINE
    assert dfw.classify(head(NOW), NOW) == dfw.STATUS_QUEUED
    assert dfw.classify(head(NOW + timedelta(minutes=3)), NOW) == dfw.STATUS_QUEUED


def test_the_stored_baseline_is_read_as_an_aware_time() -> None:
    assert dfw.parse_baseline({"at": NOW.isoformat()}) == NOW
    assert dfw.parse_baseline({"at": "2026-11-05T12:00:00"}) == NOW          # naive -> UTC
    for broken in (None, {}, {"at": None}, {"at": "yesterday"}, "2026-11-05T12:00:00+00:00", []):
        assert dfw.parse_baseline(broken) is None, broken


def test_the_vocabulary_and_the_bounds() -> None:
    assert dfw.BUILD_STATUSES == ("pending", "queued", "baseline", "abandoned", "refused")
    assert dfw.FEATURE_FLAG == "drop_folder_watcher"
    assert dfw.PENDING_WINDOW == timedelta(hours=24)
    assert 0 < dfw.MAX_QUEUED_PER_SWEEP <= min(dfw.MAX_NEW_CHECKS_PER_SWEEP, dfw.MAX_PENDING_CHECKS_PER_SWEEP)
    # the lock outlives the task's hard limit, and the sweep's own deadline ends before the soft limit
    from app.worker import tasks

    task = tasks.watch_drop_folders
    assert dfw.LOCK_TTL_SECONDS > task.time_limit and dfw.SWEEP_DEADLINE_SECONDS < task.soft_time_limit


def test_the_beat_entry_its_queue_route_and_limits() -> None:
    from app.worker.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["watch-drop-folders"]
    assert entry["task"] == "app.worker.tasks.watch_drop_folders"
    assert entry["schedule"] == crontab(minute="*")
    assert entry["options"]["queue"] == "default" and entry["options"]["ignore_result"] is True
    assert celery_app.conf.task_routes["app.worker.tasks.watch_drop_folders"] == {"queue": "default"}
    task = celery_app.tasks["app.worker.tasks.watch_drop_folders"]
    assert task.queue == "default" and task.soft_time_limit == 240 and task.time_limit == 300


def test_the_task_runs_the_sweep_with_its_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import tasks

    seen: list[str] = []

    async def _sweep(*, owner: str, now: datetime | None = None) -> dict:
        seen.append(owner)
        return {"queued": 2, "failed": 0}

    monkeypatch.setattr(tasks, "_drop_folder_watcher_sweep", _sweep)
    out = tasks.watch_drop_folders.apply(task_id="beat-tick-dfw").get()
    assert out == {"queued": 2, "failed": 0} and seen == ["beat-tick-dfw"]
