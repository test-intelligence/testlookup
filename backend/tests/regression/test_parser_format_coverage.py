"""Every format the detector recognises must actually parse.

Found by uploading one representative file per advertised format to the live
deployment and checking the counts. Nine of ten matched ground truth exactly.
**TestNG produced no run at all**, and the upload still answered:

```
HTTP 202  {"status": "accepted", "run_id": "…", "total_results": 0}
```

TestNG emits *two* different files. Under Maven, surefire writes JUnit-shaped
``TEST-*.xml`` (``<testsuite>/<testcase>``) — which the parser handled. TestNG
itself writes ``testng-results.xml``, a completely different document:

    <testng-results><suite><test><class>
      <test-method status="PASS|FAIL|SKIP" name="…" duration-ms="…">

``_detect_format`` explicitly matches ``<testng-results`` and routes it to that
parser, so the product *claimed* the format — but ``root.findall("testsuite")``
matches nothing in such a document, so a valid six-test report became zero
results behind a 202 that reported success. On one of the eight formats the
product advertises, with no error anywhere.

The guard is therefore not "TestNG works" but the class: **detection and
parsing must agree**. A format the detector can return, that then parses a
representative file to nothing, is a silent data-loss path — the worst kind,
because the uploader is told it worked.
"""
from __future__ import annotations

import uuid
from collections import Counter

import pytest

# ── Representative fixtures, one per detectable format ──────────────────────

TESTNG_NATIVE = """<?xml version="1.0" encoding="UTF-8"?>
<testng-results total="3" passed="1" failed="1" skipped="1">
  <suite name="ProbeSuite">
    <test name="ProbeTest">
      <class name="probe.Foo">
        <test-method status="PASS" name="t_pass" duration-ms="1000"/>
        <test-method status="FAIL" name="t_fail" duration-ms="1000">
          <exception class="java.lang.AssertionError">
            <message>expected 200 but was 500</message>
            <full-stacktrace>at probe.Foo.t_fail(Foo.java:42)</full-stacktrace>
          </exception>
        </test-method>
        <test-method status="SKIP" name="t_skip" duration-ms="0"/>
      </class>
    </test>
  </suite>
</testng-results>
"""

SUREFIRE = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="ProbeSuite" tests="3" failures="1" errors="0" skipped="1">
    <testcase name="t_pass" classname="probe.Foo" time="1.0"/>
    <testcase name="t_fail" classname="probe.Foo" time="1.0">
      <failure message="expected 200 but was 500">at probe.Foo.t_fail(Foo.java:42)</failure>
    </testcase>
    <testcase name="t_skip" classname="probe.Foo" time="0"><skipped/></testcase>
  </testsuite>
</testsuites>
"""

ROBOT = """<?xml version="1.0"?>
<robot generator="Robot 6.1">
  <suite id="s1" name="ProbeSuite">
    <test id="s1-t1" name="t_pass"><status status="PASS"/></test>
    <test id="s1-t2" name="t_fail"><status status="FAIL">boom</status></test>
    <status status="FAIL"/>
  </suite>
</robot>
"""

NUNIT = """<?xml version="1.0"?>
<test-run id="1" total="2" passed="1" failed="1">
  <test-suite type="TestFixture" name="ProbeSuite" fullname="probe.Foo">
    <test-case id="1" name="t_pass" fullname="probe.Foo.t_pass" result="Passed" duration="1.0"/>
    <test-case id="2" name="t_fail" fullname="probe.Foo.t_fail" result="Failed" duration="1.0">
      <failure><message>boom</message></failure>
    </test-case>
  </test-suite>
</test-run>
"""

TRX = """<?xml version="1.0"?>
<TestRun id="p" name="ProbeSuite" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testName="t_pass" outcome="Passed" duration="00:00:01.0000000"/>
    <UnitTestResult testName="t_fail" outcome="Failed" duration="00:00:01.0000000">
      <Output><ErrorInfo><Message>boom</Message></ErrorInfo></Output>
    </UnitTestResult>
  </Results>
</TestRun>
"""

XUNIT = """<?xml version="1.0"?>
<assemblies>
  <assembly name="probe.dll" total="2" passed="1" failed="1" time="2.0">
    <collection name="ProbeSuite" total="2" passed="1" failed="1" time="2.0">
      <test name="probe.Foo.t_pass" type="probe.Foo" method="t_pass" result="Pass" time="1.0"/>
      <test name="probe.Foo.t_fail" type="probe.Foo" method="t_fail" result="Fail" time="1.0">
        <failure><message>boom</message></failure>
      </test>
    </collection>
  </assembly>
</assemblies>
"""

# Real pytest-json-report emits "exitcode" and "root" at the top; detection
# keys on both, so a fixture without them is not representative of the tool.
PYTEST = """{"exitcode": 1, "root": "/repo", "tests": [
  {"nodeid": "t.py::t_pass", "outcome": "passed", "call": {"duration": 1.0, "outcome": "passed"}},
  {"nodeid": "t.py::t_fail", "outcome": "failed", "call": {"duration": 1.0, "outcome": "failed", "longrepr": "boom"}}
]}"""

# Real Mochawesome stats carry passes/failures alongside tests — the markers
# detection uses.
CYPRESS = """{"stats": {"tests": 2, "passes": 1, "failures": 1}, "results": [{"file": "p.cy.js", "suites": [{"title": "ProbeSuite", "tests": [
  {"title": "t_pass", "fullTitle": "ProbeSuite t_pass", "duration": 1000, "state": "passed", "pass": true},
  {"title": "t_fail", "fullTitle": "ProbeSuite t_fail", "duration": 1000, "state": "failed", "fail": true,
   "err": {"message": "boom"}}], "suites": []}], "tests": []}]}"""

# The Playwright JSON reporter always emits a top-level config.projects block;
# detection keys on config + projects + suites together.
PLAYWRIGHT = """{"config": {"projects": [{"name": "chromium"}]}, "suites": [{"title": "p.spec.ts", "file": "p.spec.ts", "suites": [{"title": "ProbeSuite", "specs": [
  {"title": "t_pass", "ok": true, "tests": [{"status": "expected", "results": [{"status": "passed", "duration": 1000}]}]},
  {"title": "t_fail", "ok": false, "tests": [{"status": "unexpected", "results": [{"status": "failed", "duration": 1000,
   "error": {"message": "boom"}}]}]}], "suites": []}], "specs": []}]}"""

CUCUMBER = """[{"id": "p", "name": "ProbeSuite", "uri": "p.feature", "keyword": "Feature", "elements": [
  {"id": "s1", "name": "t_pass", "type": "scenario", "keyword": "Scenario",
   "steps": [{"keyword": "Given ", "name": "x", "result": {"status": "passed", "duration": 1000000000}}]},
  {"id": "s2", "name": "t_fail", "type": "scenario", "keyword": "Scenario",
   "steps": [{"keyword": "Given ", "name": "y", "result": {"status": "failed", "duration": 1000000000,
    "error_message": "boom"}}]}]}]"""


def _parse(fmt: str, content: str, filename: str = "report.xml"):
    """Route through the dispatch the ingest worker actually uses.

    CORRECTED 2026-08-30. This helper's docstring already said "route through
    the SAME dispatch the ingest worker uses" — but its body was a *copy* of
    that dispatch, a local dict literal mapping format to parser. So the two
    halves this file guards (detection returns the right format string; each
    parser handles its own fixture) were both real, and the connector between
    them was not covered by anything: ``worker/tasks._parse_file_to_results``
    was at 23 uncovered statements, and its only caller,
    ``ingest_uploaded_file``, has never executed.

    Routing ``trx`` to the xUnit parser in the worker would have left every
    test here green while every TRX upload produced zero results behind a 202
    — the exact shape of the TestNG defect this file was written for, one seam
    further along.

    Now it calls the production function, so the dispatch table cannot drift
    away from the tests that claim to cover it. See
    [[feedback_two_modules_one_rule]].
    """
    from app.worker.tasks import _parse_file_to_results

    return _parse_file_to_results(content, fmt, filename, str(uuid.uuid4()))


# (format, fixture, filename used for detection)
COVERAGE = [
    ("testng", TESTNG_NATIVE, "testng-results.xml"),
    ("junit", SUREFIRE, "TEST-probe.xml"),
    ("robot", ROBOT, "output.xml"),
    ("nunit", NUNIT, "nunit-results.xml"),
    ("trx", TRX, "results.trx"),
    ("xunit", XUNIT, "xunit-results.xml"),
    ("pytest", PYTEST, "report.json"),
    ("cypress", CYPRESS, "mochawesome.json"),
    ("playwright", PLAYWRIGHT, "pw-results.json"),
    ("cucumber", CUCUMBER, "cucumber.json"),
]


# ── The class guard ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt,fixture,filename", COVERAGE)
def test_every_detectable_format_parses_to_something(fmt, fixture, filename):
    """A format the detector can return, that then parses a representative file
    to nothing, is a SILENT data-loss path — the uploader is told it worked.

    This is the assertion that would have caught TestNG.
    """
    parsed = _parse(fmt, fixture, filename)
    assert len(parsed) > 0, f"{fmt} parsed a representative file to ZERO results"


@pytest.mark.parametrize("fmt,fixture,filename", COVERAGE)
def test_detection_routes_each_fixture_to_its_own_format(fmt, fixture, filename):
    """The other half of the pair: a fixture must be DETECTED as the format
    whose parser handles it. Detection and parsing agreeing separately is not
    enough — they have to agree with each other."""
    from app.routers.ingest import _detect_format

    detected = _detect_format(filename, fixture.encode("utf-8"))
    # junit and testng deliberately share a parser; either answer is correct
    # for a surefire document.
    if fmt in ("junit", "testng"):
        assert detected in ("junit", "testng"), f"{filename} detected as {detected}"
    else:
        assert detected == fmt, f"{filename} detected as {detected}, want {fmt}"


# ── The TestNG regression specifically ──────────────────────────────────────

def test_native_testng_results_parses_every_method():
    """The reported case: <testng-results>/<suite>/<test>/<class>/<test-method>."""
    parsed = _parse("testng", TESTNG_NATIVE)
    assert len(parsed) == 3
    assert Counter(r["status"] for r in parsed) == {
        "passed": 1, "failed": 1, "skipped": 1,
    }


def test_native_testng_keeps_the_identity_a_human_recognises():
    parsed = _parse("testng", TESTNG_NATIVE)
    by_name = {r["test_name"]: r for r in parsed}
    assert set(by_name) == {"t_pass", "t_fail", "t_skip"}
    assert by_name["t_fail"]["suite_name"] == "ProbeSuite"
    assert by_name["t_fail"]["class_name"] == "probe.Foo"
    assert by_name["t_fail"]["package_name"] == "probe"


def test_native_testng_carries_the_failure_detail():
    """An exception with no message or stack is a failure nobody can triage."""
    parsed = _parse("testng", TESTNG_NATIVE)
    fail = next(r for r in parsed if r["status"] == "failed")
    assert "expected 200 but was 500" in (fail["error_message"] or "")
    assert "Foo.java:42" in (fail["stack_trace"] or "")


def test_native_testng_reads_durations():
    parsed = _parse("testng", TESTNG_NATIVE)
    assert next(r for r in parsed if r["test_name"] == "t_pass")["duration_ms"] == 1000


def test_configuration_methods_are_not_counted_as_tests():
    """@BeforeMethod/@AfterSuite arrive as <test-method is-config="true">.
    Counting them would inflate every total a TestNG user sees."""
    doc = TESTNG_NATIVE.replace(
        '<test-method status="PASS" name="t_pass" duration-ms="1000"/>',
        '<test-method status="PASS" name="t_pass" duration-ms="1000"/>'
        '<test-method status="PASS" name="setUp" is-config="true" duration-ms="5"/>',
    )
    parsed = _parse("testng", doc)
    assert len(parsed) == 3
    assert "setUp" not in {r["test_name"] for r in parsed}


def test_an_unrecognised_status_is_not_silently_a_pass():
    """A status this parser does not know is not evidence the test passed —
    that would turn a parser gap into a green build."""
    doc = TESTNG_NATIVE.replace('status="PASS" name="t_pass"', 'status="WOBBLE" name="t_pass"')
    parsed = _parse("testng", doc)
    assert next(r for r in parsed if r["test_name"] == "t_pass")["status"] == "unknown"


def test_surefire_still_parses_after_the_native_branch_was_added():
    """The two shapes share one entry point; adding native support must not
    have shadowed the path that already worked."""
    parsed = _parse("junit", SUREFIRE)
    assert len(parsed) == 3
    assert Counter(r["status"] for r in parsed) == {
        "passed": 1, "failed": 1, "skipped": 1,
    }


def test_malformed_xml_returns_empty_rather_than_raising():
    """A corrupt upload must not take the ingest worker down."""
    assert _parse("testng", "<testng-results><suite>") == []
