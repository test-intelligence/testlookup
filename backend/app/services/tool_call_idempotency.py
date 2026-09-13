"""Exactly-once external tool calls for mutating agent stages (architecture E7.6).

The failure this prevents
-------------------------
A stage that files a Jira ticket and then fails before it records the ticket
(a crash, a lease reclaim, a timeout on the local write) is re-run from the
start: checkpoints are written only at the end of a successful stage. Without
a durable record of the call, the re-run cannot tell "the ticket was never
filed" from "the ticket was filed and we lost the answer", and it files again.
External tickets cannot be rolled back, so the second one is permanent.

How
---
Every mutating call is guarded by a row in ``agent_action_ledger`` keyed on
``(tool, scope, subject)`` -- never on the attempt number, so a retry of the
same stage computes the same key (section 7.5):

1. **claim** -- commit an ``executing`` row *before* the external call. The
   commit is durable before anything irreversible happens;
2. **call** the external system;
3. **finish** -- record ``executed`` with the result, or ``failed``.

A later attempt that finds:

* ``executed`` -> replays the stored result. No second call.
* ``executing`` -> the previous attempt may or may not have completed the
  call. It is reported as ``outcome_unknown`` and **not** retried: a missing
  ticket is visible and a human can file it, while a duplicate is permanent.
* ``failed`` -> the call did not happen; it is claimed and tried again.

Two attempts racing for the same key are resolved by the ledger's unique
``(project_id, idempotency_key)`` constraint: the loser sees ``executing``.

Why the scope is the test run and not the pipeline run
------------------------------------------------------
Section 7.5 names ``run_id``. A same-id resume (E7.2/E7.3) keeps the pipeline
id, but a manual retry under changed config starts a NEW pipeline with
``rerun_of`` (E7.4), and the offline and deep pipelines both triage the same
test run. A ticket for one failure in one test run is one external mutation
however many pipelines ask for it, so the scope is the test run.

Fencing: the claim is fenced by the lease token, so a reclaimed attempt cannot
start a call. The finish is deliberately NOT fenced: once the external call has
happened, recording that fact is true no matter who holds the lease, and it is
exactly what stops the next attempt from calling again.

Transaction ownership: the claim must be durable before the external call and
cannot borrow a caller's transaction (the caller is a graph node), so this
module owns its own sessions -- see the allowlist entry in
``tests/test_architectural_transaction_boundaries.py``.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping, Optional

import structlog

logger = structlog.get_logger("services.tool_call_idempotency")

__all__ = [
    "JIRA_TICKET_TOOL",
    "KEY_PREFIX",
    "OutcomeUnknown",
    "PriorToolCall",
    "ToolCallFailed",
    "ToolCallOutcome",
    "prior_tool_calls",
    "record_outcome",
    "run_once",
    "tool_call_key",
]

#: ``action_type`` for a Jira ticket filed by an agent. Same string as
#: ``action_policy.ActionType.JIRA_TICKET_CREATION``.
JIRA_TICKET_TOOL = "jira_ticket_creation"

#: Prefix of every idempotency key this module writes, so its rows are never
#: confused with approval-flow proposals in the same ledger.
KEY_PREFIX = "toolcall:"

ClaimState = Literal["claimed", "executed", "executing"]


class OutcomeUnknown(RuntimeError):
    """An adapter's way to say the call may have happened.

    A read timeout, a dropped connection or a 5xx after the request was sent
    all leave the external system in an unknown state. Recording ``failed``
    would let the next attempt call again and file a duplicate, so the row is
    left ``executing`` and the exception is re-raised.
    """


class ToolCallFailed(RuntimeError):
    """An adapter's way to say the call did not happen without raising.

    Some clients swallow their own errors and return ``None``
    (``defect_promotion_service._create_jira_ticket``). The adapter converts
    that into this exception so the row is recorded ``failed`` -- and retried
    -- rather than ``executed`` with an empty result.
    """


@dataclass(frozen=True)
class ToolCallOutcome:
    """What ``run_once`` did.

    ``executed``        -- this attempt made the call.
    ``replayed``        -- an earlier attempt made it; ``result`` is its answer.
    ``outcome_unknown`` -- an earlier attempt started it and never recorded
                           how it ended. The call was not repeated.
    """

    status: Literal["executed", "replayed", "outcome_unknown"]
    key: str
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PriorToolCall:
    """A call an earlier attempt already made (or started)."""

    status: Literal["executed", "executing"]
    result: dict[str, Any] = field(default_factory=dict)


def tool_call_key(*, tool: str, scope_id: Any, subject_id: Any) -> str:
    """The idempotency key for one call. Never includes the attempt number.

    Hashed so a long subject id cannot overflow ``idempotency_key``
    (``String(128)``); the tool name stays readable for a human in the ledger.
    """
    scope = str(scope_id or "").strip()
    subject = str(subject_id or "").strip()
    if not tool or not scope or not subject:
        raise ValueError("tool_call_identity_incomplete")
    digest = hashlib.sha256(f"{tool}\x1f{scope}\x1f{subject}".encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}{tool[:40]}:{digest}"


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _sanitized(payload: Any) -> tuple[dict[str, Any], str]:
    """Redacted, bounded payload and its hash, the way the ledger stores them."""
    from app.services.canonical_json import stable_json_sha256  # noqa: PLC0415
    from app.services.evidence_sanitizer import sanitize_persistence_payload  # noqa: PLC0415

    value, _stats = sanitize_persistence_payload(payload if isinstance(payload, dict) else {})
    record: dict[str, Any] = value if isinstance(value, dict) else {}
    return record, stable_json_sha256(record, max_bytes=128_000)


async def _commit(db: Any) -> None:
    await db.commit()


async def prior_tool_calls(
    *,
    project_id: Any,
    tool: str,
    scope_id: Any,
    subject_ids: Iterable[Any],
) -> dict[str, PriorToolCall]:
    """Stage-entry lookup: subjects an earlier attempt already called for.

    Section 7.5 makes this the first thing a mutating stage does, so the work
    list is decided with the ledger in view rather than discovered one call at
    a time. Returns ``{subject_id: PriorToolCall}``; ``failed`` rows are not
    included because those subjects should simply be tried again.
    """
    subjects = [str(s) for s in subject_ids if str(s or "").strip()]
    if not subjects or not str(scope_id or "").strip():
        return {}
    by_key = {tool_call_key(tool=tool, scope_id=scope_id, subject_id=s): s for s in subjects}

    from sqlalchemy import select  # noqa: PLC0415

    from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415
    from app.models.postgres import AgentActionLedger  # noqa: PLC0415

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    AgentActionLedger.idempotency_key,
                    AgentActionLedger.status,
                    AgentActionLedger.result_payload,
                ).where(
                    AgentActionLedger.project_id == _as_uuid(project_id),
                    AgentActionLedger.idempotency_key.in_(list(by_key)),
                    AgentActionLedger.status.in_(("executed", "executing")),
                )
            )
        ).all()
    prior: dict[str, PriorToolCall] = {}
    for key, status, result in rows:
        subject = by_key.get(str(key))
        if subject is None:
            continue
        prior[subject] = PriorToolCall(
            status="executed" if status == "executed" else "executing",
            result=dict(result or {}),
        )
    return prior


async def _claim(
    *,
    project_id: Any,
    key: str,
    tool: str,
    target_type: str,
    subject_id: Any,
    request_payload: Mapping[str, Any],
    pipeline_run_id: Any,
    test_run_id: Any,
    fencing_token: Optional[str],
) -> tuple[ClaimState, dict[str, Any]]:
    """Commit an ``executing`` row, or report what an earlier attempt left."""
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415
    from app.models.postgres import AgentActionLedger  # noqa: PLC0415
    from app.services.pipeline_lease import fence_or_raise  # noqa: PLC0415

    payload, digest = _sanitized(dict(request_payload))
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        if pipeline_run_id:
            # A reclaimed attempt must not start an external call.
            await fence_or_raise(db, str(pipeline_run_id), fencing_token)
        row = (
            await db.execute(
                select(AgentActionLedger)
                .where(
                    AgentActionLedger.project_id == _as_uuid(project_id),
                    AgentActionLedger.idempotency_key == key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None:
            if row.status == "executed":
                return "executed", dict(row.result_payload or {})
            if row.status != "failed":
                # ``executing`` -- or any status this module does not write,
                # which can only mean something unexpected owns the key. Both
                # fail closed: never call twice.
                return "executing", {}
            row.status = "executing"
            row.execution_started_at = now
            row.execution_completed_at = None
            row.error_code = None
            row.request_payload = payload
            row.request_sha256 = digest
        else:
            db.add(
                AgentActionLedger(
                    project_id=_as_uuid(project_id),
                    test_run_id=_as_uuid(test_run_id),
                    pipeline_run_id=_as_uuid(pipeline_run_id),
                    action_type=tool[:60],
                    target_type=target_type[:60],
                    target_id=str(subject_id)[:255],
                    status="executing",
                    approval_required=False,
                    idempotency_key=key,
                    request_sha256=digest,
                    request_payload=payload,
                    execution_started_at=now,
                )
            )
        try:
            await _commit(db)
        except IntegrityError:
            # Another attempt inserted the same key between our read and our
            # write. It owns the call; leaving the session discards ours.
            return "executing", {}
    return "claimed", {}


async def _finish(
    *,
    project_id: Any,
    key: str,
    status: Literal["executed", "failed"],
    result_payload: Optional[Mapping[str, Any]] = None,
    error_code: Optional[str] = None,
) -> None:
    """Record how the call ended. Not fenced -- see the module docstring."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415
    from app.models.postgres import AgentActionLedger  # noqa: PLC0415

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(AgentActionLedger)
                .where(
                    AgentActionLedger.project_id == _as_uuid(project_id),
                    AgentActionLedger.idempotency_key == key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            logger.warning("tool_call_finish_row_missing", status=status)
            return
        row.status = status
        row.execution_completed_at = datetime.now(timezone.utc)
        if result_payload is not None:
            row.result_payload = _sanitized(dict(result_payload))[0]
        row.error_code = error_code[:100] if error_code else None
        await _commit(db)


async def record_outcome(
    *,
    project_id: Any,
    key: str,
    status: Literal["executed", "failed"],
    result_payload: Optional[Mapping[str, Any]] = None,
    error_code: Optional[str] = None,
) -> None:
    """Settle a claim that ``run_once`` reported as ``outcome_unknown``.

    For callers that can find out what happened: ``executed`` once the external
    system shows the call landed, ``failed`` once a person has confirmed it did
    not, which lets the next ``run_once`` claim and call again.
    """
    await _finish(
        project_id=project_id, key=key, status=status,
        result_payload=result_payload, error_code=error_code,
    )


async def run_once(
    *,
    project_id: Any,
    tool: str,
    scope_id: Any,
    subject_id: Any,
    target_type: str,
    request_payload: Mapping[str, Any],
    call: Callable[[], Awaitable[Optional[Mapping[str, Any]]]],
    pipeline_run_id: Any = None,
    test_run_id: Any = None,
    fencing_token: Optional[str] = None,
) -> ToolCallOutcome:
    """Make ``call`` at most once for ``(tool, scope_id, subject_id)``.

    ``call`` raises when the external action did not happen; its exception is
    re-raised after the row is marked ``failed``. The returned mapping is stored
    and replayed to later attempts.
    """
    key = tool_call_key(tool=tool, scope_id=scope_id, subject_id=subject_id)
    state, prior = await _claim(
        project_id=project_id,
        key=key,
        tool=tool,
        target_type=target_type,
        subject_id=subject_id,
        request_payload=request_payload,
        pipeline_run_id=pipeline_run_id,
        test_run_id=test_run_id,
        fencing_token=fencing_token,
    )
    if state == "executed":
        logger.info("tool_call_replayed", tool=tool, subject_id=str(subject_id))
        return ToolCallOutcome(status="replayed", key=key, result=prior)
    if state == "executing":
        logger.warning("tool_call_outcome_unknown", tool=tool, subject_id=str(subject_id))
        return ToolCallOutcome(status="outcome_unknown", key=key)

    try:
        result = await call()
    except OutcomeUnknown:
        # The call may have happened: leave the row ``executing`` so no later
        # attempt repeats it. Reconciling is the caller's job (record_outcome).
        logger.warning("tool_call_outcome_unknown_after_call", tool=tool, subject_id=str(subject_id))
        raise
    except Exception as exc:
        try:
            await _finish(
                project_id=project_id, key=key, status="failed",
                error_code=type(exc).__name__,
            )
        except Exception as finish_exc:  # noqa: BLE001 -- never mask the call's own error
            logger.warning(
                "tool_call_failure_not_recorded",
                tool=tool, error_type=type(finish_exc).__name__,
            )
        raise
    record = dict(result or {})
    await _finish(project_id=project_id, key=key, status="executed", result_payload=record)
    return ToolCallOutcome(status="executed", key=key, result=record)
