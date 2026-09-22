"""Re-audit N21: the pre-parse cap bounds a crafted Cypress/Playwright/Cucumber/Allure report.

M5 counted one marker key per result before parsing. For these four formats
the marker key is optional: a result without it is still parsed. So a report
that left it out carried any number of results past the pre-parse check, and
only the exact check after parsing -- after the memory was spent -- refused it.

They are now counted structurally: every object directly inside an array under
the key the parser reads results from, at any depth (Allure: every object in
the root array, or the root object). The parsers take results from nowhere
else, so the count is at least what parsing yields for any valid JSON.

Checked on the repo's REAL sample fixtures (the M5 bound: parsed <= counted <=
4 x parsed), and on crafted reports that drop every marker key.
"""
from __future__ import annotations

import json
import time
import tracemalloc
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services import upload_limits
from app.services.upload_limits import MAX_NESTING, TooManyResults, UnreadableReport
from tests.regression.test_upload_result_cap import SAMPLES, _ALLURE

pytestmark = pytest.mark.regression

CAP = 5
STRUCTURAL = ("cypress", "playwright", "cucumber", "allure")
_REAL = [s for s in SAMPLES if s[0] in STRUCTURAL] + [("allure", _ALLURE, "results.json")]
_PARSERS = {
    "cypress": "app.services.cypress_parser.parse_cypress_json",
    "playwright": "app.services.playwright_parser.parse_playwright_json",
    "cucumber": "app.services.cucumber_parser.parse_cucumber_json",
    "allure": "app.services.allure_parser.parse_allure_result",
}


def _parsed(fmt: str, report: str, filename: str = "report.json") -> int:
    from app.worker.tasks import _parse_file_to_results

    return len(_parse_file_to_results(report, fmt, filename, "run-1"))


def _marker_count(report: str, fmt: str) -> int:
    """What M5's marker count saw."""
    return len(upload_limits._RESULT_MARKERS[fmt].findall(report))


# Reports that carry each format's results WITHOUT its M5 marker key.
def _cypress(n: int) -> str:
    tests = [{"title": f"t{i}", "state": "passed", "duration": 1} for i in range(n)]
    return json.dumps({"results": [{"file": "a.cy.js", "suites": [
        {"title": "outer", "tests": [], "suites": [{"title": "inner", "tests": tests}]},
    ]}]})


def _playwright(n: int) -> str:
    tests = [{"projectName": "chromium", "status": "expected"}]
    specs = [{"title": f"t{i}", "file": "a.spec.ts", "tests": tests} for i in range(n)]
    return json.dumps({"suites": [{"title": "a.spec.ts", "file": "a.spec.ts", "specs": [],
                                   "suites": [{"title": "nested", "specs": specs}]}]})


def _cucumber(n: int) -> str:
    elements = [{"type": "scenario", "name": f"s{i}", "id": f"f;s{i}"} for i in range(n)]
    return json.dumps([{"name": "F", "uri": "f.feature", "elements": elements}])


def _allure(n: int) -> str:
    return json.dumps([{"name": f"t{i}", "fullName": f"C.t{i}", "status": "passed"} for i in range(n)])


CRAFTED = {"cypress": _cypress, "playwright": _playwright, "cucumber": _cucumber, "allure": _allure}


@pytest.mark.parametrize("fmt,fixture,filename", _REAL, ids=[f"{f}-{n}" for f, _, n in _REAL])
def test_the_real_samples_stay_inside_the_m5_bound(fmt, fixture, filename):
    parsed = _parsed(fmt, fixture, filename)
    counted = upload_limits.estimated_results(fixture, fmt)
    assert parsed > 0
    assert parsed <= counted <= parsed * 4, f"{fmt}: {counted} counted for {parsed} parsed"


def test_every_structural_format_has_a_real_sample():
    assert {fmt for fmt, _f, _n in _REAL} == set(STRUCTURAL) == set(upload_limits._CONTAINER_KEYS)


@pytest.mark.parametrize("fmt", STRUCTURAL)
def test_a_report_without_its_marker_keys_is_still_counted(fmt):
    report = CRAFTED[fmt](12)
    parsed = _parsed(fmt, report)
    assert parsed == 12, f"{fmt}: the crafted report parses to {parsed}, not 12"
    # The gap this closes: M5's marker count saw nothing at all.
    assert _marker_count(report, fmt) == 0
    assert upload_limits.estimated_results(report, fmt) >= parsed


@pytest.mark.parametrize("fmt", STRUCTURAL)
def test_a_crafted_report_far_over_the_cap_is_refused_unparsed(fmt, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_MAX_RESULTS_PER_UPLOAD", CAP)
    from app.worker.tasks import _parse_file_to_results

    with patch(_PARSERS[fmt], side_effect=AssertionError("the report was parsed")):
        with pytest.raises(TooManyResults):
            _parse_file_to_results(CRAFTED[fmt](21), fmt, "report.json", "run-1")


@pytest.mark.parametrize("fmt", ["cypress", "playwright", "cucumber"])
def test_an_escaped_container_key_is_still_the_key(fmt):
    """json.loads decodes ``"te\\u0073ts"`` to ``tests``; so must the count."""
    key = "elements" if fmt == "cucumber" else "tests"
    report = CRAFTED[fmt](9)
    escaped = report.replace(f'"{key}"', '"' + key[:2] + chr(92) + "u" + format(ord(key[2]), "04x") + key[3:] + '"')
    assert escaped != report
    assert _parsed(fmt, escaped) == 9
    assert upload_limits.estimated_results(escaped, fmt) >= 9


def test_a_decoy_inside_a_string_is_not_counted():
    decoy = 'x\\" , "tests": [{}, {}, {}] '
    report = json.dumps({"results": [{"file": "a", "suites": [], "note": decoy}]})
    assert _parsed("cypress", report) == 0
    assert upload_limits.estimated_results(report, "cypress") == 0


def test_an_allure_root_object_counts_one():
    report = json.dumps({"name": "t", "fullName": "C.t", "status": "passed"})
    assert _parsed("allure", report) == 1
    assert upload_limits.estimated_results(report, "allure") == 1


def test_the_count_stops_once_past_the_threshold():
    """A huge crafted report is refused after scanning a threshold's worth."""
    report = _cypress(50_000)
    assert upload_limits.structural_results(report, "cypress", stop_after=20) == 21


# ── Linear time, bounded memory (review R-B45-D-2, R-B45-D-3) ────────────

_ESCAPED_QUOTE = chr(92) + '"'


def _unclosed(units: int) -> str:
    """The review's probe: a string of escaped quotes that never closes."""
    return '{"results": [' + '"' + _ESCAPED_QUOTE * units


def _many_tests(units: int) -> str:
    return '{"results": [{"suites": [], "tests": [' + "{}," * units + "{}]}]}"


def _best_seconds(fn, repeat: int = 3) -> float:
    best = float("inf")
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - started)
    return best


def _refuse(report: str) -> None:
    with pytest.raises(UnreadableReport, match="never closes"):
        upload_limits.structural_results(report, "cypress")


def _count(report: str) -> None:
    upload_limits.structural_results(report, "cypress")


def test_the_reviews_probe_is_refused_fast():
    """20,000 escaped quotes took 2.9 s with the quadratic tokenizer; a linear
    scan needs well under a millisecond. Checked first, so a quadratic scanner
    fails here quickly instead of hanging on the sizes below."""
    assert _best_seconds(lambda: _refuse(_unclosed(20_000)), repeat=1) < 0.25


class _Walked:
    """A stand-in for one of the scan's two compiled patterns that adds up how
    much of the report the regex engine was asked to walk.

    Every quantifier in both patterns is possessive, so a match never
    backtracks: it stops where it succeeded, or -- having failed -- at the end
    of the input. Either way the distance from where it was pointed is what
    that step of the scan cost, and the total over a whole scan is the
    quadratic tokenizer's tell, exactly and without a clock.
    """

    def __init__(self, pattern) -> None:
        self._pattern = pattern
        self.walked = 0

    def _account(self, found, content: str, position: int):
        self.walked += (found.end() if found is not None else len(content)) - position
        return found

    def search(self, content: str, position: int):
        return self._account(self._pattern.search(content, position), content, position)

    def match(self, content: str, position: int):
        return self._account(self._pattern.match(content, position), content, position)


def _characters_walked(scan, report: str) -> int:
    patterns = [_Walked(upload_limits._STRUCTURAL), _Walked(upload_limits._STRING_REST)]
    with patch.object(upload_limits, "_STRUCTURAL", patterns[0]), \
            patch.object(upload_limits, "_STRING_REST", patterns[1]):
        scan(report)
    return sum(pattern.walked for pattern in patterns)


@pytest.mark.parametrize("make, scan, sizes, bound", [
    (_unclosed, _refuse, (2_000, 2_000_000, 4_000_000, 8_000_000), 2.0),
    (_many_tests, _count, (1_000, 100_000, 200_000, 400_000), 6.0),
], ids=["unclosed-string", "many-results"])
def test_the_count_is_linear_in_the_report(make, scan, sizes, bound):
    """Growing the report grows the scan in proportion -- never squared.

    Counted in characters the scan walks, not seconds. The property is about
    the algorithm, and timing it measured the machine too: two sub-second
    readings whose ratio had to stay under 3 flip whenever something else on
    the box takes the CPU, and this failed the push gate under a parallel
    build. The scan walks each character of the report exactly once; the
    tokenizer it replaced re-walked the tail from every quote.

    The smallest size is a canary -- a quadratic scan fails on it in
    milliseconds instead of hanging on the megabytes after it.
    """
    per_character = []
    for units in sizes:
        report = make(units)
        walked = _characters_walked(scan, report)
        assert walked <= 2 * len(report), (units, walked, len(report))
        per_character.append(walked / len(report))
    # Flat: what a character of report costs does not grow with the report.
    assert max(per_character) / min(per_character) < 1.05, list(zip(sizes, per_character))

    # ...and that constant is small enough to matter on a real upload. Timed
    # on a report built beforehand, best of three.
    biggest = make(sizes[-1])
    seconds = _best_seconds(lambda: scan(biggest))
    assert seconds < bound, (len(biggest), seconds)


def test_the_count_still_counts_what_it_scans():
    assert upload_limits.structural_results(_many_tests(1000), "cypress") == 1001


def test_a_string_that_never_closes_is_refused_before_parsing(monkeypatch):
    monkeypatch.setattr(settings, "INGEST_MAX_RESULTS_PER_UPLOAD", CAP)
    from app.worker.tasks import _parse_file_to_results

    with patch(_PARSERS["cypress"], side_effect=AssertionError("the report was parsed")):
        with pytest.raises(UnreadableReport):
            _parse_file_to_results(_unclosed(10), "cypress", "report.json", "run-1")


def test_an_unreadable_report_is_a_parse_error_not_a_cap_refusal():
    """The archive loop skips an entry that raises an ordinary parse error;
    it must never skip TooManyResults (M5). Unreadable JSON is the former."""
    assert issubclass(UnreadableReport, ValueError)
    assert not issubclass(UnreadableReport, TooManyResults)


@pytest.mark.parametrize("opener", ["[", '{"a":'], ids=["arrays", "objects"])
def test_nesting_past_the_cap_is_refused_in_bounded_memory(opener):
    report = opener * 1_000_000
    tracemalloc.start()
    try:
        with pytest.raises(UnreadableReport, match="nests deeper"):
            upload_limits.structural_results(report, "cypress")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # A million frames held 96 MB; the cap keeps it to a few hundred.
    assert peak < 2 * 1024 * 1024, peak


@pytest.mark.parametrize("kilobytes", [16, 32], ids=["16KB", "32KB"])
def test_qas_repro_is_refused_fast(kilobytes):
    """QA-B45-D-1's repro exactly: a quote, then escaped quotes, as playwright.
    The quadratic tokenizer took 0.42 s at 16 KB and 1.72 s at 32 KB."""
    report = '"' + _ESCAPED_QUOTE * (kilobytes * 1024 // 2)
    started = time.perf_counter()
    with pytest.raises(UnreadableReport):
        upload_limits.estimated_results(report, "playwright")
    assert time.perf_counter() - started < 0.1


def test_two_million_open_brackets_stay_small():
    """QA-B45-D-1's memory repro: 2,000,000 "[" peaked at 193 MB."""
    report = "[" * 2_000_000
    tracemalloc.start()
    try:
        with pytest.raises(UnreadableReport):
            upload_limits.estimated_results(report, "playwright")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024, peak


@pytest.mark.parametrize("fmt, report", [
    ("cypress", '{"results": [{"tests": {"a": {}, "b": {}}}]}'),
    ("playwright", '{"suites": [{"specs": [{"tests": {"x": {}}}]}]}'),
    ("cucumber", '[{"elements": {"a": {}}}]'),
], ids=["cypress", "playwright", "cucumber"])
def test_only_objects_inside_a_container_array_count(fmt, report):
    """QA M4: an object held under ``tests``/``elements`` but not in an array
    is no result -- the parsers iterate a list there and skip anything else."""
    assert _parsed(fmt, report) == 0
    assert upload_limits.estimated_results(report, fmt) == 0


def test_nesting_at_the_cap_is_still_counted():
    depth = MAX_NESTING
    assert upload_limits.structural_results("[" * depth + "]" * depth, "cypress") == 0
