"""
Release Risk Agent (Stage 6).

Produces a structured go/no-go release recommendation using a two-step approach:

Step 1 — Deterministic 7-dimension scoring model:
  Computes an explainable, auditable risk score from quantitative pipeline data.
  No LLM required for the score itself — results are reproducible and debuggable.

  Dimensions (each 0–100, weighted):
    1. user_impact        (weight 0.25) — PRODUCT_BUG count + failed test blast radius
    2. env_sensitivity    (weight 0.10) — infrastructure failures / env anomalies
    3. reproducibility    (weight 0.15) — fraction of non-flaky failures
    4. regression_likely  (weight 0.20) — new regressions vs historical baseline
    5. hist_recurrence    (weight 0.10) — failures that appeared before (known issues)
    6. blast_radius       (weight 0.15) — clusters spanning multiple suites/services
    7. diagnosis_conf     (weight 0.05) — average confidence score of analyses

  composite_risk = Σ(dim_i * weight_i), clipped to [0, 100]

Step 2 — LLM reasoning (optional):
  The LLM receives the deterministic scores and is asked ONLY to write a narrative
  explanation and enumerate specific blocking issues / conditions.  The recommendation
  itself is derived from the composite score, not the LLM output, making it tamper-proof.

  Recommendation thresholds (configurable via settings):
    composite_risk < 20  → GO
    20 ≤ composite_risk < 55 → CONDITIONAL_GO
    composite_risk ≥ 55  → NO_GO

  Pass-rate hard floor: if pass_rate < threshold * 0.7 → NO_GO regardless of score.
"""
import asyncio
import json
import structlog
import uuid

from app.agents.base import BaseAgent
from app.agents.consistency import (
    check_release_consistency,
    log_consistency_failures,
)
from app.agents.evidence import EvidenceRef
from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.agent_contracts import ReleaseRiskAgentOutput, validate_agent_contract
from app.models.postgres import Defect, ReleaseDecision
from app.services.criticality_service import (
    SCORE_MODEL_VERSION,
    compute_composite,
    compute_dimension_scores,
    score_to_recommendation,
)
from app.models.llm_schemas import ReleaseReasoning, validate_llm_output
from app.services.llm_factory import get_llm
from app.services.llm_json_parser import parse_llm_json
from app.services.redaction_service import redact_text

logger = structlog.get_logger("agents.release_risk")

_LLM_REASONING_TIMEOUT = min(90, settings.AI_TIMEOUT_SECONDS)

_REASONING_PROMPT = """\
You are a QA release gate analyst. The automated risk scoring model produced these results:

Deterministic Risk Scores (0-100 each):
{scores_json}

Composite Risk Score: {composite}/100
Automated Recommendation: {recommendation}

Test Run Context:
{summary}

Failure Details:
{failures}

GROUNDING RULES:
- The recommendation field ({recommendation}) is DETERMINISTIC — do not override or contradict it.
- Your job is to EXPLAIN the score, not re-evaluate it.
- If failure details are empty, state "No failures detected" — never fabricate failure descriptions.
- Only list blocking_issues that are directly supported by the failure data above.
- List conditions_for_go ONLY when recommendation is CONDITIONAL_GO; leave empty for GO or NO_GO.

EXAMPLE (CONDITIONAL_GO with blocking issues):
{{
  "reasoning": "Composite risk score of 38/100 driven primarily by 3 product bugs in the checkout flow and an elevated regression signal. Pass rate of 91% is above threshold but the checkout failures affect a critical user journey.",
  "blocking_issues": ["Checkout payment validation fails on amounts > $999", "Cart total mismatch after coupon removal"],
  "conditions_for_go": ["Fix both checkout bugs and rerun the e2e-checkout suite"]
}}

EXAMPLE (GO with no issues):
{{
  "reasoning": "Composite risk score of 12/100 with all dimensions in the green zone. 98.5% pass rate with only minor flaky test recurrences. No new regressions detected.",
  "blocking_issues": [],
  "conditions_for_go": []
}}

Respond ONLY with a valid JSON object:
{{
  "reasoning": "...",
  "blocking_issues": ["issue1", "issue2"],
  "conditions_for_go": ["condition1"]
}}"""

# Composite risk thresholds below which LLM is skipped (cost optimization)
_EXTREME_GO_THRESHOLD = 10         # Clearly safe — skip LLM
_EXTREME_NO_GO_THRESHOLD = 75      # Clearly blocked — skip LLM


class ReleaseRiskAgent(BaseAgent):
    stage_name = "release_risk"

    async def run(self, state: dict) -> dict:
        pipeline_run_id: str = state["pipeline_run_id"]
        project_id: str = state["project_id"]
        test_run_id: str = state["test_run_id"]

        await self.mark_stage_running(pipeline_run_id)
        await self.broadcast_progress(project_id, {"status": "running", "message": "Evaluating release readiness…"})

        try:
            decision = await self._evaluate(state)
        except Exception as exc:
            # structlog-on-stdlib-positional-args trap: pass via kwargs.
            logger.error(
                "release_risk_evaluation_failed",
                error=str(exc), exc_info=True,
            )
            decision = {
                "recommendation": "CONDITIONAL_GO",
                "risk_score": 50,
                "dimension_scores": {},
                "blocking_issues": [],
                "conditions_for_go": ["Manual review required — automated assessment failed"],
                "reasoning": f"Release risk agent encountered an error: {exc}",
            }

        # Assemble input snapshot for audit/reproducibility
        state["release_memory_context"] = decision.get("memory_context")
        input_snapshot = await self._assemble_input_snapshot(state)
        decision["input_snapshot"] = input_snapshot
        await self._persist_decision(test_run_id, decision, input_snapshot=input_snapshot)

        await self.mark_stage_done(
            pipeline_run_id,
            result_data={
                "recommendation": decision["recommendation"],
                "risk_score": decision["risk_score"],
                "composite_risk": decision.get("composite_risk", decision["risk_score"]),
            },
        )
        await self.broadcast_progress(project_id, {
            "status": "completed",
            "recommendation": decision["recommendation"],
            "risk_score": decision["risk_score"],
        })

        consistency_report = check_release_consistency(decision)
        log_consistency_failures(consistency_report, pipeline_run_id=pipeline_run_id)

        structured_evidence = [
            EvidenceRef(
                source="score_model",
                ref_id=str(decision.get("score_model_version", SCORE_MODEL_VERSION) or "v1"),
                strength="strong",
                contribution=max(0, min(100, int(100 - decision.get("risk_score", 50)))),
            ),
        ]
        if consistency_report is not None:
            structured_evidence.append(EvidenceRef(
                source="consistency_report",
                ref_id=consistency_report.agent,
                strength="weak",
                contribution=100 if consistency_report.all_passed else 0,
            ))

        return validate_agent_contract(
            ReleaseRiskAgentOutput,
            {"release_decision": decision},
            agent_name=self.stage_name,
            structured_evidence=structured_evidence,
            decision_reason=(
                f"Deterministic release score produced {decision['recommendation']} "
                f"at risk {decision['risk_score']}"
                + consistency_report.decision_suffix()
            ),
        )

    # ── Evaluation orchestrator ───────────────────────────────────────────────

    async def _evaluate(self, state: dict) -> dict:
        analyses = state.get("analyses", {})
        anomalies = state.get("anomalies", [])
        pass_rate = state.get("pass_rate", 0.0)
        is_regression = state.get("is_regression", False)
        regression_tests = state.get("regression_tests", [])
        failure_clusters = state.get("failure_clusters", [])
        executive_summary = state.get("executive_summary", "")

        release_memory_context = await self._load_release_memory_context(state["project_id"])
        open_defects = int(release_memory_context.get("open_defects") or 0)

        # ── Step 1: deterministic dimension scoring ───────────────────────────
        dim_scores = compute_dimension_scores(
            analyses=analyses,
            anomalies=anomalies,
            pass_rate=pass_rate,
            is_regression=is_regression,
            regression_tests=regression_tests,
            failure_clusters=failure_clusters,
            open_defects=open_defects,
        )

        composite = compute_composite(dim_scores)

        # ── Policy-based evaluation (ENT-02) ─────────────────────────────
        # Resolve the effective policy for this project and evaluate all rules.
        # If no policy is configured, falls back to hardcoded thresholds.
        policy_result = None
        try:
            from app.services.policy_evaluator_service import evaluate_policy
            async with AsyncSessionLocal() as policy_db:
                policy_result = await evaluate_policy(
                    project_id=state["project_id"],
                    dim_scores=dim_scores,
                    pass_rate=pass_rate,
                    context={
                        "flaky_count": sum(1 for a in analyses.values() if a.get("is_flaky")),
                        "open_defects": open_defects,
                        "open_defects_source": release_memory_context.get("source"),
                        "regression_test_count": len(regression_tests),
                        # Derived failure-kind breakdown (US-9.3) — consumed
                        # only by opt-in kind_rules policies; inert otherwise.
                        "failure_kind_counts": self._failure_kind_counts(analyses),
                    },
                    db=policy_db,
                )
                composite = policy_result.effective_composite
                recommendation = policy_result.recommendation
        except Exception as exc:
            logger.warning("policy_evaluation_failed_using_defaults", error=str(exc))
            policy_result = None

        if policy_result is None:
            # Fallback: use hardcoded thresholds
            threshold = settings.RELEASE_PASS_RATE_THRESHOLD
            if pass_rate < threshold * 0.7:
                composite = max(composite, 60.0)
            recommendation = score_to_recommendation(composite, pass_rate, threshold)

        # ── Step 2: LLM reasoning (non-blocking — failures gracefully degrade) ─
        # Cost optimization: skip LLM for extreme scores (saves ~10K tokens/day)
        if composite < _EXTREME_GO_THRESHOLD:
            llm_extras = {
                "reasoning": (
                    f"Composite risk score {composite:.0f}/100 — all dimensions in the green zone. "
                    f"Pass rate {pass_rate:.1f}% is well above threshold. No LLM reasoning required."
                ),
                "blocking_issues": [],
                "conditions_for_go": [],
            }
        elif composite >= _EXTREME_NO_GO_THRESHOLD:
            blocking = self._deterministic_blocking_issues(dim_scores)
            llm_extras = {
                "reasoning": (
                    f"Composite risk score {composite:.0f}/100 — critical risk across multiple dimensions. "
                    f"Pass rate {pass_rate:.1f}%. Release is strongly blocked."
                ),
                "blocking_issues": blocking,
                "conditions_for_go": [],
            }
        else:
            llm_extras = await self._get_llm_reasoning(
                dim_scores, composite, recommendation, executive_summary, analyses
            )

        result = {
            "recommendation": recommendation,
            "risk_score": int(composite),
            "composite_risk": composite,
            "dimension_scores": dim_scores,
            "blocking_issues": llm_extras.get("blocking_issues", []),
            "conditions_for_go": llm_extras.get("conditions_for_go", []),
            "reasoning": llm_extras.get("reasoning", f"Composite risk {composite:.0f}/100."),
            "score_model_version": SCORE_MODEL_VERSION,
            "memory_context": release_memory_context,
        }

        # Attach policy evaluation for persistence (ENT-02)
        if policy_result is not None:
            result["policy_id"] = policy_result.policy_id
            result["policy_evaluation"] = policy_result.to_dict()
            # Add policy-triggered blocking issues to the list
            for ev in policy_result.rule_evaluations:
                if not ev.passed and ev.action == "BLOCK":
                    result["blocking_issues"].append(f"[Policy] {ev.message}")
                elif not ev.passed and ev.action == "WARN":
                    result["conditions_for_go"].append(f"[Policy] {ev.message}")
            # Kind-budget downgrade counterfactual (US-9.3) — a downgraded
            # NO_GO is conditional on the excused failures really being
            # infra/test-code noise, so surface it as a condition for go.
            # Only when the downgrade actually survived (a failing BLOCK
            # rule can restore NO_GO, in which case the counterfactual
            # lives in the policy_evaluation trail instead).
            if policy_result.kind_rule_applied and policy_result.kind_counterfactual:
                result["conditions_for_go"].append(
                    f"[Policy] {policy_result.kind_counterfactual}"
                )

        return result

    @staticmethod
    def _failure_kind_counts(analyses: dict) -> dict[str, int]:
        """Bucket analyzed failures into the derived kind triad (US-9.3).

        Uses the canonical mapping in ``app/services/failure_kind.py`` over
        each analysis's classifier verdict. Analyses that errored (or carry
        no category) resolve to ``unknown`` — the kind-aware gate counts
        unknown as product, so classification gaps can never soften a
        verdict. Execution status isn't carried on the analysis dicts, so
        the BROKEN nudge does not apply here (also conservative: those rows
        stay unknown → product).
        """
        from app.services.failure_kind import FAILURE_KINDS, failure_kind

        counts: dict[str, int] = {kind: 0 for kind in FAILURE_KINDS}
        for analysis in analyses.values():
            if not isinstance(analysis, dict):
                counts["unknown"] += 1
                continue
            kind = failure_kind(analysis.get("failure_category"), analysis.get("status"))
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    # ── LLM reasoning (Step 2) ────────────────────────────────────────────────

    async def _get_llm_reasoning(
        self,
        dim_scores: dict,
        composite: float,
        recommendation: str,
        executive_summary: str,
        analyses: dict,
    ) -> dict:
        failure_lines = []
        for tc_id, a in list(analyses.items())[:10]:
            cat = a.get("failure_category", "UNKNOWN")
            conf = a.get("confidence_score", 0)
            summary = a.get("root_cause_summary", "")[:120]
            failure_lines.append(f"- [{cat}] conf={conf}% {summary}")

        prompt = redact_text(_REASONING_PROMPT.format(
            scores_json=json.dumps(dim_scores, indent=2),
            composite=composite,
            recommendation=recommendation,
            summary=executive_summary or f"Composite risk: {composite:.0f}/100",
            failures="\n".join(failure_lines) if failure_lines else "No failures",
        ))

        try:
            llm = await get_llm(temperature=0.0)
            response = await asyncio.wait_for(
                llm.ainvoke(prompt),
                timeout=_LLM_REASONING_TIMEOUT,
            )
            _raw = response.content if hasattr(response, "content") else str(response)
            content = str(_raw).strip()

            parsed, error = parse_llm_json(
                content,
                expected_keys=["reasoning", "blocking_issues", "conditions_for_go"],
                context="release_risk_reasoning",
            )
            if not error:
                validated = validate_llm_output(
                    ReleaseReasoning, parsed, context="release_risk_reasoning",
                )
                return validated
            logger.warning("LLM reasoning parse issue", reason=error)
        except asyncio.TimeoutError:
            logger.warning("LLM reasoning timed out", timeout=_LLM_REASONING_TIMEOUT)
        except Exception as exc:
            logger.warning("LLM reasoning step failed (non-blocking)", error=str(exc))

        # Deterministic fallback
        blocking = self._deterministic_blocking_issues(dim_scores)
        return {
            "reasoning": (
                f"Composite risk score {composite:.0f}/100 (weights: user_impact=25%, "
                f"regression=20%, blast_radius=15%, reproducibility=15%). "
                f"Recommendation derived deterministically — LLM reasoning unavailable."
            ),
            "blocking_issues": blocking,
            "conditions_for_go": ["Resolve product bugs and rerun failing suites"] if blocking else [],
        }

    @staticmethod
    def _deterministic_blocking_issues(dim_scores: dict) -> list[str]:
        """Generate blocking issues from dimension scores without LLM."""
        blocking: list[str] = []
        if dim_scores.get("user_impact", 0) > 50:
            blocking.append(f"High user impact score ({dim_scores['user_impact']:.0f}/100) — product bugs detected")
        if dim_scores.get("regression_likely", 0) > 40:
            blocking.append(f"Regression risk elevated ({dim_scores['regression_likely']:.0f}/100)")
        if dim_scores.get("blast_radius", 0) > 50:
            blocking.append(f"Wide blast radius ({dim_scores['blast_radius']:.0f}/100) — failures span multiple clusters")
        return blocking

    # ── Input snapshot ──────────────────────────────────────────────────────

    @staticmethod
    async def _assemble_input_snapshot(state: dict) -> dict:
        """Build a JSON-serializable snapshot of all inputs used for the decision."""
        from datetime import datetime, timezone as tz
        clusters = state.get("failure_clusters", [])
        return {
            "assembled_at": datetime.now(tz.utc).isoformat(),
            "score_model_version": SCORE_MODEL_VERSION,
            "memory_context": state.get("release_memory_context"),
            "pass_rate": state.get("pass_rate", 0.0),
            "total_tests": state.get("total_tests", 0),
            "is_regression": state.get("is_regression", False),
            "regression_test_count": len(state.get("regression_tests", [])),
            "analysis_count": len(state.get("analyses", {})),
            "anomaly_count": len(state.get("anomalies", [])),
            "cluster_count": len(clusters),
            "cluster_summary": [
                {
                    "label": c.get("label", ""),
                    "size": c.get("size", 0),
                    "regression_classification": c.get("regression_classification"),
                }
                for c in clusters[:20]
            ],
            "flaky_finding_count": len(state.get("flaky_findings", [])),
            "test_health_finding_count": len(state.get("test_health_findings", [])),
            # US-9.3 — frozen kind breakdown so the decision is reproducible
            # and the policy simulator can replay kind-aware policies.
            "failure_kind_counts": ReleaseRiskAgent._failure_kind_counts(
                state.get("analyses", {}) or {}
            ),
        }

    # ── DB helpers ────────────────────────────────────────────────────────────

    @staticmethod
    async def _load_release_memory_context(project_id: str) -> dict:
        async with AsyncSessionLocal() as db:
            try:
                from app.services.agent_memory_service import load_release_risk_memory_context

                memory_context = await load_release_risk_memory_context(
                    db,
                    uuid.UUID(str(project_id)),
                )
                if (
                    memory_context.get("memory_entry_count")
                    or memory_context.get("open_defects")
                ):
                    return memory_context
            except Exception as exc:
                logger.debug("release_risk_memory_context_unavailable", error=str(exc))

            open_defects = await ReleaseRiskAgent._count_open_defects_from_db(db, project_id)
            return {
                "schema_version": 1,
                "memory_layer_version": "agent_memory.consumer_context:v1",
                "source": "defect_table_fallback",
                "open_defects": open_defects,
                "memory_entry_count": 0,
                "memory_references": [],
                "retrieval_audit": {
                    "consumer": "release_risk",
                    "retrieval_strategy": "defect_table_fallback",
                    "project_id": str(project_id),
                },
            }

    @staticmethod
    async def _count_open_defects(project_id: str) -> int:
        context = await ReleaseRiskAgent._load_release_memory_context(project_id)
        return int(context.get("open_defects") or 0)

    @staticmethod
    async def _count_open_defects_from_db(db, project_id: str) -> int:
        from sqlalchemy import func as sa_func, select
        result = await db.execute(
            select(sa_func.count(Defect.id))
            .where(Defect.project_id == uuid.UUID(str(project_id)))
            .where(Defect.resolution_status == "OPEN")
        )
        return int(result.scalar() or 0)

    async def _persist_decision(self, test_run_id: str, decision: dict, input_snapshot: dict | None = None) -> None:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            existing = await db.execute(
                select(ReleaseDecision).where(ReleaseDecision.test_run_id == test_run_id)
            )
            record = existing.scalar_one_or_none()
            if record:
                record.recommendation = decision["recommendation"]
                record.risk_score = decision["risk_score"]
                record.blocking_issues = decision.get("blocking_issues", [])
                record.conditions_for_go = decision.get("conditions_for_go", [])
                record.reasoning = decision.get("reasoning", "")
                record.dimension_scores = decision.get("dimension_scores")
                record.composite_risk = decision.get("composite_risk")
                record.score_model_version = decision.get("score_model_version")
                record.policy_id = decision.get("policy_id")
                record.policy_evaluation = decision.get("policy_evaluation")
                if input_snapshot:
                    record.input_snapshot = input_snapshot
            else:
                db.add(ReleaseDecision(
                    test_run_id=test_run_id,
                    recommendation=decision["recommendation"],
                    risk_score=decision["risk_score"],
                    blocking_issues=decision.get("blocking_issues", []),
                    conditions_for_go=decision.get("conditions_for_go", []),
                    reasoning=decision.get("reasoning", ""),
                    dimension_scores=decision.get("dimension_scores"),
                    composite_risk=decision.get("composite_risk"),
                    score_model_version=decision.get("score_model_version"),
                    policy_id=decision.get("policy_id"),
                    policy_evaluation=decision.get("policy_evaluation"),
                    input_snapshot=input_snapshot,
                ))
            await db.commit()
