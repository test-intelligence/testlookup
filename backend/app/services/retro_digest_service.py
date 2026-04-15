"""
Weekly auto-retro digest — Tier 2 item 12.

Extends the existing digest pipeline with a new ``WEEKLY_RETRO``
schedule that generates a per-team "week in review" summary. The
renderer composes:

  * top 5 failures from the week (count × severity)
  * recovered flaky tests (quarantine RELEASED since last week)
  * new regressions vs. the previous week's baseline
  * an AI-written paragraph summarizing the week's quality story

The paragraph uses the existing LLM factory so it respects
``AI_OFFLINE_MODE`` — air-gapped deployments get a template-based
fallback with the raw numbers instead of a narrative.

Gated behind the ``weekly_retro_digest`` feature flag. When off, the
``generate_weekly_retro`` function returns ``None`` so the existing
``dispatch_scheduled_digests`` task skips ``WEEKLY_RETRO`` subscriptions
without any wiring changes.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    FlakyQuarantineRequest,
    Project,
    TestCase,
    TestRun,
    TestStatus,
)
from app.services.digest_content_service import generate_digest

logger = logging.getLogger("services.retro_digest")


async def _feature_enabled(db: Optional[AsyncSession] = None) -> bool:
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("weekly_retro_digest", db=db)
    except Exception:
        return False


async def _count_released_flaky(
    db: AsyncSession, project_id: uuid.UUID, since: datetime,
) -> int:
    """How many flaky tests were released from quarantine this week?"""
    result = await db.execute(
        select(func.count(FlakyQuarantineRequest.id)).where(
            FlakyQuarantineRequest.project_id == project_id,
            FlakyQuarantineRequest.status == "RELEASED",
            FlakyQuarantineRequest.updated_at >= since,
        )
    )
    return int(result.scalar() or 0)


async def _count_new_regressions(
    db: AsyncSession, project_id: uuid.UUID, since: datetime,
) -> int:
    """Count test fingerprints that passed in the previous week but
    failed this week — i.e. new regressions."""
    prev_start = since - timedelta(days=7)

    # Fingerprints that passed in the prior week.
    prev_passed = await db.execute(
        select(TestCase.test_fingerprint)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(
            TestRun.project_id == project_id,
            TestCase.created_at >= prev_start,
            TestCase.created_at < since,
            TestCase.status == TestStatus.PASSED.value,
        )
        .distinct()
    )
    prev_passed_fps = {row[0] for row in prev_passed.all() if row[0]}
    if not prev_passed_fps:
        return 0

    # Among the same fingerprints, how many failed this week?
    curr_failed = await db.execute(
        select(TestCase.test_fingerprint)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(
            TestRun.project_id == project_id,
            TestCase.created_at >= since,
            TestCase.status.in_(
                (TestStatus.FAILED.value, TestStatus.BROKEN.value)
            ),
            TestCase.test_fingerprint.in_(prev_passed_fps),
        )
        .distinct()
    )
    return len({row[0] for row in curr_failed.all() if row[0]})


async def _compose_narrative(
    project_name: str,
    digest: dict[str, Any],
    released_flaky: int,
    new_regressions: int,
) -> str:
    """Write the AI paragraph. Falls back to a template in offline mode.

    Only a handful of numbers are fed to the model so the prompt is
    small and the narrative stays grounded. We don't ask for creativity
    here — the goal is a skimmable human sentence for busy QA leads.
    """
    numbers = {
        "pass_rate_pct": digest.get("pass_rate"),
        "runs_total": digest.get("runs_total"),
        "released_flaky": released_flaky,
        "new_regressions": new_regressions,
        "top_clusters": len(digest.get("top_clusters") or []),
    }

    from app.core.config import settings
    if settings.AI_OFFLINE_MODE:
        return (
            f"**{project_name}** — week in review: "
            f"{numbers['runs_total']} runs · pass rate {numbers['pass_rate_pct']}%. "
            f"{numbers['new_regressions']} new regression(s) vs. last week. "
            f"{numbers['released_flaky']} previously-quarantined test(s) recovered. "
            f"{numbers['top_clusters']} active failure cluster(s) still open."
        )

    prompt = (
        "Write a single short paragraph (2-3 sentences) summarising a QA "
        "week for an engineering manager. Numbers you must stick to:\n\n"
        f"- Project: {project_name}\n"
        f"- Total runs: {numbers['runs_total']}\n"
        f"- Pass rate: {numbers['pass_rate_pct']}%\n"
        f"- New regressions vs. last week: {numbers['new_regressions']}\n"
        f"- Flaky tests recovered from quarantine: {numbers['released_flaky']}\n"
        f"- Active failure clusters: {numbers['top_clusters']}\n\n"
        "Be concrete. No marketing language. No bullet points."
    )

    try:
        from app.services.llm_factory import get_llm
        llm = await get_llm()
        response = await llm.ainvoke(prompt)
        text = getattr(response, "content", None) or str(response)
        return str(text).strip()[:1000]
    except Exception as exc:
        logger.warning("retro narrative LLM failed", error=str(exc))
        return (
            f"Week in review: {numbers['runs_total']} runs, "
            f"{numbers['pass_rate_pct']}% pass rate, "
            f"{numbers['new_regressions']} new regressions, "
            f"{numbers['released_flaky']} recovered flaky tests."
        )


async def generate_weekly_retro(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[dict[str, Any]]:
    """Produce the full retro document for a project.

    Returns ``None`` when the ``weekly_retro_digest`` feature flag is
    off so the existing digest dispatcher can skip this subscription
    type cleanly. Returns a dict ready for
    ``digest_content_service.render_digest_html`` when enabled.
    """
    if not await _feature_enabled(db):
        return None

    # Start with the existing weekly digest — we extend it rather
    # than re-query from scratch so numbers stay consistent with the
    # plain-weekly delivery.
    digest = await generate_digest(db, project_id=project_id, period="weekly")

    project_name_result = await db.execute(
        select(Project.name).where(Project.id == project_id)
    )
    project_name = project_name_result.scalar_one_or_none() or "Project"

    since = datetime.now(timezone.utc) - timedelta(days=7)
    released_flaky = await _count_released_flaky(db, project_id, since)
    new_regressions = await _count_new_regressions(db, project_id, since)

    narrative = await _compose_narrative(
        project_name=project_name,
        digest=digest,
        released_flaky=released_flaky,
        new_regressions=new_regressions,
    )

    # Wrap the existing digest with retro-specific sections. The
    # ``schedule_type`` discriminator lets the email template pick
    # a retro-themed header + subject line.
    return {
        **digest,
        "schedule_type": "WEEKLY_RETRO",
        "project_name": project_name,
        "retro_narrative": narrative,
        "released_flaky": released_flaky,
        "new_regressions": new_regressions,
        "subject_line": f"Week in review — {project_name}",
    }
