"""Positive, owned-project checks on the disposable release stack."""

from collections import Counter
import json
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET
import asyncio
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.live, pytest.mark.ephemeral]


def test_gate_empty_evidence(project):
    client, owned, name = project
    release = client.post("releases", json={"project_id": owned, "name": name})
    assert release.status_code == 201
    release_id = release.json()["id"]
    response = client.post(
        f"releases/{release_id}/gate/evaluate", params={"record": "false"}
    )
    assert response.status_code == 200
    decision = response.json()
    assert decision["release_id"] == release_id
    assert decision["verdict"] == "NOT_EVALUATED"
    assert decision["scorecard"]["run_count"] == 0
    assert decision["scorecard"]["measured"] is False
    assert decision["blocking_reasons"]
    assert decision["recorded"] is False and decision["decision_id"] is None


def test_mcp_sse_health(project):
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    client, _, _ = project

    async def check():
        async with sse_client(
            os.environ["TL_MCP"] + "/sse",
            headers={"Authorization": client.headers["Authorization"]},
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("health_check", {})
                assert not result.is_error
                content = "\n".join(c.text for c in result.content if c.type == "text")
                assert (
                    "- postgres: ok" in content
                    and "- mongo: ok" in content
                    and "- redis: ok" in content
                )

    asyncio.run(asyncio.wait_for(check(), timeout=40))


def test_sdk_pytest_reporter(project, tmp_path):
    client, owned, name = project
    env = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(ROOT / "client"),
        "TESTLOOKUP_URL": os.environ["TL_API"],
        "TESTLOOKUP_PROJECT_ID": owned,
        "TESTLOOKUP_BUILD": name,
        "TESTLOOKUP_TOKEN": client.headers["Authorization"].removeprefix("Bearer "),
    }
    xml = tmp_path / "upstream.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "testlookup_reporter",
            str(ROOT / "e2e-runner/fixtures/producer.py"),
            "--junitxml=" + str(xml),
            "-q",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 1, (
        "Producer must include one ordinary assertion failure."
    )
    leaves = ET.parse(xml).findall(".//testcase")
    assert any(c.find("failure") is not None for c in leaves)
    deadline = time.monotonic() + 90
    while True:
        response = client.get("runs", params={"project_id": owned})
        assert response.status_code == 200
        matches = [r for r in response.json()["items"] if r["build_number"] == name]
        if matches and matches[0]["total_tests"] == len(leaves):
            break
        assert time.monotonic() < deadline, (
            "SDK report never materialized the complete fixture"
        )
        time.sleep(0.5)
    assert len(matches) == 1
    assert matches[0]["failed_tests"] == sum(
        c.find("failure") is not None for c in leaves
    )
    assert matches[0]["skipped_tests"] == sum(
        c.find("skipped") is not None for c in leaves
    )
    run = matches[0]
    assert run["project_id"] == owned
    persisted = client.get(f"runs/{run['id']}/tests")
    assert persisted.status_code == 200
    observed = persisted.json()
    assert observed["total"] == len(leaves)
    for leaf in leaves:
        expected = (
            "FAILED"
            if leaf.find("failure") is not None
            else "SKIPPED"
            if leaf.find("skipped") is not None
            else "PASSED"
        )
        rows = [
            row
            for row in observed["items"]
            if row["test_name"].split("::")[-1] == leaf.attrib["name"]
        ]
        assert len(rows) == 1
        assert (rows[0]["test_run_id"], rows[0]["status"].upper()) == (
            run["id"],
            expected,
        )


def walk_suites(suites):
    for suite in suites:
        yield suite
        yield from walk_suites(suite.get("suites", []))


def input_counts(kind, raw):
    data = json.loads(raw) if kind != "robot" else None
    if kind == "robot":
        vocabulary = {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped"}
        states = [
            vocabulary[node.attrib["status"]]
            for node in ET.fromstring(raw).findall(".//test/status")
        ]
    elif kind == "pytest":
        states = [case["outcome"] for case in data["tests"]]
    elif kind == "allure":
        states = [entry["status"] for entry in data]
    elif kind == "playwright":
        states = [
            test["results"][-1]["status"]
            for suite in walk_suites(data["suites"])
            for spec in suite["specs"]
            for test in spec["tests"]
        ]
    elif kind == "cypress":
        states = [
            "skipped" if test.get("pending") or test.get("skipped") else test["state"]
            for suite in walk_suites(data["results"])
            for test in suite.get("tests", [])
        ]
    else:
        raise ValueError(kind)
    assert states and "failed" in states
    counts = Counter(states)
    assert set(counts) <= {"passed", "failed", "broken", "skipped"}
    return counts


@pytest.mark.parametrize("kind", ["allure", "cypress", "playwright", "pytest", "robot"])
def test_file_counts(project, kind):
    client, owned, name = project
    produced = {"pytest": "pytest-report.json", "robot": "robot.xml"}
    path = (
        ROOT / "e2e-runner/fixtures" / produced[kind]
        if kind in produced
        else ROOT / "samples" / kind / (kind + "-results.json")
    )
    raw = path.read_bytes()
    counts = input_counts(kind, raw)
    upload = client.post(
        "ingest/file",
        data={
            "project_id": owned,
            "build_number": name,
            "run_ai": "false",
            "format": kind,
        },
        files={
            "file": (
                path.name,
                raw,
                "application/xml" if kind == "robot" else "application/json",
            )
        },
    )
    assert upload.status_code == 202, (
        f"{kind} upload: {upload.status_code}, request {upload.headers.get('x-request-id')}"
    )
    receipt = upload.json()
    deadline = time.monotonic() + 90
    while True:
        response = client.get("ingest/uploads/" + receipt["task_id"])
        assert response.status_code == 200
        state = response.json()["state"]
        if state == "succeeded":
            break
        assert state != "failed" and time.monotonic() < deadline, (
            f"{kind} receipt ended in {state}"
        )
        time.sleep(0.5)
    run = client.get("runs/" + receipt["run_id"])
    assert run.status_code == 200
    result = run.json()
    assert result["project_id"] == owned
    assert result["total_tests"] == sum(counts.values())
    for status in ("passed", "failed", "skipped", "broken"):
        assert result[status + "_tests"] == counts[status], (
            f"{kind} {status} disagrees with source leaves"
        )
