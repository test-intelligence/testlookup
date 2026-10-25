"""``quarantine.apply``: apply the quarantine an approved proposal names (W15 AGT-03, §0.19).

Internal and reversible (owner decision D16). The effect is the flaky-quarantine
service's own two steps -- ``propose_quarantine`` then ``approve`` as the
approver -- and the undo is its ``release``. Those functions COMMIT on their own
(``propose_quarantine`` opens its own session; ``approve`` / ``release`` commit
the session they are given), so this adapter declares ``commits_effect = True``:
the executor records any exception after its durable claim as
``outcome_unknown``, never as a clean failure.

Every target is re-loaded inside ``action.project_id`` (the IDOR boundary): the
run must be a run of the project, a quarantine request is used only after its
``project_id`` is compared with the action's. Only typed payload fields reach a
service call; ``title`` / ``rationale`` / ``evidence`` are display-only.

Commit-free itself (``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import FlakyQuarantineRequest, FlakyQuarantineStatus, TestRun
from app.services.agent_action_adapters.adapter_base import ActionOutcome, ActionRefused
from app.services.agent_action_types import QuarantineApplyPayload
from app.services.feature_flags import is_enabled
from app.services.flaky_quarantine_service import approve, get_request, propose_quarantine, release

#: A live row in one of these states means somebody already decided this test:
#: approving it again is impossible (``approve`` 409s) and adopting it would let
#: an Undo release a quarantine the agent did not apply.
_ALREADY_DECIDED = (
    FlakyQuarantineStatus.APPROVED.value,
    FlakyQuarantineStatus.QUARANTINED.value,
    FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
    FlakyQuarantineStatus.RE_QUARANTINED.value,
)
#: Still proposable: ``approve`` accepts exactly these.
_PROPOSABLE = (
    FlakyQuarantineStatus.DETECTED.value,
    FlakyQuarantineStatus.PROPOSED.value,
)
#: ``release`` accepts exactly these.
_RELEASABLE = (
    FlakyQuarantineStatus.QUARANTINED.value,
    FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
    FlakyQuarantineStatus.RE_QUARANTINED.value,
)
_RESULT_HREF = "/flaky?tab=quarantined"


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _payload(payload: Any) -> QuarantineApplyPayload:
    if not isinstance(payload, QuarantineApplyPayload):
        raise ActionRefused("payload_invalid")
    return payload


def _status_word(row: Any) -> str:
    status = getattr(row, "status", None)
    return str(getattr(status, "value", status) or "")


class QuarantineApplyAdapter:
    action_type = "quarantine.apply"
    commits_effect = True

    async def preflight(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> None:
        p = _payload(payload)
        if not await is_enabled(
            "flaky_auto_quarantine", db=db, project_id=action.project_id, user=approver,
        ):
            raise ActionRefused("quarantine_disabled")
        run_id = _as_uuid(p.test_run_id)
        if run_id is None:
            raise ActionRefused("target_not_in_project")
        run = (
            await db.execute(
                select(TestRun.id).where(
                    TestRun.id == run_id, TestRun.project_id == action.project_id,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            raise ActionRefused("target_not_in_project")
        live = (
            await db.execute(
                select(FlakyQuarantineRequest.status).where(
                    FlakyQuarantineRequest.project_id == action.project_id,
                    FlakyQuarantineRequest.test_fingerprint == p.test_fingerprint,
                    FlakyQuarantineRequest.status.in_(_ALREADY_DECIDED),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if live is not None:
            raise ActionRefused("target_already_quarantined")

    async def execute(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> ActionOutcome:
        p = _payload(payload)
        # propose_quarantine opens (and commits) its own session; it returns
        # the live row for the fingerprint, refreshed, when one exists.
        row = await propose_quarantine(
            project_id=action.project_id,
            test_fingerprint=p.test_fingerprint,
            test_name=p.test_name,
            suite_name=p.suite_name,
            detection_method="agent_action",
            flip_rate=None,
            flip_window_size=None,
            rationale={
                "source": "agent_action",
                "action_id": str(action.id),
                "investigation_id": p.investigation_id,
            },
            actor=approver,
        )
        if row is None:
            raise ActionRefused("quarantine_disabled")
        if row.project_id != action.project_id:
            raise ActionRefused("target_not_in_project")
        if _status_word(row) not in _PROPOSABLE:
            raise ActionRefused("target_already_quarantined")
        approved = await approve(db, row.id, approver, notes="Approved agent action")
        request_id = str(approved.id)
        return ActionOutcome(
            result_payload={
                "quarantine_request_id": request_id,
                "quarantine_status": _status_word(approved),
            },
            rollback_payload={"quarantine_request_id": request_id},
            result_link={
                "kind": "quarantine", "id": request_id,
                "label": "quarantined", "href": _RESULT_HREF,
            },
        )

    async def rollback(
        self, db: AsyncSession, *, action: Any, rollback_payload: Any, actor: Any,
    ) -> ActionOutcome:
        data = rollback_payload if isinstance(rollback_payload, dict) else {}
        request_id = _as_uuid(data.get("quarantine_request_id"))
        if request_id is None:
            raise ActionRefused("rollback_target_changed")
        row = await get_request(db, request_id)
        if row is None:
            raise ActionRefused("rollback_target_changed")
        if row.project_id != action.project_id:
            raise ActionRefused("target_not_in_project")
        status = _status_word(row)
        if status == FlakyQuarantineStatus.RELEASED.value:
            # Already undone (a repeated Undo after a lost commit): idempotent.
            return ActionOutcome(
                result_payload={"quarantine_request_id": str(request_id), "quarantine_status": status},
            )
        if status not in _RELEASABLE:
            raise ActionRefused("rollback_target_changed")
        try:
            released = await release(db, request_id, actor, notes="Agent action undone")
        except HTTPException as exc:
            # A concurrent release / expiry between the read and the write.
            raise ActionRefused("rollback_target_changed") from exc
        return ActionOutcome(
            result_payload={
                "quarantine_request_id": str(request_id),
                "quarantine_status": _status_word(released),
            },
        )
