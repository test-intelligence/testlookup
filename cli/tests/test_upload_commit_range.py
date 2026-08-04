"""CLI wiring for local-git commit-range collection (US-8.1 follow-up).

Covers the multipart form field, the two new flags, single-collection for
``upload dir``, and — the one that bites in CI recipes — that ``--output json``
stdout stays a single parseable JSON document.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testlookup_cli.app import app
from testlookup_cli.commands import upload
from testlookup_cli.commit_range import collect_commit_range

runner = CliRunner()

_FAKE_RANGE = [
    {
        "sha": "a" * 40,
        "author": "Dev Example",
        "message": "fix the thing",
        "files": ["src/app.py"],
        "committed_at": "2026-08-04T10:00:00+00:00",
    }
]


@pytest.fixture
def result_file(tmp_path: Path) -> Path:
    path = tmp_path / "results.xml"
    path.write_text(
        '<?xml version="1.0"?><testsuite name="s" tests="1"><testcase name="t"/></testsuite>',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    """Capture the kwargs every ``_upload_file`` call receives, no HTTP."""
    calls: list[dict] = []

    async def _fake_upload(**kwargs):
        calls.append(kwargs)
        return {"status": "accepted", "run_id": "run-1", "task_id": "task-1", "total_results": 1}

    monkeypatch.setattr(upload, "_upload_file", _fake_upload)
    return calls


# ── Form assembly ─────────────────────────────────────────────────────────────

def test_form_carries_commit_range_as_a_json_array():
    form = upload._build_form_data(
        project="p", build="b", commit_range=_FAKE_RANGE,
    )
    assert "commit_range" in form
    parsed = json.loads(form["commit_range"])
    assert parsed == _FAKE_RANGE
    assert isinstance(parsed, list)   # the server does `isinstance(parsed, list)`


@pytest.mark.parametrize("value", [None, []])
def test_form_omits_commit_range_when_there_is_nothing_to_send(value):
    form = upload._build_form_data(project="p", build="b", commit_range=value)
    assert "commit_range" not in form


# ── Flags ─────────────────────────────────────────────────────────────────────

def test_collection_runs_by_default(monkeypatch, result_file, captured):
    seen: list[dict] = []

    def _spy(**kwargs):
        seen.append(kwargs)
        return _FAKE_RANGE

    monkeypatch.setattr(upload, "resolve_commit_range", _spy)
    res = runner.invoke(app, ["upload", "file", str(result_file), "-p", "proj", "-b", "42"])

    assert res.exit_code == 0, res.output
    assert seen == [{"explicit_base": None, "enabled": None}]
    assert captured[0]["commit_range"] == _FAKE_RANGE


def test_no_commit_range_flag_disables_collection(monkeypatch, result_file, captured):
    seen: list[dict] = []
    monkeypatch.setattr(
        upload, "resolve_commit_range",
        lambda **kw: (seen.append(kw), None)[1],
    )
    res = runner.invoke(
        app, ["upload", "file", str(result_file), "-p", "proj", "-b", "42", "--no-commit-range"],
    )

    assert res.exit_code == 0, res.output
    assert seen == [{"explicit_base": None, "enabled": False}]
    assert captured[0]["commit_range"] is None


def test_commit_range_base_flag_is_passed_through(monkeypatch, result_file, captured):
    seen: list[dict] = []
    monkeypatch.setattr(
        upload, "resolve_commit_range",
        lambda **kw: (seen.append(kw), _FAKE_RANGE)[1],
    )
    res = runner.invoke(
        app,
        ["upload", "file", str(result_file), "-p", "proj", "-b", "42",
         "--commit-range-base", "origin/main"],
    )

    assert res.exit_code == 0, res.output
    assert seen == [{"explicit_base": "origin/main", "enabled": None}]


def test_upload_dir_collects_the_range_once(monkeypatch, tmp_path, captured):
    directory = tmp_path / "reports"
    directory.mkdir()
    for i in range(3):
        (directory / f"r{i}.xml").write_text("<testsuite/>", encoding="utf-8")

    calls = []
    monkeypatch.setattr(
        upload, "resolve_commit_range",
        lambda **kw: (calls.append(kw), _FAKE_RANGE)[1],
    )
    res = runner.invoke(app, ["upload", "dir", str(directory), "-p", "proj", "-b", "42"])

    assert res.exit_code == 0, res.output
    assert len(calls) == 1, "one checkout → one git walk, not one per file"
    assert len(captured) == 3
    assert all(c["commit_range"] == _FAKE_RANGE for c in captured)


# ── --output json stays machine-parseable ─────────────────────────────────────

def test_output_json_stdout_is_a_single_json_document(monkeypatch, result_file, captured):
    """`upload ... --output json | jq -r '.run_id'` feeds ci-verdict in the CI
    recipes — commit collection must not print a byte to stdout."""
    monkeypatch.setattr(upload, "resolve_commit_range", lambda **kw: _FAKE_RANGE)
    res = runner.invoke(
        app, ["upload", "file", str(result_file), "-p", "proj", "-b", "42", "-o", "json"],
    )

    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["run_id"] == "run-1"


def test_collector_writes_nothing_to_stdout(tmp_path, capsys):
    """The collector itself logs at DEBUG only — never print()."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "dev@example.com"],
        ["config", "user.name", "Dev Example"],
        ["commit", "--allow-empty", "-m", "one"],
        ["commit", "--allow-empty", "-m", "two"],
    ):
        subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)

    capsys.readouterr()
    assert collect_commit_range(env={}, repo_path=str(repo)) is not None
    captured_io = capsys.readouterr()
    assert captured_io.out == ""
