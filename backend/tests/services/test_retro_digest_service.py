"""
Unit tests for ``services.retro_digest_service`` — covers the feature
flag gate and the offline-mode narrative fallback. The SQL-backed
aggregators (``_count_released_flaky``, ``_count_new_regressions``)
are covered by integration tests.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services import retro_digest_service as svc


@pytest.mark.asyncio
async def test_generate_weekly_retro_short_circuits_when_flag_off():
    with patch(
        "app.services.retro_digest_service._feature_enabled",
        AsyncMock(return_value=False),
    ):
        result = await svc.generate_weekly_retro(db=None, project_id=uuid.uuid4())  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_compose_narrative_offline_mode_uses_template():
    """In offline mode the narrative must be rendered from raw numbers
    without calling the LLM factory — air-gapped customers need a
    guaranteed-deterministic output."""
    from app.core.config import settings
    digest = {
        "pass_rate": 92.5,
        "runs_total": 42,
        "top_clusters": [{"id": "a"}, {"id": "b"}],
    }
    with patch.object(settings, "AI_OFFLINE_MODE", True):
        narrative = await svc._compose_narrative(
            project_name="Acme",
            digest=digest,
            released_flaky=3,
            new_regressions=2,
        )
    assert "Acme" in narrative
    assert "92.5" in narrative
    assert "2 new regression" in narrative
    assert "3 previously" in narrative


@pytest.mark.asyncio
async def test_compose_narrative_online_mode_calls_llm():
    from app.core.config import settings

    class FakeLLM:
        async def ainvoke(self, _prompt):
            return type("R", (), {"content": "Week of steady improvement."})()

    async def fake_get_llm():
        return FakeLLM()

    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.llm_factory.get_llm", fake_get_llm,
    ):
        narrative = await svc._compose_narrative(
            project_name="Acme",
            digest={"pass_rate": 92.5, "runs_total": 42, "top_clusters": []},
            released_flaky=3,
            new_regressions=2,
        )
    assert narrative == "Week of steady improvement."


@pytest.mark.asyncio
async def test_compose_narrative_falls_back_on_llm_error():
    """If the LLM call raises, the service must return a template
    fallback rather than propagating — the retro email ships with
    numbers even when the narrative model is broken."""
    from app.core.config import settings

    async def failing_get_llm():
        raise RuntimeError("provider is down")

    with patch.object(settings, "AI_OFFLINE_MODE", False), patch(
        "app.services.llm_factory.get_llm", failing_get_llm,
    ):
        narrative = await svc._compose_narrative(
            project_name="Acme",
            digest={"pass_rate": 92.5, "runs_total": 42, "top_clusters": []},
            released_flaky=3,
            new_regressions=2,
        )
    assert "92.5" in narrative
    assert "2 new regressions" in narrative
