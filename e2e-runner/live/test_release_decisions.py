"""Release decisions checked through public APIs and the installed client entry points."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.live, pytest.mark.ephemeral]
NAMES = [
    "payment_declined",
    "cart_empty",
    "cart_full",
    "valid_address",
    "invalid_address",
]
CLASS = "Checkout"
SUITE = "release-decisions"


def response(client, method, path, code=200, **kwargs):
    result = client.request(method, path, **kwargs)
    assert result.status_code == code, (
        f"{method} {path}: HTTP {result.status_code} (expected {code}), "
        f"request {result.headers.get('x-request-id')}"
    )
    return result.json() if result.content else None


def create_release(client, project_id, name, **kwargs):
    return response(
        client,
        "POST",
        "releases",
        201,
        json={"project_id": project_id, "name": name, **kwargs},
    )


def upload(client, project_id, build, states, *, case_names=NAMES, **fields):
    suite = ET.Element("testsuite", name=SUITE, tests=str(len(states)))
    for name, state in zip(case_names, states, strict=True):
        item = ET.SubElement(suite, "testcase", name=name, classname=CLASS, time="0.01")
        if state == "FAILED":
            ET.SubElement(
                item, "failure", message=f"{name}: expected decline"
            ).text = "Synthetic assertion mismatch"
    payload = {
        "project_id": project_id,
        "build_number": build,
        "format": "junit",
        "run_ai": "false",
        **fields,
    }
    receipt = response(
        client,
        "POST",
        "ingest/file",
        202,
        data=payload,
        files={"file": ("checkout.xml", ET.tostring(suite), "application/xml")},
    )
    deadline = time.monotonic() + 90
    while True:
        task = response(client, "GET", "ingest/uploads/" + receipt["task_id"])
        if task["state"] == "succeeded":
            break
        assert task["state"] != "failed" and time.monotonic() < deadline, (
            f"Ingestion {task['state']}"
        )
        time.sleep(0.4)
    run_id = receipt["run_id"]
    run = response(client, "GET", f"runs/{run_id}")
    assert (run["project_id"], run["build_number"], run["total_tests"]) == (
        project_id,
        build,
        len(states),
    )
    cases = response(client, "GET", f"runs/{run_id}/tests")["items"]
    assert {c["test_name"]: c["status"] for c in cases} == dict(
        zip(case_names, states, strict=True)
    )
    return run_id, {c["test_name"]: c for c in cases}


def evaluate(client, release, verdict, *, record=True):
    decision = response(
        client,
        "POST",
        f"releases/{release['id']}/gate/evaluate",
        params={"record": str(record).lower()},
    )
    assert decision["release_id"] == release["id"]
    assert decision["verdict"] == verdict, decision["blocking_reasons"]
    assert decision["recorded"] is record
    if record:
        assert decision["decision_id"]
        stored = response(client, "GET", f"releases/{release['id']}/gate")
        assert stored["verdict"] == verdict
        for field in ("denominator", "evidence_count", "status_counts", "run_count"):
            assert decision["scorecard"][field] == stored["scorecard"][field]
    return decision


def fingerprint(case_name):
    return hashlib.sha256(f"{CLASS}::{case_name}".encode()).hexdigest()[:16]


def test_release_attribution_gate_and_hold(project, role_client):
    lead, owned, name = project
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=3)
    previous = create_release(
        lead,
        owned,
        name + "-old",
        cutoff_start_at=start.isoformat(),
        cutoff_end_at=(start + timedelta(days=1)).isoformat(),
    )
    current = create_release(lead, owned, name + "-new")
    releases_url = f"projects/{owned}/attribution-rules"
    response(
        lead,
        "POST",
        releases_url,
        201,
        json={
            "name": name,
            "match_field": "branch",
            "match_pattern": "candidate/*",
            "target_release_name": current["name"],
        },
    )
    empty = evaluate(lead, current, "NOT_EVALUATED")
    assert empty["scorecard"]["run_count"] == empty["scorecard"]["evidence_count"] == 0
    assert not empty["scorecard"]["measured"] and empty["blocking_reasons"]
    passed = ["PASSED"] * len(NAMES)
    late_id, _ = upload(
        lead,
        owned,
        name + "-late",
        passed,
        executed_at=(start + timedelta(hours=1)).isoformat(),
        branch="archive",
    )
    before = evaluate(lead, previous, "GO")
    assert before["scorecard"]["run_count"] == 1
    assert before["scorecard"]["evidence_count"] == len(NAMES)
    assert late_id in {
        r["id"]
        for r in response(lead, "GET", "releases/" + previous["id"])["linked_runs"]
    }
    failing_id, failures = upload(
        lead, owned, name + "-rc1", ["FAILED", *passed[1:]], branch="candidate/one"
    )
    declined = evaluate(lead, current, "NO_GO")
    assert declined["scorecard"]["run_count"] == 1
    fp = fingerprint(NAMES[0])
    assert failures[NAMES[0]]["test_name"] == NAMES[0]
    assert any(fp in reason for reason in declined["blocking_reasons"])
    repaired, _ = upload(
        lead, owned, name + "-rc2", passed, release_name=current["name"]
    )
    recovered = evaluate(lead, current, "GO")
    assert recovered["scorecard"]["run_count"] == 2
    assert recovered["scorecard"]["denominator"] == len(NAMES)
    assert recovered["scorecard"]["status_counts"]["PASSED"] == len(NAMES)
    assert {
        r["id"]
        for r in response(lead, "GET", "releases/" + current["id"])["linked_runs"]
    } == {failing_id, repaired}

    admin, _ = role_client("ADMIN")
    policy_url = "release-gate-policies"
    rules = {
        "thresholds": {"go_threshold": 18, "no_go_threshold": 60},
        "pass_rate_bands": {"orange_min": 70, "yellow_min": 90, "green_min": 95},
    }
    draft = response(
        admin,
        "POST",
        policy_url,
        201,
        json={"project_id": owned, "name": name, "rules": rules},
    )
    publish = f"{policy_url}/{draft['id']}/publish"
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: admin.post(publish), range(2)))
    assert sorted(r.status_code for r in attempts) == [200, 409]
    version = response(admin, "GET", policy_url + "/effective/" + owned)
    assert version["id"] == draft["id"] and version["version"] == draft["version"]
    assert version["rules"]["thresholds"]["no_go_threshold"] == 60
    history = response(admin, "GET", policy_url + "/history/" + owned)
    assert [row["id"] for row in history if row["is_active"]] == [draft["id"]]
    red, _ = upload(
        lead,
        owned,
        name + "-rc3",
        ["FAILED"] * 4 + ["PASSED"],
        release_name=current["name"],
    )
    for rid, rate, expected in [
        (repaired, 100, "GO"),
        (failing_id, 80, "CONDITIONAL_GO"),
        (red, 20, "NO_GO"),
    ]:
        council = response(lead, "GET", "release-readiness/" + rid)
        assert (council["run_id"], council["pass_rate"], council["recommendation"]) == (
            rid,
            rate,
            expected,
        )
        assert council["band_policy_id"] == draft["id"]
        assert council["band_policy_version"] == draft["version"]
    hold_url = f"projects/{owned}/retention-holds"
    hold = response(
        lead,
        "POST",
        hold_url,
        201,
        json={
            "entity_type": "release",
            "entity_id": current["id"],
            "reason": name + " freeze gate evidence",
        },
    )
    try:
        rejection = response(
            admin, "DELETE", f"releases/{current['id']}/test-runs/{repaired}", 409
        )
        assert name in str(rejection["detail"])
        evidence = response(lead, "GET", "releases/" + current["id"])
        assert repaired in {r["id"] for r in evidence["linked_runs"]}
    finally:
        response(
            admin,
            "POST",
            f"{hold_url}/{hold['id']}/release",
            json={"reason": "Completed release rehearsal"},
        )


def test_flaky_quarantine_lifecycle(project):
    lead, owned, name = project
    release = create_release(lead, owned, name + "-release")
    policy_url = f"projects/{owned}/quarantine/policy"
    configured = response(
        lead,
        "PUT",
        policy_url,
        json={
            "detection_min_runs": 6,
            "detection_flip_rate_threshold": 0.5,
            "sla_days": 7,
        },
    )
    assert configured["project_id"] == owned
    assert configured["is_default"] is False
    persisted = response(lead, "GET", policy_url)
    assert (
        persisted["detection_min_runs"],
        persisted["detection_flip_rate_threshold"],
    ) == (6, 0.5)
    series = ["PASSED", "FAILED", "PASSED", "FAILED", "PASSED", "FAILED"]
    history = []
    for i, outcome in enumerate(series):
        run_id, rows = upload(
            lead,
            owned,
            f"{name}-{i}",
            [outcome, *["PASSED"] * 4],
            release_name=release["name"],
        )
        history.append((run_id, rows[NAMES[0]]["id"]))
    fp = fingerprint(NAMES[0])
    leaderboard = response(
        lead, "GET", "analytics/flaky-tests", params={"project_id": owned}
    )["items"]
    assert [row["test_fingerprint"] for row in leaderboard] == [fp]
    score = leaderboard[0]
    assert score["source"] == "auto"
    assert score["total_runs"] == 6 and score["fail_count"] == score["pass_count"] == 3
    assert float(score["failure_rate_pct"]) == 50.0
    last_run, last_case = history[-1]
    measured = response(lead, "GET", f"runs/{last_run}/tests/{last_case}/history")[
        "flakiness"
    ]
    assert measured["is_flaky"] and measured["failure_rate"] == 0.5
    assert measured["impact_score"] > 0 and (
        measured["passed"],
        measured["failed"],
    ) == (3, 3)
    original = evaluate(lead, release, "NO_GO")
    assert any(fp in reason for reason in original["blocking_reasons"])
    proposal = response(
        lead,
        "POST",
        "quarantine",
        201,
        json={
            "project_id": owned,
            "test_name": NAMES[0],
            "suite_name": score["suite_name"],
            "test_fingerprint": fp,
            "detection_method": "manual",
            "flip_rate": measured["failure_rate"],
            "flip_window_size": 6,
            "pass_count": 3,
            "fail_count": 3,
        },
    )
    lifecycle = "quarantine/" + proposal["id"]
    assert proposal["status"] == "PROPOSED"
    assert proposal["flip_rate"] == persisted["detection_flip_rate_threshold"]
    invalid = response(
        lead, "POST", lifecycle + "/approve", 422, json={"quarantine_duration_days": 0}
    )
    assert any(
        err["loc"][-1] == "quarantine_duration_days" for err in invalid["detail"]
    )
    assert response(lead, "GET", lifecycle)["status"] == "PROPOSED"
    approved = response(
        lead,
        "POST",
        lifecycle + "/approve",
        json={
            "notes": "Measured five consecutive flips",
            "quarantine_duration_days": 1,
        },
    )
    assert approved["status"] == "QUARANTINED"
    expires = datetime.fromisoformat(approved["quarantine_expires_at"])
    assert expires - datetime.fromisoformat(approved["quarantine_start"]) == timedelta(
        days=1
    )
    manifest_url = f"projects/{owned}/quarantine/manifest"
    try:
        manifest = response(lead, "GET", manifest_url)
        assert [e["fingerprint"] for e in manifest["entries"]] == [fp]
        assert datetime.fromisoformat(manifest["entries"][0]["expires_at"]) == expires
        run, cases = upload(
            lead,
            owned,
            name + "-held",
            ["FAILED", *["PASSED"] * 4],
            release_name=release["name"],
        )
        for case in cases.values():
            stored = response(lead, "GET", f"runs/{run}/tests/{case['id']}")
            assert ("quarantined" in (stored["tags"] or [])) == (
                case["test_name"] == NAMES[0]
            )
        allowed = evaluate(lead, release, "GO")
        assert allowed["scorecard"]["status_counts"]["FAILED"] == 1
        assert allowed["scorecard"]["quarantined_failures"] == [
            f"{cases[NAMES[0]]['suite_name']}::{fp}"
        ]
        _, with_control = upload(
            lead,
            owned,
            name + "-control",
            ["FAILED", "FAILED", *["PASSED"] * 3],
            release_name=release["name"],
        )
        blocked = evaluate(lead, release, "NO_GO")
        assert len(blocked["blocking_reasons"]) == 1
        assert (
            fingerprint(with_control[NAMES[1]]["test_name"])
            in blocked["blocking_reasons"][0]
        )
    finally:
        released = response(
            lead,
            "POST",
            lifecycle + "/release",
            json={"notes": "Completed quarantine rehearsal"},
        )
        assert released["status"] == "RELEASED"
    assert response(lead, "GET", manifest_url)["count"] == 0
    upload(
        lead,
        owned,
        name + "-released",
        ["FAILED", *["PASSED"] * 4],
        release_name=release["name"],
    )
    resumed = evaluate(lead, release, "NO_GO")
    assert resumed["scorecard"]["quarantined_failures"] == []
    assert any(fp in reason for reason in resumed["blocking_reasons"])
    for (rid, cid), expected in zip(history, series, strict=True):
        row = response(lead, "GET", f"runs/{rid}/tests/{cid}")
        assert row["status"] == expected and row["test_name"] == NAMES[0]


async def mcp_failure(client, owned, rid, release, failure, evidence_count):
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(
        os.environ["TL_MCP"] + "/sse",
        headers={"Authorization": client.headers["Authorization"]},
    ) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            details = await session.call_tool("get_run_details", {"run_id": rid})
            assert (
                not details.is_error
                and rid in details.content[0].text
                and owned in details.content[0].text
            )
            rows = await session.call_tool(
                "list_test_cases", {"run_id": rid, "status": "FAILED"}
            )
            assert (
                not rows.is_error
                and failure["id"] in rows.content[0].text
                and failure["test_name"] in rows.content[0].text
            )
            gate = await session.call_tool(
                "get_release_gate", {"release": release["id"], "project": owned}
            )
            assert not gate.is_error
            data = json.loads(gate.content[0].text)
            assert (
                data["ok"]
                and data["measured"]
                and data["release_id"] == release["id"]
                and data["verdict"] == "NO_GO"
            )
            assert data["evidence_count"] == evidence_count
            assert any(failure["test_name"] in blocker for blocker in data["blockers"])


def test_failure_spine(project, tmp_path, role_client):
    lead, owned, name = project
    release = create_release(lead, owned, name + "-release")
    baseline_names = [*NAMES, "retired_coupon"]
    upload(
        lead,
        owned,
        name + "-baseline",
        ["PASSED"] * len(baseline_names),
        case_names=baseline_names,
        release_name=release["name"],
    )
    build = name + "-current"
    rid, cases = upload(
        lead, owned, build, ["FAILED", *["PASSED"] * 4], release_name=release["name"]
    )
    failure = cases[NAMES[0]]
    decision = evaluate(lead, release, "NO_GO")
    assert (
        decision["scorecard"]["evidence_count"] == len(baseline_names)
        and decision["scorecard"]["run_count"] == 2
    )
    assert any(
        fingerprint(failure["test_name"]) in reason
        for reason in decision["blocking_reasons"]
    )
    for mode, denominator in (("window", len(baseline_names)), ("latest", len(NAMES))):
        summary = response(
            lead, "GET", "reports/summary", params={"project_id": owned, "mode": mode}
        )
        assert summary["project_id"] == owned
        assert summary["mode"] == mode
        assert summary["totals"]["failed"] == 1
        assert summary["totals"]["passed"] == denominator - 1
        assert (
            summary["totals"]["evaluated"]
            == summary["totals"]["total_test_cases"]
            == denominator
        )
        assert summary["totals"]["pass_rate_pct"] == pytest.approx(
            100 * (denominator - 1) / denominator, abs=0.05
        )
        assert NAMES[0] in {f["test_name"] for f in summary["top_failing_tests"]}
    without_project = response(lead, "GET", "reports/summary")
    assert without_project["meta"]["measured"] is False
    assert "select a project" in without_project["meta"]["reason"].lower()

    admin, _ = role_client("ADMIN")
    key = response(
        admin,
        "POST",
        "keys",
        201,
        json={
            "name": name,
            "project_id": owned,
            "target_user_id": response(lead, "GET", "auth/me")["id"],
            "expires_days": 1,
            "scopes": ["stream:write"],
        },
    )
    try:
        env = {
            **os.environ,
            "TESTLOOKUP_URL": os.environ["TL_API"],
            "TESTLOOKUP_API_KEY": key["raw_key"],
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(ROOT / "cli"),
            "XDG_CONFIG_HOME": str(tmp_path),
        }
        cmd = subprocess.run(
            [
                sys.executable,
                "-m",
                "testlookup_cli.app",
                "ci-verdict",
                "--run",
                rid,
                "--profile",
                name,
                "--output",
                "json",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            timeout=120,
        )
        assert cmd.returncode == 1, (
            f"CLI must report a real failure, exit {cmd.returncode}"
        )
        result = json.loads(cmd.stdout)
        assert result["counts"] == {
            "real_failures": 1,
            "quarantined_failures": 0,
            "total_failures": 1,
        }
        assert result["real_failures"][0]["id"] == failure["id"]
    finally:
        response(admin, "DELETE", "keys/" + key["id"], 204)
    payload = {
        "project": response(lead, "GET", "projects/" + owned),
        "user": response(lead, "GET", "auth/me"),
        "token": lead.headers["Authorization"].removeprefix("Bearer "),
        "run": rid,
        "release": release["id"],
        "build": build,
        "failure": NAMES[0],
    }
    browser = subprocess.run(
        ["node", "tests/release/spine-ui.mjs"],
        cwd=ROOT / "frontend",
        input=json.dumps(payload),
        encoding="utf8",
        capture_output=True,
        timeout=240,
    )
    assert browser.returncode == 0, "Browser spine: " + browser.stdout[-500:]
    asyncio.run(
        asyncio.wait_for(
            mcp_failure(lead, owned, rid, release, failure, len(baseline_names)),
            timeout=75,
        )
    )
