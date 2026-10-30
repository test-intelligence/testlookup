"""Is a pipeline for this run already in progress? (W14 AGT-12 / AGT-07).

Two reads the trigger routes share, so the single trigger, the bulk trigger and
the deep trigger answer "already in progress" from ONE definition of
"in progress" (``workflow_run_state.PUBLIC_STATUS``: pending, running and
retry_wait all project to ``in_progress``).

Commit-free and read-only: nothing here adds, flushes, commits or rolls back.
``routers/agents.py`` keeps the module-level name ``_latest_in_progress_pipeline``
as a one-line wrapper over :func:`latest_in_progress_pipeline` (tests patch that
name).
"""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AgentPipelineRun
from app.services.inline_invocation import not_inline_pipeline
from app.services.workflow_run_state import IN_PROGRESS_STATUSES, PUBLIC_STATUS

# W23c AGT-11 (I-8): an inline-input invocation in flight neither blocks nor satisfies the
# run's own analysis admission -- both reads below exclude it.


async def latest_in_progress_pipeline(
    db: AsyncSession, test_run_id: uuid.UUID, workflow_type: str
) -> Optional[AgentPipelineRun]:
    """The newest non-terminal pipeline for (run, workflow), or None."""
    in_progress = [s.value for s, pub in PUBLIC_STATUS.items() if pub == "in_progress"]
    result = await db.execute(
        select(AgentPipelineRun)
        .where(
            AgentPipelineRun.test_run_id == test_run_id,
            AgentPipelineRun.workflow_type == workflow_type,
            AgentPipelineRun.status.in_(in_progress),
            not_inline_pipeline(),
        )
        .order_by(AgentPipelineRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def runs_with_in_progress_pipeline(
    db: AsyncSession, run_ids: Sequence[uuid.UUID], workflow_type: str
) -> set[uuid.UUID]:
    """The runs among ``run_ids`` with a ``workflow_type`` pipeline in progress.

    ONE statement selecting ONE column, read with ``.scalars().all()`` (a
    scripted session whose ``.scalars()`` is a bare mock reads "none in
    progress"); an empty ``run_ids`` answers ``set()`` without a statement.
    """
    ids = list(dict.fromkeys(run_ids))
    if not ids:
        return set()
    result = await db.execute(
        select(AgentPipelineRun.test_run_id)
        .distinct()
        .where(
            AgentPipelineRun.test_run_id.in_(ids),
            AgentPipelineRun.workflow_type == workflow_type,
            AgentPipelineRun.status.in_(IN_PROGRESS_STATUSES),
            not_inline_pipeline(),
        )
    )
    return {run_id for run_id in result.scalars().all() if run_id is not None}
