"""Pydantic v2 request/response schemas for all API endpoints."""
import json
import re
import uuid
from datetime import date, datetime
from typing import Any, ClassVar, Dict, List, Literal, Optional, Union

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.core.config import MAX_RESULTS_PER_INGEST
# W11 (REL-05 / REL-07): the bulk-phase bound lives with the release-planning
# vocabulary, which the services and the tests import too.
from app.core.release_planning import MAX_BULK_PHASES
# W10 (INT-01 / INT-03): one definition of each integration value's shape and
# its sentence, shared with the services that refuse a stored value at use.
from app.core.integration_values import (
    DNS_LABEL_RE,
    INTEGRATION_URL_SENTENCE,
    JIRA_DOMAIN_SENTENCE,
    JIRA_HOST_RE,
    OCP_NAMESPACE_SENTENCE,
    SPLUNK_INDEX_RE,
    SPLUNK_INDEX_SENTENCE,
    integration_url_problem,
)
# W12 (SRCH-03): the saved-search rules and sentences live with the search
# vocabulary, which the services, the routers and the tests import too.
from app.core.search_vocab import (
    MAX_QUERY_CHARS,
    SAVED_SEARCH_KEYS,
    SAVED_SEARCH_KEYS_SENTENCE,
    SAVED_SEARCH_KIND,
    SAVED_SEARCH_MODE_SENTENCE,
    SAVED_SEARCH_PAGE_SENTENCE,
    SAVED_SEARCH_Q_SENTENCE,
    SAVED_SEARCH_SCOPE_SENTENCE,
    SAVED_SEARCH_SHARE_NEEDS_PROJECT,
    SEARCH_MODES,
    SEARCH_SCOPES,
)
# W13 (TCL-01): the saved case view's rules and sentences live with the
# test-library vocabulary, which the services, the routers and the tests import too.
from app.core.test_library_vocab import (
    AUTOMATION_TARGET_DEFAULT,
    CASE_AUTOMATION_FILTERS,
    CASE_FILTER_MAX_VALUES,
    CASE_PRIORITIES,
    CASE_VIEW_KEYS,
    CASE_VIEW_KEYS_SENTENCE,
    CASE_VIEW_PAGE_SENTENCE,
    CASE_VIEW_Q_MAX,
    CASE_VIEW_REQUIREMENT_MAX,
    CASE_VIEW_SHARE_NEEDS_PROJECT,
    CASE_VIEW_SUITE_MAX,
    CASE_VIEW_TYPE_MAX,
    CASE_VIEW_VALUE_SENTENCE,
    LIFECYCLE_STATES,
    OWNER_ME,
    TEST_CASE_VIEW_KIND,
    TEST_CASE_VIEW_PAGE,
)
# W03 (AI history): the types live in their own module; the existing models
# below only gain optional fields of these types.
from app.models.ai_history_schemas import (
    AIAnalysisDiffBrief,
    TestHistoryAI,
    TestHistoryAIChange,
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
# W22a: constants only (no app.* import inside), so a module-level import is safe.
from app.services.retention_vocab import (
    ARCHIVE_NOT_BACKUP,
    FLOOR_NOTE_EXPLICIT,
    KEEP_LAST_RUNS_DEFAULT,
    PURGE_WINDOW_DEFAULT_HOUR,
)
# W23b FAIL-13: constants only (no app.* import inside), so a module-level import is safe.
from app.services.run_context_vocab import (
    CONTEXT_MAX_BYTES,
    CONTEXT_VERSION,
    DIGEST_RE,
    ENV_ALLOWLIST,
    ENV_VALUE_MAX,
    IMAGE_NAME_RE,
    IMAGES_MAX,
    LOCKFILE_NAMES,
    RUN_CONTEXT_ERRORS,
    RUNTIME_KEY_RE,
    RUNTIME_MAX,
    SHA256_RE,
    VERSION_RE,
)
# W23d AGT-14 / AGT-15: constants only (no app.* import inside), so a module-level import is safe.
from app.services.http_exchange_vocab import (
    EXCHANGES_PER_RUN_MAX,
    EXCHANGES_PER_TEST_MAX,
    SCHEMA_STATES,
)
from app.services.prometheus_vocab import (
    SERVICE_PLACEHOLDER,
    TEMPLATE_ERRORS,
    TEMPLATE_ID_RE,
    TEMPLATE_LABEL_MAX,
    TEMPLATE_PROMQL_MAX,
    TEMPLATE_UNITS,
    TEMPLATES_MAX,
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


class RetireCompliancePackRequest(BaseModel):
    """Bring a pack's retention window forward to now.

    Typed confirmation rather than a checkbox: a pack is audit evidence, and
    retiring it early hands it to the next nightly purge.
    """

    confirmation_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Must equal the pack id exactly.",
    )
    reason: str = Field(..., min_length=3, max_length=500)


class CompliancePackLifecycleResponse(BaseModel):
    pack_id: uuid.UUID
    retention_expires_at: datetime
    #: True when the pack is now past its window and the next sweep will
    #: collect it. False means it was retired to a future instant, which
    #: should not happen and is worth seeing rather than assuming.
    purgeable_now: bool


# ── Retention holds / reference warnings (W22a ACT-05 / ACT-04) ──
# Shared by the deletion answers below and the retention section further down.
# Enum-like fields are plain ``str`` (``retention_vocab``); every field added
# to an EXISTING answer is defaulted so old callers, fakes and rows validate.

class HoldRefRead(BaseModel):
    """A live retention hold as it appears inside another answer."""

    hold_id: uuid.UUID
    entity_type: str
    entity_id: Optional[uuid.UUID] = None
    entity_label: Optional[str] = None
    reason: str
    expires_at: Optional[datetime] = None


class ReferenceBrokenRead(BaseModel):
    """A link a deletion breaks (a SET NULL foreign key to ``test_runs``) — a warning, never a block."""

    table: str
    column: str
    count: int
    label: str  # REFERENCE_LABELS / the fallback, rendered


# ── Export before delete (W22b ACT-03) ──
# Enum-like fields (``tier``, ``target``) are plain ``str``: the vocabulary is
# ``retention_vocab.EXPORT_TIERS`` / ``EXPORT_TARGETS`` and the service refuses
# a value outside it with one fixed sentence (``EXPORT_ERROR_SENTENCES``).

class ExportTierEstimate(BaseModel):
    """One tier's archive size estimate. ``None`` = not measured, never 0."""

    tier: str  # EXPORT_TIERS
    archive_bytes: Optional[int] = None  # an ESTIMATE before compression; None = not measured
    measured: bool = False
    # archive_bytes > max_bytes (False when not measured: the build enforces the cap)
    over_cap: bool = False


class ExportEstimate(BaseModel):
    """What archiving the DELETABLE runs of a criteria preview would cost and reclaim."""

    run_count: int
    exportable: bool  # 0 < run_count <= EXPORT_MAX_RUNS
    # EXPORT_ERROR_SENTENCES["too_many_runs" | "nothing_to_export"] when not exportable
    refusal: Optional[str] = None
    # object bytes (exact) + Mongo bytes (estimate); None = not measured
    reclaim_bytes: Optional[int] = None
    reclaim_object_bytes: Optional[int] = None  # exact, from the two object-root listings
    reclaim_mongo_bytes: Optional[int] = None  # docs × collStats.avgObjSize (estimate)
    reclaim_measured: bool = False  # the object listing succeeded (Mongo may still be None)
    tiers: List[ExportTierEstimate] = Field(default_factory=list)  # always the three tiers, EXPORT_TIERS order
    max_bytes: int  # settings.RETENTION_EXPORT_MAX_BYTES
    collect_ttl_hours: int
    note: str = ARCHIVE_NOT_BACKUP


class ExportChoice(BaseModel):
    """``DeletionExecuteRequest.export``. Validated in the service (fixed 422 sentence), not by ``Literal``."""

    tier: str
    target: str


class DeletionPreviewResponse(BaseModel):
    """A frozen candidate set an ADMIN authorises from.

    The ids are materialized and hashed here, and ``execute`` replays them.
    Re-resolving at execute time would delete a different set from the one
    that was reviewed: criteria read columns other code rewrites while the job
    is queued (``TestRun.status`` by aggregate updates and live-session close,
    ``primary_suite_name`` at session close).
    """

    job_id: uuid.UUID
    project_id: uuid.UUID
    run_count: int
    run_ids: List[uuid.UUID]
    candidate_hash: str
    truncated: bool = Field(
        False,
        description=(
            "The candidate set exceeded the reviewable bound and was cut. "
            "Reported rather than silently deleting the first N."
        ),
    )
    refused_prefixes: List[str] = Field(default_factory=list)
    blocked: List[dict] = Field(
        default_factory=list,
        description=(
            "Runs that cannot be deleted and why — in flight, or cited by a "
            "compliance pack, release or decision report. Surfaced at preview "
            "so an ADMIN sees them before authorising, not after."
        ),
    )
    # W22a: reference warnings over the deletable runs, and the floor note.
    references_broken: List[ReferenceBrokenRead] = Field(default_factory=list)
    references_measured: bool = True
    floor_applies: bool = False
    floor_note: str = FLOOR_NOTE_EXPLICIT
    # W22b: archive estimates for all three tiers over the deletable runs
    # (absent = an older server; the SPA then offers no export).
    export_estimate: Optional[ExportEstimate] = None


class DeletionExecuteRequest(BaseModel):
    """Execute takes a JOB ID, never a criteria body.

    Accepting criteria here would re-resolve them, which is the bug the freeze
    exists to prevent — the set executed would not be the set reviewed.
    """

    job_id: uuid.UUID
    confirmation_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "Must equal the project's name exactly. A typed confirmation, not "
            "a checkbox, because this is irreversible across five stores."
        ),
    )
    # W22b: build (and verify) an archive first; None = today's path.
    export: Optional[ExportChoice] = None


class DeletionExecuteAcceptedResponse(BaseModel):
    job_id: uuid.UUID
    run_count: int
    status: Literal["accepted"] = "accepted"
    export_tier: Optional[str] = None
    export_target: Optional[str] = None


class DeletionJobResponse(BaseModel):
    """One deletion that ran (or is running).

    `bytes_reclaimed` is nullable and is NOT defaulted to 0 — "reclaimed
    nothing" and "nobody measured" are opposite findings, and this is the
    surface that tells an operator whether a purge was worth running.
    """
    id: uuid.UUID
    project_id: uuid.UUID
    job_kind: str
    status: str
    criteria: Optional[dict] = None
    counts: Optional[dict] = None
    bytes_reclaimed: Optional[int] = None
    # W22a: NULL on every job written before 0239/0240, and whenever no hold
    # applied / nothing broke (see deletion_job_service.close_job).
    holds_honoured: Optional[dict] = None
    references_broken: Optional[dict] = None
    error: Optional[str] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # W22b export before delete: NULL on every job written before 0241 and on
    # every job without an export. ``export_location`` is NEVER part of this
    # answer; ``export_summary`` is filled by the ROUTE from ``export_meta``
    # with only {"run_count", "file_count", "objects_not_archived",
    # "missing_runs", "excluded_runs", "signing_state"}.
    export_tier: Optional[str] = None
    export_target: Optional[str] = None
    export_bytes: Optional[int] = None
    export_manifest_sha256: Optional[str] = None
    export_verified_at: Optional[datetime] = None
    collect_expires_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    export_summary: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class DeletionJobListResponse(BaseModel):
    jobs: List[DeletionJobResponse] = Field(default_factory=list)
    # The jobs in the project, ignoring `limit` — a top-N length is not a count.
    total: int = 0


class DeletionAcknowledgeRequest(BaseModel):
    """Confirms a collected archive was saved; the route answers ``DeletionJobResponse``."""

    manifest_sha256: str = Field(..., min_length=64, max_length=64, pattern="^[0-9a-fA-F]{64}$")


class StoreFootprintResponse(BaseModel):
    """One store's contribution to a project's storage footprint.

    ``measured=False`` means the store could not be reached — ``bytes`` and
    ``items`` are then ``None``, never ``0``. Zero and unreachable are opposite
    findings and must not render alike.

    ``exact=False`` means the byte figure is a proportional estimate over a
    store shared with other projects; ``estimate_basis`` says how.
    """
    store: str
    measured: bool
    exact: bool
    #: False when a safety cap left part of the project's namespace unscanned.
    #: The reported figures are then a floor even though the store was reached.
    complete: bool = True
    bytes: Optional[int] = None
    items: Optional[int] = None
    estimate_basis: Optional[str] = None
    unreachable_reason: Optional[str] = None


class ProjectStorageResponse(BaseModel):
    project_id: str
    computed_at: datetime
    stores: List[StoreFootprintResponse]
    #: None when nothing was measurable — an unreachable everything must not
    #: total to zero.
    total_bytes: Optional[int] = None
    #: True when any contributing store was estimated or incompletely scanned.
    total_is_estimate: bool = False
    fully_measured: bool = True


class DeletedProjectStorageEntry(BaseModel):
    project_id: str
    name: str
    #: True when the scheduled beat would eventually sweep this project anyway.
    #: The beat selects on the EFFECTIVE `enabled` (the project's own value,
    #: else the instance default) and does NOT filter `is_active`, so a
    #: project that opted in — or inherits an enabled instance default —
    #: before deletion is still reachable. One that never opted in — the
    #: default — is not.
    reachable_by_retention: bool
    footprint: ProjectStorageResponse


class DeletedProjectsStorageResponse(BaseModel):
    computed_at: datetime
    projects_total: int
    projects_measured: int
    #: True when `limit` cut the scan short. A capped total that did not say so
    #: would understate the very figure this endpoint exists to surface.
    truncated: bool = False
    projects: List[DeletedProjectStorageEntry] = Field(default_factory=list)
    total_bytes: Optional[int] = None
    total_is_estimate: bool = False
    #: Deleted projects that no retention policy will ever reach — the headline.
    unreachable_by_retention: int = 0


class UIDismissalCreate(BaseModel):
    """Dismiss a UI prompt for the authenticated user.

    ``dismissal_key`` is validated against a closed allowlist in
    ``services/ui_dismissal_service.KNOWN_DISMISSAL_KEYS`` rather than by a
    Pydantic enum here: a strict enum over a ``String(100)`` column silently
    422s when the vocabularies drift, and the UI shows nothing at all.
    """
    dismissal_key: str = Field(..., min_length=1, max_length=100)


class UIDismissalListResponse(BaseModel):
    dismissed: List[str] = Field(default_factory=list)


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

_JENKINS_JOB_PATTERN_DESCRIPTION = "Stored only; TestLookup does not use it (W10 D22)."


def _project_splunk_index(value: Optional[str], *, empty: Optional[str]) -> Optional[str]:
    """W10 INT-03: an SPL-safe index name, or ``empty`` for a cleared field."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return empty
    if SPLUNK_INDEX_RE.fullmatch(value) is None:
        raise PydanticCustomError("splunk_index", SPLUNK_INDEX_SENTENCE)
    return value


def _project_ocp_namespace(value: Optional[str], *, empty: Optional[str]) -> Optional[str]:
    """W10 INT-03: a DNS-1123 label (an OpenShift namespace), or ``empty`` for a cleared field."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return empty
    if DNS_LABEL_RE.fullmatch(value) is None:
        raise PydanticCustomError("ocp_namespace", OCP_NAMESPACE_SENTENCE)
    return value


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = Field(None, max_length=2000)
    jira_project_key: Optional[str] = Field(None, max_length=50)
    splunk_index: Optional[str] = Field(None, max_length=255)
    ocp_namespace: Optional[str] = Field(None, max_length=255)
    jenkins_job_pattern: Optional[str] = Field(
        None, max_length=500, description=_JENKINS_JOB_PATTERN_DESCRIPTION,
    )
    component_owner_map: Optional[dict] = None
    # Optional at create time — admin can set later. When set, the user must
    # already have ProjectMember.role=QA_LEAD on this project (or be ADMIN).
    # The router enforces the role check.
    default_qa_lead_user_id: Optional[uuid.UUID] = None  # migration 0079

    # W10 INT-03: validated at save (422 with a fixed sentence); an empty
    # value is stored as None on create.
    @field_validator("splunk_index", mode="after")
    @classmethod
    def _splunk_index_is_spl_safe(cls, value: Optional[str]) -> Optional[str]:
        return _project_splunk_index(value, empty=None)

    @field_validator("ocp_namespace", mode="after")
    @classmethod
    def _ocp_namespace_is_a_dns_label(cls, value: Optional[str]) -> Optional[str]:
        return _project_ocp_namespace(value, empty=None)


class ProjectUpdate(BaseModel):
    """Partial update for project attributes. None = keep existing."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    jira_project_key: Optional[str] = Field(None, max_length=50)
    splunk_index: Optional[str] = Field(None, max_length=255)
    ocp_namespace: Optional[str] = Field(None, max_length=255)
    jenkins_job_pattern: Optional[str] = Field(
        None, max_length=500, description=_JENKINS_JOB_PATTERN_DESCRIPTION,
    )
    component_owner_map: Optional[dict] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Optional[List[str]] = None
    manager_user_id: Optional[uuid.UUID] = None  # migration 0076 — program manager
    default_qa_lead_user_id: Optional[uuid.UUID] = None  # migration 0079 — default suite owner
    # W28 TCM-02 (migration 0222): a test case needs an approved review before
    # Active. exclude_none in the PUT applies a False (routers/projects.py).
    require_case_review: Optional[bool] = None
    # W13 TCL-03 (migration 0228): the library health's automation target, a
    # whole percentage; exclude_none in the PUT applies it (0 included).
    automation_target_pct: Optional[int] = Field(None, ge=0, le=100)

    # W10 INT-03: validated at save; "" clears the field (stored "", read as
    # unset by the precedence rule, so the deployment's index / namespace apply).
    @field_validator("splunk_index", mode="after")
    @classmethod
    def _splunk_index_is_spl_safe(cls, value: Optional[str]) -> Optional[str]:
        return _project_splunk_index(value, empty="")

    @field_validator("ocp_namespace", mode="after")
    @classmethod
    def _ocp_namespace_is_a_dns_label(cls, value: Optional[str]) -> Optional[str]:
        return _project_ocp_namespace(value, empty="")


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
    require_case_review: bool = False  # migration 0222 (W28 TCM-02)
    automation_target_pct: int = AUTOMATION_TARGET_DEFAULT  # migration 0228 (W13 TCL-03)
    # W17 ADM-04 (migration 0232): when / by whom it was archived (NULL when active,
    # or archived before 0232), and the archiver's name for the Archived list.
    archived_at: Optional[datetime] = None
    archived_by_user_id: Optional[uuid.UUID] = None
    archived_by_name: Optional[str] = None
    # S1 — retention posture, resolved in the SAME query as the project list
    # (LEFT JOIN, never a per-project lookup). Three states, deliberately
    # distinct: "unconfigured" (no policy row has ever existed) is the case
    # the activation nudge exists for, and collapsing it into "disabled"
    # would hide exactly the projects that need prompting.
    # None on responses built before the join existed.
    retention_status: Optional[Literal["enabled", "disabled", "unconfigured"]] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("require_case_review", mode="before")
    @classmethod
    def _unflushed_review_setting_is_off(cls, value: Any) -> Any:
        # The column is NOT NULL DEFAULT false, but its ORM default applies only
        # at INSERT: a Project answered before its flush (a hand-built row, a
        # session double) still carries None. That is the default, so: off.
        return False if value is None else value

    @field_validator("automation_target_pct", mode="before")
    @classmethod
    def _unflushed_automation_target_is_the_default(cls, value: Any) -> Any:
        # NOT NULL DEFAULT 60 (migration 0228), applied only at INSERT: an
        # unflushed Project carries None, which is the default target.
        return AUTOMATION_TARGET_DEFAULT if value is None else value


class DeleteRunRequest(BaseModel):
    """Payload for ``DELETE /api/v1/runs/{run_id}``.

    ``confirm`` is a deliberate second step, not ceremony: this deletes across
    five stores and is irreversible. ``reason`` is recorded on the deletion job
    and the tombstone, so "why is this run gone" has an answer later.
    """

    confirm: bool = Field(
        ...,
        description="Must be true. A DELETE without it is refused, not assumed.",
    )
    reason: str = Field(..., min_length=3, max_length=500)


class DeleteRunAcceptedResponse(BaseModel):
    """202, not 204.

    A synchronous delete of a multi-GB prefix times out at the gateway, and
    because the cross-store order is Mongo -> MinIO -> Postgres the caller
    would get a 504 with the artifacts already gone and the run still listed.
    Poll ``job_id`` on the deletion-jobs endpoint instead.
    """

    job_id: Optional[uuid.UUID] = Field(
        None,
        description=(
            "Deletion job to poll. NULL when the job record could not be "
            "written — the deletion still runs; only its bookkeeping failed."
        ),
    )
    run_id: uuid.UUID
    status: Literal["accepted"] = "accepted"
    refused_prefixes: List[str] = Field(
        default_factory=list,
        description=(
            "Object prefixes NOT deleted because they fall outside the "
            "project's scope. Reported rather than silently skipped: storage "
            "that does not fall needs an explanation."
        ),
    )
    # W22a: the links this deletion breaks (warnings), and the floor note —
    # an explicit delete is not limited by the keep-last floor.
    references_broken: List[ReferenceBrokenRead] = Field(default_factory=list)
    references_measured: bool = True
    floor_applies: bool = False
    floor_note: str = FLOOR_NOTE_EXPLICIT


class DeleteRunRefusedResponse(BaseModel):
    """409 body. Every blocker is listed, not just the first one found."""

    run_id: uuid.UUID
    blockers: List[str]


# ── W07 FAIL-04 / FAIL-03 / FAIL-05: the Runs list's batch reads ──
# Enum-like fields are plain ``str`` (the vocabularies live in
# ``app.services.run_verdict``); ``None`` means "not measured / not known".


class RunVerdictItem(BaseModel):
    """One run of ``GET /runs/ai-verdicts``: what its AI-verdict pill is computed from."""

    run_id: str
    # A servable snapshot was read (fresh or stale; schema_version current).
    snapshot: bool
    stale: Optional[bool] = None
    # ISO UTC.
    generated_at: Optional[str] = None
    # A finished agent pipeline exists for the run.
    analysed: bool
    # The slim payload (``run_verdict.verdict_inputs_of``); None when snapshot is False.
    verdict_inputs: Optional[dict[str, Any]] = None
    # {"state": str}; None when snapshot is False.
    review: Optional[dict[str, Any]] = None


class RunVerdictBatchResponse(BaseModel):
    items: list[RunVerdictItem]
    # Unknown OR not accessible: one list, no distinction.
    unavailable: list[str]
    max_ids: int


class RunSignatureEntry(BaseModel):
    # The FAILURE_SIGNATURE_SQL value; "__NO_MESSAGE__" (NO_MESSAGE_ID) for a blank message.
    signature: str
    # The most common RAW first line of the group, <= 160 chars; "" for no message.
    label: str
    # Failing rows read with this signature.
    count: int
    # Distinct test fingerprints.
    test_count: int
    # <= 3 test names, sorted (text, never markup).
    tests: list[str]


class RunSignatureItem(BaseModel):
    run_id: str
    # Every FAILED/BROKEN row of the run.
    failing_tests: int
    # Rows grouped (<= SIGNATURE_ROWS_PER_RUN).
    read: int
    # read < failing_tests.
    capped: bool
    # signatures[0].signature; None when the run has no failing row.
    dominant: Optional[str] = None
    # <= 3, count desc, then signature COLLATE "C".
    signatures: list[RunSignatureEntry]


class RunSignatureBatchResponse(BaseModel):
    items: list[RunSignatureItem]
    unavailable: list[str]
    max_ids: int
    rows_per_run_cap: int


class RunBranchItem(BaseModel):
    branch: str
    runs: int
    last_run_at: Optional[str] = None


class RunBranchListResponse(BaseModel):
    # Newest last_run_at first, <= 100.
    items: list[RunBranchItem]
    # Distinct branches in the window (a COUNT, not len(items)).
    total: int
    truncated: bool
    # None = all time (days=0).
    window_days: Optional[int] = None


class ProjectResetRequest(BaseModel):
    """Destructive reset payload. ``mode`` selects the wipe scope; the
    backend rejects any request whose ``confirmation_name`` doesn't
    exactly equal the project's ``name`` — a typed-confirmation guard
    against autopilot clicks. See services/project_reset_service.py for
    the table list per mode."""

    mode: Literal["runs", "full"]
    confirmation_name: str = Field(..., min_length=1, max_length=255)


class ProjectRestoreResponse(BaseModel):
    """W17 ADM-04: a restored project and what deliberately stayed revoked."""
    restored: bool = True
    project_id: uuid.UUID
    api_keys_still_revoked: int
    default_qa_lead_reactivated: bool = False


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
    ingestion_attempted_tests: Optional[int] = None
    ingestion_rejected_tests: int = 0
    ingestion_complete: Optional[bool] = None
    ingestion_rejection_reasons: Optional[List[dict]] = None
    branch: Optional[str] = None
    status: LaunchStatus
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    broken_tests: int
    unknown_tests: int = 0
    pass_rate: Optional[float] = None
    duration_ms: Optional[int] = Field(
        None,
        description=(
            "Test time (summed): the sum of the run's test durations, not how "
            "long the run took. Parallel tests make it larger than wall_clock_ms."
        ),
    )
    # EXEC-02 (migration 0203): how long the run took on the clock.
    wall_clock_ms: Optional[int] = Field(
        None,
        description=(
            "Wall clock: how long the run took, start to finish. NULL when no "
            "report or test timestamps state it (timing_source 'derived_sum' "
            "or not timed yet) -- never the summed time passed off as a wall "
            "clock. Parallelism = duration_ms / wall_clock_ms."
        ),
    )
    exec_started_at: Optional[datetime] = Field(
        None, description="When the run's tests started executing (not the ingest time)."
    )
    exec_finished_at: Optional[datetime] = Field(
        None, description="When the run's tests finished executing (not the finalize time)."
    )
    timing_source: Optional[str] = Field(
        None,
        description=(
            "Where wall_clock_ms came from: 'report' (the report stated it), "
            "'derived' (the union of the suites' start/finish spans) or "
            "'derived_sum' (no wall clock known; only the summed time). "
            "NULL = not timed yet."
        ),
    )
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
    # Execution timing (EXEC-01, migration 0202): what the report or live
    # stream said about when the test ran. All None when the format carries
    # none of it (JUnit XML, xUnit) -- "not reported", never zero.
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    setup_ms: Optional[int] = None
    call_ms: Optional[int] = None
    teardown_ms: Optional[int] = None
    worker_id: Optional[str] = None
    # report | derived | live (``report_timings.TIMING_SOURCES``); plain str so
    # a value outside the set cannot 422 the detail read.
    timing_source: Optional[str] = None


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
    source_test_run_id: Optional[uuid.UUID] = None
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
    # Allure commonly emits a list of {name, value} objects; other producers
    # emit a mapping. Values are intentionally Any so one malformed parameter
    # cannot turn an otherwise valid test-detail response into a 500.
    parameters: Optional[Union[Dict[str, Any], List[Any]]] = None
    created_at: datetime
    steps: List["TestStepResponse"] = Field(default_factory=list)
    attachments: List[TestAttachmentResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


TestStepResponse.model_rebuild()


class TestCaseIdentitySection(BaseModel):
    """Stable and source-native identifiers for one executed test result."""

    test_case_id: uuid.UUID
    test_run_id: uuid.UUID
    canonical_test_case_id: Optional[uuid.UUID] = None
    test_fingerprint: str
    test_name: str
    full_name: Optional[str] = None
    source_uuid: Optional[str] = None
    source_history_id: Optional[str] = None
    source_test_case_id: Optional[str] = None
    history_id: Optional[str] = None
    fingerprint: Optional[str] = None
    display_name: Optional[str] = None


class TestCaseClassificationSection(BaseModel):
    """Classification fields supplied by the report or existing catalog."""

    suite_name: Optional[str] = None
    class_name: Optional[str] = None
    package_name: Optional[str] = None
    severity: Optional[str] = None
    feature: Optional[str] = None
    story: Optional[str] = None
    epic: Optional[str] = None
    owner: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    # Ownership is nullable in this MVP; reports must not invent it.
    service_name: Optional[str] = None
    component_name: Optional[str] = None
    suite: Optional[Dict[str, Optional[str]]] = None
    service: Optional[Dict[str, Any]] = None
    components: List[Dict[str, Any]] = Field(default_factory=list)
    file_path: Optional[str] = None
    framework: Optional[str] = None
    language: Optional[str] = None
    labels: List[Dict[str, str]] = Field(default_factory=list)


class TestCaseExecutionSection(BaseModel):
    """Outcome and bounded execution metadata for the result snapshot."""

    status: TestStatus
    duration_ms: Optional[int] = None
    retry_count: Optional[int] = None
    is_flaky_run: Optional[bool] = None
    step_count: Optional[int] = None
    steps_present: bool = False
    has_attachments: bool = False
    failure_category: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    parameters: List[Dict[str, Any]] = Field(default_factory=list)
    # Execution timing (EXEC-01): see TestCaseDetail. None = not reported.
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    setup_ms: Optional[int] = None
    call_ms: Optional[int] = None
    teardown_ms: Optional[int] = None
    worker_id: Optional[str] = None
    timing_source: Optional[str] = None


class TestCaseProvenanceSection(BaseModel):
    """Where the current normalized detail came from."""

    source_test_run_id: Optional[uuid.UUID] = None
    parser_format: Optional[str] = None
    parser_version: Optional[str] = None
    minio_s3_prefix: Optional[str] = None
    format: Optional[str] = None
    source_file: Optional[str] = None
    field_sources: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class EnrichedTestCaseDetailResponse(TestCaseDetail):
    """Versioned additive contract for an executed test case.

    Inherited flat fields intentionally remain present for existing clients.
    Structured sections provide stable extension points for richer metadata.
    """

    contract: Literal["test-case-detail"] = "test-case-detail"
    schema_version: int = 1
    contract_version: Literal["1"] = "1"
    identity: TestCaseIdentitySection
    classification: TestCaseClassificationSection
    execution: TestCaseExecutionSection
    provenance: TestCaseProvenanceSection
    steps_present: bool = False
    steps: List[TestStepResponse] = Field(default_factory=list)
    attachments: List[TestAttachmentResponse] = Field(default_factory=list)
    definition: Optional[Dict[str, Any]] = None
    links: List[Dict[str, Optional[str]]] = Field(default_factory=list)
    extensions: Dict[str, Any] = Field(default_factory=dict)


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
    # QA Engineers may resolve the candidate without changing either case's
    # lifecycle.  Soft-deprecation is an explicit QA Lead/Admin decision, so
    # omission must select the least-privileged operation.
    deprecate_loser: bool = False


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
    # F-067: the POPULATION this rate is over. summary_report_service has set
    # these since #588, but SummaryTotals never declared them and a Pydantic
    # response_model drops undeclared keys — so the service computed the basis
    # and the API threw it away. The report published 83.3% bare while
    # /overview published 81.0% labelled "per test execution", which is the
    # contradiction the labels exist to prevent.
    pass_rate_basis: str = "unique_tests"
    pass_rate_basis_label: str = "per unique test"
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


class FlakyCountCriteria(BaseModel):
    """What a flaky-test count actually measured.

    A bare count cannot distinguish "this project has no flaky tests" from
    "no test cleared this particular bar", and surfaces applying different bars
    then look like they contradict each other.
    """

    window_runs: int = Field(
        ..., description="How many of each test's most recent runs were examined."
    )
    min_runs: int = Field(
        ...,
        description=(
            "Runs a test must have inside the window to be judged at all. A test "
            "with fewer is not counted as flaky and not counted as healthy — "
            "there is not enough history to say."
        ),
    )
    min_flips: int = Field(
        ...,
        description=(
            "Pass<->fail transitions required, in run order. This is what "
            "separates a flaky test from a persistent regression, which a "
            "failure ratio alone cannot do."
        ),
    )
    min_failure_ratio: float = Field(
        ...,
        description=(
            "Lower bound of the failure ratio. Below it the test is treated as "
            "healthy rather than flaky."
        ),
    )
    max_failure_ratio: float = Field(
        ...,
        description=(
            "Upper bound of the failure ratio. Above it the test is treated as "
            "broken rather than flaky — it is not intermittent, it is failing."
        ),
    )


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
    # Set only when the window holds NO run: the newest in-scope run before it,
    # so an empty report can say when the project last ran instead of reading
    # like a broken page ("no records in the last 24 hours", E2E 2026-10-10).
    last_run_before_window_at: Optional[str] = None
    # Field-level scope exception (VIZ-202 review): ``meta.ignored_filters``
    # is per dimension and the rest of the body IS release-scoped, so the one
    # field that is not says so here. Text: summary_report_service.FLAKY_COUNT_SCOPE_NOTE.
    flaky_test_count: int = Field(
        description=(
            "Not scoped by release_id. A test is flaky by its last N executions "
            "across the project's history (flaky_criteria) -- a run-count window, "
            "not a set of runs -- and the dashboard's flaky count and the release "
            "gate's flaky cap read that same project-wide figure; restricting it to "
            "one release's runs would be a different metric. Scoped by suite_name."
        ),
    )
    flaky_rate_pct: float
    # The rule behind ``flaky_test_count``, published with it (BUG-007).
    #
    # Without this a reader cannot tell "no flakiness" from "nothing cleared the
    # threshold", and the two are very different answers. Flaky Coach applies a
    # looser rule, so the same project can legitimately show a flaky test there
    # and 0 here; a user reported that pair as a contradiction.
    #
    # ``None`` ONLY on the empty envelope, where no project is resolved and
    # therefore nothing was measured. Stating criteria there would claim "we
    # looked and found none" about a search that never ran — the same
    # absence-is-not-health mistake this field exists to prevent. The UI falls
    # back to the plain rate rather than asserting a threshold was applied.
    flaky_criteria: Optional[FlakyCountCriteria] = None
    suites: List[SummarySuiteRow]
    top_failing_tests: List[SummaryTopFailingTest]
    # VIZ-204: the analytics envelope (contract C2, validated by
    # ``viz_contracts.EnvelopeMeta``) -- declared here because a response_model
    # drops undeclared keys. A plain dict: the contract module owns its shape.
    meta: Optional[Dict[str, Any]] = None


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


#: ``GET /metrics/summary`` fields that honour ``suite_name`` but NOT
#: ``release_id`` while the rest of the body honours both (VIZ-202 review;
#: ``routers/metrics.DASHBOARD_RELEASE_UNSCOPED``). They are the inputs of the
#: readiness verdict's hard caps (open CRITICAL defects, flaky count, new
#: failures in 24h), defined project-wide by the gate policy; scoping them to a
#: release would change what the gate gates on -- an owner decision, not a
#: filter fix.
_DASHBOARD_RELEASE_UNSCOPED = (
    "Not scoped by release_id (suite_name applies): a project-wide input of the "
    "readiness verdict's hard caps."
)


# ── GATE-04 (W11): the readiness policy the summary's verdict was decided by ──
class ReadinessPolicyBands(BaseModel):
    orange_min: float
    yellow_min: float                                             # the GO line ("the target")
    green_min: float


class ReadinessPolicy(BaseModel):
    id: uuid.UUID
    name: str
    version: int
    level: str                                                    # READINESS_POLICY_LEVELS: "project" | "system"
    bands: ReadinessPolicyBands


class DashboardSummary(BaseModel):
    total_executions_7d: MetricCard
    avg_pass_rate_7d: MetricCard
    active_defects: MetricCard = Field(description=_DASHBOARD_RELEASE_UNSCOPED)
    flaky_test_count: MetricCard = Field(description=_DASHBOARD_RELEASE_UNSCOPED)
    avg_duration_ms: MetricCard
    new_failures_24h: MetricCard = Field(description=_DASHBOARD_RELEASE_UNSCOPED)
    coverage_pct: Optional[MetricCard] = None
    release_readiness: Optional[str] = None  # "GREEN" | "AMBER" | "RED"
    # 4-band pass-rate verdict driven by the active ReleaseGatePolicy. ``None``
    # when no policy is active or no runs exist. ``red`` / ``orange`` /
    # ``yellow`` / ``green`` — see PolicyPassRateBands.
    release_readiness_band: Optional[str] = None
    release_readiness_downgrades: List[str] = Field(default_factory=list)
    # W11 GATE-04. None: no policy resolved, OR no execution evidence in the
    # window (the resolver runs only then), OR an entry cached before the
    # deploy. The pages read None as "default bands".
    readiness_policy: Optional[ReadinessPolicy] = None


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
    # Defaults, not required: a stored analysis is read back through this model,
    # and the model's own JSON (and older rows) often carry only source + excerpt.
    # Required, they 500'd GET /analyze/{id} for 372 of 406 stored analyses on
    # the homelab, so the test page never showed a stored root cause.
    reference_id: str = ""
    excerpt: str = ""


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
    # W07 FAIL-10: set when a project classification hint classified the test
    # (``routing_metadata.source == "project_hint"``), read verbatim.
    hint_id: Optional[str] = None
    hint_match_field: Optional[str] = None
    hint_pattern: Optional[str] = None
    hint_set_by: Optional[str] = None


# ── W07 FAIL-10: project classification hints ──
# ``match_field`` / ``category`` are plain ``str``: the vocabularies live in
# ``app.services.classification_hint_service`` and are checked there (422).


class ClassificationHintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_field: str = Field(..., max_length=20)
    # 3..200 after strip, checked in the service.
    pattern: str = Field(..., max_length=200)
    category: str = Field(..., max_length=30)
    note: Optional[str] = Field(None, max_length=500)
    enabled: bool = True


class ClassificationHintUpdate(BaseModel):
    # Only the fields SENT are applied (model_fields_set).
    model_config = ConfigDict(extra="forbid")
    match_field: Optional[str] = Field(None, max_length=20)
    pattern: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, max_length=30)
    note: Optional[str] = Field(None, max_length=500)
    enabled: Optional[bool] = None


class ClassificationHintResponse(BaseModel):
    id: str
    project_id: str
    match_field: str
    pattern: str
    category: str
    note: Optional[str] = None
    enabled: bool
    # full_name or username; None for a deleted user.
    created_by_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ClassificationHintList(BaseModel):
    # (created_at, id) order = precedence order.
    items: list[ClassificationHintResponse]
    total: int
    max_hints: int


class ClassificationHintPreview(BaseModel):
    matched: int
    # <= 200.
    sampled: int
    # 30.
    lookback_days: int
    # <= 5 {"test_name", "value"}: the matched field's value, <= 160 chars, text.
    examples: list[dict[str, str]] = []


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


# ── W12 SRCH-04: the parsed query (the `parsed` block of GET /search and GET /search/global) ──
# Plain str for every enum-like field; the vocabularies live in core/search_vocab.py.

class SearchParsedFilter(BaseModel):
    key: str                                   # FILTER_KEYS
    value: str                                 # unquoted, as typed (≤ MAX_FILTER_VALUE_CHARS)
    raw: str                                   # the exact source token, e.g. 'suite:"API Regression"'
    start: int                                 # [start, end) offsets into the STRIPPED q the server parsed
    end: int


class SearchIgnoredToken(BaseModel):
    token: str                                 # the source token (≤ 200 chars, "…" past that)
    key: Optional[str] = None                  # the lower-cased key when the token had one
    reason: str                                # IGNORED_REASONS
    message: str                               # ignored_message(...) — the page shows it verbatim
    start: int
    end: int


class SearchParsed(BaseModel):
    text: str                                  # the free text actually matched (ILIKE) or embedded (semantic)
    filters: List[SearchParsedFilter] = Field(default_factory=list)          # APPLIED filters, source order
    ignored_tokens: List[SearchIgnoredToken] = Field(default_factory=list)
    unsupported_by: Dict[str, List[str]] = Field(default_factory=dict)      # entity_type -> keys it cannot apply;
                                                                             # that entity type was NOT searched
    searched_entity_types: List[str] = Field(default_factory=list)          # [] = nothing was searched
    release_unresolved: Optional[str] = None   # the first release: value that named no release in scope
    notes: List[str] = Field(default_factory=list)   # every caption, rendered server-side, in this order:
                                                     # ignored messages, unsupported sentences, the release sentence

# GET /search (existing dict; ADDITIVE keys, no response_model is added):
#   "requested_search_type": str        # what was asked (keyword | semantic | hybrid)
#   "fallback_reason": Optional[str]    # FALLBACK_REASONS; None when search_type == requested_search_type
#   "fallback_message": Optional[str]   # FALLBACK_TEXT[fallback_reason]
#   "parsed": SearchParsed (as a dict)
# GET /search/global (existing dict; ADDITIVE): "parsed": SearchParsed (as a dict). result["scope"] / return result unchanged.
# GET /search/similar/{test_case_id} (existing dict; ADDITIVE): "semantic_status": str   # SIMILAR_SEMANTIC_STATUSES
# GET /search/index-status (existing dict; ADDITIVE, every key always present):
#   "collection": Optional[str]          # the ACTIVE test_case_search collection's name (None when unreadable)
#   "generation": Optional[str]          # the active generation tag ("" = the legacy generation)
#   "embedder": Optional[dict]           # {"provider": str, "model": str} of the active generation
#   "embedder_available": Optional[bool] # default embedder: local_embedder_guard.embedder_status()["available"];
#                                        # ollama / openai: None (not probed on a read)
#   "reembed": Optional[dict]            # {"status": str, "target_provider": str, "target_model": str,
#                                        #  "processed": int, "total": Optional[int]} while a build exists, else None
#   "semantic_available": bool           # semantic_availability(...)[0]
#   "semantic_reason": Optional[str]     # SEMANTIC_UNAVAILABLE_REASONS; None when available
#   "semantic_reason_text": Optional[str]   # SEMANTIC_REASON_TEXT[reason]


# ── W12 SRCH-03: recents and suggestions ──

class RecentSearchRead(BaseModel):
    q: str                                     # ≤ MAX_QUERY_CHARS
    scope: str                                 # SEARCH_SCOPES
    mode: str                                  # SEARCH_MODES (the mode that ANSWERED: a fallback stores "keyword")
    project_id: Optional[uuid.UUID] = None     # None = searched in All Projects
    result_count: int
    counts_exact: bool = True
    ts: datetime


class RecentSearchesResponse(BaseModel):
    items: List[RecentSearchRead]              # newest first; ≤ RECENT_MAX; filtered to the reader's CURRENT projects
    max: int                                   # RECENT_MAX (20)
    retention_days: int                        # RECENT_TTL_DAYS (30)


class SearchSuggestion(BaseModel):
    kind: str                                  # SUGGESTION_KINDS: failing_test | open_defect | active_release
    label: str                                 # React text (a test name, a defect key, a release name)
    q: str                                     # the query this runs (built with quote_term(); ≤ MAX_QUERY_CHARS)
    scope: str                                 # SEARCH_SCOPES
    detail: Optional[str] = None               # "12 failures in 7 days" / "Open · HIGH" / "Active release"


class SearchSuggestionsResponse(BaseModel):
    project_id: uuid.UUID
    window_days: int                           # SUGGESTED_FAILING_DAYS (7)
    failing_tests: List[SearchSuggestion]      # ≤ SUGGESTED_MAX (5), most failures first (a top-N, not a count)
    open_defects: List[SearchSuggestion]       # ≤ 5, newest first
    active_release: Optional[SearchSuggestion] = None


# ── W12 SRCH-05: search usage ──

class SearchUsageRead(BaseModel):
    project_id: Optional[uuid.UUID] = None     # None = every project the caller can see (ADMIN: the deployment)
    days: int                                  # USAGE_DAYS: 1 (today, UTC) | 7 | 30
    since: datetime                            # the window's start (00:00 UTC today for days == 1)
    measured: bool                             # False when no row is in the window: every rate below is None
    searches: int
    zero_result_searches: int                  # result_count == 0 AND counts_exact (a search that lost a source is not a zero)
    zero_result_rate: Optional[float] = None   # 0..1 = zero_result_searches / searches; None when not measured
    p50_latency_ms: Optional[float] = None     # percentile_cont(0.5)
    p95_latency_ms: Optional[float] = None     # percentile_cont(0.95)
    fallback_searches: int = 0                 # semantic / hybrid requests that a keyword search answered
    retention_days: int                        # USAGE_RETENTION_DAYS (30)


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

#: A retired event value and the one that replaced it (W09 NTF-01). "Flaky test
#: detected" never had a producer; a preference holding it now holds "Test newly
#: flaky", which fires when a test enters the known-flaky set. Migration 0220
#: repeats the literal (migrations import no app code) and rewrites stored rows;
#: ``tests/test_migration_0220_flaky_preference_rewrite.py`` holds the two equal.
LEGACY_EVENT_ALIASES: Dict[str, str] = {"flaky_test_detected": "test.newly_flaky"}


def normalise_notification_events(values: List[str]) -> List[str]:
    """Map retired aliases to their event and drop duplicates, first occurrence kept, order kept.

    ``["flaky_test_detected", "run_failed", "test.newly_flaky"]`` ->
    ``["test.newly_flaky", "run_failed"]``.
    """
    out: List[str] = []
    seen: set = set()
    for value in values:
        mapped = LEGACY_EVENT_ALIASES.get(value, value)
        if mapped in seen:
            continue
        seen.add(mapped)
        out.append(mapped)
    return out


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

    @field_validator("events")
    @classmethod
    def _normalise_events(cls, v: List[str]) -> List[str]:
        # "flaky_test_detected" is stored as "test.newly_flaky"; duplicates dropped.
        return normalise_notification_events(v)


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
    # W09 NTF-01: an in-app path the bell may link to, set by the history route
    # from ``gate_notifications.app_link(row.delivery_metadata)``: one of the
    # allow-listed shapes (``/releases/<uuid>``, ``/release-gate/<uuid>``,
    # ``/test-management?case=<uuid>``), else None.
    link: Optional[str] = None

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


# ── Duration regressions (EXEC-05) ────────────────────────────────────────────
# ``GET /api/v1/runs/{run_id}/duration-regressions``: the stored
# ``test_runs.duration_findings`` (``services/duration_regression_service``).
# Vocabulary fields stay ``str`` -- a strict enum over a stored JSON value
# 422s silently when the producer grows a value.


class DurationSpikeResponse(BaseModel):
    """A PASSED test slower than its Welford baseline (>= 3 sigma, >= 10 samples)."""
    test_fingerprint: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    observed_ms: int
    baseline_mean_ms: float
    baseline_stddev_ms: float = 0.0
    baseline_p95_ms: float = 0.0
    sample_count: int = 0
    sigma: float
    delta_ms: Optional[int] = None
    ratio: Optional[float] = None
    previous_ms: Optional[int] = None
    #: The previous run of the test was already this slow (no notification).
    was_slow_before: bool = False
    new: bool = False


class SuiteDurationRegressionResponse(BaseModel):
    """A suite whose median over its last 5 runs is >= 1.5x the 20 before."""
    suite_name: Optional[str] = None
    suite_key: str
    metric: str = "summed"  # "wall" | "summed"
    ratio: Optional[float] = None
    recent_median_ms: Optional[float] = None
    baseline_median_ms: Optional[float] = None
    recent_runs: int = 0
    baseline_runs: int = 0
    observed_ms: Optional[int] = None
    was_slow_before: bool = False
    new: bool = False


class DurationRegressionsResponse(BaseModel):
    """A run's duration regressions. ``measured: false`` + ``reason`` when
    neither check could be made -- never an empty list read as "no
    regressions". ``stored: false`` = computed at read time for a run that
    was not evaluated on ingest (nothing was written)."""
    run_id: uuid.UUID
    schema_version: int = 1
    measured: bool
    reason: Optional[str] = None
    stored: bool = False
    evaluated_at: Optional[datetime] = None
    spikes: List[DurationSpikeResponse] = Field(default_factory=list)
    spike_count: int = 0
    suites: List[SuiteDurationRegressionResponse] = Field(default_factory=list)
    suite_regression_count: int = 0
    #: ``{measured, reason, considered, judged, history, required_history, sigma}``
    test_check: dict[str, Any] = Field(default_factory=dict)
    #: ``{measured, reason, evaluated, history, required_history, recent_runs, baseline_runs, ratio}``
    suite_check: dict[str, Any] = Field(default_factory=dict)
    #: Fingerprints / suite keys this run's "became slower" message named.
    notified: dict[str, Any] = Field(default_factory=dict)


# ── EXEC-03: execution metrics ──────────────────────────────────────────────
#
# GET /runs/{run_id}/timing, GET /analytics/execution/suite-history and
# GET /analytics/execution-metrics (``services/execution_metrics_service.py``).
# Units: every ``*_ms`` field is milliseconds, every ``*_at`` field and
# ``window_*`` bound an ISO 8601 UTC instant, every ``day`` a UTC
# ``YYYY-MM-DD``. ``null`` means NOT MEASURED (the report did not carry the
# figure, or nothing in scope has it), never zero. A block that could not be
# measured says so with ``measured: false`` and a human-readable ``reason``.
# Additive only from here on: other surfaces (the UI, the CLI, MCP) are built
# against these field names.


class ExecutionPercentiles(BaseModel):
    """p50 / p95 of one figure over a set of executions."""
    #: Executions that carried the figure (the sample behind the percentiles).
    n: int = 0
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    measured: bool = False
    reason: Optional[str] = None


class RunTimingFigures(BaseModel):
    """The run's own figures (EXEC-02 columns on ``test_runs``)."""
    #: How long the run took on the clock; null = not reported (never the sum).
    wall_clock_ms: Optional[int] = None
    #: Sum of the run's test durations ("Test time (summed)").
    summed_ms: Optional[int] = None
    #: summed_ms / wall_clock_ms, 2 places; null when the wall clock is unknown.
    parallelism: Optional[float] = None
    #: ``report`` | ``derived`` | ``derived_sum``; null = not timed yet.
    timing_source: Optional[str] = None
    exec_started_at: Optional[datetime] = None
    exec_finished_at: Optional[datetime] = None
    #: Tests in the run.
    test_count: int = 0
    #: Tests carrying both a start and a finish timestamp.
    timed_count: int = 0
    #: Tests carrying a duration.
    duration_count: int = 0


class RunTimingPhases(BaseModel):
    """Setup / test body / teardown totals over the run's tests."""
    measured: bool = False
    reason: Optional[str] = None
    #: Tests that reported at least one phase.
    tests_with_phases: int = 0
    setup_ms: Optional[int] = None
    call_ms: Optional[int] = None
    teardown_ms: Optional[int] = None
    #: setup_ms / (setup_ms + call_ms + teardown_ms), 0-1, 4 places.
    setup_share: Optional[float] = None
    teardown_share: Optional[float] = None


class SuiteExecutionResponse(BaseModel):
    """One ``suite_executions`` row: how long one suite of one run took."""
    suite_name: str
    suite_key: str
    #: ``report`` | ``derived`` | ``derived_sum`` (wall clock null for the last).
    source: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    wall_ms: Optional[int] = None
    summed_ms: Optional[int] = None
    #: Suite-level fixtures (before-all / after-all) as the report states them.
    setup_ms: Optional[int] = None
    teardown_ms: Optional[int] = None
    test_count: int = 0
    timed_count: int = 0
    passed: int = 0
    failed: int = 0
    broken: int = 0
    skipped: int = 0
    unknown: int = 0
    parallelism: Optional[float] = None


class RunTimingSuites(BaseModel):
    #: False when the run has no suite_executions rows yet (finalize or the
    #: backfill has not reached it); ``items`` is then empty for that reason.
    measured: bool = False
    reason: Optional[str] = None
    total: int = 0
    truncated: bool = False
    items: List[SuiteExecutionResponse] = Field(default_factory=list)


class TestDurationBaselineResponse(BaseModel):
    """A test's running duration statistics (``perf_baselines``)."""
    mean_ms: float
    stddev_ms: float = 0.0
    p95_ms: Optional[float] = None
    sample_count: int = 0
    last_observed_ms: Optional[int] = None
    last_observed_at: Optional[datetime] = None


class RunTimingTestResponse(BaseModel):
    """One test of the run, as the slowest list shows it."""
    test_id: uuid.UUID
    test_name: str
    test_fingerprint: Optional[str] = None
    #: The effective suite (a live-stream run's label, else the row's suite).
    suite_name: Optional[str] = None
    status: str
    duration_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    setup_ms: Optional[int] = None
    call_ms: Optional[int] = None
    teardown_ms: Optional[int] = None
    worker_id: Optional[str] = None
    #: ``report`` | ``derived`` | ``live``; null when the format carried no timing.
    timing_source: Optional[str] = None
    #: Null when the test has no baseline in this project.
    baseline: Optional[TestDurationBaselineResponse] = None
    #: duration_ms / baseline.mean_ms, 2 places; null without both.
    baseline_ratio: Optional[float] = None


class RunTimingSlowest(BaseModel):
    limit: int
    #: Tests carrying a duration (the population the list was cut from).
    total: int = 0
    measured: bool = False
    reason: Optional[str] = None
    items: List[RunTimingTestResponse] = Field(default_factory=list)


class RunTimingLane(BaseModel):
    """One worker lane: the timed tests one worker ran."""
    #: Null = the report did not say which worker ran these tests.
    worker_id: Optional[str] = None
    test_count: int = 0
    #: Sum of the lane's test durations.
    busy_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    span_ms: Optional[int] = None


class RunTimingWorkers(BaseModel):
    #: True when the report named the worker of at least one timed test.
    measured: bool = False
    reason: Optional[str] = None
    total: int = 0
    truncated: bool = False
    lanes: List[RunTimingLane] = Field(default_factory=list)


class RunTimelineItem(BaseModel):
    test_id: uuid.UUID
    test_name: str
    suite_name: Optional[str] = None
    status: str
    worker_id: Optional[str] = None
    started_at: datetime
    finished_at: datetime
    #: started_at - timeline.origin, in ms (>= 0).
    offset_ms: int
    duration_ms: Optional[int] = None


class RunTimeline(BaseModel):
    """Tests with a start and a finish, for the waterfall. When more than
    ``limit`` qualify, the ``limit`` LONGEST are kept (``truncated``)."""
    measured: bool = False
    #: e.g. "report carried no timestamps".
    reason: Optional[str] = None
    limit: int
    #: Tests carrying both timestamps (before the cut).
    total: int = 0
    truncated: bool = False
    #: The earliest start of any timed test; offsets are measured from here.
    origin: Optional[datetime] = None
    #: The latest finish of any timed test.
    end: Optional[datetime] = None
    #: Ordered by started_at, then test_id.
    items: List[RunTimelineItem] = Field(default_factory=list)


class RunTimingResponse(BaseModel):
    """GET /api/v1/runs/{run_id}/timing."""
    run_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    build_number: Optional[str] = None
    branch: Optional[str] = None
    created_at: Optional[datetime] = None
    run: RunTimingFigures
    phases: RunTimingPhases
    suites: RunTimingSuites
    slowest: RunTimingSlowest
    workers: RunTimingWorkers
    timeline: RunTimeline
    #: The run's duration regressions (same body as /duration-regressions).
    duration_findings: Optional[DurationRegressionsResponse] = None


class SuiteHistoryPoint(BaseModel):
    """One run of the suite."""
    run_id: uuid.UUID
    build_number: Optional[str] = None
    run_created_at: datetime
    branch: Optional[str] = None
    run_status: Optional[str] = None
    source: str
    wall_ms: Optional[int] = None
    summed_ms: Optional[int] = None
    parallelism: Optional[float] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    test_count: int = 0
    timed_count: int = 0
    passed: int = 0
    failed: int = 0
    broken: int = 0
    skipped: int = 0
    unknown: int = 0


class SuiteHistoryStats(BaseModel):
    """Window percentiles over EVERY execution in the window (not only the
    returned points)."""
    wall: ExecutionPercentiles = Field(default_factory=ExecutionPercentiles)
    summed: ExecutionPercentiles = Field(default_factory=ExecutionPercentiles)
    parallelism_p50: Optional[float] = None


class SuiteExecutionHistoryResponse(BaseModel):
    """GET /api/v1/analytics/execution/suite-history."""
    project_id: Optional[uuid.UUID] = None
    #: The suite's ingested spelling (latest row), else the name as sent.
    suite_name: Optional[str] = None
    suite_key: Optional[str] = None
    #: The branch filter applied; null = every branch.
    branch: Optional[str] = None
    days: int
    limit: int
    #: Executions of the suite in the window (before the limit).
    total: int = 0
    truncated: bool = False
    measured: bool = False
    reason: Optional[str] = None
    #: Oldest first (a time series); the newest ``limit`` when truncated.
    points: List[SuiteHistoryPoint] = Field(default_factory=list)
    stats: SuiteHistoryStats = Field(default_factory=SuiteHistoryStats)
    meta: dict[str, Any] = Field(default_factory=dict)


class ExecutionDayPoint(BaseModel):
    day: str
    runs: int = 0
    #: Sum of the wall clocks of the day's runs that have one.
    wall_clock_ms: Optional[int] = None
    wall_runs: int = 0
    summed_ms: Optional[int] = None
    summed_runs: int = 0


class ExecutionPerDay(BaseModel):
    #: ``run`` (test_runs wall clock + summed time) or ``suite`` (when a
    #: suite_name filter is applied: the selected suites' suite_executions rows).
    grain: str = "run"
    #: Every UTC day of the window, oldest first; a day with no runs has
    #: runs 0 and null figures.
    points: List[ExecutionDayPoint] = Field(default_factory=list)


class ExecutionTotals(BaseModel):
    runs: int = 0
    wall_clock_ms: Optional[int] = None
    wall_runs: int = 0
    summed_ms: Optional[int] = None
    summed_runs: int = 0
    #: Over the runs carrying BOTH figures: their summed / their wall clock.
    parallelism: Optional[float] = None


class ExecutionSuiteWindow(BaseModel):
    runs: int = 0
    wall: ExecutionPercentiles = Field(default_factory=ExecutionPercentiles)
    summed: ExecutionPercentiles = Field(default_factory=ExecutionPercentiles)


class ExecutionSuiteRow(BaseModel):
    suite_key: str
    suite_name: str
    current: ExecutionSuiteWindow
    #: The window of the same length just before this one.
    previous: ExecutionSuiteWindow
    #: (current - previous) / previous x 100, 1 place; null without both.
    wall_p50_change_pct: Optional[float] = None
    wall_p95_change_pct: Optional[float] = None
    summed_p50_change_pct: Optional[float] = None
    summed_p95_change_pct: Optional[float] = None


class ExecutionSuites(BaseModel):
    limit: int
    #: Suites that ran in the window (before the limit).
    total: int = 0
    truncated: bool = False
    #: Ordered by current wall p95, then summed p95, descending.
    items: List[ExecutionSuiteRow] = Field(default_factory=list)


class ExecutionSuiteRegression(BaseModel):
    suite_key: str
    suite_name: Optional[str] = None
    branch: Optional[str] = None
    #: ``wall`` | ``summed`` (wall only when every run of the window has one).
    metric: str
    ratio: float
    recent_median_ms: Optional[float] = None
    baseline_median_ms: Optional[float] = None
    recent_runs: int = 0
    baseline_runs: int = 0
    observed_ms: Optional[int] = None
    #: The previous run was already this slow (the regression is not new).
    was_slow_before: bool = False
    latest_run_id: Optional[uuid.UUID] = None
    latest_run_at: Optional[datetime] = None


class ExecutionSuiteRegressions(BaseModel):
    measured: bool = False
    reason: Optional[str] = None
    #: Suite x branch histories judged (enough history).
    evaluated: int = 0
    #: Suite x branch histories considered (ran in the window).
    considered: int = 0
    total: int = 0
    items: List[ExecutionSuiteRegression] = Field(default_factory=list)


class ExecutionTestRegression(BaseModel):
    test_fingerprint: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    baseline_mean_ms: float
    baseline_p95_ms: Optional[float] = None
    sample_count: int = 0
    window_p50_ms: float
    window_n: int = 0
    #: window_p50_ms / baseline_mean_ms, 2 places.
    ratio: float


class ExecutionTestRegressions(BaseModel):
    measured: bool = False
    reason: Optional[str] = None
    #: Tests with a baseline of >= min_samples observed in the window, judged.
    considered: int = 0
    total: int = 0
    items: List[ExecutionTestRegression] = Field(default_factory=list)


class ExecutionRegressionRule(BaseModel):
    recent_runs: int
    baseline_runs: int
    min_baseline_runs: int
    ratio: float
    same_branch: bool = True
    test_min_samples: int
    test_ratio: float


class ExecutionRegressions(BaseModel):
    rule: ExecutionRegressionRule
    suites: ExecutionSuiteRegressions
    tests: ExecutionTestRegressions


class ExecutionSlowTest(BaseModel):
    test_fingerprint: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    #: Executions in the window carrying a duration.
    n: int = 0
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[int] = None


class ExecutionSlowTests(BaseModel):
    limit: int
    measured: bool = False
    reason: Optional[str] = None
    #: Ordered by p95 descending (the slowest first, not the most executed).
    items: List[ExecutionSlowTest] = Field(default_factory=list)


class ExecutionHistogramBucket(BaseModel):
    """One duration bucket: ``lower_ms <= duration < upper_ms``."""
    lower_ms: int
    #: Null = the open-ended top bucket.
    upper_ms: Optional[int] = None
    count: int = 0


class ExecutionDurationHistogram(BaseModel):
    """TRND-09: the window's test executions by duration, over fixed
    log-spaced bucket edges (every bucket present, zero-filled)."""
    measured: bool = False
    reason: Optional[str] = None
    #: Test executions in scope carrying a duration.
    n: int = 0
    buckets: List[ExecutionHistogramBucket] = Field(default_factory=list)
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None


class ExecutionMetricsResponse(BaseModel):
    """GET /api/v1/analytics/execution-metrics."""
    project_id: Optional[uuid.UUID] = None
    days: int
    branch: Optional[str] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    previous_window_start: Optional[datetime] = None
    measured: bool = False
    reason: Optional[str] = None
    totals: ExecutionTotals = Field(default_factory=ExecutionTotals)
    per_day: ExecutionPerDay = Field(default_factory=ExecutionPerDay)
    suites: ExecutionSuites
    regressions: ExecutionRegressions
    slowest_tests: ExecutionSlowTests
    duration_histogram: ExecutionDurationHistogram = Field(default_factory=ExecutionDurationHistogram)
    meta: dict[str, Any] = Field(default_factory=dict)


# ── Agent Pipeline Schemas ─────────────────────────────────────

class TriggerPipelineRequest(BaseModel):
    test_run_id: uuid.UUID
    workflow_id: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=80,
        pattern=r"^(?:wf\.[a-z0-9_.-]+|offline|deep|live)$",
    )
    workflow_version: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def version_requires_workflow(self) -> "TriggerPipelineRequest":
        if self.workflow_version is not None and self.workflow_id is None:
            raise ValueError("workflow_version requires workflow_id")
        return self


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


class AgentPipelineReviewSummary(BaseModel):
    """Minimal review projection for a pipeline card.

    Reviewer identity deliberately stays out of this API response.  A settled
    timestamp is enough for the status card to distinguish an accepted report
    from a non-report pipeline that passed without human review.
    """

    state: Literal["pending_review", "accepted", "rejected"]
    settled_at: Optional[datetime] = None


class AgentPipelineResponse(BaseModel):
    id: uuid.UUID
    test_run_id: uuid.UUID
    workflow_type: str
    status: str
    # E7.1: four-value public projection of ``status``
    # (in_progress | completed | failed | passed). ``status`` keeps the internal
    # vocabulary for one release so existing clients are not broken.
    public_status: Optional[str] = None
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
    # Retry/cancel state (E7.2, E7.4). ``attempt``/``max_attempts`` let a client
    # show "attempt 3 of 5" and grey out retry at the ceiling instead of
    # discovering it via a 409; ``rerun_of`` links a fresh run to the one it
    # replaces, and ``cancel_requested`` distinguishes "still running" from
    # "running, but stopping".
    attempt: Optional[int] = None
    max_attempts: Optional[int] = None
    next_retry_at: Optional[Any] = None
    cancel_requested: Optional[bool] = None
    rerun_of: Optional[uuid.UUID] = None
    # T20/K3: the live report review for this pipeline.  ``None`` means the
    # pipeline has no report review (for example a deterministic non-report
    # invocation); it must not be presented as human-reviewed.
    review_summary: Optional[AgentPipelineReviewSummary] = None

    model_config = ConfigDict(from_attributes=True)


class ReviewBlock(BaseModel):
    """Human-review status of an AI report (architecture section 8.2, E8.3).

    Carries WHETHER and WHEN a report was reviewed, never WHO: reviewer identity
    stays in-app, out of API and export payloads.
    """

    state: Literal["pending_review", "accepted", "rejected", "superseded", "not_applicable"] = Field(
        description=(
            "pending_review: a draft until a human accepts it (also used when no review "
            "was ever recorded). not_applicable: the content was not AI-generated."
        )
    )
    message: str
    review_id: Optional[str] = None
    reviewed_at: Optional[str] = None


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
    # E8.3: every AI report response says whether a human has reviewed it.
    requires_human_review: bool = Field(
        default=True,
        description="True for AI-generated content: a draft until review.state == accepted.",
    )
    review: Optional[ReviewBlock] = None
    ai_disclaimer: Optional[str] = None
    ai_disclaimer_version: Optional[str] = None


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
    # W14 AGT-05 / AGT-06 / AGT-08: the run's frozen workflow and how it was chosen, both read from
    # execution_metadata by the route (no statement); None for a legacy row.
    workflow_ref: Optional[str] = None             # execution_metadata.workflow_ref ("offline@1", "wf.my-fork@1")
    workflow_source: Optional[str] = None          # execution_metadata.workflow_default_source (WORKFLOW_SOURCES)


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


# ── W14 AGT-05: pipeline compare (enum-like fields are plain str; vocab in core/agent_ops_vocab.py) ──
class PipelineCompareStageCell(BaseModel):
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None       # None unless BOTH ends are stored (never 0 for "unknown")
    total_tokens: Optional[int] = None             # the stage row's value; None = not reported
    cost_usd: Optional[float] = None               # None = not reported (a reported 0.0 stays 0.0)
    fallback_used: Optional[bool] = None
    confidence_score: Optional[float] = None
    execution_path: Optional[str] = None
    output_checksum_sha256: Optional[str] = None   # result_data["_replay"]["output_checksum_sha256"] (the replay rule)


class PipelineCompareStageRow(BaseModel):
    stage_name: str
    a: Optional[PipelineCompareStageCell] = None   # None = pipeline a has no row for this stage
    b: Optional[PipelineCompareStageCell] = None
    changed: List[str] = Field(default_factory=list)   # COMPARE_FIELDS that differ (both sides known); ["presence"] if a side is None
    output_checksum_equal: Optional[bool] = None   # None when either checksum is missing: unknown, not "changed"
    duration_delta_seconds: Optional[float] = None # b − a, only when both are known (same for the two below)
    tokens_delta: Optional[int] = None
    cost_delta_usd: Optional[float] = None


class PipelineCompareSide(BaseModel):
    pipeline_run_id: uuid.UUID
    test_run_id: uuid.UUID
    workflow_type: str
    workflow_ref: Optional[str] = None             # execution_metadata.workflow_ref; None for a legacy row
    status: str
    public_status: str
    attempt: Optional[int] = None
    rerun_of: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    analysis_mode_resolved: Optional[str] = None   # execution_metadata.analysis_mode_resolved (the replay's field)
    release_verdict: Optional[str] = None          # the release_risk stage's result_data.recommendation; None when absent
    total_tokens: Optional[int] = None             # sum over its stages that REPORTED tokens; None when none did
    cost_usd: Optional[float] = None               # same rule
    stages_truncated: bool = False                 # more than MAX_COMPARE_STAGES stage rows


class PipelineCompareResponse(BaseModel):
    a: PipelineCompareSide
    b: PipelineCompareSide
    same_test_run: bool
    output: str                                    # COMPARE_OUTPUT_STATES: identical | changed | unknown
    changed_stages: int                            # rows with a non-empty `changed`
    total: int                                     # len(stages): every row, no paging
    stages: List[PipelineCompareStageRow]          # a's stage order (started_at, stage_name), then b-only rows in b's order


# ── W14 AGT-08: project default workflows ──
class WorkflowDefaultUpdate(BaseModel):            # PUT body
    workflow_id: str = Field(min_length=3, max_length=80, pattern=r"^wf\.[a-z0-9_.-]+$")
    workflow_version: Optional[int] = Field(default=None, ge=1)   # None = always the latest PUBLISHED version


class WorkflowDefaultRead(BaseModel):
    base: str                                      # WORKFLOW_DEFAULT_BASES
    source: str                                    # "project_default" | "builtin"
    workflow_id: str                               # the fork's id, or the base itself for "builtin"
    workflow_version: Optional[int] = None         # the stored pin; None = latest published (or the built-in)
    resolved_ref: Optional[str] = None             # what a run started NOW executes, e.g. "wf.my-fork@3"; None if unavailable
    resolved_name: Optional[str] = None
    state: str                                     # "ok" | "unavailable"
    reason: Optional[str] = None                   # a DEFAULT_UNAVAILABLE_* sentence when unavailable
    model_stages: List[str] = Field(default_factory=list)   # steps whose project-config tier is not "deterministic"
    updated_by: Optional[str] = None               # full name, else username; None for "builtin"
    updated_at: Optional[datetime] = None


class WorkflowDefaultsResponse(BaseModel):
    project_id: uuid.UUID
    defaults: List[WorkflowDefaultRead]            # ALWAYS both bases, in WORKFLOW_DEFAULT_BASES order
# AgentPipelineRun.execution_metadata gains (written in _create_pipeline_run, read through existing responses):
#   "workflow_default_source": str (WORKFLOW_SOURCES), "workflow_default_reason": Optional[str]


# ── W14 AGT-12: bulk jobs (BulkTriggerRequest / BulkTriggerResponse stay in routers/agents.py and grow there) ──
class BulkRunFilter(BaseModel):                    # the Runs list's own filter, resolved on the server
    project_id: Optional[uuid.UUID] = None         # None = every project the caller can access (All Projects)
    release_id: Optional[List[str]] = Field(default=None, max_length=20)
    days: Optional[int] = Field(default=30, ge=0, le=365)     # 0 = all time (the list's rule)
    status: Optional[str] = Field(default=None, max_length=20)
    suite_name: Optional[List[str]] = None
    q: Optional[str] = Field(default=None, max_length=100)
    branch: Optional[List[str]] = None
    recommendation: Optional[str] = None


class BulkJobProgress(BaseModel):
    started: int                                   # pipeline rows stamped with the job
    in_progress: int
    completed: int
    passed: int
    failed: int
    not_started: Optional[int] = None              # queued − started (≥ 0); None when `queued` is unknown


class BulkJobRead(BaseModel):
    bulk_job_id: str
    workflow_type: str
    source: str                                    # BULK_SOURCES: selection | filter
    state: str                                     # BULK_JOB_STATES: active | finished | cancelled
    counters_source: str                           # "redis" (the request record exists) | "postgres" (it expired / was flushed)
    created_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    requested: Optional[int] = None                # None when counters_source == "postgres"
    queued: Optional[int] = None
    skipped_in_progress: Optional[int] = None
    not_found: Optional[int] = None
    matched: Optional[int] = None                  # filter source: COUNT of matching runs
    truncated: bool = False                        # the filter matched more than BULK_FILTER_CAP
    progress: BulkJobProgress
    note: Optional[str] = None                     # BULK_NOT_STARTED_NOTE when finished with not_started > 0
# AgentPipelineRun.execution_metadata gains "bulk_job_id": str (only on a bulk-queued run)


# ── W14 AGT-07: deep investigation insights ──
class PreScanGroup(BaseModel):
    signature_id: str                              # failure_signature.signature_bucket_id(sig) ("__NO_MESSAGE__" for none)
    representative_error: Optional[str] = None     # min(error_message) of the group, first line, ≤ 300 characters
    members: int                                   # failing results in the group (COUNT)
    member_result_ids: List[uuid.UUID]             # ≤ PRESCAN_MEMBERS_CAP, ordered by id (what "Run on selection" sends)
    members_truncated: bool = False
    suites: List[str] = Field(default_factory=list)   # ≤ 5 distinct effective suite names
    cluster_id: Optional[str] = None               # refinement: the current cluster holding the most of its members
    cluster_label: Optional[str] = None


class DeepPreScanResponse(BaseModel):
    run_id: uuid.UUID
    measured: bool                                 # False when the run has no failing (failed / broken) result
    failing_results: int
    groups_total: int                              # every signature group (COUNT), never len(groups)
    groups: List[PreScanGroup]                     # ≤ limit, by members desc then signature_id
    grouped_by: str = "error_signature"
    refinement: str                                # PRESCAN_REFINEMENTS: clusters | signature_only
    refinement_note: str                           # PRESCAN_NOTE_CLUSTERS / PRESCAN_NOTE_SIGNATURE_ONLY


class DeepHistoryRow(BaseModel):
    run_id: uuid.UUID
    run_seq: Optional[int] = None
    build_number: Optional[str] = None
    suite_name: Optional[str] = None
    run_created_at: Optional[datetime] = None
    failures: int                                  # the run's failed + broken counts (its own columns)
    pipeline_run_id: uuid.UUID                     # the run's LATEST deep pipeline
    status: str
    public_status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    clusters: Optional[int] = None                 # the current cluster snapshot's rows; None = no snapshot
    findings: int = 0
    avg_confidence: Optional[float] = None         # 0..1 (a 0-100 producer divided by 100); None = no scored finding
    cost_usd: Optional[float] = None               # sum of that pipeline's stage cost_usd; None when no stage reported one


class DeepHistoryResponse(BaseModel):
    project_id: uuid.UUID
    total: int                                     # runs with ≥ 1 deep pipeline (COUNT)
    page: int
    size: int
    items: List[DeepHistoryRow]


class DeepGateRead(BaseModel):                     # services/deep_investigation_gate.DeepGateDecision, as data
    allowed: bool
    reason_code: Optional[str] = None              # "disabled" | "rules_mode" | None
    message: Optional[str] = None                  # the gate's own sentence (MESSAGE_DISABLED / MESSAGE_RULES_MODE)
    analysis_mode: str                             # the RESOLVED mode
    deep_investigation_enabled: bool


class DeepRunConfigurationRead(BaseModel):
    selected_failures: Optional[int] = None        # None = every failing result of the run
    failures_analysed: int                         # min(selected or failing_results, max_failures_analyzed)
    max_failures_analyzed: int                     # effective (request ≤ project)
    project_max_failures_analyzed: int
    model_tier: str                                # effective tier (TIER_ORDER value)
    project_model_tier: str
    allowed_tiers: List[str]                       # the project's tier and every one below it; [project tier] when downgrade is off
    evidence_sources: List[str]                    # effective tool allowlist of RUN_CONFIG_AGENT_ID
    project_evidence_sources: List[str]
    tool_narrowing_allowed: bool                   # override_policy.allow_tool_narrowing


class DeepCostRead(BaseModel):
    priced: bool                                   # False: no price known (usd None) — never a quiet $0.00
    local_model: bool                              # a SELF_HOSTED_PROVIDERS endpoint: usd 0.0 is a measured answer
    usd: Optional[float] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    note: Optional[str] = None                     # ESTIMATE_LOCAL_NOTE / ESTIMATE_UNPRICED / None
    price_table_updated: str                       # llm_pricing.PRICE_TABLE_UPDATED


class DeepEstimateStage(BaseModel):
    stage_name: str
    samples: int                                   # sample pipelines in which this stage has both timestamps
    duration_seconds_p50: Optional[float] = None
    input_tokens_p50: Optional[int] = None
    output_tokens_p50: Optional[int] = None
    per_failure: bool = False                      # scaled by failures_analysed (PER_FAILURE_STAGES)


class DeepEstimateResponse(BaseModel):
    run_id: uuid.UUID
    gate: DeepGateRead
    configuration: DeepRunConfigurationRead
    measured: bool                                 # False with no completed deep pipeline in the project
    reason: Optional[str] = None                   # ESTIMATE_UNMEASURED when not measured
    sample_pipelines: int                          # ≤ ESTIMATE_SAMPLE_PIPELINES
    minutes_p50: Optional[float] = None            # None when not measured (never 0)
    tokens_p50: Optional[int] = None
    cost: DeepCostRead
    stages: List[DeepEstimateStage]
# routers/deep_investigation.py grows TriggerDeepRequest / TriggerDeepResponse in place (§1.1 of the W14 contract).
# AgentPipelineRun.execution_metadata gains "run_overrides", "run_overrides_status", "run_overrides_reasons".


class PipelineEventLogHealthResponse(BaseModel):
    status: str = "healthy"
    write_failure_count: int = 0
    dead_letter_count: int = 0
    dead_letter_limit: int = 0
    recent_dead_letters: List[Dict[str, Any]] = Field(default_factory=list)


# ── W15 AGT-01: the agent-action queue (enum-like fields are plain str; vocab in services/agent_action_types.py) ──
class AgentActionTarget(BaseModel):                # what the row acts on — built server-side from TYPED payload fields only
    kind: str                                      # TARGET_KINDS: test | test_case | cluster | release_item
    id: Optional[str] = None                       # fingerprint / test case id / cluster id / decision action id
    label: Optional[str] = None                    # test name / cluster label / the action id — run data, rendered as TEXT
    href: Optional[str] = None                     # target_href(...) from ids only; None when no page shows it


class AgentActionResultLink(BaseModel):            # where an EXECUTED action's effect can be seen
    kind: str                                      # RESULT_KINDS: quarantine | defect | assignee
    id: Optional[str] = None
    label: Optional[str] = None                    # "quarantined" | the assignee's name | "defect created"
    href: Optional[str] = None                     # /flaky?tab=quarantined | /defects?defect=<id> | /runs/<run>?tab=tests


class AgentActionRead(BaseModel):
    # the twenty keys `_action_response` returns today (routers/agent_actions.py), names and types unchanged:
    id: str
    project_id: str
    test_run_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    action_type: str
    target_type: str
    target_id: Optional[str] = None
    status: str
    approval_required: bool
    idempotency_key: str
    request_sha256: str
    request_payload: dict
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    execution_started_at: Optional[str] = None
    execution_completed_at: Optional[str] = None
    result_payload: Optional[dict] = None          # NEVER carries result_payload["rejection"]["notes"] (stripped)
    rollback_payload: Optional[dict] = None
    error_code: Optional[str] = None
    created_at: Optional[str] = None
    # NEW (W15), every one defaulted:
    canonical_type: Optional[str] = None           # the catalogue type after ACTION_TYPE_ALIASES; None = unregistered
    title: Optional[str] = None                    # request_payload["title"], ≤ 200 — display only
    rationale: Optional[str] = None                # request_payload["rationale"], ≤ 500 — display only
    risk: Optional[str] = None                     # RISK_LEVELS, else None
    side_effect: Optional[str] = None              # SIDE_EFFECTS of the type; None = unregistered
    reversible: bool = False
    proposing_agent_id: Optional[str] = None
    proposing_agent_label: Optional[str] = None    # PROPOSER_LABELS ("Investigator", "Decision report"), else the id
    proposing_review_state: str = "none"           # PROPOSING_REVIEW_STATES
    review_id: Optional[str] = None                # the proposing run's newest report review (a link into AI reports)
    test_run_build_number: Optional[str] = None
    test_run_branch: Optional[str] = None
    pipeline_workflow_type: Optional[str] = None   # "investigation" | "deep" | …
    approved_by_name: Optional[str] = None         # full name, else username (the decider of approve / reject)
    target: Optional[AgentActionTarget] = None
    outcome: str = "unknown"                       # OUTCOME_CODES, from status + error_code + type
    outcome_text: str = ""                         # OUTCOME_SENTENCES[outcome], formatted server-side
    approvable: bool = False                       # pending AND review accepted (AI rows) AND (no effect OR act + type allowed)
    approve_blocked_reason: Optional[str] = None   # the sentence when a pending row is not approvable
    result_link: Optional[AgentActionResultLink] = None   # executed rows only
    rejection_reason_code: Optional[str] = None    # result_payload["rejection"]["reason_code"]


class AgentActionListResponse(BaseModel):
    items: List[AgentActionRead]
    count: int                                     # len(items) — the EXISTING key, its meaning unchanged
    total: int                                     # COUNT(*) OVER () of the filtered set — the number the UI shows


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
    # DEF-06 (approve only): what happened to the Jira ticket. Plain strings
    # (the vocabulary is the approve path's, a drifted word must not 422):
    # jira_status "filed" | "linked" | "not_filed" | "failed" | "unknown" | "not_requested";
    # jira_reason (not_filed) "offline_mode" | "disabled" | "not_configured";
    # jira_error (failed / unknown) the server's detail text.
    jira_status: Optional[str] = None
    jira_reason: Optional[str] = None
    jira_error: Optional[str] = None


# ── Defect triage (DEF-01) and the defect approval queue (DEF-06) ─────────
# Enum-like fields are plain ``str``; the vocabularies live in ONE module
# (``app.services.defect_status``) and requests are validated in the service
# with one fixed 422 text each.

class DefectPatchRequest(BaseModel):
    """PATCH /api/v1/projects/{project_id}/defects/{defect_id}.

    Only the fields SENT are applied (``model_fields_set``); ``null`` or ``""``
    clears ``assignee_user_id``, ``owner_team`` and ``jira_key``. No field takes
    a URL: a Jira link is built by the server from the configured domain, and
    ``extra="forbid"`` makes a client-sent ``jira_url`` a 422, never stored.
    """
    model_config = ConfigDict(extra="forbid")

    resolution_status: Optional[str] = Field(None, max_length=20)
    assignee_user_id: Optional[str] = Field(None, max_length=64)
    owner_team: Optional[str] = Field(None, max_length=255)
    jira_key: Optional[str] = Field(None, max_length=50)
    # Recorded in the activity-ledger row's context, never on the defect.
    note: Optional[str] = Field(None, max_length=1000)


class DefectJiraLinkResult(BaseModel):
    """What linking a Jira key did. ``status``: "validated" | "saved_locally" | "unlinked"."""
    status: str
    # saved_locally: "offline_mode" | "disabled" | "not_configured"
    reason: Optional[str] = None
    jira_key: Optional[str] = None
    # Server-built (``https://{domain}/browse/{KEY}``) or None.
    jira_url: Optional[str] = None
    # Jira's issue summary when validated: plain text, cut to 255 characters by
    # the triage service (no max_length here: a long summary must not 500 a
    # PATCH that has already committed).
    summary: Optional[str] = None


class DefectWriteBackResult(BaseModel):
    """The Jira status write-back after a status change. ``status``: "sent" | "skipped" | "not_sent" | "failed"."""
    status: str
    # skipped: "already_in_status" | "no_transition"; not_sent: an availability
    # reason; failed: "HTTP 4xx" | "<ExceptionClass>".
    reason: Optional[str] = None


class DefectPatchResponse(BaseModel):
    """The PATCH answer: the defect's list row as the server now reads it (the
    ``GET /analytics/defects`` row shape), so the page replaces its row."""
    defect: Dict[str, Any]
    changed_fields: List[str]
    jira: Optional[DefectJiraLinkResult] = None
    write_back: Optional[DefectWriteBackResult] = None


class PendingDefectItem(BaseModel):
    """One defect awaiting approval (GET /deep-investigate/defects/pending-review)."""
    defect_id: str
    project_id: str
    title: Optional[str] = None
    severity: Optional[str] = None
    component: Optional[str] = None
    owner_team: Optional[str] = None
    created_at: Optional[str] = None
    policy_reasons: List[str] = Field(default_factory=list)
    # policy_evaluation["jira_project_key"] or ["requested_project_key"]
    jira_project_key: Optional[str] = None
    # policy_evaluation["source_run_id"] or ["requested_run_id"]
    run_id: Optional[str] = None
    cluster_id: Optional[str] = None
    ai_confidence_score: Optional[int] = None


class PendingDefectList(BaseModel):
    """A page of the approval queue. ``total`` is a COUNT over the same scope, not ``len(items)``."""
    items: List[PendingDefectItem]
    total: int
    page: int
    page_size: int


# ── One-click Jira defects (PMF US-6.1 / US-6.2 / US-6.3) ─────────────────
# All status-ish fields are plain strings, not enums — they mirror String
# columns / external Jira vocabulary and a strict enum here would silently
# 422 the whole response the moment a stored value drifts.

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
    # Set only after a 409 ``jira_outcome_unknown``: the user checked Jira,
    # the issue an earlier request may have filed is not there, file it.
    # Ignored unless an earlier attempt's outcome is actually unknown.
    confirm_not_filed: bool = False


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


class StreamMessageRequest(BaseModel):
    """One streamed chat turn: a new question, or ``retry`` to answer the
    session's unanswered (or stopped) last question again."""

    message: Optional[str] = Field(None, min_length=1, max_length=4000)
    retry: bool = False

    @model_validator(mode="after")
    def _question_or_retry(self) -> "StreamMessageRequest":
        if self.retry == bool(self.message and self.message.strip()):
            raise ValueError("send either a message or retry=true, not both or neither")
        return self


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
    """Authored step shape; expected outcomes are optional by design."""
    step_number: int = Field(..., ge=1)
    action: str = Field(..., min_length=1, max_length=MAX_LONG_TEXT)
    expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)


class TestCaseParameterSchema(BaseModel):
    """Optional authored input metadata; sensitive values are never required."""
    name: str = Field(..., min_length=1, max_length=255)
    value: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    mode: Optional[str] = Field(None, max_length=30)
    masked: bool = False


class ManagedTestCaseCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    steps: Optional[List[TestCaseStepSchema]] = None
    parameters: Optional[List[TestCaseParameterSchema]] = None
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
    # The New Test Case form's Assignee: an active member of the project, or
    # an admin. Without the field the choice was dropped silently.
    assignee_id: Optional[uuid.UUID] = None


class ManagedTestCaseUpdate(BaseModel):
    expected_version: int = Field(..., ge=1)
    title: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    steps: Optional[List[TestCaseStepSchema]] = None
    parameters: Optional[List[TestCaseParameterSchema]] = None
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
    # W28 TCM-01: the Owner, validated like create's (an active member of the
    # case's project, or an admin). Omitted = keep; an explicit null = unassign
    # (exclude_unset tells them apart).
    assignee_id: Optional[uuid.UUID] = None


class ManagedTestCaseResponse(BaseModel):
    # Defaults to "managed" for rows backed by the ``managed_test_cases``
    # table; ``"automation"`` for synthesised rows derived from per-run
    # ``test_cases`` (returned by /cases when ``include_automation=true``).
    # The frontend uses this to render an "Automation-ingested" badge and
    # disable edit affordances on automation rows.
    source: str = "managed"
    # Automation-backed rows carry execution metadata from their latest run, and
    # so does an authored case linked to automation (same project + fingerprint).
    # Every case opens the same Test Management panel; these ids are its "Open
    # latest result" (run detail) and "Run history" (canonical page) links. They
    # stay null for an authored case with no automation behind it. An authored
    # case's ``owner`` is the display name of the assignee, else the author.
    owner: Optional[str] = None
    latest_run_id: Optional[uuid.UUID] = None
    latest_test_case_id: Optional[uuid.UUID] = None
    canonical_test_case_id: Optional[uuid.UUID] = None
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: Optional[str] = None
    objective: Optional[str] = None
    preconditions: Optional[str] = None
    steps: Optional[List[TestCaseStepSchema]] = None
    parameters: Optional[List[TestCaseParameterSchema]] = None
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
    allowed_actions: List[str] = Field(default_factory=list)
    lifecycle_state_changed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by_id: Optional[uuid.UUID] = None
    needs_update_reason: Optional[str] = None
    deprecation_reason: Optional[str] = None
    deprecated_at: Optional[datetime] = None
    deprecated_by_id: Optional[uuid.UUID] = None
    archived_at: Optional[datetime] = None
    archived_by_id: Optional[uuid.UUID] = None
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
    # W28 (TCM-01 / TCM-02). Automation rows (dicts) get the defaults.
    # ``parameters`` above: a masked row (masked true, or mode masked|hidden)
    # reads value = None (services/test_case_parameters.mask_parameters).
    review_required: bool = False                       # the project's require_case_review
    reason_required_actions: List[str] = Field(default_factory=list)   # the subset of allowed_actions that needs a reason FOR THIS ACTOR
    edited_since_approval_version: Optional[int] = None # the raw marker (migration 0222)
    edited_after_approval: bool = False                 # review_required and status in (approved, active) and the marker is set

    model_config = ConfigDict(from_attributes=True)

    # W13 (a pre-existing 500, made prominent by the TCL-01 automation filter):
    # ``managed_test_cases.is_automated`` / ``ai_generated`` / ``automation_status``
    # are NULLABLE columns (migration 0007: a server default, no NOT NULL), so a
    # row written without them (raw SQL, an old writer) holds NULL -- and one such
    # row on a page failed this response's validation: the whole list answered 500.
    # A NULL reads as the column's own default, the value an INSERT would have
    # stored, and exactly what the server's filters already assume:
    # ``automation=not_automated`` is ``coalesce(is_automated, false) = false`` and
    # the library health counts ``is_automated IS TRUE`` as automated. The wire
    # type stays ``bool`` / ``str`` (the SPA's ``ManagedTestCase`` is unchanged).
    _NULL_COLUMN_DEFAULTS: ClassVar[dict[str, Any]] = {
        "is_automated": False, "ai_generated": False, "automation_status": "not_automated",
    }

    @field_validator("is_automated", "ai_generated", "automation_status", mode="before")
    @classmethod
    def _null_column_reads_as_its_default(cls, value: Any, info: Any) -> Any:
        return cls._NULL_COLUMN_DEFAULTS[info.field_name] if value is None else value


class ManagedTestCaseListResponse(BaseModel):
    items: List[ManagedTestCaseResponse]
    total: int
    page: int
    size: int
    pages: int
    # W13 TCL-01: include_automation was asked and the authored-only path
    # answered (a filter the merged automation path cannot apply was set).
    automation_rows_excluded: bool = False


class TestCaseVersionExecution(BaseModel):
    """A plan execution recorded against one case version (W28 TCM-05)."""
    plan_id: uuid.UUID
    plan_name: str
    item_id: uuid.UUID
    execution_status: str
    executed_at: Optional[datetime] = None


class TestCaseVersionResponse(BaseModel):
    id: uuid.UUID
    test_case_id: uuid.UUID
    version: int
    title: str
    description: Optional[str] = None
    objective: Optional[str] = None
    preconditions: Optional[str] = None
    steps: Optional[List[TestCaseStepSchema]] = None
    parameters: Optional[List[TestCaseParameterSchema]] = None
    expected_result: Optional[str] = None
    test_data: Optional[str] = None
    test_type: Optional[str] = None
    priority: Optional[str] = None
    severity: Optional[str] = None
    feature_area: Optional[str] = None
    suite_name: Optional[str] = None
    test_suite_id: Optional[uuid.UUID] = None
    tags: Optional[List[Any]] = None
    estimated_duration_minutes: Optional[int] = None
    is_automated: Optional[bool] = None
    automation_status: Optional[str] = None
    test_fingerprint: Optional[str] = None
    status: str
    changed_by_id: Optional[uuid.UUID] = None
    change_summary: Optional[str] = None
    change_type: str
    changed_fields: Optional[List[str]] = None
    created_at: datetime
    # W28 TCM-04 (migration 0222); ``parameters`` above are masked as on the case.
    assignee_id: Optional[uuid.UUID] = None
    source: Optional[str] = None            # VERSION_SOURCES, or None = not recorded (before 0222)
    changed_by_name: Optional[str] = None   # the user's full_name, else username; None when the user is gone
    executions: List[TestCaseVersionExecution] = Field(default_factory=list)

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
    action: Literal["approve", "reject", "request_changes"]
    notes: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)


class TestCaseTransitionRequest(BaseModel):
    action: Literal[
        "request_review",
        "claim_review",
        "withdraw_review",
        "unclaim",
        "approve",
        "reject",
        "request_changes",
        "activate",
        "flag_stale",
        "revise",
        "deprecate",
        "reinstate",
        "archive",
    ]
    reason: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    expected_version: int = Field(..., ge=1)


class TestCaseDeprecateRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class AllowedTransitionResponse(BaseModel):
    action: str
    allowed: bool
    blocked_reason: Optional[str] = None
    requires_reason: bool = False  # W28: REASON_REQUIRED_ACTIONS, and a lead's self-approval


class TestCaseCommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_LONG_TEXT)
    comment_type: str = "general"
    parent_id: Optional[uuid.UUID] = None
    step_number: Optional[int] = None


class CommentMention(BaseModel):
    """A project member an ``@username`` in a test-case comment resolved to (W21d ACT-02).

    Ids and display names only: never an e-mail.
    """

    user_id: uuid.UUID
    username: str
    display_name: str


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
    # Filled by POST .../comments from the members it notified; [] on every
    # list read (mentions are not stored on the comment). The default keeps
    # every existing caller and hand-built row validating.
    mentions: List[CommentMention] = []

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
    # W28 TCM-05 (migration 0222)
    executed_case_version: Optional[int] = None   # the case version the recorded execution ran against; None = not run, or run before 0222
    case_version: Optional[int] = None            # the case's current version (the list route's join); None on a create / execute answer is allowed

    model_config = ConfigDict(from_attributes=True)


# ── W28: bulk edit (TCM-03) and restore (TCM-04) ──────────────────────────────
# Enum-like fields are plain ``str``; each vocabulary is a module constant in
# services/test_case_editing_service.py and judged there with one fixed 422.

class TestCaseBulkRequest(BaseModel):
    project_id: uuid.UUID
    case_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=200)   # BULK_MAX_IDS; duplicates collapse, order kept
    expected_versions: Dict[uuid.UUID, int] = Field(default_factory=dict)  # per case; a case missing here is not version-checked
    action: str = Field(..., max_length=30)            # BULK_ACTIONS
    transition: Optional[str] = Field(None, max_length=30)   # BULK_TRANSITIONS, when action == "transition"
    reason: Optional[str] = Field(None, max_length=500)      # required for flag_stale / deprecate / archive / reinstate
    assignee_id: Optional[uuid.UUID] = None            # set_owner; None = unassign
    priority: Optional[str] = Field(None, max_length=20)     # set_priority: PRIORITIES
    tags: Optional[List[str]] = Field(None, max_length=20)   # add_tags / remove_tags: each trimmed, 1..100 chars, deduped
    suite_name: Optional[str] = Field(None, max_length=500)  # move_to_suite: trimmed, non-empty


class TestCaseBulkItemResult(BaseModel):
    id: uuid.UUID
    outcome: str                            # BULK_OUTCOMES
    reason: Optional[str] = None            # why skipped (one sentence)
    version: Optional[int] = None           # the case's version after the request
    status: Optional[str] = None            # its status after the request


class TestCaseBulkResponse(BaseModel):
    action: str
    requested: int
    changed: int
    unchanged: int
    skipped: int
    not_found: int
    results: List[TestCaseBulkItemResult]   # one per requested id (after de-duplication), in request order


class TestCaseRestoreRequest(BaseModel):
    version: int = Field(..., ge=1)
    expected_version: int = Field(..., ge=1)
    change_summary: Optional[str] = Field(None, max_length=500)


class ExecuteTestPlanItemRequest(BaseModel):
    execution_status: Literal["passed", "failed", "blocked", "skipped"]
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
    reason: Optional[str] = None
    policy_snapshot: Optional[dict] = None
    transition_from: Optional[str] = None
    transition_to: Optional[str] = None
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
    retirement_confirmed_at: Optional[datetime] = None
    retirement_confirmed_by_id: Optional[uuid.UUID] = None
    retirement_reason: Optional[str] = None
    deleted_observed_at: Optional[datetime] = None
    review_tag: Optional[str] = None
    tags: Optional[List[str]] = None
    run_count: Optional[int] = None
    # W13 TCL-07: the created_at of the run last_seen_run_id names (read by run
    # id within the suite's project); None = unknown, never a guessed date.
    last_seen_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class CanonicalTestCaseListResponse(BaseModel):
    items: List[CanonicalTestCaseResponse]
    total: int
    # Set on paged lists (re-audit M7); absent where the whole list is returned.
    page: Optional[int] = None
    size: Optional[int] = None


class CanonicalPromotionResponse(BaseModel):
    canonical: CanonicalTestCaseResponse
    managed_case: ManagedTestCaseResponse


class CanonicalManagedUnlinkRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized


class CanonicalRetirementConfirmRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        return normalized


class EvidenceGapItem(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    status: str
    canonical_test_case_id: Optional[uuid.UUID] = None
    canonical_status: Optional[str] = None
    deleted_observed_at: Optional[datetime] = None
    last_executed_at: Optional[datetime] = None
    # W13 TCL-03: the two new kinds' facts; never_executed / automation_vanished leave them None.
    owner_state: Optional[str] = None          # no_owner: "unassigned" | "deactivated"
    flaky_score: Optional[float] = None        # flaky_active
    flaky_confidence: Optional[str] = None     # flaky_active


class EvidenceGapListResponse(BaseModel):
    items: List[EvidenceGapItem]
    total: int


# ── W13 TCL-03: library health (GET /api/v1/test-management/health) ──────────
# Enum-like fields are plain ``str``; each vocabulary is in core/test_library_vocab.


class LibraryHealthComponent(BaseModel):
    key: str                                   # HEALTH_COMPONENTS
    label: str                                 # HEALTH_COMPONENT_LABELS[key]
    weight: float                              # HEALTH_WEIGHTS[key]
    measured: bool                             # False when its denominator is 0 (then value / numerator are None)
    value: Optional[float] = None              # the component's 0..100 score
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    detail: str                                # one sentence with both numbers


class LibraryCaseRow(BaseModel):
    id: uuid.UUID
    title: str                                 # React text
    status: str
    owner: Optional[str] = None                # full name, else username; None = nobody
    age_days: Optional[int] = None             # review queue: days in review; stale lists: days since the last change
    detail: Optional[str] = None               # "flaky score 0.72 (high confidence)" / "owner deactivated" / "unassigned"


class LibraryCaseList(BaseModel):
    total: int                                 # every match (a count, not the page length)
    items: List[LibraryCaseRow]                # <= HEALTH_LIST_PREVIEW (5)
    measured: bool = True
    reason: Optional[str] = None               # when not measured: the sentence (FLAKY_UNMEASURED_*)


class ReviewQueueSummary(LibraryCaseList):
    oldest_age_days: Optional[int] = None      # None when the queue is empty


class StrategyGapRead(BaseModel):
    kind: str                                  # STRATEGY_GAP_KINDS
    severity: str                              # "critical" | "warn"
    title: str                                 # STRATEGY_GAP_TITLES[kind]
    detail: str                                # "oldest 9 days" / "38 % automated · target 75 %"
    count: int                                 # the pill's number (cases; automation_below_target: the % points gap)
    pill: str                                  # "3 in open plans" / "2 pending" / "22 % gap" / "4 stale" / "3 flaky" / "7 cases"


class RequirementGapRead(BaseModel):
    key: str                                   # the key as first written (a link's typed key, else the extracted id)
    normalized_key: str
    reason: str                                # REQUIREMENT_GAP_REASONS
    sources: List[str]                         # REQUIREMENT_KEY_SOURCES it is known from
    linked_cases: int = 0                      # cases linking it with any link type (retired cases excluded)
    batch_claimed_cases: int = 0               # requirement_coverage.covered_by_case_ids of its batches (a claim, not a link)


class RequirementCoverageSummary(BaseModel):
    measured: bool                             # False when the project knows no requirement key
    known: int                                 # distinct normalised keys known to the project
    verified: int                              # keys with >= 1 "verifies" link from a non-retired case
    partly_verified: int                       # no "verifies", >= 1 "partially_verifies"
    gaps: int                                  # known - verified
    truncated: bool = False                    # a source held more than KNOWN_KEYS_CAP distinct keys: lower bounds
    reason: Optional[str] = None               # REQUIREMENTS_UNMEASURED_SENTENCE when not measured
    top_gaps: List[RequirementGapRead] = Field(default_factory=list)   # <= 5 (no_link, related_only, partly_verified)


class LibraryHealthResponse(BaseModel):
    project_id: uuid.UUID
    computed_at: datetime
    measured: bool                             # False when the project has no non-retired authored case
    total_cases: int                           # non-retired authored cases (the banner's "Cases")
    active_cases: int
    retired_cases: int
    automated_cases: int
    automation_target_pct: int                 # projects.automation_target_pct (default 60)
    stale_drafts: int                          # drafts unchanged for STALE_DRAFT_DAYS
    retired_in_open_plans: int                 # deprecated / archived cases still items of a plan not completed / archived
    avg_age_days: Optional[float] = None
    older_than_180_days: int = 0
    older_than_180_pct: Optional[float] = None
    score: Optional[int] = None                # 0..100 over the measured components; None when not measured
    tag: Optional[str] = None                  # HEALTH_TAGS
    components: List[LibraryHealthComponent]
    review_queue: ReviewQueueSummary
    strategy_gaps: List[StrategyGapRead]
    flaky_active: LibraryCaseList
    no_owner: LibraryCaseList
    suite_names: List[str]                     # the authored suite names (the Suite filter's options), sorted, <= 500
    suite_names_total: int
    requirements: Optional[RequirementCoverageSummary] = None   # None when the traceability flag is off


# ── W13 TCL-04: import (POST /cases/import: multipart file + QUERY project_id, dry_run) ──


class ImportHeaderReport(BaseModel):
    recognised: List[str]                      # the file's headers that map to a field, as written
    ignored: List[str]                         # export-only headers (IMPORT_IGNORED_HEADERS)
    unknown: List[str]                         # anything else (reported, never imported)


class ImportDuplicateOf(BaseModel):
    case_id: Optional[uuid.UUID] = None        # an existing case of the project
    title: Optional[str] = None
    row: Optional[int] = None                  # an earlier row of the same file


class ImportRowResult(BaseModel):
    row: int                                   # the spreadsheet row number (the header is row 1)
    outcome: str                               # IMPORT_OUTCOMES: create | duplicate | invalid | created
    title: Optional[str] = None                # React text (<= 500)
    reasons: List[str] = Field(default_factory=list)    # why invalid / duplicate (IMPORT_* sentences)
    warnings: List[str] = Field(default_factory=list)   # kept-as-written values, the ignored Status, a stripped "'"
    duplicate_of: Optional[ImportDuplicateOf] = None
    case_id: Optional[uuid.UUID] = None        # outcome "created" only


class ImportSummary(BaseModel):
    rows: int
    create: int                                # dry run: would be created; apply: valid rows attempted
    duplicate: int
    invalid: int
    created: int = 0                           # apply only


class TestCaseImportResponse(BaseModel):
    project_id: uuid.UUID
    dry_run: bool
    file_name: str                             # <= 200, control characters dropped
    format: str                                # IMPORT_FORMATS: csv | xlsx
    headers: ImportHeaderReport
    summary: ImportSummary
    rows: List[ImportRowResult]                # one per data row, file order (<= IMPORT_MAX_ROWS)


# ── W13 TCL-05: the case timeline (GET /cases/{case_id}/timeline) ────────────


class TimelineLink(BaseModel):
    type: str                                  # TIMELINE_LINK_TYPES: run_result | run | plan | defect
    run_id: Optional[uuid.UUID] = None
    test_case_id: Optional[uuid.UUID] = None   # a per-run RESULT id (run_result)
    plan_id: Optional[uuid.UUID] = None
    defect_id: Optional[uuid.UUID] = None
    jira_key: Optional[str] = None


class TimelineEntry(BaseModel):
    id: str                                    # unique within its kind (review rows: "<uuid>:requested" / ":decided")
    kind: str                                  # TIMELINE_KINDS
    occurred_at: datetime
    actor_id: Optional[uuid.UUID] = None
    actor_name: Optional[str] = None           # full name, else username; None = nobody recorded
    actor_source: Optional[str] = None         # VERSION_SOURCES on version rows; "automation" on runs; else None
    title: str                                 # one server-rendered sentence (timeline_title), React text
    detail: Optional[str] = None               # <= 300 characters
    status: Optional[str] = None               # the version's status / the execution's result / the decision / the defect's state
    version: Optional[int] = None              # version rows; the executed case version on a plan execution when recorded
    link: Optional[TimelineLink] = None


class TestCaseTimelineResponse(BaseModel):
    case_id: uuid.UUID
    kinds: List[str]                           # the kinds this page covers (the request's, else every kind)
    items: List[TimelineEntry]                 # <= limit, (occurred_at DESC, kind ASC, id DESC)
    next_cursor: Optional[str] = None          # None = the end of the stream (a stream is paged, not counted)
    automation_linked: bool = False            # the case has an automation identity: automated kinds can answer


# ── W13 TCL-06: requirement links (/cases/{case_id}/requirements, /traceability/gaps) ──


class RequirementLinkCreate(BaseModel):
    requirement_key: str = Field(..., min_length=1, max_length=200)
    requirement_title: Optional[str] = Field(None, max_length=500)
    link_type: str = Field("verifies", max_length=20)   # REQUIREMENT_LINK_TYPES, judged in the service (422 sentence)


class RequirementLinkRead(BaseModel):
    id: uuid.UUID
    test_case_id: uuid.UUID
    requirement_key: str
    normalized_key: str
    requirement_title: Optional[str] = None
    link_type: str
    created_by_name: Optional[str] = None
    created_at: datetime


class RequirementLinkListResponse(BaseModel):
    items: List[RequirementLinkRead]           # oldest first
    total: int


class TraceabilityGapsResponse(BaseModel):
    project_id: uuid.UUID
    summary: RequirementCoverageSummary        # the same numbers the health answer carries (top_gaps empty here)
    gaps: List[RequirementGapRead]             # the page
    gaps_total: int
    page: int
    size: int
    cases_without_requirements_total: int      # approved / active cases with no link at all
    cases_without_requirements: List[LibraryCaseRow]   # <= 25, oldest first


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


# ── Run context (W23b FAIL-13) ────────────────────────────────────────────────
# The runtime / dependency / image / allowlisted-env context a reporter sends
# beside its results. Attacker-controlled input from an API key: every key is
# allowlisted or pattern-checked, every section and the whole object are capped,
# env VALUES are redacted here (before the object reaches object storage, a
# Celery kwarg, ``extra_metadata`` or ``test_runs.run_context``). Versions,
# hashes, image names and digests are never "cleaned": a value that does not
# match its pattern is refused (422). The vocabulary is
# ``services/run_context_vocab.py``; ``sdk-vectors/run_context.json`` holds the
# cases every SDK and this model agree on.

_RUNTIME_KEY_PATTERN = re.compile(RUNTIME_KEY_RE)
_VERSION_PATTERN = re.compile(VERSION_RE)
_SHA256_PATTERN = re.compile(SHA256_RE)
_IMAGE_NAME_PATTERN = re.compile(IMAGE_NAME_RE)
_DIGEST_PATTERN = re.compile(DIGEST_RE)
_ENV_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LOCKFILE_NAME_SET = frozenset(LOCKFILE_NAMES)
_ENV_ALLOWLIST_SET = frozenset(ENV_ALLOWLIST)


def _run_context_error(code: str, **fill: Any) -> ValueError:
    """A ``ValueError`` carrying one ``RUN_CONTEXT_ERRORS`` sentence (FastAPI renders a 422)."""
    sentence = RUN_CONTEXT_ERRORS[code]
    return ValueError(sentence.format(**fill) if fill else sentence)


def _check_section_size(section: str, value: dict, cap: int) -> None:
    if len(value) > cap:
        raise _run_context_error("too_many", section=section, max=cap)


class RunContext(BaseModel):
    """``run_context`` on ``IngestPayload`` / ``LiveSessionCreate`` / ``LiveStreamMeta`` and the
    ``/ingest/file`` form (W23b FAIL-13). An unknown future SECTION is dropped, never stored;
    inside a section the rules are strict. Keys are compared EXACTLY (``ENV_ALLOWLIST`` is
    case-sensitive, as environments are on Linux). ``model_dump(exclude_none=True)`` is the
    stored form (env values already redacted)."""

    model_config = ConfigDict(extra="ignore")

    v: int = CONTEXT_VERSION
    runtime: Optional[dict[str, str]] = None                  # RUNTIME_KEY_RE -> VERSION_RE, <= RUNTIME_MAX
    dependency_fingerprints: Optional[dict[str, str]] = None  # LOCKFILE_NAMES -> SHA256_RE
    image_digests: Optional[dict[str, str]] = None            # IMAGE_NAME_RE -> DIGEST_RE, <= IMAGES_MAX
    env: Optional[dict[str, str]] = None                      # ENV_ALLOWLIST -> str <= ENV_VALUE_MAX, no control chars

    @field_validator("v")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != CONTEXT_VERSION:
            raise _run_context_error("bad_version")
        return value

    @field_validator("runtime")
    @classmethod
    def _runtime(cls, value: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if value is None:
            return None
        _check_section_size("runtime", value, RUNTIME_MAX)
        for key, version in value.items():
            if not _RUNTIME_KEY_PATTERN.match(key) or not _VERSION_PATTERN.match(version):
                raise _run_context_error("bad_runtime")
        return value

    @field_validator("dependency_fingerprints")
    @classmethod
    def _dependency_fingerprints(cls, value: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if value is None:
            return None
        _check_section_size("dependency_fingerprints", value, len(LOCKFILE_NAMES))
        for name, digest in value.items():
            if name not in _LOCKFILE_NAME_SET:
                raise _run_context_error("lockfile_name")
            if not _SHA256_PATTERN.match(digest):
                raise _run_context_error("bad_hash")
        return value

    @field_validator("image_digests")
    @classmethod
    def _image_digests(cls, value: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if value is None:
            return None
        _check_section_size("image_digests", value, IMAGES_MAX)
        for name, digest in value.items():
            if not _IMAGE_NAME_PATTERN.match(name) or not _DIGEST_PATTERN.match(digest):
                raise _run_context_error("bad_image")
        return value

    @field_validator("env")
    @classmethod
    def _env(cls, value: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if value is None:
            return None
        _check_section_size("env", value, len(ENV_ALLOWLIST))
        for key, text in value.items():
            if key not in _ENV_ALLOWLIST_SET:
                raise _run_context_error("env_key", keys=", ".join(ENV_ALLOWLIST))
            if len(text) > ENV_VALUE_MAX or _ENV_CONTROL_CHARS.search(text):
                raise _run_context_error("env_value")
        return value

    @model_validator(mode="after")
    def _redact_and_cap(self) -> "RunContext":
        if self.env:
            # Lazy import: the redaction helper lives in a service module (the
            # ingestion_sanitization precedent of ``_NulFreeInput``). A redacted value
            # is re-capped so a stored copy re-validates (``[REDACTED_EMAIL]`` is longer
            # than the address it replaced).
            from app.services.run_context_service import redact_env_value  # noqa: PLC0415

            self.env = {key: redact_env_value(text)[:ENV_VALUE_MAX] for key, text in self.env.items()}
        for section in ("runtime", "dependency_fingerprints", "image_digests", "env"):
            if not getattr(self, section):
                setattr(self, section, None)
        size = len(json.dumps(self.model_dump(exclude_none=True), separators=(",", ":")).encode())
        if size > CONTEXT_MAX_BYTES:
            raise _run_context_error("too_large")
        return self


# ── Live Stream Schemas ───────────────────────────────────────────────────────

class _NulFreeInput(BaseModel):
    """Strips U+0000 from every string of an ingestion request body.

    PostgreSQL text cannot hold a NUL. One in a build number or branch made
    ``POST /ingest`` answer 500 (the run-identity lookup failed) and, in a
    result, rejected that row from the run (E2E 2026-10-10). Captured test
    output is where they come from; nothing downstream can store them.
    """

    #: Identity fields left as sent, so their own validation still refuses a
    #: control character: a batch id is an idempotency key, and quietly
    #: rewriting "bad<NUL>id" to "badid" would change which retry it matches.
    _NUL_KEPT_FIELDS: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _strip_nul_bytes(cls, data):
        from app.services.ingestion_sanitization import strip_nul_bytes  # noqa: PLC0415

        if isinstance(data, dict) and cls._NUL_KEPT_FIELDS:
            return {
                key: value if key in cls._NUL_KEPT_FIELDS else strip_nul_bytes(value)
                for key, value in data.items()
            }
        return strip_nul_bytes(data)


class LiveSessionCreate(_NulFreeInput):
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
    # W23b FAIL-13: the run context -> LiveSession.extra_metadata["run_context"] -> the run
    # (validated and env-redacted by ``RunContext``).
    run_context: Optional[RunContext] = None


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
    # EXEC-01: optional execution timing on a ``test_result``. The server stores ``timestamp_ms`` as the test's
    # finish and ``started_at_ms`` (or a ``test_start`` of the same test in the same batch) as its start, both
    # bounded against the session's server-side clock. A value the server cannot use is dropped, never a 422:
    # a bad timing figure must not cost the batch its results. Unset, they are left out of ``model_dump()``, so an
    # event without timing keeps exactly the stored payload — and batch-admission digest — it had before.
    started_at_ms: Optional[int] = Field(
        None,
        description="Client epoch ms when the test started (its setup included), sent on test_result.",
        exclude_if=lambda value: value is None,
    )
    setup_ms: Optional[int] = Field(
        None,
        description="Fixture / before-hook time of this test, ms (0..24 h).",
        exclude_if=lambda value: value is None,
    )
    call_ms: Optional[int] = Field(
        None,
        description="The test body's own time, ms (0..24 h).",
        exclude_if=lambda value: value is None,
    )
    teardown_ms: Optional[int] = Field(
        None,
        description="Fixture / after-hook time of this test, ms (0..24 h).",
        exclude_if=lambda value: value is None,
    )
    worker_id: Optional[str] = Field(
        None,
        description="The parallel worker / runner slot that ran the test (at most 64 characters).",
        exclude_if=lambda value: value is None,
    )

    @field_validator("started_at_ms", "setup_ms", "call_ms", "teardown_ms", mode="before")
    @classmethod
    def _usable_ms_or_none(cls, value: Any, info: Any) -> Optional[int]:
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
            return None
        ms = int(round(value))
        upper = 9_007_199_254_740_991 if info.field_name == "started_at_ms" else 86_400_000
        return ms if 0 <= ms <= upper else None

    @field_validator("worker_id", mode="before")
    @classmethod
    def _worker_text_or_none(cls, value: Any) -> Optional[str]:
        if value is None or isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        text = str(value).strip()[:64]
        return text or None


class LiveEventBatch(_NulFreeInput):
    """
    A batch of events sent from a client machine.
    Batching amortises HTTP overhead — 50–1000 events per call is recommended.
    """
    _NUL_KEPT_FIELDS: ClassVar[frozenset[str]] = frozenset({"session_id", "run_id", "batch_id"})
    session_id: str = Field(..., min_length=1, max_length=255)
    run_id: str = Field(..., min_length=1, max_length=100)
    batch_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        pattern=r"^[^\x00-\x1f\x7f]+$",
        description=(
            "Client-generated identity for this batch. The same value must be reused "
            "for every HTTP retry; omitted for legacy callers."
        ),
    )
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
    # W23b FAIL-13: the run context, typed (NOT inside ``metadata``): the auto-created
    # session's extra_metadata["run_context"] -> the run.
    run_context: Optional[RunContext] = None


class LiveStreamIngestRequest(_NulFreeInput):
    """API-key-authenticated streaming ingest. Server auto-manages the session.

    A client-chosen ``run_id`` (any stable identifier — CI build id, UUID, etc.)
    keys the live session along with the API key's bound project. The first
    call for a given ``(project_id, run_id)`` pair auto-creates the session;
    subsequent calls reuse it. Clients never call ``/sessions`` themselves.
    """
    _NUL_KEPT_FIELDS: ClassVar[frozenset[str]] = frozenset({"session_id", "run_id", "batch_id"})
    run_id: str = Field(..., min_length=1, max_length=100)
    batch_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        pattern=r"^[^\x00-\x1f\x7f]+$",
        description=(
            "Client-generated identity for this batch. The same value must be reused "
            "for every HTTP retry; omitted for legacy callers."
        ),
    )
    events: List[LiveEvent] = Field(..., min_length=1, max_length=1000)
    meta: Optional[LiveStreamMeta] = None


class LiveStreamIngestResponse(BaseModel):
    accepted: int
    run_id: str
    session_id: str
    created_session: bool


# ── Ingest Schemas (unified batch + file upload) ──────────────────────────────

BUILD_NUMBER_MAX_LENGTH = 100


# ── W23d AGT-14: captured HTTP exchanges (contract §1.2) ──────────────────────
# Opt-in per SDK run; attacker-controlled input from an API key. Every exchange is
# cut and redacted INSIDE the model (``http_exchange_redaction.redact_exchange``), so
# ``IngestPayload.model_dump()`` — the object-storage copy — only holds the redacted form.

class HttpMessageIn(BaseModel):
    """One side of an exchange, as the SDK sends it (cut + redacted by ``HttpExchangeIn``)."""
    model_config = ConfigDict(extra="ignore")
    headers: dict[str, str] = Field(default_factory=dict)   # cut to HEADERS_MAX / HEADER_*_MAX, then DENYLIST-redacted
    body: Optional[str] = None      # text; JSON bodies as JSON text; bytes are the SDK's job (base64 is NOT accepted)
    body_truncated: bool = False    # the body is incomplete (the SDK's own cut, the wire cap or the store cap)
    body_bytes: int = 0             # UTF-8 size before the store cut (recomputed; a stored copy carries it)

    @field_validator("headers", mode="before")
    @classmethod
    def _usable_headers(cls, value: Any) -> dict[str, str]:
        """Lenient: a non-object → {}; a non-string name or a value that is not text / a number is skipped."""
        if not isinstance(value, dict):
            return {}
        out: dict[str, str] = {}
        for name, item in value.items():
            if not isinstance(name, str):
                continue
            if isinstance(item, str):
                out[name] = item
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                out[name] = str(item)
        return out

    @field_validator("body", mode="before")
    @classmethod
    def _text_body_or_none(cls, value: Any) -> Optional[str]:
        return value if isinstance(value, str) else None

    @field_validator("body_truncated", mode="before")
    @classmethod
    def _truncated_flag(cls, value: Any) -> bool:
        return value is True

    @field_validator("body_bytes", mode="before")
    @classmethod
    def _usable_size(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value


class HttpExchangeIn(BaseModel):
    """One HTTP request/response a test made (AGT-14). Lenient on purpose: a bad exchange is DROPPED by the
    list pre-filter (IngestTestResult / LiveHttpExchangesIn), never a 422 that costs the batch its results."""
    model_config = ConfigDict(extra="ignore")
    method: str                                  # upper-cased; METHOD_RE
    url: str = Field(..., min_length=1)          # the concrete URL as sent; cut to URL_MAX; query values redacted
    endpoint: Optional[str] = None               # the TEMPLATE (drift key), e.g. "/api/products/{id}"; derived when absent
    status: Optional[int] = Field(None, ge=100, le=599)   # None = no response (transport error)
    duration_ms: Optional[int] = Field(None, ge=0, le=86_400_000)
    started_at: Optional[datetime] = None
    error: Optional[str] = Field(None, max_length=200)    # the client's transport error class name, e.g. "ConnectTimeout"
    request: HttpMessageIn = Field(default_factory=HttpMessageIn)
    response: HttpMessageIn = Field(default_factory=HttpMessageIn)
    # ALWAYS recomputed by the after-validator — a client-sent value is overwritten, never trusted
    # (a stored copy whose body was cut keeps its schema's safe names only):
    response_schema: Optional[dict[str, str]] = None   # top-level key -> JSON type, from the FULL redacted body
    response_schema_state: str = "empty"         # SCHEMA_STATES
    redaction_counts: dict[str, int] = Field(default_factory=dict)   # {"headers": n, "query": n, "body": n}

    @field_validator("method", mode="before")
    @classmethod
    def _method_upper(cls, value: Any) -> str:
        from app.services.http_exchange_redaction import normalise_method  # noqa: PLC0415

        method = normalise_method(value)
        if method is None:
            raise PydanticCustomError("http_method", "method must be an HTTP method name (A-Z, at most 16).")
        return method

    @field_validator("response_schema", mode="before")
    @classmethod
    def _schema_mapping_or_none(cls, value: Any) -> Optional[dict[str, str]]:
        if not isinstance(value, dict):
            return None
        return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)}

    @field_validator("response_schema_state", mode="before")
    @classmethod
    def _known_state(cls, value: Any) -> str:
        return value if value in SCHEMA_STATES else "empty"

    @field_validator("redaction_counts", mode="before")
    @classmethod
    def _counts_ignored(cls, value: Any) -> dict[str, int]:
        return {}  # recomputed by the after-validator

    @model_validator(mode="after")
    def _cut_and_redact(self) -> "HttpExchangeIn":
        from app.services.http_exchange_redaction import redact_exchange  # noqa: PLC0415

        redact_exchange(self)
        return self


def _prefiltered(data: Any, field: str, dropped_field: str) -> Any:
    """The before-validator body of IngestTestResult / LiveHttpExchangesIn: the exchange list → at most
    EXCHANGES_PER_TEST_MAX validated exchanges; every unusable or surplus one is counted in ``dropped_field``
    (a client-sent count is overwritten, never trusted)."""
    if not isinstance(data, dict):
        return data
    if data.get(field) is None:
        if dropped_field in data:
            data = {key: value for key, value in data.items() if key != dropped_field}
        return data
    from app.services.http_exchange_redaction import prefilter_exchanges  # noqa: PLC0415

    kept, dropped = prefilter_exchanges(data[field], validate=HttpExchangeIn.model_validate)
    return {**data, field: kept, dropped_field: dropped}


class LiveHttpExchangesIn(_NulFreeInput):
    """Body of POST /api/v1/stream/http-exchanges (§3.1 — no path id, on purpose): one test's exchanges
    of a live session, posted once by the reporter after the test's result."""
    _NUL_KEPT_FIELDS: ClassVar[frozenset[str]] = frozenset({"session_id", "run_id"})
    session_id: str = Field(..., min_length=1, max_length=255)   # the live session (loaded, never trusted for scope)
    run_id: str = Field(..., min_length=1, max_length=100)       # the session's client run id (== LiveSession.run_id)
    test_name: str = Field(..., min_length=1, max_length=1000)   # EXACTLY what the test_result event sends
    class_name: Optional[str] = Field(None, max_length=500)
    exchanges: List[HttpExchangeIn] = Field(..., max_length=EXCHANGES_PER_TEST_MAX)   # same lenient pre-filter
    exchanges_dropped: int = Field(0, ge=0)                      # server-set by the pre-filter

    @model_validator(mode="before")
    @classmethod
    def _prefilter_exchanges(cls, data: Any) -> Any:
        return _prefiltered(data, "exchanges", "exchanges_dropped")


class LiveHttpExchangesResponse(BaseModel):
    stored: int
    truncated: int                 # bodies cut at BODY_STORED_MAX_BYTES
    dropped: int
    test_case_id: str              # the id the drainer gives this test's row (§2.1 P-6)


class HttpMessageRead(BaseModel):
    headers: dict[str, str]
    body: Optional[str] = None
    body_truncated: bool = False
    body_bytes: int = 0            # the size before the store cut


class HttpExchangeRead(BaseModel):
    seq: int
    method: str
    endpoint: str
    url: str
    host: Optional[str] = None
    status: Optional[int] = None
    duration_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    error: Optional[str] = None
    request: HttpMessageRead
    response: HttpMessageRead
    response_schema_state: str     # SCHEMA_STATES
    source: str                    # CAPTURE_SOURCES
    captured_at: datetime
    redacted: bool                 # any redaction_counts value > 0 (the panel's "[REDACTED] markers" line)


class HttpExchangeListResponse(BaseModel):
    """GET /api/v1/runs/{run_id}/tests/{test_id}/http-exchanges."""
    items: List[HttpExchangeRead]  # seq order, ≤ LIST_PAGE_MAX
    total: int                     # docs stored for this test (a count, never len(items))
    dropped: int                   # exchanges of this test the server did not store
    run_cap_reached: bool          # the run holds EXCHANGES_PER_RUN_MAX docs (so "none" may mean "not stored")


# ── W23d AGT-15: the Prometheus metric templates (Settings › Integrations) ─────

class PrometheusMetricTemplate(BaseModel):
    """One admin-defined metric template; the server substitutes ``{service}`` (escaped) — never model PromQL."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., pattern=TEMPLATE_ID_RE)
    label: str = Field(..., min_length=1, max_length=TEMPLATE_LABEL_MAX)
    promql: str = Field(..., min_length=1, max_length=TEMPLATE_PROMQL_MAX)   # contains SERVICE_PLACEHOLDER
    unit: str = "count"            # TEMPLATE_UNITS


_C0_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")


class PrometheusMetricTemplateIn(PrometheusMetricTemplate):
    """A template as an ADMIN saves it (IntegrationsConfigUpdate): the placeholder, control characters and
    the unit are checked per item, so a 422's loc is ``prometheus_metric_templates.N.<field>``."""

    @field_validator("label", mode="after")
    @classmethod
    def _label_text(cls, value: str) -> str:
        value = value.strip()
        if not value or _C0_CONTROLS.search(value):
            raise PydanticCustomError("template_label", "A template label must be plain text of 1 to 80 characters.")
        return value

    @field_validator("promql", mode="after")
    @classmethod
    def _promql_has_the_placeholder(cls, value: str) -> str:
        value = value.strip()
        if SERVICE_PLACEHOLDER not in value:
            raise PydanticCustomError("template_placeholder", TEMPLATE_ERRORS["placeholder"])
        if _C0_CONTROLS.search(value):
            raise PydanticCustomError("template_promql", "A template's PromQL must be one line of text.")
        return value

    @field_validator("unit", mode="after")
    @classmethod
    def _known_unit(cls, value: str) -> str:
        if value not in TEMPLATE_UNITS:
            raise PydanticCustomError("template_unit", "A template unit must be one of: {units}.",
                                      {"units": ", ".join(TEMPLATE_UNITS)})
        return value


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
    # EXEC-01: optional execution timing. The write side bounds the values and
    # parses ISO text, so a value the store cannot hold becomes NULL.
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    setup_ms: Optional[int] = Field(None, ge=0)
    call_ms: Optional[int] = Field(None, ge=0)
    teardown_ms: Optional[int] = Field(None, ge=0)
    worker_id: Optional[str] = Field(None, max_length=64)
    # W23d AGT-14: the test's captured HTTP exchanges (opt-in in the SDK), cut and redacted by
    # HttpExchangeIn; a malformed one is dropped, never a 422. Unset, all three are left out of
    # model_dump(), so a batch without exchanges dumps exactly as before (the EXEC-01 rule).
    http_exchanges: Optional[List[HttpExchangeIn]] = Field(None, exclude_if=lambda value: value is None)
    # Server-set only (the pre-filter and IngestPayload's per-run cap): exchanges of this test not kept.
    http_exchanges_dropped: Optional[int] = Field(None, ge=0, exclude_if=lambda value: value is None)
    # W23d AGT-15 (M-4a): the service under test -> TestCase.service_name (the log stage's metrics context).
    service_name: Optional[str] = Field(None, max_length=255, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def _prefilter_http_exchanges(cls, data: Any) -> Any:
        return _prefiltered(data, "http_exchanges", "http_exchanges_dropped")


class IngestPayload(_NulFreeInput):
    """JSON batch ingest request body for POST /api/v1/ingest."""
    project_id: str = Field(..., description="Project UUID")
    build_number: str = Field(..., min_length=1, max_length=BUILD_NUMBER_MAX_LENGTH)
    results: List[IngestTestResult] = Field(..., min_length=1, max_length=MAX_RESULTS_PER_INGEST)
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
    jenkins_job: Optional[str] = Field(None, max_length=500)
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
    # W23b FAIL-13: the runtime / dependency / image / allowlisted-env context (validated and
    # env-redacted by ``RunContext``, so ``model_dump()`` puts the REDACTED form in object storage).
    run_context: Optional[RunContext] = None
    # W23d (contract §0.8): False -> finalize_run(run_ai=False): no suite comparison and no AI
    # pipeline (the /ingest/file form's rule). Left out of model_dump() while True, so a batch
    # that does not send it dumps exactly as before; the worker reads payload.get("run_ai", True).
    run_ai: bool = Field(
        True,
        description="False skips the AI analysis and the suite comparison for this run.",
        exclude_if=lambda value: value is True,
    )

    @model_validator(mode="after")
    def _cap_http_exchanges_per_run(self) -> "IngestPayload":
        """W23d AGT-14: at most EXCHANGES_PER_RUN_MAX exchanges per batch, kept in result order; the
        surplus of each later result is added to its http_exchanges_dropped."""
        budget = EXCHANGES_PER_RUN_MAX
        for result in self.results:
            exchanges = result.http_exchanges
            if not exchanges:
                continue
            if len(exchanges) > budget:
                result.http_exchanges = exchanges[:budget]
                result.http_exchanges_dropped = (result.http_exchanges_dropped or 0) + len(exchanges) - budget
            budget -= len(result.http_exchanges or [])
        return self


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
    # None when no test has passed or failed yet: not 0.0, which Live printed
    # as a "0.0%" outcome for a run at 10 passed, 1 failed.
    pass_rate: Optional[float] = None
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


# ── W12 SRCH-02: the embedding index (generations; services/embedding_index_state.py) ──
# Plain str for every enum-like field (the vocabularies live in G-C's
# services/embedding_function.py, chroma_collections.py, embedding_index_state.py).

class EmbeddingGenerationRead(BaseModel):
    provider: str                              # EMBEDDING_PROVIDERS (a stored unsupported value is shown, never applied)
    model: str
    prefix: str                                # "" = no prefix
    key: str                                   # embedder key: "default" | "ollama:<model>" | "openai:<model>"
    tag: str                                   # "" for the legacy generation, else "e" + 8 hex


class EmbeddingFamilyProgress(BaseModel):
    family: str                                # COLLECTION_FAMILIES[*].base
    state: str                                 # FAMILY_STATES: pending | running | done | skipped
    processed: int = 0
    total: Optional[int] = None                # None = not counted (a copy family's total is its source's count)


class EmbeddingIndexRead(BaseModel):
    active: EmbeddingGenerationRead            # what every read and write uses NOW
    configured: EmbeddingGenerationRead        # ai_config.embedding_* + the storage prefix
    in_sync: bool                              # configured.key == active.key and configured.prefix == active.prefix
    status: str                                # EMBEDDING_INDEX_STATUSES: idle | building | failed
    target: Optional[EmbeddingGenerationRead] = None
    families: List[EmbeddingFamilyProgress] = Field(default_factory=list)
    processed: int = 0
    total: Optional[int] = None
    requested_at: Optional[datetime] = None
    switched_at: Optional[datetime] = None     # when the active generation last changed (None = never: legacy)
    last_error: Optional[str] = None           # ≤ 300 chars, a sentence (no host, no key, no query text)
    configured_note: Optional[str] = None      # CONFIGURED_UNSUPPORTED_NOTE / PREFIX_INVALID_NOTE when one applies
    stalled: Optional[bool] = None             # status == "building" and its worker died / nothing claimed it (build_is_stalled)


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
    # AIC-04 / AIC-01 provenance, same vocabulary as
    # ai_confidence_threshold_source: "ai_config" (a valid value is stored
    # here and the runtime obeys it) | "env_default" (the deployment default
    # is in force). Resolved by ai_config_resolver.stored_or_default, the same
    # helper the runtime uses, so this page and the runtime cannot disagree.
    analysis_mode_source: str = "env_default"
    ai_timeout_seconds_source: str = "env_default"
    deep_investigation_enabled_source: str = "env_default"
    finetune_enabled_source: str = "env_default"
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
    # AIC-05: the training floor (settings.ML_MIN_TRAINING_SAMPLES) so the page
    # stops hard-coding "/ 200".
    ml_min_training_samples: int = 200
    # AI-F1 label integrity — honest learning-loop status
    ml_human_label_count: int = 0                     # human-provenance labels (feedback/corrections)
    ml_human_label_floor: int = 50                    # below this, ML is bootstrap (LLM-imitating)
    ml_maturity: str = "not_trained"                  # not_trained | bootstrap_llm_imitating | human_calibrated
    # Knowledge RAG feature toggle
    knowledge_rag_enabled: bool = False               # True if grounded test generation is active
    # RAG faithfulness gate. ``rag_faithfulness_evaluator`` is the stored
    # choice ("ollama" | "ragas") and round-trips through PUT; ``_effective``
    # is the strategy the gate runs, and ``_reason`` says why they differ:
    # "configured" | "offline_mode" (Ragas calls a hosted LLM) |
    # "ragas_not_installed". Plain str for the same reason as above.
    rag_faithfulness_evaluator: str = "ollama"
    rag_faithfulness_evaluator_effective: str = "ollama"
    rag_faithfulness_evaluator_reason: str = "configured"
    # The rag_faithfulness_gate flag, on in any scope. Off ⇒ no case is scored
    # and the evaluator choice has no effect.
    rag_faithfulness_gate_enabled: bool = False
    # W12 SRCH-02: which embedder every index uses NOW, what is configured and
    # any re-embed in progress. None when the state could not be read: never a guess.
    embedding_index: Optional[EmbeddingIndexRead] = None


class AIConfigUpdate(BaseModel):
    """Payload for updating AI configuration. None = keep existing."""
    # Constrained to the providers the policy layer actually knows (re-audit
    # C3). An unknown id reached provider_profile() as free-form text, and the
    # allowlist check it drives cannot filter a value it has no profile for.
    llm_provider: Optional[str] = Field(
        None, pattern=r"^(ollama|lmstudio|localai|vllm|openai|gemini|anthropic|openrouter)$"
    )
    llm_model: Optional[str] = None
    llm_temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    llm_max_tokens: Optional[int] = Field(None, ge=256, le=32768)
    ai_offline_mode: Optional[bool] = None
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    ai_confidence_threshold: Optional[int] = Field(None, ge=0, le=100)
    # AIC-02: floor 5 s (was 30) so a short LLM timeout can be set and seen to
    # degrade to the deterministic fallback; ai_config_resolver.valid_timeout_seconds
    # accepts the same 5..1800 range.
    ai_timeout_seconds: Optional[int] = Field(None, ge=5, le=1800)
    deep_investigation_enabled: Optional[bool] = None
    finetune_enabled: Optional[bool] = None
    openai_api_key: Optional[str] = Field(None, max_length=500)
    google_api_key: Optional[str] = Field(None, max_length=500)
    anthropic_api_key: Optional[str] = Field(None, max_length=500)  # LP-3
    openrouter_api_key: Optional[str] = Field(None, max_length=500)  # B-4
    base_url: Optional[str] = Field(None, max_length=500)           # LP-3: endpoint override
    analysis_mode: Optional[str] = Field(None, pattern=r"^(llm|ml|rules|auto)$")
    knowledge_rag_enabled: Optional[bool] = None
    rag_faithfulness_evaluator: Optional[str] = Field(None, pattern=r"^(ollama|ragas)$")


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
    slack_default_channel: str = Field(..., deprecated=(
        "Ignored since W10 (INT-02): an incoming webhook is bound to one channel. "
        "Route other channels per team in Team channels."))
    teams_enabled: bool
    teams_webhook_url: Optional[str] = None  # always redacted; compatibility field
    teams_webhook_set: bool
    github_repo: Optional[str]
    github_token_set: bool
    # W10 INT-01: the keys of INTEGRATION_TEXT_KEYS | INTEGRATION_SECRET_KEYS
    # (services/integration_config_service.py) whose shown value (or *_set)
    # comes from the process environment because the key was never saved here
    # (non-empty environment value only). Names only, never a value.
    environment_fields: List[str] = Field(default_factory=list)
    # W20 ADM-10: secret fields stored as Vault references (``vault:<path>#<key>``).
    vault_fields: List[str] = Field(default_factory=list)
    # W23d AGT-15: the system-under-test's Prometheus (SAVED-ONLY: no environment fallback — the env
    # PROMETHEUS_URL is TestLookup's own self-monitoring). Defaults keep old clients / fixtures valid.
    prometheus_enabled: bool = False
    prometheus_base_url: Optional[str] = None
    prometheus_token_set: bool = False
    prometheus_metric_templates: List[PrometheusMetricTemplate] = Field(default_factory=list)   # saved, else the defaults
    prometheus_templates_source: str = "default"   # "saved" | "default"


def _stripped(value: Optional[str]) -> Optional[str]:
    """None stays None; a string loses its outer whitespace ("" is kept: it
    hands the key back to the environment)."""
    return None if value is None else value.strip()


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
    slack_default_channel: Optional[str] = Field(None, max_length=100, deprecated=(
        "Accepted and ignored since W10 (INT-02)."))
    teams_enabled: Optional[bool] = None
    teams_webhook_url: Optional[str] = Field(None, max_length=1000)
    github_repo: Optional[str] = Field(None, max_length=500)
    github_token: Optional[str] = Field(None, max_length=500)
    # W23d AGT-15: the Prometheus block. The token goes to secret_refs only ("" expires it; a
    # ``vault:<path>#<key>`` value is stored as a reference); templates: [] = back to the defaults.
    prometheus_enabled: Optional[bool] = None
    prometheus_base_url: Optional[str] = Field(None, max_length=500)
    prometheus_bearer_token: Optional[str] = Field(None, max_length=4_000)
    prometheus_metric_templates: Optional[List[PrometheusMetricTemplateIn]] = Field(None, max_length=TEMPLATES_MAX)

    @field_validator("prometheus_metric_templates", mode="before")
    @classmethod
    def _at_most_twelve_templates(cls, value: Any) -> Any:
        if isinstance(value, list) and len(value) > TEMPLATES_MAX:
            raise PydanticCustomError("templates_too_many", TEMPLATE_ERRORS["too_many"])
        return value

    @field_validator("prometheus_metric_templates", mode="after")
    @classmethod
    def _template_ids_unique(
        cls, value: Optional[List[PrometheusMetricTemplateIn]],
    ) -> Optional[List[PrometheusMetricTemplateIn]]:
        if value and len({template.id for template in value}) != len(value):
            raise PydanticCustomError("templates_duplicate_id", TEMPLATE_ERRORS["duplicate_id"])
        return value

    # W10 INT-01: shapes checked at save. The host check of the two URLs (and
    # of https://{jira_domain}) is async DNS and lives in the PUT handler (422
    # "Target host is not allowed"). W23d: the Prometheus URL joins them.
    @field_validator("splunk_base_url", "ocp_api_url", "prometheus_base_url", mode="after")
    @classmethod
    def _integration_url_shape(cls, value: Optional[str]) -> Optional[str]:
        value = _stripped(value)
        if value and integration_url_problem(value) is not None:
            raise PydanticCustomError("integration_url", INTEGRATION_URL_SENTENCE)
        return value

    @field_validator("jira_domain", mode="after")
    @classmethod
    def _jira_domain_is_a_host(cls, value: Optional[str]) -> Optional[str]:
        value = _stripped(value)
        if value and JIRA_HOST_RE.fullmatch(value) is None:
            raise PydanticCustomError("jira_domain", JIRA_DOMAIN_SENTENCE)
        return value

    @field_validator("ocp_default_namespace", mode="after")
    @classmethod
    def _ocp_default_namespace_is_a_dns_label(cls, value: Optional[str]) -> Optional[str]:
        value = _stripped(value)
        if value and DNS_LABEL_RE.fullmatch(value) is None:
            raise PydanticCustomError("ocp_namespace", OCP_NAMESPACE_SENTENCE)
        return value


# ── Integration health (W10 INT-04) ──────────────────────────
# GET /integration-health/status answers list[IntegrationHealthRow]. Enum-like
# fields are plain ``str``; their vocabularies live in
# services/integration_probe_service.py (HEALTH_ROW_KINDS, the probe statuses).


class IntegrationHealthRow(BaseModel):
    provider: str                                   # jira | splunk | github | ocp | slack | teams | smtp | ollama | chromadb | gitlab | webhook
    scope_key: str = ""                             # "" = deployment-wide; the project id (github / gitlab rows); the subscription id (webhook rows)
    kind: str = "global"                            # HEALTH_ROW_KINDS
    project_id: Optional[uuid.UUID] = None
    project_name: Optional[str] = None
    label: Optional[str] = None                     # the project's name, or the webhook's name
    target: Optional[str] = None                    # host + path of the probed base, never a credential (github_target.target_label)
    status: str                                     # a probe status, "unknown" (not probed yet) or "paused" (webhook rows)
    probed: bool = True                             # False: no probe has run for this row: status "unknown", message NOT_PROBED_MESSAGE
    last_checked_at: Optional[str] = None
    message: Optional[str] = None
    response_ms: Optional[int] = None
    consecutive_failures: int = 0
    last_success_at: Optional[str] = None
    breaker_threshold: Optional[int] = None         # webhook rows only


# ── Regression diff commit range (W10 FAIL-09) ───────────────
# Built by regression_diff_service and returned inside its existing dict.


class CommitRangeStatus(BaseModel):
    available: bool                                 # commits are listed
    source: str                                     # connector | supplied | unavailable (commit_attribution_service SOURCE_*)
    base_source: str                                # supplied | green_baseline | last_completed_run | unavailable
    base_anchor_is_strong: bool
    base_run_id: Optional[uuid.UUID] = None
    base_build_number: Optional[str] = None         # the build the commit range starts after (NOT necessarily the diff's baseline)
    reason: Optional[str] = None                    # COMMIT_RANGE_REASONS; None when available
    commit_count: int = 0
    resolved_at: Optional[str] = None


class RegressionDiffCommit(BaseModel):              # one item of the dict's existing "commit_range" list (shape kept, two fields added)
    sha: str                                        # first 8 characters, as today
    message: str
    author: Optional[str] = None
    timestamp: Optional[str] = None                 # the commit's committed_at
    full_sha: Optional[str] = None
    commit_url: Optional[str] = None                # https link built server-side (_commit_html_url), else None


# ── History bisect narrowing (W23b FAIL-12) ──────────────────
# GET /api/v1/runs/{run_id}/bisect?fingerprint= (routers/commit_attribution.py).
# Enum-like fields are plain str; their values live in services/bisect_vocab.py.


class BisectRunRef(BaseModel):
    run_id: uuid.UUID
    build_number: str
    commit_hash: Optional[str] = None               # the stored value, full length; the SPA shows the first 7
    branch: Optional[str] = None
    created_at: datetime
    ci_run_url: Optional[str] = None                # only when it matches ^https?:// (else None)
    outcome: str                                    # TEST_OUTCOMES: this test in that run


class BisectCommit(BaseModel):                      # == score_commits' item + its link
    sha: Optional[str]
    author: Optional[str] = None
    message: str = ""
    committed_at: Optional[str] = None
    score: float
    rationale: dict[str, Any]
    commit_url: Optional[str] = None


class BisectCompare(BaseModel):
    state: str                                      # COMPARE_STATES
    reason: Optional[str] = None                    # COMPARE_REASONS; None when fetched
    host: Optional[str] = None                      # "github" | "gitlab" (which connector answered)
    cached: bool = False
    commit_count: int = 0                           # commits the compare returned (<= _MAX_COMMITS = 100)
    truncated: bool = False                         # commit_count == 100: the host's list was capped
    commits: list[BisectCommit] = Field(default_factory=list)   # ranked, <= LISTED_COMMITS_MAX
    has_location_signal: bool = False
    caveat: Optional[str] = None                    # MONOREPO_CAVEAT when fetched
    run_level_commit_count: Optional[int] = None    # len(run_commit_ranges.commits) of the run; None = no stored range


class BisectResponse(BaseModel):
    run_id: uuid.UUID
    fingerprint: str
    test_name: str
    state: str                                      # BISECT_STATES
    series_branch: Optional[str] = None             # the run's branch as stored; None = "runs without a branch"
    anchor: Optional[str] = None                    # ANCHOR_CLASSES
    fallback_branch: Optional[str] = None           # "main" / "master"
    good: Optional[BisectRunRef] = None
    bad: Optional[BisectRunRef] = None
    streak_length: int = 0                          # failing runs from bad to this run, inclusive (untested not counted)
    untested_runs: list[BisectRunRef] = Field(default_factory=list)   # strictly between good and bad, newest first, <= UNTESTED_LIST_MAX
    untested_count: int = 0                         # all of them
    runs_read: int = 0
    bounded: bool = False                           # runs_read == BISECT_MAX_RUNS
    narrowed_from: str = "unavailable"              # the run's run_commit_ranges.base_source (BASE_SOURCES), else "unavailable"
    compare: BisectCompare
    generated_at: datetime


# ── Configuration drift (W23b FAIL-13) ───────────────────────
# Returned as "config_drift" inside the regression-diff answer (run_diff_service.config_drift).
# Enum-like fields are plain str; their values live in services/run_context_vocab.py.


class DriftEntry(BaseModel):
    section: str                                    # DRIFT_SECTIONS
    key: str                                        # "branch", "ocp_namespace", "environment", "python", "poetry.lock", an image name, an env key
    change: str                                     # DRIFT_CHANGES
    old: Optional[str] = None                       # dependencies: the first 12 hex of each hash
    new: Optional[str] = None


class ConfigDrift(BaseModel):
    baseline_run_id: uuid.UUID
    baseline_build_number: Optional[str] = None
    context_state: str                              # CONTEXT_STATES
    entries: list[DriftEntry] = Field(default_factory=list)   # DRIFT_SECTIONS order, keys ascending inside a section, <= DRIFT_ENTRIES_MAX
    entries_total: int = 0


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
    # W12 SRCH-02: ``chroma_collection`` is the Collection PREFIX. The prefix the
    # stored value means ("" for "", the legacy default "testlookup_embeddings",
    # or a value that is not a valid prefix), the note when a stored value is
    # ignored, and the ACTIVE generation's prefix (None when the state is unreadable).
    collection_prefix: str = ""
    collection_prefix_note: Optional[str] = None     # PREFIX_INVALID_NOTE when the stored value is ignored
    active_collection_prefix: Optional[str] = None


class StorageConfigUpdate(BaseModel):
    """Payload for updating storage config. None = keep existing."""
    storage_backend: Optional[Literal["minio", "s3", "local"]] = None
    chroma_host: Optional[str] = Field(None, min_length=1, max_length=255)
    chroma_port: Optional[int] = Field(None, ge=1, le=65535)
    # W12 SRCH-02: LOOSENED only (min_length 1 -> 0, so "" clears the prefix).
    # Never tighten it: storage_config_service re-validates every STORED row
    # through this model, and a row that fails drops EVERY storage override to
    # the env defaults. The prefix rule is checked in PUT /settings/storage on a
    # change only.
    chroma_collection: Optional[str] = Field(None, min_length=0, max_length=255)
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


class InviteProjectMembership(BaseModel):
    """W17b ADM-01: a project the invitee joins on accept, with its project role."""
    project_id: uuid.UUID
    role: UserRole = UserRole.QA_ENGINEER


class InviteUserRequest(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.QA_ENGINEER
    project_memberships: List[InviteProjectMembership] = Field(default_factory=list, max_length=50)


class InviteUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    expires_at: datetime
    #: ``{public_base_url}/accept-invite#token=…`` — shown once; only its hash is stored.
    invitation_link: str
    #: True when the link was also emailed (SMTP configured and the send worked).
    email_sent: bool = False
    model_config = ConfigDict(from_attributes=True)


class InvitationMembershipView(BaseModel):
    project_id: uuid.UUID
    project_name: Optional[str] = None
    role: str


class InvitationSummary(BaseModel):
    """W17b ADM-01: one row of the Pending invitations tab. Never carries a token."""
    id: uuid.UUID
    email: str
    role: UserRole
    #: ``pending`` | ``expired`` | ``used`` | ``revoked``
    status: str
    expires_at: datetime
    created_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    invited_by_name: Optional[str] = None
    project_memberships: List[InvitationMembershipView] = Field(default_factory=list)


class InvitationTokenRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=256)


class InvitationPreviewResponse(BaseModel):
    email: str
    role: UserRole
    invited_by_name: Optional[str] = None
    expires_at: datetime
    project_names: List[str] = Field(default_factory=list)
    #: SSO is enforced and this role may not hold a password: sign in with SSO instead.
    sso_required: bool = False


class AcceptInvitationRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=256)
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = Field(None, max_length=255)
    password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)


class ResetLinkRequest(BaseModel):
    email: EmailStr


class RedeemResetLinkRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)


class PasswordResetLinkResponse(BaseModel):
    """W17b ADM-02: an admin-issued reset link. The admin never sees or sets the password."""
    link: str
    email_sent: bool = False
    expires_at: datetime


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
    # W05 REL-02: what a picker needs to leave out the synthetic per-project QA
    # lead, service accounts and deactivated users (a release owner is a person).
    is_service_account: bool = False
    is_synthetic: bool = False
    is_active: bool = True
    # W20 ADM-09: NULL = granted by a person; "scim:<group>" = granted through that SCIM group.
    source: Optional[str] = None
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
    # Policy context (ENT-02). These describe the STORED decision — what the
    # release-risk agent used when it wrote the row. They are not necessarily
    # the provenance of ``recommendation`` below; see ``band_policy_*``.
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    policy_level: Optional[str] = None  # "project" | "system" | "hardcoded"
    # The policy applied at READ time, which produced ``release_readiness_band``
    # and may have downgraded ``recommendation``.
    #
    # Publishing a policy was measured moving a live run from GO to
    # CONDITIONAL_GO while the three fields above still reported
    # ``policy_level: "hardcoded"`` with a null ``policy_id`` — accurate about
    # the stored decision, and the wrong answer to "which policy produced this
    # verdict" (TL-2026-09-19-01-008). ``None`` when no policy is active, which
    # is also when ``release_readiness_band`` is ``None``.
    band_policy_id: Optional[str] = None
    band_policy_version: Optional[int] = None
    band_policy_level: Optional[str] = None  # "project" | "system"
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
    # E8.4: the human-review gate on the value CI reads (section 8.2). While
    # the gate is enforced, an unreviewed AI decision reads PENDING_REVIEW (or
    # ADVISORY_<value> with allow_advisory) and the model's value moves here.
    requires_human_review: bool = False
    review: Optional[ReviewBlock] = None
    review_gate_enforced: bool = False
    draft_recommendation: Optional[str] = None
    # HON-06: when the stored ``ReleaseDecision`` row was written and last
    # changed. The gate's "How this was decided" timeline dates the decision
    # from these instead of inventing a time. ``None`` on a synthesised
    # quick-look view: no decision has been stored, so there is no time.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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


class FlakyScoreComponent(BaseModel):
    """One of the four signals of the nightly flakiness score (W09 FLK-03)."""
    name: str                                    # flaky_score_brief.COMPONENT_ORDER
    value: float                                 # 0..1
    weight: float                                # 0..1


class FlakyScoreBrief(BaseModel):
    """The four-signal score of a Detected row; plain ``str`` vocabularies (W09 FLK-03)."""
    status: str                                  # "scored" | "insufficient" | "pending"
    score: Optional[float] = None                # None unless "scored"
    confidence: str = "none"                     # flaky_score_service.CONFIDENCE_* ("none" | "low" | "medium" | "high")
    observations: int = 0
    min_observations: int = 5                    # flaky_score_service.MIN_OBSERVATIONS, sent so the UI never hard-codes it
    window_days: int = 30
    computed_at: Optional[str] = None            # ISO UTC; None unless "scored"
    components: List[FlakyScoreComponent] = []   # [] unless "scored"


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
    # W09 FLK-03: the nightly four-signal score (``flaky_score_brief``). None =
    # the enrichment did not run, or failed.
    flaky_score: Optional[FlakyScoreBrief] = None


class FlakyCoachScope(BaseModel):
    """What each number in the leaderboard is scoped to.

    A filtered list LOOKS release-scoped, and here only half of it is:
    membership is, the impact score is not. Stated in the payload rather than
    only in the docs, because the number is what gets read — a reader who takes
    a score as "how flaky during 2.4.0" would be wrong, and nothing in a bare
    filtered list would tell them.
    """

    membership: str = "project"
    score: str = "project_window"
    release_id: Optional[str] = None
    note: Optional[str] = None


class FlakyCoachResponse(BaseModel):
    """Project-level flaky coach leaderboard."""
    project_id: str
    total_flaky: int = 0
    quarantine_candidates: int = 0
    entries: List[FlakyCoachEntry] = []
    #: Additive: absent-by-default shape is unchanged for existing consumers.
    scope: Optional[FlakyCoachScope] = None


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
    # EXEC-03: the execution timing of this run's row (all null when the
    # format carried none -- JUnit, xUnit -- never 0).
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    setup_ms: Optional[int] = None
    call_ms: Optional[int] = None
    teardown_ms: Optional[int] = None
    worker_id: Optional[str] = None
    timing_source: Optional[str] = None
    # AIH-05 (additive): the AI analysis of this point's test result; null
    # when it has none (a passed test) or an older server answered.
    ai: Optional[TestHistoryAI] = None


class TestCaseDurationStatsResponse(BaseModel):
    """EXEC-03: duration percentiles of the test over the history window
    (every execution in the window, not only the 50 points returned)."""
    window_days: int = 30
    n: int = 0
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[int] = None
    measured: bool = False
    reason: Optional[str] = None


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
    # EXEC-03 (additive): window percentiles and the project baseline.
    duration_stats: Optional[TestCaseDurationStatsResponse] = None
    #: Null when the test has no duration baseline in this project.
    baseline: Optional[TestDurationBaselineResponse] = None
    #: AIH-05 (additive): points where the AI changed its category or root
    #: cause versus the previous point that had an analysis (oldest -> newest).
    ai_changes: List[TestHistoryAIChange] = []


# ── SSO / SAML / SCIM Schemas (ENT-01) ──────────────────────────────────────


class SSOConfigCreate(BaseModel):
    """Create a new SSO configuration.

    W20 ADM-08b: a SAML config needs the IdP entity id / SSO URL / certificate and
    the SP entity id / ACS URL; an OIDC config needs the issuer, client id and client
    secret (write-only — stored in ``secret_refs``, never returned).
    """
    display_name: str = Field(..., min_length=2, max_length=255)
    provider_type: SSOProviderType = SSOProviderType.SAML
    idp_entity_id: Optional[str] = Field(None, min_length=1, max_length=1000)
    idp_sso_url: Optional[str] = Field(None, min_length=1, max_length=2000)
    idp_slo_url: Optional[str] = Field(None, max_length=2000)
    idp_certificate: Optional[str] = Field(None, min_length=1)  # PEM-encoded X.509
    sp_entity_id: Optional[str] = Field(None, min_length=1, max_length=1000)
    sp_acs_url: Optional[str] = Field(None, min_length=1, max_length=2000)
    audience: Optional[str] = Field(None, max_length=1000)
    oidc_issuer: Optional[str] = Field(None, min_length=1, max_length=1000)
    oidc_client_id: Optional[str] = Field(None, min_length=1, max_length=255)
    oidc_client_secret: Optional[str] = Field(None, min_length=1, max_length=4000)
    oidc_scopes: Optional[str] = Field(None, max_length=500)
    oidc_groups_claim: Optional[str] = Field(None, max_length=255)
    role_mapping: Optional[dict] = None
    default_role: UserRole = UserRole.VIEWER
    group_attribute: Optional[str] = Field(None, max_length=255)
    enforcement_mode: SSOEnforcementMode = SSOEnforcementMode.OPTIONAL

    @model_validator(mode="after")
    def _fields_for_provider(self) -> "SSOConfigCreate":
        required: tuple[str, ...]
        if self.provider_type == SSOProviderType.OIDC:
            required = ("oidc_issuer", "oidc_client_id", "oidc_client_secret")
        else:
            required = ("idp_entity_id", "idp_sso_url", "idp_certificate", "sp_entity_id", "sp_acs_url")
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(f"A {self.provider_type.value} configuration needs: {', '.join(missing)}")
        return self


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
    oidc_issuer: Optional[str] = Field(None, min_length=1, max_length=1000)
    oidc_client_id: Optional[str] = Field(None, min_length=1, max_length=255)
    # Write-only: a non-empty value replaces the stored secret; omit it to keep the current one.
    oidc_client_secret: Optional[str] = Field(None, min_length=1, max_length=4000)
    oidc_scopes: Optional[str] = Field(None, max_length=500)
    oidc_groups_claim: Optional[str] = Field(None, max_length=255)
    enforcement_mode: Optional[SSOEnforcementMode] = None
    is_active: Optional[bool] = None


class SSOConfigResponse(BaseModel):
    """SSO configuration response (certificate is masked)."""
    id: uuid.UUID
    display_name: str
    provider_type: SSOProviderType
    idp_entity_id: Optional[str] = None
    idp_sso_url: Optional[str] = None
    idp_slo_url: Optional[str] = None
    idp_certificate_fingerprint: str = ""  # SHA-256 fingerprint, not the full cert
    sp_entity_id: Optional[str] = None
    sp_acs_url: Optional[str] = None
    audience: Optional[str] = None
    oidc_issuer: Optional[str] = None
    oidc_client_id: Optional[str] = None
    oidc_scopes: Optional[str] = None
    oidc_groups_claim: Optional[str] = None
    #: The client secret itself is never returned — only whether one is stored.
    oidc_client_secret_set: bool = False
    #: Where the IdP must send the browser back (register it as the client's redirect URI).
    oidc_redirect_uri: Optional[str] = None
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


class SSORolePreviewRequest(BaseModel):
    """W20 ADM-08a: preview the role a user in ``groups`` gets. ``role_mapping`` / ``default_role`` preview an
    unsaved edit; omitted, the stored configuration answers."""
    groups: list[str] = Field(default_factory=list, max_length=200)
    role_mapping: Optional[dict[str, str]] = None
    default_role: Optional[str] = None


class SSORolePreviewResponse(BaseModel):
    role: str
    matched_groups: list[str]
    source: Literal["mapping", "default"]
    ceiling: str
    clamped: bool


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


class SSOExchangeRequest(BaseModel):
    """The one-time code the ACS hands the browser after an SSO sign-in."""
    code: str = Field(..., min_length=1, max_length=128)


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
    primary: bool = False


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
    def require_storable_user_fields(self):
        if sum(email.primary for email in self.emails) > 1:
            raise ValueError("emails must contain at most one primary value")
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
    scimType: Optional[str] = None


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
    # flaky_recurrence | open_defect_limit | dimension_ceiling | override_rules
    # | perf_regression (EXEC-05, opt-in: absent from every policy unless
    # added; params ``{max_spikes, min_sigma, suite_ratio,
    # max_suite_regressions, action}`` over the run's stored duration
    # findings; an unmeasured run never trips it).
    type: str
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
    yellow = GO with watch, orange = CONDITIONAL, red = NO_GO.

    A breached hard cap (``PolicyHardCaps``) does **not** step the band down
    one or two notches — it pins the band straight to red and forces NO_GO,
    whatever the pass rate was. The stepped behaviour was abandoned because
    yellow still mapped to GO, which made a "hard cap" advisory at best.
    """
    orange_min: float = Field(default=90.0, ge=0, le=100)
    yellow_min: float = Field(default=95.0, ge=0, le=100)
    green_min: float = Field(default=99.0, ge=0, le=100)


class PolicyHardCaps(BaseModel):
    """Hard caps that downgrade the pass-rate band before the verdict map.

    Each cap is a (count) threshold. **Breaching any cap pins the band to red
    and forces NO_GO**, regardless of how green the pass rate was — a hard cap
    is a hard blocker, not a one-step downgrade. ``downgrades`` records which
    caps fired.

    **Zero does not mean the same thing for every cap**, and the difference is
    load-bearing, so it is stated per field below rather than summarised here.
    ``max_p0_defects=0`` means "no P0 defects allowed" and blocks on the first
    one; ``max_flaky_count=0`` and ``max_new_failures_24h=0`` *disable* their
    caps, because a literal zero would over-fire on real projects. That
    asymmetry is deliberate and pinned by
    ``tests/test_classify_with_policy.py``.
    """
    max_p0_defects: int = Field(
        default=0,
        ge=0,
        description=(
            "Active P0 defects allowed before the gate blocks. 0 means NONE "
            "allowed — one open P0 forces red/NO_GO. This cap cannot be "
            "disabled by setting it to 0; raise it to permit P0 defects."
        ),
    )
    max_flaky_count: int = Field(
        default=10,
        ge=0,
        description=(
            "Flaky tests allowed before the gate blocks. 0 DISABLES this cap "
            "(any flaky count passes) rather than forbidding flakiness — set 1 "
            "to block on the first flaky test."
        ),
    )
    max_new_failures_24h: int = Field(
        default=20,
        ge=0,
        description=(
            "New failures in the last 24h allowed before the gate blocks. 0 "
            "DISABLES this cap rather than forbidding new failures — set 1 to "
            "block on the first one."
        ),
    )


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
    # W11 GATE-02: filled by the two history routes only (one users read);
    # every other route leaves them None.
    created_by_name: Optional[str] = None
    activated_by_name: Optional[str] = None
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
    # ``None`` means "not measurable" — no failure in the sample could be
    # located, so the ratio has an empty denominator. Distinct from 0.0,
    # which means "rules exist and cover none of the located failures".
    coverage_pct: Optional[float] = None
    lookback_days: int = 30
    # W07 FAIL-08: the project's Java source roots (``Project.source_roots``).
    source_roots: int = 0


# ── W07 FAIL-08: Java source roots (``Project.source_roots``) ──


class SourceRoot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Dotted Java package prefix; "" matches every package.
    prefix: str = Field("", max_length=255)
    # Repo-relative source root, e.g. "payments/src/test/java"; shape checked by the service.
    root: str = Field(..., max_length=500)


class SourceRootsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The count (<= 20) is checked by the service: one 422 text.
    roots: list[SourceRoot] = Field(default_factory=list)


class SourceRootsResponse(BaseModel):
    roots: list[SourceRoot]
    max_roots: int = 20


class SourceRootsPreview(BaseModel):
    # Recent FAILED/BROKEN rows read (<= sample_cap).
    sampled: int
    # Of those, rows whose trace holds a Java frame.
    java_frames: int
    # Java rows the roots map to a repo path.
    located: int
    # Located rows a path rule matches.
    matched: int
    lookback_days: int
    # <= 5 {"test_name", "path"}: text.
    examples: list[dict[str, str]] = []


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


# VIZ-609: ``summary_report`` and ``explore`` join. Never REMOVE a value: ``page``
# is an unconstrained String(50) column, and one row carrying a retired value
# would 422 the whole list endpoint, which declares this Literal as its filter.
# W12 SRCH-03: ``search`` joins (saved searches). W13 TCL-01: ``test_management``
# joins (saved case views). Append-only.
SAVED_VIEW_PAGES = Literal[
    "dashboard", "trends", "coverage", "defects", "failures", "summary_report", "explore", "search",
    "test_management",
]
MAX_SAVED_VIEW_INSTANCES = 12

#: VIZ-609: ``filters`` is stored as JSON with no size limit of its own. 32 KB
#: holds 12 widget instances with room to spare; anything larger is not a view.
MAX_SAVED_VIEW_FILTERS_BYTES = 32 * 1024

#: The ``kind`` a named report view carries (the saved-views manager), and
#: W12 SRCH-03's saved search (``page = "search"``, ``filters = {kind, q,
#: scope, mode}``). A layout row (``useAnalyticsView``) has no kind. W13 TCL-01:
#: the saved case view (``page = "test_management"``, ``filters = {kind,
#: statuses, priorities, types, owner, automation, suite, q, ai_generated,
#: requirement}``). Append-only.
SAVED_VIEW_KINDS = ("report_view", SAVED_SEARCH_KIND, TEST_CASE_VIEW_KIND)

#: W08 TRND-10: ``filters.charts`` holds the customised charts of a view's page,
#: one entry per chart key (``trends.suite-series``, ``trends.compare``) and
#: ONLY for charts not at their default; the key is omitted when none is.
MAX_SAVED_VIEW_CHARTS = 8
MAX_SAVED_VIEW_CHART_BYTES = 2048
SAVED_VIEW_CHART_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _check_string_list(filters: dict, key: str) -> None:
    value = filters.get(key)
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"filters.{key} must be a list of non-empty strings")
    if len(value) > 50:
        raise ValueError(f"filters.{key} cannot hold more than 50 values")


def _check_saved_view_charts(filters: dict) -> None:
    """W08 TRND-10: ``filters.charts``, when present.

    An object of at most :data:`MAX_SAVED_VIEW_CHARTS` keys, each matching
    :data:`SAVED_VIEW_CHART_KEY`, each value an object carrying an integer
    ``version`` >= 1 and at most :data:`MAX_SAVED_VIEW_CHART_BYTES` as compact
    JSON. What a config holds beyond ``version`` is the page's to read (it
    falls back to its default, and says so, when a stored config no longer
    validates). One error text per rule.
    """
    import json  # noqa: PLC0415

    if "charts" not in filters:
        return
    charts = filters["charts"]
    if not isinstance(charts, dict):
        raise ValueError("filters.charts must be an object of chart configurations")
    if len(charts) > MAX_SAVED_VIEW_CHARTS:
        raise ValueError(f"filters.charts holds at most {MAX_SAVED_VIEW_CHARTS} charts")
    for key, config in charts.items():
        if not isinstance(key, str) or not SAVED_VIEW_CHART_KEY.match(key):
            raise ValueError(
                "filters.charts keys are 1-64 characters: a lower-case letter, then "
                "lower-case letters, digits, '.', '_' or '-'"
            )
        if not isinstance(config, dict):
            raise ValueError("filters.charts values must be objects")
        version = config.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("filters.charts values need a whole-number version of 1 or more")
        size = len(json.dumps(config, separators=(",", ":"), default=str).encode("utf-8"))
        if size > MAX_SAVED_VIEW_CHART_BYTES:
            raise ValueError(
                f"filters.charts values hold at most {MAX_SAVED_VIEW_CHART_BYTES} bytes each"
            )


def _validate_saved_search_filters(filters: dict) -> dict:
    """W12 SRCH-03: a saved search's ``filters`` (``kind == "search"`` ONLY).

    The keys are exactly ``{kind, q, scope, mode}``: ``q`` a string, stripped,
    1 to ``MAX_QUERY_CHARS`` characters; ``scope`` in ``SEARCH_SCOPES``;
    ``mode`` in ``SEARCH_MODES``. One sentence per rule (``core/search_vocab``),
    raised as the error's whole message. The stored ``q`` is the stripped text.
    """
    if set(filters) - SAVED_SEARCH_KEYS:
        raise PydanticCustomError("saved_search", SAVED_SEARCH_KEYS_SENTENCE)
    q = filters.get("q")
    if not isinstance(q, str) or not 1 <= len(q.strip()) <= MAX_QUERY_CHARS:
        raise PydanticCustomError("saved_search", SAVED_SEARCH_Q_SENTENCE)
    scope = filters.get("scope")
    if not isinstance(scope, str) or scope not in SEARCH_SCOPES:
        raise PydanticCustomError("saved_search", SAVED_SEARCH_SCOPE_SENTENCE)
    mode = filters.get("mode")
    if not isinstance(mode, str) or mode not in SEARCH_MODES:
        raise PydanticCustomError("saved_search", SAVED_SEARCH_MODE_SENTENCE)
    return {"kind": SAVED_SEARCH_KIND, "q": q.strip(), "scope": scope, "mode": mode}


def _case_view_refusal(key: str) -> PydanticCustomError:
    return PydanticCustomError("saved_case_view", CASE_VIEW_VALUE_SENTENCE.format(key=key))


def _case_view_list(filters: dict, key: str, allowed: tuple[str, ...] | None) -> list[str] | None:
    """A list of strings, stripped, de-duplicated (order kept); None when absent or empty."""
    value = filters.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > CASE_FILTER_MAX_VALUES:
        raise _case_view_refusal(key)
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _case_view_refusal(key)
        item = item.strip()
        if allowed is not None and item not in allowed:
            raise _case_view_refusal(key)
        if allowed is None and not 1 <= len(item) <= CASE_VIEW_TYPE_MAX:
            raise _case_view_refusal(key)
        if item not in out:
            out.append(item)
    return out or None


def _case_view_text(filters: dict, key: str, max_chars: int) -> str | None:
    value = filters.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= max_chars:
        raise _case_view_refusal(key)
    return value.strip()


def _validate_case_view_filters(filters: dict) -> dict:
    """W13 TCL-01: a saved case view's ``filters`` (``kind == "test_case_view"`` ONLY).

    Keys within ``CASE_VIEW_KEYS``; ``statuses`` lifecycle values, ``priorities``
    ``CASE_PRIORITIES``, ``types`` strings of 1-50 characters, each list at most
    ``CASE_FILTER_MAX_VALUES``; ``owner`` None, ``"me"`` or a user id;
    ``automation`` None or ``CASE_AUTOMATION_FILTERS``; ``suite`` 1-500 and
    ``requirement`` 1-200 characters; ``q`` 1-200 characters, stripped;
    ``ai_generated`` None or a bool. One sentence per rule
    (``core/test_library_vocab``). Returns the normalised dict: strings
    stripped, lists de-duplicated with their order kept, empty lists dropped,
    a user id in its canonical form. ``owner: "me"`` means the READER.
    """
    if set(filters) - CASE_VIEW_KEYS:
        raise PydanticCustomError("saved_case_view", CASE_VIEW_KEYS_SENTENCE)
    out: dict[str, Any] = {"kind": TEST_CASE_VIEW_KIND}
    for key, allowed in (("statuses", LIFECYCLE_STATES), ("priorities", CASE_PRIORITIES), ("types", None)):
        values = _case_view_list(filters, key, allowed)
        if values:
            out[key] = values
    if "owner" in filters:
        owner = filters["owner"]
        if owner is not None:
            if not isinstance(owner, str):
                raise _case_view_refusal("owner")
            owner = owner.strip()
            if owner != OWNER_ME:
                try:
                    owner = str(uuid.UUID(owner))
                except ValueError:
                    raise _case_view_refusal("owner") from None
        out["owner"] = owner
    if "automation" in filters:
        automation = filters["automation"]
        if automation is not None and automation not in CASE_AUTOMATION_FILTERS:
            raise _case_view_refusal("automation")
        out["automation"] = automation
    for key, max_chars in (("suite", CASE_VIEW_SUITE_MAX), ("q", CASE_VIEW_Q_MAX),
                           ("requirement", CASE_VIEW_REQUIREMENT_MAX)):
        if key in filters:
            out[key] = _case_view_text(filters, key, max_chars)
    if "ai_generated" in filters:
        ai_generated = filters["ai_generated"]
        if ai_generated is not None and not isinstance(ai_generated, bool):
            raise _case_view_refusal("ai_generated")
        out["ai_generated"] = ai_generated
    return out


def _validate_saved_view_filters(filters: dict) -> dict:
    """Reject malformed analytics layouts while preserving legacy filters.

    VIZ-609: a 32 KB cap, and the keys the saved-views manager writes
    (``kind``, ``release_ids``, ``suites``, ``window``) are type-checked. Other
    keys stay free-form: ``filters`` has always been an open object (MCP
    clients and older views store ``severity``, ``category``, ``days`` ...),
    so a key allow-list would 422 views that exist today. W08 TRND-10 adds
    ``charts`` (:func:`_check_saved_view_charts`).
    """
    import json  # noqa: PLC0415

    size = len(json.dumps(filters, separators=(",", ":"), default=str).encode("utf-8"))
    if size > MAX_SAVED_VIEW_FILTERS_BYTES:
        raise ValueError(
            f"filters is {size} bytes; a saved view holds at most {MAX_SAVED_VIEW_FILTERS_BYTES}"
        )
    kind = filters.get("kind")
    if kind is not None and kind not in SAVED_VIEW_KINDS:
        raise ValueError(f"filters.kind must be one of {', '.join(SAVED_VIEW_KINDS)}")
    if kind == SAVED_SEARCH_KIND:
        return _validate_saved_search_filters(filters)
    if kind == TEST_CASE_VIEW_KIND:
        return _validate_case_view_filters(filters)
    _check_string_list(filters, "release_ids")
    _check_string_list(filters, "suites")
    window = filters.get("window")
    if window is not None and (isinstance(window, bool) or not isinstance(window, int) or not 1 <= window <= 365):
        raise ValueError("filters.window must be a whole number of days, 1 to 365")
    _check_saved_view_charts(filters)
    for key in ("instances", "widgets"):
        if key not in filters:
            continue
        value = filters[key]
        if not isinstance(value, list):
            raise ValueError(f"filters.{key} must be a list")
        if len(value) > MAX_SAVED_VIEW_INSTANCES:
            raise ValueError(
                f"filters.{key} cannot contain more than {MAX_SAVED_VIEW_INSTANCES} widgets"
            )
        if key == "widgets" and any(
            not isinstance(item, str) or not item for item in value
        ):
            raise ValueError("filters.widgets entries must be non-empty strings")
        if key == "instances":
            for item in value:
                if not isinstance(item, dict):
                    raise ValueError("filters.instances entries must be objects")
                if (
                    not isinstance(item.get("instanceId"), str)
                    or not item["instanceId"]
                ):
                    raise ValueError("each saved widget instance requires instanceId")
                if (
                    not isinstance(item.get("templateId"), str)
                    or not item["templateId"]
                ):
                    raise ValueError("each saved widget instance requires templateId")
    return filters


class SavedViewCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    page: Optional[SAVED_VIEW_PAGES] = None
    filters: dict = Field(default_factory=dict)
    is_shared: bool = False
    is_default: bool = False

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, value: dict) -> dict:
        return _validate_saved_view_filters(value)

    @model_validator(mode="after")
    def saved_search_rules(self) -> "SavedViewCreate":
        """W12 SRCH-03: a saved search (``filters.kind == "search"``) belongs
        to ``page = "search"``, and one saved for All Projects (no
        ``project_id``) cannot be shared: ``list_saved_views`` shows a shared
        row only to members of its project. Every other kind is untouched.

        W13 TCL-01: the same two rules for a saved case view
        (``filters.kind == "test_case_view"``): ``page = "test_management"``,
        and sharing needs a project."""
        if self.filters.get("kind") == TEST_CASE_VIEW_KIND:
            if self.page != TEST_CASE_VIEW_PAGE:
                raise PydanticCustomError("saved_case_view", CASE_VIEW_PAGE_SENTENCE)
            if self.is_shared and self.project_id is None:
                raise PydanticCustomError("saved_case_view", CASE_VIEW_SHARE_NEEDS_PROJECT)
            return self
        if self.filters.get("kind") != SAVED_SEARCH_KIND:
            return self
        if self.page != "search":
            raise PydanticCustomError("saved_search", SAVED_SEARCH_PAGE_SENTENCE)
        if self.is_shared and self.project_id is None:
            raise PydanticCustomError("saved_search", SAVED_SEARCH_SHARE_NEEDS_PROJECT)
        return self


class SavedViewUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    page: Optional[SAVED_VIEW_PAGES] = None
    filters: Optional[dict] = None
    is_shared: Optional[bool] = None
    is_default: Optional[bool] = None

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, value: Optional[dict]) -> Optional[dict]:
        return _validate_saved_view_filters(value) if value is not None else value


class SavedViewRelease(BaseModel):
    """Whether THIS reader may apply the release stored in a view (S5-3f-ii).

    A saved view outlives the state it was saved in, and a shared view is read
    by people who did not save it — so the stored id is an id somebody else
    supplied. The verdict is reported rather than the view being refused:
    losing one filter is recoverable, refusing to open a view because one field
    went stale is not. ``reason`` is what lets the UI say the result set is
    wider than the view's author intended.
    """

    release_id: Optional[str] = None
    applied: bool = False
    reason: Optional[str] = None


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
    # Additive and optional: a view carrying no release resolves to None and
    # every existing consumer is unchanged.
    release: Optional[SavedViewRelease] = None
    model_config = ConfigDict(from_attributes=True)


# ── Timeline notes (W08 TRND-04) ─────────────────────────────────────────────
#
# ``280`` is written here because schemas must not import services; it equals
# ``timeline_note_service.NOTE_TEXT_MAX`` (``tests/test_timeline_notes.py``
# asserts the two copies agree). The service strips the text and owns every
# other rule (blank text, the day range, the list window), each a fixed 422.

TIMELINE_NOTE_TEXT_MAX = 280


class TimelineNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_on: date
    #: ``None`` = a one-day note (``ends_on := starts_on``).
    ends_on: Optional[date] = None
    #: Stripped by the service; blank is a 422.
    text: str = Field(..., max_length=TIMELINE_NOTE_TEXT_MAX)
    #: ``None`` = every suite of the project.
    suite_name: Optional[str] = Field(None, max_length=500)


class TimelineNoteUpdate(BaseModel):
    """Only the fields SENT are applied (``model_fields_set``)."""

    model_config = ConfigDict(extra="forbid")

    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    text: Optional[str] = Field(None, max_length=TIMELINE_NOTE_TEXT_MAX)
    suite_name: Optional[str] = Field(None, max_length=500)


class TimelineNoteResponse(BaseModel):
    id: str
    project_id: str
    #: YYYY-MM-DD, UTC days, inclusive.
    starts_on: str
    ends_on: str
    text: str
    suite_name: Optional[str] = None
    #: The author's full name or username; ``None`` for a deleted user.
    created_by_name: Optional[str] = None
    #: For THIS reader: the author, or a QA lead or above.
    can_edit: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TimelineNoteList(BaseModel):
    #: ``starts_on``, ``created_at``, ``id`` order; at most 200.
    items: List[TimelineNoteResponse]
    #: The notes overlapping the window, not ``len(items)``.
    total: int
    #: ``total`` > the 200 listed.
    truncated: bool
    #: The window asked, YYYY-MM-DD.
    start: str
    end: str


# ── Cross-run insights (W23a FAIL-11) ────────────────────────────────────────
# Enum-like fields are plain ``str`` over ``app.services.insight_vocab``; the
# routes refuse a value outside it with one fixed sentence.


class InsightLink(BaseModel):
    #: One of ``insight_vocab.INSIGHT_LINKS`` (server-built; the SPA re-checks).
    path: str = ""
    label: str = ""


class InsightItem(BaseModel):
    id: uuid.UUID
    #: ``insight_vocab.INSIGHT_KINDS``.
    kind: str
    #: ``insight_vocab.INSIGHT_SEVERITIES``.
    severity: str
    #: "open" (``resolved_at IS NULL``) | "resolved".
    status: str = "open"
    #: "cleared" | "stale".
    resolved_reason: Optional[str] = None
    #: ``insights_service.render_title(...)`` — a sentence; React text.
    title: str = ""
    subject_label: str = ""
    window_key: str = ""
    #: Per kind (numbers, ids, names).
    evidence: Dict[str, Any] = Field(default_factory=dict)
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: Optional[datetime] = None
    detection_count: int = 1
    dismissed_at: Optional[datetime] = None
    dismissed_by_name: Optional[str] = None
    link: InsightLink = Field(default_factory=InsightLink)
    #: perf_drift only: "/runs/<uuid>?tab=changes".
    run_path: Optional[str] = None


class InsightDetectorStatus(BaseModel):
    kind: str
    measured: bool = False
    #: A ``NOT_MEASURED`` sentence; ``None`` when measured.
    reason: Optional[str] = None
    #: ``None`` = never evaluated ("not_evaluated").
    evaluated_at: Optional[datetime] = None
    subjects_measured: int = 0
    firing: int = 0


class InsightListResponse(BaseModel):
    #: The project's ``cross_run_insights``.
    enabled: bool = False
    range: str = "7d"
    items: List[InsightItem] = Field(default_factory=list)
    #: Every row matching the filter (``items`` ≤ limit).
    total: int = 0
    #: Dismissed rows in the range (the "Show dismissed (n)" link).
    dismissed_total: int = 0
    #: ALWAYS four, in ``INSIGHT_KINDS`` order.
    detectors: List[InsightDetectorStatus] = Field(default_factory=list)
    generated_at: datetime


# ── Expected run cadences (W23a TRND-03) ─────────────────────────────────────
# TestLookup's own expectation (observe only). Enum-like fields are plain
# ``str`` over ``app.services.run_cadence_vocab``; the service refuses a value
# outside it with ``CADENCE_ERRORS`` (``{"code", "message"}``).

_HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class RunCadenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Blank -> ``None`` (any run of the project).
    suite_name: Optional[str] = Field(None, max_length=500)
    #: ``run_cadence_vocab.CADENCE_MODES``.
    mode: str = "weekly"
    #: ``WEEKDAYS`` literals, any order, de-duplicated by the service.
    weekdays: Optional[List[str]] = Field(None, max_length=7)
    #: "HH:MM", 24 h, local to ``timezone``.
    expected_by: Optional[str] = Field(None, pattern=_HHMM_PATTERN)
    cron: Optional[str] = Field(None, max_length=100)
    #: IANA name (validated against ``zoneinfo.available_timezones()``).
    timezone: str = Field(..., min_length=1, max_length=64)
    grace_minutes: int = Field(60, ge=0, le=720)


class RunCadencePatch(BaseModel):
    """Every field optional; an empty PATCH changes nothing."""

    model_config = ConfigDict(extra="forbid")

    #: JSON null keeps meaning "keep"; ``clear_suite`` sets "any run".
    suite_name: Optional[str] = Field(None, max_length=500)
    mode: Optional[str] = None
    weekdays: Optional[List[str]] = Field(None, max_length=7)
    expected_by: Optional[str] = Field(None, pattern=_HHMM_PATTERN)
    cron: Optional[str] = Field(None, max_length=100)
    timezone: Optional[str] = Field(None, min_length=1, max_length=64)
    grace_minutes: Optional[int] = Field(None, ge=0, le=720)
    #: True -> ``suite_name`` NULL ("any run"); with a ``suite_name`` too -> 422 ``suite_conflict``.
    clear_suite: bool = False


class RunCadencePauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: tz-aware (a naive value is FastAPI's 422); in (now, now + 90 d].
    until: AwareDatetime
    #: Stripped by the service; empty -> ``CADENCE_ERRORS["pause_reason_required"]``.
    reason: str = Field(..., max_length=200)


class RunCadenceRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    suite_name: Optional[str] = None
    mode: str = "weekly"
    weekdays: Optional[List[str]] = None
    expected_by: Optional[str] = None
    cron: Optional[str] = None
    timezone: str = "UTC"
    grace_minutes: int = 60
    #: ``run_cadence_schedule.describe(...)``: "Mon–Fri by 06:00 (Europe/Berlin) · grace 60 min".
    summary: str = ""
    effective_from: datetime
    #: ``paused_until > now``.
    paused: bool = False
    paused_at: Optional[datetime] = None
    paused_until: Optional[datetime] = None
    paused_reason: Optional[str] = None
    paused_by_name: Optional[str] = None
    #: The next expected-by instant (UTC), pause ignored.
    next_deadline: Optional[datetime] = None
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RunCadenceList(BaseModel):
    items: List[RunCadenceRead] = Field(default_factory=list)
    total: int = 0


class CadenceWindowStatus(BaseModel):
    deadline: datetime
    window_start: datetime
    window_end: datetime
    #: ``run_cadence_vocab.STATUS_OUTCOMES``.
    outcome: str
    #: True = the beat decided it (a ``run_cadence_windows`` row); False = computed now.
    recorded: bool = False
    run_count: Optional[int] = None
    #: "Wed 28 Oct" in the cadence's zone.
    local_label: str = ""


class RunCadenceStatusItem(BaseModel):
    cadence: RunCadenceRead
    #: False only when nothing could be judged (``reason`` says so); never "all met".
    measured: bool = False
    reason: Optional[str] = None
    #: ``expected`` = ``met`` + ``missed`` (paused / pending excluded).
    expected: int = 0
    met: int = 0
    missed: int = 0
    paused: int = 0
    pending: int = 0
    #: Newest first, ≤ ``MISSED_LIST_MAX``; ``missed`` is the full count.
    missed_windows: List[CadenceWindowStatus] = Field(default_factory=list)
    #: The newest matching run in the status window.
    last_run_at: Optional[datetime] = None


class RunCadenceStatusResponse(BaseModel):
    #: False = the project has no cadence ("No expected cadence configured").
    configured: bool = False
    days: int = 14
    window_start: datetime
    window_end: datetime
    items: List[RunCadenceStatusItem] = Field(default_factory=list)
    total: int = 0
    #: Sums over ``items``.
    expected: int = 0
    met: int = 0
    missed: int = 0
    paused: int = 0


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
    # W09 NTF-04: the bound view's name, snapshotted on every bind (0221). With
    # ``saved_view_id`` NULL it names a view that was deleted.
    saved_view_name: Optional[str] = None
    # W09 NTF-03 / NTF-04: set by ``digest_scope_service.decorate_subscriptions``.
    scope_label: Optional[str] = None
    scope_problem: Optional[str] = None          # digest_scope_service.SCOPE_PROBLEMS or None
    model_config = ConfigDict(from_attributes=True)


class DigestScopeInfo(BaseModel):
    """What a digest was filtered by (W09 NTF-04); ``digest_scope_service.scope_info``."""
    source: str                                  # digest_scope_service.SCOPE_SOURCES
    view_id: Optional[str] = None
    view_name: Optional[str] = None
    release_ids: List[str] = []
    release_names: List[str] = []
    suites: List[str] = []
    window_days: Optional[int] = None
    dropped_release_count: int = 0


# ── GATE-08b (W11): the digest's release verdict ──
class DigestReleaseVerdict(BaseModel):
    release_id: str
    release_name: str
    source: str                                                   # DIGEST_RELEASE_SOURCES: "subscription" | "active"
    verdict: Optional[str] = None                                 # GO | CONDITIONAL_GO | NO_GO | NOT_EVALUATED; None = never evaluated
    decided_at: Optional[str] = None                              # ISO-8601 UTC
    blocker_count: int = 0


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
    # W09 NTF-04: present when the digest was generated for a saved view (or one
    # that is gone); absent / None for a whole-project digest.
    scope: Optional[DigestScopeInfo] = None
    # W11 GATE-08b: set only by a caller that asks (generate_digest(...,
    # with_release_verdict=True)).
    release_verdict: Optional[DigestReleaseVerdict] = None


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


# ── W16 AGT-09 (b): candidate evaluation ─────────────────────────────────────
# Every enum-like field is a plain ``str``; the values live in
# ``services/ai_eval_vocab.py`` and the router judges them with one sentence.


class EvalCandidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: str = Field(min_length=2, max_length=20)  # CANDIDATE_TIERS; judged in the router
    model: Optional[str] = Field(None, max_length=200)  # an ASSERTION: must equal the effective llm model
    prompt_version: Optional[str] = Field(None, max_length=400)  # an ASSERTION: must equal current_prompt_tag()


class CandidateEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: EvalCandidateIn
    project_id: uuid.UUID  # budget + release policy + cost scope; resolve_project_scope
    limit: Optional[int] = Field(None, ge=1, le=200)  # <= MAX_CANDIDATE_ITEMS; default = min(item_count, 200)


class CandidateEvaluationAccepted(BaseModel):
    run_id: str
    status: str  # "queued"


class EvalCandidateResolved(BaseModel):
    """What the run ACTUALLY replays (stored in ``ai_eval_runs.candidate``)."""
    tier: str
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_version: Optional[str] = None
    offline_mode: bool
    requested: EvalCandidateIn


class CandidateTierOption(BaseModel):
    tier: str
    available: bool
    reason: Optional[str] = None  # the TIER_UNAVAILABLE sentence when unavailable
    model: Optional[str] = None
    provider: Optional[str] = None


class CandidateOptionsResponse(BaseModel):
    tiers: List[CandidateTierOption]
    prompt_version: Optional[str] = None
    offline_mode: bool
    max_items: int  # MAX_CANDIDATE_ITEMS
    supported_tiers_by_task: Dict[str, List[str]]  # TASK_TIERS


class EvalMetric(BaseModel):
    name: str
    value: Optional[float] = None
    n: int = 0
    measured: bool
    kind: str  # "score" | "sub_check"


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
    # W16 AGT-09 (b): every new field OPTIONAL and defaulted, so legacy rows,
    # fakes and an UNFLUSHED ORM row (whose server-default columns still read
    # None) validate; the validator below maps None to the default.
    metric_kind: Optional[str] = "label_agreement"
    metric_label: str = ""  # METRIC_LABELS[metric_kind]
    status: Optional[str] = "completed"
    error_code: Optional[str] = None
    error_text: Optional[str] = None  # RUN_ERROR_SENTENCES[error_code] when not given
    candidate: Optional[EvalCandidateResolved] = None
    project_id: Optional[uuid.UUID] = None
    requested_by: Optional[uuid.UUID] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    scored_items: Optional[int] = 0
    unscored_items: Optional[int] = 0
    error_items: Optional[int] = 0
    items_capped: Optional[bool] = False
    metrics: Optional[List[EvalMetric]] = None  # replay runs only; legacy rows: None
    measured: bool = True  # False when scored_items == 0 on a replay
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _fill_w16_defaults(self) -> "AIEvalRunResponse":
        from app.services.ai_eval_vocab import METRIC_LABELS, RUN_ERROR_SENTENCES  # noqa: PLC0415

        if self.metric_kind is None:
            self.metric_kind = "label_agreement"
        if self.status is None:
            self.status = "completed"
        if self.scored_items is None:
            self.scored_items = 0
        if self.unscored_items is None:
            self.unscored_items = 0
        if self.error_items is None:
            self.error_items = 0
        if self.items_capped is None:
            self.items_capped = False
        if not self.metric_label:
            self.metric_label = METRIC_LABELS.get(self.metric_kind, self.metric_kind)
        if self.error_text is None and self.error_code:
            self.error_text = RUN_ERROR_SENTENCES.get(self.error_code)
        if self.metric_kind == "replay" and not self.scored_items:
            # Absence is not health: a replay with nothing scored measured nothing.
            self.measured = False
        return self


class AIEvalRunListResponse(BaseModel):
    """GET /ai-eval/runs (was a bare list; no client read it)."""
    items: List[AIEvalRunResponse]
    total: int


class AIEvalItemOutputRead(BaseModel):
    item_index: int
    item_key: Optional[str] = None
    status: str  # ITEM_STATUSES
    error_code: Optional[str] = None
    error_text: Optional[str] = None  # ITEM_ERROR_SENTENCES
    input: Dict[str, Any] = Field(default_factory=dict)  # the dataset item's input, re-sanitized on read
    expected: Dict[str, Any]  # stored sanitized; re-sanitized on read
    output: Optional[Dict[str, Any]] = None
    score: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: Optional[int] = None
    tokens: Optional[int] = None
    mode_used: Optional[str] = None


class AIEvalItemListResponse(BaseModel):
    items: List[AIEvalItemOutputRead]
    total: int
    counts: Dict[str, int]  # by ITEM_STATUSES over the whole run


class AIEvalGateRunResponse(BaseModel):
    id: uuid.UUID
    change_id: str
    status: str
    manifest_checksum_sha256: str
    manifest: Dict[str, Any]
    gate_results: List[Dict[str, Any]]
    blocking_gates: List[Dict[str, Any]]
    version_changes: List[Dict[str, Any]]
    gate_type: Optional[str] = None
    project_id: Optional[uuid.UUID] = None
    agent_id: Optional[str] = None
    baseline_tier: Optional[str] = None
    candidate_tier: Optional[str] = None
    sample_count: Optional[int] = None
    evaluated_by: Optional[uuid.UUID] = None
    evaluated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AIEvalGateRunListResponse(BaseModel):
    """Agent-stack history (``gate_type IS NULL``) and the G2 history (W16)."""
    items: List[AIEvalGateRunResponse]
    total: int


class AIEvalManifestResponse(BaseModel):
    eval_manifest_checksum: str
    source: str
    status: str
    manifest: Dict[str, Any]
    gate_run_id: Optional[uuid.UUID] = None
    evaluated_at: Optional[datetime] = None


class ReviewerQualityRunRead(BaseModel):
    """An ``AIEvalReviewerQuality`` row WITHOUT its observation arrays (W16)."""
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: str
    source: str
    status: str
    window_started_at: datetime
    window_ended_at: datetime
    evaluated_at: datetime
    metrics: List[Dict[str, Any]]
    regressions: List[Dict[str, Any]]
    semantic_sample_counts: Dict[str, Any]
    clean_sample_count: int
    human_outcome_count: int
    deterministic_recall: Optional[float] = None
    second_model_recall: Optional[float] = None
    recall_delta: Optional[float] = None
    false_flag_rate: Optional[float] = None
    false_omission_rate: Optional[float] = None
    auto_disable_eligible: bool
    auto_disable_applied: bool
    model_config = ConfigDict(from_attributes=True)


class ReviewerQualityRunListResponse(BaseModel):
    items: List[ReviewerQualityRunRead]
    total: int


# ── W16 AGT-13: shadow pairs ─────────────────────────────────────────────────


class ShadowPairSource(BaseModel):
    kind: str  # "test_case" | "test_run"
    id: str
    label: Optional[str] = None  # the test name / "Run #<build>"
    run_id: Optional[str] = None
    build_number: Optional[str] = None
    href: Optional[str] = None  # built from ids only


class ShadowPairRead(BaseModel):
    id: str
    project_id: str
    agent_id: str
    agent_label: str  # SHADOW_AGENT_LABELS
    sample_key: str
    incumbent_tier: str
    candidate_tier: str
    incumbent_output: Dict[str, Any]  # re-sanitized on read
    candidate_output: Dict[str, Any]
    incumbent_tokens: int
    candidate_tokens: int
    label_status: str
    label: Optional[Dict[str, Any]] = None  # {verdict, failure_category?, source, feedback_id?}
    labelled_by_name: Optional[str] = None
    labelled_at: Optional[str] = None
    created_at: Optional[str] = None
    source: Optional[ShadowPairSource] = None
    expires_at: Optional[str] = None  # pending only: created_at + 30 d


class ShadowPairListResponse(BaseModel):
    items: List[ShadowPairRead]
    total: int  # COUNT(*) OVER () of the filtered set
    pending_total: int  # the project's pending pairs (the tab badge), any agent
    live_tier_pairs_seen: bool  # False = the project has never produced a pair


class ShadowPairLabelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str = Field(min_length=3, max_length=20)  # SHADOW_VERDICTS
    failure_category: Optional[str] = Field(None, max_length=40)  # root-cause agent only (SHADOW_LABEL_FIELDS)
    notes: Optional[str] = Field(None, max_length=500)  # sanitize_for_llm; stored in label.notes


class EvalProvenanceHealthResponse(BaseModel):
    window_days: int
    window_start: datetime
    window_end: datetime
    total_runs: int
    stamped_runs: int
    resolved_runs: int
    missing_checksum_count: int
    unresolvable_run_count: int
    unresolvable_checksums: List[str]
    has_unresolvable_checksums: bool


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
    eval_provenance: Optional[EvalProvenanceHealthResponse] = None


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
    canonical_url: Optional[str] = None


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
    canonical_url: Optional[str] = None


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


class RagCaseAcceptEdits(BaseModel):
    """Content fields a reviewer may change while accepting generated work."""

    title: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    objective: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    preconditions: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    steps: Optional[List[TestCaseStepSchema]] = None
    parameters: Optional[List[TestCaseParameterSchema]] = None
    expected_result: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_data: Optional[str] = Field(None, max_length=MAX_LONG_TEXT)
    test_type: Optional[str] = Field(None, max_length=50)
    priority: Optional[str] = Field(None, max_length=30)
    severity: Optional[str] = Field(None, max_length=30)
    feature_area: Optional[str] = Field(None, max_length=500)
    tags: Optional[List[str]] = None
    estimated_duration_minutes: Optional[int] = Field(None, ge=0)
    is_automated: Optional[bool] = None
    automation_status: Optional[str] = Field(None, max_length=30)

    model_config = ConfigDict(extra="forbid")


class BatchAcceptRequest(BaseModel):
    case_ids: List[uuid.UUID]
    edits: Optional[Dict[uuid.UUID, RagCaseAcceptEdits]] = None


class RejectCaseRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500)


class AcceptCaseRequest(BaseModel):
    edits: Optional[RagCaseAcceptEdits] = None


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
    # W17 ADM-07: the administrator's optional "why", kept on the audit row. Never a secret.
    reason: Optional[str] = Field(None, max_length=500)


class FeatureFlagUpdate(BaseModel):
    """Partial update.

    Omitted fields keep their value. Explicit null clears project/role
    allow-lists; for the scalar fields null is ignored.
    """
    description: Optional[str] = Field(None, max_length=2000)
    enabled_global: Optional[bool] = None
    enabled_projects: Optional[List[uuid.UUID]] = None
    enabled_roles: Optional[List[str]] = None
    rollout_percent: Optional[int] = Field(None, ge=0, le=100)
    # W17 ADM-07: the administrator's optional "why", kept on the audit row. Never a secret.
    reason: Optional[str] = Field(None, max_length=500)


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
    # W17 ADM-07: who last changed the flag (name, not only an id) and the reason
    # given with that change (None = none given, or a change before migration 0232).
    updated_by_name: Optional[str] = None
    last_change_reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class FeatureFlagHistoryEntry(BaseModel):
    """One audit row of a flag (W17 ADM-07): what changed, who, when and why. Field names only."""
    action: str
    actor_name: Optional[str] = None
    changed_fields: List[str] = Field(default_factory=list)
    reason: Optional[str] = None
    created_at: Optional[datetime] = None


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
    # AIC-06 overage (derived, display only; never enforced). None = not
    # computed: no enabled quota, or no included allowance (included_usd <= 0).
    overage_rate_usd: Optional[float] = None            # echoed whenever an enabled quota exists
    covered_usd: Optional[float] = None                 # min(spend, included)
    overage_usd: Optional[float] = None                 # max(0, spend - included)
    overage_charge_usd: Optional[float] = None          # overage_usd * overage_rate_usd
    projected_period_cost_usd: Optional[float] = None   # spend / elapsed_fraction
    projected_overage_usd: Optional[float] = None       # max(0, projected - included)
    projected_overage_charge_usd: Optional[float] = None  # projected_overage_usd * rate
    # "linear_elapsed_days" | "insufficient_elapsed_time" | "no_spend" | None.
    # Plain str, not a Literal.
    projection_basis: Optional[str] = None
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
    # AIC-06: the same derived overage fields as LlmUsageRead (all None for a
    # project without an enabled quota), plus included_usd, which this model
    # did not carry before.
    included_usd: Optional[float] = None
    overage_rate_usd: Optional[float] = None
    covered_usd: Optional[float] = None
    overage_usd: Optional[float] = None
    overage_charge_usd: Optional[float] = None
    projected_period_cost_usd: Optional[float] = None
    projected_overage_usd: Optional[float] = None
    projected_overage_charge_usd: Optional[float] = None
    projection_basis: Optional[str] = None


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
    # W09 FLK-02: the Quarantined tab warns past it; 0 = no limit. A PUT
    # replaces, as for every other field (omitted -> back to 8).
    max_active_quarantined: int = Field(8, ge=0, le=1000)


class QuarantineLifecyclePolicyResponse(BaseModel):
    project_id: uuid.UUID
    sla_days: int
    auto_create_defect: bool
    auto_promote: bool
    promote_after_passes: int
    detection_flip_rate_threshold: float
    detection_min_runs: int
    max_active_quarantined: int = 8
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
    # VIZ-204: the analytics envelope (contract C2). Declared because a
    # response_model drops undeclared keys; the contract module owns its shape.
    meta: Optional[Dict[str, Any]] = None


# ── W11 release planning: phases (REL-05 / REL-07), portfolio trend (REL-03),
# attribution (REL-09). releases.py keeps its inline PhaseIn / PhaseUpdate
# unchanged. Every enum-like field is a plain ``str``; each vocabulary is a
# constant in ``app.core.release_planning`` (or the module named beside it).


class ReleasePhasesCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_release_id: uuid.UUID                     # a release of the TARGET's project, with >= 1 phase
    shift_to: Optional[datetime] = None            # the earliest dated source phase's NEW planned_start;
                                                   # None -> the target's cutoff_start_at


class ReleasePhaseBulkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=255)          # stripped; unique (case-insensitive) in the batch AND the release
    phase_type: str = Field("qa_testing", max_length=50)          # PHASE_TYPES, else 422 PHASE_TYPE_SENTENCE
    description: Optional[str] = Field(None, max_length=4000)
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None                        # >= planned_start, else 422 PHASE_WINDOW_SENTENCE
    exit_criteria: Optional[dict] = None                          # a partial PolicyDocument (the phase-gate layer), as today
    notes: Optional[str] = Field(None, max_length=4000)


class ReleasePhasesBulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phases: List[ReleasePhaseBulkItem] = Field(..., min_length=1, max_length=MAX_BULK_PHASES)   # 30
    source: str = "manual"                                        # PHASE_DRAFT_SOURCES -- recorded in the activity row and metric only
    model_name: Optional[str] = Field(None, max_length=200)       # the drafting model, when source == "llm"


class ReleasePhasesCreatedResponse(BaseModel):
    release_id: uuid.UUID
    created: int
    phases: List[dict]                                            # serialize_model(phase) per created phase, in order_index order
    source_release_id: Optional[uuid.UUID] = None                 # clone only
    shifted_by_days: Optional[int] = None                         # clone only; None when no source phase had a date


class PhaseDraft(BaseModel):
    name: str                                                     # <= 255, stripped, never empty
    phase_type: str                                               # PHASE_TYPES
    description: Optional[str] = None                             # <= 2000
    exit_criteria_text: Optional[str] = None                      # <= 1000; TEXT the reader edits -- never a policy JSON
    planned_days: Optional[int] = None                            # 1..90; None = the PRD names no duration
    heading: Optional[str] = None                                 # the PRD heading it came from (rules), <= 255


class PhaseDraftResponse(BaseModel):
    release_id: uuid.UUID
    source: str                                                   # "rules" | "llm"
    model_name: Optional[str] = None                              # set iff source == "llm"
    fallback_reason: Optional[str] = None                         # DRAFT_FALLBACK_REASONS; set iff source == "rules" and the LLM
                                                                  # was not used (not tried, or tried and discarded)
    phases: List[PhaseDraft]                                      # <= MAX_BULK_PHASES; may be EMPTY (rules found no heading)
    warnings: List[str] = Field(default_factory=list)            # fixed sentences (DRAFT_WARNING_*), never PRD text
    text_chars: int                                               # characters read (after extraction)


class ReleasePortfolioTotals(BaseModel):                          # over the whole window
    evaluated: int                                                # releases with >= 1 release-level decision in the window
    go: int                                                       # ... whose LATEST decision in the window is GO
    conditional_go: int
    no_go: int
    not_evaluated: int
    released: int                                                 # releases with released_at in the window
    released_with_pack: int                                       # ... with >= 1 non-retired pack (retention_expires_at > now)


class ReleasePortfolioTrend(BaseModel):
    project_id: Optional[uuid.UUID] = None                        # None = every project the caller can see
    weeks: int                                                    # 4..26, default 12
    points_at: List[datetime]                                     # weeks + 1 instants: Mondays 00:00 UTC, oldest first, the LAST = now
    in_progress: List[Optional[int]]                              # per point; None before the scope's first release existed
    ready_to_ship: List[Optional[int]]
    blocked: List[Optional[int]]
    released_30d: List[Optional[int]]
    evaluated_in_week: List[int]                                  # per point: releases with a release-level decision in the 7 days before it
    go_in_week: List[int]                                         # ... whose latest decision in those 7 days is GO
    totals: ReleasePortfolioTotals
    approximated: List[str]                                       # PORTFOLIO_APPROXIMATED_SERIES: series built on today's status
    generated_at: datetime


class AttributionRuleField(BaseModel):
    value: str                                                    # AttributionMatchField value
    label: str                                                    # ATTRIBUTION_FIELD_LABELS[value]
    glob: bool                                                    # True: * and ? wildcards; False: exact, case-insensitive
    help: str                                                     # ATTRIBUTION_FIELD_HELP[value]


class AttributionRuleFieldsResponse(BaseModel):
    fields: List[AttributionRuleField]                            # in AttributionMatchField declaration order


class AttributionPreviewSample(BaseModel):
    run_id: str
    build_number: Optional[str] = None
    branch: Optional[str] = None
    environment: Optional[str] = None
    tags: List[str] = Field(default_factory=list)                 # NEW (first 10, each <= 100 chars)
    created_at: Optional[datetime] = None                         # NEW
    current_release_name: Optional[str] = None                    # NEW: its primary release today; None = Unattributed
    would_land: str = "rule"                                      # NEW, PREVIEW_LANDINGS: "rule" | "earlier_rule" | "explicit"


class AttributionPreviewResponse(BaseModel):                      # the route's existing dict keys, plus four
    considered: int
    matched: int
    field_present: int
    sample: List[AttributionPreviewSample]                        # the first 10 MATCHED runs, newest first (as today)
    shadowed: int = 0                                             # NEW: matched runs an earlier saved enabled rule claims
    explicit: int = 0                                             # NEW: matched runs attributed by rung 1/2 (explicit_client,
                                                                  #      manual_ui, external_match): a rule never moves them
    target_exists: bool = True                                    # NEW: a release of this project carries target_release_name
    unattributed_recent: int = 0                                  # NEW: of `considered`, runs with primary_release_id IS NULL


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
    # W11 REL-11 (from_attributes: a hand-built row lacking it reads None).
    # None = unsigned (every pack generated before 0225, or a signing failure).
    signing_key_id: Optional[str] = None
    # W22a ACT-05: every LIVE hold covering the pack (project ∪ pack ∪ its
    # release's), filled by the per-release pack list only; an ORM row has no
    # such attribute and reads [].
    holds: List[HoldRefRead] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


# ── W11 REL-11: verifying a pack, and the deployment's signing keys ──
class CompliancePackFileCheck(BaseModel):
    name: str
    status: str                                                   # FILE_CHECK_STATUSES: "ok" | "mismatch" | "missing" | "unexpected"
    expected_sha256: Optional[str] = None
    actual_sha256: Optional[str] = None


class CompliancePackVerification(BaseModel):
    pack_id: uuid.UUID
    result: str                                                   # VERIFY_RESULTS: "verified" | "tampered" | "unreadable"
    chain: str                                                    # CHAIN_STATES: "intact" | "broken"
    manifest_matches_record: bool                                 # sha256(manifest.json) == compliance_packs.manifest_sha256
    signature: str                                                # SIGNATURE_STATES: "valid" | "invalid" | "unsigned" | "unknown_key"
    signing_key_id: Optional[str] = None
    files_checked: int                                            # manifest-listed files
    files_ok: int
    files: List[CompliancePackFileCheck]                          # every listed file, then any unexpected one; sorted by name
    summary: str                                                  # verify_summary(...) -- the panel's and the CLI's one sentence
    checked_at: datetime


class CompliancePackSigningKeyRead(BaseModel):
    key_id: str                                                   # "k-" + the first 16 hex of sha256(raw 32-byte public key)
    algorithm: str                                                # SIGNING_ALGORITHM = "ed25519"
    public_key_pem: str                                           # SubjectPublicKeyInfo PEM; NEVER the private key
    created_at: datetime
    retired_at: Optional[datetime] = None
    active: bool


class CompliancePackSigningKeysResponse(BaseModel):
    active_key_id: Optional[str] = None                           # None until the first signed pack (a GET never creates one)
    keys: List[CompliancePackSigningKeyRead]                      # newest first, retired ones included (they verify old packs)


class CompliancePackKeyRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str                                             # must equal ROTATE_CONFIRMATION ("rotate"), else 422
    reason: str = Field(..., min_length=3, max_length=500)


class CompliancePackSummaryRead(BaseModel):
    """One pack in the project-wide list (``GET /api/v1/compliance-packs``, W05 REL-04).

    ``minio_key``, ``metadata_snapshot`` and ``notes`` are deliberately absent:
    storage keys and evidence snapshots do not belong in a side rail.
    """
    id: uuid.UUID
    release_id: Optional[uuid.UUID] = None
    #: ``None`` when the release was deleted (the FK is ``SET NULL``).
    release_name: Optional[str] = None
    release_version: Optional[str] = None
    project_id: uuid.UUID
    project_name: Optional[str] = None
    bytes: int
    file_count: int
    #: Full 64 hex; the UI shows the first 8.
    manifest_sha256: str
    generated_at: datetime
    retention_expires_at: datetime
    #: ``retention_expires_at <= now()``.
    retired: bool
    model_config = ConfigDict(from_attributes=True)


class CompliancePackListRead(BaseModel):
    items: list[CompliancePackSummaryRead]
    #: Packs in scope, counted -- not ``len(items)``.
    total: int


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
    #: W19 INT-06: ``pat`` or ``app`` (a GitHub App installation). Omitted leaves it as stored.
    auth_kind: Optional[Literal["pat", "app"]] = None


class GitHubIntegrationRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    enabled: bool
    repo_owner: str
    repo_name: str
    api_base_url: str
    has_pat: bool
    pr_comment_mode: str = "failures_only"
    #: W19 INT-06
    auth_kind: str = "pat"
    installation_id: Optional[int] = None
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
    #: W19 INT-06: which credential was used — ``pat`` | ``app_installation`` (``app`` when the App path failed).
    auth_kind: Optional[str] = None


class GitHubAppSettingsRead(BaseModel):
    """W19 INT-06: the deployment's GitHub App. Secrets are never returned, only whether they are set."""
    app_id: Optional[str] = None
    slug: Optional[str] = None
    private_key_set: bool = False
    webhook_secret_set: bool = False
    configured: bool = False


class GitHubAppSettingsWrite(BaseModel):
    app_id: str = Field(..., min_length=1, max_length=20, pattern=r"^[0-9]+$")
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9-]+$")
    #: None keeps the stored key, "" clears it, a PEM replaces it.
    private_key: Optional[str] = Field(None, max_length=10000)
    webhook_secret: Optional[str] = Field(None, max_length=500)


class GitHubAppInstallUrl(BaseModel):
    url: str
    expires_in: int


class GitHubAppSetupCallback(BaseModel):
    installation_id: int = Field(..., ge=1)
    state: str = Field(..., min_length=10, max_length=600)


class GitHubAppSetupResult(BaseModel):
    project_id: uuid.UUID
    installation_id: int
    auth_kind: str


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
    # W10 INT-07: pause after this many consecutive failed deliveries. None
    # keeps the stored value (create: webhook_breaker.DEFAULT_BREAKER_THRESHOLD).
    breaker_threshold: Optional[int] = Field(None, ge=1, le=100)


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
    # W10 INT-07 (migration 0224): the circuit breaker. A hand-built row
    # without them reads the defaults (from_attributes).
    consecutive_failures: int = 0
    breaker_threshold: int = 10
    disabled_reason: Optional[str] = None           # webhook_breaker.WEBHOOK_DISABLED_REASONS
    disabled_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

    # NOT NULL with server defaults, but the ORM defaults apply only at INSERT:
    # a row answered before its flush (a hand-built row, a session double)
    # still carries None. That is the default.
    @field_validator("consecutive_failures", mode="before")
    @classmethod
    def _unflushed_counter_is_zero(cls, value: Any) -> Any:
        return 0 if value is None else value

    @field_validator("breaker_threshold", mode="before")
    @classmethod
    def _unflushed_threshold_is_the_default(cls, value: Any) -> Any:
        return 10 if value is None else value


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
    # AIH-03 (run_compare_v2): what changed between the two runs' AI analyses,
    # computed deterministically server-side (never the model's own list).
    ai_changes: List[str] = Field(default_factory=list)


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


class ReportExportOut(BaseModel):
    """VIZ-607: one background report export."""

    id: uuid.UUID
    project_id: uuid.UUID
    format: Literal["pdf", "xlsx"]
    status: Literal["queued", "running", "completed", "failed"]
    attempts: int
    #: ``mode``, ``days``, ``release_ids``, ``suite_names``.
    params: dict[str, Any]
    filename: Optional[str] = None
    #: ``None`` until the file exists.
    size_bytes: Optional[int] = None
    #: Why it failed, in words the reader can act on.
    error: Optional[str] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    #: After this the download is 410 and the file is deleted.
    expires_at: datetime
    retryable: bool
    #: Set once ``status`` is ``completed``.
    download_url: Optional[str] = None


class ReportExportRequestOut(BaseModel):
    """VIZ-607: what ``POST /exports`` decided."""

    #: ``download``: small enough -- fetch the file now with the same query.
    #: ``background``: a job was queued (202).
    delivery: Literal["download", "background"]
    #: Tests on the window's runs: the size estimate the decision used.
    estimated_tests: int
    export: Optional[ReportExportOut] = None
    #: ``False`` when the worker could not be reached: the job stays queued
    #: (not failed) and can be retried.
    dispatched: Optional[bool] = None


class RunCompareTransition(BaseModel):
    """VIZ-507: how many tests went from one status to another between the runs.

    ``absent`` on a side means the test is not in that run (new / removed).
    Every test is in exactly one transition: the counts add up to each run's
    test count, which is what makes the Sankey's flows conserve.
    """

    before: Literal["passed", "failed", "broken", "skipped", "unknown", "absent"]
    after: Literal["passed", "failed", "broken", "skipped", "unknown", "absent"]
    count: int = Field(ge=1)


class RunCompareResponse(BaseModel):
    left: RunCompareSummary
    right: RunCompareSummary
    # VIZ-507: every test's status flow, unchanged tests included (additive).
    transitions: list[RunCompareTransition] = Field(default_factory=list)
    scope: Literal["run", "suite"] = "run"
    suite_name: Optional[str] = None
    selection: Optional[RunCompareSelection] = None
    ai_report: Optional[RunCompareAIReport] = None
    # AIH-03 (additive): the AI-analysis diff of the two runs (left =
    # baseline, right = current); {"available": false, "reason": ...} when it
    # could not be built. Absent from callers that do not ask for it.
    ai_analysis_diff: Optional[AIAnalysisDiffBrief] = None
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
    #: Which releases this defect IMPACTS — distinct from where it was found.
    #:
    #: Until this existed the column had no writer anywhere in the product, so
    #: it was permanently NULL: ``blocking_defects`` only ever took its
    #: found-in fallback, and the asserted-impact branch it was written for
    #: could not execute. NULL means "not triaged", never "harmless".
    affects_releases: Optional[list[str]] = Field(None, max_length=100)


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
    # AIH-06 (D41): AI history versions kept per run (per kind) and per test
    # result. Omitted = keep the current value; send 20 / 10 to restore the
    # defaults (there is no null-reset).
    ai_versions_per_run: Optional[int] = Field(None, ge=1, le=100)
    ai_versions_per_test: Optional[int] = Field(None, ge=1, le=100)
    # W22a ACT-04: the keep-last floor of the scheduled purge (runs clock only).
    keep_last_runs: Optional[int] = Field(None, ge=0, le=1000)
    # W22b ACT-06: the purge window (UTC hour), the pause and per-field inherit.
    # A JSON ``null`` still means "keep"; ``inherit`` names the fields to reset
    # (⊆ retention_vocab.INHERITABLE_FIELDS; each column set NULL) and is
    # validated in the service with a fixed sentence.
    purge_window_start_utc: Optional[int] = Field(None, ge=0, le=23)
    pause_days: Optional[int] = Field(None, ge=1, le=90)  # server sets purge_paused_until = now + days
    resume: Optional[bool] = None  # true clears purge_paused_until
    inherit: Optional[List[str]] = Field(None, max_length=7)


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
    # AIH-06: the effective caps (code defaults when the row holds NULL).
    ai_versions_per_run: int = 20
    ai_versions_per_test: int = 10
    # W22a ACT-04: the effective keep-last floor (10 when no row exists).
    keep_last_runs: int = KEEP_LAST_RUNS_DEFAULT
    source: str = "default"
    last_purge: Optional[RetentionLastPurge] = None
    # W22b ACT-06: every value above is the EFFECTIVE per-field value
    # (project, else instance, else code default).
    purge_window_start_utc: int = PURGE_WINDOW_DEFAULT_HOUR
    purge_paused_until: Optional[datetime] = None  # only when still in the future
    next_scheduled_purge_at: Optional[datetime] = None  # None while disabled; after the pause when paused
    # each INHERITABLE_FIELDS key -> "project" | "instance" | "default" (FIELD_SOURCES)
    field_sources: Dict[str, str] = Field(default_factory=dict)
    # what each field reads if inherited
    inherit_values: Dict[str, Union[bool, int]] = Field(default_factory=dict)


# ── Instance retention defaults (W22b ACT-06) ──


class InstanceRetentionPolicyRead(BaseModel):
    """GET/PUT ``/api/v1/settings/retention-defaults``. A NULL value = the code default."""

    enabled: bool
    raw_events_days: Optional[int] = None
    runs_days: Optional[int] = None
    artifacts_days: Optional[int] = None
    audit_days: Optional[int] = None
    keep_last_runs: Optional[int] = None
    purge_window_start_utc: Optional[int] = None
    code_defaults: Dict[str, Union[bool, int]]  # the seven code defaults (what a NULL means)
    effective: Dict[str, Union[bool, int]]  # COALESCE(instance, code default) per field
    inheriting_projects: Dict[str, int]  # per field: projects whose value comes from the instance
    enabled_at: Optional[datetime] = None
    enabled_by_name: Optional[str] = None
    updated_at: Optional[datetime] = None


class InstanceRetentionPolicyWrite(BaseModel):
    """PUT body. ``enabled: true`` is refused (422 ``enable_via_flow``); ``false`` switches it off."""

    enabled: Optional[bool] = None
    raw_events_days: Optional[int] = Field(None, ge=7, le=3650)
    runs_days: Optional[int] = Field(None, ge=30, le=3650)
    artifacts_days: Optional[int] = Field(None, ge=7, le=3650)
    audit_days: Optional[int] = Field(None, ge=365, le=3650)
    keep_last_runs: Optional[int] = Field(None, ge=0, le=1000)
    purge_window_start_utc: Optional[int] = Field(None, ge=0, le=23)
    # reset an instance field to the code default (NULL); not "enabled"
    clear: Optional[List[str]] = Field(None, max_length=6)


class InstanceEnablePreviewItem(BaseModel):
    project_id: uuid.UUID
    name: str
    is_active: bool
    has_policy_row: bool
    effective_after: Dict[str, int]  # the windows / floor / window hour it would run with
    runs_past_window: int  # runs older than its runs window (before the floor and holds)


class InstanceEnablePreviewResponse(BaseModel):
    items: List[InstanceEnablePreviewItem] = Field(default_factory=list)
    total: int
    truncated: bool
    preview_hash: str  # sha256 of the sorted affected ids + the instance values
    confirmation_phrase: str  # INSTANCE_ENABLE_PHRASE(_ONE) for `total`
    already_enabled: bool


class InstanceEnableRequest(BaseModel):
    confirmation: str = Field(..., max_length=200)
    preview_hash: str = Field(..., min_length=64, max_length=64)


# ── W23c AGT-10: the review gate's enforcement date (GET/PUT /api/v1/settings/review-gate).
# Vocabulary in services/review_gate_vocab.py; mirrored by frontend/src/types/reviewGate.ts.


class WouldRefuseChannel(BaseModel):
    channel: str  # a GATED_CHANNELS code
    decisions: int = Field(ge=0)


class WouldRefuseSummary(BaseModel):
    """The ``ai_report.distribution_would_refuse`` audit rows of the last 30 days.

    Absence is not health: ``measured`` is False when the audit read failed or
    timed out, and then every number below is None (never 0).
    """

    measured: bool
    window_days: int = 30
    # max(now - 30 d, oldest access_audit_logs.created_at): less than 30 days of trail is SAID.
    since: Optional[datetime] = None
    decisions: Optional[int] = Field(default=None, ge=0)  # rows with action == WOULD_REFUSE_ACTION in the window
    runs: Optional[int] = Field(default=None, ge=0)  # COUNT(DISTINCT after_value->>'run_id') of those rows
    by_channel: Optional[List[WouldRefuseChannel]] = None  # every channel with >= 1 row, decisions desc, then channel


class GatedChannel(BaseModel):
    channel: str
    label: str


class ReviewGateRead(BaseModel):
    """GET/PUT ``/api/v1/settings/review-gate``. Writes nothing on GET."""

    enforced: bool  # gate_enforced() as it answers NOW on this pod's fresh snapshot
    source: str  # GATE_SOURCES
    enforced_from: Optional[datetime] = None  # the stored instant (UTC), or None
    env_override: bool  # settings.REVIEW_GATE_ENFORCED
    updated_at: Optional[datetime] = None  # the app_settings row's updated_at (no actor: the audit dashboard names who)
    would_refuse: WouldRefuseSummary
    channels: List[GatedChannel]  # GATED_CHANNELS, in order


class ReviewGateWrite(BaseModel):
    """PUT body. ``None`` clears (back to audit only); a naive datetime is the route's 422 ``naive_datetime``."""

    model_config = ConfigDict(extra="forbid")

    enforced_from: Optional[datetime] = None


class RetentionPreviewCandidates(BaseModel):
    """Per-class candidate counts a purge WOULD delete right now.

    **Every class ``run_purge`` counts must have a field here.** FastAPI
    filters the handler's return through this model, so a class the service
    counts but the model omits is dropped from the response *silently* — no
    error, no warning, just a smaller object.

    That happened: the service computed twelve counts and this model declared
    eight, so ``evidence_artifact_rows``, ``analysis_cache_entries``,
    ``memory_entries_expired`` and ``search_index_documents`` never reached
    the operator. The preview is what an ADMIN authorises an irreversible
    cross-store purge from, so under-reporting it is not cosmetic — four
    categories of data were deleted by execute without ever appearing in the
    dry run.
    """
    runs: int
    test_cases: int
    mongo_docs: dict[str, int] = Field(default_factory=dict)
    minio_objects: int
    event_archive_rows: int
    audit_rows: int
    #: Revoked share links past the artifacts clock. Dead on revoke, but
    #: the row only ever died via the run CASCADE.
    revoked_share_links: int = 0
    provenance_rows: int
    compliance_packs_expired: int
    evidence_artifact_rows: int = 0
    memory_entries_expired: int = 0
    # Optional, and the default is None rather than 0. Both of these come from
    # stores that can be down independently of Postgres (Redis + the two Chroma
    # collections), and both used to report 0 on an outage — indistinguishable
    # from "nothing to delete there" on the screen an ADMIN authorises an
    # irreversible purge from. None means the store was not reached; the
    # response's ``unmeasured`` list names which.
    analysis_cache_entries: Optional[int] = None
    search_index_documents: Optional[int] = None
    #: AIH-06: AI history versions the purge would remove (ai_run_versions of
    #: purged runs + ai_analysis_versions their delete cascades + the
    #: over-cap versions of the sweep).
    ai_history_versions: int = 0
    #: W12 SRCH-05: ``search_query_events`` of the project older than 30 days
    #: (a FIXED window, ``USAGE_RETENTION_DAYS``, not the audit clock). An
    #: ``int = 0``: Postgres is always measurable.
    search_usage_rows: int = 0
    #: W16 AGT-13: the project's ``pending`` shadow pairs older than
    #: ``SHADOW_PAIR_RETENTION_DAYS`` (30, a fixed window). Labelled pairs are
    #: G2 evidence and are kept.
    shadow_pairs_unlabelled: int = 0
    #: W22b ACT-03 (E-12): retained deletion archives (``retain`` exports)
    #: that leave with their ``deletion_jobs`` rows on the AUDIT clock. An
    #: ``int = 0``: a Postgres count, always measurable.
    export_archives_expired: int = 0


# W22a: hold / floor / reference facts are TOP-LEVEL preview fields, never
# ``candidates`` keys (the preview guard reads that dict for class counts only).
class RetentionPreviewHold(HoldRefRead):
    runs_kept: int = 0   # runs past the RUNS cutoff this hold pins (a run pinned twice counts under both)
    packs_kept: int = 0  # expired packs this hold pins


class RetentionPreviewHolds(BaseModel):
    project_held: bool = False  # a live project hold: every held class counts 0
    runs_kept: int = 0          # DISTINCT runs past the runs cutoff kept by any hold
    packs_kept: int = 0
    active: List[RetentionPreviewHold] = Field(default_factory=list)  # every LIVE hold, placed_at order


class RetentionPreviewFloor(BaseModel):
    keep_last_runs: int
    runs_kept: int             # runs past the runs cutoff the floor keeps and no hold already keeps
    latest_passed_kept: bool   # the project's newest PASSED run was a candidate and is kept
    suites_kept: int           # distinct suites whose newest PASSED run was a candidate and is kept


class RetentionPreviewResponse(BaseModel):
    """POST ``.../retention-policy/preview`` — dry-run, writes nothing."""
    cutoffs: dict[str, datetime]
    candidates: RetentionPreviewCandidates
    #: Candidate classes whose store could not be reached. A client that
    #: ignores this still sees ``null`` rather than a wrong zero; a client that
    #: reads it can say "not measured" and explain why the total is short.
    unmeasured: List[str] = Field(default_factory=list)
    # W22a ACT-05 / ACT-04 (every field defaulted: an older payload validates).
    holds: RetentionPreviewHolds = Field(default_factory=RetentionPreviewHolds)
    floor: Optional[RetentionPreviewFloor] = None
    #: Only FKs with count > 0, highest count first.
    references_broken: List[ReferenceBrokenRead] = Field(default_factory=list)
    #: False = the count failed; the UI says "not measured", never "none".
    references_measured: bool = True


class RetentionPurgeRequest(BaseModel):
    """POST ``.../retention-policy/purge`` — typed-name confirmation
    (``project_reset`` convention): must equal the project name exactly."""
    confirmation_name: str = Field(..., min_length=1, max_length=255)


class RetentionPurgeQueued(BaseModel):
    """202 body — the execute-mode purge was enqueued to Celery."""
    queued: bool = True


# ── Retention holds (W22a ACT-05) ────────────────────────────────────────────
# ``entity_type`` / ``state`` are plain ``str``: the vocabularies live in
# ``services/retention_vocab.py`` and the service refuses a value outside them
# with one fixed sentence (``HOLD_ERROR_SENTENCES``), not Pydantic's 422.


class RetentionHoldCreate(BaseModel):
    """POST ``/projects/{id}/retention-holds`` body."""
    entity_type: str
    entity_id: Optional[uuid.UUID] = None
    reason: str = Field(..., max_length=2000)  # trimmed length 3-500 checked in the service
    expires_at: Optional[datetime] = None


class RetentionHoldRelease(BaseModel):
    """POST ``/projects/{id}/retention-holds/{hold_id}/release`` body."""
    reason: str = Field(..., max_length=2000)


class RetentionHoldRead(BaseModel):
    """One hold; ``state`` (live / expired / released) is computed by the service for ``now``."""
    id: uuid.UUID
    project_id: uuid.UUID
    entity_type: str
    entity_id: Optional[uuid.UUID] = None
    entity_label: Optional[str] = None
    reason: str
    expires_at: Optional[datetime] = None
    state: str
    placed_by_name: Optional[str] = None
    placed_at: datetime
    released_at: Optional[datetime] = None
    released_by_name: Optional[str] = None
    release_reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RetentionHoldListResponse(BaseModel):
    items: List[RetentionHoldRead] = Field(default_factory=list)
    total: int = 0  # rows matching the filter, ignoring limit
    #: {"live", "expired", "released"} for the project (entity filters applied,
    #: state ignored) — every key present, 0 when none.
    counts: Dict[str, int] = Field(default_factory=dict)
