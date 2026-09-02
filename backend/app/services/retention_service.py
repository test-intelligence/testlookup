"""Per-project data retention policies + cross-store purge (PMF US-11.4).

Four retention classes, each with its own clock (days before "now"):

======================  =====================================================
class                   what it covers
======================  =====================================================
``raw_events_days``     ``TestRun.event_archive`` JSONB strip; Mongo
                        ``live_execution_events`` (keyed by run_id — TestRun
                        UUID string or LiveSession slug), ``raw_allure_json``
                        (keyed by test_run_id), ``raw_testng_xml`` +
                        ``rest_api_payloads`` (keyed by test_case_id).
``runs_days``           ``test_runs`` rows — a plain DELETE; every child is
                        ``ondelete="CASCADE"`` (22 direct children plus the
                        second hop via test_cases; zero RESTRICT). Run-scoped
                        Mongo docs (``execution_logs``, ``ocp_pod_events``,
                        ``run_summaries`` by test_run_id;
                        ``ai_analysis_payloads`` by test_case_id).
``artifacts_days``      revoked ``report_share_links`` (dead on revoke, but the row
                        only ever died via the run CASCADE), and
                        MinIO objects: ``TestRun.minio_prefix`` +
                        ``uploads/{project_id}/{run_id}/`` prefixes, and
                        pipeline artifacts plus verified ``EvidenceArtifact``
                        metadata/content excerpts
                        (``pipeline/{Y}/{m}/{d}/{pipeline_run_id}/…``).
``audit_days``          ``access_audit_logs`` + ``test_case_audit_logs``
                        + Mongo ``decision_reports`` and ``decision_report_attempts``
                        (keyed on ``test_run_id`` — a report is evidence ABOUT a
                        run and must OUTLIVE it, so it rides the audit clock,
                        never the runs clock)
                        + terminal ``deletion_jobs``
                        (project-scoped), ``ai_provenance_records`` (via the
                        0113 project_id column — survives run deletion),
                        Mongo ``pipeline_event_log`` (by pipeline_run_ids
                        materialized BEFORE the run delete), and expired
                        ``compliance_packs`` (rows past their own
                        ``retention_expires_at`` + their MinIO ZIPs).
======================  =====================================================

NEVER purged: ``settings_audit_log`` (it has no project scope and holds the
purge-audit records themselves — ``setting_key = "retention_purge:{id}"``),
and compliance packs before their ``retention_expires_at``.

Purge order (the ordering trap): the Postgres CASCADE destroys the ONLY
mapping from runs to Mongo docs / MinIO keys, so per project we

1. materialize test-case ids, pipeline-run ids, live-session slugs and
   MinIO prefixes from Postgres,
2. delete Mongo docs (batched ``$in``, chunks ≤ 500),
3. delete MinIO objects,
4. delete the test_runs rows (CASCADE),
5. strip expired ``event_archive`` payloads,
6. run the audit-class deletes.

The three stores are NOT transactional together — the job is re-entrant
instead: every candidate set is a pure function of (policy, now), so a crash
mid-way leaves a state where simply re-running finds the remainder. All
storage/Mongo deletes are idempotent; the audit row records per-store counts.

Known v1 cost/limits (documented on purpose):

* Pipeline artifacts live under date prefixes (``pipeline/Y/m/d/<id>/``), so
  the purge lists the ``pipeline/`` prefix lazily ONLY when the purged runs
  actually have pipeline runs, then filters keys by pipeline_run_id — an
  O(bucket-listing) pass, acceptable at self-host scale.
* ``pipeline_event_log`` docs are deleted via pipeline_run_ids that still
  resolve at purge time. Events whose pipeline run was cascaded away by an
  earlier sweep (runs clock < audit clock) are unreachable and stay in Mongo.

Transaction ratchet: this service NEVER commits — it stages Postgres deletes
on the injected session and returns; the caller (router for preview, the
Celery worker task for execute) owns the commit. The purge-audit row is
written by the worker AFTER the purge commit, on its own session (the
``flaky_quarantine_service`` audit-after-commit pattern).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Optional

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mongo import Collections, get_mongo_db
from app.db.storage import get_storage_provider
from app.models.postgres import (
    AccessAuditLog,
    AgentPipelineRun,
    AgentMemoryEntry,
    AIProvenanceRecord,
    CompliancePack,
    DeletionJob,
    EvidenceArtifact,
    LiveSession,
    Project,
    ProjectRetentionPolicy,
    ReportShareLink,
    SettingsAuditLog,
    TestCase,
    TestCaseAuditLog,
    TestRun,
)
from app.services import deletion_job_service

logger = structlog.get_logger(__name__)

# ── Defaults + bounds ────────────────────────────────────────────────────────

DEFAULT_RAW_EVENTS_DAYS = 90
DEFAULT_RUNS_DAYS = 365
DEFAULT_ARTIFACTS_DAYS = 180
DEFAULT_AUDIT_DAYS = 2555  # ≈ 7 years — the compliance-pack retention floor

# (min_days, max_days) — mirrored by the Pydantic schema; the service
# re-checks on MERGED values so a partial PUT can't sneak past the floor.
FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "raw_events_days": (7, 3650),
    "runs_days": (30, 3650),
    "artifacts_days": (7, 3650),
    "audit_days": (365, 3650),
}

def deletion_job_purge_filter(project_id: uuid.UUID, cutoff: datetime) -> tuple:
    """WHERE clause for retiring `deletion_jobs` on the audit clock.

    Module-level so tests compile the predicate the purge actually uses. A test
    that rebuilt an equivalent clause would keep passing after the real one
    lost its project scope.

    Only TERMINAL jobs are in scope: a row still marked ``running`` past the
    window is a sweep that hung, and deleting it on a clock would erase the
    only evidence that it did.
    """
    return (
        DeletionJob.project_id == project_id,
        DeletionJob.requested_at < cutoff,
        DeletionJob.status.in_(sorted(deletion_job_service.TERMINAL_STATUSES)),
    )


def _sum_measured(values: Iterable[Optional[int]]) -> Optional[int]:
    """Total a group of per-store counts, or None if any store was unreachable.

    A partial total is worse than no total: "3 cache entries" when the semantic
    half never answered reads as a complete figure and is not one. If any
    contributor is unmeasured the aggregate is unmeasured, and the caller shows
    "not measured" rather than a number that quietly under-reports.
    """
    materialized = list(values)
    if any(v is None for v in materialized):
        return None
    return sum(v for v in materialized if v is not None)


PURGE_AUDIT_KEY_PREFIX = "retention_purge:"
POLICY_AUDIT_KEY_PREFIX = "retention_policy:"

_MONGO_CHUNK = 500
_PG_DELETE_CHUNK = 1000

_UPLOADS_BUCKET = None  # default bucket (settings.MINIO_BUCKET_NAME)
_PIPELINE_BUCKET = "pipeline-artifacts"
_PIPELINE_PREFIX = "pipeline/"
_COMPLIANCE_BUCKET = "compliance-packs"
_PIPELINE_EVENT_LOG = "pipeline_event_log"


class RetentionValidationError(Exception):
    """Raised when a policy write violates bounds or cross-field rules."""


class ProjectNotFound(Exception):
    """Raised when the project_id doesn't resolve to an active project."""


class ConfirmationMismatch(Exception):
    """Raised when the typed confirmation_name doesn't match the project name."""


class PolicyDisabled(Exception):
    """Raised when an execute-mode purge is requested for a disabled policy."""


@dataclass(frozen=True)
class EffectiveRetentionPolicy:
    """Resolved per-project retention policy (missing row → defaults)."""
    enabled: bool = False
    raw_events_days: int = DEFAULT_RAW_EVENTS_DAYS
    runs_days: int = DEFAULT_RUNS_DAYS
    artifacts_days: int = DEFAULT_ARTIFACTS_DAYS
    audit_days: int = DEFAULT_AUDIT_DAYS
    source: str = "default"  # "default" | "custom"

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "raw_events_days": self.raw_events_days,
            "runs_days": self.runs_days,
            "artifacts_days": self.artifacts_days,
            "audit_days": self.audit_days,
        }


# ── Policy resolution + upsert ───────────────────────────────────────────────


async def get_policy_row(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[ProjectRetentionPolicy]:
    """The explicit policy row for a project, or ``None`` (= code defaults
    apply, purge disabled). Read-only."""
    result = await db.execute(
        select(ProjectRetentionPolicy).where(
            ProjectRetentionPolicy.project_id == project_id
        )
    )
    return result.scalar_one_or_none()


async def get_effective_policy(
    db: AsyncSession, project_id: uuid.UUID,
) -> EffectiveRetentionPolicy:
    """Load the project's policy, resolving a missing row to the defaults."""
    row = await get_policy_row(db, project_id)
    if row is None:
        return EffectiveRetentionPolicy()
    return EffectiveRetentionPolicy(
        enabled=bool(row.enabled),
        raw_events_days=int(row.raw_events_days or DEFAULT_RAW_EVENTS_DAYS),
        runs_days=int(row.runs_days or DEFAULT_RUNS_DAYS),
        artifacts_days=int(row.artifacts_days or DEFAULT_ARTIFACTS_DAYS),
        audit_days=int(row.audit_days or DEFAULT_AUDIT_DAYS),
        source="custom",
    )


def _validate_merged(merged: dict[str, int]) -> None:
    """Bounds + cross-field checks on the MERGED (effective) values."""
    for field, (lo, hi) in FIELD_BOUNDS.items():
        value = merged[field]
        if not (lo <= value <= hi):
            raise RetentionValidationError(
                f"{field} must be between {lo} and {hi} days (got {value})"
            )
    if merged["audit_days"] < merged["runs_days"]:
        raise RetentionValidationError(
            "audit_days must be >= runs_days — audit records must outlive "
            f"the runs they describe (got audit_days={merged['audit_days']}, "
            f"runs_days={merged['runs_days']})"
        )


async def upsert_policy(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    actor_name: Optional[str] = None,
    enabled: Optional[bool] = None,
    raw_events_days: Optional[int] = None,
    runs_days: Optional[int] = None,
    artifacts_days: Optional[int] = None,
    audit_days: Optional[int] = None,
) -> EffectiveRetentionPolicy:
    """Upsert the project's policy row from a PARTIAL update.

    Omitted (``None``) fields keep their current (or default) value.
    Validates bounds AND the audit≥runs cross-check on the merged result —
    raises :class:`RetentionValidationError` (router maps to 422).

    Stages only (``flush``) — the router session owns the commit. A
    ``settings_audit_log`` row records the change (``changed_fields`` is a
    dict by house convention despite the ``list`` annotation).
    """
    row = await get_policy_row(db, project_id)
    current = {
        "raw_events_days": int(row.raw_events_days) if row else DEFAULT_RAW_EVENTS_DAYS,
        "runs_days": int(row.runs_days) if row else DEFAULT_RUNS_DAYS,
        "artifacts_days": int(row.artifacts_days) if row else DEFAULT_ARTIFACTS_DAYS,
        "audit_days": int(row.audit_days) if row else DEFAULT_AUDIT_DAYS,
    }
    provided = {
        "raw_events_days": raw_events_days,
        "runs_days": runs_days,
        "artifacts_days": artifacts_days,
        "audit_days": audit_days,
    }
    merged = {
        field: (value if value is not None else current[field])
        for field, value in provided.items()
    }
    _validate_merged(merged)

    changed: dict[str, Any] = {}
    if row is None:
        row = ProjectRetentionPolicy(
            project_id=project_id,
            enabled=bool(enabled) if enabled is not None else False,
            updated_by_user_id=actor_id,
            **merged,
        )
        db.add(row)
        action = "created"
        changed = {"enabled": row.enabled, **merged}
    else:
        action = "updated"
        if enabled is not None and bool(enabled) != bool(row.enabled):
            changed["enabled"] = {"old": bool(row.enabled), "new": bool(enabled)}
            row.enabled = bool(enabled)
        for field, new_value in merged.items():
            if new_value != current[field]:
                changed[field] = {"old": current[field], "new": new_value}
                setattr(row, field, new_value)
        row.updated_by_user_id = actor_id

    db.add(
        SettingsAuditLog(
            setting_key=f"{POLICY_AUDIT_KEY_PREFIX}{project_id}",
            action=action,
            actor_id=actor_id,
            actor_name=actor_name,
            changed_fields={
                "project_id": str(project_id),
                "changes": changed,
            },
        )
    )
    await db.flush()

    logger.info(
        "retention_policy_upserted",
        project_id=str(project_id),
        action=action,
        changed=changed,
        actor_id=str(actor_id) if actor_id else None,
    )
    return await get_effective_policy(db, project_id)


async def get_last_purge(
    db: AsyncSession, project_id: uuid.UUID,
) -> Optional[dict]:
    """Latest execute-mode purge for the project, parsed from the
    ``settings_audit_log`` purge-audit rows. Read-only."""
    rows = (
        await db.execute(
            select(SettingsAuditLog)
            .where(
                SettingsAuditLog.setting_key
                == f"{PURGE_AUDIT_KEY_PREFIX}{project_id}"
            )
            .order_by(SettingsAuditLog.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    for row in rows:
        fields = row.changed_fields or {}
        if isinstance(fields, dict) and fields.get("mode") == "execute":
            return {
                "at": row.created_at,
                "mode": "execute",
                "counts": fields.get("counts") or {},
            }
    return None


async def validate_purge_request(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    confirmation_name: str,
) -> Project:
    """Gate for the manual-purge endpoint.

    Raises :class:`ProjectNotFound` (→404), :class:`ConfirmationMismatch`
    (→422 — the typed name must equal the project name EXACTLY, the
    ``project_reset_service`` convention), or :class:`PolicyDisabled`
    (→409 — execute-mode purges only run for opted-in projects).
    """
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None or not project.is_active:
        raise ProjectNotFound(f"Project {project_id} not found or inactive")
    if confirmation_name != project.name:
        raise ConfirmationMismatch(
            "confirmation_name must match the project name exactly"
        )
    policy = await get_effective_policy(db, project_id)
    if not policy.enabled:
        raise PolicyDisabled(
            "Retention policy is not enabled for this project — enable it "
            "(or use preview) first"
        )
    return project


# ── Purge engine ─────────────────────────────────────────────────────────────


def compute_cutoffs(
    policy: EffectiveRetentionPolicy, now: datetime,
) -> dict[str, datetime]:
    """Per-class cutoff timestamps: data OLDER than the cutoff is purgeable."""
    return {
        "raw_events": now - timedelta(days=policy.raw_events_days),
        "runs": now - timedelta(days=policy.runs_days),
        "artifacts": now - timedelta(days=policy.artifacts_days),
        "audit": now - timedelta(days=policy.audit_days),
    }


def _chunks(seq: list, size: int) -> Iterator[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


async def _mongo_count(mongo, collection: str, field: str, values: list[str]) -> int:
    total = 0
    for chunk in _chunks(values, _MONGO_CHUNK):
        total += int(
            await mongo[collection].count_documents({field: {"$in": chunk}})
        )
    return total


async def _mongo_delete(mongo, collection: str, field: str, values: list[str]) -> int:
    deleted = 0
    for chunk in _chunks(values, _MONGO_CHUNK):
        result = await mongo[collection].delete_many({field: {"$in": chunk}})
        deleted += int(getattr(result, "deleted_count", 0) or 0)
    return deleted


def _pipeline_artifact_keys(
    objects: Iterable[dict], pipeline_ids: set[str],
) -> list[str]:
    """Filter ``pipeline/Y/m/d/<pipeline_run_id>/...`` keys by pipeline id.

    Segment-wise match (not substring) so an id can never partially match.
    """
    keys: list[str] = []
    for obj in objects:
        key = obj.get("Key") or ""
        if any(segment in pipeline_ids for segment in key.split("/")):
            keys.append(key)
    return keys


async def _published_report_artifact_ids(
    mongo: Any,
    project_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    """Return candidate artifacts still referenced by published reports.

    Decision reports are immutable and may outlive the raw run.  Retention
    must not delete an artifact that a published report exposes through its
    signed evidence references.  The production Mongo collection supports
    ``find``/``to_list``; intentionally minimal test doubles without that API
    are treated as having no report references.
    """
    if not candidate_ids:
        return set()
    collection = mongo[Collections.DECISION_REPORTS]
    find = getattr(collection, "find", None)
    if find is None:
        return set()
    candidate_set = {str(item) for item in candidate_ids}
    protected: set[uuid.UUID] = set()
    try:
        cursor = find(
            {"project_id": str(project_id), "status": "published"},
            {"_id": 0, "decision_intelligence": 1, "verification": 1},
        )
        reports = await cursor.to_list(length=10_000)
        stack: list[Any] = list(reports if isinstance(reports, list) else [])
        visited = 0
        while stack and len(protected) < 500:
            value = stack.pop()
            visited += 1
            if visited > 100_000:
                break
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"artifact_id", "evidence_id"} and str(item) in candidate_set:
                        protected.add(uuid.UUID(str(item)))
                    elif key == "evidence_ids" and isinstance(item, list):
                        for evidence_id in item:
                            if str(evidence_id) in candidate_set:
                                protected.add(uuid.UUID(str(evidence_id)))
                    elif isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(value, list):
                stack.extend(value)
    except Exception as exc:  # fail closed for artifact deletion
        logger.warning(
            "retention_report_reference_scan_failed",
            error_type=type(exc).__name__,
        )
        return set(candidate_ids)
    return protected


@dataclass
class _ExecutionPlan:
    """The non-run-scoped filters the execute phase needs.

    ``_Candidates`` holds what is derived FROM runs. These eight are not: seven
    never reference a run at all, and ``event_archive_where`` keys on a
    different column (``event_archive_at``) from the one the runs clock uses.
    They come from four independent clocks, which is exactly why a single
    ``older_than_days`` criterion could never express the policy purge — and why
    the executor takes them as data rather than closing over them.
    """

    event_archive_where: tuple
    access_audit_where: tuple
    tc_audit_where: tuple
    deletion_job_where: tuple
    revoked_share_link_where: tuple
    provenance_where: tuple
    memory_expired_ids: list
    expired_packs: list


@dataclass
class _Candidates:
    """Everything materialized from Postgres BEFORE any delete (the
    ordering trap: the CASCADE destroys the only run→doc/key mapping)."""
    # runs clock — rows to DELETE
    purge_run_ids: list[uuid.UUID]
    purge_run_strs: list[str]
    purge_tc_strs: list[str]
    # raw-events clock — runs that still exist but whose raw events expire
    raw_run_strs: list[str]
    raw_tc_strs: list[str]
    raw_live_run_keys: list[str]      # run uuids + live-session slugs
    # artifacts clock (∪ runs clock — a deleted run's objects must die too)
    artifact_prefixes: list[str]
    artifact_pipeline_strs: set[str]
    evidence_artifact_ids: list[uuid.UUID]
    # audit clock
    audit_pipeline_strs: list[str]
    #: Runs past the AUDIT cutoff — a strictly wider window than the runs
    #: clock, because ``audit_days >= runs_days`` is an enforced invariant.
    #: Decision reports key on this: a report is evidence ABOUT a run and
    #: outlives it, so purging it on the runs clock would destroy the evidence
    #: at the moment its subject went.
    audit_run_strs: list[str]


async def _collect_candidates(
    db: AsyncSession,
    project_id: uuid.UUID,
    cutoffs: dict[str, datetime],
) -> _Candidates:
    # One pass over test_runs older than the NEWEST cutoff (superset of every
    # class), classified in Python against each clock.
    newest_cutoff = max(cutoffs.values())
    artifact_boundary = max(cutoffs["artifacts"], cutoffs["runs"])

    run_rows = (
        await db.execute(
            select(TestRun.id, TestRun.minio_prefix, TestRun.created_at).where(
                TestRun.project_id == project_id,
                TestRun.created_at < newest_cutoff,
            )
        )
    ).all()

    purge_run_ids: list[uuid.UUID] = []
    raw_run_ids: list[uuid.UUID] = []
    audit_run_ids: list[uuid.UUID] = []
    artifact_run_ids: list[uuid.UUID] = []
    artifact_prefixes: set[str] = set()
    for run_id, minio_prefix, created_at in run_rows:
        if created_at < cutoffs["runs"]:
            purge_run_ids.append(run_id)
        if created_at < cutoffs["raw_events"]:
            raw_run_ids.append(run_id)
        if created_at < cutoffs["audit"]:
            audit_run_ids.append(run_id)
        if created_at < artifact_boundary:
            artifact_run_ids.append(run_id)
            artifact_prefixes.add(f"uploads/{project_id}/{run_id}/")
            if minio_prefix:
                artifact_prefixes.add(minio_prefix)

    # test-case ids for the Mongo collections keyed by test_case_id.
    tc_scope_ids = list({*purge_run_ids, *raw_run_ids})
    purge_tc_strs: list[str] = []
    raw_tc_strs: list[str] = []
    if tc_scope_ids:
        purge_set = set(purge_run_ids)
        raw_set = set(raw_run_ids)
        for chunk in _chunks(tc_scope_ids, _PG_DELETE_CHUNK):
            tc_rows = (
                await db.execute(
                    select(TestCase.id, TestCase.test_run_id).where(
                        TestCase.test_run_id.in_(chunk)
                    )
                )
            ).all()
            for tc_id, tr_id in tc_rows:
                if tr_id in purge_set:
                    purge_tc_strs.append(str(tc_id))
                if tr_id in raw_set:
                    raw_tc_strs.append(str(tc_id))

    # pipeline-run ids — artifacts clock for MinIO keys, audit clock for the
    # immutable pipeline_event_log stream. Materialized BEFORE the cascade.
    pipeline_scope_ids = list({*artifact_run_ids, *audit_run_ids})
    artifact_pipeline_strs: set[str] = set()
    audit_pipeline_strs: list[str] = []
    if pipeline_scope_ids:
        artifact_set = set(artifact_run_ids)
        audit_set = set(audit_run_ids)
        for chunk in _chunks(pipeline_scope_ids, _PG_DELETE_CHUNK):
            pipe_rows = (
                await db.execute(
                    select(AgentPipelineRun.id, AgentPipelineRun.test_run_id).where(
                        AgentPipelineRun.test_run_id.in_(chunk)
                    )
                )
            ).all()
            for pipe_id, tr_id in pipe_rows:
                if tr_id in artifact_set:
                    artifact_pipeline_strs.add(str(pipe_id))
                if tr_id in audit_set:
                    audit_pipeline_strs.append(str(pipe_id))

    # live_execution_events are written with the CLIENT's run_id — a
    # LiveSession slug on the webhook path, a TestRun UUID string elsewhere.
    # Cover both key shapes.
    live_slugs = (
        await db.execute(
            select(LiveSession.run_id).where(
                LiveSession.project_id == project_id,
                LiveSession.started_at < cutoffs["raw_events"],
            )
        )
    ).scalars().all()
    raw_run_strs = [str(r) for r in raw_run_ids]
    raw_live_run_keys = list({*raw_run_strs, *[s for s in live_slugs if s]})
    evidence_artifact_ids = list((
        await db.execute(
            select(EvidenceArtifact.id).where(
                EvidenceArtifact.project_id == project_id,
                EvidenceArtifact.created_at < cutoffs["artifacts"],
            )
        )
    ).scalars().all())

    return _Candidates(
        purge_run_ids=purge_run_ids,
        purge_run_strs=[str(r) for r in purge_run_ids],
        purge_tc_strs=purge_tc_strs,
        raw_run_strs=raw_run_strs,
        raw_tc_strs=raw_tc_strs,
        raw_live_run_keys=raw_live_run_keys,
        artifact_prefixes=sorted(artifact_prefixes),
        artifact_pipeline_strs=artifact_pipeline_strs,
        evidence_artifact_ids=evidence_artifact_ids,
        audit_pipeline_strs=audit_pipeline_strs,
        audit_run_strs=[str(r) for r in audit_run_ids],
    )


# (collection, key field, candidate attribute) — raw-events + runs classes.
# raw_allure_json docs carry test_run_id (the ingestion writer always sets
# it); raw_testng_xml / rest_api_payloads / ai_analysis_payloads are keyed
# only by test_case_id.
_MONGO_PLANS: tuple[tuple[str, str, str], ...] = (
    (Collections.RAW_ALLURE_JSON, "test_run_id", "raw_run_strs"),
    (Collections.RAW_TESTNG_XML, "test_case_id", "raw_tc_strs"),
    (Collections.REST_API_PAYLOADS, "test_case_id", "raw_tc_strs"),
    (Collections.LIVE_EXECUTION_EVENTS, "run_id", "raw_live_run_keys"),
    (Collections.EXECUTION_LOGS, "test_run_id", "purge_run_strs"),
    (Collections.OCP_POD_EVENTS, "test_run_id", "purge_run_strs"),
    (Collections.RUN_SUMMARIES, "test_run_id", "purge_run_strs"),
    (Collections.DECISION_EVIDENCE_SNAPSHOTS, "test_run_id", "purge_run_strs"),
    (Collections.AI_ANALYSIS_PAYLOADS, "test_case_id", "purge_tc_strs"),
    (_PIPELINE_EVENT_LOG, "pipeline_run_id", "audit_pipeline_strs"),
    # S4 — the only two Collections members that were absent from this table,
    # so nothing had ever purged a decision report. On the AUDIT clock, not
    # the runs clock: a report is evidence about a run and must outlive it.
    (Collections.DECISION_REPORTS, "test_run_id", "audit_run_strs"),
    (Collections.DECISION_REPORT_ATTEMPTS, "test_run_id", "audit_run_strs"),
)


async def run_purge(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    mode: str,
    now: Optional[datetime] = None,
    mongo=None,
    storage=None,
) -> dict:
    """Run the retention purge for one project.

    ``mode="preview"`` — read-only: returns per-class cutoffs and candidate
    counts; writes NOTHING anywhere (no deletes, no audit row). Works for
    disabled policies — preview is how admins decide whether to enable.

    ``mode="execute"`` — performs the deletes in the pinned order (Mongo →
    MinIO → Postgres runs → event_archive strip → audit class) and returns
    per-store counts. Stages Postgres deletes on the injected session and
    NEVER commits (caller owns the transaction; Mongo/MinIO deletes are
    inherently non-transactional — see the module docstring on re-entrancy).

    ``mongo`` / ``storage`` are injectable for tests; production callers
    leave them ``None``.
    """
    if mode not in ("preview", "execute"):
        raise ValueError(f"Unknown purge mode: {mode!r}")

    now = now or datetime.now(timezone.utc)
    external_stores_injected = mongo is not None or storage is not None
    policy = await get_effective_policy(db, project_id)
    cutoffs = compute_cutoffs(policy, now)
    mongo = mongo if mongo is not None else get_mongo_db()
    storage = storage if storage is not None else get_storage_provider()

    # ── (1) Materialize BEFORE any delete ────────────────────────────────
    cand = await _collect_candidates(db, project_id, cutoffs)
    protected_artifacts = await _published_report_artifact_ids(
        mongo,
        project_id,
        cand.evidence_artifact_ids,
    )
    if protected_artifacts:
        cand.evidence_artifact_ids = [
            artifact_id
            for artifact_id in cand.evidence_artifact_ids
            if artifact_id not in protected_artifacts
        ]

    # Shared Postgres-side candidate counts / filters.
    event_archive_where = (
        TestRun.project_id == project_id,
        TestRun.event_archive_at < cutoffs["raw_events"],
        TestRun.event_archive.is_not(None),
    )
    access_audit_where = (
        AccessAuditLog.project_id == project_id,
        AccessAuditLog.created_at < cutoffs["audit"],
    )
    tc_audit_where = (
        TestCaseAuditLog.project_id == project_id,
        TestCaseAuditLog.created_at < cutoffs["audit"],
    )
    provenance_where = (
        AIProvenanceRecord.project_id == project_id,
        AIProvenanceRecord.created_at < cutoffs["audit"],
    )
    deletion_job_where = deletion_job_purge_filter(project_id, cutoffs["audit"])
    # S4 — a revoked share link is dead the moment it is revoked, but the row
    # only ever died via the run CASCADE, so a revoked link on a run that is
    # still inside its window lingered forever. Artifacts clock: the link is a
    # pointer to a report, not evidence about it.
    revoked_share_link_where = (
        ReportShareLink.project_id == project_id,
        ReportShareLink.is_revoked.is_(True),
        ReportShareLink.created_at < cutoffs["artifacts"],
    )
    memory_expired_where = (
        AgentMemoryEntry.project_id == project_id,
        AgentMemoryEntry.lifecycle_status == "active",
        AgentMemoryEntry.expires_at.is_not(None),
        AgentMemoryEntry.expires_at <= now,
    )
    memory_expired_ids = list(
        (
            await db.execute(
                select(AgentMemoryEntry.id).where(*memory_expired_where)
            )
        ).scalars().all()
    )

    expired_packs = (
        await db.execute(
            select(CompliancePack).where(
                CompliancePack.project_id == project_id,
                CompliancePack.retention_expires_at < now,
            )
        )
    ).scalars().all()

    plan = _ExecutionPlan(
        event_archive_where=event_archive_where,
        access_audit_where=access_audit_where,
        tc_audit_where=tc_audit_where,
        deletion_job_where=deletion_job_where,
        revoked_share_link_where=revoked_share_link_where,
        provenance_where=provenance_where,
        memory_expired_ids=memory_expired_ids,
        expired_packs=expired_packs,
    )

    cutoffs_iso = {k: v.isoformat() for k, v in cutoffs.items()}
    # None, not 0 — these stores are SKIPPED entirely when external stores are
    # injected (the test path), so a zero here would report "nothing in the
    # cache" for a store the run never visited. Same rule as an outage.
    cache_counts: dict[str, int | None] = {"redis": None, "semantic": None}
    # SEARCH-009. Distinct from ``cache_counts["semantic"]``, which is the AI
    # ANALYSIS cache (``semantic_cache``, the ``ai_analysis_cache_*``
    # collections). This is the test-case SEARCH index (``test_case_search``) —
    # a different store that the "cross-store purge" never visited, so an
    # executed purge reported complete while 98.8% of that index was
    # embeddings of deleted projects.
    search_index_documents: int | None = None
    if not external_stores_injected:
        from app.services.analysis_cache_retention import (
            purge_project_analysis_caches,
        )
        from app.services.semantic_search import purge_project_documents

        cache_counts = await purge_project_analysis_caches(
            str(project_id),
            cutoff=max(cutoffs["raw_events"], cutoffs["artifacts"]),
            execute=mode == "execute",
        )
        search_index_documents = await purge_project_documents(
            str(project_id), execute=mode == "execute",
        )

    if mode == "preview":
        mongo_docs: dict[str, int] = {}
        for collection, field, attr in _MONGO_PLANS:
            mongo_docs[collection] = await _mongo_count(
                mongo, collection, field, list(getattr(cand, attr))
            )

        minio_objects = 0
        for prefix in cand.artifact_prefixes:
            minio_objects += len(
                await storage.list_objects(prefix, bucket=_UPLOADS_BUCKET)
            )
        if cand.artifact_pipeline_strs:
            pipeline_objects = await storage.list_objects(
                _PIPELINE_PREFIX, bucket=_PIPELINE_BUCKET
            )
            minio_objects += len(
                _pipeline_artifact_keys(
                    pipeline_objects, cand.artifact_pipeline_strs
                )
            )
        # Expired compliance-pack ZIPs are reported under their own key
        # (compliance_packs_expired), not folded into minio_objects.

        async def _count(model_id_col, where) -> int:
            return int(
                (
                    await db.execute(
                        select(func.count(model_id_col)).where(*where)
                    )
                ).scalar_one()
                or 0
            )

        candidates = {
            "runs": len(cand.purge_run_ids),
            "test_cases": len(cand.purge_tc_strs),
            "mongo_docs": mongo_docs,
            "minio_objects": minio_objects,
            "evidence_artifact_rows": len(cand.evidence_artifact_ids),
            "event_archive_rows": await _count(TestRun.id, event_archive_where),
            "audit_rows": (
                await _count(AccessAuditLog.id, access_audit_where)
                + await _count(TestCaseAuditLog.id, tc_audit_where)
                + await _count(DeletionJob.id, deletion_job_where)
            ),
            "revoked_share_links": await _count(
                ReportShareLink.id, revoked_share_link_where
            ),
            "provenance_rows": await _count(AIProvenanceRecord.id, provenance_where),
            "compliance_packs_expired": len(expired_packs),
            "analysis_cache_entries": _sum_measured(cache_counts.values()),
            "search_index_documents": search_index_documents,
            "memory_entries_expired": len(memory_expired_ids),
        }
        return {
            "mode": "preview",
            "cutoffs": cutoffs_iso,
            "candidates": candidates,
            # Names the classes whose store could not be reached, so the UI can
            # say "not measured" explicitly instead of leaving the operator to
            # infer it from a null. A caller that ignores this still sees null
            # rather than a wrong zero.
            "unmeasured": sorted(k for k, v in candidates.items() if v is None),
        }

    # ── EXECUTE ──────────────────────────────────────────────────────────

    counts = await execute_candidates(
        db,
        project_id=project_id,
        cand=cand,
        plan=plan,
        mongo=mongo,
        storage=storage,
        cache_counts=cache_counts,
        search_index_documents=search_index_documents,
        now=now,
    )

    logger.info(
        "retention_purge_executed",
        project_id=str(project_id),
        cutoffs=cutoffs_iso,
        counts=counts,
    )
    return {"mode": "execute", "cutoffs": cutoffs_iso, "counts": counts}



async def resolve_run_candidates(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    minio_prefix: Optional[str],
) -> tuple[_Candidates, _ExecutionPlan]:
    """Build the executor's inputs for deleting exactly ONE run.

    Reuses ``execute_candidates`` rather than growing a second copy of the
    deletion logic, because the ordering it encodes — materialize the
    cross-store ids BEFORE the Postgres CASCADE destroys the mapping — is the
    single hardest thing to get right and must not exist twice.

    **The six project-wide filters are deliberately empty.** ``_ExecutionPlan``
    carries clocks that never reference a run: the access and test-case audit
    logs, provenance records, deletion jobs, expired agent memory and expired
    compliance packs. Those belong to the nightly policy purge. A per-run
    delete that inherited them would silently take a project's whole audit
    trail with one run — so each is pinned to ``false()``, which compiles to a
    literal that matches nothing BY CONSTRUCTION rather than by a filter that
    happens not to match today.

    ``event_archive_where`` is the exception: it is scoped to this run, since
    the archived event blob belongs to it.
    """
    from sqlalchemy import false

    never = (false(),)

    tc_strs = [
        str(tc_id)
        for tc_id in (
            await db.execute(
                select(TestCase.id).where(TestCase.test_run_id == run_id)
            )
        ).scalars().all()
    ]

    pipeline_strs = {
        str(pid)
        for pid in (
            await db.execute(
                select(AgentPipelineRun.id).where(
                    AgentPipelineRun.test_run_id == run_id
                )
            )
        ).scalars().all()
    }

    evidence_ids = list(
        (
            await db.execute(
                select(EvidenceArtifact.id).where(
                    EvidenceArtifact.run_id == run_id
                )
            )
        ).scalars().all()
    )

    # live_execution_events is keyed by the CLIENT's run id — a LiveSession
    # slug on the webhook path. A UUID-only resolver reaches none of them.
    from app.services.run_deletion_service import (
        live_session_slugs_for_run,
        safe_artifact_prefixes,
    )

    live_keys = await live_session_slugs_for_run(
        db, project_id=project_id, run_id=run_id
    )
    safe_prefixes, _refused = safe_artifact_prefixes(
        project_id, run_id, minio_prefix
    )

    candidates = _Candidates(
        purge_run_ids=[run_id],
        purge_run_strs=[str(run_id)],
        purge_tc_strs=tc_strs,
        raw_run_strs=[str(run_id)],
        raw_tc_strs=tc_strs,
        raw_live_run_keys=live_keys,
        artifact_prefixes=safe_prefixes,
        artifact_pipeline_strs=pipeline_strs,
        evidence_artifact_ids=evidence_ids,
        audit_pipeline_strs=list(pipeline_strs),
        # An explicit single-run delete takes the report with it. The audit
        # clock exists so a report outlives its run under the POLICY purge;
        # it is not a reason to strand a report whose subject an operator
        # deliberately removed.
        audit_run_strs=[str(run_id)],
    )

    plan = _ExecutionPlan(
        event_archive_where=(TestRun.id == run_id,),
        access_audit_where=never,
        tc_audit_where=never,
        deletion_job_where=never,
        # Share links are ondelete=CASCADE on run_id, so the run delete
        # already takes them. A filter here would be a second deletion of
        # rows that are already gone.
        revoked_share_link_where=never,
        provenance_where=never,
        memory_expired_ids=[],
        expired_packs=[],
    )
    return candidates, plan


async def execute_candidates(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    cand: _Candidates,
    plan: _ExecutionPlan,
    mongo: Any,
    storage: Any,
    cache_counts: dict[str, int],
    search_index_documents: int,
    now: datetime,
) -> dict[str, Any]:
    """Perform the deletes, in the one order that is safe (see §1.2).

    Extracted verbatim from ``run_purge``'s execute branch so that the
    scheduled purge and every future deletion path share ONE executor. The
    ordering is the whole reason this is a separate function rather than a
    convenience: the Postgres CASCADE destroys the only mapping from runs to
    Mongo documents and MinIO keys, so those stores must be visited while the
    mapping still exists.

    Stages only. The caller owns ``db.commit()`` — the router for preview, the
    Celery task for execute.

    ``cand`` carries the run-scoped candidates; ``plan`` carries the eight
    non-run-scoped filters the execute branch previously closed over. Passing
    them explicitly is what makes the resolution phase separable at all: they
    are derived from four independent clocks, and seven of them never
    reference a run.
    """
    # (2) Mongo deletes — while the Postgres mapping still exists.
    mongo_deleted: dict[str, int] = {}
    for collection, field, attr in _MONGO_PLANS:
        mongo_deleted[collection] = await _mongo_delete(
            mongo, collection, field, list(getattr(cand, attr))
        )

    # (3) MinIO deletes.
    minio_deleted = 0
    for prefix in cand.artifact_prefixes:
        minio_deleted += int(
            await storage.delete_prefix(prefix, bucket=_UPLOADS_BUCKET)
        )
    if cand.artifact_pipeline_strs:
        pipeline_objects = await storage.list_objects(
            _PIPELINE_PREFIX, bucket=_PIPELINE_BUCKET
        )
        for key in _pipeline_artifact_keys(
            pipeline_objects, cand.artifact_pipeline_strs
        ):
            await storage.delete_object(key, bucket=_PIPELINE_BUCKET)
            minio_deleted += 1

    # (3.4) Expire memory rows and remove their vector copies. The SQL row is
    # retained as an audit trail; all retrieval paths require active + fresh
    # lifecycle state, and Chroma is purged before the caller commits.
    memory_vectors_deleted = 0
    memory_rows_expired = 0
    if plan.memory_expired_ids:
        from app.services.agent_memory_service import expire_memory_entries, purge_memory_vectors

        memory_vectors_deleted = await purge_memory_vectors(project_id, plan.memory_expired_ids)
        memory_rows_expired = await expire_memory_entries(
            db, project_id=project_id, now=now
        )

    # (3.5) Delete expired evidence rows after external objects and before
    # run cascades. A failed earlier storage operation leaves rows available
    # for retry; no active verified row can outlive the artifacts clock.
    evidence_rows_deleted = 0
    for chunk in _chunks(cand.evidence_artifact_ids, _PG_DELETE_CHUNK):
        result = await db.execute(
            delete(EvidenceArtifact).where(EvidenceArtifact.id.in_(chunk))
        )
        evidence_rows_deleted += int(getattr(result, "rowcount", 0) or 0)

    # (3.6) Stamp provenance project scope BEFORE the run link detaches —
    # 0113 backfilled existing rows, but a row written since (writers only
    # set run_id) would orphan once run_id goes SET NULL below and escape
    # the audit-clock delete forever.
    for chunk in _chunks(cand.purge_run_ids, _PG_DELETE_CHUNK):
        await db.execute(
            update(AIProvenanceRecord)
            .where(
                AIProvenanceRecord.run_id.in_(chunk),
                AIProvenanceRecord.project_id.is_(None),
            )
            .values(project_id=project_id)
        )

    # (3.7) H1 — same guarantee as (3.6), for evidence artifacts. Since 0146
    # ``evidence_artifacts.run_id`` is SET NULL rather than CASCADE, so an
    # artifact spared by the published-report filter above now SURVIVES the run
    # delete instead of being cascaded away behind the filter's back. Stamp the
    # project scope first or the survivor is unreachable: with run_id nulled and
    # project_id already NULL it would escape the artifacts-clock delete forever.
    for chunk in _chunks(cand.purge_run_ids, _PG_DELETE_CHUNK):
        await db.execute(
            update(EvidenceArtifact)
            .where(
                EvidenceArtifact.run_id.in_(chunk),
                EvidenceArtifact.project_id.is_(None),
            )
            .values(project_id=project_id)
        )

    # (4) Postgres run delete — CASCADE covers all 22 direct children plus
    # the second hop via test_cases (zero RESTRICT children).
    runs_deleted = 0
    for chunk in _chunks(cand.purge_run_ids, _PG_DELETE_CHUNK):
        result = await db.execute(delete(TestRun).where(TestRun.id.in_(chunk)))
        runs_deleted += int(getattr(result, "rowcount", 0) or 0)

    # (5) event_archive strip — the documented-but-never-enforced 15-day
    # purge, now driven by the raw-events clock. NULLs BOTH columns so the
    # recovery service's archive-window check stays coherent.
    strip_result = await db.execute(
        update(TestRun)
        .where(*plan.event_archive_where)
        .values(event_archive=None, event_archive_at=None)
    )
    event_archive_stripped = int(getattr(strip_result, "rowcount", 0) or 0)

    # (6) Audit-class deletes (audit clock — separate from the run clock).
    access_result = await db.execute(delete(AccessAuditLog).where(*plan.access_audit_where))
    tc_audit_result = await db.execute(delete(TestCaseAuditLog).where(*plan.tc_audit_where))
    deletion_job_result = await db.execute(
        delete(DeletionJob).where(*plan.deletion_job_where)
    )
    revoked_link_result = await db.execute(
        delete(ReportShareLink).where(*plan.revoked_share_link_where)
    )
    provenance_result = await db.execute(
        delete(AIProvenanceRecord).where(*plan.provenance_where)
    )

    # Expired compliance packs: object first, row second — if the object
    # delete fails the row survives, so the next sweep retries (re-entrant).
    packs_deleted = 0
    deletable_pack_ids: list[uuid.UUID] = []
    for pack in plan.expired_packs:
        try:
            await storage.delete_object(pack.minio_key, bucket=_COMPLIANCE_BUCKET)
            deletable_pack_ids.append(pack.id)
        except Exception as exc:  # noqa: BLE001 — per-pack isolation
            logger.warning(
                "retention_compliance_pack_object_delete_failed",
                project_id=str(project_id),
                pack_id=str(pack.id),
                error=str(exc),
            )
    for chunk in _chunks(deletable_pack_ids, _PG_DELETE_CHUNK):
        result = await db.execute(
            delete(CompliancePack).where(CompliancePack.id.in_(chunk))
        )
        packs_deleted += int(getattr(result, "rowcount", 0) or 0)

    counts = {
        "postgres": {
            "runs": runs_deleted,
            "evidence_artifact_rows": evidence_rows_deleted,
            "event_archive_stripped": event_archive_stripped,
            "access_audit_rows": int(getattr(access_result, "rowcount", 0) or 0),
            "test_case_audit_rows": int(getattr(tc_audit_result, "rowcount", 0) or 0),
            "deletion_job_rows": int(getattr(deletion_job_result, "rowcount", 0) or 0),
            "revoked_share_links": int(getattr(revoked_link_result, "rowcount", 0) or 0),
            "provenance_rows": int(getattr(provenance_result, "rowcount", 0) or 0),
            "compliance_packs": packs_deleted,
            "memory_entries_expired": memory_rows_expired,
        },
        "mongo": mongo_deleted,
        "minio": {"objects_deleted": minio_deleted},
        "analysis_cache": cache_counts,
        "search_index": {"documents_deleted": search_index_documents},
        "memory": {"vectors_deleted": memory_vectors_deleted},
    }

    return counts