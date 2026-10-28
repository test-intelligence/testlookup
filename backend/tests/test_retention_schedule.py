"""W22b ACT-06 W-3 / W-5: the due-project selector and the next scheduled purge.

``due_project_ids`` is the hourly beat's ONE target statement (G-B's sweep
reads it with ``.scalars().all()``); ``next_scheduled_purge_at`` is what the
policy GET reports. Every ``now`` is built inside its test.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.services import retention_service as svc  # noqa: E402
from app.services.retention_service import EffectiveRetentionPolicy  # noqa: E402


def _compiled(now: datetime):
    return svc.due_project_ids_statement(now=now).compile(dialect=postgresql.dialect())


def test_the_selector_resolves_enabled_and_the_window_three_ways():
    now = datetime(2026, 10, 28, 3, 0, 5, tzinfo=timezone.utc)
    sql = " ".join(str(_compiled(now)).split())
    assert "coalesce(project_retention_policies.enabled, instance_retention_policy.enabled, false)" in sql
    assert re.search(
        r"coalesce\(project_retention_policies\.purge_window_start_utc, "
        r"instance_retention_policy\.purge_window_start_utc, %\(\w+\)s\) = %\(purge_hour\)s",
        sql,
    ), sql
    assert "LEFT OUTER JOIN project_retention_policies" in sql
    assert "LEFT OUTER JOIN instance_retention_policy ON instance_retention_policy.id = " in sql


def test_the_selector_binds_the_utc_hour_and_the_pause_predicate():
    # 05:30 at UTC+02:00 is 03:30 UTC: the hour bound is 3.
    now = datetime(2026, 10, 28, 5, 30, tzinfo=timezone(timedelta(hours=2)))
    compiled = _compiled(now)
    sql = " ".join(str(compiled).split())
    assert compiled.params["purge_hour"] == 3
    assert 2 in compiled.params.values(), "the code-default window hour is the third COALESCE step"
    assert (
        "(project_retention_policies.purge_paused_until IS NULL OR "
        "project_retention_policies.purge_paused_until <= %(purge_paused_until_1)s)"
    ) in sql
    assert compiled.params["purge_paused_until_1"] == now


def test_the_selector_does_not_filter_is_active_and_orders_by_id():
    now = datetime(2026, 10, 28, 2, 0, tzinfo=timezone.utc)
    sql = str(_compiled(now))
    assert "is_active" not in sql, "the beat never filtered is_active (the deleted-project report relies on it)"
    assert sql.rstrip().endswith("ORDER BY projects.id")
    assert sql.count("SELECT") == 1, "ONE statement, no subquery"


async def test_due_project_ids_reads_one_statement_with_scalars():
    ids = [uuid.uuid4(), uuid.uuid4()]

    class _DB:
        def __init__(self):
            self.statements = []

        async def execute(self, stmt):
            self.statements.append(stmt)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(ids)))

    db = _DB()
    out = await svc.due_project_ids(db, now=datetime.now(timezone.utc))
    assert out == ids
    assert len(db.statements) == 1


def _policy(*, enabled=True, window=3, paused=None):
    return EffectiveRetentionPolicy(enabled=enabled, purge_window_start_utc=window, purge_paused_until=paused)


def test_next_purge_is_none_while_disabled():
    now = datetime(2026, 10, 28, 2, 59, tzinfo=timezone.utc)
    assert svc.next_scheduled_purge_at(_policy(enabled=False), now=now) is None


def test_next_purge_is_today_before_the_window_hour():
    now = datetime(2026, 10, 28, 2, 59, tzinfo=timezone.utc)
    assert svc.next_scheduled_purge_at(_policy(), now=now) == datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)


def test_next_purge_is_tomorrow_once_the_window_hour_started():
    now = datetime(2026, 10, 28, 3, 0, 30, tzinfo=timezone.utc)
    assert svc.next_scheduled_purge_at(_policy(), now=now) == datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)


def test_next_purge_skips_the_paused_days():
    # Wednesday 2026-10-28; paused until Friday 10:00 -> Friday 03:00 is
    # skipped, Saturday 03:00 is next.
    now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    friday = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)
    got = svc.next_scheduled_purge_at(_policy(paused=friday), now=now)
    assert got == datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc)
    assert got.weekday() == 5


def test_next_purge_answers_in_utc_for_a_local_now():
    now = datetime(2026, 10, 28, 1, 30, tzinfo=timezone(timedelta(hours=-5)))   # 06:30 UTC
    assert svc.next_scheduled_purge_at(_policy(window=2), now=now) == datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc)
