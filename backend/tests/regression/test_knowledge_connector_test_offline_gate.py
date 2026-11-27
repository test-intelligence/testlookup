"""Regression coverage for offline knowledge-connector connection tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import ConnectorConfigTestRequest
from app.routers import knowledge_sources as knowledge_routes
from app.services.connectors import confluence_connector, jira_connector


OFFLINE_RESULT = {
    "success": False,
    "latency_ms": 0,
    "error": "offline_mode",
    "detail": "AI_OFFLINE_MODE is enabled — outbound calls are disabled",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["jira_issue", "jira_epic", "confluence_page"])
async def test_route_offline_refuses_before_connector_binding(source_type):
    db = AsyncMock()
    get_connector = MagicMock(side_effect=AssertionError("must not resolve connector"))
    bind_connector = AsyncMock(side_effect=AssertionError("must not resolve deployment secrets"))

    with patch.object(knowledge_routes.settings, "AI_OFFLINE_MODE", True), \
         patch.object(knowledge_routes.svc, "require_rag_enabled_async", AsyncMock()), \
         patch("app.services.connectors.registry.get_connector", get_connector), \
         patch("app.services.connectors.registry.bind_to_deployment", bind_connector):
        result = await knowledge_routes.test_connector(
            ConnectorConfigTestRequest(source_type=source_type), db=db, _=MagicMock(),
        )

    assert result.model_dump() == OFFLINE_RESULT
    get_connector.assert_not_called()
    bind_connector.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["uploaded_document", "internal_url", "external_url"])
async def test_route_offline_allows_connection_tests_without_external_probes(source_type):
    db = AsyncMock()
    connector = MagicMock()
    connector.test_connection = AsyncMock(return_value={
        "success": True,
        "latency_ms": 0,
        "error": None,
        "detail": "No external probe required",
    })
    get_connector = MagicMock(return_value=connector)
    bind_connector = AsyncMock(return_value=connector)

    with patch.object(knowledge_routes.settings, "AI_OFFLINE_MODE", True), \
         patch.object(knowledge_routes.svc, "require_rag_enabled_async", AsyncMock()), \
         patch("app.services.connectors.registry.get_connector", get_connector), \
         patch("app.services.connectors.registry.bind_to_deployment", bind_connector):
        result = await knowledge_routes.test_connector(
            ConnectorConfigTestRequest(source_type=source_type), db=db, _=MagicMock(),
        )

    assert result.success is True
    get_connector.assert_called_once_with(source_type)
    bind_connector.assert_awaited_once_with(connector, db)
    connector.test_connection.assert_awaited_once_with()


class _OfflineSettings:
    AI_OFFLINE_MODE = True

    def __getattr__(self, name):
        raise AssertionError(f"must not read credential setting {name}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("connector_module", "connector_factory"),
    [
        (jira_connector, jira_connector.JiraKnowledgeConnector),
        (confluence_connector, confluence_connector.ConfluenceKnowledgeConnector),
    ],
)
async def test_connector_offline_refuses_before_credentials_or_http(
    connector_module, connector_factory,
):
    http_client = MagicMock(side_effect=AssertionError("must not create HTTP client"))

    with patch.object(connector_module, "settings", _OfflineSettings()), \
         patch.object(connector_module, "get_http_client", http_client):
        result = await connector_factory().test_connection()

    assert result == OFFLINE_RESULT
    http_client.assert_not_called()


@pytest.mark.asyncio
async def test_route_online_still_binds_and_tests_connector():
    db = AsyncMock()
    connector = MagicMock()
    bound_connector = MagicMock()
    bound_connector.test_connection = AsyncMock(return_value={
        "success": True,
        "latency_ms": 12,
        "error": None,
        "detail": "Authenticated",
    })
    get_connector = MagicMock(return_value=connector)
    bind_connector = AsyncMock(return_value=bound_connector)

    with patch.object(knowledge_routes.settings, "AI_OFFLINE_MODE", False), \
         patch.object(knowledge_routes.svc, "require_rag_enabled_async", AsyncMock()), \
         patch("app.services.connectors.registry.get_connector", get_connector), \
         patch("app.services.connectors.registry.bind_to_deployment", bind_connector):
        result = await knowledge_routes.test_connector(
            ConnectorConfigTestRequest(source_type="jira_issue"), db=db, _=MagicMock(),
        )

    assert result.success is True
    get_connector.assert_called_once_with("jira_issue")
    bind_connector.assert_awaited_once_with(connector, db)
    bound_connector.test_connection.assert_awaited_once_with()
