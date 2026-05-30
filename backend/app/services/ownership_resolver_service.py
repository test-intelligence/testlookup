"""
Ownership Resolver Service — resolves test→suite→component→service→team hierarchy.

Resolution precedence (highest to lowest):
1. ServiceOwnershipRule with highest priority matching the test's suite_name, component, package, etc.
2. Project.component_owner_map legacy lookup
3. TestCase.owner field (from Allure labels)
4. Fallback: "Unassigned" with low confidence

Each resolution returns a confidence level (high/medium/low/none) and the match source.
"""
from __future__ import annotations

import fnmatch
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import Project, ServiceOwnershipRule, TestCase

logger = logging.getLogger("services.ownership_resolver")


@dataclass
class OwnershipResult:
    service_name: str | None = None
    team_name: str | None = None
    team_contact: str | None = None
    confidence: str = "none"  # high | medium | low | none
    matched_rule_id: str | None = None
    match_source: str | None = None
    fallback_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "service_name": self.service_name,
            "team_name": self.team_name,
            "team_contact": self.team_contact,
            "confidence": self.confidence,
            "matched_rule_id": self.matched_rule_id,
            "match_source": self.match_source,
            "fallback_reason": self.fallback_reason,
        }


async def load_rules_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[ServiceOwnershipRule]:
    """Load all active ownership rules for a project, ordered by priority desc."""
    result = await db.execute(
        select(ServiceOwnershipRule)
        .where(
            ServiceOwnershipRule.project_id == project_id,
            ServiceOwnershipRule.is_active == True,  # noqa: E712
        )
        .order_by(ServiceOwnershipRule.priority.desc(), ServiceOwnershipRule.created_at)
    )
    return list(result.scalars().all())


def _match_rule(rule: ServiceOwnershipRule, test_attrs: dict) -> bool:
    """Check if a rule matches against the test attributes using glob matching."""
    target_value = test_attrs.get(rule.match_type, "")
    if not target_value:
        return False
    return fnmatch.fnmatch(target_value.lower(), rule.match_pattern.lower())


def resolve_test_ownership(
    rules: list[ServiceOwnershipRule],
    test_attrs: dict,
    component_owner_map: dict | None = None,
) -> OwnershipResult:
    """
    Resolve ownership for a single test case.

    test_attrs should contain keys like: suite_name, component, package, path, label, owner
    """
    # 1. Try ownership rules (highest priority first — list is pre-sorted)
    for rule in rules:
        if _match_rule(rule, test_attrs):
            return OwnershipResult(
                service_name=rule.service_name,
                team_name=rule.team_name,
                team_contact=rule.team_contact,
                confidence="high",
                matched_rule_id=str(rule.id),
                match_source=rule.match_type,
            )

    # 2. Try project component_owner_map (legacy)
    if component_owner_map:
        component = test_attrs.get("component") or test_attrs.get("suite_name") or ""
        for comp_key, owner_info in component_owner_map.items():
            if comp_key == "default":
                continue
            if component and fnmatch.fnmatch(component.lower(), comp_key.lower()):
                team = owner_info.get("team", comp_key) if isinstance(owner_info, dict) else str(owner_info)
                return OwnershipResult(
                    service_name=comp_key,
                    team_name=team,
                    confidence="medium",
                    match_source="component_owner_map",
                )
        # Try default
        default_info = component_owner_map.get("default")
        if default_info:
            team = default_info.get("team", "Default") if isinstance(default_info, dict) else str(default_info)
            return OwnershipResult(
                service_name="default",
                team_name=team,
                confidence="low",
                match_source="component_owner_map",
                fallback_reason="Matched 'default' entry in component_owner_map",
            )

    # 3. Try test-level owner (Allure label)
    test_owner = test_attrs.get("owner")
    if test_owner:
        return OwnershipResult(
            service_name=None,
            team_name=test_owner,
            confidence="low",
            match_source="test_owner_label",
            fallback_reason="Used test-level @Owner annotation",
        )

    # 4. Fallback
    return OwnershipResult(
        confidence="none",
        fallback_reason="No ownership rule matched. Configure rules in the Ownership Editor.",
    )


async def resolve_cluster_ownership(
    db: AsyncSession,
    project_id: uuid.UUID,
    member_test_ids: list[str],
) -> OwnershipResult:
    """
    Resolve ownership for a failure cluster by examining its member tests.

    Uses majority voting: the team that owns the most member tests wins.
    """
    if not member_test_ids:
        return OwnershipResult(
            confidence="none",
            fallback_reason="Cluster has no member tests",
        )

    memory_resolution = await _resolve_cluster_ownership_from_memory(
        db,
        project_id,
        member_test_ids,
    )
    if memory_resolution:
        return memory_resolution

    # Load rules and project
    rules = await load_rules_for_project(db, project_id)
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalar_one_or_none()
    component_owner_map = project.component_owner_map if project else None

    # Load test case attributes for members
    test_uuids = []
    for tid in member_test_ids[:50]:  # cap to avoid huge queries
        try:
            test_uuids.append(uuid.UUID(str(tid)))
        except (ValueError, TypeError):
            continue

    if not test_uuids:
        return OwnershipResult(confidence="none", fallback_reason="No valid test IDs in cluster")

    result = await db.execute(
        select(TestCase).where(TestCase.id.in_(test_uuids))
    )
    tests = result.scalars().all()

    # Resolve each test and vote
    team_votes: dict[str, list[OwnershipResult]] = {}
    for tc in tests:
        attrs = {
            "suite_name": tc.suite_name or "",
            "component": tc.class_name or "",
            "package": tc.package_name or "",
            "path": tc.full_name or "",
            "label": tc.feature or tc.epic or "",
            "owner": tc.owner or "",
        }
        resolution = resolve_test_ownership(rules, attrs, component_owner_map)
        team_key = resolution.team_name or "unknown"
        team_votes.setdefault(team_key, []).append(resolution)

    if not team_votes:
        return OwnershipResult(confidence="none", fallback_reason="Could not resolve any member test ownership")

    # Pick the team with most votes
    best_team = max(team_votes, key=lambda k: len(team_votes[k]))
    best_results = team_votes[best_team]
    best = best_results[0]
    vote_pct = len(best_results) / len(tests) if tests else 0

    # Confidence based on vote concentration
    if vote_pct >= 0.8:
        confidence = "high"
    elif vote_pct >= 0.5:
        confidence = "medium"
    else:
        confidence = "low"

    return OwnershipResult(
        service_name=best.service_name,
        team_name=best.team_name,
        team_contact=best.team_contact,
        confidence=confidence,
        matched_rule_id=best.matched_rule_id,
        match_source=best.match_source,
        fallback_reason=f"{len(best_results)}/{len(tests)} members mapped to this team" if vote_pct < 1.0 else None,
    )


async def _resolve_cluster_ownership_from_memory(
    db: AsyncSession,
    project_id: uuid.UUID,
    member_test_ids: list[str],
) -> OwnershipResult | None:
    """Prefer prior canonical memory ownership for ownership-aware routing."""
    try:
        from app.services.agent_memory_service import resolve_ownership_from_memory

        context = await resolve_ownership_from_memory(
            db,
            project_id,
            member_test_ids=member_test_ids,
        )
    except Exception as exc:
        logger.debug("Ownership memory lookup skipped: %s", exc)
        return None
    if not context:
        return None
    ownership = context.get("ownership") or {}
    if not isinstance(ownership, dict):
        return None
    team_name = ownership.get("team_name")
    service_name = ownership.get("service_name")
    if not team_name and not service_name:
        return None
    confidence = str(ownership.get("confidence") or "low")
    if confidence not in {"high", "medium", "low", "none"}:
        confidence = "low"
    return OwnershipResult(
        service_name=service_name,
        team_name=team_name,
        team_contact=ownership.get("team_contact"),
        confidence=confidence,
        matched_rule_id=ownership.get("matched_rule_id"),
        match_source="agent_memory",
        fallback_reason=(
            f"Resolved from canonical memory reference "
            f"{context.get('memory_reference', {}).get('memory_entry_id')}"
        ),
    )
