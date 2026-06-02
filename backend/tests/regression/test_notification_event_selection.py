"""Guard for notification event selection in ``_load_and_notify``.

Reviewed in review/notification-manager (2026-06-02): the service is clean.
This pins the one non-obvious, refactor-fragile behaviour — the
``failure_rate_threshold`` field is a misleadingly-named **pass-rate floor**:
HIGH_FAILURE_RATE fires when ``pass_rate < threshold`` (the UI label confirms
"alerts trigger when pass rate drops below {threshold}%"), and the priority
order means HIGH_FAILURE_RATE outranks RUN_FAILED for a pref subscribed to both.

(Also documents the deliberate decision NOT to gate notifications on
AI_OFFLINE_MODE — they must work in the default offline-first install, which is
why AI_OFFLINE_MODE defaults to True yet Slack/email notifications still send.)
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosmtplib")  # manager → email_service imports it; runs in CI

from app.models.postgres import NotificationChannel, NotificationEventType  # noqa: E402
from app.services.notification import manager as mgr  # noqa: E402


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return SimpleNamespace(all=lambda: self._rows)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


def _pref(threshold: float):
    return SimpleNamespace(
        enabled=True, project_id=uuid.uuid4(), user_id=uuid.uuid4(),
        channel=NotificationChannel.EMAIL,
        events=[NotificationEventType.HIGH_FAILURE_RATE.value,
                NotificationEventType.RUN_FAILED.value],
        failure_rate_threshold=threshold,
    )


async def _dispatched_event(pass_rate: float, threshold: float):
    rows = [(_pref(threshold), "u@x.com")]
    sent = AsyncMock(return_value=("sent", None))
    with patch.object(mgr, "AsyncSessionLocal", lambda: _FakeDB(rows)), \
         patch.object(mgr, "_dispatch_to_channel", sent):
        await mgr.dispatch_run_notifications(
            project_id=uuid.uuid4(), run_id=uuid.uuid4(), build_number="42",
            pass_rate=pass_rate, total_tests=100, failed_tests=10,
            project_name="acme",
        )
    if not sent.await_args_list:
        return None
    # _dispatch_to_channel(pref, user_email, title, body, event, metadata)
    return sent.await_args_list[0].args[4]


@pytest.mark.asyncio
async def test_high_failure_rate_fires_when_pass_rate_below_threshold():
    # pass_rate 70 < threshold 80 → HIGH_FAILURE_RATE (outranks RUN_FAILED).
    assert await _dispatched_event(70.0, 80.0) == NotificationEventType.HIGH_FAILURE_RATE


@pytest.mark.asyncio
async def test_high_failure_rate_suppressed_when_pass_rate_at_or_above_threshold():
    # pass_rate 90 >= threshold 80 → HIGH_FAILURE_RATE removed → RUN_FAILED only.
    assert await _dispatched_event(90.0, 80.0) == NotificationEventType.RUN_FAILED
