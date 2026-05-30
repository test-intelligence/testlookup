"""
Auto-Tagging Service — applies system tags to test cases and runs.

Called after ingestion (outcome tags) and after AI pipeline completion
(signal tags like flaky, regression, duplicate).

Usage:
    from app.services.auto_tagging_service import auto_tag_test_cases, auto_tag_test_run, auto_tag_after_analysis
"""
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIAnalysis, TestCase, TestRun
from app.services.tag_utils import merge_tags

logger = structlog.get_logger("services.auto_tagging")


async def auto_tag_test_cases(db: AsyncSession, run_id: uuid.UUID) -> int:
    """Apply outcome-based system tags to all test cases in a run.

    Tags applied: passed, failed, skipped, broken (based on TestCase.status).
    Preserves existing ingested/custom tags.

    Returns the number of test cases tagged.
    """
    result = await db.execute(
        select(TestCase).where(TestCase.test_run_id == run_id)
    )
    cases = result.scalars().all()
    count = 0

    for tc in cases:
        status_lower = (tc.status or "").lower()
        system_tag = status_lower if status_lower in ("passed", "failed", "skipped", "broken") else None
        if not system_tag:
            continue

        existing = tc.tags or []
        if system_tag not in existing:
            tc.tags = merge_tags(existing, [system_tag])
            count += 1

    if count > 0:
        await db.flush()
    logger.info("auto_tag_test_cases", run_id=str(run_id), tagged=count, total=len(cases))
    return count


async def auto_tag_test_run(db: AsyncSession, run_id: uuid.UUID) -> list[str]:
    """Apply summary-level system tags to a test run.

    Tags applied based on run aggregates:
    - all_passed: pass_rate == 100
    - has_failures: failed_tests > 0
    - has_skips: skipped_tests > 0

    Returns the list of tags applied.
    """
    result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        return []

    new_tags: list[str] = []
    pass_rate = run.pass_rate or 0

    if pass_rate == 100 and (run.total_tests or 0) > 0:
        new_tags.append("all_passed")
    if (run.failed_tests or 0) > 0:
        new_tags.append("has_failures")
    if (run.skipped_tests or 0) > 0:
        new_tags.append("has_skips")

    if new_tags:
        run.tags = merge_tags(run.tags, new_tags)
        await db.flush()

    logger.info("auto_tag_test_run", run_id=str(run_id), tags=new_tags)
    return new_tags


async def auto_tag_after_analysis(db: AsyncSession, run_id: uuid.UUID) -> dict[str, int]:
    """Apply AI-derived signal tags after pipeline analysis completes.

    Test-case-level tags:
    - flaky: test case marked as flaky by AI analysis
    - regression: identified as regression by release gate or diff
    - duplicate: part of a duplicate failure cluster

    Run-level tags:
    - flaky_content: if any test case is flaky
    - regression_detected: if any test case is regression

    Returns counts of tags applied per category.
    """
    counts = {"flaky": 0, "regression": 0, "duplicate": 0, "run_tags": 0}

    # Load run + AI analyses in minimal queries (avoid N+1)
    run = (await db.execute(select(TestRun).where(TestRun.id == run_id))).scalar_one_or_none()
    if not run:
        return counts

    result = await db.execute(
        select(AIAnalysis, TestCase)
        .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
        .where(TestCase.test_run_id == run_id)
    )
    rows = result.all()

    has_flaky = False
    has_regression = False

    for analysis, tc in rows:
        tags_to_add: list[str] = []

        # Flaky detection
        if analysis.is_flaky:
            tags_to_add.append("flaky")
            has_flaky = True
            counts["flaky"] += 1

        # Failure category hints
        category = str(analysis.failure_category or "").upper()
        if category == "PRODUCT_BUG":
            # Check if this might be a regression (new failure)
            existing_tags = tc.tags or []
            if "regression" not in existing_tags:
                tags_to_add.append("regression")
                has_regression = True
                counts["regression"] += 1

        if tags_to_add:
            tc.tags = merge_tags(tc.tags, tags_to_add)

    # Run-level signal tags (run already fetched above)
    if run:
        run_tags: list[str] = []
        if has_flaky:
            run_tags.append("flaky_content")
        if has_regression:
            run_tags.append("regression_detected")
        if run_tags:
            run.tags = merge_tags(run.tags, run_tags)
            counts["run_tags"] = len(run_tags)

    if any(v > 0 for v in counts.values()):
        await db.flush()

    logger.info("auto_tag_after_analysis", run_id=str(run_id), counts=counts)
    return counts
