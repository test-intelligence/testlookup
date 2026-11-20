"""The browser telemetry route is bounded, and keeps no URL query or fragment.

``POST /api/v1/observability/frontend`` takes no credential: the browser posts
its errors and web vitals from signed-out pages too. So:

* a batch holds at most 50 errors and 50 vitals (more is refused with 422; the
  browser's own buffer holds fewer);
* the page address of each record is cut at the first ``?`` or ``#``. The
  product carries invite, password-reset and SSO values in the URL fragment so
  that they never reach a server, and this route must not be the exception;
* the route is rate limited per client address, like the sign-in routes;
* a submitted error is logged at WARNING, never ERROR (an anonymous caller must
  not be able to raise the error rate), through structlog fields, so the
  privacy redaction reads every value.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers import observability

pytestmark = pytest.mark.regression

PATH = "/api/v1/observability/frontend"
REPO = Path(__file__).resolve().parents[3]
FRAGMENT_TOKEN = "fake-invite-token-zzz"


class _Recorder:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def _log(self, level):
        return lambda event, **fields: self.calls.append((level, event, fields))

    def __getattr__(self, name):
        if name in {"debug", "info", "warning", "error", "critical", "exception"}:
            return self._log(name)
        raise AttributeError(name)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(observability, "logger", rec)
    return rec


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(observability.router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _error(**over) -> dict:
    return {"type": "error", "message": "boom", "url": "https://app.test/runs", **over}


def _vital(**over) -> dict:
    return {"name": "LCP", "value": 1234.5678, "rating": "good", "url": "https://app.test/runs", **over}


# ── Size ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["errors", "vitals"])
async def test_a_batch_of_51_is_refused(field, recorder):
    item = _error() if field == "errors" else _vital()
    async with _client() as client:
        response = await client.post(PATH, json={field: [item] * 51})
    assert response.status_code == 422
    assert recorder.calls == [], "a refused batch must not be logged"


async def test_a_batch_of_50_of_each_is_accepted(recorder):
    async with _client() as client:
        response = await client.post(PATH, json={"errors": [_error()] * 50, "vitals": [_vital()] * 50})
    assert response.status_code == 202
    assert response.json() == {"received": {"errors": 50, "vitals": 50}}
    assert len(recorder.calls) == 100


async def test_a_batch_of_three_is_logged_as_three_warnings(recorder):
    async with _client() as client:
        response = await client.post(PATH, json={"errors": [_error(message=f"e{i}") for i in range(3)]})
    assert response.status_code == 202
    assert [(level, event) for level, event, _ in recorder.calls] == [("warning", "frontend_error")] * 3
    assert [fields["error"] for _, _, fields in recorder.calls] == ["e0", "e1", "e2"]


def test_the_browser_buffer_fits_in_one_batch():
    """The reporter adds one "events dropped" record to a full buffer before it sends."""
    source = (REPO / "frontend" / "src" / "utils" / "errorReporting.ts").read_text(encoding="utf-8")
    errors = int(re.search(r"const MAX_BUFFERED_ERRORS = (\d+)", source).group(1))
    vitals = int(re.search(r"const MAX_BUFFERED_VITALS = (\d+)", source).group(1))
    assert errors + 1 <= observability.MAX_BATCH_ITEMS, "a full error buffer plus the overflow record is refused"
    assert vitals <= observability.MAX_BATCH_ITEMS


# ── The page address ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        (f"https://app.test/accept-invite#token={FRAGMENT_TOKEN}", "https://app.test/accept-invite"),
        (f"https://app.test/sso/callback?next=/x#code={FRAGMENT_TOKEN}", "https://app.test/sso/callback"),
        (f"https://app.test/runs?ticket={FRAGMENT_TOKEN}", "https://app.test/runs"),
        (f"#token={FRAGMENT_TOKEN}", ""),
        ("https://app.test/runs", "https://app.test/runs"),
    ],
)
async def test_the_page_address_loses_its_query_and_fragment(sent, kept, recorder):
    async with _client() as client:
        response = await client.post(PATH, json={"errors": [_error(url=sent)], "vitals": [_vital(url=sent)]})
    assert response.status_code == 202
    assert [fields["url"] for _, _, fields in recorder.calls] == [kept, kept]
    assert FRAGMENT_TOKEN not in repr(recorder.calls)


def test_the_models_strip_the_address_themselves():
    assert observability.FrontendError(**_error(url=f"https://a.test/p?q=1#token={FRAGMENT_TOKEN}")).url == "https://a.test/p"
    assert observability.WebVitalReport(**_vital(url=f"https://a.test/p#token={FRAGMENT_TOKEN}")).url == "https://a.test/p"


# ── What is logged ───────────────────────────────────────────────────────────


async def test_a_vital_is_info_and_a_poor_one_a_warning(recorder):
    async with _client() as client:
        await client.post(PATH, json={"vitals": [_vital(), _vital(rating="poor")]})
    assert [(level, event) for level, event, _ in recorder.calls] == [("info", "web_vital"), ("warning", "web_vital")]
    assert recorder.calls[0][2]["value"] == 1234.568


async def test_nothing_is_logged_at_error(recorder):
    async with _client() as client:
        await client.post(PATH, json={"errors": [_error()], "vitals": [_vital(rating="poor")]})
    assert {level for level, _, _ in recorder.calls} == {"warning"}


async def test_the_logged_fields_pass_the_privacy_redaction(recorder):
    """The message and the stacks ride in free-text fields, so a secret in them is replaced."""
    from app.core.logging_config import _FREE_TEXT_FIELDS, _privacy_redaction

    jwt = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiJmYWtlLXVzZXIifQ" + "." + "ZmFrZXNpZ25hdHVyZWZha2U"
    async with _client() as client:
        await client.post(
            PATH,
            json={"errors": [_error(message=f"fetch failed: Authorization: Bearer {jwt}", stack=f"at x (password=fakepw123) Bearer {jwt}")]},
        )
    _, event, fields = recorder.calls[0]
    assert {"error", "stack"} <= set(fields) and {"error", "stack"} <= _FREE_TEXT_FIELDS
    rendered = _privacy_redaction(None, "warning", {"event": event, **fields})
    assert jwt not in repr(rendered)
    assert "fakepw123" not in repr(rendered)


def test_the_module_logs_through_structlog():
    """A stdlib logger with ``extra=`` drops every field with the shipped formatter."""
    source = Path(observability.__file__).read_text(encoding="utf-8")
    assert "extra=" not in source
    assert hasattr(observability.logger, "bind"), "the module logger must be a structlog logger"


# ── Oversized values ─────────────────────────────────────────────────────────


async def test_long_values_are_cut(recorder):
    async with _client() as client:
        response = await client.post(
            PATH,
            json={
                "errors": [
                    _error(
                        type="t" * 500,
                        message="m" * 9000,
                        stack="s" * 9000,
                        component_stack="c" * 9000,
                        url="https://app.test/" + "p" * 9000,
                        user_agent="u" * 9000,
                        context={"blob": "x" * 9000},
                    )
                ]
            },
        )
    assert response.status_code == 202
    fields = recorder.calls[0][2]
    assert len(fields["error"]) == 2000 and len(fields["stack"]) == 5000
    assert len(fields["component_stack"]) == 5000 and len(fields["url"]) == 2000
    assert len(fields["user_agent"]) == 500 and len(fields["kind"]) == 64
    assert fields["context"] == {"truncated": True}


# ── Rate limit ───────────────────────────────────────────────────────────────


def test_the_route_is_mounted_and_rate_limited():
    from app.main import _AUTH_LIMITERS, _AUTH_RATE_LIMITS, app

    assert PATH in _AUTH_RATE_LIMITS, f"{PATH} has no per-address rate limit"
    limit, message = _AUTH_RATE_LIMITS[PATH]
    assert limit.endswith("/minute") and int(limit.split("/")[0]) <= 60 and message
    assert PATH in _AUTH_LIMITERS
    assert any(
        getattr(r, "path", "") == PATH and "POST" in getattr(r, "methods", set()) for r in app.routes
    ), f"{PATH} is not mounted"
