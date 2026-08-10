"""Email delivery must not report "sent" when SMTP is not configured.

Measured on the live deployment, which has an empty ``SMTP_HOST``::

    POST /api/v1/notifications/test  {"channel": "email"}
    -> 200 {"status": "sent", "channel": "email"}

Nothing could have been delivered. The entire purpose of a "send test
notification" button is to answer *is my channel configured?* — it answered
yes when the answer was no.

The mechanism, and the asymmetry, sit inside one function. ``_dispatch_to_channel``
guards the Slack branch honestly::

    if not webhook_url:
        return "failed", "No Slack webhook URL configured"

The email branch checks only that a recipient address exists.
``email_service.send_notification`` then returns **early and silently** when
``cfg["enabled"]`` is false — a bare ``return`` behind a ``logger.debug`` — so
no exception reaches the caller and the "no exception means it worked" path
reports success.

This is not only a misleading button. ``_dispatch_to_channel``'s return value
is what gets written to ``NotificationLog``, so every suppressed email was
recorded as **delivered**: the notification history asserted deliveries that
never left the box.

The fix mirrors Slack — an unconfigured channel is a ``failed`` with a reason
you can act on, in both the test path and normal dispatch. Recording "failed —
SMTP is not configured" is noisier than silence, and correct; silence here was
indistinguishable from success.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import NotificationChannel, NotificationEventType  # noqa: E402
from app.services.notification import manager  # noqa: E402

pytestmark = pytest.mark.regression


def _pref(channel, **kw):
    from app.models.postgres import NotificationPreference as NP

    return NP(
        user_id=uuid.uuid4(),
        channel=channel,
        enabled=True,
        email_override=kw.get("email_override"),
        slack_webhook_url=kw.get("slack_webhook_url"),
        teams_webhook_url=kw.get("teams_webhook_url"),
    )


async def _dispatch(pref, smtp_cfg, user_email="qa@example.com"):
    return await manager._dispatch_to_channel(
        pref,
        user_email,
        "🔔 test",
        "body",
        NotificationEventType.RUN_PASSED,
        {},
        smtp_cfg=smtp_cfg,
    )


class TestEmailWithoutSmtp:
    @pytest.mark.asyncio
    async def test_unconfigured_smtp_is_a_failure_not_a_send(self):
        """The measured bug: 'sent' with an empty SMTP_HOST."""
        status, error = await _dispatch(
            _pref(NotificationChannel.EMAIL), smtp_cfg={"enabled": False}
        )
        assert status == "failed", (
            "email reported 'sent' with SMTP disabled — nothing was delivered, "
            "and this status is what NotificationLog records as the outcome"
        )
        assert error and "smtp" in error.lower(), (
            f"the reason must name SMTP so it is actionable, got {error!r}"
        )

    @pytest.mark.asyncio
    async def test_a_configured_smtp_still_sends(self):
        """The guard must not break the working path."""
        with patch.object(
            manager.email_service, "send_notification", AsyncMock()
        ) as sender:
            status, error = await _dispatch(
                _pref(NotificationChannel.EMAIL),
                smtp_cfg={"enabled": True, "host": "smtp.example.com"},
            )
        assert status == "sent", error
        sender.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_missing_recipient_is_still_reported(self):
        """The pre-existing check must survive."""
        status, error = await _dispatch(
            _pref(NotificationChannel.EMAIL),
            smtp_cfg={"enabled": True},
            user_email=None,
        )
        assert status == "failed"
        assert "email address" in (error or "").lower()


class TestTheOtherChannelsWereAlreadyHonest:
    """Pinned so the fix is understood as making email match, not as new policy."""

    @pytest.mark.asyncio
    async def test_slack_without_a_webhook_reports_failed(self):
        with patch.object(manager.settings, "SLACK_WEBHOOK_URL", None):
            status, error = await _dispatch(
                _pref(NotificationChannel.SLACK), smtp_cfg={"enabled": True}
            )
        assert status == "failed"
        assert "webhook" in (error or "").lower()
