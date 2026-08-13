"""Worker module used only by the disposable Redis visibility regression."""
from __future__ import annotations

import time
import os

from kombu import Exchange, Queue

from app.worker.celery_app import celery_app


_visibility_queue = "ci_visibility_drill"
_visibility_exchange = Exchange("default", type="direct")
celery_app.conf.task_queues = tuple(celery_app.conf.task_queues or ()) + (
    Queue(_visibility_queue, _visibility_exchange, routing_key=_visibility_queue),
)
celery_app.conf.broker_transport_options = {
    **dict(celery_app.conf.broker_transport_options or {}),
    "visibility_timeout": 4,
}


@celery_app.task(
    name="testlookup.regression.visibility_task",
    bind=True,
    acks_late=True,
    max_retries=0,
)
def visibility_task(self, marker: str, duration_seconds: int = 8) -> dict[str, object]:
    import os

    from redis import Redis

    redis_url = os.environ["REDIS_URL"]
    client = Redis.from_url(redis_url, decode_responses=True)
    key = f"testlookup:ci:visibility:{marker}"
    delivery = self.request.delivery_info or {}
    redelivered = bool(delivery.get("redelivered"))
    client.rpush(
        f"{key}:events",
        f"{self.request.id}:{int(redelivered)}",
    )
    client.expire(f"{key}:events", 120)
    time.sleep(duration_seconds)
    client.setex(f"{key}:done", 120, "1")
    return {"marker": marker, "redelivered": redelivered}


if __name__ == "__main__":
    # Publish readiness from the worker_ready signal, NOT before worker_main().
    # Setting it at import time only proves the module loaded: the test would
    # then publish its task and start a 15s clock while the worker was still
    # booting and consuming nothing. worker_ready fires once the consumer is
    # actually attached to the queue, which is what "ready" has to mean here.
    from celery.signals import worker_ready

    ready_key = os.environ.get("VISIBILITY_READY_KEY")

    @worker_ready.connect(weak=False)
    def _announce_ready(**_kwargs):  # pragma: no cover - subprocess-only path
        if not ready_key:
            return
        from redis import Redis

        Redis.from_url(os.environ["REDIS_URL"], decode_responses=True).setex(
            ready_key, 120, "1"
        )

    hostname = os.environ.get("VISIBILITY_WORKER_NAME", "ci-visibility")
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=WARNING",
            "--pool=solo",
            "--concurrency=1",
            f"--queues={_visibility_queue}",
            f"--hostname={hostname}",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
        ]
    )
