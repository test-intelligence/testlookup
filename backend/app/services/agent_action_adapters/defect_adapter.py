"""``defect.promote``: promote an internal defect for a failure cluster (W15 AGT-03, §0.21).

Internal and reversible (owner decision D16) -- NEVER Jira: the request carries
``project_key=None``, and ``promote_cluster`` files a ticket only when that key
is truthy, whatever ``JIRA_ENABLED`` says. No field of the request comes from
the proposal's text: the title is the service's own default (the cluster label),
the severity and labels are fixed here.

``promote_cluster`` reads the cluster ``WHERE test_run_id AND cluster_id`` and
never filters it by project, so the run is checked against ``action.project_id``
FIRST (the IDOR boundary), then the current-snapshot cluster, then that no
active defect of the project already covers the cluster.

The undo is W06's: a defect still ``pending_review`` is rejected the way the
Inbox › Defects queue rejects it (``reject_action`` + ``reject_pending_defect``,
closed ``INVALID``); an effective one still OPEN with no Jira key is closed
``INVALID`` through ``defect_status.apply_status`` with its ``defect.updated``
row; anything else (triaged, filed) is somebody else's now.

Stage-only, like ``promote_cluster`` (``STAGE_ONLY_SERVICES``); ``commits_effect = False``.
The ``defect.promoted`` webhook is sent by the executor after its finish commit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Defect, FailureCluster, TestRun
from app.services import defect_status
from app.services.action_policy import ActionStatus, reject_action
from app.services.activity.service import ActorRef, record as record_activity
from app.services.agent_action_adapters.adapter_base import ActionOutcome, ActionRefused
from app.services.agent_action_types import DefectPromotePayload
from app.services.cluster_snapshot import is_current_snapshot
from app.services.defect_promotion_service import promote_cluster
from app.services.defect_triage_service import defect_label, reject_pending_defect
from app.services.intelligence_snapshot_service import mark_stale

_SEVERITY = "HIGH"
_LABELS = ("automated-test", "agent-proposed")
_UNDO_REASON = "Agent action undone"
#: A promotion that took effect at once (the webhook's own rule).
EFFECTIVE_APPROVAL_STATUSES = (ActionStatus.APPROVED.value, ActionStatus.EXECUTED.value)


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _payload(payload: Any) -> DefectPromotePayload:
    if not isinstance(payload, DefectPromotePayload):
        raise ActionRefused("payload_invalid")
    return payload


def _word(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


class DefectPromoteAdapter:
    action_type = "defect.promote"
    commits_effect = False

    async def preflight(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> None:
        p = _payload(payload)
        run_id = _as_uuid(p.test_run_id)
        failure_cluster_id = _as_uuid(p.failure_cluster_id)
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
        if failure_cluster_id is None:
            raise ActionRefused("cluster_not_found")
        cluster = (
            await db.execute(
                select(FailureCluster.id).where(
                    FailureCluster.id == failure_cluster_id,
                    FailureCluster.test_run_id == run_id,
                    FailureCluster.cluster_id == p.cluster_id,
                    is_current_snapshot(),
                )
            )
        ).scalar_one_or_none()
        if cluster is None:
            raise ActionRefused("cluster_not_found")
        existing = (
            await db.execute(
                select(Defect.id).where(
                    Defect.project_id == action.project_id,
                    Defect.cluster_id == p.cluster_id,
                    Defect.resolution_status.in_(defect_status.ACTIVE_STATUSES),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ActionRefused("defect_already_exists")

    async def execute(self, db: AsyncSession, *, action: Any, payload: Any, approver: Any) -> ActionOutcome:
        p = _payload(payload)
        run_id = _as_uuid(p.test_run_id)
        if run_id is None:
            raise ActionRefused("target_not_in_project")
        try:
            promoted = await promote_cluster(
                str(run_id),
                p.cluster_id,
                str(action.project_id),
                {"severity": _SEVERITY, "project_key": None, "labels": list(_LABELS)},
                db,
                actor=approver,
            )
        except ValueError as exc:  # the service's "Cluster … not found for run …"
            raise ActionRefused("cluster_not_found") from exc
        # As the promote route does: the run's intelligence snapshot is stale now.
        await mark_stale(db, run_id)
        defect_id = str(promoted["defect_id"])
        approval = _word(promoted.get("approval_status")).lower()
        pending = approval == ActionStatus.PENDING_REVIEW.value
        return ActionOutcome(
            result_payload={
                "defect_id": defect_id,
                "cluster_id": p.cluster_id,
                "approval_status": approval,
                "duplicate_detected": bool(promoted.get("duplicate_detected")),
            },
            rollback_payload={"defect_id": defect_id, "approval_status": approval},
            result_link={
                "kind": "defect", "id": defect_id,
                "label": "defect created · awaiting approval" if pending else "defect created",
                "href": f"/defects?defect={defect_id}",
            },
        )

    async def rollback(
        self, db: AsyncSession, *, action: Any, rollback_payload: Any, actor: Any,
    ) -> ActionOutcome:
        data = rollback_payload if isinstance(rollback_payload, dict) else {}
        defect_id = _as_uuid(data.get("defect_id"))
        if defect_id is None:
            raise ActionRefused("rollback_target_changed")
        defect = (
            await db.execute(
                select(Defect).where(
                    Defect.id == defect_id, Defect.project_id == action.project_id,
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if defect is None:
            raise ActionRefused("target_not_in_project")
        facts = {"defect_id": str(defect_id), "resolution_status": defect_status.INVALID}
        current = _word(defect.resolution_status).upper()
        approval = _word(defect.approval_status).lower()
        if current == defect_status.INVALID:
            # Already closed as Invalid (a repeated Undo after a lost commit): idempotent.
            return ActionOutcome(result_payload=facts)
        if current != defect_status.OPEN or defect.jira_ticket_id:
            # Triaged, resolved or filed since: a person owns it now.
            raise ActionRefused("rollback_target_changed")
        if approval == ActionStatus.PENDING_REVIEW.value:
            rejected = await reject_action(
                db, model_class=Defect, record_id=defect.id, rejector_id=actor.id,
                rejector_name=getattr(actor, "username", None) or "", reason=_UNDO_REASON,
            )
            if not rejected:
                raise ActionRefused("rollback_target_changed")
            await db.refresh(defect)
            await reject_pending_defect(db, defect=defect, actor=actor, reason=_UNDO_REASON)
            return ActionOutcome(result_payload={**facts, "approval_status": ActionStatus.REJECTED.value})
        await _close_invalid(db, defect=defect, actor=actor)
        return ActionOutcome(result_payload={**facts, "approval_status": approval})


async def _close_invalid(db: AsyncSession, *, defect: Defect, actor: Any) -> None:
    """Close an effective (or duplicate-rejected) agent defect as ``INVALID``. Stage-only."""
    before = defect_status.STATUS_LABELS.get(_word(defect.resolution_status).upper(), "Open")
    defect_status.apply_status(defect, defect_status.INVALID, now=datetime.now(timezone.utc))
    await record_activity(
        db,
        project_id=defect.project_id,
        event_type="defect.updated",
        actor=ActorRef.from_user(actor),
        entity_id=defect.id,
        entity_label=defect_label(defect),
        entity_type="defect",
        release_id=defect.release_id,
        context={"changed": "status → Invalid (agent action undone)"},
        before={"status": before},
        after={"status": defect_status.STATUS_LABELS[defect_status.INVALID]},
        changed_fields=["resolution_status"],
    )
    await db.flush()
