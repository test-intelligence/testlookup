"""Immutable DecisionReportV1 persistence and compatibility projection."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from app.db.mongo import Collections
from app.services.privacy_service import sanitize_for_persistence

REPORT_SCHEMA_VERSION = 1


class DecisionReportSubjectUnavailable(RuntimeError):
    """Publication lost the run-level serialization race with deletion."""


@asynccontextmanager
async def _lock_report_subject(test_run_id: str):
    """Hold a PostgreSQL share lock until the Mongo publication completes.

    Deletion takes ``FOR UPDATE`` on the same TestRun row. Whichever operation
    locks first therefore finishes its Mongo check/write before the other can
    proceed: deletion either sees the new report and refuses, or publication
    wakes after the run was deleted and refuses to create an orphan.
    """
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import TestRun

    try:
        run_id = uuid.UUID(test_run_id)
    except (TypeError, ValueError) as exc:
        raise DecisionReportSubjectUnavailable(
            "decision report subject is not a valid run id"
        ) from exc

    async with AsyncSessionLocal() as pg:
        subject = (
            await pg.execute(
                select(TestRun.id)
                .where(TestRun.id == run_id)
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if subject is None:
            raise DecisionReportSubjectUnavailable(
                "decision report subject no longer exists"
            )
        try:
            yield
            await pg.commit()
        except Exception:
            await pg.rollback()
            raise


@asynccontextmanager
async def lock_decision_report_subject(test_run_id: str):
    """Serialize every terminal report write with deletion of its run."""
    async with _lock_report_subject(test_run_id):
        yield


class DecisionReportV1(BaseModel):
    """Immutable, tenant/run/pipeline-bound published report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str
    project_id: str
    test_run_id: str
    pipeline_run_id: str
    report_version: int = Field(ge=1)
    status: Literal["published"] = "published"
    generated_at: datetime
    supersedes_report_id: str | None = None
    decision_intelligence: dict[str, Any]
    verification: dict[str, Any]
    markdown_report: str = Field(max_length=500_000)
    evidence_snapshot_id: str | None = None
    evidence_bundle_sha256: str | None = None
    schema_version: int = REPORT_SCHEMA_VERSION


class DecisionReportAttemptV1(BaseModel):
    """Immutable rejected/failed terminal attempt; never a published report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str
    project_id: str
    test_run_id: str
    pipeline_run_id: str
    status: Literal["rejected", "failed"]
    attempted_at: datetime
    reason: str = Field(max_length=8_000)
    verification: dict[str, Any]
    supersedes_report_id: str | None = None
    schema_version: int = REPORT_SCHEMA_VERSION


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _latest_published(db: Any, test_run_id: str) -> dict[str, Any] | None:
    cursor = db[Collections.DECISION_REPORTS].find(
        {"test_run_id": test_run_id, "status": "published"},
        {"_id": 0},
    ).sort("report_version", -1).limit(1)
    rows = await cursor.to_list(length=1)
    return rows[0] if rows else None


async def _published_for_pipeline(
    db: Any,
    test_run_id: str,
    pipeline_run_id: str,
) -> dict[str, Any] | None:
    """Find an already-published result for an idempotent pipeline retry."""
    cursor = db[Collections.DECISION_REPORTS].find(
        {
            "test_run_id": test_run_id,
            "pipeline_run_id": pipeline_run_id,
            "status": "published",
        },
        {"_id": 0},
    ).sort("report_version", -1).limit(1)
    rows = await cursor.to_list(length=1)
    return rows[0] if rows else None


async def load_latest_decision_report(db: Any, test_run_id: str) -> dict[str, Any] | None:
    """Read the authoritative published version for a run."""
    return await load_decision_report(db, test_run_id)


async def load_decision_report_for_pipeline(
    db: Any,
    test_run_id: str,
    pipeline_run_id: str,
) -> dict[str, Any] | None:
    """Read the immutable report produced by one exact pipeline subject."""
    return await _published_for_pipeline(
        db,
        str(test_run_id),
        str(pipeline_run_id),
    )


async def load_decision_report(
    db: Any,
    test_run_id: str,
    report_version: int | None = None,
) -> dict[str, Any] | None:
    """Read one immutable published report version for a run.

    The run-access dependency is enforced by the API layer. Keeping the
    ``test_run_id`` predicate on every Mongo query prevents a caller from
    selecting a report version belonging to another run.
    """
    query: dict[str, Any] = {
        "test_run_id": str(test_run_id),
        "status": "published",
    }
    if report_version is not None:
        query["report_version"] = int(report_version)
    cursor = db[Collections.DECISION_REPORTS].find(query, {"_id": 0}).sort(
        "report_version", -1
    ).limit(1)
    rows = await cursor.to_list(length=1)
    return rows[0] if rows else None


async def list_decision_report_versions(
    db: Any,
    test_run_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return bounded immutable-version metadata, newest first."""
    bounded_limit = max(1, min(int(limit), 100))
    cursor = db[Collections.DECISION_REPORTS].find(
        {
            "test_run_id": str(test_run_id),
            "status": "published",
        },
        {
            "_id": 0,
            "report_id": 1,
            "pipeline_run_id": 1,
            "evidence_bundle_sha256": 1,
            "report_version": 1,
            "supersedes_report_id": 1,
            "generated_at": 1,
            "status": 1,
        },
    ).sort("report_version", -1).limit(bounded_limit)
    rows = await cursor.to_list(length=bounded_limit)
    return [
        {
            "report_id": row.get("report_id"),
            "pipeline_run_id": row.get("pipeline_run_id"),
            "evidence_bundle_sha256": row.get("evidence_bundle_sha256"),
            "report_version": row.get("report_version"),
            "supersedes_report_id": row.get("supersedes_report_id"),
            "generated_at": row.get("generated_at"),
            "status": row.get("status", "published"),
        }
        for row in rows
        if isinstance(row, dict) and row.get("report_id") and row.get("report_version")
    ]

async def publish_decision_report(
    db: Any,
    *,
    state: dict[str, Any],
    decision: dict[str, Any],
    markdown: str,
) -> dict[str, Any]:
    """Insert a new immutable published version and return its document."""
    test_run_id = str(state["test_run_id"])
    pipeline_run_id = str(state["pipeline_run_id"])
    # A broker redelivery or critic replay for the same pipeline must return
    # the immutable document already published, not create a new superseding
    # version. This lookup is intentionally before version allocation.
    existing = await _published_for_pipeline(db, test_run_id, pipeline_run_id)
    if existing is not None:
        return deepcopy(existing)

    async with _lock_report_subject(test_run_id):
        # The lock may have waited behind another publisher. Re-check the
        # idempotency key while serialization is held.
        existing = await _published_for_pipeline(db, test_run_id, pipeline_run_id)
        if existing is not None:
            return deepcopy(existing)

        snapshot = decision.get("decision_evidence_snapshot") or {}
        for attempt in range(3):
            previous = await _latest_published(db, test_run_id)
            version = int(previous.get("report_version", 0)) + 1 if previous else 1
            report = DecisionReportV1(
                report_id=str(uuid.uuid4()),
                project_id=str(state["project_id"]),
                test_run_id=test_run_id,
                pipeline_run_id=pipeline_run_id,
                report_version=version,
                generated_at=_now(),
                supersedes_report_id=previous.get("report_id") if previous else None,
                decision_intelligence=deepcopy(decision),
                verification=deepcopy(dict(decision.get("verification") or {})),
                markdown_report=markdown,
                evidence_snapshot_id=(
                    str(snapshot.get("snapshot_id"))
                    if snapshot.get("snapshot_id")
                    else None
                ),
                evidence_bundle_sha256=(
                    snapshot.get("content_sha256")
                    or decision.get("evidence_bundle_sha256")
                ),
            )
            document = report.model_dump(mode="json")
            try:
                await db[Collections.DECISION_REPORTS].insert_one(document)
                return document
            except DuplicateKeyError:
                # Another terminal critic won the version race. If it was this
                # pipeline, publication is idempotently complete; otherwise loop
                # and allocate from the new latest version.
                existing = await _published_for_pipeline(
                    db, test_run_id, pipeline_run_id
                )
                if existing is not None:
                    return deepcopy(existing)
                if attempt == 2:
                    raise
    raise RuntimeError("decision_report_publication_retry_exhausted")


async def record_decision_report_attempt(
    db: Any,
    *,
    state: dict[str, Any],
    status: Literal["rejected", "failed"],
    reason: str,
    verification: dict[str, Any],
) -> dict[str, Any]:
    """Persist a failed terminal attempt without replacing a published report."""
    test_run_id = str(state["test_run_id"])
    previous = await _latest_published(db, test_run_id)
    attempt = DecisionReportAttemptV1(
        attempt_id=str(uuid.uuid4()),
        project_id=str(state["project_id"]),
        test_run_id=test_run_id,
        pipeline_run_id=str(state["pipeline_run_id"]),
        status=status,
        attempted_at=_now(),
        reason=sanitize_for_persistence(reason)[:8_000],
        verification=verification,
        supersedes_report_id=previous.get("report_id") if previous else None,
    )
    document = attempt.model_dump(mode="json")
    await db[Collections.DECISION_REPORT_ATTEMPTS].insert_one(document)
    return document
