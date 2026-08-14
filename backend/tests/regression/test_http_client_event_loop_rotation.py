"""Regression for pooled HTTP clients crossing short-lived worker loops."""
from __future__ import annotations

import asyncio

import pytest


def test_http_client_rotates_when_worker_event_loop_changes(monkeypatch):
    """A Celery task's client must not be reused by the next task loop."""
    import app.core.http_client as module

    created = []

    class _Client:
        is_closed = False

    def _client(*_args, **_kwargs):
        value = _Client()
        created.append(value)
        return value

    monkeypatch.setattr(module.httpx, "AsyncClient", _client)
    monkeypatch.setattr(module, "_shared_client", None)
    monkeypatch.setattr(module, "_shared_loop", None)

    async def _get_client():
        return module.get_http_client()

    first = asyncio.run(_get_client())
    second = asyncio.run(_get_client())

    assert first is not second
    assert len(created) == 2


@pytest.mark.asyncio
async def test_http_client_is_reused_within_one_long_lived_loop(monkeypatch):
    import app.core.http_client as module

    created = []

    class _Client:
        is_closed = False

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: created.append(_Client()) or created[-1])
    monkeypatch.setattr(module, "_shared_client", None)
    monkeypatch.setattr(module, "_shared_loop", None)

    assert module.get_http_client() is module.get_http_client()
    assert len(created) == 1
