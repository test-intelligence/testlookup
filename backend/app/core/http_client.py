"""Shared outbound HTTP configuration.

Callers must not construct ``httpx.AsyncClient(verify=False, ...)`` directly.
Instead, pass ``verify=http_verify()`` so TLS verification is controlled by a
single configuration setting and can be audited centrally.

Precedence:
  1. ``HTTP_CA_BUNDLE`` — path to a PEM bundle for internal / self-signed CAs.
     Verification stays strict; the bundle just adds trusted roots.
  2. ``HTTP_VERIFY_TLS`` — boolean. ``False`` disables verification entirely
     and is intended only for isolated lab environments.
  3. Default — ``True`` (system CA bundle).
"""
from __future__ import annotations

import logging
from typing import Union

from app.core.config import settings

logger = logging.getLogger(__name__)

_warned_insecure = False


def http_verify() -> Union[bool, str]:
    """Return the value to pass as ``httpx.AsyncClient(verify=...)``."""
    global _warned_insecure

    if settings.HTTP_CA_BUNDLE:
        return settings.HTTP_CA_BUNDLE

    if not settings.HTTP_VERIFY_TLS:
        if not _warned_insecure:
            logger.warning(
                "HTTP_VERIFY_TLS is disabled — outbound HTTPS calls skip "
                "certificate validation. Set HTTP_CA_BUNDLE for self-signed CAs."
            )
            _warned_insecure = True
        return False

    return True
