"""`upload dir` must fail the command (non-zero exit) when any file fails.

Regression for the swallow-all-errors-exit-0 bug: the directory upload counted
per-file failures but always exited 0, so a CI ingest step (`testlookup upload
dir ...`, which reads the exit code) went green even when every report failed
to land. This mirrors `upload file`, which already exits non-zero on its single
failure, and the empty-directory guard, which already exits 1.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from testlookup_cli.app import app
from testlookup_cli.commands import upload

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    """Keep the local-git commit-range collector out of these tests."""
    monkeypatch.setattr(upload, "resolve_commit_range", lambda **kw: None)


def _make_dir(tmp_path: Path, n: int) -> Path:
    directory = tmp_path / "reports"
    directory.mkdir()
    for i in range(n):
        (directory / f"r{i}.xml").write_text("<testsuite/>", encoding="utf-8")
    return directory


def test_upload_dir_exits_nonzero_when_every_file_fails(monkeypatch, tmp_path):
    directory = _make_dir(tmp_path, 3)

    async def _boom(**kwargs):
        raise Exception("HTTP 401: bad token")

    monkeypatch.setattr(upload, "_upload_file", _boom)
    res = runner.invoke(app, ["upload", "dir", str(directory), "-p", "proj", "-b", "42"])

    assert res.exit_code == 1, res.output


def test_upload_dir_exits_nonzero_on_partial_failure(monkeypatch, tmp_path):
    directory = _make_dir(tmp_path, 3)
    seen = {"n": 0}

    async def _sometimes(**kwargs):
        seen["n"] += 1
        if seen["n"] == 2:
            raise Exception("HTTP 500: boom")
        return {"status": "accepted", "run_id": f"run-{seen['n']}"}

    monkeypatch.setattr(upload, "_upload_file", _sometimes)
    res = runner.invoke(app, ["upload", "dir", str(directory), "-p", "proj", "-b", "42"])

    # Two files landed, one failed — the command still fails so CI notices.
    assert res.exit_code == 1, res.output


def test_upload_dir_exits_zero_when_all_succeed(monkeypatch, tmp_path):
    directory = _make_dir(tmp_path, 2)

    async def _ok(**kwargs):
        return {"status": "accepted", "run_id": "run-1"}

    monkeypatch.setattr(upload, "_upload_file", _ok)
    res = runner.invoke(app, ["upload", "dir", str(directory), "-p", "proj", "-b", "42"])

    assert res.exit_code == 0, res.output
