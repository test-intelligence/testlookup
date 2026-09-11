"""R-B45-R2-5: the AI test-plan Celery task holds no DB session across the LLM call.

It read the cases, called the model, and wrote the plan inside ONE session:
a pooled connection idle in transaction for up to the task's 300 s limit.
That was harmless while the task crashed before the call (a sync ``invoke``
on an async-only tool); it has run since the round-1 fix. The rule is the
one fixer/workflow.py states: the LLM call never rides inside a transaction.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest


class _State:
    def __init__(self, cases):
        self.cases = cases
        self.open = 0
        self.sessions: list["_Session"] = []
        self.open_during_llm: list[int] = []


class _Session:
    def __init__(self, state: _State):
        self.state = state
        self.added: list = []
        self.executed = 0
        self.committed = False

    async def __aenter__(self):
        self.state.open += 1
        self.state.sessions.append(self)
        return self

    async def __aexit__(self, *exc):
        self.state.open -= 1
        return False

    async def execute(self, *args, **kwargs):
        self.executed += 1
        cases = self.state.cases
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: cases))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.committed = True


def _case(title: str):
    return SimpleNamespace(
        id=uuid.uuid4(), title=title, priority="high", test_type="functional", estimated_duration_minutes=None,
    )


def _run(monkeypatch, cases):
    import app.db.postgres as postgres
    from app.services import test_case_ai_agent as ai
    from app.worker import tasks

    state = _State(cases)
    monkeypatch.setattr(postgres, "AsyncSessionLocal", lambda: _Session(state))
    prompts: list[dict] = []

    async def run_tool_for_project(tool, args, project_id):
        state.open_during_llm.append(state.open)
        prompts.append(args)
        return json.dumps({
            "optimization_notes": "risk first",
            "optimized_order": [{"title": "checkout", "execution_order": 1}, {"title": "login", "execution_order": 2}],
        })

    monkeypatch.setattr(ai, "run_tool_for_project", run_tool_for_project)
    result = tasks.create_ai_test_plan_task.run(str(uuid.uuid4()), str(uuid.uuid4()))
    return state, prompts, result


def test_the_llm_call_runs_with_no_session_open(monkeypatch):
    from app.models.postgres import TestPlan, TestPlanItem

    login, checkout = _case("login"), _case("checkout")
    state, prompts, result = _run(monkeypatch, [login, checkout])

    assert state.open_during_llm == [0], "a DB session was open during the LLM call"
    read, write = state.sessions  # two short sessions, not one long one
    assert read.executed == 1 and read.added == [] and not read.committed
    assert write.executed == 0 and write.committed

    plans = [o for o in write.added if isinstance(o, TestPlan)]
    items = [o for o in write.added if isinstance(o, TestPlanItem)]
    assert len(plans) == 1 and plans[0].description == "risk first" and plans[0].total_cases == 2
    assert {(i.test_case_id, i.order_index) for i in items} == {(login.id, 2), (checkout.id, 1)}
    assert all(i.plan_id == plans[0].id for i in items)
    assert result == {"plan_id": str(plans[0].id), "total_cases": 2}

    # the model still sees the same case summary it always did (no ids)
    sent = json.loads(prompts[0]["test_cases_json"])
    assert sent[0] == {"title": "login", "priority": "high", "test_type": "functional",
                       "estimated_duration_minutes": 5}


def test_no_cases_means_no_llm_call_and_no_write(monkeypatch):
    state, prompts, result = _run(monkeypatch, [])
    assert result == {"error": "No approved test cases found for this project"}
    assert prompts == [] and len(state.sessions) == 1


@pytest.mark.parametrize("unused", [None])
def test_the_rule_the_task_follows_is_stated_where_the_fixer_states_it(unused):
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[1] / "app" / "agents" / "fixer" / "workflow.py"
    assert "must never ride inside a DB transaction" in workflow.read_text(encoding="utf-8")
