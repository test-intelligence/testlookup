"""`testlookup upload` retries a server shedding load, and can wait for the outcome.

Re-audit M4 (code review): /api/v1/ingest/file shares the per-project batch
budget with the SDKs and sits behind the backpressure gate, so a CI upload can
now meet a 429 or a 503. The CLI failed on the first one. It follows the SDKs'
rules now: Retry-After exactly, else doubling from 0.5 s, within five minutes.

Re-audit N15: an upload is accepted before it is parsed, so a report the server
then refused -- over the result cap, unparseable -- still printed success and
exited 0. --wait polls the upload's status to the end.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli.app import app
from testlookup_cli.commands import upload

runner = CliRunner()


class _Resp:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _Client:
    """Scripted responses; records each request, and the bytes each POST sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs["files"]["file"][1].read()))
        return self.responses.pop(0)

    async def get(self, url, **_kwargs):
        self.calls.append(("GET", url, None))
        return self.responses.pop(0)


@pytest.fixture
def fake(monkeypatch):
    """A fake clock and a scripted server; nothing sleeps for real."""
    clock = {"now": 0.0}
    sleeps: list[float] = []

    async def _sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(upload, "_sleep", _sleep)
    monkeypatch.setattr(upload, "_clock", lambda: clock["now"])
    monkeypatch.setattr(upload, "get_profile", lambda name=None: {"url": "http://tl.test"})
    monkeypatch.setattr(upload.client, "_build_headers", lambda profile: {})
    state = SimpleNamespace(sleeps=sleeps, client=None)

    def _install(*responses):
        state.client = _Client(responses)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: state.client)
        return state.client

    state.install = _install
    return state


def _report(tmp_path: Path) -> Path:
    path = tmp_path / "r.xml"
    path.write_text("<testsuite/>", encoding="utf-8")
    return path


def _upload(tmp_path: Path) -> dict:
    return asyncio.run(upload._upload_file(path=_report(tmp_path), project="p", build="1"))


# ── The retry policy (the SDKs' rules) ───────────────────────────────────


@pytest.mark.parametrize(
    "value, expected",
    [("30", 30.0), (" 2.5 ", 2.5), ("-4", 0.0), ("", None), (None, None), ("soon", None)],
)
def test_retry_after_in_seconds(value, expected):
    assert upload.parse_retry_after(value) == expected


def test_retry_after_as_an_http_date():
    target = datetime.now(UTC) + timedelta(seconds=90)
    assert 85 <= upload.parse_retry_after(format_datetime(target, usegmt=True)) <= 91


def test_the_servers_retry_after_wins_exactly():
    """No doubling, no halving: what the server said."""
    assert upload.next_retry_delay("30", attempt=5, elapsed=0) == 30.0


def test_without_retry_after_the_delay_doubles_from_the_base():
    assert [upload.next_retry_delay(None, n, 0) for n in (1, 2, 3, 4)] == [0.5, 1.0, 2.0, 4.0]


def test_no_retry_past_the_total_budget():
    assert upload.next_retry_delay("10", 1, elapsed=295) is None
    assert upload.next_retry_delay(None, 1, elapsed=300) is None


# ── The upload retries ───────────────────────────────────────────────────


def test_a_429_is_retried_after_the_servers_retry_after(fake, tmp_path):
    fake.install(
        _Resp(429, {"detail": "Ingest rate limit exceeded"}, {"Retry-After": "7"}),
        _Resp(202, {"status": "accepted", "run_id": "r1", "task_id": "t1"}),
    )
    assert _upload(tmp_path)["task_id"] == "t1"
    assert fake.sleeps == [7.0]
    posts = [call for call in fake.client.calls if call[0] == "POST"]
    assert [body for _method, _url, body in posts] == [b"<testsuite/>", b"<testsuite/>"], (
        "a retry did not send the whole file again"
    )


def test_a_503_without_retry_after_backs_off_from_the_base(fake, tmp_path):
    fake.install(_Resp(503), _Resp(503), _Resp(202, {"task_id": "t1"}))
    _upload(tmp_path)
    assert fake.sleeps == [0.5, 1.0]


def test_a_client_error_is_not_retried(fake, tmp_path):
    fake.install(_Resp(400, {"detail": "unsupported format"}))
    with pytest.raises(Exception, match="HTTP 400: unsupported format"):
        _upload(tmp_path)
    assert fake.sleeps == []


def test_retries_stop_at_the_total_budget(fake, tmp_path):
    fake.install(*[_Resp(429, {"detail": "spent"}, {"Retry-After": "200"}) for _ in range(3)])
    with pytest.raises(Exception, match="gave up after 2 attempts"):
        _upload(tmp_path)
    assert fake.sleeps == [200.0]


# ── --wait ───────────────────────────────────────────────────────────────


def test_wait_polls_until_the_upload_succeeds(fake):
    fake.install(
        _Resp(200, {"task_id": "t1", "state": "pending"}),
        _Resp(200, {"task_id": "t1", "state": "ingesting"}),
        _Resp(200, {"task_id": "t1", "state": "succeeded", "run_id": "r1"}),
    )
    status = asyncio.run(upload._wait_for_upload("t1"))
    assert status["state"] == "succeeded"
    assert fake.sleeps == [2.0, 2.0]
    assert all(url.endswith("/api/v1/ingest/uploads/t1") for _m, url, _b in fake.client.calls)


def test_wait_returns_a_refusal_with_its_reason(fake):
    fake.install(_Resp(200, {
        "task_id": "t1", "state": "failed",
        "error": {"code": "too_many_results", "message": "Split the report."},
    }))
    assert asyncio.run(upload._wait_for_upload("t1"))["error"]["code"] == "too_many_results"


def test_wait_gives_up_at_its_timeout(fake):
    fake.install(*[_Resp(200, {"task_id": "t1", "state": "parsing"}) for _ in range(10)])
    with pytest.raises(Exception, match="still parsing after 5s"):
        asyncio.run(upload._wait_for_upload("t1", timeout=5))


def test_a_status_not_yet_written_is_waited_for(fake):
    fake.install(
        _Resp(404, {"detail": "Upload task not found or expired"}),
        _Resp(200, {"task_id": "t1", "state": "succeeded"}),
    )
    assert asyncio.run(upload._wait_for_upload("t1"))["state"] == "succeeded"


def test_an_unknown_upload_fails_after_the_grace_period(fake):
    fake.install(*[_Resp(404, {"detail": "Upload task not found or expired"}) for _ in range(40)])
    with pytest.raises(Exception, match="HTTP 404"):
        asyncio.run(upload._wait_for_upload("t1"))


# ── The exit code a CI step reads ───────────────────────────────────────


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(upload, "resolve_commit_range", lambda **_kw: None)

    async def _accepted(**_kw):
        return {"status": "accepted", "run_id": "r1", "task_id": "t1", "total_results": 0}

    monkeypatch.setattr(upload, "_upload_file", _accepted)
    outcomes: dict[str, dict] = {}

    async def _waited(task_id, profile_name=None, timeout=600.0):
        return outcomes.get(task_id, {"task_id": task_id, "state": "succeeded"})

    monkeypatch.setattr(upload, "_wait_for_upload", _waited)
    return outcomes


_REFUSED = {
    "task_id": "t1", "state": "failed",
    "error": {"code": "too_many_results", "message": "Split the report."},
}


def test_upload_file_with_wait_fails_when_the_report_is_refused(cli, tmp_path):
    cli["t1"] = _REFUSED
    res = runner.invoke(app, ["upload", "file", str(_report(tmp_path)), "-p", "p", "-b", "1", "--wait"])
    assert res.exit_code == 1, res.output
    assert "too_many_results" in res.output


def test_upload_file_with_wait_reports_the_ingest(cli, tmp_path):
    res = runner.invoke(app, ["upload", "file", str(_report(tmp_path)), "-p", "p", "-b", "1", "--wait"])
    assert res.exit_code == 0, res.output
    assert "Ingested" in res.output


def test_upload_file_without_wait_does_not_poll(cli, monkeypatch, tmp_path):
    async def _never(*_a, **_k):
        raise AssertionError("polled without --wait")

    monkeypatch.setattr(upload, "_wait_for_upload", _never)
    res = runner.invoke(app, ["upload", "file", str(_report(tmp_path)), "-p", "p", "-b", "1"])
    assert res.exit_code == 0, res.output


def test_upload_dir_with_wait_fails_when_any_report_is_refused(cli, tmp_path):
    directory = tmp_path / "reports"
    directory.mkdir()
    for index in range(2):
        (directory / f"r{index}.xml").write_text("<testsuite/>", encoding="utf-8")
    cli["t1"] = _REFUSED
    res = runner.invoke(app, ["upload", "dir", str(directory), "-p", "p", "-b", "1", "--wait"])
    assert res.exit_code == 1, res.output
    assert "too_many_results" in res.output
