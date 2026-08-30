"""Every format the product accepts must be named where buyers look.

`_SUPPORTED_FORMATS` in ``app/routers/ingest.py`` is the authoritative list:
what ``/ingest/file`` accepts, what ``_detect_format`` can return, and what
``tests/regression/test_parser_format_coverage.py`` proves actually parses.

Three of those formats -- **nunit, trx, xunit** -- were supported, wired,
detected and tested, and named in *none* of the repo's front-door documents.
``README.md`` advertised "JUnit XML, TestNG, Allure JSON, Cypress, Playwright,
pytest, Robot Framework, Cucumber" and stopped there; ``README_FULL.md``'s two
upload sections listed only "JUnit XML, TestNG XML, or Allure JSON". A .NET
shop evaluating TestLookup reads exactly those files and concludes the product
cannot read their `dotnet test` output -- so working support was invisible
where it decides an adoption.

The in-app guide (``frontend/src/content/guide/getting-started.md``) and
``architecture/README.md`` already listed all three. That split is the tell: the
list was maintained in the places contributors edit and left to rot in the
places buyers read.

This is the inverse of ``test_parser_format_coverage.py``. That one guards
"everything advertised actually works"; this one guards "everything that works
is actually advertised". A format nobody knows about is a feature that does not
exist.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Docs a prospective user reads before deciding the product fits.
FRONT_DOOR_DOCS = (
    "README.md",
    "UserGuides/TESTLOOKUP_USER_GUIDE.md",
    "frontend/src/content/guide/getting-started.md",
)

#: ``auto`` is a directive, not a format. ``junit`` is the fallback every doc
#: already leads with. Everything else must be named explicitly.
NOT_A_FORMAT_NAME = {"auto", "junit"}

#: How each format identifier is allowed to appear in prose. A doc saying
#: "Visual Studio TRX" satisfies ``trx``; one saying nothing does not.
DOC_ALIASES = {
    "testng": ("testng",),
    "allure": ("allure",),
    "cypress": ("cypress",),
    "playwright": ("playwright",),
    "pytest": ("pytest",),
    "robot": ("robot",),
    "cucumber": ("cucumber",),
    "nunit": ("nunit",),
    "trx": ("trx",),
    "xunit": ("xunit",),
}


def _supported_formats() -> set[str]:
    """Read the router's own set, so the docs are pinned to the code."""
    source = (REPO_ROOT / "backend" / "app" / "routers" / "ingest.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r"_SUPPORTED_FORMATS\s*=\s*\{(.*?)\}", source, re.S)
    assert match, "_SUPPORTED_FORMATS is no longer a set literal in ingest.py"
    return set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))


def test_the_authoritative_list_is_still_where_this_test_expects_it():
    """If the list moves or shrinks unexpectedly, fail loudly rather than
    silently guarding nothing -- a guard that reads an empty set passes."""
    formats = _supported_formats()
    assert len(formats) >= 10, f"only found {sorted(formats)}"
    for expected in ("nunit", "trx", "xunit", "junit", "auto"):
        assert expected in formats, f"{expected} vanished from _SUPPORTED_FORMATS"


@pytest.mark.parametrize("doc", FRONT_DOOR_DOCS)
def test_every_supported_format_is_named_in_the_front_door_docs(doc: str):
    path = REPO_ROOT / doc
    assert path.exists(), f"{doc} is missing"
    text = path.read_text(encoding="utf-8", errors="ignore").lower()

    missing = []
    for fmt in sorted(_supported_formats() - NOT_A_FORMAT_NAME):
        aliases = DOC_ALIASES.get(fmt, (fmt,))
        if not any(alias in text for alias in aliases):
            missing.append(fmt)

    assert not missing, (
        f"{doc} does not mention {missing}. The product accepts these formats "
        "and proves it in test_parser_format_coverage.py, but a reader of this "
        "file would conclude it cannot read them."
    )
