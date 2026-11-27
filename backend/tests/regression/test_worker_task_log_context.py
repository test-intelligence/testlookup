"""Task execution context follows the current message, including reused children."""
from types import SimpleNamespace

from celery.exceptions import Retry
from celery.signals import task_postrun, task_prerun
import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars, get_contextvars

from app.worker import celery_app as worker
from app.worker.tasks import _bind_task_context

pytestmark = pytest.mark.regression


@pytest.fixture(autouse=True)
def _isolated_log_context():
    clear_contextvars()
    yield
    clear_contextvars()


def _task(task_id, *, headers=None, retries=0, **request_fields):
    return SimpleNamespace(
        name="app.worker.tasks.context_probe",
        request=SimpleNamespace(
            id=task_id, headers=headers, retries=retries, is_eager=False, **request_fields,
        ),
    )


def _send(signal, task, **fields):
    responses = signal.send(sender=None, task=task, task_id=task.request.id, **fields)
    assert all(not isinstance(result, Exception) for _, result in responses)


@pytest.mark.parametrize("state", ["SUCCESS", "FAILURE", "RETRY"])
def test_each_execution_binds_current_identity_and_cleans_up_for_reuse(state):
    first = _task("task-first", headers={"request_id": "request-first", "correlation_id": "flow-first"})
    bind_contextvars(project_id="stale-project", request_id="stale-request", path="/previous")
    _send(task_prerun, first, args=("payload",), kwargs={"api_token": "never-copy-payload"})
    assert get_contextvars() == {
        "celery_task_id": "task-first",
        "celery_task_name": first.name,
        "celery_retry": 0,
        "request_id": "request-first",
        "correlation_id": "flow-first",
    }
    _bind_task_context(first, project_id="project-first", run_id="run-first")
    assert get_contextvars()["project_id"] == "project-first"
    assert get_contextvars()["request_id"] == "request-first"
    _send(task_postrun, first, state=state)
    assert get_contextvars() == {}

    second = _task("task-second", retries=1 if state == "RETRY" else 0)
    _send(task_prerun, second)
    assert get_contextvars() == {
        "celery_task_id": "task-second",
        "celery_task_name": second.name,
        "celery_retry": second.request.retries,
    }
    _send(task_postrun, second, state="SUCCESS")
    assert get_contextvars() == {}


def test_prerun_discards_leftover_context_even_without_a_prior_postrun():
    bind_contextvars(project_id="previous-project", run_id="previous-run", request_id="previous-request")
    current = _task("current", headers={"X-Request-ID": "request-current"})
    _send(task_prerun, current)
    assert get_contextvars() == {
        "celery_task_id": "current", "celery_task_name": current.name,
        "celery_retry": 0, "request_id": "request-current",
    }
    _send(task_postrun, current, state="SUCCESS")


@pytest.mark.parametrize("invalid", ["bad\nrequest", "bad request", "x" * 129, {"secret": "value"}])
def test_only_well_formed_allowlisted_correlation_headers_are_bound(invalid):
    current = _task("current", headers={
        "request_id": invalid,
        "correlation_id": invalid,
        "authorization": "Bearer never-bind-this",
        "project_id": "untrusted-project",
        "context": {"project_id": "also-untrusted"},
    })
    _send(task_prerun, current)
    assert get_contextvars() == {
        "celery_task_id": "current", "celery_task_name": current.name, "celery_retry": 0,
    }
    _send(task_postrun, current, state="SUCCESS")


def test_task_helper_enriches_canonical_context_without_overwriting_it():
    current = _task("current", headers={"request_id": "request-current"}, retries=2,
                    correlation_id="message-current")
    _send(task_prerun, current)
    _bind_task_context(current, project_id="project-current", run_id="run-current",
                       celery_task_id="incorrect", celery_retry=99)
    assert get_contextvars() == {
        "celery_task_id": "current", "celery_task_name": current.name, "celery_retry": 2,
        "request_id": "request-current", "correlation_id": "message-current",
        "project_id": "project-current", "run_id": "run-current",
    }
    _send(task_postrun, current, state="SUCCESS")


@pytest.mark.parametrize("state", ["SUCCESS", "FAILURE", "RETRY"])
def test_real_eager_execution_runs_signals_and_restores_its_callers_context(state):
    snapshots = []
    task_name = f"tests.context_probe_{state.lower()}"

    @worker.celery_app.task(bind=True, name=task_name)
    def probe(self):
        _bind_task_context(self, project_id="project-current")
        snapshots.append(get_contextvars())
        if state == "FAILURE":
            raise ValueError("synthetic failure")
        if state == "RETRY":
            raise Retry(exc=RuntimeError("synthetic retry"))
        return "done"

    parent = {"request_id": "request-parent", "path": "/parent"}
    bind_contextvars(**parent)
    try:
        result = probe.apply(task_id="current", headers={"request_id": "request-current"}, throw=False)
        assert result.state == state
        assert snapshots == [{
            "celery_task_id": "current", "celery_task_name": task_name, "celery_retry": 0,
            "request_id": "request-current", "project_id": "project-current",
        }]
        assert get_contextvars() == parent
    finally:
        worker.celery_app.tasks.pop(task_name, None)
