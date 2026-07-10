"""SQLAlchemy ORM models — all PostgreSQL tables."""
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import Base

# Re-export stable enums so existing imports from this module keep working
from app.models.enums import (  # noqa: F401
    CriticalityLevel,
    ExecutionPath,
    InvestigationDepth,
    RegressionClassification,
    SearchType,
    WorkflowType,
)


# ── Enums ────────────────────────────────────────────────────

class TestStatus(str, PyEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BROKEN = "BROKEN"
    UNKNOWN = "UNKNOWN"


class LaunchStatus(str, PyEnum):
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class IngestionSource(str, PyEnum):
    """How a TestRun's results entered TestLookup.

    * ``live``   — SDK live-stream session (events buffered in Redis, drained).
    * ``sdk``    — SDK/CI batch POST to /api/v1/ingest (JSON results).
    * ``upload`` — operator manually uploaded a report file via the UI.
    * ``file``   — Allure/TestNG file landed via the MinIO webhook (sentinel) path.
    * ``unknown``— pre-migration rows whose origin couldn't be inferred.
    """
    LIVE = "live"
    SDK = "sdk"
    UPLOAD = "upload"
    FILE = "file"
    UNKNOWN = "unknown"


class FailureCategory(str, PyEnum):
    PRODUCT_BUG = "PRODUCT_BUG"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    TEST_DATA = "TEST_DATA"
    AUTOMATION_DEFECT = "AUTOMATION_DEFECT"
    FLAKY = "FLAKY"
    UNKNOWN = "UNKNOWN"


class Severity(str, PyEnum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    TRIVIAL = "TRIVIAL"


class UserRole(str, PyEnum):
    VIEWER = "VIEWER"
    TESTER = "TESTER"
    QA_ENGINEER = "QA_ENGINEER"
    QA_LEAD = "QA_LEAD"
    ADMIN = "ADMIN"


class TriageStatus(str, PyEnum):
    """Per-failure triage workflow state (migration 0088).

    Distinct from ``TestStatus`` (which is the test's execution outcome).
    Every auto-assigned FAILED/BROKEN TestCase starts at ``PENDING_REVIEW``
    and is moved off the ``/my-failures`` inbox once the assignee or a
    QA Lead picks one of the resolved states.
    """
    PENDING_REVIEW         = "PENDING_REVIEW"         # default — appears on /my-failures
    REVIEWED_APPROVED      = "REVIEWED_APPROVED"      # no action — accepted as-is after review
    DEFECT_CREATED         = "DEFECT_CREATED"         # defect/bug logged; notes hold the link
    WONT_FIX               = "WONT_FIX"               # deprecated test / accepted failure
    AUTOMATION_SCRIPT_ISSUE = "AUTOMATION_SCRIPT_ISSUE"  # test code bug, not a product bug
    FLAKY_TEST             = "FLAKY_TEST"             # nondeterministic — quarantine candidate


class NotificationChannel(str, PyEnum):
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"


class NotificationEventType(str, PyEnum):
    RUN_FAILED = "run_failed"
    RUN_PASSED = "run_passed"
    HIGH_FAILURE_RATE = "high_failure_rate"
    AI_ANALYSIS_COMPLETE = "ai_analysis_complete"
    QUALITY_GATE_FAILED = "quality_gate_failed"
    FLAKY_TEST_DETECTED = "flaky_test_detected"
    # ── Transition events (PMF US-7.1) — fire on state CHANGES, never
    # per-run. Evaluated by services/notification_transitions.py against
    # the notification_test_states store; routed through the same
    # NotificationPreference.events lists as the per-run events above.
    TEST_NEWLY_FAILING = "test.newly_failing"
    TEST_RECOVERED = "test.recovered"
    TEST_NEWLY_FLAKY = "test.newly_flaky"
    TEST_QUARANTINED = "test.quarantined"
    TEST_UNQUARANTINED = "test.unquarantined"
    # ── Quarantine lifecycle events (PMF US-5.4 / US-5.5) — event-driven
    # from flaky_quarantine_service hook points, same as the two above.
    TEST_QUARANTINE_STALE = "test.quarantine_stale"
    TEST_READY_TO_UNQUARANTINE = "test.ready_to_unquarantine"


# ── Models ───────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(20), default=UserRole.VIEWER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # True for self-registered users until they complete their first-time password reset
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # Avatar colour slug chosen by the user (e.g. "blue", "emerald"); null = default slate
    avatar_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    jira_project_key: Mapped[Optional[str]] = mapped_column(String(50))
    splunk_index: Mapped[Optional[str]] = mapped_column(String(255))
    ocp_namespace: Mapped[Optional[str]] = mapped_column(String(255))
    jenkins_job_pattern: Mapped[Optional[str]] = mapped_column(String(500))
    component_owner_map: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # added migration 0020
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # added migration 0022
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))     # added migration 0022
    tags: Mapped[Optional[list]] = mapped_column(JSON)                                # added migration 0022
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    manager_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(                     # added migration 0076
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Default QA lead — every new TestSuite materialised during ingest gets a
    # TestSuiteOwner row pointing here (migration 0079). When set, this is the
    # first fallback in the owner-resolution chain (TestSuiteOwner row →
    # default_qa_lead_user_id → manager_user_id). Enforced as QA_LEAD on this
    # project (or ADMIN) at the application layer — no CHECK constraint here
    # because ProjectMember.role is the source of truth.
    default_qa_lead_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(             # added migration 0079
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    test_runs: Mapped[list["TestRun"]] = relationship("TestRun", back_populates="project", lazy="dynamic")
    quality_gates: Mapped[list["QualityGate"]] = relationship("QualityGate", back_populates="project")
    test_suites: Mapped[list["TestSuite"]] = relationship(
        "TestSuite", back_populates="project", cascade="all, delete-orphan"
    )
    canonical_test_cases: Mapped[list["CanonicalTestCase"]] = relationship(
        "CanonicalTestCase", back_populates="project", cascade="all, delete-orphan"
    )


class TestRun(Base):
    """Represents a single CI/CD pipeline execution (Jenkins build)."""
    __tablename__ = "test_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "build_number", "jenkins_job", name="uq_test_run_build"),
        Index("ix_test_runs_project_status", "project_id", "status"),
        Index("ix_test_runs_created_at", "created_at"),
        # P3-3: Composite index for analytics queries that filter by project + status + time range
        Index("ix_test_runs_project_status_created", "project_id", "status", "created_at"),
        # US-4.3: PR-scoped lookups ("all runs for PR N") for PR summary comments.
        # Partial — most runs have no PR; keeps the index tiny.
        Index(
            "ix_test_runs_project_pr",
            "project_id", "pr_number",
            postgresql_where=text("pr_number IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    build_number: Mapped[str] = mapped_column(String(100), nullable=False)
    jenkins_job: Mapped[Optional[str]] = mapped_column(String(500))
    trigger_source: Mapped[Optional[str]] = mapped_column(String(50))  # push | schedule | manual
    branch: Mapped[Optional[str]] = mapped_column(String(255))
    commit_hash: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[LaunchStatus] = mapped_column(String(20), default=LaunchStatus.IN_PROGRESS)
    # How this run's results entered TestLookup (live | sdk | upload | file |
    # unknown). Drives the "Uploaded" badge and lets dashboards distinguish
    # manual uploads from CI-driven runs. See IngestionSource. (migration 0091)
    ingestion_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default=IngestionSource.UNKNOWN.value, server_default="unknown"
    )

    # CI context (US-4.3, migration 0101) — populated by SDK/CLI auto-detection
    # from standard CI env vars, or explicitly by API callers. pr_number is the
    # anchor for PR-scoped features (sticky PR summary comments, commit
    # attribution); ci_run_url deep-links back to the CI job.
    ci_provider: Mapped[Optional[str]] = mapped_column(String(30))    # github_actions|jenkins|gitlab_ci|azure_devops|circleci|other
    ci_repo: Mapped[Optional[str]] = mapped_column(String(300))       # e.g. "org/repo"
    pr_number: Mapped[Optional[int]] = mapped_column(Integer)
    ci_actor: Mapped[Optional[str]] = mapped_column(String(120))
    ci_run_url: Mapped[Optional[str]] = mapped_column(String(1000))

    # Aggregated counts
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    passed_tests: Mapped[int] = mapped_column(Integer, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, default=0)
    skipped_tests: Mapped[int] = mapped_column(Integer, default=0)
    broken_tests: Mapped[int] = mapped_column(Integer, default=0)
    pass_rate: Mapped[Optional[float]] = mapped_column(Float)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    # OpenShift metadata
    ocp_pod_name: Mapped[Optional[str]] = mapped_column(String(255))
    ocp_node: Mapped[Optional[str]] = mapped_column(String(255))
    ocp_namespace: Mapped[Optional[str]] = mapped_column(String(255))
    ocp_metadata: Mapped[Optional[dict]] = mapped_column(JSON)

    # S3 references
    minio_prefix: Mapped[Optional[str]] = mapped_column(String(1000))
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)          # TG-1: custom + system tags

    # Suite attribution — populated by ingestion finalize from TestCase.suite_name.
    # primary_suite_name = dominant suite (most test cases; alphabetical tiebreak),
    # suite_names = full sorted list of distinct suites in this run.
    primary_suite_name: Mapped[Optional[str]] = mapped_column(String(500), index=True)
    suite_names: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # 15-day durable archive of the raw SDK events for live-stream runs.
    # Written at session-close time so /runs/{id}/recover-live can replay
    # test_case rows long after the 25-hour Redis buffer TTL has lapsed
    # (migration 0086, 2026-05-16). Null for non-live runs and for live
    # runs whose archive has been purged after the 15-day window.
    event_archive: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    event_archive_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="test_runs")
    test_cases: Mapped[list["TestCase"]] = relationship("TestCase", back_populates="test_run", lazy="dynamic")


class TestCase(Base):
    """Individual test case result within a run."""
    __tablename__ = "test_cases"
    __table_args__ = (
        # One row per logical test per run. The file-ingestion path
        # (services/ingestion._upsert_test_case) already treats
        # (test_run_id, test_fingerprint) as the idempotency key; this
        # constraint enforces the same contract for the live-stream bulk
        # insert (worker/tasks.persist_live_session), so a task retry after
        # a partial commit can't create duplicate per-test rows. Added in
        # migration 0089.
        UniqueConstraint(
            "test_run_id", "test_fingerprint",
            name="uq_test_cases_run_fingerprint",
        ),
        Index("ix_test_cases_run_status", "test_run_id", "status"),
        # Run-scoped suite-breakdown reads (coverage/summary/suite_history) —
        # migration 0090. Sibling of ix_test_cases_run_status.
        Index("ix_test_cases_run_suite", "test_run_id", "suite_name"),
        Index("ix_test_cases_fingerprint", "test_fingerprint"),
        Index("ix_test_cases_search", "search_vector", postgresql_using="gin"),
        Index("ix_test_cases_canonical", "canonical_test_case_id"),
        # Hot path: ``/my-failures`` filters by ``assigned_to_user_id +
        # triage_status = 'PENDING_REVIEW'``. Composite index keeps the
        # inbox query a single index scan (added migration 0088).
        Index(
            "ix_test_cases_assignee_triage",
            "assigned_to_user_id", "triage_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    test_fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # hash(test_name + class_name)
    # Junction to the project-scoped catalog. Populated by ingestion (migration 0075).
    # SET NULL on canonical deletion so existing run rows survive a suite cleanup.
    canonical_test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("canonical_test_cases.id", ondelete="SET NULL"), nullable=True
    )

    # Core fields from Allure/TestNG
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(2000))
    suite_name: Mapped[Optional[str]] = mapped_column(String(500))
    class_name: Mapped[Optional[str]] = mapped_column(String(500))
    package_name: Mapped[Optional[str]] = mapped_column(String(500))
    status: Mapped[TestStatus] = mapped_column(String(20), nullable=False, default=TestStatus.UNKNOWN)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    # Allure labels
    severity: Mapped[Optional[Severity]] = mapped_column(String(20))
    feature: Mapped[Optional[str]] = mapped_column(String(500))
    story: Mapped[Optional[str]] = mapped_column(String(500))
    epic: Mapped[Optional[str]] = mapped_column(String(500))
    owner: Mapped[Optional[str]] = mapped_column(String(255))
    tags: Mapped[Optional[list]] = mapped_column(JSON)

    # Failure info
    failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Per-execution assignee — set by ``finalize_run`` for every FAILED/BROKEN
    # TestCase in the run (migration 0080). Resolution order at assignment
    # time: TestSuiteOwner row for the suite → Project.default_qa_lead_user_id
    # → Project.manager_user_id → NULL. Historical rows retain the owner they
    # were assigned to at ingest time; reassigning a suite owner later does
    # NOT retroactively rewrite past assignments — preserve the action ledger.
    assigned_to_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(            # added migration 0080
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Triage workflow (migration 0088) ────────────────────────────
    # Every auto-assigned FAILED/BROKEN row starts at PENDING_REVIEW.
    # The /my-failures inbox filters to PENDING_REVIEW only; moving to
    # any other status removes the row from the assignee's queue. PASSED
    # / SKIPPED rows carry PENDING_REVIEW too but no UI surfaces them —
    # the default keeps the column NOT NULL without a per-status branch
    # in the assigner.
    triage_status: Mapped[TriageStatus] = mapped_column(
        String(30),
        nullable=False,
        default=TriageStatus.PENDING_REVIEW.value,
        server_default=TriageStatus.PENDING_REVIEW.value,
    )
    # Free-form notes set when the assignee moves status off PENDING_REVIEW —
    # typically a Jira link for DEFECT_CREATED or a rationale for
    # WONT_FIX / REVIEWED_APPROVED. Cap is 2k chars to keep the row
    # bounded; longer write-ups belong on the linked defect.
    triage_notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    triage_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    triage_updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Per-run granular metadata (migration 0093) ──────────────────
    # Nullable, populated by the Allure/pytest parsers for the run that
    # produced this row. Unlike the step/attachment snapshot (which is
    # latest-run-only on the canonical anchor), these live on each per-run
    # ``test_cases`` row. retry_count = # of retries Allure recorded;
    # is_flaky_run = the run-level flaky flag the framework reported;
    # stack_trace = full failure trace (error_message is the short head);
    # step_count = # of top-level steps captured for the snapshot.
    retry_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_flaky_run: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    stack_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    step_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # S3 reference
    minio_s3_prefix: Mapped[Optional[str]] = mapped_column(String(1000))
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)

    # Full-text search vector
    search_vector: Mapped[Optional[Any]] = mapped_column(TSVECTOR)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    test_run: Mapped["TestRun"] = relationship("TestRun", back_populates="test_cases")
    history: Mapped[list["TestCaseHistory"]] = relationship("TestCaseHistory", back_populates="test_case")
    ai_analysis: Mapped[Optional["AIAnalysis"]] = relationship("AIAnalysis", back_populates="test_case", uselist=False)
    defects: Mapped[list["Defect"]] = relationship("Defect", back_populates="test_case")
    canonical_test_case: Mapped[Optional["CanonicalTestCase"]] = relationship(
        "CanonicalTestCase", back_populates="test_cases"
    )


class TestSuite(Base):
    """Project-scoped grouping of test cases.

    Replaces the prior string-based ``suite_name`` model with a first-class
    entity. Every project gets a row with ``is_default=True`` named
    ``Default Suite ({project.name})`` — new test cases ingested without an
    explicit suite are auto-assigned to it.
    """
    __tablename__ = "test_suites"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_test_suites_project_name"),
        Index("ix_test_suites_project_id", "project_id"),
        Index(
            "ix_test_suites_project_default",
            "project_id",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    project: Mapped["Project"] = relationship("Project", back_populates="test_suites")
    canonical_test_cases: Mapped[list["CanonicalTestCase"]] = relationship(
        "CanonicalTestCase", back_populates="test_suite", cascade="all, delete-orphan"
    )


class TestSuiteOwner(Base):
    """Explicit owner for a (project, suite_name) pair (migration 0076).

    Keyed by suite_name (string) to match the legacy aggregated Test Suites
    view served from ``/api/v1/test-management/suites``. Falls back to
    ``Project.manager_user_id`` when no row exists for a given suite.
    """
    __tablename__ = "test_suite_owners"
    __table_args__ = (
        UniqueConstraint("project_id", "suite_name", name="uq_test_suite_owners_proj_suite"),
        Index("ix_test_suite_owners_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )


class SuiteRunReview(Base):
    """Human-in-the-loop overlay on AI analysis for a (run, suite) (migration 0076).

    Non-gating: the AI pipeline still completes runs without waiting for a
    review. State machine: pending → confirmed | acknowledged | review_later.
    Unique on ``(test_run_id, suite_name)`` so each run+suite has at most one
    review (later updates mutate the row instead of inserting).
    """
    __tablename__ = "suite_run_reviews"
    __table_args__ = (
        UniqueConstraint("test_run_id", "suite_name", name="uq_suite_run_reviews_run_suite"),
        Index("ix_suite_run_reviews_project_suite", "project_id", "suite_name"),
        Index("ix_suite_run_reviews_state", "state"),
        Index("ix_suite_run_reviews_test_run_id", "test_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
    test_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default=text("'pending'"))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )


SUITE_REVIEW_STATES = ("pending", "confirmed", "acknowledged", "review_later")


class TestExecutionReview(Base):
    """Per-TestCase human review overlay for AI-flagged failures (migration 0081).

    When the AI analysis pipeline flags a failure as ``requires_human_review``
    (model missing, low confidence, fallback path), the UI shows a "Pending
    Human Review" tag. This row records the human's verdict once they look:
    ``reviewed`` (AI was right), ``defect_filed`` (ticket created;
    ``defect_link`` captures the URL), ``false_positive`` (flake or test bug;
    downstream un-tags), or ``reproducible`` (failure confirmed locally,
    awaiting fix).

    Naming note: the older ``test_case_reviews`` table belongs to the
    managed-test-AUTHORING workflow (review of an authored test definition
    before it's published). This table is keyed on ``test_cases.id`` —
    the execution row — and is unrelated.

    One row per test_case_id (UNIQUE). Transitions mutate the row in-place;
    cross-test audit lives in ``test_case_audit_logs`` for cases that need a
    timeline. Keep this table small and queryable for the inbox + dashboards.
    """
    __tablename__ = "test_execution_reviews"
    __table_args__ = (
        UniqueConstraint("test_case_id", name="uq_ter_test_case_id"),
        CheckConstraint(
            "state IN ('pending_review', 'reviewed', 'defect_filed', "
            "'false_positive', 'reproducible')",
            name="ck_ter_state_valid",
        ),
        CheckConstraint(
            "state <> 'defect_filed' OR NULLIF(BTRIM(defect_link), '') IS NOT NULL",
            name="ck_ter_defect_link_required",
        ),
        Index("ix_ter_project_state", "project_id", "state"),
        Index("ix_ter_reviewed_by", "reviewed_by_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending_review",
        server_default=text("'pending_review'"),
    )
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    defect_link: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transitioned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now(),
    )


# State machine for TestExecutionReview. Transitions are validated in the
# application layer (test_execution_review_service). pending_review is the
# implicit initial state — rows are auto-inserted when the user first
# transitions an AI-flagged failure.
TEST_EXECUTION_REVIEW_STATES = (
    "pending_review",
    "reviewed",
    "defect_filed",
    "false_positive",
    "reproducible",
)


class CanonicalTestCase(Base):
    """Project-scoped test case identity.

    Merges the prior ``SuiteMembership`` lifecycle model with a relational
    anchor that the per-run ``test_cases`` table FKs to. Identified by
    ``(project_id, test_fingerprint)`` — one row per logical test per project.
    Every CanonicalTestCase belongs to exactly one TestSuite; manual re-linking
    moves the row between suites without losing run history.
    """
    __tablename__ = "canonical_test_cases"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "test_fingerprint", name="uq_canonical_test_cases_project_fp"
        ),
        Index("ix_ctc_project_id", "project_id"),
        Index("ix_ctc_test_suite_id", "test_suite_id"),
        Index("ix_ctc_project_status", "project_id", "status"),
        Index("ix_ctc_fingerprint", "test_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # RESTRICT so a suite with cases can't be silently dropped — UI must reassign or move cases first.
    test_suite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_suites.id", ondelete="RESTRICT"), nullable=False
    )
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    class_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Lifecycle (merged from suite_memberships). Values:
    #   status: active | deleted | needs_review
    #   source: execution | managed | linked
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="execution", server_default="execution")

    # Run lifecycle pointers (SET NULL — a run deletion shouldn't orphan the catalog row).
    first_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True
    )

    # Optional cross-link to the authored / AI-generated catalog.
    managed_test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("managed_test_cases.id", ondelete="SET NULL"), nullable=True
    )

    review_tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    project: Mapped["Project"] = relationship("Project", back_populates="canonical_test_cases")
    test_suite: Mapped["TestSuite"] = relationship("TestSuite", back_populates="canonical_test_cases")
    test_cases: Mapped[list["TestCase"]] = relationship("TestCase", back_populates="canonical_test_case")
    managed_test_case: Mapped[Optional["ManagedTestCase"]] = relationship("ManagedTestCase")
    # Latest-run-only granular snapshot (migration 0093). delete-orphan so the
    # ingestion delete-then-insert overwrite clears the prior snapshot when the
    # collection is reassigned; CASCADE in the DB covers canonical-row deletion.
    steps: Mapped[list["TestStep"]] = relationship(
        "TestStep",
        back_populates="canonical_test_case",
        cascade="all, delete-orphan",
        lazy="select",
    )
    attachments: Mapped[list["TestAttachment"]] = relationship(
        "TestAttachment",
        back_populates="canonical_test_case",
        cascade="all, delete-orphan",
        lazy="select",
    )


class TestStep(Base):
    """One granular step in the LATEST-RUN-ONLY snapshot for a logical test.

    LOCKED retention model (migration 0093): steps anchor to the project-scoped
    ``canonical_test_cases`` identity — exactly ONE snapshot per
    ``(project_id, test_fingerprint)`` — NOT to the per-run ``test_cases`` rows
    that accumulate. On ingest of a newer run, ingestion DELETEs this canonical
    test's steps and INSERTs the new ones inside the ingestion-pipeline
    transaction. ``source_test_run_id`` is provenance only (which run produced
    the snapshot). ``parent_step_id`` (self-FK, CASCADE) models nested steps.
    """
    __tablename__ = "test_steps"
    __table_args__ = (
        Index("ix_test_steps_canonical_ordinal", "canonical_test_case_id", "ordinal"),
        Index("ix_test_steps_parent", "parent_step_id"),
        Index("ix_test_steps_source_run", "source_test_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_test_cases.id", ondelete="CASCADE"), nullable=False
    )
    source_test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True
    )
    parent_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_steps.id", ondelete="CASCADE"), nullable=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    name: Mapped[str] = mapped_column(String(2000), nullable=False)
    keyword: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_ms: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    assertion_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assertion_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actual_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parameters: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    canonical_test_case: Mapped["CanonicalTestCase"] = relationship(
        "CanonicalTestCase", back_populates="steps"
    )
    # Self-referential nesting. delete-orphan + CASCADE so trimming a parent's
    # children (delete-then-insert) cleans up the subtree.
    children: Mapped[list["TestStep"]] = relationship(
        "TestStep",
        back_populates="parent",
        cascade="all, delete-orphan",
        lazy="select",
    )
    parent: Mapped[Optional["TestStep"]] = relationship(
        "TestStep", back_populates="children", remote_side="TestStep.id"
    )
    attachments: Mapped[list["TestAttachment"]] = relationship(
        "TestAttachment",
        back_populates="test_step",
        cascade="all, delete-orphan",
        lazy="select",
    )


class TestStepRun(Base):
    """Per-run, compact step-outcome history for cross-run step-flip analysis.

    Distinct from :class:`TestStep`, which is a LATEST-RUN-ONLY snapshot
    (delete+reinsert on every ingest) and therefore cannot answer "which step
    flipped between run N-1 and run N". ``test_step_runs`` instead RETAINS one
    flat row per ``(canonical_test_case_id, source_test_run_id, ordinal)`` so a
    test's step outcomes accumulate across runs (migration 0097).

    Deliberately compact: only the identity (ordinal/depth/name/keyword) and the
    per-run signal (status/duration_ms) needed to detect a step-flip. The heavy,
    PII-bearing columns (assertion_message/trace, expected/actual, parameters,
    attachments) live ONLY on the latest-run :class:`TestStep` snapshot and are
    NOT duplicated per run. ``source_test_run_id`` is CASCADE (the row IS about
    that run — when the run is deleted its step history goes with it), unlike the
    snapshot's SET NULL provenance pointer.

    Written by ingestion alongside the snapshot, idempotent per ``(canonical,
    run)`` (delete this run's rows then reinsert). NEVER committed by the
    service — the ingestion router owns the commit.
    """
    __tablename__ = "test_step_runs"
    __table_args__ = (
        # One row per step-position per run — the idempotency invariant — and
        # the composite index for both the per-(canonical, run) overwrite delete
        # and the per-canonical cross-run scan the step-flip analysis walks.
        UniqueConstraint(
            "canonical_test_case_id",
            "source_test_run_id",
            "ordinal",
            name="uq_test_step_runs_canonical_run_ordinal",
        ),
        # FK index for the run-deletion CASCADE.
        Index("ix_test_step_runs_source_run", "source_test_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_test_cases.id", ondelete="CASCADE"), nullable=False
    )
    source_test_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    name: Mapped[str] = mapped_column(String(2000), nullable=False)
    keyword: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestAttachment(Base):
    """Index-only attachment metadata for the latest-run snapshot.

    Phase 1 stores references (``source_ref``) only — bytes are not proxied.
    Anchored to the canonical test (CASCADE) like steps; ``test_step_id``
    (CASCADE, nullable) links a step-scoped attachment, NULL = test-level.
    Migration 0093.
    """
    __tablename__ = "test_attachments"
    __table_args__ = (
        Index("ix_test_attachments_canonical", "canonical_test_case_id"),
        Index("ix_test_attachments_step", "test_step_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("canonical_test_cases.id", ondelete="CASCADE"), nullable=False
    )
    test_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_steps.id", ondelete="CASCADE"), nullable=True
    )
    source_test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_ref: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    canonical_test_case: Mapped["CanonicalTestCase"] = relationship(
        "CanonicalTestCase", back_populates="attachments"
    )
    test_step: Mapped[Optional["TestStep"]] = relationship(
        "TestStep", back_populates="attachments"
    )


class TestCaseHistory(Base):
    """Denormalized history for fast timeline queries."""
    __tablename__ = "test_case_history"
    __table_args__ = (
        Index("ix_history_fingerprint_date", "test_fingerprint", "created_at"),
        # Covering variant (migration 0098) for the windowed flaky-count scan
        # (_count_flaky_tests): partition/order by (test_fingerprint, created_at)
        # then filter status — including status makes that scan index-only.
        Index("ix_history_fingerprint_date_status", "test_fingerprint", "created_at", "status"),
        # FK indexes added in migration 0082 — see
        # docs/DATABASE_AUDIT_2026-05-16.md (P1-4).
        Index("ix_history_test_case_id", "test_case_id"),
        Index("ix_history_test_run_id",  "test_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TestStatus] = mapped_column(String(20), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    test_case: Mapped["TestCase"] = relationship("TestCase", back_populates="history")


class AIAnalysis(Base):
    """Stored AI triage results per test case."""
    __tablename__ = "ai_analysis"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), unique=True)
    root_cause_summary: Mapped[Optional[str]] = mapped_column(Text)
    failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
    backend_error_found: Mapped[bool] = mapped_column(Boolean, default=False)
    pod_issue_found: Mapped[bool] = mapped_column(Boolean, default=False)
    is_flaky: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
    recommended_actions: Mapped[Optional[list]] = mapped_column(JSON)
    evidence_references: Mapped[Optional[list]] = mapped_column(JSON)
    # Tools actually invoked by the ReAct agent during investigation.
    # Empty list = fast-classifier path. Added in migration 0015.
    tools_used: Mapped[Optional[list]] = mapped_column(JSON)
    # Role-aware recommended actions keyed by qa/developer/sre/release_manager.
    # Added in migration 0016.
    role_actions: Mapped[Optional[dict]] = mapped_column(JSON)
    llm_provider: Mapped[Optional[str]] = mapped_column(String(50))
    llm_model: Mapped[Optional[str]] = mapped_column(String(100))
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Migration 0063: per-test decision audit. Contains the ``_audit`` dict
    # that analysis_agent builds at classification time — analysis_mode,
    # mode_requested, fallback_from, fallback_reason, confidence_adjustments,
    # retry_count, duration. Rendered by the decision-trail UI so QA leads
    # can answer "why did the AI choose this engine for this test?" without
    # querying Mongo event logs.
    routing_metadata: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Relationships
    test_case: Mapped["TestCase"] = relationship("TestCase", back_populates="ai_analysis")


class Defect(Base):
    """Defect records linked to test cases or failure clusters."""
    __tablename__ = "defects"
    __table_args__ = (
        Index(
            "ix_defects_test_case_open_unique",
            "test_case_id",
            unique=True,
            postgresql_where=text("resolution_status = 'OPEN' AND test_case_id IS NOT NULL"),
        ),
        # FK index added in migration 0082 — see
        # docs/DATABASE_AUDIT_2026-05-16.md (P1-4).
        Index("ix_defects_project_id", "project_id"),
        # Composite for the release-gate open-CRITICAL count (migration 0098):
        # count_open_critical_defects filters (project_id, resolution_status,
        # severity) on every /release-gate + /overview render.
        Index(
            "ix_defects_project_status_severity",
            "project_id",
            "resolution_status",
            "severity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # nullable since migration 0019 — cluster promotion may not map to a single test case
    test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    jira_ticket_id: Mapped[Optional[str]] = mapped_column(String(50))
    jira_ticket_url: Mapped[Optional[str]] = mapped_column(String(1000))
    jira_status: Mapped[Optional[str]] = mapped_column(String(50))
    ai_confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
    failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
    resolution_status: Mapped[str] = mapped_column(String(50), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Cluster promotion fields — added migration 0019
    cluster_id: Mapped[Optional[str]] = mapped_column(String(255))
    title: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text())
    severity: Mapped[Optional[str]] = mapped_column(String(20))  # CRITICAL/HIGH/MEDIUM/LOW
    component: Mapped[Optional[str]] = mapped_column(String(255))
    owner_team: Mapped[Optional[str]] = mapped_column(String(255))
    labels: Mapped[Optional[list]] = mapped_column(JSON)
    criticality_scores: Mapped[Optional[dict]] = mapped_column(JSON)
    evidence_bundle: Mapped[Optional[dict]] = mapped_column(JSON)
    duplicate_of: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    promotion_source: Mapped[Optional[str]] = mapped_column(String(50))  # cluster_promotion | manual | jira_import (migration 0027)
    # Phase 4: Approval workflow (migration 0041)
    approval_status: Mapped[str] = mapped_column(String(20), default="approved")  # suggested | pending_review | approved | executed | rejected
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_evaluation: Mapped[Optional[dict]] = mapped_column(JSON)  # policy check result snapshot

    # Relationships
    test_case: Mapped[Optional["TestCase"]] = relationship("TestCase", back_populates="defects")
    approver: Mapped[Optional["User"]] = relationship("User", foreign_keys=[approved_by])


class DefectCandidate(Base):
    """Staging area for defect candidates before promotion to full defects."""
    __tablename__ = "defect_candidates"
    __table_args__ = (
        Index("ix_defect_cand_run", "run_id"),
        Index("ix_defect_cand_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    cluster_id: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="HIGH")
    owner_team: Mapped[Optional[str]] = mapped_column(String(255))
    component: Mapped[Optional[str]] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    duplicate_of: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_bundle: Mapped[Optional[dict]] = mapped_column(JSON)
    criticality_scores: Mapped[Optional[dict]] = mapped_column(JSON)
    composite_score: Mapped[Optional[float]] = mapped_column(Float)
    failure_category: Mapped[Optional[str]] = mapped_column(String(30))
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")  # pending | promoted | dismissed
    promoted_defect_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("defects.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class QualityGate(Base):
    """Quality gate rule configuration per project."""
    __tablename__ = "quality_gates"
    # FK index added in migration 0082 — see
    # docs/DATABASE_AUDIT_2026-05-16.md (P1-4).
    __table_args__ = (
        Index("ix_quality_gates_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    rules: Mapped[list] = mapped_column(JSON, default=list)  # List of rule objects
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="quality_gates")


class CoverageSnapshot(Base):
    """Daily test coverage snapshots for trend charts."""
    __tablename__ = "coverage_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total_suites: Mapped[int] = mapped_column(Integer, default=0)
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    automated_count: Mapped[int] = mapped_column(Integer, default=0)
    suite_coverage: Mapped[Optional[dict]] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("project_id", "snapshot_date", name="uq_coverage_project_date"),
    )


class NotificationPreference(Base):
    """Per-user, per-channel notification configuration."""
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", "channel", name="uq_notif_pref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # NULL project_id = applies to all projects
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    channel: Mapped[NotificationChannel] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # JSONB list of NotificationEventType values the user subscribed to.
    # Type promoted from JSON → JSONB in migration 0083 for consistency
    # with ``WebhookSubscription.events`` and so future "list users
    # subscribed to event X" queries can use a GIN index.
    events: Mapped[list] = mapped_column(JSONB, default=list)
    # Alert only when pass_rate falls below this percentage
    failure_rate_threshold: Mapped[Optional[float]] = mapped_column(Float, default=80.0)
    # Channel-specific overrides (if None, falls back to global settings)
    email_override: Mapped[Optional[str]] = mapped_column(String(255))
    slack_webhook_url: Mapped[Optional[str]] = mapped_column(String(2000))
    teams_webhook_url: Mapped[Optional[str]] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class NotificationTestState(Base):
    """Per-(project, test_fingerprint) rolling state for transition-based
    notifications (PMF US-7.1).

    The transition engine (``services/notification_transitions.py``) updates
    one row per logical test at run finalization and emits a notification
    only when the tracked state CHANGES (pass→confirmed-failing,
    failing→recovered, entered the known-flaky set). ``last_run_id`` is the
    idempotency anchor: re-finalizing the same run skips rows already
    stamped with that run, so transitions never double-fire (per-(entity,
    run) idempotency convention).
    """
    __tablename__ = "notification_test_states"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "test_fingerprint",
            name="uq_notif_test_state_project_fp",
        ),
        # Fingerprint lookups always carry project scope (test_fingerprint
        # has no project salt); the unique constraint above backs those.
        # This index serves "all tracked states for a project" sweeps.
        Index("ix_notif_test_states_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # Confirmed state of the machine: "passing" | "failing". A test flips to
    # "failing" only after consecutive_failures reaches the project threshold.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="passing")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The state we last actually notified about (post policy filtering).
    last_notified_state: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Whether the fingerprint was in the known-flaky set (quarantine ∪
    # flaky-coach cache) the last time it was evaluated. False→True emits
    # test.newly_flaky; seeded silently when the row is first created so a
    # long-flaky backlog doesn't flood on the first evaluated run.
    is_known_flaky: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Idempotency anchor — the run that last advanced this row.
    last_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class NotificationTransitionPolicy(Base):
    """Per-project policy for transition-based notifications (PMF US-7.1).

    One row per project. Semantics of a MISSING row = the new-project
    default: transitions ON, per-run spam OFF. Migration 0103 backfills an
    explicit row (transitions OFF, per-run ON) for every project existing
    at upgrade time so EXISTING projects keep their current behaviour with
    no surprise change; projects created afterwards get the new defaults.
    """
    __tablename__ = "notification_transition_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Master switch for the transition engine on this project.
    transitions_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Legacy per-run events (run_failed / run_passed / high_failure_rate).
    # False = "spam mode off": the run-completion fan-out is suppressed for
    # this project and only transition events notify.
    per_run_events_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # JSONB list of transition NotificationEventType values enabled for this
    # project (subset of the five test.* events).
    enabled_events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # N consecutive FAILED/BROKEN results before test.newly_failing fires.
    consecutive_failure_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class AgentPipelineRun(Base):
    """Tracks a single execution of the multi-agent pipeline for a test run."""
    __tablename__ = "agent_pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_test_run", "test_run_id"),
        Index("ix_pipeline_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(20), default="offline")  # offline | live | deep
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|completed|failed|partial
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Pipeline-level provenance (added in migration 0017)
    execution_metadata: Mapped[Optional[dict]] = mapped_column(JSON)    # {tools_used, schema_version, fallback_used}
    provenance_metadata: Mapped[Optional[dict]] = mapped_column(JSON)   # {generated_by, tools_used_count, generated_at}

    # Relationships
    stages: Mapped[list["AgentStageResult"]] = relationship("AgentStageResult", back_populates="pipeline_run", cascade="all, delete-orphan")


class AgentStageResult(Base):
    """Per-stage result for an AgentPipelineRun."""
    __tablename__ = "agent_stage_results"
    __table_args__ = (
        # pipeline_run_id index created in migration 0004 (ix_stage_results_pipeline);
        # declared here so the model reflects the real schema.
        Index("ix_stage_results_pipeline", "pipeline_run_id"),
        # (stage_name, status) backs agent_cost_service.check_alerts' 24h
        # repeated-failure count — migration 0090.
        Index("ix_agent_stage_results_stage_status", "stage_name", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_pipeline_runs.id", ondelete="CASCADE"), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(50), nullable=False)  # ingestion|anomaly|analysis|summary|triage
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending|running|completed|failed|skipped
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    result_data: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    # Stage-level skip context (added in migration 0017)
    skipped_reason: Mapped[Optional[str]] = mapped_column(Text)          # human-readable reason for skip
    execution_path: Mapped[Optional[str]] = mapped_column(String(50))    # ExecutionPath enum value
    fallback_used: Mapped[Optional[bool]] = mapped_column(Boolean)       # true when deterministic fallback ran
    # Stage-level checkpoint for pipeline resume (added in migration 0039)
    checkpoint_data: Mapped[Optional[dict]] = mapped_column(JSON)        # serialized stage output for resume
    # Phase 6: Agent observability & cost control (migration 0043)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    llm_calls_count: Mapped[Optional[int]] = mapped_column(Integer)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float)
    error_category: Mapped[Optional[str]] = mapped_column(String(30))    # transient | permanent | provider_error | token_limit | timeout
    confidence_score: Mapped[Optional[int]] = mapped_column(Integer)     # 0-100
    evidence_count: Mapped[Optional[int]] = mapped_column(Integer)       # number of evidence items produced
    route_rationale: Mapped[Optional[str]] = mapped_column(Text)         # why this stage was selected/skipped
    # Migration 0061: structured decision trail — ordered list of
    # {at, decision_point, chosen, rationale, context} records produced by
    # BaseAgent.log_decision(). This is the authoritative per-stage record
    # of *why* the agent chose each branch, used by the traceability UI.
    decision_log: Mapped[Optional[list]] = mapped_column(JSON)
    fallback_reason: Mapped[Optional[str]] = mapped_column(String(200))  # short reason when fallback_used=True
    analysis_mode: Mapped[Optional[str]] = mapped_column(String(20))     # llm|ml|rules|auto — engine actually used

    # Relationships
    pipeline_run: Mapped["AgentPipelineRun"] = relationship("AgentPipelineRun", back_populates="stages")


class ChatSession(Base):
    """A conversation session between a user and the Conversation Agent."""
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """A single message in a ChatSession."""
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_session", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[Optional[list]] = mapped_column(JSON)  # [{type, id, label}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")


class FeedbackRating(str, PyEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PARTIALLY_CORRECT = "partially_correct"


class AIFeedback(Base):
    """
    Human feedback on AI triage results — the primary training signal.

    Sources:
      - manual: engineer rates analysis card in the UI (explicit)
      - jira_resolved: Jira ticket created by AI was resolved (implicit positive)
      - jira_invalid: Jira ticket closed as invalid/won't-fix (implicit negative)
      - category_correction: engineer changed the failure_category in the UI
    """
    __tablename__ = "ai_feedback"
    __table_args__ = (
        Index("ix_ai_feedback_analysis", "analysis_id"),
        Index("ix_ai_feedback_created", "created_at"),
        Index("ix_ai_feedback_rating", "rating"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_analysis.id", ondelete="CASCADE"), nullable=False)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rating: Mapped[FeedbackRating] = mapped_column(String(25), nullable=False)
    corrected_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30), nullable=True)
    corrected_root_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # "manual" | "jira_resolved" | "jira_invalid" | "category_correction"
    source: Mapped[str] = mapped_column(String(50), default="manual")
    # Whether this record has been exported into a training batch
    exported: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ModelVersion(Base):
    """
    Registry of fine-tuned model versions per training track.

    Tracks the full lifecycle: training → evaluation → active/retired.
    The model_registry service uses this table + Redis for hot-swap lookups.
    """
    __tablename__ = "model_versions"
    __table_args__ = (
        Index("ix_model_versions_track_status", "track", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Track: "classifier" | "reasoning" | "embedding"
    track: Mapped[str] = mapped_column(String(30), nullable=False)
    # Human-readable model name (e.g. "qwen2.5:7b-testlookup-v3", "ft:gpt-4o-mini:testlookup-2025-07")
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    # Status: "training" | "evaluating" | "active" | "retired" | "failed"
    status: Mapped[str] = mapped_column(String(20), default="training")
    # Training metadata
    training_examples: Mapped[int] = mapped_column(Integer, default=0)
    holdout_examples: Mapped[int] = mapped_column(Integer, default=0)
    # Evaluation metrics
    eval_accuracy: Mapped[Optional[float]] = mapped_column(Float)
    baseline_accuracy: Mapped[Optional[float]] = mapped_column(Float)
    eval_details: Mapped[Optional[dict]] = mapped_column(JSON)
    # Provider-specific job ID (OpenAI fine-tuning job ID, Ollama model tag, etc.)
    provider_job_id: Mapped[Optional[str]] = mapped_column(String(200))
    # Path to JSONL training file in MinIO
    training_file_path: Mapped[Optional[str]] = mapped_column(String(1000))
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class FailureCluster(Base):
    """Semantic cluster of failures grouped by root-cause similarity."""
    __tablename__ = "failure_clusters"
    __table_args__ = (
        Index("ix_failure_clusters_run", "test_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_pipeline_runs.id", ondelete="SET NULL"), nullable=True)
    cluster_id: Mapped[str] = mapped_column(String(20), nullable=False)           # e.g. "cl_001"
    label: Mapped[str] = mapped_column(String(500), nullable=False)               # short human-readable label
    representative_error: Mapped[Optional[str]] = mapped_column(Text)
    member_test_ids: Mapped[list] = mapped_column(JSON, default=list)             # list[str] UUIDs
    size: Mapped[int] = mapped_column(Integer, default=1)
    cohesion_score: Mapped[Optional[float]] = mapped_column(Float)
    regression_classification: Mapped[Optional[str]] = mapped_column(String(50))  # added in migration 0018
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeepFinding(Base):
    """Deep investigation result per failure cluster."""
    __tablename__ = "deep_findings"
    __table_args__ = (
        Index("ix_deep_findings_run", "test_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    cluster_id: Mapped[str] = mapped_column(String(20), nullable=False)
    root_cause: Mapped[Optional[str]] = mapped_column(Text)
    failure_category: Mapped[Optional[FailureCategory]] = mapped_column(String(30))
    confidence_score: Mapped[Optional[int]] = mapped_column(Integer)
    causal_chain: Mapped[Optional[list]] = mapped_column(JSON)                    # list[{step, service, finding}]
    evidence: Mapped[Optional[list]] = mapped_column(JSON)                        # list[{source, excerpt}]
    affected_services: Mapped[Optional[list]] = mapped_column(JSON)               # list[str]
    contract_violations: Mapped[Optional[list]] = mapped_column(JSON)             # list[ContractViolation dicts]
    log_evidence: Mapped[Optional[dict]] = mapped_column(JSON)
    recommended_actions: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReleaseDecision(Base):
    """Release gate decision produced by ReleaseRiskAgent."""
    __tablename__ = "release_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)       # GO | NO_GO | CONDITIONAL_GO
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)  # 0-100
    blocking_issues: Mapped[Optional[list]] = mapped_column(JSON)
    conditions_for_go: Mapped[Optional[list]] = mapped_column(JSON)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    # 7-dimension deterministic scores (added in migration 0015)
    dimension_scores: Mapped[Optional[dict]] = mapped_column(JSON)
    composite_risk: Mapped[Optional[float]] = mapped_column(Float)
    score_model_version: Mapped[Optional[int]] = mapped_column(Integer)           # added in migration 0018
    human_override: Mapped[Optional[str]] = mapped_column(Text)                   # QA lead override reason
    overridden_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Release Council (migration 0021)
    input_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)                 # deterministic context assembly snapshot
    override_audit: Mapped[Optional[list]] = mapped_column(JSON)                 # [{timestamp, actor_id, actor_name, before, after, reason}]
    original_recommendation: Mapped[Optional[str]] = mapped_column(String(20))   # AI recommendation before override
    original_risk_score: Mapped[Optional[int]] = mapped_column(Integer)          # risk_score before override
    # Policy-based release gates (ENT-02, migration 0032)
    policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("release_gate_policies.id", ondelete="SET NULL"), nullable=True)
    policy_evaluation: Mapped[Optional[dict]] = mapped_column(JSON)              # full evaluation result
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class ContractViolation(Base):
    """API contract violation detected by ContractAgent."""
    __tablename__ = "contract_violations"
    __table_args__ = (
        Index("ix_contract_violations_run", "test_run_id"),
        Index("ix_contract_violations_tc", "test_case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[Optional[str]] = mapped_column(String(500))
    violation_type: Mapped[str] = mapped_column(String(50), nullable=False)       # missing_field|type_mismatch|schema_drift|constraint_violation
    field_path: Mapped[Optional[str]] = mapped_column(String(500))
    expected: Mapped[Optional[str]] = mapped_column(String(500))
    actual: Mapped[Optional[str]] = mapped_column(String(500))
    severity: Mapped[str] = mapped_column(String(20), default="warning")          # critical|warning|info
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationLog(Base):
    """Audit trail for every dispatched notification."""
    __tablename__ = "notification_logs"
    __table_args__ = (
        Index("ix_notif_log_user_created", "user_id", "created_at"),
        Index("ix_notif_log_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | sent | failed
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Test Case Management ──────────────────────────────────────────────────────

class ManagedTestCase(Base):
    """Manually authored or AI-generated test case with full lifecycle management."""
    __tablename__ = "managed_test_cases"
    __table_args__ = (
        Index("ix_mtc_project_status", "project_id", "status"),
        Index("ix_mtc_author", "author_id"),
        Index("ix_mtc_fingerprint", "test_fingerprint"),
        Index("ix_mtc_dup_fingerprint", "dup_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # Core content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    objective: Mapped[Optional[str]] = mapped_column(Text)          # What this test verifies
    preconditions: Mapped[Optional[str]] = mapped_column(Text)
    steps: Mapped[Optional[list]] = mapped_column(JSON)             # [{step_number, action, expected_result}]
    expected_result: Mapped[Optional[str]] = mapped_column(Text)
    test_data: Mapped[Optional[str]] = mapped_column(Text)          # Required test data / fixtures

    # Classification
    test_type: Mapped[str] = mapped_column(String(50), default="functional")  # unit|integration|e2e|performance|security|smoke|regression|functional
    priority: Mapped[str] = mapped_column(String(20), default="medium")       # critical|high|medium|low
    severity: Mapped[str] = mapped_column(String(20), default="major")        # blocker|critical|major|minor|trivial
    feature_area: Mapped[Optional[str]] = mapped_column(String(500))
    suite_name: Mapped[Optional[str]] = mapped_column(String(500))            # Legacy free-text suite label; superseded by test_suite_id when set.
    # Migration 0087 — structured anchor to the canonical suite entity.
    # Nullable so historical rows + create-without-suite paths keep working;
    # SET NULL on delete so a suite drop doesn't cascade authored cases away.
    test_suite_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_suites.id", ondelete="SET NULL"), nullable=True
    )
    tags: Mapped[Optional[list]] = mapped_column(JSON)              # list[str]

    # Lifecycle state machine
    # draft → review_requested → under_review → approved → active → deprecated
    # under_review → rejected → draft
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    # Attribution
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Automation linkage
    is_automated: Mapped[bool] = mapped_column(Boolean, default=False)
    automation_status: Mapped[str] = mapped_column(String(30), default="not_automated")  # not_automated|in_progress|automated|broken
    test_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))  # Links to executed TestCase

    # Duplicate detection (Phase 4, migration 0094). Normalised content hash
    # used as the Tier-0 exact-match blocking key; nullable + indexed
    # (ix_mtc_dup_fingerprint). Populated lazily by the detector — no backfill.
    dup_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # AI metadata
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_generation_prompt: Mapped[Optional[str]] = mapped_column(Text)
    ai_quality_score: Mapped[Optional[int]] = mapped_column(Integer)    # 0-100
    ai_review_notes: Mapped[Optional[dict]] = mapped_column(JSON)       # {issues, suggestions, score}

    # Execution tracking
    estimated_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    last_executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_execution_status: Mapped[Optional[str]] = mapped_column(String(20))  # PASSED|FAILED|BLOCKED

    # RAG lineage (RAG-11 / RAG-12)
    generation_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("generation_batches.id", ondelete="SET NULL", use_alter=True), nullable=True,
    )
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stale_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Tier 2 item 9 — RAG faithfulness guardrails.
    # Score in the range 0.0-1.0 produced by
    # ``services/rag_faithfulness_service.evaluate`` when the case was
    # generated. Null = never evaluated. The auto-accept path in the RAG
    # review workflow refuses to mark a case accepted below the
    # configured threshold and sets ``needs_review_reason`` to surface
    # the case on the review queue instead.
    faithfulness_score: Mapped[Optional[float]] = mapped_column(Float)
    faithfulness_evaluator: Mapped[Optional[str]] = mapped_column(String(30))  # ollama|ragas|human
    faithfulness_evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    needs_review_reason: Mapped[Optional[str]] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class TestCaseVersion(Base):
    """Immutable snapshot of a ManagedTestCase at each save."""
    __tablename__ = "test_case_versions"
    __table_args__ = (
        Index("ix_tcv_test_case", "test_case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Snapshot fields
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    steps: Mapped[Optional[list]] = mapped_column(JSON)
    expected_result: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    # Change metadata
    changed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    change_summary: Mapped[Optional[str]] = mapped_column(String(500))   # human label: "Updated steps 3-5"
    change_type: Mapped[str] = mapped_column(String(30), default="updated")  # created|updated|status_changed|approved|deprecated

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestCaseReview(Base):
    """Review cycle for a ManagedTestCase."""
    __tablename__ = "test_case_reviews"
    __table_args__ = (
        Index("ix_tcr_test_case", "test_case_id"),
        Index("ix_tcr_reviewer", "reviewer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Status: pending|in_progress|approved|rejected|changes_requested
    status: Mapped[str] = mapped_column(String(30), default="pending")

    # AI review output
    ai_review_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_quality_score: Mapped[Optional[int]] = mapped_column(Integer)
    ai_review_notes: Mapped[Optional[dict]] = mapped_column(JSON)    # {coverage_gaps, issues, suggestions, score_breakdown}
    ai_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Human review
    human_notes: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class TestCaseComment(Base):
    """Threaded comment on a ManagedTestCase."""
    __tablename__ = "test_case_comments"
    __table_args__ = (
        Index("ix_tcc_test_case", "test_case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    comment_type: Mapped[str] = mapped_column(String(30), default="general")  # general|review|suggestion|question
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_case_comments.id", ondelete="SET NULL"), nullable=True)
    step_number: Mapped[Optional[int]] = mapped_column(Integer)     # Optional: anchors comment to a step
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class DuplicateTestCaseCandidate(Base):
    """One detected near-duplicate PAIR of authored ManagedTestCases (Phase 4).

    Tiered, offline-first, per-project duplicate detection (migration 0094).
    Exactly one row per logical pair: the producer enforces canonical ordering
    ``case_a_id < case_b_id`` (also a DB CHECK), and the pair is idempotent on
    ``(project_id, case_a_id, case_b_id)``.

    EXPLAINABILITY is first-class: every row carries the component breakdown
    (``component_scores``), an aggregate ``score`` (0.0-1.0), a human-readable
    ``reason``, the detection ``method`` (fingerprint|structural|semantic), and a
    ``band`` (exact|strong|possible).

    MERGE IS NON-DESTRUCTIVE this phase: resolving a pair only flips ``status``
    (open → merged|dismissed) and may set a soft deprecate flag on the losing
    case. It never deletes cases or redirects ``test_fingerprint``.
    """
    __tablename__ = "duplicate_test_case_candidates"
    __table_args__ = (
        UniqueConstraint("project_id", "case_a_id", "case_b_id", name="uq_dup_candidate_pair"),
        CheckConstraint("case_a_id < case_b_id", name="ck_dup_candidate_canonical_order"),
        Index("ix_dup_candidate_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    case_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False
    )
    case_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False
    )
    band: Mapped[str] = mapped_column(String(20), nullable=False)        # exact|strong|possible
    score: Mapped[float] = mapped_column(Float, nullable=False)          # 0.0-1.0
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # human-readable explanation
    method: Mapped[str] = mapped_column(String(20), nullable=False)      # fingerprint|structural|semantic
    component_scores: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default=text("'open'"))


class DismissedDuplicatePair(Base):
    """Suppression record so a dismissed duplicate pair stays dismissed (Phase 4).

    Consulted by the detector before staging a candidate so a re-detection run
    never resurfaces a pair the user already dismissed. Same canonical ordering
    (``case_a_id < case_b_id``, DB CHECK) + uniqueness on
    ``(project_id, case_a_id, case_b_id)`` as the candidate table.
    """
    __tablename__ = "dismissed_duplicate_pairs"
    __table_args__ = (
        UniqueConstraint("project_id", "case_a_id", "case_b_id", name="uq_dismissed_dup_pair"),
        CheckConstraint("case_a_id < case_b_id", name="ck_dismissed_dup_canonical_order"),
        Index("ix_dismissed_dup_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    case_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False
    )
    case_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False
    )
    dismissed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestPlan(Base):
    """Named collection of test cases forming an executable test plan."""
    __tablename__ = "test_plans"
    __table_args__ = (
        Index("ix_tp_project", "project_id"),
        Index("ix_tp_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    objective: Mapped[Optional[str]] = mapped_column(Text)

    # Status: draft|active|in_progress|completed|archived
    status: Mapped[str] = mapped_column(String(30), default="draft")

    # Schedule
    planned_start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Attribution
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_generation_context: Mapped[Optional[str]] = mapped_column(Text)

    # Aggregates (denormalized for fast reads)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    executed_cases: Mapped[int] = mapped_column(Integer, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)
    failed_cases: Mapped[int] = mapped_column(Integer, default=0)
    blocked_cases: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)          # TG-1: custom tags

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class TestPlanItem(Base):
    """A test case entry within a TestPlan."""
    __tablename__ = "test_plan_items"
    __table_args__ = (
        Index("ix_tpi_plan", "plan_id"),
        UniqueConstraint("plan_id", "test_case_id", name="uq_plan_test_case"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False)

    order_index: Mapped[int] = mapped_column(Integer, default=0)
    priority_override: Mapped[Optional[str]] = mapped_column(String(20))  # overrides test case priority

    # Execution tracking
    # not_run|in_progress|passed|failed|blocked|skipped
    execution_status: Mapped[str] = mapped_column(String(30), default="not_run")
    executed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    execution_notes: Mapped[Optional[str]] = mapped_column(Text)
    actual_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)

    # Optional link to an actual automated test run result
    test_case_result_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestStrategy(Base):
    """AI-generated or manually authored test strategy document for a project."""
    __tablename__ = "test_strategies"
    __table_args__ = (
        Index("ix_ts_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    version_label: Mapped[str] = mapped_column(String(50), default="v1.0")
    status: Mapped[str] = mapped_column(String(30), default="draft")   # draft|active|archived

    # Strategy document sections
    objective: Mapped[Optional[str]] = mapped_column(Text)
    scope: Mapped[Optional[str]] = mapped_column(Text)
    out_of_scope: Mapped[Optional[str]] = mapped_column(Text)
    test_approach: Mapped[Optional[str]] = mapped_column(Text)
    risk_assessment: Mapped[Optional[list]] = mapped_column(JSON)      # [{risk, likelihood, impact, mitigation}]
    test_types: Mapped[Optional[list]] = mapped_column(JSON)           # [{type, priority, tools, coverage_target_pct}]
    entry_criteria: Mapped[Optional[list]] = mapped_column(JSON)       # list[str]
    exit_criteria: Mapped[Optional[list]] = mapped_column(JSON)        # list[str]
    environments: Mapped[Optional[list]] = mapped_column(JSON)         # [{name, type, purpose}]
    automation_approach: Mapped[Optional[str]] = mapped_column(Text)
    defect_management: Mapped[Optional[str]] = mapped_column(Text)

    # AI Generation metadata
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    generation_context: Mapped[Optional[str]] = mapped_column(Text)    # prompt / requirements text used
    ai_model_used: Mapped[Optional[str]] = mapped_column(String(100))

    # Attribution
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


# ── Knowledge Source Registry (RAG-1) ─────────────────────────────────────────


class KnowledgeSourceType(str, PyEnum):
    JIRA_ISSUE = "jira_issue"
    JIRA_EPIC = "jira_epic"
    CONFLUENCE_PAGE = "confluence_page"
    UPLOADED_DOC = "uploaded_document"
    INTERNAL_URL = "internal_url"
    EXTERNAL_URL = "external_url"


class KnowledgeSyncStatus(str, PyEnum):
    PENDING = "pending"
    SYNCING = "syncing"
    SYNCED = "synced"
    FAILED = "failed"
    SKIPPED = "skipped"


class KnowledgeClassification(str, PyEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class KnowledgeSource(Base):
    """Registry of external knowledge sources attached to a project for RAG-grounded test generation."""
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "canonical_url", name="uq_ks_project_url"),
        Index("ix_ks_project_type", "project_id", "source_type"),
        Index("ix_ks_project_status", "project_id", "sync_status"),
        Index("ix_ks_owner", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default=KnowledgeSyncStatus.PENDING.value)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    classification: Mapped[str] = mapped_column(String(20), nullable=False, default=KnowledgeClassification.INTERNAL.value)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    storage_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class KnowledgeSyncEvent(Base):
    """Audit log for every sync attempt on a KnowledgeSource."""
    __tablename__ = "knowledge_sync_events"
    __table_args__ = (
        Index("ix_kse_source_created", "source_id", "created_at"),
        Index("ix_kse_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    previous_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    content_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeChunk(Base):
    """PostgreSQL metadata for each chunk stored in ChromaDB knowledge_chunks collection."""
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_kc_source_active", "source_id", "is_active"),
        Index("ix_kc_project_active", "project_id", "is_active"),
        Index("ix_kc_sync_version", "source_id", "sync_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    vector_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    section_heading: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    requirement_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text_preview: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sync_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── RAG Generation Lineage (RAG-8 / RAG-9 / RAG-11 / RAG-12) ────────────────


class GenerationBatch(Base):
    """One row per AI test-case generation request (grounded or raw)."""
    __tablename__ = "generation_batches"
    __table_args__ = (
        Index("ix_gb_project_created", "project_id", "created_at"),
        Index("ix_gb_author", "created_by_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    prompt_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    generation_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="raw")
    generation_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    cases_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cases_accepted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cases_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt_redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    llm_model_used: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class GenerationCaseSource(Base):
    """Maps a generated ManagedTestCase to the KnowledgeChunks cited for it."""
    __tablename__ = "generation_case_sources"
    __table_args__ = (
        UniqueConstraint("case_id", "chunk_vector_id", name="uq_gcs_case_chunk"),
        Index("ix_gcs_case_id", "case_id"),
        Index("ix_gcs_batch_id", "batch_id"),
        Index("ix_gcs_source_id", "source_id"),
        Index("ix_gcs_stale", "is_stale"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_batches.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_test_cases.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False)
    chunk_vector_id: Mapped[str] = mapped_column(String(64), nullable=False)

    relevance_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    section_heading: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    chunk_text_preview: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    source_content_hash_at_generation: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stale_detected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementCoverage(Base):
    """Tracks which requirements are covered/uncovered by a generation batch."""
    __tablename__ = "requirement_coverage"
    __table_args__ = (
        UniqueConstraint("batch_id", "requirement_id", name="uq_rc_batch_req"),
        Index("ix_rc_batch_id", "batch_id"),
        Index("ix_rc_project_req", "project_id", "requirement_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_batches.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    requirement_id: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    coverage_status: Mapped[str] = mapped_column(String(20), nullable=False, default="uncovered")
    covered_by_case_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LiveSession(Base):
    """
    Tracks an active live test execution session from a client machine.

    Client machines register a session before streaming events.
    The session_token (stored as a SHA-256 hash here; plaintext lives in Redis)
    is used for lightweight authentication on the hot-path batch endpoint —
    avoiding JWT decode + DB query overhead at 10k+ concurrent sessions.
    """
    __tablename__ = "live_sessions"
    __table_args__ = (
        Index("ix_live_sessions_project_status", "project_id", "status"),
        Index("ix_live_sessions_token_hash", "session_token_hash"),
        Index("ix_live_sessions_started_at", "started_at"),
        # Partial UNIQUE: only one *active* session may exist per (project, run).
        # Completed/stale sessions do not participate, so a CI job can retry safely.
        # Protects against duplicate event streams when two runners race on the same run_id.
        Index(
            "ux_live_sessions_active_project_run",
            "project_id", "run_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # NOT a FK to ``test_runs.id`` despite the name. This is the
    # SDK-supplied build/run identifier (slug, e.g. "build-1042" or a
    # Jenkins-job-name+number string) that the client sends in the
    # ``X-Run-ID`` header on the streaming endpoints. The canonical
    # ``test_runs.id`` UUID is resolved from this slug via
    # ``canonical_test_run_uuid()`` in worker/tasks.py before any FK
    # write — see memory ``feedback_live_session_slug_vs_uuid.md`` for
    # the production incident that made this distinction expensive.
    run_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    machine_id: Mapped[Optional[str]] = mapped_column(String(255))
    build_number: Mapped[Optional[str]] = mapped_column(String(100))
    framework: Mapped[Optional[str]] = mapped_column(String(50))
    branch: Mapped[Optional[str]] = mapped_column(String(255))
    commit_hash: Mapped[Optional[str]] = mapped_column(String(64))
    # SHA-256 hash of the plaintext session token (never store plaintext)
    session_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|completed|stale
    release_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Human-readable launch label (analogous to ReportPortal's rp.launch).
    # Sourced from `testlookup.launch` in properties / system props / env vars
    # and surfaced in Live Execution and Runs columns. Nullable: legacy
    # sessions and clients that don't set it fall back to build_number.
    launch_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Run-level suite identifier supplied by the SDK (testlookup.suite, with
    # testlookup.launch as the fallback). Persisted so `upsert_test_run` can
    # stamp TestRun.primary_suite_name immediately at session close — no need
    # to wait for per-event aggregation.
    suite_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    events_received: Mapped[int] = mapped_column(Integer, default=0)
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestCaseAuditLog(Base):
    """Immutable compliance audit trail for all test management actions."""
    __tablename__ = "test_case_audit_logs"
    __table_args__ = (
        Index("ix_tcal_entity", "entity_type", "entity_id"),
        Index("ix_tcal_project_created", "project_id", "created_at"),
        Index("ix_tcal_actor", "actor_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)   # test_case|test_plan|test_strategy|review
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)

    # Action type
    action: Mapped[str] = mapped_column(String(50), nullable=False)        # created|updated|status_changed|reviewed|approved|rejected|deleted|assigned|executed|ai_generated|ai_reviewed

    # Actor (snapshot name in case user is deleted)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(200))

    # Change payload
    old_values: Mapped[Optional[dict]] = mapped_column(JSON)
    new_values: Mapped[Optional[dict]] = mapped_column(JSON)
    details: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# ── Suite Membership Traceability (TS-1) ─────────────────────────────────────


class SuiteMembership(Base):
    """Tracks which test cases belong to which suite, linked to the run that confirmed membership."""
    __tablename__ = "suite_memberships"
    __table_args__ = (
        UniqueConstraint("project_id", "suite_name", "test_fingerprint", name="uq_suite_membership"),
        Index("ix_sm_project_suite", "project_id", "suite_name"),
        Index("ix_sm_fingerprint", "test_fingerprint"),
        Index("ix_sm_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    class_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    managed_test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("managed_test_cases.id", ondelete="SET NULL"), nullable=True,
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="execution")  # execution | managed | linked
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")      # active | deleted | needs_review
    last_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    first_seen_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    deleted_at_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    review_tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)          # TG-4: custom suite tags
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class SuiteMembershipEvent(Base):
    """Immutable audit log of suite membership changes detected during sync."""
    __tablename__ = "suite_membership_events"
    __table_args__ = (
        Index("ix_sme_project_suite", "project_id", "suite_name", "created_at"),
        Index("ix_sme_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    suite_name: Mapped[str] = mapped_column(String(500), nullable=False)
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # added | deleted | modified | restored
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    old_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Release Management ────────────────────────────────────────────────────────

class Release(Base):
    """A software release tracked through the QA lifecycle."""
    __tablename__ = "releases"
    __table_args__ = (
        Index("ix_releases_project_status", "project_id", "status"),
        # Migration 0077: at most one default release per project.
        Index(
            "ix_releases_project_default",
            "project_id",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Status: planning|in_progress|released|cancelled
    status: Mapped[str] = mapped_column(String(30), default="planning")

    # Migration 0077: project-level default — used when ingestion / live
    # session create receives no explicit release_name. At most one row per
    # project (partial unique index above).
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Target/actual dates
    planned_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Attribution
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    phases: Mapped[list["ReleasePhase"]] = relationship(
        "ReleasePhase", back_populates="release", cascade="all, delete-orphan",
        order_by="ReleasePhase.order_index",
    )
    test_run_links: Mapped[list["ReleaseTestRunLink"]] = relationship(
        "ReleaseTestRunLink", back_populates="release", cascade="all, delete-orphan",
    )


class ReleasePhase(Base):
    """A phase / milestone within a Release lifecycle."""
    __tablename__ = "release_phases"
    __table_args__ = (
        Index("ix_release_phases_release", "release_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Phase type: planning|development|code_freeze|qa_testing|uat|staging|production
    phase_type: Mapped[str] = mapped_column(String(50), default="qa_testing")
    # Status: pending|in_progress|completed|skipped
    status: Mapped[str] = mapped_column(String(30), default="pending")
    description: Mapped[Optional[str]] = mapped_column(Text)

    order_index: Mapped[int] = mapped_column(Integer, default=0)

    planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Quality criteria for this phase (pass_rate_threshold, max_open_defects, etc.)
    exit_criteria: Mapped[Optional[dict]] = mapped_column(JSON)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    release: Mapped["Release"] = relationship("Release", back_populates="phases")


class ReleaseTestRunLink(Base):
    """Links a test run to a release for metrics aggregation."""
    __tablename__ = "release_test_run_links"
    __table_args__ = (
        UniqueConstraint("release_id", "test_run_id", name="uq_release_test_run"),
        Index("ix_rtr_links_release", "release_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), nullable=False)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    phase_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("release_phases.id", ondelete="SET NULL"), nullable=True)

    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    release: Mapped["Release"] = relationship("Release", back_populates="test_run_links")


class SecretRef(Base):
    """Stores sensitive values (API keys, tokens, passwords) separately from settings."""
    __tablename__ = "secret_refs"
    __table_args__ = (
        Index("ix_secret_refs_scope_key", "scope", "key_name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)          # e.g. "smtp", "ai_config", "integrations"
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="db")  # "db" | "vault" | "aws_sm"
    key_name: Mapped[str] = mapped_column(String(255), nullable=False)       # e.g. "jira_api_token"
    encrypted_value: Mapped[Optional[str]] = mapped_column(Text)             # stored encrypted; NULL if external
    masked_value: Mapped[Optional[str]] = mapped_column(String(50))          # e.g. "sk-...abc1"
    rotation_status: Mapped[str] = mapped_column(String(30), default="active")
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class AppSetting(Base):
    """Key-value store for application-level configuration (e.g. SMTP settings)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Secret backing (migration 0023)
    secret_ref_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("secret_refs.id", ondelete="SET NULL"), nullable=True)
    is_secret_backed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        server_default=func.now(),
    )


class SettingsAuditLog(Base):
    """Immutable audit trail for settings and secret changes."""
    __tablename__ = "settings_audit_log"
    __table_args__ = (
        Index("ix_settings_audit_key", "setting_key"),
        Index("ix_settings_audit_actor", "actor_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    setting_key: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)      # "created" | "updated" | "secret_rotated"
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(200))
    changed_fields: Mapped[Optional[list]] = mapped_column(JSON)         # field names only, no secret values
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── User Management ───────────────────────────────────────────────────────────

class ProjectMember(Base):
    """Per-project role assignment for a user."""
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_project_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.QA_ENGINEER.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    """Scoped personal access token (PAT) for CI/CD and API access.

    When ``project_id`` is NULL the key is **user-scoped** and inherits the
    owning user's project permissions.  When set, the key is
    **project-scoped** — requests using this key are restricted to the
    specified project.
    """
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    key_hint: Mapped[str] = mapped_column(String(12), nullable=False)  # first 8 chars shown in UI
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["test:write","report:read"]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunIntelligenceSnapshot(Base):
    """Cached run intelligence payload for fast page loads."""
    __tablename__ = "run_intelligence_snapshots"
    __table_args__ = (
        Index("ix_ris_run_id", "run_id", unique=True),
        Index("ix_ris_generated", "generated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class EvidenceArtifact(Base):
    """Reusable evidence item linked to a run and optionally a cluster/test."""
    __tablename__ = "evidence_artifacts"
    __table_args__ = (
        Index("ix_evidence_run_id", "run_id"),
        Index("ix_evidence_cluster", "cluster_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    cluster_id: Mapped[Optional[str]] = mapped_column(String(20))
    test_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)      # stack_trace | log_anomaly | api_contract | metric | build_change | config_diff
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)     # splunk | mongodb | prometheus | github | ocp | chromadb
    uri_or_ref: Mapped[Optional[str]] = mapped_column(String(1000))
    summary_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    relevance_score: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIProvenanceRecord(Base):
    """Tracks which model/method produced each AI conclusion."""
    __tablename__ = "ai_provenance_records"
    __table_args__ = (
        Index("ix_provenance_entity", "entity_type", "entity_id"),
        Index("ix_provenance_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)        # run_summary | cluster_analysis | release_decision | defect_candidate
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[Optional[int]] = mapped_column(Integer)                  # 0-100
    confidence_reason: Mapped[Optional[str]] = mapped_column(Text)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    sources_used: Mapped[Optional[list]] = mapped_column(JSON)                  # ["splunk", "stacktrace"]
    deterministic_checks_used: Mapped[Optional[list]] = mapped_column(JSON)     # ["flaky_detection", "regression_classification"]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunBaseline(Base):
    """Records which baseline was selected for a run and why."""
    __tablename__ = "run_baselines"
    __table_args__ = (
        Index("ix_run_baselines_run_id", "run_id", unique=True),
        Index("ix_run_baselines_baseline", "baseline_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    baseline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    selection_reason: Mapped[str] = mapped_column(String(100), nullable=False)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    baseline_build_number: Mapped[Optional[str]] = mapped_column(String(100))
    pass_rate_delta: Mapped[Optional[float]] = mapped_column(Float)
    commit_range: Mapped[Optional[dict]] = mapped_column(JSON)
    config_drift: Mapped[Optional[list]] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunDiff(Base):
    """Persisted diff payload for a run — avoids recomputation."""
    __tablename__ = "run_diffs"
    __table_args__ = (
        Index("ix_run_diffs_run_id", "run_id", unique=True),
        # FK index added in migration 0085 — see
        # docs/DATABASE_AUDIT_2026-05-16.md (P3-4). Used by "show every
        # run diffed against baseline X" queries. RunBaseline.baseline_run_id
        # already has a matching index (``ix_run_baselines_baseline``).
        Index("ix_run_diffs_baseline_run_id", "baseline_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, unique=True)
    baseline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    diff_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunComparisonReport(Base):
    """Cached AI report for a run or suite comparison."""
    __tablename__ = "run_comparison_reports"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "left_run_id",
            "right_run_id",
            "suite_name_normalized",
            "prompt_version",
            name="uq_run_comparison_report_scope",
        ),
        Index("ix_run_comparison_reports_project", "project_id", "created_at"),
        Index("ix_run_comparison_reports_runs", "left_run_id", "right_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    left_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    right_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    suite_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    suite_name_normalized: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    compare_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    ai_report: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="run_compare_v1")
    model_name: Mapped[Optional[str]] = mapped_column(String(200))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class IntegrationHealthCheck(Base):
    """Integration provider health status (latest snapshot per provider)."""
    __tablename__ = "integration_health_checks"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    message: Mapped[Optional[str]] = mapped_column(Text)
    response_ms: Mapped[Optional[int]] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class IntegrationProbeResult(Base):
    """Historical record of each integration health probe (OPS-01)."""
    __tablename__ = "integration_probe_results"
    __table_args__ = (
        Index("ix_ipr_provider_time", "provider", "checked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # healthy | degraded | down | auth_error | timeout
    response_ms: Mapped[Optional[int]] = mapped_column(Integer)
    message: Mapped[Optional[str]] = mapped_column(Text)
    auth_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    payload_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantOnboardingStatus(Base):
    """Tracks onboarding wizard progress per project."""
    __tablename__ = "tenant_onboarding_status"
    __table_args__ = (
        Index("ix_tos_project_step", "project_id", "step_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    step_key: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductUsageEvent(Base):
    """Tracks product adoption events for analytics."""
    __tablename__ = "product_usage_events"
    __table_args__ = (
        Index("ix_pue_user", "user_id"),
        Index("ix_pue_event", "event_name"),
        Index("ix_pue_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccessAuditLog(Base):
    """Audit trail for user role and project membership changes."""
    __tablename__ = "access_audit_logs"
    __table_args__ = (
        Index("ix_aal_actor", "actor_user_id"),
        Index("ix_aal_target", "target_user_id"),
        Index("ix_aal_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(200))
    target_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    before_value: Mapped[Optional[dict]] = mapped_column(JSON)
    after_value: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserInvitation(Base):
    """Email invite token for onboarding new users."""
    __tablename__ = "user_invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.QA_ENGINEER)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Test Health Coach ────────────────────────────────────────────────────────

class TestHealthRecommendation(Base):
    """Persisted test health findings per run from TestHealthAgent."""
    __tablename__ = "test_health_recommendations"
    __table_args__ = (
        Index("ix_thr_run", "test_run_id"),
        Index("ix_thr_test_case", "test_case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    test_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    test_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False)
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    violations: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    critical_count: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    recommendation: Mapped[Optional[str]] = mapped_column(Text)
    anti_patterns: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlakyCoachResult(Base):
    """Project-level flaky test coaching results with quarantine recommendations."""
    __tablename__ = "flaky_coach_results"
    __table_args__ = (
        Index("ix_fcr_project", "project_id"),
        Index("ix_fcr_fingerprint", "test_fingerprint"),
        Index("ix_fcr_quarantine", "quarantine_recommendation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    suite_name: Mapped[Optional[str]] = mapped_column(String(500))
    failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flaky_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    quarantine_recommendation: Mapped[str] = mapped_column(String(30), nullable=False, default="MONITOR")
    stabilization_actions: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    status_history: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    # FLK-P2: Wilson 95% confidence band on the failure ratio (failed_runs /
    # total_runs). Nullable — historical rows + manual-triage entries carry no
    # statistical interval until the next refresh recomputes them.
    flaky_confidence_low: Mapped[Optional[float]] = mapped_column(Float)
    flaky_confidence_high: Mapped[Optional[float]] = mapped_column(Float)
    # FLK-P3: ML flakiness-confidence ∈ [0, 1] from the model trained on human
    # quarantine decisions. Nullable — only set when a trained model is
    # available at refresh time.
    is_flaky_confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


# ── SSO / SAML / SCIM (ENT-01) ────────────────────────────────────────────────


class SSOProviderType(str, PyEnum):
    SAML = "SAML"
    OIDC = "OIDC"  # reserved for future use


class SSOEnforcementMode(str, PyEnum):
    OPTIONAL = "OPTIONAL"          # users can still use password login
    SSO_REQUIRED = "SSO_REQUIRED"  # all non-admin users must use SSO


class IdentityEventType(str, PyEnum):
    SSO_LOGIN = "SSO_LOGIN"
    SSO_LOGIN_FAILED = "SSO_LOGIN_FAILED"
    SSO_CONFIG_CREATED = "SSO_CONFIG_CREATED"
    SSO_CONFIG_UPDATED = "SSO_CONFIG_UPDATED"
    SSO_CONFIG_DELETED = "SSO_CONFIG_DELETED"
    SSO_TEST_CONNECTION = "SSO_TEST_CONNECTION"
    SCIM_USER_CREATED = "SCIM_USER_CREATED"
    SCIM_USER_UPDATED = "SCIM_USER_UPDATED"
    SCIM_USER_DEACTIVATED = "SCIM_USER_DEACTIVATED"
    SCIM_USER_REACTIVATED = "SCIM_USER_REACTIVATED"
    SCIM_SYNC_ERROR = "SCIM_SYNC_ERROR"
    ADMIN_FALLBACK_LOGIN = "ADMIN_FALLBACK_LOGIN"
    JIT_PROVISIONED = "JIT_PROVISIONED"
    ROLE_MAPPED = "ROLE_MAPPED"


class SSOConfiguration(Base):
    """Tenant/system-level SSO configuration for a SAML identity provider."""
    __tablename__ = "sso_configurations"
    __table_args__ = (
        Index("ix_sso_config_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_type: Mapped[SSOProviderType] = mapped_column(String(20), nullable=False, default=SSOProviderType.SAML.value)
    # SAML-specific fields
    idp_entity_id: Mapped[str] = mapped_column(String(1000), nullable=False)
    idp_sso_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    idp_slo_url: Mapped[Optional[str]] = mapped_column(String(2000))
    idp_certificate: Mapped[str] = mapped_column(Text, nullable=False)  # PEM-encoded X.509 cert
    sp_entity_id: Mapped[str] = mapped_column(String(1000), nullable=False)
    sp_acs_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    # Audience restriction (if different from sp_entity_id)
    audience: Mapped[Optional[str]] = mapped_column(String(1000))
    # Role mapping: JSON dict mapping IdP group/attribute values to UserRole values
    # e.g. {"admins": "ADMIN", "qa-leads": "QA_LEAD", "engineers": "QA_ENGINEER"}
    role_mapping: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    # Default role for users that don't match any role mapping
    default_role: Mapped[UserRole] = mapped_column(String(20), nullable=False, default=UserRole.VIEWER.value)
    # Group attribute name in SAML assertion (e.g. "memberOf", "groups")
    group_attribute: Mapped[Optional[str]] = mapped_column(String(255))
    # Enforcement
    enforcement_mode: Mapped[SSOEnforcementMode] = mapped_column(
        String(20), nullable=False, default=SSOEnforcementMode.OPTIONAL.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_test_success: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_test_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class FederatedIdentity(Base):
    """Links an external IdP subject to a local User."""
    __tablename__ = "federated_identities"
    __table_args__ = (
        UniqueConstraint("sso_config_id", "external_id", name="uq_federated_identity"),
        Index("ix_federated_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sso_config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sso_configurations.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(1000), nullable=False)  # SAML NameID or SCIM externalId
    external_email: Mapped[Optional[str]] = mapped_column(String(255))
    external_display_name: Mapped[Optional[str]] = mapped_column(String(500))
    external_groups: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class SCIMToken(Base):
    """Bearer token for SCIM 2.0 provisioning endpoints."""
    __tablename__ = "scim_tokens"
    __table_args__ = (
        Index("ix_scim_token_hash", "token_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # SHA-256
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False)  # first 8 chars + "..."
    sso_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("sso_configurations.id", ondelete="SET NULL"))
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentityEvent(Base):
    """Audit trail for SSO, SCIM, and identity lifecycle events."""
    __tablename__ = "identity_events"
    __table_args__ = (
        Index("ix_identity_event_type", "event_type"),
        Index("ix_identity_event_user", "user_id"),
        Index("ix_identity_event_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[IdentityEventType] = mapped_column(String(40), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    sso_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("sso_configurations.id", ondelete="SET NULL"))
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[Optional[str]] = mapped_column(String(200))
    detail: Mapped[Optional[dict]] = mapped_column(JSON)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))  # IPv4 or IPv6
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Release Gate Policies (ENT-02) ────────────────────────────────────────────


class ReleaseGatePolicy(Base):
    """Versioned release gate policy — per-project or system-wide default."""
    __tablename__ = "release_gate_policies"
    __table_args__ = (
        Index("ix_rgp_project_active", "project_id", "is_active"),
        UniqueConstraint("project_id", "version", name="uq_rgp_project_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False)  # PolicyDocument JSON
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=False)
    activated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


# ── Report Share Links (ENT-03) ───────────────────────────────────────────────


class ReportShareLink(Base):
    """Time-limited share token for run intelligence reports.

    The raw token is shown once at creation and never persisted. Only the
    SHA-256 hex digest (``token_hash``) is stored, so a DB compromise cannot
    recover active share tokens.
    """
    __tablename__ = "report_share_links"
    __table_args__ = (
        Index("ix_rsl_token_hash", "token_hash", unique=True),
        Index("ix_rsl_run", "run_id"),
        Index("ix_rsl_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    report_layout: Mapped[str] = mapped_column(String(20), nullable=False, default="executive")
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_name: Mapped[Optional[str]] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    storage_key_pdf: Mapped[Optional[str]] = mapped_column(String(500))
    storage_key_html: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshTokenRecord(Base):
    """Server-side record of issued refresh tokens for rotation and replay detection.

    Each refresh token carries a random ``jti`` claim; only the SHA-256 hex digest
    is persisted. On refresh, the record is marked ``rotated_to_id`` and a new
    record is created. Presenting an already-rotated or revoked token triggers
    family-wide revocation for the owning user (``replay_detected = true``).
    """
    __tablename__ = "refresh_token_records"
    __table_args__ = (
        Index("ix_rtr_jti_hash", "jti_hash", unique=True),
        Index("ix_rtr_user_id", "user_id"),
        Index("ix_rtr_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jti_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    replay_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ── Service Ownership (ENT-04) ────────────────────────────────────────────────


class ServiceOwnershipRule(Base):
    """Maps a matcher pattern (suite, component, package, path) to a team/service owner."""
    __tablename__ = "service_ownership_rules"
    __table_args__ = (
        Index("ix_sor_project", "project_id"),
        Index("ix_sor_active", "project_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # Matcher: what this rule matches against
    match_type: Mapped[str] = mapped_column(String(30), nullable=False)  # suite_name | component | package | path | label
    match_pattern: Mapped[str] = mapped_column(String(500), nullable=False)  # glob or exact match
    # Ownership target
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    team_contact: Mapped[Optional[str]] = mapped_column(String(500))  # email, slack channel, etc.
    # Priority for conflict resolution (higher = evaluated first)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


# ── Saved Views & Digest Subscriptions (ENT-05) ──────────────────────────────


class SavedView(Base):
    """Persisted filter/scope configuration — personal or shared.

    The `filters` JSON field supports both legacy filter-only payloads and
    analytics widget configurations (AC-2):
      Legacy: {"severity": "critical", "date_range": 7}
      Analytics: {"page": "dashboard", "widgets": ["w1", "w2"], "filters": {...}, "version": 1}
    """
    __tablename__ = "saved_views"
    __table_args__ = (
        Index("ix_sv_user", "user_id"),
        Index("ix_sv_project", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    page: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # dashboard | trends | coverage | defects
    filters: Mapped[dict] = mapped_column(JSON, nullable=False)  # {severity, category, owner, date_range, widgets, ...}
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)  # visible to all project members
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # auto-load on page visit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class DigestSchedule(str, PyEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    PER_RUN = "PER_RUN"
    PER_RELEASE = "PER_RELEASE"
    PER_SUITE = "PER_SUITE"
    # Tier 2 item 12 — auto-retro. Fires every Monday 07:00 UTC and
    # renders an AI-written "week in review" for the subscribed team.
    WEEKLY_RETRO = "WEEKLY_RETRO"


class DigestSubscription(Base):
    """User subscription to a scheduled or event-driven report delivery."""
    __tablename__ = "digest_subscriptions"
    __table_args__ = (
        Index("ix_ds_user", "user_id"),
        Index("ix_ds_next", "next_delivery_at"),
        Index("ix_ds_schedule_active", "schedule", "is_active", "is_paused"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    saved_view_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("saved_views.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schedule: Mapped[DigestSchedule] = mapped_column(String(20), nullable=False, default=DigestSchedule.WEEKLY.value)
    channel: Mapped[NotificationChannel] = mapped_column(String(20), nullable=False, default=NotificationChannel.EMAIL.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # EM-2: Scope and trigger fields for event-driven subscriptions
    scope_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="project")   # project | release | suite | global
    scope_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)                     # suite name, release id, etc.
    trigger_filter: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="all")    # all | failed_only | degraded_only
    last_delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivery_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


# ── Tenant-Scoped Observability (OPS-04) ──────────────────────────────────────


class TenantMetricSnapshot(Base):
    """Per-project observability metric snapshot — aggregated periodically."""
    __tablename__ = "tenant_metric_snapshots"
    __table_args__ = (
        Index("ix_tms_project_time", "project_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    avg_pass_rate: Mapped[Optional[float]] = mapped_column(Float)
    failed_runs: Mapped[int] = mapped_column(Integer, default=0)
    ai_analyses_count: Mapped[int] = mapped_column(Integer, default=0)
    release_decisions_count: Mapped[int] = mapped_column(Integer, default=0)
    audit_events_count: Mapped[int] = mapped_column(Integer, default=0)


# ── AI Evaluation (OPS-02) ────────────────────────────────────────────────────


class AIEvalDataset(Base):
    """Labeled evaluation dataset for measuring AI quality over time."""
    __tablename__ = "ai_eval_datasets"
    __table_args__ = (
        Index("ix_aed_task_type", "task_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)  # classification | root_cause | release_decision | duplicate_detection
    # Each item: {input: {...}, expected_output: {...}, metadata: {...}}
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class AIEvalRun(Base):
    """Record of running an evaluation dataset against a model version."""
    __tablename__ = "ai_eval_runs"
    __table_args__ = (
        Index("ix_aer_dataset", "dataset_id"),
        Index("ix_aer_time", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_eval_datasets.id", ondelete="CASCADE"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"))
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Aggregate metrics
    precision: Mapped[Optional[float]] = mapped_column(Float)
    recall: Mapped[Optional[float]] = mapped_column(Float)
    f1_score: Mapped[Optional[float]] = mapped_column(Float)
    accuracy: Mapped[Optional[float]] = mapped_column(Float)
    agreement_rate: Mapped[Optional[float]] = mapped_column(Float)  # human-AI agreement
    # Detailed per-item results
    # Promoted JSON → JSONB in migration 0084 for consistency with the
    # sibling ``AIEvalGateRun.manifest`` (and the rest of the eval gate
    # schema), unlocking GIN-indexed predicates like
    # ``item_results @> '[{"correct": false}]'::jsonb`` if/when the
    # eval-drift dashboards need them.
    item_results: Mapped[Optional[list]] = mapped_column(JSONB)  # [{input, expected, actual, correct}]
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    correct_items: Mapped[int] = mapped_column(Integer, default=0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)


# ── AI Evaluation Baselines (P5 — Evaluation as Release Gate) ────────────────


class AIEvalBaseline(Base):
    """Baseline metrics per agent/task_type for comparison gating."""
    __tablename__ = "ai_eval_baselines"
    __table_args__ = (
        Index("ix_aeb_task_agent", "task_type", "agent_name"),
        UniqueConstraint("task_type", "agent_name", "prompt_version", name="uq_aeb_task_agent_prompt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")
    model_name: Mapped[Optional[str]] = mapped_column(String(200))
    # Baseline metrics
    baseline_accuracy: Mapped[Optional[float]] = mapped_column(Float)
    baseline_precision: Mapped[Optional[float]] = mapped_column(Float)
    baseline_recall: Mapped[Optional[float]] = mapped_column(Float)
    baseline_f1: Mapped[Optional[float]] = mapped_column(Float)
    # Gate thresholds
    min_accuracy: Mapped[float] = mapped_column(Float, default=0.80)
    min_f1: Mapped[float] = mapped_column(Float, default=0.75)
    max_regression_pct: Mapped[float] = mapped_column(Float, default=5.0)  # max allowed drop (%)
    # Metadata
    eval_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_eval_runs.id", ondelete="SET NULL"))
    dataset_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_eval_datasets.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class AIEvalGateRun(Base):
    """Historical record of an agent-stack release gate decision."""
    __tablename__ = "ai_eval_gate_runs"
    __table_args__ = (
        Index("ix_aeg_change_id", "change_id"),
        Index("ix_aeg_status", "status"),
        Index("ix_aeg_manifest_checksum", "manifest_checksum_sha256"),
        Index("ix_aeg_evaluated_at", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    manifest_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    gate_results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    blocking_gates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    version_changes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evaluated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Agent Memory (P3 — Unified Memory & Retrieval) ──────────────────────────


class AgentMemoryEntry(Base):
    """
    Unified memory linking a run/snapshot to related entities (clusters, defects,
    release decisions, ownership) for project-scoped historical recall.

    Each entry represents a single relationship discovered during a pipeline run.
    Agents query these entries to retrieve similar historical failures, prior
    defect decisions, and release outcomes for the same project.
    """
    __tablename__ = "agent_memory_entries"
    __table_args__ = (
        Index("ix_ame_project_id", "project_id"),
        Index("ix_ame_run_id", "run_id"),
        Index("ix_ame_entity", "entity_type", "entity_id"),
        Index("ix_ame_project_entity", "project_id", "entity_type"),
        Index("ix_ame_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False)
    pipeline_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_pipeline_runs.id", ondelete="SET NULL"), nullable=True)

    # What this memory links to
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Entity types: cluster | defect_candidate | release_decision | evidence |
    #               ownership | analysis | anomaly | summary | baseline
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)  # UUID or cluster_id string

    # Searchable context for similarity recall
    error_signature: Mapped[Optional[str]] = mapped_column(Text)  # representative error for vector matching
    failure_category: Mapped[Optional[str]] = mapped_column(String(50))
    root_cause_summary: Mapped[Optional[str]] = mapped_column(Text)

    # Memory payload — entity-specific details for recall
    payload: Mapped[Optional[dict]] = mapped_column(JSON)  # entity-type-specific data
    confidence: Mapped[Optional[int]] = mapped_column(Integer)  # 0-100
    resolution: Mapped[Optional[str]] = mapped_column(String(50))  # resolved | open | wont_fix | duplicate

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Feature Flags (Tier 0A) ──────────────────────────────────────────────────
#
# Generic feature flag store replacing the hand-rolled ``KNOWLEDGE_RAG_ENABLED``
# Redis→DB→env fallback. Every new feature lands behind a flag so enterprise
# customers can toggle it per-project, per-role, or by gradual rollout.
#
# Resolution order consulted by ``services/feature_flags.is_enabled``:
#   1. In-process cache (30s TTL)   — avoids hammering Redis on every request
#   2. Redis cache (30s TTL)         — shared across workers
#   3. Postgres row (authoritative)  — this table
#   4. Environment variable fallback — only for legacy flags during migration
#
# Every create/update/delete writes a SettingsAuditLog entry so the flag
# history is a first-class audit trail for regulated customers.


class FeatureFlag(Base):
    """Named capability toggle gated by project, role, and rollout percent."""
    __tablename__ = "feature_flags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Short machine-readable identifier, e.g. "cypress_ingest", "llm_cost_budget".
    # Unique — there's exactly one row per flag.
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Global kill switch. When False, the flag is off for everyone regardless
    # of project/role/rollout overrides — use this to disable a flag that's
    # misbehaving in production without losing its project-scoped history.
    enabled_global: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Project allow-list. NULL or empty = all projects. When populated, only
    # projects whose UUID is in the list see the flag as enabled (subject to
    # ``enabled_global`` still being True).
    enabled_projects: Mapped[Optional[list]] = mapped_column(JSONB)  # list[str UUID]

    # Role allow-list using the UserRole enum values as strings. NULL or empty
    # = all roles. Otherwise the user's role must be in the list.
    enabled_roles: Mapped[Optional[list]] = mapped_column(JSONB)  # list[str]

    # Deterministic percentage rollout (0-100). 0 = disabled for rollout; 100
    # = enabled for everyone who passed the project/role gates. Bucket hash is
    # ``sha1(f"{key}:{user_id or project_id}")`` for stable assignment.
    rollout_percent: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now(),
    )
    # Last admin to change the flag — populated by the router from the JWT claims.
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# ── LLM Cost Budget (Tier 1 item 2) ──────────────────────────────────────────
#
# Usage-based billing primitive. Every project gets:
#
# * ``ProjectLlmQuota`` (optional, one row per project) — the billing config:
#   how many included dollars per period, the overage rate, the hard cap,
#   and what to do when the cap is hit (soft warn, auto-downgrade to ML,
#   auto-downgrade to rules, or hard block).
#
# * ``ProjectLlmUsage`` (one row per project per period) — the running meter:
#   atomically incremented by ``services/llm_cost_budget.record_usage`` from
#   ``BaseAgent.mark_stage_done`` every time a stage persists its cost.
#
# When no ``ProjectLlmQuota`` row exists the project is considered unlimited —
# usage is still recorded for reporting, but no cap is enforced.
#
# The at-cap behaviour is evaluated inside ``analysis_agent.run`` *before*
# the per-test routing decision so every classification for the rest of the
# stage sees the downgraded mode. The downgrade is recorded as a decision
# log entry so the decision trail UI surfaces it.


class QuotaCapAction(str, PyEnum):
    SOFT_WARN = "SOFT_WARN"                         # log + metric, do not gate
    AUTO_DOWNGRADE_TO_ML = "AUTO_DOWNGRADE_TO_ML"   # force analysis_mode=ml
    AUTO_DOWNGRADE_TO_RULES = "AUTO_DOWNGRADE_TO_RULES"  # force analysis_mode=rules
    HARD_BLOCK = "HARD_BLOCK"                        # refuse LLM work entirely


class ProjectLlmQuota(Base):
    """Per-project LLM spend config used by the usage-based billing gate."""
    __tablename__ = "project_llm_quota"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Billing period length. Currently only ``MONTHLY`` is supported; the
    # column exists so we can add daily/quarterly budgets without a migration.
    period_type: Mapped[str] = mapped_column(String(20), default="MONTHLY", nullable=False)

    # Dollars included in the plan per period (what the customer pre-paid for).
    included_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Overage rate charged per dollar above ``included_usd``. Used purely for
    # display on the billing dashboard — the gate does not use it.
    overage_rate_usd: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # The absolute ceiling. Below ``hard_cap_usd`` the ``at_cap_action`` may
    # trigger at ``soft_warn_pct``. Above ``hard_cap_usd`` the hard action
    # always fires regardless of config.
    hard_cap_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Percentage of ``hard_cap_usd`` at which the configured action starts
    # firing. Default 100 means "only trigger at the hard cap". Setting it to
    # 80 gives ops a warning lane that surfaces on the billing page before
    # customers hit the wall.
    soft_warn_threshold_pct: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Behaviour when usage crosses ``soft_warn_threshold_pct * hard_cap_usd``.
    at_cap_action: Mapped[str] = mapped_column(
        String(32),
        default=QuotaCapAction.AUTO_DOWNGRADE_TO_ML.value,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now(),
    )
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# ── Flaky Auto-Quarantine (Tier 1 item 3) ───────────────────────────────────
#
# Workflow:
#
#   [DETECTED] agent spots a flaky pattern
#        ↓ (agent proposes immediately if flip-rate >= configured floor)
#   [PROPOSED] waiting for QA Lead approval
#        ↓ (QA Lead clicks Approve)            ↘ (Reject)
#   [APPROVED] approved, will be active on next ingestion  → [REJECTED]
#        ↓ (first ingestion after approval)
#   [QUARANTINED] active — test cases tagged 'quarantined', excluded
#                  from release gate scoring                ↙ (Release manually)
#        ↓ (quarantine window nearing end)                  → [RELEASED]
#   [RECHECK_SCHEDULED] celery beat task evaluates recent runs
#        ↓ flip-rate below threshold → [RELEASED]
#        ↓ flip-rate still high → [RE_QUARANTINED] → QUARANTINED (new window)
#
# Auxiliary terminal states:
#   - EXPIRED  — proposal sat in PROPOSED too long without action
#   - REJECTED — QA Lead refused the proposal
#
# Uniqueness: at most one row per (project_id, test_fingerprint) may be in a
# live (non-terminal) state at a time. Terminal-state rows are retained
# as history so QA leads can see previous quarantine decisions for a test.


class FlakyQuarantineStatus(str, PyEnum):
    DETECTED = "DETECTED"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    QUARANTINED = "QUARANTINED"
    RECHECK_SCHEDULED = "RECHECK_SCHEDULED"
    RELEASED = "RELEASED"
    RE_QUARANTINED = "RE_QUARANTINED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# Non-terminal states — used in the partial UNIQUE index and the "active
# quarantine" lookup that the ingestion pipeline runs on every ingest.
_LIVE_QUARANTINE_STATES = (
    FlakyQuarantineStatus.DETECTED.value,
    FlakyQuarantineStatus.PROPOSED.value,
    FlakyQuarantineStatus.APPROVED.value,
    FlakyQuarantineStatus.QUARANTINED.value,
    FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
    FlakyQuarantineStatus.RE_QUARANTINED.value,
)


# ── Performance Baselines (Tier 2 item 10) ─────────────────────────────────
#
# Running duration statistics per (project_id, test_fingerprint). Used by
# ``perf_regression_service`` to detect per-test latency spikes and surface
# them as a first-class category in release gate scoring.
#
# Design: rolling stats updated in O(1) by Welford's online algorithm so
# we never have to scan the full TestCase history at scoring time. The
# nightly beat task ``refresh_perf_baselines`` sweeps new test cases
# into their baselines; the release gate reads the table directly.


class PerfBaseline(Base):
    """Per-test running duration statistics."""
    __tablename__ = "perf_baselines"
    __table_args__ = (
        Index("ix_perf_baseline_project", "project_id"),
        UniqueConstraint(
            "project_id", "test_fingerprint", name="uq_perf_baseline_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # Denormalized for the release gate UI — avoids a second lookup per
    # baseline when rendering "top perf regressions".
    test_name: Mapped[Optional[str]] = mapped_column(String(500))
    suite_name: Mapped[Optional[str]] = mapped_column(String(500))

    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mean_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Welford accumulator for variance — see ``perf_regression_service``.
    # Stored as-is so the nightly refresh can keep extending the series
    # without re-scanning history.
    m2: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stddev_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    p95_ms: Mapped[Optional[float]] = mapped_column(Float)
    last_observed_ms: Mapped[Optional[int]] = mapped_column(Integer)
    last_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


# ── Outbound Webhook Subscriptions (Tier 2 item 6) ─────────────────────────
#
# Per-project customer-managed subscriptions to TestLookup events. When a
# supported event fires (``run.completed``, ``defect.promoted``, etc.) the
# webhook service scans active subscriptions that include the event type
# in their ``events`` list and enqueues one delivery job per match.
#
# The secret used to HMAC-sign each delivery lives in ``secret_service``
# under scope ``webhook_subscription`` so a DB dump cannot recover it.
# This table stores only the boolean ``has_secret`` flag for the UI.


class WebhookSubscription(Base):
    """Customer-managed outbound webhook subscription."""
    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        Index("ix_webhook_sub_project", "project_id"),
        Index("ix_webhook_sub_enabled", "enabled", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_url: Mapped[str] = mapped_column(String(1000), nullable=False)

    # Array of event types this subscription wants — e.g.
    # ``["run.completed", "defect.promoted"]``. Validated server-side
    # against the ``_SUPPORTED_EVENTS`` set in
    # ``services/webhook_service.py`` before insert/update.
    events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Per-subscription retry budget. The delivery Celery task honours
    # this — defaults to 5 attempts so a transient outage still delivers
    # via exponential backoff.
    max_retries: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Bookkeeping surfaces to the settings UI + Integration Health page.
    last_delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class WebhookDelivery(Base):
    """Audit trail for every webhook delivery attempt.

    One row per (subscription, event emission). Retries update the same
    row — ``attempt_count`` is incremented and the final outcome lands in
    ``status``. Rows older than 30 days are pruned by a celery beat task
    to keep the table bounded.
    """
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_delivery_sub_created", "subscription_id", "created_at"),
        Index("ix_webhook_delivery_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_payload: Mapped[Optional[dict]] = mapped_column(JSONB)  # full JSON sent to the target

    # PENDING | SUCCESS | FAILED (retries exhausted) | DLQ (hit max and aborted)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    response_preview: Mapped[Optional[str]] = mapped_column(String(2000))
    error: Mapped[Optional[str]] = mapped_column(Text)

    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── GitHub Integration (Tier 1 item 5) ─────────────────────────────────────
#
# Per-project outbound GitHub Checks API integration. When a test run
# finishes, the ingestion pipeline posts a check run to the commit SHA
# from the run, giving developers a green/red check on their PR that
# deep-links back to Run Intelligence.
#
# Token storage: the Personal Access Token is NOT stored on this row.
# Instead, ``services/secret_service`` holds the encrypted value under
# scope ``github_integration`` + key ``project:{project_id}:pat``. This
# row only holds a boolean ``has_pat`` hint for the UI so we can render
# "token configured" without exposing the value.


class GitHubIntegration(Base):
    """Per-project GitHub Checks API integration config."""
    __tablename__ = "github_integrations"
    __table_args__ = (
        Index("ix_github_integrations_project", "project_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    repo_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Defaults to public GitHub. Enterprise customers override this to
    # e.g. ``https://ghe.corp.example.com/api/v3`` so the service points
    # at GitHub Enterprise Server. No trailing slash.
    api_base_url: Mapped[str] = mapped_column(
        String(500),
        default="https://api.github.com",
        nullable=False,
    )

    # Cosmetic only — the real token lives in secret_service under
    # scope "github_integration", key "project:{project_id}:pat". This
    # flag lets the UI show "token configured" without roundtripping
    # through the secret service for the list view.
    has_pat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # PMF US-4.1 — sticky PR summary comment mode (migration 0102):
    # off | failures_only | always. ``failures_only`` still UPDATES an
    # existing marker comment on a green run so a red→green PR shows green.
    pr_comment_mode: Mapped[str] = mapped_column(
        String(20),
        default="failures_only",
        server_default="failures_only",
        nullable=False,
    )

    # Last successful post bookkeeping — surfaced on the Integration
    # Health dashboard so stale configs are visible.
    last_posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# ── Release Compliance Export Pack (Tier 1 item 4) ─────────────────────────
#
# One row per generated signoff ZIP. The actual pack lives in MinIO — this
# table is the authoritative index so ops can find every pack ever produced
# for a given release, verify its SHA-256 manifest, and re-download it for
# regulators. Rows are retained for the full retention window even after
# the release itself is deleted (``ondelete=SET NULL`` on ``release_id``)
# so the audit trail survives production housekeeping.


class CompliancePack(Base):
    """Generated compliance export pack (ZIP) for a release decision."""
    __tablename__ = "compliance_packs"
    __table_args__ = (
        Index("ix_compliance_packs_release", "release_id"),
        Index("ix_compliance_packs_project", "project_id", "generated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    release_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("releases.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    # Which test run the decision was computed against. Kept even if the
    # run is later purged — the snapshot we captured into the pack remains
    # in MinIO, this column is just a pointer for fast lookups.
    test_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # MinIO object key for the ZIP. Bucket is implicit (``compliance-packs``).
    minio_key: Mapped[str] = mapped_column(String(500), nullable=False)

    # SHA-256 of the generated manifest.json. ``manifest.json`` itself
    # contains SHA-256 digests of every other file in the ZIP, so this
    # single hex digest bootstraps the entire tamper-detection chain:
    # if this hash matches the manifest, and the manifest matches each
    # file, the pack is authentic.
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Retention window. Default 7 years (2557 days) matches the common
    # SOX/HIPAA/SOC-2 retention floor; ADMIN can override at generation time.
    retention_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    generated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Structured metadata written by the service: policy_version, run_id,
    # recommendation, dim_scores. Lets the list view render without pulling
    # the ZIP.
    metadata_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class FlakyQuarantineRequest(Base):
    """Workflow record for proposing, approving, and enforcing quarantine
    of a flaky test.

    One row per quarantine lifecycle. When an approved quarantine is
    ``RELEASED`` and the test flakes again, a fresh row is created rather
    than reusing the old one — this keeps the history immutable and lets
    QA leads see every past decision on the same test.
    """
    __tablename__ = "flaky_quarantine_requests"
    __table_args__ = (
        Index("ix_fqr_project_status", "project_id", "status"),
        Index("ix_fqr_fingerprint", "project_id", "test_fingerprint"),
        # Partial unique: only one LIVE row per (project, fingerprint). Uses
        # a raw ``text()`` predicate because Alembic's autogenerate cannot
        # express enum-membership with mapped_column metadata alone. The
        # corresponding index is created in migration 0065.
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    test_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    test_name: Mapped[Optional[str]] = mapped_column(String(500))   # denormalized for display
    suite_name: Mapped[Optional[str]] = mapped_column(String(500))  # denormalized for display

    status: Mapped[str] = mapped_column(
        String(32),
        default=FlakyQuarantineStatus.PROPOSED.value,
        nullable=False,
    )

    # Detection context — populated when the agent or a human creates the row.
    detection_method: Mapped[str] = mapped_column(String(50), default="pass_fail_ratio")
    flip_rate: Mapped[Optional[float]] = mapped_column(Float)          # 0.0-1.0
    flip_window_size: Mapped[Optional[int]] = mapped_column(Integer)   # runs considered
    pass_count: Mapped[Optional[int]] = mapped_column(Integer)
    fail_count: Mapped[Optional[int]] = mapped_column(Integer)

    # Lifecycle timestamps.
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    proposed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejected_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    # Quarantine window — populated on approval.
    quarantine_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    quarantine_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    quarantine_duration_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    recheck_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Structured rationale for display — used by both the detection agent
    # ({"method": "...", "flip_rate": 0.42, "sample_size": 20, "last_5": "PFPFP"})
    # and by humans to record the reason for approve/reject/release.
    rationale: Mapped[Optional[dict]] = mapped_column(JSONB)
    reviewer_notes: Mapped[Optional[str]] = mapped_column(Text)

    # ── Lifecycle: owner + ticket + SLA (PMF US-5.4, migration 0104) ────────
    # Resolved on activation via the ownership rules for the test's suite,
    # falling back to the approving QA lead. SET NULL so deleting a user
    # never breaks the quarantine history.
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    # Auto-created INTERNAL defect record (Jira posting is Epic 6).
    defect_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("defects.id", ondelete="SET NULL"), nullable=True,
    )
    # SLA snapshot taken at activation (project policy default at that
    # moment). NULL on rows quarantined before 0104 — no SLA is enforced
    # retroactively for them.
    sla_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Precomputed ``quarantine_start + sla_days`` — the staleness sweep and
    # the ``stale`` property compare against this instead of re-deriving.
    stale_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Set (once) by the staleness sweep when it emits test.quarantine_stale
    # — the idempotency anchor for the once-per-entry notification.
    stale_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # ── Lifecycle: auto-promotion out of quarantine (PMF US-5.5) ────────────
    # Consecutive fully-PASSED runs since activation; a single FAILED/BROKEN
    # result resets it to 0. Advanced at run finalization by
    # ``flaky_quarantine_service.update_quarantine_stability``.
    consecutive_passes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Idempotency stamp: the last run that advanced the counter — a
    # re-finalized (or Celery-retried) run advances nothing.
    last_stability_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True,
    )
    # True once the pass streak reaches the project policy threshold and
    # auto_promote is OFF — surfaced on /quarantine + the CI manifest so a
    # QA lead can one-click release. Reset to False when a failure breaks
    # the streak.
    ready_to_promote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Once-per-crossing anchor for the optional test.ready_to_unquarantine
    # notification (cleared when a failure resets the streak).
    ready_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    @property
    def stale(self) -> bool:
        """True when this quarantine is currently effective AND has out-lived
        its SLA window. Derived (not stored) so list/manifest responses are
        always current without waiting for the sweep."""
        if self.stale_at is None or self.status not in (
            FlakyQuarantineStatus.QUARANTINED.value,
            FlakyQuarantineStatus.RECHECK_SCHEDULED.value,
            FlakyQuarantineStatus.RE_QUARANTINED.value,
        ):
            return False
        stale_at = self.stale_at
        if stale_at.tzinfo is None:
            stale_at = stale_at.replace(tzinfo=timezone.utc)
        return stale_at <= datetime.now(timezone.utc)


class QuarantineLifecyclePolicy(Base):
    """Per-project quarantine lifecycle configuration (PMF US-5.4/5.5/5.6).

    One row per project; a MISSING row resolves to the defaults in
    ``flaky_quarantine_service.EffectiveLifecyclePolicy`` (SLA 14 days,
    no auto-defect, no auto-promote, promote after 20 consecutive passes,
    detection floor 20% flip rate over 10 runs) — no project-creation hook
    needed.
    """
    __tablename__ = "quarantine_lifecycle_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True,
    )
    # US-5.4 — days an active quarantine may sit before it is flagged stale.
    sla_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    # US-5.4 — auto-create an internal defect record on activation.
    auto_create_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # US-5.5 — release automatically at the pass-streak threshold; when
    # False the row is only flagged ready_to_promote for human release.
    auto_promote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    promote_after_passes: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    # US-5.6 — auto-quarantine proposal thresholds consumed by the flaky
    # sentinel agent (flips-in-window floor).
    detection_flip_rate_threshold: Mapped[float] = mapped_column(Float, default=0.20, nullable=False)
    detection_min_runs: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class ProjectLlmUsage(Base):
    """Running per-period LLM cost meter for a project.

    One row per ``(project_id, period_start)``. ``record_usage`` upserts with
    atomic arithmetic increments so two workers writing concurrently don't
    clobber each other. The ``(project_id, period_start)`` unique index is
    what makes the upsert safe.
    """
    __tablename__ = "project_llm_usage"
    __table_args__ = (
        UniqueConstraint("project_id", "period_start", name="uq_project_period"),
        Index("ix_llm_usage_period", "period_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Incremented every time ``check_and_apply_cap`` returns a downgrade /
    # hard-block decision. Used by the billing dashboard to show whether a
    # project has been capped in the current period.
    cap_hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
