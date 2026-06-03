"""Unit tests for the QA_LEAD-role-checked suite-owner feature (2026-05-14).

Covers four code paths added in migrations 0079 + 0080:

1. ``services.suite_review_service.assert_user_is_qa_lead_on_project`` —
   admin bypass, QA_LEAD pass, non-QA_LEAD reject, non-member reject,
   unknown-user reject.

2. ``services.suite_review_service.set_suite_owner`` — role gate runs
   before any write; clearing (``owner_user_id=None``) skips the gate.

3. ``services.suite_review_service.resolve_suite_owner`` — precedence is
   explicit row → ``Project.default_qa_lead_user_id`` →
   ``Project.manager_user_id`` → ``(None, False)``.

4. ``services.test_suite_service._maybe_seed_default_owner`` — writes a
   ``TestSuiteOwner`` row when the project has a default QA lead, is a
   no-op when the default is NULL, never overwrites an existing row, and
   swallows DB errors (ingest must not fail because of a seeding hiccup).

5. ``services.failed_test_assignment_service.assign_failed_tests_to_suite_owners``
   — empty run, no-owner, explicit owner, project-fallback, mixed batch,
   already-assigned passthrough.

All tests are pure unit tests against the conftest ``FakeExecuteResult``
helper plus ``AsyncMock`` / ``MagicMock``. No live database needed.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.postgres import UserRole

# ── shared helpers ────────────────────────────────────────────────────────


def _scalar_result(value):
    """A MagicMock that responds to .scalar_one_or_none() with ``value``."""
    res = MagicMock()
    res.scalar_one_or_none = MagicMock(return_value=value)
    return res


def _all_result(rows):
    """A MagicMock that responds to .all() with ``rows``."""
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


def _first_result(value):
    """A MagicMock that responds to .first() with ``value``."""
    res = MagicMock()
    res.first = MagicMock(return_value=value)
    return res


def _user(role: str, *, uid: uuid.UUID | None = None):
    return SimpleNamespace(id=uid or uuid.uuid4(), role=role)


def _member(role: str, *, uid: uuid.UUID, pid: uuid.UUID):
    return SimpleNamespace(user_id=uid, project_id=pid, role=role)


# ════════════════════════════════════════════════════════════════════════════
# 1. assert_user_is_qa_lead_on_project
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_assert_admin_bypasses_project_membership():
    """Instance ADMIN passes without a ProjectMember row."""
    from app.services.suite_review_service import assert_user_is_qa_lead_on_project

    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    db = AsyncMock()
    # Only the user fetch executes — admin bypass returns before member SELECT.
    db.execute = AsyncMock(return_value=_scalar_result(_user(UserRole.ADMIN.value, uid=user_id)))

    await assert_user_is_qa_lead_on_project(db, user_id, project_id)
    # No exception = pass. Only one DB call should have fired (the user lookup).
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_assert_qa_lead_member_passes():
    from app.services.suite_review_service import assert_user_is_qa_lead_on_project

    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_row = _user(UserRole.QA_ENGINEER.value, uid=user_id)  # global role doesn't matter
    member_row = _member(UserRole.QA_LEAD, uid=user_id, pid=project_id)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(user_row),
        _scalar_result(member_row),
    ])

    await assert_user_is_qa_lead_on_project(db, user_id, project_id)


@pytest.mark.asyncio
async def test_assert_qa_engineer_member_rejected():
    from app.services.suite_review_service import assert_user_is_qa_lead_on_project

    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_row = _user(UserRole.QA_ENGINEER.value, uid=user_id)
    member_row = _member(UserRole.QA_ENGINEER, uid=user_id, pid=project_id)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(user_row),
        _scalar_result(member_row),
    ])

    with pytest.raises(HTTPException) as exc:
        await assert_user_is_qa_lead_on_project(db, user_id, project_id)
    assert exc.value.status_code == 400
    assert "QA_LEAD" in exc.value.detail


@pytest.mark.asyncio
async def test_assert_non_member_rejected():
    from app.services.suite_review_service import assert_user_is_qa_lead_on_project

    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_row = _user(UserRole.QA_LEAD.value, uid=user_id)  # global role QA_LEAD doesn't grant project access

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(user_row),
        _scalar_result(None),  # no ProjectMember row
    ])

    with pytest.raises(HTTPException) as exc:
        await assert_user_is_qa_lead_on_project(db, user_id, project_id)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_assert_unknown_user_rejected():
    from app.services.suite_review_service import assert_user_is_qa_lead_on_project

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))

    with pytest.raises(HTTPException) as exc:
        await assert_user_is_qa_lead_on_project(db, uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 400
    assert "User not found" in exc.value.detail


# ════════════════════════════════════════════════════════════════════════════
# 2. set_suite_owner — role enforcement
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_set_suite_owner_rejects_non_qa_lead():
    """Backend must reject before any DB write happens."""
    from app.services.suite_review_service import set_suite_owner

    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_row = _user(UserRole.QA_ENGINEER.value, uid=user_id)
    member_row = _member(UserRole.QA_ENGINEER, uid=user_id, pid=project_id)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(user_row),
        _scalar_result(member_row),
    ])
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()

    with pytest.raises(HTTPException):
        await set_suite_owner(db, project_id, "Smoke", user_id)
    # No write happened because the role check raised first.
    db.add.assert_not_called()
    db.delete.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_suite_owner_clear_skips_role_check():
    """Passing ``owner_user_id=None`` clears the row without a role check.

    Clearing must always work — it's the escape hatch when the configured
    owner leaves the project. The role check would block clearing if it
    tried to validate a NULL UUID.
    """
    from app.services.suite_review_service import set_suite_owner

    project_id = uuid.uuid4()
    existing = SimpleNamespace(id=uuid.uuid4())

    db = AsyncMock()
    # Only one execute fires: the SELECT for the existing row.
    db.execute = AsyncMock(return_value=_scalar_result(existing))
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()

    result = await set_suite_owner(db, project_id, "Smoke", None)
    db.delete.assert_awaited_once_with(existing)
    assert result is existing


# ════════════════════════════════════════════════════════════════════════════
# 3. resolve_suite_owner — precedence chain
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resolve_explicit_wins():
    from app.services.suite_review_service import resolve_suite_owner

    owner_user = SimpleNamespace(id=uuid.uuid4(), email="alice@x", full_name="Alice")
    explicit = SimpleNamespace(owner_user_id=owner_user.id)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(explicit),     # TestSuiteOwner row
        _scalar_result(owner_user),   # User fetch
    ])

    user, is_fallback = await resolve_suite_owner(db, uuid.uuid4(), "Smoke")
    assert user is owner_user
    assert is_fallback is False


@pytest.mark.asyncio
async def test_resolve_falls_back_to_default_qa_lead():
    """Default QA lead wins over manager_user_id."""
    from app.services.suite_review_service import resolve_suite_owner

    default_qa_lead = SimpleNamespace(id=uuid.uuid4(), email="bob@x", full_name="Bob")
    project = SimpleNamespace(
        id=uuid.uuid4(),
        default_qa_lead_user_id=default_qa_lead.id,
        manager_user_id=uuid.uuid4(),  # set but should not be used
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(None),           # no explicit TestSuiteOwner
        _scalar_result(project),        # Project fetch
        _scalar_result(default_qa_lead),  # default QA lead user fetch
    ])

    user, is_fallback = await resolve_suite_owner(db, project.id, "Smoke")
    assert user is default_qa_lead
    assert is_fallback is True


@pytest.mark.asyncio
async def test_resolve_falls_through_to_manager_when_default_null():
    """Legacy manager_user_id used when default_qa_lead_user_id is NULL."""
    from app.services.suite_review_service import resolve_suite_owner

    manager = SimpleNamespace(id=uuid.uuid4(), email="carol@x", full_name="Carol")
    project = SimpleNamespace(
        id=uuid.uuid4(),
        default_qa_lead_user_id=None,
        manager_user_id=manager.id,
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(None),
        _scalar_result(project),
        _scalar_result(manager),
    ])

    user, is_fallback = await resolve_suite_owner(db, project.id, "Smoke")
    assert user is manager
    assert is_fallback is True


@pytest.mark.asyncio
async def test_resolve_all_null_returns_none():
    from app.services.suite_review_service import resolve_suite_owner

    project = SimpleNamespace(
        id=uuid.uuid4(),
        default_qa_lead_user_id=None,
        manager_user_id=None,
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar_result(None),
        _scalar_result(project),
    ])

    user, is_fallback = await resolve_suite_owner(db, project.id, "Smoke")
    assert user is None
    assert is_fallback is False


# ════════════════════════════════════════════════════════════════════════════
# 4. _maybe_seed_default_owner — ingest-time owner seeding
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_seed_writes_owner_when_default_qa_lead_set():
    from app.services.test_suite_service import _maybe_seed_default_owner

    project_id = uuid.uuid4()
    default_qa_lead_id = uuid.uuid4()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _first_result((default_qa_lead_id,)),  # project default lookup
        _first_result(None),                    # no existing TestSuiteOwner row
    ])
    db.add = MagicMock()
    db.flush = AsyncMock()
    # The owner insert is now wrapped in a SAVEPOINT; give the mock a no-op
    # begin_nested() async context manager.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_savepoint():
        yield

    db.begin_nested = lambda: _noop_savepoint()

    await _maybe_seed_default_owner(db, project_id, "Smoke")
    db.add.assert_called_once()
    seeded = db.add.call_args.args[0]
    assert seeded.project_id == project_id
    assert seeded.suite_name == "Smoke"
    assert seeded.owner_user_id == default_qa_lead_id


@pytest.mark.asyncio
async def test_seed_skips_when_default_qa_lead_null():
    from app.services.test_suite_service import _maybe_seed_default_owner

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_first_result((None,)))
    db.add = MagicMock()
    db.flush = AsyncMock()

    await _maybe_seed_default_owner(db, uuid.uuid4(), "Smoke")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_seed_preserves_existing_owner_row():
    """Human intent (an explicit TestSuiteOwner row) must never be overwritten."""
    from app.services.test_suite_service import _maybe_seed_default_owner

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _first_result((uuid.uuid4(),)),           # project has a default
        _first_result((uuid.uuid4(),)),            # but a TestSuiteOwner already exists
    ])
    db.add = MagicMock()
    db.flush = AsyncMock()

    await _maybe_seed_default_owner(db, uuid.uuid4(), "Smoke")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_seed_swallows_errors():
    """Seed failures must NOT propagate — ingest is already committed."""
    from app.services.test_suite_service import _maybe_seed_default_owner

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("simulated DB hiccup"))
    db.add = MagicMock()
    db.flush = AsyncMock()

    # No exception expected.
    await _maybe_seed_default_owner(db, uuid.uuid4(), "Smoke")
    db.add.assert_not_called()


# ════════════════════════════════════════════════════════════════════════════
# 5. assign_failed_tests_to_suite_owners
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_assign_empty_run_returns_zero_counts():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_all_result([]))

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts == {"assigned": 0, "already_assigned": 0, "unassigned": 0}


@pytest.mark.asyncio
async def test_assign_unassigned_when_no_owner_anywhere():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    failures = [
        SimpleNamespace(id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None, test_fingerprint="fp-smoke"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((None, None)),       # no default QA lead, no manager
        _all_result([]),                    # no QA_LEAD/ADMIN project members
        _scalar_result(None),               # run.primary_suite_name (effective-suite)
        _all_result([]),                    # no TestSuiteOwner rows
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["unassigned"] == 1
    assert counts["assigned"] == 0


@pytest.mark.asyncio
async def test_assign_resolves_via_test_suite_owner():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    owner_id = uuid.uuid4()
    failures = [
        SimpleNamespace(id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None, test_fingerprint="fp-smoke"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((None, None)),
        _all_result([]),  # member fallback probe — irrelevant, explicit owner wins
        _scalar_result(None),  # run.primary_suite_name (effective-suite)
        _all_result([SimpleNamespace(suite_name="Smoke", owner_user_id=owner_id)]),
        MagicMock(),  # UPDATE result
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["assigned"] == 1
    assert counts["unassigned"] == 0


@pytest.mark.asyncio
async def test_assign_falls_back_to_default_qa_lead():
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    default_qa_lead_id = uuid.uuid4()
    failures = [
        SimpleNamespace(id=uuid.uuid4(), suite_name="UnownedSuite", assigned_to_user_id=None, test_fingerprint="fp-x"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((default_qa_lead_id, uuid.uuid4())),
        _all_result([]),       # _resolve_qa_lead_pool — no QA_LEAD/ADMIN members
        _scalar_result(None),  # run.primary_suite_name (effective-suite)
        _all_result([]),       # no explicit suite owner
        MagicMock(),           # UPDATE
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["assigned"] == 1


@pytest.mark.asyncio
async def test_assign_skips_already_assigned():
    """Pre-existing assigned_to_user_id MUST be preserved (no overwrite)."""
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    existing_assignee = uuid.uuid4()
    new_owner = uuid.uuid4()
    failures = [
        SimpleNamespace(id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=existing_assignee),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((None, None)),
        _all_result([]),  # member fallback probe — row already assigned, ignored
        _scalar_result(None),  # run.primary_suite_name (effective-suite)
        _all_result([SimpleNamespace(suite_name="Smoke", owner_user_id=new_owner)]),
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["already_assigned"] == 1
    assert counts["assigned"] == 0


@pytest.mark.asyncio
async def test_assign_falls_back_to_project_member_when_owner_config_unset():
    """When ``default_qa_lead_user_id`` and ``manager_user_id`` are both NULL
    AND no ``TestSuiteOwner`` row exists, a project member at QA_LEAD (or
    ADMIN as a secondary fallback) should still receive the assignment so
    the /my-failures inbox doesn't go permanently empty on fresh projects.
    """
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    qa_lead_member_id = uuid.uuid4()
    admin_member_id = uuid.uuid4()
    failures = [
        SimpleNamespace(id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None, test_fingerprint="fp-smoke"),
    ]
    member_rows = [
        # Order is created_at ASC; the service prefers QA_LEAD over ADMIN
        # regardless of insertion order so even though ADMIN is older here,
        # the QA_LEAD wins.
        SimpleNamespace(user_id=admin_member_id, role=UserRole.ADMIN.value),
        SimpleNamespace(user_id=qa_lead_member_id, role=UserRole.QA_LEAD.value),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((None, None)),       # no owner config
        _all_result(member_rows),           # project members include a QA_LEAD
        _scalar_result(None),               # run.primary_suite_name (effective-suite)
        _all_result([]),                    # no explicit TestSuiteOwner row
        MagicMock(),                        # UPDATE
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["assigned"] == 1
    assert counts["unassigned"] == 0


@pytest.mark.asyncio
async def test_assign_falls_back_to_admin_when_no_qa_lead_member():
    """No QA_LEAD members → first ADMIN member is the fallback."""
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    admin_id = uuid.uuid4()
    failures = [
        SimpleNamespace(id=uuid.uuid4(), suite_name="Smoke", assigned_to_user_id=None, test_fingerprint="fp-smoke"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((None, None)),
        _all_result([
            SimpleNamespace(user_id=admin_id, role=UserRole.ADMIN.value),
        ]),
        _scalar_result(None),  # run.primary_suite_name (effective-suite)
        _all_result([]),
        MagicMock(),  # UPDATE
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["assigned"] == 1


@pytest.mark.asyncio
async def test_assign_distributes_across_qa_lead_pool():
    """When 2+ QA Leads are members of a project AND no explicit suite
    owner matches, failures get distributed across the pool by hashing
    test_fingerprint. The exact bucket selection is deterministic but
    not directly asserted — instead we assert that BOTH leads end up
    receiving assignments when fed enough distinct fingerprints (the
    odds of all 20 landing on one bucket are 1 in 2^19 ≈ negligible).
    """
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    lead_a = uuid.uuid4()
    lead_b = uuid.uuid4()
    failures = [
        SimpleNamespace(
            id=uuid.uuid4(), suite_name="Smoke",
            assigned_to_user_id=None, test_fingerprint=f"fp-{i}",
        )
        for i in range(20)
    ]
    member_rows = [
        SimpleNamespace(user_id=lead_a, role=UserRole.QA_LEAD.value),
        SimpleNamespace(user_id=lead_b, role=UserRole.QA_LEAD.value),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((None, None)),       # no explicit default / manager
        _all_result(member_rows),           # 2-QA-Lead pool
        _scalar_result(None),               # run.primary_suite_name (effective-suite)
        _all_result([]),                    # no TestSuiteOwner
        MagicMock(),                        # UPDATE bucket 1
        MagicMock(),                        # UPDATE bucket 2
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["assigned"] == 20
    assert counts["unassigned"] == 0


@pytest.mark.asyncio
async def test_assign_default_qa_lead_folded_into_pool():
    """When ``Project.default_qa_lead_user_id`` is set but the configured
    user isn't yet a QA_LEAD ProjectMember, they STILL go into the pool
    (deduped) so the assignment honours operator intent without waiting
    for membership backfill."""
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    default_id = uuid.uuid4()
    failures = [
        SimpleNamespace(
            id=uuid.uuid4(), suite_name="Smoke",
            assigned_to_user_id=None, test_fingerprint="fp",
        ),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((default_id, None)),  # default set, manager NULL
        _all_result([]),                      # no QA_LEAD project members yet
        _scalar_result(None),                 # run.primary_suite_name (effective-suite)
        _all_result([]),                      # no TestSuiteOwner
        MagicMock(),                          # UPDATE
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["assigned"] == 1
    assert counts["unassigned"] == 0


@pytest.mark.asyncio
async def test_assign_mixed_batch():
    """Three failures, three resolution outcomes — verify all counted correctly."""
    from app.services.failed_test_assignment_service import (
        assign_failed_tests_to_suite_owners,
    )

    explicit_owner = uuid.uuid4()
    default_qa_lead = uuid.uuid4()
    failures = [
        SimpleNamespace(id=uuid.uuid4(), suite_name="A", assigned_to_user_id=None, test_fingerprint="fp-a"),
        SimpleNamespace(id=uuid.uuid4(), suite_name="B", assigned_to_user_id=None, test_fingerprint="fp-b"),
        SimpleNamespace(id=uuid.uuid4(), suite_name="A", assigned_to_user_id=uuid.uuid4(), test_fingerprint="fp-a2"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _all_result(failures),
        _first_result((default_qa_lead, None)),
        _all_result([]),  # _resolve_qa_lead_pool — empty; default_qa_lead is folded in
        _scalar_result(None),  # run.primary_suite_name (effective-suite)
        _all_result([SimpleNamespace(suite_name="A", owner_user_id=explicit_owner)]),
        MagicMock(),  # UPDATE bucket 1 (explicit_owner)
        MagicMock(),  # UPDATE bucket 2 (default_qa_lead via pool)
    ])

    counts = await assign_failed_tests_to_suite_owners(db, uuid.uuid4(), uuid.uuid4())
    assert counts["assigned"] == 2       # A→explicit, B→default fallback
    assert counts["already_assigned"] == 1
    assert counts["unassigned"] == 0
