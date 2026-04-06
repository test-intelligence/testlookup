"""Pydantic v2 request/response schemas for all API endpoints."""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.postgres import (
    FailureCategory,
    IdentityEventType,
    LaunchStatus,
    NotificationChannel,
    Severity,
    SSOEnforcementMode,
    SSOProviderType,
    TestStatus,
    UserRole,
)


# ── Base ─────────────────────────────────────────────────────

class TimestampMixin(BaseModel):
    created_at: datetime
    updated_at: Optional[datetime] = None


# ── Auth Schemas ─────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    full_name: Optional[str] = None
    password: str = Field(..., min_length=8)


class UserResponse(TimestampMixin):
    id: uuid.UUID
    email: str
    username: str
    full_name: Optional[str] = None
    role: UserRole
    is_active: bool
    must_change_password: bool = False

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class FirstTimeResetRequest(BaseModel):
    """Used for forced password reset on first login — no current password required."""
    new_password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)


# ── Project Schemas ───────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    jira_project_key: Optional[str] = None
    splunk_index: Optional[str] = None
    ocp_namespace: Optional[str] = None
    jenkins_job_pattern: Optional[str] = None
    component_owner_map: Optional[dict] = None


class ProjectUpdate(BaseModel):
    """Partial update for project attributes. None = keep existing."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
    jira_project_key: Optional[str] = None
    splunk_index: Optional[str] = None
    ocp_namespace: Optional[str] = None
    jenkins_job_pattern: Optional[str] = None
    component_owner_map: Optional[dict] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Optional[List[str]] = None


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

    model_config = ConfigDict(from_attributes=True)


# ── Test Run Schemas ──────────────────────────────────────────

class TestRunSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    build_number: str
    jenkins_job: Optional[str] = None
    trigger_source: Optional[str] = None
    branch: Optional[str] = None
    status: LaunchStatus
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    broken_tests: int
    pass_rate: Optional[float] = None
    duration_ms: Optional[int] = None
    ocp_pod_name: Optional[str] = None
    ocp_namespace: Optional[str] = None
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
    severity: Optional[Severity] = None
    feature: Optional[str] = None
    failure_category: Optional[FailureCategory] = None
    has_attachments: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
    llm_provider: str
    llm_model: str
    requires_human_review: bool


# ── Jira Integration Schemas ──────────────────────────────────

class JiraIssueRequest(BaseModel):
    project_key: str
    test_case_id: uuid.UUID
    test_name: str
    run_id: uuid.UUID
    ai_summary: str
    recommended_action: str


class JiraIssueResponse(BaseModel):
    ticket_id: str
    ticket_key: str
    ticket_url: str


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


# ── Quality Gate Schemas ──────────────────────────────────────

class QualityGateRule(BaseModel):
    rule_type: str  # "pass_rate" | "failure_count" | "new_failures" | "p1_bugs" | "flaky_count"
    threshold: Any
    severity: str = "FAIL"  # "FAIL" | "WARN"
    description: Optional[str] = None


class QualityGateCreate(BaseModel):
    name: str
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
    events: List[str] = Field(
        default_factory=lambda: ["run_failed", "high_failure_rate"],
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


class PipelineTimelineResponse(BaseModel):
    schema_version: int = 2
    pipeline_run_id: uuid.UUID
    workflow_type: str
    status: str
    started_at: Optional[Any] = None
    completed_at: Optional[Any] = None
    duration_seconds: Optional[float] = None
    cost_summary: Dict[str, Any] = Field(default_factory=dict)
    alerts: List[Dict[str, Any]] = Field(default_factory=list)
    stages: List[Dict[str, Any]] = Field(default_factory=list)
    events: List[PipelineTimelineEventResponse] = Field(default_factory=list)
    summary: PipelineTimelineSummary = Field(default_factory=PipelineTimelineSummary)


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
    uri_or_ref: Optional[str] = None
    cluster_id: Optional[str] = None


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
    title: Optional[str] = None


class ChatSessionResponse(BaseModel):
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
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


# ── Test Case Management Schemas ──────────────────────────────────────────────

class TestCaseStepSchema(BaseModel):
    step_number: int
    action: str
    expected_result: str


class ManagedTestCaseCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = None
    objective: Optional[str] = None
    preconditions: Optional[str] = None
    steps: Optional[List[dict]] = None
    expected_result: Optional[str] = None
    test_data: Optional[str] = None
    test_type: str = "functional"
    priority: str = "medium"
    severity: str = "major"
    feature_area: Optional[str] = None
    suite_name: Optional[str] = None
    tags: Optional[List[str]] = None
    estimated_duration_minutes: Optional[int] = None
    is_automated: bool = False
    automation_status: str = "not_automated"


class ManagedTestCaseUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = None
    objective: Optional[str] = None
    preconditions: Optional[str] = None
    steps: Optional[List[dict]] = None
    expected_result: Optional[str] = None
    test_data: Optional[str] = None
    test_type: Optional[str] = None
    priority: Optional[str] = None
    severity: Optional[str] = None
    feature_area: Optional[str] = None
    suite_name: Optional[str] = None
    tags: Optional[List[str]] = None
    estimated_duration_minutes: Optional[int] = None
    is_automated: Optional[bool] = None
    automation_status: Optional[str] = None
    change_summary: Optional[str] = None


class ManagedTestCaseResponse(BaseModel):
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
    content: str = Field(..., min_length=1)
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
    description: Optional[str] = None
    objective: Optional[str] = None
    planned_start_date: Optional[datetime] = None
    planned_end_date: Optional[datetime] = None
    assigned_to_id: Optional[uuid.UUID] = None


class TestPlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = None
    objective: Optional[str] = None
    status: Optional[str] = None
    planned_start_date: Optional[datetime] = None
    planned_end_date: Optional[datetime] = None
    actual_start_date: Optional[datetime] = None
    actual_end_date: Optional[datetime] = None
    assigned_to_id: Optional[uuid.UUID] = None


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
    version_label: str = "v1.0"
    objective: Optional[str] = None
    scope: Optional[str] = None
    test_approach: Optional[str] = None


class TestStrategyUpdate(BaseModel):
    name: Optional[str] = None
    version_label: Optional[str] = None
    status: Optional[str] = None
    objective: Optional[str] = None
    scope: Optional[str] = None
    out_of_scope: Optional[str] = None
    test_approach: Optional[str] = None
    risk_assessment: Optional[List[dict]] = None
    test_types: Optional[List[dict]] = None
    entry_criteria: Optional[List[str]] = None
    exit_criteria: Optional[List[str]] = None
    environments: Optional[List[dict]] = None
    automation_approach: Optional[str] = None
    defect_management: Optional[str] = None


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


# ── Live Stream Schemas ───────────────────────────────────────────────────────

class LiveSessionCreate(BaseModel):
    """Request body to register a new live execution session."""
    project_id: uuid.UUID
    run_id: Optional[str] = None           # auto-generated if omitted
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
        description="run_start | test_start | test_result | log | metric | run_complete",
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


class LiveSessionState(BaseModel):
    """Live state of an active or recently completed session."""
    run_id: str
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
    ai_offline_mode: bool
    embedding_provider: str
    embedding_model: str
    ai_confidence_threshold: int
    ai_timeout_seconds: int
    deep_investigation_enabled: bool
    finetune_enabled: bool
    openai_key_set: bool
    google_key_set: bool
    # Analysis mode — LLM-free operation
    analysis_mode: str                               # "llm" | "ml" | "rules" | "auto"
    ml_model_available: bool = False                  # True if a trained ML model exists
    ml_model_accuracy: Optional[float] = None         # last known accuracy (0-1)
    ml_training_sample_count: int = 0                 # total labeled samples available


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
    analysis_mode: Optional[str] = Field(None, pattern=r"^(llm|ml|rules|auto)$")


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
    slack_webhook_url: Optional[str]
    slack_default_channel: str
    teams_enabled: bool
    teams_webhook_url: Optional[str]
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
    postgres_connected: bool = True       # replaced raw host/port/db
    mongo_connected: bool = True          # replaced raw host/port/db
    redis_connected: bool = True          # replaced raw url
    # Editable storage settings (not sensitive)
    minio_endpoint: str
    minio_bucket_name: str
    minio_use_ssl: bool
    chroma_host: str
    chroma_port: int
    chroma_collection: str


class StorageConfigUpdate(BaseModel):
    """Payload for updating storage config. None = keep existing."""
    storage_backend: Optional[str] = None
    chroma_host: Optional[str] = Field(None, max_length=255)
    chroma_port: Optional[int] = Field(None, ge=1, le=65535)
    chroma_collection: Optional[str] = Field(None, max_length=255)
    minio_endpoint: Optional[str] = Field(None, max_length=500)
    minio_bucket_name: Optional[str] = Field(None, max_length=255)
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


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_hint: str
    scopes: List[str]
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


class FlakyCoachResponse(BaseModel):
    """Project-level flaky coach leaderboard."""
    project_id: str
    total_flaky: int = 0
    quarantine_candidates: int = 0
    entries: List[FlakyCoachEntry] = []


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


class SCIMName(BaseModel):
    givenName: Optional[str] = None
    familyName: Optional[str] = None
    formatted: Optional[str] = None


class SCIMEmail(BaseModel):
    value: str
    type: Optional[str] = "work"
    primary: bool = True


class SCIMGroup(BaseModel):
    value: str
    display: Optional[str] = None


class SCIMUserResource(BaseModel):
    """SCIM 2.0 User resource — used for both request and response."""
    schemas: List[str] = ["urn:ietf:params:scim:schemas:core:2.0:User"]
    id: Optional[str] = None  # set on response
    externalId: Optional[str] = None
    userName: str
    name: Optional[SCIMName] = None
    emails: List[SCIMEmail] = []
    displayName: Optional[str] = None
    active: bool = True
    groups: List[SCIMGroup] = []
    meta: Optional[dict] = None


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


class SCIMPatchRequest(BaseModel):
    schemas: List[str] = ["urn:ietf:params:scim:api:messages:2.0:PatchOp"]
    Operations: List[SCIMPatchOp]


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


class PolicyDocument(BaseModel):
    """The full policy rule document stored as JSON in release_gate_policies.rules."""
    schema_version: int = 1
    thresholds: PolicyThresholds = Field(default_factory=PolicyThresholds)
    dimension_weights: PolicyDimensionWeights = Field(default_factory=PolicyDimensionWeights)
    rules: List[PolicyRule] = Field(default_factory=list)


class ReleaseGatePolicyCreate(BaseModel):
    """Create a new draft policy."""
    project_id: Optional[uuid.UUID] = None  # None = system default
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    rules: PolicyDocument = Field(default_factory=PolicyDocument)


class ReleaseGatePolicyUpdate(BaseModel):
    """Update a draft policy (fails if already published)."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
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


class OwnershipResolution(BaseModel):
    """Result of resolving ownership for a test/cluster."""
    service_name: Optional[str] = None
    team_name: Optional[str] = None
    team_contact: Optional[str] = None
    confidence: str = "none"  # high | medium | low | none
    matched_rule_id: Optional[str] = None
    match_source: Optional[str] = None  # suite_name | component | package | path | label | component_owner_map | fallback
    fallback_reason: Optional[str] = None


# ── Saved Views & Digest Schemas (ENT-05) ────────────────────────────────────


class SavedViewCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    page: Optional[str] = Field(None, max_length=50)  # dashboard | trends | coverage | defects
    filters: dict = Field(default_factory=dict)
    is_shared: bool = False
    is_default: bool = False


class SavedViewUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
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
    schedule: str = Field(default="WEEKLY", pattern="^(DAILY|WEEKLY|PER_RUN|PER_RELEASE|PER_SUITE)$")
    channel: str = Field(default="email", pattern="^(email|slack|teams)$")
    scope_type: Optional[str] = Field(default="project", pattern="^(project|release|suite|global)$")
    scope_value: Optional[str] = Field(None, max_length=255)
    trigger_filter: Optional[str] = Field(default="all", pattern="^(all|failed_only|degraded_only)$")


class DigestSubscriptionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    schedule: Optional[str] = Field(None, pattern="^(DAILY|WEEKLY)$")
    channel: Optional[str] = Field(None, pattern="^(email|slack|teams)$")
    saved_view_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None
    is_paused: Optional[bool] = None


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


# ── AI Evaluation Schemas (OPS-02) ───────────────────────────────────────────


class AIEvalDatasetCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
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


class AIQualityDashboardResponse(BaseModel):
    """Combined dashboard data for AI quality metrics."""
    agreement: Optional[dict] = None  # agreement_rate, total_feedback, ...
    drift: Optional[dict] = None  # current vs previous window
    recent_eval_runs: List[AIEvalRunResponse] = []
    model_versions: List[dict] = []
    feedback_summary: Optional[dict] = None


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


class MemoryTimelineResponse(BaseModel):
    """Timeline of memory entries for a run, grouped by entity type."""
    run_id: uuid.UUID
    project_id: uuid.UUID
    entries_by_type: dict  # {entity_type: [AgentMemoryEntryResponse]}
    total_entries: int
