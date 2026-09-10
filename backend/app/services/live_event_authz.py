"""Run-to-project binding for the single-event live ingestion endpoint.

``POST /ws/events/{run_id}`` is a thin producer: it must stay in the low
milliseconds, so it cannot afford a relational lookup per event. But it also
must not let a caller authorised for one project write events onto another
project's run (re-audit finding H1).

The binding is established once, on ``run_start``, and checked with a single
Redis GET on every later event for that run. Establishing it is the only moment
a project is named, so that is the only moment that needs to be authorised.

Failures here are non-fatal by design: Redis is already the transport for these
events, so if it is unavailable the publish will fail on its own and there is no
value in turning a binding read into a second, separate outage. What must never
happen is a *successful* read being ignored.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("services.live_event_authz")

# Matches the live buffer's own retention: a run's events cannot outlive the
# window in which its per-run buffer is drained.
RUN_PROJECT_TTL_SECONDS = 25 * 60 * 60


def run_project_key(run_id: str) -> str:
    return f"testlookup:live:run-project:{run_id}"


async def remember_run_project(run_id: str, project_id: str) -> None:
    """Bind ``run_id`` to the project that opened it.

    Uses SET NX so the FIRST ``run_start`` wins. A second ``run_start`` naming a
    different project cannot steal an established run.
    """
    if not run_id or not project_id:
        return
    try:
        from app.db.redis_client import get_redis

        await get_redis().set(
            run_project_key(run_id),
            str(project_id),
            ex=RUN_PROJECT_TTL_SECONDS,
            nx=True,
        )
    except Exception as exc:  # noqa: BLE001 — never break the ingest path
        logger.warning(
            "live_run_project_bind_failed run_id=%s error=%r", run_id, exc
        )


async def resolve_run_project(run_id: str) -> Optional[str]:
    """Return the project bound to ``run_id``, or None when unknown.

    None means "no opinion" — either the run predates the binding or Redis
    could not answer. Callers must treat it as "cannot verify", not as
    "verified different".
    """
    if not run_id:
        return None
    try:
        from app.db.redis_client import get_redis

        value = await get_redis().get(run_project_key(run_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "live_run_project_lookup_failed run_id=%s error=%r", run_id, exc
        )
        return None
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    return str(value)
