"""Guards for integration_probe_service.

Reviewed in review/integration-probe-service (2026-06-02): clean. These pin the
two non-obvious invariants the existing ops01 suite doesn't cover:

1. ``run_all_probes`` isolates a probe that *raises* — it becomes a "down"
   ProbeResult (via ``asyncio.gather(return_exceptions=True)``) without sinking
   the rest of the batch.
2. ``probe_ollama`` only probes when ``AI_OFFLINE_MODE`` is on (online = cloud
   LLM, so Ollama isn't used and must be skipped, not marked down).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("sqlalchemy")

from app.core.config import settings  # noqa: E402
from app.services import integration_probe_service as svc  # noqa: E402


@pytest.mark.asyncio
async def test_run_all_probes_isolates_a_raising_probe():
    async def _boom():
        raise RuntimeError("kaboom")

    async def _ok():
        return svc.ProbeResult("ok_provider", "healthy", 5, "fine")

    with patch.object(svc, "ALL_PROBES", {"boom": _boom, "ok_provider": _ok}):
        results = await svc.run_all_probes()

    by = {r.provider: r for r in results}
    assert len(results) == 2
    # The exception was isolated and recorded as a "down" result for that key.
    assert by["boom"].status == "down"
    assert "kaboom" in by["boom"].message
    # The sibling probe still ran and returned its real result.
    assert by["ok_provider"].status == "healthy"


@pytest.mark.asyncio
async def test_probe_ollama_skipped_when_online():
    with patch.object(settings, "AI_OFFLINE_MODE", False):
        result = await svc.probe_ollama()
    assert result.status == "skipped"


@pytest.mark.asyncio
async def test_probe_jira_skipped_when_disabled():
    # Sanity: hosted probes gate on their own enable flag (not AI_OFFLINE_MODE),
    # so a default (disabled) install never egresses to the hosted service.
    with patch.object(settings, "JIRA_ENABLED", False):
        result = await svc.probe_jira()
    assert result.status == "skipped"
