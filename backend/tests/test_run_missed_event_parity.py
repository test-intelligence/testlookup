"""W23a TRND-03: ``run.missed`` is named the same everywhere it is delivered or offered.

The value lives in five places that change together (contract §1.1): the ORM
enum ``NotificationEventType.RUN_MISSED`` (the relay converts every stored row
with ``NotificationEventType(row.event_type)``, so a value missing there FAILS
its delivery), the TS union in ``frontend/src/types/notifications.ts``, the
exhaustive ``EVENT_LABELS`` of ``notificationEventsModel.ts`` (which builds the
My notifications checkboxes), and the Slack / Teams emoji and colour maps.

And it is OPT-IN: the preference schema's default list does not name it, so
no preference receives a missed-run alert unless someone ticks it (which is
also what keeps a deployment with live SMTP quiet). The frontend files are read
as TEXT (no Node in the backend suite), like the W22b parity tests.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.postgres import NotificationEventType
from app.models.schemas import NotificationPreferenceCreate, NotificationPreferenceResponse
from app.services.notification import slack_service, teams_service
from app.services.run_cadence_vocab import RUN_MISSED_EVENT

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
TYPES_TS = FRONTEND / "types" / "notifications.ts"
MODEL_TS = FRONTEND / "components" / "notifications" / "notificationEventsModel.ts"
EVENT = "run.missed"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _ts_union() -> list[str]:
    """The literals of ``export type NotificationEventType = | '…' | …``."""
    match = re.search(r"export type NotificationEventType\s*=(.*?)\n\s*\n", _read(TYPES_TS), re.S)
    assert match, "the NotificationEventType union was not found"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return re.findall(r"\|\s*'([^']+)'", body)


def _event_labels() -> dict[str, str]:
    """``EVENT_LABELS``' keys (bare or quoted) and their label strings."""
    match = re.search(
        r"export const EVENT_LABELS: Record<NotificationEventType, string> = \{(.*?)\n\}", _read(MODEL_TS), re.S,
    )
    assert match, "EVENT_LABELS was not found"
    pairs = re.findall(r"^\s*(?:'([^']+)'|([A-Za-z_]+)):\s*'([^']*)',", match.group(1), re.M)
    return {quoted or bare: label for quoted, bare, label in pairs}


def test_the_enum_value_is_the_vocabulary_constant() -> None:
    assert NotificationEventType.RUN_MISSED.value == EVENT == RUN_MISSED_EVENT
    # The relay's conversion of a stored row does not raise.
    assert NotificationEventType(EVENT) is NotificationEventType.RUN_MISSED


def test_the_ts_union_names_it_and_names_only_enum_values() -> None:
    union = _ts_union()
    assert EVENT in union
    assert set(union) <= {e.value for e in NotificationEventType}, set(union) - {e.value for e in NotificationEventType}


def test_my_notifications_offers_it_with_its_label() -> None:
    labels = _event_labels()
    assert labels[EVENT] == "An expected run was missed"
    # The label map is exhaustive over the union (Record<NotificationEventType, string>).
    assert set(labels) == set(_ts_union())


def test_slack_and_teams_map_it() -> None:
    assert slack_service._EVENT_EMOJI[EVENT] == ":hourglass:"
    assert slack_service._EVENT_COLOUR[EVENT] == "#f59e0b"
    assert teams_service._EVENT_COLOUR[EVENT] == "warning"
    assert teams_service._EVENT_EMOJI[EVENT] == "⏳"


def test_it_is_opt_in_not_in_the_preference_default() -> None:
    default = NotificationPreferenceCreate(channel="email").events
    assert EVENT not in default and default    # the default list exists and does not subscribe to it


def test_an_opted_in_preference_keeps_it_through_both_schemas() -> None:
    created = NotificationPreferenceCreate(channel="email", events=[EVENT])
    assert created.events == [EVENT]
    stored = NotificationPreferenceResponse.model_validate({
        "id": uuid.uuid4(), "user_id": uuid.uuid4(), "project_id": None, "channel": "email", "enabled": True,
        "events": created.events, "failure_rate_threshold": 80.0, "email_override": None,
        "slack_webhook_url": None, "teams_webhook_url": None,
        "created_at": datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc),
    })
    assert stored.events == [EVENT]
