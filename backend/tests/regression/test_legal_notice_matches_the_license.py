"""The legal notice restates the license; it cannot drift from it.

Owner request, 2026-11-11: a legal notice that makes plain that TestLookup is
free, open-source software provided "as is", and that the maintainer and
contributors are not liable for failures, data loss, data leaks, misuse or any
other damages. The notice lives in the in-app guide
(``frontend/src/content/guide/legal.md``, served at ``/docs/legal``), with a
Disclaimer section in the README and a pointer in the operator guide.

The notice must restate Apache-2.0, never invent terms that conflict with it.
It quotes sections 7 and 8 word for word, so a hand edit that softens or
"improves" the quoted text, or a LICENSE change the page does not follow, is
caught here. The other assertions pin the carve-out that keeps the notice
honest ("unless required by applicable law") and the pointers that make it
findable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGAL_PAGE = "frontend/src/content/guide/legal.md"
MANIFEST = "frontend/src/content/guide/manifest.ts"


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _squash(text: str) -> str:
    """Collapse all whitespace so line wrapping does not matter."""
    return re.sub(r"\s+", " ", text).strip()


def _license_section(number: int) -> str:
    """The text of one numbered section of LICENSE, up to the next section."""
    text = _read("LICENSE")
    match = re.search(
        rf"^\s*{number}\. (.*?)(?=^\s*{number + 1}\. )",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"LICENSE has no section {number}"
    return _squash(f"{number}. {match.group(1)}")


def _blockquotes(markdown: str) -> list[str]:
    """Each Markdown blockquote in the page, as one squashed string."""
    quotes: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if line.startswith(">"):
            current.append(line[1:].strip())
        elif current:
            quotes.append(_squash(" ".join(current)))
            current = []
    if current:
        quotes.append(_squash(" ".join(current)))
    return quotes


@pytest.mark.parametrize(
    ("number", "title"),
    [(7, "Disclaimer of Warranty"), (8, "Limitation of Liability")],
)
def test_the_legal_page_quotes_the_license_word_for_word(
    number: int, title: str
) -> None:
    section = _license_section(number)
    assert section.startswith(f"{number}. {title}."), section[:80]
    quotes = _blockquotes(_read(LEGAL_PAGE))
    assert section in quotes, (
        f"{LEGAL_PAGE} must quote LICENSE section {number} ({title}) exactly; "
        "restate it in your own words outside the quote instead of editing the quote"
    )


def test_the_legal_page_keeps_the_applicable_law_carve_out() -> None:
    page = _squash(_read(LEGAL_PAGE)).lower()
    # The plain-language restatement says both halves: no warranty, no liability ...
    assert '"as is"' in page
    assert "no warranty" in page
    assert "not liable" in page
    for harm in ("failures", "data loss", "data leaks", "misuse"):
        assert harm in page, (
            f"the notice must name {harm!r} among the damages it disclaims"
        )
    # ... and the limit Apache-2.0 itself puts on both, which a disclaimer cannot remove.
    assert "unless required by applicable law" in page
    assert "not legal advice" in page


def test_the_legal_page_is_registered_in_the_in_app_guide() -> None:
    manifest = _read(MANIFEST)
    assert re.search(r"id:\s*'legal'", manifest), (
        "the legal page needs a DOC_PAGES entry"
    )


def test_the_readme_and_operator_guide_point_to_the_notice() -> None:
    readme = _read("README.md")
    disclaimer = re.search(
        r"^## Disclaimer\n(.*?)(?=^## |\Z)", readme, flags=re.MULTILINE | re.DOTALL
    )
    assert disclaimer, "README.md needs a Disclaimer section"
    body = disclaimer.group(1)
    assert "](LICENSE)" in body
    assert f"]({LEGAL_PAGE})" in body
    assert "/docs/legal" in body

    guide = _read("user-guide/README.md")
    assert f"](../{LEGAL_PAGE})" in guide
