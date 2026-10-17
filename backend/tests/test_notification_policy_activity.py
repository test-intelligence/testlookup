"""W09 NTF-02 / NTF-01: the transition-policy PUT records activity; the history carries ``link``.

Through the real router and its real guards (``require_role(QA_LEAD,
allow_project_key=True)`` + ``require_project_access()``), with the session
and the policy store faked:

1. a QA engineer's PUT is refused 403; a QA lead who is not a member of the
   path project is refused 403 and nothing is written;
2. per-run false -> true on an existing row stages exactly one
   ``notification_policy.updated`` (``changed == "every run"``, before / after
   of that field only), before the one commit;
3. a first PUT of the defaults creates the row, records the defaults sentence,
   and the GET afterwards answers ``is_default: false``;
4. the same values PUT twice record no second row;
5. a project-bound API key of the path project is answered 200 and the row's
   actor is the key; another project's key is refused 403;
6. ``GET /notifications/history``: ``link`` is the allow-listed path, or None
   (whole-value equality, never a substring).

The same over real PostgreSQL, through the app (one transaction for the policy
row and the ledger row): ``tests/integration/test_notification_policy_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import deps
from app.core.deps import get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import NotificationLog, User, UserRole
from app.models.schemas import TRANSITION_EVENT_VALUES
from app.routers import notifications as route_module
from app.services import notification_transitions

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
URL = f"/api/v1/notifications/projects/{PROJECT}/transition-policy"
DEFAULT_BODY = {
    "transitions_enabled": True,
    "per_run_events_enabled": False,
    "enabled_events": list(TRANSITION_EVENT_VALUES),
    "consecutive_failure_threshold": 2,
}


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """The membership read of ``require_project_access``; counts commits."""

    def __init__(self, state):
        self.state = state

    async def execute(self, stmt, *args, **kwargs):
        return _Result(uuid.uuid4() if self.state.member else None)

    async def commit(self):
        self.state.journal.append("commit")

    async def refresh(self, obj):
        pass

    async def flush(self):
        pass

    def add(self, obj):
        pass


def _user(role: UserRole, *, key_project: uuid.UUID | None = None) -> User:
    user = User(id=uuid.uuid4(), username="lee", email="lee@example.test", full_name="Lee Park",
                role=role, is_active=True)
    if key_project is not None:
        deps._bind_credential_kind(user, deps.CREDENTIAL_KIND_API_KEY)
        deps._bind_api_key_project(user, key_project)
    return user


@pytest.fixture
def api(monkeypatch):
    state = SimpleNamespace(row=None, member=True, journal=[], ledger=[], user=_user(UserRole.QA_LEAD))

    async def get_policy_row(db, project_id):
        return state.row

    async def upsert_policy(db, project_id, **values):
        if state.row is None:
            state.row = SimpleNamespace(project_id=project_id)
            state.journal.append("insert")
        for name, value in values.items():
            setattr(state.row, name, list(value) if name == "enabled_events" else value)
        state.journal.append("upsert")
        return state.row

    async def record(db, **kwargs):
        state.journal.append("activity")
        state.ledger.append(kwargs)

    async def get_effective_policy(db, project_id):
        # Only asked when there is no row: the new-project defaults.
        assert state.row is None
        return notification_transitions.EffectivePolicy()

    monkeypatch.setattr(notification_transitions, "get_policy_row", get_policy_row)
    monkeypatch.setattr(notification_transitions, "get_effective_policy", get_effective_policy)
    monkeypatch.setattr(notification_transitions, "upsert_policy", upsert_policy)
    monkeypatch.setattr(route_module, "record_activity", record)

    app = FastAPI()
    app.include_router(route_module.router)

    async def _db():
        yield _Session(state)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    state.client = TestClient(app, raise_server_exceptions=False)
    return state


def _row(**values):
    row = SimpleNamespace(project_id=PROJECT, **DEFAULT_BODY)
    for name, value in values.items():
        setattr(row, name, value)
    row.enabled_events = list(row.enabled_events)
    return row


# ── 1. who may PUT ───────────────────────────────────────────────────────────


def test_a_qa_engineer_is_refused(api):
    api.user = _user(UserRole.QA_ENGINEER)
    response = api.client.put(URL, json=DEFAULT_BODY)
    assert response.status_code == 403, response.text
    assert api.journal == [] and api.ledger == []


def test_a_qa_lead_outside_the_project_is_refused_and_nothing_is_written(api):
    api.member = False
    response = api.client.put(URL, json={**DEFAULT_BODY, "per_run_events_enabled": True})
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "You do not have access to this project"
    assert api.journal == [] and api.row is None and api.ledger == []


# ── 2. a change records one row ─────────────────────────────────────────────


def test_per_run_on_records_every_run_before_the_one_commit(api):
    api.row = _row()
    response = api.client.put(URL, json={**DEFAULT_BODY, "per_run_events_enabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["per_run_events_enabled"] is True and response.json()["is_default"] is False
    assert api.journal == ["upsert", "activity", "commit"]
    (row,) = api.ledger
    assert row["event_type"] == "notification_policy.updated"
    assert row["project_id"] == PROJECT and row["entity_id"] == PROJECT
    assert row["entity_label"] == "Notification policy"
    assert row["context"] == {"changed": "every run"}
    assert row["before"] == {"per_run_events_enabled": False}
    assert row["after"] == {"per_run_events_enabled": True}
    assert row["changed_fields"] == ["per_run_events_enabled"]
    assert row["actor"].actor_type == "user"


def test_several_changes_are_named_in_one_row(api):
    api.row = _row()
    body = {
        "transitions_enabled": False,
        "per_run_events_enabled": True,
        "enabled_events": ["test.recovered", "test.newly_failing"],
        "consecutive_failure_threshold": 3,
    }
    assert api.client.put(URL, json=body).status_code == 200
    (row,) = api.ledger
    assert row["context"] == {"changed": "every run, state changes, state-change events, failure threshold"}
    assert row["before"]["enabled_events"] == list(TRANSITION_EVENT_VALUES)
    # The schema re-orders to the canonical order; the diff compares in that order.
    assert row["after"]["enabled_events"] == ["test.newly_failing", "test.recovered"]


# ── 3. the first save of the defaults ───────────────────────────────────────


def test_a_first_save_of_the_defaults_records_the_sentence_and_the_get_is_no_longer_default(api):
    before = api.client.get(URL).json()
    assert before["is_default"] is True
    response = api.client.put(URL, json=DEFAULT_BODY)
    assert response.status_code == 200, response.text
    assert api.journal == ["insert", "upsert", "activity", "commit"]
    (row,) = api.ledger
    assert row["context"] == {"changed": "saved the defaults as this project's policy"}
    assert row["changed_fields"] == [] and row["before"] == {} and row["after"] == {}
    assert api.client.get(URL).json()["is_default"] is False


def test_a_first_save_that_changes_a_field_names_it(api):
    assert api.client.put(URL, json={**DEFAULT_BODY, "per_run_events_enabled": True}).status_code == 200
    (row,) = api.ledger
    assert row["context"] == {"changed": "every run"}
    # ``before`` is the effective defaults when there was no row.
    assert row["before"] == {"per_run_events_enabled": False}


# ── 4. nothing changed, nothing recorded ────────────────────────────────────


def test_the_same_values_twice_record_one_row(api):
    body = {**DEFAULT_BODY, "consecutive_failure_threshold": 4}
    assert api.client.put(URL, json=body).status_code == 200
    assert api.client.put(URL, json=body).status_code == 200
    assert len(api.ledger) == 1
    assert api.journal.count("commit") == 2


def test_an_unchanged_put_on_an_existing_row_records_nothing(api):
    api.row = _row(enabled_events=list(reversed(TRANSITION_EVENT_VALUES)))  # stored order differs
    assert api.client.put(URL, json=DEFAULT_BODY).status_code == 200
    assert api.ledger == []


# ── 5. project-bound API keys ───────────────────────────────────────────────


def test_a_key_of_the_path_project_is_answered_and_recorded_as_the_key(api):
    api.member = False  # the key's owner is an ADMIN; membership is not read for one
    api.user = _user(UserRole.ADMIN, key_project=PROJECT)
    api.row = _row()
    response = api.client.put(URL, json={**DEFAULT_BODY, "per_run_events_enabled": True})
    assert response.status_code == 200, response.text
    (row,) = api.ledger
    assert row["actor"].actor_type == "api_key"


def test_another_projects_key_is_refused(api):
    api.user = _user(UserRole.ADMIN, key_project=OTHER)
    response = api.client.put(URL, json={**DEFAULT_BODY, "per_run_events_enabled": True})
    assert response.status_code == 403, response.text
    assert api.journal == [] and api.ledger == []


# ── 6. the history link ─────────────────────────────────────────────────────

GOOD = "12345678-1234-4234-8234-123456789abc"


@pytest.fixture
def history(monkeypatch):
    rows: list = []

    async def list_history(db, user, *, unread_only=False, limit=50):
        return rows

    monkeypatch.setattr(route_module, "list_notification_history", list_history)
    app = FastAPI()
    app.include_router(route_module.router)

    async def _db():
        yield _Session(SimpleNamespace(member=True, journal=[]))

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: _user(UserRole.VIEWER)
    return rows, TestClient(app, raise_server_exceptions=False)


def _log(metadata):
    return NotificationLog(
        id=uuid.uuid4(), channel="email", event_type="quality_gate_failed", title="Quality gate failed: 2026.10",
        body="The gate recorded NO_GO.", status="pending", is_read=False, sent_at=None,
        created_at=datetime(2026, 10, 16, tzinfo=timezone.utc), delivery_metadata=metadata,
    )


@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"app_path": f"/releases/{GOOD}"}, f"/releases/{GOOD}"),
        ({"app_path": f"/release-gate/{GOOD}"}, f"/release-gate/{GOOD}"),
        ({"app_path": "/releases/x"}, None),
        ({"app_path": f"https://example.com/releases/{GOOD}"}, None),
        ({"app_path": "//example.com"}, None),
        ({"app_path": "/settings"}, None),
        ({"app_path": 42}, None),
        (None, None),
    ],
)
def test_the_history_link_is_the_allow_listed_path_or_none(history, metadata, expected):
    rows, client = history
    rows.append(_log(metadata))
    response = client.get("/api/v1/notifications/history")
    assert response.status_code == 200, response.text
    (item,) = response.json()
    assert item["link"] == expected
    assert item["title"] == "Quality gate failed: 2026.10"
