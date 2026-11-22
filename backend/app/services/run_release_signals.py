"""What a run carries into its release verdict beyond the risk score.

The run verdict was a weighted score of seven dimensions. Two things the product
already knows never entered it as such (product findings D-04, D-83, D-146):

* **Failing tests.** Their number entered only through the pass rate, so four
  product failures in 65 tests moved the score by under 1.5 points and the run
  read Go.
* **Open defects by severity.** Each open defect added 2 raw points whatever its
  severity; an open P1 contributed 0.5 to the composite. The release gate, on
  the other hand, blocks on any open CRITICAL or HIGH defect
  (``release_defect_service.BLOCKING_SEVERITIES``), so a release read No-Go
  while its newest run read Go.

This module reads both, once, for the quick look and the stored decision alike.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.models.postgres import Defect, FailureCategory, TestCase, TestStatus
from app.services.defect_status import ACTIVE_STATUSES
from app.services.release_defect_service import BLOCKING_SEVERITIES
from app.services.release_rollup_service import _is_quarantined

#: Failure categories that do not hold a release on their own: an intermittent
#: test, or the environment rather than the product. Everything else (product
#: bug, test data, automation defect, unknown, not yet classified) does.
NON_BLOCKING_CATEGORIES = frozenset(
    {FailureCategory.FLAKY.value, FailureCategory.INFRASTRUCTURE.value}
)

_FAILED = (TestStatus.FAILED.value, TestStatus.BROKEN.value)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def is_blocking_failure(category: Any, tags: Any) -> bool:
    """A failed test that holds the release: not quarantined, not flaky or infrastructure."""
    if _is_quarantined(tags):
        return False
    value = str(getattr(category, "value", category) or "").upper()
    return value not in NON_BLOCKING_CATEGORIES


async def load_run_release_signals(db, run_id: Any, project_id: Any) -> dict[str, Any]:
    """``{blocking_failures, open_defects_by_severity, open_defects, blocking_defects}``.

    ``open_defects`` counts every live defect of the project (the old input);
    ``open_defects_by_severity`` splits it by severity, upper-cased, with
    ``UNSET`` for a defect that has none.
    """
    run_uuid = _as_uuid(run_id)
    project_uuid = _as_uuid(project_id)

    blocking_failures = 0
    if run_uuid is not None:
        rows = (
            await db.execute(
                select(TestCase.failure_category, TestCase.tags).where(
                    TestCase.test_run_id == run_uuid,
                    TestCase.status.in_(_FAILED),
                )
            )
        ).all()
        blocking_failures = sum(1 for row in rows if is_blocking_failure(row[0], row[1]))

    by_severity: dict[str, int] = {}
    if project_uuid is not None:
        for severity, count in (
            await db.execute(
                select(Defect.severity, func.count(Defect.id))
                .where(Defect.project_id == project_uuid)
                .where(Defect.resolution_status.in_(ACTIVE_STATUSES))
                .group_by(Defect.severity)
            )
        ).all():
            key = str(getattr(severity, "value", severity) or "UNSET").upper()
            by_severity[key] = by_severity.get(key, 0) + int(count or 0)

    return {
        "blocking_failures": blocking_failures,
        "open_defects_by_severity": by_severity,
        "open_defects": sum(by_severity.values()),
        "blocking_defects": sum(by_severity.get(s, 0) for s in BLOCKING_SEVERITIES),
    }
