"""Small defects from the product findings of 2026-11-21, one test each.

* D-155: a decision report showed its section separator as literal text
  between "FINAL DECISION INTELLIGENCE" and "REPORT STATUS". The sections were
  joined with an escaped separator, a backslash followed by ``n``, instead of
  a blank line.
* S-E-1: under a release filter the Flaky tests header always read "0
  quarantine candidates". The count read an attribute the entry never had,
  through ``getattr`` with a default, so it could not fail loudly.
"""
from __future__ import annotations

import inspect

from app.agents import decision_report_agent
from app.agents.decision_report_agent import render_decision_markdown
from app.models.schemas import FlakyCoachEntry
from app.services import test_health_coach_service

_BACKSLASH = chr(92)


def test_decision_markdown_sections_are_separated_by_blank_lines():
    markdown = render_decision_markdown(
        {"status": "final", "verification": {"status": "passed"}}
    )

    assert _BACKSLASH + "n" not in markdown
    heading, status_line = markdown.split("\n\n")[:2]
    assert heading == "## Final Decision Intelligence"
    assert status_line.startswith("**Report status:**")


def test_no_report_text_is_joined_with_an_escaped_newline():
    """The same slip sat on the line that appends the decision to the summary."""
    source = inspect.getsource(decision_report_agent)
    assert '"' + _BACKSLASH * 2 + "n" not in source


def test_release_scoped_quarantine_count_reads_a_field_the_entry_has():
    source = inspect.getsource(test_health_coach_service.get_flaky_coach)

    assert "quarantine_recommended" not in source
    assert "quarantine_recommended" not in FlakyCoachEntry.model_fields
    # Both scopes must count the same thing.
    assert source.count('quarantine_recommendation == "QUARANTINE"') == 2
