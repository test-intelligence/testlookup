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
count bounds what parsing can cost. For XML that holds whatever the result
element's name looks like: the TRX parser matches names without their
namespace prefix, so ``<t:UnitTestResult>`` is parsed and counted; the other
XML parsers match only the unprefixed name, so a prefixed element is neither
parsed nor counted. A Cypress, Playwright, Cucumber or Allure result can leave
its marker key out and still be parsed, so those four are counted
structurally instead (re-audit N21, ``structural_results``): every object in
an array under the key their parser reads results from. That bounds a crafted
report too.
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
#
# Namespace prefixes (QA of the M5 review). Whether a prefixed result element
# such as ``<t:UnitTestResult>`` is parsed depends on how its parser matches
# names, and the count has to follow the parser. The TRX parser compares LOCAL
# names, so a prefixed result is parsed like a plain one, and its marker takes
# any prefix: with the plain marker, a TRX report written with prefixes counted
# 0 against 2,000 parsed results. The other XML parsers look results up by the
# unprefixed name (``findall("testcase")``) and parse a prefixed element to
# nothing, so their markers count unprefixed tags only. The prefix is matched
# loosely, as anything up to a colon except XML whitespace and delimiters: a
# count that sees too much can only lean toward refusal.
_ANY_PREFIX = r"(?:[^ \t\r\n<>/:]+:)?"
_JUNIT = re.compile(r"<(?:testcase|test-method)[\s/>]")
_RESULT_MARKERS: dict[str, re.Pattern[str]] = {
    "junit": _JUNIT,
    "testng": _JUNIT,
    "nunit": re.compile(r"<test-case[\s/>]"),
    "trx": re.compile("<" + _ANY_PREFIX + r"UnitTestResult[\s/>]"),
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


# ── A structural count for the formats whose marker is optional (N21) ────
#
# A Cypress, Playwright, Cucumber or Allure result may leave its marker key
# out and still be parsed, so a crafted report could carry any number of
# results past the marker count. For those four the count follows the
# parser's own structure instead: every OBJECT directly inside an array held
# under the key the parser reads results from -- at any depth, so no nesting
# hides one -- or, for Allure, every object in the root array (or the root
# object itself). The parsers take results from nowhere else, so for any valid
# JSON the count is at least what parsing yields; extra containers (a Cucumber
# background, a nested ``tests`` the parser never visits) only lean toward
# refusal.
#
# It streams, in LINEAR time and bounded memory, with no objects built. Keys
# are decoded when they carry an escape, so a ``"tests"`` key is the
# ``tests`` key json.loads sees.
#
# Linear (review R-B45-D-2). The first version found tokens with one
# ``finditer`` over "a string, or punctuation". When a quote never closed,
# every later quote started a new string attempt that scanned to the end of
# the input and failed: quadratic, so 20,000 escaped quotes took 2.9 s and one
# 50 MB upload would pin a worker for weeks -- before any JSON parse. Now the
# scan jumps from one structural character to the next, and each string's
# body is matched ONCE from its opening quote with possessive quantifiers
# (no backtracking). A string that never closes is not JSON: the report is
# refused at that point.
#
# Bounded (review R-B45-D-3). One frame per open container, and the depth is
# capped: a million "[" held a million frames (96 MB). Real reports nest a few
# dozen deep; json.loads itself gives up near a thousand.
_ROOT = object()
_CONTAINER_KEYS: dict[str, frozenset] = {
    "cypress": frozenset({"tests"}),
    "playwright": frozenset({"tests"}),
    "cucumber": frozenset({"elements"}),
    "allure": frozenset({_ROOT}),
}
MAX_NESTING = 512
# The next character that can change the structure, or open a string.
# Numbers, literals and whitespace never can: they cannot open a result.
_STRUCTURAL = re.compile(r'[\[\]{},"]')
# The rest of a string after its opening quote, through the closing quote.
_STRING_REST = re.compile(r'[^"\\]*+(?:\\.[^"\\]*+)*+"', re.DOTALL)


class UnreadableReport(ValueError):
    """The report cannot be JSON: refused before any parse.

    A ValueError on purpose, unlike :class:`TooManyResults`: it is a parse
    error, and the archive loop skips an entry that raises one.
    """


def structural_results(content: str, fmt: str, *, stop_after: int | None = None) -> int:
    """Count the objects the ``fmt`` parser could turn into results, streaming.

    Raises :class:`UnreadableReport` for a string that never closes or nesting
    deeper than :data:`MAX_NESTING`.
    """
    import json

    containers = _CONTAINER_KEYS[fmt]
    search = _STRUCTURAL.search
    string_rest = _STRING_REST.match
    # One frame per open container: [is_array, key it sits under, awaiting a
    # key (objects), the last key read (objects)].
    stack: list[list] = []
    count = 0
    position = 0
    while True:
        found = search(content, position)
        if found is None:
            return count
        head = found.group()
        position = found.end()
        if head == '"':
            rest = string_rest(content, position)
            if rest is None:
                raise UnreadableReport(
                    "The report has a string that never closes, so it is not JSON."
                )
            start, position = position - 1, rest.end()
            frame = stack[-1] if stack else None
            if frame is not None and not frame[0] and frame[2]:
                key = content[start + 1:position - 1]
                if "\\" in key:
                    try:
                        key = json.loads(content[start:position])
                    except ValueError:
                        pass
                frame[3] = key
                frame[2] = False
            continue
        if head in "{[" and len(stack) >= MAX_NESTING:
            raise UnreadableReport(
                f"The report nests deeper than {MAX_NESTING} levels; no test report does."
            )
        if head == "{":
            if stack:
                parent = stack[-1]
                counted = parent[0] and parent[1] in containers
            else:
                counted = _ROOT in containers
            if counted:
                count += 1
                if stop_after is not None and count > stop_after:
                    return count
            stack.append([False, None, True, None])
        elif head == "[":
            under: object
            if not stack:
                under = _ROOT
            elif stack[-1][0]:
                under = None  # an array in an array: no parser reads results there
            else:
                under = stack[-1][3]
            stack.append([True, under, False, None])
        elif head == ",":
            if stack and not stack[-1][0]:
                stack[-1][2] = True
        elif stack:  # "}" or "]"
            stack.pop()
    return count


def estimated_results(content: str, fmt: str, *, stop_after: int | None = None) -> int:
    """Count results in raw report text, stopping once past ``stop_after``.

    The four formats whose marker key is optional are counted structurally
    (re-audit N21); the rest by their marker, which every result they parse
    must carry.
    """
    if fmt in _CONTAINER_KEYS:
        return structural_results(content, fmt, stop_after=stop_after)
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
