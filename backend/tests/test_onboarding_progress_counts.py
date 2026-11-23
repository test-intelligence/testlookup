"""D-127: a skipped Getting started step is not progress.

A brand-new project read "60% · 3 of 5" because skipped steps counted as done.
``completed_count`` now counts done steps only, ``skipped_count`` the skipped
ones, and ``progress_pct`` is the share of the steps not skipped that are done.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import onboarding_service as svc

PID = uuid.uuid4()


def _steps(**statuses: str) -> list[dict]:
    return [{**s, "status": statuses.get(s["key"], "pending"), "completed_at": None} for s in svc.ONBOARDING_STEPS]


def test_two_skipped_steps_are_not_counted_as_done():
    status = svc._summarise(PID, _steps(create_project="completed", connect_jira="skipped",
                                       connect_telemetry="skipped"))
    assert status["completed_count"] == 1
    assert status["skipped_count"] == 2
    assert status["total_count"] == 5
    # One of the three steps still open is done.
    assert status["progress_pct"] == 33
    assert status["is_complete"] is False


def test_every_step_done_or_skipped_is_complete():
    status = svc._summarise(PID, _steps(create_project="completed", upload_run="completed",
                                       connect_jira="skipped", connect_telemetry="skipped",
                                       view_intelligence="completed"))
    assert status["completed_count"] == 3
    assert status["progress_pct"] == 100
    assert status["is_complete"] is True


def test_a_new_project_with_nothing_done_reads_zero():
    status = svc._build_default_status(PID)
    assert (status["completed_count"], status["skipped_count"], status["progress_pct"]) == (0, 0, 0)


@pytest.mark.asyncio
async def test_get_onboarding_status_reads_skips_apart():
    rows = [
        SimpleNamespace(step_key="create_project", status="completed", completed_at=None),
        SimpleNamespace(step_key="connect_jira", status="skipped", completed_at=None),
        SimpleNamespace(step_key="connect_telemetry", status="skipped", completed_at=None),
    ]
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: rows))))
    with patch.object(svc, "_table_exists", AsyncMock(return_value=True)):
        status = await svc.get_onboarding_status(PID, db)
    assert status["completed_count"] == 1
    assert status["skipped_count"] == 2
    assert status["progress_pct"] == 33


def test_the_upload_step_names_the_upload_form():
    upload = next(s for s in svc.ONBOARDING_STEPS if s["key"] == "upload_run")
    assert upload["description"] == "Upload a report, or send results from CI"
    assert "SDK" not in upload["description"]
