"""Opt-in core API completion measurements, independent of HTTP diagnostics.

No request credential, ID, query, raw path or response content becomes a label.
These counters censor requests that never reach a terminal response/exception;
they are not a durable accepted-to-completed journey or an adopted guarantee.
"""
from __future__ import annotations

from time import perf_counter
import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette._utils import get_route_path

from app.core.config import settings
from app.core.metrics import api_journey_events, api_sli_configured
from app.services.journey_sli_contracts import API_OBJECTIVES, API_OUTCOMES, API_ROUTES

_TEMPLATES = tuple(
    (method, re.compile(r"\A" + "/".join("[^/]+" if part.startswith("{") and part.endswith("}") else re.escape(part)
                                            for part in template.split("/")) + r"\Z"), threshold)
    for (method, template), threshold in API_ROUTES.items()
)


def _threshold(scope: Scope) -> float | None:
    route = scope.get("route")
    if route is not None:
        method, path = scope.get("method"), getattr(route, "path", None)
        return API_ROUTES.get((method, path)) if isinstance(method, str) and isinstance(path, str) else None
    # An early application guard can return 429/500 before Router sets route.
    # Match only the same six fixed templates; never derive a metric label
    # from an arbitrary path, credential-bearing header, Host or query string.
    for method, pattern, threshold in _TEMPLATES:
        if scope.get("method") == method and pattern.fullmatch(get_route_path(scope)):
            return threshold
    return None


class JourneySLIMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.labels = {"installation": settings.OBSERVABILITY_INSTALLATION, "environment": settings.APP_ENV}
        self.enabled = bool(self.labels["installation"])
        if self.enabled:
            for objective in API_OBJECTIVES:
                for outcome in API_OUTCOMES:
                    api_journey_events.labels(**self.labels, objective=objective, outcome=outcome)
                api_sli_configured.labels(**self.labels, objective=objective).set(1)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started, status, finished, recorded = perf_counter(), None, False, False

        def record(*, raised: bool = False) -> None:
            nonlocal recorded
            if recorded:
                return
            # The router resolves this after middleware entry, including errors
            # from matched handlers. Unknown/raw paths are never keys or labels.
            threshold = _threshold(scope)
            if threshold is None:
                return
            if not raised and (status is None or (400 <= status < 500 and status != 429)):
                return
            recorded = True
            good = not raised and status is not None and 200 <= status < 400
            api_journey_events.labels(**self.labels, objective="api_availability", outcome="good" if good else "bad").inc()
            timely = good and perf_counter() - started <= threshold
            api_journey_events.labels(**self.labels, objective="api_latency", outcome="good" if timely else "bad").inc()

        async def observed_send(message: Message) -> None:
            nonlocal status, finished
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
                record()

        try:
            await self.app(scope, receive, observed_send)
        except BaseException:
            # Includes an unhandled server error after response.start or a
            # cancelled eligible handler. Reraise without changing behavior.
            record(raised=True)
            raise
        else:
            record(raised=not finished)
