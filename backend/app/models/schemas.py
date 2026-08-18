"""Pydantic v2 request/response schemas for all API endpoints."""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.postgres import (
    FailureCategory,
    IdentityEventType,
    LaunchStatus,
    NotificationChannel,
    SSOEnforcementMode,
    SSOProviderType,
    TestStatus,
    UserRole,
)


# Generous upper bound for long-form free-text fields backed by a Postgres
# ``Text`` column (test-case bodies, plans, strategies, descriptions). These
# columns have no DB length limit, so without a Pydantic cap a caller can POST a
# multi-MB/GB string and exhaust memory / DB write capacity (audit item S4,
# OWASP A03). 50 000 chars is far above any realistic human-authored test field
# while still decisively blocking abuse — chosen to avoid rejecting legitimate
# content (behaviour-preserving). Fields backed by ``String(N)`` instead match
# ``N`` directly so an over-long value returns a clean 422 rather than a DB 500.
MAX_LONG_TEXT = 50_000


# ── Base ─────────────────────────────────────────────────────

class TimestampMixin(BaseModel):
    created_at: datetime
    updated_at: Optional[datetime] = None


# ── Auth Schemas ─────────────────────────────────────────────

# Every password-accepting schema uses this cap. An unbounded value reaches
# ``get_password_hash`` → bcrypt, so a multi-MB password is a cheap CPU/memory
# DoS (audit item S4) — the cap is a resource guard, not a password policy.
#
# Why 128 and not something larger: **bcrypt only reads the first 72 bytes.**
# Every byte past 72 is security theatre — two passwords sharing a 72-byte
# prefix hash identically — so a generous cap buys the user nothing real. 128
# is the historical value from ``UserCreate`` and is kept so this change adds
# no new rejection to the one path that was already capped.
#
# Caveat worth knowing (NOT fixed here — see CHANGELOG): the pinned bcrypt
# does not truncate silently, it raises ``ValueError`` past 72 bytes, so a
# 73–128 char password still 500s inside ``get_password_hash``. The cap
# narrows that window but does not close it; closing it is a password-policy
# decision (reject at 72 vs. truncate vs. pre-hash), not a drive-by.
MAX_PASSWORD_LENGTH = 128


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = Field(None, max_length=255)
    password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)


class UserResponse(TimestampMixin):
    id: uuid.UUID
    email: str
    username: str
    full_name: Optional[str] = None
    role: UserRole
    is_active: bool
    must_change_password: bool = False
    avatar_color: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class SelfUpdateProfileRequest(BaseModel):
    """Fields a user can update about themselves (no role/status changes)."""
    full_name: Optional[str] = Field(None, max_length=255)
    avatar_color: Optional[str] = Field(None, max_length=20)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool = False


class LoginRequest(BaseModel):
    username: str
    # Capped like every other password field. No working password can exceed
    # the cap — bcrypt reads only 72 bytes, so a longer one could never have
    # been set in the first place — so this rejects nothing that works today.
    # NOTE: this schema is currently unreferenced; ``POST /auth/login`` binds
    # ``OAuth2PasswordRequestForm``, whose ``password`` is uncapped. That is
    # the live unauthenticated bcrypt surface and it is NOT closed here (see
    # CHANGELOG); the cap is on the schema so it is right the day the schema
    # is wired up.
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    # Both fields reach bcrypt — ``current_password`` via verify, ``new_password``
    # via hash — so both carry the cap. Only ``new_password`` carries the minimum;
    # ``current_password`` is checked against the stored hash, and rejecting a
    # short one at the schema would leak that no short password can be current.
    current_password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)


class FirstTimeResetRequest(BaseModel):
    """Used for forced password reset on first login — no current password required."""
    new_password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)
    confirm_password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)


# ── MFA (TOTP) Schemas ────────────────────────────────────────
#
# ``POST /auth/login`` returns one of three shapes. They are discriminated by
# the literal flags below rather than by HTTP status because all three are
# successful outcomes of a correct password — the caller has to branch anyway,
# and a 4xx for "now do your second factor" would be a lie the SPA's global
# error handling would act on.


class MfaChallengeResponse(BaseModel):
    """Password accepted; a second factor is required to finish.

    ``challenge_token`` is NOT an access token — it carries ``type:
    "mfa_challenge"`` and is rejected by ``get_current_user`` at the decode
    layer. It is only accepted by ``POST /auth/mfa/verify``.
    """
    mfa_required: Literal[True] = True
    challenge_token: str
    expires_in: int
    methods: List[str] = Field(default_factory=lambda: ["totp", "recovery_code"])


class MfaEnrollmentRequiredResponse(BaseModel):
    """Password accepted; workspace policy requires MFA and the user has none.

    ``enrollment_token`` carries ``type: "mfa_enroll"`` and is accepted only by
    the two enrollment endpoints.
    """
    mfa_enrollment_required: Literal[True] = True
    enrollment_token: str
    expires_in: int
    required_for_role: Optional[str] = None


class MfaEnrollStartRequest(BaseModel):
    # Supplied only on the forced-enrollment path (no session yet). Omitted
    # when an already-authenticated user enrolls voluntarily.
    enrollment_token: Optional[str] = None


class MfaEnrollStartResponse(BaseModel):
    secret: str
    otpauth_uri: str
    issuer: str
    account_name: str
    digits: int
    period_seconds: int


class MfaEnrollConfirmRequest(BaseModel):
    code: str = Field(..., max_length=12)
    enrollment_token: Optional[str] = None


class MfaEnrollConfirmResponse(BaseModel):
    enabled: Literal[True] = True
    # Shown exactly once. Only digests are stored server-side.
    recovery_codes: List[str]
    # Present only on the forced-enrollment path, where confirming enrollment
    # is also what completes the login.
    tokens: Optional[TokenResponse] = None


class MfaVerifyRequest(BaseModel):
    challenge_token: str
    code: Optional[str] = Field(None, max_length=12)
    recovery_code: Optional[str] = Field(None, max_length=64)


class MfaDisableRequest(BaseModel):
    """Disabling requires the password *and* a live second factor.

    Password alone would let anyone holding a stolen session strip the factor
    that session was supposed to be protected by.
    """
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
    code: Optional[str] = Field(None, max_length=12)
    recovery_code: Optional[str] = Field(None, max_length=64)


class MfaRecoveryCodesRequest(BaseModel):
    password: str = Field(..., max_length=MAX_PASSWORD_LENGTH)
    code: Optional[str] = Field(None, max_length=12)
    recovery_code: Optional[str] = Field(None, max_length=64)


class MfaRecoveryCodesResponse(BaseModel):
    recovery_codes: List[str]


class MfaStatusResponse(BaseModel):
    enabled: bool
    enrolled_at: Optional[datetime] = None
    recovery_codes_remaining: int = 0
    # Does workspace policy require this user to hold a second factor?
    required_by_policy: bool = False
    # True when an external IdP owns this account, so the local requirement
    # does not apply to it.
    sso_managed: bool = False
    # True when the account is flagged as enrolled but the stored seed cannot
    # be read — MFA is broken, not off, and login is denied until it is fixed.
    secret_unreadable: bool = False


class MfaPolicyRead(BaseModel):
    require_mfa: bool
    required_for_role: Optional[UserRole] = None
    lockout_enabled: bool
    lockout_threshold: int
    lockout_duration_minutes: int


class MfaPolicyUpdate(BaseModel):
    require_mfa: Optional[bool] = None
    required_for_role: Optional[UserRole] = None
    lockout_enabled: Optional[bool] = None
    lockout_threshold: Optional[int] = Field(None, ge=3, le=100)
    lockout_duration_minutes: Optional[int] = Field(None, ge=1, le=1440)
    # ``required_for_role`` is the one field where "not supplied" and
    # "explicitly cleared" differ — null means *everyone*, which is stricter
    # than any role floor. Setting this true applies the null.
    clear_required_for_role: bool = False


# ── Project Schemas ───────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = Field(None, max_length=2000)
    jira_project_key: Optional[str] = Field(None, max_length=50)
    splunk_index: Optional[str] = Field(None, max_length=255)
    ocp_namespace: Optional[str] = Field(None, max_length=255)
    jenkins_job_pattern: Optional[str] = Field(None, max_length=500)
    component_owner_map: Optional[dict] = None
    # Optional at create time — admin can set later. When set, the user must
    # already have ProjectMember.role=QA_LEAD on this project (or be ADMIN).
    # The router enforces the role check.
    default_qa_lead_user_id: Optional[uuid.UUID] = None  # migration 0079


class ProjectUpdate(BaseModel):
    """Partial update for project attributes. None = keep existing."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    jira_project_key: Optional[str] = Field(None, max_length=50)
    splunk_index: Optional[str] = Field(None, max_length=255)
    ocp_namespace: Optional[str] = Field(None, max_length=255)
    jenkins_job_pattern: Optional[str] = Field(None, max_length=500)
    component_owner_map: Optional[dict] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Optional[List[str]] = None
    manager_user_id: Optional[uuid.UUID] = None  # migration 0076 — program manager
    default_qa_lead_user_id: Optional[uuid.UUID] = None  # migration 0079 — default suite owner


class ProjectResponse(TimestampMixin):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str] = None
    jira_project_key: Optional[str] = None
    splunk_index: Optional[str] = None
    ocp_namespace: Optional[str] = None
    jenkins_job_pattern: Optional[str] = None
    component_owner_map: Optional[dict] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Optional[List[Any]] = None
    is_active: bool
    manager_user_id: Optional[uuid.UUID] = None  # migration 0076
    default_qa_lead_user_id: Optional[uuid.UUID] = None  # migration 0079

    model_config = ConfigDict(from_attributes=True)


class ProjectResetRequest(BaseModel):
    """Destructive reset payload. ``mode`` selects the wipe scope; the
    backend rejects any request whose ``confirmation_name`` doesn't
    exactly equal the project's ``name`` — a typed-confirmation guard
    against autopilot clicks. See services/project_reset_service.py for
    the table list per mode."""

    mode: Literal["runs", "full"]
    confirmation_name: str = Field(..., min_length=1, max_length=255)


class ProjectResetResponse(BaseModel):
    mode: Literal["runs", "full"]
    deleted: dict[str, int]


# ── Test Run Schemas ──────────────────────────────────────────

class TestRunSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    build_number: str
    jenkins_job: Optional[str] = None
    trigger_source: Optional[str] = None
    ingestion_source: Optional[str] = None
    branch: Optional[str] = None
    status: LaunchStatus
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    broken_tests: int
    unknown_tests: int = 0
    pass_rate: Optional[float] = None
    duration_ms: Optional[int] = None
    ocp_pod_name: Optional[str] = None
    ocp_namespace: Optional[str] = None
    primary_suite_name: Optional[str] = None
    suite_names: Optional[List[str]] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestRunListResponse(BaseModel):
    items: List[TestRunSummary]
    total: int
    page: int
    size: int
    pages: int


# ── Test Case Schemas ─────────────────────────────────────────

class TestCaseSummary(BaseModel):
    id: uuid.UUID
    test_run_id: uuid.UUID
    test_name: str
    suite_name: Optional[str] = None
    class_name: Optional[str] = None
    status: TestStatus
    duration_ms: Optional[int] = None
    # severity / failure_category are stored as String(20) in Postgres, not as
    # real enums. Historical rows (seeded mock data, raw Allure labels) can
    # contain lowercase strings like "blocker"/"normal"/"minor" that don't
    # match the strict Severity/FailureCategory enum values. With the old
    # Optional[Severity] / Optional[FailureCategory] types, Pydantic v2 would
    # 422 the entire list response and the run detail page would show an
    # empty test case table. Falling back to plain strings lets the response
    # surface what's actually in the database; the frontend already treats
    # these columns as strings.
    severity: Optional[str] = None
    feature: Optional[str] = None
    failure_category: Optional[str] = None
    has_attachments: bool = False
    # Number of top-level granular steps captured in the latest-run snapshot
    # (Phase 1 granular steps). NULL when no parser emitted a step tree for this
    # producer; 0 when the parser ran but the test had no steps. The run-detail
    # list renders a small badge from this so a test's granularity is visible
    # without opening the per-test steps panel. Live-buffer fallback rows omit
    # it (defaults to None).
    step_count: Optional[int] = None
    created_at: datetime
    # Auto-assigned at ingest for FAILED/BROKEN cases (migration 0080).
    # Resolves to the suite owner → default QA lead → manager → NULL.
    assigned_to_user_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failure_kind(self) -> Optional[str]:
        """Derived failure-kind triad (US-9.1): product / test_code /
        infrastructure / unknown. AI-derived from the stored
        ``failure_category`` + the FAILED-vs-BROKEN status distinction —
        see ``app/services/failure_kind.py`` for the mapping rationale.
        None for non-failing rows (a kind only makes sense for failures).
        """
        status = getattr(self.status, "value", self.status)
        if status not in (TestStatus.FAILED.value, TestStatus.BROKEN.value):
            return None
        # Lazy import: keeps the models → services dependency one-way at
        # import time (services import this module heavily).
        from app.services.failure_kind import failure_kind

        return failure_kind(self.failure_category, status)


class TestCaseDetail(TestCaseSummary):
    full_name: Optional[str] = None
    package_name: Optional[str] = None
    story: Optional[str] = None
    epic: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[List[str]] = None
    error_message: Optional[str] = None
    minio_s3_prefix: Optional[str] = None


class TestCaseListResponse(BaseModel):
    items: List[TestCaseSummary]
    total: int
    page: int
    size: int
    pages: int


# ── Granular step / attachment snapshot (migration 0093) ──────────────────
# Latest-run-only snapshot anchored to the canonical (project, fingerprint)
# test identity. TestStepResponse is recursive (nested steps), so it must be
# rebuilt after definition (model_rebuild()).


class TestAttachmentResponse(BaseModel):
    """Index-only attachment metadata (Phase 1 stores refs, not bytes)."""
    id: uuid.UUID
    test_step_id: Optional[uuid.UUID] = None
    name: str
    source_ref: Optional[str] = None
    media_type: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TestStepResponse(BaseModel):
    """One granular step in a logical test's latest-run snapshot.

    ``steps`` carries the nested child steps (Allure before/after + sub-steps).
    """
    id: uuid.UUID
    parent_step_id: Optional[uuid.UUID] = None
    ordinal: int
    depth: int
    name: str
    keyword: Optional[str] = None
    status: TestStatus
    duration_ms: Optional[int] = None
    start_ms: Optional[int] = None
    assertion_message: Optional[str] = None
    assertion_trace: Optional[str] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    created_at: datetime
    steps: List["TestStepResponse"] = Field(default_factory=list)
    attachments: List[TestAttachmentResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


TestStepResponse.model_rebuild()


# ── Duplicate authored-test-case detection (Phase 4, migration 0094) ──────

# Detection bands and methods. Kept in sync with the ORM ``String(N)`` columns
# on ``DuplicateTestCaseCandidate`` (band / method) so a value drift returns a
# clean 422 rather than silently emptying the UI.
DuplicateBand = Literal["exact", "strong", "possible"]
DuplicateMethod = Literal["fingerprint", "structural", "semantic"]
DuplicateCandidateStatus = Literal["open", "merged", "dismissed"]


class DuplicateCaseRef(BaseModel):
    """Minimal reference to one ManagedTestCase in a duplicate pair."""
    id: uuid.UUID
    title: str
    suite_name: Optional[str] = None
    status: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class DuplicateCandidateResponse(BaseModel):
    """One detected near-duplicate pair with its explainable score breakdown."""
    id: uuid.UUID
    project_id: uuid.UUID
    band: DuplicateBand
    score: float
    reason: Optional[str] = None
    method: DuplicateMethod
    component_scores: Optional[Dict[str, Any]] = None
    status: DuplicateCandidateStatus
    detected_at: datetime
    case_a: DuplicateCaseRef
    case_b: DuplicateCaseRef
    model_config = ConfigDict(from_attributes=True)


class DuplicateCandidateListResponse(BaseModel):
    """Paginated list of duplicate candidates for a project's review queue."""
    items: List[DuplicateCandidateResponse] = Field(default_factory=list)
    total: int = 0
    open_count: int = 0


class DuplicateDismissRequest(BaseModel):
    """Dismiss a candidate pair so it stays suppressed across re-detection runs."""
    candidate_id: uuid.UUID


class DuplicateMergeRequest(BaseModel):
    """Non-destructive merge: flip the candidate to ``merged`` and optionally
    soft-deprecate the losing case. NEVER deletes a case this phase.
    """
    candidate_id: uuid.UUID
    # The case to keep; the other case in the pair becomes the merge loser and
    # may be soft-deprecated. Must be one of the pair's two case ids.
    keep_case_id: uuid.UUID
    deprecate_loser: bool = True


class DuplicateActionResponse(BaseModel):
    """Result of a dismiss / merge action on a candidate pair."""
    candidate_id: uuid.UUID
    status: DuplicateCandidateStatus
    deprecated_case_id: Optional[uuid.UUID] = None


class DuplicateDetectionRunResponse(BaseModel):
    """Summary of a triggered detection sweep over a project's authored cases."""
    project_id: uuid.UUID
    candidates_created: int = 0
    candidates_total: int = 0
    cases_scanned: int = 0
    sampled: bool = False  # True when the project was too large and detection was capped
    note: Optional[str] = None


# ── Test Execution Review (migration 0081) ────────────────────────────────


# Mirror of ``models.postgres.TEST_EXECUTION_REVIEW_STATES``. Kept in sync
# with the ORM via the service-level validator.
TestExecutionReviewState = Literal[
    "pending_review",
    "reviewed",
    "defect_filed",
    "false_positive",
    "reproducible",
]


class TestExecutionReviewRead(BaseModel):
    """Current review state for an AI-flagged failure."""
    id: uuid.UUID
    test_case_id: uuid.UUID
    project_id: uuid.UUID
    state: TestExecutionReviewState
    reviewed_by_user_id: Optional[uuid.UUID] = None
    reviewed_by_username: Optional[str] = None
    reviewed_by_full_name: Optional[str] = None
    defect_link: Optional[str] = None
    note: Optional[str] = None
    transitioned_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class TestExecutionReviewUpdate(BaseModel):
    """Transition the review state. ``state`` is required; other fields are
    optional context the reviewer can attach (e.g. defect URL on
    ``defect_filed``, freeform note explaining the verdict)."""
    state: TestExecutionReviewState
    defect_link: Optional[str] = Field(None, max_length=2000)
    note: Optional[str] = Field(None, max_length=4000)


# ── Summary Report (per-project consolidated stats) ────────────────────────


class SummaryTotals(BaseModel):
    total_test_cases: int
    passed: int
    failed: int
    skipped: int
    broken: int
    # ``evaluated`` = passed + failed + broken (skipped excluded from rate math).
    evaluated: int
    pass_rate_pct: float
    fail_rate_pct: float
    skip_rate_pct: float
    broken_rate_pct: float
    # Pass rate that ignores skipped tests — matches the /overview headline.
    weighted_pass_rate_pct: float


class SummaryStepBreakdownRow(BaseModel):
    """One captured granular step for a failing test (Phase 5 enrichment)."""
    name: Optional[str] = None
    status: Optional[str] = None
    assertion_message: Optional[str] = None


class SummarySuiteRow(BaseModel):
    suite_name: str
    total: int
    passed: int
    failed: int
    skipped: int
    broken: int
    pass_rate_pct: float
    weighted_pass_rate_pct: float
    last_run_at: Optional[str] = None
    # Phase 5 granular STEP success-rate (additive/optional). Populated only
    # for suites whose tests have captured step data (LATEST-RUN-ONLY
    # snapshot); ``None`` otherwise so existing consumers are unaffected.
    # Flat shape matches the frontend ``SummarySuiteRow`` contract.
    step_success_rate: Optional[float] = None
    passed_steps: Optional[int] = None
    total_steps: Optional[int] = None


class SummaryTopFailingTest(BaseModel):
    suite_name: Optional[str] = None
    class_name: Optional[str] = None
    test_name: str
    failures: int
    # Phase 5 FAILURE LOCATION (additive/optional). ``failure_step`` is the
    # first FAILED/BROKEN step name from the LATEST-RUN-ONLY snapshot;
    # ``step_breakdown`` is the ordered step list (for the PDF engineering
    # section). Both ``None`` when no snapshot exists for the test.
    failure_step: Optional[str] = None
    step_breakdown: Optional[List[SummaryStepBreakdownRow]] = None


class SummaryReportResponse(BaseModel):
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    mode: Literal["window", "latest"]
    window_days: int
    generated_at: str
    period_start: str
    period_end: str
    totals: SummaryTotals
    run_count: int
    # Average runs per day. ``None`` in ``latest`` mode (where the
    # denominator is meaningless — only the latest run per suite counts).
    runs_per_day: Optional[float] = None
    avg_duration_ms: int
    latest_run_at: Optional[str] = None
    flaky_test_count: int
    flaky_rate_pct: float
    suites: List[SummarySuiteRow]
    top_failing_tests: List[SummaryTopFailingTest]


# ── My Failures inbox (migration 0080) ─────────────────────────────────────

class MyFailureItem(BaseModel):
    """A single auto-assigned failure surfaced on the calling user's inbox.

    Carries enough context to render a triage row without a follow-up fetch:
    test name + suite + run identity + project label + relative age. The
    ``navigation_url`` is the canonical deep link to the run-detail page's
    test-case drawer.
    """
    id: uuid.UUID
    test_name: str
    suite_name: Optional[str] = None
    class_name: Optional[str] = None
    status: TestStatus
    severity: Optional[str] = None
    failure_category: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime
    test_run_id: uuid.UUID
    build_number: Optional[str] = None
    project_id: uuid.UUID
    project_name: Optional[str] = None
    navigation_url: str
    # Triage workflow state (migration 0088). The inbox endpoint filters
    # to PENDING_REVIEW, but exposing the field lets callers like the
    # run-detail page render the full status without a separate fetch.
    triage_status: str = "PENDING_REVIEW"
    triage_notes: Optional[str] = None
    # Per-(project, primary_suite_name) human-readable run number. Starts
    # at 1 and increments with each new run in the same partition.
    # Optional because legacy clients of this schema may not populate it.
    run_seq: Optional[int] = None
    # Count of times THIS test (same project + suite + class + test name) has
    # failed for this user inside the active time window. Lets the inbox row
    # show "× 7 in 7 days" so repeat offenders are visible at a glance.
    failure_count: int = 1
    # Granular step enrichment (Phase 5). Name of the FIRST FAILED/BROKEN step
    # in this test's latest-run snapshot, when step data was captured.
    # ``None`` when the test has no granular step snapshot or no failing step —
    # additive/optional so existing clients are unaffected.
    last_failure_step: Optional[str] = None
    # Why this failure landed with the current assignee, when it was resolved
    # via a path/CODEOWNERS ownership rule (US-8.4). Derived read-time, e.g.
    # "via CODEOWNERS: src/api/**". ``None`` for pool/manager/explicit-owner
    # assignments — additive/optional so existing clients are unaffected.
    assignment_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TriageStatusUpdate(BaseModel):
    """Body of ``PUT /api/v1/me/assigned-failures/{id}/triage``.

    ``status`` is validated against the ``TriageStatus`` enum at the
    service layer (the regex form here keeps the OpenAPI schema readable
    while still rejecting arbitrary strings; we don't gain anything
    from using a Pydantic Enum directly because the service maps to the
    canonical enum anyway).
    """
    status: str = Field(
        ...,
        pattern=r"^(PENDING_REVIEW|REVIEWED_APPROVED|DEFECT_CREATED|WONT_FIX|AUTOMATION_SCRIPT_ISSUE|FLAKY_TEST)$",
        description="New triage status. Any value other than PENDING_REVIEW drops the row from the assignee's /my-failures inbox.",
    )
    notes: Optional[str] = Field(
        None,
        max_length=2000,
        description="Free-form context. Typically a defect link for DEFECT_CREATED or a rationale for WONT_FIX / REVIEWED_APPROVED.",
    )


class MyFailureListResponse(BaseModel):
    items: List[MyFailureItem]
    total: int
    page: int
    size: int
    pages: int
    # Total across the same filter without pagination — used for the
    # sidebar badge so the user sees "you have N waiting" even on page 2.
    unresolved_total: int


# ── Metrics Schemas ───────────────────────────────────────────

class MetricCard(BaseModel):
    value: Any
    trend: Optional[float] = None  # Percentage change vs previous period
    trend_direction: Optional[str] = None  # "up" | "down" | "flat"


class DashboardSummary(BaseModel):
    total_executions_7d: MetricCard
    avg_pass_rate_7d: MetricCard
    active_defects: MetricCard
    flaky_test_count: MetricCard
    avg_duration_ms: MetricCard
    new_failures_24h: MetricCard
    coverage_pct: Optional[MetricCard] = None
    release_readiness: Optional[str] = None  # "GREEN" | "AMBER" | "RED"
    # 4-band pass-rate verdict driven by the active ReleaseGatePolicy. ``None``
    # when no policy is active or no runs exist. ``red`` / ``orange`` /
    # ``yellow`` / ``green`` — see PolicyPassRateBands.
    release_readiness_band: Optional[str] = None
    release_readiness_downgrades: List[str] = Field(default_factory=list)


class TrendDataPoint(BaseModel):
    date: str
    passed: int
    failed: int
    skipped: int
    broken: int
    total: int
    pass_rate: float


class TrendResponse(BaseModel):
    data: List[TrendDataPoint]
    period_days: int


# ── Webhook Schemas ───────────────────────────────────────────

class MinIOWebhookEvent(BaseModel):
    """MinIO ObjectCreated webhook payload."""
    EventName: str
    Key: str
    Records: Optional[List[dict]] = None


class SentinelFile(BaseModel):
    """upload_complete.json sentinel file content."""
    build_number: str
    project_id: str
    jenkins_job: Optional[str] = None
    trigger_source: Optional[str] = "push"
    branch: Optional[str] = None
    commit_hash: Optional[str] = None
    ocp_pod_name: Optional[str] = None
    ocp_namespace: Optional[str] = None
    # Optional: name of the software release this run belongs to.
    # If the release does not exist it is auto-created in "planning" status.
    release_name: Optional[str] = None


# ── AI Analysis Schemas ───────────────────────────────────────

class AnalyzeRequest(BaseModel):
    test_case_id: uuid.UUID
    service_name: Optional[str] = None
    timestamp: Optional[str] = None
    ocp_pod_name: Optional[str] = None
    ocp_namespace: Optional[str] = None


class EvidenceReference(BaseModel):
    source: str  # "splunk" | "stacktrace" | "ocp_events" | "flakiness"
    reference_id: str
    excerpt: str


class RoleActions(BaseModel):
    """Role-aware recommended actions generated by the ReAct agent."""
    qa: str = ""
    developer: str = ""
    sre: str = ""
    release_manager: str = ""


class ConfidenceWhy(BaseModel):
    """Explains the basis of the confidence score for transparency."""
    evidence_count: int = 0
    data_sources: List[str] = []
    is_llm_inference: bool = False
    investigation_depth: str = "fast_path"  # "fast_path" | "standard" | "deep"
    # AI-F4: calibration basis for rules-engine confidences —
    # "empirical" (measured precision on labeled eval samples) or
    # "heuristic_estimate" (engineering estimate, not empirically calibrated).
    # AI-4 adds "human_corrected" (pinned by an authoritative human
    # correction) to the shared vocabulary. None for LLM/ML analyses that
    # don't carry a band.
    confidence_basis: Optional[str] = None


class ThresholdCheck(BaseModel):
    """US-15.2 — the recorded confidence-gate evaluation for one AI output.

    Persisted verbatim in ``AIAnalysis.routing_metadata["threshold_check"]``
    and ``Defect.policy_evaluation["threshold_check"]``. Built by
    ``services.confidence_gate.build_threshold_check`` — keep the shapes in
    sync (four keys, no more).
    """
    threshold: int
    # None when the gate ran without a confidence to judge (see gate_status).
    observed_confidence: Optional[int] = None
    passed: bool
    # "ai_config" (stored operator override) | "env_default". Plain str, not a
    # Literal — a strict enum over a widening vocabulary 422s the response.
    source: str


class AnalysisProvenance(BaseModel):
    """US-15.1 — which engine actually produced this conclusion, and why.

    Populated verbatim from ``AIAnalysis.routing_metadata`` (written by the
    analysis router). It is NEVER recomputed or inferred: rows analysed before
    this feature carry no routing metadata, so ``AnalysisResponse.provenance``
    is ``None`` for them rather than a plausible-looking guess.

    The point of this block is a specific honesty case: when the LLM was
    unavailable and the rules engine ran instead, the card must be able to say
    so (``fallback_occurred`` / ``fallback_from`` / ``fallback_reason``)
    instead of silently presenting heuristics as model output.
    """
    # "llm" | "ml" | "rules" — the engine that actually ran.
    mode_used: Optional[str] = None
    # What the caller asked for (None = no explicit override) and what the
    # router resolved before dispatch (matters for "auto").
    mode_requested: Optional[str] = None
    mode_resolved: Optional[str] = None
    # Set only when the requested/resolved engine could not run.
    fallback_from: Optional[str] = None
    fallback_reason: Optional[str] = None
    # Convenience marker so the UI does not have to null-check two fields.
    fallback_occurred: bool = False
    # Engine/model identity, carried through from the analysis row.
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    # AI-F2 registry version tags of the prompts in force ({} for the
    # prompt-free rules/ML engines).
    prompt_versions: Dict[str, str] = Field(default_factory=dict)
    # AI-F4: "empirical" | "heuristic_estimate" | "human_corrected".
    # heuristic_estimate = self-declared, NOT empirically calibrated.
    confidence_basis: Optional[str] = None
    # US-15.2: the gate evaluation recorded at analysis time (historical).
    # None on rows written before the gate existed.
    threshold_check: Optional[ThresholdCheck] = None


class AnalysisResponse(BaseModel):
    test_case_id: uuid.UUID
    root_cause_summary: str
    failure_category: FailureCategory
    backend_error_found: bool
    pod_issue_found: bool
    is_flaky: bool
    confidence_score: int
    recommended_actions: List[str]
    # Role-aware actions: separate guidance for QA, Developer, SRE, and Release Manager.
    role_actions: RoleActions = Field(default_factory=RoleActions)
    evidence_references: List[EvidenceReference]
    # Actual tools invoked by the ReAct agent (empty list = fast-classifier path or error).
    # Frontend uses this to display honest stage progress instead of simulated delays.
    tools_used: List[str] = []
    # Explains the basis of the confidence score (derived from evidence and tools used).
    confidence_why: ConfidenceWhy = Field(default_factory=ConfidenceWhy)
    # AI-4: evidence-checklist record for the derived failure kind —
    # {kind, confidence, confidence_basis, checks: [{check, verdict, detail}]}.
    # Stored in AIAnalysis.routing_metadata.kind_evidence by the pipeline;
    # computed on demand for older rows. None when no analysis context exists.
    kind_evidence: Optional[Dict[str, Any]] = None
    llm_provider: str
    llm_model: str
    requires_human_review: bool
    # US-15.1: identity of the persisted ``ai_analysis`` row behind this
    # conclusion. The UI gates its confirm/correct buttons on this, because the
    # correction loop is POST /api/v1/feedback/{analysis_id} — without it the
    # AI card cannot reach the human-correction loop at all. None for a freshly
    # computed result that was never persisted; the UI then omits the buttons
    # rather than rendering ones that would 404.
    analysis_id: Optional[uuid.UUID] = None
    # US-15.1: engine provenance for this conclusion. None (not a stub) when
    # the row predates routing metadata — absent, never fabricated.
    provenance: Optional[AnalysisProvenance] = None
    # US-15.2: explicit low-confidence marker. True ⇒ render
    # "low confidence — needs human review". The UI must NOT re-derive this by
    # comparing confidence_score to a threshold it guessed at.
    low_confidence: bool = False
    # "above_threshold" | "below_threshold" | "not_evaluated". The third value
    # is a real answer: the gate did not run (or had no confidence to judge),
    # which is different from "we judged it low".
    confidence_gate_status: str = "not_evaluated"
    # The gate as evaluated for THIS response against the CURRENTLY effective
    # threshold (provenance.threshold_check is the historical record from
    # analysis time — the two differ after an operator moves the knob).
    confidence_gate: Optional[ThresholdCheck] = None


# ── Jira Integration Schemas ──────────────────────────────────

class JiraIssueRequest(BaseModel):
    project_key: str
    test_case_id: uuid.UUID
    test_name: str
    run_id: uuid.UUID
    ai_summary: str
    recommended_action: str


class JiraIssueResponse(BaseModel):
    ticket_id: Optional[str] = None
    ticket_key: Optional[str] = None
    ticket_url: Optional[str] = None
    approval_status: Optional[str] = None
    requires_approval: bool = False
    policy_reasons: List[str] = Field(default_factory=list)
    defect_id: Optional[uuid.UUID] = None
    mutating_action: Optional[str] = None


# ── Search Schemas ────────────────────────────────────────────

class SearchRequest(BaseModel):
    q: str = Field(..., min_length=1)
    project_id: Optional[uuid.UUID] = None
    status: Optional[TestStatus] = None
    suite: Optional[str] = None
    days: Optional[int] = Field(None, ge=1, le=365)
    use_semantic: bool = False
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=100)


class SearchResult(BaseModel):
    test_case_id: uuid.UUID
    test_run_id: uuid.UUID
    test_name: str
    suite_name: Optional[str] = None
    status: TestStatus
    last_run_date: datetime
    failure_count: int
    relevance_score: Optional[float] = None
    match_reasons: List[str] = []
    source_mode_used: str = "keyword"

    model_config = ConfigDict(from_attributes=True)


class SearchResponse(BaseModel):
    items: List[SearchResult]
    total: int
    query: str
    search_type: str  # "keyword" | "semantic" | "hybrid"


# ── Global Search Schemas (GS-2) ─────────────────────────────

class GlobalSearchResult(BaseModel):
    """A single result from system-wide global search."""
    entity_type: str                          # test_case | test_run | suite | defect | flaky_test | release
    entity_id: str                            # UUID as string
    title: str                                # display title
    subtitle: str = ""                        # secondary context
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    navigation_url: str                       # frontend route
    relevance_score: float = 0.0
    match_reasons: List[str] = []
    metadata: dict = Field(default_factory=dict)


class GlobalSearchResponse(BaseModel):
    """Response from the global search endpoint."""
    items: List[GlobalSearchResult]
    total: int
    query: str
    search_type: str = "keyword"
    entity_counts: dict = Field(default_factory=dict)   # {"test_case": 5, "test_run": 3}
    page: int = 1
    size: int = 20
    pages: int = 1


# ── Quality Gate Schemas ──────────────────────────────────────

class QualityGateRule(BaseModel):
    rule_type: str  # "pass_rate" | "failure_count" | "new_failures" | "p1_bugs" | "flaky_count"
    threshold: Any
    severity: str = "FAIL"  # "FAIL" | "WARN"
    description: Optional[str] = None


class QualityGateCreate(BaseModel):
    name: str = Field(..., max_length=255)
    rules: List[QualityGateRule]


class QualityGateEvaluationResult(BaseModel):
    gate_id: uuid.UUID
    run_id: uuid.UUID
    status: str  # "PASSED" | "FAILED" | "WARNED"
    rules_evaluated: List[dict]
    evaluated_at: datetime


# ── Notification Schemas ──────────────────────────────────────

class NotificationPreferenceCreate(BaseModel):
    """Create or replace a single channel preference."""
    project_id: Optional[uuid.UUID] = None  # None = all projects
    channel: NotificationChannel
    enabled: bool = True
    # Default includes the transition events (PMF US-7.1) so a freshly
    # created preference routes them without extra clicks. Harmless for
    # existing projects: their transition policy is backfilled OFF, so no
    # transition event is ever emitted there until someone opts in.
    events: List[str] = Field(
        default_factory=lambda: [
            "run_failed",
            "high_failure_rate",
            "test.newly_failing",
            "test.recovered",
            "test.newly_flaky",
            "test.quarantined",
            "test.unquarantined",
        ],
        description="List of NotificationEventType values",
    )
    failure_rate_threshold: float = Field(default=80.0, ge=0.0, le=100.0)
    email_override: Optional[EmailStr] = None
    slack_webhook_url: Optional[str] = Field(None, max_length=2000)
    teams_webhook_url: Optional[str] = Field(None, max_length=2000)


class NotificationPreferenceResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: Optional[uuid.UUID]
    channel: str
    enabled: bool
    events: List[str]
    failure_rate_threshold: Optional[float]
    email_override: Optional[str]
    slack_webhook_url: Optional[str]
    teams_webhook_url: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class NotificationLogResponse(BaseModel):
    id: uuid.UUID
    channel: str
    event_type: str
    title: str
    body: str
    status: str
    is_read: bool
    sent_at: Optional[datetime]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestNotificationRequest(BaseModel):
    channel: NotificationChannel
    preference_id: Optional[uuid.UUID] = None


# Transition event vocabulary (PMF US-7.1) — kept in sync with the
# ``test.*`` members of ``NotificationEventType``.
TRANSITION_EVENT_VALUES: tuple = (
    "test.newly_failing",
    "test.recovered",
    "test.newly_flaky",
    "test.quarantined",
    "test.unquarantined",
    # Quarantine lifecycle events (PMF US-5.4 / US-5.5)
    "test.quarantine_stale",
    "test.ready_to_unquarantine",
)


class NotificationTransitionPolicyUpdate(BaseModel):
    """Per-project transition-notification policy (PMF US-7.1)."""
    transitions_enabled: bool = True
    per_run_events_enabled: bool = False
    enabled_events: List[str] = Field(
        default_factory=lambda: list(TRANSITION_EVENT_VALUES),
        description="Transition NotificationEventType values enabled for the project",
    )
    consecutive_failure_threshold: int = Field(default=2, ge=1, le=20)

    @field_validator("enabled_events")
    @classmethod
    def _known_transition_events(cls, v: List[str]) -> List[str]:
        unknown = [e for e in v if e not in TRANSITION_EVENT_VALUES]
        if unknown:
            raise ValueError(
                f"Unknown transition events: {unknown}; "
                f"allowed: {list(TRANSITION_EVENT_VALUES)}"
            )
        # de-dupe, preserve canonical order
        chosen = set(v)
        return [e for e in TRANSITION_EVENT_VALUES if e in chosen]


class NotificationTransitionPolicyResponse(BaseModel):
    project_id: uuid.UUID
    transitions_enabled: bool
    per_run_events_enabled: bool
    enabled_events: List[str]
    consecutive_failure_threshold: int
    # True when the project has no explicit policy row yet and the
    # new-project defaults apply (transitions ON, per-run spam OFF).
    is_default: bool = False

    model_config = ConfigDict(from_attributes=True)


# ── Agent Pipeline Schemas ─────────────────────────────────────

class TriggerPipelineRequest(BaseModel):
    test_run_id: uuid.UUID


class AgentStageResultResponse(BaseModel):
    stage_name: str
    status: str
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None
    result_data: Optional[Any] = None
    error: Optional[str] = None
    # Stage-level skip context (nullable — absent on older rows pre-migration 0017)
    skipped_reason: Optional[str] = None
    execution_path: Optional[str] = None
    fallback_used: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class AgentPipelineResponse(BaseModel):
    id: uuid.UUID
    test_run_id: uuid.UUID
    workflow_type: str
    status: str
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None
    error: Optional[str] = None
    created_at: Any
    execution_metadata: Optional[Any] = None
    provenance_metadata: Optional[Any] = None
    # Run context (which run/suite this pipeline analysed) — attached by the
    # router from the owning TestRun so the /agents cards can show "Run #N ·
    # <suite>" instead of just a workflow type. All optional/None for legacy
    # rows whose TestRun is missing or whose run_seq can't be computed.
    build_number: Optional[str] = None
    run_seq: Optional[int] = None
    suite_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AgentRunSummaryResponse(BaseModel):
    test_run_id: str
    project_id: Optional[str] = None
    build_number: Optional[str] = None
    executive_summary: str
    markdown_report: str
    executive_panel: Optional[dict] = None
    anomaly_count: int = 0
    is_regression: bool = False
    analysis_count: int = 0
    generated_at: Optional[Any] = None


# ── Agent Workflow Timeline Schemas ─────────────────────────────────────────

class PipelineTimelineEventResponse(BaseModel):
    event_type: str
    stage_name: Optional[str] = None
    test_case_id: Optional[str] = None
    timestamp: datetime
    detail: Dict[str, Any] = Field(default_factory=dict)


class PipelineTimelineSummary(BaseModel):
    total_stages: int = 0
    completed_stages: int = 0
    running_stages: int = 0
    failed_stages: int = 0
    skipped_stages: int = 0
    pending_stages: int = 0
    progress_percent: float = 0.0


class PipelineReplayAuditGaps(BaseModel):
    missing_start_events: List[str] = Field(default_factory=list)
    missing_terminal_events: List[str] = Field(default_factory=list)
    missing_replay_checksums: List[str] = Field(default_factory=list)
    missing_checkpoints: List[str] = Field(default_factory=list)
    missing_final_state_checksum: bool = False


class PipelineReplayIntegritySummary(BaseModel):
    replayable: bool = False
    audit_gaps: PipelineReplayAuditGaps = Field(default_factory=PipelineReplayAuditGaps)


class PipelineTimelineResponse(BaseModel):
    schema_version: int = 2
    pipeline_run_id: uuid.UUID
    workflow_type: str
    status: str
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None
    duration_seconds: Optional[float] = None
    cost_summary: Dict[str, Any] = Field(default_factory=dict)
    agent_observability: Dict[str, Any] = Field(default_factory=dict)
    alerts: List[Dict[str, Any]] = Field(default_factory=list)
    stages: List[Dict[str, Any]] = Field(default_factory=list)
    events: List[PipelineTimelineEventResponse] = Field(default_factory=list)
    summary: PipelineTimelineSummary = Field(default_factory=PipelineTimelineSummary)
    replay_integrity: PipelineReplayIntegritySummary = Field(default_factory=PipelineReplayIntegritySummary)


class PipelineReplayEventResponse(BaseModel):
    event_type: str
    stage_name: Optional[str] = None
    test_case_id: Optional[str] = None
    timestamp: Optional[Any] = None
    detail: Dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = None


class PipelineReplayStageResponse(BaseModel):
    stage_name: str
    status: str
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None
    input_checksum_sha256: Optional[str] = None
    output_checksum_sha256: Optional[str] = None
    runtime_versions: Dict[str, str] = Field(default_factory=dict)
    checkpoint_available: bool = False
    restored_from_checkpoint: bool = False
    decision_count: int = 0


class MemoryReference(BaseModel):
    """Canonical pointer from generated output back to an auditable memory row."""
    memory_entry_id: uuid.UUID
    entity_type: str
    entity_id: str
    source_snapshot_id: Optional[uuid.UUID] = None
    payload_sha256: str
    retrieval_audit: Optional[Dict[str, Any]] = None
    retrieval_audit_sha256: Optional[str] = None
    memory_reference_id: Optional[str] = None
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)


class PipelineReplayResponse(BaseModel):
    schema_version: int = 1
    pipeline_run_id: uuid.UUID
    test_run_id: uuid.UUID
    workflow_type: str
    status: str
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None
    analysis_mode_requested: Optional[str] = None
    analysis_mode_resolved: Optional[str] = None
    analysis_mode_resolution: Dict[str, Any] = Field(default_factory=dict)
    final_state_checksum_sha256: Optional[str] = None
    runtime_versions: Dict[str, str] = Field(default_factory=dict)
    workflow_plan: Dict[str, Any] = Field(default_factory=dict)
    workflow_verification: Dict[str, Any] = Field(default_factory=dict)
    route_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    stage_replay: List[PipelineReplayStageResponse] = Field(default_factory=list)
    memory_references: List[MemoryReference] = Field(default_factory=list)
    events: List[PipelineReplayEventResponse] = Field(default_factory=list)
    event_counts: Dict[str, int] = Field(default_factory=dict)
    replayable: bool = False
    audit_gaps: PipelineReplayAuditGaps = Field(default_factory=PipelineReplayAuditGaps)


class PipelineEventLogHealthResponse(BaseModel):
    status: str = "healthy"
    write_failure_count: int = 0
    dead_letter_count: int = 0
    dead_letter_limit: int = 0
    recent_dead_letters: List[Dict[str, Any]] = Field(default_factory=list)


# ── Run Intelligence Schemas ──────────────────────────────────

class Provenance(BaseModel):
    schema_version: int = 1
    fallback_used: bool = False
    generated_by: str = "ai_pipeline"
    tools_used_count: int = 0
    generated_at: Optional[datetime] = None
    # Epic 4: trustworthy AI provenance
    confidence: Optional[int] = None                           # 0-100 overall confidence
    confidence_reason: Optional[str] = None                    # human-readable explanation
    evidence_count: int = 0                                    # total evidence items backing conclusions
    sources_used: List[str] = []                               # ["splunk", "stacktrace", "chromadb", "ocp"]
    deterministic_checks_used: List[str] = []                  # ["flaky_detection", "regression_classification", "criticality_scoring"]


class EvidenceArtifactResponse(BaseModel):
    """Evidence item returned in API responses."""
    id: str
    artifact_type: str
    source_system: str
    summary_excerpt: Optional[str] = None
    relevance_score: Optional[float] = None
    cluster_id: Optional[str] = None
    test_case_id: Optional[str] = None
    producer_pipeline_run_id: Optional[str] = None
    content_sha256: Optional[str] = None
    sensitivity: Optional[str] = None
    freshness: Optional[str] = None
    integrity_status: Optional[str] = None


class DimensionScore(BaseModel):
    name: str
    label: str
    score: float          # 0-100
    weight: float
    contribution: float   # score * weight


class ClusterInsightResponse(BaseModel):
    id: str
    cluster_id: str
    label: str
    size: int
    representative_error: Optional[str] = None
    member_test_ids: List[str] = []
    cohesion_score: Optional[float] = None
    criticality_level: Optional[str] = None   # CriticalityLevel enum value
    dimension_scores: List[DimensionScore] = []


class CommitRange(BaseModel):
    from_commit: Optional[str] = None
    to_commit: Optional[str] = None
    same_commit: bool = False


class ConfigDriftEntry(BaseModel):
    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class BaselineDiff(BaseModel):
    baseline_run_id: Optional[str] = None
    baseline_build_number: Optional[str] = None
    pass_rate_delta: Optional[float] = None             # positive = improved
    new_failures: List[str] = []                        # test names newly failing
    resolved_failures: List[str] = []                   # test names now passing
    regression_classification: str = "unclassified"
    # Extended fields (Epic 3 Phase 1)
    regression_clusters: List[dict] = []                # [{cluster_id, label, size, classification}]
    classified_new_failures: List[dict] = []            # [{name, classification}]
    suites_impacted_delta: int = 0                      # current suite count − baseline suite count
    current_suite_count: int = 0
    baseline_suite_count: int = 0
    # Phase 2 Epic 3 additions
    commit_range: Optional[dict] = None                 # {from_commit, to_commit, same_commit}
    config_drift: List[dict] = []                       # [{field, old_value, new_value}]
    selection_reason: str = "latest_passing"             # "latest_passing" | "approved_release" | "no_baseline"


class DefectCandidate(BaseModel):
    cluster_id: str
    label: str
    severity_hint: str
    failure_category: str
    confidence: int
    recommended_actions: List[str] = []


class DefectCandidateResponse(BaseModel):
    """Pre-assembled defect candidate for a cluster (editable before submission)."""
    cluster_id: str
    run_id: str
    title: str
    description: str
    severity: str           # CRITICAL | HIGH | MEDIUM | LOW
    component: str
    owner_team: str
    labels: List[str] = []
    duplicate_hint: str = ""
    duplicate_detected: bool = False
    duplicate_defect_id: Optional[str] = None
    criticality_scores: dict = {}
    composite_score: float = 0.0
    evidence_bundle: dict = {}   # {stack_traces: [], log_anomalies: [], data_sources: []}
    failure_category: str = "UNKNOWN"
    member_count: int = 0


class DefectPromotionRequest(BaseModel):
    """User-editable fields submitted from the promotion modal."""
    title: str
    severity: str = "HIGH"
    component: str = ""
    owner_team: str = ""
    labels: List[str] = []
    description: str = ""
    project_key: Optional[str] = None   # Jira project key (None = local-only draft)


class DefectPromotionResponse(BaseModel):
    defect_id: str
    cluster_id: str
    severity: str
    title: str
    owner_team: Optional[str] = None
    component: Optional[str] = None
    duplicate_detected: bool = False
    duplicate_defect_id: Optional[str] = None
    jira_ticket: Optional[dict] = None
    jira_url: Optional[str] = None
    # Phase 4: Approval workflow fields
    approval_status: Optional[str] = None
    requires_approval: Optional[bool] = None
    policy_reasons: Optional[List[str]] = None


class DefectApprovalRequest(BaseModel):
    action: str  # "approve" | "reject"
    reason: Optional[str] = None


class DefectApprovalResponse(BaseModel):
    defect_id: str
    approval_status: str
    message: str
    jira_ticket: Optional[dict] = None
    jira_url: Optional[str] = None


# ── One-click Jira defects (PMF US-6.1 / US-6.2 / US-6.3) ─────────────────
# All status-ish fields are plain strings, not enums — they mirror String
# columns / external Jira vocabulary and a strict enum here would silently
# 422 the response when a value drifts (backend/CLAUDE.md pitfall).

class JiraDefectCreateRequest(BaseModel):
    """Body for POST /projects/{project_id}/defects/jira.

    Exactly one of ``fingerprint`` / ``cluster_id`` identifies the failure.
    ``target`` picks the delivery: "jira" calls the Jira REST API, "webhook"
    emits the ``defect.create_requested`` outbound-webhook event instead
    (US-6.3).
    """
    fingerprint: Optional[str] = Field(None, min_length=1, max_length=64)
    cluster_id: Optional[str] = Field(None, min_length=1, max_length=255)
    issue_type: str = Field("Bug", max_length=100)
    jira_project_key: Optional[str] = Field(None, max_length=50)
    assignee: Optional[str] = Field(None, max_length=128)  # Jira accountId
    extra_comment: Optional[str] = Field(None, max_length=2000)
    target: str = Field("jira", pattern="^(jira|webhook)$")


class JiraDefectCreateResponse(BaseModel):
    target: str                                   # "jira" | "webhook"
    deduplicated: bool = False
    defect_id: Optional[str] = None
    jira_key: Optional[str] = None
    jira_url: Optional[str] = None
    external_status: Optional[str] = None
    recurrence_count: int = 0
    recurrence_comment_posted: bool = False
    subscriptions_notified: Optional[int] = None  # webhook target only
    message: str = ""


class JiraDefectOccurrences(BaseModel):
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    failing_runs: int = 0


class JiraDefectContext(BaseModel):
    branch: Optional[str] = None
    build_number: Optional[str] = None
    ci_run_url: Optional[str] = None


class JiraDefectExistingLink(BaseModel):
    defect_id: str
    jira_key: Optional[str] = None
    jira_url: Optional[str] = None
    external_status: Optional[str] = None


class JiraDefectPreviewResponse(BaseModel):
    """Pre-filled payload shown (read-only) in the create dialog."""
    signature: str
    summary: str
    description: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    cluster_id: Optional[str] = None
    error_message: Optional[str] = None
    occurrences: JiraDefectOccurrences
    context: JiraDefectContext
    ai_analysis: Optional[dict] = None            # {root_cause, confidence, failure_category}
    deep_link: str
    latest_run_id: Optional[str] = None
    existing_defect: Optional[JiraDefectExistingLink] = None


class JiraProjectOption(BaseModel):
    key: str
    name: Optional[str] = None


class JiraDefectMetadataResponse(BaseModel):
    """Dialog-picker metadata. ``available=false`` + ``reason`` instead of
    an HTTP error when Jira is offline-gated/unconfigured/unreachable —
    the UI uses it to disable the action with a tooltip."""
    available: bool
    reason: Optional[str] = None                  # offline_mode | disabled | not_configured | unreachable | jira_http_NNN
    projects: List[JiraProjectOption] = []
    issue_types: List[str] = []
    default_project_key: Optional[str] = None
    webhook_available: bool = False


class SummaryModes(BaseModel):
    available: List[str]   # ["executive", "developer", "manager"]
    default: str = "executive"


class Citation(BaseModel):
    source: str
    excerpt: str
    test_id: str = ""


class RunModeSummaryResponse(BaseModel):
    """Response for GET /runs/{run_id}/summary?mode=developer|manager"""
    test_run_id: str
    mode: str                        # developer | manager | executive
    executive_summary: Optional[str] = None
    markdown_report: str
    layer1_executive: Optional[str] = None
    layer2_incident: Optional[Any] = None
    layer3_evidence: Optional[Any] = None
    layer4_action_plan: Optional[Any] = None
    executive_panel: Optional[dict] = None
    fallback_used: bool = False
    generated_at: Optional[Any] = None
    citations: List[Citation] = []
    similar_failures: List[dict] = []
    provenance: Optional[dict] = None


# ── Chat Schemas ───────────────────────────────────────────────

class ChatSessionCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    active_test_run_id: Optional[uuid.UUID] = None
    active_report_id: Optional[str] = Field(None, min_length=1, max_length=64)
    active_report_version: Optional[int] = Field(None, ge=1)
    title: Optional[str] = Field(None, max_length=500)


class ChatSessionResponse(BaseModel):
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    active_test_run_id: Optional[uuid.UUID] = None
    active_report_id: Optional[str] = None
    active_report_version: Optional[int] = None
    title: Optional[str] = None
    created_at: Any
    updated_at: Any

    model_config = ConfigDict(from_attributes=True)


class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    sources: Optional[List[Any]] = None
    created_at: Any

    model_config = ConfigDict(from_attributes=True)


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    project_id: Optional[str] = None


class SendMessageResponse(BaseModel):
    session_id: uuid.UUID
    reply: str
    sources: List[Any] = []
    # AI-6 copilot: [{tool, summary}] transparency trace and
    # [{type, label, prefill}] human action handoffs. Both empty on the
    # single-shot / rules-mode path.
    tool_trace: List[Any] = []
    suggested_actions: List[Any] = []


# ── Test Case Management Schemas ──────────────────────────────────────────────

class TestCaseStepSchema(BaseModel):
    step_number: int
    action: str
    expected_result: str


class ManagedTestCaseCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    steps: Optional[List[dict]] = None
    expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_data: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_type: str = "functional"
    priority: str = "medium"
    severity: str = "major"
    feature_area: Optional[str] = Field(None, max_length=500)
    # Free-text suite label (legacy). When set without ``test_suite_id``,
    # the service resolves-or-creates a matching TestSuite and populates
    # the FK so authored cases participate in the same catalog graph as
    # executed ones (migration 0087).
    suite_name: Optional[str] = Field(None, max_length=500)
    tags: Optional[List[str]] = None
    estimated_duration_minutes: Optional[int] = None
    is_automated: bool = False
    automation_status: str = "not_automated"


class ManagedTestCaseUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    steps: Optional[List[dict]] = None
    expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_data: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_type: Optional[str] = None
    priority: Optional[str] = None
    severity: Optional[str] = None
    feature_area: Optional[str] = Field(None, max_length=500)
    suite_name: Optional[str] = Field(None, max_length=500)
    tags: Optional[List[str]] = None
    estimated_duration_minutes: Optional[int] = None
    is_automated: Optional[bool] = None
    automation_status: Optional[str] = None
    change_summary: Optional[str] = Field(None, max_length=500)


class ManagedTestCaseResponse(BaseModel):
    # Defaults to "managed" for rows backed by the ``managed_test_cases``
    # table; ``"automation"`` for synthesised rows derived from per-run
    # ``test_cases`` (returned by /cases when ``include_automation=true``).
    # The frontend uses this to render an "Automation-ingested" badge and
    # disable edit affordances on automation rows.
    source: str = "managed"
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: Optional[str] = None
    objective: Optional[str] = None
    preconditions: Optional[str] = None
    steps: Optional[List[dict]] = None
    expected_result: Optional[str] = None
    test_data: Optional[str] = None
    test_type: str
    priority: str
    severity: str
    feature_area: Optional[str] = None
    suite_name: Optional[str] = None
    # Structured suite anchor (migration 0087). Null for legacy rows that
    # haven't been backfilled; new rows created with a ``suite_name`` get
    # this populated by the service create path.
    test_suite_id: Optional[uuid.UUID] = None
    tags: Optional[List[Any]] = None
    status: str
    version: int
    author_id: Optional[uuid.UUID] = None
    assignee_id: Optional[uuid.UUID] = None
    reviewer_id: Optional[uuid.UUID] = None
    is_automated: bool
    automation_status: str
    test_fingerprint: Optional[str] = None
    ai_generated: bool
    ai_quality_score: Optional[int] = None
    ai_review_notes: Optional[dict] = None
    estimated_duration_minutes: Optional[int] = None
    last_executed_at: Optional[datetime] = None
    last_execution_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ManagedTestCaseListResponse(BaseModel):
    items: List[ManagedTestCaseResponse]
    total: int
    page: int
    size: int
    pages: int


class TestCaseVersionResponse(BaseModel):
    id: uuid.UUID
    test_case_id: uuid.UUID
    version: int
    title: str
    description: Optional[str] = None
    steps: Optional[List[dict]] = None
    expected_result: Optional[str] = None
    status: str
    changed_by_id: Optional[uuid.UUID] = None
    change_summary: Optional[str] = None
    change_type: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestCaseReviewResponse(BaseModel):
    id: uuid.UUID
    test_case_id: uuid.UUID
    reviewer_id: Optional[uuid.UUID] = None
    requested_by_id: Optional[uuid.UUID] = None
    status: str
    ai_review_completed: bool
    ai_quality_score: Optional[int] = None
    ai_review_notes: Optional[dict] = None
    ai_reviewed_at: Optional[datetime] = None
    human_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewActionRequest(BaseModel):
    action: str  # approve|reject|request_changes
    notes: Optional[str] = None


class TestCaseCommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_LONG_TEXT)
    comment_type: str = "general"
    parent_id: Optional[uuid.UUID] = None
    step_number: Optional[int] = None


class TestCaseCommentResponse(BaseModel):
    id: uuid.UUID
    test_case_id: uuid.UUID
    author_id: Optional[uuid.UUID] = None
    content: str
    comment_type: str
    parent_id: Optional[uuid.UUID] = None
    step_number: Optional[int] = None
    is_resolved: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestPlanCreate(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    planned_start_date: Optional[datetime] = None
    planned_end_date: Optional[datetime] = None
    assigned_to_id: Optional[uuid.UUID] = None
    tags: Optional[List[str]] = None


class TestPlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    status: Optional[str] = None
    planned_start_date: Optional[datetime] = None
    planned_end_date: Optional[datetime] = None
    actual_start_date: Optional[datetime] = None
    actual_end_date: Optional[datetime] = None
    assigned_to_id: Optional[uuid.UUID] = None
    tags: Optional[List[str]] = None


class TestPlanResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: Optional[str] = None
    objective: Optional[str] = None
    status: str
    planned_start_date: Optional[datetime] = None
    planned_end_date: Optional[datetime] = None
    actual_start_date: Optional[datetime] = None
    actual_end_date: Optional[datetime] = None
    created_by_id: Optional[uuid.UUID] = None
    assigned_to_id: Optional[uuid.UUID] = None
    ai_generated: bool
    total_cases: int
    executed_cases: int
    passed_cases: int
    failed_cases: int
    blocked_cases: int
    tags: Optional[List[str]] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestPlanListResponse(BaseModel):
    items: List[TestPlanResponse]
    total: int
    page: int
    size: int
    pages: int


class TestPlanItemCreate(BaseModel):
    test_case_id: uuid.UUID
    order_index: int = 0
    priority_override: Optional[str] = None


class TestPlanItemResponse(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    test_case_id: uuid.UUID
    order_index: int
    priority_override: Optional[str] = None
    execution_status: str
    executed_by_id: Optional[uuid.UUID] = None
    executed_at: Optional[datetime] = None
    execution_notes: Optional[str] = None
    actual_duration_minutes: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExecuteTestPlanItemRequest(BaseModel):
    execution_status: str  # passed|failed|blocked|skipped
    execution_notes: Optional[str] = None
    actual_duration_minutes: Optional[int] = None


class TestStrategyCreate(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=3, max_length=500)
    version_label: str = Field("v1.0", max_length=50)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    scope: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_approach: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)


class TestStrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=500)
    version_label: Optional[str] = Field(None, max_length=50)
    status: Optional[str] = None
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    scope: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    out_of_scope: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_approach: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    risk_assessment: Optional[List[dict]] = None
    test_types: Optional[List[dict]] = None
    entry_criteria: Optional[List[str]] = None
    exit_criteria: Optional[List[str]] = None
    environments: Optional[List[dict]] = None
    automation_approach: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    defect_management: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)


class TestStrategyResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version_label: str
    status: str
    objective: Optional[str] = None
    scope: Optional[str] = None
    out_of_scope: Optional[str] = None
    test_approach: Optional[str] = None
    risk_assessment: Optional[List[Any]] = None
    test_types: Optional[List[Any]] = None
    entry_criteria: Optional[List[Any]] = None
    exit_criteria: Optional[List[Any]] = None
    environments: Optional[List[Any]] = None
    automation_approach: Optional[str] = None
    defect_management: Optional[str] = None
    ai_generated: bool
    ai_model_used: Optional[str] = None
    created_by_id: Optional[uuid.UUID] = None
    approved_by_id: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    action: str
    actor_id: Optional[uuid.UUID] = None
    actor_name: Optional[str] = None
    old_values: Optional[dict] = None
    new_values: Optional[dict] = None
    details: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    items: List[AuditLogResponse]
    total: int
    page: int
    size: int
    pages: int


# ── Suite Membership Traceability Schemas (TS-1) ────────────────────────────


class SuiteMembershipResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    suite_name: str
    test_fingerprint: str
    test_name: str
    class_name: Optional[str] = None
    managed_test_case_id: Optional[uuid.UUID] = None
    source: str
    status: str
    last_seen_run_id: Optional[uuid.UUID] = None
    first_seen_run_id: Optional[uuid.UUID] = None
    deleted_at_run_id: Optional[uuid.UUID] = None
    review_tag: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class SuiteMembershipEventResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    suite_name: str
    test_fingerprint: str
    test_name: str
    event_type: str
    run_id: Optional[uuid.UUID] = None
    old_values: Optional[dict] = None
    new_values: Optional[dict] = None
    details: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SuiteSyncSummary(BaseModel):
    suite_name: str
    run_id: uuid.UUID
    added_count: int = 0
    deleted_count: int = 0
    modified_count: int = 0
    restored_count: int = 0
    unchanged_count: int = 0


# ── Test Suite & Canonical Test Case Schemas (Phase 1, migration 0075) ──────


class TestSuiteCreate(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    tags: Optional[List[str]] = None
    # Optional owner picked at creation. When provided, the suite-owner
    # row is written immediately via ``set_suite_owner`` — which enforces
    # the QA_LEAD role check (HTTP 400 if the user isn't eligible). Leave
    # unset to let the project's default QA lead become the implicit
    # owner via the read-time fallback chain.
    owner_user_id: Optional[uuid.UUID] = None


class TestSuiteUpdate(BaseModel):
    """None = keep existing value."""
    name: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    tags: Optional[List[str]] = None


class TestSuiteResponse(TimestampMixin):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: Optional[str] = None
    is_default: bool
    tags: Optional[List[str]] = None
    test_case_count: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class TestSuiteListResponse(BaseModel):
    items: List[TestSuiteResponse]
    total: int


class CanonicalTestCaseResponse(TimestampMixin):
    id: uuid.UUID
    project_id: uuid.UUID
    test_suite_id: uuid.UUID
    test_suite_name: Optional[str] = None
    test_fingerprint: str
    test_name: str
    class_name: Optional[str] = None
    status: str
    source: str
    first_seen_run_id: Optional[uuid.UUID] = None
    last_seen_run_id: Optional[uuid.UUID] = None
    # Per-run TestCase.id matching this canonical's fingerprint in the
    # last_seen_run. Surfaced so the suite-detail UI can deep-link to
    # ``/runs/<run>/tests/<case>``. ``None`` when unresolved (e.g. the run was
    # GC'd) — UI then falls back to the run detail page.
    last_seen_test_case_id: Optional[uuid.UUID] = None
    deleted_at_run_id: Optional[uuid.UUID] = None
    managed_test_case_id: Optional[uuid.UUID] = None
    review_tag: Optional[str] = None
    tags: Optional[List[str]] = None
    run_count: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class CanonicalTestCaseListResponse(BaseModel):
    items: List[CanonicalTestCaseResponse]
    total: int


class CanonicalTestCaseLinkRequest(BaseModel):
    """Move a canonical test case to a different suite within the same project."""
    test_suite_id: uuid.UUID


class CanonicalTestCaseBulkLinkRequest(BaseModel):
    """Move multiple canonical test cases to a different suite within the
    same project. Pair with ``POST /api/v1/canonical-test-cases/bulk-link``."""
    target_test_suite_id: uuid.UUID
    # Hard ceiling matches the service-side ``BULK_LINK_MAX_IDS`` so the
    # validation 422 happens before the handler runs. The minimum of 1
    # rules out an empty-body request that does nothing — callers should
    # not POST a no-op.
    canonical_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=200)


class CanonicalTestCaseBulkLinkResponse(BaseModel):
    """Outcome of a bulk-link request. ``moved`` and
    ``skipped_already_in_target`` always sum to the number of ids that
    actually resolved to a canonical row; ``missing_ids`` lists requested
    ids that didn't resolve (stale UI selection, deleted in flight)."""
    moved: int
    skipped_already_in_target: int
    missing_ids: List[uuid.UUID]


class AIGenerateTestCasesRequest(BaseModel):
    project_id: uuid.UUID
    requirements: str = Field(..., min_length=3)
    persist: bool = False  # if True, save generated cases as DRAFT


class AIGenerateTestCasesResponse(BaseModel):
    test_cases: List[dict]
    coverage_summary: Optional[str] = None
    gaps_noted: List[str] = []
    created_ids: List[str] = []


class AIReviewTestCaseResponse(BaseModel):
    quality_score: Optional[int] = None
    grade: Optional[str] = None
    summary: Optional[str] = None
    score_breakdown: Optional[dict] = None
    issues: Optional[List[dict]] = None
    suggestions: Optional[List[dict]] = None
    best_practices_violations: Optional[List[str]] = None
    coverage_gaps: Optional[List[str]] = None
    positive_aspects: Optional[List[str]] = None
    error: Optional[str] = None


class AICoverageAnalysisRequest(BaseModel):
    project_id: uuid.UUID
    requirements: str = Field(..., min_length=3)


class AICoverageAnalysisResponse(BaseModel):
    coverage_score: Optional[int] = None
    covered_areas: Optional[List[str]] = None
    partial_coverage: Optional[List[dict]] = None
    uncovered_areas: Optional[List[str]] = None
    recommended_new_tests: Optional[List[dict]] = None
    risk_assessment: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None


class AIGenerateStrategyRequest(BaseModel):
    project_id: uuid.UUID
    project_context: str = Field(..., min_length=3)
    strategy_name: Optional[str] = None


class AIOptimizePlanRequest(BaseModel):
    project_id: uuid.UUID
    plan_name: Optional[str] = None
    constraints: Optional[str] = None


class AIOptimizePlanResponse(BaseModel):
    optimized_order: Optional[List[dict]] = None
    execution_phases: Optional[List[dict]] = None
    total_estimated_duration_minutes: Optional[int] = None
    parallel_execution_possible: Optional[bool] = None
    parallel_groups: Optional[List[Any]] = None
    risk_areas_first: Optional[bool] = None
    optimization_notes: Optional[str] = None
    error: Optional[str] = None


class AITaskEnqueueResponse(BaseModel):
    task_id: str
    status: str = "queued"


class AITaskStatusResponse(BaseModel):
    task_id: str
    status: str  # pending | success | failure
    result: Optional[dict] = None
    error: Optional[str] = None


# ── Commit attribution (Epic 8 US-8.1) ────────────────────────────────────────

class SuppliedCommit(BaseModel):
    """One commit in a caller-supplied commit range (US-8.1 air-gapped path).

    CI/SDK callers that already know the commits landed since the last green
    run can push them on ingest so TestLookup needs NO outbound VCS call.
    Bounded so a payload can't balloon: ``files`` is capped and the enclosing
    ``commit_range`` list is capped on each ingest schema.
    """
    sha: str = Field(..., min_length=1, max_length=64)
    author: Optional[str] = Field(None, max_length=255)
    message: Optional[str] = Field(None, max_length=2000)
    files: Optional[List[str]] = Field(None, max_length=500)
    committed_at: Optional[str] = Field(None, max_length=40)


# Max commits accepted on a single ingest's supplied commit range.
_COMMIT_RANGE_MAX = 100


class SuppliedCommitRange(BaseModel):
    """A caller-supplied commit range WITH its boundary refs.

    The original US-8.1 wire shape for ``commit_range`` was a bare list of
    commits, which threw away the range's boundary: the backend stored
    ``base_commit = NULL`` and nobody downstream could reconstruct what the
    range was *relative to*. That makes the row useless as test-impact
    (Epic 10) training data. This object form carries the boundary the
    caller already knows (it ran ``git log base..head`` to build the list).

    Both wire shapes stay accepted — ``commit_range`` is
    ``list[SuppliedCommit] | SuppliedCommitRange`` on every ingest schema,
    so existing SDK/CLI callers that push a bare list are unaffected.
    Field aliases accept ``base``/``base_commit`` and ``head``/``head_commit``
    so callers do not have to guess which spelling we wanted.
    """
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    base: Optional[str] = Field(
        None, max_length=64,
        validation_alias=AliasChoices("base", "base_commit", "from_commit"),
        description="Commit the range starts AFTER (exclusive) — the baseline ref.",
    )
    head: Optional[str] = Field(
        None, max_length=64,
        validation_alias=AliasChoices("head", "head_commit", "to_commit"),
        description="Commit the range ends AT (inclusive) — usually this run's commit.",
    )
    commits: List[SuppliedCommit] = Field(
        default_factory=list, max_length=_COMMIT_RANGE_MAX,
    )


# The accepted wire shapes for a supplied commit range: the legacy bare list
# or the boundary-carrying object above.
SuppliedCommitRangeInput = Union[List[SuppliedCommit], SuppliedCommitRange]


# ── Live Stream Schemas ───────────────────────────────────────────────────────

class LiveSessionCreate(BaseModel):
    """Request body to register a new live execution session.

    ``project_id`` accepts either a project UUID *or* a human-readable project
    name (case-insensitive exact match). The server resolves it to a real UUID
    in ``stream_service.create_session``. Keeping the field name ``project_id``
    preserves wire compatibility with SDK callers that already map their
    ``testlookup.project`` config (conventionally a name, à la
    ``rp.project``) onto this field.
    """
    project_id: str = Field(..., min_length=1, max_length=255)
    client_name: str = Field(..., min_length=1, max_length=255)
    machine_id: Optional[str] = Field(None, max_length=255)
    build_number: Optional[str] = Field(None, max_length=100)
    framework: Optional[str] = Field(None, max_length=50)  # pytest|junit|testng|mocha|…
    branch: Optional[str] = Field(None, max_length=255)
    commit_hash: Optional[str] = Field(None, max_length=64)
    total_tests: Optional[int] = Field(None, ge=0)
    metadata: Optional[dict] = None
    # Optional: name of the release this execution belongs to.
    # Auto-created in "planning" status if it does not exist in the project.
    release_name: Optional[str] = Field(None, max_length=255)
    # Human-readable launch label (analogous to ReportPortal's rp.launch).
    # When present this is what gets shown to humans in Live Execution and
    # Runs columns; when null the UI falls back to build_number.
    launch_name: Optional[str] = Field(None, max_length=255)
    # Run-level suite identifier (testlookup.suite, falling back to
    # testlookup.launch on the SDK side). Propagated to LiveSession.suite_name
    # and TestRun.primary_suite_name so every page that links to the run
    # shows a single, user-configured suite label.
    suite_name: Optional[str] = Field(None, max_length=500)
    # CI context (US-4.3) — stored in LiveSession.extra_metadata["ci_context"]
    # and stamped onto the TestRun at persist time (no LiveSession columns).
    ci_provider: Optional[str] = Field(None, max_length=30)
    ci_repo: Optional[str] = Field(None, max_length=300)
    pr_number: Optional[int] = Field(None, ge=1)
    ci_actor: Optional[str] = Field(None, max_length=120)
    ci_run_url: Optional[str] = Field(None, max_length=1000)
    # Commit attribution (US-8.1, air-gapped path) — optional pushed commit
    # list so air-gapped callers get suspect ranking with no VCS call. Accepts
    # either the legacy bare list or the boundary-carrying
    # ``{base, head, commits}`` object (see ``SuppliedCommitRange``).
    commit_range: Optional[SuppliedCommitRangeInput] = None


class LiveSessionResponse(BaseModel):
    """Response returned when a session is created."""
    session_id: str
    session_token: str    # plaintext token — client stores this for X-Session-Token header
    run_id: str
    project_id: str
    expires_in: int       # seconds until token expires
    created_at: datetime


class LiveEvent(BaseModel):
    """A single test execution event from a client machine."""
    event_type: str = Field(
        ...,
        description=(
            "run_start | test_start | test_result | log | metric | run_complete | live_heartbeat. "
            "live_heartbeat is a no-op refresh emitted by SDK clients during long inter-test "
            "gaps — it only bumps the Redis last_event_at field so the reaper doesn't close "
            "the session as idle."
        ),
    )
    test_name: Optional[str] = Field(None, max_length=1000)
    status: Optional[str] = Field(None, description="PASSED | FAILED | SKIPPED | BROKEN")
    duration_ms: Optional[int] = Field(None, ge=0)
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    suite_name: Optional[str] = Field(None, max_length=500)
    class_name: Optional[str] = Field(None, max_length=500)
    tags: Optional[List[str]] = None
    # Client-side unix epoch ms — preserved for ordering; falls back to server time if absent
    timestamp_ms: Optional[int] = None
    metadata: Optional[dict] = None


class LiveEventBatch(BaseModel):
    """
    A batch of events sent from a client machine.
    Batching amortises HTTP overhead — 50–1000 events per call is recommended.
    """
    session_id: str
    run_id: str
    events: List[LiveEvent] = Field(..., min_length=1, max_length=1000)


class LiveEventBatchResponse(BaseModel):
    accepted: int
    run_id: str
    session_id: str


class LiveStreamMeta(BaseModel):
    """Optional CI/run metadata that enriches the auto-created session.

    All fields are optional — when omitted the server falls back to the API
    key's name (for client_name) and the run_id (for build_number).
    """
    build_number: Optional[str] = Field(None, max_length=100)
    branch: Optional[str] = Field(None, max_length=255)
    commit_hash: Optional[str] = Field(None, max_length=64)
    framework: Optional[str] = Field(None, max_length=50)
    total_tests: Optional[int] = Field(None, ge=0)
    machine_id: Optional[str] = Field(None, max_length=255)
    release_name: Optional[str] = Field(None, max_length=255)
    launch_name: Optional[str] = Field(None, max_length=255)
    metadata: Optional[dict] = None


class LiveStreamIngestRequest(BaseModel):
    """API-key-authenticated streaming ingest. Server auto-manages the session.

    A client-chosen ``run_id`` (any stable identifier — CI build id, UUID, etc.)
    keys the live session along with the API key's bound project. The first
    call for a given ``(project_id, run_id)`` pair auto-creates the session;
    subsequent calls reuse it. Clients never call ``/sessions`` themselves.
    """
    run_id: str = Field(..., min_length=1, max_length=255)
    events: List[LiveEvent] = Field(..., min_length=1, max_length=1000)
    meta: Optional[LiveStreamMeta] = None


class LiveStreamIngestResponse(BaseModel):
    accepted: int
    run_id: str
    session_id: str
    created_session: bool


# ── Ingest Schemas (unified batch + file upload) ──────────────────────────────

class IngestTestResult(BaseModel):
    """A single test result in a JSON batch ingest."""
    test_name: str = Field(..., min_length=1, max_length=1000)
    status: str = Field(..., pattern=r"^(PASSED|FAILED|SKIPPED|BROKEN)$")
    duration_ms: Optional[int] = Field(None, ge=0)
    suite_name: Optional[str] = Field(None, max_length=500)
    class_name: Optional[str] = Field(None, max_length=500)
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class IngestPayload(BaseModel):
    """JSON batch ingest request body for POST /api/v1/ingest."""
    project_id: str = Field(..., description="Project UUID")
    build_number: str = Field(..., min_length=1, max_length=255)
    results: List[IngestTestResult] = Field(..., min_length=1, max_length=50_000)
    branch: Optional[str] = Field(None, max_length=255)
    commit_hash: Optional[str] = Field(None, max_length=64)
    framework: Optional[str] = Field(None, max_length=50)
    trigger_source: Optional[str] = "api"
    release_name: Optional[str] = Field(None, max_length=255)
    # CI context (US-4.3) — SDKs auto-detect these from standard CI env vars
    # (GITHUB_*, GITLAB CI_*, Jenkins CHANGE_*); explicit values win.
    ci_provider: Optional[str] = Field(None, max_length=30)
    ci_repo: Optional[str] = Field(None, max_length=300)
    pr_number: Optional[int] = Field(None, ge=1)
    ci_actor: Optional[str] = Field(None, max_length=120)
    ci_run_url: Optional[str] = Field(None, max_length=1000)
    # Environment this run executed against (roadmap Phase 0, migration 0129).
    # Optional: omitting it means "not recorded", and readers derive a
    # best-effort key rather than assuming every silent run shared one
    # environment. Free text so a team's own vocabulary (staging, ci-linux,
    # pixel-7) survives; normalized on the way in.
    environment: Optional[str] = Field(None, max_length=100)
    # Commit attribution (US-8.1, air-gapped path) — optional pushed commit
    # list ([{sha, author, message, files}]) so callers can supply the range
    # since the last green run and get suspect ranking with no VCS call.
    # Accepts either the legacy bare list or the boundary-carrying
    # ``{base, head, commits}`` object (see ``SuppliedCommitRange``) — only
    # the latter lets the backend persist the range's base ref.
    commit_range: Optional[SuppliedCommitRangeInput] = None


class IngestResponse(BaseModel):
    """Response for accepted ingest request."""
    status: str = "accepted"
    run_id: str
    task_id: str
    total_results: int


class UploadStatusResponse(BaseModel):
    """Async status of an uploaded report (GET /api/v1/ingest/uploads/{task_id}).

    state: pending | parsing | ingesting | succeeded | failed.
    """
    task_id: str
    run_id: Optional[str] = None
    state: str
    progress: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[dict] = None


class LiveSessionState(BaseModel):
    """Live state of an active or recently completed session."""
    run_id: str
    # Canonical TestRun.id this live session resolves to (deterministic when
    # ``run_id`` is a non-UUID slug). The frontend uses this — not the raw
    # ``run_id`` — for ``/runs/<id>`` navigation, since the latter 422's
    # against the UUID-typed path validator on the GET /api/v1/runs/{run_id}
    # endpoint.
    test_run_id: Optional[str] = None
    project_id: str
    build_number: str
    status: str
    total: int
    passed: int
    failed: int
    skipped: int
    broken: int
    pass_rate: float
    current_test: Optional[str] = None
    started_at: Optional[str] = None
    last_event_at: Optional[str] = None
    client_name: Optional[str] = None
    completed_at: Optional[str] = None
    release_name: Optional[str] = None
    launch_name: Optional[str] = None
    # Run-level suite identifier (testlookup.suite > testlookup.launch).
    # Surfaced by /live's UI as a dedicated Suite column.
    suite_name: Optional[str] = None
    # Per-(project, primary_suite_name) human-readable run number, 1-based.
    # The /live UI shows ``Run #N`` instead of the SDK-supplied
    # build_number so users can correlate the same run across pages.
    run_seq: Optional[int] = None


class ActiveSessionsResponse(BaseModel):
    sessions: List[LiveSessionState]
    count: int


# ── SMTP / App Settings Schemas ────────────────────────────────

class SmtpConfigRead(BaseModel):
    """SMTP server configuration returned to the client (no password)."""
    enabled: bool
    host: str
    port: int
    user: Optional[str]
    from_address: str
    implicit_tls: bool = Field(
        description=(
            "When True, implicit TLS (SSL/TLS on connect, typically port 465) is used. "
            "When False, STARTTLS (upgrade after connect, typically port 587) is used. "
            "Plain (unencrypted) SMTP is not supported."
        )
    )
    password_set: bool  # True if a password is stored; never returns the value


class SmtpConfigUpdate(BaseModel):
    """Payload for updating SMTP server configuration."""
    enabled: bool = False
    host: str = Field(default="localhost", max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    user: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, max_length=1000)  # None = keep existing
    from_address: str = Field(default="noreply@testlookup.io", max_length=255)
    implicit_tls: bool = Field(
        default=True,
        description=(
            "When True, implicit TLS (SSL/TLS on connect, typically port 465) is used. "
            "When False, STARTTLS (upgrade after connect, typically port 587) is used. "
            "Plain (unencrypted) SMTP is not supported."
        ),
    )


class SmtpTestResult(BaseModel):
    success: bool
    message: str


# ── AI Configuration Schemas ─────────────────────────────────

class AIConfigRead(BaseModel):
    """AI / LLM configuration returned to the client (no API keys)."""
    llm_provider: str
    llm_model: str
    llm_temperature: float
    llm_max_tokens: int
    # EFFECTIVE offline state, after the AI_OFFLINE_MODE environment ceiling.
    # The env var can only tighten this — a stored override never loosens it.
    ai_offline_mode: bool
    # Why it is what it is: "env" (pinned by the environment) | "override"
    # (stored setting turned it on) | "not_offline". Plain str, not a Literal —
    # a strict enum over a widening vocabulary 422s the whole response.
    ai_offline_mode_source: str = "not_offline"
    # True ⇒ the toggle is read-only in this deployment; the UI must say so
    # rather than accept a click it cannot honour.
    ai_offline_mode_env_pinned: bool = False
    embedding_provider: str
    embedding_model: str
    ai_confidence_threshold: int
    # US-15.2 provenance: "ai_config" (an operator override is stored) |
    # "env_default" (the deployment default is in force). Plain str for the
    # same reason as ai_offline_mode_source.
    ai_confidence_threshold_source: str = "env_default"
    ai_timeout_seconds: int
    deep_investigation_enabled: bool
    finetune_enabled: bool
    openai_key_set: bool
    google_key_set: bool
    anthropic_key_set: bool = False                   # LP-3: Anthropic/Claude support
    openrouter_key_set: bool = False                  # B-4: OpenRouter support
    base_url: Optional[str] = None                    # LP-3: provider endpoint override
    # Analysis mode — LLM-free operation
    analysis_mode: str                               # "llm" | "ml" | "rules" | "auto"
    ml_model_available: bool = False                  # True if a trained ML model exists
    ml_model_accuracy: Optional[float] = None         # last known accuracy (0-1)
    ml_training_sample_count: int = 0                 # total labeled samples available
    # AI-F1 label integrity — honest learning-loop status
    ml_human_label_count: int = 0                     # human-provenance labels (feedback/corrections)
    ml_human_label_floor: int = 50                    # below this, ML is bootstrap (LLM-imitating)
    ml_maturity: str = "not_trained"                  # not_trained | bootstrap_llm_imitating | human_calibrated
    # Knowledge RAG feature toggle
    knowledge_rag_enabled: bool = False               # True if grounded test generation is active


class AIConfigUpdate(BaseModel):
    """Payload for updating AI configuration. None = keep existing."""
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    llm_max_tokens: Optional[int] = Field(None, ge=256, le=32768)
    ai_offline_mode: Optional[bool] = None
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    ai_confidence_threshold: Optional[int] = Field(None, ge=0, le=100)
    ai_timeout_seconds: Optional[int] = Field(None, ge=30, le=1800)
    deep_investigation_enabled: Optional[bool] = None
    finetune_enabled: Optional[bool] = None
    openai_api_key: Optional[str] = Field(None, max_length=500)
    google_api_key: Optional[str] = Field(None, max_length=500)
    anthropic_api_key: Optional[str] = Field(None, max_length=500)  # LP-3
    openrouter_api_key: Optional[str] = Field(None, max_length=500)  # B-4
    base_url: Optional[str] = Field(None, max_length=500)           # LP-3: endpoint override
    analysis_mode: Optional[str] = Field(None, pattern=r"^(llm|ml|rules|auto)$")
    knowledge_rag_enabled: Optional[bool] = None


# ── Integrations Schemas ─────────────────────────────────────

class IntegrationsConfigRead(BaseModel):
    """External integrations configuration (no tokens)."""
    jira_enabled: bool
    jira_domain: Optional[str]
    jira_email: Optional[str]
    jira_token_set: bool
    jira_default_project_key: str
    splunk_enabled: bool
    splunk_base_url: Optional[str]
    splunk_token_set: bool
    ocp_enabled: bool
    ocp_api_url: Optional[str]
    ocp_token_set: bool
    ocp_default_namespace: str
    slack_enabled: bool
    slack_webhook_url: Optional[str] = None  # always redacted; compatibility field
    slack_webhook_set: bool
    slack_default_channel: str
    teams_enabled: bool
    teams_webhook_url: Optional[str] = None  # always redacted; compatibility field
    teams_webhook_set: bool
    github_repo: Optional[str]
    github_token_set: bool


class IntegrationsConfigUpdate(BaseModel):
    """Payload for updating integrations. None = keep existing."""
    jira_enabled: Optional[bool] = None
    jira_domain: Optional[str] = Field(None, max_length=500)
    jira_email: Optional[str] = Field(None, max_length=255)
    jira_api_token: Optional[str] = Field(None, max_length=500)
    jira_default_project_key: Optional[str] = Field(None, max_length=50)
    splunk_enabled: Optional[bool] = None
    splunk_base_url: Optional[str] = Field(None, max_length=500)
    splunk_api_token: Optional[str] = Field(None, max_length=500)
    ocp_enabled: Optional[bool] = None
    ocp_api_url: Optional[str] = Field(None, max_length=500)
    ocp_sa_token: Optional[str] = Field(None, max_length=1000)
    ocp_default_namespace: Optional[str] = Field(None, max_length=255)
    slack_enabled: Optional[bool] = None
    slack_webhook_url: Optional[str] = Field(None, max_length=1000)
    slack_default_channel: Optional[str] = Field(None, max_length=100)
    teams_enabled: Optional[bool] = None
    teams_webhook_url: Optional[str] = Field(None, max_length=1000)
    github_repo: Optional[str] = Field(None, max_length=500)
    github_token: Optional[str] = Field(None, max_length=500)


# ── Data & Storage Schemas ───────────────────────────────────

class StorageConfigRead(BaseModel):
    """Data & storage configuration returned to the client.
    Infrastructure connection details are masked to prevent credential/topology disclosure."""
    storage_backend: str
    # Infrastructure details are masked — only admin needs these, and they're in .env
    postgres_connected: bool              # replaced raw host/port/db
    mongo_connected: bool                 # replaced raw host/port/db
    redis_connected: bool                 # replaced raw url
    # Editable storage settings (not sensitive)
    minio_endpoint: str
    minio_bucket_name: str
    minio_use_ssl: bool
    chroma_host: str
    chroma_port: int
    chroma_collection: str


class StorageConfigUpdate(BaseModel):
    """Payload for updating storage config. None = keep existing."""
    storage_backend: Optional[Literal["minio", "s3", "local"]] = None
    chroma_host: Optional[str] = Field(None, min_length=1, max_length=255)
    chroma_port: Optional[int] = Field(None, ge=1, le=65535)
    chroma_collection: Optional[str] = Field(None, min_length=1, max_length=255)
    minio_endpoint: Optional[str] = Field(None, min_length=1, max_length=500)
    minio_bucket_name: Optional[str] = Field(None, min_length=1, max_length=255)
    minio_use_ssl: Optional[bool] = None


# ── User Management Schemas ───────────────────────────────────

class UserListResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    full_name: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UpdateUserRoleRequest(BaseModel):
    role: UserRole


class UpdateUserStatusRequest(BaseModel):
    is_active: bool


class UpdateUserProfileRequest(BaseModel):
    """Editable user attributes. Only non-None fields are applied."""
    email: Optional[EmailStr] = None
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class InviteUserRequest(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.QA_ENGINEER


class InviteUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    expires_at: datetime
    invitation_link: str  # frontend URL with token
    model_config = ConfigDict(from_attributes=True)


class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = None
    role: UserRole = UserRole.QA_ENGINEER


class AdminCreateUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    full_name: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: datetime
    temp_password: str  # shown once — admin must share with the user


# ── Project Member Schemas ────────────────────────────────────

class ProjectMemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    role: UserRole
    created_at: datetime
    # Joined user fields
    email: str
    username: str
    full_name: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class AddProjectMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: UserRole = UserRole.QA_ENGINEER


class UpdateProjectMemberRoleRequest(BaseModel):
    role: UserRole


# ── API Key Schemas ───────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    scopes: List[str] = Field(default_factory=list)
    expires_days: Optional[int] = Field(None, ge=1, le=365)
    project_id: Optional[uuid.UUID] = Field(None, description="Bind key to a single project (ADMIN only)")
    target_user_id: Optional[uuid.UUID] = Field(None, description="Create key for another user (ADMIN only)")


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_hint: str
    scopes: List[str]
    project_id: Optional[uuid.UUID] = None
    is_active: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Returned ONCE at creation — includes the plaintext key."""
    raw_key: str


# ── Release Council Schemas ──────────────────────────────────────────────────

class OverrideAuditEntry(BaseModel):
    """Single entry in the override audit trail."""
    timestamp: str
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    before_recommendation: str
    before_risk_score: int
    after_recommendation: str
    reason: str
    # Policy context (ENT-02) — added fields have defaults for backward compat
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None


class ReleaseCouncilResponse(BaseModel):
    """Extended release decision with council context."""
    run_id: str
    recommendation: str
    risk_score: int
    composite_risk: Optional[float] = None
    dimension_scores: List[DimensionScore] = []
    blocking_issues: List[str] = []
    conditions_for_go: List[str] = []
    reasoning: Optional[str] = None
    score_model_version: Optional[int] = None
    # Council context
    input_snapshot: Optional[dict] = None
    cluster_insights: List[ClusterInsightResponse] = []
    baseline_diff: Optional[BaselineDiff] = None
    open_defects_by_component: List[dict] = []
    # Override audit
    human_override: Optional[str] = None
    overridden_by: Optional[str] = None
    original_recommendation: Optional[str] = None
    original_risk_score: Optional[int] = None
    override_audit: List[OverrideAuditEntry] = []
    # Test run context
    pass_rate: Optional[float] = None
    build_number: Optional[str] = None
    # Policy context (ENT-02)
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    policy_level: Optional[str] = None  # "project" | "system" | "hardcoded"
    rule_evaluations: List["RuleEvaluationResponse"] = []
    # Synthesised quick-look response — when True, this council view was
    # derived from the run's aggregates because no ReleaseDecision row
    # exists yet (deep investigation has not run). The UI surfaces a
    # note prompting the user to run deep investigation for richer
    # context (clusters, defect breakdown, override audit, LLM narrative).
    synthesized: bool = False
    # Pass-rate band classification from the active ReleaseGatePolicy
    # (migration 0079 + 2026-05-14 feature). When set, the band is what
    # the /overview verdict colour also reads from — keeping the two pages
    # in lockstep. ``band_downgrades`` enumerates which hard caps fired,
    # e.g. ``["p0_defects:2>0"]``.
    release_readiness_band: Optional[str] = None
    band_downgrades: List[str] = []


class ReleaseCouncilOverrideRequest(BaseModel):
    """Override request with enhanced audit fields."""
    override_recommendation: str
    reason: str


# ── Test Health Coach Schemas ────────────────────────────────────────────────

class TestHealthViolation(BaseModel):
    pattern: str
    severity: str
    occurrences: int = 1


class TestHealthFinding(BaseModel):
    """Per-test health finding."""
    test_case_id: str
    test_name: str
    health_score: int
    violations: List[TestHealthViolation] = []
    critical_count: int = 0
    warning_count: int = 0
    recommendation: str = ""
    anti_patterns: List[str] = []


class TestHealthResponse(BaseModel):
    """Test health findings for a run."""
    run_id: str
    total_analyzed: int = 0
    with_violations: int = 0
    avg_health_score: Optional[float] = None
    findings: List[TestHealthFinding] = []


class FlakyCoachEntry(BaseModel):
    """Single flaky test with coaching recommendation."""
    test_fingerprint: str
    test_name: str
    suite_name: Optional[str] = None
    failure_rate: float
    total_runs: int = 0
    failed_runs: int = 0
    flaky_since: Optional[str] = None
    last_failure_at: Optional[str] = None
    quarantine_recommendation: str = "MONITOR"
    stabilization_actions: List[str] = []
    impact_score: float = 0.0
    status_history: List[str] = []
    # FLK-P1 intermittency signals (computed at read time; None when the test
    # has no granular window to score). status_volatility = flips/(runs-1);
    # error_signature_diversity = unique error prefixes / fail_count; label
    # discriminates intermittent flakiness from low-volatility regression.
    status_volatility: Optional[float] = None
    error_signature_diversity: Optional[float] = None
    stack_trace_diversity: Optional[float] = None
    in_run_retry_rate: Optional[float] = None
    intermittency_label: Optional[str] = None
    # FLK-P2 statistical confidence: Wilson 95% interval on the failure ratio.
    # None for manual-triage entries (no statistical sample) and historical
    # cached rows not yet recomputed. The lower bound expresses statistical
    # strength — for a fixed point estimate it rises with the sample size, so a
    # flake confirmed over many runs outranks one inferred from a few.
    flaky_confidence_low: Optional[float] = None
    flaky_confidence_high: Optional[float] = None
    # FLK-P3 ML flakiness-confidence ∈ [0, 1], learned from human quarantine
    # decisions. None when no trained model is available.
    is_flaky_confidence: Optional[float] = None
    # FLK-P4 likely-cause attribution (read-time, from intermittency signals +
    # the ML confidence). None when there is no granular window to attribute.
    flaky_likely_cause: Optional[str] = None
    flaky_likely_cause_code: Optional[str] = None
    # FLK-P5 granular step-level attribution from the latest step snapshot:
    # the failing step's name + a surgical-fix recommendation. None when the
    # test has no captured steps / no failing step.
    failing_step: Optional[str] = None
    failing_step_detail: Optional[str] = None


class FlakyCoachResponse(BaseModel):
    """Project-level flaky coach leaderboard."""
    project_id: str
    total_flaky: int = 0
    quarantine_candidates: int = 0
    entries: List[FlakyCoachEntry] = []


# ── Granular test-case history / flakiness / metadata (Phase 2) ──────────────


class TestCaseHistoryPointResponse(BaseModel):
    """One cross-run point in a logical test's timeline (most-recent-first)."""
    model_config = ConfigDict(from_attributes=True)

    run_id: Optional[str] = None
    run_label: str
    build_number: Optional[str] = None
    run_seq: Optional[int] = None
    status: str
    duration_ms: Optional[int] = None
    created_at: Optional[datetime] = None


class TestCaseFlakinessResponse(BaseModel):
    """Computed flakiness for the in-window timeline.

    ``failure_rate``/``failure_rate_pct`` match ``analytics_service.flaky_tests``;
    ``classification`` + ``impact_score`` reuse ``test_health_coach_service``
    thresholds (no new formula).
    """
    model_config = ConfigDict(from_attributes=True)

    is_flaky: bool = False
    failure_rate: float = 0.0
    failure_rate_pct: float = 0.0
    impact_score: float = 0.0
    classification: str = "HEALTHY"
    window_days: int = 30
    total_runs: int = 0
    passed: int = 0
    failed: int = 0


class TestCaseMetadataResponse(BaseModel):
    """Identity metadata: owner, effective suite, first/last seen, timestamps."""
    model_config = ConfigDict(from_attributes=True)

    owner: Optional[str] = None
    assigned_to_user_id: Optional[str] = None
    suite: Optional[str] = None
    severity: Optional[str] = None
    feature: Optional[str] = None
    first_seen_run_id: Optional[str] = None
    first_seen_run_label: Optional[str] = None
    first_seen_at: Optional[datetime] = None
    last_seen_run_id: Optional[str] = None
    last_seen_run_label: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TestCaseHistoryResponse(BaseModel):
    """Wrapper for GET /runs/{run_id}/tests/{test_id}/history."""
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    test_id: str
    test_fingerprint: Optional[str] = None
    test_name: str
    history: List[TestCaseHistoryPointResponse] = []
    flakiness: TestCaseFlakinessResponse
    metadata: TestCaseMetadataResponse


# ── SSO / SAML / SCIM Schemas (ENT-01) ──────────────────────────────────────


class SSOConfigCreate(BaseModel):
    """Create a new SSO configuration."""
    display_name: str = Field(..., min_length=2, max_length=255)
    provider_type: SSOProviderType = SSOProviderType.SAML
    idp_entity_id: str = Field(..., min_length=1, max_length=1000)
    idp_sso_url: str = Field(..., min_length=1, max_length=2000)
    idp_slo_url: Optional[str] = Field(None, max_length=2000)
    idp_certificate: str = Field(..., min_length=1)  # PEM-encoded X.509
    sp_entity_id: str = Field(..., min_length=1, max_length=1000)
    sp_acs_url: str = Field(..., min_length=1, max_length=2000)
    audience: Optional[str] = Field(None, max_length=1000)
    role_mapping: Optional[dict] = None
    default_role: UserRole = UserRole.VIEWER
    group_attribute: Optional[str] = Field(None, max_length=255)
    enforcement_mode: SSOEnforcementMode = SSOEnforcementMode.OPTIONAL


class SSOConfigUpdate(BaseModel):
    """Partial update of SSO configuration."""
    display_name: Optional[str] = Field(None, min_length=2, max_length=255)
    idp_entity_id: Optional[str] = Field(None, min_length=1, max_length=1000)
    idp_sso_url: Optional[str] = Field(None, min_length=1, max_length=2000)
    idp_slo_url: Optional[str] = Field(None, max_length=2000)
    idp_certificate: Optional[str] = None
    sp_entity_id: Optional[str] = Field(None, min_length=1, max_length=1000)
    sp_acs_url: Optional[str] = Field(None, min_length=1, max_length=2000)
    audience: Optional[str] = Field(None, max_length=1000)
    role_mapping: Optional[dict] = None
    default_role: Optional[UserRole] = None
    group_attribute: Optional[str] = Field(None, max_length=255)
    enforcement_mode: Optional[SSOEnforcementMode] = None
    is_active: Optional[bool] = None


class SSOConfigResponse(BaseModel):
    """SSO configuration response (certificate is masked)."""
    id: uuid.UUID
    display_name: str
    provider_type: SSOProviderType
    idp_entity_id: str
    idp_sso_url: str
    idp_slo_url: Optional[str] = None
    idp_certificate_fingerprint: str = ""  # SHA-256 fingerprint, not the full cert
    sp_entity_id: str
    sp_acs_url: str
    audience: Optional[str] = None
    role_mapping: Optional[dict] = None
    default_role: UserRole
    group_attribute: Optional[str] = None
    enforcement_mode: SSOEnforcementMode
    is_active: bool
    last_test_at: Optional[datetime] = None
    last_test_success: Optional[bool] = None
    last_test_error: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class SSOTestConnectionResponse(BaseModel):
    """Result of testing SSO configuration."""
    success: bool
    message: str
    idp_entity_id: Optional[str] = None
    certificate_valid: Optional[bool] = None
    certificate_expires_at: Optional[str] = None


class SAMLLoginInitResponse(BaseModel):
    """Response with redirect URL for SP-initiated SAML login."""
    redirect_url: str
    request_id: str


class SAMLACSRequest(BaseModel):
    """SAML Assertion Consumer Service callback payload."""
    SAMLResponse: str
    RelayState: Optional[str] = None


class SSOLoginResponse(BaseModel):
    """Token response after successful SSO authentication."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
    is_new_user: bool = False


# ── SCIM 2.0 Schemas ──────────────────────────────────────────

SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_USERNAME_MAX_LENGTH = 100
SCIM_EMAIL_MAX_LENGTH = 255
SCIM_DISPLAY_NAME_MAX_LENGTH = 255
SCIM_EXTERNAL_ID_MAX_LENGTH = 1000
SCIM_EMAILS_MAX_ITEMS = 100
SCIM_GROUPS_MAX_ITEMS = 1000
SCIM_GROUP_VALUE_MAX_LENGTH = 1000
SCIM_GROUP_DISPLAY_MAX_LENGTH = 500


class SCIMName(BaseModel):
    givenName: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)
    familyName: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)
    formatted: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)


class SCIMEmail(BaseModel):
    value: str = Field(..., min_length=1, max_length=SCIM_EMAIL_MAX_LENGTH)
    type: Optional[str] = Field("work", max_length=100)
    primary: bool = True


class SCIMGroup(BaseModel):
    value: str = Field(..., min_length=1, max_length=SCIM_GROUP_VALUE_MAX_LENGTH)
    display: Optional[str] = Field(None, max_length=SCIM_GROUP_DISPLAY_MAX_LENGTH)


class SCIMUserResource(BaseModel):
    """SCIM 2.0 User resource — used for both request and response."""
    schemas: List[str] = [SCIM_USER_SCHEMA]
    id: Optional[str] = None  # set on response
    externalId: Optional[str] = Field(None, max_length=SCIM_EXTERNAL_ID_MAX_LENGTH)
    userName: str = Field(..., min_length=1, max_length=SCIM_USERNAME_MAX_LENGTH)
    name: Optional[SCIMName] = None
    emails: List[SCIMEmail] = Field(default=[], max_length=SCIM_EMAILS_MAX_ITEMS)
    displayName: Optional[str] = Field(None, max_length=SCIM_DISPLAY_NAME_MAX_LENGTH)
    active: bool = True
    groups: List[SCIMGroup] = Field(default=[], max_length=SCIM_GROUPS_MAX_ITEMS)
    meta: Optional[dict] = None

    @field_validator("schemas")
    @classmethod
    def require_user_schema(cls, value: List[str]) -> List[str]:
        if SCIM_USER_SCHEMA not in value:
            raise ValueError(f"schemas must include {SCIM_USER_SCHEMA}")
        return value


class SCIMUserRequest(SCIMUserResource):
    """Inbound SCIM user payload; the protocol schemas member is required."""

    schemas: List[str] = Field(...)

    @model_validator(mode="after")
    def require_storable_derived_display_name(self):
        if self.displayName is None and self.name is not None:
            derived = " ".join(
                part for part in (self.name.givenName, self.name.familyName) if part
            )
            if len(derived) > SCIM_DISPLAY_NAME_MAX_LENGTH:
                raise ValueError(
                    f"derived displayName must be at most {SCIM_DISPLAY_NAME_MAX_LENGTH} characters"
                )
        return self


class SCIMListResponse(BaseModel):
    """SCIM 2.0 ListResponse."""
    schemas: List[str] = ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    totalResults: int
    startIndex: int = 1
    itemsPerPage: int = 100
    Resources: List[SCIMUserResource] = []


class SCIMPatchOp(BaseModel):
    op: str  # "replace", "add", "remove"
    path: Optional[str] = None
    value: Optional[Any] = None


SCIM_PATCH_MAX_OPERATIONS = 100


class SCIMPatchRequest(BaseModel):
    schemas: List[str] = [SCIM_PATCH_SCHEMA]
    Operations: List[SCIMPatchOp] = Field(
        ...,
        min_length=1,
        max_length=SCIM_PATCH_MAX_OPERATIONS,
    )

    @field_validator("schemas")
    @classmethod
    def require_patch_schema(cls, value: List[str]) -> List[str]:
        if SCIM_PATCH_SCHEMA not in value:
            raise ValueError(f"schemas must include {SCIM_PATCH_SCHEMA}")
        return value


class SCIMPatchRequestPayload(SCIMPatchRequest):
    """Inbound PATCH payload; the protocol schemas member is required."""

    schemas: List[str] = Field(...)


class SCIMErrorResponse(BaseModel):
    schemas: List[str] = ["urn:ietf:params:scim:api:messages:2.0:Error"]
    status: str
    detail: str


class SCIMTokenCreate(BaseModel):
    """Create a new SCIM bearer token."""
    name: str = Field(..., min_length=2, max_length=255)
    sso_config_id: Optional[uuid.UUID] = None
    expires_days: Optional[int] = Field(None, ge=1, le=365)


class SCIMTokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    token_hint: str
    sso_config_id: Optional[uuid.UUID] = None
    is_active: bool
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SCIMTokenCreatedResponse(SCIMTokenResponse):
    """Returned once at creation — includes the plaintext token."""
    raw_token: str


# ── Identity Event Schemas ───────────────────────────────────


class IdentityEventResponse(BaseModel):
    id: uuid.UUID
    event_type: IdentityEventType
    user_id: Optional[uuid.UUID] = None
    sso_config_id: Optional[uuid.UUID] = None
    actor_id: Optional[uuid.UUID] = None
    actor_name: Optional[str] = None
    detail: Optional[dict] = None
    ip_address: Optional[str] = None
    success: bool
    error_message: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class IdentityEventListResponse(BaseModel):
    total: int
    items: List[IdentityEventResponse]


class IdentitySyncStatus(BaseModel):
    """Aggregated sync health for the admin dashboard."""
    sso_config_id: Optional[uuid.UUID] = None
    sso_display_name: Optional[str] = None
    total_federated_users: int = 0
    last_sso_login_at: Optional[datetime] = None
    last_scim_sync_at: Optional[datetime] = None
    recent_failures: int = 0
    recent_events: List[IdentityEventResponse] = []


# ── Release Gate Policy Schemas (ENT-02) ─────────────────────────────────────


class PolicyThresholds(BaseModel):
    """Risk score thresholds for GO/NO_GO classification."""
    go_threshold: float = Field(default=20.0, ge=0, le=100)
    no_go_threshold: float = Field(default=55.0, ge=0, le=100)
    pass_rate_minimum: float = Field(default=90.0, ge=0, le=100)
    pass_rate_hard_floor_factor: float = Field(default=0.7, ge=0, le=1.0)


class PolicyDimensionWeights(BaseModel):
    """Weights for 7-dimension risk scoring (should sum to 1.0)."""
    user_impact: float = Field(default=0.25, ge=0, le=1.0)
    env_sensitivity: float = Field(default=0.10, ge=0, le=1.0)
    reproducibility: float = Field(default=0.15, ge=0, le=1.0)
    regression_likely: float = Field(default=0.20, ge=0, le=1.0)
    hist_recurrence: float = Field(default=0.10, ge=0, le=1.0)
    blast_radius: float = Field(default=0.15, ge=0, le=1.0)
    diagnosis_conf: float = Field(default=0.05, ge=0, le=1.0)


class PolicyRule(BaseModel):
    """A single rule within a release gate policy."""
    id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    type: str  # flaky_recurrence | open_defect_limit | dimension_ceiling | override_rules
    enabled: bool = True
    params: dict = Field(default_factory=dict)


class PolicyPassRateBands(BaseModel):
    """Project-level 4-band classification for the build colour and verdict.

    Bands are defined by the *lower* edge of each colour and must be strictly
    increasing: ``orange_min < yellow_min < green_min``. A pass rate below
    ``orange_min`` is red; ``[orange_min, yellow_min)`` is orange;
    ``[yellow_min, green_min)`` is yellow; ``>= green_min`` is green.

    Defaults match the user-requested levels (red <90, orange 90-95,
    yellow 95-99, green >=99). Verdict mapping is fixed: green = GO,
    yellow = GO with watch, orange = CONDITIONAL, red = NO_GO. Hard caps
    (PolicyHardCaps) can downgrade the resolved band by one or two steps.
    """
    orange_min: float = Field(default=90.0, ge=0, le=100)
    yellow_min: float = Field(default=95.0, ge=0, le=100)
    green_min: float = Field(default=99.0, ge=0, le=100)


class PolicyHardCaps(BaseModel):
    """Hard caps that downgrade the pass-rate band before the verdict map.

    Each cap is a (count) threshold; exceeding it downgrades the resolved
    band by one step (green → yellow → orange → red, no wrap). Multiple
    breached caps stack, capped at red. ``None`` (or 0 where ``ge=0``)
    disables the cap.
    """
    max_p0_defects: int = Field(default=0, ge=0, description="Active P0 defects allowed before downgrade")
    max_flaky_count: int = Field(default=10, ge=0, description="Flaky tests allowed before downgrade")
    max_new_failures_24h: int = Field(default=20, ge=0, description="New failures in last 24h allowed before downgrade")


class PolicyKindBudget(BaseModel):
    """Failure budget for one excludable failure kind (US-9.3).

    ``max_failures`` is the count of failures of this kind the gate will
    excuse from the NO_GO trigger. ``downgrade_to`` is deliberately a
    single-value Literal: a NO_GO may be softened at most to CONDITIONAL_GO —
    never to GO — so the schema itself makes the hard rule unrepresentable.

    ``min_confidence_to_excuse`` (AI-4, optional): when set, a failure only
    counts toward this kind's excusable budget if its per-failure kind
    confidence (the evidence-checklist confidence, falling back to the
    classifier confidence) is at or above the floor. Below-floor and
    unknown-confidence failures count as product — conservative. ``None``
    (the default) applies no floor: verdicts are byte-identical to the
    pre-AI-4 US-9.3 behaviour (pinned by tests/test_kind_gate_policy.py).
    """
    max_failures: int = Field(default=0, ge=0)
    downgrade_to: Literal["CONDITIONAL_GO"] = "CONDITIONAL_GO"
    min_confidence_to_excuse: Optional[int] = Field(default=None, ge=0, le=100)


class PolicyKindRules(BaseModel):
    """Opt-in failure-kind weighting for the release gate (US-9.3).

    STRICTLY OPT-IN: ``enabled`` defaults to False and the evaluator treats
    a disabled/absent block as byte-identical to today's behaviour (pinned by
    ``tests/test_kind_gate_policy.py``). Kinds come from the derived triad in
    ``app/services/failure_kind.py`` (AI-classified — never ground truth).

    Only ``infrastructure`` and ``test_code`` may carry budgets. ``product``
    failures always count and ``unknown`` failures are conservatively counted
    as product — neither is representable here on purpose.
    """
    enabled: bool = False
    infrastructure: Optional[PolicyKindBudget] = None
    test_code: Optional[PolicyKindBudget] = None


class PolicyDocument(BaseModel):
    """The full policy rule document stored as JSON in release_gate_policies.rules."""
    schema_version: int = 1
    thresholds: PolicyThresholds = Field(default_factory=PolicyThresholds)
    dimension_weights: PolicyDimensionWeights = Field(default_factory=PolicyDimensionWeights)
    rules: List[PolicyRule] = Field(default_factory=list)
    # Tier-1 pass-rate gating — feature added 2026-05-14. Existing rows
    # default these on read via Pydantic, so no migration is required.
    pass_rate_bands: PolicyPassRateBands = Field(default_factory=PolicyPassRateBands)
    hard_caps: PolicyHardCaps = Field(default_factory=PolicyHardCaps)
    # Kind-aware gating (US-9.3) — added 2026-07-10. Defaults to disabled on
    # read for existing rows via Pydantic, so no migration is required.
    kind_rules: PolicyKindRules = Field(default_factory=PolicyKindRules)


class ReleaseGatePolicyCreate(BaseModel):
    """Create a new draft policy."""
    project_id: Optional[uuid.UUID] = None  # None = system default
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    rules: PolicyDocument = Field(default_factory=PolicyDocument)


class ReleaseGatePolicyUpdate(BaseModel):
    """Update a draft policy (fails if already published)."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    rules: Optional[PolicyDocument] = None


class ReleaseGatePolicyResponse(BaseModel):
    """Policy summary response."""
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    version: int
    name: str
    description: Optional[str] = None
    rules: dict
    is_active: bool
    is_draft: bool
    created_by: uuid.UUID
    activated_by: Optional[uuid.UUID] = None
    activated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class RuleEvaluationResponse(BaseModel):
    """Result of evaluating a single policy rule."""
    rule_id: str
    rule_name: str
    rule_type: str
    passed: bool
    action: str  # BLOCK | WARN | INFO
    message: str
    actual_value: Optional[float] = None
    threshold_value: Optional[float] = None


class PolicySimulateRequest(BaseModel):
    """Simulate a draft policy against a past run."""
    run_id: uuid.UUID
    policy_document: PolicyDocument


class PolicySimulateResponse(BaseModel):
    """Side-by-side comparison: original vs simulated policy result."""
    original_recommendation: str
    simulated_recommendation: str
    original_composite: float
    simulated_composite: float
    rule_evaluations: List[RuleEvaluationResponse] = []
    diff_summary: str


# ── Service Ownership Schemas (ENT-04) ───────────────────────────────────────


class OwnershipRuleCreate(BaseModel):
    """Create a new ownership rule."""
    match_type: str = Field(..., pattern="^(suite_name|component|package|path|label)$")
    match_pattern: str = Field(..., min_length=1, max_length=500)
    service_name: str = Field(..., min_length=1, max_length=255)
    team_name: str = Field(..., min_length=1, max_length=255)
    team_contact: Optional[str] = Field(None, max_length=500)
    priority: int = Field(default=0, ge=0, le=1000)


class OwnershipRuleUpdate(BaseModel):
    """Partial update for an ownership rule."""
    match_type: Optional[str] = Field(None, pattern="^(suite_name|component|package|path|label)$")
    match_pattern: Optional[str] = Field(None, min_length=1, max_length=500)
    service_name: Optional[str] = Field(None, min_length=1, max_length=255)
    team_name: Optional[str] = Field(None, min_length=1, max_length=255)
    team_contact: Optional[str] = Field(None, max_length=500)
    priority: Optional[int] = Field(None, ge=0, le=1000)
    is_active: Optional[bool] = None


class OwnershipRuleResponse(BaseModel):
    """Ownership rule response."""
    id: uuid.UUID
    project_id: uuid.UUID
    match_type: str
    match_pattern: str
    service_name: str
    team_name: str
    team_contact: Optional[str] = None
    priority: int
    is_active: bool
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class OwnershipBulkImportItem(BaseModel):
    """Single item for bulk import."""
    match_type: str = Field(..., pattern="^(suite_name|component|package|path|label)$")
    match_pattern: str = Field(..., min_length=1, max_length=500)
    service_name: str = Field(..., min_length=1, max_length=255)
    team_name: str = Field(..., min_length=1, max_length=255)
    team_contact: Optional[str] = None
    priority: int = 0


class OwnershipBulkImportRequest(BaseModel):
    """Bulk import of ownership rules."""
    rules: List[OwnershipBulkImportItem] = Field(..., min_length=1, max_length=500)
    replace_existing: bool = False


class CodeownersImportRequest(BaseModel):
    """Import a CODEOWNERS file into ``path`` ownership rules (US-8.3).

    ``source == "text"`` carries the raw file body in ``text`` (air-gapped /
    paste-upload). ``source == "github"`` fetches it over the configured
    GitHub connector and ``text`` is ignored.
    """
    source: str = Field(..., pattern="^(github|text)$")
    text: Optional[str] = Field(None, max_length=1_000_000)


class CodeownersCoverage(BaseModel):
    """Coverage of recent failing-test paths by ``path`` ownership rules."""
    path_rules: int = 0
    codeowners_rules: int = 0
    sampled: int = 0
    located: int = 0
    matched: int = 0
    coverage_pct: float = 0.0
    lookback_days: int = 30


class CodeownersImportResponse(BaseModel):
    """Summary of a CODEOWNERS import."""
    imported: int
    rules_created: int
    rules_replaced: int
    source: str
    coverage: CodeownersCoverage


class OwnershipResolution(BaseModel):
    """Result of resolving ownership for a test/cluster."""
    service_name: Optional[str] = None
    team_name: Optional[str] = None
    team_contact: Optional[str] = None
    confidence: str = "none"  # high | medium | low | none
    matched_rule_id: Optional[str] = None
    match_source: Optional[str] = None  # suite_name | component | package | path | label | component_owner_map | fallback
    fallback_reason: Optional[str] = None


class TeamChannelUpsert(BaseModel):
    """Create/replace the notification channel for one ownership team
    (PMF US-7.3). The team is keyed by name in the URL path."""
    channel_type: str = Field(..., pattern="^(email|slack|teams)$")
    target: str = Field(..., min_length=1, max_length=2000)  # webhook URL or email address
    is_active: bool = True


class TeamChannelResponse(BaseModel):
    """Team → notification channel mapping (PMF US-7.3)."""
    id: uuid.UUID
    project_id: uuid.UUID
    team_name: str
    channel_type: str
    target: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ── Saved Views & Digest Schemas (ENT-05) ────────────────────────────────────


class SavedViewCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    page: Optional[str] = Field(None, max_length=50)  # dashboard | trends | coverage | defects
    filters: dict = Field(default_factory=dict)
    is_shared: bool = False
    is_default: bool = False


class SavedViewUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    page: Optional[str] = Field(None, max_length=50)
    filters: Optional[dict] = None
    is_shared: Optional[bool] = None
    is_default: Optional[bool] = None


class SavedViewResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    name: str
    description: Optional[str] = None
    page: Optional[str] = None
    filters: dict
    is_shared: bool
    is_default: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class DigestSubscriptionCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    saved_view_id: Optional[uuid.UUID] = None
    name: str = Field(..., min_length=2, max_length=255)
    # Must stay in sync with the ``DigestSchedule`` ORM enum — pinned by
    # tests/regression/test_digest_schedule_vocab.py, which derives its cases
    # from the enum so a new member fails until it is wired through here.
    schedule: str = Field(
        default="WEEKLY",
        pattern="^(DAILY|WEEKLY|WEEKLY_RETRO|PER_RUN|PER_RELEASE|PER_SUITE)$",
    )
    channel: str = Field(default="email", pattern="^(email|slack|teams)$")
    scope_type: Optional[str] = Field(default="project", pattern="^(project|release|suite|global)$")
    scope_value: Optional[str] = Field(None, max_length=255)
    trigger_filter: Optional[str] = Field(default="all", pattern="^(all|failed_only|degraded_only)$")
    # US-7.4: zero-change windows send a one-liner (True, default) or skip
    # delivery entirely (False).
    send_when_unchanged: bool = True
    # US-7.5: attach the self-contained HTML analysis report to email
    # digests (1d for DAILY, 7d for WEEKLY). Default off.
    report_attachment: bool = False


class DigestSubscriptionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    # Same vocabulary as ``DigestSubscriptionCreate`` — pinned for BOTH schemas
    # by tests/regression/test_digest_schedule_vocab.py. This pattern was left
    # at DAILY|WEEKLY when the other four members were added, so a subscription
    # could be *created* as WEEKLY_RETRO / PER_RUN / PER_RELEASE / PER_SUITE but
    # never *changed* to one — PATCH 422'd. Worse, a WEEKLY_RETRO subscription
    # PATCHed to DAILY could not be put back, since the only route to those
    # values was create; the fix was delete-and-recreate.
    schedule: Optional[str] = Field(
        None,
        pattern="^(DAILY|WEEKLY|WEEKLY_RETRO|PER_RUN|PER_RELEASE|PER_SUITE)$",
    )
    channel: Optional[str] = Field(None, pattern="^(email|slack|teams)$")
    saved_view_id: Optional[uuid.UUID] = None
    # Settable at create (and stored) but previously absent here, so a PATCH
    # carrying them returned 200 with the old values silently retained — a
    # subscription's scope and trigger were immutable after creation. The UI's
    # own `updateSubscription` is typed to send all three.
    #
    # ``trigger_filter`` is the one that bites: the delivery task gates on it,
    # so a user could not switch an existing subscription between "everything"
    # and "failures only" at all.
    #
    # ``project_id`` is deliberately NOT updatable. It is authorization-checked
    # once at create, and the delivery task reads it straight off the row
    # without re-checking membership — allowing it here would let a caller
    # re-point an existing subscription at another tenant's project.
    scope_type: Optional[str] = Field(None, pattern="^(project|release|suite|global)$")
    scope_value: Optional[str] = Field(None, max_length=255)
    trigger_filter: Optional[str] = Field(None, pattern="^(all|failed_only|degraded_only)$")
    is_active: Optional[bool] = None
    is_paused: Optional[bool] = None
    send_when_unchanged: Optional[bool] = None
    report_attachment: Optional[bool] = None


class DigestSubscriptionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    saved_view_id: Optional[uuid.UUID] = None
    name: str
    schedule: str
    channel: str
    is_active: bool
    is_paused: bool
    scope_type: Optional[str] = "project"
    scope_value: Optional[str] = None
    trigger_filter: Optional[str] = "all"
    send_when_unchanged: bool = True
    report_attachment: bool = False
    last_delivered_at: Optional[datetime] = None
    next_delivery_at: Optional[datetime] = None
    delivery_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class DigestContentResponse(BaseModel):
    """Actionable digest content for a project or saved view scope."""
    project_name: Optional[str] = None
    period: str  # "daily" | "weekly"
    generated_at: str
    total_runs: int = 0
    avg_pass_rate: Optional[float] = None
    pass_rate_trend: Optional[float] = None  # delta from previous period
    new_regressions: int = 0
    top_blockers: List[str] = []
    top_clusters: List[dict] = []  # [{label, size, criticality}]
    flaky_test_count: int = 0
    release_decisions: List[dict] = []  # [{run_id, recommendation, risk_score}]
    action_items: List[str] = []
    # ── Delta digest fields (PMF US-7.4) — present when the digest was
    # generated against a last-sent watermark (``since``). ``delta`` shape:
    # {new_failures, new_failures_top, newly_flaky, recovered,
    #  quarantine_debt: {active, stale, ready_to_promote},
    #  gate_change: {from, to} | None}
    changes_since: Optional[str] = None
    delta: Optional[dict] = None
    is_zero_change: Optional[bool] = None
    latest_run_total_tests: Optional[int] = None


# ── AI Evaluation Schemas (OPS-02) ───────────────────────────────────────────


class AIEvalDatasetCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    task_type: str = Field(..., pattern="^(classification|root_cause|release_decision|duplicate_detection)$")
    items: List[dict] = Field(default_factory=list)


class AIEvalDatasetResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    task_type: str
    item_count: int
    is_active: bool
    created_by: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class AIEvalRunResponse(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    model_name: str
    model_version_id: Optional[uuid.UUID] = None
    task_type: str
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None
    accuracy: Optional[float] = None
    agreement_rate: Optional[float] = None
    total_items: int = 0
    correct_items: int = 0
    fallback_used: bool = False
    evaluated_at: datetime
    duration_ms: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class AIEvalGateRunResponse(BaseModel):
    id: uuid.UUID
    change_id: str
    status: str
    manifest_checksum_sha256: str
    manifest: Dict[str, Any]
    gate_results: List[Dict[str, Any]]
    blocking_gates: List[Dict[str, Any]]
    version_changes: List[Dict[str, Any]]
    evaluated_by: Optional[uuid.UUID] = None
    evaluated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AIQualityDashboardResponse(BaseModel):
    """Combined dashboard data for AI quality metrics."""
    agreement: Optional[dict] = None  # agreement_rate, total_feedback, ...
    drift: Optional[dict] = None  # current vs previous window
    recent_eval_runs: List[AIEvalRunResponse] = []
    model_versions: List[dict] = []
    feedback_summary: Optional[dict] = None
    # AI-F1: human-label coverage of the ML training pool + last-trained
    # provenance composition (human_direct / human_indirect / llm_pseudo)
    label_health: Optional[dict] = None


# ── Agent Memory Schemas (P3 — Unified Memory & Retrieval) ─────────────────


class AgentMemoryEntryResponse(BaseModel):
    """Single memory entry returned to the client."""
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    pipeline_run_id: Optional[uuid.UUID] = None
    entity_type: str
    entity_id: str
    error_signature: Optional[str] = None
    failure_category: Optional[str] = None
    root_cause_summary: Optional[str] = None
    payload: Optional[dict] = None
    confidence: Optional[int] = None
    resolution: Optional[str] = None
    source_type: str = "pipeline_agent"
    trust_level: str = "derived"
    lifecycle_status: str = "active"
    source_snapshot_id: Optional[str] = None
    source_hash: Optional[str] = None
    expires_at: Optional[datetime] = None
    superseded_by_id: Optional[uuid.UUID] = None
    superseded_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AgentMemoryListResponse(BaseModel):
    """Paginated memory entry list."""
    total: int
    items: List[AgentMemoryEntryResponse]
    page: int
    size: int


class SimilarMemoryResponse(BaseModel):
    """A memory entry with a similarity score from vector recall."""
    memory: AgentMemoryEntryResponse
    similarity: float = Field(ge=0.0, le=1.0)
    retrieval_audit: Optional[Dict[str, Any]] = None
    memory_reference: Optional[MemoryReference] = None


class SimilarMemoryRecallRequest(BaseModel):
    """Request body for semantic similarity recall."""
    error_signature: str = Field(..., min_length=5, max_length=5000)
    entity_type: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=50)


class SimilarMemoryRecallResponse(BaseModel):
    """Response with ranked similar memories."""
    query_signature: str
    results: List[SimilarMemoryResponse]
    total_found: int
    retrieval_audit: Optional[Dict[str, Any]] = None


class MemoryTimelineResponse(BaseModel):
    """Timeline of memory entries for a run, grouped by entity type."""
    run_id: uuid.UUID
    project_id: uuid.UUID
    entries_by_type: dict  # {entity_type: [AgentMemoryEntryResponse]}
    total_entries: int


# ── Knowledge Source Schemas (RAG-1 / RAG-2 / RAG-3) ─────────────────────────


class KnowledgeSourceCreate(BaseModel):
    source_type: str = Field(..., max_length=30)
    title: str = Field(..., min_length=1, max_length=500)
    canonical_url: str = Field(..., min_length=1, max_length=2000)
    external_id: Optional[str] = Field(None, max_length=500)
    classification: str = Field("internal", max_length=20)


class KnowledgeSourceUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    classification: Optional[str] = Field(None, max_length=20)
    is_archived: Optional[bool] = None


class KnowledgeSourceResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    source_type: str
    title: str
    canonical_url: str
    external_id: Optional[str] = None
    owner_id: Optional[uuid.UUID] = None
    sync_status: str
    last_synced_at: Optional[datetime] = None
    sync_error: Optional[str] = None
    content_hash: Optional[str] = None
    classification: str
    is_archived: bool
    storage_path: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class KnowledgeSourceListResponse(BaseModel):
    items: List[KnowledgeSourceResponse]
    total: int
    page: int
    page_size: int


class KnowledgeSourceSyncResponse(BaseModel):
    source_id: uuid.UUID
    task_id: str
    sync_status: str


class ConnectorTestResult(BaseModel):
    success: bool
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    detail: Optional[str] = None


class ConnectorConfigTestRequest(BaseModel):
    source_type: str
    params: dict = Field(default_factory=dict)


class KnowledgeDomainAllowlistUpdate(BaseModel):
    domains: List[str] = Field(
        ...,
        description="FQDN list, e.g. ['confluence.corp.com', 'jira.corp.com']",
    )

    @field_validator("domains")
    @classmethod
    def _validate_domains(cls, value: List[str]) -> List[str]:
        """S4-audit S8: the allowlist is the domain gate that complements the
        url_connector SSRF guard, so each entry must be a real FQDN. Reject
        wildcards / schemes / ports / paths / IP addresses — none of which the
        ``hostname == d or hostname.endswith('.'+d)`` matcher honours anyway, so
        rejecting them is behaviour-preserving. An empty list is allowed (it
        clears the allowlist → permissive, the existing semantics)."""
        import ipaddress as _ip
        import re as _re

        _fqdn = _re.compile(
            r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
        )
        cleaned: List[str] = []
        for raw in value:
            d = raw.strip().lower()
            if not d:
                continue  # blank entries are dropped (clear-allowlist preserved)
            if "://" in d or any(c in d for c in ("*", "/", ":", " ", "\t")):
                raise ValueError(
                    f"Invalid domain '{raw}': wildcards, schemes, ports, and "
                    "paths are not allowed — use a bare FQDN like 'jira.corp.com'"
                )
            if "." not in d:
                raise ValueError(
                    f"Invalid domain '{raw}': must be a fully-qualified domain"
                )
            try:
                _ip.ip_address(d)
            except ValueError:
                pass  # not an IP literal — good
            else:
                raise ValueError(
                    f"Invalid domain '{raw}': IP addresses are not allowed, "
                    "use a hostname"
                )
            if not _fqdn.match(d):
                raise ValueError(f"Invalid domain '{raw}': not a valid domain name")
            cleaned.append(d)
        return cleaned


# ── Knowledge Sync Events (RAG-4) ─────────────────────────────────────────────


class KnowledgeSyncEventResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    project_id: uuid.UUID
    trigger: str
    status: str
    content_hash: Optional[str] = None
    previous_hash: Optional[str] = None
    content_changed: bool
    chunk_count: Optional[int] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Knowledge Chunks (RAG-5) ──────────────────────────────────────────────────


class KnowledgeChunkResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    project_id: uuid.UUID
    vector_id: str
    section_heading: Optional[str] = None
    requirement_id: Optional[str] = None
    chunk_index: int
    chunk_text_preview: Optional[str] = None
    token_count: Optional[int] = None
    sync_version: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Source Freshness (RAG-6) ──────────────────────────────────────────────────


class KnowledgeSourceFreshnessResponse(BaseModel):
    source_id: uuid.UUID
    is_stale: bool
    stale_since: Optional[datetime] = None
    hours_since_sync: Optional[float] = None
    staleness_threshold_hours: int
    content_changed_on_last_sync: bool
    active_chunk_count: int
    last_sync_status: Optional[str] = None
    sync_event_count: int


# ── RAG Retrieval (RAG-7) ─────────────────────────────────────────────────────


class RagRetrieveRequest(BaseModel):
    project_id: uuid.UUID
    query_text: str = Field(..., min_length=1, max_length=5000)
    source_ids: Optional[List[uuid.UUID]] = None
    top_k: int = Field(10, ge=1, le=50)
    min_score: float = Field(0.0, ge=0.0, le=1.0)


class RetrievedChunkSchema(BaseModel):
    vector_id: str
    source_id: uuid.UUID
    source_title: str
    section_heading: Optional[str] = None
    chunk_text: str
    relevance_score: float
    requirement_id: Optional[str] = None


class RagRetrieveResponse(BaseModel):
    chunks: List[RetrievedChunkSchema]
    total: int


# ── RAG Generation (RAG-8) ────────────────────────────────────────────────────


class RagGenerateRequest(BaseModel):
    project_id: uuid.UUID
    prompt_text: str = Field("", max_length=10000)
    source_ids: List[uuid.UUID] = Field(default_factory=list)
    persist: bool = False
    generation_config: Optional[dict] = None


class CitationSchema(BaseModel):
    case_index: int
    vector_id: str
    source_id: uuid.UUID
    source_title: str
    section_heading: Optional[str] = None
    chunk_text_preview: Optional[str] = None
    relevance_score: Optional[float] = None


class RagGenerateResponse(BaseModel):
    batch_id: uuid.UUID
    generation_mode: str
    test_cases: List[dict]
    citations: List[CitationSchema]
    coverage_summary: Optional[str] = None
    gaps_noted: List[str] = Field(default_factory=list)
    created_ids: List[str] = Field(default_factory=list)


# ── RAG Coverage (RAG-9) ──────────────────────────────────────────────────────


class RequirementCoverageSchema(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    project_id: uuid.UUID
    requirement_id: str
    requirement_text: Optional[str] = None
    coverage_status: str
    covered_by_case_ids: Optional[List[str]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── RAG Batch Review (RAG-10) ─────────────────────────────────────────────────


class GenerationBatchResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    created_by_id: Optional[uuid.UUID] = None
    generation_mode: str
    cases_generated: int
    cases_accepted: int
    cases_rejected: int
    coverage_score: Optional[int] = None
    status: str
    llm_model_used: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class BatchAcceptRequest(BaseModel):
    case_ids: List[uuid.UUID]
    edits: Optional[dict] = None  # {str(case_id): {field: value}}


class RejectCaseRequest(BaseModel):
    reason: Optional[str] = None


class AcceptCaseRequest(BaseModel):
    edits: Optional[dict] = None


# ── RAG Staleness (RAG-12) ────────────────────────────────────────────────────


class StaleCaseDismissRequest(BaseModel):
    pass  # empty body — just the POST acknowledges


# ── RAG Status (RAG-14) ───────────────────────────────────────────────────────


class RagStatusResponse(BaseModel):
    enabled: bool
    feature_flag: str = "KNOWLEDGE_RAG_ENABLED"
    total_sources: int = 0
    total_batches: int = 0
    total_chunks: int = 0


# ── Feature Flags (Tier 0A) ──────────────────────────────────────────────────


class FeatureFlagCreate(BaseModel):
    key: str = Field(..., min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    description: Optional[str] = Field(None, max_length=2000)
    enabled_global: bool = False
    enabled_projects: Optional[List[uuid.UUID]] = None
    enabled_roles: Optional[List[str]] = None
    rollout_percent: int = Field(100, ge=0, le=100)


class FeatureFlagUpdate(BaseModel):
    """All fields optional — partial update. None means keep existing."""
    description: Optional[str] = Field(None, max_length=2000)
    enabled_global: Optional[bool] = None
    enabled_projects: Optional[List[uuid.UUID]] = None
    enabled_roles: Optional[List[str]] = None
    rollout_percent: Optional[int] = Field(None, ge=0, le=100)


class FeatureFlagResponse(BaseModel):
    id: uuid.UUID
    key: str
    description: Optional[str] = None
    enabled_global: bool
    enabled_projects: Optional[List[uuid.UUID]] = None
    enabled_roles: Optional[List[str]] = None
    rollout_percent: int
    created_at: datetime
    updated_at: datetime
    updated_by_user_id: Optional[uuid.UUID] = None
    model_config = ConfigDict(from_attributes=True)


# ── Decision Trail (Tier 0B) ─────────────────────────────────────────────────


class DecisionLogEntry(BaseModel):
    """A single structured decision made by an agent — mirror of
    ``BaseAgent.log_decision`` output stored on AgentStageResult.decision_log."""
    at: str                              # ISO timestamp
    decision_point: str                  # e.g. "route_analysis_mode", "triage_skip"
    chosen: str                          # option taken
    rationale: str                       # why
    alternatives: Optional[List[str]] = None
    test_case_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class StageDecisionSummary(BaseModel):
    stage_name: str
    status: str                          # pending|running|completed|failed|skipped
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    analysis_mode: Optional[str] = None  # llm|ml|rules|auto|mixed
    fallback_used: Optional[bool] = None
    fallback_reason: Optional[str] = None
    route_rationale: Optional[str] = None
    error_category: Optional[str] = None
    skipped_reason: Optional[str] = None
    execution_path: Optional[str] = None
    confidence_score: Optional[int] = None
    evidence_count: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    decision_log: List[DecisionLogEntry] = Field(default_factory=list)


class PerTestRouting(BaseModel):
    test_case_id: uuid.UUID
    test_name: Optional[str] = None
    analysis_mode: Optional[str] = None
    mode_requested: Optional[str] = None
    fallback_from: Optional[str] = None
    fallback_reason: Optional[str] = None
    confidence_adjustments: Optional[List[Dict[str, Any]]] = None
    retry_count: Optional[int] = None
    duration_seconds: Optional[float] = None
    # US-15.2: the confidence-gate evaluation recorded for this analysis.
    # None for rows analysed before the gate existed.
    threshold_check: Optional[ThresholdCheck] = None


class WorkflowDecisionEvent(BaseModel):
    at: str
    decision_point: str
    chosen: str
    rationale: str
    alternatives: Optional[List[str]] = None
    context: Optional[Dict[str, Any]] = None


class DecisionTrailResponse(BaseModel):
    """Full decision trail for a pipeline run — the user-facing audit surface."""
    run_id: uuid.UUID
    pipeline_run_id: Optional[uuid.UUID] = None
    workflow_type: Optional[str] = None
    pipeline_status: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    # Stage-level decisions, in execution order.
    stages: List[StageDecisionSummary] = Field(default_factory=list)
    # Workflow router decisions (fast-path skips, specialist-stage selection).
    workflow_events: List[WorkflowDecisionEvent] = Field(default_factory=list)
    # Per-test routing rollup — one entry per analysed test.
    per_test: List[PerTestRouting] = Field(default_factory=list)
    # Aggregate: how many tests used each engine and how many fell back.
    mode_distribution: Dict[str, int] = Field(default_factory=dict)
    fallback_count: int = 0
    # US-15.2: how many analyses in this run failed the confidence gate.
    # Counts only tests that recorded a check — legacy rows count as neither.
    below_threshold_count: int = 0


# ── LLM Cost Budget (Tier 1 item 2) ──────────────────────────────────────────


class LlmQuotaWrite(BaseModel):
    """Admin-editable billing config for a project."""
    enabled: bool = True
    period_type: str = Field("MONTHLY", pattern=r"^(MONTHLY)$")
    included_usd: float = Field(0.0, ge=0)
    overage_rate_usd: float = Field(1.0, ge=0)
    hard_cap_usd: float = Field(0.0, ge=0)
    soft_warn_threshold_pct: int = Field(100, ge=1, le=100)
    at_cap_action: str = Field(
        "AUTO_DOWNGRADE_TO_ML",
        pattern=r"^(SOFT_WARN|AUTO_DOWNGRADE_TO_ML|AUTO_DOWNGRADE_TO_RULES|HARD_BLOCK)$",
    )


class LlmQuotaRead(LlmQuotaWrite):
    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    updated_by_user_id: Optional[uuid.UUID] = None
    model_config = ConfigDict(from_attributes=True)


class LlmUsageRead(BaseModel):
    project_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_llm_calls: int
    cap_hits: int
    # Derived fields — populated by the service, not persisted:
    included_usd: Optional[float] = None
    hard_cap_usd: Optional[float] = None
    utilization_pct: Optional[float] = None  # total_cost_usd / hard_cap_usd * 100
    status: Optional[str] = None  # OK | SOFT_WARN | CAPPED
    model_config = ConfigDict(from_attributes=True)


class LlmUsageHistoryEntry(BaseModel):
    period_start: datetime
    period_end: datetime
    total_cost_usd: float
    total_llm_calls: int
    cap_hits: int


class BillingOverviewProject(BaseModel):
    project_id: uuid.UUID
    project_name: str
    current_cost_usd: float
    hard_cap_usd: Optional[float] = None
    utilization_pct: Optional[float] = None
    status: str  # OK | SOFT_WARN | CAPPED | UNLIMITED
    cap_hits: int


class BillingOverviewResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    total_cost_usd: float
    total_llm_calls: int
    projects: List[BillingOverviewProject]
    # Costs are derived from a checked-in rate table, not from provider
    # invoices. Surfacing when it was last verified keeps a stale table
    # visible instead of quietly believed — these are estimates, and the
    # UI should say so.
    price_table_updated: Optional[str] = None
    pricing_is_estimated: bool = True


# ── Flaky Auto-Quarantine (Tier 1 item 3) ───────────────────────────────────


class FlakyQuarantineRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    test_fingerprint: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    status: str
    detection_method: str
    flip_rate: Optional[float] = None
    flip_window_size: Optional[int] = None
    pass_count: Optional[int] = None
    fail_count: Optional[int] = None
    detected_at: datetime
    last_failure_at: Optional[datetime] = None
    proposed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by_user_id: Optional[uuid.UUID] = None
    rejected_at: Optional[datetime] = None
    rejected_by_user_id: Optional[uuid.UUID] = None
    quarantine_start: Optional[datetime] = None
    quarantine_expires_at: Optional[datetime] = None
    quarantine_duration_days: int
    recheck_at: Optional[datetime] = None
    rationale: Optional[Dict[str, Any]] = None
    reviewer_notes: Optional[str] = None
    # ── Lifecycle: owner + SLA + auto-promotion (PMF US-5.4 / US-5.5) ──────
    owner_user_id: Optional[uuid.UUID] = None
    # Display name resolved by the router (batched lookup) — not an ORM column.
    owner_name: Optional[str] = None
    defect_id: Optional[uuid.UUID] = None
    # Jira link + mirrored status of the linked defect (US-6.1/US-6.2) —
    # resolved by the router via a batched Defect lookup, not ORM columns.
    defect_jira_key: Optional[str] = None
    defect_jira_url: Optional[str] = None
    defect_external_status: Optional[str] = None
    defect_external_status_conflict: bool = False
    sla_days: Optional[int] = None
    stale_at: Optional[datetime] = None
    # Derived from the ORM ``stale`` property: active quarantine past its SLA.
    stale: bool = False
    consecutive_passes: int = 0
    ready_to_promote: bool = False
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class QuarantineDecisionRequest(BaseModel):
    """Body for approve / reject / release endpoints."""
    notes: Optional[str] = Field(None, max_length=2000)
    quarantine_duration_days: Optional[int] = Field(None, ge=1, le=90)


class QuarantineProposeRequest(BaseModel):
    """Manual proposal — rarely used. Detection agent is the primary path."""
    project_id: uuid.UUID
    test_fingerprint: str = Field(..., min_length=1, max_length=64)
    test_name: Optional[str] = Field(None, max_length=500)
    suite_name: Optional[str] = Field(None, max_length=500)
    detection_method: str = Field("manual", max_length=50)
    flip_rate: Optional[float] = Field(None, ge=0, le=1)
    flip_window_size: Optional[int] = Field(None, ge=1)
    pass_count: Optional[int] = Field(None, ge=0)
    fail_count: Optional[int] = Field(None, ge=0)
    rationale: Optional[Dict[str, Any]] = None
    quarantine_duration_days: int = Field(14, ge=1, le=90)


class QuarantineManifestEntry(BaseModel):
    """One currently-quarantined test in the CI manifest (US-5.1).

    Identity tuple for CI-side matching: ``fingerprint`` (primary key —
    ``sha256(class_name::test_name)[:16]``, same formula as ingestion's
    ``make_test_fingerprint``) plus the human-readable ``test_name`` /
    ``suite_name`` / ``class_name`` for name-based fallback matching.
    """
    fingerprint: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    class_name: Optional[str] = None
    status: str
    quarantined_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    reason: Optional[str] = None
    # Lifecycle surfacing (PMF US-5.4 / US-5.5): SLA exceeded / pass-streak
    # threshold reached with auto_promote off.
    stale: bool = False
    ready_to_promote: bool = False


class QuarantineManifestResponse(BaseModel):
    """Versioned quarantine manifest consumed by CI (``testlookup ci-verdict``).

    Contains ONLY currently-effective quarantines (QUARANTINED /
    RECHECK_SCHEDULED / RE_QUARANTINED) — released, rejected, and expired
    rows never appear, nor do un-reviewed proposals.
    """
    version: int = 1
    project_id: uuid.UUID
    generated_at: datetime
    etag: str
    count: int
    entries: List[QuarantineManifestEntry]


class QuarantineLifecyclePolicyUpdate(BaseModel):
    """Per-project quarantine lifecycle policy (PMF US-5.4 / US-5.5 / US-5.6)."""
    sla_days: int = Field(14, ge=1, le=365)
    auto_create_defect: bool = False
    auto_promote: bool = False
    promote_after_passes: int = Field(20, ge=1, le=1000)
    detection_flip_rate_threshold: float = Field(0.20, ge=0.0, le=1.0)
    detection_min_runs: int = Field(10, ge=1, le=1000)


class QuarantineLifecyclePolicyResponse(BaseModel):
    project_id: uuid.UUID
    sla_days: int
    auto_create_defect: bool
    auto_promote: bool
    promote_after_passes: int
    detection_flip_rate_threshold: float
    detection_min_runs: int
    # True when the project has no explicit row yet and code defaults apply.
    is_default: bool = False

    model_config = ConfigDict(from_attributes=True)


class QuarantineStatsResponse(BaseModel):
    """Counts per status for the /quarantine page header tiles."""
    proposed: int = 0
    approved: int = 0
    quarantined: int = 0
    recheck_scheduled: int = 0
    released: int = 0
    rejected: int = 0
    expired: int = 0
    re_quarantined: int = 0
    detected: int = 0
    total_live: int = 0


# ── Release Compliance Pack (Tier 1 item 4) ─────────────────────────────────


class CompliancePackGenerateRequest(BaseModel):
    """Body for POST /api/v1/releases/{id}/compliance-pack."""
    notes: Optional[str] = Field(None, max_length=2000)
    retention_days: Optional[int] = Field(
        None,
        ge=1,
        le=3650,
        description="Override retention window (default 2557 = ~7 years)",
    )


class CompliancePackRead(BaseModel):
    id: uuid.UUID
    release_id: Optional[uuid.UUID] = None
    project_id: uuid.UUID
    test_run_id: Optional[uuid.UUID] = None
    minio_key: str
    manifest_sha256: str
    file_count: int
    bytes: int
    retention_expires_at: datetime
    generated_at: datetime
    generated_by_user_id: Optional[uuid.UUID] = None
    metadata_snapshot: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class CompliancePackDownloadResponse(BaseModel):
    """Response for the download endpoint — either a presigned URL or
    a streaming hint."""
    pack_id: uuid.UUID
    download_url: Optional[str] = None
    expires_in_seconds: Optional[int] = None
    bytes: int
    manifest_sha256: str


# ── GitHub Integration (Tier 1 item 5) ──────────────────────────────────────


class GitHubIntegrationWrite(BaseModel):
    enabled: bool = True
    repo_owner: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")
    repo_name: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")
    api_base_url: str = Field("https://api.github.com", max_length=500)
    # When provided, the PAT is upserted into secret_service and the
    # ``has_pat`` flag is flipped on. When null, the existing secret (if
    # any) is left alone — send an empty string to clear it.
    pat: Optional[str] = Field(None, max_length=200)
    # PMF US-4.1 — sticky PR summary comment mode. ``failures_only``
    # still updates an existing marker comment on a green run so a PR
    # that went red→green shows green.
    pr_comment_mode: Literal["off", "failures_only", "always"] = "failures_only"


class GitHubIntegrationRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    enabled: bool
    repo_owner: str
    repo_name: str
    api_base_url: str
    has_pat: bool
    pr_comment_mode: str = "failures_only"
    last_posted_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class GitHubConnectionTestResponse(BaseModel):
    success: bool
    status_code: Optional[int] = None
    message: str
    repo_html_url: Optional[str] = None


# ── GitLab Integration (PMF Epic 3 US-3.1/3.2/3.3) ──────────────────────────
#
# Contract note (frontend built in parallel — implement verbatim): the PAT is
# NEVER returned; ``has_token`` is the only token signal on GET. PUT accepts an
# optional write-only ``token`` that sets/rotates the PAT via secret_service.


class ValueMetricAssumptionsWrite(BaseModel):
    """PUT body for ``/projects/{id}/value-metrics/assumptions`` (US-12.1).

    All fields optional — omitted fields keep their current (or default)
    value. Bounds: 0 < x <= 480 minutes; anything outside 422s.
    """
    triage_minutes_per_failure: Optional[float] = Field(None, gt=0, le=480)
    blocked_run_wait_minutes: Optional[float] = Field(None, gt=0, le=480)
    defect_filing_minutes: Optional[float] = Field(None, gt=0, le=480)


class ValueMetricAssumptionsRead(BaseModel):
    """GET/PUT response — the EFFECTIVE assumptions plus their source
    (``default`` = no row, ``custom`` = project row exists)."""
    triage_minutes_per_failure: float
    blocked_run_wait_minutes: float
    defect_filing_minutes: float
    source: str = "default"


class GitLabConfigWrite(BaseModel):
    """PUT body for ``/projects/{id}/integrations/gitlab``.

    ``token`` is the optional write-only field: ``None`` leaves the stored
    secret alone, ``""`` clears it, any value sets/rotates it. The PAT is
    NEVER present on the read side (see ``GitLabConfigRead``).
    """
    enabled: bool = False
    # Scheme is mandatory — a schemeless host has no urlparse netloc, which
    # would silently no-op the SSRF egress guard downstream.
    base_url: str = Field("https://gitlab.com", max_length=500, pattern=r"^https?://")
    project_path: str = Field("", max_length=500)
    mr_comment_mode: Literal["off", "failures_only", "always"] = "failures_only"
    commit_status_enabled: bool = True
    token: Optional[str] = Field(None, max_length=200)


class GitLabConfigRead(BaseModel):
    """GET/PUT response for ``/projects/{id}/integrations/gitlab``.

    Structurally token-free — the PAT can never leak through this model;
    ``has_token`` is the only token signal. ``mr_comment_mode`` is a plain
    ``str`` on the read side (GitHub-sibling pattern): the column is an
    unconstrained ``String(20)``, and a drifted row value must degrade
    gracefully instead of turning GET into a ResponseValidationError 500.
    """
    enabled: bool = False
    base_url: str = Field("https://gitlab.com", max_length=500)
    project_path: str = Field("", max_length=500)
    mr_comment_mode: str = "failures_only"
    commit_status_enabled: bool = True
    has_token: bool = False
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None


class GitLabConnectionTestResponse(BaseModel):
    ok: bool
    detail: str
    project_id_resolved: Optional[str] = None


# ── Outbound Webhooks (Tier 2 item 6) ───────────────────────────────────────


class WebhookSubscriptionWrite(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    target_url: str = Field(..., min_length=8, max_length=1000, pattern=r"^https?://")
    events: List[str] = Field(..., min_length=1)
    enabled: bool = True
    max_retries: int = Field(5, ge=0, le=10)
    # Null = leave existing secret alone; "" = clear; any value = upsert.
    secret: Optional[str] = Field(None, max_length=200)


class WebhookSubscriptionRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    target_url: str
    events: List[str]
    enabled: bool
    has_secret: bool
    max_retries: int
    last_delivered_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_error: Optional[str] = None
    failure_count: int
    total_delivered: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class WebhookDeliveryRead(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    event_type: str
    event_payload: Optional[Dict[str, Any]] = None
    status: str
    attempt_count: int
    http_status: Optional[int] = None
    response_preview: Optional[str] = None
    error: Optional[str] = None
    delivered_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class WebhookTestResponse(BaseModel):
    success: bool
    status_code: Optional[int] = None
    message: str
    latency_ms: Optional[int] = None


class WebhookDeliveryReplayResponse(BaseModel):
    """Result of POST /webhooks/{sub_id}/deliveries/{delivery_id}/replay."""
    delivery_id: uuid.UUID
    # The id of the *new* PENDING delivery row created by replay. The
    # original row is left in place so delivery history remains
    # auditable — a replay is never an in-place mutation.
    status: str = "PENDING"


class WebhookEventCatalogEntry(BaseModel):
    event_type: str
    description: str


class WebhookEventCatalogResponse(BaseModel):
    events: List[WebhookEventCatalogEntry]


# ── Run Compare (Tier 2 item 8) ─────────────────────────────────────────────


class RunCompareSummary(BaseModel):
    """One side of the compare view — the subset of TestRun fields used
    by the diff UI. Kept tiny so the JSON payload is fast even on big runs."""
    id: uuid.UUID
    project_id: uuid.UUID
    build_number: Optional[str] = None
    branch: Optional[str] = None
    commit_hash: Optional[str] = None
    status: Optional[str] = None
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    broken_tests: int = 0
    skipped_tests: int = 0
    unknown_tests: int = 0
    pass_rate: Optional[float] = None
    duration_ms: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    primary_suite_name: Optional[str] = None
    suite_names: Optional[List[str]] = None


class RunCompareSelection(BaseModel):
    mode: Literal["latest_vs_previous", "explicit"] = "explicit"
    scope: Literal["run", "suite"] = "run"
    suite_name: Optional[str] = None
    selection_reason: str = ""
    project_id: uuid.UUID
    branch: Optional[str] = None
    branch_mismatch: bool = False
    release_name: Optional[str] = None


class RunCompareAIReport(BaseModel):
    status: Literal["ready", "queued", "failed"] = "ready"
    executive_summary: str = ""
    markdown_report: str = ""
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    key_differences: List[str] = Field(default_factory=list)
    new_risks: List[str] = Field(default_factory=list)
    resolved_risks: List[str] = Field(default_factory=list)
    duration_concerns: List[str] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)
    confidence: int = 0
    confidence_reason: str = ""
    fallback_used: bool = False
    message: Optional[str] = None


class RunCompareTestDelta(BaseModel):
    """A single test whose status or duration differed between the two runs."""
    test_fingerprint: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    left_status: Optional[str] = None   # None = test did not exist in left run
    right_status: Optional[str] = None
    left_duration_ms: Optional[int] = None
    right_duration_ms: Optional[int] = None
    delta_duration_ms: Optional[int] = None
    classification: str
    # One of: new_failure | fixed | still_failing | regressed |
    # improved | new_test | removed_test | duration_spike | renamed
    paired_by: Optional[str] = None
    # "fingerprint" (first-pass stable-hash match) or
    # "fuzzy_name_match" (second-pass SequenceMatcher rename pairing).
    # Legacy responses omit this field — frontend should default to
    # "fingerprint" when absent.
    previous_test_name: Optional[str] = None
    previous_test_fingerprint: Optional[str] = None
    # Set when ``paired_by == "fuzzy_name_match"``. Carries the
    # pre-rename identity so the UI can render a "was: <old_name>"
    # label next to the current name.


class RunCompareResponse(BaseModel):
    left: RunCompareSummary
    right: RunCompareSummary
    scope: Literal["run", "suite"] = "run"
    suite_name: Optional[str] = None
    selection: Optional[RunCompareSelection] = None
    ai_report: Optional[RunCompareAIReport] = None
    # Aggregate deltas (right - left).
    delta_total: int = 0
    delta_passed: int = 0
    delta_failed: int = 0
    delta_broken: int = 0
    delta_skipped: int = 0
    delta_pass_rate: Optional[float] = None
    delta_duration_ms: Optional[int] = None
    # Category counts from the per-test diff.
    new_failures: int = 0
    fixed: int = 0
    still_failing: int = 0
    regressed: int = 0
    improved: int = 0
    new_tests: int = 0
    removed_tests: int = 0
    duration_spikes: int = 0
    renamed: int = 0
    # ``renamed`` counts second-pass fuzzy-paired deltas whose base
    # classification was None (same status, no duration spike). Tests
    # paired by fuzzy match that also changed status are counted in
    # their status-change bucket, not in ``renamed``.
    # Detailed per-test diff — capped at 500 entries.
    test_deltas: List[RunCompareTestDelta] = Field(default_factory=list)
    truncated: bool = False


# ── Suite Owners & Reviews (migration 0076) ─────────────────────────────────

SuiteReviewStateLiteral = Literal["pending", "confirmed", "acknowledged", "review_later"]


class SuiteOwnerUpdate(BaseModel):
    """PUT body for setting/clearing a suite's explicit owner."""
    owner_user_id: Optional[uuid.UUID] = None  # None clears the explicit owner


class SuiteOwnerResponse(BaseModel):
    project_id: uuid.UUID
    suite_name: str
    owner_user_id: Optional[uuid.UUID] = None
    owner_email: Optional[str] = None
    owner_full_name: Optional[str] = None
    is_fallback: bool = False  # True when resolved owner is project.manager_user_id
    model_config = ConfigDict(from_attributes=True)


class SuiteReviewUpdate(BaseModel):
    state: SuiteReviewStateLiteral
    note: Optional[str] = Field(None, max_length=4000)


class SuiteReviewResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    suite_name: str
    test_run_id: uuid.UUID
    state: str
    note: Optional[str] = None
    reviewer_user_id: Optional[uuid.UUID] = None
    reviewer_email: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class NotifyTestOwnerRequest(BaseModel):
    """POST body for /api/v1/analytics/notify-owner — fires an email at the
    suite owner of the test that's been failing repeatedly."""
    project_id: uuid.UUID
    test_name: str = Field(..., min_length=1, max_length=1000)
    days: int = Field(30, ge=1, le=365)
    fail_count: Optional[int] = Field(None, ge=0, le=10_000)


class NotifyTestOwnerResponse(BaseModel):
    queued: bool
    sent_to: Optional[str] = None
    owner_name: Optional[str] = None
    suite_name: Optional[str] = None
    is_fallback_owner: bool = False
    # When ``queued=False`` this carries a user-readable reason
    # ("Test not found in window", "Suite has no owner", etc.).
    reason: Optional[str] = None


class ClassifyUncategorizedRequest(BaseModel):
    """POST body for /api/v1/analytics/classify-uncategorized — bulk-assign a
    failure category to every test case currently labelled UNKNOWN (or
    NULL) in the requested project + window."""
    project_id: uuid.UUID
    category: FailureCategory
    days: int = Field(30, ge=1, le=365)
    # Optional: restrict to a single suite (e.g. when the user is on the
    # failures page filtered by a specific suite).
    suite_name: Optional[str] = Field(None, max_length=500)


class ClassifyUncategorizedResponse(BaseModel):
    updated: int
    category: str
    project_id: uuid.UUID
    days: int
    suite_name: Optional[str] = None


class DefectIntakeRequest(BaseModel):
    """POST body for /api/v1/analytics/defects — manual defect intake.

    Severity uses the P0–P3 vocabulary the Defects UI renders; it maps to the
    `defects.severity` column's CRITICAL/HIGH/MEDIUM/LOW values server-side.
    `test_name`/`suite_name` are optional — when both are supplied the service
    will try to attach the new defect to the most-recent matching TestCase row,
    otherwise the defect is created standalone (test_case_id NULL).
    """
    project_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=10_000)
    severity: Literal["P0", "P1", "P2", "P3"]
    failure_category: FailureCategory = FailureCategory.PRODUCT_BUG
    component: Optional[str] = Field(None, max_length=255)
    test_name: Optional[str] = Field(None, max_length=1000)
    suite_name: Optional[str] = Field(None, max_length=500)
    jira_ticket_url: Optional[str] = Field(None, max_length=1000)


class DefectIntakeResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    severity: str
    failure_category: Optional[str] = None
    component: Optional[str] = None
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    jira_ticket_id: Optional[str] = None
    jira_ticket_url: Optional[str] = None
    resolution_status: str
    ai_confidence_score: Optional[int] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ── Retention policies (PMF US-11.4) ─────────────────────────────────────────


class RetentionPolicyWrite(BaseModel):
    """PUT body for ``/projects/{id}/retention-policy``.

    All fields optional — omitted fields keep their current (or default)
    value. Per-field bounds 422 here; the cross-field ``audit_days >=
    runs_days`` check runs in the service on the MERGED values (a partial
    body can't be validated field-locally).
    """
    enabled: Optional[bool] = None
    raw_events_days: Optional[int] = Field(None, ge=7, le=3650)
    runs_days: Optional[int] = Field(None, ge=30, le=3650)
    artifacts_days: Optional[int] = Field(None, ge=7, le=3650)
    audit_days: Optional[int] = Field(None, ge=365, le=3650)


class RetentionLastPurge(BaseModel):
    """Latest execute-mode purge, parsed from the settings_audit_log
    purge-audit rows (``setting_key = "retention_purge:{project_id}"``)."""
    at: datetime
    mode: str = "execute"
    counts: dict = Field(default_factory=dict)  # per-store counts


class RetentionPolicyRead(BaseModel):
    """GET/PUT response — the EFFECTIVE policy plus its source
    (``default`` = no row, ``custom`` = project row exists)."""
    enabled: bool
    raw_events_days: int
    runs_days: int
    artifacts_days: int
    audit_days: int
    source: str = "default"
    last_purge: Optional[RetentionLastPurge] = None


class RetentionPreviewCandidates(BaseModel):
    """Per-class candidate counts a purge WOULD delete right now."""
    runs: int
    test_cases: int
    mongo_docs: dict[str, int] = Field(default_factory=dict)
    minio_objects: int
    event_archive_rows: int
    audit_rows: int
    provenance_rows: int
    compliance_packs_expired: int


class RetentionPreviewResponse(BaseModel):
    """POST ``.../retention-policy/preview`` — dry-run, writes nothing."""
    cutoffs: dict[str, datetime]
    candidates: RetentionPreviewCandidates


class RetentionPurgeRequest(BaseModel):
    """POST ``.../retention-policy/purge`` — typed-name confirmation
    (``project_reset`` convention): must equal the project name exactly."""
    confirmation_name: str = Field(..., min_length=1, max_length=255)


class RetentionPurgeQueued(BaseModel):
    """202 body — the execute-mode purge was enqueued to Celery."""
    queued: bool = True
