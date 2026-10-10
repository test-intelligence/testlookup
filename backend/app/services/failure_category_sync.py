"""Keep ``test_cases.failure_category`` in step with the analysis of that test.

Two columns hold a failure's category: ``ai_analysis.failure_category`` (what
the analysis concluded) and ``test_cases.failure_category`` (what Failure
Analysis, the trend and chart rows, digests, failure groups and My Failures
read). ``POST /analyze`` wrote both; the pipeline's batch upsert wrote only the
first. So every run analysed by the pipeline showed its categories on Run
Intelligence and "100% Unknown" everywhere else -- the homelab had 69 such
analyses and none on its test cases (E2E 2026-10-10).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, update

from app.models.postgres import FailureCategory, TestCase

_VALID = {c.value for c in FailureCategory}


def _category_value(category: Any) -> str | None:
    value = getattr(category, "value", category)
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if value in _VALID else None


async def mirror_ai_category(db, test_case_id, category: Any) -> None:
    """Copy an analysis's category onto its test case when it has none.

    Never overrides a label someone set (a correction, the bulk Classify): a
    re-run pipeline must not undo a human decision. UNKNOWN is not a category
    worth copying -- it is what an unset one already reads as.
    """
    value = _category_value(category)
    if value is None or value == FailureCategory.UNKNOWN.value:
        return
    await db.execute(
        update(TestCase)
        .where(
            TestCase.id == test_case_id,
            or_(
                TestCase.failure_category.is_(None),
                TestCase.failure_category == FailureCategory.UNKNOWN.value,
            ),
        )
        .values(failure_category=value)
    )


async def apply_human_category(db, test_case_id, category: Any) -> None:
    """A person's correction is the category, whatever was there before."""
    value = _category_value(category)
    if value is None:
        return
    await db.execute(
        update(TestCase).where(TestCase.id == test_case_id).values(failure_category=value)
    )
