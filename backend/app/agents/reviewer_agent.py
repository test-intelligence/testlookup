"""Generic reviewer with deterministic and bounded independent model checks."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

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
    ReviewerAgentOutput,
    ReviewerInputV1,
    ReviewVerdictV1,
    SecondModelCheckV1,
    validate_agent_contract,
)
from app.models.postgres import AgentPipelineRun
from app.services.agent_catalog import resolve_schema_model
from app.services.agent_capability_registry import get_capability
from app.services.agent_config_resolver import (
    ResolvedAgentConfig,
    ResolvedEndpoint,
    resolve_for_pipeline,
)
from app.services.llm_factory import get_llm
from app.services.llm_json_parser import parse_llm_json
from app.services.pipeline_budget_service import get_pipeline_budget_context
from app.services.prompt_registry import get_prompt_text, prompt_versions_used
from app.services.review_supervisor import supervise_review
from app.services.step_llm_budget import StepLLMBudget

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
_HIDDEN_REASONING_KEYS = frozenset({
    "chain_of_thought", "chainOfThought", "agent_scratchpad", "reasoning_trace",
    "hidden_reasoning", "thoughts",
})


class _ClaimExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_ids: list[str] = Field(default_factory=list, max_length=100)
    numeric_facts: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bounded_finite_claims(self) -> "_ClaimExtraction":
        if any(not item or len(item) > 200 for item in self.reference_ids):
            raise ValueError("reference ids must contain 1-200 characters")
        if len(self.numeric_facts) > 100:
            raise ValueError("numeric_facts exceeds 100 entries")
        if any(
            not key or len(key) > 120 or not math.isfinite(value)
            for key, value in self.numeric_facts.items()
        ):
            raise ValueError("numeric facts must have bounded names and finite values")
        return self


class _SecondModelAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agreement_score: float = Field(ge=0.0, le=1.0)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=100)
    blocking_unsupported_claims: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _bounded_claims(self) -> "_SecondModelAssessment":
        claims = [*self.unsupported_claims, *self.blocking_unsupported_claims]
        if any(not item or len(item) > 500 for item in claims):
            raise ValueError("unsupported claims must contain 1-500 characters")
        return self


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


def _without_hidden_reasoning(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_hidden_reasoning(item)
            for key, item in value.items()
            if str(key) not in _HIDDEN_REASONING_KEYS
        }
    if isinstance(value, list):
        return [_without_hidden_reasoning(item) for item in value]
    return value


def _response_text(response: Any) -> str:
    value = response.content if hasattr(response, "content") else response
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _allowed_reference_ids(payload: ReviewerInputV1) -> set[str]:
    return {
        *payload.references.test_case_ids,
        *payload.references.cluster_ids,
        *payload.references.artifact_ids,
    }


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


async def _self_consistency_check(
    step: ReviewedStepV1,
    payload: ReviewerInputV1,
    *,
    endpoint: ResolvedEndpoint,
) -> ReviewCheckV1:
    prompt = get_prompt_text("reviewer_claim_extraction").format(
        output=json.dumps(_without_hidden_reasoning(step.output), sort_keys=True, default=str)[:12_000]
    )
    try:
        llm = await get_llm(endpoint=endpoint)
        response = await llm.ainvoke(prompt)
        parsed, error = parse_llm_json(
            _response_text(response),
            expected_keys=["reference_ids", "numeric_facts"],
            context="reviewer_claim_extraction",
        )
        if error:
            raise ValueError(error)
        claims = _ClaimExtraction.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001 -- a reviewer model failure degrades safely
        return ReviewCheckV1(
            family=3,
            name="self_consistency_available",
            passed=False,
            severity="warning",
            detail=f"claim extraction unavailable: {type(exc).__name__}",
            step_name=step.step_name,
        )

    allowed = _allowed_reference_ids(payload)
    offenders = [f"reference:{item}" for item in claims.reference_ids if item not in allowed]
    for fact, observed in claims.numeric_facts.items():
        expected = payload.numeric_facts.get(fact)
        if expected is None:
            offenders.append(f"numeric:{fact}:unsupported")
            continue
        tolerance = max(payload.numeric_tolerance, abs(expected) * payload.numeric_tolerance)
        if abs(observed - expected) > tolerance:
            offenders.append(f"numeric:{fact}:{observed:g}!={expected:g}")
    return ReviewCheckV1(
        family=3,
        name="self_consistency_claims_grounded",
        passed=not offenders,
        severity="warning",
        detail=(
            "model-extracted claims agree with the evidence snapshot"
            if not offenders else "model-extracted claims are absent from the evidence snapshot"
        ),
        step_name=step.step_name,
        offending_refs=_bounded(offenders),
    )


async def _second_model_check(
    step: ReviewedStepV1,
    payload: ReviewerInputV1,
    *,
    endpoint: ResolvedEndpoint,
) -> tuple[ReviewCheckV1, SecondModelCheckV1 | None, list[ReviewDisagreementV1]]:
    evidence = {
        "references": payload.references.model_dump(mode="json"),
        "numeric_facts": payload.numeric_facts,
        "run_data": payload.run_data,
        "analyses": payload.analyses,
    }
    prompt = get_prompt_text("reviewer_second_model").format(
        output=json.dumps(_without_hidden_reasoning(step.output), sort_keys=True, default=str)[:12_000],
        evidence=json.dumps(
            _without_hidden_reasoning(evidence), sort_keys=True, default=str
        )[:12_000],
    )
    try:
        llm = await get_llm(endpoint=endpoint)
        response = await llm.ainvoke(prompt)
        parsed, error = parse_llm_json(
            _response_text(response),
            expected_keys=[
                "agreement_score", "unsupported_claims", "blocking_unsupported_claims"
            ],
            context="reviewer_second_model",
        )
        if error:
            raise ValueError(error)
        assessment = _SecondModelAssessment.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001 -- optional critique degrades safely
        return (
            ReviewCheckV1(
                family=4, name="second_model_available", passed=False,
                severity="warning", detail=f"second model unavailable: {type(exc).__name__}",
                step_name=step.step_name,
            ),
            None,
            [],
        )

    second_model = SecondModelCheckV1(
        provider=endpoint.provider,
        model=endpoint.model,
        agreement_score=assessment.agreement_score,
    )
    offenders = [*assessment.blocking_unsupported_claims, *assessment.unsupported_claims]
    passed = assessment.agreement_score >= 0.7 and not offenders
    severity: Literal["warning", "blocking"] = (
        "blocking" if assessment.blocking_unsupported_claims else "warning"
    )
    disagreements = [] if passed else [ReviewDisagreementV1(
        kind="second_model_disagreement",
        agents=[step.step_name, "reviewer"],
        resolution="retry" if severity == "blocking" else "flag_for_review",
        severity=severity,
    )]
    return (
        ReviewCheckV1(
            family=4,
            name="second_model_agreement",
            passed=passed,
            severity=severity,
            detail="independent model agrees" if passed else "independent model found unsupported claims",
            step_name=step.step_name,
            offending_refs=_bounded(offenders),
        ),
        second_model,
        disagreements,
    )


def _verdict_with_model_checks(
    base: ReviewVerdictV1,
    *,
    checks: list[ReviewCheckV1],
    disagreements: list[ReviewDisagreementV1],
    second_model: SecondModelCheckV1 | None,
    retry_count: int,
    budget_blocked: bool,
) -> ReviewVerdictV1:
    all_checks = [*base.checks, *checks]
    all_disagreements = [*base.disagreements, *disagreements]
    deterministic_block = any(
        not check.passed and check.severity == "blocking" and check.family in {1, 2, 5}
        for check in all_checks
    )
    semantic_block = any(
        not check.passed and check.severity == "blocking" and check.family in {3, 4}
        for check in all_checks
    )
    failed = any(not check.passed for check in all_checks)
    if deterministic_block:
        verdict: Literal["pass", "pass_with_flags", "retry", "reject"] = "reject"
    elif semantic_block:
        verdict = "retry" if retry_count < 1 else "reject"
    elif failed or all_disagreements or budget_blocked:
        verdict = "pass_with_flags"
    else:
        verdict = "pass"
    risk: Literal["low", "medium", "high"] = (
        "high" if deterministic_block or semantic_block else "medium" if verdict != "pass" else "low"
    )
    return ReviewVerdictV1(
        reviewed_steps=base.reviewed_steps,
        checks=all_checks,
        verdict=verdict,
        disagreements=all_disagreements,
        hallucination_risk=risk,
        requires_human_review=verdict != "pass",
        second_model=second_model,
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

    async def _resolved_config(self, state: dict[str, Any]) -> ResolvedAgentConfig | None:
        pipeline_run_id = str(state.get("pipeline_run_id") or "")
        project_id = state.get("project_id")
        if not pipeline_run_id or not project_id:
            return None
        try:
            import uuid

            async with self._db_session() as db:
                pipeline = (
                    await db.execute(
                        select(AgentPipelineRun).where(AgentPipelineRun.id == pipeline_run_id)
                    )
                ).scalar_one_or_none()
                return await resolve_for_pipeline(
                    db, pipeline, uuid.UUID(str(project_id)), get_capability(self.stage_name).capability_id
                )
        except Exception as exc:  # noqa: BLE001 -- model review is optional
            self.logger.warning(
                "reviewer_config_unavailable",
                pipeline_run_id=pipeline_run_id,
                error_type=type(exc).__name__,
            )
            return None

    def _db_session(self):
        from app.db.postgres import AsyncSessionLocal

        return AsyncSessionLocal()

    @staticmethod
    def _budget(state: dict[str, Any], resolved: ResolvedAgentConfig | None) -> StepLLMBudget:
        raw = state.get("step_llm_budget")
        if raw is not None:
            try:
                return StepLLMBudget.from_state(raw)
            except ValueError:
                return StepLLMBudget(limit=0)
        max_escalations = resolved.config.model.escalation.max_escalations if resolved else 1
        raw_iterations = state.get("review_max_iterations", 1)
        max_iterations = (
            max(0, raw_iterations)
            if isinstance(raw_iterations, int) and not isinstance(raw_iterations, bool)
            else 0
        )
        run_remaining = (
            resolved.config.budget.max_llm_calls_per_run if resolved else 0
        )
        context = get_pipeline_budget_context() or {}
        if context.get("blocked"):
            run_remaining = 0
        observed = context.get("observed_llm_calls", 0)
        if not isinstance(observed, int) or isinstance(observed, bool) or observed < 0:
            observed = run_remaining
        run_remaining = max(0, run_remaining - observed)
        return StepLLMBudget.create(
            max_escalations=max_escalations,
            max_iterations=max_iterations,
            run_llm_calls_remaining=run_remaining,
        )

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        pipeline_run_id = str(state.get("pipeline_run_id") or "")
        if pipeline_run_id:
            await self.mark_stage_running(pipeline_run_id, input_keys=list(state))
        raw = state.get("reviewer_input", state)
        payload: ReviewerInputV1 | None = None
        try:
            payload = ReviewerInputV1.model_validate(raw)
            verdict = review_payload(payload)
        except ValidationError as exc:
            verdict = validate_review_verdict({
                "reviewed_steps": ["unknown"], "checks": [], "verdict": "pass",
                "hallucination_risk": "high", "requires_human_review": False,
                "input_error": str(exc),
            })
        resolved = await self._resolved_config(state)
        budget = self._budget(state, resolved)
        raw_retry_count = state.get("review_retry_count", 0)
        retry_count = (
            min(max(0, raw_retry_count), 1)
            if isinstance(raw_retry_count, int) and not isinstance(raw_retry_count, bool)
            else 1
        )
        model_checks: list[ReviewCheckV1] = []
        model_disagreements: list[ReviewDisagreementV1] = []
        second_model = None
        budget_blocked = False
        if (
            payload is not None
            and verdict.verdict != "reject"
            and resolved is not None
            and resolved.config.review.auto_reviewer
        ):
            for step in payload.reviewed_steps:
                if step.model_tier in {"slm", "llm"}:
                    endpoint = resolved.endpoints.get(step.model_tier)
                    if endpoint is None:
                        budget_blocked = True
                        model_checks.append(ReviewCheckV1(
                            family=3, name="self_consistency_budgeted", passed=False,
                            severity="warning", detail="self-consistency used deterministic fallback",
                            step_name=step.step_name,
                        ))
                    else:
                        model_checks.append(await _self_consistency_check(
                            step, payload, endpoint=endpoint
                        ))
                if resolved.config.review.second_model_check:
                    endpoint = resolved.endpoints.get("llm")
                    generator_known = bool(step.model_provider and step.model_name)
                    same_or_unknown_model = bool(
                        not generator_known
                        or (
                            endpoint is not None
                            and (step.model_provider or "").strip().lower()
                            == endpoint.provider.strip().lower()
                            and (step.model_name or "").strip().lower()
                            == endpoint.model.strip().lower()
                        )
                    )
                    if (
                        endpoint is None
                        or same_or_unknown_model
                    ):
                        budget_blocked = budget_blocked or endpoint is None or budget.remaining == 0
                        model_checks.append(ReviewCheckV1(
                            family=4, name="independent_second_model", passed=False,
                            severity="warning",
                            detail=("no distinct reviewer model is configured" if same_or_unknown_model
                                    else "second-model check used deterministic fallback"),
                            step_name=step.step_name,
                        ))
                    else:
                        check, observed_model, observed_disagreements = await _second_model_check(
                            step, payload, endpoint=endpoint
                        )
                        model_checks.append(check)
                        second_model = observed_model
                        model_disagreements.extend(observed_disagreements)
            verdict = _verdict_with_model_checks(
                verdict,
                checks=model_checks,
                disagreements=model_disagreements,
                second_model=second_model,
                retry_count=retry_count,
                budget_blocked=budget_blocked,
            )
        verdict, supervisor = supervise_review(
            verdict, retry_count=retry_count, budget=budget
        )
        policy_incidents = [
            check for check in verdict.checks
            if check.family == 5 and not check.passed and check.severity == "blocking"
        ]
        if policy_incidents:
            await self.log_decision(
                pipeline_run_id, "reviewer_incident", "policy_denied",
                "review found unauthorized tool use",
                context={"checks": [check.name for check in policy_incidents]},
            )
        used_model_checks = bool(model_checks)
        await self.log_decision(
            pipeline_run_id,
            "model_review" if used_model_checks else "deterministic_review",
            verdict.verdict,
            "reviewed deterministic and configured independent-model checks",
            alternatives=["pass", "pass_with_flags", "reject"],
            context={
                "failed_checks": [check.name for check in verdict.checks if not check.passed],
                "prompt_versions": prompt_versions_used(
                    "reviewer_claim_extraction", "reviewer_second_model"
                ) if used_model_checks else {},
                "supervisor_route": supervisor.route,
                "step_llm_budget": budget.as_state(),
            },
        )
        result = verdict.model_dump(mode="json")
        if pipeline_run_id:
            await self.mark_stage_done(pipeline_run_id, result_data=result)
        contracted = validate_agent_contract(
            ReviewerAgentOutput,
            {
                "review_verdict": result,
                "supervisor": supervisor.model_dump(mode="json"),
                "step_llm_budget": budget.as_state(),
            },
            agent_name=self.stage_name,
            fallback_used=verdict.verdict == "reject",
            confidence={"pass": 100, "pass_with_flags": 70}.get(verdict.verdict, 0),
            evidence_refs=[
                {"type": "reviewed_step", "id": step_name}
                for step_name in verdict.reviewed_steps
            ],
            decision_reason=(
                f"model_review_{verdict.verdict}"
                if used_model_checks else f"deterministic_review_{verdict.verdict}"
            ),
        )
        return {**contracted, "current_stage": "reviewer"}
