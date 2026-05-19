"""Regression guard for ``/api/v1/runs`` route registration order.

``runs.router`` declares ``GET /{run_id}`` (UUID-typed). ``run_compare.router``
declares ``GET /compare`` and ``GET /compare/latest`` under the same
``/api/v1/runs`` prefix. FastAPI matches routes in registration order, so
``run_compare`` must come first — otherwise ``compare`` is parsed as a UUID
``run_id`` and the endpoint returns 422.
"""
from app.bootstrap import PROTECTED_ROUTERS
from app.routers import run_compare, runs


def test_run_compare_registered_before_runs():
    routers = list(PROTECTED_ROUTERS)
    compare_idx = routers.index(run_compare.router)
    runs_idx = routers.index(runs.router)
    assert compare_idx < runs_idx, (
        "run_compare.router must be registered before runs.router because both "
        "share the /api/v1/runs prefix and runs.router has GET /{run_id:UUID} "
        "which otherwise swallows /compare and /compare/latest as a UUID path "
        "param, yielding 422 Unprocessable Entity."
    )
