"""EXEC-01 (group B): the JSON / Robot report parsers put each test's start, finish, phase split and worker on the case.

One reporter-shaped fixture per format lives in ``tests/fixtures/timing/``; the samples in ``samples/`` (which carry
no per-test clock) pin the other half of the contract: a format, or a report, that states no timing yields cases with
NO timing keys at all — never a None, a zero or "now". Every fixture test also pins ``duration_ms`` so the timing work
cannot move the number the rest of the product already reads (Cucumber is the one deliberate change: hooks count).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.allure_parser import parse_allure_result, parse_allure_zip, parse_allure_zip_with_timings
from app.services.cucumber_parser import parse_cucumber_json, parse_cucumber_json_with_timings
from app.services.cypress_parser import parse_cypress_json, parse_cypress_json_with_timings
from app.services.playwright_parser import parse_playwright_json, parse_playwright_json_with_timings
from app.services.pytest_parser import parse_pytest_json, parse_pytest_json_with_timings
from app.services.report_timings import CASE_TIMING_KEYS, case_timing_columns
from app.services.robot_parser import parse_robot_xml, parse_robot_xml_with_timings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "timing"
SAMPLES = Path(__file__).resolve().parents[3] / "samples"
RUN = "00000000-0000-0000-0000-00000000e001"
TIMING_KEYS = (*CASE_TIMING_KEYS, "timing_source")


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _by_name(cases: list[dict]) -> dict[str, dict]:
    return {c["test_name"]: c for c in cases}


def _no_timing_keys(case: dict) -> bool:
    return not any(key in case for key in TIMING_KEYS)


# ── Playwright ───────────────────────────────────────────────────────────────


def test_playwright_attempt_start_worker_and_hook_split():
    cases, timings = parse_playwright_json_with_timings(_read("playwright-2workers-retry.json"), RUN)
    by = _by_name(cases)

    shown = by["shows the run list [chromium]"]
    assert shown["started_at"] == _utc("2026-10-14T10:00:01.250")
    assert shown["finished_at"] == _utc("2026-10-14T10:00:03.050")  # start + duration
    assert shown["worker_id"] == "1"
    assert (shown["setup_ms"], shown["call_ms"], shown["teardown_ms"]) == (120, 1600, 80)
    assert shown["duration_ms"] == 1800  # unchanged: the final attempt's duration
    assert shown["timing_source"] == "derived"  # the format states no finish
    # The step tree keeps its shape; a step that states a start carries it as epoch ms.
    assert [s["name"] for s in shown["steps"]] == ["Before Hooks", "open /runs", "After Hooks"]
    assert shown["steps"][1]["start_ms"] == int(_utc("2026-10-14T10:00:01.370").timestamp() * 1000)

    # Stock JSON-reporter steps (no category, no startTime): no hook split, step start stays None.
    plain = by["filters runs by branch [chromium]"]
    assert plain["started_at"] == _utc("2026-10-14T10:00:03.060")
    assert plain["worker_id"] == "1"
    assert not any(k in plain for k in ("setup_ms", "call_ms", "teardown_ms"))
    assert plain["steps"][0]["start_ms"] is None

    # A retried test is timed by the attempt that decided it (the one duration_ms describes), and its worker is
    # the SLOT (parallelIndex 0) even though Playwright restarted the worker process (workerIndex 2).
    retried = by["uploads an avatar [chromium]"]
    assert retried["retry_count"] == 1 and retried["is_flaky"] is True
    assert retried["started_at"] == _utc("2026-10-14T10:00:04.140")
    assert retried["finished_at"] == _utc("2026-10-14T10:00:07.040")
    assert retried["worker_id"] == "0"
    assert retried["duration_ms"] == 2900

    assert {c["worker_id"] for c in cases} == {"0", "1"}
    assert sum(c["duration_ms"] for c in cases) == 17400
    assert timings["run_started_at"] == _utc("2026-10-14T10:00:01")
    assert timings["run_finished_at"] == _utc("2026-10-14T10:00:10")
    assert timings["run_wall_ms"] == 9000
    assert timings["suites"] == []


def test_playwright_public_parser_is_the_twin_without_timings():
    content = _read("playwright-2workers-retry.json")
    assert parse_playwright_json(content, RUN) == parse_playwright_json_with_timings(content, RUN)[0]


def test_playwright_sample_without_clock_or_worker_has_no_timing_keys():
    cases, timings = parse_playwright_json_with_timings(
        (SAMPLES / "playwright" / "playwright-results.json").read_text(encoding="utf-8"), RUN,
    )
    assert cases and all(_no_timing_keys(c) for c in cases)
    assert timings["run_started_at"] is None and timings["run_wall_ms"] is None


def test_playwright_never_run_attempt_has_no_worker():
    report = {"suites": [{"title": "a.spec.ts", "file": "a.spec.ts", "specs": [{"title": "t", "tests": [{
        "projectName": "chromium", "status": "skipped",
        "results": [{"status": "skipped", "duration": 0, "workerIndex": -1, "parallelIndex": -1}],
    }]}]}]}
    case = parse_playwright_json(json.dumps(report), RUN)[0]
    assert _no_timing_keys(case)


# ── pytest-json-report ───────────────────────────────────────────────────────


def test_pytest_phase_split_lands_on_the_case():
    cases, timings = parse_pytest_json_with_timings(_read("pytest-json-report.json"), RUN)
    by = _by_name(cases)

    created = by["test_create"]
    assert (created["setup_ms"], created["call_ms"], created["teardown_ms"]) == (120, 1600, 80)
    assert created["duration_ms"] == 1800  # unchanged: the sum of the three phases
    assert created["timing_source"] == "report"
    assert "started_at" not in created and "finished_at" not in created  # the format has no per-test clock

    refund = by["test_refund"]
    assert (refund["setup_ms"], refund["call_ms"], refund["teardown_ms"]) == (3, 2250, 500)
    assert refund["duration_ms"] == 2753

    # A setup error never reaches the call phase: no call_ms, not a zero.
    errored = by["test_migrate"]
    assert errored["status"] == "BROKEN"
    assert (errored["setup_ms"], errored["teardown_ms"]) == (750, 2)
    assert "call_ms" not in errored

    assert timings["run_finished_at"] == _utc("2026-10-14T10:00:12.500")  # ``created`` = session finish
    assert timings["run_wall_ms"] == 12500
    assert timings["run_started_at"] == _utc("2026-10-14T10:00:00")


def test_pytest_test_without_phases_has_no_timing_keys():
    report = {"created": "not-a-clock", "tests": [{"nodeid": "tests/test_a.py::test_x", "outcome": "passed"}]}
    cases, timings = parse_pytest_json_with_timings(json.dumps(report), RUN)
    assert _no_timing_keys(cases[0])
    assert timings["run_finished_at"] is None and timings["run_wall_ms"] is None


# ── Cypress / Mochawesome ────────────────────────────────────────────────────


def test_mochawesome_run_and_suite_timing():
    cases, timings = parse_cypress_json_with_timings(_read("mochawesome-merged.json"), RUN)
    # Mochawesome states no per-test clock: the cases carry no timing keys and keep their durations.
    assert all(_no_timing_keys(c) for c in cases)
    assert [c["duration_ms"] for c in cases] == [2400, 3100, 4100, 4300]

    assert timings["run_started_at"] == _utc("2026-10-14T10:00:00")
    assert timings["run_finished_at"] == _utc("2026-10-14T10:00:14.250")
    # end - start, not the merged ``stats.duration`` (13900, a sum of the files' durations).
    assert timings["run_wall_ms"] == 14250
    suites = {s["name"]: s for s in timings["suites"]}
    login = suites["cypress/e2e/login.cy.ts :: Login"]
    assert (login["wall_ms"], login["setup_ms"], login["teardown_ms"]) == (5500, 450, 50)
    coupon = suites["cypress/e2e/checkout.cy.ts :: Checkout > with a coupon"]
    assert (coupon["wall_ms"], coupon["setup_ms"], coupon["teardown_ms"]) == (8400, 200, None)
    # The wrapper suite has no tests of its own, so it names no suite a case carries.
    assert "cypress/e2e/checkout.cy.ts :: Checkout" not in suites
    assert {c["suite_name"] for c in cases} == set(suites)


def test_cypress_sample_keeps_its_cases_and_states_the_run():
    content = (SAMPLES / "cypress" / "cypress-results.json").read_text(encoding="utf-8")
    cases, timings = parse_cypress_json_with_timings(content, RUN)
    assert cases == parse_cypress_json(content, RUN)
    assert all(_no_timing_keys(c) for c in cases)
    assert timings["run_started_at"] == _utc("2026-04-15T10:00:00")
    assert timings["run_wall_ms"] == 18500


# ── Allure ───────────────────────────────────────────────────────────────────


def _allure_files() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (FIXTURES / "allure-results").iterdir()}


def test_allure_start_stop_and_container_fixtures():
    cases, timings = parse_allure_zip_with_timings(_allure_files(), RUN)
    by = _by_name(cases)

    adds = by["adds an item"]
    assert adds["started_at"] == _utc("2026-10-14T10:00:01")
    assert adds["finished_at"] == _utc("2026-10-14T10:00:02.500")
    # Two one-child containers (c-1: 190 / 50, c-4: 10 / -) are this test's own fixtures.
    assert (adds["setup_ms"], adds["call_ms"], adds["teardown_ms"]) == (200, 1500, 50)
    assert adds["duration_ms"] == 1500  # unchanged: stop - start
    assert adds["timing_source"] == "derived"  # fixtures attributed through ``children``

    # The shared container (2 children) is NOT copied onto each test; the empty one adds nothing.
    removes = by["removes an item"]
    assert removes["started_at"] == _utc("2026-10-14T10:00:03")
    assert removes["timing_source"] == "report"
    assert not any(k in removes for k in ("setup_ms", "call_ms", "teardown_ms"))

    assert _no_timing_keys(by["pays"])  # a result with no start / stop

    assert timings["suites"] == [{
        "name": "CartSuite",
        "started_at": _utc("2026-10-14T10:00:00"),
        "finished_at": _utc("2026-10-14T10:00:04"),
        "wall_ms": None,
        "setup_ms": 400,
        "teardown_ms": 100,
    }]
    assert timings["run_started_at"] is None  # Allure states no run clock
    assert parse_allure_zip(_allure_files(), RUN) == cases


def test_allure_sample_results_carry_their_start_and_stop():
    docs = json.loads((SAMPLES / "allure" / "allure-results.json").read_text(encoding="utf-8"))
    case = parse_allure_result(docs[0], RUN, "uploads/x/a-result.json")
    assert case["started_at"] == datetime.fromtimestamp(docs[0]["start"] / 1000, tz=timezone.utc)
    assert case["finished_at"] == datetime.fromtimestamp(docs[0]["stop"] / 1000, tz=timezone.utc)
    assert case["duration_ms"] == docs[0]["stop"] - docs[0]["start"]
    assert not any(k in case for k in ("setup_ms", "call_ms", "teardown_ms", "worker_id"))


def test_allure_container_with_malformed_children_is_ignored():
    files = _allure_files()
    files["bad-container.json"] = json.dumps({
        "children": [{"not": "a uuid"}, None, "r-2"],
        "befores": [{"start": 1, "stop": "x"}, {"start": 1791972002900, "stop": 1791972002950}],
    }).encode()
    by = _by_name(parse_allure_zip(files, RUN))
    # Three children = a shared fixture: never put on the one child that matched.
    assert "setup_ms" not in by["removes an item"]


# ── Robot Framework ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fixture", "source"),
    [("robot-rf6-output.xml", "report"), ("robot-rf7-output.xml", "derived")],
)
def test_robot_test_clock_fixtures_and_suite_timing(fixture, source):
    cases, timings = parse_robot_xml_with_timings(_read(fixture), RUN)
    by = _by_name(cases)

    add = by["Add Item"]
    assert add["started_at"] == _utc("2026-10-14T10:00:00.700")
    assert add["finished_at"] == _utc("2026-10-14T10:00:02.500")
    assert (add["setup_ms"], add["call_ms"], add["teardown_ms"]) == (120, 1600, 80)
    assert add["duration_ms"] == 1800  # unchanged
    assert add["timing_source"] == source  # RF 7 states start + elapsed, so its finish is computed
    assert add["source_extensions"] == {"timing_tz": "assumed_utc"}  # Robot writes local time with no zone

    remove = by["Remove Item"]
    assert remove["started_at"] == _utc("2026-10-14T10:00:02.600")
    assert remove["finished_at"] == _utc("2026-10-14T10:00:03.100")
    assert not any(k in remove for k in ("setup_ms", "call_ms", "teardown_ms"))  # no fixtures: no fake split

    assert timings["run_started_at"] == _utc("2026-10-14T10:00:00")
    assert timings["run_finished_at"] == _utc("2026-10-14T10:00:03.500")
    assert timings["run_wall_ms"] == 3500
    assert timings["suites"] == [{
        "name": "Shop > Cart",
        "started_at": _utc("2026-10-14T10:00:00.050"),
        "finished_at": _utc("2026-10-14T10:00:03.450"),
        "wall_ms": 3400,
        "setup_ms": 500,
        "teardown_ms": 250,
    }]
    assert parse_robot_xml(_read(fixture), RUN) == cases


def test_robot_status_without_times_has_no_timing_keys():
    xml = """<robot><suite name="S"><test name="T"><status status="PASS"/></test>
    <status status="PASS"/></suite></robot>"""
    cases, timings = parse_robot_xml_with_timings(xml, RUN)
    assert _no_timing_keys(cases[0]) and "source_extensions" not in cases[0]
    assert timings["suites"] == [] and timings["run_wall_ms"] is None


# ── Cucumber ─────────────────────────────────────────────────────────────────


def test_cucumber_hooks_count_toward_the_scenario_and_split_it():
    cases, timings = parse_cucumber_json_with_timings(_read("cucumber-jvm7-hooks.json"), RUN)
    by = _by_name(cases)

    add = by["Add an item"]
    # Before EXEC-01 duration_ms was the steps only: background 200 + step 1300 = 1500. It is now what the
    # framework reports: + before hook 120 + after hook 80 + the step's BeforeStep/AfterStep 50 + 50.
    assert add["duration_ms"] == 1800
    assert (add["setup_ms"], add["call_ms"], add["teardown_ms"]) == (120, 1600, 80)
    assert add["started_at"] == _utc("2026-10-14T10:00:01.250")
    assert add["finished_at"] == _utc("2026-10-14T10:00:03.050")
    assert add["timing_source"] == "derived"

    # No hooks: the old figure exactly, a clock, and no fabricated split.
    remove = by["Remove an item"]
    assert remove["duration_ms"] == 900
    assert remove["started_at"] == _utc("2026-10-14T10:00:03.100")
    assert not any(k in remove for k in ("setup_ms", "call_ms", "teardown_ms"))

    # behave / cucumber-js style: no start_timestamp, no hooks — no timing keys, duration unchanged.
    plain = by["Find by keyword"]
    assert plain["duration_ms"] == 500
    assert _no_timing_keys(plain)

    assert timings["run_started_at"] is None and timings["suites"] == []
    assert parse_cucumber_json(_read("cucumber-jvm7-hooks.json"), RUN) == cases


# ── The contract the ingestion writer reads ──────────────────────────────────


@pytest.mark.parametrize(
    ("parse", "fixture"),
    [
        (parse_playwright_json, "playwright-2workers-retry.json"),
        (parse_pytest_json, "pytest-json-report.json"),
        (parse_robot_xml, "robot-rf6-output.xml"),
        (parse_robot_xml, "robot-rf7-output.xml"),
        (parse_cucumber_json, "cucumber-jvm7-hooks.json"),
    ],
)
def test_every_timed_case_maps_onto_the_columns(parse, fixture):
    for case in parse(_read(fixture), RUN):
        columns = case_timing_columns(case)
        if _no_timing_keys(case):
            assert columns["timing_source"] is None
            continue
        assert columns["timing_source"] == case["timing_source"]
        for key in CASE_TIMING_KEYS:
            assert columns[key] == case.get(key)


@pytest.mark.parametrize(
    "parse",
    [
        parse_playwright_json_with_timings,
        parse_pytest_json_with_timings,
        parse_cypress_json_with_timings,
        parse_robot_xml_with_timings,
        parse_cucumber_json_with_timings,
    ],
)
def test_malformed_input_still_returns_an_empty_pair(parse):
    cases, timings = parse("{not json or xml", RUN)
    assert cases == []
    assert timings == {"run_started_at": None, "run_finished_at": None, "run_wall_ms": None, "suites": []}
