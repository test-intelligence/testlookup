"""VIZ-210 -- the analytics error contract.

Every error an analytics route returns has one body shape:

* validation (422): ``{code, param, message, allowed, request_id, detail}``
* anything else:    ``{code, message, request_id, detail}``

``code`` is the C1 rule id from ``contracts/viz/README.md`` where one applies
(``window_days_range``, ``release_cap``, ``release_id_format`` ...), so the
frontend can match the same string its own validator reports. ``detail`` is the
human-readable message again, kept because the SPA's shared interceptor
(``frontend/src/services/apiErrors.ts::extractErrorMessage``) and the MCP client
read ``detail`` -- dropping it would turn every analytics error toast into the
generic fallback. A stack trace never reaches the body.

**Scoped, not global.** Only endpoints marked with
:func:`analytics_error_contract` get this shape; every other route keeps
FastAPI's default bodies (and SCIM keeps its own), because dozens of clients
and tests read ``detail`` as a list on a 422. The handlers below are installed
app-wide and fall through to the previous handler for unmarked routes.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.scim_errors import (
    scim_http_exception_handler,
    scim_validation_exception_handler,
)

logger = structlog.get_logger(__name__)

_MARK = "__analytics_error_contract__"
_F = TypeVar("_F", bound=Callable[..., Any])

#: HTTP status -> ``code`` for errors raised as a plain ``HTTPException`` (403
#: and 404 from ``resolve_release_query_scope``, for instance). Specific rule
#: ids come from :class:`AnalyticsQueryError` instead.
_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "invalid_parameter",
    429: "rate_limited",
    503: "unavailable",
}

#: Query parameter -> C1 rule id, for failures FastAPI detects itself (a
#: non-integer ``days``) before any of our code runs.
_PARAM_RULES = {
    "days": "window_days_range",
    "project_id": "project_id_format",
    "release_id": "release_id_format",
    "suite_name": "suite_name_length",
    "from": "window_order",
    "to": "window_order",
}


class AnalyticsQueryError(Exception):
    """A request an analytics route refuses, with the contract's fields."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        param: Optional[str] = None,
        allowed: Any = None,
        status_code: int = 422,
        headers: Optional[dict] = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.param = param
        self.allowed = allowed
        self.status_code = status_code
        #: Response headers the refusal needs to be actionable -- VIZ-209's
        #: ``Retry-After`` on a 429 and on a 503 ``analytics_timeout``. The
        #: frontend's chart layer waits on that header rather than showing an
        #: error, so a refusal without it is a worse answer than no refusal.
        self.headers = headers


def analytics_error_contract(endpoint: _F) -> _F:
    """Mark an endpoint as speaking the analytics error contract.

    An attribute, not a wrapper: FastAPI reads the endpoint's signature, and a
    wrapper would be one more thing to keep signature-transparent.
    """
    setattr(endpoint, _MARK, True)
    return endpoint


def uses_analytics_contract(request: Request) -> bool:
    route = request.scope.get("route")
    endpoint = getattr(route, "endpoint", None) or request.scope.get("endpoint")
    return bool(getattr(endpoint, _MARK, False))


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid:
        return str(rid)
    from app.middleware.telemetry import REQUEST_ID_CTX

    return REQUEST_ID_CTX.get() or ""


def error_body(
    request: Request,
    *,
    code: str,
    message: str,
    status_code: int,
    param: Optional[str] = None,
    allowed: Any = None,
) -> dict:
    body: dict[str, Any] = {"code": code, "message": message}
    if status_code == 422:
        body["param"] = param
        body["allowed"] = allowed
    body["request_id"] = request_id_of(request)
    body["detail"] = message
    return body


def _json(request: Request, status_code: int, body: dict, headers=None) -> JSONResponse:
    merged = dict(headers or {})
    rid = body.get("request_id")
    if rid:
        # Set here too: an unhandled error bypasses the telemetry middleware
        # that normally stamps the header.
        merged.setdefault("X-Request-ID", rid)
    return JSONResponse(status_code=status_code, content=body, headers=merged or None)


async def analytics_query_error_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, AnalyticsQueryError)
    logger.info(
        "analytics_query_rejected",
        code=exc.code,
        param=exc.param,
        status_code=exc.status_code,
    )
    body = error_body(
        request,
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        param=exc.param,
        allowed=exc.allowed,
    )
    return _json(request, exc.status_code, body, headers=exc.headers)


#: Set on an analytics scope dependency (``analytics_scope.analytics_scope``)
#: to its ``ScopePolicy``, so a type error FastAPI raises before the policy's
#: own checks can still state the route's range.
POLICY_ATTR = "__analytics_scope_policy__"


def _route_policy(request: Request) -> Any:
    dependant = getattr(request.scope.get("route"), "dependant", None)
    stack = list(getattr(dependant, "dependencies", ()))
    while stack:
        sub = stack.pop()
        policy = getattr(sub.call, POLICY_ATTR, None)
        if policy is not None:
            return policy
        stack.extend(sub.dependencies)
    return None


def _validation_fields(exc: RequestValidationError) -> tuple[str, Optional[str], str, Any]:
    """``(code, param, message, allowed)`` from FastAPI's first validation error."""
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = [str(part) for part in first.get("loc", ())]
    param = loc[1] if len(loc) > 1 and loc[0] in ("query", "path") else (loc[-1] if loc else None)
    kind = str(first.get("type", ""))
    ctx = first.get("ctx") or {}
    allowed: Any = None
    if "expected" in ctx:
        allowed = ctx["expected"]
    elif any(key in ctx for key in ("ge", "gt", "le", "lt")):
        # Pydantic reports only the bound that was broken.
        bounds = {"min": ctx.get("ge", ctx.get("gt")), "max": ctx.get("le", ctx.get("lt"))}
        allowed = {key: value for key, value in bounds.items() if value is not None}
    if kind == "missing":
        code = "missing_parameter"
    else:
        code = _PARAM_RULES.get(param or "", "invalid_parameter")
    message = f"{param}: {first.get('msg', 'invalid value')}" if param else str(
        first.get("msg", "invalid request")
    )
    return code, param, message, allowed


async def validation_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    if not uses_analytics_contract(request):
        response: Response = await scim_validation_exception_handler(request, exc)
        return response
    code, param, message, allowed = _validation_fields(exc)
    if code == "window_days_range" and allowed is None:
        # ``days=seven``: FastAPI's int parse failed before the policy ran.
        policy = _route_policy(request)
        if policy is not None:
            allowed = {"min": 1, "max": policy.max_days}
    body = error_body(
        request, code=code, message=message, status_code=422, param=param, allowed=allowed
    )
    return _json(request, 422, body)


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if not uses_analytics_contract(request):
        response: Response = await scim_http_exception_handler(request, exc)
        return response
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    code = _STATUS_CODES.get(exc.status_code, "error")
    body = error_body(request, code=code, message=message, status_code=exc.status_code)
    if exc.status_code == 422:
        body["param"], body["allowed"] = None, None
    return _json(request, exc.status_code, body, headers=exc.headers)


async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """The last resort. For analytics routes a JSON body carrying the request id
    and no trace; everywhere else exactly Starlette's non-debug default."""
    if not uses_analytics_contract(request):
        return PlainTextResponse("Internal Server Error", status_code=500)
    # The body tells the caller to quote this id, so the log line must carry
    # the same one -- and the trace, which the body deliberately does not.
    logger.error(
        "analytics_unhandled_error",
        request_id=request_id_of(request),
        error_type=type(exc).__name__,
        path=request.url.path,
        exc_info=exc,
    )
    body = error_body(
        request,
        code="internal_error",
        message="The analytics query failed. Quote the request id when reporting it.",
        status_code=500,
    )
    return _json(request, 500, body)
