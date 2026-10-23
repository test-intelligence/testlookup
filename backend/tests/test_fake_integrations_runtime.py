"""The W10 sections of the shared integration fake answer as the real services do (§2.0).

``tests/fakes/fake_integrations.py`` is a CONTRACT double: Splunk, OpenShift,
GitHub / GitHub Enterprise, GitLab and the outbound-webhook receiver each
answer the documented shape to an accepted call and refuse what the real
service refuses (a bad or missing credential, an unknown repo / sha /
namespace / pod / project, an empty search). Every W10 backend test drives the
real code against it through ``httpx.ASGITransport``, and the local demo and
the homelab mock pod run this same file, so its contract is pinned here.

The request log records which service a call was for, whether its credential
was right (``auth``) -- never the credential itself -- and, for GitHub, which
base (``dotcom`` / ``ghe``) it reached. The existing Jira and Slack behaviour is
pinned by ``tests/test_mock_integrations.py`` (unedited).
"""
from __future__ import annotations

import json

import httpx
import pytest

from tests.fakes.fake_integrations import FakeState, create_app, demo_sha, parse_spl

TOKENS = ("fake-splunk-token", "fake-ocp-token", "fake-gh-pat", "fake-gh-pat-2", "fake-gitlab-pat", "fake-jira-token")
PAYMENTS = "acme/payments"


@pytest.fixture
def fake():
    state = FakeState()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)), base_url="http://fake.test")
    return state, client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _spl_form(search: str, **extra) -> dict[str, str]:
    return {"search": search, "output_mode": "json", **extra}


# ── Splunk ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_splunk_server_info_checks_the_token(fake):
    state, client = fake
    ok = await client.get("/services/server/info", headers=_bearer("fake-splunk-token"))
    assert ok.status_code == 200
    assert ok.json() == {"entry": [{"content": {"serverName": "fake-splunk", "version": "9.2.0"}}]}
    for headers in (_bearer("wrong"), {}):
        refused = await client.get("/services/server/info", headers=headers)
        assert refused.status_code == 401
        assert refused.json() == {"messages": [{"type": "WARN", "text": "call not properly authenticated"}]}
    assert [r["auth"] for r in state.requests] == ["ok", "bad", "none"]
    assert {r["service"] for r in state.requests} == {"splunk"}


@pytest.mark.asyncio
async def test_a_oneshot_search_answers_the_index_events_for_the_service_and_level(fake):
    state, client = fake
    search = ('search index=payments service="checkout" (status>=500 OR level=ERROR OR "Exception") '
              'earliest="10/20/2026:09:55:00" latest="10/20/2026:10:05:00" | head 20 | table _time, level, message')
    resp = await client.post(
        "/services/search/jobs", headers=_bearer("fake-splunk-token"),
        data=_spl_form(search, exec_mode="oneshot"),
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 3
    assert {r["level"] for r in results} == {"ERROR"}
    assert any(r["message"] == "db pool exhausted after 30000 ms" for r in results)
    [recorded] = state.splunk_searches
    assert recorded == {
        "endpoint": "/services/search/jobs", "search": search, "index": "payments",
        "services": ["checkout"], "auth": "ok",
    }
    assert state.requests[-1]["form"]["exec_mode"] == "oneshot"


@pytest.mark.asyncio
async def test_the_service_and_level_terms_filter(fake):
    _, client = fake
    headers = _bearer("fake-splunk-token")

    async def count(search):
        resp = await client.post("/services/search/jobs", headers=headers, data=_spl_form(search, exec_mode="oneshot"))
        return len(resp.json()["results"])

    assert await count('search index=main service="checkout" level=ERROR') == 0       # main holds 2 WARN
    assert await count('search index=main service="checkout" level=WARN') == 2
    assert await count('search index=main (service="checkout" OR service="ledger")') == 2
    assert await count('search index=payments service="ledger"') == 0
    assert await count('search index=payments service="checkout" | head 1') == 1
    assert await count('search index=nowhere service="checkout"') == 0              # Splunk does not error


@pytest.mark.asyncio
async def test_export_answers_ndjson_and_stats_count(fake):
    _, client = fake
    headers = _bearer("fake-splunk-token")
    counted = await client.post(
        "/services/search/jobs/export", headers=headers,
        data=_spl_form('search index=payments service="checkout" level="ERROR" earliest="a" latest="b" | stats count',
                       count="1"),
    )
    assert counted.status_code == 200
    assert [json.loads(line) for line in counted.text.strip().split("\n")] == [{"result": {"count": "3"}}]
    listed = await client.post(
        "/services/search/jobs/export", headers=headers,
        data=_spl_form('search index=main (service="checkout") (ERROR OR WARN) | sort _time'),
    )
    lines = [json.loads(line) for line in listed.text.strip().split("\n")]
    assert [line["result"]["level"] for line in lines] == ["WARN", "WARN"]


@pytest.mark.asyncio
async def test_splunk_refuses_what_splunk_refuses(fake):
    state, client = fake
    headers = _bearer("fake-splunk-token")
    empty = await client.post("/services/search/jobs", headers=headers, data={"output_mode": "json", "exec_mode": "oneshot"})
    assert empty.status_code == 400
    assert empty.json() == {"messages": [{"type": "FATAL", "text": "Empty search."}]}
    blocking = await client.post("/services/search/jobs", headers=headers, data=_spl_form("search index=main"))
    assert blocking.status_code == 400 and "oneshot" in blocking.json()["messages"][0]["text"]
    bad = await client.post(
        "/services/search/jobs", headers=_bearer("nope"), data=_spl_form("search index=main", exec_mode="oneshot"),
    )
    assert bad.status_code == 401
    assert state.splunk_searches[-1]["auth"] == "bad"


@pytest.mark.asyncio
async def test_seeded_events_join_an_index(fake):
    state, client = fake
    seeded = await client.post("/_fake/splunk/events", json={"index": "main", "events": [
        {"_time": "2026-10-23T10:00:00Z", "level": "ERROR", "service": "Payments API", "message": "boom", "host": "h"},
    ]})
    assert seeded.json() == {"index": "main", "events": 3}
    resp = await client.post(
        "/services/search/jobs", headers=_bearer("fake-splunk-token"),
        data=_spl_form('search index=main service="Payments API" level=ERROR', exec_mode="oneshot"),
    )
    assert [r["message"] for r in resp.json()["results"]] == ["boom"]
    listed = await client.get("/_fake/splunk/searches")
    assert listed.json()[-1]["services"] == ["Payments API"]


def test_parse_spl():
    assert parse_spl('search index=payments (service="a" OR service=b) level="error" | head 5') == {
        "index": "payments", "services": ["a", "b"], "levels": ["ERROR"], "head": 5, "count": False,
    }
    assert parse_spl("index=x | stats count")["count"] is True


# ── OpenShift / Kubernetes ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openshift_namespace_pod_and_events(fake):
    state, client = fake
    headers = _bearer("fake-ocp-token")
    ns = await client.get("/api/v1/namespaces/qa-payments", headers=headers)
    assert ns.status_code == 200 and ns.json()["metadata"]["name"] == "qa-payments"
    pod = await client.get("/api/v1/namespaces/qa-payments/pods/payment-runner-7d9f", headers=headers)
    body = pod.json()
    assert body["metadata"] == {"name": "payment-runner-7d9f", "namespace": "qa-payments"}
    assert body["status"]["phase"] == "Running" and body["status"]["hostIP"]
    assert body["spec"]["nodeName"] == "worker-2"
    assert body["spec"]["containers"][0]["resources"]["limits"] == {"cpu": "500m", "memory": "512Mi"}
    events = await client.get(
        "/api/v1/namespaces/qa-payments/events?fieldSelector=involvedObject.name=payment-runner-7d9f",
        headers=headers,
    )
    items = events.json()["items"]
    assert [(e["reason"], e["count"]) for e in items] == [("OOMKilled", 2), ("BackOff", 1)]
    other = await client.get(
        "/api/v1/namespaces/qa-payments/events?fieldSelector=involvedObject.name=someone-else", headers=headers,
    )
    assert other.json()["items"] == []
    assert {r["service"] for r in state.requests} == {"ocp"}


@pytest.mark.asyncio
async def test_openshift_refuses_what_kubernetes_refuses(fake):
    _, client = fake
    headers = _bearer("fake-ocp-token")
    missing_ns = await client.get("/api/v1/namespaces/nowhere", headers=headers)
    assert missing_ns.status_code == 404
    assert missing_ns.json()["kind"] == "Status" and missing_ns.json()["reason"] == "NotFound"
    missing_pod = await client.get("/api/v1/namespaces/qa-testing/pods/ghost", headers=headers)
    assert missing_pod.status_code == 404 and missing_pod.json()["code"] == 404
    for bad in (_bearer("wrong"), {}):
        refused = await client.get("/api/v1/namespaces/qa-payments/pods/payment-runner-7d9f", headers=bad)
        assert refused.status_code == 401
        assert refused.json()["reason"] == "Unauthorized" and refused.json()["code"] == 401


@pytest.mark.asyncio
async def test_a_seeded_pod_is_served(fake):
    _, client = fake
    await client.post("/_fake/ocp/pods", json={
        "namespace": "qa-new", "pod": "runner-1", "phase": "Failed",
        "events": [{"reason": "Evicted", "message": "low on memory", "type": "Warning", "count": 1}],
    })
    headers = _bearer("fake-ocp-token")
    assert (await client.get("/api/v1/namespaces/qa-new", headers=headers)).status_code == 200
    pod = await client.get("/api/v1/namespaces/qa-new/pods/runner-1", headers=headers)
    assert pod.json()["status"]["phase"] == "Failed"


# ── GitHub / GitHub Enterprise ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix, base", [("", "dotcom"), ("/api/v3", "ghe")])
async def test_github_answers_on_both_bases(fake, prefix, base):
    state, client = fake
    headers = {"Authorization": "Bearer fake-gh-pat"}
    rate = await client.get(f"{prefix}/rate_limit", headers={"Authorization": "token fake-gh-pat"})
    assert rate.json() == {"rate": {"limit": 5000, "remaining": 4999, "used": 1}}
    repo = await client.get(f"{prefix}/repos/{PAYMENTS}", headers=headers)
    assert repo.status_code == 200
    assert repo.json()["full_name"] == PAYMENTS
    assert repo.json()["html_url"] == f"https://github.fake.test/{PAYMENTS}"
    assert repo.json()["private"] is True
    assert [(r["service"], r["base"], r["auth"]) for r in state.requests] == [("github", base, "ok")] * 2


@pytest.mark.asyncio
async def test_github_compare_lists_the_commits_after_base_up_to_head(fake):
    state, client = fake
    headers = {"Authorization": "Bearer fake-gh-pat"}
    commits = state.github_commits[PAYMENTS]
    assert len(commits) == 4 and commits[0]["sha"] == demo_sha(PAYMENTS, 0)
    base, head = commits[0]["sha"], commits[3]["sha"]
    resp = await client.get(f"/api/v3/repos/{PAYMENTS}/compare/{base}...{head}", headers=headers)
    assert resp.status_code == 200
    listed = resp.json()["commits"]
    assert [c["sha"] for c in listed] == [c["sha"] for c in commits[1:]]          # oldest -> newest
    assert listed[0]["commit"]["author"] == {"name": "Ben Dev", "date": commits[1]["date"]}
    assert listed[0]["commit"]["message"] == commits[1]["message"]
    short = await client.get(f"/repos/{PAYMENTS}/compare/{base[:7]}...{head[:12]}", headers=headers)
    assert len(short.json()["commits"]) == 3
    unknown = await client.get(f"/repos/{PAYMENTS}/compare/{base}...{'f' * 40}", headers=headers)
    assert unknown.status_code == 404 and unknown.json()["message"] == "Not Found"


@pytest.mark.asyncio
async def test_github_commit_detail_and_window(fake):
    state, client = fake
    headers = {"Authorization": "Bearer fake-gh-pat"}
    commits = state.github_commits[PAYMENTS]
    detail = await client.get(f"/repos/{PAYMENTS}/commits/{commits[2]['sha']}", headers=headers)
    assert detail.json()["sha"] == commits[2]["sha"]
    assert detail.json()["files"] == [{"filename": "payments/db.py"}, {"filename": "config/pool.yaml"}]
    assert (await client.get(f"/repos/{PAYMENTS}/commits/{'0' * 40}", headers=headers)).status_code == 404
    window = await client.get(
        f"/api/v3/repos/{PAYMENTS}/commits",
        params={"since": commits[1]["date"], "until": commits[2]["date"], "per_page": "20"},
        headers=headers,
    )
    assert [c["sha"] for c in window.json()] == [commits[2]["sha"], commits[1]["sha"]]   # newest first
    everything = await client.get(f"/repos/{PAYMENTS}/commits", params={"per_page": "2"}, headers=headers)
    assert [c["sha"] for c in everything.json()] == [commits[3]["sha"], commits[2]["sha"]]


@pytest.mark.asyncio
async def test_github_refuses_bad_credentials_and_unseen_repos(fake):
    state, client = fake
    for headers in ({"Authorization": "Bearer nope"}, {}):
        refused = await client.get(f"/repos/{PAYMENTS}", headers=headers)
        assert refused.status_code == 401 and refused.json()["message"] == "Bad credentials"
    # fake-gh-pat-2 sees acme/checkout only.
    unseen = await client.get(f"/repos/{PAYMENTS}", headers={"Authorization": "Bearer fake-gh-pat-2"})
    assert unseen.status_code == 404
    seen = await client.get("/repos/acme/checkout", headers={"Authorization": "Bearer fake-gh-pat-2"})
    assert seen.status_code == 200
    assert (await client.get("/repos/acme/nothing", headers={"Authorization": "Bearer fake-gh-pat"})).status_code == 404
    assert [r["auth"] for r in state.requests[:2]] == ["bad", "none"]


@pytest.mark.asyncio
async def test_a_revoked_pat_answers_401(fake):
    _, client = fake
    headers = {"Authorization": "Bearer fake-gh-pat"}
    assert (await client.get(f"/repos/{PAYMENTS}", headers=headers)).status_code == 200
    revoked = await client.post("/_fake/github/revoke", json={"token": "fake-gh-pat"})
    assert revoked.status_code == 200
    assert (await client.get(f"/repos/{PAYMENTS}", headers=headers)).status_code == 401
    # The other PAT is untouched; a reset forgets the revocation.
    assert (await client.get("/repos/acme/checkout", headers={"Authorization": "Bearer fake-gh-pat-2"})).status_code == 200
    await client.post("/_fake/reset")
    assert (await client.get(f"/repos/{PAYMENTS}", headers=headers)).status_code == 200


# ── GitLab ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gitlab_project_by_encoded_path_or_id(fake):
    state, client = fake
    headers = {"PRIVATE-TOKEN": "fake-gitlab-pat"}
    by_path = await client.get("/api/v4/projects/group%2Fapi-gateway", headers=headers)
    assert by_path.status_code == 200
    assert by_path.json() == {
        "id": 42, "name": "api-gateway", "path_with_namespace": "group/api-gateway",
        "web_url": "https://gitlab.fake.test/group/api-gateway",
    }
    by_id = await client.get("/api/v4/projects/42", headers=headers)
    assert by_id.json()["path_with_namespace"] == "group/api-gateway"
    missing = await client.get("/api/v4/projects/group%2Fnothing", headers=headers)
    assert missing.status_code == 404 and missing.json() == {"message": "404 Project Not Found"}
    bad = await client.get("/api/v4/projects/42", headers={"PRIVATE-TOKEN": "nope"})
    assert bad.status_code == 401 and bad.json() == {"message": "401 Unauthorized"}
    none = await client.get("/api/v4/projects/42")
    assert none.status_code == 401
    assert [r["auth"] for r in state.requests] == ["ok", "ok", "ok", "bad", "none"]
    assert {r["service"] for r in state.requests} == {"gitlab"}


# ── The webhook receiver ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_receiver_answers_and_logs_each_delivery(fake):
    state, client = fake
    sent = {"X-TestLookup-Event": "run.completed", "X-TestLookup-Delivery": "d-1", "X-TestLookup-Signature": "abc"}
    assert (await client.post("/hooks/ok", json={"a": 1}, headers=sent)).text == "ok"
    failed = await client.post("/hooks/fail", json={}, headers={"X-TestLookup-Event": "run.completed"})
    assert (failed.status_code, failed.text) == (500, "receiver error")
    rejected = await client.post("/hooks/reject", json={})
    assert (rejected.status_code, rejected.text) == (400, "bad request")
    assert (await client.post("/hooks/elsewhere", json={})).status_code == 404
    await client.post("/_fake/faults", json={"method": "POST", "path": "/hooks/ok", "status": 500, "times": 1})
    assert (await client.post("/hooks/ok", json={})).status_code == 500
    assert (await client.post("/hooks/ok", json={})).status_code == 200
    log = (await client.get("/_fake/hooks")).json()
    assert log[0] == {"path": "/hooks/ok", "status": 200, "event": "run.completed", "delivery_id": "d-1", "signed": True}
    assert [(h["path"], h["status"], h["signed"]) for h in log[1:]] == [
        ("/hooks/fail", 500, False), ("/hooks/reject", 400, False), ("/hooks/elsewhere", 404, False),
        ("/hooks/ok", 500, False), ("/hooks/ok", 200, False),
    ]
    await client.post("/_fake/reset")
    assert (await client.get("/_fake/hooks")).json() == []
    assert state.splunk_searches == [] and state.requests == []


# ── Dispatch order and secrecy ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_slack_webhook_under_services_still_reaches_slack(fake):
    state, client = fake
    resp = await client.post("/services/T000/B000/X", json={"text": "hello"})
    assert (resp.status_code, resp.text) == (200, "ok")
    assert state.slack_messages[-1]["path"] == "/services/T000/B000/X"
    assert state.requests[-1]["service"] == "slack"
    assert state.splunk_searches == []


@pytest.mark.asyncio
async def test_the_request_log_never_holds_a_credential(fake):
    state, client = fake
    await client.get("/services/server/info", headers=_bearer("fake-splunk-token"))
    await client.post(
        "/services/search/jobs", headers=_bearer("fake-splunk-token"),
        data=_spl_form('search index=payments service="checkout"', exec_mode="oneshot"),
    )
    await client.get("/api/v1/namespaces/qa-payments", headers=_bearer("fake-ocp-token"))
    await client.get(f"/api/v3/repos/{PAYMENTS}", headers={"Authorization": "token fake-gh-pat"})
    await client.get("/repos/acme/checkout", headers={"Authorization": "Bearer fake-gh-pat-2"})
    await client.get("/api/v4/projects/42", headers={"PRIVATE-TOKEN": "fake-gitlab-pat"})
    await client.get("/rest/api/3/myself", auth=(state.jira_email, state.jira_token))
    dumped = json.dumps(state.requests) + json.dumps(state.splunk_searches) + json.dumps(state.hooks)
    for token in TOKENS:
        assert token not in dumped
    assert all(r["auth"] == "ok" for r in state.requests)
    assert [r["service"] for r in state.requests] == ["splunk", "splunk", "ocp", "github", "github", "gitlab", "jira"]


def test_the_fake_reads_its_tokens_from_the_environment(monkeypatch):
    from tests.fakes import fake_integrations

    monkeypatch.setenv("FAKE_SPLUNK_TOKEN", "s-env")
    monkeypatch.setenv("FAKE_OCP_TOKEN", "o-env")
    monkeypatch.setenv("FAKE_GITHUB_PAT", "g-env")
    monkeypatch.setenv("FAKE_GITLAB_PAT", "l-env")
    state = fake_integrations._state_from_env()
    assert (state.splunk_token, state.ocp_token, state.gitlab_token) == ("s-env", "o-env", "l-env")
    assert state.github_tokens["g-env"] == {PAYMENTS}
    assert "fake-gh-pat" not in state.github_tokens
