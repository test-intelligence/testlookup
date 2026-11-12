"""The path a request was routed by, never one rebuilt from the Host header.

Starlette builds ``request.url`` as ``f"{scheme}://{host_header}{path}"`` and
parses that string again, so a Host header holding ``?`` or ``/`` changes
``request.url.path`` while routing still uses ``scope["path"]``
(CVE-2026-48710, GHSA-86qp-5c8j-p5mr, fixed in starlette 1.0.1; this backend
runs 0.52.x). Measured on starlette 0.52.1 for ``POST /api/v1/auth/login``:

* ``Host: x?`` gives ``request.url.path == ""``, so a lookup keyed on it
  (the login rate limiter) finds nothing and the request goes unlimited;
* ``Host: x/api/v1/auth/logout?`` gives ``"/api/v1/auth/logout"``, so a gate
  that allowlists paths (demo read-only mode, the forced password change)
  judges a different route from the one that runs.

nginx forwards ``Host $host`` and accepts ``?`` in it, so the first case
reaches a default install through the frontend. Every security decision and
every rate-limit or metrics key reads :func:`routed_path` instead;
``backend/tests/regression/test_routed_path.py`` refuses ``request.url.path``
anywhere in ``backend/app``.
"""
from __future__ import annotations

from starlette.requests import HTTPConnection


def routed_path(request: HTTPConnection) -> str:
    """``scope["path"]``: the path the router matched, whatever Host said."""
    return str(request.scope.get("path") or "")
