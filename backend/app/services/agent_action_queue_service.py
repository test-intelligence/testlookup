"""The agent-action queue: the list, one row, and the approve check (W15 AGT-01, contract §1.3 / §2.1).

Reads only and commit-free: nothing here writes, commits or opens a session. Every
statement rides the caller's (the request's) session -- the live Act gate too
(``act_refusal`` resolves the investigator config with
``global_ai_config=await get_effective_ai_config(db=db)``), so a request never
holds its connection while it waits for a second one
(``tests/regression/test_no_second_session_while_the_request_holds_one.py``).

The list is at most three reads (§2.1):

1. the ledger page with ``count(*) OVER ()`` as ``total``, LEFT JOINed to the run
   (build number, branch), the proposing pipeline (workflow type, requester) and
   the decider (name). Every QUERY id is applied INSIDE ``project_id = :p``, so a
   foreign id matches nothing -- never a lookup by that id alone;
2. ONE ``review_requests`` read for the page's pipelines (newest report review per
   pipeline) -> ``proposing_review_state`` and ``review_id``;
3. only for a pending row whose type has an effect: the live Act gate, once per
   (type, proposer) pair on the page -- never once per row.

``result_payload["rejection"]["notes"]`` is stripped from every answer (D-11).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentActionLedger, AgentPipelineRun, ReviewRequest, TestRun, User
from app.models.schemas import AgentActionListResponse, AgentActionRead
from app.services.agent_action_adapters.adapter_base import act_refusal
from app.services.agent_action_types import (
    ACTION_ERROR_SENTENCES,
    ACTION_TYPE_ALIASES,
    DECIDED_STATUSES,
    DECISION_REFUSAL_SENTENCES,
    PROPOSING_REVIEW_STATES,
    RISK_LEVELS,
    ActionTypeSpec,
    canonical_action_type,
    outcome_of,
    proposer_label,
    rejection_reason_code,
    resolve_action_type,
    result_link_of,
    target_of,
)

__all__ = [
    "APPROVABLE_STATUSES",
    "REJECTABLE_STATUSES",
    "action_type_filter",
    "approval_refusal",
    "get_action_for_update",
    "list_actions",
    "proposing_review",
    "read_action",
    "refusal_message",
    "serialize_action",
]

#: A person may approve a row in these statuses (``transition_action`` allows both).
APPROVABLE_STATUSES: tuple[str, ...] = ("proposed", "pending_review")
#: ... and reject it in these (not once the executor has claimed it).
REJECTABLE_STATUSES: tuple[str, ...] = ("proposed", "pending_review", "approved")
MAX_LIMIT = 200

Refusal = tuple[int, str]


@dataclass(frozen=True)
class _Facts:
    """What the page statement joined onto one ledger row."""

    build_number: Optional[str] = None
    branch: Optional[str] = None
    workflow_type: Optional[str] = None
    requested_by: Optional[uuid.UUID] = None
    decider_name: Optional[str] = None


def refusal_message(code: str, *, status: Optional[str] = None) -> str:
    """The one sentence of a refusal code: the PATCH's own codes first, then the stored ones."""
    text = DECISION_REFUSAL_SENTENCES.get(code) or ACTION_ERROR_SENTENCES.get(code) or code
    return text.replace("{status}", status or "decided").replace("{type}", "agent action")


def action_type_filter(raw: str) -> tuple[str, ...]:
    """``raw`` and every stored spelling of the same catalogue type (aliases, both ways)."""
    canonical = ACTION_TYPE_ALIASES.get(raw, raw)
    names = {raw, canonical}
    names.update(alias for alias, target in ACTION_TYPE_ALIASES.items() if target == canonical)
    return tuple(sorted(names))


async def get_action_for_update(
    db: AsyncSession, project_id: uuid.UUID, action_id: uuid.UUID
) -> Optional[AgentActionLedger]:
    """The row ``WHERE id AND project_id ... FOR UPDATE`` (another project's id: None, no oracle).

    ``populate_existing`` so a re-lock after a target service committed the
    session reads the row as it is now, not as the identity map last saw it.
    """
    result = await db.execute(
        select(AgentActionLedger)
        .where(AgentActionLedger.id == action_id, AgentActionLedger.project_id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def _review_statement(project_id: uuid.UUID, pipeline_ids: Sequence[uuid.UUID]) -> Any:
    """Every report review of these pipelines, newest first (matched as the executor's gate does)."""
    return (
        select(ReviewRequest.pipeline_run_id, ReviewRequest.id, ReviewRequest.state)
        .where(
            ReviewRequest.project_id == project_id,
            ReviewRequest.kind == "report",
            ReviewRequest.subject_type == "pipeline_run",
            ReviewRequest.subject_id.in_([str(p) for p in pipeline_ids]),
            ReviewRequest.pipeline_run_id.in_(list(pipeline_ids)),
        )
        .order_by(ReviewRequest.created_at.desc(), ReviewRequest.id.desc())
    )


async def _reviews_by_pipeline(
    db: AsyncSession, project_id: uuid.UUID, pipeline_ids: Sequence[uuid.UUID]
) -> dict[Any, tuple[str, Optional[uuid.UUID]]]:
    if not pipeline_ids:
        return {}
    newest: dict[Any, tuple[str, Optional[uuid.UUID]]] = {}
    for pipeline_id, review_id, state in (await db.execute(_review_statement(project_id, pipeline_ids))).all():
        if pipeline_id in newest:
            continue
        newest[pipeline_id] = (state if state in PROPOSING_REVIEW_STATES else "none", review_id)
    return newest


async def proposing_review(db: AsyncSession, action: Any) -> tuple[str, Optional[uuid.UUID]]:
    """(``PROPOSING_REVIEW_STATES`` member, review id) of the action's proposing run; ``none`` without one."""
    pipeline_id = getattr(action, "pipeline_run_id", None)
    if pipeline_id is None:
        return "none", None
    reviews = await _reviews_by_pipeline(db, action.project_id, [pipeline_id])
    return reviews.get(pipeline_id, ("none", None))


async def _pipeline_requester(db: AsyncSession, pipeline_run_id: Any) -> Optional[uuid.UUID]:
    if pipeline_run_id is None:
        return None
    result = await db.execute(
        select(AgentPipelineRun.requested_by).where(AgentPipelineRun.id == pipeline_run_id)
    )
    return result.scalar_one_or_none()


def _proposer(action: Any) -> Optional[str]:
    payload = getattr(action, "request_payload", None)
    agent_id = payload.get("proposing_agent_id") if isinstance(payload, Mapping) else None
    return agent_id[:120] if isinstance(agent_id, str) and agent_id else None


async def _refusal(
    db: AsyncSession,
    *,
    action: Any,
    spec: Optional[ActionTypeSpec],
    approver_id: Any,
    review_state: Callable[[], Awaitable[str]],
    requester: Callable[[], Awaitable[Optional[uuid.UUID]]],
    act_codes: dict[tuple[str, Optional[str]], Optional[str]],
) -> Optional[Refusal]:
    """The approve check in the contract's order; each fact is read only when its step is reached."""
    if action.status not in APPROVABLE_STATUSES:
        return 409, "action_not_pending"
    if spec is None:
        # D-2: the executor would end it ``action_executor_not_configured``; refuse up front.
        return 409, "action_executor_not_configured"
    if action.pipeline_run_id is not None and await review_state() != "accepted":
        return 409, "review_not_accepted"
    if spec.side_effect == "none":
        return None  # acknowledge: the review gate only -- the requester may acknowledge (D-3)
    key = (spec.action_type, _proposer(action))
    if key not in act_codes:
        act_codes[key] = await act_refusal(
            db, project_id=action.project_id, spec=spec, proposing_agent_id=key[1],
        )
    if act_codes[key] is not None:
        return 409, str(act_codes[key])
    requested_by = await requester()
    if requested_by is not None and approver_id is not None and requested_by == approver_id:
        return 403, "separation_of_duties"
    return None


async def approval_refusal(
    db: AsyncSession, *, action: Any, spec: Optional[ActionTypeSpec], approver: Any
) -> Optional[Refusal]:
    """(status_code, code) the PATCH refuses an approval with, or None (§1.3 order).

    Not pending (409 ``action_not_pending``) -> unregistered type (409
    ``action_executor_not_configured``) -> the proposing report not accepted
    (409 ``review_not_accepted``, rows WITH a pipeline only) -> the live Act gate
    (409 ``act_mode_required`` / ``action_type_not_allowed`` /
    ``proposer_not_permitted``) -> separation of duties (403: an effectful type
    and the approver requested the proposing pipeline).
    """

    async def _review() -> str:
        return (await proposing_review(db, action))[0]

    async def _requester() -> Optional[uuid.UUID]:
        return await _pipeline_requester(db, action.pipeline_run_id)

    return await _refusal(
        db, action=action, spec=spec, approver_id=getattr(approver, "id", None),
        review_state=_review, requester=_requester, act_codes={},
    )


def _text(payload: Mapping[str, Any], key: str, limit: int) -> Optional[str]:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _strip_notes(result_payload: Any) -> Any:
    """``result_payload`` without ``rejection.notes`` (stored redacted, never returned: D-11)."""
    if not isinstance(result_payload, dict):
        return result_payload
    rejection = result_payload.get("rejection")
    if not isinstance(rejection, dict) or "notes" not in rejection:
        return result_payload
    return {**result_payload, "rejection": {k: v for k, v in rejection.items() if k != "notes"}}


def serialize_action(
    row: AgentActionLedger,
    *,
    facts: Optional[_Facts] = None,
    review: tuple[str, Optional[uuid.UUID]] = ("none", None),
    refusal: Optional[Refusal] = None,
) -> AgentActionRead:
    """One row as ``AgentActionRead``: the twenty existing keys + the §1.1 fields (typed fields only)."""
    facts = facts or _Facts()
    spec = resolve_action_type(row.action_type)
    payload: Mapping[str, Any] = row.request_payload if isinstance(row.request_payload, dict) else {}
    target = target_of(spec, payload, test_run_id=row.test_run_id) if spec is not None else None
    outcome, outcome_text = outcome_of(
        row, spec, decider_name=facts.decider_name, target_label=target.label if target else None,
    )
    pending = row.status in APPROVABLE_STATUSES
    risk = payload.get("risk")
    agent_id = _proposer(row)
    return AgentActionRead(
        id=str(row.id),
        project_id=str(row.project_id),
        test_run_id=str(row.test_run_id) if row.test_run_id else None,
        pipeline_run_id=str(row.pipeline_run_id) if row.pipeline_run_id else None,
        action_type=row.action_type,
        target_type=row.target_type,
        target_id=row.target_id,
        status=row.status,
        approval_required=bool(row.approval_required),
        idempotency_key=row.idempotency_key,
        request_sha256=row.request_sha256,
        request_payload=dict(payload),
        approved_by=str(row.approved_by) if row.approved_by else None,
        approved_at=_iso(row.approved_at),
        execution_started_at=_iso(row.execution_started_at),
        execution_completed_at=_iso(row.execution_completed_at),
        result_payload=_strip_notes(row.result_payload),
        rollback_payload=row.rollback_payload,
        error_code=row.error_code,
        created_at=_iso(row.created_at),
        canonical_type=canonical_action_type(row.action_type),
        title=_text(payload, "title", 200),
        rationale=_text(payload, "rationale", 500),
        risk=risk if isinstance(risk, str) and risk in RISK_LEVELS else None,
        side_effect=spec.side_effect if spec is not None else None,
        reversible=bool(spec is not None and spec.reversible),
        proposing_agent_id=agent_id,
        proposing_agent_label=proposer_label(agent_id),
        proposing_review_state=review[0],
        review_id=str(review[1]) if review[1] else None,
        test_run_build_number=facts.build_number,
        test_run_branch=facts.branch,
        pipeline_workflow_type=facts.workflow_type,
        approved_by_name=facts.decider_name,
        target=target,
        outcome=outcome,
        outcome_text=outcome_text,
        approvable=pending and refusal is None,
        approve_blocked_reason=(
            refusal_message(refusal[1], status=row.status) if pending and refusal is not None else None
        ),
        result_link=result_link_of(row),
        rejection_reason_code=rejection_reason_code(row),
    )


def _page_statement(
    project_id: uuid.UUID,
    *,
    view: Optional[str] = None,
    status: Optional[str] = None,
    action_type: Optional[str] = None,
    pipeline_run_id: Optional[uuid.UUID] = None,
    test_run_id: Optional[uuid.UUID] = None,
    action_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> Any:
    ledger = AgentActionLedger
    stmt = (
        select(
            ledger,
            TestRun.build_number,
            TestRun.branch,
            AgentPipelineRun.workflow_type,
            AgentPipelineRun.requested_by,
            User.full_name,
            User.username,
            func.count().over().label("total"),
        )
        .select_from(ledger)
        .outerjoin(TestRun, (TestRun.id == ledger.test_run_id) & (TestRun.project_id == ledger.project_id))
        .outerjoin(AgentPipelineRun, AgentPipelineRun.id == ledger.pipeline_run_id)
        .outerjoin(User, User.id == ledger.approved_by)
        .where(ledger.project_id == project_id)  # every filter below rides INSIDE the project
    )
    if action_id is not None:
        stmt = stmt.where(ledger.id == action_id)
    if pipeline_run_id is not None:
        stmt = stmt.where(ledger.pipeline_run_id == pipeline_run_id)
    if test_run_id is not None:
        stmt = stmt.where(ledger.test_run_id == test_run_id)
    if action_type:
        stmt = stmt.where(ledger.action_type.in_(action_type_filter(action_type)))
    if status:
        stmt = stmt.where(ledger.status == status)
    if view == "pending":
        stmt = stmt.where(ledger.status == "pending_review").order_by(ledger.created_at.asc(), ledger.id.asc())
    elif view == "decided":
        decided_at = func.coalesce(ledger.execution_completed_at, ledger.approved_at, ledger.updated_at)
        stmt = stmt.where(ledger.status.in_(DECIDED_STATUSES)).order_by(decided_at.desc(), ledger.id.desc())
    else:
        stmt = stmt.order_by(ledger.created_at.desc(), ledger.id.desc())
    return stmt.limit(max(1, min(int(limit), MAX_LIMIT)))


async def _build(
    db: AsyncSession, project_id: uuid.UUID, rows: Sequence[Any], viewer: Any
) -> list[AgentActionRead]:
    """Statement 2 (reviews) and, only when needed, step 3 (the Act gate) for a fetched page."""
    pipeline_ids = sorted({r[0].pipeline_run_id for r in rows if r[0].pipeline_run_id is not None}, key=str)
    reviews = await _reviews_by_pipeline(db, project_id, pipeline_ids)
    act_codes: dict[tuple[str, Optional[str]], Optional[str]] = {}
    viewer_id = getattr(viewer, "id", None)
    items: list[AgentActionRead] = []
    for row, build_number, branch, workflow_type, requested_by, full_name, username, *_total in rows:
        review = reviews.get(row.pipeline_run_id, ("none", None)) if row.pipeline_run_id else ("none", None)
        facts = _Facts(
            build_number=build_number, branch=branch, workflow_type=workflow_type,
            requested_by=requested_by,
            decider_name=(full_name or username) if row.approved_by else None,
        )
        refusal: Optional[Refusal] = None
        if row.status in APPROVABLE_STATUSES:

            async def _review(state: str = review[0]) -> str:
                return state

            async def _requester(value: Optional[uuid.UUID] = requested_by) -> Optional[uuid.UUID]:
                return value

            refusal = await _refusal(
                db, action=row, spec=resolve_action_type(row.action_type), approver_id=viewer_id,
                review_state=_review, requester=_requester, act_codes=act_codes,
            )
        items.append(serialize_action(row, facts=facts, review=review, refusal=refusal))
    return items


async def list_actions(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    view: Optional[str] = None,
    status: Optional[str] = None,
    action_type: Optional[str] = None,
    pipeline_run_id: Optional[uuid.UUID] = None,
    test_run_id: Optional[uuid.UUID] = None,
    limit: int = 50,
    viewer: Any = None,
) -> AgentActionListResponse:
    """One page of the project's queue + ``total`` (the filtered COUNT, not the page length).

    ``view``/``status`` are judged by the router. ``viewer`` (the requesting user)
    makes ``approvable`` answer for THEM: the requester of an investigation sees
    its effects blocked by separation of duties before clicking.
    """
    stmt = _page_statement(
        project_id, view=view, status=status, action_type=action_type,
        pipeline_run_id=pipeline_run_id, test_run_id=test_run_id, limit=limit,
    )
    rows = (await db.execute(stmt)).all()
    items = await _build(db, project_id, rows, viewer)
    total = int(rows[0][-1]) if rows else 0
    return AgentActionListResponse(items=items, count=len(items), total=total)


async def read_action(
    db: AsyncSession, *, project_id: uuid.UUID, action_id: uuid.UUID, viewer: Any = None
) -> AgentActionRead:
    """The PATCH's answer: one row through the list's own builders. LookupError when absent."""
    rows = (await db.execute(_page_statement(project_id, action_id=action_id, limit=1))).all()
    if not rows:
        raise LookupError("action_not_found")
    return (await _build(db, project_id, rows, viewer))[0]
