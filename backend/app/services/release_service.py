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
from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.postgres import (
    LinkSource,
    Project,
    Release,
    ReleasePhase,
    ReleaseTestRunLink,
    TestRun,
)
from app.models.serializers import serialize_model  # noqa: F401
from app.services import release_lifecycle_service
from app.services.release_linker import sync_primary_release
from app.services.release_sort_key import compute_sort_key

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
        # Computed on write so ordering is an index scan rather than a Python
        # sort over every release in the project (migration 0151).
        sort_key=compute_sort_key(body.version, body.name),
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

    # Take over as the active release only when the incumbent is an
    # auto-created placeholder (migration 0150). That covers onboarding — the
    # project has an ``Unreleased`` row, a user creates "2.4.0" and reasonably
    # expects runs to land there — without ever displacing a release somebody
    # deliberately activated. Pre-planning 2.6.0 while shipping 2.5.0 must not
    # hijack attribution.
    # A release created directly in a terminal status must never take the
    # active flag: nothing would ever rotate it out, because rotation only
    # fires on a transition INTO a terminal status and it is already there.
    # The project would be permanently attributing new runs to something
    # already shipped.
    if (
        body.status not in release_lifecycle_service.TERMINAL_STATUSES
        and await release_lifecycle_service.should_activate_on_create(db, project_uuid)
    ):
        await release_lifecycle_service.activate_release(
            db, release, reason="explicit_create_over_auto_named"
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
        # ``tr.project_id = :proj_id`` is not redundant with the release
        # filter. Nothing at the database level stops a link joining a run and
        # a release in different projects — the service layer rejects new ones
        # and 0151 reports existing ones, but historical rows can violate it.
        # Without this predicate a single bad link would surface another
        # tenant's run in this release's detail view, and from there in the
        # signed compliance pack.
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
              AND tr.project_id = :proj_id
            ORDER BY tr.created_at DESC
            """
        )
        runs_rows = (
            await db.execute(
                runs_query, {"rel_id": release_id, "proj_id": release.project_id}
            )
        ).fetchall()
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
          AND tr.project_id = :proj_id
        """
    )
    agg = (
        await db.execute(
            agg_query, {"rel_id": release_id, "proj_id": release.project_id}
        )
    ).one()
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

    # Renaming a release, or giving it a version for the first time, changes
    # where it sorts. Recompute unconditionally rather than guarding on which
    # fields changed — the guard is the part that rots.
    release.sort_key = compute_sort_key(release.version, release.name)

    # A release that just finished must not keep the active flag, or the next
    # unlabelled run lands in something already shipped. Hand it on rather than
    # blocking the transition: an invariant that makes shipping harder gets
    # routed around, and teams would simply leave releases ``in_progress``.
    if (
        new_status in release_lifecycle_service.TERMINAL_STATUSES
        and release.is_active
    ):
        await release_lifecycle_service.rotate_on_close(
            db, release, reason=new_status
        )
    return release


async def delete_release(db: AsyncSession, release_id: str) -> None:
    """Stage deletion of a release. Handler commits.

    Deleting the active release would leave the project with none, so the flag
    is handed to a successor first. This is the enforcement point the active
    invariant would otherwise lose to a plain DELETE.
    """
    release = await get_release_or_404(db, release_id)
    if release.is_active:
        await release_lifecycle_service.rotate_on_close(
            db, release, reason="deleted"
        )
        # Land the demotion before the row is marked deleted. SQLAlchemy drops
        # pending UPDATEs for deleted objects, so without this the successor's
        # promotion reaches Postgres while this row still holds the flag.
        await db.flush()

    # The release's links CASCADE away with it. Any run whose PRIMARY link was
    # one of them is left attributed to several releases and flagged for none,
    # so every is_primary-scoped read drops it silently — it just stops
    # appearing. Collect those runs first, then repair them after the delete.
    orphaned_runs = (
        await db.execute(
            select(ReleaseTestRunLink.test_run_id).where(
                ReleaseTestRunLink.release_id == release.id,
                ReleaseTestRunLink.is_primary.is_(True),
            )
        )
    ).scalars().all()

    await db.delete(release)
    await db.flush()

    for run_id in orphaned_runs:
        survivor = (
            await db.execute(
                select(ReleaseTestRunLink)
                .where(ReleaseTestRunLink.test_run_id == run_id)
                .order_by(
                    case(
                        (ReleaseTestRunLink.link_source == LinkSource.EXPLICIT_CLIENT.value, 0),
                        (ReleaseTestRunLink.link_source == LinkSource.MANUAL_UI.value, 0),
                        # Rank 0 with the other assertions: GitHub itself
                        # maintains the milestone-to-PR link, so this was read
                        # from a system of record, not derived. Omitting it
                        # would drop it to the ELSE branch and let a pattern
                        # match outrank it when promoting a survivor.
                        (ReleaseTestRunLink.link_source == LinkSource.EXTERNAL_MATCH.value, 0),
                        (ReleaseTestRunLink.link_source == LinkSource.RULE_MATCH.value, 1),
                        (ReleaseTestRunLink.link_source == LinkSource.CUTOFF_WINDOW.value, 1),
                        (ReleaseTestRunLink.link_source == LinkSource.ACTIVE_RELEASE.value, 2),
                        else_=3,
                    ),
                    ReleaseTestRunLink.linked_at.asc(),
                    ReleaseTestRunLink.id.asc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if survivor is not None:
            survivor.is_primary = True
        await db.flush()
        await sync_primary_release(db, run_id)


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
    updates = body.model_dump(exclude_none=True)

    # Checked BEFORE the phase is loaded: an incoherent payload cannot succeed
    # whatever the phase turns out to be, so it needs no database round-trip.
    #
    # Changing what a phase must satisfy and declaring it done are two acts,
    # and doing both in one request makes the second unanswerable: the gate
    # would be evaluated against criteria that were never in force while the
    # work happened. Refused rather than ordered, because there is no ordering
    # of the two that is honest.
    #
    # This is the half of S6b that needs no feature flag. ENFORCEMENT — refusing
    # to complete a phase whose gate does not say GO — is a breaking change to
    # a live endpoint and is deliberately NOT here; `GET
    # /releases/{id}/phases/gate` answers, and nothing yet obliges a caller to
    # ask.
    if "status" in updates and "exit_criteria" in updates:
        raise HTTPException(
            status_code=400,
            detail=(
                "Change a phase's exit criteria and its status in separate "
                "requests — otherwise the status is decided against criteria "
                "that were not in force."
            ),
        )

    phase = await get_phase_or_404(db, release_id, phase_id)

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


async def link_test_run(
    db: AsyncSession,
    release_id: str,
    body,
    linked_by_id: uuid.UUID | None = None,
) -> tuple[ReleaseTestRunLink, bool]:
    """Stage a release↔run link. Returns (link, is_new).

    If a matching link already exists we return it with ``is_new=False`` and
    the handler just re-serializes without committing.
    """
    release = await get_release_or_404(db, release_id)
    run_uuid = uuid.UUID(body.test_run_id)
    run = (await db.execute(select(TestRun).where(TestRun.id == run_uuid))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Test run not found")

    # A link may only join a run and a release in the SAME project. Nothing
    # enforced this before, so a caller who is QA_LEAD on project A and knows a
    # run UUID from project B could pull B's results into A's release — and
    # from there into A's release scorecard and its signed compliance pack.
    # 404 rather than 403: a caller with no access to that run should not learn
    # whether the id exists.
    if run.project_id != release.project_id:
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

    # A human deliberately choosing a release outranks anything the system
    # inferred, so this link takes over as primary — demoting the automatic one
    # rather than sitting beside it non-deterministically, which is what made
    # the run list's release badge unstable before migration 0151.
    await db.execute(
        update(ReleaseTestRunLink)
        .where(
            ReleaseTestRunLink.test_run_id == run_uuid,
            ReleaseTestRunLink.is_primary.is_(True),
        )
        .values(is_primary=False)
    )

    link = ReleaseTestRunLink(
        release_id=uuid.UUID(release_id),
        test_run_id=run_uuid,
        phase_id=uuid.UUID(body.phase_id) if body.phase_id else None,
        # A human chose this. It is the strongest provenance there is, and the
        # scorecard counts it as asserted rather than inferred.
        link_source=LinkSource.MANUAL_UI.value,
        is_primary=True,
        project_id=release.project_id,
        linked_by_id=linked_by_id,
    )
    db.add(link)
    await db.flush()
    # Third of the four paths that change which link is primary — see
    # release_linker.sync_primary_release for why there is no single writer.
    await sync_primary_release(db, run_uuid)
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

    # Removing the primary link would leave the run attributed to several
    # releases but flagged for none, so every analytics read scoped by
    # ``is_primary`` would drop it silently — it would simply stop appearing,
    # with no error anywhere. Promote a survivor first, by the same evidence
    # ranking migration 0151's backfill uses.
    survivor = None
    if link.is_primary:
        survivor = (
            await db.execute(
                select(ReleaseTestRunLink)
                .where(
                    ReleaseTestRunLink.test_run_id == link.test_run_id,
                    ReleaseTestRunLink.id != link.id,
                )
                .order_by(
                    case(
                        (ReleaseTestRunLink.link_source == LinkSource.EXPLICIT_CLIENT.value, 0),
                        (ReleaseTestRunLink.link_source == LinkSource.MANUAL_UI.value, 0),
                        # Rank 0 with the other assertions: GitHub itself
                        # maintains the milestone-to-PR link, so this was read
                        # from a system of record, not derived. Omitting it
                        # would drop it to the ELSE branch and let a pattern
                        # match outrank it when promoting a survivor.
                        (ReleaseTestRunLink.link_source == LinkSource.EXTERNAL_MATCH.value, 0),
                        (ReleaseTestRunLink.link_source == LinkSource.RULE_MATCH.value, 1),
                        (ReleaseTestRunLink.link_source == LinkSource.CUTOFF_WINDOW.value, 1),
                        (ReleaseTestRunLink.link_source == LinkSource.ACTIVE_RELEASE.value, 2),
                        else_=3,
                    ),
                    ReleaseTestRunLink.linked_at.asc(),
                    ReleaseTestRunLink.id.asc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    run_id_for_sync = link.test_run_id
    was_primary = link.is_primary

    # Delete FIRST, then flush, then promote. Assigning ``is_primary = False``
    # to a row that is about to be deleted does nothing: SQLAlchemy never
    # emits an UPDATE for an object in the deleted set, and within one mapper
    # it runs every save/update before every delete. So the promote statement
    # would reach Postgres while the outgoing row still held the flag, and
    # ``ix_rtr_links_primary`` would reject it — deterministically, every time
    # the primary link of a multi-linked run is removed. The endpoint 500s,
    # the DELETE never runs, and the run keeps both the link and the flag.
    await db.delete(link)
    await db.flush()

    if was_primary and survivor is not None:
        survivor.is_primary = True
        await db.flush()
    # The promoted survivor (or None, if that was the run's last link) has to
    # reach the denormalized column, or analytics keeps answering from a
    # release the run is no longer linked to.
    await sync_primary_release(db, run_id_for_sync)
