"""Dev-only seed data management endpoints.

Invisible in staging/production (returns 404).
Requires ADMIN role in development.
"""
import asyncio
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_active_user, require_role
from app.db.postgres import get_db
from app.models.postgres import Project, User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dev/seed", tags=["Dev Seed Data"])

SEED_MARKER = "seed_dev_data_v1"

_SEED_SCRIPT = str(
    Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_dev_data.py"
)


def _require_dev() -> None:
    """Raise 404 if not in development environment."""
    if not settings.is_development:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _run_seed_script(*args: str) -> tuple[int, str, str]:
    """Run the seed script as a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, _SEED_SCRIPT, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


@router.get("/status")
async def seed_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Check whether seed data is currently loaded."""
    _require_dev()
    result = await db.execute(
        select(Project).where(Project.description.contains(SEED_MARKER)).limit(1)
    )
    seeded = result.scalar_one_or_none() is not None
    return {"seeded": seeded}


@router.post(
    "",
    status_code=200,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def load_seed_data():
    """Load seed data (idempotent — skips if already seeded)."""
    _require_dev()
    returncode, stdout, stderr = await _run_seed_script()
    if returncode != 0:
        logger.error("Seed script failed: %s", stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Seed script failed: {stderr[:500]}",
        )
    logger.info("Seed data loaded via API")
    return {"status": "ok", "message": "Seed data loaded", "output": stdout[-1000:]}


@router.post(
    "/reset",
    status_code=200,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def reset_seed_data():
    """Wipe existing seed data and regenerate from scratch."""
    _require_dev()
    returncode, stdout, stderr = await _run_seed_script("--reset")
    if returncode != 0:
        logger.error("Seed reset failed: %s", stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Seed reset failed: {stderr[:500]}",
        )
    logger.info("Seed data reset via API")
    return {"status": "ok", "message": "Seed data reset", "output": stdout[-1000:]}


@router.delete(
    "",
    status_code=200,
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
async def delete_seed_data():
    """Wipe seed data without re-seeding."""
    _require_dev()
    returncode, stdout, stderr = await _run_seed_script("--wipe-only")
    if returncode != 0:
        logger.error("Seed wipe failed: %s", stderr)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Seed wipe failed: {stderr[:500]}",
        )
    logger.info("Seed data wiped via API")
    return {"status": "ok", "message": "Seed data deleted", "output": stdout[-1000:]}
