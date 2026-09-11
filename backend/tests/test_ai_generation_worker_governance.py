"""Async AI generation must match synchronous lifecycle/audit guarantees."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.postgres import ManagedTestCase


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def test_async_ai_worker_stages_snapshot_audit_and_postcommit_state_refresh(monkeypatch):
    from app.db import postgres
    from app.services import test_management_metrics_service as metrics
    from app.services import test_case_ai_agent
    from app.worker import tasks

    project_id = uuid.uuid4()
    author_id = uuid.uuid4()
    actor = SimpleNamespace(
        id=author_id,
        full_name="AI Generation Author",
        username="ai.author",
    )
    added = []
    calls: list[str] = []
    session = SimpleNamespace(info={})

    def add(row):
        added.append(row)

    async def flush():
        for row in added:
            if isinstance(row, ManagedTestCase) and row.id is None:
                row.id = uuid.uuid4()

    session.add = add
    session.get = AsyncMock(return_value=actor)
    session.flush = AsyncMock(side_effect=flush)
    session.commit = AsyncMock(side_effect=lambda: calls.append("commit"))
    monkeypatch.setattr(
        postgres,
        "AsyncSessionLocal",
        lambda: _SessionContext(session),
    )
    monkeypatch.setattr(
        test_case_ai_agent,
        "generate_test_cases_tool",
        SimpleNamespace(
            ainvoke=AsyncMock(return_value={
                "test_cases": [
                    {
                        "title": "AI checkout coverage",
                        "objective": "Verify checkout",
                        "steps": [],
                        "expected_result": "Order placed",
                    }
                ]
            })
        ),
    )
    emit = AsyncMock(side_effect=lambda _db: calls.append("emit"))
    monkeypatch.setattr(metrics, "emit_staged_test_management_metrics", emit)

    result = tasks.generate_ai_test_cases_task.run(
        "checkout requirements",
        str(project_id),
        str(author_id),
    )

    assert result == {"saved": 1}
    managed = [row for row in added if isinstance(row, ManagedTestCase)]
    snapshots = [
        row for row in added if row.__class__.__name__ == "TestCaseVersion"
    ]
    audits = [
        row for row in added if row.__class__.__name__ == "TestCaseAuditLog"
    ]
    assert len(managed) == len(snapshots) == len(audits) == 1
    assert managed[0].status == snapshots[0].status == "draft"
    assert snapshots[0].change_type == "created"
    assert audits[0].action == "ai_generated"
    assert audits[0].actor_id == author_id
    assert calls == ["commit", "emit"]
    assert session.info["test_management_state_metric_projects"] == {project_id}
