"""Text that came from the server is printed as text, never as terminal markup.

Test names, suite names, project names, error details and AI summaries were
first written by whoever ingested a run. The CLI prints them for someone else.
So in table mode:

* Rich markup inside the data is shown literally (``[link=...]`` makes no
  hyperlink, ``[red]`` changes no colour);
* an unmatched closing tag such as ``[/]`` does not crash the command (a crash
  in ``ci-verdict`` would fail a pipeline gate on a test's name);
* control characters (ESC and the rest of C0 and C1, except newline and tab)
  are removed, so the data cannot drive the reader's terminal.

The CLI's own colours still work, and the machine-readable modes (``--output
json``, ``--output yaml``, ``--json``) print the data byte for byte as before.
"""
from __future__ import annotations

import io
import json
import re

import pytest
from rich.console import Console
from typer.testing import CliRunner

from testlookup_cli import client, output
from testlookup_cli.app import app

ESC = "\x1b"
BACKSLASH = chr(92)
LINK = "[link=https://evil.example/x]login_test[/link]"
TITLE_ESCAPE = f"esc:{ESC}]0;T\x07 {ESC}[2J end"
UNMATCHED = "a [/] b"
_SGR = re.compile(ESC + r"\[[0-9;]*m")


@pytest.fixture
def terminal(monkeypatch):
    """Both consoles as colour terminals writing to one buffer."""
    buffer = io.StringIO()
    for name in ("console", "error_console"):
        monkeypatch.setattr(
            output,
            name,
            Console(
                file=buffer, force_terminal=True, color_system="truecolor",
                width=200, legacy_windows=False,
            ),
        )
    return _Terminal(buffer)


class _Terminal:
    """What the fake terminal received.

    ``raw`` is every byte. ``shown`` is the text with the colour codes the CLI
    itself adds (SGR sequences) removed; any ESC still in it came from the
    data."""

    def __init__(self, buffer: io.StringIO):
        self._buffer = buffer

    @property
    def raw(self) -> str:
        return self._buffer.getvalue()

    @property
    def shown(self) -> str:
        return _SGR.sub("", self.raw)


# ── the helpers ──────────────────────────────────────────────────────────


def test_plain_removes_control_characters_and_keeps_newline_and_tab():
    assert output.plain(f"a{ESC}[2Jb\x07c\x00d\x7fe\x9bf\x85g\rh") == "a[2Jbcdefgh"
    assert output.plain("line 1\n\tline 2") == "line 1\n\tline 2"
    assert output.plain("naïve 日本語 ✓") == "naïve 日本語 ✓"
    assert output.plain(42) == "42"


def test_safe_is_plain_text_with_the_markup_escaped(terminal):
    output.console.print(f"[bold]name:[/] {output.safe(LINK)} {output.safe(UNMATCHED)}")

    printed = terminal.shown
    assert ESC not in printed, "a hyperlink was made from the data"
    assert LINK in printed and UNMATCHED in printed
    assert ESC not in output.safe(TITLE_ESCAPE)


def test_encodable_also_removes_control_characters():
    clean = output.encodable(io.StringIO())

    assert clean(TITLE_ESCAPE) == "esc:]0;T [2J end"


# ── tables ───────────────────────────────────────────────────────────────


def test_a_cell_with_an_unmatched_closing_tag_prints_literally(terminal):
    output.print_table([{"test_name": UNMATCHED}], ["test_name"])

    assert UNMATCHED in terminal.shown


def test_a_cell_with_a_link_tag_makes_no_hyperlink(terminal):
    output.print_table([{"test_name": LINK}], ["test_name"])

    printed = terminal.shown
    assert ESC not in printed
    assert "evil.example" in printed, "the tag must be visible, not hidden"


def test_a_cell_with_escape_sequences_reaches_the_terminal_without_them(terminal):
    output.print_table([{"test_name": TITLE_ESCAPE}], ["test_name"])

    printed = terminal.shown
    assert ESC not in printed and "\x07" not in printed
    assert "esc:]0;T [2J end" in printed


def test_a_cell_ending_in_a_backslash_is_printed_once(terminal):
    path = "C:" + BACKSLASH + "reports" + BACKSLASH

    output.print_table([{"path": path}], ["path"])

    assert path in terminal.shown
    assert path + BACKSLASH not in terminal.shown


def test_a_table_title_with_brackets_prints_literally(terminal):
    output.print_table([{"name": "a name wide enough to keep the title on one line"}], ["name"], title='Search: "x [/] y" (1 results)')

    assert "x [/] y" in terminal.shown


def test_the_key_value_table_prints_values_literally(terminal):
    output.render({"name": UNMATCHED, "summary": LINK, "note": TITLE_ESCAPE}, "table")

    printed = terminal.shown
    assert UNMATCHED in printed and LINK in printed
    assert ESC not in printed


def test_the_first_column_keeps_its_colour(terminal):
    """Positive case: the CLI's own styling is untouched."""
    output.print_table([{"name": "plain_name", "status": "PASSED"}], ["name", "status"])

    printed = terminal.shown
    assert "plain_name" in printed and "PASSED" in printed
    assert f"{ESC}[36m" in terminal.raw, "the first column is cyan"


# ── messages ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("helper", ["print_success", "print_error", "print_warning"])
def test_a_message_with_markup_prints_literally(terminal, helper):
    message = f"Not found. test [/oops] detail {LINK} {TITLE_ESCAPE}"

    getattr(output, helper)(message)

    printed = terminal.shown
    assert "test [/oops] detail" in printed and LINK in printed
    assert ESC not in printed


@pytest.mark.parametrize(
    "helper, colour",
    [("print_success", "32"), ("print_error", "31"), ("print_warning", "33")],
)
def test_the_status_glyph_keeps_its_colour(terminal, helper, colour):
    """Positive case: green for success, red for an error, yellow for a warning."""
    getattr(output, helper)("plain message")

    printed = terminal.shown
    assert "plain message" in printed
    assert f"{ESC}[{colour}m" in terminal.raw


def test_a_message_ending_in_a_backslash_is_printed_once(terminal):
    path = "No such directory: C:" + BACKSLASH + "reports" + BACKSLASH

    output.print_error(path)

    assert path in terminal.shown
    assert path + BACKSLASH not in terminal.shown


# ── commands ─────────────────────────────────────────────────────────────

RUN_ID = "run-0000-server-text-test-only"
PROJECT_ID = "11111111-1111-4111-8111-111111111111"
HOSTILE_TEST = {
    "test_name": "x [/] y",
    "class_name": LINK,
    "suite_name": TITLE_ESCAPE,
    "status": "FAILED",
}
INTELLIGENCE = {
    "run": {"build_number": "7 [/] b", "status": "FAILED [/x]", "pass_rate": 50.0, "failed_tests": 1},
    "release_decision": {"recommendation": "HOLD [/]", "risk_score": 80},
    "structured_summary": {"executive_summary": f"summary {LINK} {TITLE_ESCAPE} [/]"},
    "deep_pipeline_status": {"status": "done [/]", "completed_at": "now [/]"},
    "review": {"state": "draft"},
}


def _route(monkeypatch, routes: dict):
    async def _request(method, path, *, params=None, **_kw):
        for suffix, answer in routes.items():
            if path.endswith(suffix):
                return answer(params or {}) if callable(answer) else answer
        raise AssertionError(f"unexpected request {method} {path}")

    monkeypatch.setattr(client, "request", _request)


def _ci_verdict_routes():
    def _tests(params):
        items = [HOSTILE_TEST] if params["status"] == "FAILED" else []
        return {"items": items, "total": len(items), "page": 1, "pages": 1}

    return {
        f"/runs/{RUN_ID}": {
            "id": RUN_ID, "status": "FAILED", "project_id": PROJECT_ID, "total_tests": 4,
        },
        "/tests": _tests,
        "/quarantine/manifest": {"entries": []},
    }


def test_ci_verdict_table_follows_the_verdict_for_a_hostile_test_name(monkeypatch):
    _route(monkeypatch, _ci_verdict_routes())

    result = CliRunner().invoke(app, ["ci-verdict", "--run", RUN_ID])

    assert result.exit_code == 1, result.output
    assert not isinstance(result.exception, Exception) or isinstance(
        result.exception, SystemExit
    ), f"the table crashed on a test name: {result.exception!r}"
    assert "x [/] y" in result.output
    assert ESC not in result.output


def test_intelligence_show_prints_the_summary_literally(monkeypatch):
    _route(monkeypatch, {"/intelligence": INTELLIGENCE})

    result = CliRunner().invoke(app, ["intelligence", "show", RUN_ID])

    assert result.exit_code == 0, result.output
    plain = " ".join(result.output.split())
    assert "Build 7 [/] b" in plain
    assert "Status: FAILED [/x]" in plain
    assert "HOLD [/]" in plain
    assert LINK in plain
    assert ESC not in result.output


def test_doctor_prints_a_skipped_check_literally(monkeypatch, terminal):
    from testlookup_cli.commands import doctor

    doctor._print_human(
        [{"name": "auth", "status": doctor._STATUS_SKIP, "detail": f"{UNMATCHED} {LINK}"}]
    )

    printed = terminal.shown
    assert f"- auth: {UNMATCHED} {LINK}" in printed
    assert ESC not in printed
    assert f"{ESC}[2m" in terminal.raw, "a skipped check is still dimmed"


def test_an_api_error_detail_with_markup_is_reported_not_crashed(monkeypatch):
    from testlookup_cli.errors import map_http_error

    async def _request(*_a, **_kw):
        raise map_http_error(404, "Run [/oops] not found")

    monkeypatch.setattr(client, "request", _request)

    result = CliRunner().invoke(app, ["intelligence", "show", RUN_ID])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "[/oops]" in result.output


# ── machine-readable modes are unchanged ─────────────────────────────────

HOSTILE_PAYLOAD = {
    "name": UNMATCHED,
    "summary": LINK,
    "note": TITLE_ESCAPE,
    "path": "C:" + BACKSLASH + "reports" + BACKSLASH,
    "items": [{"test_name": "x [/] y", "n": 1, "none": None}],
}


def test_print_json_is_the_json_document_byte_for_byte(capsys):
    output.print_json(HOSTILE_PAYLOAD)

    assert capsys.readouterr().out == json.dumps(HOSTILE_PAYLOAD, indent=2, default=str) + "\n"


def test_print_yaml_is_the_yaml_document_byte_for_byte(capsys):
    import yaml

    output.print_yaml(HOSTILE_PAYLOAD)

    expected = yaml.dump(HOSTILE_PAYLOAD, default_flow_style=False, sort_keys=False) + "\n"
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_render_in_a_machine_mode_does_not_touch_the_data(capsys, fmt):
    import yaml

    output.render([HOSTILE_PAYLOAD], fmt, columns=["name"], title="[/] title")

    out = capsys.readouterr().out
    loaded = json.loads(out) if fmt == "json" else yaml.safe_load(out)
    assert loaded == [HOSTILE_PAYLOAD]


def test_ci_verdict_json_carries_the_names_unchanged(monkeypatch):
    from testlookup_cli.verdict_logic import build_verdict

    _route(monkeypatch, _ci_verdict_routes())

    result = CliRunner().invoke(app, ["ci-verdict", "--run", RUN_ID, "-o", "json"])

    assert result.exit_code == 1, result.output
    expected = build_verdict([HOSTILE_TEST], [], strict=False)
    assert result.stdout == json.dumps(expected, indent=2, default=str) + "\n"


def test_intelligence_show_json_is_the_server_document(monkeypatch):
    _route(monkeypatch, {"/intelligence": INTELLIGENCE})

    result = CliRunner().invoke(app, ["intelligence", "show", RUN_ID, "-o", "json"])

    assert result.exit_code == 0, result.output
    assert result.stdout == json.dumps(INTELLIGENCE, indent=2, default=str) + "\n"


def test_activity_show_json_is_the_server_document(monkeypatch):
    event = {"event_type": "run.created", "summary": f"{LINK} {TITLE_ESCAPE} [/]", "id": "e1"}
    _route(monkeypatch, {"/activity/e1": event})

    result = CliRunner().invoke(
        app, ["activity", "show", "e1", "--project", PROJECT_ID, "--json"]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == json.dumps(event, indent=2, default=str) + "\n"


def test_activity_show_text_has_no_control_characters(monkeypatch):
    event = {"event_type": "run.created", "summary": f"{TITLE_ESCAPE} [/]", "id": "e1"}
    _route(monkeypatch, {"/activity/e1": event})

    result = CliRunner().invoke(app, ["activity", "show", "e1", "--project", PROJECT_ID])

    assert result.exit_code == 0, result.output
    assert ESC not in result.output and "\x07" not in result.output
    assert "summary esc:]0;T [2J end [/]" in result.output
