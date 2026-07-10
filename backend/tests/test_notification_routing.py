"""Ownership-routed transition notifications (PMF US-7.3).

Covers the routing resolution matrix:

* owned test → the team's channel bucket
* team resolved but no channel row → default, ``no_team_channel``
* no team at all → default, ``unowned``
* resolver crash → default, ``routing_error`` (fail open, never dropped)
* cluster with a majority owner → the majority team's bucket
* mixed-ownership cluster → default, ``mixed_ownership``
* team-channel delivery failure → events merged back into default
  (``delivery_failed``) — the fail-open contract
* the "unowned" coverage-nudge note rendering
* ``send_to_team_channel`` — channel fan-out + never-raises contract
* ``record_team_delivery_logs`` — never raises on audit-store outage
"""
from __future__ import annotations

import sys
import types
import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

# aiosmtplib is optional locally; notification_routing lazily imports the
# email service. Stub before import (same pattern as the sibling tests).
if "aiosmtplib" not in sys.modules:
    _stub = types.ModuleType("aiosmtplib")
    _stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub

from app.services import notification_routing as routing  # noqa: E402
from app.services.notification_transitions import (  # noqa: E402
    ClusterGroup,
    TransitionEvent,
    render_transition_message,
)


def _event(fp: str, name: str, suite: str | None = None, event: str = "test.newly_failing"):
    return TransitionEvent(
        event=event, fingerprint=fp, test_name=name, suite_name=suite,
        test_case_id=uuid.uuid4(), consecutive_failures=2,
    )


def _channel(team: str, channel_type: str = "slack", target: str = "https://hooks.example/x"):
    return routing.TeamChannelInfo(
        team_name=team, channel_type=channel_type, target=target,
    )


def _suite_team_resolver(mapping: dict[str, str]):
    """team_of resolver: suite_name → team via a plain dict."""
    def _resolve(ev):
        return mapping.get(ev.suite_name or "")
    return _resolve


# ── Routing matrix: single events ───────────────────────────────────────────


def test_owned_event_routes_to_team_channel():
    channels = {"Identity": _channel("Identity")}
    team_of = _suite_team_resolver({"auth": "Identity"})
    teams, default = routing.partition_transitions(
        [], [_event("f1", "t1", "auth")], [], [], team_of, channels,
    )
    assert len(teams) == 1
    assert teams[0].channel is not None
    assert teams[0].channel.team_name == "Identity"
    assert [e.test_name for e in teams[0].singles] == ["t1"]
    assert default.is_empty
    assert default.fallback_counts == {}


def test_unowned_event_falls_back_to_default_with_note_count():
    channels = {"Identity": _channel("Identity")}
    team_of = _suite_team_resolver({})  # nothing resolves
    teams, default = routing.partition_transitions(
        [], [_event("f1", "t1", "misc")], [], [], team_of, channels,
    )
    assert teams == []
    assert [e.test_name for e in default.singles] == ["t1"]
    assert default.fallback_counts == {routing.FALLBACK_UNOWNED: 1}


def test_team_without_channel_falls_back_as_no_team_channel():
    team_of = _suite_team_resolver({"auth": "Identity"})
    teams, default = routing.partition_transitions(
        [], [_event("f1", "t1", "auth")], [], [], team_of, {},
    )
    assert teams == []
    assert default.fallback_counts == {routing.FALLBACK_NO_TEAM_CHANNEL: 1}


def test_resolver_crash_fails_open_to_default():
    def _boom(ev):
        raise RuntimeError("rules table gone")
    channels = {"Identity": _channel("Identity")}
    teams, default = routing.partition_transitions(
        [], [_event("f1", "t1", "auth")], [_event("f2", "t2", "auth", "test.recovered")],
        [], _boom, channels,
    )
    assert teams == []
    assert len(default.singles) == 1 and len(default.recovered) == 1
    assert default.fallback_counts == {routing.FALLBACK_ROUTING_ERROR: 2}


def test_recovered_and_flaky_events_route_by_ownership_too():
    channels = {"Identity": _channel("Identity")}
    team_of = _suite_team_resolver({"auth": "Identity"})
    teams, default = routing.partition_transitions(
        [], [],
        [_event("f1", "t1", "auth", "test.recovered")],
        [_event("f2", "t2", "auth", "test.newly_flaky")],
        team_of, channels,
    )
    assert len(teams) == 1
    assert len(teams[0].recovered) == 1
    assert len(teams[0].newly_flaky) == 1
    assert teams[0].event_values() == {"test.recovered", "test.newly_flaky"}


# ── Routing matrix: cluster groups ──────────────────────────────────────────


def _group(label: str, members: list[TransitionEvent]) -> ClusterGroup:
    group = ClusterGroup(cluster_id=f"cl_{label}", label=label)
    group.tests = members
    return group


def test_cluster_with_majority_owner_routes_whole_group():
    channels = {"Identity": _channel("Identity")}
    team_of = _suite_team_resolver({"auth": "Identity", "billing": "Payments"})
    group = _group("DB timeout", [
        _event("f1", "t1", "auth"),
        _event("f2", "t2", "auth"),
        _event("f3", "t3", "billing"),
    ])
    teams, default = routing.partition_transitions(
        [group], [], [], [], team_of, channels,
    )
    assert len(teams) == 1
    assert teams[0].channel.team_name == "Identity"
    assert teams[0].groups == [group]
    assert default.is_empty


def test_mixed_ownership_cluster_falls_back_to_default():
    channels = {"Identity": _channel("Identity"), "Payments": _channel("Payments")}
    team_of = _suite_team_resolver({"auth": "Identity", "billing": "Payments"})
    group = _group("DB timeout", [
        _event("f1", "t1", "auth"),
        _event("f2", "t2", "billing"),
    ])
    teams, default = routing.partition_transitions(
        [group], [], [], [], team_of, channels,
    )
    assert teams == []
    assert default.groups == [group]
    assert default.fallback_counts == {routing.FALLBACK_MIXED_OWNERSHIP: 2}


def test_unowned_cluster_falls_back_as_unowned():
    team_of = _suite_team_resolver({})
    group = _group("x", [_event("f1", "t1", "a"), _event("f2", "t2", "b")])
    teams, default = routing.partition_transitions(
        [group], [], [], [], team_of, {"Identity": _channel("Identity")},
    )
    assert teams == []
    assert default.fallback_counts == {routing.FALLBACK_UNOWNED: 2}


def test_cluster_majority_owner_without_channel_falls_back():
    team_of = _suite_team_resolver({"auth": "Identity"})
    group = _group("x", [_event("f1", "t1", "auth"), _event("f2", "t2", "auth")])
    teams, default = routing.partition_transitions(
        [group], [], [], [], team_of, {},
    )
    assert teams == []
    assert default.fallback_counts == {routing.FALLBACK_NO_TEAM_CHANNEL: 2}


# ── Fail-open merge on delivery failure ─────────────────────────────────────


def test_failed_delivery_merges_batch_back_into_default():
    channels = {"Identity": _channel("Identity")}
    team_of = _suite_team_resolver({"auth": "Identity"})
    teams, default = routing.partition_transitions(
        [], [_event("f1", "t1", "auth")], [], [], team_of, channels,
    )
    assert default.is_empty
    default.merge(teams[0], routing.FALLBACK_DELIVERY_FAILED)
    assert [e.test_name for e in default.singles] == ["t1"]
    assert default.fallback_counts == {routing.FALLBACK_DELIVERY_FAILED: 1}


# ── Note rendering ──────────────────────────────────────────────────────────


def test_unowned_note_lists_reasons_and_total():
    note = routing.render_unowned_note({
        routing.FALLBACK_UNOWNED: 2,
        routing.FALLBACK_MIXED_OWNERSHIP: 3,
    })
    assert note is not None
    assert "5 transitions" in note
    assert "2 unowned" in note
    assert "3 mixed ownership" in note
    assert "Ownership" in note  # nudge toward the ownership editor


def test_unowned_note_absent_when_nothing_fell_back():
    assert routing.render_unowned_note({}) is None


def test_team_tagged_title():
    title, _ = render_transition_message(
        project_name="acme", build_number="7",
        groups=[], single_failing=[_event("f1", "t1", "auth")],
        recovered=[], newly_flaky=[], dashboard_url="#",
        team_name="Identity",
    )
    assert title.endswith("· Identity")


# ── Delivery fan-out (send_to_team_channel) ─────────────────────────────────


@pytest.mark.asyncio
async def test_send_to_team_channel_slack():
    sent = AsyncMock()
    from app.services.notification import slack_service
    with patch.object(slack_service, "send_notification", sent):
        status, err = await routing.send_to_team_channel(
            _channel("Identity", "slack", "https://hooks/x"),
            "title", "body", "test.newly_failing", {"k": "v"},
        )
    assert (status, err) == ("sent", None)
    assert sent.await_args.kwargs["webhook_url"] == "https://hooks/x"


@pytest.mark.asyncio
async def test_send_to_team_channel_email():
    sent = AsyncMock()
    from app.services.notification import email_service
    with patch.object(email_service, "send_notification", sent):
        status, err = await routing.send_to_team_channel(
            _channel("Identity", "email", "team@x.com"),
            "title", "body", "test.newly_failing", {},
        )
    assert (status, err) == ("sent", None)
    assert sent.await_args.kwargs["to"] == "team@x.com"


@pytest.mark.asyncio
async def test_send_to_team_channel_unsupported_type_fails_cleanly():
    status, err = await routing.send_to_team_channel(
        _channel("Identity", "carrier_pigeon", "coop-3"),
        "title", "body", "test.newly_failing", {},
    )
    assert status == "failed"
    assert "carrier_pigeon" in (err or "")


@pytest.mark.asyncio
async def test_send_to_team_channel_never_raises():
    boom = AsyncMock(side_effect=RuntimeError("webhook down"))
    from app.services.notification import teams_service
    with patch.object(teams_service, "send_notification", boom):
        status, err = await routing.send_to_team_channel(
            _channel("Identity", "teams"),
            "title", "body", "test.newly_failing", {},
        )
    assert status == "failed"
    assert "webhook down" in (err or "")


# ── Audit log write never breaks dispatch ───────────────────────────────────


@pytest.mark.asyncio
async def test_record_team_delivery_logs_never_raises(monkeypatch):
    import app.db.postgres as _pg

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(_pg, "get_session_factory", _boom)
    await routing.record_team_delivery_logs(
        uuid.uuid4(), uuid.uuid4(),
        [(_channel("Identity"), "test.newly_failing", "t", "b", "sent", None)],
    )


@pytest.mark.asyncio
async def test_record_team_delivery_logs_noop_on_empty():
    # Must not even open a session for an empty batch.
    await routing.record_team_delivery_logs(uuid.uuid4(), None, [])
