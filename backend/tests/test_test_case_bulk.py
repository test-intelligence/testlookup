"""W28 TCM-03: ``POST /api/v1/test-management/cases/bulk``.

The route authorises the body's ``project_id`` FIRST, refuses a retire move
(deprecate / archive / reinstate) below QA_LEAD for the WHOLE request (the
contract's AMENDMENT keeps those moves QA_LEAD or above), and 404s a status
change where lifecycle v2 is off. The service judges the request before it
loads anything, loads the cases in ONE statement filtered by the project (a
foreign id is ``not_found``, like an unknown one), and isolates every case in a
SAVEPOINT. The service tests drive the REAL ``transition()`` and the REAL edit
path (``update_managed_test_case``) through a session fake that keeps rows and
models a SAVEPOINT rollback; nothing here mocks the lifecycle.

PostgreSQL proof: ``tests/integration/test_test_case_editing_postgres.py``.
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models.postgres import (
    ManagedTestCase,
    ProjectActivityEvent,
    TestCaseAuditLog as AuditRow,
    TestCaseReview as ReviewRow,
    TestCaseVersion as VersionRow,
    User,
)
from app.models.schemas import TestCaseBulkRequest as BulkRequest, TestCaseBulkResponse as BulkResponse
from app.services import test_case_editing_service as editing
from app.services.test_case_editing_service import (
    BULK_ACTIONS,
    BULK_MAX_IDS,
    BULK_OUTCOMES,
    BULK_TRANSITIONS,
    PRIORITIES,
    apply_bulk_action,
)

PROJECT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
OTHER_PROJECT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")
_TRACKED = (
    "status", "version", "priority", "tags", "assignee_id", "suite_name",
    "test_suite_id", "deprecation_reason", "deprecated_at", "deprecated_by_id",
    "needs_update_reason", "edited_since_approval_version", "lifecycle_state_changed_at",
)


def actor(role: str = "QA_ENGINEER", **kw) -> SimpleNamespace:
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()), role=role, is_active=True,
        full_name=kw.pop("full_name", f"{role.title()} Person"), username=role.lower(), **kw,
    )


def make_case(status: str = "draft", *, version: int = 1, project_id=PROJECT, **kw) -> ManagedTestCase:
    return ManagedTestCase(
        id=kw.pop("id", uuid.uuid4()),
        project_id=project_id,
        title=kw.pop("title", f"Case {status}"),
        status=status,
        version=version,
        priority=kw.pop("priority", "medium"),
        severity="major",
        test_type="functional",
        tags=kw.pop("tags", ["smoke"]),
        is_automated=False,
        automation_status="not_automated",
        ai_generated=False,
        author_id=kw.pop("author_id", None),
        **kw,
    )


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalars(self):
        rows = self._rows
        return SimpleNamespace(all=lambda: list(rows), first=lambda: rows[0] if rows else None)

    def scalar_one_or_none(self):
        return self._scalar

    def scalar(self):
        return self._scalar

    def all(self):
        return list(self._rows)


class FakeSession:
    """Keeps cases, users, memberships and versions; models a SAVEPOINT.

    ``begin_nested()`` snapshots every case's tracked fields and the staged
    rows; leaving it with an exception restores both (what ROLLBACK TO
    SAVEPOINT does to the case rows and the version / audit inserts) and
    records the rollback.
    """

    def __init__(self, cases=(), *, users=(), members=(), versions=(), review_required=False):
        self.cases: dict[uuid.UUID, ManagedTestCase] = {c.id: c for c in cases}
        self.users = {u.id: u for u in users}
        self.members = set(members)  # (project_id, user_id)
        self.versions = list(versions)
        self.review_required = review_required
        self.added: list[Any] = []
        self.info: dict = {}
        self.statements: list[str] = []
        self.savepoints: list[dict] = []
        self.flushes = 0
        self.commits = 0

    # ── reads ──
    async def execute(self, stmt):
        text = " ".join(str(stmt).split())
        self.statements.append(text)
        params = stmt.compile().params
        values = list(params.values())
        if "FROM managed_test_cases" in text and "managed_test_cases.id IN" in text:
            ids = next(v for v in values if isinstance(v, (list, tuple)))
            pid = next((v for k, v in params.items() if k.startswith("project_id")), None)
            rows = [self.cases[i] for i in ids if i in self.cases and self.cases[i].project_id == pid]
            return _Result(sorted(rows, key=lambda c: c.id))
        if "FROM managed_test_cases" in text and "managed_test_cases.id =" in text:
            case = self.cases.get(next(v for v in values if isinstance(v, uuid.UUID)))
            return _Result([case] if case else [], scalar=case)
        if "FROM test_case_reviews" in text:
            return _Result([])
        if "FROM project_members" in text:
            pid = next(v for k, v in params.items() if k.startswith("project_id"))
            uid = next(v for k, v in params.items() if k.startswith("user_id"))
            return _Result(scalar=uid if (pid, uid) in self.members else None)
        if "projects.require_case_review" in text:
            return _Result(scalar=self.review_required)
        if "FROM test_case_versions" in text:
            number = next(v for k, v in params.items() if k.startswith("version"))
            case_id = next(v for k, v in params.items() if k.startswith("test_case_id"))
            hit = next(
                (v for v in self.versions if v.test_case_id == case_id and v.version == number), None
            )
            return _Result([hit] if hit else [], scalar=hit)
        raise AssertionError(f"unexpected statement: {text}")

    async def get(self, model, pk):
        if model is User:
            return self.users.get(pk)
        if model is ManagedTestCase:
            return self.cases.get(pk)
        return None

    # ── writes ──
    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    def begin_nested(self):
        session = self

        class _Savepoint:
            async def __aenter__(self_inner):
                self_inner.record = {"entered": True, "exited": False, "rolled_back": False}
                session.savepoints.append(self_inner.record)
                self_inner.added = len(session.added)
                self_inner.state = {
                    cid: {f: copy.deepcopy(getattr(c, f, None)) for f in _TRACKED}
                    for cid, c in session.cases.items()
                }
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                self_inner.record["exited"] = True
                if exc_type is not None:
                    self_inner.record["rolled_back"] = True
                    del session.added[self_inner.added:]
                    for cid, fields in self_inner.state.items():
                        for f, value in fields.items():
                            setattr(session.cases[cid], f, value)
                return False

        return _Savepoint()

    # ── what landed ──
    def of(self, model):
        return [o for o in self.added if isinstance(o, model)]


def request(case_ids, action="transition", **kw) -> BulkRequest:
    return BulkRequest(project_id=kw.pop("project_id", PROJECT), case_ids=case_ids, action=action, **kw)


def by_id(response: BulkResponse) -> dict[uuid.UUID, Any]:
    return {item.id: item for item in response.results}


# ═══════════════════════ vocabularies ═══════════════════════


def test_the_vocabularies_are_the_contract_s():
    assert BULK_MAX_IDS == 200
    assert BULK_ACTIONS == ("transition", "set_owner", "set_priority", "add_tags", "remove_tags", "move_to_suite")
    assert BULK_TRANSITIONS == ("activate", "revise", "flag_stale", "request_review", "deprecate", "archive", "reinstate")
    assert BULK_OUTCOMES == ("changed", "unchanged", "skipped", "not_found")
    assert PRIORITIES == ("critical", "high", "medium", "low")
    assert editing.RETIRE_TRANSITIONS == ("archive", "deprecate", "reinstate")
    assert BulkRequest.model_fields["case_ids"].metadata[-1].max_length == BULK_MAX_IDS


# ═══════════════════════ the route ═══════════════════════


@pytest.fixture
def client_as():
    """The real test-management router, signed in as ``state['user']``; the service patched."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management, test_management_editing as route_mod

    app = FastAPI()
    app.include_router(test_management.router)
    db = AsyncMock()

    async def _db():
        yield db

    state: dict[str, Any] = {"user": actor("QA_LEAD")}
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state["user"]
    answer = BulkResponse(
        action="set_priority", requested=1, changed=1, unchanged=0, skipped=0, not_found=0, results=[],
    )
    with (
        patch.object(route_mod, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None))) as scope,
        patch.object(route_mod, "apply_bulk_action", AsyncMock(return_value=answer)) as service,
        patch.object(route_mod, "project_requires_case_review", AsyncMock(return_value=False)) as review,
        patch.object(route_mod, "emit_staged_test_management_metrics", AsyncMock()),
        patch("app.services.feature_flags.is_enabled", AsyncMock(return_value=True)) as flag,
    ):
        yield SimpleNamespace(
            client=TestClient(app, raise_server_exceptions=False), state=state, db=db,
            scope=scope, service=service, review=review, flag=flag,
        )


URL = "/api/v1/test-management/cases/bulk"


def _body(**kw) -> dict:
    body = {"project_id": str(PROJECT), "case_ids": [str(uuid.uuid4())], "action": "set_priority", "priority": "high"}
    body.update(kw)
    return body


def test_the_route_resolves_the_body_project_first(client_as):
    client_as.scope.side_effect = HTTPException(status_code=403, detail="You do not have access to this project")
    # Even a retire move a TESTER may not make answers the scope's refusal first.
    client_as.state["user"] = actor("TESTER")
    resp = client_as.client.post(URL, json=_body(action="transition", transition="deprecate", reason="gone"))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You do not have access to this project"
    assert client_as.scope.await_args.args[2] == str(PROJECT)
    client_as.service.assert_not_awaited()
    client_as.flag.assert_not_awaited()
    assert client_as.db.commit.await_count == 0


def test_the_route_answers_the_service_result_and_commits(client_as):
    resp = client_as.client.post(URL, json=_body())
    assert resp.status_code == 200
    assert resp.json()["changed"] == 1
    client_as.service.assert_awaited_once()
    payload = client_as.service.await_args.args[1]
    assert payload.project_id == PROJECT and payload.action == "set_priority"
    assert client_as.service.await_args.kwargs == {"review_required": False}
    assert client_as.db.commit.await_count == 1
    client_as.flag.assert_not_awaited()  # a metadata action needs no lifecycle flag


def test_a_viewer_is_refused_before_anything(client_as):
    client_as.state["user"] = actor("VIEWER")
    resp = client_as.client.post(URL, json=_body())
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Requires at least TESTER role"
    client_as.scope.assert_not_awaited()
    client_as.service.assert_not_awaited()


@pytest.mark.parametrize("move", ["deprecate", "archive", "reinstate"])
@pytest.mark.parametrize("role", ["TESTER", "QA_ENGINEER"])
def test_a_retire_move_below_qa_lead_is_refused_for_the_whole_request(client_as, role, move):
    """The contract's AMENDMENT: Deprecate / Archive / Reinstate stay QA_LEAD+."""
    client_as.state["user"] = actor(role)
    resp = client_as.client.post(
        URL, json=_body(action="transition", transition=move, reason="retired", case_ids=[str(uuid.uuid4()) for _ in range(3)])
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "This action requires QA_LEAD or ADMIN"
    client_as.scope.assert_awaited_once()
    client_as.service.assert_not_awaited()
    assert client_as.db.commit.await_count == 0


@pytest.mark.parametrize("role", ["QA_LEAD", "ADMIN"])
def test_a_lead_may_ask_for_a_retire_move(client_as, role):
    client_as.state["user"] = actor(role)
    resp = client_as.client.post(URL, json=_body(action="transition", transition="deprecate", reason="retired"))
    assert resp.status_code == 200
    client_as.service.assert_awaited_once()


@pytest.mark.parametrize("move", ["activate", "revise", "request_review"])
def test_a_tester_may_ask_for_the_other_moves(client_as, move):
    client_as.state["user"] = actor("TESTER")
    resp = client_as.client.post(URL, json=_body(action="transition", transition=move))
    assert resp.status_code == 200
    client_as.service.assert_awaited_once()


def test_a_status_change_with_lifecycle_v2_off_is_404(client_as):
    client_as.flag.return_value = False
    resp = client_as.client.post(URL, json=_body(action="transition", transition="activate"))
    assert resp.status_code == 404
    assert resp.json()["detail"] == (
        "Direct test-case lifecycle endpoints are disabled for this project; "
        "use the compatibility review endpoints"
    )
    assert client_as.flag.await_args.kwargs["project_id"] == PROJECT
    client_as.service.assert_not_awaited()


def test_the_route_passes_the_project_s_review_setting(client_as):
    client_as.review.return_value = True
    resp = client_as.client.post(URL, json=_body(action="transition", transition="activate"))
    assert resp.status_code == 200
    assert client_as.review.await_args.args[1] == PROJECT
    assert client_as.service.await_args.kwargs == {"review_required": True}


def test_201_ids_are_422_and_reach_nothing(client_as):
    resp = client_as.client.post(URL, json=_body(case_ids=[str(uuid.uuid4()) for _ in range(BULK_MAX_IDS + 1)]))
    assert resp.status_code == 422
    client_as.scope.assert_not_awaited()
    client_as.service.assert_not_awaited()


def test_an_empty_id_list_is_422(client_as):
    resp = client_as.client.post(URL, json=_body(case_ids=[]))
    assert resp.status_code == 422
    client_as.service.assert_not_awaited()


def test_the_bulk_route_is_matched_before_any_case_id_route():
    from app.routers import test_management

    paths = [(getattr(r, "path", ""), sorted(getattr(r, "methods", []) or [])) for r in test_management.router.routes]
    bulk = paths.index(("/api/v1/test-management/cases/bulk", ["POST"]))
    first_case_id = min(i for i, (p, _) in enumerate(paths) if p.startswith("/api/v1/test-management/cases/{case_id}"))
    assert bulk < first_case_id


# ═══════════════════════ validation (before any load) ═══════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "detail"),
    [
        ({"action": "delete_everything"}, "Unknown bulk action: delete_everything"),
        ({"action": "transition"}, "Unknown bulk status change: (none)"),
        ({"action": "transition", "transition": "approve"}, "Unknown bulk status change: approve"),
        ({"action": "transition", "transition": "flag_stale"}, "reason is required for flag_stale"),
        ({"action": "transition", "transition": "flag_stale", "reason": "   "}, "reason is required for flag_stale"),
        ({"action": "transition", "transition": "flag_stale", "reason": "x" * 101},
         "reason must be at most 100 characters for flag_stale"),
        ({"action": "transition", "transition": "deprecate"}, "reason is required for deprecate"),
        ({"action": "transition", "transition": "archive"}, "reason is required for archive"),
        ({"action": "transition", "transition": "reinstate"}, "reason is required for reinstate"),
        ({"action": "set_priority", "priority": "urgent"}, "Unknown priority: urgent"),
        ({"action": "set_priority"}, "Unknown priority: None"),
        ({"action": "add_tags"}, "Name at least one tag"),
        ({"action": "add_tags", "tags": ["  ", ""]}, "Name at least one tag"),
        ({"action": "remove_tags", "tags": []}, "Name at least one tag"),
        ({"action": "add_tags", "tags": ["ok", "y" * 101]}, "A tag is at most 100 characters"),
        ({"action": "move_to_suite"}, "Name the suite to move to"),
        ({"action": "move_to_suite", "suite_name": "   "}, "Name the suite to move to"),
    ],
)
async def test_every_422_is_judged_before_any_load(fields, detail):
    db = FakeSession([make_case()])
    case_id = next(iter(db.cases))
    with pytest.raises(HTTPException) as exc:
        await apply_bulk_action(db, request([case_id], **fields), actor("QA_LEAD"), review_required=False)
    assert (exc.value.status_code, exc.value.detail) == (422, detail)
    assert db.statements == [] and db.added == [] and db.savepoints == []


@pytest.mark.asyncio
async def test_the_service_refuses_a_retire_move_below_qa_lead_too():
    """Defence in depth for a non-HTTP caller: the same 403, nothing loaded."""
    db = FakeSession([make_case("active")])
    with pytest.raises(HTTPException) as exc:
        await apply_bulk_action(
            db, request(list(db.cases), transition="deprecate", reason="gone"),
            actor("QA_ENGINEER"), review_required=False,
        )
    assert (exc.value.status_code, exc.value.detail) == (403, "This action requires QA_LEAD or ADMIN")
    assert db.statements == []


@pytest.mark.asyncio
async def test_an_owner_who_is_not_a_member_is_422_once_before_any_load():
    db = FakeSession([make_case()], users=[actor("TESTER", id=uuid.uuid4())])
    stranger = next(iter(db.users))
    with pytest.raises(HTTPException) as exc:
        await apply_bulk_action(
            db, request(list(db.cases), action="set_owner", assignee_id=stranger),
            actor(), review_required=False,
        )
    assert (exc.value.status_code, exc.value.detail) == (422, "The assignee must be an active member of this project.")
    # Only the membership read ran: no case was loaded.
    assert len(db.statements) == 1 and "FROM project_members" in db.statements[0]


# ═══════════════════════ per case ═══════════════════════


@pytest.mark.asyncio
async def test_activate_with_review_off_moves_draft_and_approved_and_skips_retired():
    draft, approved, retired = make_case("draft"), make_case("approved", version=4), make_case("deprecated", version=2)
    db = FakeSession([draft, approved, retired])
    response = await apply_bulk_action(
        db, request([draft.id, approved.id, retired.id], transition="activate"),
        actor("TESTER"), review_required=False,
    )
    results = by_id(response)
    assert (results[draft.id].outcome, results[draft.id].status, results[draft.id].version) == ("changed", "active", 2)
    assert (results[approved.id].outcome, results[approved.id].status, results[approved.id].version) == ("changed", "active", 5)
    assert results[retired.id].outcome == "skipped"
    assert results[retired.id].reason == "Not available from deprecated"
    assert (results[retired.id].status, results[retired.id].version) == ("deprecated", 2)
    assert (response.requested, response.changed, response.skipped, response.unchanged, response.not_found) == (3, 2, 1, 0, 0)
    assert retired.status == "deprecated" and retired.version == 2


@pytest.mark.asyncio
async def test_activate_with_review_on_skips_a_draft_with_the_blocked_reason():
    draft, approved = make_case("draft"), make_case("approved")
    db = FakeSession([draft, approved], review_required=True)
    response = await apply_bulk_action(
        db, request([draft.id, approved.id], transition="activate"), actor("TESTER"), review_required=True,
    )
    results = by_id(response)
    assert (results[draft.id].outcome, results[draft.id].reason) == ("skipped", "Not available from draft")
    assert results[approved.id].outcome == "changed"
    assert draft.status == "draft" and draft.version == 1


@pytest.mark.asyncio
async def test_a_stale_expected_version_is_skipped_with_the_exact_sentence():
    case = make_case("draft", version=3)
    db = FakeSession([case])
    response = await apply_bulk_action(
        db, request([case.id], transition="activate", expected_versions={case.id: 2}),
        actor("TESTER"), review_required=False,
    )
    item = response.results[0]
    assert (item.outcome, item.reason) == ("skipped", "Changed since you selected it (v2 → v3)")
    assert (item.version, item.status) == (3, "draft")
    assert case.status == "draft" and db.of(VersionRow) == []
    assert db.savepoints == []  # nothing was attempted


@pytest.mark.asyncio
async def test_a_matching_expected_version_is_accepted():
    case = make_case("draft", version=3)
    db = FakeSession([case])
    response = await apply_bulk_action(
        db, request([case.id], action="set_priority", priority="high", expected_versions={case.id: 3}),
        actor("TESTER"), review_required=False,
    )
    assert response.results[0].outcome == "changed" and case.priority == "high" and case.version == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["deprecated", "archived"])
@pytest.mark.parametrize(
    "fields",
    [
        {"action": "set_priority", "priority": "high"},
        {"action": "add_tags", "tags": ["regression"]},
        {"action": "remove_tags", "tags": ["smoke"]},
        {"action": "move_to_suite", "suite_name": "Checkout"},
        {"action": "set_owner"},
    ],
)
async def test_a_retired_case_under_a_metadata_action_is_skipped(status, fields):
    case = make_case(status)
    db = FakeSession([case])
    response = await apply_bulk_action(db, request([case.id], **fields), actor("TESTER"), review_required=False)
    item = response.results[0]
    assert (item.outcome, item.reason, item.status) == ("skipped", "Reinstate it to edit it", status)
    assert db.added == [] and db.savepoints == []


@pytest.mark.asyncio
async def test_an_equal_priority_is_unchanged_and_writes_no_version():
    same, other = make_case(priority="high"), make_case(priority="low")
    db = FakeSession([same, other])
    response = await apply_bulk_action(
        db, request([same.id, other.id], action="set_priority", priority="high"), actor("TESTER"), review_required=False,
    )
    results = by_id(response)
    assert (results[same.id].outcome, results[same.id].version) == ("unchanged", 1)
    assert results[other.id].outcome == "changed"
    assert same.version == 1
    assert [v.test_case_id for v in db.of(VersionRow)] == [other.id]
    assert len(db.savepoints) == 1


@pytest.mark.asyncio
async def test_tags_are_added_and_removed_per_case_deduplicated():
    a = make_case(tags=["smoke", "login"])
    b = make_case(tags=["regression"])
    c = make_case(tags=None)
    db = FakeSession([a, b, c])
    added = await apply_bulk_action(
        db, request([a.id, b.id, c.id], action="add_tags", tags=[" regression ", "smoke", "regression"]),
        actor("TESTER"), review_required=False,
    )
    assert a.tags == ["smoke", "login", "regression"]
    assert b.tags == ["regression", "smoke"]
    assert c.tags == ["regression", "smoke"]
    assert added.changed == 3

    db2 = FakeSession([a, b, c])
    removed = await apply_bulk_action(
        db2, request([a.id, b.id, c.id], action="remove_tags", tags=["smoke", "absent"]),
        actor("TESTER"), review_required=False,
    )
    assert a.tags == ["login", "regression"] and b.tags == ["regression"] and c.tags == ["regression"]
    assert removed.changed == 3

    db3 = FakeSession([a])
    unchanged = await apply_bulk_action(
        db3, request([a.id], action="remove_tags", tags=["absent"]), actor("TESTER"), review_required=False,
    )
    assert unchanged.results[0].outcome == "unchanged" and db3.added == []


@pytest.mark.asyncio
async def test_set_owner_assigns_a_member_and_unassigns_with_none():
    member = actor("TESTER", full_name="Priya Shah")
    member_user = User(id=member.id, username="priya", full_name="Priya Shah", email="p@example.invalid",
                       hashed_password="x", is_active=True, role="TESTER")
    case = make_case()
    db = FakeSession([case], users=[member_user], members=[(PROJECT, member.id)])
    response = await apply_bulk_action(
        db, request([case.id], action="set_owner", assignee_id=member.id), actor(), review_required=False,
    )
    assert response.results[0].outcome == "changed" and case.assignee_id == member.id
    event = db.of(ProjectActivityEvent)[0]
    assert event.context["change"] == "owner → Priya Shah"
    snapshot = db.of(VersionRow)[0]
    assert snapshot.assignee_id == member.id and snapshot.change_type == "bulk_updated"

    db2 = FakeSession([case])
    cleared = await apply_bulk_action(
        db2, request([case.id], action="set_owner", assignee_id=None), actor(), review_required=False,
    )
    assert cleared.results[0].outcome == "changed" and case.assignee_id is None
    assert db2.of(ProjectActivityEvent)[0].context["change"] == "owner → unassigned"
    assert not any("project_members" in s for s in db2.statements)  # unassigning reads no membership


@pytest.mark.asyncio
async def test_move_to_suite_goes_through_the_edit_path_s_suite_anchor():
    case = make_case(suite_name="Old")
    suite = SimpleNamespace(id=uuid.uuid4())
    db = FakeSession([case])
    with patch("app.services.test_suite_service.get_or_create_suite_by_name", AsyncMock(return_value=suite)) as anchor:
        response = await apply_bulk_action(
            db, request([case.id], action="move_to_suite", suite_name="  Checkout "), actor(), review_required=False,
        )
    assert response.results[0].outcome == "changed"
    assert (case.suite_name, case.test_suite_id) == ("Checkout", suite.id)
    assert anchor.await_args.args[1:] == (PROJECT, "Checkout")
    assert db.of(ProjectActivityEvent)[0].context["change"] == "suite → Checkout"


# ═══════════════════════ ids: duplicates, foreign, unknown ═══════════════════════


@pytest.mark.asyncio
async def test_duplicate_ids_collapse_in_request_order_and_foreign_ids_are_not_found():
    first, second = make_case(), make_case()
    foreign = make_case(project_id=OTHER_PROJECT)
    unknown = uuid.uuid4()
    db = FakeSession([first, second, foreign])
    response = await apply_bulk_action(
        db,
        request([second.id, foreign.id, second.id, unknown, first.id, foreign.id], action="set_priority", priority="high"),
        actor(), review_required=False,
    )
    assert [r.id for r in response.results] == [second.id, foreign.id, unknown, first.id]
    assert [r.outcome for r in response.results] == ["changed", "not_found", "not_found", "changed"]
    # A foreign id answers exactly like an unknown one.
    assert response.results[1].model_dump(exclude={"id"}) == response.results[2].model_dump(exclude={"id"})
    assert (response.requested, response.changed, response.not_found) == (4, 2, 2)
    assert foreign.priority == "medium" and foreign.version == 1
    loads = [s for s in db.statements if "managed_test_cases.id IN" in s]
    assert len(loads) == 1  # ONE load, filtered by the project
    assert "managed_test_cases.project_id =" in loads[0] and "FOR UPDATE" in loads[0]


# ═══════════════════════ versions, audit, one ledger row ═══════════════════════


@pytest.mark.asyncio
async def test_every_changed_case_gets_its_own_version_and_audit_row_and_one_ledger_row():
    cases = [make_case("draft") for _ in range(3)]
    cases.append(make_case("deprecated"))
    db = FakeSession(cases)
    user = actor("TESTER", full_name="Tess Tester")
    response = await apply_bulk_action(
        db, request([c.id for c in cases], transition="activate"), user, review_required=False,
    )
    versions = db.of(VersionRow)
    assert sorted(v.test_case_id for v in versions) == sorted(c.id for c in cases[:3])
    assert {v.change_type for v in versions} == {"activate"}
    assert {v.source for v in versions} == {"person"}
    audits = db.of(AuditRow)
    assert sorted(a.entity_id for a in audits) == sorted(c.id for c in cases[:3])
    events = db.of(ProjectActivityEvent)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "test_case.bulk_changed"
    assert event.category == "test_management"
    assert (event.entity_type, event.entity_id) == ("test_library", str(PROJECT))
    assert event.project_id == PROJECT
    assert event.entity_label == "3 test cases"
    assert event.context == {"count": 3, "change": "status → active", "skipped": 1, "not_found": 0}
    assert event.diff == {"changed_fields": ["status"]}
    assert event.summary == "Bulk edit of 3 test cases: status → active"
    assert event.actor_name == "Tess Tester"
    assert response.changed == 3


@pytest.mark.asyncio
async def test_a_metadata_bulk_snapshot_says_bulk_updated_and_names_the_change():
    case = make_case(priority="low")
    db = FakeSession([case])
    await apply_bulk_action(db, request([case.id], action="set_priority", priority="critical"), actor(), review_required=False)
    (snapshot,) = db.of(VersionRow)
    assert (snapshot.change_type, snapshot.change_summary, snapshot.changed_fields) == (
        "bulk_updated", "Bulk edit: priority → critical", ["priority"],
    )
    (audit,) = db.of(AuditRow)
    assert audit.action == "updated" and audit.new_values == {"priority": "critical"}
    (event,) = db.of(ProjectActivityEvent)
    assert event.diff == {"changed_fields": ["priority"]}


@pytest.mark.asyncio
async def test_no_ledger_row_when_nothing_changed():
    retired, same = make_case("deprecated"), make_case(priority="high")
    db = FakeSession([retired, same])
    response = await apply_bulk_action(
        db, request([retired.id, same.id, uuid.uuid4()], action="set_priority", priority="high"),
        actor(), review_required=False,
    )
    assert (response.changed, response.skipped, response.unchanged, response.not_found) == (0, 1, 1, 1)
    assert db.added == []


@pytest.mark.asyncio
async def test_a_lead_s_bulk_deprecate_carries_the_reason_to_every_case():
    a, b = make_case("active"), make_case("draft")
    db = FakeSession([a, b])
    response = await apply_bulk_action(
        db, request([a.id, b.id], transition="deprecate", reason="  Replaced by the new checkout  "),
        actor("QA_LEAD"), review_required=False,
    )
    assert response.changed == 2
    assert a.status == b.status == "deprecated"
    assert a.deprecation_reason == b.deprecation_reason == "Replaced by the new checkout"
    assert db.of(ProjectActivityEvent)[0].context["change"] == "status → deprecated"


@pytest.mark.asyncio
async def test_request_review_opens_a_review_per_case():
    a, b = make_case("draft"), make_case("rejected")
    db = FakeSession([a, b])
    response = await apply_bulk_action(db, request([a.id, b.id], transition="request_review"), actor("TESTER"), review_required=True)
    assert response.changed == 2 and a.status == b.status == "review_requested"
    assert sorted(r.test_case_id for r in db.of(ReviewRow)) == sorted([a.id, b.id])


# ═══════════════════════ SAVEPOINT isolation ═══════════════════════


@pytest.mark.asyncio
async def test_the_second_of_three_failing_inside_transition_rolls_back_only_itself():
    cases = [make_case("draft") for _ in range(3)]
    db = FakeSession(cases)
    real_transition = editing.transition

    async def _flaky(session, case_id, action, user, **kwargs):
        result = await real_transition(session, case_id, action, user, **kwargs)
        if case_id == cases[1].id:
            # Fails AFTER the lifecycle staged its status, version, snapshot and audit row.
            raise HTTPException(status_code=409, detail="A pending review already exists")
        return result

    with patch.object(editing, "transition", _flaky):
        response = await apply_bulk_action(
            db, request([c.id for c in cases], transition="activate"), actor("TESTER"), review_required=False,
        )

    assert [r.outcome for r in response.results] == ["changed", "skipped", "changed"]
    assert response.results[1].reason == "A pending review already exists"
    assert (response.results[1].version, response.results[1].status) == (1, "draft")
    assert [c.status for c in cases] == ["active", "draft", "active"]
    assert [c.version for c in cases] == [2, 1, 2]
    assert [sp["rolled_back"] for sp in db.savepoints] == [False, True, False]
    assert all(sp["entered"] and sp["exited"] for sp in db.savepoints)
    assert sorted(v.test_case_id for v in db.of(VersionRow)) == sorted([cases[0].id, cases[2].id])
    assert sorted(a.entity_id for a in db.of(AuditRow)) == sorted([cases[0].id, cases[2].id])
    assert db.of(ProjectActivityEvent)[0].context["count"] == 2


@pytest.mark.asyncio
async def test_a_database_refusal_inside_a_case_is_that_case_s_skip():
    from sqlalchemy.exc import IntegrityError

    cases = [make_case("draft") for _ in range(2)]
    db = FakeSession(cases)
    real_flush = db.flush
    calls = {"n": 0}

    async def _flush():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        await real_flush()

    db.flush = _flush  # type: ignore[method-assign]
    response = await apply_bulk_action(
        db, request([c.id for c in cases], action="set_priority", priority="high"), actor(), review_required=False,
    )
    assert [r.outcome for r in response.results] == ["skipped", "changed"]
    assert response.results[0].reason == "This case could not be changed"
    assert [c.priority for c in cases] == ["medium", "high"]


@pytest.mark.parametrize(
    ("detail", "sentence"),
    [
        ("Cannot edit a deprecated test case", "Cannot edit a deprecated test case"),
        ({"current_version": 4, "expected_version": 3, "message": "Test case changed; refresh before applying this action"},
         "Test case changed; refresh before applying this action"),
        ({"current_state": "archived", "attempted_action": "activate", "allowed_actions": []}, "Not available from archived"),
        ({"something": "else"}, "This case could not be changed"),
        ("", "This case could not be changed"),
    ],
)
def test_a_refusal_becomes_one_sentence(detail, sentence):
    assert editing._skip_sentence(HTTPException(status_code=409, detail=detail)) == sentence
