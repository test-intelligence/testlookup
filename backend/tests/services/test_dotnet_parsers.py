"""
Regression tests for the .NET-ecosystem parsers — NUnit3 XML, Visual Studio
TRX and xUnit.net v2 XML (PMF backlog US-1.3 / US-1.4) — plus their
_detect_format sniff rules.

Covers: happy paths with realistic mixed-outcome fixtures, status mapping
incl. the FAILED-vs-BROKEN vocabulary (NUnit label="Error", TRX Timeout/
Aborted/Error), duration conversion (NUnit/xUnit float seconds → ms, TRX
HH:MM:SS.fffffff → ms), error message/stack extraction, tags, malformed
input → [], and auto-detection precedence (nunit/trx/xunit before the junit
<testsuite fallback; a TRX file must NOT land on junit even though it
contains no <testsuite marker; plain junit and robot stay untouched).
"""
from __future__ import annotations

import uuid

from app.routers.ingest import _detect_format
from app.services.nunit_parser import parse_nunit_xml
from app.services.trx_parser import parse_trx_xml
from app.services.xunit_parser import parse_xunit_xml

# ── NUnit3 ─────────────────────────────────────────────────────────────────

_NUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<test-run id="2" testcasecount="5" result="Failed" total="5" passed="1" failed="2" skipped="2">
  <test-suite type="Assembly" id="0-1010" name="MyApp.Tests.dll" fullname="C:/ci/MyApp.Tests.dll" result="Failed">
    <test-suite type="TestSuite" id="0-1011" name="MyApp" fullname="MyApp" result="Failed">
      <test-suite type="TestFixture" id="0-1000" name="CalculatorTests" fullname="MyApp.CalculatorTests" classname="MyApp.CalculatorTests" result="Failed">
        <test-case id="0-1001" name="Adds" fullname="MyApp.CalculatorTests.Adds"
                   methodname="Adds" classname="MyApp.CalculatorTests"
                   result="Passed" duration="0.052">
          <properties>
            <property name="Category" value="smoke"/>
            <property name="Author" value="anand"/>
          </properties>
        </test-case>
        <test-case id="0-1002" name="Subtracts" fullname="MyApp.CalculatorTests.Subtracts"
                   methodname="Subtracts" classname="MyApp.CalculatorTests"
                   result="Failed" duration="0.110">
          <failure>
            <message><![CDATA[  Expected: 1
  But was:  2
]]></message>
            <stack-trace><![CDATA[at MyApp.CalculatorTests.Subtracts() in /src/CalculatorTests.cs:line 27]]></stack-trace>
          </failure>
        </test-case>
        <test-case id="0-1003" name="Divides" fullname="MyApp.CalculatorTests.Divides"
                   methodname="Divides" classname="MyApp.CalculatorTests"
                   result="Failed" label="Error" duration="0.010">
          <failure>
            <message><![CDATA[System.DivideByZeroException : Attempted to divide by zero.]]></message>
            <stack-trace><![CDATA[at MyApp.Calculator.Divide(Int32 a, Int32 b)]]></stack-trace>
          </failure>
        </test-case>
        <test-case id="0-1004" name="SlowPath" fullname="MyApp.CalculatorTests.SlowPath"
                   methodname="SlowPath" classname="MyApp.CalculatorTests"
                   result="Skipped" duration="0.001">
          <reason><message><![CDATA[Ignored: not run on CI]]></message></reason>
        </test-case>
        <test-suite type="ParameterizedMethod" id="0-1005" name="Multiplies" fullname="MyApp.CalculatorTests.Multiplies" result="Inconclusive">
          <test-case id="0-1006" name="Multiplies(2,3)" fullname="MyApp.CalculatorTests.Multiplies(2,3)"
                     methodname="Multiplies" classname="MyApp.CalculatorTests"
                     result="Inconclusive" duration="0.5"/>
        </test-suite>
      </test-suite>
    </test-suite>
  </test-suite>
</test-run>
"""


def test_nunit_parser_happy_path_statuses_durations_and_suites():
    run_id = str(uuid.uuid4())
    results = parse_nunit_xml(_NUNIT_XML, run_id)

    assert len(results) == 5
    by_name = {r["test_name"]: r for r in results}

    passed = by_name["Adds"]
    assert passed["status"] == "PASSED"
    assert passed["suite_name"] == "MyApp.CalculatorTests"  # nearest TestFixture fullname
    assert passed["full_name"] == "MyApp.CalculatorTests.Adds"
    assert passed["class_name"] == "MyApp.CalculatorTests"
    assert passed["package_name"] == "MyApp"
    assert passed["duration_ms"] == 52  # float seconds → ms
    assert passed["error_message"] is None
    assert passed["tags"] == ["smoke"]  # Category property only, not Author
    assert passed["framework"] == "nunit"
    assert passed["test_run_id"] == run_id
    assert passed["steps"] == []

    # Plain result="Failed" = assertion failure → FAILED.
    failed = by_name["Subtracts"]
    assert failed["status"] == "FAILED"
    assert "Expected: 1" in failed["error_message"]
    assert "line 27" in failed["stack_trace"]
    assert len(failed["steps"]) == 1
    step = failed["steps"][0]
    assert step["status"] == "FAILED"
    assert step["keyword"] == "failure"
    assert "Expected: 1" in step["assertion_message"]
    assert "line 27" in step["assertion_trace"]

    # label="Error" on a Failed case = unexpected exception → BROKEN
    # (FAILED-vs-BROKEN vocab, matching the TestNG failure-vs-error split).
    broken = by_name["Divides"]
    assert broken["status"] == "BROKEN"
    assert "DivideByZeroException" in broken["error_message"]
    assert broken["steps"][0]["status"] == "BROKEN"
    assert broken["steps"][0]["keyword"] == "error"

    skipped = by_name["SlowPath"]
    assert skipped["status"] == "SKIPPED"
    assert "not run on CI" in skipped["error_message"]

    # Inconclusive → SKIPPED with reason "inconclusive"; parameterized
    # test-cases (leaf nodes under a ParameterizedMethod suite) still parse
    # with the fixture ancestor as the suite.
    inconclusive = by_name["Multiplies(2,3)"]
    assert inconclusive["status"] == "SKIPPED"
    assert inconclusive["error_message"] == "inconclusive"
    assert inconclusive["suite_name"] == "MyApp.CalculatorTests"
    assert inconclusive["duration_ms"] == 500


def test_nunit_parser_falls_back_to_assembly_suite_name():
    xml = """<test-run id="1">
    <test-suite type="Assembly" name="Bare.Tests.dll" fullname="C:/ci/Bare.Tests.dll">
      <test-case name="Orphan" fullname="Orphan" result="Passed" duration="0.2"/>
    </test-suite>
    </test-run>"""
    results = parse_nunit_xml(xml, "run-1")
    assert len(results) == 1
    assert results[0]["suite_name"] == "Bare.Tests.dll"
    assert results[0]["duration_ms"] == 200


def test_nunit_parser_rejects_malformed_input():
    assert parse_nunit_xml("not xml at all", "run-1") == []
    assert parse_nunit_xml("<testsuite><testcase name='x'/></testsuite>", "run-1") == []
    assert parse_nunit_xml("", "run-1") == []


# ── TRX ────────────────────────────────────────────────────────────────────

_TRX_NS = "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"

_TRX_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<TestRun id="c72" xmlns="{_TRX_NS}" name="ci@agent 2026-07-09 10:00:00">
  <Results>
    <UnitTestResult executionId="e1" testId="id-1" testName="Adds"
                    outcome="Passed" duration="00:00:00.0520000"/>
    <UnitTestResult executionId="e2" testId="id-2" testName="Subtracts"
                    outcome="Failed" duration="00:00:01.5000000">
      <Output>
        <ErrorInfo>
          <Message>Assert.AreEqual failed. Expected:&lt;1&gt;. Actual:&lt;2&gt;.</Message>
          <StackTrace>at MyApp.CalculatorTests.Subtracts() in /src/CalculatorTests.cs:line 27</StackTrace>
        </ErrorInfo>
      </Output>
    </UnitTestResult>
    <UnitTestResult executionId="e3" testId="id-3" testName="SlowPath"
                    outcome="NotExecuted"/>
    <UnitTestResult executionId="e4" testId="id-4" testName="Hangs"
                    outcome="Timeout" duration="00:01:00.0000000"/>
    <UnitTestResult executionId="e5" testId="id-5" testName="Flaky"
                    outcome="Inconclusive" duration="00:00:00.1000000"/>
  </Results>
  <TestDefinitions>
    <UnitTest id="id-1" name="Adds">
      <TestMethod codeBase="MyApp.Tests.dll" className="MyApp.CalculatorTests, MyApp.Tests, Version=1.0.0.0" name="Adds"/>
    </UnitTest>
    <UnitTest id="id-2" name="Subtracts">
      <TestMethod codeBase="MyApp.Tests.dll" className="MyApp.CalculatorTests" name="Subtracts"/>
    </UnitTest>
    <UnitTest id="id-3" name="SlowPath">
      <TestMethod codeBase="MyApp.Tests.dll" className="MyApp.CalculatorTests" name="SlowPath"/>
    </UnitTest>
    <UnitTest id="id-4" name="Hangs">
      <TestMethod codeBase="MyApp.Tests.dll" className="MyApp.Integration.PipelineTests" name="Hangs"/>
    </UnitTest>
  </TestDefinitions>
</TestRun>
"""


def test_trx_parser_happy_path_join_durations_and_statuses():
    run_id = str(uuid.uuid4())
    results = parse_trx_xml(_TRX_XML, run_id)

    assert len(results) == 5
    by_name = {r["test_name"]: r for r in results}

    passed = by_name["Adds"]
    assert passed["status"] == "PASSED"
    # Joined via testId → TestDefinitions; assembly qualification stripped.
    assert passed["class_name"] == "MyApp.CalculatorTests"
    assert passed["suite_name"] == "MyApp.CalculatorTests"
    assert passed["package_name"] == "MyApp"
    assert passed["full_name"] == "MyApp.CalculatorTests.Adds"
    assert passed["duration_ms"] == 52  # HH:MM:SS.fffffff → ms
    assert passed["framework"] == "trx"
    assert passed["test_run_id"] == run_id

    failed = by_name["Subtracts"]
    assert failed["status"] == "FAILED"
    assert failed["duration_ms"] == 1500
    assert "Assert.AreEqual failed" in failed["error_message"]
    assert "line 27" in failed["stack_trace"]
    assert len(failed["steps"]) == 1
    assert failed["steps"][0]["status"] == "FAILED"
    assert "Assert.AreEqual failed" in failed["steps"][0]["assertion_message"]

    skipped = by_name["SlowPath"]
    assert skipped["status"] == "SKIPPED"  # NotExecuted → SKIPPED
    assert skipped["duration_ms"] is None  # no duration attribute

    # Timeout = infrastructure shape → BROKEN, minutes convert too.
    hangs = by_name["Hangs"]
    assert hangs["status"] == "BROKEN"
    assert hangs["duration_ms"] == 60_000
    assert hangs["suite_name"] == "MyApp.Integration.PipelineTests"
    assert hangs["package_name"] == "MyApp.Integration"
    assert hangs["error_message"] == "timeout"  # raw outcome surfaced
    assert hangs["steps"][0]["status"] == "BROKEN"
    assert hangs["steps"][0]["keyword"] == "error"

    # Inconclusive → SKIPPED; no definition entry → testName fallback.
    flaky = by_name["Flaky"]
    assert flaky["status"] == "SKIPPED"
    assert flaky["class_name"] is None
    assert flaky["suite_name"] == "Unknown Suite"
    assert flaky["full_name"] == "Flaky"


def test_trx_parser_tolerates_missing_namespace():
    xml = """<TestRun id="1">
    <Results>
      <UnitTestResult testId="t1" testName="Bare" outcome="Passed" duration="00:00:00.2500000"/>
    </Results>
    </TestRun>"""
    results = parse_trx_xml(xml, "run-1")
    assert len(results) == 1
    assert results[0]["status"] == "PASSED"
    assert results[0]["duration_ms"] == 250


def test_trx_parser_rejects_malformed_input():
    assert parse_trx_xml("not xml at all", "run-1") == []
    assert parse_trx_xml("<testsuite><testcase name='x'/></testsuite>", "run-1") == []
    assert parse_trx_xml("", "run-1") == []


# ── xUnit.net v2 ───────────────────────────────────────────────────────────

_XUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<assemblies timestamp="07/09/2026 10:00:00">
  <assembly name="C:\\ci\\MyApp.Tests.dll" test-framework="xUnit.net 2.4.2" total="4" passed="1" failed="1" skipped="1">
    <collection name="Test collection for MyApp.CalculatorTests" total="3">
      <test name="MyApp.CalculatorTests.Adds" type="MyApp.CalculatorTests" method="Adds"
            time="0.052" result="Pass">
        <traits>
          <trait name="category" value="smoke"/>
          <trait name="priority" value="1"/>
        </traits>
      </test>
      <test name="MyApp.CalculatorTests.Divides" type="MyApp.CalculatorTests" method="Divides"
            time="0.010" result="Fail">
        <failure exception-type="Xunit.Sdk.EqualException">
          <message><![CDATA[Assert.Equal() Failure
Expected: 1
Actual:   2]]></message>
          <stack-trace><![CDATA[at MyApp.CalculatorTests.Divides() in /src/CalculatorTests.cs:line 42]]></stack-trace>
        </failure>
      </test>
      <test name="MyApp.CalculatorTests.Slow" type="MyApp.CalculatorTests" method="Slow"
            time="0" result="Skip">
        <reason><![CDATA[flaky on CI — quarantined]]></reason>
      </test>
    </collection>
    <collection name="Test collection for MyApp.ParserTests" total="1">
      <test name="MyApp.ParserTests.Parses" type="MyApp.ParserTests" method="Parses"
            time="1.5" result="Pass"/>
    </collection>
  </assembly>
</assemblies>
"""


def test_xunit_parser_happy_path_collections_traits_and_durations():
    run_id = str(uuid.uuid4())
    results = parse_xunit_xml(_XUNIT_XML, run_id)

    assert len(results) == 4
    by_full = {r["full_name"]: r for r in results}

    passed = by_full["MyApp.CalculatorTests.Adds"]
    assert passed["status"] == "PASSED"
    assert passed["test_name"] == "Adds"  # method attr, not the display name
    assert passed["suite_name"] == "Test collection for MyApp.CalculatorTests"
    assert passed["class_name"] == "MyApp.CalculatorTests"
    assert passed["package_name"] == "MyApp"
    assert passed["duration_ms"] == 52  # float seconds → ms
    assert passed["tags"] == ["category:smoke", "priority:1"]  # name:value traits
    assert passed["framework"] == "xunit"
    assert passed["test_run_id"] == run_id

    failed = by_full["MyApp.CalculatorTests.Divides"]
    assert failed["status"] == "FAILED"
    assert "Assert.Equal() Failure" in failed["error_message"]
    assert "line 42" in failed["stack_trace"]
    assert len(failed["steps"]) == 1
    assert failed["steps"][0]["status"] == "FAILED"
    assert "Assert.Equal() Failure" in failed["steps"][0]["assertion_message"]

    skipped = by_full["MyApp.CalculatorTests.Slow"]
    assert skipped["status"] == "SKIPPED"
    assert "flaky on CI" in skipped["error_message"]  # CDATA reason text

    other = by_full["MyApp.ParserTests.Parses"]
    assert other["suite_name"] == "Test collection for MyApp.ParserTests"
    assert other["duration_ms"] == 1500


def test_xunit_parser_single_assembly_root_and_assembly_fallback():
    xml = """<assembly name="/ci/out/Solo.Tests.dll" test-framework="xUnit.net 2.4.2">
    <collection>
      <test name="Solo.Tests.Works" type="Solo.Tests" method="Works" time="0.25" result="Pass"/>
    </collection>
    </assembly>"""
    results = parse_xunit_xml(xml, "run-1")
    assert len(results) == 1
    # Unnamed collection → assembly file basename as the suite.
    assert results[0]["suite_name"] == "Solo.Tests.dll"
    assert results[0]["status"] == "PASSED"
    assert results[0]["duration_ms"] == 250


def test_xunit_parser_rejects_malformed_input():
    assert parse_xunit_xml("not xml at all", "run-1") == []
    assert parse_xunit_xml("<testsuite><testcase name='x'/></testsuite>", "run-1") == []
    assert parse_xunit_xml("", "run-1") == []


# ── _detect_format sniff rules ─────────────────────────────────────────────


def test_detect_format_nunit_trx_xunit_before_junit():
    assert _detect_format("TestResult.xml", _NUNIT_XML.encode()) == "nunit"
    assert _detect_format("results.trx", _TRX_XML.encode()) == "trx"
    assert _detect_format("xunit-results.xml", _XUNIT_XML.encode()) == "xunit"
    # Plain JUnit must still land on junit.
    junit = b'<?xml version="1.0"?><testsuites><testsuite name="s"/></testsuites>'
    assert _detect_format("results.xml", junit) == "junit"
    # Robot's existing rule is untouched.
    robot = b'<robot generator="Robot 6.1"><suite name="S"/></robot>'
    assert _detect_format("output.xml", robot) == "robot"


def test_detect_format_trx_not_swallowed_by_junit_default():
    # A TRX file contains NO <testsuite marker — without the dedicated rule it
    # would fall through to the junit default and parse to zero results.
    detected = _detect_format("run.trx", _TRX_XML.encode())
    assert detected == "trx"
    assert detected != "junit"
    # But a namespace-less <TestRun without the VS TeamTest namespace does NOT
    # match the trx sniff (the namespace string is the distinctive marker).
    bare = b'<TestRun id="1"><Results/></TestRun>'
    assert _detect_format("run.xml", bare) != "trx"


def test_detect_format_xunit_bare_assembly_needs_framework_marker():
    with_marker = b'<assembly name="T.dll" test-framework="xUnit.net 2.4.2"><collection/></assembly>'
    assert _detect_format("r.xml", with_marker) == "xunit"
    # A random <assembly element without the marker must not land on xunit.
    without_marker = b'<assembly name="something"><thing/></assembly>'
    assert _detect_format("r.xml", without_marker) != "xunit"
