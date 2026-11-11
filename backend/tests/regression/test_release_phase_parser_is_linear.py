"""CodeQL py/polynomial-redos #57-#64: the PRD heading parser must stay linear.

``draft_by_rules`` reads a PRD of up to ``MAX_PRD_CHARS`` (200,000) characters,
all of which can sit on one line. Before the fix, one heading line of 20,000
tabs ("## a" + tabs + "x") ran for more than ten minutes: the heading, list-item
and exit-criteria patterns had neighbouring ``[ \\t]`` runs that backtracked
quadratically, and the link and emphasis patterns rescanned the rest of the
line from every ``[``, ``*`` or ``_``. Each adversarial line below is the full
200,000 characters and must parse in well under the time limit.

The second half pins the behaviour the rewrite keeps: closing ``#`` runs,
exit-criteria separators and inline markup are removed as before.
"""
from __future__ import annotations

import time

import pytest

from app.core.release_planning import MAX_PRD_CHARS
from app.services import release_phase_generator as gen

LIMIT_SECONDS = 5.0
N = MAX_PRD_CHARS - 64


ADVERSARIAL = {
    "heading with a run of tabs": "## a" + "\t" * N + "x\n",
    "list item with a run of tabs": "## Smoke\n*\t" + "\t" * N + "\x00\n## UAT\n",
    "exit criteria with a run of tabs": "## Smoke\nexit criteria" + "\t" * N + "\x00\n",
    "unclosed link brackets": "## Smoke\n" + "[" * N + "\n",
    "link with an unclosed target": "## Smoke\n" + "[](" * (N // 3) + "\n",
    "star emphasis pump": "## " + "*a" * (N // 2) + "\n",
    "strikethrough pump": "## " + "~~a" * (N // 3) + "\n",
    "underscore emphasis pump": "## " + " _a" * (N // 3) + "\n",
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_a_full_size_adversarial_line_parses_quickly(name: str) -> None:
    text = ADVERSARIAL[name]
    assert len(text) <= MAX_PRD_CHARS
    started = time.perf_counter()
    gen.draft_by_rules(text)
    elapsed = time.perf_counter() - started
    assert elapsed < LIMIT_SECONDS, f"{name}: {elapsed:.1f}s"


@pytest.mark.parametrize(
    ("rest", "title"),
    [
        ("Smoke testing", "Smoke testing"),
        ("  Smoke testing  ", "Smoke testing"),
        ("Smoke testing ##", "Smoke testing"),
        ("Smoke testing\t#\t", "Smoke testing"),
        ("C# ", "C"),            # the old pattern's lazy match dropped a trailing # too
        ("a#b", "a#b"),
        ("Title\t#\t#", "Title\t#"),
        ("###", "#"),            # a heading of only #s keeps the first one
        ("   ", ""),
    ],
)
def test_atx_title_matches_the_old_pattern(rest: str, title: str) -> None:
    assert gen._atx_title(rest) == title


@pytest.mark.parametrize(
    ("rest", "expected"),
    [
        (": all P1 tests pass", "all P1 tests pass"),
        ("\t-\tno open blockers", "no open blockers"),
        (" — sign-off from QA", "sign-off from QA"),
        (" – sign-off from QA", "sign-off from QA"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_exit_rest_drops_one_separator_and_the_blanks(rest: str, expected: str) -> None:
    assert gen._exit_rest(rest) == expected


@pytest.mark.parametrize(
    ("text", "clean"),
    [
        ("[UAT](https://wiki.example.test/uat) sign-off", "UAT sign-off"),
        ("**Smoke** testing", "Smoke testing"),
        ("**bold *nested* text**", "bold nested text"),
        ("~~old~~ new", "old new"),
        ("_em_ and __strong__", "em and strong"),
        ("run test_pay_flow first", "run test_pay_flow first"),
        ("`e2e` suite", "e2e suite"),
    ],
)
def test_inline_markup_is_still_removed(text: str, clean: str) -> None:
    assert gen._clean_inline(text) == clean


def test_exit_criteria_and_list_items_still_parse() -> None:
    phases, _ = gen.draft_by_rules(
        "## Smoke testing (2 days)\n\nQuick checks.\n\nExit criteria:\n-   all P1 tests pass\n*\tno blockers\n"
    )
    assert [p["name"] for p in phases] == ["Smoke testing"]
    assert phases[0]["exit_criteria_text"] == "all P1 tests pass; no blockers"
