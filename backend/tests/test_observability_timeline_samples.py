"""W39: the samples helper's ``socket.getaddrinfo`` patch must not break the integration test's real stores on CI.

The patch is process-wide (``url_safety.socket`` IS the ``socket`` module). Its first version let only IP literals
through, so the Postgres + Mongo integration module passed locally (stores on ``127.0.0.1``) and failed on CI, whose
protected Postgres step addresses them as ``localhost`` ("Name or service not known", PR #344's first CI run).
"""
from __future__ import annotations

import socket

import pytest

from tests import observability_timeline_samples as samples


def test_a_fake_integration_host_never_resolves():
    resolve = samples._resolver()
    with pytest.raises(socket.gaierror):
        resolve("splunk.fake.test", 8089)
    with pytest.raises(socket.gaierror):
        resolve(b"ocp.fake.test", 6443)


def test_an_ip_literal_resolves_to_itself_with_its_port():
    resolve = samples._resolver()
    assert resolve("127.0.0.1", 5432) == [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5432))]
    assert resolve("::1", 27017)[0][0] == socket.AF_INET6


def test_any_other_name_goes_to_the_real_resolver(monkeypatch):
    """`localhost` (CI's store host) must reach the resolver the process had before the patch."""
    seen = []
    monkeypatch.setattr(samples, "_REAL_GETADDRINFO", lambda host, port, *a, **k: seen.append((host, port)) or ["real"])
    assert samples._resolver()("localhost", 5432) == ["real"]
    assert seen == [("localhost", 5432)]
