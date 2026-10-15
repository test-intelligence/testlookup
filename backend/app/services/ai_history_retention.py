"""AI-history retention: the over-cap sweep (AIH-06, owner decision D41).

What is kept, and on which clock:

* ``ai_analysis_versions`` (Postgres, one row per AI answer about one test
  result) dies with its run: the FK is ``ON DELETE CASCADE``, so the runs clock
  of the policy purge and the single-run delete take it with the run. The purge
  counts those rows BEFORE the run delete (:func:`count_cascaded`), because
  after it nothing maps them to the purge any more.
* ``ai_run_versions`` (Mongo, the run summary and the analysis digest of each
  pipeline) rides the runs clock through ``retention_service._MONGO_PLANS``.
* On top of the clocks, two CAPS: at most ``ai_versions_per_run`` versions per
  (run, kind) and ``ai_versions_per_test`` per test result (default 20 / 10).
  The writers prune at write time; this module is the sweep that catches up
  (a bulk reclassify does not prune, a write-time prune whose citation lookup
  failed pruned nothing, a cap lowered by an admin applies to existing rows).

**Retention bugs are data-loss bugs.** The rules are structural:

1. ONE candidate function. :func:`plan_over_cap` is what the preview counts
   and what :func:`execute_over_cap` deletes; the purge calls it once per run
   of ``run_purge`` in either mode, so the preview cannot promise one set and
   the execute delete another (the re-entrant pattern of ``retention_service``).
2. ONE pruning rule. Which versions of a group go is
   ``ai_history_core.plan_prune``: the oldest first, never the lowest version
   (v1, the first answer) and never a version whose pipeline a PUBLISHED
   decision report cites.
3. FAIL CLOSED. Citations live in Mongo. When they cannot be read, or the Mongo
   side of the plan cannot be read, the plan is EMPTY (``unreadable=True``):
   nothing is pruned in either store, and the next sweep retries. Pruning
   without knowing which versions are cited could delete the evidence a
   published report points at.

Runs about to be purged are excluded from the cap plan: their versions go with
the run (cascade / runs clock), and counting them twice would make the preview
disagree with what the execute reports.

Transaction ratchet: stages Postgres deletes on the caller's session and never
commits (``run_purge``'s caller owns the commit). Mongo deletes are immediate;
the sweep is re-entrant, so a crash between the stores leaves a state the next
run finishes.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, NamedTuple, Optional, Sequence

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import Collections
from app.models.postgres import AIAnalysisVersion
from app.services.ai_history_core import AIHistoryCaps, VersionRef, plan_prune

logger = structlog.get_logger(__name__)

_PG_CHUNK = 1000
_MONGO_CHUNK = 500


class RunVersionDoc(NamedTuple):
    """One ``ai_run_versions`` document the sweep would delete."""

    test_run_id: str
    kind: str
    version: int
    version_id: str


@dataclass
class OverCapPlan:
    """What the cap sweep deletes: the SAME object feeds the preview count and
    the execute delete.

    ``unreadable`` = the Mongo side (citations or the run-version groups) could
    not be read, so the plan is empty on purpose (fail closed).
    """

    analysis_version_ids: list[uuid.UUID] = field(default_factory=list)
    run_version_docs: list[RunVersionDoc] = field(default_factory=list)
    unreadable: bool = False

    @property
    def total(self) -> int:
        return len(self.analysis_version_ids) + len(self.run_version_docs)


def _chunks(seq: Sequence, size: int) -> Iterator[Sequence]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


async def count_cascaded(db: AsyncSession, run_ids: Sequence[uuid.UUID]) -> int:
    """``ai_analysis_versions`` rows the run delete will CASCADE away.

    Read BEFORE the delete: afterwards the rows are gone and the count is 0.
    """
    total = 0
    for chunk in _chunks(list(run_ids), _PG_CHUNK):
        total += int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(AIAnalysisVersion)
                    .where(AIAnalysisVersion.test_run_id.in_(list(chunk)))
                )
            ).scalar_one()
            or 0
        )
    return total


async def cited_pipeline_ids(
    mongo: Any,
    *,
    project_id: uuid.UUID,
    test_run_ids: Iterable[str] = (),
) -> Optional[set[str]]:
    """Pipeline ids that a PUBLISHED decision report cites, or ``None`` when
    the reports could not be read (the caller must then prune NOTHING).

    Reads the project's published reports (the read pattern of
    ``retention_service._published_report_artifact_ids``) and, in addition,
    the published reports of the runs whose versions are candidates: a report
    is matched by its run even if its ``project_id`` field were missing, so a
    cited version can never be pruned because of how the report was keyed.
    Every match is read (no length cap): a truncated read would silently
    unprotect the citations past the cut.
    """
    collection = mongo[Collections.DECISION_REPORTS]
    filters: list[dict[str, Any]] = [{"project_id": str(project_id), "status": "published"}]
    runs = sorted({str(run) for run in test_run_ids})
    filters += [
        {"test_run_id": {"$in": list(chunk)}, "status": "published"}
        for chunk in _chunks(runs, _MONGO_CHUNK)
    ]
    cited: set[str] = set()
    try:
        for filt in filters:
            docs = await collection.find(
                filt, {"_id": 0, "pipeline_run_id": 1, "test_run_id": 1},
            ).to_list(length=None)
            for doc in docs or []:
                if isinstance(doc, dict) and doc.get("pipeline_run_id"):
                    cited.add(str(doc["pipeline_run_id"]))
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown": prune nothing
        logger.warning(
            "ai_history_citations_unreadable",
            project_id=str(project_id),
            error_type=type(exc).__name__,
        )
        return None
    return cited


async def _over_cap_run_groups(
    mongo: Any, *, project_id: uuid.UUID, cap: int, excluded: set[str],
) -> list[tuple[str, str]]:
    """(test_run_id, kind) groups of the project holding more than ``cap`` docs."""
    cursor = mongo[Collections.AI_RUN_VERSIONS].aggregate([
        {"$match": {"project_id": str(project_id)}},
        {"$group": {"_id": {"run": "$test_run_id", "kind": "$kind"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": int(cap)}}},
    ])
    groups: list[tuple[str, str]] = []
    for row in await cursor.to_list(length=None) or []:
        key = row.get("_id") if isinstance(row, dict) else None
        if not isinstance(key, dict) or not key.get("run") or not key.get("kind"):
            continue
        run, kind = str(key["run"]), str(key["kind"])
        if run not in excluded:
            groups.append((run, kind))
    return sorted(groups)


async def _run_group_versions(mongo: Any, test_run_id: str, kind: str) -> list[dict]:
    docs = await mongo[Collections.AI_RUN_VERSIONS].find(
        {"test_run_id": test_run_id, "kind": kind},
        {"_id": 0, "version_id": 1, "version": 1, "pipeline_run_id": 1},
    ).to_list(length=None)
    return [d for d in docs or [] if isinstance(d, dict) and isinstance(d.get("version"), int)]


async def _over_cap_analysis_rows(
    db: AsyncSession, *, project_id: uuid.UUID, cap: int, excluded: set[str],
) -> dict[uuid.UUID, list[tuple]]:
    """Every version of the project's test results that hold more than ``cap``
    versions, grouped by test result (runs about to be purged left out: a test
    result belongs to one run, so its whole group cascades with it)."""
    over_cap_cases = (
        select(AIAnalysisVersion.test_case_id)
        .where(AIAnalysisVersion.project_id == project_id)
        .group_by(AIAnalysisVersion.test_case_id)
        .having(func.count() > int(cap))
    )
    rows = (
        await db.execute(
            select(
                AIAnalysisVersion.id,
                AIAnalysisVersion.test_case_id,
                AIAnalysisVersion.test_run_id,
                AIAnalysisVersion.version,
                AIAnalysisVersion.pipeline_run_id,
            ).where(
                AIAnalysisVersion.project_id == project_id,
                AIAnalysisVersion.test_case_id.in_(over_cap_cases),
            )
        )
    ).all()
    groups: dict[uuid.UUID, list[tuple]] = defaultdict(list)
    for version_id, test_case_id, test_run_id, version, pipeline_run_id in rows:
        if str(test_run_id) in excluded:
            continue
        groups[test_case_id].append((version_id, test_run_id, version, pipeline_run_id))
    return groups


async def plan_over_cap(
    db: AsyncSession,
    mongo: Any,
    *,
    project_id: uuid.UUID,
    caps: AIHistoryCaps,
    exclude_run_ids: Iterable[uuid.UUID | str] = (),
) -> OverCapPlan:
    """THE candidate function of the cap sweep: preview counts it, execute
    deletes it. Read-only.

    Any Mongo failure (the run-version groups or the citations) returns an
    EMPTY plan with ``unreadable=True``: nothing is pruned in either store.
    A Postgres failure propagates (the purge itself has failed then).
    """
    excluded = {str(run) for run in exclude_run_ids}
    try:
        run_groups = await _over_cap_run_groups(
            mongo, project_id=project_id, cap=caps.per_run, excluded=excluded,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on any Mongo fault
        logger.warning(
            "ai_history_over_cap_unreadable",
            project_id=str(project_id),
            stage="run_version_groups",
            error_type=type(exc).__name__,
        )
        return OverCapPlan(unreadable=True)

    analysis_groups = await _over_cap_analysis_rows(
        db, project_id=project_id, cap=caps.per_test, excluded=excluded,
    )
    if not run_groups and not analysis_groups:
        return OverCapPlan()

    involved_runs = {run for run, _kind in run_groups}
    involved_runs.update(
        str(row[1]) for rows in analysis_groups.values() for row in rows
    )
    cited = await cited_pipeline_ids(mongo, project_id=project_id, test_run_ids=involved_runs)
    if cited is None:
        return OverCapPlan(unreadable=True)

    analysis_ids: list[uuid.UUID] = []
    for test_case_id in sorted(analysis_groups, key=str):
        rows = analysis_groups[test_case_id]
        refs = [
            VersionRef(
                id=str(version_id),
                version=int(version),
                pipeline_run_id=str(pipeline_run_id) if pipeline_run_id else None,
            )
            for version_id, _run, version, pipeline_run_id in rows
        ]
        analysis_ids += [
            uuid.UUID(ref_id)
            for ref_id in plan_prune(refs, cap=caps.per_test, cited_pipeline_ids=cited)
        ]

    run_docs: list[RunVersionDoc] = []
    try:
        for test_run_id, kind in run_groups:
            docs = await _run_group_versions(mongo, test_run_id, kind)
            by_id = {str(doc.get("version_id")): doc for doc in docs if doc.get("version_id")}
            refs = [
                VersionRef(
                    id=version_id,
                    version=int(doc["version"]),
                    pipeline_run_id=str(doc["pipeline_run_id"]) if doc.get("pipeline_run_id") else None,
                )
                for version_id, doc in by_id.items()
            ]
            for ref_id in plan_prune(refs, cap=caps.per_run, cited_pipeline_ids=cited):
                run_docs.append(RunVersionDoc(test_run_id, kind, int(by_id[ref_id]["version"]), ref_id))
    except Exception as exc:  # noqa: BLE001 - fail closed on any Mongo fault
        logger.warning(
            "ai_history_over_cap_unreadable",
            project_id=str(project_id),
            stage="run_version_docs",
            error_type=type(exc).__name__,
        )
        return OverCapPlan(unreadable=True)

    return OverCapPlan(analysis_version_ids=analysis_ids, run_version_docs=run_docs)


async def execute_over_cap(db: AsyncSession, mongo: Any, plan: OverCapPlan) -> dict[str, int]:
    """Delete exactly what :func:`plan_over_cap` planned. Stages the Postgres
    deletes on ``db`` (the caller commits); Mongo deletes are immediate."""
    analysis_deleted = 0
    for chunk in _chunks(plan.analysis_version_ids, _PG_CHUNK):
        result = await db.execute(
            delete(AIAnalysisVersion).where(AIAnalysisVersion.id.in_(list(chunk)))
        )
        analysis_deleted += int(getattr(result, "rowcount", 0) or 0)

    by_group: dict[tuple[str, str], list[int]] = defaultdict(list)
    for doc in plan.run_version_docs:
        by_group[(doc.test_run_id, doc.kind)].append(doc.version)
    run_deleted = 0
    for (test_run_id, kind), versions in sorted(by_group.items()):
        result = await mongo[Collections.AI_RUN_VERSIONS].delete_many(
            {"test_run_id": test_run_id, "kind": kind, "version": {"$in": sorted(versions)}}
        )
        run_deleted += int(getattr(result, "deleted_count", 0) or 0)

    if analysis_deleted or run_deleted:
        logger.info(
            "ai_history_over_cap_pruned",
            analysis_versions=analysis_deleted,
            run_versions=run_deleted,
        )
    return {"analysis_versions_over_cap": analysis_deleted, "run_versions_over_cap": run_deleted}
