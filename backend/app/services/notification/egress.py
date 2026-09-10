"""Where a notification is allowed to be delivered under AI_OFFLINE_MODE.

Re-audit finding H10. ``THREAT_MODEL.md`` states that an air-gapped deployment
"will see zero application-level egress" when ``AI_OFFLINE_MODE=true``, and
lists the outbound paths that honour it — LLM inference, GitHub Checks,
outbound webhooks. Slack, Teams and SMTP appear nowhere in that table and
checked nothing: every one of them would happily post a notification containing
failure text, test names and build metadata to a public endpoint on a
deployment whose entire premise is that it does not talk to the internet.

The gate is **residency, not product name**. The same reasoning as re-audit C3:
a destination is remote because of where it resolves, not because of what it is
called. That distinction matters in both directions here —

* a self-hosted, Slack-compatible webhook on the LAN is a legitimate offline
  destination, and blocking it by channel name would break an air-gapped
  deployment's own alerting;
* an SMTP relay at ``smtp.gmail.com`` is egress no matter that "email" sounds
  internal, and an operator who set it has left the box.

So each sender asks whether *this* destination is on-box, and refuses only when
it is not.

Refusing raises rather than returning quietly. ``manager.py`` renders the
exception into the delivery's stored status, and that file already draws the
same line for a channel that is not configured: "An unconfigured channel is a
failure, not a send." A notification that was silently dropped but recorded as
delivered is worse than one recorded as blocked, because only the second tells
an operator to go and look.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from app.core.config import settings
from app.services.llm_policy_service import host_is_local

logger = logging.getLogger("services.notification.egress")


class OfflineEgressBlocked(RuntimeError):
    """Raised when offline mode forbids delivering to this destination."""


def _host_of(destination: str) -> str | None:
    """Hostname from a webhook URL or a bare SMTP host."""
    text = (destination or "").strip()
    if not text:
        return None
    if "://" in text:
        return urlparse(text).hostname
    # A bare host, optionally with a port ("smtp.example.com:587").
    return text.rsplit(":", 1)[0] if text.count(":") == 1 else text


def assert_delivery_allowed(channel: str, destination: str | None) -> None:
    """Refuse an off-box notification while ``AI_OFFLINE_MODE`` is set.

    Fails CLOSED: a destination that cannot be resolved, or that resolves to
    even one routable address, is treated as remote. Under an offline ceiling
    "we could not prove this stays on-box" must deny.
    """
    if not settings.AI_OFFLINE_MODE:
        return

    host = _host_of(destination or "")
    if not host:
        # Nothing to check means nothing to trust.
        raise OfflineEgressBlocked(
            f"AI_OFFLINE_MODE=true and the {channel} destination is unset — "
            "refusing to deliver"
        )

    if host_is_local(host):
        return

    logger.warning(
        "offline_egress_blocked channel=%s host=%s", channel, host
    )
    raise OfflineEgressBlocked(
        f"AI_OFFLINE_MODE=true but the {channel} destination '{host}' is not a "
        "loopback or private address — refusing to send notification content "
        "off-box"
    )
