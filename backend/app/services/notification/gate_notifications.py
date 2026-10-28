"""The "Quality gate failed" notifications (W09 NTF-01).

``NotificationEventType.QUALITY_GATE_FAILED`` was declared, prioritised and
given colours in every channel, yet nothing staged it. This module is its one
producer, for three subjects:

* a RECORDED release or phase gate decision whose verdict moved INTO ``NO_GO``
  (the verdict recorded immediately before it, for the same release / phase,
  was ``GO``, ``NOT_EVALUATED`` or none) -- ``stage_gate_no_go``, called by
  ``release_gate_service.evaluate_release`` / ``release_phase_gate_service.
  evaluate_phase`` right after ``record_decision``. Preview evaluations
  (``record=False``) never reach it;
* a QA lead's override of a run's release decision into ``NO_GO`` --
  ``stage_override_no_go``, called by ``routers/release_readiness.py`` before
  its commit;
* the deep pipeline's published release-readiness value of ``NO_GO`` for a run
  -- ``stage_agent_decision_no_go``, called ONLY by the Celery task
  ``notify_release_decision_no_go`` on the ``default`` queue (never inside the
  pipeline task), which the decision-report critic enqueues after it publishes.
  The value is ``report_distribution_policy.release_review_projection``'s, the
  rule the release-readiness response and the ``release.decided`` webhook
  share: a value the review gate withholds reads ``PENDING_REVIEW`` and never
  notifies.

Stage-only: nothing here commits or opens a session. A row staged here is a
``notification_logs`` delivery child (``status = 'pending'``), which is also the
in-app entry the bell lists from the commit on, before the relay sends it. Each
row's ``delivery_metadata.app_path`` is the one in-app path the bell may link
to: ``APP_PATH_RE`` allows three shapes (``/releases/<uuid>``,
``/release-gate/<uuid>``, ``/test-management?case=<uuid>``) and ``app_link``
re-checks them when the history is read.

A notification never fails the user's action: the two request-path stages run
in a SAVEPOINT, log one warning and return False on any error.

De-duplication is the manager's ``delivery_key`` (scope + user + channel +
event, ``ON CONFLICT DO NOTHING``): one delivery per subscriber-channel per gate
decision id, one per run for the agent (re-analyses do not re-notify), one per
override ordinal (the committed audit position, as ``release.decided``).

The quality gate notification is governed by the subscriber's preference only,
never by the project's notification policy (W09 contract D-3).
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Mapping, Sequence

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.models.postgres import (
    NotificationEventType,
    Project,
    Release,
    ReleaseDecision,
    TestRun,
)
from app.services import release_gate_reasons
from app.services.notification import manager

logger = structlog.get_logger("services.gate_notifications")

EVENT = NotificationEventType.QUALITY_GATE_FAILED        # "quality_gate_failed"
NO_GO = "NO_GO"
MAX_REASONS = 3              # reasons quoted in a body
REASON_MAX_CHARS = 300       # each reason clipped with "…"
TITLE_MAX_CHARS = 200        # NotificationLog.title is String(500); the bell truncates at its width anyway
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# The ONLY three shapes a notification may link to: a release, a run's release
# decision, and (W21d ACT-02, a comment mention) a test case on /test-management.
APP_PATH_RE = re.compile(rf"^(?:/(?:releases|release-gate)/{_UUID}|/test-management\?case={_UUID})$")

TRIGGER_AGENT = "agent"
TRIGGER_OVERRIDE = "override"

#: What ``stage_agent_decision_no_go`` answers, in the order it checks.
AGENT_OUTCOMES = (
    "no_decision",
    "pipeline_mismatch",
    "overridden",
    "no_council",
    "withheld",
    "not_no_go",
    "staged",
)

_ELLIPSIS = "…"
_NO_REASONS_BODY = "The gate recorded NO_GO."


# ── Pure rules ───────────────────────────────────────────────────────────────


def is_transition_into_no_go(previous_verdict: str | None, verdict: str) -> bool:
    """``verdict`` is ``NO_GO`` and the one before it was not. None (a first verdict) is not ``NO_GO``."""
    return verdict == NO_GO and previous_verdict != NO_GO


def gate_delivery_scope(decision_id: uuid.UUID | str) -> str:
    """One delivery per subscriber-channel per recorded gate decision (release or phase)."""
    return f"quality-gate-failed:gate:{decision_id}:v1"


def run_delivery_scope(run_id: uuid.UUID | str, *, override_ordinal: int | None = None) -> str:
    """The agent's: ONE per run however often the pipeline re-publishes; an override's: one per audit position."""
    if override_ordinal is None:
        return f"quality-gate-failed:run:{run_id}:agent:v1"
    return f"quality-gate-failed:run:{run_id}:override:{override_ordinal}:v1"


def app_link(metadata: Mapping[str, Any] | None) -> str | None:
    """``metadata["app_path"]`` when it is one of the allowed in-app shapes, else None. Never raises.

    The shapes (``APP_PATH_RE``, whole value, lower-case uuid): ``/releases/<uuid>``,
    ``/release-gate/<uuid>`` and ``/test-management?case=<uuid>`` (a mention's case).
    """
    try:
        if not isinstance(metadata, Mapping):
            return None
        value = metadata.get("app_path")
        if isinstance(value, str) and APP_PATH_RE.fullmatch(value):
            return value
    except Exception:  # noqa: BLE001 -- a malformed metadata blob is "no link", never a 500
        return None
    return None


def clip(value: Any, limit: int) -> str:
    """``value`` as text, cut to at most ``limit`` characters with a trailing ``…``."""
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + _ELLIPSIS


def build_body(shown: Sequence[str], total: int) -> str:
    """At most ``MAX_REASONS`` bullet lines, then ``+N more``; no reasons -> the plain sentence."""
    lines = [f"• {clip(reason, REASON_MAX_CHARS)}" for reason in list(shown)[:MAX_REASONS]]
    if not lines:
        return _NO_REASONS_BODY
    body = "\n".join(lines)
    if total > MAX_REASONS:
        body += f"\n+{total - MAX_REASONS} more"
    return body


def _base_url() -> str:
    return str(settings.public_base_url).rstrip("/")


def build_gate_message(
    *,
    release_id: uuid.UUID | str,
    release_name: str,
    project_name: str | None,
    decision_id: uuid.UUID | str,
    previous_verdict: str | None,
    reasons: Sequence[str],
    total_reasons: int,
    phase_id: uuid.UUID | str | None = None,
    phase_name: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """(title, body, metadata) of a release / phase gate's transition into ``NO_GO``.

    Names stay verbatim (escaping is each renderer's job: ``email_service.escape``,
    React text in the SPA). ``reasons`` are already readable (test names, not
    fingerprints) and at most ``MAX_REASONS``; ``total_reasons`` counts them all.
    No ``pass_rate`` / ``total_tests`` / ``failed_tests`` keys: every sender reads
    them with ``.get``, so the stats rows stay empty rather than invented.
    """
    shown = [clip(r, REASON_MAX_CHARS) for r in list(reasons)[:MAX_REASONS]]
    head = f"Quality gate failed: {release_name}"
    if phase_name:
        head += f" · {phase_name}"
    if shown:
        head += f" — {shown[0]}"
    title = clip(head, TITLE_MAX_CHARS)
    body = build_body(shown, total_reasons)
    path = f"/releases/{release_id}"
    metadata: dict[str, Any] = {
        "project_name": project_name,
        "release_id": str(release_id),
        "release_name": release_name,
        "phase_id": str(phase_id) if phase_id is not None else None,
        "phase_name": phase_name,
        "decision_id": str(decision_id),
        "verdict": NO_GO,
        "previous_verdict": previous_verdict,
        "blocking_reasons": shown,
        "app_path": path,
        "dashboard_url": f"{_base_url()}{path}",
    }
    return title, body, metadata


def build_run_message(
    *,
    run_id: uuid.UUID | str,
    project_name: str | None,
    build_number: str | None,
    issues: Sequence[str],
    trigger: str,
) -> tuple[str, str, dict[str, Any]]:
    """(title, body, metadata) of a run's release decision reading ``NO_GO`` (agent or override)."""
    issues = [str(i) for i in (issues or [])]
    shown = [clip(i, REASON_MAX_CHARS) for i in issues[:MAX_REASONS]]
    subject = str(project_name or "Project")
    # A run always has a build number (the column is NOT NULL); a blank one is
    # left out rather than printed as "build None".
    if build_number not in (None, ""):
        subject += f" build {build_number}"
    head = f"Quality gate failed: {subject}"
    if shown:
        head += f" — {shown[0]}"
    title = clip(head, TITLE_MAX_CHARS)
    body = build_body(shown, len(issues))
    path = f"/release-gate/{run_id}"
    metadata: dict[str, Any] = {
        "project_name": project_name,
        "build_number": build_number if build_number is not None else "",
        "run_id": str(run_id),
        "trigger": trigger,
        "verdict": NO_GO,
        "blocking_reasons": shown,
        "app_path": path,
        "dashboard_url": f"{_base_url()}{path}",
    }
    return title, body, metadata


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _entry_field(entry: Any, name: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


async def _stage(
    db: Any,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None,
    title: str,
    body: str,
    metadata: dict[str, Any],
    delivery_scope: str,
) -> None:
    await manager.stage_scoped_preference_deliveries(
        db,
        project_id=project_id,
        run_id=run_id,
        events=[EVENT],
        build_title_fn=lambda _event: (title, body),
        metadata=metadata,
        delivery_scope=delivery_scope,
    )


# ── Request-path stages (SAVEPOINT + log + swallow) ─────────────────────────


async def stage_gate_no_go(
    db: Any,
    *,
    release_id: uuid.UUID | str,
    decision: Any,
    previous_verdict: str | None,
    phase: Any = None,
) -> bool:
    """Stage one "Quality gate failed" entry per subscriber for a recorded gate decision.

    The caller has already established ``is_transition_into_no_go``. Runs inside
    a SAVEPOINT on the caller's session; any exception (a fake session without
    ``begin_nested`` included) is logged once and answered False, so a
    notification fault never fails the gate evaluation. Returns True when the
    stage ran.
    """
    try:
        async with db.begin_nested():
            found = (
                await db.execute(
                    select(Release, Project.name)
                    .join(Project, Project.id == Release.project_id)
                    .where(Release.id == release_id)
                )
            ).first()
            if found is None:
                return False
            release, project_name = found
            all_reasons = [str(r) for r in (getattr(decision, "blocking_reasons", None) or [])]
            readable = await release_gate_reasons.readable_reasons(
                db, release.project_id, all_reasons[:MAX_REASONS]
            )
            title, body, metadata = build_gate_message(
                release_id=release.id,
                release_name=release.name,
                project_name=project_name,
                decision_id=decision.id,
                previous_verdict=previous_verdict,
                reasons=readable,
                total_reasons=len(all_reasons),
                phase_id=getattr(phase, "id", None) if phase is not None else None,
                phase_name=getattr(phase, "name", None) if phase is not None else None,
            )
            await _stage(
                db,
                project_id=release.project_id,
                run_id=None,
                title=title,
                body=body,
                metadata=metadata,
                delivery_scope=gate_delivery_scope(decision.id),
            )
        return True
    except Exception as exc:  # noqa: BLE001 -- a notification never fails the gate
        logger.warning(
            "quality_gate_notification_failed",
            release_id=str(release_id),
            error_type=type(exc).__name__,
        )
        return False


async def stage_override_no_go(
    db: Any,
    *,
    run_id: uuid.UUID | str,
    project_id: uuid.UUID | str,
    council: Any,
) -> bool:
    """Stage "Quality gate failed" for an override INTO ``NO_GO`` (before the handler's commit).

    Stages iff the council now reads ``NO_GO`` and the committed audit entry's
    ``before_recommendation`` was not ``NO_GO``; the delivery scope is the
    entry's ordinal. Same SAVEPOINT / swallow rule as ``stage_gate_no_go``.
    """
    try:
        if str(getattr(council, "recommendation", "") or "") != NO_GO:
            return False
        audit = list(getattr(council, "override_audit", None) or [])
        if not audit or _entry_field(audit[-1], "before_recommendation") == NO_GO:
            return False
        run_uuid = _as_uuid(run_id)
        project_uuid = _as_uuid(project_id)
        async with db.begin_nested():
            found = (
                await db.execute(
                    select(TestRun.build_number, Project.name)
                    .join(Project, Project.id == TestRun.project_id)
                    .where(TestRun.id == run_uuid, TestRun.project_id == project_uuid)
                )
            ).first()
            if found is None:
                return False
            build_number, project_name = found
            title, body, metadata = build_run_message(
                run_id=run_uuid,
                project_name=project_name,
                build_number=build_number,
                issues=list(getattr(council, "blocking_issues", None) or []),
                trigger=TRIGGER_OVERRIDE,
            )
            await _stage(
                db,
                project_id=project_uuid,
                run_id=run_uuid,
                title=title,
                body=body,
                metadata=metadata,
                delivery_scope=run_delivery_scope(run_uuid, override_ordinal=len(audit)),
            )
        return True
    except Exception as exc:  # noqa: BLE001 -- the override stands whatever happens here
        logger.warning(
            "quality_gate_notification_failed",
            run_id=str(run_id),
            error_type=type(exc).__name__,
        )
        return False


# ── The agent path (the Celery task's own session; the task commits) ─────────


async def stage_agent_decision_no_go(
    db: Any,
    *,
    run_id: str,
    pipeline_run_id: str | None,
    evidence_bundle_sha256: str | None,
) -> str:
    """Stage "Quality gate failed" for the pipeline's published ``NO_GO``; answer the outcome.

    Called ONLY by ``app.worker.tasks.notify_release_decision_no_go`` on its own
    session, which commits. Read-only apart from the staging: the review-gate
    projection writes no audit row. Raises on a database error (the task logs
    it and answers ``"error"``). Outcomes, in the order checked:
    ``AGENT_OUTCOMES``.
    """
    from app.services import report_distribution_policy  # noqa: PLC0415
    from app.services.release_council_service import get_release_council  # noqa: PLC0415

    run_uuid = _as_uuid(run_id)
    decision = (
        await db.execute(select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_uuid))
    ).scalar_one_or_none()
    if decision is None:
        return "no_decision"
    if pipeline_run_id is not None and str(decision.pipeline_run_id) != str(pipeline_run_id):
        return "pipeline_mismatch"
    if decision.human_override is not None:
        # A person decided; the override path notifies for that (as release.decided).
        return "overridden"
    council = await get_release_council(run_uuid, db)
    if council is None:
        return "no_council"
    _envelope, _enforced, withhold = await report_distribution_policy.release_review_projection(
        db,
        run_id=run_uuid,
        synthesized=bool(getattr(council, "synthesized", False)),
        human_override=getattr(council, "human_override", None),
        pipeline_run_id=decision.pipeline_run_id,
        evidence_bundle_sha256=evidence_bundle_sha256,
    )
    if withhold:
        return "withheld"
    if str(getattr(council, "recommendation", "") or "") != NO_GO:
        return "not_no_go"
    found = (
        await db.execute(
            select(TestRun.project_id, TestRun.build_number, Project.name)
            .join(Project, Project.id == TestRun.project_id)
            .where(TestRun.id == run_uuid)
        )
    ).first()
    if found is None:
        return "no_decision"
    project_id, build_number, project_name = found
    title, body, metadata = build_run_message(
        run_id=run_uuid,
        project_name=project_name,
        build_number=build_number,
        issues=list(getattr(council, "blocking_issues", None) or []),
        trigger=TRIGGER_AGENT,
    )
    await _stage(
        db,
        project_id=project_id,
        run_id=run_uuid,
        title=title,
        body=body,
        metadata=metadata,
        delivery_scope=run_delivery_scope(run_uuid),
    )
    return "staged"


__all__ = [
    "AGENT_OUTCOMES",
    "APP_PATH_RE",
    "EVENT",
    "MAX_REASONS",
    "NO_GO",
    "REASON_MAX_CHARS",
    "TITLE_MAX_CHARS",
    "app_link",
    "build_body",
    "build_gate_message",
    "build_run_message",
    "clip",
    "gate_delivery_scope",
    "is_transition_into_no_go",
    "run_delivery_scope",
    "stage_agent_decision_no_go",
    "stage_gate_no_go",
    "stage_override_no_go",
]
