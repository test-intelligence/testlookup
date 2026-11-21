"""Regression: knowledge sync fetched external content even in offline mode.

Bug pinned (review/knowledge-sync-service, 2026-06-02): ``run_sync`` had no
``AI_OFFLINE_MODE`` gate (nor did the connectors / router), so syncing a
Jira / Confluence / external-URL source made outbound network calls even when
the air-gapped kill switch was on — violating "offline mode is a hard gate
above feature flags". Fix: short-circuit external-fetch source types before
touching the network (UPLOADED_DOC stays enabled: it is read from the
deployment's own object storage).

2026-11-19: INTERNAL_URL is gated too. It was left out as "same-network", but
it is fetched by the same public-only connector as EXTERNAL_URL, so in offline
mode it was a way to reach a public host.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.core.config import settings  # noqa: E402
from app.models.postgres import KnowledgeSourceType  # noqa: E402
from app.services import knowledge_sync_service as svc  # noqa: E402
from app.services.connectors.base import ConnectorFetchError  # noqa: E402


def _source(source_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), source_type=source_type,
        canonical_url="https://example.com/x", external_id=None,
        content_hash="h", sync_status="synced", sync_error=None,
        last_synced_at=None, storage_path=None, created_at=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stype", [
    KnowledgeSourceType.JIRA_ISSUE.value,
    KnowledgeSourceType.JIRA_EPIC.value,
    KnowledgeSourceType.CONFLUENCE_PAGE.value,
    KnowledgeSourceType.INTERNAL_URL.value,
    KnowledgeSourceType.EXTERNAL_URL.value,
])
async def test_offline_skips_external_fetch_without_connector(stype):
    db = AsyncMock()
    with patch.object(settings, "AI_OFFLINE_MODE", True), \
         patch.object(svc, "get_connector", MagicMock(side_effect=AssertionError("must not fetch"))) as gc:
        result = await svc.run_sync(db, _source(stype), trigger="manual")

    assert result["status"] == "skipped"
    assert result["reason"] == "offline_mode"
    gc.assert_not_called()        # no connector resolved → no network call
    db.commit.assert_not_awaited()  # no state mutation persisted


@pytest.mark.asyncio
@pytest.mark.parametrize("stype", [
    KnowledgeSourceType.UPLOADED_DOC.value,
])
async def test_offline_allows_uploaded(stype):
    # Read from the deployment's own storage → the offline gate must NOT short-circuit.
    # Stub the connector to fail fast so we only assert the gate let it through.
    conn = MagicMock()
    conn.fetch_content = AsyncMock(side_effect=ConnectorFetchError("stop", retryable=False))
    db = AsyncMock()
    db.add = MagicMock()
    with patch.object(settings, "AI_OFFLINE_MODE", True), \
         patch.object(svc, "get_connector", MagicMock(return_value=conn)) as gc:
        result = await svc.run_sync(db, _source(stype), trigger="manual")

    gc.assert_called_once()       # gate did not short-circuit → fetch attempted
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_online_external_proceeds_to_fetch():
    conn = MagicMock()
    conn.fetch_content = AsyncMock(side_effect=ConnectorFetchError("stop", retryable=False))
    db = AsyncMock()
    db.add = MagicMock()
    from app.services import defect_jira_service

    # A Jira source first asks which Jira project its project may read: here
    # the project has no Jira key, so the sync is not narrowed.
    no_key = AsyncMock(return_value=defect_jira_service.JiraKeyScope(project_key=None, default_key=None, any_key=False))
    with patch.object(settings, "AI_OFFLINE_MODE", False), \
         patch.object(defect_jira_service, "jira_key_scope", no_key), \
         patch.object(svc, "bind_to_deployment", AsyncMock(return_value=conn)), \
         patch.object(svc, "get_connector", MagicMock(return_value=conn)) as gc:
        result = await svc.run_sync(db, _source(KnowledgeSourceType.JIRA_ISSUE.value))

    conn.fetch_content.assert_awaited_once()
    gc.assert_called_once()     # online → external fetch attempted
    assert result["status"] == "failed"
