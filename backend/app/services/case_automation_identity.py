"""An authored test case's automation identity (W13 TCL-03 / TCL-05).

The rule ``/cases`` already uses for "the automated test of this case": the
fingerprint of the canonical test case PROMOTED from it (the explicit link,
``canonical_test_cases.managed_test_case_id``) when one exists in the case's own
project, else the case's own ``test_fingerprint``. Fingerprints are not
project-salted, so every reader of this identity also scopes its own rows to
the case's project.

Commit-free and read-only: nothing here adds, flushes or commits.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import String, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import CanonicalTestCase, ManagedTestCase


def _linked_fingerprint_subquery() -> Any:
    """The promoted canonical's fingerprint, correlated to ``managed_test_cases``.

    ``uq_ctc_managed_test_case_id`` allows one canonical per authored case; the
    ``LIMIT 1`` only keeps the scalar subquery scalar should that ever change.
    """
    return (
        select(CanonicalTestCase.test_fingerprint)
        .where(
            CanonicalTestCase.managed_test_case_id == ManagedTestCase.id,
            CanonicalTestCase.project_id == ManagedTestCase.project_id,
        )
        .limit(1)
        .correlate(ManagedTestCase)
        .scalar_subquery()
    )


def automation_fingerprint_expr() -> Any:
    """SQL: the automation fingerprint of each ``managed_test_cases`` row.

    ``coalesce((SELECT ctc.test_fingerprint FROM canonical_test_cases ctc
    WHERE ctc.managed_test_case_id = managed_test_cases.id AND ctc.project_id =
    managed_test_cases.project_id LIMIT 1), managed_test_cases.test_fingerprint)``
    """
    return func.coalesce(_linked_fingerprint_subquery(), ManagedTestCase.test_fingerprint)


async def automation_fingerprint(db: AsyncSession, case: ManagedTestCase) -> Optional[str]:
    """The same rule for one case, in ONE statement; ``None`` = no identity."""
    linked = (
        select(CanonicalTestCase.test_fingerprint)
        .where(
            CanonicalTestCase.managed_test_case_id == case.id,
            CanonicalTestCase.project_id == case.project_id,
        )
        .limit(1)
        .scalar_subquery()
    )
    own = (case.test_fingerprint or "").strip() or None
    stmt = select(func.coalesce(linked, literal(own, String()))) if own else select(linked)
    value = (await db.execute(stmt)).scalar()
    text = (value or "").strip() if isinstance(value, str) else ""
    return text or None
