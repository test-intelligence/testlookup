"""PMF US-6.1 / US-6.2 / US-6.3: one-click Jira defects.

Pins the contracts the frontend + the orchestrating beat rely on:

1. **Dedup before create** — an OPEN defect already linked to Jira for the
   same signature short-circuits issue creation: the existing issue gets a
   "recurred in build X" comment (best-effort), the recurrence counter is
   bumped, and the response carries ``deduplicated=true`` + the existing
   link. No second Jira issue is ever filed.
2. **Creation payload shape** — the Jira REST v3 body carries the prefilled
   summary, the requested project key / issue type, TestLookup labels, and
   an ADF description; the staged Defect row records the bidirectional
   link (key + URL + signature).
3. **Offline / unconfigured blocking** — ``AI_OFFLINE_MODE`` (hard kill
   switch) and missing config produce an actionable 503, mirroring the
   promote-flow contract.
4. **Metadata cache** — Jira project/issue-type fetches are cached ~5 min;
   unreachable/unconfigured Jira yields a graceful ``available=false``
   shape, never a 5xx.
5. **Webhook fallback (US-6.3)** — ``target="webhook"`` emits the
   registered ``defect.create_requested`` event with the prefilled payload
   and never touches Jira; gated the same way the webhook subsystem is.
6. **Status sync-back (US-6.2)** — the beat-driven sweep mirrors Jira
   status + timestamp and raises ``external_status_conflict`` only when a
   Done-category status coincides with recent failures of the signature.
7. **Router shape** — project-scoped paths carry ``require_project_access``
   (authorization ratchet) and the router is registered in bootstrap.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.routers import defect_jira as defect_jira_router  # noqa: E402
from app.services import defect_jira_service as svc  # noqa: E402


# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")

    def json(self):
        return self._payload


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDB:
    """Programmable AsyncSession stand-in: pops one result per execute."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.stmts = []
        self.added = []
        self.flush_count = 0

    async def execute(self, stmt, params=None):
        self.stmts.append(stmt)
        if self.results:
            return self.results.pop(0)
        return _FakeResult(None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_count += 1


_ONLINE_CFG = {
    "enabled": True,
    "domain": "example.atlassian.net",
    "email": "bot@example.com",
    "api_token": "token",
    "default_project_key": "QA",
}


def _prefill(signature: str = "f" * 16) -> dict:
    return {
        "signature": signature,
        "summary": "[TestLookup] checkout_flow_test",
        "description": "Automated defect filed from TestLookup for: checkout_flow_test",
        "test_name": "checkout_flow_test",
        "suite_name": "checkout",
        "cluster_id": None,
        "error_message": "AssertionError: expected 200 got 500",
        "occurrences": {
            "first_seen": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "last_seen": datetime(2026, 7, 9, tzinfo=timezone.utc),
            "failing_runs": 6,
        },
        "context": {"branch": "main", "build_number": "1234", "ci_run_url": None},
        "ai_analysis": {"root_cause": "NPE in cart service", "confidence": 82,
                        "failure_category": "PRODUCT_BUG"},
        "deep_link": "http://localhost:3000/runs/abc",
        "latest_run_id": "abc",
        "existing_defect": None,
    }


@pytest.fixture()
def online(monkeypatch):
    """Jira reachable + offline mode off + prefill stubbed."""
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    async def _pref(db, project_id, *, fingerprint=None, cluster_id=None):
        return _prefill()

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "build_prefill", _pref)
    yield


# ── 1. Dedup before create ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dedup_links_existing_and_comments_instead_of_creating(online, monkeypatch):
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        jira_ticket_id="QA-17",
        jira_ticket_url="https://example.atlassian.net/browse/QA-17",
        jira_status="In Progress",
        recurrence_count=2,
        last_recurrence_at=None,
    )

    async def _find(db, project_id, signature):
        return existing

    comments: list[tuple] = []

    async def _comment(cfg, issue_key, prefill, recurrence_count):
        comments.append((issue_key, recurrence_count))
        return True

    creates: list = []

    async def _post(cfg, path, json):
        creates.append(path)
        return _FakeResp(201, {"key": "QA-99"})

    monkeypatch.setattr(svc, "find_open_linked_defect", _find)
    monkeypatch.setattr(svc, "_post_recurrence_comment", _comment)
    monkeypatch.setattr(svc, "_jira_post", _post)

    db = _FakeDB()
    out = await svc.create_or_link_issue(
        db, str(uuid.uuid4()), SimpleNamespace(id=uuid.uuid4(), username="qa"),
        fingerprint="f" * 16,
    )

    assert out["deduplicated"] is True
    assert out["jira_key"] == "QA-17"
    assert out["recurrence_count"] == 3          # bumped 2 → 3
    assert out["recurrence_comment_posted"] is True
    assert "recurrence noted" in out["message"]
    assert comments == [("QA-17", 3)]
    assert creates == []                          # no duplicate issue filed
    assert existing.last_recurrence_at is not None
    assert db.added == []                         # no new Defect row staged


# ── 2. Creation payload shape + staged Defect link ──────────────────────────


@pytest.mark.asyncio
async def test_create_posts_prefilled_payload_and_stages_linked_defect(online, monkeypatch):
    async def _find(db, project_id, signature):
        return None

    posts: list[tuple] = []

    async def _post(cfg, path, json):
        posts.append((path, json))
        return _FakeResp(201, {"key": "QA-42", "id": "10001"})

    monkeypatch.setattr(svc, "find_open_linked_defect", _find)
    monkeypatch.setattr(svc, "_jira_post", _post)

    db = _FakeDB()
    pid = uuid.uuid4()
    # cluster path skips the TestCase attach lookup — no DB rows needed.
    out = await svc.create_or_link_issue(
        db, str(pid), SimpleNamespace(id=uuid.uuid4(), username="qa"),
        cluster_id="cl_001", issue_type="Task", jira_project_key="PLAT",
        extra_comment="Seen during release hardening.",
    )

    assert out["deduplicated"] is False
    assert out["jira_key"] == "QA-42"
    assert out["jira_url"].endswith("/browse/QA-42")

    (path, body), = posts
    assert path == "/rest/api/3/issue"
    fields = body["fields"]
    assert fields["project"] == {"key": "PLAT"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["summary"] == "[TestLookup] checkout_flow_test"
    assert "testlookup" in fields["labels"]
    # ADF description carries the clearly-labelled AI block + the deep link.
    adf_text = str(fields["description"])
    assert "Suggested root cause (AI, confidence 82%)" in adf_text
    assert "http://localhost:3000/runs/abc" in adf_text
    assert "Seen during release hardening." in adf_text

    # Bidirectional link staged on the Defect row; router commits.
    (defect,) = db.added
    assert defect.jira_ticket_id == "QA-42"
    assert defect.jira_ticket_url.endswith("/browse/QA-42")
    assert defect.signature_fingerprint == "f" * 16
    assert defect.promotion_source == "one_click_jira"
    assert defect.resolution_status == "OPEN"
    assert db.flush_count >= 1


@pytest.mark.asyncio
async def test_jira_error_surfaces_as_actionable_502(online, monkeypatch):
    async def _find(db, project_id, signature):
        return None

    async def _post(cfg, path, json):
        return _FakeResp(400, {"errors": {"summary": "bad"}}, text="field error")

    monkeypatch.setattr(svc, "find_open_linked_defect", _find)
    monkeypatch.setattr(svc, "_jira_post", _post)

    with pytest.raises(HTTPException) as exc:
        await svc.create_or_link_issue(
            _FakeDB(), str(uuid.uuid4()), SimpleNamespace(id=uuid.uuid4()),
            cluster_id="cl_001",
        )
    assert exc.value.status_code == 502
    assert "Jira rejected" in exc.value.detail


# ── 3. Offline / unconfigured blocking ───────────────────────────────────────


@pytest.mark.asyncio
async def test_offline_mode_blocks_jira_target_with_actionable_503(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    async def _pref(db, project_id, *, fingerprint=None, cluster_id=None):
        return _prefill()

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    monkeypatch.setattr(svc, "build_prefill", _pref)
    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)

    with pytest.raises(HTTPException) as exc:
        await svc.create_or_link_issue(
            _FakeDB(), str(uuid.uuid4()), SimpleNamespace(id=uuid.uuid4()),
            fingerprint="f" * 16,
        )
    assert exc.value.status_code == 503
    assert "AI_OFFLINE_MODE" in exc.value.detail


def test_availability_reason_order():
    # Offline wins over everything (hard kill switch).
    settings_offline = settings.AI_OFFLINE_MODE
    try:
        settings.AI_OFFLINE_MODE = True
        assert svc.availability_reason(dict(_ONLINE_CFG)) == "offline_mode"
        settings.AI_OFFLINE_MODE = False
        assert svc.availability_reason({**_ONLINE_CFG, "enabled": False}) == "disabled"
        assert svc.availability_reason({**_ONLINE_CFG, "api_token": None}) == "not_configured"
        assert svc.availability_reason(dict(_ONLINE_CFG)) is None
    finally:
        settings.AI_OFFLINE_MODE = settings_offline


# ── 4. Metadata: graceful shape + 5-minute cache ─────────────────────────────


@pytest.mark.asyncio
async def test_metadata_unconfigured_is_graceful_not_5xx(monkeypatch):
    svc._metadata_cache_clear()
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _cfg(db):
        return {**_ONLINE_CFG, "domain": None}

    async def _wh(db, project_id):
        return False

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "_webhook_target_available", _wh)

    out = await svc.get_metadata(_FakeDB(), str(uuid.uuid4()))
    assert out["available"] is False
    assert out["reason"] == "not_configured"
    assert out["projects"] == []


@pytest.mark.asyncio
async def test_metadata_caches_jira_fetches_for_ttl(monkeypatch):
    svc._metadata_cache_clear()
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    async def _wh(db, project_id):
        return True

    calls: list[str] = []

    async def _get(cfg, path, params=None):
        calls.append(path)
        if "project/search" in path:
            return _FakeResp(200, {"values": [{"key": "QA", "name": "Quality"}]})
        return _FakeResp(200, [
            {"name": "Bug", "subtask": False},
            {"name": "Sub-task", "subtask": True},
            {"name": "Task", "subtask": False},
        ])

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "_webhook_target_available", _wh)
    monkeypatch.setattr(svc, "_jira_get", _get)

    pid = str(uuid.uuid4())
    first = await svc.get_metadata(_FakeDB(), pid)
    assert first["available"] is True
    assert first["projects"] == [{"key": "QA", "name": "Quality"}]
    assert first["issue_types"] == ["Bug", "Task"]      # subtasks filtered
    assert first["webhook_available"] is True
    assert len(calls) == 2

    second = await svc.get_metadata(_FakeDB(), pid)
    assert second["available"] is True
    assert len(calls) == 2                               # served from cache

    svc._metadata_cache_clear()
    await svc.get_metadata(_FakeDB(), pid)
    assert len(calls) == 4                               # cache expiry refetches


@pytest.mark.asyncio
async def test_metadata_unreachable_jira_reports_reason(monkeypatch):
    svc._metadata_cache_clear()
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    async def _wh(db, project_id):
        return False

    async def _get(cfg, path, params=None):
        raise ConnectionError("boom")

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "_webhook_target_available", _wh)
    monkeypatch.setattr(svc, "_jira_get", _get)

    out = await svc.get_metadata(_FakeDB(), str(uuid.uuid4()))
    assert out["available"] is False
    assert out["reason"] == "unreachable"


# ── 5. Webhook fallback (US-6.3) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_target_emits_registered_event_with_prefill(online, monkeypatch):
    from app.services import webhook_service

    # The event must be in the authoritative registry…
    assert "defect.create_requested" in webhook_service.SUPPORTED_EVENTS
    assert any(
        e["event_type"] == "defect.create_requested"
        for e in webhook_service.list_supported_events()
    )

    emitted: list[tuple] = []

    async def _allowed():
        return True

    async def _emit(event_type, *, project_id, payload):
        emitted.append((event_type, project_id, payload))
        return 2

    monkeypatch.setattr(webhook_service, "_post_allowed", _allowed)
    monkeypatch.setattr(webhook_service, "emit_event", _emit)

    pid = uuid.uuid4()
    out = await svc.create_or_link_issue(
        _FakeDB(), str(pid), SimpleNamespace(id=uuid.uuid4(), username="qa"),
        fingerprint="f" * 16, target="webhook", extra_comment="please route",
    )

    assert out["target"] == "webhook"
    assert out["subscriptions_notified"] == 2
    assert out["jira_key"] is None

    (event_type, event_pid, payload), = emitted
    assert event_type == "defect.create_requested"
    assert event_pid == pid
    assert payload["summary"] == "[TestLookup] checkout_flow_test"
    assert payload["signature"] == "f" * 16
    assert payload["issue_type"] == "Bug"
    assert payload["occurrences"]["failing_runs"] == 6
    assert payload["ai_analysis"]["root_cause"] == "NPE in cart service"
    assert payload["extra_comment"] == "please route"
    assert payload["requested_by"] == "qa"


@pytest.mark.asyncio
async def test_webhook_target_blocked_when_subsystem_gated(online, monkeypatch):
    from app.services import webhook_service

    async def _blocked():
        return False

    monkeypatch.setattr(webhook_service, "_post_allowed", _blocked)

    with pytest.raises(HTTPException) as exc:
        await svc.create_or_link_issue(
            _FakeDB(), str(uuid.uuid4()), SimpleNamespace(id=uuid.uuid4()),
            fingerprint="f" * 16, target="webhook",
        )
    assert exc.value.status_code == 503
    assert "outbound_webhooks" in exc.value.detail


# ── 6. Status sync-back (US-6.2) ─────────────────────────────────────────────


def _defect(key: str, signature: str = "f" * 16) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        jira_ticket_id=key,
        jira_status=None,
        external_status_at=None,
        external_status_conflict=False,
        resolution_status="OPEN",
        signature_fingerprint=signature,
        test_case_id=None,
    )


@pytest.mark.asyncio
async def test_sync_mirrors_status_and_flags_conflict(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)

    d_open = _defect("QA-1")
    d_done_still_failing = _defect("QA-2")
    d_done_quiet = _defect("QA-3")

    statuses = {
        "QA-1": ("In Progress", "indeterminate"),
        "QA-2": ("Done", "done"),
        "QA-3": ("Done", "done"),
    }

    async def _get(cfg, path, params=None):
        key = path.rsplit("/", 1)[-1]
        name, category = statuses[key]
        return _FakeResp(200, {
            "fields": {"status": {"name": name, "statusCategory": {"key": category}}},
        })

    still_failing = {d_done_still_failing.id: True, d_done_quiet.id: False}

    async def _failing(db, defect):
        return still_failing.get(defect.id, False)

    monkeypatch.setattr(svc, "_jira_get", _get)
    monkeypatch.setattr(svc, "_signature_still_failing", _failing)

    db = _FakeDB(results=[
        _FakeResult(rows=[d_open, d_done_still_failing, d_done_quiet]),
    ])
    out = await svc.sync_external_statuses(db)

    assert out == {"checked": 3, "conflicts": 1, "errors": 0}
    assert d_open.jira_status == "In Progress"
    assert d_open.external_status_conflict is False
    assert d_open.external_status_at is not None
    # Done in Jira + still failing → conflict badge.
    assert d_done_still_failing.external_status_conflict is True
    # Done in Jira + quiet → no conflict.
    assert d_done_quiet.external_status_conflict is False
    assert db.flush_count == 1                    # staged; the beat task commits


@pytest.mark.asyncio
async def test_sync_skips_entirely_when_offline(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)

    db = _FakeDB()
    out = await svc.sync_external_statuses(db)
    assert out["skipped"] == "offline_mode"
    assert db.stmts == []                         # no DB scan, no HTTP


@pytest.mark.asyncio
async def test_sync_respects_per_cycle_cap_in_query(monkeypatch):
    """The defect scan is LIMITed to the cap so one cycle can never fan out
    an unbounded number of Jira calls."""
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)

    db = _FakeDB(results=[_FakeResult(rows=[])])
    await svc.sync_external_statuses(db, cap=7)
    (stmt,) = db.stmts
    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    assert "LIMIT 7" in str(compiled).upper()
    # Oldest-refreshed first so a big backlog rotates through cycles.
    assert "external_status_at" in str(compiled)


@pytest.mark.asyncio
async def test_sync_stamps_timestamp_on_http_error_so_bad_issue_cannot_starve_batch(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)

    async def _cfg(db):
        return dict(_ONLINE_CFG)

    async def _get(cfg, path, params=None):
        return _FakeResp(404, {})

    monkeypatch.setattr(svc, "resolve_jira_config", _cfg)
    monkeypatch.setattr(svc, "_jira_get", _get)

    d = _defect("QA-404")
    db = _FakeDB(results=[_FakeResult(rows=[d])])
    out = await svc.sync_external_statuses(db)
    assert out["errors"] == 1
    assert d.external_status_at is not None       # rotates out of the window


# ── 7. Router shape + guards + registration ──────────────────────────────────


def _walk_dep_names(dependant) -> list[str]:
    out: list[str] = []
    stack = [dependant]
    while stack:
        dep = stack.pop()
        call = dep.call
        if call is not None:
            out.append(getattr(call, "__qualname__", None) or type(call).__name__)
        stack.extend(dep.dependencies)
    return out


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/v1/projects/{project_id}/defects/jira/metadata"),
        ("GET", "/api/v1/projects/{project_id}/defects/jira/preview"),
        ("POST", "/api/v1/projects/{project_id}/defects/jira"),
    ],
)
def test_routes_carry_project_access_guard(method, path):
    from fastapi.routing import APIRoute

    routes = [r for r in defect_jira_router.router.routes if isinstance(r, APIRoute)]
    matches = [r for r in routes if r.path == path and method in r.methods]
    assert matches, [(sorted(r.methods), r.path) for r in routes]
    dep_names = _walk_dep_names(matches[0].dependant)
    assert any("require_project_access" in n for n in dep_names), dep_names


def test_create_route_requires_qa_engineer_role():
    from fastapi.routing import APIRoute

    route = next(
        r for r in defect_jira_router.router.routes
        if isinstance(r, APIRoute) and r.path == "/api/v1/projects/{project_id}/defects/jira"
    )
    dep_names = _walk_dep_names(route.dependant)
    assert any("require_role" in n for n in dep_names), dep_names


def test_router_registered_as_protected():
    from app import bootstrap

    assert defect_jira_router.router in bootstrap.PROTECTED_ROUTERS


# ── Prefill text assembly (pure) ─────────────────────────────────────────────


def test_description_text_carries_all_sections():
    text_out = svc._build_description_text(
        label="checkout_flow_test",
        suite_name="checkout",
        error_message="AssertionError: expected 200 got 500",
        stack_trace="Traceback...\n" + "x" * 5000,
        first_seen=datetime(2026, 7, 1, tzinfo=timezone.utc),
        last_seen=datetime(2026, 7, 9, tzinfo=timezone.utc),
        failing_runs=6,
        member_count=None,
        branch="main",
        build_number="1234",
        ci_run_url="https://ci.example.com/run/9",
        deep_link="http://localhost:3000/runs/abc",
        ai_block={"root_cause": "NPE in cart service", "confidence": 82},
        )
    assert "[TestLookup]" not in text_out          # summary carries the prefix, not the body
    assert "First seen: 2026-07-01" in text_out
    assert "Failing runs: 6" in text_out
    assert "branch main, build 1234" in text_out
    assert "AssertionError" in text_out
    assert "Suggested root cause (AI, confidence 82%): NPE in cart service" in text_out
    assert "http://localhost:3000/runs/abc" in text_out
    # Stack trace truncated to 3000 chars.
    assert len(text_out) < 5000


def test_cluster_signature_is_stable_and_fingerprint_shaped():
    a = svc.cluster_signature("Timeout waiting for element")
    b = svc.cluster_signature("Timeout waiting for element")
    c = svc.cluster_signature("NullPointerException in cart")
    assert a == b
    assert a != c
    assert len(a) == 64
    assert all(ch in "0123456789abcdef" for ch in a)
