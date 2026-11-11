"""Live journeys with synthetic evidence and namespace-local contract fixtures."""

import hashlib
import html
import io
import json
import os
import time
import xml.etree.ElementTree as ET
import zipfile

import httpx
import pytest

pytestmark = [pytest.mark.live, pytest.mark.ephemeral]


def data(response, *codes):
    assert response.status_code in (codes or (200,)), (
        f"{response.request.method} {response.request.url.path}: {response.status_code}; "
        f"request {response.headers.get('x-request-id')}"
    )
    return response.json()


def until(read, accept, what, seconds=90):
    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if accept(value):
            return value
        assert time.monotonic() < deadline, f"Timed out waiting for {what}"
        time.sleep(1)


def account(project, role_client, role):
    _, pid, _ = project
    client, who = role_client(role)
    admin, _ = role_client("ADMIN")
    route = f"projects/{pid}/members"
    if who["id"] not in {row["user_id"] for row in data(admin.get(route))}:
        data(admin.post(route, json={"user_id": who["id"], "role": role}), 201)
    return client, who


def upload(project, label, suffix="one"):
    client, pid, _ = project
    suite = ET.Element("testsuite", name=label, tests="3", failures="2")
    for leaf in ("failure_a", "failure_b", "passing"):
        node = ET.SubElement(
            suite, "testcase", classname=label, name=label + "_" + leaf, time="0.1"
        )
        if leaf.startswith("failure"):
            ET.SubElement(
                node,
                "failure",
                type="AssertionError",
                message="Synthetic order assertion",
            ).text = "expected 3, observed 2"
    receipt = data(
        client.post(
            "ingest/file",
            data={
                "project_id": pid,
                "build_number": label + "-" + suffix,
                "format": "junit",
                "run_ai": "false",
            },
            files={"file": ("fixture.xml", ET.tostring(suite), "application/xml")},
        ),
        202,
    )

    def receipt_state():
        state = data(client.get("ingest/uploads/" + receipt["task_id"]))["state"]
        assert state != "failed", "The owned fixture failed ingestion"
        return state

    until(receipt_state, lambda state: state == "succeeded", "upload receipt")
    run = receipt["run_id"]
    rows = data(client.get(f"runs/{run}/tests"))["items"]
    assert {row["test_name"] for row in rows} == {
        label + "_" + key for key in ("failure_a", "failure_b", "passing")
    }
    return run, {row["test_name"]: row for row in rows}


def fixture_label(project):
    return "journey_" + project[2].removeprefix("e2e-")[:14]


@pytest.fixture
def receiver():
    origin = os.environ.get("TL_FAKE_URL")
    if not origin:
        pytest.fail("BLOCKED: no isolated contract receiver configured", pytrace=False)
    with httpx.Client(base_url=origin, timeout=20) as client:
        data(client.get("/api/tags"))
        yield client


def test_defect_triage_recurrence(project, role_client, receiver):
    client, who = account(project, role_client, "QA_ENGINEER")
    _, pid, _ = project
    label = fixture_label(project)
    name = label + "_failure_a"
    initial_run, rows = upload(project, label)
    fingerprint = hashlib.sha256(
        (rows[name]["class_name"] + "::" + name).encode()
    ).hexdigest()[:16]
    route = f"projects/{pid}/defects/jira"
    before = data(client.get(route + "/preview", params={"fingerprint": fingerprint}))
    assert before["test_name"] == name and before["latest_run_id"] == initial_run
    assert before["occurrences"]["failing_runs"] == 1
    filing = {"fingerprint": fingerprint, "jira_project_key": "QA", "issue_type": "Bug"}
    # Jira creates the issue but its response times out. Repeating the
    # product operation must reconcile the existing external issue.
    data(
        receiver.post(
            "/_fake/faults",
            json={
                "method": "POST",
                "path": "/rest/api/3/issue",
                "after": True,
                "status": 504,
                "times": 1,
            },
        )
    )
    uncertain = data(client.post(route, json=filing), 409)
    assert uncertain["detail"]["code"] == "jira_outcome_unknown"
    filed = data(client.post(route, json=filing))
    assert (
        filed["target"] == "jira"
        and filed["deduplicated"]
        and filed["recurrence_count"] == 0
    )
    defect, jira_key = filed["defect_id"], filed["jira_key"]
    assert jira_key.startswith("QA-")
    assigned = data(
        client.patch(
            f"projects/{pid}/defects/{defect}",
            json={
                "assignee_user_id": who["id"],
                "resolution_status": "IN_PROGRESS",
                "note": "Fixture triage",
            },
        )
    )
    assert assigned["defect"]["assignee_user_id"] == who["id"]
    second_run, _ = upload(project, label, "two")
    again = data(client.post(route, json=filing))
    assert again["deduplicated"] and again["jira_key"] == jira_key
    assert again["defect_id"] == defect and again["recurrence_count"] == 1
    preview = data(client.get(route + "/preview", params={"fingerprint": fingerprint}))
    assert (
        preview["latest_run_id"] == second_run
        and preview["occurrences"]["failing_runs"] == 2
    )
    assert preview["existing_defect"]["jira_key"] == jira_key
    assert preview["signature"] == before["signature"] == fingerprint
    surviving = data(client.get("analytics/defects", params={"project_id": pid}))[
        "items"
    ]
    assert [
        (
            row["id"],
            row["jira_ticket_id"],
            row["assignee_user_id"],
            row["resolution_status"],
        )
        for row in surviving
    ] == [(defect, jira_key, who["id"], "IN_PROGRESS")]
    issues = data(receiver.get("/_fake/jira/issues"))
    matches = [issue for issue in issues if name in issue["fields"]["summary"]]
    assert [issue["key"] for issue in matches] == [jira_key]


def test_search_saved_view_chat(project, role_client, receiver):
    client, _ = account(project, role_client, "TESTER")
    _, pid, _ = project
    label = fixture_label(project)
    run, rows = upload(project, label)
    expected = {r["id"] for r in rows.values() if r["status"].lower() == "failed"}
    query = label + " status:failed"
    filters = {"kind": "search", "q": query, "scope": "tests", "mode": "keyword"}

    def search(q):
        return data(
            client.get(
                "search", params={"project_id": pid, "q": q, "search_type": "keyword"}
            )
        )

    first = search(query)
    assert (
        first["total"] == 2 and {r["test_case_id"] for r in first["items"]} == expected
    )
    fallback = data(
        client.get(
            "search", params={"project_id": pid, "q": query, "search_type": "semantic"}
        )
    )
    assert (
        fallback["requested_search_type"],
        fallback["search_type"],
        fallback["fallback_reason"],
    ) == ("semantic", "keyword", "semantic_unavailable")
    assert {r["test_case_id"] for r in fallback["items"]} == expected
    view = data(
        client.post(
            "saved-views",
            json={
                "project_id": pid,
                "name": label,
                "page": "search",
                "filters": filters,
            },
        ),
        201,
    )
    try:
        saved = data(client.get("saved-views/" + view["id"]))
        assert saved["filters"] == filters and saved["project_id"] == pid
        assert {
            r["test_case_id"] for r in search(saved["filters"]["q"])["items"]
        } == expected
        assert search(label + "noresults")["total"] == 0
        session = data(
            client.post("chat/sessions", json={"project_id": pid, "title": label}), 201
        )
        try:
            endpoint = f"chat/sessions/{session['id']}/messages"
            answer = data(
                client.post(
                    endpoint,
                    json={
                        "message": "Which tests failed in the latest build? Name them from the recorded evidence."
                    },
                    timeout=90,
                )
            )
            sources = {
                source["id"]
                for source in answer["sources"]
                if source["type"] == "test_case"
            }
            assert sources == expected
            for leaf in ("failure_a", "failure_b"):
                assert label + "_" + leaf in answer["reply"]
            persisted = data(client.get(endpoint))
            assert [m["content"] for m in persisted if m["role"] == "assistant"] == [
                answer["reply"]
            ]
        finally:
            assert client.delete("chat/sessions/" + session["id"]).status_code == 204
    finally:
        assert client.delete("saved-views/" + view["id"]).status_code == 204


def test_ai_review_pipeline(project, role_client, receiver):
    client, pid, _ = project
    label = fixture_label(project)
    reviews = []
    for suffix in ("accept", "reject"):
        run, _ = upload(project, label, suffix)
        request = data(
            client.post("agents/pipelines/trigger", json={"test_run_id": run}), 202
        )
        assert request["task_id"] and request["run_id"] == run

        def load_review():
            return [
                row
                for row in data(client.get(f"projects/{pid}/reviews"))
                if row["test_run_id"] == run and row["state"] == "pending_review"
            ]

        row = until(
            load_review,
            bool,
            "review created by the real analysis pipeline",
            seconds=180,
        )[0]
        assert row["pipeline_run_id"] and row["test_run_id"] == run
        action = (
            {"notes": "Synthetic review"}
            if suffix == "accept"
            else {
                "reason_code": "unsupported_claim",
                "notes": "Synthetic rejected claim",
            }
        )
        decision = data(client.post(f"reviews/{row['id']}/{suffix}", json=action))
        assert decision["state"] == ("accepted" if suffix == "accept" else "rejected")
        assert data(client.get("reviews/" + row["id"]))["state"] == decision["state"]
        reviews.append((run, row["id"], decision["state"]))
    for run, review_id, expected in reviews:
        report = data(client.get(f"agents/runs/{run}/summary"))
        assert report["review"]["state"] == expected
        assert report["review"]["review_id"] == review_id
        publication = client.get(
            f"reports/runs/{run}/pdf", params={"layout": "engineering"}
        )
        if expected == "accepted":
            assert publication.status_code == 200 and publication.content.startswith(
                b"%PDF-"
            )
            panel = report["executive_panel"]
            if panel:
                assert (
                    panel["metrics"]["total_tests"] == 3
                    and panel["metrics"]["failed"] == 2
                )
            # The downloadable report publishes the export-safe executive
            # summary; the interactive panel is a separate representation.
            accepted = report["executive_summary"]
            assert isinstance(accepted, str) and accepted.strip()
            link = data(
                client.post(
                    f"reports/runs/{run}/share",
                    json={"layout": "engineering", "expiry_days": 1},
                ),
                201,
            )
            try:
                with httpx.Client(base_url=client.base_url, timeout=30) as reader:
                    published = reader.get("shared/reports/" + link["token"])
                assert published.status_code == 200
                assert accepted in html.unescape(published.text)
                assert label + "-accept" in published.text
            finally:
                assert (
                    client.delete("reports/share-links/" + link["id"]).status_code
                    == 204
                )
        else:
            refusal = data(publication, 409)["detail"]
            assert (refusal["review_state"], refusal["review_id"]) == (
                "rejected",
                review_id,
            )
    run, _, _ = reviews[0]
    agent = "agent.summary.v1"
    invocation = data(
        client.post(
            f"agents/{agent}/invoke",
            json={
                "project_id": pid,
                "input": {"agent_id": agent, "payload": {"test_run_id": run}},
                "mode": "async",
            },
        ),
        202,
    )
    invocation_path = "agents/invocations/" + invocation["id"]
    data(client.post(invocation_path + "/cancel"), 202)
    terminal = until(
        lambda: data(client.get(invocation_path)),
        lambda item: item["status"] == "failed",
        "cancelled invocation",
    )
    assert terminal["test_run_id"] == run and "cancel" in terminal["error"].lower()
    refused = data(client.post(invocation_path + "/retry"), 409)["detail"]
    assert (
        refused["reason"] == "cancelled"
        and refused["invocation_id"] == invocation["id"]
    )
    history = data(
        client.get(
            f"projects/{pid}/activity",
            params={"event_type": "analysis.cancelled", "entity_id": run},
        )
    )
    assert any(
        row["context"]["invocation_id"] == invocation["id"] for row in history["items"]
    )
    admin, _ = role_client("ADMIN")
    status_url = "settings/ai/model-status"
    original = data(admin.get(status_url))
    assert original["ollama_reachable"] and not original["offline_mode"]
    try:
        data(receiver.post("/_release/model-probe-delay"))
        delayed = data(admin.get(status_url))
        assert not delayed["ollama_reachable"] and not delayed["offline_mode"]
    finally:
        data(receiver.delete("/_release/model-probe-delay"))
    try:
        data(admin.put("settings/ai", json={"ai_offline_mode": True}))
        offline = data(admin.get(status_url))
        assert offline["offline_mode"] and offline["ollama_reachable"]
        assert "release-fixture" in offline["installed_models"]
    finally:
        data(
            admin.put("settings/ai", json={"ai_offline_mode": original["offline_mode"]})
        )


def test_admin_delivery_retention(project, role_client, receiver):
    client, actor = account(project, role_client, "ADMIN")
    _, pid, project_name = project
    label = fixture_label(project)
    path = "/hooks/" + label
    subscription = {
        "name": label,
        "target_url": "http://release-fixture:8080" + path,
        "events": ["run.completed"],
        "max_retries": 0,
        "breaker_threshold": 2,
    }
    hook = data(
        client.post(
            "webhooks",
            params={"project_id": pid},
            json={**subscription, "secret": label},
        ),
        201,
    )
    endpoint = "webhooks/" + hook["id"]
    try:

        def received():
            return [
                row for row in data(receiver.get("/_fake/hooks")) if row["path"] == path
            ]

        # Contract responses apply only to this project's unique recipient.
        for status, times in ((200, 1), (503, 2), (200, 1)):
            data(
                receiver.post(
                    "/_fake/faults",
                    json={
                        "method": "POST",
                        "path": path,
                        "status": status,
                        "times": times,
                    },
                )
            )
        result = data(client.post(endpoint + "/test"))
        assert result["success"]
        until(received, bool, "receiver ping")
        rows = received()
        assert len(rows) == 1 and rows[0]["signed"] and rows[0]["status"] == 200
        assert rows[0]["delivery_id"]
        deliveries = data(client.get(endpoint + "/deliveries"))
        assert any(row["id"] == rows[0]["delivery_id"] for row in deliveries)
        for failures in (1, 2):
            assert data(client.post(endpoint + "/test"))["success"]
            until(
                lambda: data(client.get(endpoint)),
                lambda row: row["consecutive_failures"] == failures,
                "failed receiver delivery",
            )
        circuit = data(client.get(endpoint))
        assert not circuit["enabled"] and circuit["disabled_reason"] == "circuit_open"
        assert not data(client.post(endpoint + "/test"))["success"]
        assert [row["status"] for row in received()] == [200, 503, 503]
        data(client.patch(endpoint, json={**subscription, "enabled": True}))
        assert data(client.post(endpoint + "/test"))["success"]
        final = until(received, lambda rows: len(rows) == 4, "recovery delivery")
        assert [row["status"] for row in final] == [200, 503, 503, 200]
        assert len({row["delivery_id"] for row in final}) == 4
        deliveries_received = [
            row["body"]
            for row in data(receiver.get("/_fake/requests"))
            if row["path"] == path
        ]
        assert [event["delivery_id"] for event in deliveries_received] == [
            row["delivery_id"] for row in final
        ]
        for event in deliveries_received:
            assert (
                event["project_id"],
                event["subscription_id"],
                event["event_type"],
            ) == (pid, hook["id"], "run.completed")
            assert (
                event["data"]["is_test"]
                and event["data"]["triggered_by"] == actor["id"]
            )
        activity = data(
            client.get(
                f"projects/{pid}/activity",
                params={"event_type": "webhook.circuit_opened"},
            )
        )
        assert [row["entity"]["id"] for row in activity["items"]] == [hook["id"]]
    finally:
        assert client.delete(endpoint).status_code == 204
    run, _ = upload(project, label)
    release_id = data(client.get("runs/" + run))["release_id"]
    if release_id:
        # Ingest can attribute a run to the disposable project's default
        # release. Verify protection and remove only this owned run's link.
        release = data(client.get("releases/" + release_id))
        assert release["project_id"] == pid
        protected = data(
            client.post(f"projects/{pid}/deletion/preview", json={"run_ids": [run]})
        )
        assert protected["run_count"] == 0 and protected["blocked"][0]["run_id"] == run
        assert any(
            reason.startswith("linked to 1 release")
            for reason in protected["blocked"][0]["reasons"]
        )
        assert (
            client.delete(f"releases/{release_id}/test-runs/{run}").status_code == 204
        )
    preview = data(
        client.post(f"projects/{pid}/deletion/preview", json={"run_ids": [run]})
    )
    assert preview["run_ids"] == [run] and preview["run_count"] == 1, preview["blocked"]
    job = data(
        client.post(
            f"projects/{pid}/deletion/execute",
            json={
                "job_id": preview["job_id"],
                "confirmation_name": project_name,
                "export": {"tier": "summary", "target": "collect"},
            },
        ),
        202,
    )
    assert job["job_id"] == preview["job_id"]
    job_path = f"projects/{pid}/deletion/jobs/" + job["job_id"]

    def export_state():
        state = data(client.get(job_path))
        assert state["status"] not in {"failed", "export_failed"}, state["status"]
        return state

    archived = until(
        export_state,
        lambda row: bool(row["export_verified_at"]),
        "verified retention archive",
    )
    assert client.get("runs/" + run).status_code == 200
    archive = client.post(job_path + "/collect")
    assert (
        archive.status_code == 200
        and hashlib.sha256(archive.content).hexdigest()
        == archive.headers["X-Archive-Sha256"]
    )
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        signature = json.loads(zf.read("signature.json"))
        assert [item["id"] for item in json.loads(zf.read("runs.json"))] == [run]
    key = signature["key_id"]
    pem = client.get(f"compliance-packs/signing-keys/{key}/pem")
    assert pem.status_code == 200
    from testlookup_cli.pack_verify import verify_pack_bytes

    verified = verify_pack_bytes(
        archive.content,
        recorded_manifest_sha256=archived["export_manifest_sha256"],
        public_keys={key: pem.text},
    )
    assert (
        verified["signature"] == "valid"
        and verified["result"] == "verified"
        and verified["manifest_matches_record"] is True
        and verified["files_ok"] == verified["files_checked"] > 0
    )
    data(
        client.post(
            job_path + "/acknowledge",
            json={"manifest_sha256": archived["export_manifest_sha256"]},
        )
    )
    until(
        lambda: client.get("runs/" + run).status_code,
        lambda code: code == 404,
        "erasure of owned run",
    )
