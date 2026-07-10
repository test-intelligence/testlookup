"""Service Ownership router — CRUD, bulk import/export, resolver (ENT-04)."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_project_access, require_role
from app.db.postgres import get_db
from app.models.postgres import (
    ServiceOwnershipRule,
    TeamNotificationChannel,
    User,
    UserRole,
)
from app.models.schemas import (
    OwnershipBulkImportRequest,
    OwnershipResolution,
    OwnershipRuleCreate,
    OwnershipRuleResponse,
    OwnershipRuleUpdate,
    TeamChannelResponse,
    TeamChannelUpsert,
)

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


@router.post("/rules", response_model=OwnershipRuleResponse, status_code=201)
async def create_ownership_rule(
    project_id: uuid.UUID,
    payload: OwnershipRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_ownership_rule(
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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

    await db.delete(rule)
    await db.commit()
    return None


@router.post("/rules/bulk-import", response_model=list[OwnershipRuleResponse], status_code=201)
async def bulk_import_rules(
    project_id: uuid.UUID,
    payload: OwnershipBulkImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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
    current_user: User = Depends(require_role(UserRole.QA_LEAD)),
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
