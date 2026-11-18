"""D-0006 in one piece on real Postgres: a "Skip AI analysis" upload never reaches the model.

GPT-6 uploaded reports with ``run_ai=false`` and every newly failing test still
started an automatic Investigator run (paid model calls, daily slots). The fix
has three layers, each unit-tested on its own; the model-call proof stopped at
a spy on ``maybe_auto_trigger``. This module drives the chain end to end:

1. a run is ingested through the real pipeline (``create_run_from_payload`` +
   ``ingest_test_results`` + ``finalize_run``) on a project whose Investigator
   is enabled and whose transition threshold is 1, so its failing test is a
   newly failing transition;
2. the ``transition_notifications`` intent ``finalize_run`` staged is read back
   from ``run_downstream_outbox``, bound to the real task's signature and run
   with ``dispatch_transition_notifications.apply()`` (the real task body, the
   real ``evaluate_run_transitions`` and ``maybe_auto_trigger``);
3. every investigation it enqueued is run with the real
   ``run_agent_investigation`` body, whose model is the real ``BudgetedLLM``
   around a scripted ``FakeListChatModel`` (``get_llm`` returns the wrapper as
   in production; only its budget and breaker stores are stubbed).

With ``run_ai=false``: the payload carries ``run_ai: False``, the transition is
detected and stamped, no ``agent_investigations`` row exists, nothing is
enqueued and the wrapper reserves budget 0 times. The control, ``run_ai=true``,
starts exactly one ``auto:newly_failing`` investigation that reaches the model.

The test functions are synchronous on purpose: Celery's ``.apply()`` runs the
task body on the worker loop (``worker/loop_runner.run_async``), which refuses
to run inside a running event loop, so every coroutine here runs on that same
worker loop, and the loop is shut down at the end of the module.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (also the app's ``DATABASE_URL``)
migrated to head; skips only without it. Writes one throwaway project per test
and removes it (the cleanup raises if a row is left behind).
"""

from __future__ import annotations

import inspect
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.integration

FAILING = "test_checkout_total_includes_delivery"
CLASS_NAME = "tests.checkout.TotalsTest"
MODEL_REPLY = json.dumps(
    {
        "narrative": "Scripted narrative: the checkout total changed.",
        "structured_actions": [],
    }
)


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _run(coro):
    from app.worker.loop_runner import run_async

    return run_async(coro)


@pytest.fixture(scope="module", autouse=True)
def _worker_loop():
    """The app's sessions must point at the throwaway database; drain the worker loop at the end."""
    from app.worker.loop_runner import shutdown_worker_loop

    if os.environ.get("DATABASE_URL", "").strip() != _dsn():
        pytest.fail(
            "DATABASE_URL must name the same throwaway database as TESTLOOKUP_POSTGRES_TEST_DSN"
        )
    from app.db.postgres import dispose_engine_for_loop

    # As the module family does: no engine built on another module's loop is reused here.
    _run(dispose_engine_for_loop())
    try:
        yield
    finally:
        shutdown_worker_loop()


async def _instance_settings() -> dict[str, str]:
    """``app_settings`` as key -> JSON text: finalize_run's duration step moves an
    instance-wide cursor (``perf_baseline.refresh_cursor``)."""
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return dict(
            (await db.execute(text("SELECT key, value::text FROM app_settings"))).all()
        )


async def _cleanup(project_id: uuid.UUID, settings_before: dict[str, str]) -> None:
    """Delete the project (runs, cases, outbox, states, policies, investigations
    cascade), put the instance settings back as they were, and prove both."""
    from app.db.postgres import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project_id})
        for key, value in (
            await db.execute(text("SELECT key, value::text FROM app_settings"))
        ).all():
            if key not in settings_before:
                await db.execute(
                    text("DELETE FROM app_settings WHERE key = :k"), {"k": key}
                )
            elif value != settings_before[key]:
                await db.execute(
                    text(
                        "UPDATE app_settings SET value = CAST(:v AS json) WHERE key = :k"
                    ),
                    {"k": key, "v": settings_before[key]},
                )
        await db.commit()
    if await _instance_settings() != settings_before:
        raise AssertionError(
            "skip-AI investigation cleanup could not restore app_settings"
        )
    checks = {
        "projects": "SELECT count(*) FROM projects WHERE id = :p",
        "test_runs": "SELECT count(*) FROM test_runs WHERE project_id = :p",
        "run_downstream_outbox": "SELECT count(*) FROM run_downstream_outbox WHERE project_id = :p",
        "agent_investigations": "SELECT count(*) FROM agent_investigations WHERE project_id = :p",
        "notification_test_states": "SELECT count(*) FROM notification_test_states WHERE project_id = :p",
        "agent_configs": "SELECT count(*) FROM agent_configs WHERE project_id = :p",
    }
    async with AsyncSessionLocal() as db:
        left = {
            table: (await db.execute(text(sql), {"p": project_id})).scalar_one()
            for table, sql in checks.items()
        }
    leaked = {table: count for table, count in left.items() if count}
    if leaked:
        raise AssertionError(
            f"skip-AI investigation cleanup left rows behind: {leaked}"
        )


async def _seed_project(tag: str) -> uuid.UUID:
    """A project whose Investigator is on and whose threshold makes one failure a transition."""
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import NotificationTransitionPolicy, Project
    from app.services.agent_investigation_service import (
        AGENT_ID_INVESTIGATOR,
        upsert_policy,
    )

    project_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Project(
                id=project_id,
                name=f"d0006-{tag}",
                slug=f"d0006-{tag}",
                is_active=True,
                description="throwaway D-0006 skip-AI chain",
            )
        )
        await db.flush()
        # Detection is pre-filter: no event is delivered, the transition is still detected.
        db.add(
            NotificationTransitionPolicy(
                project_id=project_id,
                transitions_enabled=True,
                per_run_events_enabled=False,
                enabled_events=[],
                consecutive_failure_threshold=1,
            )
        )
        await upsert_policy(
            db, project_id, AGENT_ID_INVESTIGATOR, enabled=True, mode="shadow"
        )
        await db.commit()
    return project_id


async def _ingest(project_id: uuid.UUID, tag: str, *, run_ai: bool) -> uuid.UUID:
    from app.db.postgres import AsyncSessionLocal
    from app.services.ingestion_pipeline import (
        create_run_from_payload,
        finalize_run,
        ingest_test_results,
    )

    async with AsyncSessionLocal() as db:
        run = await create_run_from_payload(
            db,
            project_id=str(project_id),
            build_number=f"b-{tag}",
            ingestion_source="upload",
            reuse_existing=False,
        )
        results = [
            {
                "test_name": "test_login",
                "class_name": CLASS_NAME,
                "status": "PASSED",
                "duration_ms": 120,
            },
            {
                "test_name": FAILING,
                "class_name": CLASS_NAME,
                "status": "FAILED",
                "duration_ms": 340,
                "error_message": "AssertionError: expected 42.50 got 40.00",
            },
        ]
        assert await ingest_test_results(db, run, results) == 2
        run_id, build = run.id, run.build_number
        await db.commit()
    await finalize_run(str(run_id), str(project_id), build, run_ai=run_ai)
    return run_id


async def _transition_payload(run_id: uuid.UUID) -> dict[str, Any]:
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import RunDownstreamOutbox

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(RunDownstreamOutbox.payload).where(
                        RunDownstreamOutbox.run_id == run_id,
                        RunDownstreamOutbox.operation == "transition_notifications",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, (
        f"finalize_run must stage exactly one transition intent, found {rows}"
    )
    return dict(rows[0])


async def _facts(project_id: uuid.UUID, fingerprint: str) -> SimpleNamespace:
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AgentInvestigation, NotificationTestState

    async with AsyncSessionLocal() as db:
        investigations = (
            (
                await db.execute(
                    select(AgentInvestigation).where(
                        AgentInvestigation.project_id == project_id
                    )
                )
            )
            .scalars()
            .all()
        )
        state = (
            await db.execute(
                select(NotificationTestState).where(
                    NotificationTestState.project_id == project_id,
                    NotificationTestState.test_fingerprint == fingerprint,
                )
            )
        ).scalar_one_or_none()
    return SimpleNamespace(investigations=list(investigations), state=state)


def _scripted_model(monkeypatch) -> dict[str, int]:
    """``get_llm`` returns the real BudgetedLLM around a scripted model; count its reservations."""
    from langchain_core.language_models import FakeListChatModel

    import app.services.llm_factory as llm_factory
    from app.core.config import settings
    from app.services import llm_circuit_breaker
    from app.services import llm_cost_reservation as cost
    from app.services.llm_cost_reservation import Reservation
    from app.services.llm_factory import BudgetedLLM

    calls = {"get_llm": 0, "reserve": 0}
    scripted = FakeListChatModel(responses=[MODEL_REPLY] * 20)

    async def _get_llm(*_a: Any, **_kw: Any) -> Any:
        calls["get_llm"] += 1
        return BudgetedLLM(scripted, provider="ollama", model="scripted-test-only")

    async def _reserve(
        provider: Any, model: Any, *, input_tokens: Any, max_output_tokens: Any
    ) -> Any:
        calls["reserve"] += 1
        return Reservation(
            key=f"ci-reservation-test-only-{calls['reserve']}",
            project_id="p",
            estimated_usd=0.0,
            ttl_seconds=60,
        )

    async def _quiet(*_a: Any, **_kw: Any) -> None:
        return None

    # The Investigator skips its model calls outright under AI_OFFLINE_MODE, so
    # with the flag on, "0 reservations" would hold even if an investigation
    # started. Turn it off in-process only, with get_llm returning the scripted
    # wrapper and the provider pinned to the on-box one (as
    # test_agent_act_end_to_end_postgres.py does): no request can leave the box.
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_CLUSTER_MAX_CONCURRENT", 0)
    monkeypatch.setattr(llm_factory, "get_llm", _get_llm)
    monkeypatch.setattr(llm_circuit_breaker, "require_available", _quiet)
    monkeypatch.setattr(
        llm_circuit_breaker.LLMCircuitBreaker, "record_failure", staticmethod(_quiet)
    )
    monkeypatch.setattr(
        llm_circuit_breaker.LLMCircuitBreaker, "record_success", staticmethod(_quiet)
    )
    monkeypatch.setattr(cost, "reserve", _reserve)
    monkeypatch.setattr(cost, "settle", _quiet)
    return calls


def _chain(monkeypatch, *, run_ai: bool) -> SimpleNamespace:
    """Ingest, run the staged transition task, then every investigation it enqueued."""
    from app.services.ingestion import make_test_fingerprint
    from app.worker import tasks

    tag = uuid.uuid4().hex[:10]
    calls = _scripted_model(monkeypatch)
    enqueued: list[tuple] = []
    monkeypatch.setattr(
        tasks.run_agent_investigation, "delay", lambda *a, **kw: enqueued.append(a)
    )

    settings_before = _run(_instance_settings())
    project_id = _run(_seed_project(tag))
    try:
        run_id = _run(_ingest(project_id, tag, run_ai=run_ai))
        payload = _run(_transition_payload(run_id))
        bound = inspect.signature(tasks.dispatch_transition_notifications.run).bind(
            **payload
        )
        transition = tasks.dispatch_transition_notifications.apply(
            kwargs=dict(bound.arguments)
        )
        investigation_results = [
            tasks.run_agent_investigation.apply(
                kwargs=dict(
                    inspect.signature(tasks.run_agent_investigation.run)
                    .bind(*args)
                    .arguments
                )
            )
            for args in list(enqueued)
        ]
        facts = _run(_facts(project_id, make_test_fingerprint(FAILING, CLASS_NAME)))
    finally:
        _run(_cleanup(project_id, settings_before))
    return SimpleNamespace(
        run_id=run_id,
        payload=payload,
        transition=transition,
        enqueued=enqueued,
        investigation_results=investigation_results,
        calls=calls,
        **vars(facts),
    )


def test_a_skip_ai_upload_detects_the_transition_but_never_reaches_the_model(
    monkeypatch,
):
    chain = _chain(monkeypatch, run_ai=False)

    # The intent finalize_run staged carries the opt-out.
    assert chain.payload == {"run_id": str(chain.run_id), "run_ai": False}
    # The real task body ran to the end and the transition was really detected
    # and stamped: the only thing that kept the Investigator away is run_ai.
    assert chain.transition.successful(), chain.transition.traceback
    assert chain.state is not None, (
        "the transition state was never written: nothing was evaluated"
    )
    assert (chain.state.state, chain.state.consecutive_failures) == ("failing", 1)
    assert chain.state.last_run_id == chain.run_id

    assert [i.triggered_by for i in chain.investigations] == [], (
        "a Skip-AI run started an investigation"
    )
    assert chain.enqueued == []
    assert chain.calls == {"get_llm": 0, "reserve": 0}, (
        "a Skip-AI run reached the model"
    )


def test_control_an_ai_on_upload_starts_one_investigation_that_reaches_the_model(
    monkeypatch,
):
    chain = _chain(monkeypatch, run_ai=True)

    assert chain.payload == {"run_id": str(chain.run_id)}
    assert chain.transition.successful(), chain.transition.traceback
    assert (chain.state.state, chain.state.consecutive_failures) == ("failing", 1)

    assert [(i.run_id, i.triggered_by) for i in chain.investigations] == [
        (chain.run_id, "auto:newly_failing")
    ]
    assert chain.enqueued == [(str(chain.investigations[0].id),)]
    assert all(r.successful() for r in chain.investigation_results), [
        r.traceback for r in chain.investigation_results
    ]
    assert chain.calls["get_llm"] >= 1, "the investigation never asked for a model"
    assert chain.calls["reserve"] >= 1, (
        "the scripted model was never reached through the budget wrapper"
    )
