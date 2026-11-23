"""``testlookup upload --executed-at`` and the commit-range outcome (D-19, S-C-5).

D-19: charts placed every run on its upload day, and the CLI had no way to say
when the tests ran (the only ``executed`` in ``cli/`` was the --environment
help text). The ingest file route already takes an ``executed_at`` form field;
the CLI now sends it, after checking it locally: a value the server cannot
parse would be a 422 after the whole file went over the wire, and one in the
future or more than a year back would be silently replaced by the upload time.

S-C-5: a supplied commit range can be refused after the 202; the upload status
now says so (``result.commit_range``), and ``--wait`` prints it.

Also D-117 on the upload path: a 200 that is not JSON is a clear error.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from testlookup_cli.app import app
from testlookup_cli.commands import upload
from typer.testing import CliRunner

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROJECT = "11111111-1111-4111-8111-111111111111"


_BOX = re.compile(r"[─-╿]")


def _plain(text: str) -> str:
    """Without colour, without the usage-error panel's box (styled on CI), whitespace collapsed."""
    return " ".join(_BOX.sub(" ", _ANSI.sub("", text)).split())


@pytest.fixture
def server(monkeypatch):
    """A fake ingest server; records each upload's form fields."""
    state = {"forms": [], "status": {"state": "succeeded", "result": {"total": 3}}, "accept_body": None}
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = request.content.decode("utf-8", "replace")
            fields = dict(re.findall(r'name="([^"]+)"\r\n\r\n([^\r]*)\r\n', body))
            state["forms"].append(fields)
            if state["accept_body"] is not None:
                return httpx.Response(200, text=state["accept_body"], headers={"content-type": "text/html"})
            return httpx.Response(202, json={"task_id": "t1", "run_id": "r1", "status": "queued"})
        return httpx.Response(200, json=state["status"])

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    monkeypatch.setattr(upload, "get_profile", lambda _n=None: {"url": "https://tl.example", "api_key": "k", "auth_type": "api_key"})
    monkeypatch.setenv("TESTLOOKUP_COMMIT_RANGE", "0")
    monkeypatch.setenv("TESTLOOKUP_RUN_CONTEXT", "0")
    return state


@pytest.fixture
def report(tmp_path: Path) -> Path:
    path = tmp_path / "results.xml"
    path.write_text('<testsuite name="s" tests="1"><testcase name="t"/></testsuite>', encoding="utf-8")
    return path


def _upload(report: Path, *args: str):
    return CliRunner().invoke(app, ["upload", "file", str(report), "-p", PROJECT, "-b", "build-41", *args])


# ── --executed-at ───────────────────────────────────────────────────────────


def test_executed_at_is_sent_as_the_form_field(server, report):
    when = (datetime.now(UTC) - timedelta(days=2)).replace(microsecond=0)
    result = _upload(report, "--executed-at", when.strftime("%Y-%m-%dT%H:%M:%SZ"))

    assert result.exit_code == 0, result.output
    assert server["forms"][0]["executed_at"] == when.isoformat()


def test_a_time_without_a_zone_is_sent_as_utc(server, report):
    when = (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0, tzinfo=None)
    result = _upload(report, "--executed-at", when.isoformat())

    assert result.exit_code == 0, result.output
    assert server["forms"][0]["executed_at"] == when.replace(tzinfo=UTC).isoformat()


def test_no_executed_at_sends_no_field(server, report):
    assert _upload(report).exit_code == 0
    assert "executed_at" not in server["forms"][0]


@pytest.mark.parametrize("value,words", [
    ("yesterday", "not an ISO 8601 date-time"),
    ("20/11/2026", "not an ISO 8601 date-time"),
    ((datetime.now(UTC) + timedelta(days=1)).isoformat(), "in the future"),
    ((datetime.now(UTC) - timedelta(days=400)).isoformat(), "more than 365 days ago"),
])
def test_a_bad_executed_at_is_a_usage_error_before_any_upload(server, report, value, words):
    result = _upload(report, "--executed-at", value)

    assert result.exit_code == 2, result.output
    assert words in _plain(result.output)
    assert server["forms"] == [], "nothing may be uploaded with a value the server would refuse or replace"


def test_upload_dir_sends_executed_at_on_every_file(server, tmp_path):
    for name in ("a.xml", "b.xml"):
        (tmp_path / name).write_text('<testsuite name="s" tests="0"/>', encoding="utf-8")
    when = (datetime.now(UTC) - timedelta(hours=10)).replace(microsecond=0)
    result = CliRunner().invoke(
        app, ["upload", "dir", str(tmp_path), "-p", PROJECT, "-b", "b", "--executed-at", when.isoformat()],
    )
    assert result.exit_code == 0, result.output
    assert [f["executed_at"] for f in server["forms"]] == [when.isoformat()] * 2


# ── the commit-range outcome (S-C-5) ────────────────────────────────────────


@pytest.mark.parametrize("outcome,words", [
    ("stored", "Commit range: stored"),
    ("truncated", "Commit range: stored, but cut"),
    ("ignored_malformed", "Commit range: ignored: the server could not read it"),
    ("store_failed", "Commit range: not stored"),
])
def test_wait_prints_the_commit_range_outcome(server, report, outcome, words):
    server["status"] = {"state": "succeeded", "result": {"total": 3, "commit_range": outcome}}
    result = _upload(report, "--wait")

    assert result.exit_code == 0, result.output
    assert words in _plain(result.output)


def test_no_commit_range_line_when_the_status_has_none(server, report):
    result = _upload(report, "--wait")
    assert result.exit_code == 0, result.output
    assert "Commit range" not in _plain(result.output)


# ── D-117 on the upload path ────────────────────────────────────────────────


def test_an_html_answer_to_the_upload_is_a_clear_error(server, report):
    server["accept_body"] = "<!doctype html><html></html>"
    result = _upload(report)

    assert result.exit_code == 1
    assert not isinstance(result.exception, ValueError), result.exception
    assert "not JSON" in _plain(result.output)
