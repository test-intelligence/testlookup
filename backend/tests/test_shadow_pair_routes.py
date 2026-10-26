"""W16 AGT-13: the shadow-pair list / label routes and ``include_live_labels``.

IDOR first: the label route reaches a pair by id ALONE, so a pair of a project
the caller cannot reach answers the SAME 404 as a random id (no existence
oracle); the list checks its QUERY ``project_id`` before anything is read. Both
are QA lead+, interactive login only, and the label refuses a synthetic account.
The REAL ``shadow_pair_service.label_pair`` runs over a scripted session that
applies its conditional UPDATE, so ``labelled_by`` and the activity row are the
route's real write.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.sql.dml import Update

from app.core import deps
from app.core.deps import CREDENTIAL_KIND_API_KEY, CREDENTIAL_KIND_JWT, _bind_credential_kind, get_current_active_user
from app.db.postgres import get_db
from app.models.postgres import AIEvalShadowPair
from app.routers import ai_evaluation as router

PROJECT_A = uuid.UUID("aa11bb22-cc33-4d44-8e55-ff6677889900")
PROJECT_B = uuid.UUID("bb22cc33-dd44-4e55-8f66-a07788990011")
CREATED = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)


def _pair(project=PROJECT_A, *, agent="agent.root_cause_analysis.v1", status="pending") -> AIEvalShadowPair:
    return AIEvalShadowPair(
        id=uuid.uuid4(), project_id=project, agent_id=agent, sample_key=f"root_cause:{uuid.uuid4()}",
        incumbent_tier="llm", candidate_tier="slm",
        incumbent_output={"failure_category": "PRODUCT_BUG"}, candidate_output={"failure_category": "FLAKY"},
        incumbent_tokens=40, candidate_tokens=12, total_tokens=52, label_status=status, created_at=CREATED,
    )


class Session:
    """Scripted reads; APPLIES the label UPDATE to the pair it was handed (the row in the DB)."""

    def __init__(self, *results, pair=None, source_rows=()):
        self._results = list(results)
        self.pair = pair
        self.source_rows = source_rows  # (kind, id, label, run_id, build_number) rows of the source read
        self.statements: list = []
        self.calls: list = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        if isinstance(statement, Update):
            if self.pair is None or self.pair.label_status != "pending":
                return SimpleNamespace(rowcount=0)
            values = statement.compile().params
            for key in ("label", "label_status", "labelled_by", "labelled_at"):
                setattr(self.pair, key, values[key])
            return SimpleNamespace(rowcount=1)
        result = self._results.pop(0) if self._results else None
        rows = list(self.source_rows) if result is None else []
        return SimpleNamespace(scalar_one_or_none=lambda: result, all=lambda: rows)

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.pair.label_status if self.pair is not None else None

    def add(self, obj):
        self.calls.append(("add", obj))

    async def flush(self):
        self.calls.append("flush")

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")

    async def refresh(self, obj):
        self.calls.append("refresh")


def _user(role="QA_LEAD", kind=CREDENTIAL_KIND_JWT, **kw):
    user = SimpleNamespace(id=uuid.UUID("c0ffee00-1a2b-4c3d-8e4f-5a6b7c8d9e0f"), role=role, is_active=True,
                           api_key_project_id=None, username="lead.a", full_name="Lead A", is_synthetic=False)
    for key, value in kw.items():
        setattr(user, key, value)
    return _bind_credential_kind(user, kind) if kind else user


@pytest.fixture
def members(monkeypatch):
    """The caller is a member of PROJECT_A only (``None`` = an instance admin)."""
    state = {"accessible": {PROJECT_A}}

    async def _accessible(db, user):
        return state["accessible"]

    monkeypatch.setattr(deps, "get_accessible_project_ids", _accessible)
    return state


@pytest.fixture
def activity(monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr(router, "record_activity", recorded)
    return recorded


@pytest.fixture
def client(members):
    app = FastAPI()
    app.include_router(router.router)
    state = {"user": _user()}
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]

    def make(session: Session) -> AsyncClient:
        async def _db():
            yield session

        app.dependency_overrides[get_db] = _db
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    return make, state


def _label_path(pair_id) -> str:
    return f"/api/v1/ai-eval/shadow-pairs/{pair_id}/label"


VERDICT = {"verdict": "candidate", "failure_category": "PRODUCT_BUG"}


# ── IDOR ─────────────────────────────────────────────────────────────────────


async def test_another_projects_pair_is_the_same_404_as_a_random_id(client, activity):
    make, _ = client
    foreign = _pair(PROJECT_B)
    foreign_session = Session(foreign, pair=foreign)
    missing_session = Session(None)
    async with make(foreign_session) as http:
        foreign_resp = await http.post(_label_path(foreign.id), json=VERDICT)
    async with make(missing_session) as http:
        missing_resp = await http.post(_label_path(uuid.uuid4()), json=VERDICT)
    assert foreign_resp.status_code == missing_resp.status_code == 404
    assert foreign_resp.content == missing_resp.content  # byte-identical: no existence oracle
    assert foreign_resp.json() == {"detail": {"code": "shadow_pair_not_found", "message": "Shadow pair not found."}}
    assert foreign.label_status == "pending" and foreign.labelled_by is None
    assert foreign_session.calls == [] and missing_session.calls == []
    activity.assert_not_awaited()


async def test_an_instance_admin_reaches_any_projects_pair(client, members, activity):
    members["accessible"] = None
    make, state = client
    state["user"] = _user("ADMIN")
    pair = _pair(PROJECT_B)
    async with make(Session(pair, pair=pair)) as http:
        resp = await http.post(_label_path(pair.id), json=VERDICT)
    assert resp.status_code == 200, resp.text


async def test_the_list_checks_its_query_project_before_reading(client, monkeypatch):
    from app.services import shadow_pair_service

    listed = AsyncMock()
    monkeypatch.setattr(shadow_pair_service, "list_pairs", listed)
    make, _ = client
    session = Session()
    async with make(session) as http:
        resp = await http.get("/api/v1/ai-eval/shadow-pairs", params={"project_id": str(PROJECT_B)})
    assert resp.status_code == 403 and resp.json() == {"detail": "You do not have access to this project"}
    listed.assert_not_awaited()
    assert session.statements == []


# ── who may call ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method, path", [
    ("GET", f"/api/v1/ai-eval/shadow-pairs?project_id={PROJECT_A}"),
    ("POST", "LABEL"),
])
async def test_an_api_key_is_refused_whatever_its_role(client, activity, method, path):
    make, state = client
    state["user"] = _user("ADMIN", kind=CREDENTIAL_KIND_API_KEY)
    pair = _pair()
    session = Session(pair, pair=pair)
    async with make(session) as http:
        if path == "LABEL":
            resp = await http.post(_label_path(pair.id), json=VERDICT)
        else:
            resp = await http.get(path)
    assert resp.status_code == 403
    assert resp.json()["detail"] == {
        "code": "interactive_login_required",
        "message": "Labelling shadow pairs requires an interactive login; API keys are refused.",
    }
    assert pair.label_status == "pending" and "commit" not in session.calls


async def test_a_synthetic_account_cannot_label(client, activity):
    make, state = client
    state["user"] = _user(is_synthetic=True)
    pair = _pair()
    session = Session(pair, pair=pair)
    async with make(session) as http:
        resp = await http.post(_label_path(pair.id), json=VERDICT)
    assert resp.status_code == 403
    assert resp.json()["detail"] == {
        "code": "synthetic_account", "message": "Synthetic accounts cannot label shadow pairs."}
    assert pair.label_status == "pending" and session.calls == []


@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER"])
async def test_below_qa_lead_is_refused_before_the_pair_is_read(client, role):
    make, state = client
    state["user"] = _user(role)
    session = Session(_pair())
    async with make(session) as http:
        label = await http.post(_label_path(uuid.uuid4()), json=VERDICT)
        listing = await http.get("/api/v1/ai-eval/shadow-pairs", params={"project_id": str(PROJECT_A)})
    assert label.status_code == listing.status_code == 403
    assert "Requires at least QA_LEAD" in label.text
    assert session.statements == []


# ── the label: one commit, labelled_by, the activity row ─────────────────────


async def test_a_label_is_persisted_with_its_labeller_and_activity_in_one_commit(client, activity):
    make, state = client
    pair = _pair()
    case_id, run_id = uuid.uuid4(), uuid.uuid4()
    pair.sample_key = f"root_cause:{case_id}"
    session = Session(pair, pair=pair, source_rows=[("test_case", case_id, "test_checkout_retry", run_id, "b412")])
    activity.side_effect = lambda *a, **k: session.calls.append("activity")
    async with make(session) as http:
        resp = await http.post(_label_path(pair.id), json={**VERDICT, "notes": "the slm got the flake right"})
    assert resp.status_code == 200, resp.text
    # the REAL label_pair's conditional UPDATE: only a still-pending row is taken
    assert (pair.label_status, pair.labelled_by) == ("human_labelled", state["user"].id)
    assert pair.label == {"verdict": "candidate", "failure_category": "PRODUCT_BUG",
                          "notes": "the slm got the flake right", "source": "human"}
    assert session.calls == ["flush", "refresh", "activity", "commit", "refresh"]  # ONE commit
    body = resp.json()
    assert (body["id"], body["label_status"], body["labelled_by_name"]) == (str(pair.id), "human_labelled", "Lead A")
    assert body["expires_at"] is None  # a labelled pair no longer expires
    # the subject is resolved inside the pair's OWN project, after the commit
    assert body["source"] == {"kind": "test_case", "id": str(case_id), "label": "test_checkout_retry",
                              "run_id": str(run_id), "build_number": "b412", "href": f"/runs/{run_id}?tab=tests"}
    source_sql = str(session.statements[-1].compile(compile_kwargs={"literal_binds": False}))
    assert "test_runs.project_id" in source_sql
    kwargs = activity.await_args.kwargs
    assert activity.await_args.args[0] is session
    assert kwargs["event_type"] == "ai_eval.shadow_pair_labelled"
    assert (kwargs["project_id"], kwargs["entity_id"], kwargs["entity_label"]) == (
        PROJECT_A, PROJECT_A, "Root-cause analysis")
    assert kwargs["context"] == {"pair_id": str(pair.id), "agent_id": "agent.root_cause_analysis.v1",
                                 "verdict": "candidate", "label_status": "human_labelled"}
    assert "notes" not in str(kwargs["context"])  # a person's notes never reach the feed


@pytest.mark.parametrize("pair_kw, body, status, code, message", [
    ({"status": "golden_match"}, VERDICT, 409, "shadow_pair_already_labelled",
     "This pair is already labelled (golden_match)."),
    ({}, {"verdict": "maybe"}, 422, "verdict_invalid", "verdict must be incumbent, candidate, both or neither."),
    ({}, {"verdict": "both", "failure_category": "COSMIC_RAY"}, 422, "failure_category_invalid",
     "failure_category must be one of PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY, UNKNOWN."),
    ({"agent": "agent.summary.v1"}, VERDICT, 422, "failure_category_not_applicable",
     "failure_category applies to root-cause pairs only."),
])
async def test_a_refused_label_writes_nothing(client, activity, pair_kw, body, status, code, message):
    make, _ = client
    pair = _pair(**pair_kw)
    session = Session(pair, pair=pair)
    async with make(session) as http:
        resp = await http.post(_label_path(pair.id), json=body)
    assert resp.status_code == status
    assert resp.json()["detail"] == {"code": code, "message": message}
    assert "commit" not in session.calls and pair.labelled_by is None
    activity.assert_not_awaited()


# ── the list ─────────────────────────────────────────────────────────────────


async def test_the_list_passes_its_filters_inside_the_project(client, monkeypatch):
    from app.models.schemas import ShadowPairListResponse
    from app.services import shadow_pair_service

    listed = AsyncMock(return_value=ShadowPairListResponse(
        items=[], total=0, pending_total=3, live_tier_pairs_seen=True))
    monkeypatch.setattr(shadow_pair_service, "list_pairs", listed)
    make, _ = client
    session = Session()
    async with make(session) as http:
        resp = await http.get("/api/v1/ai-eval/shadow-pairs", params={
            "project_id": str(PROJECT_A), "agent_id": "agent.summary.v1", "label_status": "all",
            "limit": 10, "offset": 20})
        bad_agent = await http.get("/api/v1/ai-eval/shadow-pairs", params={
            "project_id": str(PROJECT_A), "agent_id": "agent.triage.v1"})
        bad_status = await http.get("/api/v1/ai-eval/shadow-pairs", params={
            "project_id": str(PROJECT_A), "label_status": "skipped"})
        no_project = await http.get("/api/v1/ai-eval/shadow-pairs")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "pending_total": 3, "live_tier_pairs_seen": True}
    assert listed.await_args.kwargs == {"project_id": PROJECT_A, "agent_id": "agent.summary.v1",
                                        "label_status": "all", "limit": 10, "offset": 20}
    assert listed.await_args.args == (session,)
    assert bad_agent.json()["detail"] == {
        "code": "agent_id_invalid", "message": "agent_id must be agent.root_cause_analysis.v1 or agent.summary.v1."}
    assert bad_status.json()["detail"] == {
        "code": "label_status_invalid", "message": "label_status must be pending, human_labelled, golden_match or all."}
    assert (bad_agent.status_code, bad_status.status_code, no_project.status_code) == (422, 422, 422)
    assert listed.await_count == 1 and session.calls == []


# ── include_live_labels (the direct-call shape of tests/test_tier_comparison_router.py) ──


def _tier_body(**kw) -> router.TierComparisonRequest:
    return router.TierComparisonRequest(
        project_id=PROJECT_A, agent_id="agent.summary.v1", capability="summary",
        incumbent_tier="llm", candidate_tier="slm", pairs=[], **kw,
    )


@pytest.fixture
def tier_route(monkeypatch):
    from app.services import shadow_pair_service, tier_comparison_service

    monkeypatch.setattr(router, "_authorize_body_project", AsyncMock())
    persisted = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(tier_comparison_service, "persist_tier_comparison", persisted)
    loaded = AsyncMock(return_value=[
        tier_comparison_service.LabelledPair(
            pair_id=f"pA{i}", verdict="candidate", label_status="human_labelled",
            incumbent_output={"summary": "x"}, candidate_output={"summary": "y"})
        for i in range(3)
    ])
    monkeypatch.setattr(shadow_pair_service, "labelled_pairs_for", loaded)
    return loaded


async def test_include_live_labels_scores_the_projects_labelled_pairs(tier_route, activity):
    db = SimpleNamespace(commit=AsyncMock())
    result = await router.run_tier_comparison(
        _tier_body(include_live_labels=True), current_user=SimpleNamespace(id=uuid.uuid4()), db=db,
    )
    assert tier_route.await_args.args == (db,)
    assert tier_route.await_args.kwargs == {"project_id": PROJECT_A, "agent_id": "agent.summary.v1",
                                            "incumbent_tier": "llm", "candidate_tier": "slm"}
    assert result["live_labelled_pairs"] == 3 and result["sample_count"] == 3
    assert {row["provenance"] for row in result["scored_pairs"]} == {"human_labelled"}
    assert activity.await_args.kwargs["context"]["live_labelled_pairs"] == 3
    db.commit.assert_awaited_once()


async def test_without_include_live_labels_no_pair_is_read(tier_route, activity):
    result = await router.run_tier_comparison(
        _tier_body(persist=False), current_user=SimpleNamespace(id=uuid.uuid4()), db=SimpleNamespace(),
    )
    tier_route.assert_not_awaited()
    assert "live_labelled_pairs" not in result  # today's answer, byte-identical
    assert result["sample_count"] == 0


async def test_asking_for_live_labels_and_finding_none_says_zero(tier_route, activity):
    tier_route.return_value = []
    result = await router.run_tier_comparison(
        _tier_body(include_live_labels=True, persist=False),
        current_user=SimpleNamespace(id=uuid.uuid4()), db=SimpleNamespace(),
    )
    assert result["live_labelled_pairs"] == 0
