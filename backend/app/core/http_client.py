"""Shared outbound HTTP client configuration.

Two responsibilities:

1. **Central TLS policy** — ``http_verify()`` returns whatever ``verify=``
   value outbound calls should pass to ``httpx``. Callers must never
   hard-code ``verify=False``; the value is driven by ``HTTP_VERIFY_TLS``
   / ``HTTP_CA_BUNDLE`` so operators can change it in one place.

2. **Connection pooling** — ``get_http_client()`` returns a process-wide
   ``httpx.AsyncClient`` singleton so repeated calls reuse connections.
   Per-request needs (timeout, headers, auth) are still set at call time
   via keyword arguments on ``client.get(...)`` / ``client.post(...)``.
   The previous pattern — ``async with httpx.AsyncClient(...) as client``
   inside every call site — forced a fresh TCP + TLS handshake per
   request and could not be tuned centrally.

TLS precedence:
  1. ``HTTP_CA_BUNDLE`` — path to a PEM bundle for internal / self-signed CAs.
     Verification stays strict; the bundle just adds trusted roots.
  2. ``HTTP_VERIFY_TLS`` — boolean. ``False`` disables verification entirely
     and is intended only for isolated lab environments.
  3. Default — ``True`` (system CA bundle).
"""
from __future__ import annotations

import logging
from typing import Optional, Union

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_warned_insecure = False
_shared_client: Optional[httpx.AsyncClient] = None


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


def get_http_client() -> httpx.AsyncClient:
    """Return the process-wide shared :class:`httpx.AsyncClient`.

    Lazily constructed on first call. Callers should pass per-request
    options (``timeout``, ``headers``, ``auth``, ``params``) directly on
    the request methods, e.g.::

        client = get_http_client()
        resp = await client.get(url, headers={"Authorization": ...}, timeout=10.0)

    Do **not** use ``async with`` on the returned client — that would close
    the shared instance and break subsequent callers.
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            verify=http_verify(),
            # Generous default timeout; call sites override per request.
            timeout=httpx.Timeout(30.0, connect=10.0),
            # Pool tuned for dozens of concurrent integrations (Splunk,
            # Jira, OCP, GitHub, Slack…) without saturating the event
            # loop. ``max_keepalive_connections`` controls idle reuse.
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
        )
        logger.debug("http_client.shared_client_created")
    return _shared_client


async def close_http_client() -> None:
    """Close the shared client on application shutdown.

    Safe to call more than once. Called from the FastAPI lifespan handler
    so that in-flight requests drain cleanly before the process exits.
    """
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        try:
            await _shared_client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("http_client.close_failed error=%s", exc)
    _shared_client = None
