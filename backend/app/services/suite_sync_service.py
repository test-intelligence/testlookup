"""
Suite Sync Service — maintains suite membership traceability from test runs.

After each ingestion, syncs the suite_memberships table against the test cases
in the newly ingested run. Detects additions, deletions, modifications, and
restorations. Deleted tests are tracked in a `<suite>-deleted` bucket with a
`needs_review` tag.

Triggered from: ingestion.py after _update_run_aggregates()

Usage:
    from app.services.suite_sync_service import sync_suite_membership
    summary = await sync_suite_membership(db, project_id, run_id)
"""
import uuid
from typing import Any

import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    ManagedTestCase,
    SuiteMembership,
    SuiteMembershipEvent,
    TestCase,
    TestCaseAuditLog,
)

logger = structlog.get_logger("services.suite_sync")


async def sync_suite_membership(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[dict]:
    """Sync suite membership from a completed test run.

    Compares test_cases in the run against current suite_memberships.
    Produces events for additions, deletions, modifications, and restorations.
    Idempotent: re-running for the same run_id produces no duplicate changes.

    Returns list of SuiteSyncSummary dicts (one per affected suite).
    """
    # 1. Fetch all test cases from this run, grouped by suite
    result = await db.execute(
        select(TestCase).where(
            TestCase.test_run_id == run_id,
            TestCase.suite_name.isnot(None),
            TestCase.suite_name != "",
        )
    )
    run_cases = result.scalars().all()

    if not run_cases:
        return []

    # Group by suite_name
    suites: dict[str, list[TestCase]] = {}
    for tc in run_cases:
        suites.setdefault(tc.suite_name, []).append(tc)

    summaries = []
    for suite_name, cases in suites.items():
        summary = await _sync_one_suite(db, project_id, run_id, suite_name, cases)
        summaries.append(summary)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    logger.info(
        "suite_sync_complete",
        project_id=str(project_id),
        run_id=str(run_id),
        suites_synced=len(summaries),
        total_added=sum(s["added_count"] for s in summaries),
        total_deleted=sum(s["deleted_count"] for s in summaries),
    )
    return summaries


async def _sync_one_suite(
    db: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    suite_name: str,
    run_cases: list[TestCase],
) -> dict[str, Any]:
    """Sync membership for a single suite from the run's test cases."""
    summary = {
        "suite_name": suite_name,
        "run_id": str(run_id),
        "added_count": 0,
        "deleted_count": 0,
        "modified_count": 0,
        "restored_count": 0,
        "unchanged_count": 0,
    }

    # Build run fingerprint map
    run_map: dict[str, TestCase] = {}
    for tc in run_cases:
        if tc.test_fingerprint:
            run_map[tc.test_fingerprint] = tc

    # Batch-fetch managed test cases for auto-linking (avoids N+1 per-fingerprint queries)
    managed_map: dict[str, uuid.UUID] = {}
    if run_map:
        managed_result = await db.execute(
            select(ManagedTestCase.id, ManagedTestCase.test_fingerprint).where(
                and_(
                    ManagedTestCase.project_id == project_id,
                    ManagedTestCase.test_fingerprint.in_(list(run_map.keys())),
                    ManagedTestCase.status != "deprecated",
                )
            )
        )
        for row in managed_result.all():
            managed_map[row.test_fingerprint] = row.id

    # Fetch current memberships for this suite (active + deleted)
    existing_result = await db.execute(
        select(SuiteMembership).where(
            SuiteMembership.project_id == project_id,
            SuiteMembership.suite_name == suite_name,
        )
    )
    existing = {m.test_fingerprint: m for m in existing_result.scalars().all()}

    # Also check the -deleted bucket for potential restorations
    deleted_bucket = f"{suite_name}-deleted"
    deleted_result = await db.execute(
        select(SuiteMembership).where(
            SuiteMembership.project_id == project_id,
            SuiteMembership.suite_name == deleted_bucket,
        )
    )
    deleted_members = {m.test_fingerprint: m for m in deleted_result.scalars().all()}

    # Process each test case in the run
    for fingerprint, tc in run_map.items():
        if fingerprint in existing:
            member = existing[fingerprint]
            # Already seen — check for modifications
            if member.last_seen_run_id == run_id:
                # Idempotent: already synced for this run
                summary["unchanged_count"] += 1
                continue

            modified = False
            old_vals = {}
            new_vals = {}

            if member.test_name != tc.test_name:
                old_vals["test_name"] = member.test_name
                new_vals["test_name"] = tc.test_name
                member.test_name = tc.test_name
                modified = True

            if member.class_name != tc.class_name:
                old_vals["class_name"] = member.class_name
                new_vals["class_name"] = tc.class_name
                member.class_name = tc.class_name
                modified = True

            member.last_seen_run_id = run_id

            # If it was deleted, restore it
            if member.status in ("deleted", "needs_review"):
                member.status = "active"
                member.review_tag = None
                member.deleted_at_run_id = None
                summary["restored_count"] += 1
                _add_event(db, project_id, suite_name, fingerprint, tc.test_name, "restored", run_id,
                           details=f"Test restored to {suite_name} from {deleted_bucket}")
            elif modified:
                summary["modified_count"] += 1
                _add_event(db, project_id, suite_name, fingerprint, tc.test_name, "modified", run_id,
                           old_values=old_vals, new_values=new_vals)
            else:
                summary["unchanged_count"] += 1

        elif fingerprint in deleted_members:
            # Was in the -deleted bucket — restore it
            deleted_member = deleted_members[fingerprint]
            deleted_member.suite_name = suite_name
            deleted_member.status = "active"
            deleted_member.review_tag = None
            deleted_member.deleted_at_run_id = None
            deleted_member.last_seen_run_id = run_id
            deleted_member.test_name = tc.test_name
            deleted_member.class_name = tc.class_name
            summary["restored_count"] += 1
            _add_event(db, project_id, suite_name, fingerprint, tc.test_name, "restored", run_id,
                       details=f"Test restored from {deleted_bucket}")

        else:
            # New test case — add to suite (use batch-fetched managed_map)
            managed_id = managed_map.get(fingerprint)
            new_member = SuiteMembership(
                project_id=project_id,
                suite_name=suite_name,
                test_fingerprint=fingerprint,
                test_name=tc.test_name,
                class_name=tc.class_name,
                managed_test_case_id=managed_id,
                source="linked" if managed_id else "execution",
                status="active",
                last_seen_run_id=run_id,
                first_seen_run_id=run_id,
            )
            db.add(new_member)
            summary["added_count"] += 1
            _add_event(db, project_id, suite_name, fingerprint, tc.test_name, "added", run_id)

    # Detect deletions: active members in this suite NOT in the run
    for fingerprint, member in existing.items():
        if fingerprint not in run_map and member.status == "active":
            # Mark as deleted, move to -deleted bucket
            member.status = "needs_review"
            member.suite_name = deleted_bucket
            member.deleted_at_run_id = run_id
            member.review_tag = "needs_review"
            summary["deleted_count"] += 1
            _add_event(db, project_id, suite_name, fingerprint, member.test_name, "deleted", run_id,
                       details=f"Test absent from latest run; moved to {deleted_bucket}")

            # Write audit log entry
            db.add(TestCaseAuditLog(
                entity_type="suite_membership",
                entity_id=member.id,
                project_id=project_id,
                action="sync_deleted",
                actor_name="system:suite_sync",
                new_values={"suite_name": deleted_bucket, "review_tag": "needs_review"},
                details=f"Test {member.test_name} absent from run {run_id}; moved to {deleted_bucket}",
            ))

    return summary


def _add_event(
    db: AsyncSession,
    project_id: uuid.UUID,
    suite_name: str,
    fingerprint: str,
    test_name: str,
    event_type: str,
    run_id: uuid.UUID,
    old_values: dict | None = None,
    new_values: dict | None = None,
    details: str | None = None,
) -> None:
    """Add an immutable suite membership event."""
    db.add(SuiteMembershipEvent(
        project_id=project_id,
        suite_name=suite_name,
        test_fingerprint=fingerprint,
        test_name=test_name,
        event_type=event_type,
        run_id=run_id,
        old_values=old_values,
        new_values=new_values,
        details=details,
    ))
