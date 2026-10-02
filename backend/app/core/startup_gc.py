"""Move the start-up heap out of CPython's cyclic garbage collector.

Why
---
An API worker finishes start-up holding ~700,000 GC-tracked objects: modules,
classes, functions, the pydantic/SQLAlchemy/FastAPI schemas and the route
table. Nearly all of them live as long as the process. CPython's collector does
not know that, so every full (generation-2) collection walks all of them again.
The Wave 3 perf work measured what that costs: one full collection over a
~710,000-735,000-object heap took 180-260 ms, it ran about every 7-12 requests
of a 3,520-row heatmap, and it was the whole p95 tail of those responses on
Linux (every outlier contained exactly one gen-2 collection).

``gc.freeze()`` moves every tracked object into the permanent generation, which
no collection ever examines. Collecting first means we freeze live objects,
not garbage the collector would have reclaimed. After this, a full collection
only walks what was allocated since start-up.

What it does not change
-----------------------
* Reference counting still frees frozen objects whose count drops to zero. Only
  a *cycle* among frozen objects is never reclaimed -- acceptable for
  import-time objects, which live for the life of the worker anyway.
* The thresholds stay CPython's defaults (700, 10, 10). Measured with the
  freeze, a generation-0 threshold of 10,000 cut GC time from ~1.6 to ~0.7 ms
  per 200 ms heatmap request and left p50/p95 inside run-to-run noise, while
  letting up to 10,000 dead cycles linger between collections. Not worth it.

Where it is called
------------------
At the end of the FastAPI lifespan start-up (``app.main.lifespan``), which
runs once in every API process: each gunicorn ``UvicornWorker`` imports the app
after the fork (``preload_app`` is off), so a ``post_fork``/``when_ready`` hook
would run before the heap exists.

Not in Celery workers: a worker parent forks its prefork children with ~56,500
tracked objects (the task modules import their services lazily), where a full
collection takes ~4-5 ms, against ~700,000 in an API worker. A freeze
in ``worker_process_init`` would come before the lazy imports, so it would
freeze almost nothing, and tasks are minutes-long batch jobs with no latency
budget whose children are recycled every 200 tasks.
"""
from __future__ import annotations

import gc

import structlog

logger = structlog.get_logger(__name__)

_frozen_in_this_process = False


def freeze_startup_heap() -> int | None:
    """Collect, then freeze, the heap built during start-up -- once per process.

    Returns the number of objects in the permanent generation afterwards, or
    ``None`` when this process already froze (a second call must not move the
    objects allocated since the first into a generation that is never
    collected).
    """
    global _frozen_in_this_process
    if _frozen_in_this_process:
        return None
    _frozen_in_this_process = True
    gc.collect()
    gc.freeze()
    frozen = gc.get_freeze_count()
    logger.info("startup_heap_frozen", frozen_objects=frozen, gc_thresholds=gc.get_threshold())
    return frozen
