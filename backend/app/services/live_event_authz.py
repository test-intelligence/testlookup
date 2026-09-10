"""Run-to-project binding for the single-event live ingestion endpoint.

``POST /ws/events/{run_id}`` is a thin producer: it must stay in the low
milliseconds, so it cannot afford a relational lookup per event. But it also
must not let a caller authorised for one project write events onto another
project's run (re-audit finding H1).

The binding is established once, on ``run_start``, and checked with a single
Redis GET on every later event for that run. Establishing it is the only moment
a project is named, so that is the only moment that needs to be authorised.

A failed READ is non-fatal by design: Redis is already the transport for these
events, so if it is unavailable the publish fails on its own and there is no
value in turning a binding lookup into a second, separate outage. A read that
returns nothing means "cannot verify", never "verified different".

A failed WRITE is different and fails closed. Establishing the binding is the
single moment the tenant is decided; swallowing that error would leave the run
unbound and the cross-tenant check disabled for its entire 25-hour life, with
nothing in the response to say so.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

logger = logging.getLogger("services.live_event_authz")

# Matches the live buffer's own retention: a run's events cannot outlive the
# window in which its per-run buffer is drained.
RUN_PROJECT_TTL_SECONDS = 25 * 60 * 60


#: How long a verified streaming credential's project stays cached.
#:
#: Deliberately short. A cache hit skips the whole of
#: ``get_streaming_api_key_context`` — active flag, expiry, scope, and the
#: owner's account state — so this is also the worst-case lag between revoking
#: a key and the API refusing it. Thirty seconds buys back the two SELECTs and
#: the last_used_at UPDATE that would otherwise run on EVERY live event.
STREAMING_KEY_TTL_SECONDS = 30


def streaming_key_cache_key(api_key: str) -> str:
    """Namespace a credential by digest — never store the key itself."""
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    return f"testlookup:live:key-project:{digest}"


async def cached_streaming_project(api_key: str) -> Optional[str]:
    """Project last verified for ``api_key``, or None to check properly.

    None always means "ask the database", so a cache miss, a cold Redis and a
    Redis outage all degrade to the full check rather than to a bypass.
    """
    if not api_key:
        return None
    try:
        from app.db.redis_client import get_redis

        value = await get_redis().get(streaming_key_cache_key(api_key))
    except Exception as exc:  # noqa: BLE001 — a cache miss is never fatal
        logger.warning("live_key_cache_lookup_failed error=%r", exc)
        return None
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    return str(value)


async def remember_streaming_project(api_key: str, project_id: str) -> None:
    """Cache a credential that the full check has just accepted."""
    if not api_key or not project_id:
        return
    try:
        from app.db.redis_client import get_redis

        await get_redis().set(
            streaming_key_cache_key(api_key),
            str(project_id),
            ex=STREAMING_KEY_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("live_key_cache_store_failed error=%r", exc)


def run_project_key(run_id: str) -> str:
    return f"testlookup:live:run-project:{run_id}"


class RunProjectBindingUnavailable(RuntimeError):
    """Redis could not answer while ESTABLISHING a binding.

    Raised only from ``remember_run_project``. A binding that is never written
    silently disables the cross-tenant check for that run's whole life, so the
    one moment it matters fails closed; reads stay fail-open (see
    ``resolve_run_project``).
    """


async def remember_run_project(run_id: str, project_id: str) -> Optional[str]:
    """Bind ``run_id`` to the project opening it; return the effective owner.

    Uses SET NX so the FIRST ``run_start`` wins: a later one naming a different
    project cannot steal an established run. The return value is the project
    that actually owns the run afterwards — the caller's own when the bind won,
    the incumbent when it lost — so a caller that lost can be told immediately
    rather than discovering it as a wall of 403s on every subsequent event.

    ``run_id`` is caller-chosen (``validate_live_identifier`` accepts any
    printable string), so two projects picking the same one — "build-42" is not
    imaginative — is an ordinary collision, not an attack. It has to surface.

    Returns None only when there is nothing to bind.
    """
    if not run_id or not project_id:
        return None
    try:
        from app.db.redis_client import get_redis

        redis = get_redis()
        won = await redis.set(
            run_project_key(run_id),
            str(project_id),
            ex=RUN_PROJECT_TTL_SECONDS,
            nx=True,
        )
        if won:
            return str(project_id)
        incumbent = await redis.get(run_project_key(run_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "live_run_project_bind_failed run_id=%s error=%r", run_id, exc
        )
        raise RunProjectBindingUnavailable(str(exc)) from exc

    if incumbent is None:
        # The key expired between the SET and the GET. Nothing owns the run.
        return str(project_id)
    if isinstance(incumbent, (bytes, bytearray)):
        incumbent = incumbent.decode("utf-8", "replace")
    return str(incumbent)


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
