"""Every outbound HTTP client honours the deployment's TLS policy.

``app/core/http_client.py`` is the central TLS policy: ``http_verify()`` turns
``HTTP_CA_BUNDLE`` (a PEM bundle for internal or self-signed CAs) and
``HTTP_VERIFY_TLS`` into the value for ``verify=``. Fifteen call sites built
their own ``httpx.AsyncClient(timeout=...)`` without it -- GitHub and GitLab
integrations, CODEOWNERS, commit attribution, PR comments, release sync, the
fixer agent, the Ollama check -- so on a deployment behind an internal CA
(GitHub Enterprise, GitLab, Jira) those paths failed TLS while the rest of the
app worked. Found 2026-10-10 testing Jira against a fake served over TLS:
fix-version sync failed where defect filing succeeded.

The guard makes it structural: a client constructed under ``app/`` passes
``verify=http_verify()``, or a ``transport=`` that carries the policy itself
(the public-only and local-only clients).
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.regression

APP = Path(__file__).resolve().parents[2] / "app"
POLICY_MODULE = APP / "core" / "http_client.py"
BUNDLE = "/etc/testlookup-ca/bundle.pem"


def _client_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("AsyncClient", "Client")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "httpx"
        ):
            yield node


def _honours_policy(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "transport":
            return True
        if (
            kw.arg == "verify"
            and isinstance(kw.value, ast.Call)
            and isinstance(kw.value.func, ast.Name)
            and kw.value.func.id == "http_verify"
        ):
            return True
    return False


def test_every_outbound_client_passes_the_tls_policy():
    offenders = []
    scanned = 0
    for path in sorted(APP.rglob("*.py")):
        if path == POLICY_MODULE:
            continue  # defines the policy; its clients pass http_verify()'s value directly
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _client_calls(tree):
            scanned += 1
            if not _honours_policy(call):
                offenders.append(f"{path.relative_to(APP.parent)}:{call.lineno}")
    assert scanned >= 16, f"the scan found only {scanned} clients -- did the pattern drift?"
    assert not offenders, (
        "httpx clients that ignore HTTP_CA_BUNDLE / HTTP_VERIFY_TLS; pass "
        "verify=http_verify() (from app.core.http_client) or use get_http_client():\n  "
        + "\n  ".join(offenders)
    )


class _Recorder:
    """Stands in for ``httpx.AsyncClient``: records its kwargs, answers 200."""

    seen: list[dict] = []

    def __init__(self, *args, **kwargs):
        _Recorder.seen.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, *args, **kwargs):
        return SimpleNamespace(status_code=200)

    async def get(self, *args, **kwargs):
        return SimpleNamespace(status_code=200)


async def _pr_comment(svc):
    await svc._request("GET", "https://ghe.corp.example/api/v3/x", headers={})


async def _gitlab(svc):
    await svc._request("GET", "https://gitlab.corp.example/api/v4/x", headers={})


async def _commit_attribution(svc):
    await svc._gh_get("https://ghe.corp.example/api/v3/x", headers={})


@pytest.mark.asyncio
@pytest.mark.parametrize("module, call", [
    ("app.services.github_pr_comment_service", _pr_comment),
    ("app.services.gitlab_integration_service", _gitlab),
    ("app.services.commit_attribution_service", _commit_attribution),
])
async def test_an_internal_ca_bundle_reaches_the_client(monkeypatch, module, call):
    import importlib

    from app.core.config import settings

    svc = importlib.import_module(module)
    _Recorder.seen = []
    monkeypatch.setattr(settings, "HTTP_CA_BUNDLE", BUNDLE)
    monkeypatch.setattr(svc.httpx, "AsyncClient", _Recorder)

    await call(svc)

    assert _Recorder.seen and _Recorder.seen[0].get("verify") == BUNDLE
