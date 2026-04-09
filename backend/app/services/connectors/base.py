"""Base connector interface for knowledge source ingestion."""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FetchedContent:
    """Normalized output from any connector's fetch_content() call."""

    raw_text: str
    content_hash: str
    title: str
    source_url: str
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)

    @staticmethod
    def compute_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ConnectorFetchError(Exception):
    """Raised by connectors when content cannot be retrieved."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class KnowledgeConnectorBase(abc.ABC):
    """
    Interface every knowledge connector must implement.

    Connectors are stateless — credentials come from settings, not instance state.
    """

    @property
    @abc.abstractmethod
    def connector_type(self) -> str:
        """Unique string matching KnowledgeSourceType values."""
        ...

    @abc.abstractmethod
    async def test_connection(self) -> dict:
        """
        Probe connectivity and credential validity.

        Returns: {'success': bool, 'latency_ms': int|None, 'error': str|None, 'detail': str|None}
        Must not raise — return success=False with error message instead.
        """
        ...

    @abc.abstractmethod
    async def fetch_content(
        self,
        canonical_url: str,
        external_id: Optional[str] = None,
    ) -> FetchedContent:
        """
        Retrieve and normalize content for the given source.

        Raises ConnectorFetchError on unrecoverable failures.
        """
        ...
