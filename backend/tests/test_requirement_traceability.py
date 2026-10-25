"""W13 TCL-06 (D10 "lite"): manual requirement keys on test cases.

1. ``normalize_requirement_key`` table; a key with a TAB / NUL is refused.
2. ``add_link``: its four sentences; the same key in another letter case is the
   409; a retired case is the 409; ``test_case.requirement_linked`` is staged.
3. ``remove_link`` of another case's link answers exactly as an unknown id.
4. The four routes: TESTER adds, VIEWER is 403, another project's case is 403
   (``require_case_access``), the flag off is 404 ``TRACEABILITY_OFF`` on all four.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core import deps
from app.core.deps import get_current_active_user, get_db
from app.core.test_library_vocab import (
    REQUIREMENT_CASE_RETIRED,
    REQUIREMENT_KEY_INVALID,
    REQUIREMENT_LINK_NOT_FOUND,
    REQUIREMENT_LINK_TYPE_INVALID,
    TRACEABILITY_NEEDS_PROJECT,
    TRACEABILITY_OFF,
    normalize_requirement_key,
)
from app.models.postgres import ManagedTestCase, ProjectActivityEvent, UserRole
from app.models.postgres import TestCaseRequirementLink as Link
from app.models.schemas import RequirementLinkCreate
from app.routers import test_management_library as routes
from app.services import feature_flags
from app.services import requirement_traceability_service as svc


@pytest.mark.parametrize("raw, expected", [
    ("[REQ-12]", "REQ-12"), ("(REQ-12)", "REQ-12"), (" req-12 ", "REQ-12"), ("REQ  12", "REQ 12"),
    ("", ""), ("[]", ""), ("[[REQ-1]]", "[REQ-1]"), ("req-pay-07", "REQ-PAY-07"),
])
def test_the_one_normaliser(raw, expected):
    assert normalize_requirement_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "[]", "REQ\t12", "REQ\x0012", "x" * 201, None])
def test_a_key_that_is_empty_too_long_or_holds_a_control_character_is_refused(raw):
    with pytest.raises(HTTPException) as exc:
        svc.validated_key(raw)
    assert (exc.value.status_code, exc.value.detail) == (422, REQUIREMENT_KEY_INVALID)


class _Result:
    def __init__(self, scalar=None, one=None, rows=()):
        self._scalar, self._one, self._rows = scalar, one, list(rows)

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._one

    def all(self):
        return list(self._rows)


class _Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, answers=(), case=None):
        self.answers, self.case = list(answers), case
        self.added: list = []
        self.deleted: list = []
        self.commits = 0
        self.execute = AsyncMock(side_effect=lambda *a, **k: self.answers.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        return None

    def begin_nested(self):
        return _Savepoint()

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)

    async def get(self, model, ident):
        return self.case if model is ManagedTestCase and self.case and self.case.id == ident else None


def _case(status="active", project_id=None):
    return SimpleNamespace(id=uuid.uuid4(), project_id=project_id or uuid.uuid4(), status=status,
                           title="Pay by card")


def _actor(role=UserRole.TESTER):
    return SimpleNamespace(id=uuid.uuid4(), role=role.value, is_active=True, username="tess", full_name="Tess T",
                           email="t@x.test", is_service_account=False)


def _payload(key="REQ-PAY-07", link_type="verifies", title=None):
    return SimpleNamespace(requirement_key=key, link_type=link_type, requirement_title=title)


async def test_add_link_stages_the_link_and_its_activity_row():
    case, actor = _case(), _actor()
    db = _Session([_Result(scalar=None)])
    link = await svc.add_link(db, case, _payload(key=" req-pay-07 ", title="Card\tpayments"), actor)
    assert (link.requirement_key, link.requirement_key_norm, link.link_type) == ("req-pay-07", "REQ-PAY-07", "verifies")
    assert link.project_id == case.project_id and link.test_case_id == case.id and link.created_by_id == actor.id
    assert link.requirement_title == "Card payments"                     # a control character never stored
    events = [o for o in db.added if isinstance(o, ProjectActivityEvent)]
    assert [e.event_type for e in events] == ["test_case.requirement_linked"]
    assert events[0].context == {"requirement_key": "req-pay-07", "link_type": "verifies"}
    assert events[0].entity_id == str(case.id) and events[0].entity_label == "Pay by card"
    assert db.commits == 0                                               # the router commits


@pytest.mark.parametrize("payload, status_code, detail", [
    (_payload(key="REQ\x0012"), 422, REQUIREMENT_KEY_INVALID),
    (_payload(link_type="covers"), 422, REQUIREMENT_LINK_TYPE_INVALID),
])
async def test_add_link_refuses_a_bad_key_or_link_type_before_any_statement(payload, status_code, detail):
    db = _Session()
    with pytest.raises(HTTPException) as exc:
        await svc.add_link(db, _case(), payload, _actor())
    assert (exc.value.status_code, exc.value.detail) == (status_code, detail)
    db.execute.assert_not_awaited()


async def test_a_retired_case_and_an_existing_key_are_409():
    with pytest.raises(HTTPException) as exc:
        await svc.add_link(_Session(), _case(status="deprecated"), _payload(), _actor())
    assert (exc.value.status_code, exc.value.detail) == (409, REQUIREMENT_CASE_RETIRED)
    db = _Session([_Result(scalar=uuid.uuid4())])
    with pytest.raises(HTTPException) as exc:
        await svc.add_link(db, _case(), _payload(key="req-pay-07"), _actor())    # REQ-PAY-07 already on it
    assert (exc.value.status_code, exc.value.detail) == (409, "This case already links REQ-PAY-07.")
    assert not db.added


async def test_remove_link_of_another_case_is_the_unknown_id_404():
    db = _Session([_Result(one=None)])
    case = _case()
    with pytest.raises(HTTPException) as exc:
        await svc.remove_link(db, case, uuid.uuid4(), _actor())
    assert (exc.value.status_code, exc.value.detail) == (404, REQUIREMENT_LINK_NOT_FOUND)
    compiled = db.execute.await_args_list[0].args[0].compile()
    assert "test_case_requirement_links.test_case_id = " in str(compiled)
    assert case.id in compiled.params.values()                           # loaded WITH the path case


async def test_remove_link_stages_the_delete_and_its_activity_row():
    case = _case()
    link = Link(id=uuid.uuid4(), project_id=case.project_id, test_case_id=case.id,
                                   requirement_key="REQ-1", requirement_key_norm="REQ-1", link_type="related")
    db = _Session([_Result(one=link)])
    await svc.remove_link(db, case, link.id, _actor())
    assert db.deleted == [link]
    events = [o for o in db.added if isinstance(o, ProjectActivityEvent)]
    assert [(e.event_type, e.context) for e in events] == [
        ("test_case.requirement_unlinked", {"requirement_key": "REQ-1", "link_type": "related"}),
    ]


# ── 4. the routes ────────────────────────────────────────────────────────────


@pytest.fixture
def flag(monkeypatch):
    state = {"on": True}

    async def _is_enabled(key, **kwargs):
        return state["on"]

    monkeypatch.setattr(feature_flags, "is_enabled", _is_enabled)
    return state


def _client(session, user, monkeypatch, accessible):
    monkeypatch.setattr(deps, "get_accessible_project_ids", AsyncMock(return_value=accessible))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/test-management")

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _base(case_id):
    return f"/api/v1/test-management/cases/{case_id}/requirements"


async def test_a_tester_adds_and_the_router_commits_once(monkeypatch, flag):
    case = _case()
    session = _Session([_Result(scalar=None)], case=case)
    async with _client(session, _actor(), monkeypatch, {case.project_id}) as client:
        res = await client.post(_base(case.id), json={"requirement_key": "req-pay-07", "link_type": "partially_verifies"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["normalized_key"], body["link_type"], body["created_by_name"]) == (
        "REQ-PAY-07", "partially_verifies", "Tess T")
    assert session.commits == 1


async def test_a_viewer_is_refused_before_the_case_is_read(monkeypatch, flag):
    case = _case()
    session = _Session(case=case)
    async with _client(session, _actor(UserRole.VIEWER), monkeypatch, {case.project_id}) as client:
        add = await client.post(_base(case.id), json={"requirement_key": "REQ-1"})
        remove = await client.delete(f"{_base(case.id)}/{uuid.uuid4()}")
    assert add.status_code == 403 and add.json()["detail"] == "Requires at least TESTER role"
    assert remove.status_code == 403
    assert session.commits == 0 and not session.added


async def test_another_projects_case_is_403_on_every_case_route(monkeypatch, flag):
    case = _case()
    session = _Session(case=case)
    async with _client(session, _actor(), monkeypatch, {uuid.uuid4()}) as client:
        answers = [
            await client.get(_base(case.id)),
            await client.post(_base(case.id), json={"requirement_key": "REQ-1"}),
            await client.delete(f"{_base(case.id)}/{uuid.uuid4()}"),
        ]
    assert [r.status_code for r in answers] == [403, 403, 403]
    assert {r.json()["detail"] for r in answers} == {"You do not have access to this project"}
    session.execute.assert_not_awaited()


async def test_the_flag_off_is_404_on_all_four(monkeypatch, flag):
    flag["on"] = False
    case = _case()
    session = _Session(case=case)
    async with _client(session, _actor(), monkeypatch, {case.project_id}) as client:
        answers = [
            await client.get(_base(case.id)),
            await client.post(_base(case.id), json={"requirement_key": "REQ-1"}),
            await client.delete(f"{_base(case.id)}/{uuid.uuid4()}"),
            await client.get("/api/v1/test-management/traceability/gaps", params={"project_id": str(case.project_id)}),
        ]
    assert [(r.status_code, r.json()["detail"]) for r in answers] == [(404, TRACEABILITY_OFF)] * 4
    session.execute.assert_not_awaited()


async def test_the_gaps_need_a_project_and_a_membership(monkeypatch, flag):
    session = _Session()
    async with _client(session, _actor(), monkeypatch, {uuid.uuid4()}) as client:
        none = await client.get("/api/v1/test-management/traceability/gaps")
        other = await client.get("/api/v1/test-management/traceability/gaps", params={"project_id": str(uuid.uuid4())})
    assert (none.status_code, none.json()["detail"]) == (422, TRACEABILITY_NEEDS_PROJECT)
    assert other.status_code == 403
    session.execute.assert_not_awaited()


def test_the_request_model_bounds_the_key():
    with pytest.raises(ValueError):
        RequirementLinkCreate(requirement_key="")
    assert RequirementLinkCreate(requirement_key="REQ-1").link_type == "verifies"
