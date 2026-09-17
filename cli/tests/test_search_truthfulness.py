"""Global-search consumers must not turn partial evidence into exact zero."""
from __future__ import annotations

from typer.testing import CliRunner

from testlookup_cli import client
from testlookup_cli.app import app


def test_search_warns_and_marks_a_partial_total(monkeypatch):
    async def request(*_args, **_kwargs):
        return {
            "items": [],
            "total": 0,
            "result_status": "partial",
            "counts_are_exact": False,
            "failed_entity_types": ["defect"],
        }

    monkeypatch.setattr(client, "request", request)
    result = CliRunner().invoke(app, ["search", "checkout"])

    assert result.exit_code == 0
    assert "lower bounds" in result.stderr
    assert "Unavailable sources: defect" in result.stderr
    assert "0+ results" in result.stdout
