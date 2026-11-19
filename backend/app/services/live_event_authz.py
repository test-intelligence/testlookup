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


#: Prefix for cached credential->project entries. Shared with
#: ``forget_streaming_key_hash`` so revocation and lookup cannot drift apart.
STREAMING_KEY_PREFIX = "testlookup:live:key-project:"


def streaming_key_cache_key(api_key: str) -> str:
    """Namespace a credential by digest — never store the key itself.

    The digest is deliberately the same one ``ApiKey.key_hash`` stores, so a
    revocation holding only the hash can invalidate this entry.
    """
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    return f"{STREAMING_KEY_PREFIX}{digest}"


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
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    # An EMPTY entry is not an answer. Returning "" would be a project id the
    # caller never proved, and the handler's "did we resolve one?" test would
    # see a non-None value and skip the real credential check -- the same
    # falsy-versus-None confusion as the defects this batch is correcting.
    if not value:
        return None
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


async def forget_streaming_key_hash(key_hash: str) -> None:
    """Drop a cached credential immediately, by its stored digest.

    ``ApiKey.key_hash`` is ``sha256(raw_key)`` — the same digest
    ``streaming_key_cache_key`` builds from — so revocation can invalidate the
    entry without ever seeing the key again. Without this the documented 30
    second lag is not a worst case but a floor: a revoked key keeps working for
    the full TTL no matter how urgent the revocation was.
    """
    if not key_hash:
        return
    try:
        from app.db.redis_client import get_redis

        await get_redis().delete(f"{STREAMING_KEY_PREFIX}{key_hash}")
    except Exception as exc:  # noqa: BLE001 — revocation already succeeded
        logger.warning("live_key_cache_invalidate_failed error=%r", exc)


#: Deployment-wide binding: the legacy shared-secret path, which has no
#: server-derived project, and every binding written before bindings were
#: scoped to the project.
RUN_PROJECT_PREFIX = "testlookup:live:run-project:"

#: Project-scoped binding, written on the project-key path. A different prefix
#: (not a longer form of the one above) because ``run_id`` is caller-chosen and
#: may contain colons: a deployment-wide key built from a crafted run id can
#: then never equal another project's scoped key.
PROJECT_RUN_PREFIX = "testlookup:live:project-run:"


def run_project_key(run_id: str, project_id: Optional[str] = None) -> str:
    """Redis key binding ``run_id`` to a project.

    With ``project_id`` (the project of the API key that sent the event) the
    key is that project's own, so two projects can use the same run id without
    seeing or blocking each other. Without it the key is deployment-wide.
    """
    if project_id:
        return f"{PROJECT_RUN_PREFIX}{project_id}:{run_id}"
    return f"{RUN_PROJECT_PREFIX}{run_id}"


#: SET NX / GET is two round-trips; a key can vanish between them.
_BIND_ATTEMPTS = 3


class RunProjectBindingUnavailable(RuntimeError):
    """Redis could not answer while ESTABLISHING a binding.

    Raised only from ``remember_run_project``. A binding that is never written
    silently disables the cross-tenant check for that run's whole life, so the
    one moment it matters fails closed; reads stay fail-open (see
    ``resolve_run_project``).
    """


async def remember_run_project(
    run_id: str, project_id: str, *, project_scoped: bool = False
) -> Optional[str]:
    """Bind ``run_id`` to the project opening it; return the effective owner.

    ``project_scoped=True`` is for a caller whose project the SERVER derived
    (a project-bound API key). The binding then lives under that project's own
    key, so the answer is always the caller's project: another project using
    the same run id is neither refused nor revealed. Leave it False only for
    the legacy shared-secret path, where the project comes from the request
    body and the first ``run_start`` in the whole deployment wins.

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
        key = run_project_key(run_id, str(project_id) if project_scoped else None)
        incumbent = None
        # Two round-trips are not atomic: the key can expire or be evicted
        # between the SET and the GET. Retrying is the only answer that is
        # neither a lie nor an outage -- claiming ownership we never recorded
        # would leave the run UNBOUND with a success response, which is exactly
        # the state the 503 below exists to prevent.
        for _attempt in range(_BIND_ATTEMPTS):
            won = await redis.set(
                key,
                str(project_id),
                ex=RUN_PROJECT_TTL_SECONDS,
                nx=True,
            )
            if won:
                return str(project_id)
            incumbent = await redis.get(key)
            if incumbent is not None:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "live_run_project_bind_failed run_id=%s error=%r", run_id, exc
        )
        raise RunProjectBindingUnavailable(str(exc)) from exc

    if incumbent is None:
        # Lost the SET every time and still found nothing. We cannot say who
        # owns this run, and we did not record ourselves -- fail closed.
        logger.warning(
            "live_run_project_bind_unstable run_id=%s attempts=%d",
            run_id,
            _BIND_ATTEMPTS,
        )
        raise RunProjectBindingUnavailable(
            "could not establish run ownership after "
            f"{_BIND_ATTEMPTS} attempts"
        )
    if isinstance(incumbent, (bytes, bytearray)):
        incumbent = incumbent.decode("utf-8", "replace")
    return str(incumbent)


async def resolve_run_project(
    run_id: str, *, project_id: Optional[str] = None
) -> Optional[str]:
    """Return the project bound to ``run_id``, or None when unknown.

    None means "no opinion" — either the run predates the binding or Redis
    could not answer. Callers must treat it as "cannot verify", not as
    "verified different".

    ``project_id`` is the project of the API key that sent the event. That
    project's own binding is read first. Only when it has none is the
    deployment-wide key read: it holds runs started on the legacy
    shared-secret path and runs bound before bindings were project-scoped
    (those expire within ``RUN_PROJECT_TTL_SECONDS`` of the upgrade), and
    another project's event for such a run must still be refused.
    """
    if not run_id:
        return None
    try:
        from app.db.redis_client import get_redis

        redis = get_redis()
        value = None
        if project_id:
            value = await redis.get(run_project_key(run_id, str(project_id)))
        if value is None:
            value = await redis.get(run_project_key(run_id))
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
