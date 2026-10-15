"""Pydantic models of the AI-history wave (W03: AIH-01..AIH-07).

Every request/response model the new routes, the run-compare payload, the
test-case history and the export use. ``app/models/schemas.py`` imports the
four it embeds in existing models (``AIAnalysisDiffBrief``, ``TestHistoryAI``,
``TestHistoryAIChange``) from here; this module never imports ``schemas.py``.

Rules (W03 contract, preamble and section 6.2):

* Every enum-like field is a plain ``str``, never ``Literal`` / Enum: a strict
  enum over a stored string turns vocabulary drift into a silent 422.
* Stored payloads (digests, summaries, diffs) are typed ``dict[str, Any]`` in
  responses, so an older stored shape can never 500 a read. :class:`AIDigest`
  exists for typing in tests only.
* ``null`` means not measured / not known, never 0 or "none".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ── Shared blocks ────────────────────────────────────────────────────────────


class AIHistoryReviewBlock(BaseModel):
    """E8.3 review status of an AI artefact (state is a plain string here).

    Named apart from ``schemas.ReviewBlock`` (whose ``state`` is a Literal) so
    the OpenAPI schema keeps one component per name.
    """

    state: str
    message: str
    review_id: Optional[str] = None
    reviewed_at: Optional[str] = None


#: The contract's name for the block.
ReviewBlock = AIHistoryReviewBlock


class DecisionReportRef(BaseModel):
    report_id: str
    report_version: int


class _Envelope(BaseModel):
    """The E8.3 envelope every AI-history read carries."""

    requires_human_review: bool = True
    review: Optional[AIHistoryReviewBlock] = None
    ai_disclaimer: Optional[str] = None
    ai_disclaimer_version: Optional[str] = None


# ── Run-level versions (R1, R4) ──────────────────────────────────────────────


class AIVersionSummary(BaseModel):
    """One row of a run's AI history list, and the head of a single version."""

    version_id: str
    kind: str
    version: int
    trigger: str  # pipeline | backfill
    pipeline_run_id: Optional[str] = None
    generated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    #: digest: provenance.engine (rules|ml|llm|auto|mixed); summary: llm | deterministic
    analysis_mode: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    fallback_used: bool = False
    deterministic: bool = False
    verdict: Optional[str] = None
    risk_score: Optional[int] = None
    #: digest: analysed_tests
    failing_tests: Optional[int] = None
    categories: Optional[Dict[str, int]] = None
    cluster_count: Optional[int] = None
    #: <=200 chars (summary: executive summary; digest: null)
    headline: Optional[str] = None
    content_sha256: str
    decision_report_ref: Optional[DecisionReportRef] = None
    #: digest rows: the run_summary version of the same pipeline
    summary_version: Optional[int] = None
    review: Optional[AIHistoryReviewBlock] = None
    is_latest: bool = False


class AIVersionResponse(AIVersionSummary):
    payload: Dict[str, Any]
    requires_human_review: bool = True
    ai_disclaimer: Optional[str] = None
    ai_disclaimer_version: Optional[str] = None


class AIHistoryListResponse(_Envelope):
    run_id: str
    kind: str
    total: int
    limit: int
    truncated: bool
    latest_version: Optional[int] = None
    #: {"available": bool}: the run has >= 1 ai_analysis row or a published decision report
    live: Dict[str, Any] = Field(default_factory=lambda: {"available": False})
    #: newest first
    versions: List[AIVersionSummary] = Field(default_factory=list)


# ── Diff (R2) ────────────────────────────────────────────────────────────────


class AIVersionRef(BaseModel):
    """One side of a diff or a compare."""

    ref: str  # version_id | "live"
    source: str  # stored | live
    version: Optional[int] = None
    kind: str = "analysis_digest"
    generated_at: Optional[datetime] = None
    pipeline_run_id: Optional[str] = None
    content_sha256: Optional[str] = None
    analysis_mode: Optional[str] = None
    llm_model: Optional[str] = None
    run_id: str
    build_number: Optional[str] = None


class AIDigestDiffResponse(_Envelope):
    """``from`` / ``to`` are emitted under those JSON keys (aliases)."""

    model_config = ConfigDict(populate_by_name=True)

    run_id: str
    from_ref: AIVersionRef = Field(alias="from")
    to_ref: AIVersionRef = Field(alias="to")
    #: AIDigestDiff (W03 contract section 3.1)
    diff: Dict[str, Any]


# ── Per-test versions (R5) ───────────────────────────────────────────────────


class AIActor(BaseModel):
    user_id: str
    name: Optional[str] = None  # users.full_name or username


class AITestVersion(BaseModel):
    version_id: str
    version: int
    trigger: str  # pipeline | manual_analyze | human_correction | bulk_reclassify | backfill | current
    pipeline_run_id: Optional[str] = None
    actor: Optional[AIActor] = None
    failure_category: Optional[str] = None
    confidence_score: Optional[int] = None
    analysis_mode: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    is_flaky: bool = False
    requires_human_review: bool = False
    #: <=300 chars, whitespace-normalised
    root_cause_excerpt: str = ""
    content_sha256: str
    created_at: datetime


class AITestHistoryResponse(BaseModel):
    run_id: str
    test_id: str
    test_fingerprint: Optional[str] = None
    test_name: str
    #: version_id = ai_analysis.id, version = latest stored version or 0, trigger "current"
    current: Optional[AITestVersion] = None
    versions: List[AITestVersion] = Field(default_factory=list)
    total: int = 0


# ── Previous run (R6) and compare (R7) ───────────────────────────────────────


class RunBrief(BaseModel):
    run_id: str
    build_number: Optional[str] = None
    run_seq: Optional[int] = None
    branch: Optional[str] = None
    primary_suite_name: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    #: coalesce(end_time, created_at)
    finished_at: Optional[datetime] = None


class AIPreviousRunResponse(BaseModel):
    run_id: str
    rule: Dict[str, Any]
    current: RunBrief
    baseline: Optional[RunBrief] = None
    #: None | no_primary_suite | run_lacks_suite | no_previous_run
    reason: Optional[str] = None
    #: a sentence safe to toast
    message: Optional[str] = None


class AICompareNarrative(BaseModel):
    status: str  # ready | deterministic
    source: str  # llm | deterministic
    label: str  # "AI narrative" | "deterministic summary"
    fallback_used: bool = False
    executive_summary: str = ""
    ai_changes: List[str] = Field(default_factory=list)
    risk_level: Optional[str] = None
    prompt_version: str = "run_compare_v2"
    model_name: Optional[str] = None
    generated_at: Optional[datetime] = None
    report_status: str = "absent"  # ready | queued | failed | absent


class DigestSide(BaseModel):
    """Compact per-side summary for the Run Compare columns (core.digest_side)."""

    verdict: Optional[str] = None
    risk_score: Optional[int] = None
    composite_risk: Optional[float] = None
    analysed_tests: int = 0
    categories: Dict[str, int] = Field(default_factory=dict)
    avg_confidence: Optional[float] = None
    #: <=5 by size: {"label", "size", "signature"}
    top_clusters: List[Dict[str, Any]] = Field(default_factory=list)
    engine: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    deterministic: bool = False
    fallback_used: bool = False
    decision_report_version: Optional[int] = None
    #: null for a live side
    generated_at: Optional[datetime] = None


class AICompareResponse(_Envelope):
    run_id: str
    project_id: str
    #: RunBrief fields + {"selection": "default"|"explicit", "selection_reason": str, "branch_mismatch": bool}
    baseline: Optional[Dict[str, Any]] = None
    #: None | no_primary_suite | run_lacks_suite | no_previous_run | baseline_not_analysed | current_not_analysed
    reason: Optional[str] = None
    message: Optional[str] = None
    current_ref: Optional[AIVersionRef] = None
    baseline_ref: Optional[AIVersionRef] = None
    #: {"baseline": DigestSide|None, "current": DigestSide|None}; null when there is no baseline
    sides: Optional[Dict[str, Optional[DigestSide]]] = None
    #: AIDigestDiff, mode "cross_run"; null whenever ``reason`` is set
    diff: Optional[Dict[str, Any]] = None
    #: null whenever diff is null
    narrative: Optional[AICompareNarrative] = None


# ── Run compare payload (section 7.1) ────────────────────────────────────────


class AIAnalysisDiffBrief(BaseModel):
    """``RunCompareResponse.ai_analysis_diff``. Only ``available`` is required, so
    ``{"available": false, "reason": ...}`` validates."""

    available: bool
    #: baseline_not_analysed | current_not_analysed | ai_history_unavailable | different_projects
    reason: Optional[str] = None
    left_ref: Optional[str] = None
    right_ref: Optional[str] = None
    verdict: Optional[Dict[str, Any]] = None
    provenance: Optional[Dict[str, Any]] = None
    counts: Dict[str, int] = Field(default_factory=dict)
    #: <=10 each: {"label", "size"}
    clusters_added: List[Dict[str, Any]] = Field(default_factory=list)
    clusters_removed: List[Dict[str, Any]] = Field(default_factory=list)
    #: <=50 material: {test_name, suite_name, category_from, category_to, confidence_from, confidence_to}
    test_changes: List[Dict[str, Any]] = Field(default_factory=list)
    ai_changes: List[str] = Field(default_factory=list)


# ── Test-case history (section 7.3) ──────────────────────────────────────────


class TestHistoryAI(BaseModel):
    __test__ = False  # not a pytest class

    #: that point's own test_cases.id (for GET /runs/{run_id}/tests/{test_id}/ai-history)
    test_id: str
    category: Optional[str] = None
    confidence: Optional[int] = None
    #: <=300 chars, whitespace-normalised, sanitized
    root_cause_excerpt: str = ""
    #: 16 hex (ai_history_core.root_cause_hash)
    root_cause_sha: Optional[str] = None
    analysis_mode: Optional[str] = None
    model: Optional[str] = None
    is_flaky: bool = False
    requires_human_review: bool = False
    #: ai_analysis_versions rows of that test_cases row (0 before any version exists)
    version_count: int = 0


class TestHistoryAIChange(BaseModel):
    __test__ = False  # not a pytest class

    run_id: Optional[str] = None
    run_label: str
    from_category: Optional[str] = None
    to_category: Optional[str] = None
    from_confidence: Optional[int] = None
    to_confidence: Optional[int] = None
    #: "category" | "root_cause" (sorted)
    kinds: List[str] = Field(default_factory=list)


# ── Export (section 6.6) ─────────────────────────────────────────────────────


class AIHistoryExportBundle(BaseModel):
    """The ``run-<build>-ai-history.json`` file (pure JSON)."""

    schema_version: int = 1
    report_type: str = "ai_history"
    exported_at: datetime
    run: Dict[str, Any]
    #: kind analysis_digest, oldest -> newest, each AIVersionSummary + "payload"
    versions: List[Dict[str, Any]] = Field(default_factory=list)
    #: kind run_summary, oldest -> newest
    run_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    #: consecutive pairs: {"from_version", "to_version", "diff"}
    diffs: List[Dict[str, Any]] = Field(default_factory=list)
    #: {"total", "truncated", "items": [AITestVersion + test_fingerprint, test_name]}
    per_test_versions: Dict[str, Any] = Field(default_factory=dict)
    limits: Dict[str, Any] = Field(default_factory=dict)
    partial: List[str] = Field(default_factory=list)
    draft_watermark: Optional[str] = None
    requires_human_review: bool = True
    review: Optional[AIHistoryReviewBlock] = None
    ai_disclaimer: Optional[str] = None
    ai_disclaimer_version: Optional[str] = None


# ── The digest (typing for tests; served as a plain dict) ────────────────────


class AIDigestRun(BaseModel):
    run_id: str
    project_id: str
    build_number: Optional[str] = None
    branch: Optional[str] = None
    primary_suite_name: Optional[str] = None
    total_tests: int = 0
    failed_tests: int = 0
    broken_tests: int = 0
    pass_rate: Optional[float] = None


class AIDigestVerdict(BaseModel):
    recommendation: Optional[str] = None
    risk_score: Optional[int] = None
    composite_risk: Optional[float] = None
    source: Optional[str] = None
    decision_report_version: Optional[int] = None
    overridden: bool = False


class AIDigestCluster(BaseModel):
    signature: str
    cluster_id: str
    label: str
    size: int
    member_count: int
    member_fingerprints: List[str] = Field(default_factory=list)
    representative_error: Optional[str] = None


class AIDigestTest(BaseModel):
    test_fingerprint: str
    test_id: str
    test_name: Optional[str] = None
    suite_name: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[int] = None
    root_cause_sha: Optional[str] = None
    root_cause_excerpt: str = ""
    is_flaky: bool = False
    requires_human_review: bool = False
    analysis_mode: Optional[str] = None


class AIDigestProvenance(BaseModel):
    pipeline_run_id: Optional[str] = None
    source: str
    captured_at: Optional[str] = None
    engine: Optional[str] = None
    mode_counts: Dict[str, int] = Field(default_factory=dict)
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    fallback_used: bool = False
    fallback_count: int = 0
    deterministic: bool = False
    summary_fallback_used: Optional[bool] = None
    carried_over_tests: Optional[int] = None
    stage_quality: Optional[str] = None
    prompt_versions: Dict[str, str] = Field(default_factory=dict)
    partial: List[str] = Field(default_factory=list)


class AIDigest(BaseModel):
    """The ``analysis_digest`` payload (W03 contract section 2.1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    kind: str
    run: AIDigestRun
    verdict: AIDigestVerdict
    analysed_tests: int
    categories: Dict[str, int]
    avg_confidence: Optional[float] = None
    flaky_count: int
    requires_review_count: int
    clusters: List[AIDigestCluster]
    tests: List[AIDigestTest]
    actions: List[str]
    actions_source: Optional[str] = None
    provenance: AIDigestProvenance
    limits: Dict[str, Any]
    content_sha256: str
