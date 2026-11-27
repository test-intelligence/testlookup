"""Run the candidate's real downstream relay schedule in the private fixture."""
from __future__ import annotations

import os
from pathlib import Path

RELAY_TASK = "app.worker.tasks.relay_run_downstream_outbox"


def relay_schedule(schedule):
    selected = {name: entry for name, entry in schedule.items() if entry.get("task") == RELAY_TASK}
    if len(selected) != 1:
        raise ValueError("candidate must define exactly one normal downstream relay schedule")
    return selected


def main():
    # The fixture mounts a fresh private /tmp. Preserve the image's normal
    # multiprocess-client initialization contract before task modules import it.
    directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    from celery.beat import PersistentScheduler
    from app.worker.celery_app import celery_app

    class RelayOnlyScheduler(PersistentScheduler):
        def install_default_entries(self, data):
            # No backend-cleanup or unrelated maintenance is added by defaults.
            return None

    celery_app.loader.import_default_modules()
    celery_app.finalize()
    celery_app.conf.beat_schedule = relay_schedule(celery_app.conf.beat_schedule)
    celery_app.Beat(
        loglevel="WARNING", scheduler=RelayOnlyScheduler,
        schedule="/tmp/monitored-downstream-relay", pidfile="",
    ).run()


if __name__ == "__main__":
    main()
