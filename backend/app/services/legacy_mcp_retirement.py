"""Retire the deployment-managed process-wide MCP account during upgrades."""

from __future__ import annotations

from sqlalchemy import delete, select

from app.models.postgres import ProjectMember, User
from app.services.refresh_token_service import _revoke_family


async def stage_legacy_account_retirement(db, username: str):  # noqa: ANN001, ANN201
    """Stage retirement for exactly one named machine account."""
    account = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if account is None:
        return "absent", None
    if not account.is_service_account:
        raise RuntimeError(
            f"refusing to retire {username!r}: account is not marked as a service account"
        )

    account.is_active = False
    await db.execute(delete(ProjectMember).where(ProjectMember.user_id == account.id))
    await _revoke_family(db, account.id, reason="legacy_mcp_retired")
    return "retired", account.id
