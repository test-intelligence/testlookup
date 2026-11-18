"""D-0006: the staged ``transition_notifications`` payload binds to its task and carries ``run_ai``.

An upload with ``run_ai=false`` stages ``{"run_id": ..., "run_ai": False}``; an
AI-on upload stages exactly ``{"run_id": ...}`` as before, so its payload and
input version are unchanged. Each payload is BOUND to
``inspect.signature(dispatch_transition_notifications.run)`` (a mocked
``apply_async`` alone hides Celery's argument check: the outbox once staged a
key its task did not take, and every attempt failed before publishing), sent
through the relay's real ``_publish_downstream``, then run through the real
task body (``.apply``) to see what reaches ``evaluate_run_transitions``.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _signature() -> inspect.Signature:
    from app.worker.tasks import dispatch_transition_notifications

    return inspect.signature(dispatch_transition_notifications.run)


def _staged_payload(monkeypatch, *, run_ai: bool) -> tuple[dict, str]:
    from app.services import run_downstream_outbox as service

    staged = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "stage_downstream_operation", staged)
    run = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="build-42", pass_rate=0.0,
        total_tests=1, failed_tests=1, end_time=datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc),
    )
    asyncio.run(service.stage_finalize_operations(
        MagicMock(), run=run, project=SimpleNamespace(name="payments"), run_ai=run_ai,
    ))
    [call] = [c for c in staged.await_args_list if c.kwargs["operation"] == "transition_notifications"]
    return call.kwargs["payload"], call.kwargs["input_version"]


def test_the_new_parameter_is_last_and_defaulted():
    params = list(_signature().parameters.values())
    assert [p.name for p in params] == ["run_id", "run_ai"]
    assert params[-1].default is True
    # A payload staged before this parameter existed still binds.
    _signature().bind(run_id="r")


def test_an_ai_on_payload_is_unchanged(monkeypatch):
    from app.services.run_downstream_outbox import operation_input_version

    payload, version = _staged_payload(monkeypatch, run_ai=True)
    assert payload == {"run_id": payload["run_id"]}
    assert version == operation_input_version("transition_notifications", {"run_id": payload["run_id"]})


def test_a_skip_ai_payload_carries_the_flag_and_a_different_version(monkeypatch):
    payload, version = _staged_payload(monkeypatch, run_ai=False)
    assert payload == {"run_id": payload["run_id"], "run_ai": False}
    _, ai_on_version = _staged_payload(monkeypatch, run_ai=True)
    assert version != ai_on_version


@pytest.fixture
def worker(monkeypatch):
    """The real task body; the evaluation itself is replaced by a recorder."""
    import app.services.flaky_quarantine_service as quarantine
    import app.services.notification_transitions as transitions
    from app.worker import tasks

    reached: list[dict] = []

    async def record(run_id, **kwargs):
        reached.append({"run_id": run_id, **kwargs})
        return {"recorded": True}

    async def no_stability(_run_id):
        return {"tracked": False}

    monkeypatch.setattr(transitions, "evaluate_run_transitions", record)
    monkeypatch.setattr(quarantine, "update_quarantine_stability", no_stability)

    def run(kwargs: dict) -> dict:
        _signature().bind(**kwargs)  # raises TypeError for a payload the task refuses
        tasks.dispatch_transition_notifications.apply(
            kwargs=kwargs, task_id=f"task-{uuid.uuid4().hex[:8]}"
        ).get()
        return reached[-1]

    return run


@pytest.mark.parametrize("run_ai", [True, False])
def test_the_relayed_payload_reaches_the_evaluation_with_run_ai(monkeypatch, worker, run_ai):
    from app.services import run_downstream_outbox as service
    from app.worker import tasks

    payload, _ = _staged_payload(monkeypatch, run_ai=run_ai)
    apply_async = MagicMock()
    monkeypatch.setattr(tasks.dispatch_transition_notifications, "apply_async", apply_async)
    service._publish_downstream(SimpleNamespace(
        id=uuid.uuid4(), operation="transition_notifications", payload=payload,
        queue="default", priority=5, dispatch_token=uuid.uuid4(),
    ))
    sent = apply_async.call_args.kwargs["kwargs"]
    reached = worker(sent)
    assert str(reached["run_id"]) == payload["run_id"]
    assert reached["auto_investigate"] is run_ai
