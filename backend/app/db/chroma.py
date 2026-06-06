"""
Shared ChromaDB client factory.

Single source of truth for constructing the ChromaDB HttpClient so that
telemetry is disabled consistently at every call site.

Why an explicit Settings is required
-------------------------------------
The installed chromadb (0.5.20) does NOT reliably honour the
``ANONYMIZED_TELEMETRY`` environment variable for the HttpClient telemetry
path. Even with ``ANONYMIZED_TELEMETRY=False`` present in the worker pod's
environment, ChromaDB still floods the AI-worker logs with::

    Failed to send telemetry event ClientStartEvent:
    capture() takes 1 positional argument but 3 were given

(~7x per pipeline). Passing an explicit ``Settings(anonymized_telemetry=False)``
to every client is the authoritative fix. ``config.py`` keeps the env
``setdefault`` as a harmless additional safeguard.
"""
from __future__ import annotations

import logging
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import settings

# Live verification (loop cycles 1-2) showed chromadb 0.5.20 attempts the
# ``ClientStartEvent`` posthog capture REGARDLESS of ``anonymized_telemetry``
# (env var AND explicit Settings, both deployed and tested) and fails against
# the installed posthog 7.18 with "capture() takes 1 positional argument but 3
# were given" — flooding the AI-worker logs ~7x per pipeline. It's a
# chromadb/posthog version incompatibility, not an app fault and not functional
# (pipelines complete fine). Since the setting can't suppress it in this
# version, silence the telemetry logger outright. This runs at import (before
# any client is constructed) and after ``import chromadb`` so it wins over
# chromadb's own import-time logger setup.
logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)

__all__ = ["get_chroma_client"]


def get_chroma_client(host: Optional[str] = None, port: Optional[int] = None):
    """Return a ChromaDB HttpClient with anonymous telemetry disabled.

    Args:
        host: Override for ``settings.CHROMA_HOST``.
        port: Override for ``settings.CHROMA_PORT``.
    """
    return chromadb.HttpClient(
        host=host or settings.CHROMA_HOST,
        port=port or settings.CHROMA_PORT,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
