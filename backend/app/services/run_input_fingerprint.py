"""Detect a run's authoritative inputs changing underneath a pipeline (F-8).

Ten capability specs declare ``RunEvidenceBundleV1`` as their input, but the
bundle is built only in the terminal stage. Specialists therefore do not receive
one -- ``anomaly_agent``, ``change_ownership_agent``, ``flaky_sentinel_agent``
and ``regression_watchman`` each ``select(TestRun)`` again, in their own
sessions, at their own point in the run. If the row changes mid-pipeline (a
concurrent finalize, an aggregate repair, a status update) two specialists can
reason over different numbers, and the decision report combines them without
anything noticing.

This module does not fix that. It measures it.

The full fix is to build the bundle once and thread it through every specialist
-- a change across ten agents and dozens of query sites. Before paying for that,
it is worth knowing whether the drift actually happens, and how often. The
ingestion stage already reads the authoritative row at pipeline start; this
records a fingerprint of what it saw, and the terminal stage re-reads and
compares.

**Deliberately non-blocking.** The drift flag is a *warning*, not an error, so
it is recorded and countable without failing verification. Adding an
error-severity flag to this bundle is exactly what stopped 24 decision reports
from publishing earlier today: the guard fired on healthy runs before anyone
knew the real rate. Measure first; escalate on evidence.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from app.services.canonical_json import stable_json_sha256

# Fields whose change mid-run would make two specialists disagree. Deliberately
# the counts and status -- not timestamps or free text, which move for reasons
# that do not affect any specialist's reasoning.
_FINGERPRINTED_FIELDS = (
    "total_tests",
    "passed_tests",
    "failed_tests",
    "skipped_tests",
    "broken_tests",
    "unknown_tests",
    "status",
)

DRIFT_FLAG_CODE = "run_inputs_changed_mid_pipeline"
# A probe that could not run is not a probe that found nothing.
PROBE_FAILED_FLAG_CODE = "run_input_drift_unverified"


def run_input_fingerprint(
    run_data: Optional[dict[str, Any]],
    failed_ids: Optional[Iterable[Any]] = None,
) -> str:
    """A stable hash of the inputs every specialist reasons over.

    Sorted failed ids, because two reads of the same set in a different order
    are the same set -- flagging that would be noise, and a noisy signal gets
    switched off.
    """
    data = run_data if isinstance(run_data, dict) else {}
    payload = {
        "counts": {field: data.get(field) for field in _FINGERPRINTED_FIELDS},
        "failed_test_ids": sorted(str(item) for item in (failed_ids or [])),
    }
    return stable_json_sha256(payload)


def describe_drift(expected: str, observed: str) -> Optional[dict[str, str]]:
    """A quality flag when the run's inputs moved, or None when they held.

    Severity is ``warning`` on purpose -- see the module docstring.
    """
    if not expected or not observed or expected == observed:
        return None
    return {
        "code": DRIFT_FLAG_CODE,
        "severity": "warning",
        "detail": (
            "the run's authoritative counts or failed-test set changed while the "
            f"pipeline was running (start {expected[:12]}, end {observed[:12]}); "
            "specialists that re-queried may have reasoned over different data"
        ),
    }


async def detect_run_input_drift(state: dict[str, Any]) -> Optional[dict[str, str]]:
    """Re-read the authoritative row and compare with what ingestion saw.

    Returns a quality flag when the run moved underneath the pipeline, or None.
    Never raises: a drift probe that breaks the run would be worse than the
    inconsistency it is trying to measure.
    """
    expected = str(state.get("run_input_fingerprint") or "")
    test_run_id = str(state.get("test_run_id") or "")
    if not expected or not test_run_id:
        return None
    try:
        from sqlalchemy import select

        from app.db.postgres import AsyncSessionLocal
        from app.models.postgres import TestCase, TestRun, TestStatus

        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(select(TestRun).where(TestRun.id == test_run_id))
            ).scalar_one_or_none()
            if run is None:
                return None
            # Exactly the filter ingestion used, or the two fingerprints are
            # not comparable and every run looks like it drifted.
            rows = (
                await db.execute(
                    select(TestCase.id).where(
                        TestCase.test_run_id == test_run_id,
                        TestCase.status.in_(
                            [s.value for s in (TestStatus.FAILED, TestStatus.BROKEN)]
                        ),
                    )
                )
            ).all()
            observed_ids = [str(r.id) for r in rows]
            current = {
                field: getattr(run, field, None) for field in _FINGERPRINTED_FIELDS
            }
            current["status"] = getattr(run.status, "value", run.status) or "UNKNOWN"
            observed = run_input_fingerprint(current, observed_ids)
    except Exception as exc:  # probe must never break the pipeline
        # But a probe that could not look must NOT report "no drift" -- that is
        # a clean bill of health issued by a broken instrument, and this session
        # has already been bitten three times by absence reading as success.
        return {
            "code": PROBE_FAILED_FLAG_CODE,
            "severity": "warning",
            "detail": (
                "could not verify whether the run's inputs changed mid-pipeline: "
                f"{type(exc).__name__}"
            ),
        }
    return describe_drift(expected, observed)
