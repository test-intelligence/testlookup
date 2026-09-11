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

One explicit exception (code review of H10): ``OFFLINE_NOTIFICATION_ALLOWED_HOSTS``
names hosts an operator has decided may receive notifications although they
are off-box -- a hosted Slack workspace, say -- so keeping the LLM ceiling does
not mean giving up alerting. Anything not named there still has to be on-box.

The exception covers only the deployment's own destinations: the global Slack
and Teams webhooks and the SMTP relay, which an admin configures (second code
review of H10). Slack and Teams put every workspace on the same few hosts, so
naming ``hooks.slack.com`` admits every Slack workspace, not only the
operator's. A webhook a user or a team lead typed in is therefore judged by
residency alone; otherwise any user could point a personal webhook at a
workspace of their own and receive failure text there.
"""
from __future__ import annotations

import asyncio
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


def assert_delivery_allowed(
    channel: str, destination: str | None, *, deployment_wide: bool = False
) -> None:
    """Refuse an off-box notification while ``AI_OFFLINE_MODE`` is set.

    Fails CLOSED: a destination that cannot be resolved, or that resolves to
    even one routable address, is treated as remote. Under an offline ceiling
    "we could not prove this stays on-box" must deny.

    ``deployment_wide=True`` marks the deployment's own destination (a global
    webhook, the SMTP relay), the only kind ``OFFLINE_NOTIFICATION_ALLOWED_HOSTS``
    can admit. The default is the stricter rule, so a sender that does not say
    is judged by residency alone.
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

    listed = _allow_listed(host)
    if listed and deployment_wide:
        return

    if host_is_local(host):
        return

    logger.warning(
        "offline_egress_blocked channel=%s host=%s deployment_wide=%s",
        channel, host, deployment_wide,
    )
    message = (
        f"AI_OFFLINE_MODE=true but the {channel} destination '{host}' is not a "
        "loopback or private address — refusing to send notification content "
        "off-box"
    )
    if listed:
        # Say why the operator's exception did not apply; a refusal of a host
        # they named would otherwise read as a bug.
        message += (
            f"; OFFLINE_NOTIFICATION_ALLOWED_HOSTS names '{host}', but it covers "
            "only the deployment's own webhooks and mail relay, not a webhook "
            "set per user or per team"
        )
    raise OfflineEgressBlocked(message)


def _allow_listed(host: str) -> bool:
    """Whether an operator named this off-box host as an allowed destination.

    ``OFFLINE_NOTIFICATION_ALLOWED_HOSTS`` is comma-separated. ``hooks.slack.com``
    matches that host only; ``.example.com`` matches any subdomain of
    example.com, on a dot boundary, and not example.com itself. Consulted for a
    deployment-wide destination only (see :func:`assert_delivery_allowed`).
    """
    entries = [
        entry.strip().lower()
        for entry in (settings.OFFLINE_NOTIFICATION_ALLOWED_HOSTS or "").split(",")
        if entry.strip()
    ]
    name = host.strip().lower().rstrip(".")
    for entry in entries:
        if entry.startswith("."):
            if name.endswith(entry):
                return True
        elif name == entry:
            return True
    return False


def delivery_http_client(destination: str | None, *, deployment_wide: bool = False):
    """The HTTP client a webhook send must use once the gate has passed.

    Re-audit N8. Offline, a webhook is admitted because its host resolved
    on-box -- and it was then posted through the PUBLIC-only client, which
    resolved the name again and would connect to whatever public address it
    answered this time (and refused the on-box one the gate approved). Every
    offline destination that was admitted by residency now goes through a
    client that re-validates and pins the on-box address at connect time.

    Only the operator's allow-listed deployment-wide host (e.g. a hosted Slack
    workspace in ``OFFLINE_NOTIFICATION_ALLOWED_HOSTS``) is off-box by design,
    and keeps the public client. Online, the public client as before.
    """
    from app.core.http_client import get_public_http_client

    if settings.AI_OFFLINE_MODE:
        host = _host_of(destination or "")
        if not (host and deployment_wide and _allow_listed(host)):
            from app.services.llm_egress import get_local_only_http_client

            return get_local_only_http_client()
    return get_public_http_client()


async def assert_delivery_allowed_async(
    channel: str, destination: str | None, *, deployment_wide: bool = False
) -> None:
    """:func:`assert_delivery_allowed`, with the DNS lookup off the event loop.

    The residency check resolves the host with a blocking ``getaddrinfo``, and
    a public host's answer is cached for 30 s only, so a burst of sends would
    re-resolve on the loop that also serves requests (code review of H10; the
    same class as N11).
    """
    if not settings.AI_OFFLINE_MODE:
        return
    await asyncio.to_thread(
        assert_delivery_allowed, channel, destination, deployment_wide=deployment_wide
    )


async def _configured_destinations() -> list[tuple[str, str | None]]:
    """The deployment-wide Slack, Teams and SMTP destinations, as sends see them.

    Webhooks come from the resolver the probes use (encrypted settings, then
    legacy inline values, then the environment); SMTP from the configuration
    the email senders use. Per-user webhooks are judged when they are used.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.services.integration_config_service import resolve_global_notification_webhooks
    from app.services.notification.email_service import _get_smtp_cfg, smtp_host

    try:
        async with AsyncSessionLocal() as db:
            hooks = await resolve_global_notification_webhooks(db)
    except Exception:  # noqa: BLE001 -- fall back to what the environment says
        hooks = {
            "slack_enabled": settings.SLACK_ENABLED,
            "slack_webhook_url": settings.SLACK_WEBHOOK_URL,
            "teams_enabled": settings.TEAMS_ENABLED,
            "teams_webhook_url": settings.TEAMS_WEBHOOK_URL,
        }
    destinations: list[tuple[str, str | None]] = []
    if hooks.get("slack_enabled") and hooks.get("slack_webhook_url"):
        destinations.append(("Slack", hooks["slack_webhook_url"]))
    if hooks.get("teams_enabled") and hooks.get("teams_webhook_url"):
        destinations.append(("Teams", hooks["teams_webhook_url"]))
    smtp_cfg = await _get_smtp_cfg()
    if smtp_cfg.get("enabled"):
        destinations.append(("SMTP", smtp_host(smtp_cfg)))
    return destinations


async def offline_destination_warnings() -> list[str]:
    """One message per configured destination offline mode will refuse.

    Called at API startup (code review of H10), so an operator learns that the
    Slack webhook or the mail relay is refused from the startup log, not from
    the first notification that never arrives.
    """
    if not settings.AI_OFFLINE_MODE:
        return []
    warnings: list[str] = []
    for channel, destination in await _configured_destinations():
        try:
            # Every destination listed is the deployment's own.
            await assert_delivery_allowed_async(channel, destination, deployment_wide=True)
        except OfflineEgressBlocked as exc:
            warnings.append(str(exc))
    return warnings
