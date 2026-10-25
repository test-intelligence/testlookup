"""The Investigator's typed actions → ``pending_review`` ledger proposals (W15 AGT-02).

Called by ``agents/investigator/workflow._finalize`` on ITS session, inside the
terminal transaction (the finalize commit covers every row staged here). This
module is commit-free: it reads, locks, stages and flushes — never commits.

For a Suggest / Act investigation of a RUN (a deep pipeline's cluster children
propose nothing, D-7) and the verdict's ``structured_actions``:

1. every action is re-validated against ``InvestigatorStructuredActionV1`` and
   its type's payload model, with the four identity fields re-stamped from the
   investigation row (``proposing_agent_id`` / ``_mode``, ``investigation_id``,
   ``test_run_id``); a payload whose TYPED field the persistence sanitizer would
   rewrite is SKIPPED (R-9: a mangled id is never proposed);
2. what is already done is dropped (D-8), one statement per kind present: a
   fingerprint already quarantined in the project, a test case already assigned
   to the proposed owner (or not a test case of the project), a cluster an OPEN
   defect of the project already covers;
3. older PENDING proposals of the same (project, type, target) from another
   pipeline are superseded (``rejected`` / ``superseded``, D-10) — approved or
   executed history is never rewritten;
4. each remaining action becomes ``create_action_proposal(…, approval_required=True)``
   under the stable key ``investigation_action_key`` (D-9). D16: never
   ``approval_required=False``, whatever the mode. A key reused with another
   payload is skipped and logged; a proposal problem never fails the run.

Shadow persists nothing (no row, no supersede). Logs carry ids, codes and counts
only — never a test name, a title, a rationale or a payload.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

import structlog
from pydantic import ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_contracts import InvestigatorStructuredActionV1
from app.models.postgres import (
    AgentActionLedger,
    Defect,
    FlakyQuarantineRequest,
    FlakyQuarantineStatus,
    TestCase,
    TestRun,
)
from app.services.agent_action_ledger_service import create_action_proposal
from app.services.agent_action_types import ACTION_TYPES
from app.services.evidence_sanitizer import sanitize_persistence_payload

logger = structlog.get_logger("services.investigation_action_proposals")

PROPOSING_AGENT_ID = "investigator"
PROPOSING_MODES: tuple[str, ...] = ("suggest", "act")
SUPERSEDED_CODE = "superseded"

#: A live quarantine the agent must not propose again (the adapter refuses it too).
QUARANTINED_STATES: tuple[str, ...] = (
    FlakyQuarantineStatus.QUARANTINED.value,
    FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
    FlakyQuarantineStatus.RE_QUARANTINED.value,
)

#: The payload fields an effect reads (ids, fingerprints, names): none of them may
#: change under the persistence sanitizer (R-9).
TYPED_PAYLOAD_KEYS: tuple[str, ...] = (
    "proposing_agent_id", "proposing_agent_mode", "investigation_id", "test_run_id",
    "test_fingerprint", "test_name", "suite_name", "test_case_id", "assignee_user_id",
    "failure_cluster_id", "cluster_id",
)

#: The payload field that names each type's target (must equal ``target_id``).
_TARGET_FIELD: dict[str, str] = {
    "quarantine.apply": "test_fingerprint",
    "failure.reassign": "test_case_id",
    "defect.promote": "cluster_id",
}


def investigation_action_key(investigation_id: Any, action_type: str, target_id: Any) -> str:
    """D-9: ``inv:{investigation}:{type}:{sha256(target)[:24]}`` — stable, ≤ 128."""
    digest = hashlib.sha256(str(target_id).encode("utf-8")).hexdigest()[:24]
    return f"inv:{investigation_id}:{action_type}:{digest}"


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _prepare(
    raw: Any, *, investigation: Any, mode: str
) -> Optional[tuple[str, str, str, dict[str, Any]]]:
    """``(action_type, target_type, target_id, payload)`` ready to persist, or
    ``None`` when the action must not be proposed (logged, ids only)."""
    investigation_id = str(investigation.id)
    try:
        action = InvestigatorStructuredActionV1.model_validate(raw)
    except ValidationError as exc:
        logger.warning(
            "investigator_action_invalid",
            investigation_id=investigation_id,
            error_count=len(exc.errors()),
        )
        return None
    spec = ACTION_TYPES.get(action.action_type)
    if (
        spec is None
        or PROPOSING_AGENT_ID not in spec.proposers
        or spec.target_type != action.target_type
        or action.action_type not in _TARGET_FIELD
    ):
        logger.warning(
            "investigator_action_not_proposable",
            investigation_id=investigation_id,
            action_type=action.action_type,
        )
        return None
    stamped = {
        **action.payload,
        "proposing_agent_id": PROPOSING_AGENT_ID,
        "proposing_agent_mode": mode,
        "investigation_id": investigation_id,
        "test_run_id": str(investigation.run_id),
    }
    try:
        payload = spec.payload_model.model_validate(stamped).model_dump(mode="json")
    except ValidationError as exc:
        logger.warning(
            "investigator_action_payload_invalid",
            investigation_id=investigation_id,
            action_type=action.action_type,
            error_count=len(exc.errors()),
        )
        return None
    if str(payload.get(_TARGET_FIELD[action.action_type]) or "") != action.target_id:
        logger.warning(
            "investigator_action_target_mismatch",
            investigation_id=investigation_id,
            action_type=action.action_type,
        )
        return None
    sanitized, _stats = sanitize_persistence_payload(payload)
    changed = [key for key in TYPED_PAYLOAD_KEYS if sanitized.get(key) != payload.get(key)]
    if changed:
        # R-9: a fingerprint / id / name the redactor would rewrite (e.g. an
        # all-digit id that reads as a card number) must never be proposed
        # mangled. The field NAMES are logged, never their values.
        logger.warning(
            "investigator_action_payload_redacted",
            investigation_id=investigation_id,
            action_type=action.action_type,
            changed_fields=changed,
        )
        return None
    return action.action_type, action.target_type, action.target_id, payload


def _ids_of(prepared: Iterable[tuple[str, str, str, dict[str, Any]]], action_type: str) -> list[str]:
    return sorted({target_id for kind, _t, target_id, _p in prepared if kind == action_type})


async def _already_done(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    prepared: list[tuple[str, str, str, dict[str, Any]]],
) -> set[tuple[str, str]]:
    """D-8: the ``(action_type, target_id)`` pairs already done in the project
    — ONE statement per kind present, every one scoped to the project."""
    done: set[tuple[str, str]] = set()
    fingerprints = _ids_of(prepared, "quarantine.apply")
    if fingerprints:
        rows = await db.execute(
            select(FlakyQuarantineRequest.test_fingerprint).where(
                FlakyQuarantineRequest.project_id == project_id,
                FlakyQuarantineRequest.test_fingerprint.in_(fingerprints),
                FlakyQuarantineRequest.status.in_(QUARANTINED_STATES),
            )
        )
        done |= {("quarantine.apply", str(fp)) for fp in rows.scalars().all()}
    case_ids = {raw: _uuid(raw) for raw in _ids_of(prepared, "failure.reassign")}
    valid_case_ids = [value for value in case_ids.values() if value is not None]
    if case_ids:
        current: dict[str, Optional[str]] = {}
        if valid_case_ids:
            rows = await db.execute(
                select(TestCase.id, TestCase.assigned_to_user_id)
                .join(TestRun, TestRun.id == TestCase.test_run_id)
                .where(TestRun.project_id == project_id, TestCase.id.in_(valid_case_ids))
            )
            current = {
                str(row.id): (str(row.assigned_to_user_id) if row.assigned_to_user_id else None)
                for row in rows.all()
            }
        for kind, _t, target_id, payload in prepared:
            if kind != "failure.reassign":
                continue
            parsed = case_ids.get(target_id)
            key = str(parsed) if parsed is not None else None
            assignee = _uuid(payload.get("assignee_user_id"))
            if (
                key is None
                or assignee is None
                or key not in current          # not a test case of this project
                or current[key] == str(assignee)
            ):
                done.add((kind, target_id))
    cluster_ids = _ids_of(prepared, "defect.promote")
    if cluster_ids:
        rows = await db.execute(
            select(Defect.cluster_id).where(
                Defect.project_id == project_id,
                Defect.cluster_id.in_(cluster_ids),
                Defect.resolution_status == "OPEN",
            )
        )
        done |= {("defect.promote", str(cid)) for cid in rows.scalars().all()}
    return done


async def _supersede(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    pipeline_run_id: uuid.UUID,
    targets: list[tuple[str, str, str]],
    now: datetime,
) -> int:
    """D-10: older PENDING rows of the same (project, type, target) from
    another pipeline → ``rejected`` / ``superseded``. ONE locking statement;
    the rows are locked before they change (the PATCH locks only the ledger
    row, so the order cannot cycle with finalize's run → investigation →
    ledger)."""
    if not targets:
        return 0
    rows = (
        await db.execute(
            select(AgentActionLedger)
            .where(
                AgentActionLedger.project_id == project_id,
                AgentActionLedger.status == "pending_review",
                tuple_(
                    AgentActionLedger.action_type,
                    AgentActionLedger.target_type,
                    AgentActionLedger.target_id,
                ).in_(targets),
                AgentActionLedger.pipeline_run_id.is_distinct_from(pipeline_run_id),
            )
            .with_for_update()
        )
    ).scalars().all()
    for row in rows:
        row.status = "rejected"
        row.error_code = SUPERSEDED_CODE
        row.execution_completed_at = now
    return len(rows)


async def persist_investigation_proposals(
    db: AsyncSession,
    *,
    investigation: Any,
    pipeline_run_id: Any,
    actions: Iterable[Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> list[uuid.UUID]:
    """Stage the verdict's typed actions as ``pending_review`` proposals (module doc).

    Returns the ledger ids (new or the existing ones of a replayed finalize),
    in the plan's order. Shadow, a non-run scope or no action → ``[]`` and NO
    statement.
    """
    mode = str(getattr(investigation, "mode", "") or "")
    if mode not in PROPOSING_MODES:
        return []
    if (getattr(investigation, "scope_type", "run") or "run") != "run":
        return []
    raw_actions = list(actions or [])
    if not raw_actions:
        return []
    pipeline_id = _uuid(pipeline_run_id)
    if pipeline_id is None:
        return []
    project_id = investigation.project_id
    prepared = [
        item for item in (_prepare(raw, investigation=investigation, mode=mode) for raw in raw_actions)
        if item is not None
    ]
    if not prepared:
        return []
    done = await _already_done(db, project_id=project_id, prepared=prepared)
    remaining = [item for item in prepared if (item[0], item[2]) not in done]
    superseded = await _supersede(
        db,
        project_id=project_id,
        pipeline_run_id=pipeline_id,
        targets=sorted({(kind, target_type, target_id) for kind, target_type, target_id, _p in remaining}),
        now=now or datetime.now(timezone.utc),
    )
    ids: list[uuid.UUID] = []
    for action_type, target_type, target_id, payload in remaining:
        key = investigation_action_key(investigation.id, action_type, target_id)
        try:
            row, _created = await create_action_proposal(
                db,
                project_id=project_id,
                test_run_id=investigation.run_id,
                pipeline_run_id=pipeline_id,
                action_type=action_type,
                target_type=target_type,
                target_id=target_id,
                idempotency_key=key,
                request_payload=payload,
                approval_required=True,  # D16: a person approves EACH action, whatever the mode
            )
        except ValueError as exc:
            logger.warning(
                "investigator_action_proposal_conflict",
                investigation_id=str(investigation.id),
                action_type=action_type,
                key_hash=_key_hash(key),
                code=str(exc)[:60],
            )
            continue
        ids.append(row.id)
    logger.info(
        "investigator_action_proposals_staged",
        investigation_id=str(investigation.id),
        mode=mode,
        planned=len(raw_actions),
        already_done=len(prepared) - len(remaining),
        superseded=superseded,
        proposed=len(ids),
    )
    return ids
