"""Public read-only demo mode (``DEMO_MODE``).

A public demo must not be writable: anyone on the internet can reach it, and
everyone shares one demo account. With ``settings.DEMO_MODE`` on, this
middleware answers every request that is not GET, HEAD or OPTIONS with
``403 {"detail": DEMO_READ_ONLY}`` before routing or authentication run, so no
write handler is ever reached.

Writes are blocked at three layers in the demo deployment
(``docs/operations/public-demo.md``): the Cloudflare edge rule, this
middleware, and a nightly rebuild from seed. This layer is the one that holds
even if the edge rule is misconfigured.

The setting is read from the environment on every request rather than from a
database feature flag, so it cannot be switched off through the API it guards.
(The unrelated ``demo_mode`` feature-flag constant in
``feature_flag_service`` does not control this.)
"""
from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.request_path import routed_path

#: Methods that never change state. Everything else is refused in demo mode.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: The only non-safe requests a demo visitor needs. Each is either a session
#: operation on the visitor's own token or a computation that writes nothing.
#: Password login is deliberately absent: the demo signs in with
#: ``/auth/demo-login``, so there are no credentials to stuff or change.
DEMO_WRITE_ALLOWLIST = frozenset({
    "/api/v1/auth/demo-login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    # Evaluates a draft release-gate policy against stored runs; persists nothing.
    "/api/v1/release-gate-policies/simulate",
})

#: Allowlisted paths that carry an id. Each must be idempotent and take no
#: visitor content, so every caller converges on the same stored state.
DEMO_WRITE_PATTERNS = (
    # Onboarding progress, recomputed from the project's existing data: the
    # onboarding page READS through this (no body; same result for everyone).
    # Found by the demo route sweep: without it the page shows "Could not load
    # onboarding status".
    re.compile(r"^/api/v1/onboarding/[0-9a-fA-F-]{36}/detect$"),
)

#: The refusal body. ``{code, message}`` is the shape the SPA renders as a
#: sentence (``apiErrors.extractErrorMessage``) and keys its demo toast on.
DEMO_READ_ONLY = {
    "code": "demo_read_only",
    "message": (
        "This is a read-only demo, so changes are turned off. "
        "Install TestLookup to try everything."
    ),
}


def demo_blocks(method: str, path: str) -> bool:
    """Whether demo mode refuses this request (ignores the setting itself)."""
    if method.upper() in SAFE_METHODS:
        return False
    normalized = path.rstrip("/") or "/"
    if normalized in DEMO_WRITE_ALLOWLIST:
        return False
    # fullmatch, not match: "$" alone would also accept a trailing newline (%0A).
    return not any(pattern.fullmatch(normalized) for pattern in DEMO_WRITE_PATTERNS)


def install_demo_mode(app: FastAPI) -> None:
    """Register the demo write-block middleware on ``app``.

    Always installed; it does nothing unless ``settings.DEMO_MODE`` is true at
    request time (which also lets tests switch it per test).
    """

    @app.middleware("http")
    async def demo_read_only(request: Request, call_next):
        if settings.DEMO_MODE and demo_blocks(request.method, routed_path(request)):
            return JSONResponse(status_code=403, content={"detail": DEMO_READ_ONLY})
        return await call_next(request)
