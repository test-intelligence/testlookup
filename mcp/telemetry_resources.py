"""Validated resource identity and private OTLP/HTTP transport configuration."""
from __future__ import annotations

import os
import logging
import re
import threading
import uuid
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import requests
from opentelemetry.sdk.resources import Resource

_INSTANCE = (os.getpid(), str(uuid.uuid4()))


def resource(environment: str) -> Resource:
    global _INSTANCE
    if _INSTANCE[0] != os.getpid():
        _INSTANCE = (os.getpid(), str(uuid.uuid4()))
    revision = os.environ.get("BUILD_REVISION", "")
    installation = os.environ.get("TESTLOOKUP_TELEMETRY_INSTALLATION", "")
    version = os.environ.get("APP_VERSION", "0.1.0")
    return Resource({
        "service.name": "testlookup", "service.namespace": "testlookup",
        "service.instance.id": _INSTANCE[1],
        "service.version": version if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", version) else "unknown",
        "deployment.environment.name": environment if environment in {"development", "staging", "production"} else "unknown",
        "testlookup.service.role": "mcp",
        "testlookup.build.revision": revision if re.fullmatch(r"[0-9a-f]{40}", revision) else "",
        "testlookup.installation": installation if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", installation) else "",
    })


def validated_endpoint(endpoint: str, signal: str) -> str:
    url = urlsplit(endpoint)
    private_http = url.scheme == "http" and url.hostname in {"otel-collector", "localhost", "127.0.0.1", "::1"}
    if (signal not in {"logs", "traces", "metrics"} or not url.hostname
        or (url.scheme != "https" and not private_http) or url.path != f"/v1/{signal}"
        or url.username is not None or url.password is not None or url.query or url.fragment):
        raise ValueError("Expected a private or HTTPS full OTLP/HTTP signal endpoint")
    _ = url.port  # Reject malformed ports before an exporter starts.
    return endpoint


def exporter_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.max_redirects = 0
    return session


def shutdown_bounded(providers: Iterable[Any], *, timeout: float = 2.0) -> None:
    """Bound caller shutdown even if an SDK flush ignores its timeout argument."""
    owned = tuple(providers)
    if not owned:
        return

    def drain() -> None:
        for provider in reversed(owned):
            try:
                provider.force_flush(timeout_millis=2000)
                if type(provider).__module__.startswith("opentelemetry.sdk.metrics"):
                    provider.shutdown(timeout_millis=2000)
                else:
                    provider.shutdown()
            except Exception:  # noqa: BLE001 — drain is isolated from protocol shutdown
                logging.getLogger("testlookup.mcp.telemetry").warning(
                    "MCP telemetry drain failed", extra={"event_code": "provider_shutdown_failed"})

    worker = threading.Thread(target=drain, name="mcp-telemetry-drain", daemon=True)
    worker.start()
    worker.join(timeout=max(0.0, min(timeout, 2.0)))
