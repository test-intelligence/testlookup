"""Connector registry — maps source types to connector instances."""
from __future__ import annotations

from app.services.connectors.base import KnowledgeConnectorBase
from app.services.connectors.confluence_connector import ConfluenceKnowledgeConnector
from app.services.connectors.document_connector import DocumentConnector
from app.services.connectors.jira_connector import JiraKnowledgeConnector
from app.services.connectors.url_connector import URLConnector

_REGISTRY: dict[str, KnowledgeConnectorBase] = {
    "jira_issue": JiraKnowledgeConnector(),
    "jira_epic": JiraKnowledgeConnector(),
    "confluence_page": ConfluenceKnowledgeConnector(),
    "uploaded_document": DocumentConnector(),
    "internal_url": URLConnector(),
    "external_url": URLConnector(),
}


def get_connector(source_type: str) -> KnowledgeConnectorBase:
    """Return the connector instance for the given source type."""
    connector = _REGISTRY.get(source_type)
    if connector is None:
        raise ValueError(f"No connector registered for source_type={source_type!r}")
    return connector
