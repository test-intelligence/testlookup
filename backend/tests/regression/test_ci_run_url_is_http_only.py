"""A CI job link is kept only when it is an http(s) URL, and mail numbers are escaped.

``ci_run_url`` arrives with an ingest (JSON batch, file upload, live session) and is
printed as a link in the run-report e-mail and the app. Whoever holds an ingest key
chose where that link points, with any scheme. The rule now:

- at ingest a value that does not start with ``http://`` or ``https://`` is DROPPED
  (stored as "no link") and one log line is written; the upload itself still succeeds,
  so an existing CI job does not start failing;
- the run-report e-mail prints no link for a stored value that is not http(s)
  (covers rows stored before this rule);
- the count fields of the executive e-mail panel are escaped like every other value.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import IngestPayload, LiveSessionCreate
from app.services.notification import email_templates
from app.services.notification import run_report as rr

pytestmark = pytest.mark.regression

_HTTPS = "https://ci.example.com/acme/webapp/runs/99"
_BAD = [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "//ci.example.com/run/1",
    "/run/1",
    "ftp://ci.example.com/run/1",
    "  ",
    " javascript:alert(1)//https://ci.example.com",
]


def _batch(**overrides) -> dict:
    base = {
        "project_id": str(uuid.uuid4()),
        "build_number": "ci-1234",
        "results": [{"test_name": "t", "suite_name": "s", "status": "PASSED"}],
    }
    base.update(overrides)
    return base


# -- JSON bodies ---------------------------------------------------------------


@pytest.mark.parametrize("value", _BAD)
def test_a_batch_drops_a_ci_link_that_is_not_http(value):
    assert IngestPayload(**_batch(ci_run_url=value)).ci_run_url is None


@pytest.mark.parametrize("value", _BAD)
def test_a_live_session_drops_a_ci_link_that_is_not_http(value):
    payload = LiveSessionCreate(project_id="demo", client_name="pytest-runner", ci_run_url=value)
    assert payload.ci_run_url is None


@pytest.mark.parametrize("value", [_HTTPS, "http://jenkins.internal:8080/job/x/12/", "HTTPS://CI.EXAMPLE.COM/1"])
def test_an_http_ci_link_is_kept_as_sent(value):
    assert IngestPayload(**_batch(ci_run_url=value)).ci_run_url == value
    assert LiveSessionCreate(project_id="demo", client_name="c", ci_run_url=value).ci_run_url == value


def test_a_batch_without_a_ci_link_is_unchanged():
    assert IngestPayload(**_batch()).ci_run_url is None


# -- POST /api/v1/ingest/file (multipart) --------------------------------------

_JUNIT = (
    b'<?xml version="1.0"?><testsuite name="s" tests="1">'
    b'<testcase name="checkout_flow" classname="pay"/></testsuite>'
)


@pytest.fixture
def file_route(monkeypatch):
    """The real app with auth, DB, storage and the broker replaced; yields the queued task kwargs."""
    pytest.importorskip("asyncpg")
    from app.core.deps import get_api_key_context, get_current_user_or_api_key, get_db
    from app.main import app

    user = SimpleNamespace(
        id=uuid.uuid4(), username="qa_runner", email="qa@example.com", full_name="QA",
        role="QA_ENGINEER", is_active=True, must_change_password=False,
    )
    queued: list[dict] = []

    async def _db():
        yield AsyncMock()

    async def _ctx():
        return user, None

    async def _open(*_a, **_kw):
        return None

    storage = MagicMock()
    storage.put_object = AsyncMock()
    storage.delete_object = AsyncMock()
    task = MagicMock()
    task.apply_async = lambda *, kwargs, task_id, **_options: queued.append(kwargs) or SimpleNamespace(id=task_id)

    async def _user():
        return user

    overrides = {get_db: _db, get_api_key_context: _ctx, get_current_user_or_api_key: _user}
    app.dependency_overrides.update(overrides)
    monkeypatch.setattr("app.services.ingestion_backpressure.enforce_redis_memory_backpressure", _open)
    monkeypatch.setattr("app.services.ingestion_rate_limit.enforce_ingest_rate_limit", _open)
    monkeypatch.setattr("app.routers.ingest.resolve_project_scope", AsyncMock())
    monkeypatch.setattr("app.routers.ingest.record_activity", AsyncMock())
    monkeypatch.setattr("app.services.upload_status.set_status", AsyncMock())
    monkeypatch.setattr("app.db.storage.get_storage_provider", lambda: storage)
    monkeypatch.setattr("app.worker.tasks.ingest_uploaded_file", task)
    try:
        yield queued
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)


async def _upload(ci_run_url: str):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    data = {"project_id": str(uuid.uuid4()), "build_number": "b-link-1", "format": "junit", "ci_run_url": ci_run_url}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post(
            "/api/v1/ingest/file", data=data, files={"file": ("r.xml", _JUNIT, "application/xml")},
        )


@pytest.mark.asyncio
async def test_a_file_upload_with_a_script_link_is_accepted_and_the_link_dropped(file_route):
    resp = await _upload("javascript:alert(1)")
    assert resp.status_code == 202, resp.text
    (kwargs,) = file_route
    assert kwargs["ci_run_url"] is None


@pytest.mark.asyncio
async def test_a_file_upload_keeps_an_https_link(file_route):
    resp = await _upload(_HTTPS)
    assert resp.status_code == 202, resp.text
    (kwargs,) = file_route
    assert kwargs["ci_run_url"] == _HTTPS


# -- The run-report e-mail -----------------------------------------------------


def _report(ci_run_url):
    return {
        "run": {"build_number": "b-1", "ci_run_url": ci_run_url},
        "project": {"name": "Shop"},
        "counts": {"total": 1, "passed": 1, "failed": 0, "broken": 0, "skipped": 0, "pass_rate": 100.0},
    }


@pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/html,x", "//ci.example.com/x"])
def test_the_mail_prints_no_ci_link_for_a_stored_value_that_is_not_http(value):
    html = rr.render_run_report_html(_report(value))
    text = rr.render_run_report_text(_report(value))
    assert html and "CI job" not in html and "javascript:" not in html and "data:text" not in html
    assert text and "CI job" not in text


def test_the_mail_prints_an_https_ci_link():
    html = rr.render_run_report_html(_report(_HTTPS))
    assert f'<a href="{_HTTPS}"' in html
    assert f"CI job: {_HTTPS}" in rr.render_run_report_text(_report(_HTTPS))


# -- The executive panel's numbers ---------------------------------------------

_MARKUP = '<img src=x onerror="alert(1)">'


def test_the_panel_escapes_every_count_field():
    panel = {
        "headline": "Nightly",
        "status_signal": "NO_GO",
        "risk_score": _MARKUP,
        "metrics": {"pass_rate": 50.0, "total_tests": _MARKUP, "failed": _MARKUP},
        "dominant_failure": {"category": "product_bug", "count": _MARKUP, "percentage": 40},
        "baseline_comparison": {"pass_rate_delta": -1.5, "new_failures": _MARKUP, "resolved": _MARKUP},
    }
    html = email_templates.render_executive_panel_email(panel)
    assert "<img" not in html
    assert html.count("&lt;img") == 6
    assert "<img" not in email_templates.render_release_signal_email("GO", _MARKUP)  # type: ignore[arg-type]


def test_the_panel_still_prints_ordinary_numbers():
    panel = {
        "headline": "Nightly",
        "status_signal": "GO",
        "risk_score": 12,
        "metrics": {"pass_rate": 97.5, "total_tests": 200, "failed": 5},
        "dominant_failure": {"category": "product_bug", "count": 3, "percentage": 60},
        "baseline_comparison": {"pass_rate_delta": 1.5, "new_failures": 2, "resolved": 4},
    }
    html = email_templates.render_executive_panel_email(panel)
    for expected in ("Risk 12/100", ">200<", ">5<", "3 failures (60%)", "2 new failures", "4 resolved", "97.5%"):
        assert expected in html, expected
    assert "Risk 7/100" in email_templates.render_release_signal_email("GO", 7)


# -- a live session's metadata cannot carry a non-http link past the typed field --


@pytest.mark.parametrize("smuggled", ["javascript:alert(1)", "data:text/html,x", "/relative", 7])
def test_a_ci_context_in_session_metadata_gets_the_same_rule(smuggled):
    from types import SimpleNamespace

    from app.services.stream_service import _with_ci_context

    payload = SimpleNamespace(ci_run_url="https://ci.example/job/1", commit_range=None)
    merged = _with_ci_context({"ci_context": {"ci_run_url": smuggled}}, payload)

    assert merged["ci_context"]["ci_run_url"] is None


def test_an_http_link_in_session_metadata_is_kept():
    from types import SimpleNamespace

    from app.services.stream_service import _with_ci_context

    payload = SimpleNamespace(ci_run_url=None, ci_provider="jenkins", commit_range=None)
    merged = _with_ci_context({"ci_context": {"ci_run_url": "https://ci.example/job/2"}}, payload)

    assert merged["ci_context"]["ci_run_url"] == "https://ci.example/job/2"


def test_session_metadata_alone_cannot_carry_a_non_http_link():
    """No typed CI field at all: the metadata's own ci_context is still read back at persist time."""
    from types import SimpleNamespace

    from app.services.stream_service import _with_ci_context

    payload = SimpleNamespace(commit_range=None)
    merged = _with_ci_context({"ci_context": {"ci_run_url": "javascript:alert(1)", "ci_provider": "x"}}, payload)

    assert merged["ci_context"]["ci_run_url"] is None
    assert merged["ci_context"]["ci_provider"] == "x"
