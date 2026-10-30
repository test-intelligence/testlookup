"""Per-project data retention policies + cross-store purge (PMF US-11.4).

Four retention classes, each with its own clock (days before "now"):

======================  =====================================================
class                   what it covers
======================  =====================================================
``raw_events_days``     ``TestRun.event_archive`` JSONB strip; Mongo
                        ``live_execution_events`` (keyed by run_id — TestRun
                        UUID string or LiveSession slug), ``raw_allure_json``
                        (keyed by test_run_id), ``raw_testng_xml`` (keyed by
                        test_case_id), ``rest_api_payloads`` (W23d: keyed by
                        test_run_id — every captured-exchange doc carries it).
``runs_days``           ``test_runs`` rows — a plain DELETE; every child is
                        ``ondelete="CASCADE"`` (22 direct children plus the
                        second hop via test_cases; zero RESTRICT). Run-scoped
                        Mongo docs (``execution_logs``, ``ocp_pod_events``,
                        ``run_summaries`` by test_run_id;
                        ``ai_analysis_payloads`` by test_case_id).
                        AI history (AIH-06, owner decision D41: runs clock):
                        Postgres ``ai_analysis_versions`` CASCADE with the run
                        (counted before the delete); Mongo ``ai_run_versions``
                        by test_run_id. Plus two CAPS on the surviving runs
                        (``ai_versions_per_run`` per kind, default 20;
                        ``ai_versions_per_test``, default 10), swept by
                        ``ai_history_retention`` — oldest first, never v1, never
                        a version cited by a published decision report, and
                        nothing at all when the citations cannot be read.
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

Plus one class on a FIXED clock (W12 SRCH-05): ``search_usage_rows``, the
project's ``search_query_events`` older than 30 days (``USAGE_RETENTION_DAYS``;
rows hold no query text). The nightly ``purge_search_usage_events`` task
deletes those deployment-wide; the preview counts and execute deletes the
project's own, so the dry run lists every class the purge removes. W16
AGT-13 adds a second: ``shadow_pairs_unlabelled``, the project's PENDING
``ai_eval_shadow_pairs`` older than 30 days (``SHADOW_PAIR_RETENTION_DAYS``;
labelled pairs are G2 evidence and stay), deleted deployment-wide nightly by
``expire_unlabelled_shadow_pairs``.

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

W22b ACT-06 — inheritance and the schedule. Every policy column of the seven
``INHERITABLE_FIELDS`` is nullable: NULL = inherit. ``get_effective_policy`` is
the ONE place a NULL is read, per field ``project ▸ instance ▸ code default``
(the instance row: ``retention_instance_service``), with the audit window
clamped to the runs window; ``effective_policy_columns`` /
``effective_enabled_expr`` are its SQL twins for set-based reads (the project
list, the deleted-project report, the D12 preview) and ``due_project_ids`` is
the hourly beat's one target select (window hour + not paused). The AI caps are
not inherited. W22b E-12: a retained deletion archive (``deletion_jobs
.export_location``) leaves with its job row on the audit clock — object first,
row second.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Optional, Sequence

import structlog
from sqlalchemy import and_, any_, bindparam, case, delete, exists, false, func, literal, or_, select, true, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.mongo import Collections, get_mongo_db
from app.db.storage import get_storage_provider
from app.models.postgres import (
    AccessAuditLog,
    AIEvalShadowPair,
    AgentPipelineRun,
    AgentMemoryEntry,
    AIProvenanceRecord,
    CompliancePack,
    DeletionJob,
    EvidenceArtifact,
    InstanceRetentionPolicy,
    LaunchStatus,
    LiveSession,
    Project,
    ProjectRetentionPolicy,
    ReportShareLink,
    RunBaseline,
    SearchQueryEvent,
    SettingsAuditLog,
    TestCase,
    ProjectActivityEvent,
    TestCaseAuditLog,
    TestRun,
)
from app.services import ai_history_retention, deletion_job_service, retention_instance_service, retention_references
from app.services.ai_eval_vocab import SHADOW_PAIR_RETENTION_DAYS
from app.services.search_usage_service import usage_cutoff
from app.services.ai_history_core import (
    CAP_MAX,
    CAP_MIN,
    DEFAULT_CAP_PER_RUN,
    DEFAULT_CAP_PER_TEST,
    AIHistoryCaps,
    clamp_cap,
)
from app.services.retention_vocab import (
    INHERITABLE_FIELDS,
    KEEP_LAST_RUNS_DEFAULT,
    KEEP_LAST_RUNS_MAX,
    KEEP_LAST_RUNS_MIN,
    PAUSE_DAYS_MAX,
    PAUSE_DAYS_MIN,
    POLICY_ERROR_SENTENCES,
    PURGE_WINDOW_DEFAULT_HOUR,
    PURGE_WINDOW_MAX,
    PURGE_WINDOW_MIN,
)

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

#: AI-history caps (AIH-06): versions kept per (run, kind) and per test result.
#: NULL in the row = the code default; bounds mirrored by the Pydantic schema.
CAP_FIELDS: dict[str, int] = {
    "ai_versions_per_run": DEFAULT_CAP_PER_RUN,
    "ai_versions_per_test": DEFAULT_CAP_PER_TEST,
}

#: W22b ACT-06: the code default of each INHERITABLE field — the third step of
#: the per-field resolution ``project ▸ instance ▸ code default`` (what a NULL
#: means at both levels). The AI caps are NOT inherited (D-12): their NULL
#: already means the code default above.
CODE_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "raw_events_days": DEFAULT_RAW_EVENTS_DAYS,
    "runs_days": DEFAULT_RUNS_DAYS,
    "artifacts_days": DEFAULT_ARTIFACTS_DAYS,
    "audit_days": DEFAULT_AUDIT_DAYS,
    "keep_last_runs": KEEP_LAST_RUNS_DEFAULT,
    "purge_window_start_utc": PURGE_WINDOW_DEFAULT_HOUR,
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


def _hold_service() -> Any:
    """The retention-hold service (W22a ACT-05), imported on use.

    Every held class of the policy purge appends one of its ``NOT EXISTS``
    clause builders (``run_not_held`` / ``project_not_held`` /
    ``pack_not_held`` / ``activity_not_held``) to its WHERE; the reads
    (``live_holds``, ``held_run_ids``, ``pinned_*``) feed the preview and the
    job record. Imported here rather than at module import so this module's
    import never depends on the hold service's own import graph; tests patch
    the reads on the module object.
    """
    from app.services import retention_hold_service

    return retention_hold_service


def _latest_passed_subquery(project_id: uuid.UUID) -> Any:
    """Floor arm 2: the project's newest PASSED run — the next run's baseline
    (``run_diff_service`` chooses exactly that)."""
    return (
        select(TestRun.id.label("id"))
        .where(TestRun.project_id == project_id, TestRun.status == LaunchStatus.PASSED.value)
        .order_by(TestRun.created_at.desc(), TestRun.id.desc())
        .limit(1)
        .subquery("floor_latest_passed")
    )


def _suite_latest_passed_subquery(project_id: uuid.UUID) -> Any:
    """Floor arm 3: the newest PASSED run of EACH suite, read from the RUN's
    ``suite_names`` (else ``primary_suite_name``) — the effective suite at run
    level (§7.2 D-6). The ``json_typeof`` guard keeps a scalar / NULL
    ``suite_names`` (old rows) from raising "cannot extract elements from a
    scalar"; such a run is covered by its ``primary_suite_name`` or by arm 2."""
    suites = (
        func.json_array_elements_text(
            case(
                (func.json_typeof(TestRun.suite_names) == "array", TestRun.suite_names),
                else_=func.json_build_array(TestRun.primary_suite_name),
            )
        )
        .table_valued("suite")
        .render_derived(name="s")
        .lateral()
    )
    suite_key = func.lower(func.trim(suites.c.suite))
    # ``row_number() … = 1`` per suite is DISTINCT ON (suite_key) … ORDER BY
    # suite_key, created_at DESC, id DESC, spelled portably (no dialect
    # warning when a statement-routing fake renders it).
    rows = (
        select(
            TestRun.id.label("id"),
            suite_key.label("suite_key"),
            func.row_number()
            .over(partition_by=suite_key, order_by=(TestRun.created_at.desc(), TestRun.id.desc()))
            .label("suite_rank"),
        )
        .select_from(TestRun)
        .join(suites, true())
        .where(
            TestRun.project_id == project_id,
            TestRun.status == LaunchStatus.PASSED.value,
            func.nullif(func.trim(suites.c.suite), "").is_not(None),
        )
        .subquery("floor_suite_rows")
    )
    return (
        select(rows.c.id, rows.c.suite_key)
        .where(rows.c.suite_rank == 1)
        .subquery("floor_suite_latest_passed")
    )


def floor_not_kept(
    run_id_col: Any, project_id: uuid.UUID, *, keep_last_runs: int,
) -> ColumnElement[bool]:
    """The keep-last floor as anti-joins (W22a ACT-04, contract §2.2 F-2).

    Module-level so tests compile the predicate the purge actually uses (the
    ``deletion_job_purge_filter`` precedent). The AND of ``NOT EXISTS`` against
    uncorrelated derived tables, each matched on ``id = run_id_col``:

    1. the project's newest ``keep_last_runs`` runs — omitted when N = 0;
    2. the project's newest PASSED run;
    3. the newest PASSED run of each suite.

    The RUNS clock only: it is appended to the runs-clock select, never to the
    raw-events / artifacts / audit selections.
    """
    arms: list[Any] = []
    if int(keep_last_runs) > 0:
        newest = (
            select(TestRun.id.label("id"))
            .where(TestRun.project_id == project_id)
            .order_by(TestRun.created_at.desc(), TestRun.id.desc())
            .limit(int(keep_last_runs))
            .subquery("floor_newest")
        )
        arms.append(~exists().where(newest.c.id == run_id_col))
    latest = _latest_passed_subquery(project_id)
    arms.append(~exists().where(latest.c.id == run_id_col))
    per_suite = _suite_latest_passed_subquery(project_id)
    arms.append(~exists().where(per_suite.c.id == run_id_col))
    return and_(*arms)


def _floor_kept_detail_statement(project_id: uuid.UUID, kept_ids: list[uuid.UUID]) -> Any:
    """ONE statement classifying the floor-kept runs: is the project's newest
    PASSED run among them, and how many suites' newest PASSED runs are."""
    kept = bindparam("floor_kept_ids", value=list(kept_ids), type_=ARRAY(PG_UUID(as_uuid=True)))
    latest = _latest_passed_subquery(project_id)
    per_suite = _suite_latest_passed_subquery(project_id)
    suites_kept = (
        select(func.count(func.distinct(per_suite.c.suite_key)))
        .where(per_suite.c.id == any_(kept))
        .scalar_subquery()
    )
    return select(
        exists().where(latest.c.id == any_(kept)).label("latest_passed_kept"),
        suites_kept.label("suites_kept"),
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


class PolicyFieldError(Exception):
    """A structured policy-write refusal (W22b): ``code`` is a
    ``POLICY_ERROR_SENTENCES`` key; the route answers 422 ``{"code","message"}``.

    Deliberately NOT a :class:`RetentionValidationError` subclass, so the
    route's existing ``except svc.RetentionValidationError`` (a plain-string
    422) keeps its meaning and a separate ``except`` maps this one.
    """

    def __init__(self, code: str) -> None:
        super().__init__(POLICY_ERROR_SENTENCES[code])
        self.code = code

    @property
    def message(self) -> str:
        return POLICY_ERROR_SENTENCES[self.code]


@dataclass(frozen=True)
class EffectiveRetentionPolicy:
    """Resolved per-project retention policy (missing row → defaults)."""
    enabled: bool = False
    raw_events_days: int = DEFAULT_RAW_EVENTS_DAYS
    runs_days: int = DEFAULT_RUNS_DAYS
    artifacts_days: int = DEFAULT_ARTIFACTS_DAYS
    audit_days: int = DEFAULT_AUDIT_DAYS
    #: AIH-06 caps. They apply at WRITE time whatever ``enabled`` says (the
    #: owner-accepted defaults, D41); ``enabled`` only gates the scheduled sweep.
    ai_versions_per_run: int = DEFAULT_CAP_PER_RUN
    ai_versions_per_test: int = DEFAULT_CAP_PER_TEST
    source: str = "default"  # "default" | "custom"
    #: W22a ACT-04: the scheduled purge always keeps the newest N runs of the
    #: project (plus the newest PASSED run of the project and of each suite).
    #: The RUNS clock only; explicit deletions are not limited by it.
    keep_last_runs: int = KEEP_LAST_RUNS_DEFAULT
    #: W22b ACT-06: the UTC hour the hourly beat purges this project in
    #: (inheritable; 02:00 by default, so a deployment that sets none purges at
    #: the hour it always did).
    purge_window_start_utc: int = PURGE_WINDOW_DEFAULT_HOUR
    #: Project-only (never inherited); kept only while still in the future.
    purge_paused_until: Optional[datetime] = None
    #: Per inheritable field, where its value came from ("project" | "instance"
    #: | "default"), and what it would read if it inherited. Tuples keep the
    #: frozen dataclass hashable; ``compare=False`` keeps
    #: ``EffectiveRetentionPolicy() == <no row, no instance>`` true.
    field_sources: tuple[tuple[str, str], ...] = dc_field(default=(), compare=False)
    inherit_values: tuple[tuple[str, Any], ...] = dc_field(default=(), compare=False)

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "raw_events_days": self.raw_events_days,
            "runs_days": self.runs_days,
            "artifacts_days": self.artifacts_days,
            "audit_days": self.audit_days,
            "ai_versions_per_run": self.ai_versions_per_run,
            "ai_versions_per_test": self.ai_versions_per_test,
            "keep_last_runs": self.keep_last_runs,
            "purge_window_start_utc": self.purge_window_start_utc,
        }

    @property
    def sources(self) -> dict[str, str]:
        """``field_sources`` as a dict (the policy GET's ``field_sources``)."""
        return dict(self.field_sources)

    @property
    def inherited(self) -> dict[str, Any]:
        """``inherit_values`` as a dict (the policy GET's ``inherit_values``)."""
        return dict(self.inherit_values)

    @property
    def ai_history_caps(self) -> AIHistoryCaps:
        return AIHistoryCaps(per_run=self.ai_versions_per_run, per_test=self.ai_versions_per_test)


def _stored_cap(row: Any, field: str) -> int:
    """The effective cap of a policy row: its stored value, or the default when
    NULL (the column is nullable; the CHECK keeps a stored value in 1-100)."""
    return clamp_cap(getattr(row, field, None), CAP_FIELDS[field])


def _coerce(field: str, value: Any) -> Any:
    return bool(value) if field == "enabled" else int(value)


def _inherited_value(field: str, inst: Any) -> tuple[Any, str]:
    """What ``field`` reads when the project does not set it: the instance
    value when stored (for ``enabled``: when the instance default is ON), else
    the code default."""
    if field == "enabled":
        if bool(getattr(inst, "enabled", False)):
            return True, "instance"
        return False, "default"
    value = getattr(inst, field, None)
    if value is not None:
        return int(value), "instance"
    return CODE_DEFAULTS[field], "default"


@dataclass(frozen=True)
class _Resolution:
    values: dict[str, Any]
    sources: dict[str, str]
    inherit_values: dict[str, Any]


def _resolve(row: Any, inst: Any) -> _Resolution:
    """The per-field resolution ``project ▸ instance ▸ code default`` — the ONE
    place a NULL policy column is read (W22b W-1). ``getattr(row, field,
    None)`` keeps the ``SimpleNamespace`` policy fakes valid (a fake without a
    column inherits it). Then the audit clamp (D-9): a mix of sources never
    resolves an audit window shorter than the runs window."""
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    inherit_values: dict[str, Any] = {}
    for field in INHERITABLE_FIELDS:
        inherited, inherited_source = _inherited_value(field, inst)
        inherit_values[field] = inherited
        project_value = getattr(row, field, None) if row is not None else None
        if project_value is not None:
            values[field], sources[field] = _coerce(field, project_value), "project"
        else:
            values[field], sources[field] = inherited, inherited_source
    values["audit_days"] = max(values["audit_days"], values["runs_days"])
    return _Resolution(values=values, sources=sources, inherit_values=inherit_values)


def _effective_from(row: Any, inst: Any, *, now: datetime) -> EffectiveRetentionPolicy:
    resolution = _resolve(row, inst)
    paused = getattr(row, "purge_paused_until", None) if row is not None else None
    if paused is not None and paused <= now:
        paused = None
    return EffectiveRetentionPolicy(
        **resolution.values,
        ai_versions_per_run=_stored_cap(row, "ai_versions_per_run"),
        ai_versions_per_test=_stored_cap(row, "ai_versions_per_test"),
        source="custom" if row is not None else "default",
        purge_paused_until=paused,
        field_sources=tuple(resolution.sources.items()),
        inherit_values=tuple(resolution.inherit_values.items()),
    )


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
    db: AsyncSession, project_id: uuid.UUID, *, now: Optional[datetime] = None,
) -> EffectiveRetentionPolicy:
    """The project's EFFECTIVE policy, per field ``project ▸ instance ▸ code
    default`` (W22b W-1). Two statements, in this order: ``get_policy_row``
    (unchanged) and the instance policy (ONE select; a missing row = the code
    defaults; a read ERROR propagates — fail closed, never a silent code
    default that may be shorter than the instance's). ``source`` keeps its
    meaning: ``custom`` = a project row exists."""
    row = await get_policy_row(db, project_id)
    inst = await retention_instance_service.get_instance_policy(db)
    return _effective_from(row, inst, now=now or datetime.now(timezone.utc))


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
    ai_versions_per_run: Optional[int] = None,
    ai_versions_per_test: Optional[int] = None,
    actor: Any = None,
    keep_last_runs: Optional[int] = None,
    purge_window_start_utc: Optional[int] = None,
    pause_days: Optional[int] = None,
    resume: Optional[bool] = None,
    inherit: Sequence[str] = (),
    now: Optional[datetime] = None,
) -> EffectiveRetentionPolicy:
    """Upsert the project's policy row from a PARTIAL update.

    Omitted (``None``) fields keep their current value. W22b W-2: ``inherit``
    names the inheritable fields to reset (each column set NULL = inherit the
    instance, else the code default); ``pause_days`` sets
    ``purge_paused_until = now + days`` and ``resume`` clears it. Refusals in
    order, each a :class:`PolicyFieldError` (router: 422 ``{"code","message"}``):
    an unknown ``inherit`` field, a field both given and inherited,
    ``pause_days`` with ``resume``. Then the bounds AND the audit≥runs
    cross-check on the MERGED EFFECTIVE values (given ▸ the inherited value of
    an ``inherit`` field ▸ the current effective value) — raises
    :class:`RetentionValidationError` (router maps to a plain-string 422).

    A NEW row stores ONLY the fields the admin sent (every other column NULL =
    inherit), so its effective policy is what it was before the save. The audit
    ``changed`` dict records ``{"old", "new", "source_old", "source_new"}`` per
    field whose effective value OR source changed (an inherit that keeps the
    value still changes its source), plus ``purge_paused_until``.

    The two AI-history caps (AIH-06) are 1-100 versions; omitted keeps the
    current value, and there is no reset-to-NULL: sending 20 / 10 restores the
    defaults' behaviour. A cap that changes the effective value is in the
    ``changed`` audit dict like any other field.

    Stages only (``flush``) — the router session owns the commit. A
    ``settings_audit_log`` row records the change (``changed_fields`` is a
    dict by house convention despite the ``list`` annotation).
    """
    now = now or datetime.now(timezone.utc)
    inherit_fields = list(inherit or ())
    if any(field not in INHERITABLE_FIELDS for field in inherit_fields):
        raise PolicyFieldError("unknown_inherit_field")
    provided: dict[str, Any] = {
        "enabled": enabled,
        "raw_events_days": raw_events_days,
        "runs_days": runs_days,
        "artifacts_days": artifacts_days,
        "audit_days": audit_days,
        "keep_last_runs": keep_last_runs,
        "purge_window_start_utc": purge_window_start_utc,
    }
    given = {field: _coerce(field, value) for field, value in provided.items() if value is not None}
    if set(given) & set(inherit_fields):
        raise PolicyFieldError("inherit_conflict")
    if pause_days is not None and resume is not None:
        raise PolicyFieldError("pause_conflict")

    row = await get_policy_row(db, project_id)
    inst = await retention_instance_service.get_instance_policy(db)
    before = _resolve(row, inst)
    merged: dict[str, Any] = {
        field: given[field] if field in given
        else before.inherit_values[field] if field in inherit_fields
        else before.values[field]
        for field in INHERITABLE_FIELDS
    }
    _validate_merged(merged)
    caps_provided = {
        "ai_versions_per_run": ai_versions_per_run,
        "ai_versions_per_test": ai_versions_per_test,
    }
    for field, value in caps_provided.items():
        if value is not None and not (CAP_MIN <= int(value) <= CAP_MAX):
            raise RetentionValidationError(
                f"{field} must be between {CAP_MIN} and {CAP_MAX} versions (got {value})"
            )
    caps_current = {field: _stored_cap(row, field) for field in CAP_FIELDS}
    # W22a ACT-04: the keep-last floor, 0-1000 (0 = only the PASSED keeps).
    if keep_last_runs is not None and not (
        KEEP_LAST_RUNS_MIN <= int(keep_last_runs) <= KEEP_LAST_RUNS_MAX
    ):
        raise RetentionValidationError(
            f"keep_last_runs must be between {KEEP_LAST_RUNS_MIN} and {KEEP_LAST_RUNS_MAX} (got {keep_last_runs})"
        )
    # W22b: the window hour and the pause length (the schema bounds them too).
    if purge_window_start_utc is not None and not (
        PURGE_WINDOW_MIN <= int(purge_window_start_utc) <= PURGE_WINDOW_MAX
    ):
        raise RetentionValidationError(
            f"purge_window_start_utc must be between {PURGE_WINDOW_MIN} and {PURGE_WINDOW_MAX} "
            f"(got {purge_window_start_utc})"
        )
    if pause_days is not None and not (PAUSE_DAYS_MIN <= int(pause_days) <= PAUSE_DAYS_MAX):
        raise RetentionValidationError(
            f"pause_days must be between {PAUSE_DAYS_MIN} and {PAUSE_DAYS_MAX} (got {pause_days})"
        )

    changed: dict[str, Any] = {}
    if row is None:
        # D-19: ONLY the fields the admin sent; every other column stays NULL
        # (= inherit), so the new row's effective policy is unchanged.
        row = ProjectRetentionPolicy(
            project_id=project_id,
            updated_by_user_id=actor_id,
            **given,
            # NULL (= the code default) unless the admin set the cap.
            **{field: value for field, value in caps_provided.items() if value is not None},
        )
        db.add(row)
        action = "created"
    else:
        action = "updated"
        for field in INHERITABLE_FIELDS:
            if field in given:
                setattr(row, field, given[field])
            elif field in inherit_fields:
                setattr(row, field, None)
        row.updated_by_user_id = actor_id
    paused_old = getattr(row, "purge_paused_until", None)
    paused_new = paused_old
    if pause_days is not None:
        paused_new = now + timedelta(days=int(pause_days))
    elif resume:
        paused_new = None
    if paused_new != paused_old:
        row.purge_paused_until = paused_new

    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value is not None else None

    if action == "created":
        # A created row's audit keeps its shape: the stored values, flat.
        changed.update(given)
        changed.update({field: int(value) for field, value in caps_provided.items() if value is not None})
        if paused_new is not None:
            changed["purge_paused_until"] = _iso(paused_new)
    else:
        for field, cap_value in caps_provided.items():
            if cap_value is not None and int(cap_value) != caps_current[field]:
                changed[field] = {"old": caps_current[field], "new": int(cap_value)}
                setattr(row, field, int(cap_value))
        # Per field: the effective value OR its source changed (resolved on the
        # in-memory row, before the flush). The source pair rides along only
        # when the source moved, so a plain value change keeps {"old", "new"}.
        after = _resolve(row, inst)
        for field in INHERITABLE_FIELDS:
            old, new = before.values[field], after.values[field]
            source_old, source_new = before.sources[field], after.sources[field]
            if old != new or source_old != source_new:
                entry: dict[str, Any] = {"old": old, "new": new}
                if source_old != source_new:
                    entry.update(source_old=source_old, source_new=source_new)
                changed[field] = entry
        if paused_new != paused_old:
            changed["purge_paused_until"] = {"old": _iso(paused_old), "new": _iso(paused_new)}

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
    if changed and actor is not None:
        # W21 ACT-01: outcome row staged on the router's session beside the
        # settings audit row, so a rolled-back save records nothing.
        from app.services.activity.service import ActorRef, record as record_activity

        await record_activity(
            db, project_id=project_id, event_type="retention_policy.updated", actor=ActorRef.from_user(actor),
            entity_id=row.id, entity_label="Retention policy",
            context={"changed": ", ".join(f.replace("_", " ") for f in changed)},
            changed_fields=list(changed),
        )

    logger.info(
        "retention_policy_upserted",
        project_id=str(project_id),
        action=action,
        changed=changed,
        actor_id=str(actor_id) if actor_id else None,
    )
    return await get_effective_policy(db, project_id, now=now)


# ── W22b ACT-06: the SQL twins, the due-project selector, the next purge ─────


def effective_policy_columns(prp: Any, inst: Any) -> dict[str, ColumnElement]:
    """The resolver's SQL twin for set-based reads: ``COALESCE(prp.x, inst.x,
    code default)`` per inheritable field, over a statement that LEFT JOINs the
    project's policy (``prp``) and the instance row (``inst``, on ``id = 1``).
    ``audit_days`` carries the resolver's clamp (``GREATEST(audit, runs)``,
    D-9); ``enabled`` is :func:`effective_enabled_expr`."""
    columns: dict[str, ColumnElement] = {"enabled": effective_enabled_expr(prp, inst)}
    for field in INHERITABLE_FIELDS[1:]:
        columns[field] = func.coalesce(getattr(prp, field), getattr(inst, field), literal(CODE_DEFAULTS[field]))
    columns["audit_days"] = func.greatest(columns["audit_days"], columns["runs_days"])
    return columns


def effective_enabled_expr(prp: Any, inst: Any) -> ColumnElement[bool]:
    """``COALESCE(prp.enabled, inst.enabled, false)``: an explicit project value
    wins (``false`` stays off); a NULL / missing row follows the instance
    default; no instance row = off."""
    return func.coalesce(prp.enabled, inst.enabled, false())


def due_project_ids_statement(*, now: datetime) -> Any:
    """The hourly beat's target select (W-3): projects whose effective
    ``enabled`` is on, whose effective window hour is the current UTC hour, and
    that are not paused. No ``is_active`` filter — the beat never filtered it
    and the deleted-project report relies on that."""
    prp, inst = ProjectRetentionPolicy, InstanceRetentionPolicy
    hour = now.astimezone(timezone.utc).hour
    window = func.coalesce(
        prp.purge_window_start_utc, inst.purge_window_start_utc, literal(PURGE_WINDOW_DEFAULT_HOUR),
    )
    return (
        select(Project.id)
        .select_from(Project)
        .outerjoin(prp, prp.project_id == Project.id)
        .outerjoin(inst, inst.id == retention_instance_service.INSTANCE_ID)
        .where(
            effective_enabled_expr(prp, inst),
            window == bindparam("purge_hour", hour),
            or_(prp.purge_paused_until.is_(None), prp.purge_paused_until <= now),
        )
        .order_by(Project.id)
    )


async def due_project_ids(db: AsyncSession, *, now: datetime) -> list[uuid.UUID]:
    """The projects the hourly beat purges at ``now``: ONE statement read with
    ``.scalars().all()`` (the sweep's fakes answer exactly that shape)."""
    result = await db.execute(due_project_ids_statement(now=now))
    return list(result.scalars().all())


def next_scheduled_purge_at(policy: EffectiveRetentionPolicy, *, now: datetime) -> Optional[datetime]:
    """When the beat next purges this project: ``None`` while disabled; else the
    first ``HH:00`` UTC (``HH`` = the effective window hour) strictly after
    ``max(now, purge_paused_until)``."""
    if not policy.enabled:
        return None
    start = now.astimezone(timezone.utc)
    paused = policy.purge_paused_until
    if paused is not None and paused > start:
        start = paused.astimezone(timezone.utc)
    candidate = start.replace(hour=int(policy.purge_window_start_utc), minute=0, second=0, microsecond=0)
    if candidate <= start:
        candidate += timedelta(days=1)
    return candidate


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
    activity_where: tuple
    deletion_job_where: tuple
    revoked_share_link_where: tuple
    provenance_where: tuple
    memory_expired_ids: list
    expired_packs: list
    #: W22b E-12: ``(job_id, export_location)`` of the retained deletion
    #: archives whose job rows leave on the AUDIT clock (object first, row
    #: second). Explicit deletes pass an empty list (never project-wide rows).
    retained_archives: list = dc_field(default_factory=list)


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
    #: W22a: every run past the RUNS cutoff that no hold keeps (the superset
    #: select's rows). ``runs_window_ids - purge_run_ids`` = what the floor kept.
    runs_window_ids: list[uuid.UUID] = dc_field(default_factory=list)


async def _collect_candidates(
    db: AsyncSession,
    project_id: uuid.UUID,
    cutoffs: dict[str, datetime],
    *,
    now: datetime,
    keep_last_runs: int = KEEP_LAST_RUNS_DEFAULT,
    held_run_ids: frozenset[uuid.UUID] = frozenset(),
) -> _Candidates:
    holds = _hold_service()
    # One pass over test_runs older than the NEWEST cutoff (superset of every
    # class), classified in Python against each clock. W22a: held runs never
    # enter it (P-1/P-2/P-4/P-6 — every list derived from it inherits that).
    newest_cutoff = max(cutoffs.values())

    run_rows = (
        await db.execute(
            select(TestRun.id, TestRun.minio_prefix, TestRun.created_at).where(
                TestRun.project_id == project_id,
                TestRun.created_at < newest_cutoff,
                holds.run_not_held(TestRun.id, project_id, now=now),
            )
        )
    ).all()

    runs_window_ids: list[uuid.UUID] = []
    raw_run_ids: list[uuid.UUID] = []
    audit_run_ids: list[uuid.UUID] = []
    for run_id, _minio_prefix, created_at in run_rows:
        if created_at < cutoffs["runs"]:
            runs_window_ids.append(run_id)
        if created_at < cutoffs["raw_events"]:
            raw_run_ids.append(run_id)
        if created_at < cutoffs["audit"]:
            audit_run_ids.append(run_id)

    # W22a F-2: the RUNS clock is its own select carrying BOTH exclusions in
    # SQL (holds and the keep-last floor, as anti-joins) — same three columns
    # as the superset, so hand-built rows keep their shape. Issued only when
    # the window holds a candidate. The Python cutoff re-check is a no-op on
    # Postgres; it keeps a statement-routing fake (which answers every run
    # select with all its rows) meaningful.
    purge_run_ids: list[uuid.UUID] = []
    if runs_window_ids:
        purge_rows = (
            await db.execute(
                select(TestRun.id, TestRun.minio_prefix, TestRun.created_at).where(
                    TestRun.project_id == project_id,
                    TestRun.created_at < cutoffs["runs"],
                    holds.run_not_held(TestRun.id, project_id, now=now),
                    floor_not_kept(TestRun.id, project_id, keep_last_runs=keep_last_runs),
                )
            )
        ).all()
        purge_run_ids = [rid for rid, _prefix, created_at in purge_rows if created_at < cutoffs["runs"]]
    purge_set = set(purge_run_ids)

    # Artifacts clock ∪ the runs actually purged: a deleted run's objects must
    # die with it, but a FLOOR-kept run keeps its objects until the artifacts
    # clock itself expires them (D-21).
    artifact_run_ids: list[uuid.UUID] = []
    artifact_prefixes: set[str] = set()
    for run_id, minio_prefix, created_at in run_rows:
        if created_at < cutoffs["artifacts"] or run_id in purge_set:
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
    # W22a P-3: a project hold empties the select; a slug whose canonical run
    # id is held (a run / release hold, or a held run's baseline) is dropped
    # here — the slug -> uuid mapping is uuid5 in Python, not SQL.
    live_slugs = (
        await db.execute(
            select(LiveSession.run_id).where(
                LiveSession.project_id == project_id,
                LiveSession.started_at < cutoffs["raw_events"],
                holds.project_not_held(project_id, now=now),
            )
        )
    ).scalars().all()
    if held_run_ids:
        from app.services.stream_service import canonical_test_run_uuid

        live_slugs = [s for s in live_slugs if s and canonical_test_run_uuid(str(s)) not in held_run_ids]
    raw_run_strs = [str(r) for r in raw_run_ids]
    raw_live_run_keys = list({*raw_run_strs, *[s for s in live_slugs if s]})
    # W22a P-5: an artifact of a held run stays; a NULL run_id falls to the
    # project arm of the clause.
    evidence_artifact_ids = list((
        await db.execute(
            select(EvidenceArtifact.id).where(
                EvidenceArtifact.project_id == project_id,
                EvidenceArtifact.created_at < cutoffs["artifacts"],
                holds.run_not_held(EvidenceArtifact.run_id, project_id, now=now),
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
        runs_window_ids=runs_window_ids,
    )


# (collection, key field, candidate attribute) — raw-events + runs classes.
# raw_allure_json docs carry test_run_id (the ingestion writer always sets
# it); raw_testng_xml / ai_analysis_payloads are keyed only by test_case_id.
# W23d: rest_api_payloads moved to test_run_id — its only writer
# (http_exchange_store) always sets it, and a live exchange whose result never
# projected has no test_cases row, so a case-keyed purge could never reach it.
# Held runs stay out of raw_run_strs (the hold anti-join), so their docs stay.
_MONGO_PLANS: tuple[tuple[str, str, str], ...] = (
    (Collections.RAW_ALLURE_JSON, "test_run_id", "raw_run_strs"),
    (Collections.RAW_TESTNG_XML, "test_case_id", "raw_tc_strs"),
    (Collections.REST_API_PAYLOADS, "test_run_id", "raw_run_strs"),
    (Collections.LIVE_EXECUTION_EVENTS, "run_id", "raw_live_run_keys"),
    (Collections.EXECUTION_LOGS, "test_run_id", "purge_run_strs"),
    (Collections.OCP_POD_EVENTS, "test_run_id", "purge_run_strs"),
    (Collections.RUN_SUMMARIES, "test_run_id", "purge_run_strs"),
    # AIH-06 (D41): the versioned run summaries and analysis digests ride the
    # RUNS clock, like the latest-pointer ``run_summaries`` above. The single-run
    # delete (``resolve_run_candidates`` -> ``execute_candidates``) takes them
    # through this same entry, so a deleted run leaves no orphan version.
    (Collections.AI_RUN_VERSIONS, "test_run_id", "purge_run_strs"),
    (Collections.DECISION_EVIDENCE_SNAPSHOTS, "test_run_id", "purge_run_strs"),
    (Collections.AI_ANALYSIS_PAYLOADS, "test_case_id", "purge_tc_strs"),
    (_PIPELINE_EVENT_LOG, "pipeline_run_id", "audit_pipeline_strs"),
    # S4 — the only two Collections members that were absent from this table,
    # so nothing had ever purged a decision report. On the AUDIT clock, not
    # the runs clock: a report is evidence about a run and must outlive it.
    (Collections.DECISION_REPORTS, "test_run_id", "audit_run_strs"),
    (Collections.DECISION_REPORT_ATTEMPTS, "test_run_id", "audit_run_strs"),
)

#: ``DeletionJob.holds_honoured.run_ids_kept`` is a sample, not a list of record.
_RUN_IDS_KEPT_MAX = 200


async def _holds_report(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    now: datetime,
    runs_cutoff: datetime,
    active_holds: list[Any],
    project_held: bool,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """The preview's ``holds`` block and the job's ``holds_honoured`` record.

    ``active`` lists EVERY live hold (one that keeps nothing today shows
    ``runs_kept: 0``); the per-hold counts come from the hold service's
    ``pinned_*`` reads, made only when a hold exists, before any delete.
    ``holds_honoured`` is ``None`` unless a hold covered something this purge
    would otherwise have deleted — a live PROJECT hold always does (every held
    class was skipped for it), any other hold when it kept a run past the runs
    cutoff or an expired pack. Fail closed: a raising read propagates.
    """
    block: dict[str, Any] = {"project_held": project_held, "runs_kept": 0, "packs_kept": 0, "active": []}
    if not active_holds:
        return block, None
    holds = _hold_service()
    runs_by_hold = await holds.pinned_runs_before(
        db, project_id=project_id, created_before=runs_cutoff, now=now,
    )
    packs_by_hold = await holds.pinned_expired_packs(db, project_id=project_id, now=now)
    all_runs: set[uuid.UUID] = set()
    all_packs: set[uuid.UUID] = set()
    covering: list[dict[str, Any]] = []
    for hold in active_holds:
        run_ids = set(runs_by_hold.get(hold.id, ()) or ())
        pack_ids = set(packs_by_hold.get(hold.id, ()) or ())
        all_runs |= run_ids
        all_packs |= pack_ids
        block["active"].append({**hold.as_json(), "runs_kept": len(run_ids), "packs_kept": len(pack_ids)})
        if hold.entity_type == "project" or run_ids or pack_ids:
            covering.append(hold.as_json())
    block["runs_kept"] = len(all_runs)
    block["packs_kept"] = len(all_packs)
    if not (project_held or all_runs or all_packs):
        return block, None
    honoured = {
        "project_held": project_held,
        "holds": covering,
        "runs_kept": len(all_runs),
        "packs_kept": len(all_packs),
        "run_ids_kept": sorted(str(r) for r in all_runs)[:_RUN_IDS_KEPT_MAX],
    }
    return block, honoured


async def _floor_report(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    keep_last_runs: int,
    cand: _Candidates,
) -> dict[str, Any]:
    """The preview's ``floor`` block. ``runs_kept`` comes from the two run
    selects already made (window minus purged — no statement); ONLY when the
    floor kept something, ONE statement classifies the kept runs for the two
    detail fields."""
    purged = set(cand.purge_run_ids)
    kept = [run_id for run_id in cand.runs_window_ids if run_id not in purged]
    block: dict[str, Any] = {
        "keep_last_runs": int(keep_last_runs),
        "runs_kept": len(kept),
        "latest_passed_kept": False,
        "suites_kept": 0,
    }
    if kept:
        rows = (await db.execute(_floor_kept_detail_statement(project_id, kept))).all()
        if rows:
            block["latest_passed_kept"] = bool(rows[0][0])
            block["suites_kept"] = int(rows[0][1] or 0)
    return block


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
    if mode == "execute":
        # W22a D-14: FIRST, FOR NO KEY UPDATE on the project row. It conflicts
        # with place_hold's FOR SHARE (a hold placed mid-purge waits, then
        # finds what is left) but not with ingestion's FK KEY SHARE. SQLAlchemy
        # spells it key_share=True without read=True. The preview takes no lock.
        await db.execute(
            select(Project.id).where(Project.id == project_id).with_for_update(key_share=True)
        )
    external_stores_injected = mongo is not None or storage is not None
    policy = await get_effective_policy(db, project_id, now=now)
    cutoffs = compute_cutoffs(policy, now)
    mongo = mongo if mongo is not None else get_mongo_db()
    storage = storage if storage is not None else get_storage_provider()

    # W22a ACT-05: the live holds, read BEFORE any candidate. FAIL CLOSED — a
    # failed read raises (the sweep records the job failed, nothing deleted;
    # the preview 500s); it never proceeds as "no holds".
    holds = _hold_service()
    active_holds = list(await holds.live_holds(db, project_id, now=now))
    project_held = any(h.entity_type == "project" for h in active_holds)
    held_ids: frozenset[uuid.UUID] = frozenset()
    if active_holds and not project_held:
        held_ids = frozenset(await holds.held_run_ids(db, project_id=project_id, now=now))

    # ── (1) Materialize BEFORE any delete ────────────────────────────────
    cand = await _collect_candidates(
        db, project_id, cutoffs,
        now=now, keep_last_runs=policy.keep_last_runs, held_run_ids=held_ids,
    )
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

    # Shared Postgres-side candidate counts / filters. W22a: every HELD class
    # appends its hold clause (contract §2.1 P-8 … P-14, P-16) — always a
    # NOT EXISTS anti-join, never NOT IN (a NULL entity_id would make NOT IN
    # match nothing and the purge would silently report success).
    event_archive_where = (
        TestRun.project_id == project_id,
        TestRun.event_archive_at < cutoffs["raw_events"],
        TestRun.event_archive.is_not(None),
        holds.run_not_held(TestRun.id, project_id, now=now),
    )
    access_audit_where = (
        AccessAuditLog.project_id == project_id,
        AccessAuditLog.created_at < cutoffs["audit"],
        holds.project_not_held(project_id, now=now),
    )
    tc_audit_where = (
        TestCaseAuditLog.project_id == project_id,
        TestCaseAuditLog.created_at < cutoffs["audit"],
        holds.project_not_held(project_id, now=now),
    )
    # Epic ACT. The activity ledger rides the AUDIT clock, not the runs clock:
    # audit_days is validated to be >= runs_days precisely so a record of what
    # happened to a run outlives the run itself.
    activity_where = (
        ProjectActivityEvent.project_id == project_id,
        ProjectActivityEvent.occurred_at < cutoffs["audit"],
        holds.activity_not_held(project_id, now=now),
    )
    provenance_where = (
        AIProvenanceRecord.project_id == project_id,
        AIProvenanceRecord.created_at < cutoffs["audit"],
        holds.run_not_held(AIProvenanceRecord.run_id, project_id, now=now),
    )
    deletion_job_where = (
        *deletion_job_purge_filter(project_id, cutoffs["audit"]),
        holds.project_not_held(project_id, now=now),
    )
    # S4 — a revoked share link is dead the moment it is revoked, but the row
    # only ever died via the run CASCADE, so a revoked link on a run that is
    # still inside its window lingered forever. Artifacts clock: the link is a
    # pointer to a report, not evidence about it.
    revoked_share_link_where = (
        ReportShareLink.project_id == project_id,
        ReportShareLink.is_revoked.is_(True),
        ReportShareLink.created_at < cutoffs["artifacts"],
        holds.run_not_held(ReportShareLink.run_id, project_id, now=now),
    )
    # P-15, P-21, P-22 (and the caches / search index below) are NOT held
    # (§7.2 D-7): derived, rebuildable, or fixed-window telemetry.
    memory_expired_where = (
        AgentMemoryEntry.project_id == project_id,
        AgentMemoryEntry.lifecycle_status == "active",
        AgentMemoryEntry.expires_at.is_not(None),
        AgentMemoryEntry.expires_at <= now,
    )
    # W12 SRCH-05. Search usage rows (no query text) ride a FIXED 30-day window,
    # not the audit clock (whose floor is 365 days): the nightly
    # purge_search_usage_events task covers every row deployment-wide, and this
    # class makes a project's purge preview list -- and its execute delete --
    # the project's rows past that window.
    search_usage_where = (
        SearchQueryEvent.project_id == project_id,
        SearchQueryEvent.created_at < usage_cutoff(now),
    )
    # W16 AGT-13: unlabelled shadow pairs ride the same kind of FIXED window (30 days); the nightly
    # expire_unlabelled_shadow_pairs task covers every project, this class the project's own purge.
    # Labelled pairs are G2 evidence and are never taken here.
    shadow_pairs_where = (
        AIEvalShadowPair.project_id == project_id,
        AIEvalShadowPair.label_status == "pending",
        AIEvalShadowPair.created_at < now - timedelta(days=SHADOW_PAIR_RETENTION_DAYS),
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
                holds.pack_not_held(CompliancePack.id, CompliancePack.release_id, project_id, now=now),
            )
        )
    ).scalars().all()

    # W22b E-12: a `retain` deletion archive rides its job row — it leaves when
    # the row does, on the AUDIT clock. ONE statement, both modes (the preview
    # counts them; execute deletes each object before its row).
    retained_archives = list(
        (
            await db.execute(
                select(DeletionJob.id, DeletionJob.export_location).where(
                    *deletion_job_where,
                    DeletionJob.export_location.is_not(None),
                )
            )
        ).all()
    )

    plan = _ExecutionPlan(
        event_archive_where=event_archive_where,
        access_audit_where=access_audit_where,
        tc_audit_where=tc_audit_where,
        activity_where=activity_where,
        deletion_job_where=deletion_job_where,
        revoked_share_link_where=revoked_share_link_where,
        provenance_where=provenance_where,
        memory_expired_ids=memory_expired_ids,
        expired_packs=expired_packs,
        retained_archives=retained_archives,
    )

    # AIH-06. Both counted BEFORE any delete and in BOTH modes, so the preview
    # reports exactly what the execute removes: the per-test versions the run
    # delete will cascade, and the ONE over-cap plan (runs being purged left
    # out — their versions go with them). An unreadable citation store yields
    # an empty plan: nothing is pruned over the cap until the next sweep.
    ai_cascaded = await ai_history_retention.count_cascaded(db, cand.purge_run_ids)
    # W22a P-18: held runs join the exclusions; a live project hold skips the
    # over-cap sweep entirely (an empty plan deletes nothing).
    if project_held:
        ai_plan = ai_history_retention.OverCapPlan()
    else:
        ai_exclude: list[uuid.UUID] = cand.purge_run_ids
        if held_ids:
            purge_set = set(cand.purge_run_ids)
            ai_exclude = [*cand.purge_run_ids, *sorted((r for r in held_ids if r not in purge_set), key=str)]
        ai_plan = await ai_history_retention.plan_over_cap(
            db,
            mongo,
            project_id=project_id,
            caps=policy.ai_history_caps,
            exclude_run_ids=ai_exclude,
        )

    # W22a: what the holds, the floor and the SET NULL references mean for
    # THIS purge — computed in both modes, BEFORE any delete, so the preview
    # reports exactly what the execute records.
    holds_block, holds_honoured = await _holds_report(
        db, project_id=project_id, now=now, runs_cutoff=cutoffs["runs"],
        active_holds=active_holds, project_held=project_held,
    )
    floor_block = await _floor_report(
        db, project_id=project_id, keep_last_runs=policy.keep_last_runs, cand=cand,
    )
    references = await retention_references.references_broken(db, cand.purge_run_ids)

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
                + await _count(ProjectActivityEvent.id, activity_where)
                + await _count(DeletionJob.id, deletion_job_where)
            ),
            "revoked_share_links": await _count(
                ReportShareLink.id, revoked_share_link_where
            ),
            "provenance_rows": await _count(AIProvenanceRecord.id, provenance_where),
            "compliance_packs_expired": len(expired_packs),
            # W22b E-12: retained deletion archives leaving with their job rows.
            "export_archives_expired": len(retained_archives),
            "analysis_cache_entries": _sum_measured(cache_counts.values()),
            "search_index_documents": search_index_documents,
            "memory_entries_expired": len(memory_expired_ids),
            "ai_history_versions": (
                mongo_docs.get(Collections.AI_RUN_VERSIONS, 0)
                + ai_cascaded
                + ai_plan.total
            ),
            "search_usage_rows": await _count(SearchQueryEvent.id, search_usage_where),
            "shadow_pairs_unlabelled": await _count(AIEvalShadowPair.id, shadow_pairs_where),
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
            # W22a: top-level facts, never inside ``candidates`` (whose keys
            # are class COUNTS only — the preview guard).
            "holds": holds_block,
            "floor": floor_block,
            "references_broken": references.items,
            "references_measured": references.measured,
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
    # W12 SRCH-05: beside the audit-class deletes, the project's search usage
    # rows past the fixed 30-day window -- the class the preview counted. Kept
    # out of execute_candidates on purpose: that executor is shared with the
    # per-run delete, which must never take project-wide rows.
    usage_result = await db.execute(delete(SearchQueryEvent).where(*search_usage_where))
    counts["postgres"]["search_usage_rows"] = int(getattr(usage_result, "rowcount", 0) or 0)
    # W16 AGT-13: the project's unlabelled shadow pairs past 30 days -- the class the preview counted
    # (project-wide rows, so beside the search-usage delete and outside execute_candidates for the same reason).
    shadow_result = await db.execute(delete(AIEvalShadowPair).where(*shadow_pairs_where))
    counts["postgres"]["shadow_pairs_unlabelled"] = int(getattr(shadow_result, "rowcount", 0) or 0)
    # The cap sweep runs on the SURVIVING runs, so after the run delete; it
    # deletes exactly ``ai_plan`` (the object the preview counted).
    over_cap = await ai_history_retention.execute_over_cap(db, mongo, ai_plan)
    counts["ai_history"] = {
        "analysis_versions_cascaded": ai_cascaded,
        "analysis_versions_over_cap": over_cap["analysis_versions_over_cap"],
        "run_versions_over_cap": over_cap["run_versions_over_cap"],
    }

    logger.info(
        "retention_purge_executed",
        project_id=str(project_id),
        cutoffs=cutoffs_iso,
        counts=counts,
        holds_honoured=holds_honoured is not None,
        floor_runs_kept=floor_block["runs_kept"],
    )
    return {
        "mode": "execute",
        "cutoffs": cutoffs_iso,
        "counts": counts,
        "holds": holds_block,
        "floor": floor_block,
        "references_broken": references.items,
        "references_measured": references.measured,
        # For the sweep to pass to close_job unchanged (contract §1.4).
        "holds_honoured": holds_honoured,
        "references_job": references.job_record(),
    }



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
        activity_where=never,
        deletion_job_where=never,
        # Share links are ondelete=CASCADE on run_id, so the run delete
        # already takes them. A filter here would be a second deletion of
        # rows that are already gone.
        revoked_share_link_where=never,
        provenance_where=never,
        memory_expired_ids=[],
        expired_packs=[],
        retained_archives=[],
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

    # (3.8) W22a ACT-04 F-5 — mark the runs that lose their BASELINE before the
    # delete NULLs the link (``run_baselines.baseline_run_id`` is SET NULL;
    # ``baseline_build_number`` is plain text and stays). The FIRST purge date
    # sticks. Rows whose own run is purged in the same chunk cascade away a
    # moment later — harmless. This is the ONE executor, so explicit deletes
    # stamp too (D-19).
    baselines_marked = 0
    for chunk in _chunks(cand.purge_run_ids, _PG_DELETE_CHUNK):
        result = await db.execute(
            update(RunBaseline)
            .where(
                RunBaseline.baseline_run_id.in_(chunk),
                RunBaseline.baseline_purged_at.is_(None),
            )
            .values(baseline_purged_at=now)
        )
        baselines_marked += int(getattr(result, "rowcount", 0) or 0)

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
    activity_result = await db.execute(
        delete(ProjectActivityEvent).where(*plan.activity_where)
    )
    # W22b E-12: a retained deletion archive leaves with its job row — object
    # FIRST, row second (the expired-pack rule below): a row whose object
    # delete failed survives, so the next sweep retries. Never logs the key.
    archives_deleted = 0
    archive_delete_failed: list[uuid.UUID] = []
    for job_id, location in plan.retained_archives:
        try:
            await storage.delete_object(location, bucket=_COMPLIANCE_BUCKET)
            archives_deleted += 1
        except Exception as exc:  # noqa: BLE001 — per-archive isolation
            archive_delete_failed.append(job_id)
            logger.warning(
                "retention_export_archive_delete_failed",
                project_id=str(project_id),
                job_id=str(job_id),
                error_type=type(exc).__name__,
            )
    deletion_job_delete = delete(DeletionJob).where(*plan.deletion_job_where)
    if archive_delete_failed:
        deletion_job_delete = deletion_job_delete.where(DeletionJob.id.not_in(archive_delete_failed))
    deletion_job_result = await db.execute(deletion_job_delete)
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
            "activity_rows": int(getattr(activity_result, "rowcount", 0) or 0),
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
        # NOT under "postgres": those values are summed as "records removed";
        # a marked baseline row is updated, not removed.
        "baselines": {"marked_purged": baselines_marked},
        # W22b E-12: object-store archives, not rows — kept out of "postgres".
        "exports": {"archives_deleted": archives_deleted},
    }

    return counts