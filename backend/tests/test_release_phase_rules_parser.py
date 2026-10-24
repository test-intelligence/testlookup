"""W11 REL-07: the PRD heading parser over real-shaped PRDs.

The rules draft is the answer whenever the model is off, refused, slow or
wrong, so it has to be a good draft ON ITS OWN. Pinned against three
real-shaped documents (``tests/fixtures/prd/``; the ``.docx`` is built here
with python-docx, so no binary is committed) with their exact phase lists:
names, types, days and exit criteria. Plus the edges: an untyped heading
(one warning), 31 headings (30 and a warning), no headings (none and a
warning), a numbered plan with no headings, fenced code, Markdown in names.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.release_planning import (
    DRAFT_WARNING_NO_HEADINGS,
    DRAFT_WARNING_TRUNCATED,
    DRAFT_WARNING_UNTYPED,
    PHASE_TYPES,
)
from app.services.release_phase_generator import draft_by_rules, extract_prd_text

FIXTURES = Path(__file__).parent / "fixtures" / "prd"


def _table(phases):
    return [(p["name"], p["phase_type"], p["planned_days"]) for p in phases]


def test_checkout_v2_markdown():
    text = extract_prd_text(text=None, filename="checkout-v2.md",
                            data=(FIXTURES / "checkout-v2.md").read_bytes())
    phases, warnings = draft_by_rules(text)

    assert _table(phases) == [
        ("Smoke testing", "qa_testing", None),
        ("Regression", "qa_testing", 5),
        ("UAT", "uat", 10),
        ("Production cutover", "production", None),
    ]
    assert warnings == []
    smoke, regression, uat, cutover = phases
    assert smoke["description"] == (
        "Run the checkout-smoke suite against every release candidate build, on "
        "staging data. A red smoke run stops the candidate."
    )
    assert smoke["exit_criteria_text"] == "all smoke tests pass on two consecutive builds"
    assert regression["description"] == (
        "The full checkout-e2e and payments-api suites, plus the refund regression pack, "
        "on the release candidate. Flaky tests are re-run once."
    )
    assert regression["heading"] == "Regression (5 days)"
    assert regression["exit_criteria_text"] is None
    assert uat["exit_criteria_text"] == (
        "Finance signs off the reconciliation report; No open P1 or P2 defects; "
        "Support has the new refund macro"
    )
    assert uat["description"].startswith("Finance and customer support walk the new flows")
    assert "checkout_v2" in cutover["description"]   # an identifier keeps its underscore


def test_mobile_release_setext_text():
    text = extract_prd_text(text=None, filename="mobile-release.txt",
                            data=(FIXTURES / "mobile-release.txt").read_bytes())
    phases, warnings = draft_by_rules(text)

    assert _table(phases) == [
        ("Code freeze", "code_freeze", None),
        ("Smoke and regression", "qa_testing", 5),       # "1 week" in the heading
        ("Beta with pilot users", "uat", 10),             # "2 weeks"
        ("Store release", "qa_testing", 7),               # no type keyword; "7 days" in its body
    ]
    # The numbered steps inside a phase are its body, never phases of their own.
    assert not any(p["name"].startswith("Install") for p in phases)
    assert phases[1]["exit_criteria_text"] == (
        "no P1 defects are open and the crash-free rate is above 99.5%"
    )
    assert phases[1]["description"] == (
        "The device lab runs the smoke pack on every nightly build, then the full "
        "regression on the freeze build:"
    )
    assert warnings == [DRAFT_WARNING_UNTYPED]


@pytest.fixture
def payments_docx(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Payments PRD", level=0)            # Title
    document.add_heading("Background", level=1)
    document.add_paragraph("Card payments move to the new acquirer.")
    document.add_heading("Test phases", level=1)
    document.add_heading("Integration testing (3 days)", level=2)
    document.add_paragraph("The acquirer sandbox with the contract suite.")
    document.add_heading("Staging soak", level=2)
    document.add_paragraph("A week of mirrored traffic on staging.")
    document.add_paragraph("Exit criteria: error rate below 0.1%")
    document.add_heading("Go-live", level=2)
    document.add_paragraph("Switch the routing table at 06:00 UTC.")
    document.add_heading("Appendix", level=1)
    document.add_paragraph("Contact list.")
    path = tmp_path / "payments-prd.docx"
    document.save(str(path))
    return path


def test_payments_docx_with_heading_styles(payments_docx):
    text = extract_prd_text(text=None, filename="payments-prd.docx", data=payments_docx.read_bytes())

    assert text.splitlines()[0] == "# Payments PRD"
    assert "## Integration testing (3 days)" in text.splitlines()
    phases, warnings = draft_by_rules(text)
    assert _table(phases) == [
        ("Integration testing", "qa_testing", 3),
        ("Staging soak", "staging", None),   # "a week" is not a number
        ("Go-live", "production", None),
    ]
    assert phases[1]["exit_criteria_text"] == "error rate below 0.1%"
    assert phases[2]["description"] == "Switch the routing table at 06:00 UTC."
    assert warnings == []


def test_an_untyped_heading_warns_once():
    phases, warnings = draft_by_rules("## Dry run\n\n## Rehearsal two\n\n## Smoke\n")
    assert [p["phase_type"] for p in phases] == ["qa_testing", "qa_testing", "qa_testing"]
    assert warnings == [DRAFT_WARNING_UNTYPED]


def test_31_headings_draft_30_and_say_so():
    text = "\n\n".join(f"## Regression wave {i}" for i in range(1, 32))
    phases, warnings = draft_by_rules(text)
    assert len(phases) == 30
    assert phases[-1]["name"] == "Regression wave 30"
    assert warnings == [DRAFT_WARNING_TRUNCATED]


def test_a_document_without_headings_drafts_nothing_and_says_why():
    phases, warnings = draft_by_rules("We will test it properly.\nThen ship it.\n")
    assert phases == []
    assert warnings == [DRAFT_WARNING_NO_HEADINGS]


def test_a_numbered_plan_with_no_heading_is_the_plan():
    phases, warnings = draft_by_rules(
        "Test plan for 3.1\n\n1. Smoke testing\n2. Regression (3 days)\n   Done when: all green\n3. UAT\n"
    )
    assert _table(phases) == [
        ("Smoke testing", "qa_testing", None),
        ("Regression", "qa_testing", 3),
        ("UAT", "uat", None),
    ]
    assert phases[1]["exit_criteria_text"] == "all green"
    assert warnings == []


def test_a_lone_title_is_the_phase_list_only_without_level_two_headings():
    phases, _ = draft_by_rules("# Smoke\n\ntext\n\n# UAT\n")
    assert [p["name"] for p in phases] == ["Smoke", "UAT"]
    phases, _ = draft_by_rules("# Checkout PRD\n\n## Smoke\n\n## UAT\n")
    assert [p["name"] for p in phases] == ["Smoke", "UAT"]


def test_headings_inside_fenced_code_are_not_phases():
    phases, _ = draft_by_rules("## Smoke\n\n```\n## Not a phase\n```\n\n## UAT\n")
    assert [p["name"] for p in phases] == ["Smoke", "UAT"]


def test_markdown_in_a_heading_is_removed_from_the_name():
    phases, _ = draft_by_rules(
        "## **Smoke** testing:\n\n## [UAT](https://wiki.example.test/uat) sign-off\n\n## `e2e` suite\n"
    )
    assert [p["name"] for p in phases] == ["Smoke testing", "UAT sign-off", "e2e suite"]


def test_the_type_table_is_ordered_and_matches_word_starts():
    cases = {
        "Release to production": "production",
        "Pre-prod dress rehearsal": "staging",
        "User acceptance testing": "uat",
        "Hardening sprint": "code_freeze",
        "Performance and load": "qa_testing",
        "Development": "development",
        "Kickoff": "planning",
    }
    text = "\n\n".join(f"## {name}" for name in cases)
    phases, warnings = draft_by_rules(text)
    assert {p["name"]: p["phase_type"] for p in phases} == cases
    assert all(p["phase_type"] in PHASE_TYPES for p in phases)
    assert warnings == []


def test_days_are_clamped_and_weeks_are_five_working_days():
    phases, _ = draft_by_rules("## Soak (95 days)\n\n## Beta\nRuns for 3 weeks.\n\n## UAT\nTen business days.\n")
    assert [p["planned_days"] for p in phases] == [90, 15, None]


def test_the_parser_is_deterministic():
    text = (FIXTURES / "checkout-v2.md").read_text(encoding="utf-8")
    assert draft_by_rules(text) == draft_by_rules(text)
