"""Production API process lifecycle hooks."""

from prometheus_client import multiprocess

bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"


def child_exit(_server, worker) -> None:
    """Remove live-gauge files for every reaped API worker, including crashes."""
    multiprocess.mark_process_dead(worker.pid)
