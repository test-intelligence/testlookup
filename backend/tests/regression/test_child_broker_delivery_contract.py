"""Regression guard for durable child-task delivery semantics."""

import pytest

pytest.importorskip("celery")


def test_child_delivery_uses_late_ack_and_isolated_queue_without_blind_retry():
    from app.worker.celery_app import celery_app
    import app.worker.tasks  # noqa: F401 - registers the task in Celery

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss is True
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == 3600
    route = celery_app.conf.task_routes[
        "app.worker.tasks.run_agent_child_investigation"
    ]
    assert route["queue"] == "agent_children"

    task = celery_app.tasks["app.worker.tasks.run_agent_child_investigation"]
    assert task.max_retries == 0
    assert task.queue == "agent_children"
