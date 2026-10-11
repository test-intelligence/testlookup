"""Server-side flags are evaluated for the project (and user) they are about.

Open-source docs pass, 2026-10-11. ``feature_flags.is_enabled`` applies a
flag's project allow-list, role allow-list and rollout, but eleven server call
sites asked it with no project and no user. A flag allow-listed to one project
therefore read as OFF for every project on the server, while the UI (which
asks ``GET /feature-flags/{key}/status`` with the active project and the
signed-in user) showed it ON: the Failures page offered Mute and the API
answered 503; the meter never metered an allow-listed project; and so on.

Each test below drives the REAL call site with the REAL flag evaluation. Only
the flag store is faked: Redis "holds" a row allow-listed to ``ALLOWED``. The
call site must behave as on for ``ALLOWED`` and as off for ``OTHER``. With the
project left out of the call (the old code) the ``ALLOWED`` case fails.

``any_scope`` is the deliberate deployment-wide question ("is this on for
anyone?"): a sweep's early exit before it asks per row, and the settings page
that shows the switch itself.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.regression, pytest.mark.asyncio]

ALLOWED = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
OTHER = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")


@pytest.fixture
def flag_rows(monkeypatch):
    """Serve every flag from a fake Redis row; tests set the row's shape.

    Default: on, allow-listed to ``ALLOWED`` only. Nothing reaches a real
    Redis or Postgres: a hit in ``_load_from_redis`` is final.
    """
    from app.services import feature_flags as ff

    row = {"enabled_global": True, "enabled_projects": [str(ALLOWED)],
           "enabled_roles": [], "rollout_percent": 100}

    async def _load(key):
        return {"key": key, **row}

    async def _no_db(*_a, **_k):
        raise AssertionError("the flag store fake must answer from 'Redis'")

    monkeypatch.setattr(ff, "_load_from_redis", _load)
    monkeypatch.setattr(ff, "_load_from_db", _no_db)
    return row


@pytest.fixture
def online(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.value or []))

    def all(self):
        return list(self.value or [])


class _Session:
    """Answers ``execute`` from a queue (then ``None``) and counts the calls."""

    def __init__(self, *results):
        self.results = list(results)
        self.executed = 0
        self.commits = 0

    async def execute(self, *_a, **_k):
        self.executed += 1
        return _Result(self.results.pop(0) if self.results else None)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        return None

    def add(self, _row):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


# ── the any_scope question itself ────────────────────────────────────────────


async def test_any_scope_ignores_allow_lists_but_not_the_kill_switch(flag_rows):
    from app.services.feature_flags import is_enabled

    assert await is_enabled("x", any_scope=True) is True
    assert await is_enabled("x") is False  # no project: no allow-list matches
    flag_rows["enabled_projects"] = []
    flag_rows["enabled_roles"] = ["ADMIN"]
    assert await is_enabled("x", any_scope=True) is True
    flag_rows["rollout_percent"] = 0
    assert await is_enabled("x", any_scope=True) is False
    flag_rows["rollout_percent"] = 100
    flag_rows["enabled_global"] = False
    assert await is_enabled("x", any_scope=True) is False


# ── llm_cost_budget ──────────────────────────────────────────────────────────


async def test_llm_cost_budget_caps_and_meters_the_allow_listed_project(flag_rows, monkeypatch):
    from app.services import llm_cost_budget as budget
    from app.services import llm_cost_reservation as reservation

    load_quota = AsyncMock(return_value=None)
    sessions: list[_Session] = []

    def _factory():
        sessions.append(_Session())
        return sessions[-1]

    monkeypatch.setattr(budget, "_load_quota", load_quota)
    monkeypatch.setattr(budget, "AsyncSessionLocal", _factory)
    monkeypatch.setattr("app.db.postgres.AsyncSessionLocal", _factory)

    await budget.check_and_apply_cap(ALLOWED)
    assert load_quota.await_count == 1, "the cap was not consulted for the allow-listed project"
    await budget.check_and_apply_cap(OTHER)
    assert load_quota.await_count == 1

    await reservation._cap_context(ALLOWED)
    assert load_quota.await_count == 2, "the reservation skipped the allow-listed project's cap"
    await reservation._cap_context(OTHER)
    assert load_quota.await_count == 2

    sessions.clear()
    await budget.record_usage(ALLOWED, cost_usd=1.0, llm_calls=1)
    assert len(sessions) == 1, "the allow-listed project's usage was not metered"
    await budget.record_usage(OTHER, cost_usd=1.0, llm_calls=1)
    assert len(sessions) == 1


# ── flaky_auto_quarantine ────────────────────────────────────────────────────


async def test_quarantine_enforcement_follows_the_projects_flag(flag_rows):
    from app.services import flaky_quarantine_service as svc

    allowed_db, other_db = _Session([]), _Session([])
    await svc.active_quarantines_for_project(allowed_db, ALLOWED)
    await svc.active_quarantines_for_project(other_db, OTHER)
    assert allowed_db.executed == 1, "ingest stopped tagging the allow-listed project's quarantines"
    assert other_db.executed == 0


async def test_a_manual_quarantine_proposal_follows_the_projects_flag(flag_rows, monkeypatch):
    from app.services import flaky_quarantine_service as svc

    opened: list[uuid.UUID] = []

    class _Boom(_Session):
        async def __aenter__(self):
            opened.append(uuid.uuid4())
            raise RuntimeError("stop after the gate")

    monkeypatch.setattr(svc, "AsyncSessionLocal", _Boom)
    kwargs = dict(test_fingerprint="fp", test_name="t", suite_name=None,
                  detection_method="manual", flip_rate=None, flip_window_size=None,
                  actor=SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD"))
    assert await svc.propose_quarantine(project_id=OTHER, **kwargs) is None
    assert opened == []
    with pytest.raises(RuntimeError, match="stop after the gate"):
        await svc.propose_quarantine(project_id=ALLOWED, **kwargs)
    assert len(opened) == 1, "Mute answered 503 for the allow-listed project"


async def test_the_stale_sweep_keeps_only_rows_whose_project_has_the_flag(flag_rows, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.services import flaky_quarantine_service as svc

    def _row(project_id):
        return SimpleNamespace(
            project_id=project_id, test_name="t", test_fingerprint=f"fp-{project_id}",
            suite_name=None, sla_days=14, stale_notified_at=None, updated_at=None,
            stale_at=datetime.now(timezone.utc) - timedelta(days=2),
        )

    allowed_row, other_row = _row(ALLOWED), _row(OTHER)
    db = _Session([allowed_row, other_row])
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: db)
    dispatch = AsyncMock()
    monkeypatch.setattr(
        "app.services.notification_transitions.dispatch_quarantine_transitions", dispatch,
    )

    assert await svc.mark_stale_quarantines() == 1
    assert allowed_row.stale_notified_at is not None
    assert other_row.stale_notified_at is None
    assert dispatch.await_args.args[0] == ALLOWED


# ── release_compliance_pack ──────────────────────────────────────────────────


async def test_a_compliance_pack_is_generated_for_the_allow_listed_project(flag_rows, monkeypatch):
    from app.services import compliance_pack_service as svc

    monkeypatch.setattr(
        svc, "_build_pack_payload",
        AsyncMock(side_effect=svc.CompliancePackNotAvailableError("no run")),
    )
    release = lambda pid: SimpleNamespace(id=uuid.uuid4(), project_id=pid)  # noqa: E731

    with pytest.raises(svc.CompliancePackNotAvailableError):  # past the gate
        await svc.generate_pack(AsyncMock(), release(ALLOWED), actor=None)
    with pytest.raises(svc.CompliancePackDisabledError):
        await svc.generate_pack(AsyncMock(), release(OTHER), actor=None)


async def test_a_compliance_pack_honours_a_role_allow_list(flag_rows, monkeypatch):
    from app.services import compliance_pack_service as svc

    flag_rows["enabled_projects"] = []
    flag_rows["enabled_roles"] = ["QA_LEAD"]
    monkeypatch.setattr(
        svc, "_build_pack_payload",
        AsyncMock(side_effect=svc.CompliancePackNotAvailableError("no run")),
    )
    release = SimpleNamespace(id=uuid.uuid4(), project_id=OTHER)
    lead = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD")
    viewer = SimpleNamespace(id=uuid.uuid4(), role="VIEWER")

    with pytest.raises(svc.CompliancePackNotAvailableError):
        await svc.generate_pack(AsyncMock(), release, actor=lead)
    with pytest.raises(svc.CompliancePackDisabledError):
        await svc.generate_pack(AsyncMock(), release, actor=viewer)


# ── github_checks ────────────────────────────────────────────────────────────


async def test_github_checks_post_for_the_allow_listed_runs_project(flag_rows, online, monkeypatch):
    from app.services import github_checks_service as gh

    def _run(project_id):
        return SimpleNamespace(id=uuid.uuid4(), project_id=project_id, commit_hash=None)

    monkeypatch.setattr(gh, "AsyncSessionLocal", lambda: _Session(_run(ALLOWED)))
    assert await gh.post_check_run_for_run(uuid.uuid4()) == {"skipped": "no_full_commit_sha"}
    monkeypatch.setattr(gh, "AsyncSessionLocal", lambda: _Session(_run(OTHER)))
    assert await gh.post_check_run_for_run(uuid.uuid4()) == {
        "skipped": "feature_flag_off_or_offline_mode",
    }


async def test_codeowners_fetch_follows_the_projects_github_flag(flag_rows, online, monkeypatch):
    from app.services import codeowners_service as cs
    from app.services import github_checks_service as gh

    monkeypatch.setattr(gh, "get_integration", AsyncMock(return_value=None))
    assert await cs.fetch_codeowners_text(AsyncMock(), ALLOWED) == (
        None, "integration_disabled_or_missing",
    )
    assert await cs.fetch_codeowners_text(AsyncMock(), OTHER) == (None, "offline_or_flag_off")


# ── gitlab ───────────────────────────────────────────────────────────────────


async def test_gitlab_commit_status_follows_the_runs_project(flag_rows, online, monkeypatch):
    from app.services import gitlab_integration_service as gl

    def _run(project_id):
        return SimpleNamespace(id=uuid.uuid4(), project_id=project_id, commit_hash=None)

    monkeypatch.setattr(gl, "AsyncSessionLocal", lambda: _Session(_run(ALLOWED)))
    assert await gl.post_commit_status_for_run(uuid.uuid4()) == {"skipped": "no_commit_sha"}
    monkeypatch.setattr(gl, "AsyncSessionLocal", lambda: _Session(_run(OTHER)))
    assert await gl.post_commit_status_for_run(uuid.uuid4()) == {
        "skipped": "feature_flag_off_or_offline_mode",
    }


# ── outbound_webhooks ────────────────────────────────────────────────────────


async def test_webhook_events_fan_out_for_the_allow_listed_project(flag_rows, online, monkeypatch):
    from app.services import webhook_service as svc

    sessions: list[_Session] = []

    def _factory():
        sessions.append(_Session([]))
        return sessions[-1]

    monkeypatch.setattr(svc, "AsyncSessionLocal", _factory)
    payload = {"run_id": str(uuid.uuid4())}
    await svc.emit_event("run.completed", project_id=ALLOWED, payload=payload)
    assert len(sessions) == 1, "the allow-listed project's subscriptions were never read"
    await svc.emit_event("run.completed", project_id=OTHER, payload=payload)
    assert len(sessions) == 1


async def test_the_jira_webhook_fallback_follows_the_projects_flag(flag_rows, online):
    from app.services import defect_jira_service as svc

    allowed_db, other_db = _Session([]), _Session([])
    await svc._webhook_target_available(allowed_db, ALLOWED)
    await svc._webhook_target_available(other_db, OTHER)
    assert allowed_db.executed == 1
    assert other_db.executed == 0


# ── perf_regression_detection ────────────────────────────────────────────────


async def test_perf_spikes_are_detected_for_the_allow_listed_runs_project(flag_rows):
    from app.services import perf_regression_service as svc

    allowed_db, other_db = _Session(ALLOWED, []), _Session(OTHER, [])
    assert await svc.detect_spikes_for_run(allowed_db, uuid.uuid4()) == []
    assert await svc.detect_spikes_for_run(other_db, uuid.uuid4()) == []
    assert allowed_db.executed == 2, "spike detection stopped after the run lookup"
    assert other_db.executed == 1


# ── rag_faithfulness_gate ────────────────────────────────────────────────────


async def test_the_faithfulness_gate_blocks_in_the_allow_listed_project(flag_rows):
    from app.services import rag_faithfulness_service as svc

    def _case(project_id):
        return SimpleNamespace(
            id=uuid.uuid4(), project_id=project_id, faithfulness_score=0.1,
            faithfulness_evaluator="ollama", needs_review_reason=None,
        )

    assert (await svc.check_accept(_case(ALLOWED)))["allow"] is False
    decision = await svc.check_accept(_case(OTHER))
    assert decision == {"allow": True, "reason": "faithfulness gate disabled"}


# ── weekly_retro_digest ──────────────────────────────────────────────────────


async def test_the_weekly_retro_is_built_for_the_allow_listed_project(flag_rows, monkeypatch):
    from app.services import retro_digest_service as retro

    class _Built(Exception):
        pass

    monkeypatch.setattr(retro, "generate_digest", AsyncMock(side_effect=_Built()))
    with pytest.raises(_Built):
        await retro.generate_weekly_retro(AsyncMock(), ALLOWED)
    assert await retro.generate_weekly_retro(AsyncMock(), OTHER) is None


# ── release_phase_gate_enforcement ───────────────────────────────────────────


async def test_phase_gate_enforcement_applies_to_the_allow_listed_project(flag_rows, monkeypatch):
    from app.services import release_service as svc

    phase = SimpleNamespace(id=uuid.uuid4(), status="in_progress")
    project = AsyncMock(return_value=ALLOWED)
    monkeypatch.setattr(svc, "_project_of_release", project)
    with pytest.raises(HTTPException) as refused:  # an unexplained skip is refused
        await svc._enforce_phase_gate(AsyncMock(), str(uuid.uuid4()), phase, {"status": "skipped"})
    assert refused.value.status_code == 409

    project.return_value = OTHER
    assert await svc._enforce_phase_gate(
        AsyncMock(), str(uuid.uuid4()), phase, {"status": "skipped"},
    ) is None


# ── knowledge_rag ────────────────────────────────────────────────────────────


async def test_rag_retrieval_runs_for_the_allow_listed_project(flag_rows, monkeypatch):
    from app.services import rag_retrieval_service as svc

    collection = AsyncMock(side_effect=RuntimeError("chroma down"))
    monkeypatch.setattr(svc, "_get_or_create_knowledge_collection", collection)
    assert await svc.retrieve_chunks(AsyncMock(), ALLOWED, "q") == []
    assert collection.await_count == 1, "retrieval was skipped for the allow-listed project"
    assert await svc.retrieve_chunks(AsyncMock(), OTHER, "q") == []
    assert collection.await_count == 1


async def test_a_knowledge_source_answers_by_its_own_projects_flag(flag_rows, monkeypatch):
    from app.services import knowledge_source_service as svc

    monkeypatch.setattr(svc, "get_accessible_project_ids", AsyncMock(return_value=None))
    admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    source = SimpleNamespace(id=uuid.uuid4(), project_id=ALLOWED)
    assert await svc.get_source_or_404(_Session(source), source.id, admin) is source

    source.project_id = OTHER
    with pytest.raises(HTTPException) as off:
        await svc.get_source_or_404(_Session(source), source.id, admin)
    assert off.value.status_code == 503


async def test_rag_status_answers_for_the_page_project_and_the_switch(flag_rows):
    from app.services import rag_eval_service as svc

    counts = lambda: _Session(0, 0, 0)  # noqa: E731
    assert (await svc.get_rag_status(counts(), project_id=ALLOWED))["enabled"] is True
    assert (await svc.get_rag_status(counts(), project_id=OTHER))["enabled"] is False
    # No project: the deployment's switch, on while any project has RAG.
    assert (await svc.get_rag_status(counts()))["enabled"] is True


async def test_the_ai_settings_page_shows_the_rag_switch_that_is_stored(flag_rows, monkeypatch):
    """Asked without a scope, a project allow-list rendered the switch as off,
    and saving the page then wrote ``enabled_global = false``."""
    from unittest.mock import MagicMock

    from app.routers import app_settings as settings_router

    monkeypatch.setattr(settings_router, "_load_ai_config", AsyncMock(return_value={
        "llm_provider": "ollama", "llm_model": "m", "llm_temperature": 0.1,
        "llm_max_tokens": 100, "ai_offline_mode": True,
        "ai_offline_mode_source": "env", "ai_offline_mode_env_pinned": False,
        "embedding_provider": "ollama", "embedding_model": "e",
        "ai_confidence_threshold": 70, "ai_confidence_threshold_source": "default",
        "ai_timeout_seconds": 30, "deep_investigation_enabled": False,
        "finetune_enabled": False, "analysis_mode": "auto",
    }))
    monkeypatch.setattr(settings_router, "_ml_status", AsyncMock(return_value={}))
    result = await settings_router.get_ai_config(_=MagicMock(), db=MagicMock())
    assert result.knowledge_rag_enabled is True
