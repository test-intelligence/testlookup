"""Execute real framework adapters and compare the server's persisted leaves."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "e2e-runner/reporters"
JS = FIXTURES / "js"
JAVA = FIXTURES / "prepared/java"
pytestmark = [pytest.mark.live, pytest.mark.ephemeral]

JAVASCRIPT = {
    "jest": (
        "jest",
        [
            "--runInBand",
            "--runTestsByPath",
            "release.jest.test.cjs",
            "--reporters=testlookup-reporter/jest-reporter",
        ],
    ),
    "mocha": (
        "mocha",
        ["--reporter", "testlookup-reporter/mocha-reporter", "release.mocha.cjs"],
    ),
    "playwright": ("@playwright/test", ["test", "--config", "playwright.config.cjs"]),
    "vitest": (
        "vitest",
        [
            "run",
            "release.vitest.test.mjs",
            "--reporter=testlookup-reporter/vitest-reporter",
        ],
    ),
    "cypress": (
        "cypress",
        ["run", "--headless", "--browser", "electron", "--project", "."],
    ),
    "cucumber": (
        "@cucumber/cucumber",
        [
            "release.feature",
            "--require",
            "steps.cjs",
            "--format",
            "testlookup-reporter/cucumber-formatter",
        ],
    ),
}


def producer(framework, work):
    if framework in JAVASCRIPT:
        package, args = JAVASCRIPT[framework]
        package_dir = JS / "node_modules" / package
        manifest = json.loads((package_dir / "package.json").read_bytes())
        binary = manifest["bin"]
        if isinstance(binary, dict):
            name = {"playwright": "playwright", "cucumber": "cucumber-js"}.get(
                framework, framework
            )
            binary = binary[name]
        return JS, ["node", package_dir / binary, *args]
    if framework == "robot":
        return work, [
            sys.executable,
            "-m",
            "robot",
            "--outputdir",
            work,
            "--listener",
            "testlookup_robot_listener.TestLookupListener",
            FIXTURES / "robot.robot",
        ]
    if framework == "go":
        binary = (
            FIXTURES
            / "prepared"
            / ("go-reporter.exe" if os.name == "nt" else "go-reporter")
        )
        return work, [binary, "-test.v"]
    family = "testng" if framework == "testng" else framework
    jars = sorted((JAVA / family).glob("*.jar"))
    assert jars, f"Compile {family} fixtures with reporters/prepare.py before testing"
    cp = os.pathsep.join(map(str, [JAVA, JAVA / "sdk.jar", *jars]))
    if family == "testng":
        return work, [
            "java",
            "-cp",
            cp,
            "org.testng.TestNG",
            "-usedefaultlisteners",
            "false",
            "-d",
            work / "testng",
            "-testclass",
            "TestNgFixture",
        ]
    return work, [
        "java",
        "-cp",
        cp,
        "org.junit.platform.console.ConsoleLauncher",
        "execute",
        "--disable-banner",
        "--select-class=JupiterFixture",
    ]


@pytest.mark.parametrize(
    "framework", ["robot", "junit5", "junit6", "testng", "go", *JAVASCRIPT]
)
def test_framework_reporter(project, tmp_path, framework):
    client, owned, build = project
    cwd, command = producer(framework, tmp_path)
    env = {
        **os.environ,
        "TESTLOOKUP_URL": os.environ["TL_API"],
        "TESTLOOKUP_PROJECT_ID": owned,
        "TESTLOOKUP_BUILD": build,
        "TESTLOOKUP_TOKEN": client.headers["Authorization"].split(" ", 1)[1],
        "PYTHONPATH": str(ROOT / "client"),
        "CI": "true",
    }
    env.pop("TESTLOOKUP_API_KEY", None)
    env.pop("TESTLOOKUP_ENDPOINT", None)
    env.pop("TESTLOOKUP_PROJECT", None)
    if framework == "go":
        # The Go SDK reads its build label from CI_BUILD_NUMBER.
        env["CI_BUILD_NUMBER"] = build
    log = tmp_path / "producer.log"
    with log.open("wb") as output:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=150,
        )
    # All inputs deliberately contain one assertion failure. The readback below
    # prevents a tool startup failure with the same exit code from passing.
    assert result.returncode == 1, (
        f"{framework} producer exit {result.returncode}; private log {log.name}"
    )
    deadline = time.monotonic() + 45
    while True:
        response = client.get("runs", params={"project_id": owned})
        assert response.status_code == 200
        selected = [
            run for run in response.json()["items"] if run["build_number"] == build
        ]
        if selected and selected[0]["total_tests"] == 2:
            break
        assert time.monotonic() < deadline, (
            f"{framework} did not deliver both leaves to build {build}"
        )
        time.sleep(0.5)
    assert len(selected) == 1
    run = selected[0]
    assert run["project_id"] == owned
    assert (
        run["passed_tests"],
        run["failed_tests"],
        run["skipped_tests"],
        run["broken_tests"],
    ) == (1, 1, 0, 0)
    tests = client.get(f"runs/{run['id']}/tests")
    assert tests.status_code == 200
    rows = tests.json()
    assert rows["total"] == 2
    for suffix, expected in (("releasepass", "PASSED"), ("releasefail", "FAILED")):
        matches = [
            row
            for row in rows["items"]
            if suffix in re.sub("[^a-z0-9]", "", row["test_name"].lower())
        ]
        assert len(matches) == 1
        assert matches[0]["test_run_id"] == run["id"]
        assert matches[0]["status"].upper() == expected
