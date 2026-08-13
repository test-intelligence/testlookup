from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.conversation import ConversationAgent  # noqa: E402
from app.models.schemas import ChatSessionCreate  # noqa: E402
from app.services import chat_service  # noqa: E402


class _Collection:
    def __init__(self, document):
        self.document = document
        self.queries = []

    async def find_one(self, query, projection=None):
        self.queries.append(query)
        if self.document is None:
            return None
        expected = {key: value for key, value in query.items() if key != "report_version"}
        if any(self.document.get(key) != value for key, value in expected.items()):
            return None
        if "report_version" in query and self.document.get("report_version") != query["report_version"]:
            return None
        return dict(self.document)


class _Mongo:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, key):
        return self.collection


@pytest.mark.asyncio
async def test_bound_report_context_uses_exact_version_and_source(monkeypatch):
    project_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())
    collection = _Collection({
        "report_id": report_id,
        "project_id": project_id,
        "test_run_id": run_id,
        "report_version": 3,
        "status": "published",
        "verification": {"status": "passed"},
        "decision_intelligence": {"release_decision": {"gate": "GO"}},
    })
    monkeypatch.setattr("app.agents.conversation.get_mongo_db", lambda: _Mongo(collection))

    context, sources = await ConversationAgent()._fetch_bound_report_context(
        project_id, run_id, report_id, 3
    )

    assert "immutable v3" in context
    assert '"gate": "GO"' in context
    assert sources == [{
        "type": "decision_report",
        "id": report_id,
        "test_run_id": run_id,
        "report_version": 3,
        "status": "published",
    }]
    assert collection.queries[0]["report_version"] == 3


@pytest.mark.asyncio
async def test_missing_bound_report_does_not_fallback_to_latest(monkeypatch):
    project_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())
    collection = _Collection(None)
    monkeypatch.setattr("app.agents.conversation.get_mongo_db", lambda: _Mongo(collection))

    context, sources = await ConversationAgent()._fetch_bound_report_context(
        project_id, run_id, report_id, 7
    )

    assert "Do not substitute" in context
    assert sources[0]["status"] == "unavailable"
    assert sources[0]["report_version"] == 7


def test_chat_session_schema_rejects_nonpositive_report_version():
    with pytest.raises(ValueError):
        ChatSessionCreate(active_report_version=0)


@pytest.mark.asyncio
async def test_create_session_persists_report_binding():
    class _DB:
        def __init__(self):
            self.added = None

        def add(self, value):
            self.added = value

        async def flush(self):
            return None

    payload = SimpleNamespace(
        project_id=uuid.uuid4(),
        active_test_run_id=uuid.uuid4(),
        active_report_id="report-1",
        active_report_version=2,
        title="Bound",
    )
    user = SimpleNamespace(id=uuid.uuid4())
    db = _DB()
    session = await chat_service.create_session(db, payload, user)
    assert session.active_test_run_id == payload.active_test_run_id
    assert session.active_report_id == "report-1"
    assert session.active_report_version == 2