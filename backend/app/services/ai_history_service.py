"""AI history: the WRITE half and the digest build (AIH-01, AIH-06).

* Per test result (Postgres ``ai_analysis_versions``): :func:`stage_analysis_versions`
  is called by the four ``ai_analysis`` writers (pipeline upsert, single-test
  analyze, human correction, bulk reclassify via
  :func:`stage_bulk_reclassify_versions`) inside THEIR transaction. It flushes
  only, inside a SAVEPOINT, and never fails or rolls back the analysis write
  around it: a history error is logged and counted, nothing more.
* Per run (Mongo ``ai_run_versions``): :func:`record_run_version` allocates
  ``version = max + 1`` per ``(run, kind)`` under the decision-report subject
  lock (a run deleted meanwhile leaves no orphan doc), idempotent per
  ``(pipeline_run_id, content_sha256)``. :func:`record_run_summary_version`
  (summary agent) never raises; :func:`capture_analysis_digest` (pipeline
  success hook) records the digest and stages the ``analysis.completed`` /
  ``analysis.verdict_changed`` activity rows on the caller's session.
* :func:`build_digest` loads the run's rows and hands them to the pure
  ``ai_history_core.assemble_digest``; ``pipeline_run_id=None`` is the "live"
  digest of the current data.
* Retention at write time (D41): :func:`plan_prune` per test and per run, fail
  closed when the published-report citations cannot be read.

No ``commit()`` here (transaction ratchet): callers commit. Contract:
``docs/deferred-scope-2026-10-13/work/W03-contract.md`` sections 1, 2, 5.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import structlog
from pymongo.errors import DuplicateKeyError
from sqlalchemy import bindparam, func, literal, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, insert as pg_insert
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import SQLAlchemyError

from app.db.mongo import Collections
from app.services import ai_history_core as core
from app.services.ai_history_core import AIHistoryCaps, VersionRef
from app.services.decision_report_service import (
    DecisionReportSubjectUnavailable,
    load_decision_report_for_pipeline,
    load_latest_decision_report,
    lock_decision_report_subject,
)

logger = structlog.get_logger(__name__)

#: Bulk reclassify inserts at most this many ids per statement.
BULK_CHUNK = 5000
#: Cluster member ids resolved per query.
_MEMBER_CHUNK = 1000
#: Version-allocation attempts. The subject lock is a SHARE lock (it excludes
#: run deletion, not other writers), so N concurrent writers of one run+kind
#: race: each round one wins, and N writers need up to N rounds. The decision
#: report's 3 would fail the 4th and 5th of five concurrent captures.
_ALLOCATION_ATTEMPTS = 8


class AIHistorySubjectUnavailable(DecisionReportSubjectUnavailable):
    """The run was deleted before its version could be recorded (no orphan doc)."""


class AIHistoryUnavailable(RuntimeError):
    """Mongo failed; routers map it to 503 ``ai_history_unavailable``."""


class AIHistoryRunNotFound(LookupError):
    """``build_digest`` was asked for a run that does not exist."""


# ── Snapshots of one analysis ────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalysisSnapshot:
    test_case_id: uuid.UUID
    failure_category: Optional[str]
    confidence_score: Optional[int]
    root_cause_summary: Optional[str]
    is_flaky: bool
    backend_error_found: bool
    pod_issue_found: bool
    requires_human_review: bool
    recommended_actions: Optional[list]
    evidence_references: Optional[list]
    tools_used: Optional[list]
    role_actions: Optional[dict]
    llm_provider: Optional[str]
    llm_model: Optional[str]
    routing_metadata: Optional[dict]


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _opt_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _category(value: Any) -> Optional[str]:
    value = core.enum_value(value)
    return None if value is None else str(value)


def snapshot_from_analysis_dict(
    test_case_id: Any,
    analysis: Mapping[str, Any],
    routing_metadata: Optional[Mapping[str, Any]],
) -> AnalysisSnapshot:
    """What the pipeline upsert writes for ``analysis`` (same defaults as its INSERT)."""
    return AnalysisSnapshot(
        test_case_id=_uuid(test_case_id),
        failure_category=_category(analysis.get("failure_category", "UNKNOWN")),
        confidence_score=_opt_int(analysis.get("confidence_score", 0)),
        root_cause_summary=analysis.get("root_cause_summary"),
        is_flaky=bool(analysis.get("is_flaky", False)),
        backend_error_found=bool(analysis.get("backend_error_found", False)),
        pod_issue_found=bool(analysis.get("pod_issue_found", False)),
        requires_human_review=bool(analysis.get("requires_human_review", True)),
        recommended_actions=analysis.get("recommended_actions", []),
        evidence_references=analysis.get("evidence_references", []),
        tools_used=analysis.get("tools_used"),
        role_actions=analysis.get("role_actions"),
        llm_provider=analysis.get("llm_provider"),
        llm_model=analysis.get("llm_model"),
        routing_metadata=dict(routing_metadata) if isinstance(routing_metadata, Mapping) else None,
    )


def snapshot_from_orm(row: Any) -> AnalysisSnapshot:
    """The in-memory (possibly unflushed) attributes of an ``AIAnalysis`` row."""
    routing = getattr(row, "routing_metadata", None)
    return AnalysisSnapshot(
        test_case_id=_uuid(row.test_case_id),
        failure_category=_category(getattr(row, "failure_category", None)),
        confidence_score=_opt_int(getattr(row, "confidence_score", None)),
        root_cause_summary=getattr(row, "root_cause_summary", None),
        is_flaky=bool(getattr(row, "is_flaky", False)),
        backend_error_found=bool(getattr(row, "backend_error_found", False)),
        pod_issue_found=bool(getattr(row, "pod_issue_found", False)),
        requires_human_review=bool(getattr(row, "requires_human_review", False)),
        recommended_actions=getattr(row, "recommended_actions", None),
        evidence_references=getattr(row, "evidence_references", None),
        tools_used=getattr(row, "tools_used", None),
        role_actions=getattr(row, "role_actions", None),
        llm_provider=getattr(row, "llm_provider", None),
        llm_model=getattr(row, "llm_model", None),
        routing_metadata=dict(routing) if isinstance(routing, Mapping) else None,
    )


def _analysis_mode(routing: Optional[Mapping[str, Any]]) -> Optional[str]:
    mode = (routing or {}).get("analysis_mode")
    return mode[:20] if isinstance(mode, str) and mode else None


def snapshot_sha256(snapshot: AnalysisSnapshot) -> str:
    return core.analysis_content_sha256(
        failure_category=snapshot.failure_category,
        confidence_score=snapshot.confidence_score,
        is_flaky=snapshot.is_flaky,
        requires_human_review=snapshot.requires_human_review,
        backend_error_found=snapshot.backend_error_found,
        pod_issue_found=snapshot.pod_issue_found,
        llm_provider=snapshot.llm_provider,
        llm_model=snapshot.llm_model,
        analysis_mode=(snapshot.routing_metadata or {}).get("analysis_mode"),
        root_cause_summary=snapshot.root_cause_summary,
    )


def _plain_json(value: Any) -> Any:
    """Coerce to plain JSON (datetimes, UUIDs, enums -> str)."""
    if value is None:
        return None
    return json.loads(core.canonical_json(value))


def _slim_provenance(routing: Optional[Mapping[str, Any]], source: str) -> dict:
    routing = routing or {}
    snapshot = routing.get("model_config_snapshot")
    slim: dict[str, Any] = {
        "analysis_mode": routing.get("analysis_mode"),
        "mode_requested": routing.get("mode_requested"),
        "fallback_from": routing.get("fallback_from"),
        "fallback_reason": routing.get("fallback_reason"),
        "prompt_versions": routing.get("prompt_versions"),
        "eval_manifest_checksum": routing.get("eval_manifest_checksum"),
        "model_config_snapshot": (
            {"provider": snapshot.get("provider"), "model": snapshot.get("model")}
            if isinstance(snapshot, Mapping) else None
        ),
        "confidence_basis": routing.get("confidence_basis"),
        "source": source,
    }
    return _plain_json({k: v for k, v in slim.items() if v is not None}) or {}


# ── Per-test versions ────────────────────────────────────────────────────────


@dataclass
class StageResult:
    staged: int = 0
    skipped_identical: int = 0
    skipped_replay: int = 0
    failed: int = 0
    pruned: int = 0


async def resolve_caps(db: Any, project_id: Any) -> AIHistoryCaps:
    """The project's caps; NULL (or no policy row) -> the defaults 20 / 10."""
    from app.models.postgres import ProjectRetentionPolicy

    row = (
        await db.execute(
            select(
                ProjectRetentionPolicy.ai_versions_per_run,
                ProjectRetentionPolicy.ai_versions_per_test,
            ).where(ProjectRetentionPolicy.project_id == _uuid(project_id))
        )
    ).first()
    if row is None:
        return AIHistoryCaps()
    return AIHistoryCaps(
        per_run=core.clamp_cap(row[0], core.DEFAULT_CAP_PER_RUN),
        per_test=core.clamp_cap(row[1], core.DEFAULT_CAP_PER_TEST),
    )


async def cited_pipeline_ids(
    mongo: Any,
    *,
    test_run_ids: Optional[Sequence[str]] = None,
    project_id: Optional[str] = None,
) -> Optional[set[str]]:
    """Pipelines cited by a PUBLISHED decision report of these runs / this project.

    ``None`` = could not be read: the caller must NOT prune (fail closed).
    """
    if mongo is None:
        return None
    if test_run_ids is not None:
        query: dict[str, Any] = {"test_run_id": {"$in": [str(r) for r in test_run_ids]}}
    elif project_id is not None:
        query = {"project_id": str(project_id)}
    else:
        return None
    query["status"] = "published"
    try:
        cursor = mongo[Collections.DECISION_REPORTS].find(
            query, {"_id": 0, "pipeline_run_id": 1}
        )
        rows = await cursor.to_list(length=None)
    except Exception as exc:  # noqa: BLE001 -- fail closed, never prune blind
        logger.warning("ai_history_citations_unreadable", error_type=type(exc).__name__)
        return None
    return {str(r["pipeline_run_id"]) for r in rows if isinstance(r, Mapping) and r.get("pipeline_run_id")}


async def stage_analysis_versions(
    db: Any,
    snapshots: Sequence[AnalysisSnapshot],
    *,
    trigger: str,
    pipeline_run_id: Optional[uuid.UUID] = None,
    actor_user_id: Optional[uuid.UUID] = None,
    mongo: Any = None,
    created_at: Optional[datetime] = None,
) -> StageResult:
    """Append one ``ai_analysis_versions`` row per snapshot (flush only).

    Dedup (contract 1.5): ``pipeline`` with a pipeline id -> a replay of the
    same pipeline inserts nothing (partial unique index), a new pipeline always
    versions; every other trigger is skipped when its hash equals the latest
    version's. Never commits, never rolls back the caller's transaction, never
    raises: the analysis write around it must not fail because of history.
    """
    result = StageResult()
    if not snapshots:
        return result
    try:
        return await _stage(
            db,
            list(snapshots),
            trigger=trigger,
            pipeline_run_id=pipeline_run_id,
            actor_user_id=actor_user_id,
            mongo=mongo,
            created_at=created_at or datetime.now(timezone.utc),
            result=result,
        )
    except Exception as exc:  # noqa: BLE001 -- history never fails the analysis write
        logger.warning(
            "ai_history_versions_not_recorded",
            error_type=type(exc).__name__,
            count=len(snapshots),
            trigger=trigger,
        )
        result.failed = len(snapshots) - result.staged - result.skipped_identical - result.skipped_replay
        return result


async def _stage(
    db: Any,
    snapshots: list[AnalysisSnapshot],
    *,
    trigger: str,
    pipeline_run_id: Optional[uuid.UUID],
    actor_user_id: Optional[uuid.UUID],
    mongo: Any,
    created_at: datetime,
    result: StageResult,
) -> StageResult:
    from app.models.postgres import AIAnalysisVersion, TestCase, TestRun

    # The last snapshot of a test case wins (one version per case per call).
    by_case: dict[uuid.UUID, AnalysisSnapshot] = {}
    for snap in snapshots:
        by_case[snap.test_case_id] = snap
    ids = list(by_case)
    pipeline_key = _uuid(pipeline_run_id) if pipeline_run_id else None
    hash_skip = trigger != "pipeline" or pipeline_key is None
    inserted_cases: list[uuid.UUID] = []
    latest: dict[uuid.UUID, tuple[int, str, int]] = {}
    subjects: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, str]] = {}

    try:
        async with db.begin_nested():
            resolved = (
                await db.execute(
                    select(TestCase.id, TestCase.test_run_id, TestRun.project_id, TestCase.test_fingerprint)
                    .join(TestRun, TestRun.id == TestCase.test_run_id)
                    .where(TestCase.id.in_(ids))
                )
            ).all()
            subjects = {row[0]: (row[1], row[2], row[3]) for row in resolved}
            count_over = func.count().over(partition_by=AIAnalysisVersion.test_case_id)
            latest_rows = (
                await db.execute(
                    select(
                        AIAnalysisVersion.test_case_id,
                        AIAnalysisVersion.version,
                        AIAnalysisVersion.content_sha256,
                        count_over,
                    )
                    .where(AIAnalysisVersion.test_case_id.in_(ids))
                    .distinct(AIAnalysisVersion.test_case_id)
                    .order_by(AIAnalysisVersion.test_case_id, AIAnalysisVersion.version.desc())
                )
            ).all()
            latest = {row[0]: (int(row[1]), row[2], int(row[3])) for row in latest_rows}

            values: list[dict] = []
            for case_id in ids:
                subject = subjects.get(case_id)
                if subject is None:
                    result.failed += 1
                    continue
                snap = by_case[case_id]
                sha = snapshot_sha256(snap)
                prev = latest.get(case_id)
                if hash_skip and prev is not None and prev[1] == sha:
                    result.skipped_identical += 1
                    continue
                run_id, project_id, fingerprint = subject
                values.append({
                    "id": uuid.uuid4(),
                    "test_case_id": case_id,
                    "test_run_id": run_id,
                    "project_id": project_id,
                    "test_fingerprint": fingerprint or "",
                    "version": (prev[0] + 1) if prev else 1,
                    "trigger": trigger,
                    "pipeline_run_id": pipeline_key,
                    "actor_user_id": _uuid(actor_user_id) if actor_user_id else None,
                    "content_sha256": sha,
                    "failure_category": (snap.failure_category or None) and snap.failure_category[:30],
                    "confidence_score": snap.confidence_score,
                    "root_cause_summary": snap.root_cause_summary,
                    "is_flaky": snap.is_flaky,
                    "backend_error_found": snap.backend_error_found,
                    "pod_issue_found": snap.pod_issue_found,
                    "requires_human_review": snap.requires_human_review,
                    "recommended_actions": _plain_json(snap.recommended_actions),
                    "evidence_references": _plain_json(snap.evidence_references),
                    "tools_used": _plain_json(snap.tools_used),
                    "role_actions": _plain_json(snap.role_actions),
                    "llm_provider": (snap.llm_provider or None) and str(snap.llm_provider)[:50],
                    "llm_model": (snap.llm_model or None) and str(snap.llm_model)[:100],
                    "analysis_mode": _analysis_mode(snap.routing_metadata),
                    "provenance": _slim_provenance(snap.routing_metadata, trigger),
                    "created_at": created_at,
                })
            if values:
                stmt = (
                    pg_insert(AIAnalysisVersion)
                    .values(values)
                    .on_conflict_do_nothing()
                    .returning(AIAnalysisVersion.test_case_id)
                )
                inserted_cases = [row[0] for row in (await db.execute(stmt)).all()]
                await db.flush()
                result.staged = len(inserted_cases)
                result.skipped_replay = len(values) - len(inserted_cases)
    except SQLAlchemyError as exc:
        logger.warning(
            "ai_history_versions_not_recorded",
            error_type=type(exc).__name__,
            count=len(ids),
            trigger=trigger,
        )
        result.failed = len(ids) - result.skipped_identical
        result.staged = 0
        result.skipped_replay = 0
        return result

    if inserted_cases:
        result.pruned = await _prune_tests_after_insert(
            db, inserted_cases, latest=latest, subjects=subjects, mongo=mongo
        )
    return result


async def _prune_tests_after_insert(
    db: Any,
    inserted_cases: Sequence[uuid.UUID],
    *,
    latest: Mapping[uuid.UUID, tuple[int, str, int]],
    subjects: Mapping[uuid.UUID, tuple[uuid.UUID, uuid.UUID, str]],
    mongo: Any,
) -> int:
    """Write-time per-test prune (D41). Fail closed: no Mongo, no prune."""
    from app.models.postgres import AIAnalysisVersion

    project_caps: dict[uuid.UUID, AIHistoryCaps] = {}
    over: list[uuid.UUID] = []
    try:
        async with db.begin_nested():
            for case_id in inserted_cases:
                project_id = subjects[case_id][1]
                if project_id not in project_caps:
                    project_caps[project_id] = await resolve_caps(db, project_id)
                count_after = (latest[case_id][2] if case_id in latest else 0) + 1
                if count_after > project_caps[project_id].per_test:
                    over.append(case_id)
    except SQLAlchemyError as exc:
        logger.warning("ai_history_prune_failed", error_type=type(exc).__name__)
        return 0
    if not over:
        return 0
    cited = await cited_pipeline_ids(
        mongo, test_run_ids=sorted({str(subjects[c][0]) for c in over})
    )
    if cited is None:
        return 0
    try:
        async with db.begin_nested():
            rows = (
                await db.execute(
                    select(
                        AIAnalysisVersion.id,
                        AIAnalysisVersion.test_case_id,
                        AIAnalysisVersion.version,
                        AIAnalysisVersion.pipeline_run_id,
                    ).where(AIAnalysisVersion.test_case_id.in_(over))
                )
            ).all()
            grouped: dict[uuid.UUID, list[VersionRef]] = {}
            for vid, case_id, version, pipeline in rows:
                grouped.setdefault(case_id, []).append(
                    VersionRef(id=str(vid), version=int(version), pipeline_run_id=str(pipeline) if pipeline else None)
                )
            doomed: list[uuid.UUID] = []
            for case_id, refs in grouped.items():
                cap = project_caps[subjects[case_id][1]].per_test
                doomed.extend(uuid.UUID(x) for x in core.plan_prune(refs, cap=cap, cited_pipeline_ids=cited))
            if doomed:
                from sqlalchemy import delete

                await db.execute(delete(AIAnalysisVersion).where(AIAnalysisVersion.id.in_(doomed)))
                await db.flush()
            return len(doomed)
    except SQLAlchemyError as exc:
        logger.warning("ai_history_prune_failed", error_type=type(exc).__name__)
        return 0


async def stage_analysis_version(
    db: Any,
    snapshot: AnalysisSnapshot,
    *,
    trigger: str,
    pipeline_run_id: Optional[uuid.UUID] = None,
    actor_user_id: Optional[uuid.UUID] = None,
    mongo: Any = None,
) -> StageResult:
    return await stage_analysis_versions(
        db,
        [snapshot],
        trigger=trigger,
        pipeline_run_id=pipeline_run_id,
        actor_user_id=actor_user_id,
        mongo=mongo,
    )


_BULK_SHA = core.ANALYSIS_CONTENT_SHA256_SQL.format(a="a")
BULK_RECLASSIFY_SQL = (
    "INSERT INTO ai_analysis_versions (id, test_case_id, test_run_id, project_id, test_fingerprint, "
    "version, trigger, pipeline_run_id, actor_user_id, content_sha256, failure_category, "
    "confidence_score, root_cause_summary, is_flaky, backend_error_found, pod_issue_found, "
    "requires_human_review, recommended_actions, evidence_references, tools_used, role_actions, "
    "llm_provider, llm_model, analysis_mode, provenance, created_at) "
    "SELECT gen_random_uuid(), a.test_case_id, tc.test_run_id, tr.project_id, tc.test_fingerprint, "
    "coalesce((SELECT max(v.version) FROM ai_analysis_versions v WHERE v.test_case_id = a.test_case_id), 0) + 1, "
    "'bulk_reclassify', NULL, CAST(:actor AS uuid), " + _BULK_SHA + ", "
    "a.failure_category, a.confidence_score, a.root_cause_summary, "
    "coalesce(a.is_flaky, false), coalesce(a.backend_error_found, false), "
    "coalesce(a.pod_issue_found, false), coalesce(a.requires_human_review, false), "
    "a.recommended_actions, a.evidence_references, a.tools_used, a.role_actions, "
    "a.llm_provider, a.llm_model, left(a.routing_metadata->>'analysis_mode', 20), "
    "jsonb_strip_nulls(jsonb_build_object("
    "'analysis_mode', a.routing_metadata->>'analysis_mode', "
    "'mode_requested', a.routing_metadata->>'mode_requested', "
    "'fallback_from', a.routing_metadata->>'fallback_from', "
    "'fallback_reason', a.routing_metadata->>'fallback_reason', "
    "'prompt_versions', a.routing_metadata->'prompt_versions', "
    "'eval_manifest_checksum', a.routing_metadata->>'eval_manifest_checksum', "
    "'source', 'bulk_reclassify')), "
    "clock_timestamp() "
    "FROM ai_analysis a "
    "JOIN test_cases tc ON tc.id = a.test_case_id "
    "JOIN test_runs tr ON tr.id = tc.test_run_id "
    "WHERE a.test_case_id = ANY(CAST(:ids AS uuid[])) "
    "AND " + _BULK_SHA + " IS DISTINCT FROM ("
    "SELECT v.content_sha256 FROM ai_analysis_versions v WHERE v.test_case_id = a.test_case_id "
    "ORDER BY v.version DESC LIMIT 1) "
    "ON CONFLICT (test_case_id, version) DO NOTHING"
)


async def stage_bulk_reclassify_versions(
    db: Any,
    test_case_ids: Sequence[uuid.UUID],
    *,
    actor_user_id: Optional[uuid.UUID],
) -> int:
    """One version per reclassified ``ai_analysis`` row, by ``INSERT ... SELECT``.

    Runs AFTER the ``UPDATE ai_analysis`` in the same transaction, so the hash
    is over the updated row; rows whose latest version already has that hash
    are skipped. Chunks of :data:`BULK_CHUNK` ids. Pruning is left to the
    retention sweep (a reclassify adds at most one version per test). Returns
    the inserted count; a history error is logged and returns 0, the
    reclassify itself is untouched.
    """
    ids = [_uuid(i) for i in test_case_ids]
    if not ids:
        return 0
    stmt = text(BULK_RECLASSIFY_SQL).bindparams(
        bindparam("ids", type_=ARRAY(PG_UUID(as_uuid=True))),
        bindparam("actor", type_=PG_UUID(as_uuid=True)),
    )
    inserted = 0
    try:
        async with db.begin_nested():
            for start in range(0, len(ids), BULK_CHUNK):
                chunk = ids[start:start + BULK_CHUNK]
                res = await db.execute(
                    stmt, {"ids": chunk, "actor": _uuid(actor_user_id) if actor_user_id else None}
                )
                inserted += max(0, int(getattr(res, "rowcount", 0) or 0))
            await db.flush()
    except Exception as exc:  # noqa: BLE001 -- history never fails the reclassify
        logger.warning(
            "ai_history_versions_not_recorded",
            error_type=type(exc).__name__,
            count=len(ids),
            trigger="bulk_reclassify",
        )
        return 0
    return inserted


# ── The digest ───────────────────────────────────────────────────────────────


@dataclass
class _DigestContext:
    project_id: str
    build_number: Optional[str]
    report_ref: Optional[dict] = None
    max_analysis_created_at: Optional[datetime] = None


def _report_actions(decision: Mapping[str, Any]) -> list[str]:
    """Action texts of a decision report.

    ``proposed_actions[].title`` is a generic heading ("Resolve release
    condition"); the action itself is its ``rationale`` (the release condition
    / blocker text, the same strings ``conditions_for_go`` holds), so that is
    what a diff compares. Falls back to the title when there is no rationale.
    """
    out = []
    for action in decision.get("proposed_actions") or []:
        if not isinstance(action, Mapping):
            continue
        text_value = action.get("rationale") or action.get("title")
        if text_value:
            out.append(str(text_value))
    return out


async def _resolve_verdict(
    db: Any,
    mongo: Any,
    *,
    run_id: uuid.UUID,
    pipeline_run_id: Optional[uuid.UUID],
    partial: list[str],
) -> tuple[dict, list[str], Optional[str], Optional[dict]]:
    """Contract 2.3: decision report, else release_decisions, else nulls."""
    from app.models.postgres import ReleaseDecision

    report = None
    if mongo is None:
        partial.append("decision_report_unavailable")
    else:
        try:
            if pipeline_run_id is not None:
                report = await load_decision_report_for_pipeline(mongo, str(run_id), str(pipeline_run_id))
            else:
                report = await load_latest_decision_report(mongo, str(run_id))
        except Exception as exc:  # noqa: BLE001 -- degrade to the release decision
            logger.warning("ai_history_decision_report_unreadable", error_type=type(exc).__name__)
            partial.append("decision_report_unavailable")
            report = None
    report_ref = None
    if isinstance(report, Mapping):
        if report.get("report_id") and report.get("report_version"):
            report_ref = {
                "report_id": str(report["report_id"]),
                "report_version": int(report["report_version"]),
            }
        decision = report.get("decision_intelligence")
        decision = decision if isinstance(decision, Mapping) else {}
        release = decision.get("release_decision")
        release = release if isinstance(release, Mapping) else {}
        if release.get("recommendation"):
            verdict = {
                "recommendation": release.get("recommendation"),
                "risk_score": release.get("risk_score"),
                "composite_risk": release.get("composite_risk"),
                "source": "decision_report",
                "decision_report_version": report.get("report_version"),
                "overridden": False,
            }
            return verdict, _report_actions(decision), "decision_report", report_ref

    row = (
        await db.execute(select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id))
    ).scalar_one_or_none()
    if row is not None and (pipeline_run_id is None or row.pipeline_run_id == pipeline_run_id):
        verdict = {
            "recommendation": row.original_recommendation or row.recommendation,
            "risk_score": row.original_risk_score if row.original_risk_score is not None else row.risk_score,
            "composite_risk": row.composite_risk,
            "source": "release_decision",
            "decision_report_version": None,
            "overridden": bool(row.human_override),
        }
        actions = [str(x) for x in (row.conditions_for_go or []) if x]
        return verdict, actions, "release_decision", report_ref
    return {"recommendation": None, "source": None}, [], None, report_ref


async def _load_clusters(
    db: Any, *, run_id: uuid.UUID, pipeline_run_id: Optional[uuid.UUID]
) -> tuple[list[dict], dict[str, str]]:
    from app.models.postgres import FailureCluster, TestCase

    if pipeline_run_id is None:
        newest = (
            await db.execute(
                select(FailureCluster.pipeline_run_id)
                .where(FailureCluster.test_run_id == run_id)
                .order_by(FailureCluster.created_at.desc(), FailureCluster.id.desc())
                .limit(1)
            )
        ).first()
        if newest is None:
            return [], {}
        pipeline_filter = FailureCluster.pipeline_run_id.is_not_distinct_from(newest[0])
    else:
        pipeline_filter = FailureCluster.pipeline_run_id == pipeline_run_id
    rows = (
        await db.execute(
            select(
                FailureCluster.cluster_id,
                FailureCluster.label,
                FailureCluster.representative_error,
                FailureCluster.size,
                FailureCluster.member_test_ids,
            ).where(FailureCluster.test_run_id == run_id, pipeline_filter)
        )
    ).all()
    clusters = [
        {
            "cluster_id": r[0],
            "label": r[1],
            "representative_error": r[2],
            "size": r[3],
            "member_test_ids": r[4] if isinstance(r[4], list) else [],
        }
        for r in rows
    ]
    member_ids: set[uuid.UUID] = set()
    for c in clusters:
        for m in c["member_test_ids"]:
            try:
                member_ids.add(uuid.UUID(str(m)))
            except (TypeError, ValueError):
                continue
    fingerprints: dict[str, str] = {}
    ordered = sorted(member_ids)
    for start in range(0, len(ordered), _MEMBER_CHUNK):
        chunk = ordered[start:start + _MEMBER_CHUNK]
        # Scoped to THIS run: an id in the JSON that belongs to another run
        # never resolves, so it can never leak into the digest.
        for tc_id, fp in (
            await db.execute(
                select(TestCase.id, TestCase.test_fingerprint).where(
                    TestCase.id.in_(chunk), TestCase.test_run_id == run_id
                )
            )
        ).all():
            fingerprints[str(tc_id)] = fp
    return clusters, fingerprints


async def _build_digest(
    db: Any,
    mongo: Any,
    *,
    run_id: uuid.UUID,
    pipeline_run_id: Optional[uuid.UUID],
    stage_quality: Optional[str],
    summary_fallback_used: Optional[bool],
    source: str,
    captured_at: Optional[str],
) -> tuple[dict, _DigestContext]:
    from app.models.postgres import AIAnalysis, AIAnalysisVersion, TestCase, TestRun

    run_id = _uuid(run_id)
    pipeline_run_id = _uuid(pipeline_run_id) if pipeline_run_id else None
    run = (
        await db.execute(
            select(
                TestRun.id,
                TestRun.project_id,
                TestRun.build_number,
                TestRun.branch,
                TestRun.primary_suite_name,
                TestRun.total_tests,
                TestRun.failed_tests,
                TestRun.broken_tests,
                TestRun.pass_rate,
            ).where(TestRun.id == run_id)
        )
    ).first()
    if run is None:
        raise AIHistoryRunNotFound(str(run_id))
    run_map = {
        "run_id": str(run[0]),
        "project_id": str(run[1]),
        "build_number": run[2],
        "branch": run[3],
        "primary_suite_name": run[4],
        "total_tests": run[5],
        "failed_tests": run[6],
        "broken_tests": run[7],
        "pass_rate": run[8],
    }
    analysis_rows = (
        await db.execute(
            select(
                AIAnalysis.test_case_id,
                AIAnalysis.failure_category,
                AIAnalysis.confidence_score,
                AIAnalysis.root_cause_summary,
                AIAnalysis.is_flaky,
                AIAnalysis.requires_human_review,
                AIAnalysis.llm_provider,
                AIAnalysis.llm_model,
                AIAnalysis.routing_metadata,
                AIAnalysis.created_at,
                TestCase.test_fingerprint,
                TestCase.test_name,
                TestCase.suite_name,
                TestCase.status,
            )
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .where(TestCase.test_run_id == run_id)
        )
    ).all()
    analyses = []
    max_created: Optional[datetime] = None
    for r in analysis_rows:
        analyses.append({
            "test_case_id": str(r[0]),
            "failure_category": r[1],
            "confidence_score": r[2],
            "root_cause_summary": r[3],
            "is_flaky": r[4],
            "requires_human_review": r[5],
            "llm_provider": r[6],
            "llm_model": r[7],
            "routing_metadata": r[8],
            "test_fingerprint": r[10],
            "test_name": r[11],
            "suite_name": r[12],
            "status": r[13],
        })
        if r[9] is not None and (max_created is None or r[9] > max_created):
            max_created = r[9]
    clusters, fingerprints = await _load_clusters(db, run_id=run_id, pipeline_run_id=pipeline_run_id)
    partial: list[str] = []
    verdict, actions, actions_source, report_ref = await _resolve_verdict(
        db, mongo, run_id=run_id, pipeline_run_id=pipeline_run_id, partial=partial
    )
    carried_over: Optional[int] = None
    if pipeline_run_id is not None:
        covered = (
            await db.execute(
                select(func.count(func.distinct(AIAnalysisVersion.test_case_id))).where(
                    AIAnalysisVersion.test_run_id == run_id,
                    AIAnalysisVersion.pipeline_run_id == pipeline_run_id,
                )
            )
        ).scalar_one()
        carried_over = max(0, len(analyses) - int(covered or 0))
    digest = core.assemble_digest(
        run=run_map,
        analyses=analyses,
        clusters=clusters,
        fingerprint_by_test_id=fingerprints,
        verdict=verdict,
        actions=actions,
        actions_source=actions_source,
        pipeline_run_id=str(pipeline_run_id) if pipeline_run_id else None,
        source=source,
        captured_at=captured_at,
        stage_quality=stage_quality,
        summary_fallback_used=summary_fallback_used,
        carried_over_tests=carried_over,
        partial=partial,
    )
    context = _DigestContext(
        project_id=run_map["project_id"],
        build_number=run[2],
        report_ref=report_ref,
        max_analysis_created_at=max_created,
    )
    return digest, context


async def build_digest(
    db: Any,
    mongo: Any,
    *,
    run_id: uuid.UUID,
    pipeline_run_id: Optional[uuid.UUID] = None,
    stage_quality: Optional[str] = None,
    summary_fallback_used: Optional[bool] = None,
) -> dict:
    """The §2 digest of a run. ``pipeline_run_id=None`` = live (current data).

    Raises :class:`AIHistoryRunNotFound` for an unknown run. A Mongo failure
    while reading the decision report degrades to
    ``provenance.partial = ["decision_report_unavailable"]``, never an error.
    """
    stored = pipeline_run_id is not None
    digest, _ = await _build_digest(
        db,
        mongo,
        run_id=run_id,
        pipeline_run_id=pipeline_run_id,
        stage_quality=stage_quality,
        summary_fallback_used=summary_fallback_used,
        source="stored" if stored else "live",
        captured_at=_now().isoformat() if stored else None,
    )
    return digest


# ── Run-level versions (Mongo) ───────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RecordedVersion:
    version_id: str
    version: int
    created: bool
    doc: dict


def _as_recorded(doc: Mapping[str, Any], created: bool) -> RecordedVersion:
    clean = {k: v for k, v in doc.items() if k != "_id"}
    return RecordedVersion(
        version_id=str(clean.get("version_id")),
        version=int(clean.get("version") or 0),
        created=created,
        doc=clean,
    )


async def _find_idempotent(
    mongo: Any, *, test_run_id: str, kind: str, pipeline_run_id: Optional[str], sha: str
) -> Optional[dict]:
    if not pipeline_run_id:
        return None
    doc = await mongo[Collections.AI_RUN_VERSIONS].find_one(
        {
            "test_run_id": test_run_id,
            "kind": kind,
            "pipeline_run_id": pipeline_run_id,
            "content_sha256": sha,
        },
        {"_id": 0},
    )
    return doc if isinstance(doc, dict) else None


async def record_run_version(
    mongo: Any,
    *,
    test_run_id: str,
    project_id: str,
    kind: str,
    payload: dict,
    meta: dict,
    provenance: dict,
    pipeline_run_id: Optional[str],
    trigger: str = "pipeline",
    generated_at: Optional[datetime] = None,
    caps: Optional[AIHistoryCaps] = None,
) -> RecordedVersion:
    """Insert the next immutable version of ``(test_run_id, kind)``.

    The decision-report allocation pattern: idempotency check, subject lock
    (a run deleted meanwhile -> :class:`AIHistorySubjectUnavailable`, no doc),
    re-check, then up to :data:`_ALLOCATION_ATTEMPTS` ``max + 1`` attempts on
    ``DuplicateKeyError``.
    After a real insert, prunes the kind to ``caps.per_run`` when ``caps`` is
    given (best-effort). Other pymongo errors propagate: callers decide.
    """
    if kind not in core.KINDS:
        raise ValueError(f"unknown AI history kind {kind!r}")
    test_run_id = str(test_run_id)
    pipeline_key = str(pipeline_run_id) if pipeline_run_id else None
    sha = (
        core.digest_content_sha256(payload)
        if kind == core.KIND_DIGEST
        else core.summary_content_sha256(payload)
    )
    existing = await _find_idempotent(
        mongo, test_run_id=test_run_id, kind=kind, pipeline_run_id=pipeline_key, sha=sha
    )
    if existing is not None:
        return _as_recorded(existing, created=False)

    collection = mongo[Collections.AI_RUN_VERSIONS]
    recorded: Optional[RecordedVersion] = None
    try:
        async with lock_decision_report_subject(test_run_id):
            existing = await _find_idempotent(
                mongo, test_run_id=test_run_id, kind=kind, pipeline_run_id=pipeline_key, sha=sha
            )
            if existing is not None:
                return _as_recorded(existing, created=False)
            for attempt in range(_ALLOCATION_ATTEMPTS):
                previous = await collection.find_one(
                    {"test_run_id": test_run_id, "kind": kind},
                    {"_id": 0, "version": 1},
                    sort=[("version", -1)],
                )
                version = int(previous.get("version") or 0) + 1 if previous else 1
                now = _now()
                doc = {
                    "version_id": str(uuid.uuid4()),
                    "test_run_id": test_run_id,
                    "project_id": str(project_id),
                    "kind": kind,
                    "version": version,
                    "pipeline_run_id": pipeline_key,
                    "trigger": trigger,
                    "content_sha256": sha,
                    "generated_at": generated_at or now,
                    "created_at": now,
                    "schema_version": core.SCHEMA_VERSION,
                    "provenance": dict(provenance or {}),
                    "meta": dict(meta or {}),
                    "payload": payload,
                }
                try:
                    await collection.insert_one(doc)
                    recorded = _as_recorded(doc, created=True)
                    break
                except DuplicateKeyError:
                    existing = await _find_idempotent(
                        mongo, test_run_id=test_run_id, kind=kind, pipeline_run_id=pipeline_key, sha=sha
                    )
                    if existing is not None:
                        return _as_recorded(existing, created=False)
                    if attempt == _ALLOCATION_ATTEMPTS - 1:
                        raise
    except AIHistorySubjectUnavailable:
        raise
    except DecisionReportSubjectUnavailable as exc:
        raise AIHistorySubjectUnavailable(str(exc)) from exc
    if recorded is None:  # pragma: no cover - the loop either breaks, returns or raises
        raise RuntimeError("ai_history_version_retry_exhausted")
    if caps is not None:
        try:
            await prune_run_versions(mongo, test_run_id=test_run_id, kind=kind, cap=caps.per_run)
        except Exception as exc:  # noqa: BLE001 -- the sweep catches up
            logger.warning("ai_history_prune_failed", error_type=type(exc).__name__, kind=kind)
    return recorded


def run_summary_payload(source: Mapping[str, Any], *, backfill: bool = False) -> dict:
    """The §1.4 ``run_summary`` payload from a ``run_summaries`` doc (or its $set).

    A backfilled one never carries ``markdown_report``: the decision-report
    critic overwrites that field with the DECISION report's markdown, so the
    stored doc no longer holds the summary's own.
    """
    payload = {
        "executive_summary": source.get("executive_summary"),
        "markdown_report": source.get("markdown_report"),
        "layer1_executive_summary": source.get("layer1_executive_summary"),
        "layer2_incident_view": source.get("layer2_incident_view"),
        "layer3_evidence_pack": source.get("layer3_evidence_pack"),
        "layer4_action_plan": source.get("layer4_action_plan"),
        "executive_panel": source.get("executive_panel"),
        "anomaly_count": source.get("anomaly_count"),
        "is_regression": source.get("is_regression"),
        "analysis_count": source.get("analysis_count"),
        "fallback_used": bool(source.get("fallback_used")),
        "citations": source.get("citations"),
        "summary_provenance": source.get("summary_provenance"),
    }
    if backfill:
        payload["markdown_report"] = None
        payload["markdown_source"] = "overwritten_by_decision_report"
    return payload


async def record_run_summary_version(
    mongo: Any,
    *,
    test_run_id: Any,
    project_id: Any,
    pipeline_run_id: Any,
    payload: dict,
    generated_at: Optional[datetime] = None,
) -> Optional[RecordedVersion]:
    """Version a just-stored run summary. NEVER raises (the summary is already stored)."""
    try:
        if not test_run_id or not project_id:
            logger.warning("ai_history_summary_version_skipped", reason="missing_subject")
            return None
        return await record_run_version(
            mongo,
            test_run_id=str(test_run_id),
            project_id=str(project_id),
            kind=core.KIND_SUMMARY,
            payload=payload,
            meta=core.meta_from_summary(payload),
            provenance=core.provenance_from_summary(payload),
            pipeline_run_id=str(pipeline_run_id) if pipeline_run_id else None,
            trigger="pipeline",
            generated_at=generated_at,
        )
    except Exception as exc:  # noqa: BLE001 -- history never fails the summary stage
        logger.warning(
            "ai_history_summary_version_not_recorded",
            error_type=type(exc).__name__,
            test_run_id=str(test_run_id),
        )
        return None


async def prune_run_versions(mongo: Any, *, test_run_id: str, kind: str, cap: int) -> int:
    """Delete over-cap versions of one run+kind (D41). Unreadable citations -> 0."""
    cited = await cited_pipeline_ids(mongo, test_run_ids=[str(test_run_id)])
    if cited is None:
        return 0
    collection = mongo[Collections.AI_RUN_VERSIONS]
    rows = await collection.find(
        {"test_run_id": str(test_run_id), "kind": kind},
        {"_id": 0, "version_id": 1, "version": 1, "pipeline_run_id": 1},
    ).to_list(length=None)
    refs = [
        VersionRef(
            id=str(r["version_id"]),
            version=int(r.get("version") or 0),
            pipeline_run_id=r.get("pipeline_run_id"),
        )
        for r in rows
        if isinstance(r, Mapping) and r.get("version_id")
    ]
    doomed = core.plan_prune(refs, cap=cap, cited_pipeline_ids=cited)
    if not doomed:
        return 0
    result = await collection.delete_many(
        {"test_run_id": str(test_run_id), "kind": kind, "version_id": {"$in": doomed}}
    )
    return int(getattr(result, "deleted_count", 0) or 0)


@dataclass
class CaptureResult:
    version: Optional[RecordedVersion]
    previous_verdict: Optional[str]
    verdict_changed: bool
    skipped_reason: Optional[str]


async def capture_analysis_digest(
    db: Any,
    mongo: Any,
    *,
    run_id: uuid.UUID,
    pipeline_run_id: uuid.UUID,
    stage_quality: Optional[str] = None,
) -> CaptureResult:
    """Record the run's ``analysis_digest`` for a finished pipeline (W7).

    Stages ``analysis.completed`` (and ``analysis.verdict_changed`` when the
    verdict differs from the previous digest version's) on ``db`` once per NEW
    version; an idempotent replay emits nothing. The CALLER commits ``db``.
    """
    from app.services.activity.service import ActorRef, record

    run_id = _uuid(run_id)
    pipeline_key = str(_uuid(pipeline_run_id))
    collection = mongo[Collections.AI_RUN_VERSIONS]
    summary_doc = None
    try:
        summary_doc = await collection.find_one(
            {"test_run_id": str(run_id), "kind": core.KIND_SUMMARY, "pipeline_run_id": pipeline_key},
            {"_id": 0, "version": 1, "provenance": 1, "payload.fallback_used": 1},
            sort=[("version", -1)],
        )
    except Exception as exc:  # noqa: BLE001 -- the summary pairing is context, not content
        logger.warning("ai_history_summary_lookup_failed", error_type=type(exc).__name__)
    summary_version = int(summary_doc["version"]) if summary_doc and summary_doc.get("version") else None
    summary_fallback = None
    if summary_doc:
        summary_fallback = bool(((summary_doc.get("payload") or {}).get("fallback_used")))

    digest, ctx = await _build_digest(
        db,
        mongo,
        run_id=run_id,
        pipeline_run_id=_uuid(pipeline_run_id),
        stage_quality=stage_quality,
        summary_fallback_used=summary_fallback,
        source="stored",
        captured_at=_now().isoformat(),
    )
    caps = await resolve_caps(db, ctx.project_id)
    meta = core.meta_from_digest(digest)
    meta["summary_version"] = summary_version
    meta["decision_report_ref"] = ctx.report_ref
    try:
        rec = await record_run_version(
            mongo,
            test_run_id=str(run_id),
            project_id=ctx.project_id,
            kind=core.KIND_DIGEST,
            payload=digest,
            meta=meta,
            provenance=core.provenance_from_digest(digest),
            pipeline_run_id=pipeline_key,
            trigger="pipeline",
            generated_at=_now(),
            caps=caps,
        )
    except AIHistorySubjectUnavailable:
        logger.info("ai_history_capture_skipped", reason="run_deleted", test_run_id=str(run_id))
        return CaptureResult(version=None, previous_verdict=None, verdict_changed=False, skipped_reason="run_deleted")

    if not rec.created:
        return CaptureResult(version=rec, previous_verdict=None, verdict_changed=False, skipped_reason="replay")

    # The summary versions of this run are pruned with the same per-run cap
    # here: the summary agent has no Postgres session to read the policy.
    try:
        await prune_run_versions(mongo, test_run_id=str(run_id), kind=core.KIND_SUMMARY, cap=caps.per_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_history_prune_failed", error_type=type(exc).__name__, kind=core.KIND_SUMMARY)

    new_verdict = meta.get("verdict")
    previous_verdict = None
    if rec.version > 1:
        try:
            prev = await collection.find_one(
                {"test_run_id": str(run_id), "kind": core.KIND_DIGEST, "version": rec.version - 1},
                {"_id": 0, "meta.verdict": 1},
            )
            previous_verdict = ((prev or {}).get("meta") or {}).get("verdict")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_history_previous_verdict_unreadable", error_type=type(exc).__name__)
    verdict_changed = bool(previous_verdict and new_verdict and previous_verdict != new_verdict)

    label = f"Build {ctx.build_number}" if ctx.build_number else f"Run {str(run_id)[:8]}"
    actor = ActorRef.system("ai-pipeline")
    await record(
        db,
        project_id=ctx.project_id,
        event_type="analysis.completed",
        actor=actor,
        entity_id=run_id,
        entity_label=label,
        context={
            "version": rec.version,
            "verdict": new_verdict,
            "pipeline_run_id": pipeline_key,
            "analysis_mode": (digest.get("provenance") or {}).get("engine"),
            "failing_tests": digest.get("analysed_tests"),
        },
    )
    if verdict_changed:
        await record(
            db,
            project_id=ctx.project_id,
            event_type="analysis.verdict_changed",
            actor=actor,
            entity_id=run_id,
            entity_label=label,
            context={
                "from_verdict": previous_verdict,
                "to_verdict": new_verdict,
                "version": rec.version,
            },
        )
    return CaptureResult(
        version=rec,
        previous_verdict=previous_verdict,
        verdict_changed=verdict_changed,
        skipped_reason=None,
    )


# ── One-shot backfill (the Celery task is G-B's; this is one chunk) ──────────


@dataclass
class BackfillChunk:
    scanned: int = 0
    written: int = 0
    skipped: int = 0
    done: bool = False
    last_created_at: Optional[datetime] = None
    last_id: Optional[uuid.UUID] = None
    errors: list[str] = field(default_factory=list)


async def _backfill_pipeline_for(db: Any, mongo: Any, run_id: uuid.UUID) -> Optional[uuid.UUID]:
    """Newest published decision report's pipeline, else the newest completed pipeline."""
    from app.models.postgres import AgentPipelineRun
    from app.services.inline_invocation import not_inline_pipeline

    try:
        report = await load_latest_decision_report(mongo, str(run_id))
    except Exception:  # noqa: BLE001
        report = None
    if isinstance(report, Mapping) and report.get("pipeline_run_id"):
        try:
            return uuid.UUID(str(report["pipeline_run_id"]))
        except (TypeError, ValueError):
            pass
    row = (
        await db.execute(
            select(AgentPipelineRun.id)
            .where(
                AgentPipelineRun.test_run_id == run_id,
                AgentPipelineRun.status == "completed",
                # W23c I-8: an inline-input invocation never backfills the run's AI history.
                not_inline_pipeline(),
            )
            .order_by(AgentPipelineRun.completed_at.desc().nulls_last(), AgentPipelineRun.created_at.desc())
            .limit(1)
        )
    ).first()
    return row[0] if row else None


async def backfill_chunk(
    db: Any,
    mongo: Any,
    cursor: Optional[tuple[datetime, uuid.UUID]],
    limit: int = 100,
) -> BackfillChunk:
    """Version up to ``limit`` existing runs, ordered ``(created_at, id)`` after ``cursor``.

    Per run: a v1 ``run_summary`` from its ``run_summaries`` doc and a v1
    ``analysis_digest`` of its current data, both ``trigger="backfill"``, each
    only when the run has no version of that kind yet. A version a live
    pipeline wrote meanwhile (duplicate key) or a run deleted meanwhile is a
    skip. Reads Postgres only; the task commits nothing on its behalf.
    """
    from app.models.postgres import AIAnalysis, TestCase, TestRun

    out = BackfillChunk()
    query = select(TestRun.id, TestRun.project_id, TestRun.created_at).order_by(
        TestRun.created_at, TestRun.id
    )
    if cursor is not None:
        after = tuple_(literal(cursor[0], TestRun.created_at.type), literal(cursor[1], TestRun.id.type))
        query = query.where(tuple_(TestRun.created_at, TestRun.id) > after)
    runs = (await db.execute(query.limit(max(1, int(limit))))).all()
    out.done = len(runs) < max(1, int(limit))
    versions = mongo[Collections.AI_RUN_VERSIONS]
    for run_id, project_id, created_at in runs:
        out.scanned += 1
        out.last_created_at, out.last_id = created_at, run_id
        rid = str(run_id)
        # (a) run_summary
        try:
            has_summary = await versions.find_one({"test_run_id": rid, "kind": core.KIND_SUMMARY}, {"_id": 1})
            if has_summary is None:
                doc = await mongo[Collections.RUN_SUMMARIES].find_one({"test_run_id": rid}, {"_id": 0})
                if doc:
                    payload = run_summary_payload(doc, backfill=True)
                    rec = await record_run_version(
                        mongo,
                        test_run_id=rid,
                        project_id=str(project_id),
                        kind=core.KIND_SUMMARY,
                        payload=payload,
                        meta=core.meta_from_summary(payload),
                        provenance=core.provenance_from_summary(payload),
                        pipeline_run_id=None,
                        trigger="backfill",
                        generated_at=doc.get("generated_at") if isinstance(doc.get("generated_at"), datetime) else None,
                    )
                    out.written += 1 if rec.created else 0
                    out.skipped += 0 if rec.created else 1
        except (DuplicateKeyError, AIHistorySubjectUnavailable):
            out.skipped += 1
        except Exception as exc:  # noqa: BLE001 -- one bad run never stops the chunk
            out.errors.append(f"{rid}:summary:{type(exc).__name__}")
        # (b) analysis_digest
        try:
            has_digest = await versions.find_one({"test_run_id": rid, "kind": core.KIND_DIGEST}, {"_id": 1})
            if has_digest is not None:
                continue
            analysed = (
                await db.execute(
                    select(func.count())
                    .select_from(AIAnalysis)
                    .join(TestCase, TestCase.id == AIAnalysis.test_case_id)
                    .where(TestCase.test_run_id == run_id)
                )
            ).scalar_one()
            report = None
            try:
                report = await load_latest_decision_report(mongo, rid)
            except Exception:  # noqa: BLE001
                report = None
            if not analysed and not report:
                continue
            pipeline = await _backfill_pipeline_for(db, mongo, run_id)
            digest, ctx = await _build_digest(
                db,
                mongo,
                run_id=run_id,
                pipeline_run_id=pipeline,
                stage_quality=None,
                summary_fallback_used=None,
                source="stored",
                captured_at=_now().isoformat(),
            )
            meta = core.meta_from_digest(digest)
            meta["decision_report_ref"] = ctx.report_ref
            rec = await record_run_version(
                mongo,
                test_run_id=rid,
                project_id=str(project_id),
                kind=core.KIND_DIGEST,
                payload=digest,
                meta=meta,
                provenance=core.provenance_from_digest(digest),
                pipeline_run_id=str(pipeline) if pipeline else None,
                trigger="backfill",
                generated_at=ctx.max_analysis_created_at,
            )
            out.written += 1 if rec.created else 0
            out.skipped += 0 if rec.created else 1
        except (DuplicateKeyError, AIHistorySubjectUnavailable, AIHistoryRunNotFound):
            out.skipped += 1
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"{rid}:digest:{type(exc).__name__}")
    return out
