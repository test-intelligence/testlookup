"""VIZ-209 -- the bounded-read layer every analytics GET goes through.

One decorator, :func:`analytics_read`, wraps an endpoint and gives it four
things the analytics surfaces had none of: a Redis cache with conditional
requests, a statement timeout that cannot leak, a per-principal rate limit and
a latency histogram.

**Opt-in, not global.** VIZ-203's ``/analytics/chart-data`` (and VIZ-205's
heatmap, VIZ-208's rows) are not in the tree yet; a middleware keyed on a path
prefix would either miss them or catch routes that must not be cached (the
POSTs on the same router). A decorator applies the moment a route lands --
``@analytics_read(namespace="chart_data", ...)`` above the handler -- and says
so in the source of the route it protects. :data:`RATE_LIMITED_ROUTES` names
the three routes the story rate-limits, so the limit is configured for them
before they exist.

Cache identity (the story's key)::

    key    = analytics:{namespace}:{project|all}:e{epoch}:k={digest}
    digest = sha256(route, schema_version, RESOLVED request identity,
                    user scope class, project epoch)

* **route** -- the path template, so two routes never share an entry.
* **schema_version** -- this layer's plus ``META_SCHEMA_VERSION``: a payload
  cached in an older envelope shape is never served as the new one.
* **the resolved request** -- NOT the raw query string. A route supplies an
  ``identity`` hook returning the canonical parts of the request it actually
  answered (``chart_data_service.cache_identity_parts(scope, spec)``), and the
  digest keys on those. Keying on the query string sorted every repeated
  parameter's values, which is wrong twice: ``group_by`` is ORDER-SIGNIFICANT
  (first = x axis, second = series key), so ``?group_by=status&group_by=env``
  and its reverse -- two different charts, transposes of each other -- hashed
  to ONE digest and ONE ETag, and a member was served an admin's transposed
  chart and then 304'd on revalidation. And a repeated SCALAR
  (``metric=failed&metric=passed``) binds "last wins" but keyed "sorted", so a
  co-tenant could poison the entry another caller reads. Without a hook the
  fallback keeps VALUE ORDER for :data:`ORDER_SIGNIFICANT_PARAMS` and refuses
  (422) a repeated scalar the route binds as a single value -- the shape
  ``analytics_scope.parse_project_id(..., repeated=…)`` already had for
  ``project_id``, generalised to every declared scalar.
* **user scope class** -- ``project:<id>`` when the request pinned a project
  (the payload is that project's data, identical for every caller who is
  allowed to ask), else the caller's ACCESSIBLE PROJECT SET: an all-projects
  answer mixes exactly the projects the caller can read, so two callers with
  different memberships must never share an entry -- or an ETag.
* **project epoch** -- VIZ-212's counter, already in the key ``cache_set``
  builds. A mutation bumps it, the next read misses and gets a new ETag.

``epoch is None`` (Redis unreachable, a counter we cannot interpret, or a read
that did not come back inside
``cache_service.ANALYTICS_READ_TIMEOUT_SECONDS``) means the cache is bypassed
entirely and the request is served from the database -- never 500, never a
value whose epoch is unknown.

**No scope, no cache.** The cache class of a caller comes from the request's
:class:`AnalyticsScope`. A decorated handler that does not hand one to this
layer used to collapse into a single ``"anonymous-scope"`` class shared by
every caller -- with cache on by default, a cross-tenant leak waiting for one
renamed parameter. It now FAILS CLOSED: no scope, no cache entry read and none
written (counted as ``analytics_read_degraded_total{reason="no_scope"}``), and
:func:`test_every_decorated_route_has_an_analytics_scope` keeps it from
happening in the first place.

**ETag.** ``sha256(digest + canonical body)``, where the canonical body is the
payload WITHOUT ``meta.generated_at``. A hit replays the cached bytes with
``generated_at`` restamped to this response's moment (``as_of`` -- when the
numbers were READ -- is left exactly as it was, so a hit shows
``as_of < generated_at`` instead of claiming a five-minute-old body was
generated now), and because ``generated_at`` is deliberately kept OUT of the
ETag material, the restamped body still carries the SAME ETag: ``If-None-Match``
inside the TTL is still a 304 with no body. Everything else about the payload
IS in the material, ``as_of`` included, and so is the digest -- so an epoch
bump produces a new ETag even when the recomputed payload is identical.

**Statement timeout.** ``SET LOCAL statement_timeout`` runs inside the
request's transaction, so it is discarded by the COMMIT/ROLLBACK that ends the
request and cannot ride a pooled connection into the next one -- and it is
applied HERE, on the decorated read, so ingestion (which never wears this
decorator) keeps the server default. A cancelled query (SQLSTATE 57014) is a
503 ``analytics_timeout`` carrying ``Retry-After``; the session is rolled back
so the very next request on that connection is healthy.

**Rate limit.** slowapi, bucketed per principal and path. Storage is per
process -- ``Limiter`` is built without a ``storage_uri``, so with 4 workers a
pod's effective ceiling is up to 4x the number, and N pods multiply it again.
That is approximate BY DESIGN: this is a courtesy brake on one busy page, not
a quota. The limiters are built once per (route, limit) and cached; rebuilding
one per request is the defect ``_build_auth_limiters`` documents (slowapi
appends to ``_route_limits`` keyed on ``module.name``, so re-decorating makes
the ceiling collapse quadratically).

The limit is counted BEFORE the scope is resolved. FastAPI resolves an
endpoint's dependencies in signature order, so the layer injects its own
``analytics_gate`` dependency AHEAD of the route's ``scope`` parameter (every
other parameter is rewritten keyword-only so the gate can be first). The gate
enforces the limit, refuses a repeated scalar and applies the statement
timeout; only then does ``analytics_scope`` run its ``resolve_project_scope``
and release queries. Counting inside the handler instead meant every 403, 404
and 422 was free, unlimited database work: 12 forbidden and 6 malformed
requests cost a principal nothing and were served while it was over the limit.
Authentication brute force is still the auth limiter's job (``app.main``); this
one bounds the analytics work a principal can ask for, refused or not.

**Direct calls.** Several regression tests call these handlers as plain
functions (``analytics.flaky_scores(limit=50, scope=..., db=...)``). The
injected parameters are then absent, and the wrapper calls the endpoint
straight through and returns its dict -- the layer is a *transport* concern.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import re
import time
import types
import typing
from typing import Any, Callable, Optional, Sequence, TypeVar

import structlog
from fastapi import Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_errors import AnalyticsQueryError

logger = structlog.get_logger("core.analytics_read")

_F = TypeVar("_F", bound=Callable[..., Any])

#: Bumped when the CACHED SHAPE this layer stores changes (the entry envelope,
#: the digest recipe). A deploy then reads no entry written by the old code.
ANALYTICS_READ_SCHEMA_VERSION = 1

#: Default TTL. Short enough that an epoch bump is the invalidation that
#: matters and the TTL is only the backstop for a bump that could not be made.
ANALYTICS_CACHE_TTL_SECONDS = 300

#: The analytics statement timeout, in milliseconds (the story's 5 s).
ANALYTICS_STATEMENT_TIMEOUT_MS = 5_000

#: Seconds a 503 ``analytics_timeout`` asks the client to wait.
ANALYTICS_TIMEOUT_RETRY_AFTER_SECONDS = 5

#: The default per-principal limit for a decorated route.
ANALYTICS_RATE_LIMIT = "120/minute"

#: The three routes the story rate-limits by name. They are declared here so
#: the limit exists the moment VIZ-203/205/208 land; a route not listed uses
#: :data:`ANALYTICS_RATE_LIMIT` when it asks for a limit at all.
RATE_LIMITED_ROUTES: dict[str, str] = {
    "/api/v1/analytics/chart-data": "120/minute",
    "/api/v1/analytics/chart-data/rows": "120/minute",
    "/api/v1/analytics/heatmap": "60/minute",
}

#: PostgreSQL's "query canceled" -- what ``statement_timeout`` raises.
STATEMENT_TIMEOUT_SQLSTATE = "57014"

#: Query parameters whose VALUE ORDER changes the answer, for a route that
#: supplies no ``identity`` hook. ``group_by`` is the one the analytics
#: surfaces have: the first dimension is the x axis and the second keys the
#: series, so the two orders are transposes of each other -- different charts,
#: which must never share a cache entry or an ETag. The default applies to
#: EVERY hookless route on purpose: a route that lands later with a ``group_by``
#: and forgets its hook is still keyed correctly.
ORDER_SIGNIFICANT_PARAMS: frozenset = frozenset({"group_by"})

#: Single-valued parameters that already refuse repetition with their own C1
#: rule id, so the generic ``repeated_parameter`` must not preempt them.
#: ``project_id`` is ``analytics_scope.parse_project_id(..., repeated=…)``.
SELF_POLICED_PARAMS: frozenset = frozenset({"project_id"})

#: Names of the parameters this layer injects into a decorated endpoint's
#: signature. They are popped before the endpoint is called, so an endpoint
#: never sees them. ``_GATE`` is the pre-scope dependency (rate limit,
#: repeated-scalar refusal, statement timeout) and carries no value.
_REQ, _DB, _USER, _GATE = "_viz_request", "_viz_db", "_viz_user", "_viz_gate"

_CACHE_HEADER = "X-Analytics-Cache"
_CACHE_CONTROL = "private, max-age=0, must-revalidate"

#: A cached analytics body is one caller's data horizon. ``private`` already
#: forbids a shared cache from storing it; ``Vary`` says the same thing to the
#: caches that store private responses anyway (and to a browser holding two
#: sessions), naming the two headers the answer actually depends on.
_VARY = "Authorization, Cookie"


# ── The decorator ───────────────────────────────────────────────────────────


#: "whatever the module constant says when the request arrives". The three
#: knobs are read per request, not captured at import: a decorator argument
#: evaluated at decoration time cannot be tuned (or tested) without a redeploy.
DEFAULT = "__viz209_default__"


def analytics_read(
    *,
    namespace: str,
    ttl: Any = DEFAULT,
    cache: bool = True,
    rate_limit: Any = DEFAULT,
    timeout_ms: Any = DEFAULT,
    identity: Optional[Callable[[dict], Sequence[str]]] = None,
    order_significant: Any = DEFAULT,
) -> Callable[[_F], _F]:
    """Wrap an analytics GET in the VIZ-209 layer.

    ``namespace`` is the cache namespace (``analytics:{namespace}:...``), so it
    must be unique per route. ``rate_limit=None`` turns the limit off for a
    route that is not worth one; anything left at :data:`DEFAULT` follows the
    module constant at request time.

    ``identity`` is the per-route cache-identity hook: it is handed the
    RESOLVED endpoint arguments (the scope object included, exactly as the
    handler will see them) and returns the canonical parts of the request the
    route is about to answer. ``/chart-data`` passes
    ``chart_data_service.cache_identity_parts(scope, spec)``, which is the
    order-preserving identity that service owns -- the layer consumes it rather
    than re-deriving a second, disagreeing one from the query string. A route
    without a hook is keyed on its query parameters, order preserved for
    ``order_significant`` (default :data:`ORDER_SIGNIFICANT_PARAMS`).
    """

    def decorate(endpoint: _F) -> _F:
        original = list(inspect.signature(endpoint).parameters.values())
        single_valued, _repeatable = declared_params(endpoint)
        ordered = (
            ORDER_SIGNIFICANT_PARAMS
            if order_significant is DEFAULT
            else frozenset(order_significant)
        )

        @functools.wraps(endpoint)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            kwargs.pop(_GATE, None)  # the gate's value is its side effects
            request = kwargs.pop(_REQ, None)
            db = kwargs.pop(_DB, None)
            user = kwargs.pop(_USER, None)
            if not isinstance(request, Request):
                # A direct call (a test, a job): no transport, no layer.
                return await endpoint(*args, **kwargs)
            return await _serve(
                endpoint,
                args,
                kwargs,
                request=request,
                db=db if isinstance(db, AsyncSession) else None,
                user=user,
                namespace=namespace,
                ttl=ttl,
                cache=cache,
                rate_limit=rate_limit,
                timeout_ms=timeout_ms,
                identity=identity,
                order_significant=ordered,
                single_valued=single_valued,
            )

        gate = _gate_dependency(
            namespace,
            rate_limit=rate_limit,
            timeout_ms=timeout_ms,
            single_valued=single_valued,
        )
        wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            _signature_params(original, gate)
        )
        setattr(wrapper, "__analytics_read__", namespace)
        return wrapper  # type: ignore[return-value]

    return decorate


def _signature_params(
    original: list[inspect.Parameter], gate: Callable[..., Any]
) -> list[inspect.Parameter]:
    """The signature FastAPI reads, with the gate FIRST.

    ``solve_dependencies`` awaits ``dependant.dependencies`` in the order
    ``get_dependant`` built them, which is the order of the signature's
    parameters -- so the gate is only ahead of the route's ``scope`` if it is
    ahead of it here. ``inspect.Signature`` orders by parameter KIND, so the
    route's own parameters are rewritten KEYWORD_ONLY: that is what lets a
    defaulted gate precede a parameter that has no default, and FastAPI calls
    every endpoint with keyword arguments anyway. It changes nothing at
    runtime -- ``wrapper(*args, **kwargs)`` forwards whatever it is given, so
    a direct positional call still works.
    """
    kw = inspect.Parameter.KEYWORD_ONLY
    if any(
        param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD) for param in original
    ):
        # ``*args``/``**kwargs`` cannot be reordered into keyword-only. No
        # analytics route is written that way; if one ever is, it keeps the
        # old layout and ``_serve`` enforces the limit itself (later, but not
        # never).
        return original + _injected()
    gate_param = inspect.Parameter(_GATE, kw, default=Depends(gate))
    return [gate_param] + [param.replace(kind=kw) for param in original] + _injected()


def _gate_dependency(
    namespace: str, *, rate_limit: Any, timeout_ms: Any, single_valued: frozenset
) -> Callable[..., Any]:
    """The dependency that runs BEFORE the route's scope is resolved."""
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    async def analytics_gate(
        request: Request,
        db: AsyncSession = Depends(get_db),
        user: Any = Depends(get_current_active_user),
    ) -> None:
        await open_read(
            request,
            user,
            db,
            rate_limit=rate_limit,
            timeout_ms=timeout_ms,
            single_valued=single_valued,
        )

    analytics_gate.__name__ = "analytics_gate_" + re.sub(r"[^a-z0-9]+", "_", namespace.lower())
    return analytics_gate


async def open_read(
    request: Request,
    user: Any,
    db: Optional[AsyncSession],
    *,
    rate_limit: Any,
    timeout_ms: Any,
    single_valued: frozenset = frozenset(),
) -> None:
    """Charge the request, refuse what is malformed, bound what it may run.

    In this order deliberately: the limit is charged FIRST, so a request the
    next line refuses still counts against the principal's budget -- an
    attacker cannot buy unlimited refusals. The statement timeout is applied
    last and before the caller returns, so the scope dependency's OWN queries
    (``resolve_project_scope``, the release ``IN`` lookup) already run under
    it rather than only the handler's.
    """
    route = route_of(request)
    limit = ANALYTICS_RATE_LIMIT if rate_limit is DEFAULT else rate_limit
    ms = ANALYTICS_STATEMENT_TIMEOUT_MS if timeout_ms is DEFAULT else int(timeout_ms)
    await enforce_rate_limit(request, user, route=route, limit=limit)
    request.state.analytics_read_opened = True
    reject_repeated_scalars(request, single_valued)
    await apply_statement_timeout(db, ms)


def reject_repeated_scalars(request: Request, single_valued: frozenset) -> None:
    """422 a parameter the route binds as ONE value but was sent twice.

    ``?metric=failed&metric=passed`` binds "last wins", so the answer is one
    chart -- while a key built from the sorted values is the same for both
    orders. A co-tenant sending the reverse order then writes the entry the
    first caller reads back. Refusing is the only answer that is true to what
    the route does with the parameter.
    """
    params = request.query_params
    for name in sorted(single_valued - SELF_POLICED_PARAMS):
        if len(params.getlist(name)) > 1:
            raise AnalyticsQueryError(
                "repeated_parameter",
                f"{name} is single-valued: send it once. Repeating it would bind "
                "only the last value, so a request that reads like two filters "
                "is answered as one.",
                param=name,
                allowed={"max": 1},
            )


# ── What the route declares ────────────────────────────────────────────────

#: Parameters FastAPI resolves from the transport, never from the query string.
_TRANSPORT_ANNOTATIONS = (Request, Response, AsyncSession)


def _query_name(param: inspect.Parameter) -> str:
    """The name on the wire: ``Query(..., alias="from")`` is sent as ``from``."""
    alias = getattr(param.default, "alias", None)
    return str(alias) if alias else param.name


def _is_repeatable(annotation: Any) -> bool:
    """True for ``list[str]``, ``Optional[list[str]]``, ``str | None`` list
    unions -- the annotations FastAPI reads as "send this as often as you
    like"."""
    pending = [annotation]
    while pending:
        current = pending.pop()
        origin = typing.get_origin(current)
        if origin is typing.Union or origin is types.UnionType:
            pending.extend(typing.get_args(current))
            continue
        if origin in (list, set, tuple, frozenset) or current in (
            list, set, tuple, frozenset,
        ):
            return True
    return False


def _declares_a_query_param(param: inspect.Parameter) -> bool:
    """A parameter FastAPI fills from the QUERY STRING.

    A ``Query(...)`` default, or a plain default with no ``FieldInfo`` at all
    (FastAPI's own rule for a scalar parameter). Explicitly NOT ``Header``,
    ``Cookie``, ``Path``, ``Body``, ``Form`` or ``File``: the auth chain
    declares ``x_api_key: Optional[str] = Header(...)``, and reading that as a
    query parameter would let this layer refuse a query string that merely
    shares its name.
    """
    from fastapi import params as fastapi_params

    default = param.default
    if isinstance(default, fastapi_params.Query):
        return True
    return not isinstance(default, fastapi_params.Param) and not isinstance(
        default, fastapi_params.Body
    )


def declared_params(endpoint: Callable[..., Any]) -> tuple[frozenset, frozenset]:
    """``(single-valued, repeatable)`` query parameter names of a route.

    The endpoint's own signature plus every ``Depends`` below it -- the scope
    dependency declares ``days`` and ``release_id``, and they are as much this
    route's parameters as the ones written on the handler. A name that is
    repeatable ANYWHERE wins: two declarations disagreeing is not a reason to
    refuse a caller.
    """
    single: set = set()
    repeated: set = set()
    seen: set = set()

    def walk(call: Any) -> None:
        if call is None or id(call) in seen:
            return
        seen.add(id(call))
        try:
            parameters = list(inspect.signature(call).parameters.values())
        except (TypeError, ValueError):  # a builtin, or a signature we cannot read
            return
        for param in parameters:
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            dependency = getattr(param.default, "dependency", None)
            if dependency is not None:
                walk(dependency)
                continue
            annotation = param.annotation
            if isinstance(annotation, type) and issubclass(annotation, _TRANSPORT_ANNOTATIONS):
                continue
            if not _declares_a_query_param(param):
                continue
            (repeated if _is_repeatable(annotation) else single).add(_query_name(param))

    walk(endpoint)
    return frozenset(single - repeated), frozenset(repeated)


def _injected() -> list[inspect.Parameter]:
    """The parameters FastAPI resolves for the layer.

    ``get_db`` and ``get_current_active_user`` are the same dependencies the
    endpoint (and the scope dependency) already declare, and FastAPI caches a
    dependency per request -- so this is the endpoint's own session and the
    already-authenticated user, not a second resolution.
    """
    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    kw = inspect.Parameter.KEYWORD_ONLY
    return [
        inspect.Parameter(_REQ, kw, annotation=Request, default=None),
        inspect.Parameter(_DB, kw, annotation=AsyncSession, default=Depends(get_db)),
        inspect.Parameter(_USER, kw, default=Depends(get_current_active_user)),
    ]


# ── The request path ────────────────────────────────────────────────────────


async def _serve(
    endpoint: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    *,
    request: Request,
    db: Optional[AsyncSession],
    user: Any,
    namespace: str,
    ttl: Any,
    cache: bool,
    rate_limit: Any,
    timeout_ms: Any,
    identity: Optional[Callable[[dict], Sequence[str]]] = None,
    order_significant: Any = None,
    single_valued: frozenset = frozenset(),
) -> Any:
    ttl = ANALYTICS_CACHE_TTL_SECONDS if ttl is DEFAULT else int(ttl)
    timeout_ms = ANALYTICS_STATEMENT_TIMEOUT_MS if timeout_ms is DEFAULT else int(timeout_ms)
    rate_limit = ANALYTICS_RATE_LIMIT if rate_limit is DEFAULT else rate_limit
    route = route_of(request)
    if not getattr(request.state, "analytics_read_opened", False):
        # No gate ran: a direct ``_serve`` call, or an endpoint whose signature
        # could not be reordered. Later than the gate, but not never.
        await enforce_rate_limit(request, user, route=route, limit=rate_limit)
        reject_repeated_scalars(request, single_valued)

    scope = _scope_of(kwargs)
    project_id = scope.project if scope is not None else None
    started = time.perf_counter()

    cacheable = cache
    if cache and scope is None:
        # FAIL CLOSED. Without a scope every caller shares one cache class,
        # which is a cross-tenant leak, not a cache miss.
        cacheable = False
        count_degraded("no_scope")
        logger.warning(
            "analytics_read_uncacheable_no_scope", route=route, namespace=namespace
        )

    identity_parts = None if identity is None else list(identity(kwargs))

    epoch: Optional[int] = None
    if cacheable:
        from app.services.cache_service import get_analytics_epoch

        # Read ONCE, before the query, and reuse for cache_set (VIZ-212).
        epoch = await get_analytics_epoch(project_id)
    digest = cache_digest(
        route=route,
        request=request,
        scope=scope,
        epoch=epoch,
        identity_parts=identity_parts,
        order_significant=order_significant,
    )

    if cacheable:
        from app.services.cache_service import cache_get

        entry = await cache_get(namespace, project_id, epoch=epoch, k=digest)
        cached_body, etag = _entry_parts(entry)
        if cached_body is not None and etag is not None:
            observe(route, "hit", time.perf_counter() - started)
            # The ETag is the one the miss computed, over the body WITHOUT
            # ``generated_at`` -- restamping does not invalidate it, so a
            # revalidation inside the TTL is still a 304.
            return _conditional(request, etag=etag, body=restamp(cached_body), cached=True)

    await apply_statement_timeout(db, timeout_ms)
    try:
        result = await endpoint(*args, **kwargs)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        if is_statement_timeout(exc):
            observe(route, "timeout", elapsed)
            await _rollback(db)
            logger.warning(
                "analytics_statement_timeout", route=route, timeout_ms=timeout_ms
            )
            raise AnalyticsQueryError(
                "analytics_timeout",
                (
                    f"The analytics query exceeded the {timeout_ms / 1000:g}s limit. "
                    f"Retry in {ANALYTICS_TIMEOUT_RETRY_AFTER_SECONDS}s, or narrow the "
                    "window (fewer days) or the filters."
                ),
                status_code=503,
                headers={"Retry-After": str(ANALYTICS_TIMEOUT_RETRY_AFTER_SECONDS)},
            ) from exc
        observe(route, "error", elapsed)
        raise
    observe(route, "miss", time.perf_counter() - started)

    if isinstance(result, Response):
        # A route that builds its own response owns its headers; nothing to
        # cache and nothing to compare.
        return result

    body = render(result)
    # The ETag is computed over the payload WITHOUT ``meta.generated_at``: that
    # field is "when THIS response was built", and folding it in would make
    # every hit carry a tag no client could ever revalidate against. The bytes
    # STORED keep it in place, so restamping a hit only rewrites a value that
    # is already there and cannot reorder the JSON a client diffs.
    etag = etag_for(digest, canonical_body(result, body))
    if cacheable and epoch is not None:
        from app.services.cache_service import cache_set

        await cache_set(
            namespace,
            {"etag": etag, "json": body, "v": ANALYTICS_READ_SCHEMA_VERSION},
            project_id,
            ttl=ttl,
            epoch=epoch,
            k=digest,
        )
    return _conditional(request, etag=etag, body=body, cached=False)


def _entry_parts(entry: Any) -> tuple[Optional[str], Optional[str]]:
    """The body and ETag of a cache entry, or ``(None, None)`` for anything
    that is not one this version wrote (an entry from an older shape is a
    miss, never a half-served payload)."""
    if not isinstance(entry, dict):
        return None, None
    if entry.get("v") != ANALYTICS_READ_SCHEMA_VERSION:
        return None, None
    body, etag = entry.get("json"), entry.get("etag")
    if isinstance(body, str) and isinstance(etag, str) and etag:
        return body, etag
    return None, None


def _scope_of(kwargs: dict) -> Any:
    from app.services.analytics_scope import AnalyticsScope

    scope = kwargs.get("scope")
    return scope if isinstance(scope, AnalyticsScope) else None


def route_of(request: Request) -> str:
    """The path TEMPLATE (``/api/v1/analytics/chart-data``), never the raw path
    -- a metric label and a cache namespace must not carry an id."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or request.url.path)


# ── Cache identity and ETag ────────────────────────────────────────────────


def user_scope_class(scope: Any) -> list:
    """The caller's data horizon, as cache-key material.

    A pinned project: the project. Everything else: the caller's accessible
    project set, sorted -- an all-projects payload is exactly the union of the
    projects that caller can read, so an admin's answer must never be served
    (or revalidated with a 304) to a member of one project. ``["all", "*"]``
    is the unrestricted (admin) horizon, which is a class of its own.
    """
    if scope is None:
        return ["anonymous-scope"]
    if scope.project_id is not None:
        return ["project", str(scope.project_id)]
    allowed = scope.allowed_project_ids
    if allowed is None:
        return ["all", "*"]
    return ["all", *sorted(str(pid) for pid in allowed)]


def canonical_scope(request: Request, *, order_significant: Any = None) -> list:
    """Every query parameter, each name once.

    Values are sorted -- ``?release_id=a&release_id=b`` and the reverse are one
    question -- EXCEPT for a name in ``order_significant``, where the order the
    caller sent IS the question (``group_by``: first dimension is the x axis,
    second keys the series). Sorting those made two different charts share one
    entry and one ETag.
    """
    ordered = (
        ORDER_SIGNIFICANT_PARAMS
        if order_significant is None
        else frozenset(order_significant)
    )
    params = request.query_params
    return [
        [name, params.getlist(name) if name in ordered else sorted(params.getlist(name))]
        for name in sorted(set(params.keys()))
    ]


def cache_digest(
    *,
    route: str,
    request: Request,
    scope: Any,
    epoch: Optional[int] = None,
    identity_parts: Optional[Sequence[str]] = None,
    order_significant: Any = None,
) -> str:
    """The hash half of the cache key -- and the ETag's identity half.

    The epoch is in here even though ``cache_set``'s own key builder also puts
    it in the Redis key: the ETag is derived from this digest, and a bump whose
    recomputed payload happens to be byte-identical must still produce a NEW
    ETag. Otherwise a client revalidating with the pre-mutation tag is told
    "not modified" about a snapshot that is not the one it holds.
    """
    from app.services.analytics_meta import META_SCHEMA_VERSION

    identity = [
        route,
        [ANALYTICS_READ_SCHEMA_VERSION, META_SCHEMA_VERSION],
        # The route's OWN identity for the request it resolved, when it has
        # one; otherwise the query string, canonicalised.
        list(identity_parts)
        if identity_parts is not None
        else canonical_scope(request, order_significant=order_significant),
        user_scope_class(scope),
        bool(getattr(scope, "denied", False)),
        # ``None`` = the epoch could not be read; the answer is then uncached
        # and every such response shares the "unknown epoch" identity.
        epoch,
    ]
    blob = json.dumps(identity, separators=(",", ":"), ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def render(result: Any) -> str:
    """The response body, exactly as FastAPI's ``JSONResponse`` would render
    it, so caching changes no byte of any payload."""
    return json.dumps(
        jsonable_encoder(result),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def canonical_body(result: Any, body: str) -> str:
    """The bytes the ETag is computed over: ``result`` without
    ``meta.generated_at``. Identical to ``body`` for a payload that has no
    such field, so nothing is re-rendered for the routes without an envelope."""
    from app.services.analytics_meta import without_generated_at

    stripped = without_generated_at(result)
    return body if stripped is result else render(stripped)


def restamp(body: str) -> str:
    """A cached body with ``meta.generated_at`` moved to now.

    ``as_of`` -- the moment the NUMBERS were read -- is deliberately left
    alone: that is what the cached bytes are, and overwriting it would be the
    same lie in the other direction. The field is rewritten IN PLACE (it is
    already in the stored bytes), so key order does not change between a miss
    and a hit.
    """
    from app.services.analytics_meta import restamped

    try:
        payload = json.loads(body)
    except ValueError:  # not JSON we wrote; serve it unchanged
        return body
    fresh = restamped(payload)
    return body if fresh is payload else render(fresh)


def etag_for(digest: str, body: str) -> str:
    """The ETag: sha256 over the identity AND the canonical bytes.

    The bytes alone would collide across epochs when a mutation did not change
    the numbers -- the story requires a NEW ETag after a bump, because a client
    holding the old one must not be told "not modified" about a different
    snapshot.

    ``body`` here is :func:`canonical_body` -- the payload apart from
    ``meta.generated_at``, which is when THIS response was built and moves on
    every request by definition. Folding it in would give every response a tag
    no client could ever match, which is a cache that revalidates nothing. So
    two responses with one tag can differ in that one field: the tag is
    semantically weak about ``generated_at`` and exact about everything else,
    ``as_of`` included. It is kept in the STRONG syntax deliberately -- it is
    only ever used for ``If-None-Match`` on a whole GET, never for a Range,
    and ``W/`` would read as "this payload may differ in ways that matter".
    """
    material = f"{digest}:{body}".encode("utf-8")
    return '"' + hashlib.sha256(material).hexdigest()[:32] + '"'


def if_none_match(header: Optional[str], etag: str) -> bool:
    """RFC 9110 If-None-Match: ``*``, or any listed tag equal to ours with the
    weak prefix ignored on both sides."""
    if not header:
        return False
    header = header.strip()
    if header == "*":
        return True
    wanted = etag[2:] if etag.startswith("W/") else etag
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate and candidate == wanted:
            return True
    return False


def cache_header_enabled() -> bool:
    """Whether to tell the caller ``X-Analytics-Cache: hit|miss``.

    Off in production. It is a diagnostic, and in production it is also an
    oracle: the cache class is shared by every caller with the same project
    horizon, so a "hit" on a request you have never made says someone else
    with your access made it. Every environment a developer or a test drives
    still gets the header, which is where it is read.
    """
    try:
        from app.core.config import settings

        return not settings.is_production
    except Exception:  # noqa: BLE001 - a header is not worth a 500
        return True


def _conditional(request: Request, *, etag: str, body: str, cached: bool) -> Response:
    headers = {
        "ETag": etag,
        "Cache-Control": _CACHE_CONTROL,
        "Vary": _VARY,
    }
    if cache_header_enabled():
        headers[_CACHE_HEADER] = "hit" if cached else "miss"
    if if_none_match(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


# ── Statement timeout ───────────────────────────────────────────────────────


def _dialect_of(db: Any) -> str:
    for getter in (lambda: db.bind, lambda: db.get_bind()):
        try:
            bind = getter()
        except Exception:  # noqa: BLE001 - a session without a bind is not PG
            continue
        name = getattr(getattr(bind, "dialect", None), "name", "")
        if name:
            return str(name)
    return ""


async def apply_statement_timeout(db: Optional[AsyncSession], timeout_ms: int) -> bool:
    """``SET LOCAL statement_timeout`` inside the request's transaction.

    LOCAL, never SET: a session-level ``SET`` survives the COMMIT and rides the
    pooled connection into whatever runs next on it -- an ingestion batch would
    then inherit a 5 s ceiling it never asked for and fail halfway. LOCAL is
    scoped to the transaction the statement itself opens (SQLAlchemy autobegins
    on the first execute) and is gone when the request's session commits or
    rolls back.

    The value is interpolated because PostgreSQL's ``SET`` takes no bind
    parameters; ``int()`` is what makes that safe.
    """
    if db is None or timeout_ms <= 0:
        return False
    if not _dialect_of(db).startswith("postgres"):
        return False
    try:
        await db.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
    except Exception as exc:  # noqa: BLE001 - a read must not 500 over its own guard
        # Failing open is right (a guard must not be the thing that breaks the
        # read) but it is not free: the query that follows runs under the
        # server default, which on this route means "until it finishes". A log
        # line alone pages nobody, so it is counted.
        count_degraded("statement_timeout_not_applied")
        logger.warning("analytics_statement_timeout_not_applied", error=str(exc))
        return False
    return True


def is_statement_timeout(exc: BaseException) -> bool:
    """True for the cancellation ``statement_timeout`` causes, and only that.

    A user cancellation (``pg_cancel_backend``) raises the same SQLSTATE; both
    mean "this query did not finish", which is what the 503 says.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        orig = getattr(current, "orig", None)
        for candidate in (current, orig):
            if candidate is None:
                continue
            code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
            if code == STATEMENT_TIMEOUT_SQLSTATE:
                return True
            if type(candidate).__name__ in ("QueryCanceledError", "QueryCanceled"):
                return True
        current = orig if orig is not None else current.__cause__
    return False


async def _rollback(db: Optional[AsyncSession]) -> None:
    """Leave the pooled connection usable: a cancelled statement aborts the
    transaction, and every later statement on it fails with 25P02 until it is
    rolled back."""
    if db is None:
        return
    try:
        await db.rollback()
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics_rollback_failed", error=str(exc))


# ── Rate limit ──────────────────────────────────────────────────────────────


_LIMITERS: dict[tuple[str, str], Any] = {}


def principal_of(request: Request, user: Any) -> str:
    """Who is being limited: the authenticated user, else the client address.

    The user id, not the token: a client that re-logs-in must not get a fresh
    budget. The address is the fallback for a route reached without a user.
    """
    uid = getattr(user, "id", None)
    if uid:
        return f"user:{uid}"
    from slowapi.util import get_remote_address

    return f"ip:{get_remote_address(request)}"


def rate_limit_for(route: str, default: Optional[str]) -> Optional[str]:
    return RATE_LIMITED_ROUTES.get(route, default)


def _rate_limit_key(request: Request) -> str:
    principal = getattr(request.state, "analytics_principal", "anonymous")
    # The path is in the key as well: slowapi derives its storage key from
    # key_func plus the limit STRING, so two routes sharing a limit string
    # would otherwise share one budget.
    return f"{principal}|{request.url.path}"


def _limiter_for(route: str, limit: str) -> Any:
    """One decorated callable per (route, limit), built ONCE.

    Re-decorating per request appends to slowapi's ``_route_limits`` list for
    that function name, so request N evaluates N limits against one counter --
    the quadratic collapse ``app.main._build_auth_limiters`` documents.
    """
    key = (route, limit)
    built = _LIMITERS.get(key)
    if built is None:
        from app.main import limiter

        async def _limited(request: Request) -> None:
            return None

        _limited.__name__ = "analytics_rate_limit_" + re.sub(
            r"[^a-z0-9]+", "_", f"{route}_{limit}".lower()
        ).strip("_")
        built = limiter.limit(limit, key_func=_rate_limit_key)(_limited)
        _LIMITERS[key] = built
    return built


def _retry_after(exc: Any) -> int:
    """Seconds until the window rolls over, from the limit slowapi refused."""
    try:
        return max(1, int(exc.limit.limit.get_expiry()))
    except Exception:  # noqa: BLE001 - a header is better wrong than missing
        return 60


async def enforce_rate_limit(
    request: Request, user: Any, *, route: str, limit: Optional[str]
) -> None:
    """Raise 429 with ``Retry-After`` when this principal is over the limit."""
    if limit is None:
        # An explicit "no limit on this route" is not overridden by the
        # registry: the decorator is the statement of record for a route that
        # exists.
        return
    resolved = rate_limit_for(route, limit)
    if not resolved:
        return
    from slowapi.errors import RateLimitExceeded

    try:
        request.state.analytics_principal = principal_of(request, user)
        limited = _limiter_for(route, resolved)
        await limited(request)
    except RateLimitExceeded as exc:
        retry = _retry_after(exc)
        logger.info("analytics_rate_limited", route=route, retry_after=retry)
        raise AnalyticsQueryError(
            "rate_limited",
            f"Too many analytics requests. Retry in {retry}s.",
            status_code=429,
            headers={"Retry-After": str(retry)},
        ) from None
    except Exception as exc:  # noqa: BLE001 - a broken limiter must not 500 a read
        logger.warning("analytics_rate_limit_unavailable", route=route, error=str(exc))


# ── Metrics ─────────────────────────────────────────────────────────────────

#: Every value the ``outcome`` label takes. A vocabulary, so a dashboard can
#: name the series it needs and a guard can check the two stay together.
ANALYTICS_OUTCOMES = ("hit", "miss", "timeout", "error")

#: Every value the ``reason`` label of ``analytics_read_degraded_total`` takes.
#: Each one is a guard that failed OPEN: the read still answered, from the
#: database, without the protection the name says. They are the series that
#: say "this layer is running degraded", which no latency histogram shows --
#: a bypassed cache and an unbounded query both look like a plain ``miss``.
ANALYTICS_DEGRADED_REASONS = (
    "no_scope",
    "epoch_timeout",
    "cache_timeout",
    "cache_write_timeout",
    "statement_timeout_not_applied",
)


def count_degraded(reason: str) -> None:
    """Count one degraded read. Never raises: telemetry cannot break a read."""
    try:
        from app.core.metrics import analytics_read_degraded_total

        analytics_read_degraded_total.labels(reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


def observe(route: str, outcome: str, seconds: float) -> None:
    """Observe one analytics read. Never raises: telemetry cannot break a read."""
    try:
        from app.core.metrics import analytics_query_duration_seconds

        analytics_query_duration_seconds.labels(route=route, outcome=outcome).observe(
            max(0.0, float(seconds))
        )
    except Exception:  # noqa: BLE001
        pass
