"""Celery routing stays inside the queue set every supported topology consumes (GAP-C08).

``celery_app.conf.task_routes`` (``app/worker/celery_app.py``) and ``required_queues``
(``compose_topology_contract.py``) are two hand-kept lists. The Compose topology contract
(``tests/regression/test_compose_worker_topology_contract.py``) proves every required queue has a
consumer; nothing proved every *routed* queue is a required one. A route to a queue nobody consumes
means tasks that are accepted and never run; a misspelt route key is silently ignored and the task
lands on ``default`` beside the beat work. Both pass every other test.

Static checks only: no broker, no worker, no clock.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

import pytest

from app.worker.celery_app import celery_app
from compose_topology_contract import required_queues

# A static route or a task decorator must name a base queue. Shard queues
# (``ingestion.shard.N``) are chosen per call by ``queue_for_project`` and exist only when
# LIVE_INGEST_SHARD_COUNT > 0, so a static route to one would strand tasks on a 0-shard deployment.
BASE_QUEUES = required_queues(0)

# The heavy or latency-sensitive tasks whose queue the worker split depends on.
PINNED_ROUTES = {
    "app.worker.tasks.ingest_test_run": "ingestion",
    "app.worker.tasks.run_ai_analysis": "ai_analysis",
    "app.worker.tasks.run_agent_pipeline": "ai_analysis",
    "app.worker.tasks.run_agent_child_investigation": "agent_children",
    "app.worker.tasks.run_live_test_analysis": "critical",
    "app.worker.tasks.reindex_search": "default",
}


@pytest.fixture(scope="module")
def registered_tasks() -> dict:
    celery_app.loader.import_default_modules()
    tasks = {
        name: task
        for name, task in celery_app.tasks.items()
        if not name.startswith("celery.")
    }
    # A broken import would leave the registry nearly empty and every check below vacuous.
    assert len(tasks) >= 90, (
        f"only {len(tasks)} app tasks registered; did a worker module fail to import?"
    )
    return tasks


def _routes() -> dict[str, dict]:
    routes = celery_app.conf.task_routes
    assert isinstance(routes, dict) and routes, (
        "task_routes is no longer a non-empty mapping"
    )
    return routes


def _effective_queue(task) -> str:
    """The queue ``apply_async`` would publish to with no call-time override.

    Uses Celery's own router: the decorator's exec options are merged over the matching route, exactly
    as ``Task.apply_async`` does.
    """
    options = dict(task._get_exec_options())
    routed = celery_app.amqp.router.route(options, task.name, (), {}, task)
    queue = routed["queue"]
    return queue.name if hasattr(queue, "name") else str(queue)


def test_every_routed_queue_is_a_required_queue():
    routes = _routes()
    stray = {
        key: route.get("queue")
        for key, route in routes.items()
        if route.get("queue") not in BASE_QUEUES
    }
    assert not stray, (
        f"task_routes names queues no supported topology consumes: {stray}; "
        f"required_queues(0) = {sorted(BASE_QUEUES)}. Add the queue to compose_topology_contract.py "
        "and a worker -Q in every compose file, or route the task to an existing queue."
    )


def test_every_route_key_names_a_registered_task(registered_tasks):
    routes = _routes()
    unknown = sorted(
        key for key in routes if "*" not in key and key not in registered_tasks
    )
    assert not unknown, (
        f"task_routes keys that name no registered task (a typo routes the task to 'default'): {unknown}"
    )
    dead_globs = sorted(
        key
        for key in routes
        if "*" in key and not any(fnmatchcase(name, key) for name in registered_tasks)
    )
    assert not dead_globs, (
        f"task_routes glob keys that match no registered task: {dead_globs}"
    )


def test_every_task_decorator_queue_is_a_required_queue(registered_tasks):
    stray = {
        name: task.queue
        for name, task in registered_tasks.items()
        if getattr(task, "queue", None) is not None and task.queue not in BASE_QUEUES
    }
    assert not stray, f"tasks declare queues no supported topology consumes: {stray}"


def test_route_and_decorator_queue_agree(registered_tasks):
    # apply_async merges the decorator's queue OVER the route, so a route that disagrees with the
    # decorator is dead configuration that reads as if it took effect.
    routes = _routes()
    disagree = {
        key: (route.get("queue"), registered_tasks[key].queue)
        for key, route in routes.items()
        if key in registered_tasks
        and getattr(registered_tasks[key], "queue", None) is not None
        and registered_tasks[key].queue != route.get("queue")
    }
    assert not disagree, (
        f"route queue vs decorator queue (the decorator wins): {disagree}"
    )


def test_every_registered_task_publishes_to_a_required_queue(registered_tasks):
    effective = {
        name: _effective_queue(task) for name, task in registered_tasks.items()
    }
    stray = {
        name: queue for name, queue in effective.items() if queue not in BASE_QUEUES
    }
    assert not stray, (
        f"tasks whose effective queue no supported topology consumes: {stray}"
    )
    # Positive: the split is real, not everything collapsed onto one queue.
    assert {"critical", "ingestion", "ai_analysis", "agent_children", "default"} <= set(
        effective.values()
    ), f"effective queues used: {sorted(set(effective.values()))}"


@pytest.mark.parametrize(("task_name", "queue"), sorted(PINNED_ROUTES.items()))
def test_pinned_task_publishes_to_its_queue(registered_tasks, task_name, queue):
    assert task_name in registered_tasks, f"{task_name} is not registered"
    assert _effective_queue(registered_tasks[task_name]) == queue
