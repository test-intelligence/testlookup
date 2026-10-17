"""W09 FLK-02: ``GET`` / ``PUT /api/v1/projects/{project_id}/quarantine/policy``.

Driven through the real router, the real ``upsert_lifecycle_policy`` and the
real activity ``record`` over a fake session that keeps the policy rows of
each project, the project memberships, and every other row staged:

* a QA engineer's PUT is 403; a QA lead who is not a member of the PATH
  project is 403 and no policy row is written (the guard is the path id);
* ``max_active_quarantined`` is editable: a PUT of 3 reads back 3; a project
  with no row answers the default 8 and ``is_default: true``;
* a PUT stages one ``quarantine_policy.updated`` ledger row naming the
  changed fields by label, with before / after of those fields only, and
  commits once; the same body again stages nothing; a first PUT of the
  defaults says so;
* the schema's bounds answer 422.

The ledger row and the policy row committing together, and the SLA reaching
an approved request: ``tests/integration/test_quarantine_policy_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.postgres import ProjectActivityEvent, QuarantineLifecyclePolicy
from app.routers import flaky_quarantine as route_module

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
URL = f"/api/v1/projects/{PROJECT}/quarantine/policy"

DEFAULTS = {
    "sla_days": 14,
    "auto_create_defect": False,
    "auto_promote": False,
    "promote_after_passes": 20,
    "detection_flip_rate_threshold": 0.2,
    "detection_min_runs": 10,
    "max_active_quarantined": 8,
}


def _user(role: str):
    return SimpleNamespace(id=uuid.uuid4(), role=role, full_name="Quinn Lead", username="quinn",
                           email="quinn@example.test", is_active=True, is_service_account=False,
                           api_key_project_id=None)


class _Result:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


def _param(params: dict, prefix: str):
    return next(value for key, value in params.items() if key.startswith(prefix))


class _Session:
    """Answers the two reads these routes make: a membership and a policy row."""

    def __init__(self, world):
        self.world = world

    async def execute(self, stmt):
        sql = str(stmt)
        params = stmt.compile().params
        if "FROM project_members" in sql:
            key = (_param(params, "user_id"), _param(params, "project_id"))
            return _Result(uuid.uuid4() if key in self.world.members else None)
        if "FROM quarantine_lifecycle_policies" in sql:
            return _Result(self.world.policies.get(_param(params, "project_id")))
        raise AssertionError(f"unexpected statement: {sql}")

    def add(self, obj):
        if isinstance(obj, QuarantineLifecyclePolicy):
            self.world.policies[obj.project_id] = obj
        elif isinstance(obj, ProjectActivityEvent):
            self.world.ledger.append(obj)
        else:  # pragma: no cover - nothing else is staged by these routes
            raise AssertionError(f"unexpected row: {obj!r}")

    async def flush(self):
        return None

    async def commit(self):
        self.world.commits += 1

    async def refresh(self, obj):
        return None

    async def rollback(self):
        return None

    async def close(self):
        return None


@pytest.fixture
def world():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    state = SimpleNamespace(policies={}, members=set(), ledger=[], commits=0, user=_user("QA_LEAD"))

    def member(role: str, project=PROJECT):
        state.user = _user(role)
        state.members.add((state.user.id, project))
        return state.user

    state.member = member

    async def _db():
        yield _Session(state)

    app = FastAPI()
    app.include_router(route_module.manifest_router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    state.client = TestClient(app, raise_server_exceptions=False)
    return state


def test_a_qa_engineer_cannot_change_the_policy(world):
    world.member("QA_ENGINEER")
    response = world.client.put(URL, json=DEFAULTS)
    assert response.status_code == 403, response.text
    assert world.policies == {} and world.ledger == [] and world.commits == 0


def test_a_qa_lead_of_another_project_is_refused_by_the_path_id(world):
    world.member("QA_LEAD", project=OTHER)
    response = world.client.put(URL, json={**DEFAULTS, "sla_days": 7})
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "You do not have access to this project"
    assert world.policies == {} and world.ledger == [] and world.commits == 0
    # and reading it is refused the same way
    assert world.client.get(URL).status_code == 403


def test_a_project_without_a_row_reads_the_defaults(world):
    world.member("VIEWER")
    response = world.client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["max_active_quarantined"] == 8
    assert body["is_default"] is True
    assert {k: body[k] for k in DEFAULTS} == DEFAULTS


def test_the_active_cap_is_editable_and_reads_back(world):
    world.member("QA_LEAD")
    put = world.client.put(URL, json={**DEFAULTS, "max_active_quarantined": 3})
    assert put.status_code == 200, put.text
    assert put.json()["max_active_quarantined"] == 3
    assert world.policies[PROJECT].max_active_quarantined == 3
    got = world.client.get(URL).json()
    assert got["max_active_quarantined"] == 3 and got["is_default"] is False


def test_a_change_stages_one_ledger_row_with_its_fields_and_commits_once(world):
    world.member("QA_LEAD")
    assert world.client.put(URL, json=DEFAULTS).status_code == 200
    world.ledger.clear()
    world.commits = 0

    body = {**DEFAULTS, "sla_days": 7, "auto_promote": True}
    response = world.client.put(URL, json=body)
    assert response.status_code == 200, response.text
    assert world.commits == 1
    [row] = world.ledger
    assert row.event_type == "quarantine_policy.updated"
    assert row.category == "configuration"
    assert row.entity_type == "quarantine_policy"
    assert row.entity_id == str(PROJECT)
    assert row.entity_label == "Quarantine policy"
    assert row.actor_type == "user" and row.actor_id == world.user.id
    assert row.context == {"changed": "SLA days, auto-release"}
    assert row.summary == "Quarantine policy changed: SLA days, auto-release"
    assert row.diff == {
        "before": {"sla_days": 14, "auto_promote": False},
        "after": {"sla_days": 7, "auto_promote": True},
        "changed_fields": ["sla_days", "auto_promote"],
    }

    again = world.client.put(URL, json=body)
    assert again.status_code == 200
    assert len(world.ledger) == 1, "the same values again record nothing"
    assert world.commits == 2


def test_a_first_save_of_the_defaults_says_so(world):
    world.member("QA_LEAD")
    response = world.client.put(URL, json=DEFAULTS)
    assert response.status_code == 200, response.text
    assert response.json()["is_default"] is False
    [row] = world.ledger
    assert row.context == {"changed": "saved the defaults as this project's policy"}
    assert row.diff is None
    assert world.client.get(URL).json()["is_default"] is False


def test_a_first_save_with_a_change_names_the_change(world):
    world.member("QA_LEAD")
    assert world.client.put(URL, json={**DEFAULTS, "max_active_quarantined": 0}).status_code == 200
    [row] = world.ledger
    assert row.context == {"changed": "max active quarantined"}
    assert row.diff["before"] == {"max_active_quarantined": 8}
    assert row.diff["after"] == {"max_active_quarantined": 0}


def test_an_omitted_cap_resets_to_the_default_like_every_other_field(world):
    world.member("QA_LEAD")
    assert world.client.put(URL, json={**DEFAULTS, "max_active_quarantined": 3}).status_code == 200
    payload = {k: v for k, v in DEFAULTS.items() if k != "max_active_quarantined"}
    response = world.client.put(URL, json=payload)
    assert response.json()["max_active_quarantined"] == 8


@pytest.mark.parametrize("field,value", [
    ("sla_days", 0),
    ("sla_days", 366),
    ("detection_flip_rate_threshold", 1.5),
    ("max_active_quarantined", -1),
    ("max_active_quarantined", 1001),
])
def test_out_of_range_values_are_refused(world, field, value):
    world.member("QA_LEAD")
    response = world.client.put(URL, json={**DEFAULTS, field: value})
    assert response.status_code == 422, response.text
    assert world.policies == {} and world.ledger == []


def test_the_labels_name_every_field_of_the_schema_once():
    from app.models.schemas import QuarantineLifecyclePolicyUpdate

    names = [name for name, _label in route_module._POLICY_FIELD_LABELS]
    assert names == list(QuarantineLifecyclePolicyUpdate.model_fields)
    labels = [label for _name, label in route_module._POLICY_FIELD_LABELS]
    assert len(set(labels)) == len(labels)
