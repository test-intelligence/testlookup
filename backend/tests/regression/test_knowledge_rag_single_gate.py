"""Regression: Knowledge RAG had three gates reading three different stores.

Measured on the live homelab (2026-08-16)::

    GET /feature-flags/knowledge_rag/status  ->  {"enabled": false}
    GET /settings/ai                          ->  knowledge_rag_enabled: true
    /settings/feature-flags rendered              knowledge_rag   OFF
    /settings/ai rendered                         "Enable Knowledge RAG  Active"
    GET /knowledge-sources/<id>/freshness     ->  503 "Knowledge RAG feature is
                                                  not enabled. Enable it from
                                                  Settings > AI Configuration."

The 503 sent the operator to the page that already said **Active**.

``services/knowledge_source_service.py`` carried three resolvers:

* ``_is_rag_enabled_from_db``  — the ``ai_config.knowledge_rag_enabled``
  AppSetting, i.e. the switch the UI wrote. One consumer in the whole tree.
* ``require_rag_enabled_async`` — the ``feature_flags`` row. Gated all four
  real endpoints.
* ``require_rag_enabled``      — env var only. **Zero callers.**

So flipping the switch the error message named changed whether the RAG
*evaluator* ran, and nothing else.

Fix: one gate (``feature_flags.is_enabled``), and ``PUT /settings/ai`` writes
that flag instead of a private copy. The flag service is the survivor because
``LEGACY_ENV_VAR_MAP`` in ``services/feature_flags.py`` declares it the
successor — the AppSetting was the hand-rolled flag being retired.

**Behaviour change:** a workspace whose AppSetting said true while the flag said
false (this homelab) will now show the switch OFF, which is what was actually in
force. Re-enabling it there now takes effect.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import app_settings as settings_router  # noqa: E402
from app.services import knowledge_source_service as ks_service  # noqa: E402

pytestmark = pytest.mark.regression


def test_only_one_gate_survives():
    """The two resolvers that could disagree with the endpoints are gone."""
    assert hasattr(ks_service, "require_rag_enabled_async")
    # Env-only, no callers — a gate nothing could reach.
    assert not hasattr(ks_service, "require_rag_enabled")
    # Read the AppSetting the endpoints never consulted.
    assert not hasattr(ks_service, "_is_rag_enabled_from_db")


def test_ai_config_no_longer_keeps_its_own_copy():
    """``_load_ai_config`` must not resurrect the second store.

    A copy in ``ai_config`` is the whole mechanism of the bug: the page renders
    from it, the gate ignores it, and the two drift apart silently.
    """
    import inspect
    src = inspect.getsource(settings_router._load_ai_config)
    assert '"knowledge_rag_enabled": overrides.get' not in src


@pytest.mark.asyncio
async def test_the_ai_page_reports_what_the_gate_would_answer():
    """GET /settings/ai resolves through the flag, not an AppSetting."""
    db = MagicMock()
    with patch("app.routers.app_settings._load_ai_config", new=AsyncMock(return_value={
        "llm_provider": "ollama", "llm_model": "m", "llm_temperature": 0.1,
        "llm_max_tokens": 100, "ai_offline_mode": True,
        "ai_offline_mode_source": "env", "ai_offline_mode_env_pinned": False,
        "embedding_provider": "ollama", "embedding_model": "e",
        "ai_confidence_threshold": 70, "ai_confidence_threshold_source": "default",
        "ai_timeout_seconds": 30, "deep_investigation_enabled": False,
        "finetune_enabled": False, "analysis_mode": "auto",
    })), patch("app.routers.app_settings._ml_status", new=AsyncMock(return_value={})), \
         patch("app.services.feature_flags.is_enabled", new=AsyncMock(return_value=False)) as gate:
        result = await settings_router.get_ai_config(_=MagicMock(), db=db)

    assert result.knowledge_rag_enabled is False
    # It asked the gate's own resolver, by the gate's own key.
    assert gate.await_args.args[0] == "knowledge_rag"


@pytest.mark.asyncio
async def test_the_gate_and_the_status_reader_cannot_disagree():
    """``get_rag_status`` used to read a Redis key + AppSetting the gate never
    consulted, which is how the UI showed "Active" for a feature that 503'd."""
    from app.services import rag_eval_service

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

    with patch("app.services.feature_flags.is_enabled", new=AsyncMock(return_value=False)):
        status = await rag_eval_service.get_rag_status(db)
    assert status["enabled"] is False

    with patch("app.services.feature_flags.is_enabled", new=AsyncMock(return_value=True)):
        status = await rag_eval_service.get_rag_status(db)
    assert status["enabled"] is True

    # It also advertised the wrong knob name to anyone reading the payload.
    assert status["feature_flag"] == "knowledge_rag"


def test_the_toggle_writes_through_the_cache_invalidating_path():
    """Two flag services exist. ``feature_flag_service.set_flag`` does not clear
    the in-process/Redis caches ``is_enabled`` reads, so a write through it
    leaves the switch looking broken for up to the 30s TTL. The AI page must
    use ``feature_flags.update_flag``/``create_flag``, which invalidate.
    """
    import inspect
    src = inspect.getsource(settings_router.update_ai_config)
    # Comments discuss the rejected module by name, so compare code only.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "from app.services.feature_flags import create_flag, get_flag, update_flag" in code
    assert "feature_flag_service" not in code


def test_the_redis_mirror_of_the_second_store_is_gone():
    """``config:knowledge_rag_enabled`` was a third copy of the same bit."""
    import inspect
    src = inspect.getsource(settings_router.update_ai_config)
    assert "config:knowledge_rag_enabled" not in src

@pytest.mark.asyncio
async def test_flag_caches_are_dropped_after_the_commit_not_before():
    """Invalidating mid-transaction is the same as not invalidating.

    ``update_flag`` clears the caches, but it runs inside the caller's
    transaction and ``get_db`` commits only after the handler returns. Any
    reader in that window re-reads the *old* committed row and re-populates
    both caches with it, where it stands for the 30s TTL.

    Caught live, not by review: right after the toggle was wired up,
    ``PUT /settings/ai {knowledge_rag_enabled: true}`` returned 200 and the
    flag row read ``true`` (that endpoint queries Postgres directly), while
    ``GET /settings/ai`` and the gate — both via ``is_enabled`` — still
    answered ``false``.
    """
    calls: list[str] = []

    db = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: calls.append("commit"))
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()

    async def _invalidate(key):
        calls.append(f"invalidate:{key}")

    async def _update_flag(_db, *, key, updates, actor):
        calls.append("update_flag")
        return MagicMock()

    async def _lock(_db):
        calls.append("authority_lock")

    async def _invalidate_ai():
        calls.append("invalidate:ai_config")

    payload = MagicMock()
    payload.model_dump.return_value = {"knowledge_rag_enabled": True}

    with patch("app.routers.app_settings._load_ai_config", new=AsyncMock(return_value={})),          patch("app.routers.app_settings._ml_status", new=AsyncMock(return_value={})),          patch("app.routers.app_settings.extract_secrets_from_config", return_value={}),          patch("app.routers.app_settings.log_settings_change", new=AsyncMock()),          patch("app.services.agent_authority_lock.lock_global_agent_authority", new=_lock),          patch("app.services.ai_config_resolver.invalidate_ai_config_cache", new=_invalidate_ai),          patch("app.services.ai_config_resolver.env_offline_pinned", return_value=False),          patch("app.services.ai_config_resolver.resolve_offline_mode", return_value=(True, "env")),          patch("app.services.feature_flags.get_flag", new=AsyncMock(return_value=MagicMock())),          patch("app.services.feature_flags.update_flag", new=_update_flag),          patch("app.services.feature_flags.invalidate_flag_cache", new=_invalidate),          patch("app.services.feature_flags.is_enabled", new=AsyncMock(return_value=True)),          patch("app.routers.app_settings.AIConfigRead", new=MagicMock()):
        try:
            await settings_router.update_ai_config(payload, current_user=MagicMock(), db=db)
        except Exception:
            # The response model is mocked; only the call ordering is under test.
            pass

    assert "commit" in calls, f"handler never committed: {calls}"
    assert calls.index("authority_lock") < calls.index("commit")
    assert calls.index("invalidate:ai_config") > calls.index("commit")
    assert "invalidate:knowledge_rag" in calls, f"caches never dropped: {calls}"
    assert calls.index("invalidate:knowledge_rag") > calls.index("commit"), (
        f"invalidated before the commit — the stale row wins: {calls}"
    )
