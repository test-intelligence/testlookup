"""Guards that make a per-run delete safe to expose (S2c).

The nightly purge gets every one of these for free. It only touches projects
that opted in, and only selects rows already past a cutoff — so a prefix
pointing somewhere unexpected is still a prefix inside a project that asked to
be purged, and a run old enough to purge is not one an auditor is reading.

A DELETE endpoint has neither bound. An ADMIN names one run, now, and the
executor does what the row says. These are the checks that difference requires.

**Retention holds (W22a ACT-05) live in** :func:`citation_blockers`, as its
LAST read: every explicit path already calls it — the single-run DELETE route,
the criteria preview, and both workers through :func:`execution_blockers` — so
a run covered by a live hold (directly, through its release, as a held run's
baseline, or by a project hold) is refused everywhere with ``Held: <reason>``
listed first. A hold read that fails refuses (``HOLDS_UNREADABLE``), like an
unreachable decision-report store. The policy purge reads the same hold service
through its anti-join clauses instead (``retention_service``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import structlog
from sqlalchemy import func, select

from app.db.mongo import Collections
from app.models.postgres import (
    CompliancePack,
    LaunchStatus,
    ReleaseTestRunLink,
)
from app.services import retention_hold_service
from app.services.retention_vocab import HELD_SENTENCE

logger = structlog.get_logger(__name__)

#: The blocker a failed hold read adds — fail CLOSED, like the Mongo branch.
HOLDS_UNREADABLE = (
    "could not read retention holds — refusing rather than deleting evidence "
    "that may be held"
)
#: Every hold refusal starts with this (``HELD_SENTENCE`` up to its reason).
HELD_PREFIX = HELD_SENTENCE.split("{", 1)[0]


def held_first(reasons: Iterable[str]) -> list[str]:
    """The same reasons with every ``Held: …`` sentence first (stable otherwise)."""
    items = list(reasons)
    return [r for r in items if r.startswith(HELD_PREFIX)] + [
        r for r in items if not r.startswith(HELD_PREFIX)
    ]


def has_hold_blocker(reasons: Iterable[str]) -> bool:
    """True when a live retention hold is among the reasons (the worker then reads the refs)."""
    return any(r.startswith(HELD_PREFIX) for r in reasons)

#: Statuses that refuse deletion. A run still executing has writers pointed at
#: it; deleting it races them and produces a half-run.
NON_DELETABLE_STATUSES = frozenset({LaunchStatus.IN_PROGRESS.value})


def status_blocks_deletion(status: Any) -> bool:
    """True when a run in this status must not be deleted.

    Accepts the enum or its stored string: ``TestRun.status`` is a
    ``String(20)`` column typed as ``LaunchStatus``, so both shapes reach
    callers depending on whether the row came from the ORM or a raw select.
    """
    value = getattr(status, "value", status)
    return str(value) in NON_DELETABLE_STATUSES


def safe_artifact_prefixes(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    minio_prefix: Optional[str],
) -> tuple[list[str], list[str]]:
    """Split this run's object prefixes into (safe to delete, refused).

    **Why this exists (RET-D9).** ``TestRun.minio_prefix`` is uploader-derived:
    ``webhooks.py`` builds it as ``key.split("/")[:-1]`` from the object key
    whoever uploaded chose, and the only existing validation rejects empty. A
    sentinel written to ``uploads/shared/upload_complete.json`` yields the
    prefix ``uploads/shared/`` — and a delete that honours that wipes a shared
    area belonging to every project.

    The nightly purge is bounded by only ever running against opted-in
    projects. This endpoint is not, so the prefix is validated against the
    project scope here.

    Refused prefixes are RETURNED, not dropped. An object prefix that survives
    the delete has to be reported or the storage simply will not fall and
    nothing says why.
    """
    pid, rid = str(project_id), str(run_id)

    # The canonical prefix is derived, not stored: a run whose minio_prefix is
    # NULL still has objects there, and the nightly purge already deletes it
    # unconditionally.
    safe: list[str] = [f"uploads/{pid}/{rid}/"]
    refused: list[str] = []

    if not minio_prefix:
        return safe, refused

    candidate = minio_prefix.strip()
    # Trailing slash matters: without it, ``{project}x/`` shares a string
    # prefix with ``{project}`` and a bare startswith accepts it.
    allowed_roots = (f"uploads/{pid}/", f"{pid}/")
    if candidate.startswith(allowed_roots) and ".." not in candidate:
        if candidate not in safe:
            safe.append(candidate)
    else:
        refused.append(candidate)
        logger.warning(
            "run_delete_prefix_outside_project_scope",
            project_id=pid,
            run_id=rid,
            prefix=candidate,
        )

    return safe, refused


async def citation_blockers(db, *, run_id: uuid.UUID, mongo) -> list[str]:
    """Reasons this run must not be deleted, or an empty list.

    Every blocker is collected rather than returning on the first: an operator
    who clears one should not then discover a second, which turns one refusal
    into a sequence of round trips.

    **Unreachable stores BLOCK.** This is the opposite stance from the
    tombstone lookup, and deliberately so. Failing open there risks a
    resurrected row an operator can delete again; failing open here destroys
    evidence that a published report cites, which nothing can undo. When the
    citation store cannot be reached, the honest answer is "cannot prove this
    is uncited", not "it is uncited".

    **Retention holds are read LAST** (one statement, ``.all()``) and their
    ``Held: <reason>`` sentences are PREPENDED, one per distinct live hold
    covering the run. A failing hold read appends ``HOLDS_UNREADABLE``: no
    route, flag or role deletes a run whose holds could not be read.
    """
    blockers: list[str] = []

    packs = (
        await db.execute(
            select(CompliancePack.id).where(CompliancePack.test_run_id == run_id)
        )
    ).scalars().all()
    if packs:
        blockers.append(
            f"cited by {len(packs)} compliance pack(s) — retire the pack first"
        )

    releases = (
        await db.execute(
            select(ReleaseTestRunLink.release_id).where(
                ReleaseTestRunLink.test_run_id == run_id
            )
        )
    ).scalars().all()
    if releases:
        blockers.append(
            f"linked to {len(releases)} release(s) — unlink the run first"
        )

    try:
        reports = await mongo[Collections.DECISION_REPORTS].count_documents(
            {"test_run_id": str(run_id)}
        )
    except Exception as exc:  # noqa: BLE001 — unreachable must block, not allow
        logger.warning("run_delete_citation_check_failed", error=str(exc))
        blockers.append(
            "could not reach the decision-report store to check citations — "
            "refusing rather than deleting evidence that may be cited"
        )
    else:
        if reports:
            blockers.append(
                f"cited by {reports} decision report(s) — the report would "
                "outlive its subject"
            )

    try:
        holds = await retention_hold_service.holds_covering_run(
            db, run_id=run_id, now=datetime.now(timezone.utc)
        )
    except Exception as exc:  # noqa: BLE001 — fail closed: an unread hold must block
        logger.warning(
            "run_delete_hold_check_failed",
            run_id=str(run_id),
            error_type=type(exc).__name__,
        )
        blockers.append(HOLDS_UNREADABLE)
    else:
        blockers = [hold.sentence() for hold in holds] + blockers

    return blockers


async def execution_blockers(db, *, run, mongo) -> list[str]:
    """Revalidate mutable deletion protections at the worker boundary.

    Preview and task execution are separated by a queue. During that gap a run
    can resume or become cited by a release, compliance pack, or decision
    report — or become HELD (W22a). Deleting from the frozen set without
    checking again turns an approved preview into permission to destroy newly
    protected evidence. A ``Held: …`` reason is returned first.
    """
    reasons: list[str] = []
    if status_blocks_deletion(run.status):
        reasons.append("run is still executing")
    reasons.extend(await citation_blockers(db, run_id=run.id, mongo=mongo))
    return held_first(reasons)


async def hold_refs_for_refusal(db, *, run_id: uuid.UUID, reasons: Iterable[str]) -> list:
    """The structured live holds behind a refusal, for the job's ``holds_honoured``.

    Reads ONLY when a reason is a ``Held: …`` sentence (one more statement); a
    refusal for any other reason reads nothing. The run is refused either way,
    so a failure here costs the job record its detail, never the refusal.
    """
    if not has_hold_blocker(reasons):
        return []
    try:
        return await retention_hold_service.holds_covering_run(
            db, run_id=run_id, now=datetime.now(timezone.utc)
        )
    except Exception as exc:  # noqa: BLE001 — the refusal stands without the detail
        logger.warning(
            "run_delete_hold_refs_unreadable",
            run_id=str(run_id),
            error_type=type(exc).__name__,
        )
        return []


class RunDeletionBlocked(RuntimeError):
    """A run gained a mutable protection after the request was accepted.

    ``holds`` (W22a) carries the structured live holds (``HoldRef``) when a
    ``Held: …`` reason is among ``reasons``, for the job's ``holds_honoured``.
    """

    def __init__(
        self,
        run_id: uuid.UUID,
        reasons: list[str],
        holds: Sequence[Any] = (),
    ):
        self.run_id = run_id
        self.reasons = reasons
        self.holds = list(holds)
        super().__init__(f"run {run_id} is protected: {'; '.join(reasons)}")


async def live_session_slugs_for_run(db, *, project_id: uuid.UUID, run_id: uuid.UUID):
    """The client-side run ids under which this run's live events were written.

    ``live_execution_events`` is keyed by the CLIENT's run id — a
    ``LiveSession.run_id`` slug on the webhook path, a ``TestRun.id`` string
    elsewhere. A run-id-scoped resolver has no join to reach them, so a delete
    that only looked up the UUID would leave every live event behind while
    reporting the run removed.
    """
    from app.models.postgres import LiveSession
    from app.services.stream_service import canonical_test_run_uuid

    rows = (
        await db.execute(
            select(LiveSession.run_id).where(LiveSession.project_id == project_id)
        )
    ).scalars().all()

    keys = {str(run_id)}
    for slug in rows:
        if not slug:
            continue
        try:
            if canonical_test_run_uuid(str(slug)) == run_id:
                keys.add(str(slug))
        except Exception:  # noqa: BLE001 — a slug that will not map is not ours
            continue
    return sorted(keys)


async def count_live_sessions(db, *, project_id: uuid.UUID) -> int:
    """Cheap existence probe used by the endpoint's diagnostics."""
    from app.models.postgres import LiveSession

    return int(
        (
            await db.execute(
                select(func.count(LiveSession.id)).where(
                    LiveSession.project_id == project_id
                )
            )
        ).scalar()
        or 0
    )


async def perform_run_deletion(
    db,
    *,
    run,
    mongo,
    storage,
    search_index_documents,
    reason: str = "",
    deleted_by_id: Optional[uuid.UUID] = None,
    deletion_job_id: Optional[uuid.UUID] = None,
) -> dict:
    """The transaction a single-run delete performs. Stages; caller commits.

    Extracted from the Celery task so the integration suite exercises THIS
    sequence rather than a copy of it. The first version of that test used its
    own helper, which meant its rollback assertion proved a property of
    SQLAlchemy rather than anything about the ordering here — a mutation that
    split the tombstone into its own commit survived it.

    The order is load-bearing:

    1. Resolve candidates while the Postgres rows still exist. The CASCADE
       destroys the only mapping from a run to its Mongo documents and MinIO
       keys.
    2. Run the shared executor.
    3. Stage the tombstone.

    All three land in the caller's single commit. Committing the tombstone
    first blocks live ingestion into a run that still exists; committing it
    after leaves a window in which the run is gone and the five re-creation
    paths are free to bring it back.
    """
    from datetime import datetime, timezone

    from app.services import retention_service, run_tombstone_service

    cand, plan = await retention_service.resolve_run_candidates(
        db,
        project_id=run.project_id,
        run_id=run.id,
        minio_prefix=run.minio_prefix,
    )
    counts = await retention_service.execute_candidates(
        db,
        project_id=run.project_id,
        cand=cand,
        plan=plan,
        mongo=mongo,
        storage=storage,
        # Analysis caches are project-scoped and shared; a single-run delete
        # has no business clearing another run's entries.
        cache_counts={"redis": None, "semantic": None},
        search_index_documents=search_index_documents,
        now=datetime.now(timezone.utc),
    )
    run_tombstone_service.stage_tombstone(
        db,
        run_id=run.id,
        project_id=run.project_id,
        reason=reason,
        deleted_by_id=deleted_by_id,
        deletion_job_id=deletion_job_id,
    )
    return counts
