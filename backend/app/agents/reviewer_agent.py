"""Generic deterministic reviewer (architecture E6.1).

This first slice runs the cheap checks that precede optional model critique:
output-schema and reference grounding, existing cross-agent consistency checks,
and tool/mode policy checks. E6.2 adds model checks and Supervisor routing.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Literal

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.agents.consistency import (
    ConsistencyReport,
    check_analysis_consistency,
    check_release_consistency,
    check_summary_consistency,
)
from app.models.agent_contracts import (
    ReviewCheckV1,
    ReviewDisagreementV1,
    ReviewedStepV1,
    ReviewerInputV1,
    ReviewVerdictV1,
)
from app.services.agent_catalog import resolve_schema_model
from app.services.agent_capability_registry import get_capability

_REFERENCE_KEYS = {
    "test_case_id": "test_case_ids",
    "test_id": "test_case_ids",
    "test_case_ids": "test_case_ids",
    "test_ids": "test_case_ids",
    "cluster_id": "cluster_ids",
    "failure_cluster_id": "cluster_ids",
    "cluster_ids": "cluster_ids",
    "artifact_id": "artifact_ids",
    "artifact_ids": "artifact_ids",
}
_NUMBER_PATTERN = r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])"
_PROPOSAL_KEYS = frozenset({"proposed_actions", "action_proposals", "mutating_proposals"})


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return [] if value is None else [str(value)]


def _bounded(values: Iterable[str]) -> list[str]:
    return sorted(set(values))[:100]


def _schema_payload(step: ReviewedStepV1) -> dict[str, Any]:
    payload = dict(step.output)
    if "contract" not in payload and isinstance(payload.get("agent_contracts"), dict):
        contracts = payload.pop("agent_contracts")
        contract = contracts.get(step.step_name)
        if contract is None and len(contracts) == 1:
            contract = next(iter(contracts.values()))
        if contract is not None:
            payload["contract"] = contract
    return payload


def _schema_check(step: ReviewedStepV1) -> ReviewCheckV1:
    try:
        model = resolve_schema_model(get_capability(step.step_name).output_schema)
    except ValueError as exc:
        return ReviewCheckV1(
            family=1, name="output_schema_registered", passed=False,
            severity="blocking", detail=str(exc), step_name=step.step_name,
        )
    if model is None:
        return ReviewCheckV1(
            family=1, name="output_schema_resolved", passed=False,
            severity="blocking", detail="registered output schema has no Pydantic model",
            step_name=step.step_name,
        )
    try:
        model.model_validate(_schema_payload(step))
    except ValidationError as exc:
        return ReviewCheckV1(
            family=1, name="output_schema_valid", passed=False,
            severity="blocking",
            detail=f"output violates {model.__name__}: {exc.error_count()} validation error(s)",
            step_name=step.step_name,
        )
    return ReviewCheckV1(
        family=1, name="output_schema_valid", passed=True, severity="blocking",
        detail=f"output validates as {model.__name__}", step_name=step.step_name,
    )


def _grounding_check(step: ReviewedStepV1, payload: ReviewerInputV1) -> ReviewCheckV1:
    allowed = {
        name: set(getattr(payload.references, name))
        for name in ("test_case_ids", "cluster_ids", "artifact_ids")
    }
    offenders: list[str] = []
    for key, value in _walk(step.output):
        bucket = _REFERENCE_KEYS.get(key)
        if bucket is None:
            continue
        offenders.extend(
            f"{key}:{item}" for item in _values(value) if item not in allowed[bucket]
        )
    return ReviewCheckV1(
        family=1, name="references_grounded", passed=not offenders,
        severity="blocking",
        detail=("all cited identifiers exist in state" if not offenders
                else "output cites identifiers absent from state"),
        step_name=step.step_name, offending_refs=_bounded(offenders),
    )


def _numeric_check(step: ReviewedStepV1, payload: ReviewerInputV1) -> ReviewCheckV1:
    offenders: list[str] = []
    prose = [value for _, value in _walk(step.output) if isinstance(value, str)]
    for fact, expected in payload.numeric_facts.items():
        labels = {fact.lower(), fact.replace("_", " ").lower()}
        label_pattern = "|".join(re.escape(label) for label in sorted(labels))
        labelled_number = re.compile(
            rf"(?:(?:{label_pattern})[^\d-]{{0,16}}(?P<after>{_NUMBER_PATTERN})|"
            rf"(?P<before>{_NUMBER_PATTERN})[^A-Za-z_]{{0,16}}(?:{label_pattern}))",
            re.IGNORECASE,
        )
        for prose_item in prose:
            for match in labelled_number.finditer(prose_item.replace(",", "")):
                token = match.group("after") or match.group("before")
                observed = float(token)
                tolerance = max(payload.numeric_tolerance, abs(expected) * payload.numeric_tolerance)
                if abs(observed - expected) > tolerance:
                    offenders.append(f"{fact}:{observed:g}!={expected:g}")
    return ReviewCheckV1(
        family=1, name="quoted_numbers_match_state", passed=not offenders,
        severity="blocking",
        detail=("quoted numeric facts match state" if not offenders
                else "prose contradicts numeric state"),
        step_name=step.step_name, offending_refs=_bounded(offenders),
    )


def _consistency_report(
    step: ReviewedStepV1, payload: ReviewerInputV1
) -> ConsistencyReport | None:
    if step.step_name == "summary":
        return check_summary_consistency(
            step.output.get("structured_summary"), payload.run_data,
            payload.references.test_case_ids, payload.analyses,
        )
    if step.step_name == "root_cause_analysis":
        return check_analysis_consistency(
            step.output.get("analyses"), payload.references.test_case_ids
        )
    if step.step_name == "release_risk":
        return check_release_consistency(step.output.get("release_decision"))
    if step.step_name in {"decision_report", "decision_report_critic"}:
        decision = step.output.get("decision_intelligence")
        if isinstance(decision, dict):
            return check_release_consistency(decision.get("release_decision"))
    return None


def _consistency_checks(
    step: ReviewedStepV1, payload: ReviewerInputV1
) -> tuple[list[ReviewCheckV1], list[ReviewDisagreementV1]]:
    report = _consistency_report(step, payload)
    if report is None:
        return ([ReviewCheckV1(
            family=2, name="cross_agent_consistency_not_applicable", passed=True,
            severity="info", detail="no existing consistency family applies to this step",
            step_name=step.step_name,
        )], [])
    checks: list[ReviewCheckV1] = []
    disagreements: list[ReviewDisagreementV1] = []
    for check in report.checks:
        severity: Literal["warning", "blocking"] = (
            "blocking" if check.severity == "error" else "warning"
        )
        checks.append(ReviewCheckV1(
            family=2, name=check.name, passed=check.passed, severity=severity,
            detail=check.details, step_name=step.step_name,
            offending_refs=_bounded(check.offending_refs),
        ))
        if not check.passed:
            disagreements.append(ReviewDisagreementV1(
                kind=check.name, agents=[step.step_name, "workflow_state"],
                resolution="flag_for_review" if severity == "warning" else "reject",
                severity=severity,
            ))
    return checks, disagreements


def _policy_checks(step: ReviewedStepV1) -> list[ReviewCheckV1]:
    unknown = sorted(set(step.tools_used) - set(step.tool_permissions))
    try:
        stage_permission = get_capability(step.step_name).permission
    except ValueError:
        stage_permission = "mutating"
    unauthorized = sorted(
        tool for tool in step.tools_used
        if (
            step.tool_permissions.get(tool) == "mutating"
            or stage_permission == "mutating"
        ) and step.mode != "act"
    )
    proposals: list[Any] = []
    for key, value in _walk(step.output):
        if key in _PROPOSAL_KEYS and isinstance(value, list):
            proposals.extend(value)
    non_proposals: list[str] = []
    if step.mode != "act":
        for item in proposals:
            if not isinstance(item, dict):
                non_proposals.append(str(item))
            elif item.get("status", "proposed") != "proposed":
                non_proposals.append(str(item.get("id") or item.get("action_id") or "unknown"))
    policy_ok = not unknown and not unauthorized
    return [
        ReviewCheckV1(
            family=5, name="tools_have_declared_policy", passed=policy_ok,
            severity="blocking",
            detail=("all used tools are policy-authorized" if policy_ok
                    else "a used tool lacks authorization"),
            step_name=step.step_name,
            offending_refs=[
                *(f"undeclared:{tool}" for tool in unknown),
                *(f"mode:{tool}" for tool in unauthorized),
            ],
        ),
        ReviewCheckV1(
            family=5, name="mutating_actions_are_proposals_below_act",
            passed=not non_proposals, severity="warning",
            detail=("mutating actions remain proposals until mode=act" if not non_proposals
                    else "non-act output contains a mutating action beyond proposed state"),
            step_name=step.step_name, offending_refs=_bounded(non_proposals),
        ),
    ]


def review_payload(value: ReviewerInputV1 | dict[str, Any]) -> ReviewVerdictV1:
    payload = value if isinstance(value, ReviewerInputV1) else ReviewerInputV1.model_validate(value)
    checks: list[ReviewCheckV1] = []
    disagreements: list[ReviewDisagreementV1] = []
    for step in payload.reviewed_steps:
        checks.extend((_schema_check(step), _grounding_check(step, payload), _numeric_check(step, payload)))
        family_checks, family_disagreements = _consistency_checks(step, payload)
        checks.extend(family_checks)
        disagreements.extend(family_disagreements)
        checks.extend(_policy_checks(step))
    failed = [check for check in checks if not check.passed]
    blocking = [check for check in failed if check.severity == "blocking"]
    verdict: Literal["pass", "pass_with_flags", "reject"] = (
        "reject" if blocking else "pass_with_flags" if failed or disagreements else "pass"
    )
    risk: Literal["low", "medium", "high"] = (
        "high" if blocking else "medium" if failed or disagreements else "low"
    )
    return ReviewVerdictV1(
        reviewed_steps=[step.step_name for step in payload.reviewed_steps],
        checks=checks, verdict=verdict, disagreements=disagreements,
        hallucination_risk=risk, requires_human_review=verdict != "pass",
        second_model=None,
    )


def validate_review_verdict(value: ReviewVerdictV1 | dict[str, Any]) -> ReviewVerdictV1:
    """Validate a reviewer result, replacing any contradiction with rejection."""
    if isinstance(value, ReviewVerdictV1):
        return value
    try:
        return ReviewVerdictV1.model_validate(value)
    except ValidationError as exc:
        steps = value.get("reviewed_steps") if isinstance(value, dict) else None
        safe_steps = (
            [str(step)[:80] for step in steps[:20]]
            if isinstance(steps, list) and steps else ["unknown"]
        )
        return ReviewVerdictV1(
            reviewed_steps=safe_steps,
            checks=[ReviewCheckV1(
                family=1, name="review_verdict_invariants", passed=False,
                severity="blocking",
                detail=f"review verdict failed closed: {exc.error_count()} invariant error(s)",
            )],
            verdict="reject", hallucination_risk="high", requires_human_review=True,
        )


class ReviewerAgent(BaseAgent):
    stage_name = "reviewer"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        pipeline_run_id = str(state.get("pipeline_run_id") or "")
        if pipeline_run_id:
            await self.mark_stage_running(pipeline_run_id, input_keys=list(state))
        raw = state.get("reviewer_input", state)
        try:
            verdict = review_payload(raw)
        except ValidationError as exc:
            verdict = validate_review_verdict({
                "reviewed_steps": ["unknown"], "checks": [], "verdict": "pass",
                "hallucination_risk": "high", "requires_human_review": False,
                "input_error": str(exc),
            })
        await self.log_decision(
            pipeline_run_id, "deterministic_review", verdict.verdict,
            "reviewed schema, grounding, consistency, and policy checks",
            alternatives=["pass", "pass_with_flags", "reject"],
            context={"failed_checks": [check.name for check in verdict.checks if not check.passed]},
        )
        result = verdict.model_dump(mode="json")
        if pipeline_run_id:
            await self.mark_stage_done(pipeline_run_id, result_data=result)
        return {"review_verdict": result, "current_stage": "reviewer"}
