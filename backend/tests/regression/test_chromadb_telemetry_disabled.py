"""
Regression: BUG-001 — ChromaDB anonymous telemetry must be disabled.

Symptom (live homelab, run 493d5c1f, 2026-06-06): the AI worker logged, ~5x per
pipeline, ``Failed to send telemetry event ClientStartEvent: capture() takes 1
positional argument but 3 were given`` — ChromaDB's bundled posthog telemetry
breaking against the installed posthog, plus an unwanted outbound phone-home in
an offline-first app.

Fix: ``app.core.config`` sets ``ANONYMIZED_TELEMETRY=False`` in the process
environment at import time (before any ``chromadb.HttpClient`` is constructed),
and the k8s configmap sets it explicitly in-cluster. This test locks in the
process-level guard so a refactor of config.py can't silently re-enable it.
"""
import importlib
import os


def test_importing_config_disables_chroma_telemetry(monkeypatch):
    # Simulate a fresh process with the var unset, then re-import config.
    monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)
    import app.core.config as config

    importlib.reload(config)
    assert os.environ.get("ANONYMIZED_TELEMETRY") == "False"


def test_explicit_env_override_is_respected(monkeypatch):
    # setdefault must not clobber an operator-provided value.
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "True")
    import app.core.config as config

    importlib.reload(config)
    assert os.environ.get("ANONYMIZED_TELEMETRY") == "True"
