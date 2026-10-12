"""A fake TestLookup server for driving the SDK end to end without a
network. ``conftest.py`` turns it into the ``wire`` fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx

import testlookup_reporter as tr

SESSION_RESPONSE = {
    "session_id": "s-1",
    "session_token": "st-1",
    "run_id": "r-1",
    "project_id": "11111111-1111-1111-1111-111111111111",
    "expires_in": 3600,
    "created_at": "2026-10-11T00:00:00Z",
}

#: Environment variables that would leak the developer's (or CI's) own setup
#: into a test: config, CI auto-detection, and proxies (an httpx proxy mount
#: would build a real TLS context and ignore the fake transport).
LEAKY_ENV_PREFIXES = (
    "TESTLOOKUP_", "GITHUB_", "GITLAB_", "CI_", "CIRCLE", "JENKINS_", "BUILD_", "SYSTEM_",
)
LEAKY_ENV_NAMES = {"TF_BUILD", "CIRCLECI", "GIT_URL", "CHANGE_ID", "CHANGE_AUTHOR",
                    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}


class Wire:
    """A fake server that answers like the real one, and a record of what
    the SDK sent it.

    Authentication follows ``backend/app/core/deps.py``: the session routes
    accept a JWT as ``Authorization: Bearer`` or an API key as ``X-API-Key``.
    A Bearer value is decoded as a JWT, so a ``qai_`` key sent that way is a
    401 -- the failure the pytest plugin hit with every API key.
    ``/stream/ingest`` takes only ``X-API-Key``; ``/events/batch`` takes only
    the session token.
    """

    def __init__(self) -> None:
        #: keyword arguments of every httpx.AsyncClient the SDK built
        self.clients: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        #: keyword arguments of every resolve_commit_range call
        self.commit_range_calls: list[dict[str, Any]] = []

    # ── fake server ───────────────────────────────────────────────────────

    @staticmethod
    def _authenticated(request: httpx.Request) -> bool:
        bearer = request.headers.get("authorization", "")
        # backend/app/routers/api_keys.py mints every key as "qai_" + suffix.
        if bearer.startswith("Bearer ") and not bearer[len("Bearer "):].startswith("qai_"):
            return True
        return bool(request.headers.get("x-api-key"))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/api/v1/stream/sessions" and method == "POST":
            if not self._authenticated(request):
                return httpx.Response(401, json={"detail": "Could not validate credentials"})
            return httpx.Response(201, json=SESSION_RESPONSE)
        if path.startswith("/api/v1/stream/sessions/") and method == "DELETE":
            if not self._authenticated(request):
                return httpx.Response(401, json={"detail": "Could not validate credentials"})
            return httpx.Response(204)
        if path == "/api/v1/stream/events/batch":
            if request.headers.get("x-session-token") != SESSION_RESPONSE["session_token"]:
                return httpx.Response(401, json={"detail": "Invalid session token"})
            events = json.loads(request.content)["events"]
            return httpx.Response(202, json={"accepted": len(events), "run_id": "r-1"})
        if path == "/api/v1/stream/ingest":
            if not request.headers.get("x-api-key"):
                return httpx.Response(401, json={"detail": "Streaming requires an X-API-Key header"})
            events = json.loads(request.content)["events"]
            return httpx.Response(202, json={
                "accepted": len(events), "run_id": "ci-run-1",
                "session_id": "s-1", "created_session": True,
            })
        return httpx.Response(404, json={"detail": f"no route {method} {path}"})

    # ── what was sent ─────────────────────────────────────────────────────

    def bodies(self, method: str, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(r.content) for r in self.requests
            if r.method == method and r.url.path == path and r.content
        ]

    def first(self, method: str, path_prefix: str) -> Optional[httpx.Request]:
        for r in self.requests:
            if r.method == method and r.url.path.startswith(path_prefix):
                return r
        return None

    def told(self) -> dict[str, Any]:
        """What the server learned about the run, in one shape for the
        session-create path (TestLookupReporter, pytest plugin) and the
        ingest path (LiveStream)."""
        create = self.first("POST", "/api/v1/stream/sessions")
        if create is not None:
            request = create
            body = json.loads(create.content)
            fields = dict(body)
        else:
            request = self.first("POST", "/api/v1/stream/ingest")
            assert request is not None, "the SDK sent nothing"
            meta = json.loads(request.content).get("meta") or {}
            fields = {k: v for k, v in meta.items() if k != "metadata"}
            fields.update((meta.get("metadata") or {}).get("ci_context") or {})
        if request.headers.get("x-api-key"):
            auth = ("X-API-Key", request.headers["x-api-key"])
        else:
            auth = ("Bearer", request.headers.get("authorization", "")[len("Bearer "):])
        commit_range = self.commit_range_calls[0] if self.commit_range_calls else {}
        from commit_range import _user_base, is_collection_enabled
        return {
            **fields,
            "url": f"{request.url.scheme}://{request.url.host}",
            "auth": auth,
            "verify": self.clients[0].get("verify", True) if self.clients else None,
            "commit_range_enabled": is_collection_enabled(
                os.environ, overrides=commit_range.get("overrides"),
                explicit=commit_range.get("enabled"),
            ),
            "commit_range_base": _user_base(
                commit_range.get("explicit_base"), commit_range.get("overrides"), os.environ,
            ),
        }


# ── Driving the pytest plugin's hooks ─────────────────────────────────────────

#: Shaped like a minted key (``qai_`` + 43 url-safe characters); not a key.
FAKE_KEY = "qai_" + "x" * 43
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.e30.not-a-real-signature"


class _PytestGroup:
    def __init__(self) -> None:
        self.defaults: dict[str, Any] = {}

    def addoption(self, name: str, **kwargs: Any) -> None:
        self.defaults[name] = kwargs.get("default")


class PytestParser:
    def __init__(self) -> None:
        self.group = _PytestGroup()

    def getgroup(self, *_args: Any, **_kwargs: Any) -> _PytestGroup:
        return self.group


class _PytestPluginManager:
    def __init__(self) -> None:
        self.plugins: dict[str, Any] = {}

    def register(self, plugin: Any, name: str) -> None:
        self.plugins[name] = plugin


class PytestConfig:
    """The two pieces of ``pytest.Config`` the plugin's hooks touch."""

    def __init__(self, options: dict[str, Any]) -> None:
        self._options = options
        self.pluginmanager = _PytestPluginManager()

    def getoption(self, name: str, default: Any = None) -> Any:
        return self._options.get(name, default)


class PytestReport:
    def __init__(self, nodeid: str, when: str = "call", failed: bool = False) -> None:
        self.nodeid, self.when = nodeid, when
        self.passed, self.failed, self.skipped = not failed, failed, False
        self.longrepr, self.duration = None, 0.01


def run_pytest_plugin(**flags: str) -> Any:
    """Run the plugin through a whole pytest session the way pytest does:
    options registered by ``pytest_addoption`` (``api_key="x"`` stands for
    ``--testlookup-api-key x``), then configure, sessionstart, one report,
    sessionfinish. Returns the plugin, or fails when it did not activate."""
    parser = PytestParser()
    tr.pytest_addoption(parser)
    options = dict(parser.group.defaults)
    for name, value in flags.items():
        option = "--testlookup-" + name.replace("_", "-")
        assert option in options, f"pytest has no {option} option"
        options[option] = value
    config = PytestConfig(options)
    tr.pytest_configure(config)
    plugin = config.pluginmanager.plugins.get("testlookup_live")
    assert plugin is not None, "the plugin did not activate"
    plugin.pytest_sessionstart(None)
    plugin.pytest_runtest_logreport(PytestReport("tests/test_a.py::test_login"))
    plugin.pytest_sessionfinish(None, 0)
    return plugin


def write_properties(path: Path, **keys: str) -> None:
    """Write a testlookup.properties; ``api_key="x"`` → ``testlookup.api.key=x``."""
    names = {"api_key": "testlookup.api.key", "ca_cert_path": "testlookup.ca_cert_path"}
    lines = [f"{names.get(k, 'testlookup.' + k)}={v}" for k, v in keys.items()]
    path.write_bytes(("\n".join(lines) + "\n").encode())
