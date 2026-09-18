"""Commands must expose the stable error codes produced by the shared client."""
from __future__ import annotations

import pytest
from testlookup_cli import client
from testlookup_cli.app import app
from testlookup_cli.errors import (
    EXIT_AUTH,
    EXIT_NOT_FOUND,
    EXIT_PERMISSION,
    EXIT_TIMEOUT,
    CLIError,
    exit_code_for,
)
from typer.testing import CliRunner


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (("projects", "list"), EXIT_PERMISSION),
        (("runs", "get", "run-id"), EXIT_NOT_FOUND),
        (("tests", "get", "run-id", "test-id"), EXIT_AUTH),
        (("search", "needle"), EXIT_TIMEOUT),
        (("intelligence", "show", "run-id"), EXIT_PERMISSION),
        (("deep", "status", "task-id"), EXIT_NOT_FOUND),
        (("reviews", "list", "project-id"), EXIT_AUTH),
        (("keys", "list"), EXIT_PERMISSION),
        (("health",), EXIT_TIMEOUT),
    ],
)
def test_commands_preserve_mapped_client_exit_codes(monkeypatch, argv, code):
    async def fail(*_args, **_kwargs):
        raise CLIError("sentinel", code)

    monkeypatch.setattr(client, "request", fail)
    result = CliRunner().invoke(app, list(argv))
    assert result.exit_code == code, result.output
    assert "sentinel" in result.output


def test_generic_exceptions_keep_the_general_failure_code():
    assert exit_code_for(RuntimeError("sentinel")) == 1
