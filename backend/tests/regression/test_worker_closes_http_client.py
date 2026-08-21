"""Regression guard: worker task teardown must close the shared httpx client.

The gap (AI-HTTPX-001, residual)
--------------------------------
Every Celery task runs on its own event loop (``asyncio.new_event_loop()`` in
``worker/tasks.py``), and ``get_http_client()`` rotates the process-wide
``httpx.AsyncClient`` when it notices the owning loop changed::

    loop_changed = current_loop is not None and _shared_loop is not None \\
        and current_loop is not _shared_loop
    if _shared_client is None or _shared_client.is_closed or loop_changed:
        _shared_client = httpx.AsyncClient(...)

Rotating **replaces** the reference. The outgoing client is never closed:
``close_http_client()`` was called from the FastAPI lifespan and from nowhere
in the worker path, so each task abandoned a client whose connection pool still
held sockets bound to a loop that was about to close.

``reset_loop_bound_clients()`` does not cover it either — it drops the Redis
and Mongo clients, and its docstring says those "only need dropping, not
draining". A pool with live TCP connections needs draining, which is why
Postgres is disposed explicitly on the owning loop. The httpx client belongs
in that second category and was in neither.

Scope of the claim
------------------
The original AI-HTTPX-001 symptom — ``RuntimeError: Event loop is closed`` —
did **not** reproduce on the deployment: zero occurrences across every worker
and beat pod while the integration probe ran. That part appears fixed by the
loop-rotation guard. This guard covers the residual mechanism, which was
verified by reading the code and by the absence of any caller, **not** by
observing a leak in production.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.regression

WORKER = pathlib.Path(__file__).resolve().parents[2] / "app" / "worker"
RUNNERS = ("tasks.py", "training_tasks.py")


def _runner_source(filename: str) -> str:
    """Source of the function that owns a task's event loop."""
    path = WORKER / filename
    src = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node) or ""
            if "new_event_loop" in seg and "loop.close()" in seg:
                return seg
    return ""


@pytest.mark.parametrize("filename", RUNNERS)
def test_the_guard_found_the_loop_runner(filename):
    """Fail-open: no runner found makes the assertions below vacuous."""
    src = _runner_source(filename)
    assert len(src) > 200, (
        f"could not find the event-loop runner in {filename} (got "
        f"{len(src)} chars); the checks below would pass without checking."
    )
    assert "loop.close()" in src


def _calls_close_http_client(src: str) -> bool:
    """Is ``close_http_client`` actually CALLED, not merely imported?

    Searching for the bare identifier is not enough: the ``from
    app.core.http_client import close_http_client`` line keeps the name in the
    source even after the call is deleted, so a substring check passes over a
    teardown that closes nothing. Two mutations proved exactly that.
    """
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "close_http_client":
                return True
    return False


@pytest.mark.parametrize("filename", RUNNERS)
def test_http_client_is_closed_before_the_loop(filename):
    """A pool holding live sockets must be drained on the loop that owns it."""
    src = _runner_source(filename)
    assert _calls_close_http_client(src), (
        f"{filename} closes its event loop without CALLING close_http_client. "
        "get_http_client() rotates the client per loop but only drops the "
        "outgoing one, so every task abandons a connection pool bound to a "
        "loop that is about to close. (An import alone does not count.)"
    )
    close_at = src.index("close_http_client()")
    loop_close_at = src.rindex("loop.close()")
    assert close_at < loop_close_at, (
        f"{filename} closes the http client AFTER loop.close(); the await "
        "needs the loop still running to drain the pool."
    )


@pytest.mark.parametrize("filename", RUNNERS)
def test_teardown_never_raises(filename):
    """Teardown runs in a ``finally``; raising there would mask the task result."""
    src = _runner_source(filename)
    after = src[src.index("close_http_client()"):]
    assert "except" in after.split("loop.close()")[0], (
        f"the close_http_client call in {filename} is not wrapped in a "
        "try/except. A failure during teardown would replace the task's real "
        "outcome with a teardown error."
    )


def test_reset_loop_bound_clients_still_excludes_httpx():
    """Pin the premise, so the fix is not silently made redundant.

    If httpx is ever added to ``reset_loop_bound_clients``, that only DROPS
    the reference — it does not drain the pool — so this guard's reasoning
    would need re-reading rather than quietly passing.
    """
    from app.db import loop_bound

    src = pathlib.Path(loop_bound.__file__).read_text(encoding="utf-8")
    assert "http_client" not in src, (
        "reset_loop_bound_clients now touches the http client. Dropping a "
        "reference does not close sockets; re-read whether teardown should "
        "still drain it explicitly."
    )
