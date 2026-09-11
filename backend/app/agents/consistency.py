"""
Pure-local self-critique / verification layer (AIQ-P2).

This module is ADDITIVE and side-effect-free with respect to agent outputs.
It runs cheap, local consistency checks over already-assembled agent results
and surfaces any failures in two ways only:

  * a structured ``consistency_check_failed`` warning emitted via structlog, and
  * an extra ``decision_reason`` suffix + evidence ref folded into the existing
    ``agent_contracts`` metadata.

It NEVER performs outbound calls, NEVER writes to a database, NEVER mutates the
underlying analysis, and NEVER raises. Check functions are defensive: missing
or empty data is treated as "nothing to evaluate" (``passed=True``). Reports
carry ONLY structural tokens — test ids, check names, release tokens, numeric
scores — never raw error or summary text.
"""
from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = structlog.get_logger("agents.consistency")

_VALID_SEVERITIES = {"warning", "error"}


def _as_list(value) -> list:
    """Coerce a value to a list, returning [] for any non-collection input.

    ``x or []`` only guards falsy values; a truthy non-list (an int, a bool)
    would still reach ``set(...)`` / ``len(...)`` / iteration and raise. This
    helper makes the list-typed-field guard absolute: no check function may
    raise on ANY input shape.
    """
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []

# Release recommendation score bands (see ReleaseRiskAgent docstring):
#   risk_score < 20            → GO
#   20 <= risk_score < 55      → CONDITIONAL_GO
#   risk_score >= 55           → NO_GO
_RELEASE_GO_MAX = 20
_RELEASE_NO_GO_MIN = 55

_LOW_RISK_PHRASES = (
    "low risk",
    "minimal risk",
    "safe to release",
    "green zone",
    "no significant risk",
)
_HIGH_RISK_PHRASES = (
    "high risk",
    "critical risk",
    "strongly blocked",
    "do not release",
)

# Negators that, when they immediately precede a risk phrase, invert its sense
# (e.g. "not low risk" is NOT a low-risk claim). Kept deterministic — no NLP.
_NEGATORS = ("not", "no", "n't", "isn't", "aren't", "never", "wasn't", "weren't")
# Window (chars) before a phrase within which a negator flips its meaning.
_NEGATION_WINDOW = 6


def _phrase_present_unnegated(text: str, phrases) -> bool:
    """True if any phrase appears in ``text`` NOT immediately negated.

    A phrase is considered negated when one of ``_NEGATORS`` ends within
    ``_NEGATION_WINDOW`` characters before the phrase start (one token of
    slack). This keeps correct high-risk prose like "is not low risk" from
    tripping a low-risk contradiction.
    """
    for phrase in phrases:
        start = 0
        while True:
            idx = text.find(phrase, start)
            if idx == -1:
                break
            preceding = text[max(0, idx - _NEGATION_WINDOW):idx]
            preceding_stripped = preceding.rstrip(" \t")
            negated = any(
                preceding_stripped.endswith(neg) for neg in _NEGATORS
            )
            if not negated:
                return True
            start = idx + len(phrase)
    return False


class ConsistencyCheck(BaseModel):
    """A single named consistency verification result."""

    model_config = ConfigDict(extra="ignore")

    name: str
    passed: bool
    severity: str = "warning"
    details: str = ""
    offending_refs: list[str] = Field(default_factory=list)

    @field_validator("severity")
    @classmethod
    def _clamp_severity(cls, value: str) -> str:
        return value if value in _VALID_SEVERITIES else "warning"


class ConsistencyReport(BaseModel):
    """A collection of consistency checks for one agent's output."""

    model_config = ConfigDict(extra="ignore")

    agent: str
    checks: list[ConsistencyCheck] = Field(default_factory=list)

    @property
    def failed(self) -> list[ConsistencyCheck]:
        return [chk for chk in self.checks if not chk.passed]

    @property
    def all_passed(self) -> bool:
        return not self.failed

    def decision_suffix(self) -> str:
        if self.all_passed:
            return "; consistency_ok"
        return "; consistency_check_failed:" + ",".join(chk.name for chk in self.failed)

    def evidence_ref(self) -> dict:
        return {
            "type": "consistency_report",
            "passed": self.all_passed,
            "failed_checks": [chk.name for chk in self.failed],
        }


def log_consistency_failures(
    report: ConsistencyReport,
    *,
    pipeline_run_id: str | None = None,
) -> None:
    """Emit one ``consistency_check_failed`` warning per failed check."""
    for chk in report.failed:
        logger.warning(
            "consistency_check_failed",
            agent=report.agent,
            check=chk.name,
            severity=chk.severity,
            details=chk.details,
            offending_refs=chk.offending_refs[:20],
            pipeline_run_id=pipeline_run_id,
        )


# ── SummaryAgent ──────────────────────────────────────────────────────────────


def check_summary_consistency(
    structured,
    run_data,
    failed_test_ids,
    analyses,
) -> ConsistencyReport:
    """Verify the structured summary is internally and cross-layer coherent."""
    checks: list[ConsistencyCheck] = []
    structured = structured if isinstance(structured, dict) else {}
    run_data = run_data if isinstance(run_data, dict) else {}
    analyses = analyses if isinstance(analyses, dict) else {}

    # Build the valid-id universe from STRINGIFIED ids so membership tests are
    # consistent regardless of whether ids arrive as int or str (M1). Coerce
    # failed_test_ids defensively — a truthy non-list must not crash (B1).
    universe = {str(tid) for tid in _as_list(failed_test_ids)} | {
        str(k) for k in analyses.keys()
    }

    layer3 = structured.get("layer3_evidence_pack")
    layer3 = layer3 if isinstance(layer3, dict) else {}
    layer2 = structured.get("layer2_incident_view")
    layer2 = layer2 if isinstance(layer2, dict) else {}
    layer4 = structured.get("layer4_action_plan")
    layer4 = layer4 if isinstance(layer4, dict) else {}

    # 1. referential_integrity_flaky_ids
    flaky_ids = _as_list(layer3.get("flaky_test_ids"))
    if universe:
        missing_flaky = [str(tid) for tid in flaky_ids if str(tid) not in universe]
    else:
        # Empty universe (no failed ids, no analyses) but flaky ids cited ⇒ the
        # summary fabricated ids with zero analysed tests (Mn1). That IS a
        # violation: every cited id is unverifiable.
        missing_flaky = [str(tid) for tid in flaky_ids]
    checks.append(ConsistencyCheck(
        name="referential_integrity_flaky_ids",
        passed=not missing_flaky,
        severity="error",
        details=f"{len(missing_flaky)} flaky id(s) absent from analysed universe",
        offending_refs=missing_flaky,
    ))

    # 2. referential_integrity_citations
    citations = _as_list(layer3.get("citations"))
    citation_offenders: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        cid = citation.get("test_id")
        if cid is None:
            continue
        # Empty universe ⇒ any cited id is unverifiable (Mn1); non-empty
        # universe ⇒ flag only ids absent from it.
        if not universe or str(cid) not in universe:
            citation_offenders.append(str(cid))
    checks.append(ConsistencyCheck(
        name="referential_integrity_citations",
        passed=not citation_offenders,
        severity="error",
        details=f"{len(citation_offenders)} citation id(s) absent from analysed universe",
        offending_refs=citation_offenders,
    ))

    # 3. cross_layer_release_signal
    failed = run_data.get("failed_tests")
    pass_rate = run_data.get("pass_rate")
    release_impact = layer2.get("release_impact")
    signal_ok = True
    signal_detail = ""
    if failed == 0 and release_impact not in (None, "GO"):
        signal_ok = False
        signal_detail = f"failed=0 but release_impact={release_impact}"
    elif pass_rate is not None and pass_rate < 75 and release_impact == "GO":
        signal_ok = False
        signal_detail = f"pass_rate={pass_rate} but release_impact=GO"
    checks.append(ConsistencyCheck(
        name="cross_layer_release_signal",
        passed=signal_ok,
        severity="warning",
        details=signal_detail,
    ))

    # 4. cross_layer_criticality_coherence
    crit = layer2.get("criticality")
    crit_ok = not (crit in ("CRITICAL", "HIGH") and release_impact == "GO")
    checks.append(ConsistencyCheck(
        name="cross_layer_criticality_coherence",
        passed=crit_ok,
        severity="warning",
        details=(
            f"criticality={crit} but release_impact=GO" if not crit_ok else ""
        ),
    ))

    # 5. evidence_vs_action_plan
    fix_recommendations = _as_list(layer4.get("fix_recommendations"))
    # Evidence-pack fields holding stack traces / anomalies (see summary_agent.py).
    stack_traces = _as_list(layer3.get("top_stack_traces"))
    log_anomalies = _as_list(layer3.get("log_anomalies"))
    # Only fire when we can positively confirm zero evidence AND zero failures
    # AND a fix plan exists. When uncertain, pass.
    has_evidence_pack = isinstance(structured.get("layer3_evidence_pack"), dict)
    zero_evidence = has_evidence_pack and not stack_traces and not log_anomalies
    action_incoherent = bool(fix_recommendations) and failed == 0 and zero_evidence
    checks.append(ConsistencyCheck(
        name="evidence_vs_action_plan",
        passed=not action_incoherent,
        severity="warning",
        details=(
            "fix_recommendations present with failed=0 and no evidence"
            if action_incoherent else ""
        ),
    ))

    return ConsistencyReport(agent="summary", checks=checks)


# ── ReleaseRiskAgent ──────────────────────────────────────────────────────────


def _release_band(score) -> str | None:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value < _RELEASE_GO_MAX:
        return "GO"
    if value < _RELEASE_NO_GO_MIN:
        return "CONDITIONAL_GO"
    return "NO_GO"


def check_release_consistency(decision) -> ConsistencyReport:
    """Verify the release decision's narrative and recommendation match its score."""
    checks: list[ConsistencyCheck] = []
    decision = decision if isinstance(decision, dict) else {}

    recommendation = decision.get("recommendation")
    risk_score = decision.get("risk_score")
    if risk_score is None:
        risk_score = decision.get("composite_risk")
    reasoning = str(decision.get("reasoning") or "").lower()
    blocking_issues = _as_list(decision.get("blocking_issues"))
    policy_id = decision.get("policy_id")

    band = _release_band(risk_score)

    # 1. narrative_vs_score_band
    narrative_ok = True
    narrative_detail = ""
    try:
        score_value = float(risk_score) if risk_score is not None else None
    except (TypeError, ValueError):
        score_value = None
    if score_value is not None:
        if score_value >= _RELEASE_NO_GO_MIN and _phrase_present_unnegated(
            reasoning, _LOW_RISK_PHRASES
        ):
            narrative_ok = False
            narrative_detail = f"low-risk narrative with risk_score={int(score_value)}"
        elif score_value < _RELEASE_GO_MAX and _phrase_present_unnegated(
            reasoning, _HIGH_RISK_PHRASES
        ):
            narrative_ok = False
            narrative_detail = f"high-risk narrative with risk_score={int(score_value)}"
    checks.append(ConsistencyCheck(
        name="narrative_vs_score_band",
        passed=narrative_ok,
        severity="error",
        details=narrative_detail,
    ))

    # 2. recommendation_vs_score
    #
    # Re-audit N34: this used to re-derive the verdict from the score alone
    # (``_release_band``), while the gate derives it from the score AND the
    # pass rate AND the policy (hard pass-rate floor -> NO_GO, pass rate under
    # the minimum -> CONDITIONAL_GO, BLOCK/WARN rules, kind-budget downgrade).
    # Two modules, one rule: a third of all homelab decisions were flagged,
    # every one of them the gate's own correct answer. When the decision
    # carries its policy evaluation and pass rate, the check now REPLAYS the
    # gate's mapping -- the same functions -- and a mismatch is a real
    # inconsistency (an error). Older decisions without them keep the
    # score-band comparison below.
    replayed = _replayed_recommendation(decision)
    if replayed is not None and recommendation is not None:
        replay_ok = recommendation == replayed
        checks.append(ConsistencyCheck(
            name="recommendation_vs_score",
            passed=replay_ok,
            severity="error",
            details="" if replay_ok else (
                f"recommendation={recommendation} but the gate's mapping of its own "
                f"inputs gives {replayed}"
            ),
        ))
        return _finish_release_checks(checks, recommendation, blocking_issues)

    rec_ok = True
    rec_detail = ""
    # Conservatism ordering: GO (least) < CONDITIONAL_GO < NO_GO (most).
    _conservatism = {"GO": 0, "CONDITIONAL_GO": 1, "NO_GO": 2}
    conservative_override = False
    if band is not None and recommendation is not None:
        rec_ok = recommendation == band
        if not rec_ok:
            rec_detail = f"recommendation={recommendation} but score band={band}"
            rec_rank = _conservatism.get(recommendation)
            band_rank = _conservatism.get(band)
            if (
                rec_rank is not None
                and band_rank is not None
                and rec_rank > band_rank
            ):
                # Recommendation is MORE conservative than the score (e.g. NO_GO
                # at a low score). This is the safe direction and can be driven
                # by a pass-rate hard floor the checker cannot see (M3) — a
                # warning, not an error. The reverse (a LESS conservative rec
                # like GO at a high score) stays error.
                conservative_override = True
    # policy_id downgrade takes precedence (kept intact); otherwise a
    # conservative override also downgrades error → warning.
    if policy_id:
        rec_severity = "warning"
    elif conservative_override:
        rec_severity = "warning"
    else:
        rec_severity = "error"
    if policy_id and not rec_ok:
        rec_detail = (rec_detail + "; policy_override_present").strip("; ")
    elif conservative_override:
        rec_detail = (rec_detail + "; conservative_override").strip("; ")
    checks.append(ConsistencyCheck(
        name="recommendation_vs_score",
        passed=rec_ok,
        severity=rec_severity,
        details=rec_detail,
    ))

    return _finish_release_checks(checks, recommendation, blocking_issues)


def _finish_release_checks(checks: list, recommendation, blocking_issues: list) -> ConsistencyReport:
    """Check 3 and the report, shared by the replay and the score-band paths."""
    # 3. blocking_issues_vs_recommendation
    blocking_ok = not (recommendation == "GO" and bool(blocking_issues))
    checks.append(ConsistencyCheck(
        name="blocking_issues_vs_recommendation",
        passed=blocking_ok,
        severity="warning",
        details=(
            f"recommendation=GO with {len(blocking_issues)} blocking issue(s)"
            if not blocking_ok else ""
        ),
    ))

    return ConsistencyReport(agent="release_risk", checks=checks)


def _replayed_recommendation(decision: dict) -> str | None:
    """The verdict the release gate's own mapping gives for this decision.

    Replays ``criticality_service.score_to_recommendation`` with the recorded
    effective composite, pass rate and policy thresholds, then the recorded
    kind-budget downgrade and ``policy_evaluator_service.escalate_recommendation``
    over the recorded rule evaluations -- the functions the gate itself ran,
    not a copy of their thresholds (re-audit N34). ``None`` when the decision
    does not carry those inputs. Never raises.
    """
    evaluation = decision.get("policy_evaluation")
    if not isinstance(evaluation, dict):
        return None
    thresholds = evaluation.get("effective_thresholds")
    composite = evaluation.get("effective_composite")
    pass_rate = decision.get("pass_rate")
    if pass_rate is None:
        snapshot = decision.get("input_snapshot")
        pass_rate = snapshot.get("pass_rate") if isinstance(snapshot, dict) else None
    if not isinstance(thresholds, dict) or composite is None or pass_rate is None:
        return None
    try:
        from app.services.criticality_service import score_to_recommendation
        from app.services.policy_evaluator_service import escalate_recommendation

        recommendation = score_to_recommendation(
            composite=float(composite),
            pass_rate=float(pass_rate),
            threshold=float(thresholds["pass_rate_minimum"]),
            go_threshold=float(thresholds["go_threshold"]),
            no_go_threshold=float(thresholds["no_go_threshold"]),
            hard_floor_factor=float(thresholds["pass_rate_hard_floor_factor"]),
        )
        # The kind-budget downgrade is applied to the base verdict before rule
        # escalation, and is recorded as applied only when it survived.
        if evaluation.get("kind_rule_applied") and recommendation == "NO_GO":
            recommendation = "CONDITIONAL_GO"
        recommendation, _overall = escalate_recommendation(
            recommendation, evaluation.get("rule_evaluations")
        )
        return recommendation
    except Exception:  # noqa: BLE001 -- a check must never raise (module contract)
        return None


# ── AnalysisAgent ─────────────────────────────────────────────────────────────


def check_analysis_consistency(analyses, failed_test_ids) -> ConsistencyReport:
    """Verify the analysis set covers every failed test and flaky flags are sane."""
    checks: list[ConsistencyCheck] = []
    analyses = analyses if isinstance(analyses, dict) else {}
    # Stringify analyses keys so membership tests are key-type agnostic: an
    # int-keyed analyses dict must still match a stringified failed id (M1).
    analysis_keys = {str(k) for k in analyses}

    # coverage_completeness
    missing = [
        str(tid) for tid in _as_list(failed_test_ids) if str(tid) not in analysis_keys
    ]
    checks.append(ConsistencyCheck(
        name="coverage_completeness",
        passed=not missing,
        severity="warning",
        details=f"{len(missing)} failed id(s) without an analysis entry",
        offending_refs=missing,
    ))

    # flaky_confidence_floor
    flaky_floor_offenders: list[str] = []
    for tc_id, analysis in analyses.items():
        if not isinstance(analysis, dict):
            continue
        if (
            analysis.get("is_flaky") is True
            and analysis.get("confidence_score") == 0
            and not analysis.get("error")
        ):
            flaky_floor_offenders.append(str(tc_id))
    checks.append(ConsistencyCheck(
        name="flaky_confidence_floor",
        passed=not flaky_floor_offenders,
        severity="warning",
        details=f"{len(flaky_floor_offenders)} flaky analysis(es) at confidence 0",
        offending_refs=flaky_floor_offenders,
    ))

    return ConsistencyReport(agent="root_cause_analysis", checks=checks)
