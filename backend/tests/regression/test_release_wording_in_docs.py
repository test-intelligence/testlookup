"""The README and the user guide say "release", not "ship" (owner review, 2026-11-16).

TestLookup's own words are release words: the release gate, the release
decision, GO / NO-GO. The README's tagline still ended "Know when to ship.",
the user guide said flags "ship off", and the escape rate was defined over
"shipped" releases. The public website changed first, to "Know why your
software tests fail. Know when your release is ready."; this keeps the repo's
front page and the guide people read from drifting back.

The app's own screens and in-app guide are pinned by the frontend test
``frontend/src/releaseWording.test.ts``. The changelog is history and is not
checked; neither are the operator and developer docs (``docs/``,
``architecture/``), where "ships" means "includes".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

TAGLINE = "Know why your software tests fail. Know when your release is ready."

SHIP = re.compile(r"\bship(?:s|ped|ping|pable)?\b", re.IGNORECASE)

#: What a person reads first on GitHub, and the guide they read next.
READER_DOCS = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CITATION.cff"]


def _reader_docs() -> list[Path]:
    paths = [REPO_ROOT / name for name in READER_DOCS]
    paths += sorted((REPO_ROOT / "user-guide").rglob("*.md"))
    paths += sorted((REPO_ROOT / "user-guide").rglob("*.yml"))
    return paths


@pytest.fixture(autouse=True)
def _needs_the_checkout() -> None:
    if not (REPO_ROOT / "README.md").is_file():
        pytest.skip("runs from a full checkout (README.md is not in this image)")


def test_the_readme_tagline_is_the_release_one() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<b>{TAGLINE}</b>" in readme
    assert "Know when to ship" not in readme


def test_no_reader_doc_says_ship() -> None:
    docs = _reader_docs()
    assert len(docs) > 15, "the user guide was not found"
    found = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: {line.strip()[:120]}"
        for path in docs
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if SHIP.search(line)
    ]
    assert found == []


def test_the_scan_catches_the_old_wording() -> None:
    # The pattern itself: every form the docs used is caught, the words that
    # merely contain the letters are not.
    for old in ("Know when to ship.", "ships off", "had shipped", "before shipping", "shippable"):
        assert SHIP.search(old), old
    for fine in ("relationship", "membership", "worship", "shipment"):
        assert not SHIP.search(fine), fine
