"""W24 ADM-05: ``provision_new_project`` is what ``create_project`` did inline, in the same order.

The project import builds its copy through the same helper, so "what a new
project needs" has ONE definition. A recording fake session + recording
stand-ins for the four collaborators pin the sequence: the creator's
membership, the default QA lead (validated when supplied, provisioned when
not), the service-account enrolment, the active release (skipped with
``provision_release=False``) and the ``project.created`` event — and that
``create_project`` calls the helper exactly once, BEFORE its commit, and
invalidates the caches it returns AFTER it.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.postgres import Project, ProjectMember
from app.services import project_provisioning


class _RecordingSession:
    def __init__(self, events: list) -> None:
        self.events = events
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)
        self.events.append(("add", type(obj).__name__))

    async def flush(self) -> None:
        self.events.append(("flush",))

    async def commit(self) -> None:
        self.events.append(("commit",))

    async def refresh(self, obj) -> None:
        self.events.append(("refresh",))

    async def execute(self, *_a, **_k):
        self.events.append(("execute",))
        return SimpleNamespace(scalar_one_or_none=lambda: None)


def _collaborators(events: list, enrolled: list[uuid.UUID]):
    async def ensure_default_qa_lead(db, project):
        events.append(("ensure_default_qa_lead", project.id))

    async def assert_qa_lead(db, user_id, project_id):
        events.append(("assert_user_is_qa_lead_on_project", user_id, project_id))

    async def enroll(db, project_id):
        events.append(("enroll_service_accounts_in_project", project_id))
        return enrolled

    async def ensure_release(db, project):
        events.append(("ensure_active_release_for_new_project", project.id))

    async def record(db, **kwargs):
        events.append(("record_activity", kwargs["event_type"], kwargs["context"]))

    return (
        patch("app.services.default_qa_lead_service.ensure_default_qa_lead", new=ensure_default_qa_lead),
        patch("app.services.suite_review_service.assert_user_is_qa_lead_on_project", new=assert_qa_lead),
        patch("app.services.service_account_service.enroll_service_accounts_in_project", new=enroll),
        patch("app.services.release_lifecycle_service.ensure_active_release_for_new_project", new=ensure_release),
        patch("app.services.project_provisioning.record_activity", new=record),
    )


def _project(**kw) -> Project:
    return Project(id=uuid.uuid4(), name="Payment Service", slug="payment-service", **kw)


async def _provision(project: Project, creator, **kwargs):
    events: list = []
    db = _RecordingSession(events)
    enrolled = [uuid.uuid4()]
    patches = _collaborators(events, enrolled)
    for p in patches:
        p.start()
    try:
        result = await project_provisioning.provision_new_project(db, project=project, creator=creator, **kwargs)
    finally:
        for p in patches:
            p.stop()
    return db, events, result, enrolled


@pytest.mark.asyncio
async def test_the_steps_run_in_create_projects_order():
    project = _project()
    creator = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD", username="lead", full_name=None)
    db, events, result, enrolled = await _provision(project, creator)
    assert events == [
        ("add", "ProjectMember"),
        ("flush",),
        ("ensure_default_qa_lead", project.id),
        ("enroll_service_accounts_in_project", project.id),
        ("ensure_active_release_for_new_project", project.id),
        ("record_activity", "project.created", {"slug": "payment-service"}),
    ]
    (member,) = db.added
    assert isinstance(member, ProjectMember)
    assert (member.user_id, member.project_id, member.role) == (creator.id, project.id, "QA_LEAD")
    assert result == enrolled
    assert ("commit",) not in events            # stage-only: the caller commits


@pytest.mark.asyncio
async def test_a_supplied_default_qa_lead_is_validated_not_provisioned():
    lead_id = uuid.uuid4()
    project = _project(default_qa_lead_user_id=lead_id)
    creator = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    _db, events, _result, _enrolled = await _provision(project, creator)
    assert [e[0] for e in events] == [
        "add", "flush", "assert_user_is_qa_lead_on_project", "enroll_service_accounts_in_project",
        "ensure_active_release_for_new_project", "record_activity",
    ]
    assert events[2] == ("assert_user_is_qa_lead_on_project", lead_id, project.id)


@pytest.mark.asyncio
async def test_provision_release_false_skips_the_release_and_context_is_merged():
    project = _project()
    creator = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    _db, events, _result, _enrolled = await _provision(
        project, creator, provision_release=False, activity_context={"imported_from": "Payment Service"},
    )
    assert "ensure_active_release_for_new_project" not in [e[0] for e in events]
    assert events[-1] == (
        "record_activity", "project.created", {"slug": "payment-service", "imported_from": "Payment Service"},
    )


@pytest.mark.asyncio
async def test_create_project_calls_the_helper_once_before_its_commit():
    from app.models.schemas import ProjectCreate
    from app.routers import projects as router

    events: list = []
    db = _RecordingSession(events)
    creator = SimpleNamespace(id=uuid.uuid4(), role="ADMIN")
    account = uuid.uuid4()
    calls: list = []

    async def helper(db_arg, *, project, creator):
        calls.append((db_arg, project, creator))
        events.append(("provision_new_project", project.slug))
        return [account]

    async def invalidate(user_id):
        events.append(("invalidate_membership_cache", user_id))

    with patch.object(router, "provision_new_project", new=helper), \
            patch("app.core.deps.invalidate_membership_cache", new=invalidate):
        result = await router.create_project(ProjectCreate(name="New Project", slug="new-project"), db, creator)

    assert len(calls) == 1 and calls[0][0] is db and calls[0][2] is creator
    assert calls[0][1] is result and result.slug == "new-project"
    assert [e[0] for e in events] == [
        "execute", "add", "flush", "provision_new_project", "commit", "refresh",
        "invalidate_membership_cache", "invalidate_membership_cache",
    ]
    assert events[-2:] == [("invalidate_membership_cache", creator.id), ("invalidate_membership_cache", account)]


def test_create_project_has_one_provisioning_path():
    from app.routers import projects as router

    body = inspect.getsource(router.create_project)
    assert body.count("provision_new_project(") == 1
    for inline in ("ensure_default_qa_lead(", "ensure_active_release_for_new_project(", "record_activity(",
                   "ProjectMember("):
        assert inline not in body, f"create_project calls {inline} itself: it belongs to provision_new_project"
