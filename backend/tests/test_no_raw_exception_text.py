"""S-D-5: an exception's text never reaches the client.

Broker, SMTP, webhook and Jira errors carry hosts, ports, errno values and
account names. These routes used to return ``str(exc)`` as the 4xx/5xx
detail; they now answer with a plain sentence and keep the detail in the log.
Each test raises an error whose text is recognisable and asserts the response
does NOT contain it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

BROKER_ERROR = "Error 111 connecting to redis:6379. Connection refused."


@pytest.mark.asyncio
async def test_a_deep_investigation_queue_failure_answers_503_without_the_broker_text(monkeypatch):
    from app.routers import deep_investigation as route
    from app.services import pipeline_admission

    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="b-1")
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    monkeypatch.setattr(route, "require_deep_investigation", AsyncMock())
    monkeypatch.setattr(pipeline_admission, "runs_with_in_progress_pipeline", AsyncMock(return_value=set()))
    monkeypatch.setattr(
        route.run_agent_pipeline, "apply_async", MagicMock(side_effect=ConnectionError(BROKER_ERROR)),
    )

    with pytest.raises(HTTPException) as refused:
        await route.trigger_deep_investigation(
            run.id, route.TriggerDeepRequest(), current_user=SimpleNamespace(id=uuid.uuid4()), _=None, db=db,
        )

    assert refused.value.status_code == 503
    detail = refused.value.detail
    assert "redis" not in detail and "111" not in detail and "6379" not in detail
    assert "analysis queue is unavailable" in detail


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        ("No Slack webhook URL configured", "Test notification failed: No Slack webhook URL configured."),
        ("SMTP is not configured — no email was sent", "Test notification failed: SMTP is not configured — no email was sent."),
    ],
)
def test_a_test_notification_keeps_the_dispatchers_own_sentences(error, shown):
    from app.routers.notifications import notification_test_failure_sentence

    assert notification_test_failure_sentence(error) == shown


@pytest.mark.parametrize(
    "raw",
    [
        "(535, b'5.7.8 Username and Password not accepted. smtp.internal.corp')",
        "[Errno -3] Temporary failure in name resolution",
        "Client error '404 Not Found' for url 'https://hooks.slack.com/services/T000/B000/XXXX'",
    ],
)
def test_a_test_notification_hides_an_exceptions_text(raw):
    from app.routers.notifications import notification_test_failure_sentence

    shown = notification_test_failure_sentence(raw)
    assert raw not in shown
    for fragment in ("535", "smtp.internal", "Errno", "hooks.slack.com", "404"):
        assert fragment not in shown
    assert shown.startswith("Test notification failed:")


def test_an_offline_refusal_reads_plainly_without_the_setting_name():
    from app.routers.notifications import notification_test_failure_sentence

    shown = notification_test_failure_sentence(
        "AI_OFFLINE_MODE=true but the slack destination 'hooks.slack.com' is not a loopback address"
    )
    assert "AI_OFFLINE_MODE" not in shown and "hooks.slack.com" not in shown
    assert "offline mode is on" in shown


@pytest.mark.asyncio
async def test_the_test_notification_route_does_not_echo_the_smtp_reply(monkeypatch):
    from app.routers import notifications as route

    raw = "(535, b'5.7.8 Username and Password not accepted. smtp.internal.corp')"
    monkeypatch.setattr(route, "resolve_notification_overrides", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(route, "send_test_notification", AsyncMock(return_value=("failed", raw)))

    with pytest.raises(HTTPException) as refused:
        await route.send_test(
            SimpleNamespace(channel="email", preference_id=None),
            current_user=SimpleNamespace(id=uuid.uuid4()), db=MagicMock(),
        )

    assert refused.value.status_code == 502
    assert "smtp.internal" not in refused.value.detail and "535" not in refused.value.detail
