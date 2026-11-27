"""D-58: the AI test-plan task FAILS when it builds no plan; it never reports success.

It returned ``{"error": "No approved test cases..."}`` as a SUCCESSFUL task
result, so the status route said ``success``; and when the model call failed
the tool answered ``{"error": ...}`` and the task saved a plan with no order
at all, again a success. Now no case and a definite refusal raise
``AIPlanNotBuilt`` with a plain sentence at once; a transient model error
(timeout, 429, 5xx, connection) takes the task's Celery retry and, once the
retries run out, settles as ``AIPlanNotBuilt(AI_PLAN_BUSY)``. The status route
passes on only that sentence. The model cases drive the real tool and the real
``get_llm`` / ``BudgetedLLM`` wrapper with a fake model underneath.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel


class _Session:
    def __init__(self, cases, opened):
        self.cases = cases
        self.added: list = []
        opened.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        cases = self.cases
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: cases))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None


def _case(title: str):
    return SimpleNamespace(
        id=uuid.uuid4(), title=title, priority="high", test_type="functional", estimated_duration_minutes=None,
    )


@pytest.fixture
def opened(monkeypatch):
    import app.db.postgres as postgres

    world = SimpleNamespace(sessions=[], cases=[])
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _Session(world.cases, world.sessions))
    return world


#: What ``POST /plans/ai-create/async`` passes to ``create_ai_test_plan_task.delay`` (the route test
#: captures the same four positional values and binds them to the real signature).
PAYLOAD = (str(uuid.uuid4()), str(uuid.uuid4()), "AI plan", "risk first")


class _Retried(Exception):
    """Stands in for celery.exceptions.Retry: what ``self.retry`` raises while retries remain."""


def _recording_retry(monkeypatch):
    from app.worker import tasks

    calls: list[dict] = []

    def retry(*, exc=None, countdown=None, **_kwargs):
        calls.append({"exc": exc, "countdown": countdown})
        return _Retried()

    monkeypatch.setattr(tasks.create_ai_test_plan_task, "retry", retry)
    return calls


def _run(*payload):
    """The task body, its arguments bound to the REAL signature first (a wrong shape fails here)."""
    import inspect

    from app.worker import tasks

    inspect.signature(tasks.create_ai_test_plan_task.run).bind(*payload)
    return tasks.create_ai_test_plan_task.run(*payload)


def _fake_model(monkeypatch, opened, exc: BaseException) -> list[dict]:
    """The real tool, get_llm and BudgetedLLM; a ChatOllama underneath whose call raises ``exc``."""
    from app.services import ai_config_resolver, llm_policy_service

    opened.cases.extend([_case("login"), _case("checkout")])
    monkeypatch.setattr(
        ai_config_resolver, "get_effective_ai_config",
        AsyncMock(return_value={
            "provider": "ollama", "model": "qwen2.5:7b", "temperature": 0.1, "max_tokens": 256,
            # A base URL of its own per test: the circuit breaker keys its state on it, so these
            # deliberate failures never open the circuit for another test's endpoint.
            "base_url": f"http://127.0.0.1:{20000 + uuid.uuid4().int % 10000}", "offline_mode": False,
        }),
    )
    monkeypatch.setattr(llm_policy_service, "enforce_provider_policy_async", AsyncMock())
    built: list[dict] = []

    class _Failing(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            raise exc

        def _generate(self, *args, **kwargs):
            raise exc

    def _chat_ollama(**kwargs):
        built.append(kwargs)
        return _Failing(responses=["never"])

    monkeypatch.setattr("langchain_ollama.ChatOllama", _chat_ollama)
    return built


def test_no_approved_case_is_a_failure_not_a_success(monkeypatch, opened):
    from app.worker import tasks

    retries = _recording_retry(monkeypatch)
    with pytest.raises(tasks.AIPlanNotBuilt) as raised:
        _run(*PAYLOAD)

    assert str(raised.value) == "No approved test cases found for this project"
    assert len(opened.sessions) == 1 and opened.sessions[0].added == []    # read only, nothing written
    assert retries == []


@pytest.mark.parametrize("exc", [
    ConnectionError("connection refused by 10.0.0.7:11434"),
    TimeoutError("read timed out"),
    RuntimeError("429 Too Many Requests"),
    RuntimeError("503 Service Unavailable"),
], ids=["connection", "timeout", "rate-limit", "5xx"])
def test_a_transient_model_error_goes_through_the_celery_retry(monkeypatch, opened, exc):
    """Review finding 9: a busy model is retried; when the retries run out the task settles as
    AIPlanNotBuilt(AI_PLAN_BUSY), a plain sentence (the host in the error stays in the log)."""
    from app.worker import tasks

    built = _fake_model(monkeypatch, opened, exc)
    retries = _recording_retry(monkeypatch)

    with pytest.raises(_Retried):
        _run(*PAYLOAD)

    assert built and built[0]["model"] == "qwen2.5:7b"          # the real factory built the model
    assert len(retries) == 1 and retries[0]["countdown"] == 30
    final = retries[0]["exc"]
    assert isinstance(final, tasks.AIPlanNotBuilt) and str(final) == tasks.AI_PLAN_BUSY
    assert "10.0.0.7" not in str(final)
    assert len(opened.sessions) == 1 and opened.sessions[0].added == []    # no write session: no plan


def test_a_definite_refusal_fails_at_once_and_saves_no_plan(monkeypatch, opened):
    """A model the server does not have will not appear in 30 s: no retry."""
    from app.worker import tasks

    _fake_model(monkeypatch, opened, RuntimeError("model 'qwen2.5:7b' not found, try pulling it first"))
    retries = _recording_retry(monkeypatch)

    with pytest.raises(tasks.AIPlanNotBuilt) as raised:
        _run(*PAYLOAD)

    assert str(raised.value) == tasks.AI_PLAN_NO_ANSWER and "Settings" in str(raised.value)
    assert retries == []
    assert len(opened.sessions) == 1 and opened.sessions[0].added == []


def _status_of(monkeypatch, info):
    import celery.result

    from app.services.test_management_ai_service import get_ai_task_status

    class _Result:
        def __init__(self, task_id, app=None):
            self.state = "FAILURE"
            self.info = info

    monkeypatch.setattr(celery.result, "AsyncResult", _Result)
    return get_ai_task_status("t-1")


def test_the_status_route_passes_on_only_the_plain_sentence(monkeypatch):
    """Review finding 10: ``AIPlanNotBuilt`` text reaches the page; any other failure text does not."""
    from app.worker import tasks

    assert _status_of(monkeypatch, tasks.AIPlanNotBuilt(tasks.AI_PLAN_BUSY)) == {
        "task_id": "t-1", "status": "failure", "error": tasks.AI_PLAN_BUSY,
    }
    leaked = _status_of(monkeypatch, OSError("could not connect to server at db.internal:5432"))
    assert leaked == {"task_id": "t-1", "status": "failure", "error": None}
