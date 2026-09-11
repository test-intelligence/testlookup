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
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


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


# ── --wait rides out what a rollout does to a poll (code review of N15) ──
#
# A status poll is a GET that changes nothing, so a 429, a 5xx or a dropped
# connection says nothing about the upload. One 502 from the ingress during a
# rolling deploy failed the CI step although the upload went on to succeed.

_SUCCEEDED = {"task_id": "t1", "state": "succeeded", "run_id": "r1"}


def _dropped(kind=httpx.ConnectError, message="connection reset by peer"):
    return kind(message, request=httpx.Request("GET", "http://tl.test/api/v1/ingest/uploads/t1"))


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_a_transient_answer_while_polling_is_not_yet(fake, code):
    fake.install(_Resp(code, {"detail": "busy"}), _Resp(200, _SUCCEEDED))
    assert asyncio.run(upload._wait_for_upload("t1"))["state"] == "succeeded"
    assert fake.sleeps == [2.0]


def test_a_dropped_connection_while_polling_is_not_yet(fake):
    fake.install(_dropped(), _dropped(httpx.ReadTimeout, "timed out"), _Resp(200, _SUCCEEDED))
    assert asyncio.run(upload._wait_for_upload("t1"))["state"] == "succeeded"
    assert fake.sleeps == [2.0, 2.0]


def test_a_polls_retry_after_is_honoured(fake):
    fake.install(
        _Resp(503, {}, {"Retry-After": "7"}),
        _Resp(429, {}, {"Retry-After": "11"}),
        _Resp(200, _SUCCEEDED),
    )
    asyncio.run(upload._wait_for_upload("t1"))
    assert fake.sleeps == [7.0, 11.0]


def test_retry_after_zero_does_not_poll_in_a_hot_loop(fake):
    """Proxies send ``Retry-After: 0``; honoured exactly, QA counted 22,333
    polls in 1.5 seconds."""
    fake.install(_Resp(503, {}, {"Retry-After": "0"}), _Resp(503, {}, {"Retry-After": "0"}), _Resp(200, _SUCCEEDED))
    assert asyncio.run(upload._wait_for_upload("t1"))["state"] == "succeeded"
    assert fake.sleeps == [upload.WAIT_MIN_RETRY_SECONDS] * 2
    assert upload.WAIT_MIN_RETRY_SECONDS >= 1.0


def test_the_last_poll_comes_at_the_deadline_not_after_it(fake):
    """A Retry-After past the deadline is cut to the deadline."""
    fake.install(_Resp(429, {}, {"Retry-After": "60"}), _Resp(200, _SUCCEEDED))
    assert asyncio.run(upload._wait_for_upload("t1", timeout=10))["state"] == "succeeded"
    assert fake.sleeps == [10.0]


@pytest.mark.parametrize("code", [400, 401, 403, 409, 422])
def test_an_answer_that_cannot_change_fails_the_wait_at_once(fake, code):
    fake.install(_Resp(code, {"detail": "refused"}))
    with pytest.raises(Exception, match=f"HTTP {code} while waiting for upload t1: refused"):
        asyncio.run(upload._wait_for_upload("t1"))
    assert fake.sleeps == []


def test_transient_answers_until_the_deadline_fail_with_the_last_one(fake):
    fake.install(*[_Resp(503) for _ in range(10)])
    with pytest.raises(Exception, match="no status for upload t1 after 5s: the last poll got HTTP 503"):
        asyncio.run(upload._wait_for_upload("t1", timeout=5))
    assert sum(fake.sleeps) == 5


def test_a_server_unreachable_until_the_deadline_fails_as_unreachable(fake):
    fake.install(*[_dropped() for _ in range(10)])
    with pytest.raises(Exception, match="Cannot reach the TestLookup server"):
        asyncio.run(upload._wait_for_upload("t1", timeout=5))
    assert sum(fake.sleeps) == 5


def test_upload_file_with_wait_survives_a_rollout(fake, monkeypatch, tmp_path):
    """The exit code a CI step reads, through the real poll loop."""
    monkeypatch.setattr(upload, "resolve_commit_range", lambda **_kw: None)

    async def _accepted(**_kw):
        return {"status": "accepted", "run_id": "r1", "task_id": "t1"}

    monkeypatch.setattr(upload, "_upload_file", _accepted)
    fake.install(
        _Resp(502), _dropped(), _Resp(503, {}, {"Retry-After": "3"}), _Resp(200, _SUCCEEDED)
    )
    res = runner.invoke(
        app, ["upload", "file", str(_report(tmp_path)), "-p", "p", "-b", "1", "--wait"]
    )
    assert res.exit_code == 0, res.output
    assert "Ingested" in res.output
    assert fake.sleeps == [2.0, 2.0, 3.0]


# ── The exit code a CI step reads ───────────────────────────────────────


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(upload, "resolve_commit_range", lambda **_kw: None)

    async def _accepted(**_kw):
        return {"status": "accepted", "run_id": "r1", "task_id": "t1", "total_results": 0}

    monkeypatch.setattr(upload, "_upload_file", _accepted)
    outcomes: dict[str, dict] = {}

    async def _waited(task_id, profile_name=None, timeout=600.0, deadline=None):
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


# ── upload dir --wait: one deadline for every file (QA of N15) ───────────


@pytest.fixture
def dir_upload(fake, monkeypatch, tmp_path):
    """`upload dir` over real files: each accepted as task-<stem>, its status served per task."""
    monkeypatch.setattr(upload, "resolve_commit_range", lambda **_kw: None)

    async def _accepted(path, **_kw):
        return {"status": "accepted", "run_id": f"r-{path.stem}", "task_id": f"task-{path.stem}"}

    monkeypatch.setattr(upload, "_upload_file", _accepted)
    states: dict[str, str] = {}

    class _StatusServer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, url, **_kwargs):
            task_id = url.rsplit("/", 1)[-1]
            return _Resp(200, {"task_id": task_id, "state": states.get(task_id, "pending")})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _StatusServer())
    directory = tmp_path / "reports"
    directory.mkdir()

    def _files(count: int) -> Path:
        for index in range(count):
            (directory / f"r{index}.xml").write_text("<testsuite/>", encoding="utf-8")
        return directory

    return SimpleNamespace(states=states, files=_files, sleeps=fake.sleeps)


def _wait_dir(directory: Path):
    return runner.invoke(
        app,
        ["upload", "dir", str(directory), "-p", "p", "-b", "1", "--wait", "--wait-timeout", "30"],
    )


def test_upload_dir_waits_once_for_every_file_not_once_per_file(dir_upload):
    """QA: with the workers down, 20 files waited 20 x 600 s = 3 h 20 min."""
    res = _wait_dir(dir_upload.files(4))
    assert res.exit_code == 1, res.output
    assert sum(dir_upload.sleeps) <= 30, (
        f"waited {sum(dir_upload.sleeps):.0f}s in all for a 30s --wait-timeout"
    )
    for name in ("r0.xml", "r1.xml", "r2.xml", "r3.xml"):
        assert f"Not finished: {name}" in res.output, res.output
    assert "Ingested 0/4 files (4 errors)" in res.output


def test_upload_dir_names_only_the_files_that_did_not_finish(dir_upload):
    """A file reached after the deadline is still checked once, so one that
    finished meanwhile is not reported as unfinished."""
    dir_upload.states.update({"task-r1": "succeeded", "task-r2": "succeeded"})
    res = _wait_dir(dir_upload.files(3))
    assert res.exit_code == 1, res.output
    assert "Not finished: r0.xml" in res.output
    assert "Not finished: r1.xml" not in res.output
    assert "Not finished: r2.xml" not in res.output
    assert "Ingested 2/3 files (1 errors)" in res.output
    assert sum(dir_upload.sleeps) <= 30


def test_upload_dir_within_the_deadline_succeeds(dir_upload):
    dir_upload.states.update({f"task-r{i}": "succeeded" for i in range(3)})
    res = _wait_dir(dir_upload.files(3))
    assert res.exit_code == 0, res.output
    assert dir_upload.sleeps == []
