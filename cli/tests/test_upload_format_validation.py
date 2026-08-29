"""``--format`` is validated locally, before any network round-trip.

Regression for the no-client-validation gap: the CLI passed an arbitrary
``--format`` straight to the server, so a typo (``--format juint``) was only
caught by the ingest endpoint's 400 — one wasted round-trip for ``upload
file``, and one per file for ``upload dir``. A local ``BadParameter`` now
rejects an unknown format immediately, and the accepted set is a single
constant both commands derive their help and validation from (so the two
can't drift, and can't fall behind the backend's ``_SUPPORTED_FORMATS`` gate
silently — an unadvertised format reads as unsupported to a self-hoster).
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


def _make_file(tmp_path: Path) -> Path:
    f = tmp_path / "results.xml"
    f.write_text("<testsuite/>", encoding="utf-8")
    return f


def _make_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "reports"
    directory.mkdir()
    (directory / "r0.xml").write_text("<testsuite/>", encoding="utf-8")
    return directory


def test_supported_formats_match_backend_ingest_gate():
    # The backend's ingest endpoint (backend/app/routers/ingest.py) gates on
    # exactly this set; the CLI must advertise/accept the same one so it never
    # rejects a format the server would parse, nor forwards one it won't.
    assert upload.SUPPORTED_UPLOAD_FORMATS == (
        "auto", "junit", "testng", "allure", "cypress", "playwright", "pytest",
        "robot", "cucumber", "nunit", "trx", "xunit",
    )


def test_upload_file_rejects_unknown_format_without_network(monkeypatch, tmp_path):
    f = _make_file(tmp_path)

    called = {"n": 0}

    async def _should_not_run(**kwargs):  # pragma: no cover - must never fire
        called["n"] += 1
        return {"run_id": "x"}

    monkeypatch.setattr(upload, "_upload_file", _should_not_run)
    res = runner.invoke(
        app, ["upload", "file", str(f), "-p", "proj", "-b", "42", "--format", "juint"]
    )

    assert res.exit_code != 0, res.output
    assert "juint" in res.output
    assert "supported format" in res.output.lower()
    assert called["n"] == 0  # validation happened before any upload attempt


def test_upload_dir_rejects_unknown_format_without_network(monkeypatch, tmp_path):
    directory = _make_dir(tmp_path)

    called = {"n": 0}

    async def _should_not_run(**kwargs):  # pragma: no cover - must never fire
        called["n"] += 1
        return {"run_id": "x"}

    monkeypatch.setattr(upload, "_upload_file", _should_not_run)
    res = runner.invoke(
        app, ["upload", "dir", str(directory), "-p", "proj", "-b", "42", "-f", "nope"]
    )

    assert res.exit_code != 0, res.output
    assert "nope" in res.output
    assert called["n"] == 0


@pytest.mark.parametrize("fmt", list(upload.SUPPORTED_UPLOAD_FORMATS))
def test_upload_file_accepts_every_supported_format(monkeypatch, tmp_path, fmt):
    f = _make_file(tmp_path)

    seen = {"format": None}

    async def _capture(**kwargs):
        seen["format"] = kwargs["format"]
        return {"run_id": "run-1"}

    monkeypatch.setattr(upload, "_upload_file", _capture)
    res = runner.invoke(
        app, ["upload", "file", str(f), "-p", "proj", "-b", "42", "--format", fmt]
    )

    assert res.exit_code == 0, res.output
    assert seen["format"] == fmt  # the validated value is forwarded unchanged
