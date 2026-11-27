"""Bounded measurement contracts; proposed thresholds are not adopted SLOs."""
from dataclasses import dataclass

from app.services.performance_budgets import LATENCY_BUDGETS

API_OBJECTIVES = ("api_availability", "api_latency")
API_OUTCOMES = ("good", "bad")
_CORE_OPERATIONS = frozenset({
    "project_list", "run_list", "run_scoped_guard", "run_intelligence", "keyword_search", "flaky_coach",
})
# Route templates are used only for lookup, never emitted as metric labels.
API_ROUTES = {
    (budget.http_method, budget.http_handler): budget.p95_ms / 1000
    for budget in LATENCY_BUDGETS if budget.operation in _CORE_OPERATIONS and budget.http_handler
}
WINDOWS = {"1h": 3600, "6h": 21600, "24h": 86400, "30d": 30 * 86400}
COHORT_OUTCOMES = ("good", "late", "failed", "overdue", "degraded", "unknown")


@dataclass(frozen=True)
class JourneyContract:
    name: str
    source: str
    acceptance: str
    completion: str
    owner: str
    supported: bool


JOURNEYS = (
    JourneyContract("downstream", "run_downstream_outbox", "committed operation/input_version intent",
                    "explicit completed status and completed_at", "backend", True),
    JourneyContract("live_materialization", "run_downstream_outbox", "committed persist_live_session intent",
                    "explicit completed status and completed_at", "backend", True),
    JourneyContract("ai_completion", "agent_pipeline_runs", "committed pipeline identity",
                    "completed/passed plus completed_at; degraded allowed", "AI platform", True),
    JourneyContract("ai_reported_full_quality", "agent_pipeline_runs", "committed pipeline identity",
                    "timely completed/passed with explicit no-fallback and no degraded quality", "AI platform", True),
    JourneyContract("api_durable", "none", "no durable request acceptance ledger", "process completion counters only", "backend", False),
    JourneyContract("upload_acceptance", "none", "202 precedes durable TestRun; advisory Redis status expires",
                    "no durable acceptance/completion timestamps", "backend", False),
    JourneyContract("live_event_acceptance", "none", "Redis accepted stream evidence; SQL receipts begin at projection",
                    "no complete SQL denominator for all accepted events", "backend", False),
    JourneyContract("scheduled_history", "none", "nightly/digest observations retain latest slot, not every expected slot",
                    "current due/last-success metrics only", "SRE", False),
    JourneyContract("backup_verified_restore", "none", "CronJob status is not verified restorable archive",
                    "no durable application restore-verification ledger", "SRE", False),
)
