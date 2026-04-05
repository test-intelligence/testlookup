"""
Role Actions Service — deterministic role-specific action generation.

Single source of truth for qa/developer/sre/release_manager action hints.
Used by: agent.py (fallback), run_intelligence_service (aggregation),
         defect_promotion_service (defect candidate).
"""
from __future__ import annotations
from typing import Optional

ROLES = ("qa", "developer", "sre", "release_manager")

_CATEGORY_ACTIONS: dict[str, dict[str, str]] = {
    "PRODUCT_BUG": {
        "qa": "Verify reproduction steps and update test data if needed.",
        "developer": "Investigate the root cause in the affected component and apply a targeted fix.",
        "sre": "Monitor for cascading failures in dependent services.",
        "release_manager": "Hold release pending fix verification.",
    },
    "INFRASTRUCTURE": {
        "qa": "Re-run tests in a stable environment; mark as environmental blocker if confirmed.",
        "developer": "Check service dependencies and environment configuration.",
        "sre": "Investigate pod crashes, resource limits, and network issues.",
        "release_manager": "Assess whether infrastructure issues are transient or blocking.",
    },
    "TEST_DATA": {
        "qa": "Refresh test data fixtures and verify data integrity.",
        "developer": "Check if recent schema changes invalidated test data.",
        "sre": "Verify external data source availability.",
        "release_manager": "Test data issues are unlikely to block release — monitor after fix.",
    },
    "AUTOMATION_DEFECT": {
        "qa": "Review and fix the automation script (selectors, timing, assertions).",
        "developer": "No code fix needed — this is an automation issue.",
        "sre": "No infrastructure action needed.",
        "release_manager": "Automation defect — does not indicate product risk.",
    },
    "FLAKY": {
        "qa": "Quarantine confirmed flaky tests; stabilize before next release cycle.",
        "developer": "Review for timing issues, shared state, or data dependencies.",
        "sre": "No immediate infra action required; monitor recurrence rate.",
        "release_manager": "Flaky tests are noisy — do not block release unless recurrence is high.",
    },
}

_DEFAULT_ACTIONS: dict[str, str] = {
    "qa": "Triage failing tests and categorize failures before proceeding.",
    "developer": "Investigate root causes of failures in linked analyses.",
    "sre": "Check infrastructure health metrics alongside test failure times.",
    "release_manager": "Assess release readiness based on failure analysis.",
}


def generate_role_actions(
    failure_category: str = "UNKNOWN",
    root_cause_summary: str = "",
    component: Optional[str] = None,
    component_owner_map: Optional[dict] = None,
    confidence_score: int = 0,
    is_flaky: bool = False,
) -> dict[str, str]:
    """
    Generate role-specific actions based on failure category and context.
    Uses component_owner_map for team-specific guidance when available.
    """
    if is_flaky:
        failure_category = "FLAKY"

    base = dict(_CATEGORY_ACTIONS.get(failure_category, _DEFAULT_ACTIONS))

    # Enrich with ownership if available
    if component_owner_map and component:
        base = enrich_with_ownership(base, component, component_owner_map)
    elif component_owner_map:
        base = enrich_with_ownership(base, None, component_owner_map)

    # Low confidence caveat
    if 0 < confidence_score < 50:
        for role in ROLES:
            if role in base:
                base[role] = f"[Low confidence] {base[role]}"

    return base


def enrich_with_ownership(
    base_actions: dict[str, str],
    component: Optional[str],
    component_owner_map: Optional[dict],
) -> dict[str, str]:
    """
    Prepend owner name from component_owner_map to action text.

    component_owner_map format:
      {"auth-service": {"team": "identity", "qa": "alice", "developer": "bob"},
       "default": {"team": "platform", "qa": "QA team"}}
    """
    if not component_owner_map:
        return base_actions

    entry = component_owner_map.get(component or "", {})
    if not entry:
        entry = component_owner_map.get("default", {})
    if not entry:
        return base_actions

    result = dict(base_actions)
    team = entry.get("team", "")
    for role in ROLES:
        owner = entry.get(role, "")
        if owner and role in result:
            result[role] = f"@{owner}: {result[role]}"
        elif team and role in result:
            result[role] = f"[{team}] {result[role]}"
    return result


def merge_role_actions(*action_dicts: dict[str, str]) -> dict[str, str]:
    """
    Merge multiple role_actions dicts. Later non-empty values override earlier ones.
    """
    merged: dict[str, str] = {}
    for d in action_dicts:
        for role in ROLES:
            val = d.get(role, "").strip()
            if val:
                merged[role] = val
    return merged
