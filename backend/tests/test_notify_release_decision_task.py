"""W09 NTF-01: the Celery task that stages "Quality gate failed" for an agent NO_GO.

``decision_report_critic_agent._persist`` enqueues
``app.worker.tasks.notify_release_decision_no_go`` after it publishes, so the
reads and the staging run on ``default`` in their own task, never inside the
pipeline task. The task opens ONE session, lets
``gate_notifications.stage_agent_decision_no_go`` decide, commits once, and
answers ``{"run_id", "outcome"}``. It never raises and never retries: the relay
of staged rows is the durable half, and a failure costs this one notification
(as it does for the ``release.decided`` webhook) -- logged with its type.

The signature is pinned in the W09 contract (§1.1) because G-A's critic test
binds its ``apply_async`` arguments to it.
"""
from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services.notification import gate_notifications
from app.worker import tasks

RUN, PIPELINE, SHA = "6f1c3c1e-2c55-4a4e-9a52-0d9f2c1b7e10", "pipeline-7", "ab" * 32


class _Session:
    def __init__(self, world):
        self.world = world

    async def commit(self):
        self.world.commits += 1


def _world(monkeypatch, stage):
    world = SimpleNamespace(opened=0, commits=0, calls=[], logs=[])

    @asynccontextmanager
    async def _factory():
        world.opened += 1
        yield _Session(world)

    async def _stage(db, **kwargs):
        world.calls.append((db, kwargs))
        return await stage(db, **kwargs)

    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)
    monkeypatch.setattr(gate_notifications, "stage_agent_decision_no_go", _stage)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)
    # structlog's capture_logs cannot see a logger bound under the cached
    # configuration by an earlier test: record the module's own.
    monkeypatch.setattr(
        tasks, "_slog", SimpleNamespace(warning=lambda event, **kw: world.logs.append({"event": event, **kw}))
    )
    return world


def test_the_task_stages_on_one_session_and_commits_once(monkeypatch):
    async def _staged(_db, **_kwargs):
        return "staged"

    world = _world(monkeypatch, _staged)
    result = tasks.notify_release_decision_no_go.run(RUN, PIPELINE, SHA)

    assert result == {"run_id": RUN, "outcome": "staged"}
    assert (world.opened, world.commits) == (1, 1)
    [(db, kwargs)] = world.calls
    assert isinstance(db, _Session)
    assert kwargs == {"run_id": RUN, "pipeline_run_id": PIPELINE, "evidence_bundle_sha256": SHA}
    assert world.logs == []


@pytest.mark.parametrize("outcome", ["no_decision", "pipeline_mismatch", "overridden", "no_council", "withheld", "not_no_go"])
def test_every_non_staging_outcome_is_answered_as_is(monkeypatch, outcome):
    async def _answer(_db, **_kwargs):
        return outcome

    world = _world(monkeypatch, _answer)
    assert tasks.notify_release_decision_no_go.run(RUN) == {"run_id": RUN, "outcome": outcome}
    assert world.calls[0][1] == {"run_id": RUN, "pipeline_run_id": None, "evidence_bundle_sha256": None}


def test_a_failing_stage_answers_error_and_never_raises(monkeypatch):
    async def _boom(_db, **_kwargs):
        raise RuntimeError("database went away")

    world = _world(monkeypatch, _boom)
    result = tasks.notify_release_decision_no_go.run(RUN, PIPELINE, SHA)

    assert result == {"run_id": RUN, "outcome": "error"}
    assert world.commits == 0, "nothing staged may be committed after a failure"
    assert world.logs == [{"event": "release_decision_notification_failed", "error_type": "RuntimeError"}]


def test_the_task_is_declared_as_the_contract_pins_it():
    task = tasks.notify_release_decision_no_go
    assert task.name == "app.worker.tasks.notify_release_decision_no_go"
    assert (task.queue, task.max_retries, task.time_limit) == ("default", 0, 60)
    params = inspect.signature(task.run).parameters
    assert list(params) == ["run_id", "pipeline_run_id", "evidence_bundle_sha256"]
    assert (params["pipeline_run_id"].default, params["evidence_bundle_sha256"].default) == (None, None)


def test_the_critics_enqueue_arguments_bind_to_the_real_signature():
    """``_persist`` sends ``args=[run_id, pipeline_run_id, evidence_bundle_sha256]``
    (contract §1.1); a positional list that no longer binds would fail only on
    a live worker."""
    inspect.signature(tasks.notify_release_decision_no_go.run).bind(RUN, PIPELINE, SHA)
    inspect.signature(tasks.notify_release_decision_no_go.run).bind(RUN, PIPELINE, None)
