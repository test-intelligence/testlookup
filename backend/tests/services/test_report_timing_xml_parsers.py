"""EXEC-01: the XML parsers keep what each report says about WHEN its tests ran.

TestNG (native and surefire/JUnit), NUnit 3, TRX and xUnit.net v2, on
reporter-shaped fixtures under ``tests/fixtures/timing/`` and on the real
``samples/junit/auth-suite.xml``. Each case asserts the exact values, that
``duration_ms`` is unchanged, and that a format (or a file) with no clock leaves
the timing keys ABSENT: "not reported" must never read as a zero or a "now".

The contract the parsers share (key names, ``ReportTimings``) is
``app.services.report_timings``; its helpers are pinned at the bottom.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.nunit_parser import parse_nunit_xml, parse_nunit_xml_with_timings
from app.services.report_timings import (
    CASE_TIMING_KEYS,
    TIMING_SOURCE_DERIVED,
    add_suite_timing,
    apply_case_clock,
    case_timing_columns,
    empty_timings,
    parse_report_time,
)
from app.services.testng_parser import parse_testng_xml, parse_testng_xml_with_timings
from app.services.trx_parser import parse_trx_xml, parse_trx_xml_with_timings
from app.services.xunit_parser import parse_xunit_xml, parse_xunit_xml_with_timings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "timing"
SAMPLES = Path(__file__).resolve().parents[3] / "samples"
RUN = "run-1"
#: Every key a result dict may carry about timing (the six of the contract + the source).
TIMING_KEYS = (*CASE_TIMING_KEYS, "timing_source")


def _at(hour: int, minute: int, second: int, ms: int = 0) -> datetime:
    return datetime(2026, 10, 14, hour, minute, second, ms * 1000, tzinfo=timezone.utc)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _by_name(cases: list[dict]) -> dict[str, dict]:
    return {case["test_name"]: case for case in cases}


def _suites(timings) -> dict[str, dict]:
    return {suite["name"]: suite for suite in timings["suites"]}


def _no_timing(case: dict) -> bool:
    return not any(key in case for key in TIMING_KEYS)


# ── TestNG native (testng-results.xml) ─────────────────────────────────────


@pytest.fixture(scope="module")
def testng():
    return parse_testng_xml_with_timings(_fixture("testng-results-config.xml"), RUN)


def test_testng_config_methods_are_still_not_tests(testng):
    cases, _ = testng
    assert sorted(case["test_name"] for case in cases) == [
        "addsItem", "appliesCoupon", "paysByCard", "paysByVoucher",
    ]


def test_testng_reads_each_tests_own_start_and_finish(testng):
    cases = _by_name(testng[0])
    assert cases["addsItem"]["started_at"] == _at(10, 0, 1)
    assert cases["addsItem"]["finished_at"] == _at(10, 0, 3)
    assert cases["appliesCoupon"]["started_at"] == _at(10, 0, 3)
    assert cases["appliesCoupon"]["finished_at"] == _at(10, 0, 6)
    assert cases["paysByVoucher"]["started_at"] == _at(10, 0, 8)
    # Native clocks: nothing derived, and "UTC" is exact (no assumed-zone flag).
    assert all(case.get("timing_source") is None for case in cases.values())
    assert all("source_extensions" not in case for case in cases.values())


def test_testng_before_and_after_method_become_the_tests_setup_and_teardown(testng):
    cases = _by_name(testng[0])
    adds, coupon = cases["addsItem"], cases["appliesCoupon"]
    assert (adds["setup_ms"], adds["call_ms"], adds["teardown_ms"]) == (120, 1600, 80)
    assert (coupon["setup_ms"], coupon["call_ms"], coupon["teardown_ms"]) == (110, 2400, 70)
    # A test with no method-level config has no phase split to show.
    for name in ("paysByCard", "paysByVoucher"):
        assert not {"setup_ms", "call_ms", "teardown_ms"} & set(cases[name])


def test_testng_durations_are_unchanged(testng):
    cases = _by_name(testng[0])
    assert {name: case["duration_ms"] for name, case in cases.items()} == {
        "addsItem": 1600, "appliesCoupon": 2400, "paysByCard": 1000, "paysByVoucher": 0,
    }


def test_testng_class_level_config_is_suite_time(testng):
    _, timings = testng
    assert _suites(timings) == {
        "Checkout Suite": {
            "name": "Checkout Suite",
            "started_at": _at(10, 0, 0),
            "finished_at": _at(10, 0, 9),
            "wall_ms": 9000,
            "setup_ms": 900,     # @BeforeClass startBrowser
            "teardown_ms": 300,  # @AfterClass quitBrowser
        },
    }
    # The native file states no run-level clock: the suites are the evidence.
    assert (timings["run_started_at"], timings["run_finished_at"], timings["run_wall_ms"]) == (None, None, None)


def test_testng_public_parser_returns_exactly_the_twins_cases(testng):
    assert parse_testng_xml(_fixture("testng-results-config.xml"), RUN) == testng[0]


def test_testng_zone_abbreviation_is_kept_as_wall_time_and_flagged():
    """TestNG writes its JVM's zone; ``IST`` names three offsets, so it is not guessed."""
    xml = """<testng-results><suite name="S"><test name="T"><class name="a.B">
      <test-method name="m" status="PASS" duration-ms="250"
                   started-at="2026-10-14T15:30:01 IST" finished-at="2026-10-14T15:30:02 IST"/>
    </class></test></suite></testng-results>"""
    (case,) = parse_testng_xml(xml, RUN)
    assert case["started_at"] == datetime(2026, 10, 14, 15, 30, 1, tzinfo=timezone.utc)
    assert case["source_extensions"] == {"timing_tz": "assumed_utc"}


def test_testng_start_without_finish_derives_the_finish():
    xml = """<testng-results><suite name="S"><test name="T"><class name="a.B">
      <test-method name="m" status="PASS" duration-ms="250" started-at="2026-10-14T10:00:01Z"/>
    </class></test></suite></testng-results>"""
    (case,) = parse_testng_xml(xml, RUN)
    assert case["started_at"] == _at(10, 0, 1)
    assert case["finished_at"] == _at(10, 0, 1, 250)
    assert case["timing_source"] == TIMING_SOURCE_DERIVED


def test_testng_after_method_follows_the_test_it_ran_after_when_the_writer_grouped_configs():
    """A writer that lists configs apart from tests is re-sorted by start."""
    xml = """<testng-results><suite name="S"><test name="T"><class name="a.B">
      <test-method name="t1" status="PASS" duration-ms="100" started-at="2026-10-14T10:00:01Z"/>
      <test-method name="t2" status="PASS" duration-ms="100" started-at="2026-10-14T10:00:03Z"/>
      <test-method is-config="true" is-before-method="true" name="up" duration-ms="5" started-at="2026-10-14T10:00:00Z"/>
      <test-method is-config="true" is-after-method="true" name="down" duration-ms="7" started-at="2026-10-14T10:00:02Z"/>
      <test-method is-config="true" is-before-method="true" name="up" duration-ms="6" started-at="2026-10-14T10:00:02.5Z"/>
    </class></test></suite></testng-results>"""
    cases = _by_name(parse_testng_xml(xml, RUN))
    assert (cases["t1"]["setup_ms"], cases["t1"]["teardown_ms"]) == (5, 7)
    assert cases["t2"]["setup_ms"] == 6 and "teardown_ms" not in cases["t2"]


# ── JUnit / surefire (the default upload route) ────────────────────────────


def test_junit_sample_carries_no_per_test_clock_so_the_keys_are_absent():
    """The real sample: JUnit XML has no per-test start/finish."""
    cases, timings = parse_testng_xml_with_timings(
        (SAMPLES / "junit" / "auth-suite.xml").read_text(encoding="utf-8"), RUN,
    )
    assert len(cases) == 6
    assert all(_no_timing(case) for case in cases)
    assert {case["test_name"]: case["duration_ms"] for case in cases}["test_login_success"] == 1234
    # The suite's own time is known; when it started is not.
    assert timings["suites"] == [{
        "name": "AuthSuite", "started_at": None, "finished_at": None, "wall_ms": 12456,
        "setup_ms": None, "teardown_ms": None,
    }]
    assert (timings["run_started_at"], timings["run_wall_ms"]) == (None, None)


def test_junit_suite_timestamp_and_testsuites_time_are_the_suite_and_run_timing():
    cases, timings = parse_testng_xml_with_timings(_fixture("junit-jest-suites.xml"), RUN)
    assert len(cases) == 3 and all(_no_timing(case) for case in cases)
    suites = _suites(timings)
    # jest-junit writes the runner's local clock with no zone: read as UTC.
    assert suites["cart.test.js"]["started_at"] == _at(10, 0, 0)
    assert suites["cart.test.js"]["wall_ms"] == 3002
    assert suites["cart.test.js"]["finished_at"] == _at(10, 0, 3, 2)
    assert suites["payment.test.js"]["wall_ms"] == 1204
    assert timings["run_wall_ms"] == 4521
    assert timings["run_started_at"] is None  # <testsuites> states a time, not a start


def test_junit_nonstandard_testcase_timestamp_gives_a_start_and_a_derived_finish():
    xml = """<testsuite name="S" time="1.5">
      <testcase classname="a.B" name="t" time="1.5" timestamp="2026-10-14T10:00:01"/>
    </testsuite>"""
    (case,) = parse_testng_xml(xml, RUN)
    assert case["started_at"] == _at(10, 0, 1)
    assert case["finished_at"] == _at(10, 0, 2, 500)
    assert case["timing_source"] == TIMING_SOURCE_DERIVED
    assert case["source_extensions"] == {"timing_tz": "assumed_utc"}
    assert case["duration_ms"] == 1500


def test_junit_suites_of_one_name_merge_into_one_suite_timing():
    xml = """<testsuites>
      <testsuite name="pytest" time="2.0" timestamp="2026-10-14T10:00:00+00:00"><testcase name="a" time="1"/></testsuite>
      <testsuite name="pytest" time="3.0" timestamp="2026-10-14T10:00:05+00:00"><testcase name="b" time="1"/></testsuite>
    </testsuites>"""
    _, timings = parse_testng_xml_with_timings(xml, RUN)
    assert timings["suites"] == [{
        "name": "pytest", "started_at": _at(10, 0, 0), "finished_at": _at(10, 0, 8),
        "wall_ms": 8000, "setup_ms": None, "teardown_ms": None,
    }]


def test_unparseable_xml_still_returns_empty_results_and_empty_timings():
    assert parse_testng_xml_with_timings("<not-xml", RUN) == ([], empty_timings())


# ── NUnit 3 ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def nunit():
    return parse_nunit_xml_with_timings(_fixture("nunit3-cart.xml"), RUN)


def test_nunit_reads_each_test_cases_start_and_end_time(nunit):
    cases = _by_name(nunit[0])
    assert (cases["AddsItem"]["started_at"], cases["AddsItem"]["finished_at"]) == (
        _at(10, 0, 0, 300), _at(10, 0, 1, 500),
    )
    assert (cases["AppliesCoupon"]["started_at"], cases["AppliesCoupon"]["finished_at"]) == (
        _at(10, 0, 1, 550), _at(10, 0, 4, 250),
    )
    assert cases["RemovesItem"]["started_at"] == cases["RemovesItem"]["finished_at"] == _at(10, 0, 4, 260)
    # NUnit reports no per-test setup/teardown split and no worker.
    for case in cases.values():
        assert not {"setup_ms", "call_ms", "teardown_ms", "worker_id", "timing_source"} & set(case)
    assert {name: case["duration_ms"] for name, case in cases.items()} == {
        "AddsItem": 1200, "AppliesCoupon": 2700, "RemovesItem": 0,
    }


def test_nunit_suite_timing_is_the_fixture_that_names_the_suite_and_the_run_is_test_run(nunit):
    cases, timings = nunit
    assert {case["suite_name"] for case in cases} == {"Shop.CartTests"}
    assert _suites(timings) == {
        "Shop.CartTests": {
            "name": "Shop.CartTests", "started_at": _at(10, 0, 0, 250), "finished_at": _at(10, 0, 4, 300),
            "wall_ms": 4050, "setup_ms": None, "teardown_ms": None,
        },
    }
    # <test-run> writes "yyyy-MM-dd HH:mm:ssZ" (a space, second resolution).
    assert (timings["run_started_at"], timings["run_finished_at"], timings["run_wall_ms"]) == (
        _at(10, 0, 0), _at(10, 0, 5), 4512,
    )


def test_nunit_public_parser_returns_exactly_the_twins_cases(nunit):
    assert parse_nunit_xml(_fixture("nunit3-cart.xml"), RUN) == nunit[0]


def test_nunit_without_clocks_leaves_the_keys_absent():
    from tests.services.test_dotnet_parsers import _NUNIT_XML

    cases, timings = parse_nunit_xml_with_timings(_NUNIT_XML, RUN)
    assert len(cases) == 5 and all(_no_timing(case) for case in cases)
    assert timings == empty_timings()


def test_nunit_clock_without_a_zone_is_flagged_assumed_utc():
    xml = """<test-run><test-suite type="TestFixture" name="F" fullname="A.F">
      <test-case name="t" result="Passed" duration="0.5" start-time="2026-10-14 10:00:01" end-time="2026-10-14 10:00:01.5"/>
    </test-suite></test-run>"""
    (case,) = parse_nunit_xml(xml, RUN)
    assert case["started_at"] == _at(10, 0, 1)
    assert case["source_extensions"] == {"timing_tz": "assumed_utc"}


# ── TRX ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def trx():
    return parse_trx_xml_with_timings(_fixture("trx-cart.trx"), RUN)


def test_trx_reads_start_and_end_time_and_converts_the_offset_to_utc(trx):
    cases = _by_name(trx[0])
    assert (cases["AddsItem"]["started_at"], cases["AddsItem"]["finished_at"]) == (
        _at(10, 0, 1), _at(10, 0, 2, 200),
    )
    assert (cases["AppliesCoupon"]["started_at"], cases["AppliesCoupon"]["finished_at"]) == (
        _at(10, 0, 2, 300), _at(10, 0, 5),
    )
    assert cases["RemovesItem"]["status"] == "SKIPPED"
    assert {name: case["duration_ms"] for name, case in cases.items()} == {
        "AddsItem": 1200, "AppliesCoupon": 2700, "RemovesItem": 0,
    }
    # computerName is the machine, not a worker slot.
    assert all("worker_id" not in case for case in cases.values())


def test_trx_run_timing_is_testrun_times(trx):
    _, timings = trx
    assert (timings["run_started_at"], timings["run_finished_at"], timings["run_wall_ms"]) == (
        _at(10, 0, 0, 500), _at(10, 0, 6, 250), 5750,
    )
    assert timings["suites"] == []  # TRX has no per-class timing


def test_trx_public_parser_returns_exactly_the_twins_cases(trx):
    assert parse_trx_xml(_fixture("trx-cart.trx"), RUN) == trx[0]


def test_trx_never_executed_clock_is_not_a_timestamp():
    """``0001-01-01`` is a placeholder, not when a test ran."""
    xml = """<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>
      <UnitTestResult testId="x" testName="t" outcome="NotExecuted"
                      startTime="0001-01-01T00:00:00.0000000+00:00" endTime="0001-01-01T00:00:00.0000000+00:00"/>
    </Results></TestRun>"""
    (case,) = parse_trx_xml(xml, RUN)
    assert _no_timing(case)


# ── xUnit.net v2 ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def xunit():
    return parse_xunit_xml_with_timings(_fixture("xunit-v2-cart.xml"), RUN)


def test_xunit_has_no_per_test_clock_so_no_test_carries_timing_keys(xunit):
    cases, _ = xunit
    assert len(cases) == 3
    assert all(_no_timing(case) for case in cases)
    assert {case["test_name"]: case["duration_ms"] for case in cases} == {
        "AddsItem": 1200, "AppliesCoupon": 2700, "PaysByVoucher": 0,
    }


def test_xunit_collection_time_is_its_suites_wall_and_the_run_is_start_rtf(xunit):
    _, timings = xunit
    assert _suites(timings) == {
        "Test collection for Shop.Tests.CartTests": {
            "name": "Test collection for Shop.Tests.CartTests", "started_at": None, "finished_at": None,
            "wall_ms": 3900, "setup_ms": None, "teardown_ms": None,
        },
        "Test collection for Shop.Tests.PaymentTests": {
            "name": "Test collection for Shop.Tests.PaymentTests", "started_at": None, "finished_at": None,
            "wall_ms": 0, "setup_ms": None, "teardown_ms": None,
        },
    }
    assert (timings["run_started_at"], timings["run_finished_at"], timings["run_wall_ms"]) == (
        _at(10, 0, 0), _at(10, 0, 7, 500), 7500,
    )


def test_xunit_older_runner_run_date_and_time_start_the_run():
    xml = """<assemblies timestamp="10/14/2026 10:00:07">
      <assembly name="/ci/T.dll" run-date="2026-10-14" run-time="10:00:00" time="7.300" total="1">
        <collection name="C" time="7.000"><test name="T.A" method="A" time="1.0" result="Pass"/></collection>
      </assembly></assemblies>"""
    cases, timings = parse_xunit_xml_with_timings(xml, RUN)
    assert _no_timing(cases[0])
    assert (timings["run_started_at"], timings["run_finished_at"], timings["run_wall_ms"]) == (
        _at(10, 0, 0), _at(10, 0, 7, 300), 7300,
    )


def test_xunit_public_parser_returns_exactly_the_twins_cases(xunit):
    assert parse_xunit_xml(_fixture("xunit-v2-cart.xml"), RUN) == xunit[0]


# ── The shared helpers (report_timings) ────────────────────────────────────


@pytest.mark.parametrize("text, expected, assumed", [
    ("2026-10-14T10:00:01Z", _at(10, 0, 1), False),
    ("2026-10-14T10:00:01 UTC", _at(10, 0, 1), False),
    ("2026-10-14 10:00:01Z", _at(10, 0, 1), False),
    ("2026-10-14T12:00:01.2500000+02:00", _at(10, 0, 1, 250), False),
    ("2026-10-14T10:00:01", _at(10, 0, 1), True),
    ("2026-10-14T10:00:01 IST", _at(10, 0, 1), True),
    ("0001-01-01T00:00:00+00:00", None, False),
    ("not a time", None, False),
    ("", None, False),
    (None, None, False),
])
def test_parse_report_time(text, expected, assumed):
    assert parse_report_time(text) == (expected, assumed)


def test_apply_case_clock_adds_nothing_without_a_clock():
    case = {"duration_ms": 5}
    apply_case_clock(case, None, "")
    assert case == {"duration_ms": 5}


def test_case_timing_columns_bounds_every_value_for_its_column():
    columns = case_timing_columns({
        "started_at": "2026-10-14T10:00:05Z",
        "finished_at": _at(10, 0, 1),         # before the start: a broken clock, dropped
        "setup_ms": -3,                        # negative: dropped
        "call_ms": 24 * 3600 * 1000 + 1,       # over a day: dropped
        "teardown_ms": 80.4,
        "worker_id": "w" * 100,
        "timing_source": "invented",
    })
    assert columns == {
        "started_at": _at(10, 0, 5), "finished_at": None, "setup_ms": None, "call_ms": None,
        "teardown_ms": 80, "worker_id": "w" * 64, "timing_source": "report",
    }


def test_case_timing_columns_claim_no_source_without_timing():
    assert case_timing_columns({"duration_ms": 10, "timing_source": "report"}) == dict.fromkeys(
        ("started_at", "finished_at", "setup_ms", "call_ms", "teardown_ms", "worker_id", "timing_source"),
    )


def test_case_timing_columns_keep_a_derived_source_and_a_zero_worker():
    columns = case_timing_columns({"started_at": _at(10, 0, 0), "worker_id": 0, "timing_source": "derived"})
    assert (columns["worker_id"], columns["timing_source"]) == ("0", "derived")


def test_add_suite_timing_skips_a_silent_suite_and_sums_walls_without_a_span():
    timings = empty_timings()
    add_suite_timing(timings, {"name": "S"})
    assert timings["suites"] == []
    add_suite_timing(timings, {"name": "S", "wall_ms": 100, "setup_ms": 5})
    add_suite_timing(timings, {"name": "S", "wall_ms": 50, "teardown_ms": 2})
    assert timings["suites"] == [{
        "name": "S", "started_at": None, "finished_at": None, "wall_ms": 150, "setup_ms": 5, "teardown_ms": 2,
    }]
    add_suite_timing(timings, {"name": "S", "started_at": _at(10, 0, 0)})  # wall unknown: a sum would understate
    assert timings["suites"][0]["wall_ms"] is None
    assert timings["suites"][0]["started_at"] == _at(10, 0, 0)


def test_finish_before_start_is_not_a_span():
    from app.services.report_timings import wall_ms_between

    assert wall_ms_between(_at(10, 0, 1), _at(10, 0, 0)) is None
    assert wall_ms_between(_at(10, 0, 0), _at(10, 0, 0) + timedelta(milliseconds=1500)) == 1500
