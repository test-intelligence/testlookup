"""Ask-AI chat HTTP surface: the streamed turn, the message window, the session list.

Regressions pinned here (homelab evaluation, 2026-10-09):
  * ``GET .../messages`` returned the OLDEST 50 messages, so a long
    conversation stopped showing its new turns, and it returned the history
    compression's ``summary`` row, which the page rendered as an answer;
  * ``GET /sessions`` ignored the active project: an admin saw every
    project's conversations (purged projects' included) under one name;
  * a provider failure came back as a 200 whose "reply" was an apology.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.conversation import ChatTurnError
from app.models.postgres import UserRole
from app.models.schemas import SendMessageRequest, StreamMessageRequest
from app.routers import chat as chat_router
from app.services import chat_service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements = []
        self.commit = AsyncMock()

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.rows)


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# ── Request shape ────────────────────────────────────────────────────────────


def test_a_streamed_turn_is_a_question_or_a_retry():
    assert StreamMessageRequest(message="What failed?").retry is False
    assert StreamMessageRequest(retry=True).message is None
    with pytest.raises(ValidationError):
        StreamMessageRequest()
    with pytest.raises(ValidationError):
        StreamMessageRequest(message="x", retry=True)
    with pytest.raises(ValidationError):
        StreamMessageRequest(message="x" * 4001)
    # The page's limit is the server's limit (the page allowed 5,000; 4,001+
    # came back as a silent 422).
    assert SendMessageRequest.model_fields["message"].metadata[-1].max_length == 4000


# ── Message window and session list ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_messages_are_the_latest_window_oldest_first_without_summaries():
    newest, older = SimpleNamespace(n=2), SimpleNamespace(n=1)
    db = _DB(rows=[newest, older])  # what ORDER BY created_at DESC returns
    out = await chat_service.get_messages(db, uuid.uuid4(), 50)

    assert out == [older, newest]
    sql = _sql(db.statements[0])
    assert "ORDER BY chat_messages.created_at DESC" in sql
    assert "LIMIT 50" in sql
    assert "chat_messages.role IN ('user', 'assistant')" in sql


@pytest.mark.asyncio
async def test_sessions_can_be_narrowed_to_the_active_project(monkeypatch):
    project = uuid.uuid4()
    monkeypatch.setattr("app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None))
    db = _DB()
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN)

    await chat_service.list_sessions(db, user)
    await chat_service.list_sessions(db, user, project_id=project)

    unfiltered, filtered = (_sql(s) for s in db.statements)
    assert "chat_sessions.project_id =" not in unfiltered
    assert f"chat_sessions.project_id = '{project.hex}'" in filtered or str(project) in filtered


# ── The SSE pump ────────────────────────────────────────────────────────────


class _Turn:
    def __init__(self, items, delay=0.0):
        self.items = list(items)
        self.delay = delay
        self.cancelled = False

    async def next_event(self, timeout=None):
        if not self.items:
            raise StopAsyncIteration
        item = self.items.pop(0)
        if item == "idle":
            await asyncio.sleep(timeout or 0)
            return None
        return item

    def cancel_nowait(self):
        self.cancelled = True


class _Request:
    def __init__(self, disconnect_after=None):
        self.checks = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self):
        self.checks += 1
        return self.disconnect_after is not None and self.checks >= self.disconnect_after


async def _drain(gen):
    return [frame async for frame in gen]


@pytest.mark.asyncio
async def test_events_are_framed_as_server_sent_events(monkeypatch):
    turn = _Turn([("start", {"user_message_id": "q"}), ("delta", {"text": "Hi"}), ("done", {"message": {}})])
    frames = await _drain(chat_service.sse_events(turn, _Request()))
    assert frames == [
        'event: start\ndata: {"user_message_id": "q"}\n\n',
        'event: delta\ndata: {"text": "Hi"}\n\n',
        'event: done\ndata: {"message": {}}\n\n',
    ]
    assert turn.cancelled is True  # always released (a no-op once the turn ended)


@pytest.mark.asyncio
async def test_a_quiet_turn_sends_pings_and_a_gone_reader_stops_it(monkeypatch):
    monkeypatch.setattr(chat_service, "_SSE_DISCONNECT_POLL_SECONDS", 0.001)
    monkeypatch.setattr(chat_service, "_SSE_HEARTBEAT_SECONDS", 0.0)
    turn = _Turn(["idle", "idle", ("delta", {"text": "never sent"})])
    frames = await _drain(chat_service.sse_events(turn, _Request(disconnect_after=2)))
    assert frames == [": ping\n\n"]
    assert turn.cancelled is True


# ── The routes ───────────────────────────────────────────────────────────────


def _session(project=True, title="New conversation"):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4() if project else None, title=title,
        active_test_run_id=None, active_report_id=None, active_report_version=None,
    )


@pytest.mark.asyncio
async def test_the_stream_route_answers_with_an_event_stream(monkeypatch):
    session = _session()
    db = _DB()
    started = {}

    def start_turn(s, payload, user):
        started["payload"] = payload
        return _Turn([("done", {"message": {}})])

    monkeypatch.setattr(chat_service, "start_turn", start_turn)
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER, api_key_project_id=None)
    response = await chat_router.stream_message(
        StreamMessageRequest(message="What failed in build 105?"), _Request(), session, db, user,
    )

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert session.title == "What failed in build 105?"  # named before streaming
    db.commit.assert_awaited()
    assert started["payload"].message == "What failed in build 105?"


@pytest.mark.asyncio
async def test_the_stream_route_needs_a_project_for_non_admins(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_ENGINEER, api_key_project_id=None)
    with pytest.raises(HTTPException) as exc:
        await chat_router.stream_message(
            StreamMessageRequest(message="hi there you"), _Request(), _session(project=False), _DB(), user,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_a_failed_turn_is_an_http_error_not_an_apology(monkeypatch):
    async def failing(db, session, payload, user):
        raise ChatTurnError("timeout", "The AI provider took too long to answer.", retryable=True)

    monkeypatch.setattr(chat_service, "send_message", failing)
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, api_key_project_id=None)
    with pytest.raises(HTTPException) as exc:
        await chat_router.send_message(SendMessageRequest(message="what failed?"), _session(), _DB(), user)
    assert exc.value.status_code == 504
    assert exc.value.detail == {
        "code": "timeout", "message": "The AI provider took too long to answer.", "retryable": True,
    }
