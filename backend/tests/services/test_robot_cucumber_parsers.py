"""
Regression tests for the Robot Framework and Cucumber JSON parsers
(PMF backlog US-1.1 / US-1.2) plus their _detect_format sniff rules.

Covers: happy path across nested suites, status mapping (incl. the
FAILED-vs-BROKEN vocabulary for Cucumber undefined steps), RF3-6 vs RF7
timing shapes, Background step folding, Scenario Outline example rows,
malformed input, and auto-detection precedence (cucumber before the
.json→allure fallback; robot before the junit <testsuite marker).
"""
from __future__ import annotations

import json
import uuid

from app.routers.ingest import _detect_format
from app.services.cucumber_parser import parse_cucumber_json
from app.services.robot_parser import parse_robot_xml

# ── Robot Framework ────────────────────────────────────────────────────────

_ROBOT_XML_RF6 = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 6.1.1 (Python 3.11.4 on win32)" generated="20260709 10:00:00.000">
<suite name="Regression" source="/tests">
  <suite name="Login">
    <test name="Valid Login">
      <kw name="Open Browser">
        <status status="PASS" starttime="20260709 10:00:00.000" endtime="20260709 10:00:01.250"/>
      </kw>
      <kw name="Login As Admin">
        <status status="PASS" starttime="20260709 10:00:01.250" endtime="20260709 10:00:02.000"/>
      </kw>
      <tag>smoke</tag>
      <status status="PASS" starttime="20260709 10:00:00.000" endtime="20260709 10:00:02.000"/>
    </test>
    <test name="Invalid Password Shows Error">
      <kw name="Login As">
        <msg timestamp="20260709 10:00:03.000" level="FAIL">Element 'id=error-banner' not visible</msg>
        <status status="FAIL" starttime="20260709 10:00:02.100" endtime="20260709 10:00:07.400">Element 'id=error-banner' not visible</status>
      </kw>
      <kw name="Capture Screenshot">
        <status status="NOT RUN"/>
      </kw>
      <status status="FAIL" starttime="20260709 10:00:02.000" endtime="20260709 10:00:07.500">Element 'id=error-banner' not visible after 5 seconds.</status>
    </test>
    <test name="SSO Login">
      <status status="SKIP" starttime="20260709 10:00:07.500" endtime="20260709 10:00:07.501">SSO not configured in this environment</status>
    </test>
    <status status="FAIL" starttime="20260709 10:00:00.000" endtime="20260709 10:00:07.501"/>
  </suite>
  <status status="FAIL" starttime="20260709 10:00:00.000" endtime="20260709 10:00:07.501"/>
</suite>
<statistics><total><stat pass="1" fail="1" skip="1">All Tests</stat></total></statistics>
</robot>
"""


def test_robot_parser_nested_suites_statuses_and_timing():
    run_id = str(uuid.uuid4())
    results = parse_robot_xml(_ROBOT_XML_RF6, run_id)

    assert len(results) == 3
    by_name = {r["test_name"]: r for r in results}

    passed = by_name["Valid Login"]
    assert passed["status"] == "PASSED"
    assert passed["suite_name"] == "Regression > Login"
    assert passed["full_name"] == "Regression > Login.Valid Login"
    assert passed["duration_ms"] == 2000  # RF3-6 starttime/endtime pair
    assert passed["error_message"] is None
    assert passed["tags"] == ["smoke"]
    assert passed["framework"] == "robot"
    assert passed["test_run_id"] == run_id
    # Direct child keywords surface as steps.
    assert [s["name"] for s in passed["steps"]] == ["Open Browser", "Login As Admin"]
    assert all(s["status"] == "PASSED" for s in passed["steps"])
    assert passed["steps"][0]["duration_ms"] == 1250

    failed = by_name["Invalid Password Shows Error"]
    assert failed["status"] == "FAILED"
    assert "not visible after 5 seconds" in failed["error_message"]
    # Failing keyword's status body aggregates into the coarse trace.
    assert "Login As" in failed["stack_trace"]
    # NOT RUN keywords are dropped from steps; the FAIL keyword is kept.
    assert [s["name"] for s in failed["steps"]] == ["Login As"]
    step = failed["steps"][0]
    assert step["status"] == "FAILED"
    assert "not visible" in step["assertion_message"]
    assert "not visible" in step["assertion_trace"]

    skipped = by_name["SSO Login"]
    assert skipped["status"] == "SKIPPED"
    assert "SSO not configured" in skipped["error_message"]


def test_robot_parser_rf7_elapsed_attribute():
    xml = """<robot generator="Robot 7.0">
    <suite name="Smoke">
      <test name="Ping">
        <status status="PASS" start="2026-07-09T10:00:00.000000" elapsed="1.5"/>
      </test>
    </suite>
    </robot>"""
    results = parse_robot_xml(xml, "run-1")
    assert len(results) == 1
    assert results[0]["status"] == "PASSED"
    assert results[0]["duration_ms"] == 1500  # RF7 float-seconds elapsed


def test_robot_parser_rf3_tags_wrapper_and_not_run_status():
    xml = """<robot generator="Robot 3.2.2">
    <suite name="Legacy">
      <test name="Old Style">
        <tags><tag>regression</tag><tag>slow</tag></tags>
        <status status="NOT RUN" starttime="20260709 10:00:00.000" endtime="20260709 10:00:00.001"/>
      </test>
    </suite>
    </robot>"""
    results = parse_robot_xml(xml, "run-1")
    assert results[0]["status"] == "SKIPPED"  # NOT RUN → SKIPPED
    assert results[0]["tags"] == ["regression", "slow"]


def test_robot_parser_rejects_malformed_input():
    assert parse_robot_xml("not xml at all", "run-1") == []
    assert parse_robot_xml("<testsuite><testcase name='x'/></testsuite>", "run-1") == []
    assert parse_robot_xml("", "run-1") == []


# ── Cucumber ───────────────────────────────────────────────────────────────


def _cucumber_report() -> str:
    return json.dumps([
        {
            "keyword": "Feature",
            "name": "Login",
            "uri": "features/login.feature",
            "tags": [{"name": "@auth"}],
            "elements": [
                {
                    "keyword": "Background",
                    "type": "background",
                    "name": "Given a running app",
                    "steps": [
                        {
                            "keyword": "Given ",
                            "name": "the app is running",
                            "result": {"status": "passed", "duration": 100_000_000},
                        }
                    ],
                },
                {
                    "keyword": "Scenario",
                    "type": "scenario",
                    "name": "Valid login",
                    "id": "login;valid-login",
                    "tags": [{"name": "@smoke"}],
                    "steps": [
                        {
                            "keyword": "When ",
                            "name": "I log in as admin",
                            "result": {"status": "passed", "duration": 1_500_000_000},
                        },
                        {
                            "keyword": "Then ",
                            "name": "I see the dashboard",
                            "result": {"status": "passed", "duration": 400_000_000},
                        },
                    ],
                },
                {
                    "keyword": "Background",
                    "type": "background",
                    "name": "Given a running app",
                    "steps": [
                        {
                            "keyword": "Given ",
                            "name": "the app is running",
                            "result": {"status": "passed", "duration": 100_000_000},
                        }
                    ],
                },
                {
                    "keyword": "Scenario",
                    "type": "scenario",
                    "name": "Bad password",
                    "id": "login;bad-password",
                    "steps": [
                        {
                            "keyword": "When ",
                            "name": "I log in with a bad password",
                            "result": {"status": "passed", "duration": 900_000_000},
                        },
                        {
                            "keyword": "Then ",
                            "name": "I see an error banner",
                            "result": {
                                "status": "failed",
                                "duration": 5_000_000_000,
                                "error_message": "AssertionError: banner not visible\n  at features/steps/login.py:42",
                            },
                        },
                        {
                            "keyword": "And ",
                            "name": "I stay on the login page",
                            "result": {"status": "skipped"},
                        },
                    ],
                },
                {
                    "keyword": "Scenario",
                    "type": "scenario",
                    "name": "Locked account",
                    "id": "login;locked-account",
                    "steps": [
                        {
                            "keyword": "When ",
                            "name": "I log in as a locked user",
                            "result": {"status": "undefined"},
                        }
                    ],
                },
                {
                    "keyword": "Scenario",
                    "type": "scenario",
                    "name": "MFA prompt",
                    "id": "login;mfa-prompt",
                    "steps": [
                        {
                            "keyword": "When ",
                            "name": "I log in with MFA",
                            "result": {"status": "passed", "duration": 200_000_000},
                        },
                        {
                            "keyword": "Then ",
                            "name": "I am prompted for a code",
                            "result": {"status": "pending"},
                        },
                    ],
                },
            ],
        }
    ])


def test_cucumber_parser_statuses_background_folding_and_durations():
    run_id = str(uuid.uuid4())
    results = parse_cucumber_json(_cucumber_report(), run_id)

    assert len(results) == 4  # backgrounds fold, they don't emit cases
    by_name = {r["test_name"]: r for r in results}

    passed = by_name["Valid login"]
    assert passed["status"] == "PASSED"
    assert passed["suite_name"] == "Login"
    assert passed["class_name"] == "features/login.feature"
    assert passed["full_name"] == "Login.login;valid-login"
    # Background step folds in ahead of the scenario's own steps.
    assert [s["name"] for s in passed["steps"]][0] == "the app is running"
    assert len(passed["steps"]) == 3
    # Nanoseconds → ms, summed across background + scenario steps.
    assert passed["duration_ms"] == 100 + 1500 + 400
    # Feature tag + scenario tag both ingest, @ stripped.
    assert passed["tags"] == ["auth", "smoke"]
    assert passed["framework"] == "cucumber"

    failed = by_name["Bad password"]
    assert failed["status"] == "FAILED"
    # error_message = failing step label + first line; trace = full text.
    assert "Then I see an error banner" in failed["error_message"]
    assert "banner not visible" in failed["error_message"]
    assert "login.py:42" in failed["stack_trace"]
    then_step = next(s for s in failed["steps"] if s["name"] == "I see an error banner")
    assert then_step["status"] == "FAILED"
    assert then_step["keyword"] == "Then"
    assert then_step["duration_ms"] == 5000
    skipped_step = next(s for s in failed["steps"] if s["name"] == "I stay on the login page")
    assert skipped_step["status"] == "SKIPPED"

    # undefined step = missing step definition = automation bug → BROKEN
    # (FAILED-vs-BROKEN vocab, matching the TestNG failure-vs-error split).
    undefined = by_name["Locked account"]
    assert undefined["status"] == "BROKEN"
    assert "step undefined" in undefined["error_message"]

    pending = by_name["MFA prompt"]
    assert pending["status"] == "SKIPPED"
    assert "step pending" in pending["error_message"]


def test_cucumber_parser_rejects_malformed_input():
    assert parse_cucumber_json("not json", "run-1") == []
    assert parse_cucumber_json('{"object": "at root"}', "run-1") == []
    assert parse_cucumber_json("[]", "run-1") == []
    # Feature without an elements list is skipped, not fatal.
    assert parse_cucumber_json('[{"keyword": "Feature", "name": "X"}]', "run-1") == []


def test_cucumber_parser_steps_without_results_are_unknown_not_passed():
    report = json.dumps([
        {
            "keyword": "Feature",
            "name": "F",
            "elements": [
                {
                    "type": "scenario",
                    "name": "No results",
                    "steps": [{"keyword": "Given ", "name": "something"}],
                }
            ],
        }
    ])
    results = parse_cucumber_json(report, "run-1")
    assert len(results) == 1
    assert results[0]["status"] == "UNKNOWN"


# ── _detect_format sniff rules ─────────────────────────────────────────────


def test_detect_format_robot_before_junit():
    assert _detect_format("output.xml", _ROBOT_XML_RF6.encode()) == "robot"
    # A plain JUnit file must still land on junit.
    junit = b'<?xml version="1.0"?><testsuites><testsuite name="s"/></testsuites>'
    assert _detect_format("results.xml", junit) == "junit"


def test_detect_format_cucumber_before_allure_json_fallback():
    assert _detect_format("cucumber.json", _cucumber_report().encode()) == "cucumber"
    # A non-cucumber .json still falls back to allure.
    other = json.dumps({"some": "object"}).encode()
    assert _detect_format("mystery.json", other) == "allure"
    # Allure single-result JSON is untouched by the new rule.
    allure = json.dumps({"uuid": "u", "name": "n", "status": "passed"}).encode()
    assert _detect_format("x-result.json", allure) == "allure"
