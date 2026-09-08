"""
Ownership-routed transition notifications — PMF US-7.3.

Routes the per-run transition batch (``notification_transitions``) to the
owning team's channel instead of only the project-wide default channels:

  test → suite → ownership rules → team_name → team_notification_channels

Routing contract (fail-open — an event is NEVER dropped because routing
failed):

* Event resolves to a team WITH an active channel  → team channel.
* Event resolves to a team WITHOUT a channel       → default, "no_team_channel".
* Event resolves to no team at all                 → default, "unowned".
* Ownership resolution raises                      → default, "routing_error".
* Cluster group: majority owner (> half the member tests resolve to one
  team) routes the WHOLE group to that team; mixed-ownership clusters
  (two or more distinct teams, no majority) fall back to default with
  "mixed_ownership".
* Team-channel delivery fails                      → the bucket's events are
  merged back into the default batch, "delivery_failed".

The default batch keeps today's behaviour (``NotificationPreference`` fan-out
via the notification manager) plus an "unowned" note line that nudges
ownership coverage.

Audit: every team-channel delivery is staged as a durable ``NotificationLog``
row with ``routed_team`` set and delivered by the shared leased relay. The
default batch's rows are written by the manager, and fallback reasons are
additionally logged via structlog with stable kwargs
(``transition_routing_decision``).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import TeamNotificationChannel

logger = structlog.get_logger("services.notification_routing")


# Fallback-reason vocabulary (persisted to notification_logs.routing_fallback
# and logged via structlog — keep values stable).
FALLBACK_UNOWNED = "unowned"
FALLBACK_NO_TEAM_CHANNEL = "no_team_channel"
FALLBACK_MIXED_OWNERSHIP = "mixed_ownership"
FALLBACK_ROUTING_ERROR = "routing_error"
FALLBACK_DELIVERY_FAILED = "delivery_failed"


@dataclass(frozen=True)
class TeamChannelInfo:
    """Resolved notification target for one team."""
    team_name: str
    channel_type: str   # NotificationChannel value: email | slack | teams
    target: str         # webhook URL or email address


@dataclass
class TransitionBatch:
    """One routed batch of transition content (per team, or the default).

    ``groups`` / ``singles`` / ``recovered`` / ``newly_flaky`` mirror the
    arguments of ``notification_transitions.render_transition_message`` —
    this module treats them as opaque beyond ``suite_name`` /
    ``test_name`` / ``event`` / ``tests`` attributes to avoid an import
    cycle with the transition engine.
    """
    groups: list = field(default_factory=list)          # ClusterGroup-shaped
    singles: list = field(default_factory=list)         # TransitionEvent-shaped
    recovered: list = field(default_factory=list)
    newly_flaky: list = field(default_factory=list)
    # Default batch only: fallback reason → number of events that fell back
    # for that reason.
    fallback_counts: dict[str, int] = field(default_factory=dict)

    @property
    def event_count(self) -> int:
        return (
            sum(len(g.tests) for g in self.groups)
            + len(self.singles)
            + len(self.recovered)
            + len(self.newly_flaky)
        )

    @property
    def is_empty(self) -> bool:
        return self.event_count == 0

    def event_values(self) -> set[str]:
        """Distinct NotificationEventType values present in this batch."""
        vals = {e.event for e in self.singles}
        vals |= {e.event for e in self.recovered}
        vals |= {e.event for e in self.newly_flaky}
        for group in self.groups:
            vals |= {e.event for e in group.tests}
        return vals

    def merge(self, other: "TransitionBatch", reason: str) -> None:
        """Fold another batch into this one, recording the fallback reason."""
        self.groups.extend(other.groups)
        self.singles.extend(other.singles)
        self.recovered.extend(other.recovered)
        self.newly_flaky.extend(other.newly_flaky)
        if other.event_count:
            self.fallback_counts[reason] = (
                self.fallback_counts.get(reason, 0) + other.event_count
            )


@dataclass
class TeamBatch(TransitionBatch):
    """A batch bound for one team's channel."""
    channel: Optional[TeamChannelInfo] = None


# ── Channel loading ──────────────────────────────────────────────────────────


async def load_team_channels(
    db: AsyncSession, project_id: uuid.UUID,
) -> dict[str, TeamChannelInfo]:
    """Active team → channel mappings for a project. Read-only."""
    rows = (
        await db.execute(
            select(TeamNotificationChannel).where(
                TeamNotificationChannel.project_id == project_id,
                TeamNotificationChannel.is_active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    return {
        row.team_name: TeamChannelInfo(
            team_name=row.team_name,
            channel_type=row.channel_type,
            target=row.target,
        )
        for row in rows
    }


# ── Pure partition logic (unit-tested directly) ──────────────────────────────


def _safe_team_of(
    team_of: Callable[[Any], Optional[str]], event: Any,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve an event's team; a resolver crash is a per-event fail-open
    (reason ``routing_error``), never a dropped event."""
    try:
        team = team_of(event)
    except Exception as exc:  # noqa: BLE001 — fail open per event
        logger.warning(
            "transition_routing_resolver_failed",
            test_name=getattr(event, "test_name", None),
            error=str(exc),
        )
        return None, FALLBACK_ROUTING_ERROR
    return (team or None), None


def _cluster_majority_team(
    group: Any, team_of: Callable[[Any], Optional[str]],
) -> tuple[Optional[str], str]:
    """(majority_team, fallback_reason_if_none) for a cluster group.

    Majority = strictly more than half of ALL member tests resolve to the
    same team. No majority with >= 2 distinct teams = mixed ownership;
    otherwise the cluster is simply unowned.
    """
    votes: dict[str, int] = {}
    saw_error = False
    for ev in group.tests:
        team, err = _safe_team_of(team_of, ev)
        if err is not None:
            saw_error = True
        if team:
            votes[team] = votes.get(team, 0) + 1
    if votes:
        best_team = max(votes, key=lambda t: votes[t])
        if votes[best_team] * 2 > len(group.tests):
            return best_team, ""
        if len(votes) >= 2:
            return None, FALLBACK_MIXED_OWNERSHIP
    if saw_error:
        return None, FALLBACK_ROUTING_ERROR
    return None, FALLBACK_UNOWNED


def partition_transitions(
    groups: Sequence[Any],
    singles: Sequence[Any],
    recovered: Sequence[Any],
    newly_flaky: Sequence[Any],
    team_of: Callable[[Any], Optional[str]],
    channels: dict[str, TeamChannelInfo],
) -> tuple[list[TeamBatch], TransitionBatch]:
    """Split the run's transition content into per-team batches and the
    default batch.

    ``team_of`` maps a TransitionEvent-shaped object to a team name (or
    ``None``); exceptions it raises degrade to the default batch per event.
    ``channels`` holds the ACTIVE team channels only.
    """
    team_batches: dict[str, TeamBatch] = {}
    default = TransitionBatch()

    def _bucket_for(team: str) -> TeamBatch:
        batch = team_batches.get(team)
        if batch is None:
            batch = TeamBatch(channel=channels[team])
            team_batches[team] = batch
        return batch

    def _fall_back(reason: str, count: int = 1) -> None:
        default.fallback_counts[reason] = (
            default.fallback_counts.get(reason, 0) + count
        )

    def _route_event(ev: Any, kind: str) -> None:
        team, err = _safe_team_of(team_of, ev)
        if err is not None:
            target_list = getattr(default, kind)
            target_list.append(ev)
            _fall_back(err)
            return
        if team is None:
            getattr(default, kind).append(ev)
            _fall_back(FALLBACK_UNOWNED)
            return
        if team not in channels:
            getattr(default, kind).append(ev)
            _fall_back(FALLBACK_NO_TEAM_CHANNEL)
            return
        getattr(_bucket_for(team), kind).append(ev)

    for group in groups:
        team, reason = _cluster_majority_team(group, team_of)
        if team is not None and team in channels:
            _bucket_for(team).groups.append(group)
        elif team is not None:  # majority owner exists but has no channel
            default.groups.append(group)
            _fall_back(FALLBACK_NO_TEAM_CHANNEL, len(group.tests))
        else:
            default.groups.append(group)
            _fall_back(reason or FALLBACK_UNOWNED, len(group.tests))

    for ev in singles:
        _route_event(ev, "singles")
    for ev in recovered:
        _route_event(ev, "recovered")
    for ev in newly_flaky:
        _route_event(ev, "newly_flaky")

    return list(team_batches.values()), default


def render_unowned_note(fallback_counts: dict[str, int]) -> Optional[str]:
    """The coverage-nudge line appended to the default batch's message."""
    total = sum(fallback_counts.values())
    if total == 0:
        return None
    reasons = ", ".join(
        f"{count} {reason.replace('_', ' ')}"
        for reason, count in sorted(fallback_counts.items())
    )
    return (
        f"ℹ️ {total} transition{'s' if total != 1 else ''} without a team "
        f"channel ({reasons}) — map teams to channels on the Ownership page "
        f"to route these directly."
    )


# ── Delivery + audit (Celery-task-owned session) ─────────────────────────────


async def send_to_team_channel(
    channel: TeamChannelInfo,
    title: str,
    body: str,
    event_type_value: str,
    metadata: dict,
) -> tuple[str, Optional[str]]:
    """Deliver one rendered batch to a team channel.

    Returns ``(status, error_detail)`` — never raises (a failed delivery is
    merged back into the default batch by the caller).
    """
    try:
        if channel.channel_type == "slack":
            from app.services.notification import slack_service
            await slack_service.send_notification(
                webhook_url=channel.target,
                title=title,
                body=body,
                event_type=event_type_value,
                metadata=metadata,
            )
        elif channel.channel_type == "teams":
            from app.services.notification import teams_service
            await teams_service.send_notification(
                webhook_url=channel.target,
                title=title,
                body=body,
                event_type=event_type_value,
                metadata=metadata,
            )
        elif channel.channel_type == "email":
            from app.services.notification import email_service
            await email_service.send_notification(
                to=channel.target,
                title=title,
                body=body,
                event_type=event_type_value,
                metadata=metadata,
            )
        else:
            return "failed", f"Unsupported channel_type '{channel.channel_type}'"
        return "sent", None
    except Exception as exc:  # noqa: BLE001 — fail open to default channels
        logger.warning(
            "team_channel_delivery_failed",
            team_name=channel.team_name,
            channel_type=channel.channel_type,
            error=str(exc),
        )
        return "failed", str(exc)


async def record_team_delivery_logs(
    project_id: uuid.UUID,
    run_id: Optional[uuid.UUID],
    entries: Sequence[tuple[TeamChannelInfo, str, str, str, str, Optional[str]]],
) -> None:
    """Persist one ``NotificationLog`` row per team-channel delivery.

    ``entries`` items: (channel, event_type_value, title, body, status,
    error_detail). Celery-task-owned commit (see module docstring); never
    raises — an audit outage must not break notification dispatch.
    """
    if not entries:
        return
    try:
        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import NotificationLog

        now_sent = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            for channel, event_value, title, body, status, error_detail in entries:
                db.add(NotificationLog(
                    user_id=None,
                    project_id=project_id,
                    run_id=run_id,
                    channel=channel.channel_type,
                    event_type=event_value,
                    title=title,
                    body=body,
                    status=status,
                    error_detail=error_detail,
                    routed_team=channel.team_name,
                    routing_fallback=None,
                    sent_at=now_sent if status == "sent" else None,
                ))
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — audit must not break dispatch
        logger.warning(
            "team_delivery_log_write_failed",
            project_id=str(project_id),
            error=str(exc),
        )
