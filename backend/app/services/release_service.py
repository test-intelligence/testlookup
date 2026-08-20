"""
Release lifecycle service — CRUD for releases, phases, and test-run links.

Transaction model (item #2: one commit per request):
  * Commands stage changes via ``db.add`` / ``db.delete`` / mutation and
    call ``db.flush()`` only when they need a generated ID before
    continuing. They never call ``db.commit()``.
  * The ``releases`` router owns ``db.commit()`` — one commit per request.
  * Read commands (``list_releases``, ``get_release_details``) are pure
    queries and never touch the transaction.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.postgres import Project, Release, ReleasePhase, ReleaseTestRunLink, TestRun
from app.models.serializers import serialize_model  # noqa: F401

logger = structlog.get_logger(__name__)


async def get_release_or_404(db: AsyncSession, release_id: str) -> Release:
    release = (
        await db.execute(select(Release).where(Release.id == uuid.UUID(release_id)))
    ).scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


async def get_phase_or_404(db: AsyncSession, release_id: str, phase_id: str) -> ReleasePhase:
    phase = (
        await db.execute(
            select(ReleasePhase).where(
                ReleasePhase.id == uuid.UUID(phase_id),
                ReleasePhase.release_id == uuid.UUID(release_id),
            )
        )
    ).scalar_one_or_none()
    if not phase:
        raise HTTPException(status_code=404, detail="Phase not found")
    return phase


async def list_releases(
    db: AsyncSession,
    project_id: Optional[str],
    status: Optional[str] = None,
    accessible_project_ids: Optional[set] = None,
) -> dict:
    stmt = (
        select(Release)
        .options(selectinload(Release.phases))
        .order_by(Release.created_at.desc())
    )
    # Soft-deleted projects are excluded unconditionally; the pin below is
    # added on top. Without this the unscoped list returned every release ever
    # created — measured live, **36 of 38 rows belonged to 36 deleted
    # projects**, so the Releases page was almost entirely entries the user
    # cannot open, filter by, or navigate to.
    #
    # Seventh surface in this family (#535 runs, #538 dashboard, #539
    # analytics, #541 ROI, #547 trends, #549 defect KPI). Found by sweeping for
    # the *class* — a project filter conditional on ``project_id`` — rather
    # than waiting for the next one to be reported.
    stmt = stmt.where(
        Release.project_id.in_(select(Project.id).where(Project.is_active.is_(True)))
    )
    if project_id:
        stmt = stmt.where(Release.project_id == uuid.UUID(project_id))
    if accessible_project_ids is not None:
        # Tenant isolation (defence-in-depth): confine non-admin callers to
        # their memberships even when an explicit project_id is supplied, so a
        # provided project_id can't bypass scoping at the service layer if a
        # future caller forgets the router check. ``None`` = admin (no filter).
        stmt = stmt.where(Release.project_id.in_(list(accessible_project_ids)))
    if status:
        stmt = stmt.where(Release.status == status)
    rows = (await db.execute(stmt)).scalars().all()

    # Build project_name lookup when returning all-projects (no project filter)
    project_name_map: dict[str, str] = {}
    if not project_id and rows:
        project_ids = list({str(r.project_id) for r in rows})
        proj_result = await db.execute(
            select(Project).where(Project.id.in_([uuid.UUID(pid) for pid in project_ids]))
        )
        project_name_map = {str(p.id): p.name for p in proj_result.scalars().all()}

    items = []
    for release in rows:
        item = serialize_model(release)
        item["phases"] = [serialize_model(phase) for phase in release.phases]
        item["test_run_count"] = 0
        item["project_name"] = project_name_map.get(item["project_id"])
        items.append(item)

    if items:
        ids = [release.id for release in rows]
        count_query = text(
            """
            SELECT release_id::text, COUNT(*) AS cnt
            FROM release_test_run_links
            WHERE release_id = ANY(:ids)
            GROUP BY release_id
            """
        )
        count_rows = (await db.execute(count_query, {"ids": ids})).fetchall()
        count_map = {row[0]: row[1] for row in count_rows}
        for item in items:
            item["test_run_count"] = count_map.get(item["id"], 0)

    return {"items": items, "total": len(items)}


async def create_release(db: AsyncSession, body) -> Release:
    """Stage a new release (and its initial phases). Handler commits + serializes."""
    # Project-existence guard. Without it, a stale ``activeProjectId`` on
    # the FE (or a hand-rolled API call against a deleted project) hits
    # the asyncpg ForeignKeyViolationError as an opaque 500. The 404 here
    # is the same shape ``test_management_service.create_managed_test_case``
    # uses so the FE error-toast pipeline can format it identically.
    project_uuid = uuid.UUID(body.project_id)
    project = await db.get(Project, project_uuid)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {body.project_id} not found — refresh the page or pick a different project.",
        )
    logger.info("creating_release", project_id=body.project_id, name=body.name)
    release = Release(
        project_id=project_uuid,
        name=body.name,
        version=body.version,
        description=body.description,
        status=body.status,
        planned_date=body.planned_date,
    )
    db.add(release)
    await db.flush()  # need release.id for child phases

    for idx, phase_in in enumerate(body.phases):
        db.add(
            ReleasePhase(
                release_id=release.id,
                name=phase_in.name,
                phase_type=phase_in.phase_type,
                status=phase_in.status,
                description=phase_in.description,
                order_index=phase_in.order_index if phase_in.order_index else idx,
                planned_start=phase_in.planned_start,
                planned_end=phase_in.planned_end,
                exit_criteria=phase_in.exit_criteria,
                notes=phase_in.notes,
            )
        )
    return release


async def serialize_created_release(db: AsyncSession, release: Release) -> dict:
    """Serialize a freshly-committed release into the response shape."""
    stmt = select(Release).where(Release.id == release.id).options(selectinload(Release.phases))
    release = (await db.execute(stmt)).scalar_one()
    data = serialize_model(release)
    data["phases"] = [serialize_model(phase) for phase in release.phases]
    return data


async def get_release_details(db: AsyncSession, release_id: str) -> dict:
    logger.debug("fetching_release_details", release_id=release_id)
    stmt = (
        select(Release)
        .where(Release.id == uuid.UUID(release_id))
        .options(selectinload(Release.phases), selectinload(Release.test_run_links))
    )
    release = (await db.execute(stmt)).scalar_one_or_none()
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    data = serialize_model(release)
    data["phases"] = [serialize_model(phase) for phase in release.phases]

    if release.test_run_links:
        runs_query = text(
            """
            SELECT
                tr.id::text, tr.build_number, tr.status,
                tr.total_tests, tr.passed_tests, tr.failed_tests,
                tr.broken_tests, tr.skipped_tests, tr.pass_rate,
                tr.created_at,
                tr.primary_suite_name, tr.suite_names,
                rtr.phase_id::text AS phase_id
            FROM test_runs tr
            JOIN release_test_run_links rtr ON rtr.test_run_id = tr.id
            WHERE rtr.release_id = :rel_id
            ORDER BY tr.created_at DESC
            """
        )
        runs_rows = (await db.execute(runs_query, {"rel_id": release_id})).fetchall()
        data["linked_runs"] = [dict(row._mapping) for row in runs_rows]
    else:
        data["linked_runs"] = []

    agg_query = text(
        """
        SELECT
            COUNT(tr.id) AS total_runs,
            SUM(tr.total_tests) AS total_tests,
            SUM(tr.passed_tests) AS total_passed,
            -- A failure is FAILED *or* BROKEN. This summed failed_tests
            -- alone, so a release's headline "Failed" count excluded every
            -- infrastructure error while avg_pass_rate -- an average of
            -- per-run pass_rate, which DOES treat BROKEN as a non-pass --
            -- sat beside it counting them. Measured on a ground-truth
            -- release of 3 runs: total_failed read 9 against a truth of 12,
            -- and total(30) - passed(15) - failed(9) left 6 unaccounted for
            -- where only 3 tests were skipped.
            SUM(tr.failed_tests) + SUM(tr.broken_tests) AS total_failed,
            ROUND(AVG(tr.pass_rate)::numeric, 1) AS avg_pass_rate
        FROM test_runs tr
        JOIN release_test_run_links rtr ON rtr.test_run_id = tr.id
        WHERE rtr.release_id = :rel_id
        """
    )
    agg = (await db.execute(agg_query, {"rel_id": release_id})).one()
    metrics = dict(agg._mapping)
    for key, value in metrics.items():
        if hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
            metrics[key] = float(value)
    data["metrics"] = metrics
    return data


async def update_release(db: AsyncSession, release_id: str, body) -> Release:
    """Stage updates to a release. Handler commits; returns the mutated row."""
    release = await get_release_or_404(db, release_id)
    updates = body.model_dump(exclude_none=True)
    new_status = updates.get("status")

    # If transitioning to "released", validate all phases are completed or skipped
    if new_status == "released" and release.status != "released":
        phases = (
            await db.execute(
                select(ReleasePhase).where(ReleasePhase.release_id == release.id)
            )
        ).scalars().all()

        if phases:
            incomplete = [
                p.name for p in phases
                if p.status not in ("completed", "skipped")
            ]
            if incomplete:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot mark as released — {len(incomplete)} phase(s) not completed: {', '.join(incomplete[:5])}",
                )

        # Auto-set released_at if not explicitly provided
        if "released_at" not in updates or not updates["released_at"]:
            updates["released_at"] = datetime.now()

    for field, value in updates.items():
        setattr(release, field, value)
    return release


async def delete_release(db: AsyncSession, release_id: str) -> None:
    """Stage deletion of a release. Handler commits."""
    release = await get_release_or_404(db, release_id)
    await db.delete(release)


async def add_phase(db: AsyncSession, release_id: str, body) -> ReleasePhase:
    """Stage a new phase under an existing release. Handler commits."""
    release = await get_release_or_404(db, release_id)

    # Check for duplicate phase name within the same release
    existing_phases = (
        await db.execute(
            select(ReleasePhase).where(ReleasePhase.release_id == release.id)
        )
    ).scalars().all()

    duplicate = next(
        (p for p in existing_phases if p.name.strip().lower() == body.name.strip().lower()),
        None,
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"Phase '{body.name.strip()}' already exists in this release",
        )

    # Auto-assign order_index if not specified (append at end)
    order = body.order_index if body.order_index else len(existing_phases)

    phase = ReleasePhase(
        release_id=release.id,
        name=body.name.strip(),
        phase_type=body.phase_type,
        status=body.status,
        description=body.description,
        order_index=order,
        planned_start=body.planned_start,
        planned_end=body.planned_end,
        actual_start=body.actual_start,
        actual_end=body.actual_end,
        exit_criteria=body.exit_criteria,
        notes=body.notes,
    )
    db.add(phase)
    await db.flush()  # materialize phase.id for any follow-up queries
    return phase


async def update_phase(db: AsyncSession, release_id: str, phase_id: str, body) -> tuple[ReleasePhase, bool]:
    """Stage updates to a phase. Returns (phase, all_phases_completed).

    The "all phases completed" flag is computed before commit so the handler
    can surface it alongside the response without a second round-trip.
    """
    phase = await get_phase_or_404(db, release_id, phase_id)
    updates = body.model_dump(exclude_none=True)

    # Check for duplicate name if renaming
    if "name" in updates:
        updates["name"] = updates["name"].strip()
        if updates["name"].lower() != phase.name.strip().lower():
            siblings = (
                await db.execute(
                    select(ReleasePhase).where(
                        ReleasePhase.release_id == uuid.UUID(release_id),
                        ReleasePhase.id != uuid.UUID(phase_id),
                    )
                )
            ).scalars().all()
            dup = next((s for s in siblings if s.name.strip().lower() == updates["name"].lower()), None)
            if dup:
                raise HTTPException(status_code=409, detail=f"Phase '{updates['name']}' already exists in this release")

    # Auto-set actual_start when transitioning to in_progress
    if updates.get("status") == "in_progress" and phase.status == "pending":
        if "actual_start" not in updates:
            updates["actual_start"] = datetime.now()

    # Auto-set actual_end when transitioning to completed
    if updates.get("status") == "completed" and phase.status != "completed":
        if "actual_end" not in updates:
            updates["actual_end"] = datetime.now()

    for field, value in updates.items():
        setattr(phase, field, value)

    # Check if all phases of this release are now completed/skipped. Count the
    # NOT-done phases instead of fetching every row and scanning in Python
    # (one aggregate vs an O(N) row fetch). A phase is "done" iff its status is
    # in ("completed", "skipped"); NULL status counts as NOT done, matching the
    # Python ``all(p.status in (...))`` semantics (status is nullable). Zero
    # phases → 0 incomplete → all_done True (``all([]) is True``).
    incomplete = (
        await db.execute(
            select(func.count(ReleasePhase.id)).where(
                ReleasePhase.release_id == uuid.UUID(release_id),
                or_(
                    ReleasePhase.status.notin_(("completed", "skipped")),
                    ReleasePhase.status.is_(None),
                ),
            )
        )
    ).scalar() or 0
    all_done = incomplete == 0
    return phase, all_done


async def delete_phase(db: AsyncSession, release_id: str, phase_id: str) -> None:
    """Stage deletion of a phase. Handler commits."""
    phase = await get_phase_or_404(db, release_id, phase_id)
    await db.delete(phase)


async def link_test_run(db: AsyncSession, release_id: str, body) -> tuple[ReleaseTestRunLink, bool]:
    """Stage a release↔run link. Returns (link, is_new).

    If a matching link already exists we return it with ``is_new=False`` and
    the handler just re-serializes without committing.
    """
    await get_release_or_404(db, release_id)
    run_uuid = uuid.UUID(body.test_run_id)
    run = (await db.execute(select(TestRun).where(TestRun.id == run_uuid))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    existing = (
        await db.execute(
            select(ReleaseTestRunLink).where(
                ReleaseTestRunLink.release_id == uuid.UUID(release_id),
                ReleaseTestRunLink.test_run_id == run_uuid,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing, False

    link = ReleaseTestRunLink(
        release_id=uuid.UUID(release_id),
        test_run_id=run_uuid,
        phase_id=uuid.UUID(body.phase_id) if body.phase_id else None,
    )
    db.add(link)
    await db.flush()
    return link, True


async def unlink_test_run(db: AsyncSession, release_id: str, run_id: str) -> None:
    """Stage deletion of a release↔run link. Handler commits."""
    link = (
        await db.execute(
            select(ReleaseTestRunLink).where(
                ReleaseTestRunLink.release_id == uuid.UUID(release_id),
                ReleaseTestRunLink.test_run_id == uuid.UUID(run_id),
            )
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    await db.delete(link)
