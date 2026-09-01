"""S-P promotion, retirement, orphan, evidence-gap, and metric contracts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.models.postgres import ManagedTestCase
from app.services import test_suite_service as svc
from tests.conftest import FakeExecuteResult


def _actor():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="qa.lead",
        full_name="QA Lead",
    )


def _canonical(**overrides):
    values = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "test_suite_id": uuid.uuid4(),
        "test_fingerprint": "pkg.LoginTest::test_valid_login[param=admin]",
        "test_name": "test_valid_login",
        "class_name": "pkg.LoginTest",
        "status": "active",
        "source": "execution",
        "managed_test_case_id": None,
        "retirement_confirmed_at": None,
        "retirement_confirmed_by_id": None,
        "retirement_reason": None,
        "deleted_observed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session_for_scalar(value, *, suite=None):
    db = SimpleNamespace(
        execute=AsyncMock(return_value=FakeExecuteResult(scalar_value=value)),
        get=AsyncMock(return_value=suite),
        add=Mock(),
        flush=AsyncMock(),
    )

    async def _flush():
        for call in db.add.call_args_list:
            obj = call.args[0]
            if isinstance(obj, ManagedTestCase) and obj.id is None:
                obj.id = uuid.uuid4()

    db.flush.side_effect = _flush
    return db


@pytest.mark.asyncio
async def test_promotion_copies_identity_verbatim_and_stages_complete_draft(monkeypatch):
    canonical = _canonical()
    suite = SimpleNamespace(id=canonical.test_suite_id, name="Regression")
    actor = _actor()
    db = _session_for_scalar(canonical, suite=suite)
    canonical_result = FakeExecuteResult(scalar_value=canonical)
    fingerprint_result = FakeExecuteResult()
    fingerprint_result.scalars = lambda: SimpleNamespace(all=lambda: [])
    db.execute.side_effect = [canonical_result, fingerprint_result]
    stage_counter = Mock()
    monkeypatch.setattr(svc, "stage_test_management_counter", stage_counter)

    returned, managed = await svc.promote_canonical_test_case(
        db, canonical.id, actor
    )

    assert returned is canonical
    assert managed.status == "draft"
    assert managed.version == 1
    assert managed.author_id == actor.id
    assert managed.project_id == canonical.project_id
    assert managed.test_suite_id == canonical.test_suite_id
    assert managed.suite_name == "Regression"
    assert managed.test_type == "automation"
    assert managed.is_automated is True
    assert managed.automation_status == "automated"
    assert managed.test_fingerprint == canonical.test_fingerprint
    assert canonical.managed_test_case_id == managed.id
    assert canonical.source == "linked"

    staged = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "TestCaseVersion"
    ]
    assert len(staged) == 1
    assert staged[0].test_fingerprint == canonical.test_fingerprint
    assert staged[0].status == "draft"
    assert "test_fingerprint" in staged[0].changed_fields
    stage_counter.assert_called_once_with(
        db, "promotion", (str(canonical.project_id),)
    )


@pytest.mark.asyncio
async def test_promotion_reuses_one_existing_same_fingerprint_case(monkeypatch):
    canonical = _canonical()
    actor = _actor()
    managed = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=canonical.project_id,
        test_fingerprint=canonical.test_fingerprint,
        status="approved",
        created_at=datetime.now(timezone.utc),
    )
    canonical_result = FakeExecuteResult(scalar_value=canonical)
    fingerprint_result = FakeExecuteResult()
    fingerprint_result.scalars = lambda: SimpleNamespace(all=lambda: [managed])
    existing_link_result = FakeExecuteResult(scalar_value=None)
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                canonical_result,
                fingerprint_result,
                existing_link_result,
            ]
        ),
        get=AsyncMock(),
        add=Mock(),
        flush=AsyncMock(),
    )
    stage_counter = Mock()
    monkeypatch.setattr(svc, "stage_test_management_counter", stage_counter)

    returned, promoted = await svc.promote_canonical_test_case(
        db, canonical.id, actor
    )

    assert returned is canonical
    assert promoted is managed
    assert canonical.managed_test_case_id == managed.id
    assert canonical.source == "linked"
    assert not any(
        isinstance(call.args[0], ManagedTestCase)
        for call in db.add.call_args_list
    )
    stage_counter.assert_called_once_with(
        db, "promotion", (str(canonical.project_id),)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["deprecated", "archived"])
async def test_promotion_refuses_terminal_same_fingerprint_without_side_effects(
    terminal_status,
    monkeypatch,
):
    canonical = _canonical()
    managed = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=canonical.project_id,
        test_fingerprint=canonical.test_fingerprint,
        status=terminal_status,
        created_at=datetime.now(timezone.utc),
    )
    canonical_result = FakeExecuteResult(scalar_value=canonical)
    fingerprint_result = FakeExecuteResult()
    fingerprint_result.scalars = lambda: SimpleNamespace(all=lambda: [managed])
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[canonical_result, fingerprint_result]),
        get=AsyncMock(),
        add=Mock(),
        flush=AsyncMock(),
    )
    audit = AsyncMock()
    counter = Mock()
    monkeypatch.setattr(svc, "audit_event", audit)
    monkeypatch.setattr(svc, "stage_test_management_counter", counter)

    with pytest.raises(HTTPException) as exc:
        await svc.promote_canonical_test_case(db, canonical.id, _actor())

    assert exc.value.status_code == 409
    assert "terminal" in exc.value.detail
    assert canonical.managed_test_case_id is None
    audit.assert_not_awaited()
    counter.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_promotion_refuses_ambiguous_same_fingerprint_cases_without_linking():
    canonical = _canonical()
    matches = [
        SimpleNamespace(id=uuid.uuid4(), created_at=datetime.now(timezone.utc)),
        SimpleNamespace(id=uuid.uuid4(), created_at=datetime.now(timezone.utc)),
    ]
    canonical_result = FakeExecuteResult(scalar_value=canonical)
    fingerprint_result = FakeExecuteResult()
    fingerprint_result.scalars = lambda: SimpleNamespace(all=lambda: matches)
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[canonical_result, fingerprint_result]),
        get=AsyncMock(),
        add=Mock(),
        flush=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc:
        await svc.promote_canonical_test_case(db, canonical.id, _actor())

    assert exc.value.status_code == 409
    assert "Multiple managed test cases" in exc.value.detail
    assert canonical.managed_test_case_id is None
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_promotion_is_row_locked_and_rejects_double_promotion():
    canonical = _canonical(managed_test_case_id=uuid.uuid4(), source="linked")
    db = _session_for_scalar(canonical)

    with pytest.raises(HTTPException) as exc:
        await svc.promote_canonical_test_case(db, canonical.id, _actor())

    assert exc.value.status_code == 409
    statement = db.execute.await_args.args[0]
    assert "FOR UPDATE" in str(statement.compile()).upper()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_promotion_refuses_canonical_without_stable_fingerprint():
    canonical = _canonical(test_fingerprint="")
    db = _session_for_scalar(canonical)

    with pytest.raises(HTTPException) as exc:
        await svc.promote_canonical_test_case(db, canonical.id, _actor())

    assert exc.value.status_code == 409
    assert "fingerprint" in exc.value.detail


@pytest.mark.asyncio
async def test_unlink_preserves_both_rows_and_audits_the_required_reason():
    managed_id = uuid.uuid4()
    canonical = _canonical(managed_test_case_id=managed_id, source="linked")
    db = _session_for_scalar(canonical)

    returned = await svc.unlink_canonical_managed_case(
        db, canonical.id, _actor(), reason="Wrong authored-case association"
    )

    assert returned is canonical
    assert canonical.managed_test_case_id is None
    assert canonical.source == "execution"
    # Unlink is identity separation, not deletion of either source row.
    assert not hasattr(db, "delete")
    audits = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "TestCaseAuditLog"
    ]
    assert len(audits) == 1
    assert audits[0].reason == "Wrong authored-case association"
    assert audits[0].old_values["managed_test_case_id"] == str(managed_id)


@pytest.mark.asyncio
async def test_unlink_unlinked_canonical_is_conflict_without_writes():
    db = _session_for_scalar(_canonical())
    with pytest.raises(HTTPException) as exc:
        await svc.unlink_canonical_managed_case(
            db, uuid.uuid4(), _actor(), reason="No longer equivalent"
        )
    assert exc.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [svc.unlink_canonical_managed_case, svc.confirm_canonical_retirement],
)
async def test_direct_governance_reason_over_500_is_422_before_database_access(
    operation,
):
    db = SimpleNamespace(execute=AsyncMock(), add=Mock(), flush=AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await operation(
            db,
            uuid.uuid4(),
            _actor(),
            reason="x" * 501,
        )

    assert exc.value.status_code == 422
    assert "500" in exc.value.detail
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_retirement_confirmation_requires_deleted_unconfirmed_case():
    for canonical, detail in (
        (_canonical(status="active"), "Only a deleted"),
        (
            _canonical(
                status="deleted",
                retirement_confirmed_at=datetime.now(timezone.utc),
            ),
            "already confirmed",
        ),
    ):
        db = _session_for_scalar(canonical)
        with pytest.raises(HTTPException) as exc:
            await svc.confirm_canonical_retirement(
                db, canonical.id, _actor(), reason="Removed from repository"
            )
        assert exc.value.status_code == 409
        assert detail in exc.value.detail


@pytest.mark.asyncio
async def test_retirement_confirmation_captures_actor_reason_and_timestamp():
    actor = _actor()
    canonical = _canonical(
        status="deleted",
        deleted_observed_at=datetime.now(timezone.utc),
    )
    db = _session_for_scalar(canonical)

    await svc.confirm_canonical_retirement(
        db, canonical.id, actor, reason="Feature intentionally retired"
    )

    assert canonical.retirement_confirmed_at is not None
    assert canonical.retirement_confirmed_at.tzinfo is timezone.utc
    assert canonical.retirement_confirmed_by_id == actor.id
    assert canonical.retirement_reason == "Feature intentionally retired"
    audits = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "TestCaseAuditLog"
    ]
    assert len(audits) == 1
    assert audits[0].reason == "Feature intentionally retired"


@pytest.mark.asyncio
async def test_orphan_query_is_project_scoped_unconfirmed_and_updates_gauge(monkeypatch):
    project_id = uuid.uuid4()
    orphan = _canonical(
        project_id=project_id,
        status="deleted",
        deleted_observed_at=datetime.now(timezone.utc),
    )
    total_result = FakeExecuteResult(scalar_value=1)
    rows_result = FakeExecuteResult()
    rows_result.scalars = lambda: SimpleNamespace(all=lambda: [orphan])
    count_result = FakeExecuteResult(all_value=[(project_id, 1)])
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[total_result, rows_result, count_result]
        )
    )
    metric = Mock()
    monkeypatch.setattr(svc, "automation_cases_orphaned", metric)

    rows, total = await svc.list_orphaned_canonical_cases(
        db, [project_id], page=2, size=7
    )
    assert rows == [orphan]
    assert total == 1

    compiled = str(db.execute.await_args_list[1].args[0].compile())
    assert "canonical_test_cases.status" in compiled
    assert "retirement_confirmed_at IS NULL" in compiled
    assert "canonical_test_cases.project_id IN" in compiled
    assert "deleted_observed_at ASC NULLS FIRST" in compiled
    assert "LIMIT" in compiled and "OFFSET" in compiled
    metric.labels.assert_called_once_with(str(project_id))
    metric.labels.return_value.set.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_orphan_gauge_is_reset_when_scoped_project_has_no_orphans(monkeypatch):
    project_id = uuid.uuid4()
    total_result = FakeExecuteResult(scalar_value=0)
    rows_result = FakeExecuteResult()
    rows_result.scalars = lambda: SimpleNamespace(all=lambda: [])
    count_result = FakeExecuteResult(all_value=[])
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[total_result, rows_result, count_result]
        )
    )
    metric = Mock()
    monkeypatch.setattr(svc, "automation_cases_orphaned", metric)

    assert await svc.list_orphaned_canonical_cases(db, [project_id]) == ([], 0)
    metric.labels.assert_called_once_with(str(project_id))
    metric.labels.return_value.set.assert_called_once_with(0)


@pytest.mark.asyncio
async def test_orphan_gauge_failure_is_best_effort(monkeypatch):
    project_id = uuid.uuid4()
    total_result = FakeExecuteResult(scalar_value=0)
    rows_result = FakeExecuteResult()
    rows_result.scalars = lambda: SimpleNamespace(all=lambda: [])
    count_result = FakeExecuteResult(all_value=[])
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[total_result, rows_result, count_result]
        )
    )
    metric = Mock()
    metric.labels.return_value.set.side_effect = RuntimeError("registry down")
    monkeypatch.setattr(svc, "automation_cases_orphaned", metric)

    assert await svc.list_orphaned_canonical_cases(db, [project_id]) == ([], 0)
    metric.labels.return_value.set.assert_called_once_with(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "required_sql"),
    [
        ("never_executed", "last_executed_at IS NULL"),
        ("automation_vanished", "canonical_test_cases.status"),
    ],
)
async def test_evidence_gap_queries_are_project_scoped(kind, required_sql):
    project_id = uuid.uuid4()
    managed = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        title="Login",
        status="active",
        last_executed_at=None,
    )
    canonical = None if kind == "never_executed" else _canonical(
        project_id=project_id,
        status="deleted",
        deleted_observed_at=datetime.now(timezone.utc),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                FakeExecuteResult(scalar_value=1),
                FakeExecuteResult(all_value=[(managed, canonical)]),
            ]
        )
    )

    rows, total = await svc.list_test_case_evidence_gaps(
        db, [project_id], kind=kind, page=3, size=13
    )

    assert total == 1
    assert rows[0]["id"] == managed.id
    assert rows[0]["canonical_test_case_id"] == (
        canonical.id if canonical is not None else None
    )
    compiled = str(db.execute.await_args_list[1].args[0].compile())
    assert "managed_test_cases.project_id IN" in compiled
    assert required_sql in compiled
    assert "LIMIT" in compiled and "OFFSET" in compiled


@pytest.mark.asyncio
async def test_evidence_gap_unknown_kind_and_empty_scope_fail_closed():
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await svc.list_test_case_evidence_gaps(db, None, kind="unknown")
    assert exc.value.status_code == 422

    assert await svc.list_test_case_evidence_gaps(
        db, [], kind="never_executed"
    ) == ([], 0)
    db.execute.assert_not_awaited()
