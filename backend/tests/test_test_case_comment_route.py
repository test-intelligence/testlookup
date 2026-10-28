"""W21d ACT-02: ``POST /api/v1/test-management/cases/{case_id}/comments`` (``add_comment``).

Over a scripted session (no database), the route as a whole:

* a ``parent_id`` (a BODY id) must name a comment of THE SAME case -- a missing
  and a foreign one get the same 404 sentence, before anything is staged;
* every comment records ONE ``test_case.commented`` ledger row (the literal
  producer of the registry entry) whose ``context.mentions`` are user IDS;
* each resolved ``@mention`` stages ONE in-app ``NotificationLog`` that is
  already ``sent`` (never claimable by the delivery relay);
* a body without ``@`` issues no members query and stages no notification;
* the existing role floor (TESTER) still refuses a VIEWER first;
* one commit; the answer grows ``mentions``.

The real ``add_test_case_comment`` service and the real ``activity.record``
run; only the session is scripted.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.models.postgres import (
    ManagedTestCase,
    NotificationLog,
    ProjectActivityEvent,
)
from app.models.postgres import TestCaseComment as CommentRow
from app.services.activity.events import entity_href

P = uuid.UUID("5a5a5a5a-1111-4111-8111-111111111111")
CASE = uuid.UUID("c0ffee00-2222-4222-8222-222222222222")
OTHER_CASE = uuid.UUID("c0ffee00-9999-4999-8999-999999999999")
BASE = "/api/v1/test-management"
NOT_FOUND = "Parent comment not found on this test case."


class ScriptedSession:
    """Just enough of an AsyncSession for the comment route, recording every call."""

    def __init__(self, *, members=(), comments=()):
        self.members = list(members)                  # (user_id, username, full_name) of eligible members
        self.comments = dict(comments)                # comment id -> its test_case_id
        self.case = SimpleNamespace(id=CASE, project_id=P, title="Login works")
        self.added: list = []
        self.queries: list[str] = []
        self.commits = 0
        self.refreshed: list = []

    async def get(self, model, ident):
        assert model is ManagedTestCase and ident == CASE
        return self.case

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        now = datetime.now(timezone.utc)
        for obj in self.added:
            if isinstance(obj, CommentRow) and obj.id is None:
                obj.id, obj.created_at, obj.updated_at, obj.is_resolved = uuid.uuid4(), now, now, False
                obj.comment_type = obj.comment_type or "general"

    async def execute(self, stmt):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = re.sub(r"\s+", " ", str(compiled))
        self.queries.append(sql)
        if "FROM test_case_comments" in sql:
            assert "test_case_comments.id = %(id_1)s" in sql
            assert "test_case_comments.test_case_id = %(test_case_id_1)s" in sql
            parent_id, case_id = compiled.params["id_1"], compiled.params["test_case_id_1"]
            hit = self.comments.get(parent_id) == case_id
            return SimpleNamespace(scalar_one_or_none=lambda: parent_id if hit else None)
        if "FROM users JOIN project_members" in sql:
            return SimpleNamespace(all=lambda: list(self.members))
        raise AssertionError(f"unexpected query: {sql}")

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def rollback(self):  # pragma: no cover - the route never rolls back
        raise AssertionError("rollback")

    def of(self, model):
        return [obj for obj in self.added if isinstance(obj, model)]


def _user(role: str, username: str, full_name: str):
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, is_active=True, api_key_project_id=None,
        full_name=full_name, username=username, is_service_account=False,
    )


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management
    from app.routers.test_management_shared import require_case_access

    state: dict = {"db": ScriptedSession(), "user": _user("TESTER", "bob", "Bob Ray")}
    app = FastAPI()
    app.include_router(test_management.router)

    async def _db():
        yield state["db"]

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    app.dependency_overrides[require_case_access] = lambda: state["db"].case
    return TestClient(app, raise_server_exceptions=False), state


def _post(http, body):
    return http.post(f"{BASE}/cases/{CASE}/comments", json=body)


def test_a_mention_records_one_ledger_row_and_one_in_app_notification(client):
    http, state = client
    alice = uuid.uuid4()
    db = state["db"] = ScriptedSession(members=[
        (alice, "alice", "Alice Ng"), (state["user"].id, "bob", "Bob Ray"),
    ])
    resp = _post(http, {"content": "@alice please re-check step 3 (cc @bob, @carol)", "step_number": 3})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mentions"] == [{"user_id": str(alice), "username": "alice", "display_name": "Alice Ng"}]

    [comment] = db.of(CommentRow)
    assert comment.test_case_id == CASE and comment.author_id == state["user"].id
    [event] = db.of(ProjectActivityEvent)
    assert event.event_type == "test_case.commented"
    assert event.project_id == P
    assert event.entity_type == "test_case" and event.entity_id == str(CASE)
    assert event.entity_label == "Login works"
    assert event.actor_type == "user" and event.actor_id == state["user"].id
    assert event.context == {
        "comment_id": str(comment.id), "mentions": [str(alice)], "mention_count": 1, "step_number": 3,
    }
    assert event.source_table == "test_case_comments" and event.source_id == comment.id
    assert entity_href(event.entity_type, event.entity_id) == f"/test-management?case={CASE}"

    [note] = db.of(NotificationLog)
    assert note.user_id == alice and note.project_id == P
    assert note.channel == "in_app" and note.event_type == "test_case.mentioned"
    assert note.status == "sent" and note.sent_at is not None and note.is_read is False
    assert re.fullmatch(r"[0-9a-f]{64}", note.delivery_key)
    assert note.title == "Bob Ray mentioned you on Login works"
    assert note.delivery_metadata == {"app_path": f"/test-management?case={CASE}", "comment_id": str(comment.id)}

    assert db.commits == 1 and db.refreshed == [comment]
    assert len([q for q in db.queries if "FROM users JOIN project_members" in q]) == 1


def test_a_comment_without_a_mention_is_one_ledger_row_and_no_notification(client):
    http, state = client
    db = state["db"]
    resp = _post(http, {"content": "Looks right"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == []
    [event] = db.of(ProjectActivityEvent)
    assert event.context["mentions"] == [] and event.context["mention_count"] == 0
    assert db.of(NotificationLog) == []
    assert db.queries == []            # no parent, no "@": no query at all
    assert db.commits == 1


def test_an_unknown_or_non_member_name_stays_text(client):
    http, state = client
    db = state["db"] = ScriptedSession(members=[(uuid.uuid4(), "alice", "Alice Ng")])
    resp = _post(http, {"content": "@carol and @alice2 and mail@alice"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["mentions"] == []
    assert db.of(NotificationLog) == []
    assert db.of(ProjectActivityEvent)[0].context["mentions"] == []


@pytest.mark.parametrize("where", ["missing", "foreign"])
def test_a_parent_from_another_case_or_nowhere_is_the_same_404(client, where):
    http, state = client
    foreign = uuid.uuid4()
    db = state["db"] = ScriptedSession(comments={foreign: OTHER_CASE})
    parent = foreign if where == "foreign" else uuid.uuid4()
    resp = _post(http, {"content": "@alice replying", "parent_id": str(parent)})
    assert resp.status_code == 404
    assert resp.json()["detail"] == NOT_FOUND
    assert db.added == [] and db.commits == 0
    assert len(db.queries) == 1        # the parent check only: no members read, nothing staged


def test_a_parent_on_the_same_case_is_accepted(client):
    http, state = client
    parent = uuid.uuid4()
    db = state["db"] = ScriptedSession(comments={parent: CASE})
    resp = _post(http, {"content": "agreed", "parent_id": str(parent)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["parent_id"] == str(parent)
    assert db.of(CommentRow)[0].parent_id == parent
    assert db.commits == 1


def test_a_malformed_parent_id_is_422(client):
    http, state = client
    resp = _post(http, {"content": "agreed", "parent_id": "not-a-uuid"})
    assert resp.status_code == 422
    assert state["db"].added == [] and state["db"].commits == 0


def test_a_viewer_is_refused_by_the_existing_floor(client):
    http, state = client
    state["user"] = _user("VIEWER", "vera", "Vera")
    resp = _post(http, {"content": "@alice hi"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Requires at least TESTER role"
    assert state["db"].added == [] and state["db"].queries == [] and state["db"].commits == 0
