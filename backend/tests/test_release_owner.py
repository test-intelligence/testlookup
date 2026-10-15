"""W05 REL-02: a release has a real owner instead of the project name.

Before W05 the release card printed the PROJECT's name where an owner belonged,
and nothing could store a person: ``Release.created_by_id`` existed and no code
path wrote it. These pin the backend half:

* who may own a release (``release_owner_service.validate_owner``): an ADMIN, or
  an active, interactive member of the release's project. Every other id --
  another tenant's user, a deactivated user, a service account, the synthetic
  per-project QA lead, an unknown id, a malformed one -- is ONE 422 text, so the
  check cannot be used to probe which user ids exist;
* what a reader sees (``owners_by_id``): a name for an eligible owner, ``None``
  ("No owner") for anything else, resolved for a whole list in ONE statement;
* the default (``default_owner_for``): the creating human, never an API key or
  a robot; the creator is recorded either way;
* the update: ``null`` clears (``exclude_none`` could never carry it), the id is
  checked against the RELEASE's project, and the ledger row names people, not
  ids. The live-database half is
  ``tests/integration/test_releases_list_gate_owner_postgres.py``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.core.deps import CREDENTIAL_KIND_API_KEY, CREDENTIAL_KIND_JWT, _bind_credential_kind  # noqa: E402
from app.models.postgres import UserRole  # noqa: E402
from app.routers import releases as rel_router  # noqa: E402
from app.routers.releases import ReleaseIn, ReleaseUpdate  # noqa: E402
from app.services import release_owner_service as owners  # noqa: E402
from app.services import release_service  # noqa: E402

PROJECT = uuid.uuid4()


def _user(role=UserRole.QA_ENGINEER, *, active=True, service=False, synthetic=False, name="Priya Nair"):
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, is_active=active, is_service_account=service,
        is_synthetic=synthetic, full_name=name, username="priya", email="p@example.test",
    )


class _OwnerSession:
    """Serves ``db.get(User, id)`` from a dict and the membership probe from a set."""

    def __init__(self, users=(), members=()):
        self.users = {u.id: u for u in users}
        self.members = set(members)  # {(user_id, project_id)}
        self.gets = 0
        self.scalars: list[str] = []

    async def get(self, model, pk):
        self.gets += 1
        return self.users.get(pk)

    async def scalar(self, stmt):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.scalars.append(sql)
        for user_id, project_id in self.members:
            if user_id.hex in sql.replace("-", "") and project_id.hex in sql.replace("-", ""):
                return uuid.uuid4()
        return None


# ── validate_owner ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_of_the_project_may_own_a_release():
    priya = _user()
    db = _OwnerSession([priya], members=[(priya.id, PROJECT)])

    assert await owners.validate_owner(db, PROJECT, str(priya.id)) == priya.id
    assert "project_members" in db.scalars[0]


@pytest.mark.asyncio
async def test_an_admin_who_is_not_a_member_may_own_a_release():
    """An ADMIN has no membership rows (``get_accessible_project_ids`` answers
    ``None``) yet acts on every project; refusing them would leave a person who
    can work the release unable to own it."""
    admin = _user(UserRole.ADMIN)
    db = _OwnerSession([admin])

    assert await owners.validate_owner(db, PROJECT, str(admin.id)) == admin.id
    assert db.scalars == [], "an ADMIN needs no membership probe"


def _ineligible_cases():
    other_project = uuid.uuid4()
    outsider = _user()
    gone = _user(active=False)
    robot = _user(service=True)
    synth = _user(synthetic=True)
    members = [(u.id, PROJECT) for u in (gone, robot, synth)] + [(outsider.id, other_project)]
    db = _OwnerSession([outsider, gone, robot, synth], members=members)
    return db, {
        "a user of another project": str(outsider.id),
        "an inactive member": str(gone.id),
        "a service-account member": str(robot.id),
        "a synthetic member": str(synth.id),
        "an unknown id": str(uuid.uuid4()),
        "a malformed id": "not-a-uuid",
    }


@pytest.mark.asyncio
async def test_every_ineligible_id_is_the_same_422():
    db, cases = _ineligible_cases()
    for case, owner_id in cases.items():
        with pytest.raises(HTTPException) as exc:
            await owners.validate_owner(db, PROJECT, owner_id)
        assert exc.value.status_code == 422, case
        # One text for all of them: a distinct message per case would let any
        # QA lead learn which user ids exist and what kind of account they are.
        assert exc.value.detail == owners.OWNER_INELIGIBLE, case


@pytest.mark.asyncio
async def test_the_membership_probe_is_scoped_to_the_given_project():
    """A member of project B naming themselves on project A's release is refused:
    the probe must carry BOTH ids, or any member of anything would pass."""
    priya = _user()
    db = _OwnerSession([priya], members=[(priya.id, uuid.uuid4())])

    with pytest.raises(HTTPException):
        await owners.validate_owner(db, PROJECT, str(priya.id))
    assert PROJECT.hex in db.scalars[0].replace("-", "")


# ── default_owner_for ────────────────────────────────────────────────────────


def test_an_interactive_human_is_the_default_owner():
    human = _bind_credential_kind(_user(), CREDENTIAL_KIND_JWT)
    assert owners.default_owner_for(human) == human.id


@pytest.mark.parametrize(
    "actor",
    [
        pytest.param(_bind_credential_kind(_user(), CREDENTIAL_KIND_API_KEY), id="api-key"),
        pytest.param(_user(), id="unknown-credential"),
        pytest.param(_bind_credential_kind(_user(service=True), CREDENTIAL_KIND_JWT), id="service-account"),
        pytest.param(_bind_credential_kind(_user(synthetic=True), CREDENTIAL_KIND_JWT), id="synthetic"),
        pytest.param(None, id="no-actor"),
    ],
)
def test_nothing_but_an_interactive_human_is_a_default_owner(actor):
    assert owners.default_owner_for(actor) is None


# ── owners_by_id ─────────────────────────────────────────────────────────────


class _RowsSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, stmt, *a, **kw):
        self.statements.append(str(stmt))
        rows = self.rows
        return SimpleNamespace(all=lambda: rows)


@pytest.mark.asyncio
async def test_owners_of_a_whole_page_resolve_in_one_statement():
    people = [_user(name=f"Person {i}") for i in range(20)]
    robot, gone = _user(service=True), _user(active=False)
    db = _RowsSession([*people, robot, gone])

    found = await owners.owners_by_id(db, [p.id for p in people] + [robot.id, gone.id, None])

    assert len(db.statements) == 1
    assert "FROM users" in db.statements[0]
    assert found[people[3].id] == {"id": str(people[3].id), "name": "Person 3"}
    assert robot.id not in found and gone.id not in found, "a robot or departed owner must read 'No owner'"


@pytest.mark.asyncio
async def test_no_owner_on_the_page_costs_no_statement():
    db = _RowsSession([])
    assert await owners.owners_by_id(db, [None, None]) == {}
    assert db.statements == []


@pytest.mark.asyncio
async def test_the_name_falls_back_to_the_username():
    nameless = _user(name=None)
    found = await owners.owners_by_id(_RowsSession([nameless]), [nameless.id])
    assert found[nameless.id]["name"] == "priya"


# ── create_release ───────────────────────────────────────────────────────────


class _CreateSession:
    """Enough of a session for ``create_release``: the project exists, nothing
    else is found, and the staged release is captured."""

    def __init__(self):
        self.added = []

    async def get(self, model, pk):
        return SimpleNamespace(id=pk)

    async def execute(self, stmt=None, *a, **kw):
        return SimpleNamespace(scalar_one_or_none=lambda: None, scalar=lambda: 0)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _staged(db):
    from app.models.postgres import Release

    return next(o for o in db.added if isinstance(o, Release))


@pytest.mark.asyncio
async def test_create_records_the_creator_and_defaults_the_owner(monkeypatch):
    monkeypatch.setattr(
        release_service.release_lifecycle_service, "should_activate_on_create", AsyncMock(return_value=False)
    )
    human = _bind_credential_kind(_user(), CREDENTIAL_KIND_JWT)
    db = _CreateSession()

    await release_service.create_release(db, ReleaseIn(project_id=str(PROJECT), name="2026.11"), actor=human)

    row = _staged(db)
    assert row.created_by_id == human.id, "created_by_id is written by no creation path"
    assert row.owner_user_id == human.id


@pytest.mark.asyncio
async def test_an_api_key_create_is_recorded_but_owns_nothing(monkeypatch):
    monkeypatch.setattr(
        release_service.release_lifecycle_service, "should_activate_on_create", AsyncMock(return_value=False)
    )
    ci = _bind_credential_kind(_user(), CREDENTIAL_KIND_API_KEY)
    db = _CreateSession()

    await release_service.create_release(db, ReleaseIn(project_id=str(PROJECT), name="2026.11"), actor=ci)

    row = _staged(db)
    assert row.created_by_id == ci.id
    assert row.owner_user_id is None


@pytest.mark.asyncio
async def test_create_validates_a_supplied_owner_against_the_bodys_project(monkeypatch):
    monkeypatch.setattr(
        release_service.release_lifecycle_service, "should_activate_on_create", AsyncMock(return_value=False)
    )
    chosen = uuid.uuid4()
    validate = AsyncMock(return_value=chosen)
    monkeypatch.setattr(owners, "validate_owner", validate)
    db = _CreateSession()

    await release_service.create_release(
        db, ReleaseIn(project_id=str(PROJECT), name="2026.11", owner_user_id=str(chosen)),
        actor=_bind_credential_kind(_user(), CREDENTIAL_KIND_JWT),
    )

    validate.assert_awaited_once_with(db, PROJECT, str(chosen))
    assert _staged(db).owner_user_id == chosen


@pytest.mark.asyncio
async def test_a_refused_owner_stages_no_release(monkeypatch):
    monkeypatch.setattr(owners, "validate_owner", AsyncMock(side_effect=HTTPException(422, owners.OWNER_INELIGIBLE)))
    db = _CreateSession()

    with pytest.raises(HTTPException) as exc:
        await release_service.create_release(
            db, ReleaseIn(project_id=str(PROJECT), name="x", owner_user_id=str(uuid.uuid4())), actor=None,
        )
    assert exc.value.status_code == 422
    assert db.added == []


@pytest.mark.asyncio
async def test_an_explicit_null_owner_on_create_means_no_owner(monkeypatch):
    monkeypatch.setattr(
        release_service.release_lifecycle_service, "should_activate_on_create", AsyncMock(return_value=False)
    )
    db = _CreateSession()

    await release_service.create_release(
        db, ReleaseIn(project_id=str(PROJECT), name="2026.11", owner_user_id=None),
        actor=_bind_credential_kind(_user(), CREDENTIAL_KIND_JWT),
    )

    assert _staged(db).owner_user_id is None


# ── update_release (service) ─────────────────────────────────────────────────


def _release(owner=None):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=PROJECT, owner_user_id=owner, name="2026.11",
        version=None, status="in_progress", sort_key=None, is_active=False, source_system=None,
    )


def _patch_lookup(monkeypatch, release):
    monkeypatch.setattr(release_service, "get_release_or_404", AsyncMock(return_value=release))


@pytest.mark.asyncio
async def test_null_clears_the_owner(monkeypatch):
    rel = _release(owner=uuid.uuid4())
    _patch_lookup(monkeypatch, rel)

    await release_service.update_release(object(), str(rel.id), ReleaseUpdate(owner_user_id=None))

    assert rel.owner_user_id is None, "exclude_none dropped the null, so the owner could never be cleared"


@pytest.mark.asyncio
async def test_an_empty_string_also_clears_the_owner(monkeypatch):
    rel = _release(owner=uuid.uuid4())
    _patch_lookup(monkeypatch, rel)

    await release_service.update_release(object(), str(rel.id), ReleaseUpdate(owner_user_id=""))

    assert rel.owner_user_id is None


@pytest.mark.asyncio
async def test_omitting_the_owner_keeps_it(monkeypatch):
    keep = uuid.uuid4()
    rel = _release(owner=keep)
    _patch_lookup(monkeypatch, rel)

    await release_service.update_release(object(), str(rel.id), ReleaseUpdate(description="notes"))

    assert rel.owner_user_id == keep


@pytest.mark.asyncio
async def test_an_owner_is_checked_against_the_releases_own_project(monkeypatch):
    """The body carries no project on update; the id must be checked against
    the release's project, not trusted."""
    rel = _release()
    _patch_lookup(monkeypatch, rel)
    chosen = uuid.uuid4()
    validate = AsyncMock(return_value=chosen)
    monkeypatch.setattr(owners, "validate_owner", validate)
    db = object()

    await release_service.update_release(db, str(rel.id), ReleaseUpdate(owner_user_id=str(chosen)))

    validate.assert_awaited_once_with(db, PROJECT, str(chosen))
    assert rel.owner_user_id == chosen


@pytest.mark.asyncio
async def test_a_refused_owner_leaves_the_row_untouched(monkeypatch):
    keep = uuid.uuid4()
    rel = _release(owner=keep)
    _patch_lookup(monkeypatch, rel)
    monkeypatch.setattr(owners, "validate_owner", AsyncMock(side_effect=HTTPException(422, owners.OWNER_INELIGIBLE)))

    with pytest.raises(HTTPException):
        await release_service.update_release(
            object(), str(rel.id), ReleaseUpdate(owner_user_id=str(uuid.uuid4()), description="new")
        )
    assert rel.owner_user_id == keep
    assert not hasattr(rel, "description"), "a field was applied before the owner was refused"


# ── update_release (router): the ledger row ──────────────────────────────────


async def _put(monkeypatch, rel, body, *, labels):
    async def _update(db, release_id, b):
        if "owner_user_id" in b.model_fields_set:
            rel.owner_user_id = uuid.UUID(b.owner_user_id) if b.owner_user_id else None
        return rel

    monkeypatch.setattr(rel_router.release_service, "get_release_or_404", AsyncMock(return_value=rel))
    monkeypatch.setattr(rel_router.release_service, "update_release", _update)
    monkeypatch.setattr(
        rel_router.release_owner_service, "owners_by_id",
        AsyncMock(return_value={k: {"id": str(k), "name": v} for k, v in labels.items()}),
    )
    activity = AsyncMock()
    monkeypatch.setattr(rel_router, "record_activity", activity)
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(rel_router, "serialize_model", lambda row: {"id": str(row.id)})
    await rel_router.update_release(
        release_id=str(rel.id), body=body, db=db,
        current_user=SimpleNamespace(id=uuid.uuid4(), full_name="Lead", username="lead"), __=None,
    )
    return activity


@pytest.mark.asyncio
async def test_setting_an_owner_is_recorded_by_name(monkeypatch):
    priya = uuid.uuid4()
    rel = _release()
    activity = await _put(monkeypatch, rel, ReleaseUpdate(owner_user_id=str(priya)), labels={priya: "Priya Nair"})

    kwargs = activity.await_args.kwargs
    assert kwargs["event_type"] == "release.updated"
    assert kwargs["changed_fields"] == ["owner"]
    assert kwargs["context"] == {"changed": "owner"}
    assert kwargs["before"] == {"owner": "No owner"}
    assert kwargs["after"] == {"owner": "Priya Nair"}


@pytest.mark.asyncio
async def test_clearing_an_owner_is_recorded_by_name(monkeypatch):
    priya = uuid.uuid4()
    rel = _release(owner=priya)
    activity = await _put(monkeypatch, rel, ReleaseUpdate(owner_user_id=None), labels={priya: "Priya Nair"})

    kwargs = activity.await_args.kwargs
    assert kwargs["changed_fields"] == ["owner"]
    assert kwargs["before"] == {"owner": "Priya Nair"}
    assert kwargs["after"] == {"owner": "No owner"}


@pytest.mark.asyncio
async def test_resending_the_current_owner_records_nothing(monkeypatch):
    """The row holds a UUID and the body a str: a change test against the BODY
    would read the same owner as a change on every save. The router compares
    the row before with the row after."""
    priya = uuid.uuid4()
    rel = _release(owner=priya)
    activity = await _put(monkeypatch, rel, ReleaseUpdate(owner_user_id=str(priya)), labels={priya: "Priya Nair"})

    activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_resending_the_current_baseline_records_nothing(monkeypatch):
    """The same UUID-against-str comparison made a re-sent baseline a change."""
    base = uuid.uuid4()
    rel = _release()
    rel.baseline_release_id = base

    async def _update(db, release_id, b):
        rel.baseline_release_id = base  # resolved back to the same UUID
        return rel

    monkeypatch.setattr(rel_router.release_service, "get_release_or_404", AsyncMock(return_value=rel))
    monkeypatch.setattr(rel_router.release_service, "update_release", _update)
    activity = AsyncMock()
    monkeypatch.setattr(rel_router, "record_activity", activity)
    monkeypatch.setattr(rel_router, "serialize_model", lambda row: {})
    await rel_router.update_release(
        release_id=str(rel.id), body=ReleaseUpdate(baseline_release_id=str(base)),
        db=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
        current_user=SimpleNamespace(id=uuid.uuid4()), __=None,
    )
    activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_router_passes_the_caller_to_create(monkeypatch):
    """``create_release`` can only record a creator it is handed."""
    caller = SimpleNamespace(id=uuid.uuid4(), full_name="Lead", username="lead")
    created = SimpleNamespace(id=uuid.uuid4(), name="2026.11", version=None)
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(rel_router, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None)))
    monkeypatch.setattr(rel_router.release_service, "create_release", create)
    monkeypatch.setattr(rel_router.release_service, "serialize_created_release", AsyncMock(return_value={}))
    monkeypatch.setattr(rel_router, "record_activity", AsyncMock())

    body = ReleaseIn(project_id=str(PROJECT), name="2026.11")
    await rel_router.create_release(body=body, db=SimpleNamespace(commit=AsyncMock()), current_user=caller)

    assert create.await_args.kwargs["actor"] is caller


# ── The member list the Owner picker reads ───────────────────────────────────


def test_the_member_list_says_which_members_are_not_people():
    from app.routers.users import _build_project_member_response

    member = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=PROJECT, role="QA_LEAD",
        created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    robot = SimpleNamespace(email="ci@example.test", username="ci", full_name=None,
                            is_service_account=True, is_synthetic=False, is_active=True)
    synth = SimpleNamespace(email="qa@qa-lead.testlookup.local", username="qa", full_name=None,
                            is_service_account=False, is_synthetic=True, is_active=False)

    r = _build_project_member_response(member, robot)
    s = _build_project_member_response(member, synth)

    assert (r.is_service_account, r.is_synthetic, r.is_active) == (True, False, True)
    assert (s.is_service_account, s.is_synthetic, s.is_active) == (False, True, False)
