"""Canonical logical task queues shared by routing and operational reads."""
from __future__ import annotations

CRITICAL_QUEUE = "critical"
INGESTION_QUEUE = "ingestion"
AI_ANALYSIS_QUEUE = "ai_analysis"
AGENT_CHILDREN_QUEUE = "agent_children"
DEFAULT_QUEUE = "default"

BASE_TASK_QUEUES = (
    CRITICAL_QUEUE, INGESTION_QUEUE, AI_ANALYSIS_QUEUE, AGENT_CHILDREN_QUEUE, DEFAULT_QUEUE,
)


def all_task_queue_names() -> tuple[str, ...]:
    """Read effective shard settings at call time, keeping legacy queue names."""
    from app.worker.ingestion_routing import all_shard_queues

    return (*BASE_TASK_QUEUES, *all_shard_queues())


def redis_queue_keys(queue: str) -> tuple[str, ...]:
    """Physical Redis priority lists for one configured logical queue.

    Use the installed transport's defaults and configured overrides rather
    than assuming LLEN(queue) includes nonzero-priority work. This reproduces
    the existing key contract; it does not change message ordering or encoding.
    """
    from kombu.transport.redis import Channel

    from app.worker.celery_app import celery_app

    options = celery_app.conf.broker_transport_options or {}
    steps = options.get("priority_steps", Channel.priority_steps)
    separator = options.get("sep", Channel.sep)
    prefix = options.get("global_keyprefix", "")
    if (
        not isinstance(steps, (list, tuple)) or not steps
        or any(type(step) is not int or not 0 <= step <= 9 for step in steps)
        or list(steps) != sorted(set(steps))
        or not isinstance(separator, str) or not isinstance(prefix, str)
    ):
        raise ValueError("unsupported Redis queue transport configuration")
    return tuple(prefix + queue + (separator + str(step) if step else "") for step in steps)
