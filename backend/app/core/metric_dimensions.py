"""Closed operational dimensions; detailed identities belong in SQL/events.

Changing these inventories changes the series budget. Never admit a value just
because it has a short length or matches an identifier-shaped regular expression.
"""
from __future__ import annotations

STAGE_NAMES = frozenset({
    "unknown", "other", "change_ownership", "anomaly_detection", "root_cause_analysis",
    "failure_clustering", "contract_validation", "decision_report", "decision_report_critic",
    "defect_commander", "log_intelligence", "flaky_sentinel", "regression_watchman",
    "gap_detection", "release_risk", "ingestion", "report_refinement", "reviewer",
    "hypothesis_infra", "hypothesis_commit", "hypothesis_environment", "hypothesis_known_flaky",
    "hypothesis_regression", "investigator_synthesis", "test_health", "summary", "triage",
})
FALLBACK_REASONS = frozenset({
    "llm_timeout", "llm_error", "token_limit", "provider_unavailable",
    "no_failure_cluster", "no_log_context", "other",
})
ERROR_CATEGORIES = frozenset({
    "transient", "permanent", "provider_error", "token_limit", "timeout",
    "classification_error", "anomaly_agent_exception", "change_ownership_error", "other",
})
LIFECYCLE_STATES = frozenset({
    "draft", "review_requested", "under_review", "approved", "active", "rejected",
    "needs_update", "deprecated", "archived", "other",
})
ACTOR_ROLES = frozenset({"VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN", "other"})
TASK_STATES = frozenset({
    "pending", "received", "started", "success", "failure", "retry", "revoked",
    "rejected", "ignored", "unknown", "other",
})
# A hard ceiling on the deployed code registry, frozen by the worker on first
# observation. Late/dynamic registrations and over-budget names share "other".
MAX_METRIC_TASK_NAMES = 128
TASK_QUEUE_FAMILIES = frozenset({
    "critical", "ingestion", "ai_analysis", "agent_children", "default",
    "ingestion_shard", "unknown", "other",
})
TASK_QUEUE_WAIT_UNAVAILABLE_REASONS = frozenset({"missing_stamp", "invalid_stamp", "clock_skew", "invalid_eta"})


def bounded_dimension(value: object, allowed: frozenset[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "other"


def task_queue_family(value: object) -> str:
    """Collapse shard indexes without changing routing or per-queue depth."""
    if not value:
        return "unknown"
    if isinstance(value, str) and value.startswith("ingestion.shard."):
        suffix = value.removeprefix("ingestion.shard.")
        if suffix.isascii() and suffix.isdecimal():
            return "ingestion_shard"
    return bounded_dimension(value, TASK_QUEUE_FAMILIES)
