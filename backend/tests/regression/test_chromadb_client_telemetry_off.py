"""Regression: every ChromaDB client must disable anonymous telemetry.

BUG-001 (rework). Setting ``ANONYMIZED_TELEMETRY=False`` in the environment /
k8s configmap is NOT sufficient — chromadb 0.5.20's HttpClient ignores it and
still floods the AI-worker logs with::

    Failed to send telemetry event ClientStartEvent:
    capture() takes 1 positional argument but 3 were given

The authoritative fix is the shared ``app.db.chroma.get_chroma_client`` helper,
which passes an explicit ``Settings(anonymized_telemetry=False)`` to every
client. This test pins that contract: the helper must call
``chromadb.HttpClient`` with a ``settings=`` whose ``anonymized_telemetry`` is
``False``.

chromadb is not installed in the local dev/test environment, so we inject a
lightweight fake ``chromadb`` (and ``chromadb.config``) into ``sys.modules``
before importing the helper. In CI where the real chromadb is present, the
monkeypatched ``HttpClient`` simply replaces the real one for the test's
duration.
"""
from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

import pytest


def _install_fake_chromadb(monkeypatch):
    """Ensure ``chromadb`` + ``chromadb.config`` are importable with a
    capturable ``HttpClient`` and a ``Settings`` that records its kwargs."""

    class _FakeSettings:
        def __init__(self, anonymized_telemetry: bool = True, **kwargs):
            self.anonymized_telemetry = anonymized_telemetry
            for k, v in kwargs.items():
                setattr(self, k, v)

    http_client = MagicMock(name="HttpClient")

    chromadb_mod = sys.modules.get("chromadb")
    config_mod = sys.modules.get("chromadb.config")

    if chromadb_mod is None:
        chromadb_mod = types.ModuleType("chromadb")
        monkeypatch.setitem(sys.modules, "chromadb", chromadb_mod)
    if config_mod is None:
        config_mod = types.ModuleType("chromadb.config")
        monkeypatch.setitem(sys.modules, "chromadb.config", config_mod)

    monkeypatch.setattr(chromadb_mod, "HttpClient", http_client, raising=False)
    monkeypatch.setattr(config_mod, "Settings", _FakeSettings, raising=False)
    monkeypatch.setattr(chromadb_mod, "config", config_mod, raising=False)

    return http_client, _FakeSettings


def _load_helper():
    # Fresh import so the helper binds to the (possibly faked) chromadb.
    sys.modules.pop("app.db.chroma", None)
    return importlib.import_module("app.db.chroma")


def test_get_chroma_client_disables_telemetry(monkeypatch):
    http_client, _ = _install_fake_chromadb(monkeypatch)
    chroma = _load_helper()

    chroma.get_chroma_client()

    assert http_client.called, "chromadb.HttpClient was not invoked"
    _, kwargs = http_client.call_args
    assert "settings" in kwargs, "HttpClient must be passed an explicit settings="
    assert kwargs["settings"].anonymized_telemetry is False


def test_get_chroma_client_passes_host_and_port(monkeypatch):
    http_client, _ = _install_fake_chromadb(monkeypatch)
    chroma = _load_helper()

    chroma.get_chroma_client(host="chroma.example", port=1234)

    _, kwargs = http_client.call_args
    assert kwargs["host"] == "chroma.example"
    assert kwargs["port"] == 1234
    assert kwargs["settings"].anonymized_telemetry is False


def test_env_setdefault_kept_as_safeguard():
    """The belt-and-suspenders env setdefault is retained in config.py."""
    import os

    # Importing config triggers the module-level setdefault.
    importlib.import_module("app.core.config")
    assert os.environ.get("ANONYMIZED_TELEMETRY") == "False"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
