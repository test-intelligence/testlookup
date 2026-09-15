"""E2.3 canonical DLQ inspection and guarded replay API."""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from app.models.postgres import UserRole
from app.streams import DLQ_STREAM

pytestmark = pytest.mark.asyncio


class _Redis:
    def __init__(self, entry_id="1720000000000-0", fields=None):
        self.entry_id = entry_id
        self.fields = fields
        self.locks: set[str] = set()

    async def set(self, key, _value, *, ex, nx):
        assert ex == 60 and nx is True
        if key in self.locks:
            return False
        self.locks.add(key)
        return True

    async def delete(self, key):
        self.locks.discard(key)
        return 1

    async def xrange(self, key, *, min, max, count):
        assert key == DLQ_STREAM and min == max == self.entry_id and count == 1
        return [] if self.fields is None else [(self.entry_id, self.fields)]

    async def xdel(self, key, entry_id):
        assert key == DLQ_STREAM and entry_id == self.entry_id
        self.fields = None
        return 1

    async def xrevrange(self, _key, count):
        assert count >= 1
        return [] if self.fields is None else [(self.entry_id, self.fields)]

    async def xlen(self, _key):
        return int(self.fields is not None)

    async def lrange(self, _key, _start, _end):
        return []

    async def llen(self, _key):
        return 0


def _celery_fields(task_name="app.worker.tasks.run_agent_pipeline"):
    return {
        "source": "celery",
        "task_name": task_name,
        "task_id": "old-task",
        "kwargs": (
            '{"test_run_id": "run-1", "project_id": "project-1", '
            '"build_number": "7", "workflow_type": "deep", "rerun_of": null}'
        ),
        "error": "RuntimeError: failed",
    }


def _install(monkeypatch, redis):
    from app.db import redis_client
    from app.worker.celery_app import celery_app

    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    replacement = MagicMock(id="replacement-task")
    send = MagicMock(return_value=replacement)
    monkeypatch.setattr(celery_app, "send_task", send)
    return send


async def test_pipeline_dead_letter_preserves_the_replay_signature():
    from app.worker import tasks

    source = inspect.getsource(tasks.run_agent_pipeline.run)
    send_call = source[source.index("_send_to_dlq(") :]
    for required in (
        '"test_run_id": test_run_id',
        '"project_id": project_id',
        '"build_number": build_number',
        '"workflow_type": workflow_type',
        '"rerun_of": rerun_of',
        '"requested_by": requested_by',
    ):
        assert required in send_call, f"pipeline replay payload lost {required}"


async def test_admin_lists_the_canonical_dlq(client, auth_as, monkeypatch):
    redis = _Redis(fields=_celery_fields())
    _install(monkeypatch, redis)
    auth_as(role=UserRole.ADMIN)

    response = await client.get("/api/v1/admin/dlq")

    assert response.status_code == 200, response.text
    assert response.json()["sources"]["stream"]["entries"][0]["id"] == redis.entry_id


async def test_admin_replays_allowlisted_celery_entry_once(client, auth_as, monkeypatch):
    redis = _Redis(fields=_celery_fields())
    send = _install(monkeypatch, redis)
    auth_as(role=UserRole.ADMIN)

    response = await client.post(f"/api/v1/admin/dlq/{redis.entry_id}/replay")

    assert response.status_code == 202, response.text
    assert response.json() == {
        "accepted": True,
        "entry_id": redis.entry_id,
        "task_name": "app.worker.tasks.run_agent_pipeline",
        "task_id": "replacement-task",
    }
    send.assert_called_once_with(
        "app.worker.tasks.run_agent_pipeline",
        kwargs={
            "test_run_id": "run-1",
            "project_id": "project-1",
            "build_number": "7",
            "workflow_type": "deep",
            "rerun_of": None,
        },
    )
    assert redis.fields is None, "the accepted entry stayed replayable"


async def test_replay_refuses_unknown_task_and_keeps_entry(client, auth_as, monkeypatch):
    redis = _Redis(fields=_celery_fields("app.worker.tasks.attacker_chosen"))
    send = _install(monkeypatch, redis)
    auth_as(role=UserRole.ADMIN)

    response = await client.post(f"/api/v1/admin/dlq/{redis.entry_id}/replay")

    assert response.status_code == 409
    send.assert_not_called()
    assert redis.fields is not None


async def test_broker_failure_keeps_entry_replayable(client, auth_as, monkeypatch):
    redis = _Redis(fields=_celery_fields())
    send = _install(monkeypatch, redis)
    send.side_effect = ConnectionError("broker down")
    auth_as(role=UserRole.ADMIN)

    response = await client.post(f"/api/v1/admin/dlq/{redis.entry_id}/replay")

    assert response.status_code == 503
    assert redis.fields is not None, "broker refusal deleted the only replay copy"
    assert not redis.locks, "a failed dispatch blocked the next operator attempt"


async def test_non_admin_cannot_list_or_replay(client, auth_as, monkeypatch):
    redis = _Redis(fields=_celery_fields())
    send = _install(monkeypatch, redis)
    auth_as(role=UserRole.QA_LEAD)

    listing = await client.get("/api/v1/admin/dlq")
    replay = await client.post(f"/api/v1/admin/dlq/{redis.entry_id}/replay")

    assert listing.status_code == replay.status_code == 403
    send.assert_not_called()
