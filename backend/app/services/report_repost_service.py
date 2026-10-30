"""Re-post a run's PR / MR comment after a person accepts its AI report (W23c AGT-10 R-1 ... R-4).

Accepting a run's AI report lets its failure-kind labels pass the review gate
as reviewed. The run's existing TestLookup comment on the pull / merge request
was posted at ingest, before anyone looked; this module brings it up to date:

* :func:`stage_report_reposts` stages up to two ``run_downstream_outbox`` rows
  (``pr_comment_repost`` / ``mr_note_repost``) on the ACCEPT's transaction --
  only when the project has that host's integration or the run carries PR
  context for it. No integration and no PR context = nothing to re-post,
  nothing staged, nothing recorded (the common case);
* :func:`execute_repost` (the two Celery tasks) re-checks everything on short
  read sessions, applies the race rule (a newer run of the same PR owns the
  comment), then EDITS the marker comment through the git-host service -- it
  never POSTs one -- and records ONE ``integration.comment_reposted`` activity
  row per terminal outcome. A timeout is retried by the outbox and recorded
  once, at the end, as ``timed_out`` -- never ``failed``.

Stage-only: nothing here commits (the activity row is an ``attempt`` event
recorded with ``db=None``; the activity service owns that write). Log lines
carry ids, kinds, outcomes and reasons -- never a URL, a token, a PR body or a
reviewer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import func, select

from app.services.activity.service import ActorRef, record as record_activity
from app.services.review_gate_vocab import (
    REPOST_KINDS,
    REPOST_SENTENCES,
    REVIEWED_LINE,
)

__all__ = [
    "RepostTimedOut",
    "execute_repost",
    "repost_kinds_for",
    "stage_report_reposts",
]

_slog = structlog.get_logger(__name__)

_GITHUB_KIND, _GITLAB_KIND = REPOST_KINDS  # ("pr_comment_repost", "mr_note_repost")
_HOSTS = {
    _GITHUB_KIND: {"host": "PR comment", "provider": "GitHub", "code": "github"},
    _GITLAB_KIND: {"host": "MR note", "provider": "GitLab", "code": "gitlab"},
}
REPOST_QUEUE = "default"
REPOST_PRIORITY = 6


class RepostTimedOut(Exception):
    """The git host timed out and the outbox has attempts left: retry later."""


# ── R-1 staging (the accept's transaction) ───────────────────────────────────


def _reposts_for_review(review: Any) -> bool:
    """Only a run's own AI report review re-posts: not an inline invocation's
    (subject ``invocation``), not an eval-drift / capability review, not an
    Investigator review."""
    return (
        getattr(review, "kind", None) == "report"
        and getattr(review, "subject_type", None) == "pipeline_run"
        and getattr(review, "test_run_id", None) is not None
        and getattr(review, "workflow_type", None) != "investigation"
    )


def repost_kinds_for(
    *,
    pr_number: Optional[int],
    ci_repo: Optional[str],
    ci_provider: Optional[str],
    has_github: bool,
    has_gitlab: bool,
) -> list[str]:
    """PURE: the kinds to stage. A host's kind when the project has that host's
    integration, or the run has PR context (``pr_number`` + ``ci_repo``) on it."""
    pr_context = bool(pr_number) and bool((ci_repo or "").strip())
    gitlab_run = "gitlab" in (ci_provider or "").lower()
    kinds: list[str] = []
    if has_github or (pr_context and not gitlab_run):
        kinds.append(_GITHUB_KIND)
    if has_gitlab or (pr_context and gitlab_run):
        kinds.append(_GITLAB_KIND)
    return kinds


async def stage_report_reposts(db: Any, *, review: Any) -> list[str]:
    """Stage the re-post rows on the CALLER's transaction; return the kinds
    inserted. ``input_version`` carries no clock, so a replayed accept hits
    ``uq_run_downstream_operation_version`` and stages nothing twice."""
    if not _reposts_for_review(review):
        return []
    from app.models.postgres import TestRun  # noqa: PLC0415
    from app.services import (  # noqa: PLC0415
        github_checks_service,
        gitlab_integration_service,
        run_downstream_outbox,
    )

    run = (
        await db.execute(
            select(
                TestRun.id, TestRun.project_id, TestRun.pr_number,
                TestRun.ci_repo, TestRun.ci_provider,
            ).where(
                TestRun.id == review.test_run_id,
                TestRun.project_id == review.project_id,
            )
        )
    ).first()
    if run is None:
        return []
    run_id, project_id, pr_number, ci_repo, ci_provider = run
    kinds = repost_kinds_for(
        pr_number=pr_number,
        ci_repo=ci_repo,
        ci_provider=ci_provider,
        has_github=await github_checks_service.get_integration(db, project_id) is not None,
        has_gitlab=await gitlab_integration_service.get_integration(db, project_id) is not None,
    )
    staged: list[str] = []
    for kind in kinds:
        payload = {"test_run_id": str(run_id), "review_id": str(review.id)}
        inserted = await run_downstream_outbox.stage_downstream_operation(
            db,
            run_id=run_id,
            project_id=project_id,
            operation=kind,
            input_version=run_downstream_outbox.operation_input_version(kind, payload),
            payload=payload,
            queue=REPOST_QUEUE,
            priority=REPOST_PRIORITY,
        )
        if inserted:
            staged.append(kind)
    if staged:
        _slog.info(
            "report_repost_staged", review_id=str(review.id), run_id=str(run_id), kinds=staged,
        )
    return staged


# ── R-3 the executor (task-owned; short read sessions, released before HTTP) ──


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _reviewed_at_text(reviewed_at: Optional[datetime]) -> str:
    instant = reviewed_at or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def _host_gate(db: Any, kind: str, project_id: Any, ci_repo: Optional[str]) -> Optional[str]:
    """The first refusing reason, in the git-host resolver's own order."""
    if kind == _GITHUB_KIND:
        from app.services.github_target import github_target_status  # noqa: PLC0415

        reason, _api_base = await github_target_status(db, project_id, ci_repo=ci_repo)
        return reason
    from app.core.config import settings  # noqa: PLC0415
    from app.services import gitlab_integration_service as gitlab  # noqa: PLC0415

    if settings.AI_OFFLINE_MODE:
        return "offline_mode"
    target = await gitlab.load_gitlab_target(db, project_id)
    if target.reason:
        return target.reason
    if not await gitlab._post_allowed(db, project_id=project_id):
        return "gitlab_off"
    return None


async def _comments_off(db: Any, kind: str, project_id: Any) -> bool:
    from app.services import github_checks_service, gitlab_integration_service  # noqa: PLC0415

    if kind == _GITHUB_KIND:
        github_row = await github_checks_service.get_integration(db, project_id)
        mode = getattr(github_row, "pr_comment_mode", None)
    else:
        gitlab_row = await gitlab_integration_service.get_integration(db, project_id)
        mode = getattr(gitlab_row, "mr_comment_mode", None)
    return (mode or "failures_only").lower() == "off"


async def _preflight(kind: str, run_id: uuid.UUID, review_id: uuid.UUID) -> dict[str, Any]:
    """Steps 1-5 on ONE short read session, closed before any HTTP. Returns the
    facts the activity row needs and ``skip`` (a reason, or None to go on)."""
    from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415
    from app.models.postgres import ReviewRequest, TestRun  # noqa: PLC0415
    from app.services.review_envelope import review_envelope_for_run  # noqa: PLC0415

    async with AsyncSessionLocal() as db:
        run = (
            await db.execute(
                select(
                    TestRun.id, TestRun.project_id, TestRun.build_number,
                    TestRun.pr_number, TestRun.ci_repo,
                ).where(TestRun.id == run_id)
            )
        ).first()
        review = (
            await db.execute(
                select(ReviewRequest.project_id, ReviewRequest.reviewed_at).where(
                    ReviewRequest.id == review_id
                )
            )
        ).first()
        facts: dict[str, Any] = {
            "project_id": run.project_id if run is not None else None,
            "build_number": run.build_number if run is not None else None,
            "reviewed_at": review.reviewed_at if review is not None else None,
            "skip": None,
        }
        if run is None or review is None or review.project_id != run.project_id:
            if run is None and review is not None:
                facts["project_id"] = review.project_id
            elif run is not None and review is not None:
                facts["project_id"] = None  # ids that disagree: record nowhere
            return {**facts, "skip": "run_not_found"}
        envelope = await review_envelope_for_run(db, run.id)
        if envelope.state != "accepted" or envelope.review_id != str(review_id):
            return {**facts, "skip": "review_no_longer_accepted"}
        reason = await _host_gate(db, kind, run.project_id, run.ci_repo)
        if reason:
            return {**facts, "skip": reason}
        if not run.pr_number or not (run.ci_repo or "").strip():
            return {**facts, "skip": "no_pr_for_run"}
        if await _comments_off(db, kind, run.project_id):
            return {**facts, "skip": "mode_off"}
        # The race rule: the newest run of this PR owns the marker comment.
        latest = (
            await db.execute(
                select(TestRun.id)
                .where(
                    TestRun.project_id == run.project_id,
                    TestRun.pr_number == run.pr_number,
                    func.lower(TestRun.ci_repo) == func.lower(run.ci_repo),
                )
                .order_by(TestRun.created_at.desc(), TestRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None and latest != run.id:
            return {**facts, "skip": "superseded_by_newer_run"}
        return facts


async def _is_final_attempt(outbox_id: Any) -> bool:
    """True when the delivering outbox row has no attempt left -- or there is
    no row to retry (a direct call, a deleted row, an unreadable one): the
    timeout is then recorded now instead of looping."""
    from app.services.run_downstream_outbox import MAX_EXECUTION_ATTEMPTS  # noqa: PLC0415

    oid = _as_uuid(outbox_id) if outbox_id else None
    if oid is None:
        return True
    try:
        from app.db.postgres import AsyncSessionLocal  # noqa: PLC0415
        from app.models.postgres import RunDownstreamOutbox  # noqa: PLC0415

        async with AsyncSessionLocal() as db:
            attempts = (
                await db.execute(
                    select(RunDownstreamOutbox.execution_attempts).where(
                        RunDownstreamOutbox.id == oid
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 -- record rather than loop
        _slog.warning("report_repost_attempts_unreadable", error_type=type(exc).__name__)
        return True
    return attempts is None or int(attempts) >= MAX_EXECUTION_ATTEMPTS


def _sentence(kind: str, result: dict[str, Any], error_type: Optional[str]) -> str:
    host = _HOSTS[kind]
    outcome = result["outcome"]
    if outcome == "updated":
        template = REPOST_SENTENCES["updated"]
    elif outcome == "timed_out":
        template = REPOST_SENTENCES["timed_out_final"]
    elif outcome == "failed":
        template = REPOST_SENTENCES["failed"]
    else:
        template = REPOST_SENTENCES.get(
            f"skipped:{result['reason']}", "{host} re-post skipped (" + str(result["reason"]) + ")."
        )
    status_code = result.get("status_code")
    status = f"HTTP {status_code}" if status_code else (error_type or "no response")
    return template.format(host=host["host"], provider=host["provider"], status=status)


async def _record_outcome_row(
    kind: str,
    *,
    facts: dict[str, Any],
    run_id: Optional[uuid.UUID],
    review_id: Optional[uuid.UUID],
    result: dict[str, Any],
    error_type: Optional[str],
) -> None:
    """ONE ``integration.comment_reposted`` row for a terminal outcome."""
    project_id = facts.get("project_id")
    if project_id is None or run_id is None:
        _slog.warning("report_repost_not_recorded", kind=kind, reason="no_project_for_ids")
        return
    build_number = facts.get("build_number")
    await record_activity(
        None,
        project_id=project_id,
        event_type="integration.comment_reposted",
        actor=ActorRef.system("review_repost"),
        entity_id=run_id,
        entity_label=f"Build {build_number}" if build_number else f"run {str(run_id)[:8]}",
        context={
            "sentence": _sentence(kind, result, error_type),
            "host": _HOSTS[kind]["code"],
            "outcome": result["outcome"],
            "reason": result.get("reason"),
            "review_id": str(review_id) if review_id else None,
            "status_code": result.get("status_code"),
        },
    )


async def execute_repost(
    kind: str,
    *,
    test_run_id: str,
    review_id: str,
    outbox_id: Optional[str] = None,
) -> dict[str, Any]:
    """Re-post one host's comment for an accepted review. Returns
    ``{"outcome", "reason", "status_code"}``; raises :class:`RepostTimedOut` for
    a timeout while the delivering outbox row has attempts left (no activity
    row yet). Every terminal outcome records ONE activity row."""
    if kind not in _HOSTS:
        raise ValueError(f"unsupported_repost_kind:{kind}")
    run_uuid, review_uuid = _as_uuid(test_run_id), _as_uuid(review_id)
    error_type: Optional[str] = None
    if run_uuid is None or review_uuid is None:
        facts: dict[str, Any] = {"project_id": None, "skip": "run_not_found"}
    else:
        facts = await _preflight(kind, run_uuid, review_uuid)
    if facts["skip"] or run_uuid is None:
        result: dict[str, Any] = {
            "outcome": "skipped", "reason": facts["skip"] or "run_not_found", "status_code": None,
        }
    else:
        from app.services import (  # noqa: PLC0415
            github_pr_comment_service,
            gitlab_integration_service,
        )

        reviewed_line = REVIEWED_LINE.format(reviewed_at=_reviewed_at_text(facts["reviewed_at"]))
        repost = (
            github_pr_comment_service.repost_pr_summary_for_run
            if kind == _GITHUB_KIND
            else gitlab_integration_service.repost_mr_note_for_run
        )
        answer = await repost(run_uuid, reviewed_line=reviewed_line)
        result = {
            "outcome": answer.get("outcome") or "failed",
            "reason": answer.get("reason"),
            "status_code": answer.get("status_code"),
        }
        error_type = answer.get("error_type")
        if result["outcome"] == "timed_out" and not await _is_final_attempt(outbox_id):
            _slog.info(
                "report_repost_retrying",
                kind=kind,
                run_id=str(run_uuid),
                sentence=REPOST_SENTENCES["timed_out"].format(host=_HOSTS[kind]["host"]),
            )
            raise RepostTimedOut(kind)
    await _record_outcome_row(
        kind, facts=facts, run_id=run_uuid, review_id=review_uuid,
        result=result, error_type=error_type,
    )
    _slog.info(
        "report_repost_done",
        kind=kind,
        run_id=str(run_uuid) if run_uuid else None,
        review_id=str(review_uuid) if review_uuid else None,
        outcome=result["outcome"],
        reason=result["reason"],
        status_code=result["status_code"],
    )
    return result
