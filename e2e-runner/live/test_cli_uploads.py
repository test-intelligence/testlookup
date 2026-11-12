"""Upload real framework output using the installed TestLookup command."""

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.live, pytest.mark.ephemeral]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PYTHON_CASES = {
    "test_report_pass": "PASSED",
    "test_report_failure": "FAILED",
    "test_report_skipped": "SKIPPED",
}
LOWER_CASES = {"passes": "PASSED", "fails": "FAILED", "skips": "SKIPPED"}
UPPER_CASES = {"Passes": "PASSED", "Fails": "FAILED", "Skips": "SKIPPED"}
ROBOT_CASES = {
    "Producer observed pass": "PASSED",
    "Producer observed failure": "FAILED",
    "Producer observed skip": "SKIPPED",
}
# Expected names and outcomes come from the test programs under fixtures/native/producers.
REPORTS = {
    "junit": ("pytest-junit.xml", PYTHON_CASES),
    "pytest": ("pytest-report.json", PYTHON_CASES),
    "robot": ("robot.xml", ROBOT_CASES),
    "testng": ("native/testng.xml", LOWER_CASES),
    "nunit": ("native/nunit.xml", UPPER_CASES),
    "trx": ("native/report.trx", UPPER_CASES),
    "xunit": ("native/xunit.xml", UPPER_CASES),
    "playwright": ("native/playwright.json", LOWER_CASES),
    "cypress": ("native/cypress.json", LOWER_CASES),
    "cucumber": ("native/cucumber.json", UPPER_CASES),
    "allure": (
        "native/allure-results",
        {"test_report_broken": "BROKEN"},
    ),
}


@pytest.fixture
def run_upload(project, role_client, tmp_path):
    executable = Path(sys.executable).with_name(
        "testlookup.exe" if os.name == "nt" else "testlookup"
    )
    if not executable.is_file():
        pytest.fail(
            "BLOCKED: install the release CLI in the runner environment", pytrace=False
        )
    owner, project_id, name = project
    admin, _ = role_client("ADMIN")
    identity = owner.get("auth/me")
    assert identity.status_code == 200
    created = admin.post(
        "keys",
        json={
            "name": name,
            "project_id": project_id,
            "target_user_id": identity.json()["id"],
            "expires_days": 1,
            "scopes": ["project:write"],
        },
    )
    assert created.status_code == 201, (
        f"Create disposable upload key: HTTP {created.status_code}"
    )
    key = created.json()

    def invoke(arguments):
        result = subprocess.run(
            [str(executable), "upload", "file", *arguments],
            env={
                **os.environ,
                "TESTLOOKUP_URL": os.environ["TL_API"],
                "TESTLOOKUP_API_KEY": key["raw_key"],
                "XDG_CONFIG_HOME": str(tmp_path),
                "PYTHONUTF8": "1",
            },
            cwd=tmp_path,
            capture_output=True,
            timeout=180,
        )
        # Never include raw subprocess output or the API key in release evidence.
        exit_code = result.returncode
        assert exit_code == 0, f"CLI upload --wait exited {exit_code}"
        return json.loads(result.stdout)

    try:
        yield invoke
    finally:
        assert admin.delete("keys/" + key["id"]).status_code == 204


@pytest.mark.parametrize("fmt", REPORTS)
def test_cli_upload_native(project, run_upload, fmt):
    client, project_id, build = project
    filename, expected = REPORTS[fmt]
    source = FIXTURES / filename
    if fmt == "allure":
        # allure-pytest generates a new UUID filename each time fixtures are regenerated.
        [source] = [
            record
            for record in sorted(source.glob("*-result.json"))
            if json.loads(record.read_bytes())["name"] == "test_report_broken"
        ]
    if fmt == "junit":
        from testlookup_cli.commands.upload import SUPPORTED_UPLOAD_FORMATS

        assert set(SUPPORTED_UPLOAD_FORMATS) - {"auto"} == set(REPORTS)
    receipt = run_upload(
        [
            str(source),
            "--project",
            project_id,
            "--build",
            build,
            "--format",
            fmt,
            "--wait",
            "--wait-timeout",
            "90",
            "--no-ai",
            "--no-run-context",
            "--no-commit-range",
            "--profile",
            build,
            "--output",
            "json",
        ],
    )
    assert receipt["state"] == "succeeded" and receipt["task_id"] and receipt["run_id"]
    run_id = receipt["run_id"]
    persisted = client.get("runs/" + run_id)
    assert persisted.status_code == 200
    run = persisted.json()
    assert (run["id"], run["project_id"], run["build_number"], run["total_tests"]) == (
        run_id,
        project_id,
        build,
        len(expected),
    )
    counts = Counter(expected.values())
    for status in ("PASSED", "FAILED", "BROKEN", "SKIPPED"):
        assert run[status.lower() + "_tests"] == counts[status]
    cases = client.get(f"runs/{run_id}/tests")
    assert cases.status_code == 200
    rows = cases.json()
    assert rows["total"] == len(expected)
    assert sorted(
        (row["test_name"], row["status"], row["test_run_id"]) for row in rows["items"]
    ) == sorted((name, status, run_id) for name, status in expected.items())
