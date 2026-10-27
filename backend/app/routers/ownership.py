"""Service Ownership router — CRUD, bulk import/export, resolver (ENT-04)."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import (
    Project,
    ServiceOwnershipRule,
    TeamNotificationChannel,
    User,
    UserRole,
)
from app.models.schemas import (
    CodeownersCoverage,
    CodeownersImportRequest,
    CodeownersImportResponse,
    OwnershipBulkImportRequest,
    OwnershipResolution,
    OwnershipRuleCreate,
    OwnershipRuleResponse,
    OwnershipRuleUpdate,
    SourceRoot,
    SourceRootsPreview,
    SourceRootsResponse,
    SourceRootsUpdate,
    TeamChannelResponse,
    TeamChannelUpsert,
)
from app.services.activity.service import ActorRef, record as record_activity

logger = logging.getLogger("routers.ownership")

router = APIRouter(prefix="/api/v1/projects/{project_id}/ownership", tags=["Service Ownership"])


@router.get("/rules", response_model=list[OwnershipRuleResponse])
async def list_ownership_rules(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """List all ownership rules for a project."""
    result = await db.execute(
        select(ServiceOwnershipRule)
        .where(ServiceOwnershipRule.project_id == project_id)
        .order_by(ServiceOwnershipRule.priority.desc(), ServiceOwnershipRule.match_type)
    )
    return result.scalars().all()


def _rule_label(rule: ServiceOwnershipRule) -> str:
    return f"{rule.match_pattern} → {rule.team_name}"


@router.post("/rules", response_model=OwnershipRuleResponse, status_code=201)
async def create_ownership_rule(
    project_id: uuid.UUID,
    payload: OwnershipRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Create a new ownership rule (QA_LEAD+)."""
    rule = ServiceOwnershipRule(
        project_id=project_id,
        match_type=payload.match_type,
        match_pattern=payload.match_pattern,
        service_name=payload.service_name,
        team_name=payload.team_name,
        team_contact=payload.team_contact,
        priority=payload.priority,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    # W21 ACT-01: outcome rows staged on the route's session for every
    # ownership mutation below; the route's one commit carries them.
    await record_activity(
        db, project_id=project_id, event_type="ownership_rule.created", actor=ActorRef.from_user(current_user),
        entity_id=rule.id, entity_label=_rule_label(rule),
        after={"match_pattern": rule.match_pattern, "team_name": rule.team_name},
    )
    await db.commit()
    await db.refresh(rule)
    logger.info("Ownership rule created: %s → %s by %s", payload.match_pattern, payload.team_name, current_user.username)
    return rule


@router.patch("/rules/{rule_id}", response_model=OwnershipRuleResponse)
async def update_ownership_rule(
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    payload: OwnershipRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Update an ownership rule (QA_LEAD+)."""
    result = await db.execute(
        select(ServiceOwnershipRule).where(
            ServiceOwnershipRule.id == rule_id,
            ServiceOwnershipRule.project_id == project_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    updates = payload.model_dump(exclude_unset=True)
    changed = [field for field, value in updates.items() if getattr(rule, field) != value]
    before = {field: getattr(rule, field) for field in changed}
    for field, value in updates.items():
        setattr(rule, field, value)
    if changed:
        await record_activity(
            db, project_id=project_id, event_type="ownership_rule.updated", actor=ActorRef.from_user(current_user),
            entity_id=rule.id, entity_label=_rule_label(rule), context={"changed": ", ".join(changed)},
            before=before, after={field: getattr(rule, field) for field in changed}, changed_fields=changed,
        )

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_ownership_rule(
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Delete an ownership rule (QA_LEAD+)."""
    result = await db.execute(
        select(ServiceOwnershipRule).where(
            ServiceOwnershipRule.id == rule_id,
            ServiceOwnershipRule.project_id == project_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    await record_activity(
        db, project_id=project_id, event_type="ownership_rule.deleted", actor=ActorRef.from_user(current_user),
        entity_id=rule.id, entity_label=_rule_label(rule),
    )
    await db.delete(rule)
    await db.commit()
    return None


@router.post("/rules/bulk-import", response_model=list[OwnershipRuleResponse], status_code=201)
async def bulk_import_rules(
    project_id: uuid.UUID,
    payload: OwnershipBulkImportRequest,
    db: AsyncSession = Depends(get_db),
    # N20: require_project_access() confines a project-bound key to {project_id}.
    current_user: User = Depends(require_role(UserRole.ADMIN, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Bulk import ownership rules (ADMIN only). Optionally replaces existing."""
    if payload.replace_existing:
        existing = await db.execute(
            select(ServiceOwnershipRule).where(ServiceOwnershipRule.project_id == project_id)
        )
        for old_rule in existing.scalars().all():
            await db.delete(old_rule)

    created = []
    for item in payload.rules:
        rule = ServiceOwnershipRule(
            project_id=project_id,
            match_type=item.match_type,
            match_pattern=item.match_pattern,
            service_name=item.service_name,
            team_name=item.team_name,
            team_contact=item.team_contact,
            priority=item.priority,
            created_by=current_user.id,
        )
        db.add(rule)
        created.append(rule)

    # One row for the whole import (not one per rule), so a large import
    # does not flood the feed. The entity is the project's rule set.
    await record_activity(
        db, project_id=project_id, event_type="ownership_rules.bulk_imported",
        actor=ActorRef.from_user(current_user), entity_id=project_id, entity_label="Ownership rules",
        context={"count": len(created), "replace_existing": bool(payload.replace_existing)},
    )
    await db.commit()
    for rule in created:
        await db.refresh(rule)

    logger.info("Bulk imported %d ownership rules for project %s by %s", len(created), project_id, current_user.username)
    return created


@router.get("/rules/export", response_model=list[OwnershipRuleResponse])
async def export_ownership_rules(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Export all ownership rules for a project (same as list, for import/export workflows)."""
    result = await db.execute(
        select(ServiceOwnershipRule)
        .where(ServiceOwnershipRule.project_id == project_id)
        .order_by(ServiceOwnershipRule.priority.desc())
    )
    return result.scalars().all()


# ── CODEOWNERS import (PMF US-8.3) ────────────────────────────────────────────


@router.post("/codeowners/import", response_model=CodeownersImportResponse, status_code=201)
async def import_codeowners(
    project_id: uuid.UUID,
    payload: CodeownersImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Import a GitHub CODEOWNERS file into ``path`` ownership rules (QA_LEAD+).

    Two acquisition paths:
      * ``source="text"`` — the raw CODEOWNERS body is in ``payload.text``
        (air-gapped / paste-upload). No egress.
      * ``source="github"`` — fetched over the configured GitHub connector.
        422s (with the connector's reason) when offline / flag-off / not
        configured / file absent, so the caller can surface why.

    Idempotent: replaces the project's prior CODEOWNERS-sourced rules only,
    leaving hand-authored rules untouched. This router owns the commit.
    """
    from app.services import codeowners_service

    if payload.source == "text":
        text = payload.text or ""
        if not text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="source='text' requires a non-empty CODEOWNERS body",
            )
    else:  # github
        text, detail = await codeowners_service.fetch_codeowners_text(
            db, project_id, user=current_user,
        )
        if text is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not fetch CODEOWNERS from GitHub ({detail})",
            )

    try:
        summary = await codeowners_service.import_codeowners_rules(
            db, project_id=project_id, text=text, actor_id=current_user.id,
        )
    except ValueError as exc:
        # e.g. the file parses to more than MAX_IMPORT_ENTRIES entries.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    # Coverage is computed after the flush so it sees the freshly imported rows.
    coverage = await codeowners_service.compute_coverage(db, project_id)
    await record_activity(
        db, project_id=project_id, event_type="codeowners.imported", actor=ActorRef.from_user(current_user),
        entity_id=project_id, entity_label="CODEOWNERS",
        context={"count": summary["rules_created"], "source": payload.source},
    )
    await db.commit()

    logger.info(
        "CODEOWNERS imported for project %s: %d rules (source=%s) by %s",
        project_id, summary["rules_created"], payload.source, current_user.username,
    )
    return CodeownersImportResponse(
        imported=summary["imported"],
        rules_created=summary["rules_created"],
        rules_replaced=summary["rules_replaced"],
        source=payload.source,
        coverage=CodeownersCoverage(**coverage),
    )


@router.get("/codeowners/coverage", response_model=CodeownersCoverage)
async def codeowners_coverage(
    project_id: uuid.UUID,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Percentage of recent failing-test paths matched by a ``path`` rule.

    Bounded lookback (``days``, clamped 1-365) over a capped sample of recent
    FAILED/BROKEN test cases. ``coverage_pct`` is over the *locatable* failures.
    """
    from app.services import codeowners_service

    lookback = max(1, min(int(days), 365))
    coverage = await codeowners_service.compute_coverage(
        db, project_id, lookback_days=lookback,
    )
    return CodeownersCoverage(**coverage)


# ── Java source roots (FAIL-08) ──────────────────────────────────────────────
#
# A JVM frame names a class and a file, not a path. A QA lead lists where the
# project's test sources live (``{prefix, root}`` in priority order) and every
# locator — the coverage badge, the ingest-time path-owner assignment, the
# inbox "via CODEOWNERS" reason and the check-run annotations — places Java
# frames through them. No default root: none listed = Java unlocated.


def _source_roots_422(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc),
    )


@router.get("/source-roots", response_model=SourceRootsResponse)
async def get_source_roots(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """The project's Java source roots, in priority order (any member)."""
    from app.services import codeowners_service

    roots = await codeowners_service.load_source_roots(db, project_id)
    return SourceRootsResponse(
        roots=[SourceRoot(**r) for r in roots], max_roots=codeowners_service.MAX_SOURCE_ROOTS,
    )


@router.put("/source-roots", response_model=SourceRootsResponse)
async def put_source_roots(
    project_id: uuid.UUID,
    payload: SourceRootsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Replace the project's Java source roots (QA_LEAD+).

    The whole list is sent and stored (stripped, a trailing ``/`` removed);
    an empty list removes every root and restores the pre-FAIL-08 behaviour.
    A ``project.updated`` ledger row is staged only when the stored list
    actually changes, and this router owns the single commit."""
    from app.services import codeowners_service

    try:
        roots = codeowners_service.validate_source_roots(
            [r.model_dump() for r in payload.roots]
        )
    except codeowners_service.SourceRootsError as exc:
        raise _source_roots_422(exc) from exc

    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    stored = project.source_roots
    before_effective = codeowners_service.sanitize_stored_source_roots(stored)
    if (stored or []) != roots:
        project.source_roots = roots or None
        await record_activity(
            db,
            project_id=project.id,
            event_type="project.updated",
            actor=ActorRef.from_user(current_user),
            entity_id=project.id,
            entity_label=project.name,
            before={"source_roots": len(before_effective)},
            after={"source_roots": len(roots)},
            # Named explicitly: swapping two roots for two others leaves the
            # counts equal, and a diff computed from them would say nothing
            # changed beside a summary that says something did.
            changed_fields=["source_roots"],
            context={"changed": "Java source roots"},
        )
        await db.commit()
        logger.info(
            "Java source roots saved for project %s: %d root(s)", project_id, len(roots),
        )
    return SourceRootsResponse(
        roots=[SourceRoot(**r) for r in roots], max_roots=codeowners_service.MAX_SOURCE_ROOTS,
    )


# activity: none -- a read-only dry run: resolves the proposed roots against recent failures and writes nothing.
@router.post("/source-roots/preview", response_model=SourceRootsPreview)
async def preview_source_roots(
    project_id: uuid.UUID,
    payload: SourceRootsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """What an EDITED list would locate over the project's recent failures
    (the coverage sample: newest 500 FAILED/BROKEN rows of 30 days). Writes
    nothing; the same 422 texts as the PUT."""
    from app.services import codeowners_service

    try:
        roots = codeowners_service.validate_source_roots(
            [r.model_dump() for r in payload.roots]
        )
    except codeowners_service.SourceRootsError as exc:
        raise _source_roots_422(exc) from exc
    preview = await codeowners_service.preview_source_roots(db, project_id, roots)
    return SourceRootsPreview(**preview)


# ── Team notification channels (PMF US-7.3) ──────────────────────────────────


@router.get("/team-channels", response_model=list[TeamChannelResponse])
async def list_team_channels(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """List the team → notification-channel mappings for a project.

    Teams are the free-text ``team_name`` values used by the ownership
    rules; a mapping here makes transition notifications for tests owned
    by that team route directly to the team's channel.
    """
    result = await db.execute(
        select(TeamNotificationChannel)
        .where(TeamNotificationChannel.project_id == project_id)
        .order_by(TeamNotificationChannel.team_name)
    )
    return result.scalars().all()


@router.put("/team-channels/{team_name}", response_model=TeamChannelResponse)
async def upsert_team_channel(
    project_id: uuid.UUID,
    team_name: str,
    payload: TeamChannelUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Create or replace the notification channel for one team (QA_LEAD+)."""
    team = team_name.strip()
    if not team or len(team) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="team_name must be 1-255 characters",
        )
    result = await db.execute(
        select(TeamNotificationChannel).where(
            TeamNotificationChannel.project_id == project_id,
            TeamNotificationChannel.team_name == team,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = TeamNotificationChannel(project_id=project_id, team_name=team)
        db.add(row)
    row.channel_type = payload.channel_type
    row.target = payload.target
    row.is_active = payload.is_active
    await db.flush()
    # The target (a webhook URL or address) is not copied into the ledger;
    # the channel type is enough to read the change.
    await record_activity(
        db, project_id=project_id, event_type="team_channel.set", actor=ActorRef.from_user(current_user),
        entity_id=row.id, entity_label=team,
        context={"channel_type": payload.channel_type, "is_active": payload.is_active},
    )
    await db.commit()
    await db.refresh(row)
    logger.info(
        "Team channel upserted: %s → %s (%s) by %s",
        team, payload.channel_type, project_id, current_user.username,
    )
    return row


@router.delete("/team-channels/{team_name}", status_code=204)
async def delete_team_channel(
    project_id: uuid.UUID,
    team_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD, allow_project_key=True)),
    _: User = Depends(require_project_access()),
):
    """Remove a team's notification channel (QA_LEAD+). Transition events
    for that team's tests fall back to the project default channels."""
    result = await db.execute(
        select(TeamNotificationChannel).where(
            TeamNotificationChannel.project_id == project_id,
            TeamNotificationChannel.team_name == team_name.strip(),
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team channel not found")
    await record_activity(
        db, project_id=project_id, event_type="team_channel.removed", actor=ActorRef.from_user(current_user),
        entity_id=row.id, entity_label=row.team_name,
    )
    await db.delete(row)
    await db.commit()
    return None


@router.get("/resolve/{cluster_id}", response_model=OwnershipResolution)
async def resolve_cluster_ownership_endpoint(
    project_id: uuid.UUID,
    cluster_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_access()),
):
    """Resolve the probable owner for a specific failure cluster."""
    from app.models.postgres import FailureCluster
    from app.services.ownership_resolver_service import resolve_cluster_ownership

    # Find the cluster
    result = await db.execute(
        select(FailureCluster).where(
            FailureCluster.cluster_id == cluster_id,
        ).limit(1)
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cluster not found")

    resolution = await resolve_cluster_ownership(
        db=db,
        project_id=project_id,
        member_test_ids=cluster.member_test_ids or [],
    )
    return OwnershipResolution(**resolution.to_dict())
