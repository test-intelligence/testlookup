"""How many test results one uploaded report may carry (re-audit M5).

The JSON batch route caps a batch at ``MAX_RESULTS_PER_INGEST`` results in its
schema. The file route had no equivalent. Its 50 MB size limit bounds bytes, not
rows: a file of minimal JUnit elements packs ~1.3M results into 50 MB. Parsing
that one file peaked at 1.3 GB with the real parser (a pytest-json file of the
same size: 0.9 GB), past an ingestion worker child's ~1 GB share -- and every
stage after parsing (the run's transaction, fingerprinting, clustering,
analysis) then ran once per row.

So the cap is enforced twice:

* **Before parsing**, by counting one marker per result in the raw text. The
  count is an estimate -- TestNG config methods, pytest collector entries and
  cucumber backgrounds inflate it -- so it only refuses a report
  ``PREPARSE_HEADROOM`` times over the cap: far enough over that parsing it
  would be the problem, never close enough to refuse a report the exact check
  would accept.
* **After parsing**, exactly, on the results themselves, for every format.

How far the count can be trusted depends on the format. For the XML formats
and pytest, every result the parser keeps must carry the counted marker, so the
count bounds what parsing can cost. A Cypress, Playwright, Cucumber or Allure
result can leave its marker key out and still be parsed, so for those four the
count stops accidents rather than a crafted report. The exact check after
parsing refuses that report either way, and the 50 MB upload limit bounds what
parsing it costs; a streaming count would close the gap (recorded as a
follow-up to re-audit M5).
"""
from __future__ import annotations

import re

from app.core.config import settings

PREPARSE_HEADROOM = 4

# Something that occurs once per test result, countable without parsing. XML:
# the result element's start tag, with a delimiter so a tag that merely starts
# the same way is not counted. JSON: a key only a test object carries. Anything
# unlisted is parsed as JUnit by ``_parse_file_to_results``, so it is counted as
# JUnit here too.
#
# Each marker is checked against a real report of its format
# (tests/regression/test_upload_result_cap.py), because one that matches
# nothing turns the guard off for that format without a sound. Two first
# drafts did exactly that: TRX counts results, not definitions -- a
# results-only TRX has no ``<UnitTest>`` element at all -- and Playwright counts
# ``"results"``, which every test object carries, where ``"expectedStatus"`` is
# missing from some reporters' output.
_JUNIT = re.compile(r"<(?:testcase|test-method)[\s/>]")
_RESULT_MARKERS: dict[str, re.Pattern[str]] = {
    "junit": _JUNIT,
    "testng": _JUNIT,
    "nunit": re.compile(r"<test-case[\s/>]"),
    "trx": re.compile(r"<UnitTestResult[\s/>]"),
    "xunit": re.compile(r"<test[\s/>]"),
    "robot": re.compile(r"<test[\s/>]"),
    "pytest": re.compile(r'"nodeid"\s*:'),
    "cypress": re.compile(r'"fullTitle"\s*:'),
    "playwright": re.compile(r'"results"\s*:'),
    "cucumber": re.compile(r'"steps"\s*:'),
    "allure": re.compile(r'"uuid"\s*:'),
}

# JSON lets any character of a key be written as a unicode escape (a backslash,
# "u" and four hex digits), which json.loads decodes and a regex does not: a
# pytest report whose keys spelled "nodeid" with one escaped letter counted no
# markers against 25 parsed results (code review and QA of M5). Escapes are
# decoded before counting. XML needs no such step: a tag name cannot be written
# with a character reference, and the XML parsers refuse entity declarations.
_BACKSLASH_U = chr(92) + "u"
_UNICODE_ESCAPE = re.compile(re.escape(_BACKSLASH_U) + "([0-9a-fA-F]{4})")
_JSON_FORMATS = frozenset({"pytest", "cypress", "playwright", "cucumber", "allure"})


def _countable(content: str, fmt: str) -> str:
    """The text to count markers in: for JSON, with its unicode escapes decoded."""
    if fmt in _JSON_FORMATS and _BACKSLASH_U in content:
        return _UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), content)
    return content

_ADVICE = (
    "Split it into smaller reports, or ask an admin to raise "
    "INGEST_MAX_RESULTS_PER_UPLOAD."
)


class TooManyResults(Exception):
    """A report carries more results than one upload may.

    Deliberately not a ``ValueError``: the archive parser skips an entry that
    raises an ordinary parse error, and must never skip this one.
    """

    code = "too_many_results"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def result_limit() -> int:
    """The cap in force. 0 or less means no cap."""
    return int(settings.INGEST_MAX_RESULTS_PER_UPLOAD)


def estimated_results(content: str, fmt: str, *, stop_after: int | None = None) -> int:
    """Count result markers in raw report text, stopping once past ``stop_after``."""
    pattern = _RESULT_MARKERS.get(fmt, _JUNIT)
    seen = 0
    for seen, _match in enumerate(pattern.finditer(_countable(content, fmt)), start=1):
        if stop_after is not None and seen > stop_after:
            break
    return seen


def refuse_before_parsing(content: str, fmt: str) -> None:
    """Raise when a cheap count says parsing this report would be the problem."""
    limit = result_limit()
    if limit <= 0:
        return
    threshold = limit * PREPARSE_HEADROOM
    if estimated_results(content, fmt, stop_after=threshold) > threshold:
        raise TooManyResults(
            f"The report appears to hold more than {threshold:,} test results; "
            f"one upload may carry at most {limit:,}. {_ADVICE}"
        )


def enforce_result_limit(count: int) -> None:
    """The exact check, on results already parsed."""
    limit = result_limit()
    if limit > 0 and count > limit:
        raise TooManyResults(
            f"The report holds {count:,} test results; one upload may carry at "
            f"most {limit:,}. {_ADVICE}"
        )
