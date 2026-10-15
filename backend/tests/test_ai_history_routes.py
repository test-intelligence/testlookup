"""W03 G-B: the AI-history routes (contract §6, the §6.8 matrix) on fakes.

The read service is stubbed with the REAL route outputs recorded by
``tests/integration/test_ai_history_routes_postgres.py``
(``tests/fixtures/ai_history/route_*.golden.json``): each route must hand that
body through its response model unchanged (aliases, datetimes, nulls), wrapped
in the E8.3 envelope. Then the matrix: who gets a 200, who a 404 and with which
body, which ids are 422 before any read, Mongo down = 503
``ai_history_unavailable``, no GET writes anything, and the export's E8.4 audit
row, 409, watermark, metric and header-safe filename.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import ai_history_read_service as reads
from app.services.ai_history_service import AIHistoryUnavailable

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "ai_history"
ENVELOPE_KEYS = ("requires_human_review", "review", "ai_disclaimer", "ai_disclaimer_version")

PROJECT = uuid.UUID("00000000-0000-4000-8000-000000000002")
OTHER_PROJECT = uuid.UUID("99999999-9999-4999-8999-999999999999")
RUN = uuid.UUID("00000000-0000-4000-8000-000000000001")
TEST = uuid.UUID("00000000-0000-4000-8000-000000000002")
VERSION = "00000000-0000-4000-8000-000000000004"
BASE = f"/api/v1/runs/{RUN}"


def _golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / f"route_{name}.golden.json").read_text(encoding="utf-8"))


def _service_body(name: str) -> dict:
    """What the read service returns: the golden minus the envelope the route adds."""
    return {k: v for k, v in _golden(name).items() if k not in ENVELOPE_KEYS}


def _user(role: str, bound_project=None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, username="vera")
    if bound_project is not None:
        user._testlookup_api_key_project_id = bound_project
    return user


class _Res:
    def __init__(self, value=None, rows=()):
        self._value, self._rows = value, list(rows)

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._rows[0] if self._rows else None, all=lambda: list(self._rows))


class _RouteDB:
    """Answers the guard, the run load, the review envelope and the drafts flag; spies every write."""

    def __init__(self, state):
        self.state = state
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM project_members" in sql:
            return _Res(uuid.uuid4() if self.state.member else None)
        if "FROM review_requests" in sql:
            return _Res(rows=[])
        if "FROM projects" in sql:
            return _Res(self.state.allow_drafts)
        if "FROM test_runs" in sql:
            ids = [v for v in stmt.compile().params.values() if isinstance(v, uuid.UUID)]
            run = self.state.runs.get(ids[0]) if ids else None
            if sql.startswith("SELECT test_runs.project_id"):
                return _Res(run.project_id if run else None)
            return _Res(run)
        raise AssertionError(f"unexpected query: {sql[:200]}")


@pytest.fixture
def api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import ai_history

    run = SimpleNamespace(id=RUN, project_id=PROJECT, build_number="101", branch="main", primary_suite_name="checkout")
    state = SimpleNamespace(user=_user("VIEWER"), member=True, allow_drafts=False, runs={RUN: run})
    db = _RouteDB(state)

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(ai_history.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    mongo = object()
    monkeypatch.setattr(ai_history, "get_mongo_db", lambda: mongo)
    stubs = SimpleNamespace(
        list_versions=AsyncMock(side_effect=lambda *a, **k: _service_body("r1_list")),
        diff_versions=AsyncMock(side_effect=lambda *a, **k: _service_body("r2_diff")),
        get_version_detail=AsyncMock(side_effect=lambda *a, **k: _service_body("r4_version")),
        list_test_versions=AsyncMock(side_effect=lambda *a, **k: _service_body("r5_test_versions")),
        previous_run_body=AsyncMock(side_effect=lambda *a, **k: _service_body("r6_previous")),
        compare_runs_ai=AsyncMock(side_effect=lambda *a, **k: _service_body("r7_compare")),
        # The bundle embeds the envelope itself (the compliance pack carries it too).
        export_bundle=AsyncMock(side_effect=lambda *a, **k: _golden("r3_export")),
    )
    for name in vars(stubs):
        monkeypatch.setattr(reads, name, getattr(stubs, name))
    return SimpleNamespace(http=TestClient(app, raise_server_exceptions=False), state=state, db=db, stubs=stubs,
                           mongo=mongo, run=run)


GETS = [
    ("r1_list", f"{BASE}/ai-history", {}),
    ("r2_diff", f"{BASE}/ai-history/diff", {"from": VERSION}),
    ("r4_version", f"{BASE}/ai-history/{VERSION}", {}),
    ("r5_test_versions", f"{BASE}/tests/{TEST}/ai-history", {}),
    ("r6_previous", f"{BASE}/previous", {}),
    ("r7_compare", f"{BASE}/ai-compare", {}),
]
EXPORT = ("r3_export", f"{BASE}/ai-history/export", {})


def _stub_for(api, name):
    return {
        "r1_list": api.stubs.list_versions, "r2_diff": api.stubs.diff_versions,
        "r4_version": api.stubs.get_version_detail, "r5_test_versions": api.stubs.list_test_versions,
        "r6_previous": api.stubs.previous_run_body, "r7_compare": api.stubs.compare_runs_ai,
        "r3_export": api.stubs.export_bundle,
    }[name]


# ── The goldens go through the response models unchanged ─────────────────────


@pytest.mark.parametrize("name,path,params", GETS)
def test_a_viewer_gets_the_golden_body_through_the_response_model(api, name, path, params):
    resp = api.http.get(path, params=params)
    assert resp.status_code == 200, resp.text
    assert resp.json() == _golden(name), f"{name}: the response model reshaped the real service output"
    _stub_for(api, name).assert_awaited_once()


@pytest.mark.parametrize("name,path,params", [g for g in GETS if g[0] in ("r1_list", "r2_diff", "r4_version",
                                                                         "r7_compare")])
def test_ai_reads_carry_the_review_headers(api, name, path, params):
    resp = api.http.get(path, params=params)
    assert resp.headers["X-TestLookup-AI-Generated"] == "true"
    assert resp.headers["X-TestLookup-Review-State"] == "pending_review"


def test_a_deterministic_run_summary_is_not_labelled_ai_generated(api):
    body = {**_service_body("r4_version"), "kind": "run_summary", "fallback_used": True, "pipeline_run_id": None}
    api.stubs.get_version_detail.side_effect = None
    api.stubs.get_version_detail.return_value = body
    resp = api.http.get(f"{BASE}/ai-history/{VERSION}")
    assert resp.status_code == 200 and resp.headers["X-TestLookup-AI-Generated"] == "false"
    assert resp.json()["requires_human_review"] is False


# ── Who gets what ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,path,params", GETS + [EXPORT])
def test_a_non_member_and_a_key_of_another_project_get_404_and_nothing_is_read(api, name, path, params):
    for user, member in ((_user("QA_LEAD"), False), (_user("ADMIN", bound_project=OTHER_PROJECT), True)):
        api.state.user, api.state.member = user, member
        resp = api.http.get(path, params=params)
        assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}, (name, user.role)
    _stub_for(api, name).assert_not_awaited()


@pytest.mark.parametrize("name,path,params", GETS + [EXPORT])
def test_an_admin_on_an_unknown_run_gets_404_not_the_service(api, name, path, params):
    api.state.user, api.state.runs = _user("ADMIN"), {}
    resp = api.http.get(path, params=params)
    assert resp.status_code == 404 and resp.json() == {"detail": "Test run not found"}
    _stub_for(api, name).assert_not_awaited()


@pytest.mark.parametrize("stub,path,params", [
    ("diff_versions", f"{BASE}/ai-history/diff", {"from": VERSION}),
    ("diff_versions", f"{BASE}/ai-history/diff", {"from": VERSION, "to": str(uuid.uuid4())}),
    ("get_version_detail", f"{BASE}/ai-history/{VERSION}", {}),
    ("compare_runs_ai", f"{BASE}/ai-compare", {"version_id": VERSION}),
    ("compare_runs_ai", f"{BASE}/ai-compare", {"baseline_version_id": VERSION}),
])
def test_a_version_of_another_run_is_404_with_one_body(api, stub, path, params):
    getattr(api.stubs, stub).side_effect = reads.AIVersionNotFound()
    resp = api.http.get(path, params=params)
    assert resp.status_code == 404 and resp.json() == {"detail": "AI version not found"}


def test_latest_on_an_empty_history_and_a_foreign_baseline_name_themselves(api):
    api.stubs.diff_versions.side_effect = reads.AIHistoryEmpty()
    resp = api.http.get(f"{BASE}/ai-history/diff", params={"from": VERSION, "to": "latest"})
    assert resp.status_code == 404 and resp.json() == {"detail": "AI history is empty for this run"}
    api.stubs.compare_runs_ai.side_effect = reads.BaselineRunNotFound()
    resp = api.http.get(f"{BASE}/ai-compare", params={"baseline_run_id": str(uuid.uuid4())})
    assert resp.status_code == 404 and resp.json() == {"detail": "Baseline run not found"}


def test_a_test_of_another_run_is_404(api):
    api.stubs.list_test_versions.side_effect = None
    api.stubs.list_test_versions.return_value = None
    resp = api.http.get(f"{BASE}/tests/{uuid.uuid4()}/ai-history")
    assert resp.status_code == 404 and resp.json() == {"detail": "Test case not found"}


@pytest.mark.parametrize("path,params", [
    (f"{BASE}/ai-history/not-a-uuid", {}),
    (f"{BASE}/ai-history/diff", {}),
    (f"{BASE}/ai-history/diff", {"from": "v1"}),
    (f"{BASE}/ai-history/diff", {"from": VERSION, "to": "v2"}),
    (f"{BASE}/ai-history/diff", {"from": VERSION, "to": "LATEST"}),
    (f"{BASE}/ai-compare", {"version_id": "1"}),
    (f"{BASE}/ai-compare", {"baseline_version_id": "x"}),
    (f"{BASE}/ai-compare", {"baseline_run_id": "nope"}),
    (f"{BASE}/ai-compare", {"baseline_run_id": str(RUN)}),
    (f"{BASE}/ai-history", {"kind": "digest"}),
    (f"{BASE}/ai-history", {"limit": 0}),
    (f"{BASE}/ai-history", {"limit": 101}),
    (f"{BASE}/tests/{TEST}/ai-history", {"limit": 51}),
    (f"{BASE}/previous", {"suite_name": "x" * 501}),
])
def test_malformed_ids_and_bounds_are_422_before_any_read(api, path, params):
    resp = api.http.get(path, params=params)
    assert resp.status_code == 422, (path, params, resp.text)
    for stub in vars(api.stubs).values():
        stub.assert_not_awaited()


@pytest.mark.parametrize("stub,path,params", [
    ("list_versions", f"{BASE}/ai-history", {}),
    ("diff_versions", f"{BASE}/ai-history/diff", {"from": VERSION}),
    ("get_version_detail", f"{BASE}/ai-history/{VERSION}", {}),
    ("compare_runs_ai", f"{BASE}/ai-compare", {"version_id": VERSION}),
    ("export_bundle", f"{BASE}/ai-history/export", {}),
])
def test_mongo_down_is_503_with_a_stable_code_and_no_audit_row(api, stub, path, params):
    getattr(api.stubs, stub).side_effect = AIHistoryUnavailable("ServerSelectionTimeoutError")
    resp = api.http.get(path, params=params)
    assert resp.status_code == 503 and resp.json() == {"detail": "ai_history_unavailable"}
    api.db.add.assert_not_called()
    api.db.commit.assert_not_awaited()


def test_the_query_params_reach_the_service_verbatim(api):
    baseline, version, baseline_version = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
    api.http.get(f"{BASE}/ai-compare", params={"baseline_run_id": str(baseline), "suite_name": "checkout",
                                               "version_id": version, "baseline_version_id": baseline_version})
    kwargs = api.stubs.compare_runs_ai.await_args.kwargs
    assert (kwargs["baseline_run_id"], kwargs["suite_name"], kwargs["version_id"], kwargs["baseline_version_id"]) == (
        baseline, "checkout", version, baseline_version,
    )
    assert kwargs["run"] is api.run and api.stubs.compare_runs_ai.await_args.args[1] is api.mongo
    api.http.get(f"{BASE}/ai-history/diff", params={"from": VERSION.upper(), "to": "live"})
    diff_kwargs = api.stubs.diff_versions.await_args.kwargs
    assert (diff_kwargs["from_id"], diff_kwargs["to_ref"]) == (VERSION, "live")
    api.http.get(f"{BASE}/ai-history", params={"kind": "run_summary", "limit": 100})
    assert api.stubs.list_versions.await_args.kwargs["kind"] == "run_summary"
    assert api.stubs.list_versions.await_args.kwargs["limit"] == 100
    api.http.get(f"{BASE}/previous", params={"suite_name": "payments"})
    assert api.stubs.previous_run_body.await_args.kwargs["suite_name"] == "payments"
    api.http.get(f"{BASE}/tests/{TEST}/ai-history", params={"limit": 5})
    assert api.stubs.list_test_versions.await_args.kwargs == {"run_id": RUN, "test_id": TEST, "limit": 5}


def test_no_get_writes_anything(api):
    for _name, path, params in GETS:
        assert api.http.get(path, params=params).status_code == 200
    api.db.add.assert_not_called()
    api.db.flush.assert_not_awaited()
    api.db.commit.assert_not_awaited()


# ── The export (E8.4) ────────────────────────────────────────────────────────


def _exports_counted() -> float:
    from app.core.metrics import report_exports_total

    return report_exports_total.labels(format="json", type="ai_history")._value.get()


def test_the_export_downloads_the_bundle_and_commits_its_audit_row(api):
    before = _exports_counted()
    resp = api.http.get(f"{BASE}/ai-history/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"] == 'attachment; filename="run-101-ai-history.json"'
    assert resp.headers["content-type"].startswith("application/json")
    assert json.loads(resp.text) == _golden("r3_export")
    entry = api.db.add.call_args.args[0]
    assert entry.action == "ai_report.distribution_would_refuse"
    assert entry.after_value["channel"] == "ai_history_export" and entry.project_id == PROJECT
    api.db.commit.assert_awaited_once()
    assert _exports_counted() == before + 1


def test_an_enforced_gate_refuses_the_export_and_still_commits_the_audit_row(api, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "REVIEW_GATE_ENFORCED", True)
    before = _exports_counted()
    resp = api.http.get(f"{BASE}/ai-history/export")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "report_pending_review"
    assert api.db.add.call_args.args[0].action == "ai_report.distribution_refused"
    api.db.commit.assert_awaited_once()
    assert _exports_counted() == before


def test_a_project_that_allows_drafts_gets_a_watermarked_export(api):
    api.state.allow_drafts = True
    body = json.loads(api.http.get(f"{BASE}/ai-history/export").text)
    assert body["draft_watermark"] == "DRAFT - AI-generated, not human-reviewed"
    assert api.db.add.call_args.args[0].action == "ai_report.distributed_unreviewed"


@pytest.mark.parametrize("build,expected", [
    ('rel"1\r\n/2', "run-rel_1___2-ai-history.json"),
    ("nightly/2026 10;x=y", "run-nightly_2026_10_x_y-ai-history.json"),
    ("b" * 80, f"run-{'b' * 60}-ai-history.json"),
    (None, f"run-{str(RUN)[:8]}-ai-history.json"),
])
def test_the_export_filename_cannot_inject_a_header_or_a_path(api, build, expected):
    from app.routers.ai_history import export_filename

    assert export_filename(build, RUN) == expected
    api.run.build_number = build
    resp = api.http.get(f"{BASE}/ai-history/export")
    assert resp.headers["content-disposition"] == f'attachment; filename="{expected}"'


# ── Registration ─────────────────────────────────────────────────────────────


def test_the_router_is_mounted_after_run_intelligence_with_its_literals_first():
    from app.bootstrap import PROTECTED_ROUTERS
    from app.routers import ai_history, run_intelligence

    assert PROTECTED_ROUTERS.index(ai_history.router) > PROTECTED_ROUTERS.index(run_intelligence.router)
    paths = [route.path for route in ai_history.router.routes]
    version_path = "/api/v1/runs/{run_id}/ai-history/{version_id}"
    assert paths.index("/api/v1/runs/{run_id}/ai-history/diff") < paths.index(version_path)
    assert paths.index("/api/v1/runs/{run_id}/ai-history/export") < paths.index(version_path)
    assert all(r.methods == {"GET"} for r in ai_history.router.routes)
