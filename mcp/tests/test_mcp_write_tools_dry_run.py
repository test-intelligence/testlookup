"""MCP write tools, called for real with a fake ``api`` (GAP-E07).

``test_mcp_write_tools.py`` greps the source for ``dry_run: bool = True`` and
tests the pure ``_select_ready_to_promote`` helper. Inverting ``if dry_run:``
left every test green: an agent's "preview" call would release quarantines.
Here each write tool is registered on a fake MCP and called, with
``client.get`` / ``client.post`` recording every request:

* ``promote_ready_quarantines`` by default sends NO write; ``dry_run=False``
  sends one release per ready row (exactly one for one row, at most 10);
* ``create_defect`` with ``dry_run=True`` sends no write; without it, exactly one;
* approve / reject / propose / release post once, to the right path, with the notes;
* ``create_project`` drops ``None`` fields;
* a 403 reaches the agent as ``status_code`` + ``detail`` where the tool
  catches, and as a raised tool error where it does not.
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

PROJECT_ID = "11111111-1111-4111-8111-111111111111"


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _register


def _row(i, ready=True):
    return {
        "id": f"00000000-0000-4000-8000-0000000000{i:02d}",
        "test_name": f"test_flaky_{i:02d}",
        "test_fingerprint": f"{i:016x}",
        "consecutive_passes": 5,
        "status": "QUARANTINED",
        "ready_to_promote": ready,
    }


def _http_error(status, detail):
    request = httpx.Request("POST", "http://testlookup.example.test/api/v1/x")
    response = httpx.Response(status, json={"detail": detail}, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status}'", request=request, response=response
    )


@pytest.fixture
def api(monkeypatch):
    import client
    from tools import defects, projects, quarantine

    state = {
        "calls": [],
        "rows": [_row(i) for i in range(12)] + [_row(99, ready=False)],
        "post_error": {},  # path -> exception
    }

    async def _get(path, params=None):
        state["calls"].append(("GET", path, params))
        if path == "/api/v1/quarantine":
            return state["rows"]
        if path.endswith("/defects/jira/preview"):
            return {"summary": "Checkout total is wrong", "deduplicated": False}
        raise AssertionError(f"unexpected GET {path}")

    async def _post(path, json_body=None, params=None, extra_headers=None):
        state["calls"].append(("POST", path, json_body))
        if path in state["post_error"]:
            raise state["post_error"][path]
        if path == "/api/v1/projects":
            return {"id": "proj-new", "created_at": "2026-11-18T00:00:00Z", **json_body}
        if path.endswith("/defects/jira"):
            return {"defect_id": "def-1", "issue_key": "PAY-1", "deduplicated": False}
        request_id = path.split("/")[4] if path.count("/") >= 5 else "req-new"
        return {
            "id": request_id,
            # A proposal answers with the request awaiting review; a release with the released one.
            "status": state.get("proposal_status", "PROPOSED") if path == "/api/v1/quarantine" else "RELEASED",
            "test_name": "test_x",
            "test_fingerprint": "f" * 16,
        }

    monkeypatch.setattr(client, "get", _get)
    monkeypatch.setattr(client, "post", _post)
    mcp = _FakeMCP()
    for module in (quarantine, defects, projects):
        module.register(mcp)
    state["tools"] = mcp.tools
    return state


def _call(api, tool_name, **kwargs):
    return asyncio.run(api["tools"][tool_name](**kwargs))


def _writes(api):
    return [(path, body) for method, path, body in api["calls"] if method == "POST"]


# ── promote_ready_quarantines ────────────────────────────────────────────────


def test_promote_by_default_is_a_dry_run_that_sends_no_write(api):
    result = _call(api, "promote_ready_quarantines", project_id=PROJECT_ID)

    assert _writes(api) == [], "the default call must not release anything"
    assert result["dry_run"] is True and result["ok"] is True
    assert result["count"] == 10, "the listing is capped at 10 per call"
    assert [r["request_id"] for r in result["ready_to_promote"]] == [
        f"00000000-0000-4000-8000-0000000000{i:02d}" for i in range(10)
    ]
    assert api["calls"] == [
        (
            "GET",
            "/api/v1/quarantine",
            {"project_id": PROJECT_ID, "live_only": True, "limit": 500},
        )
    ]


def test_promote_with_dry_run_false_releases_one_ready_row_exactly_once(api):
    api["rows"] = [_row(1), _row(2, ready=False)]

    result = _call(
        api, "promote_ready_quarantines", project_id=PROJECT_ID, dry_run=False
    )

    writes = _writes(api)
    assert [path for path, _ in writes] == ["/api/v1/quarantine/00000000-0000-4000-8000-000000000001/release"]
    assert "Promoted out of quarantine" in writes[0][1]["notes"]
    assert result == {
        "ok": True,
        "dry_run": False,
        "released_count": 1,
        "remaining_ready": 0,
        "results": [
            {
                "request_id": "00000000-0000-4000-8000-000000000001",
                "test_name": "test_flaky_01",
                "fingerprint": f"{1:016x}",
                "consecutive_passes": 5,
                "status": "RELEASED",
                "released": True,
            }
        ],
    }


def test_promote_with_dry_run_false_releases_at_most_ten(api):
    result = _call(
        api, "promote_ready_quarantines", project_id=PROJECT_ID, dry_run=False
    )

    paths = [path for path, _ in _writes(api)]
    assert paths == [f"/api/v1/quarantine/00000000-0000-4000-8000-0000000000{i:02d}/release" for i in range(10)]
    assert result["released_count"] == 10


def test_a_refused_release_is_reported_and_does_not_stop_the_batch(api):
    api["rows"] = [_row(1), _row(2), _row(3)]
    api["post_error"]["/api/v1/quarantine/00000000-0000-4000-8000-000000000002/release"] = _http_error(
        409, "Request is not in an active state"
    )

    result = _call(
        api, "promote_ready_quarantines", project_id=PROJECT_ID, dry_run=False
    )

    assert len(_writes(api)) == 3
    assert result["ok"] is False and result["released_count"] == 2
    failed = [r for r in result["results"] if not r["released"]]
    assert len(failed) == 1 and failed[0]["request_id"] == "00000000-0000-4000-8000-000000000002"
    assert failed[0]["status_code"] == 409
    assert failed[0]["detail"] == "Request is not in an active state"


# ── create_defect ────────────────────────────────────────────────────────────


def test_create_defect_dry_run_previews_and_sends_no_write(api):
    result = _call(
        api,
        "create_defect",
        project_id=PROJECT_ID,
        fingerprint="abcd1234abcd1234",
        dry_run=True,
    )

    assert _writes(api) == [], "dry_run=True must not file an issue"
    assert api["calls"] == [
        (
            "GET",
            f"/api/v1/projects/{PROJECT_ID}/defects/jira/preview",
            {"fingerprint": "abcd1234abcd1234"},
        )
    ]
    assert (
        result["dry_run"] is True
        and result["preview"]["summary"] == "Checkout total is wrong"
    )


def test_create_defect_without_dry_run_files_exactly_one_issue(api):
    result = _call(
        api,
        "create_defect",
        project_id=PROJECT_ID,
        cluster_id="cluster-7",
        extra_comment="seen twice",
    )

    assert _writes(api) == [
        (
            f"/api/v1/projects/{PROJECT_ID}/defects/jira",
            {
                "cluster_id": "cluster-7",
                "target": "jira",
                "issue_type": "Bug",
                "extra_comment": "seen twice",
            },
        )
    ]
    assert result == {
        "ok": True,
        "dry_run": False,
        "defect_id": "def-1",
        "issue_key": "PAY-1",
        "deduplicated": False,
    }


def test_create_defect_needs_exactly_one_signature_and_sends_nothing_otherwise(api):
    both = _call(
        api, "create_defect", project_id=PROJECT_ID, fingerprint="f", cluster_id="c"
    )
    neither = _call(api, "create_defect", project_id=PROJECT_ID)

    assert api["calls"] == []
    assert both["ok"] is False and neither["ok"] is False
    assert "exactly one" in both["error"]


# ── single-request write tools ───────────────────────────────────────────────


def test_approve_posts_once_with_notes_and_duration(api):
    _call(
        api,
        "approve_quarantine",
        request_id="00000000-0000-4000-8000-000000000005",
        notes="flaky wait",
        quarantine_duration_days=7,
    )

    assert _writes(api) == [
        (
            "/api/v1/quarantine/00000000-0000-4000-8000-000000000005/approve",
            {"notes": "flaky wait", "quarantine_duration_days": 7},
        )
    ]


def test_reject_posts_once_with_notes(api):
    text = _call(api, "reject_quarantine", request_id="00000000-0000-4000-8000-000000000006", notes="real failure")

    assert _writes(api) == [
        ("/api/v1/quarantine/00000000-0000-4000-8000-000000000006/reject", {"notes": "real failure"})
    ]
    assert "rejected" in text


def test_propose_posts_a_proposal_and_never_approves(api):
    result = _call(
        api,
        "propose_quarantine",
        project_id=PROJECT_ID,
        fingerprint="abcd",
        reason="flips 40%",
    )

    assert _writes(api) == [
        (
            "/api/v1/quarantine",
            {
                "project_id": PROJECT_ID,
                "test_fingerprint": "abcd",
                "detection_method": "manual",
                "rationale": {"reason": "flips 40%", "source": "mcp"},
                "quarantine_duration_days": 14,
            },
        )
    ]
    assert result["ok"] is True and result["action"] == "proposed"
    assert "approve_quarantine" in result["next_step"]


@pytest.mark.parametrize("status", ["QUARANTINED", "RECHECK_SCHEDULED", "RE_QUARANTINED", "APPROVED"])
def test_propose_on_a_test_already_quarantined_does_not_claim_a_new_proposal(api, status):
    """O-01: the route answers with the test's live request; only a PROPOSED one awaits review."""
    api["proposal_status"] = status

    result = _call(api, "propose_quarantine", project_id=PROJECT_ID, fingerprint="abcd", reason="flips 40%")

    assert result["ok"] is True and result["action"] == "already_live" and result["status"] == status
    assert "approve_quarantine" not in result["next_step"] and status in result["next_step"]


def test_release_posts_once_with_the_reason(api):
    result = _call(
        api, "release_quarantine", request_id="00000000-0000-4000-8000-000000000007", reason="fix merged in #123"
    )

    assert _writes(api) == [
        ("/api/v1/quarantine/00000000-0000-4000-8000-000000000007/release", {"notes": "fix merged in #123"})
    ]
    assert result["ok"] is True and result["action"] == "released"


def test_create_project_drops_none_fields(api):
    _call(
        api,
        "create_project",
        name="Payments Service",
        slug="payments-service",
        jira_project_key="PAY",
    )

    assert _writes(api) == [
        (
            "/api/v1/projects",
            {
                "name": "Payments Service",
                "slug": "payments-service",
                "jira_project_key": "PAY",
            },
        )
    ]


# ── refusals reach the agent ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tool", "kwargs", "path"),
    [
        (
            "release_quarantine",
            {"request_id": "00000000-0000-4000-8000-000000000008", "reason": "r"},
            "/api/v1/quarantine/00000000-0000-4000-8000-000000000008/release",
        ),
        (
            "propose_quarantine",
            {"project_id": PROJECT_ID, "fingerprint": "f", "reason": "r"},
            "/api/v1/quarantine",
        ),
        (
            "create_defect",
            {"project_id": PROJECT_ID, "fingerprint": "f"},
            f"/api/v1/projects/{PROJECT_ID}/defects/jira",
        ),
    ],
)
def test_a_403_surfaces_status_and_detail_through_error_payload(
    api, tool, kwargs, path
):
    api["post_error"][path] = _http_error(403, "QA_LEAD role required")

    result = _call(api, tool, **kwargs)

    assert result["ok"] is False
    assert result["status_code"] == 403
    assert result["detail"] == "QA_LEAD role required"


def test_a_403_from_a_tool_that_does_not_catch_is_raised_as_a_tool_error(api):
    api["post_error"]["/api/v1/projects"] = _http_error(403, "ADMIN role required")

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _call(api, "create_project", name="X", slug="x")

    assert raised.value.response.status_code == 403
