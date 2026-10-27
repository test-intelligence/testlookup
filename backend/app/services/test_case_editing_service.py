"""Editing services for authored test cases (W28: TCM-03 bulk, TCM-04 restore and history).

* :func:`apply_bulk_action` changes the status, owner, priority, tags or suite of
  up to :data:`BULK_MAX_IDS` cases of ONE project in one request. Every case is
  isolated in a SAVEPOINT, so one refused case rolls back only itself and the
  answer says, per case, ``changed`` / ``unchanged`` / ``skipped`` (with one
  sentence) / ``not_found``. A foreign or unknown id is ``not_found``: the load
  is filtered by the project, so the two are indistinguishable (no 403 oracle).
* :func:`restore_case_version` saves the content of an older version as a NEW
  version. Status, owner and fingerprint are never restored.
* :func:`case_history` is the History tab's data: every version with the name
  of whoever saved it, where the change came from, masked parameters and the
  plan executions that ran against it.

Stage-only: nothing here commits or rolls back the request session (the router
owns the commit). Status is written ONLY by ``transition()``; field writes go
through ``update_managed_test_case`` (which applies them through
``apply_model_updates`` and its ``status`` deny-list). This is the only W28
module that records activity-ledger rows (``test_case.bulk_changed``,
``test_case.restored``), both on the request session.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional

import structlog
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    ManagedTestCase,
    TestCaseVersion,
    TestPlan,
    TestPlanItem,
    User,
)
from app.models.schemas import (
    ManagedTestCaseUpdate,
    TestCaseBulkItemResult,
    TestCaseBulkRequest,
    TestCaseBulkResponse,
    TestCaseRestoreRequest,
    TestCaseVersionExecution,
    TestCaseVersionResponse,
)
from app.services.activity.service import ActorRef, record as record_activity
from app.services.test_case_lifecycle_service import (
    RETIRE_ACTIONS,
    RETIRE_REFUSAL,
    RETIRE_ROLES,
    RETIRED_STATES,
    transition,
)
from app.services.test_case_parameters import mask_parameters
from app.services.test_management_audit_service import activity_summarised_elsewhere
from app.services.test_management_service import (
    require_assignable,
    update_managed_test_case,
)

logger = structlog.get_logger(__name__)

# ── Vocabularies (tests import them; the request fields are plain ``str``) ───

BULK_MAX_IDS = 200
BULK_ACTIONS = ("transition", "set_owner", "set_priority", "add_tags", "remove_tags", "move_to_suite")
BULK_TRANSITIONS = (
    "activate", "revise", "flag_stale", "request_review", "deprecate", "archive", "reinstate",
)
BULK_OUTCOMES = ("changed", "unchanged", "skipped", "not_found")
PRIORITIES = ("critical", "high", "medium", "low")
RESTORABLE_FIELDS = (
    "title", "description", "objective", "preconditions", "steps", "parameters", "expected_result",
    "test_data", "test_type", "priority", "severity", "feature_area", "suite_name", "tags",
    "estimated_duration_minutes", "is_automated", "automation_status",
)   # never status, owner or fingerprint

#: The retire moves (the lifecycle's ``RETIRE_ACTIONS``). They stay QA_LEAD or
#: above (W28 contract AMENDMENT, 2026-10-17: OR-04 keeps today's rule), and a
#: bulk request that asks for one below that is refused WHOLE, with the
#: lifecycle's own sentence (``RETIRE_REFUSAL``), before anything is loaded.
RETIRE_TRANSITIONS = tuple(sorted(action.value for action in RETIRE_ACTIONS))
#: Status changes that need a reason (``REASON_REQUIRED_ACTIONS`` of the lifecycle).
_REASON_TRANSITIONS = ("flag_stale", "deprecate", "archive", "reinstate")
#: The lifecycle stores a flag_stale reason in a 100-character column.
_FLAG_STALE_REASON_MAX = 100
_TAG_MAX = 100
#: Columns that are NOT NULL on ``managed_test_cases``: a snapshot that did not
#: record one (``None``) leaves the case's value as it is instead of failing.
_NOT_NULL_FIELDS = frozenset({
    "title", "test_type", "priority", "severity", "is_automated", "automation_status",
})
#: The history route's cap (unchanged) and the executions read's.
HISTORY_LIMIT = 100
EXECUTIONS_LIMIT = 200


def _role(actor: User) -> str:
    role = getattr(actor, "role", None)
    return role.value if isinstance(role, Enum) else str(role)


def _normalised(value: Any) -> str:
    """One comparable text for a stored value (steps / parameters / tags are JSON)."""
    return json.dumps(value, sort_keys=True, default=str)


def _display_name(full_name: Optional[str], username: Optional[str]) -> Optional[str]:
    return (full_name or "").strip() or username or None


def refuse_retire_below_lead(payload: TestCaseBulkRequest, actor: User) -> None:
    """403 for the WHOLE request: a retire move asked for by someone below QA_LEAD."""
    if (
        payload.action == "transition"
        and payload.transition in RETIRE_TRANSITIONS
        and _role(actor) not in RETIRE_ROLES
    ):
        raise HTTPException(status_code=403, detail=RETIRE_REFUSAL)


# ── Bulk (TCM-03) ────────────────────────────────────────────────────────────


def _clean_tags(raw: Optional[list[str]]) -> list[str]:
    tags: list[str] = []
    for tag in raw or []:
        text = str(tag).strip()
        if len(text) > _TAG_MAX:
            raise HTTPException(status_code=422, detail="A tag is at most 100 characters")
        if text and text not in tags:
            tags.append(text)
    if not tags:
        raise HTTPException(status_code=422, detail="Name at least one tag")
    return tags


async def _validated_value(
    db: AsyncSession, payload: TestCaseBulkRequest, actor: User
) -> tuple[Any, str]:
    """Judge the request before any case is loaded: (the value to apply, its change label)."""
    action = payload.action
    if action not in BULK_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Unknown bulk action: {action}")
    if action == "transition":
        move = payload.transition
        if move not in BULK_TRANSITIONS:
            raise HTTPException(
                status_code=422, detail=f"Unknown bulk status change: {move or '(none)'}"
            )
        reason = (payload.reason or "").strip()
        if move in _REASON_TRANSITIONS and not reason:
            raise HTTPException(status_code=422, detail=f"reason is required for {move}")
        if move == "flag_stale" and len(reason) > _FLAG_STALE_REASON_MAX:
            raise HTTPException(
                status_code=422, detail="reason must be at most 100 characters for flag_stale"
            )
        refuse_retire_below_lead(payload, actor)
        return move, ""
    if action == "set_priority":
        if payload.priority not in PRIORITIES:
            raise HTTPException(status_code=422, detail=f"Unknown priority: {payload.priority}")
        return payload.priority, f"priority → {payload.priority}"
    if action in ("add_tags", "remove_tags"):
        tags = _clean_tags(payload.tags)
        sign = "+" if action == "add_tags" else "-"
        return tags, f"tags {sign} {', '.join(tags)}"
    if action == "move_to_suite":
        suite = (payload.suite_name or "").strip()
        if not suite:
            raise HTTPException(status_code=422, detail="Name the suite to move to")
        return suite, f"suite → {suite}"
    # set_owner: None unassigns; anyone else must be assignable in THIS project.
    if payload.assignee_id is None:
        return None, "owner → unassigned"
    await require_assignable(db, payload.project_id, payload.assignee_id)
    owner = await db.get(User, payload.assignee_id)
    name = _display_name(getattr(owner, "full_name", None), getattr(owner, "username", None))
    return payload.assignee_id, f"owner → {name or 'someone'}"


def _metadata_update(
    action: str, case: ManagedTestCase, value: Any
) -> Optional[tuple[str, Any]]:
    """(field, new value) for this case, or ``None`` when it already has it."""
    if action == "set_priority":
        field, new = "priority", value
    elif action == "set_owner":
        field, new = "assignee_id", value
    elif action == "move_to_suite":
        field, new = "suite_name", value
    else:
        existing = [str(tag) for tag in (getattr(case, "tags", None) or [])]
        if action == "add_tags":
            new = existing + [tag for tag in value if tag not in existing]
        else:
            new = [tag for tag in existing if tag not in value]
        field = "tags"
        if new == existing:
            return None
        return field, new
    if getattr(case, field, None) == new:
        return None
    return field, new


def _skip_sentence(exc: HTTPException) -> str:
    """One sentence for a refused case, from the refusal the single-case route would give."""
    detail = exc.detail
    if isinstance(detail, str) and detail.strip():
        return detail
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message
        state = detail.get("current_state")
        if isinstance(state, str) and state:
            return f"Not available from {state}"
    return "This case could not be changed"


_CHANGED_FIELDS = {
    "transition": ["status"],
    "set_owner": ["assignee_id"],
    "set_priority": ["priority"],
    "add_tags": ["tags"],
    "remove_tags": ["tags"],
    "move_to_suite": ["suite_name"],
}


@activity_summarised_elsewhere
async def apply_bulk_action(
    db: AsyncSession,
    payload: TestCaseBulkRequest,
    actor: User,
    *,
    review_required: bool,
) -> TestCaseBulkResponse:
    """Apply one action to the requested cases of ``payload.project_id`` (staged only).

    The caller has resolved the project scope. Each case gets its own SAVEPOINT,
    its own version and its own audit row (written by ``transition()`` or the
    edit path); one ``test_case.bulk_changed`` ledger row covers the request
    when at least one case changed.
    """
    value, change = await _validated_value(db, payload, actor)

    ids = list(dict.fromkeys(payload.case_ids))
    if len(ids) > BULK_MAX_IDS:
        raise HTTPException(
            status_code=422, detail=f"At most {BULK_MAX_IDS} test cases per request"
        )
    # ONE statement, filtered by the project: an id of another project is not
    # loaded, so it answers exactly like an unknown id. Locked in a fixed order
    # so the values compared below are the ones the per-case writes will see.
    loaded = (
        await db.execute(
            select(ManagedTestCase)
            .where(
                ManagedTestCase.id.in_(ids),
                ManagedTestCase.project_id == payload.project_id,
            )
            .order_by(ManagedTestCase.id)
            .with_for_update()
        )
    ).scalars().all()
    by_id = {case.id: case for case in loaded}

    results: list[TestCaseBulkItemResult] = []
    new_statuses: list[str] = []
    for case_id in ids:
        case = by_id.get(case_id)
        if case is None:
            results.append(TestCaseBulkItemResult(id=case_id, outcome="not_found"))
            continue
        # Read before the SAVEPOINT: a rolled-back savepoint expires what it
        # touched, and an expired attribute cannot be read lazily here.
        version_before: int = case.version
        status_before: str = case.status

        def _skipped(reason: str) -> TestCaseBulkItemResult:
            return TestCaseBulkItemResult(
                id=case_id, outcome="skipped", reason=reason,
                version=version_before, status=status_before,
            )

        if payload.action != "transition" and status_before in RETIRED_STATES:
            results.append(_skipped("Reinstate it to edit it"))
            continue
        expected = payload.expected_versions.get(case_id)
        if expected is not None and expected != version_before:
            results.append(
                _skipped(f"Changed since you selected it (v{expected} → v{version_before})")
            )
            continue

        field_update: dict[str, Any] = {}
        if payload.action != "transition":
            update = _metadata_update(payload.action, case, value)
            if update is None:
                results.append(TestCaseBulkItemResult(
                    id=case_id, outcome="unchanged", version=version_before, status=status_before,
                ))
                continue
            field_update = {update[0]: update[1]}

        try:
            async with db.begin_nested():
                if payload.action == "transition":
                    moved = await transition(
                        db,
                        case_id,
                        payload.transition or "",
                        actor,
                        reason=payload.reason,
                        expected_version=expected,
                        review_required=review_required,
                    )
                    changed_case = moved.case
                else:
                    changed_case = await update_managed_test_case(
                        db,
                        case_id,
                        ManagedTestCaseUpdate(
                            expected_version=version_before,
                            change_summary=f"Bulk edit: {change}",
                            **field_update,
                        ),
                        actor,
                        change_type="bulk_updated",
                    )
                # Surface a database refusal INSIDE this case's savepoint.
                await db.flush()
                version_after: int = changed_case.version
                status_after: str = changed_case.status
        except HTTPException as exc:
            results.append(_skipped(_skip_sentence(exc)))
            continue
        except SQLAlchemyError as exc:
            logger.warning(
                "test_case_bulk_case_failed",
                test_case_id=str(case_id),
                action=payload.action,
                error_type=type(exc).__name__,
            )
            results.append(_skipped("This case could not be changed"))
            continue
        new_statuses.append(status_after)
        results.append(TestCaseBulkItemResult(
            id=case_id, outcome="changed", version=version_after, status=status_after,
        ))

    counts = {outcome: 0 for outcome in BULK_OUTCOMES}
    for item in results:
        counts[item.outcome] += 1

    if counts["changed"] > 0:
        if payload.action == "transition":
            change = "status → " + ", ".join(dict.fromkeys(new_statuses))
        await record_activity(
            db,
            project_id=payload.project_id,
            event_type="test_case.bulk_changed",
            actor=ActorRef.from_user(actor),
            entity_id=payload.project_id,
            entity_type="test_library",
            entity_label=f"{counts['changed']} test cases",
            context={
                "count": counts["changed"],
                "change": change,
                "skipped": counts["skipped"],
                "not_found": counts["not_found"],
            },
            changed_fields=_CHANGED_FIELDS[payload.action],
        )

    return TestCaseBulkResponse(
        action=payload.action,
        requested=len(ids),
        changed=counts["changed"],
        unchanged=counts["unchanged"],
        skipped=counts["skipped"],
        not_found=counts["not_found"],
        results=results,
    )


# ── Restore (TCM-04) ─────────────────────────────────────────────────────────


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid content"
    first = errors[0]
    where = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid"))
    return f"{where}: {message}" if where else message


async def restore_case_version(
    db: AsyncSession,
    test_case: ManagedTestCase,
    payload: TestCaseRestoreRequest,
    actor: User,
) -> ManagedTestCase:
    """Save the content of ``payload.version`` as a new version (staged only).

    Returns the case unchanged (no version, no ledger row) when nothing differs.
    Through the edit path, so the stale-version 409 is the PATCH's exact body.
    """
    number = payload.version
    if number == test_case.version:
        raise HTTPException(status_code=422, detail=f"v{number} is the current version")
    snapshot = (
        await db.execute(
            select(TestCaseVersion).where(
                TestCaseVersion.test_case_id == test_case.id,
                TestCaseVersion.version == number,
            )
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if test_case.status in RETIRED_STATES:
        raise HTTPException(
            status_code=409, detail=f"Cannot edit a {test_case.status} test case"
        )

    differing: dict[str, Any] = {}
    for field in RESTORABLE_FIELDS:
        old_value = getattr(snapshot, field, None)
        if old_value is None and field in _NOT_NULL_FIELDS:
            continue
        if _normalised(old_value) != _normalised(getattr(test_case, field, None)):
            differing[field] = old_value
    if not differing:
        return test_case

    try:
        update = ManagedTestCaseUpdate.model_validate({
            "expected_version": payload.expected_version,
            "change_summary": payload.change_summary or f"Restored from v{number}",
            **differing,
        })
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"v{number} cannot be restored: {_first_error(exc)}"
        ) from exc

    restored = await update_managed_test_case(
        db, test_case.id, update, actor, change_type="restored"
    )
    await record_activity(
        db,
        project_id=restored.project_id,
        event_type="test_case.restored",
        actor=ActorRef.from_user(actor),
        entity_id=restored.id,
        entity_type="test_case",
        entity_label=restored.title,
        context={"from_version": number, "to_version": restored.version},
        changed_fields=sorted(differing),
    )
    return restored


# ── History (TCM-04, TCM-05) ─────────────────────────────────────────────────


async def case_history(
    db: AsyncSession, test_case: ManagedTestCase
) -> list[TestCaseVersionResponse]:
    """Newest first, at most :data:`HISTORY_LIMIT` versions; two statements at most."""
    rows = (
        await db.execute(
            select(TestCaseVersion, User.full_name, User.username)
            .outerjoin(User, User.id == TestCaseVersion.changed_by_id)
            .where(TestCaseVersion.test_case_id == test_case.id)
            .order_by(TestCaseVersion.version.desc())
            .limit(HISTORY_LIMIT)
        )
    ).all()
    if not rows:
        return []

    executions = (
        await db.execute(
            select(
                TestPlanItem.executed_case_version,
                TestPlanItem.id,
                TestPlanItem.execution_status,
                TestPlanItem.executed_at,
                TestPlan.id,
                TestPlan.name,
            )
            .join(TestPlan, TestPlan.id == TestPlanItem.plan_id)
            .where(
                TestPlanItem.test_case_id == test_case.id,
                TestPlanItem.executed_case_version.is_not(None),
                TestPlan.project_id == test_case.project_id,
            )
            .order_by(TestPlanItem.executed_at.desc(), TestPlanItem.id)
            .limit(EXECUTIONS_LIMIT)
        )
    ).all()
    by_version: dict[int, list[TestCaseVersionExecution]] = {}
    for ran_version, item_id, execution_status, executed_at, plan_id, plan_name in executions:
        if not isinstance(ran_version, int):
            continue
        by_version.setdefault(ran_version, []).append(TestCaseVersionExecution(
            plan_id=plan_id,
            plan_name=plan_name,
            item_id=item_id,
            execution_status=str(execution_status),
            executed_at=executed_at,
        ))

    ai_generated = getattr(test_case, "ai_generated", None) is True
    history: list[TestCaseVersionResponse] = []
    for version, full_name, username in rows:
        data = {name: getattr(version, name, None) for name in TestCaseVersionResponse.model_fields}
        number = getattr(version, "version", None)
        data["parameters"] = mask_parameters(getattr(version, "parameters", None))
        data["changed_by_name"] = _display_name(full_name, username)
        data["source"] = getattr(version, "source", None) or (
            "ai" if number == 1 and ai_generated else None
        )
        data["executions"] = by_version.get(number, []) if isinstance(number, int) else []
        history.append(TestCaseVersionResponse.model_validate(data))
    return history


__all__ = [
    "BULK_ACTIONS",
    "BULK_MAX_IDS",
    "BULK_OUTCOMES",
    "BULK_TRANSITIONS",
    "PRIORITIES",
    "RESTORABLE_FIELDS",
    "RETIRE_REFUSAL",
    "RETIRE_TRANSITIONS",
    "apply_bulk_action",
    "case_history",
    "refuse_retire_below_lead",
    "restore_case_version",
]
