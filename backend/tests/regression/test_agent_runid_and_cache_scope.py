"""Regression: run_triage_agent rejected the live caller's run_id/project_id
kwargs, and its analysis caches weren't tenant-scoped.

Bugs pinned (review/agent-service, 2026-06-01):

1. ``worker/tasks.py::run_live_test_analysis`` (dispatched per live failure)
   calls ``run_triage_agent(..., run_id=run_id, project_id=project_id)``, but
   the function accepted neither → TypeError every invocation → live
   root-cause analysis never completed. Fix: accept both params.

2. The exact (Redis) + semantic (ChromaDB) analysis caches were keyed on test
   content only, no project. A slow-path analysis embeds project-specific
   Splunk/OCP evidence, so an identical failure in another project was served
   the first project's evidence. Fix: scope the cache key / collection by
   project_id.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.services import agent as svc  # noqa: E402


def test_run_triage_agent_accepts_live_caller_kwargs():
    """The signature must accept run_id + project_id — the exact kwargs
    run_live_test_analysis passes — or every live analysis TypeErrors."""
    params = inspect.signature(svc.run_triage_agent).parameters
    assert "run_id" in params
    assert "project_id" in params


def test_analysis_cache_key_is_project_scoped():
    a = svc._scoped_cache_key("LoginTest.test_x", "NPE", "stack", "project-A")
    b = svc._scoped_cache_key("LoginTest.test_x", "NPE", "stack", "project-B")
    # Same failure content, different tenants → different cache entries.
    assert a != b
    assert a.endswith(":proj:project-A")
    # No project → NO key, and therefore no cache (re-audit M15).
    #
    # This used to assert the opposite: that a missing project "falls back to
    # the legacy global key (back-compat)". That fallback was the defect. Both
    # callers pass None whenever the analysis context lacks a project, so the
    # fallback put every such analysis -- which embeds project-specific
    # Splunk/OCP evidence -- into one namespace shared by all tenants. Pinning
    # it as back-compat is what kept it alive.
    g = svc._scoped_cache_key("LoginTest.test_x", "NPE", "stack", None)
    assert g is None


@pytest.mark.asyncio
async def test_semantic_cache_collection_is_project_scoped():
    """The semantic (ChromaDB) collection name must include the project so a
    similar failure in another tenant can't match a cached analysis."""
    pytest.importorskip("chromadb")
    from unittest.mock import MagicMock, patch

    from app.services import semantic_cache as sc

    captured = {}

    async def _fake_client():
        client = MagicMock()
        client.get_or_create_collection = MagicMock(
            side_effect=lambda name, **kw: captured.setdefault("name", name) or MagicMock()
        )
        return client

    with patch.object(sc, "_get_chroma_client", _fake_client):
        await sc._get_or_create_collection("project-XYZ")

    assert captured["name"] == "ai_analysis_cache_project-XYZ"
