"""Every task can still be sent on Celery 5.7: no inherited soft time limit above the task's hard limit (W47).

Celery 5.7's ``Task.apply_async`` raises ``ValueError('soft_time_limit must be less than or equal to time_limit')``
before anything reaches the broker. A task that declares only ``time_limit=120`` inherits ``task_soft_time_limit``
(1740 s) when it binds, so on the W47 local stack beat logged "Couldn't apply scheduled task relay-…" for every relay,
and 34 tasks (run_ai_analysis and execute_agent_action among them) could not be queued from the API either. Unit tests
that mock ``apply_async`` never reach that check, so these tests call the real one and stub only ``send_task``.
"""
from __future__ import annotations

import inspect

import pytest
from celery import Celery

from app.worker.celery_app import TimeLimitedTask, celery_app


@pytest.fixture(scope="module")
def registered_tasks():
    celery_app.loader.import_default_modules()
    return {name: task for name, task in celery_app.tasks.items() if not name.startswith("celery.")}


def test_no_registered_task_has_a_soft_limit_above_its_hard_limit(registered_tasks):
    refused = sorted(
        (name, task.soft_time_limit, task.time_limit)
        for name, task in registered_tasks.items()
        if task.soft_time_limit and task.time_limit and task.soft_time_limit > task.time_limit
    )
    assert len(registered_tasks) > 90
    assert refused == []


@pytest.mark.parametrize(
    "name",
    [
        "app.worker.tasks.relay_queued_criteria_deletions",  # beat, every minute
        "app.worker.tasks.run_ai_analysis",  # queued from the API
        "app.worker.tasks.execute_agent_action",
    ],
)
def test_a_formerly_refused_task_reaches_send_task(registered_tasks, monkeypatch, name):
    sent = []
    monkeypatch.setattr(type(celery_app), "send_task", lambda self, task_name, *a, **k: sent.append(task_name))
    task = registered_tasks[name]
    # The real apply_async also checks the call against the task's signature: pass every required argument.
    required = {
        p.name: "00000000-0000-0000-0000-0000000000a7"
        for p in inspect.signature(task.run).parameters.values()
        if p.default is inspect.Parameter.empty and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }

    task.apply_async(kwargs=required, ignore_result=True)

    assert sent == [name]
    assert task.soft_time_limit == task.time_limit < celery_app.conf.task_soft_time_limit


def test_tasks_with_their_own_window_keep_the_global_limits(registered_tasks):
    ingest = registered_tasks["app.worker.tasks.ingest_uploaded_file"]
    assert (ingest.soft_time_limit, ingest.time_limit) == (1740, 1860)


def test_only_the_inherited_soft_limit_is_capped():
    app = Celery("w47-time-limits", task_cls=TimeLimitedTask, set_as_current=False)
    app.conf.task_soft_time_limit = 1740
    app.conf.task_time_limit = 1860

    @app.task(name="w47.inherits_soft", time_limit=120)
    def inherits_soft():
        return None

    @app.task(name="w47.declares_soft_above_hard", time_limit=100, soft_time_limit=200)
    def declares_soft_above_hard():
        return None

    sent = []
    app.send_task = lambda task_name, *a, **k: sent.append(task_name)
    app.finalize()

    assert (inherits_soft.soft_time_limit, inherits_soft.time_limit) == (120, 120)
    inherits_soft.apply_async()
    assert sent == ["w47.inherits_soft"]
    # An author's own soft > hard is an error, and Celery 5.7 still says so.
    with pytest.raises(ValueError, match="soft_time_limit must be less than or equal to time_limit"):
        declares_soft_above_hard.apply_async()
