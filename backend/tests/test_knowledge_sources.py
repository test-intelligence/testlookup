"""
Knowledge Source Registry — Unit Tests (RAG-1 / RAG-2 / RAG-3).

Tests source CRUD, duplicate handling, connector test, domain allowlist,
classification, and access control.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.postgres import (
    KnowledgeClassification,
    KnowledgeSource,
    KnowledgeSourceType,
    KnowledgeSyncStatus,
)
from app.services.connectors.base import FetchedContent, KnowledgeConnectorBase


# ── Model & Enum Tests ────────────────────────────────────────────────────────


class TestKnowledgeSourceEnums:
    def test_source_types_are_strings(self):
        assert KnowledgeSourceType.JIRA_ISSUE.value == "jira_issue"
        assert KnowledgeSourceType.CONFLUENCE_PAGE.value == "confluence_page"
        assert KnowledgeSourceType.UPLOADED_DOC.value == "uploaded_document"
        assert KnowledgeSourceType.INTERNAL_URL.value == "internal_url"
        assert KnowledgeSourceType.EXTERNAL_URL.value == "external_url"
        assert KnowledgeSourceType.JIRA_EPIC.value == "jira_epic"

    def test_sync_statuses(self):
        assert KnowledgeSyncStatus.PENDING.value == "pending"
        assert KnowledgeSyncStatus.SYNCING.value == "syncing"
        assert KnowledgeSyncStatus.SYNCED.value == "synced"
        assert KnowledgeSyncStatus.FAILED.value == "failed"
        assert KnowledgeSyncStatus.SKIPPED.value == "skipped"

    def test_classifications(self):
        assert KnowledgeClassification.PUBLIC.value == "public"
        assert KnowledgeClassification.INTERNAL.value == "internal"
        assert KnowledgeClassification.CONFIDENTIAL.value == "confidential"
        assert KnowledgeClassification.RESTRICTED.value == "restricted"


class TestKnowledgeSourceModel:
    def test_explicit_sync_status(self):
        """Server defaults apply at DB level; test with explicit values."""
        src = KnowledgeSource(
            project_id=uuid.uuid4(),
            source_type=KnowledgeSourceType.JIRA_ISSUE.value,
            title="Test",
            canonical_url="https://jira.example.com/browse/PROJ-1",
            sync_status=KnowledgeSyncStatus.PENDING.value,
        )
        assert src.sync_status == KnowledgeSyncStatus.PENDING.value

    def test_explicit_classification(self):
        src = KnowledgeSource(
            project_id=uuid.uuid4(),
            source_type=KnowledgeSourceType.JIRA_ISSUE.value,
            title="Test",
            canonical_url="https://jira.example.com/browse/PROJ-1",
            classification=KnowledgeClassification.INTERNAL.value,
        )
        assert src.classification == KnowledgeClassification.INTERNAL.value

    def test_explicit_archived_false(self):
        src = KnowledgeSource(
            project_id=uuid.uuid4(),
            source_type=KnowledgeSourceType.JIRA_ISSUE.value,
            title="Test",
            canonical_url="https://jira.example.com/browse/PROJ-1",
            is_archived=False,
        )
        assert src.is_archived is False


# ── Schema Tests ──────────────────────────────────────────────────────────────


class TestKnowledgeSourceSchemas:
    def test_create_schema_accepts_valid(self):
        from app.models.schemas import KnowledgeSourceCreate
        s = KnowledgeSourceCreate(
            source_type="jira_issue",
            title="PROJ-123",
            canonical_url="https://jira.example.com/browse/PROJ-123",
            external_id="PROJ-123",
        )
        assert s.source_type == "jira_issue"
        assert s.classification == "internal"

    def test_update_schema_all_optional(self):
        from app.models.schemas import KnowledgeSourceUpdate
        u = KnowledgeSourceUpdate()
        assert u.title is None
        assert u.classification is None
        assert u.is_archived is None

    def test_response_schema_from_dict(self):
        from app.models.schemas import KnowledgeSourceResponse
        from datetime import datetime, timezone
        pid = uuid.uuid4()
        sid = uuid.uuid4()
        resp = KnowledgeSourceResponse.model_validate({
            "id": sid,
            "project_id": pid,
            "source_type": "jira_issue",
            "title": "Test",
            "canonical_url": "https://jira.example.com/browse/PROJ-1",
            "sync_status": "pending",
            "classification": "internal",
            "is_archived": False,
            "created_at": datetime.now(timezone.utc),
        })
        assert resp.project_id == pid
        assert resp.source_type == "jira_issue"

    def test_connector_test_result(self):
        from app.models.schemas import ConnectorTestResult
        r = ConnectorTestResult(success=True, latency_ms=42)
        assert r.success is True
        assert r.latency_ms == 42

    def test_domain_allowlist_update(self):
        from app.models.schemas import KnowledgeDomainAllowlistUpdate
        u = KnowledgeDomainAllowlistUpdate(domains=["jira.corp.com", "confluence.corp.com"])
        assert len(u.domains) == 2


# ── Connector Tests ───────────────────────────────────────────────────────────


class TestConnectorBase:
    def test_fetched_content_hash(self):
        h = FetchedContent.compute_hash("hello world")
        assert len(h) == 64  # SHA-256 hex digest

    def test_fetched_content_hash_deterministic(self):
        h1 = FetchedContent.compute_hash("test content")
        h2 = FetchedContent.compute_hash("test content")
        assert h1 == h2

    def test_fetched_content_hash_changes_with_content(self):
        h1 = FetchedContent.compute_hash("content A")
        h2 = FetchedContent.compute_hash("content B")
        assert h1 != h2


class TestConnectorRegistry:
    def test_all_source_types_registered(self):
        from app.services.connectors.registry import get_connector
        for st in KnowledgeSourceType:
            connector = get_connector(st.value)
            assert isinstance(connector, KnowledgeConnectorBase)

    def test_unknown_type_raises(self):
        from app.services.connectors.registry import get_connector
        with pytest.raises(ValueError, match="No connector"):
            get_connector("nonexistent_type")


@pytest.mark.asyncio
class TestConnectorStubs:
    async def test_jira_test_connection_returns_dict(self):
        from app.services.connectors.jira_connector import JiraKnowledgeConnector
        result = await JiraKnowledgeConnector().test_connection()
        # Without real credentials, returns success=False with a helpful error
        assert "success" in result
        assert "latency_ms" in result

    async def test_jira_connector_type(self):
        from app.services.connectors.jira_connector import JiraKnowledgeConnector
        assert JiraKnowledgeConnector().connector_type == "jira_issue"

    async def test_confluence_test_connection_returns_dict(self):
        from app.services.connectors.confluence_connector import ConfluenceKnowledgeConnector
        result = await ConfluenceKnowledgeConnector().test_connection()
        assert "success" in result
        assert "latency_ms" in result

    async def test_confluence_connector_type(self):
        from app.services.connectors.confluence_connector import ConfluenceKnowledgeConnector
        assert ConfluenceKnowledgeConnector().connector_type == "confluence_page"

    async def test_document_connector_type(self):
        from app.services.connectors.document_connector import DocumentConnector
        assert DocumentConnector().connector_type == "uploaded_document"

    async def test_url_test_connection(self):
        from app.services.connectors.url_connector import URLConnector
        result = await URLConnector().test_connection()
        assert result["success"] is True

    async def test_url_connector_type(self):
        from app.services.connectors.url_connector import URLConnector
        assert URLConnector().connector_type == "internal_url"


# ── Domain Allowlist Tests ────────────────────────────────────────────────────


class TestDomainAllowlistValidation:
    def test_empty_allowlist_permits_all(self):
        from app.services.knowledge_source_service import _validate_url_domain
        # Should not raise
        _validate_url_domain("https://any-domain.com/page", [])

    def test_matching_domain_passes(self):
        from app.services.knowledge_source_service import _validate_url_domain
        _validate_url_domain("https://jira.corp.com/browse/PROJ-1", ["jira.corp.com"])

    def test_subdomain_passes(self):
        from app.services.knowledge_source_service import _validate_url_domain
        _validate_url_domain("https://east.jira.corp.com/browse/PROJ-1", ["jira.corp.com"])

    def test_blocked_domain_raises(self):
        from fastapi import HTTPException
        from app.services.knowledge_source_service import _validate_url_domain
        with pytest.raises(HTTPException) as exc_info:
            _validate_url_domain("https://evil.com/page", ["jira.corp.com"])
        assert exc_info.value.status_code == 422
        assert "not in the approved" in exc_info.value.detail

    def test_partial_match_blocked(self):
        from fastapi import HTTPException
        from app.services.knowledge_source_service import _validate_url_domain
        with pytest.raises(HTTPException):
            _validate_url_domain("https://notjira.corp.com/page", ["jira.corp.com"])


# ── Feature Flag Tests ────────────────────────────────────────────────────────


class TestFeatureFlag:
    """These used to exercise ``require_rag_enabled()`` — a sync, env-only gate
    with **zero callers**. Passing tests over dead code were part of why three
    disagreeing gates survived. They now drive the gate the endpoints use.
    """

    @pytest.mark.asyncio
    async def test_gate_raises_503_when_the_flag_is_off(self, monkeypatch):
        from fastapi import HTTPException
        from app.services import knowledge_source_service as svc

        async def _off(key, **kwargs):
            assert key == "knowledge_rag"
            return False

        monkeypatch.setattr("app.services.feature_flags.is_enabled", _off)
        with pytest.raises(HTTPException) as exc_info:
            await svc.require_rag_enabled_async(object())
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_gate_passes_when_the_flag_is_on(self, monkeypatch):
        from app.services import knowledge_source_service as svc

        async def _on(key, **kwargs):
            return True

        monkeypatch.setattr("app.services.feature_flags.is_enabled", _on)
        await svc.require_rag_enabled_async(object())  # must not raise

    def test_the_dead_sync_gate_is_gone(self):
        """It read the env var only, so it could never agree with the flag."""
        from app.services import knowledge_source_service as svc
        assert not hasattr(svc, "require_rag_enabled")
        assert not hasattr(svc, "_is_rag_enabled_from_db")
