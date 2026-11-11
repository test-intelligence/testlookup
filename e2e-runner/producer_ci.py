"""Pytest collector for deliberate pass/fail fixtures in their native frameworks."""

from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Thread
import xml.etree.ElementTree as ET

import pytest

HERE = Path(__file__).resolve().parent
NATIVE = HERE / "fixtures/native/producers"
JS = HERE / "reporters/js"
MODULES = JS / "node_modules"
FILES = {
    NATIVE / "playwright/reports.spec.cjs": "playwright",
    NATIVE / "testng/src/test/java/ReportsTest.java": "testng",
    HERE / "reporters/go/reporter_test.go": "go",
    JS / "release.jest.test.cjs": "jest",
    JS / "release.playwright.spec.ts": "playwright",
    JS / "release.vitest.test.mjs": "vitest",
}


def expected_failure(command, cwd, env, report=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=240)
    assert result.returncode == 1, (
        f"Producer returned {result.returncode}, expected the deliberate failure."
    )
    if report is not None and not report.is_file():
        diagnostic = (result.stderr or result.stdout).decode(errors="replace")
        pytest.fail(
            f"Native report {report.name} was not created.\n{diagnostic[:1600]}",
            pytrace=False,
        )
    return result.stdout


@contextmanager
def go_receiver():
    # Go's TestMain opens its SDK session before the native tests run.
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/v1/stream/sessions":
                reply = {
                    "run_id": "ci-run",
                    "session_id": "ci-session",
                    "session_token": "ci-token",
                }
            elif self.path == "/api/v1/stream/events/batch":
                reply = {"accepted": len(payload["events"])}
            else:
                self.send_error(404)
                return
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()


def verify_file(source, work):
    framework = FILES[source]
    env = {**os.environ, "NODE_PATH": str(MODULES), "CI": "true"}
    if framework == "go":
        with go_receiver() as url:
            env.update(
                TESTLOOKUP_URL=url,
                TESTLOOKUP_PROJECT_ID="ci-project",
                TESTLOOKUP_TOKEN="ci-token",
            )
            data = expected_failure(
                ["go", "test", "-count=1", "-json", "."], source.parent, env
            )
        events = [json.loads(line) for line in data.splitlines()]
        outcomes = [
            (event["Test"], event["Action"])
            for event in events
            if "Test" in event and event["Action"] in {"pass", "fail", "skip"}
        ]
        expected = [("TestReleasePass", "pass"), ("TestReleaseFail", "fail")]
    elif framework == "testng":
        project = work / "testng"
        shutil.copytree(
            NATIVE / "testng", project, ignore=shutil.ignore_patterns("target")
        )
        report = project / "target/surefire-reports/TEST-ReportsTest.xml"
        expected_failure(
            [shutil.which("mvn"), "-B", "-ntp", "test"], project, env, report
        )
        junit = ET.parse(report)
        outcomes = [
            (
                case.get("name"),
                next(
                    (c.tag for c in case if c.tag in {"failure", "skipped", "error"}),
                    "passed",
                ),
            )
            for case in junit.findall("testcase")
        ]
        expected = [("passes", "passed"), ("fails", "failure"), ("skips", "skipped")]
    elif framework == "playwright":
        # Keep Playwright's generated browser artifacts outside the checkout.
        report = work / "playwright.json"
        env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(report)
        expected_failure(
            [
                "node",
                MODULES / "@playwright/test/cli.js",
                "test",
                source.name,
                "--reporter=json",
                "--workers=1",
                f"--output={work / 'artifacts'}",
            ],
            source.parent,
            env,
            report,
        )
        data = json.loads(report.read_bytes())
        outcomes = [
            (spec["title"], attempt["status"])
            for suite in data["suites"]
            for spec in suite["specs"]
            for test in spec["tests"]
            for attempt in test["results"]
        ]
        expected = (
            [("passes", "passed"), ("fails", "failed"), ("skips", "skipped")]
            if source.parent != JS
            else [("release_pass", "passed"), ("release_fail", "failed")]
        )
    else:
        report = work / "results.json"
        if framework == "jest":
            args = [
                MODULES / "jest/bin/jest.js",
                "--runInBand",
                "--runTestsByPath",
                source.name,
                "--json",
                f"--outputFile={report}",
            ]
        else:
            args = [
                MODULES / "vitest/vitest.mjs",
                "run",
                source.name,
                "--reporter=json",
                f"--outputFile={report}",
            ]
        expected_failure(["node", *args], source.parent, env, report)
        data = json.loads(report.read_bytes())
        outcomes = [
            (case["title"], case["status"])
            for suite in data["testResults"]
            for case in suite["assertionResults"]
        ]
        expected = [("release_pass", "passed"), ("release_fail", "failed")]
    assert Counter(outcomes) == Counter(expected)


class ProducerFile(pytest.File):
    def collect(self):
        yield ProducerCase.from_parent(self, name=FILES[self.path])


class ProducerCase(pytest.Item):
    def runtest(self):
        with tempfile.TemporaryDirectory() as work:
            verify_file(self.path, Path(work))


def pytest_collect_file(file_path, parent):
    if file_path in FILES:
        return ProducerFile.from_parent(parent, path=file_path)
