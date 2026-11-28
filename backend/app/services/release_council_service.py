"""
Release Council Service — deterministic context assembly for release decisions.

Gathers cluster criticality, regression classifications, baseline diff, and open
defects grouped by component into a single input_snapshot.  The snapshot is
persisted with the ReleaseDecision for full traceability: every release
recommendation can be reproduced from its recorded inputs.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from app.core.metrics import release_overrides_total

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import (
    Defect,
    FailureCluster,
    LaunchStatus,
    ReleaseDecision,
    TestRun,
)
from app.models.schemas import (
    BaselineDiff,
    ClusterInsightResponse,
    DimensionScore,
    OverrideAuditEntry,
    ReleaseCouncilResponse,
    RuleEvaluationResponse,
)
from app.services.criticality_service import (
    HARD_FLOOR_FACTOR as _HARD_FLOOR_FACTOR,
    SCORE_MODEL_VERSION,
    compute_dimension_scores,
    score_cluster,
)
from app.core.config import settings

from app.models.constants import DIMENSION_METADATA
from app.services.cluster_snapshot import is_current_snapshot
# Live = OPEN or IN_PROGRESS (W06 DEF-01): an in-progress defect still counts as open.
from app.services.defect_status import ACTIVE_STATUSES

logger = logging.getLogger("services.release_council")


# Worst → best so worse_of returns the leftmost in a sorted pair.
#
# Two vocabularies converge here: the recommendation vocabulary produced by
# ``criticality_service.score_to_recommendation`` (and persisted on
# ``ReleaseDecision.recommendation``) is ``CONDITIONAL_GO``, while
# ``metrics_service.classify_with_policy`` emits ``CONDITIONAL`` for the band
# verdict. Both must rank identically — otherwise an unrecognised
# ``CONDITIONAL_GO`` falls through ``_worse_verdict``'s "unknown → return the
# other" branch and a green band SOFTENS the composite to GO (fail-OPEN). Rank
# both spellings the same.
_VERDICT_RANK = {"NO_GO": 0, "CONDITIONAL": 1, "CONDITIONAL_GO": 1, "GO": 2}


def _normalize_verdict(verdict: str) -> str:
    """Map the band vocabulary (``CONDITIONAL``) onto the canonical
    recommendation vocabulary (``CONDITIONAL_GO``) so the value returned to the
    API/UI/override endpoint is always one of GO / CONDITIONAL_GO / NO_GO."""
    return "CONDITIONAL_GO" if verdict == "CONDITIONAL" else verdict


def _worse_verdict(a: str, b: str) -> str:
    """Return whichever recommendation is stricter (NO_GO > CONDITIONAL > GO).

    Unknown verdicts fall through to the input value — never softens.
    """
    if a not in _VERDICT_RANK:
        return b
    if b not in _VERDICT_RANK:
        return a
    return a if _VERDICT_RANK[a] <= _VERDICT_RANK[b] else b


async def _apply_band_floor(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    recommendation: str,
    pass_rate: float,
) -> tuple[str, Optional[str], list[str], Optional[tuple[str, int, str]]]:
    """Layer the project's ``PolicyPassRateBands`` over the composite verdict.

    "Fail-closed" semantics: if the band-derived verdict is *stricter* than
    the composite, the recommendation is downgraded. The composite is never
    softened — bands can only block, never unblock. This keeps /overview and
    /release-gate in lockstep: when /overview shows red (band=red), the gate
    cannot say GO even if the composite was below the NO_GO threshold.

    The band classifier mirrors what ``metrics_service.classify_with_policy``
    uses for /overview, so both pages produce the same colour for the same
    project + run. Returns
    ``(final_recommendation, band, downgrades, band_policy)`` where
    ``band_policy`` is ``(policy_id, version, level)`` for the policy that
    produced the band, or ``None``. ``band`` is ``None`` and ``downgrades`` is
    empty when no policy is active.

    The identity is returned because this function can CHANGE the verdict. It
    did so on a live deployment — a published policy moved ``GO`` to
    ``CONDITIONAL_GO`` — while the response still reported
    ``policy_level: "hardcoded"`` with a null ``policy_id``, because those
    fields describe the STORED decision and this floor is applied at read time
    (TL-2026-09-19-01-008). A caller that lets a policy move a ship decision
    has to be able to name the policy.

    The open-CRITICAL defect count for the ``max_p0_defects`` hard cap is
    resolved here via the shared ``count_open_critical_defects`` helper rather
    than trusting a caller-supplied number — the synth and deep call sites
    previously passed an all-severities total and a dead ``severity == "P0"``
    zero respectively, so the cap either over-counted or never fired.
    """
    final, band, downgrades, band_policy, _bands = await _band_floor_detail(
        db, project_id, recommendation, pass_rate,
    )
    return final, band, downgrades, band_policy


async def _band_floor_detail(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    recommendation: str,
    pass_rate: float,
) -> tuple[str, Optional[str], list[str], Optional[tuple[str, int, str]], Optional[dict]]:
    """``_apply_band_floor`` plus the band edges it applied (batch 8 review F1).

    The gate card states the numbers that can decide the verdict; the
    pass-rate bands are among them, and they come from the policy in force at
    read time, which a stored decision's frozen snapshot does not carry.
    """
    if project_id is None:
        return recommendation, None, [], None, None

    # Local import to keep release_council_service free of metrics-service
    # coupling at module level — _resolve_policy_for_project also depends on
    # ReleaseGatePolicy, which release_council already touches via the
    # policy_id / policy_version fields. Both services live downstream of
    # the same data, so this is safe.
    from app.services.metrics_service import _resolve_active_policy_for_project

    resolved = await _resolve_active_policy_for_project(db, str(project_id))
    if resolved is None:
        return recommendation, None, [], None, None
    policy_doc, band_policy_id, band_policy_version, band_policy_level = resolved

    bands = policy_doc.get("pass_rate_bands") or {}
    final, band, downgrades = await apply_policy_band_floor(
        db,
        project_id,
        recommendation,
        pass_rate,
        bands=bands,
        hard_caps=policy_doc.get("hard_caps") or {},
    )
    return (
        final, band, downgrades,
        (band_policy_id, band_policy_version, band_policy_level),
        _effective_bands(bands),
    )


def _effective_bands(bands: dict) -> dict:
    """The band edges ``classify_with_policy`` applies: its defaults (orange 90,
    yellow 95, green 99) for any edge the document leaves out. Under orange is
    red (No-Go); orange is Conditional; yellow and green are Go."""
    return {
        "orange_min": float(bands.get("orange_min", 90.0)),
        "yellow_min": float(bands.get("yellow_min", 95.0)),
        "green_min": float(bands.get("green_min", 99.0)),
    }


def _band_held_back(before: str, after: str, downgrades: list[str]) -> bool:
    """True when the pass-rate band itself made the verdict stricter. A hard
    cap also pins the band red, but ``band_downgrades`` already names it."""
    return not downgrades and _normalize_verdict(after) != _normalize_verdict(before)


async def apply_policy_band_floor(
    db: AsyncSession,
    project_id: Optional[uuid.UUID],
    recommendation: str,
    pass_rate: float,
    *,
    bands: dict,
    hard_caps: dict,
) -> tuple[str, Optional[str], list[str]]:
    """One policy document's pass-rate bands and hard caps over a verdict.

    Shared by the gate (with the active policy) and the policy simulator (with
    the draft), so a simulation predicts what publishing will show (D-133).
    Returns ``(final_recommendation, band, downgrades)``. A document without
    bands gets the classifier's default bands, as the gate always did.
    """
    from app.services.metrics_service import (  # noqa: PLC0415
        classify_with_policy,
        count_open_critical_defects,
    )

    # Open CRITICAL defects for the P0 hard cap. The run gate applies the
    # pass-rate bands and this cap; the flaky and new-failure caps apply to the
    # project readiness verdict on Home (D-136: the policy editor says so).
    active_p0 = await count_open_critical_defects(db, project_id) if project_id else 0
    classified = classify_with_policy(
        pass_rate=pass_rate,
        active_defects_p0=active_p0,
        flaky_count=0,
        new_failures_24h=0,
        bands=bands,
        hard_caps=hard_caps,
    )
    # Normalise the band verdict onto the recommendation vocabulary so
    # ``_worse_verdict`` compares like-with-like and the returned value stays
    # canonical (CONDITIONAL_GO, not the band's CONDITIONAL).
    band_verdict = _normalize_verdict(classified["verdict"])
    return (
        _worse_verdict(recommendation, band_verdict),
        classified["band"],
        list(classified["downgrades"]),
    )


def _build_dimension_scores(scores_dict: Optional[dict]) -> list[DimensionScore]:
    if not scores_dict:
        return []
    result = []
    for key, (label, weight) in DIMENSION_METADATA.items():
        score = float(scores_dict.get(key, 0))
        result.append(DimensionScore(
            name=key,
            label=label,
            score=round(score, 1),
            weight=weight,
            contribution=round(score * weight, 2),
        ))
    return result


async def assemble_input_snapshot(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Build a deterministic input_snapshot capturing every data source the
    release decision depends on.

    Returns a JSON-serializable dict persisted alongside the ReleaseDecision.
    """
    snapshot: dict[str, Any] = {
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "score_model_version": SCORE_MODEL_VERSION,
    }

    # ── Test run context ─────────────────────────────────────────────────────
    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if run:
        snapshot["test_run"] = {
            "id": str(run.id),
            "build_number": run.build_number,
            "pass_rate": run.pass_rate,
            "total_tests": run.total_tests,
            "passed_tests": run.passed_tests,
            "failed_tests": run.failed_tests,
            "branch": run.branch,
        }
    else:
        snapshot["test_run"] = None

    # ── Failure clusters ─────────────────────────────────────────────────────
    cluster_result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id, is_current_snapshot())
        .order_by(FailureCluster.size.desc())
    )
    clusters = cluster_result.scalars().all()
    snapshot["clusters"] = [
        {
            "cluster_id": c.cluster_id,
            "label": c.label,
            "size": c.size,
            "cohesion_score": c.cohesion_score,
            "regression_classification": c.regression_classification,
            "member_count": len(c.member_test_ids or []),
        }
        for c in clusters
    ]

    # ── Open defects by component ────────────────────────────────────────────
    project_id = run.project_id if run else None
    if project_id:
        defect_result = await db.execute(
            select(
                Defect.component,
                sa_func.count(Defect.id).label("count"),
            )
            .where(Defect.project_id == project_id)
            .where(Defect.resolution_status.in_(ACTIVE_STATUSES))
            .group_by(Defect.component)
        )
        defect_rows = defect_result.all()
        snapshot["open_defects_by_component"] = [
            {"component": row.component or "unassigned", "count": row.count}
            for row in defect_rows
        ]
    else:
        snapshot["open_defects_by_component"] = []

    return snapshot


def _quick_look_conditions(
    recommendation: str, pass_rate: float, threshold: float
) -> list[str]:
    """The conditions a quick-look verdict is actually contingent on.

    A CONDITIONAL_GO whose ``conditions_for_go`` is empty tells a release manager
    "conditional" and nothing else. ``ReleaseGatePage`` renders that block behind
    ``conditions_for_go.length > 0``, so an empty list hides the section outright
    and the page shows a conditional verdict with no conditions anywhere on it.
    The ``reasoning`` prose already names what is missing; this puts the same
    facts in the structured field the UI reads.

    Only conditions this path genuinely gates on are returned. The
    analysis-driven dimensions (regression likelihood, historical recurrence,
    env sensitivity) score 0 here because no per-test analyses exist -- NOT
    because those risks were measured and found absent -- so the first condition
    is the honest one: the verdict is provisional. Reporting an unmeasured
    dimension as a zero risk is how absence gets read as health.
    """
    if recommendation == "GO":
        return []
    conditions = [
        "Run Deep Investigation — this verdict used run aggregates only; "
        "failure clusters, regression likelihood and historical recurrence "
        "were not measured."
    ]
    if pass_rate < threshold:
        conditions.append(
            f"Raise pass rate to at least {threshold:.1f}% "
            f"(currently {pass_rate:.1f}%)."
        )
    return conditions


def _unmeasured_quick_look(
    run_id: uuid.UUID, run: TestRun, *, in_progress: bool
) -> ReleaseCouncilResponse:
    """The quick look for a run with nothing to grade yet.

    No pass rate is not a 0% pass rate. A run still in progress has none yet,
    and a finished run that reported no passed or failed tests has nothing to
    measure. Graded as 0.0, both came back NO_GO with "Raise pass rate to at
    least 90.0% (currently 0.0%)" and the NO-GO floor note, and /release-gate
    opens the project's newest run, often one still running (the UX redesign's
    browser E2E pass, 2026-10-08: a run at 10 passed, 1 failed so far).

    Nothing is graded here: no pass rate and no dimension scores, so the page
    shows its pending state ("No test evidence yet"). The value stays NO_GO,
    so a consumer gating on it still fails closed.
    """
    why = (
        "the run is still in progress, so it has no pass rate yet"
        if in_progress
        else "the run reported no passed or failed tests, so there is no pass rate to grade"
    )
    return ReleaseCouncilResponse(
        run_id=str(run_id),
        project_id=str(run.project_id) if run.project_id else None,
        recommendation="NO_GO",
        risk_score=0,
        composite_risk=None,
        dimension_scores=[],
        blocking_issues=[],
        conditions_for_go=[
            "Wait for the run to finish: it is still in progress."
            if in_progress
            else "Re-run with tests that pass or fail: this run measured none."
        ],
        reasoning=f"Not graded: {why}.",
        score_model_version=SCORE_MODEL_VERSION,
        input_snapshot={
            "synthesized": True,
            "pass_rate": None,
            "verdict_driver": "in_progress" if in_progress else "no_pass_rate",
        },
        cluster_insights=[],
        baseline_diff=None,
        open_defects_by_component=[],
        human_override=None,
        override_audit=[],
        pass_rate=None,
        build_number=run.build_number,
        policy_level="hardcoded",
        rule_evaluations=[],
        synthesized=True,
    )


async def _synthesize_release_council(
    run_id: uuid.UUID,
    db: AsyncSession,
) -> Optional[ReleaseCouncilResponse]:
    """Deterministic quick-look release decision derived from a run's
    aggregates when no ``ReleaseDecision`` row has been persisted yet.

    Why this exists: the ReleaseRiskAgent only runs as part of the
    *deep* pipeline. Runs ingested via the live-stream SDK queue
    ``run_agent_pipeline`` with ``workflow_type="offline"``, so they
    never get a ReleaseDecision row. Without this helper, every such
    run produced a 404 on /api/v1/release-readiness/{id} and the
    /release-gate page showed an "empty state — go trigger deep
    investigation" prompt for routine successful runs.

    What this is NOT:
      * A replacement for the deep pipeline. We don't have failure
        clusters, defect-by-component, override audit, or LLM
        narrative here — those still require deep investigation.
      * A persisted row. The synthesis runs at read time only; the
        next deep run still writes the real ReleaseDecision and
        supersedes this view.
      * A full policy evaluation. It goes through the same evaluator as
        the stored decision with the project's effective policy (D-142),
        but only the rules whose inputs it has are checked; the others
        are named in ``input_snapshot.rules_not_checked``. With no policy
        it uses the built-in thresholds in ``settings``.

    Returns ``None`` (caller maps to 404) only when the run itself
    doesn't exist — that's a genuine "not found" the user should see.
    """
    run = (await db.execute(
        select(TestRun).where(TestRun.id == run_id)
    )).scalar_one_or_none()
    if run is None:
        return None

    status = getattr(run, "status", None)
    in_progress = str(getattr(status, "value", status) or "").upper() == LaunchStatus.IN_PROGRESS.value
    if in_progress or run.pass_rate is None:
        return _unmeasured_quick_look(run_id, run, in_progress=in_progress)

    # The run's unquarantined real failures and the project's open defects by
    # severity: the same inputs the stored decision uses (D-04).
    from app.services.run_release_signals import load_run_release_signals

    signals = await load_run_release_signals(db, run_id, run.project_id)
    open_defects = int(signals["open_defects"])

    pass_rate = float(run.pass_rate)

    # D-142: the project's effective policy, applied whole. The quick look used
    # to score with the built-in thresholds (``policy_level="hardcoded"``) and
    # then layer the published policy's bands and P0 cap on top, so a policy
    # was half applied and the footer named both. One resolution now feeds the
    # policy evaluator (thresholds, weights, the rules a quick look can check)
    # and the band floor. No policy: the built-in numbers, as before.
    from app.services.metrics_service import _resolve_active_policy_for_project

    resolved = (
        await _resolve_active_policy_for_project(db, str(run.project_id))
        if run.project_id is not None
        else None
    )
    policy_obj, policy_level, rules_not_checked = _quick_look_policy(resolved)

    # With no per-test analyses available (those are produced by the
    # offline pipeline's analysis stage, which has already run by the
    # time this endpoint is called — but the synth deliberately stays
    # cheap and deterministic), every analysis-driven dimension scores
    # 0. The pass-rate-driven dimensions still produce meaningful
    # numbers, and the hard floor below catches the failing-run case
    # even when ``composite`` is small.
    dim_scores = compute_dimension_scores(
        analyses={},
        anomalies=[],
        pass_rate=pass_rate,
        is_regression=False,
        regression_tests=[],
        failure_clusters=[],
        open_defects=signals["open_defects_by_severity"] or open_defects,
    )
    # The evaluator the stored decision uses: the policy's weights and
    # thresholds, the verdict floor (D-04), then the rules. Called with the
    # resolved policy as an override, so it reads no table of its own and
    # counts no decision (a page read is not a gate decision).
    from app.services.policy_evaluator_service import evaluate_policy

    evaluation = await evaluate_policy(
        run.project_id,
        dim_scores,
        pass_rate,
        {
            "open_defects": open_defects,
            "open_defects_by_severity": signals["open_defects_by_severity"],
            "blocking_failures": int(signals["blocking_failures"]),
            "blocking_defects": int(signals["blocking_defects"]),
        },
        db,
        policy_override=policy_obj,
    )
    thresholds = evaluation.effective_thresholds
    threshold = float(thresholds["pass_rate_minimum"])
    hard_floor_factor = float(thresholds["pass_rate_hard_floor_factor"])
    composite = evaluation.effective_composite
    if pass_rate < threshold * hard_floor_factor:
        # Mirror the agent's hard floor: a run under the floor is NO_GO
        # whatever the composite says (``score_to_recommendation`` rule 1).
        # Raise the shown score to the No-Go band so it agrees with the
        # verdict: 60 with the built-in No-Go line of 55.
        composite = max(composite, 60.0, float(thresholds["no_go_threshold"]))
    recommendation = evaluation.recommendation
    floor_reasons = list((evaluation.verdict_floor or {}).get("reasons") or [])
    policy_blocking = [
        f"[Policy] {ev.message}" for ev in evaluation.rule_evaluations
        if not ev.passed and ev.action == "BLOCK"
    ]
    policy_conditions = [
        f"[Policy] {ev.message}" for ev in evaluation.rule_evaluations
        if not ev.passed and ev.action == "WARN"
    ]

    # Layer the same policy's pass-rate bands and P0 cap over the verdict so
    # /release-gate honours the colours /overview renders. Fail-closed: the
    # band can downgrade the verdict but never soften it.
    band: Optional[str] = None
    band_downgrades: list[str] = []
    band_policy: Optional[tuple[str, int, str]] = None
    bands_applied: Optional[dict] = None
    band_held_back = False
    if resolved is not None:
        policy_doc, band_policy_id, band_policy_version, band_policy_level = resolved
        before_band = recommendation
        policy_bands = policy_doc.get("pass_rate_bands") or {}
        recommendation, band, band_downgrades = await apply_policy_band_floor(
            db,
            run.project_id,
            recommendation,
            pass_rate,
            bands=policy_bands,
            hard_caps=policy_doc.get("hard_caps") or {},
        )
        band_policy = (band_policy_id, band_policy_version, band_policy_level)
        bands_applied = _effective_bands(policy_bands)
        band_held_back = _band_held_back(before_band, recommendation, band_downgrades)

    defects_noun = "open defect" if open_defects == 1 else "open defects"
    reasoning = (
        "Quick-look decision derived from this run's aggregates "
        f"(pass rate {pass_rate:.1f}%, {open_defects} {defects_noun}"
        + (f"; held back by {', '.join(floor_reasons)}" if floor_reasons else "")
        + "). Run Deep Investigation for richer insights — failure "
        "clusters, defect breakdown, and AI narrative."
    )

    conditions = _quick_look_conditions(recommendation, pass_rate, threshold)
    conditions = [f"Resolve: {reason}" for reason in floor_reasons] + policy_conditions + conditions

    return ReleaseCouncilResponse(
        run_id=str(run_id),
        project_id=str(run.project_id) if run.project_id else None,
        recommendation=recommendation,
        release_readiness_band=band,
        band_downgrades=band_downgrades,
        pass_rate_bands=bands_applied,
        band_held_back=band_held_back,
        # Floor, not round (D-96): a composite of 19.6 decided as "under 20"
        # must not be shown as 20.
        risk_score=int(math.floor(composite)),
        composite_risk=composite,
        dimension_scores=_build_dimension_scores(dim_scores),
        blocking_issues=policy_blocking,
        conditions_for_go=conditions,
        reasoning=reasoning,
        score_model_version=SCORE_MODEL_VERSION,
        input_snapshot={
            "synthesized": True,
            "pass_rate": pass_rate,
            "open_defects": open_defects,
            "open_defects_by_severity": signals["open_defects_by_severity"],
            "blocking_failures": signals["blocking_failures"],
            "blocking_defects": signals["blocking_defects"],
            # The pass-rate target applied: the policy's ``pass_rate_minimum``,
            # or RELEASE_PASS_RATE_THRESHOLD with no policy. Reporting it
            # alone was actively misleading: a run at 83% sat next to
            # "threshold: 90.0" and a GO verdict, which reads as a contradiction.
            "threshold": threshold,
            # ...because `threshold` is NOT the GO cutoff. Pass rate only forces
            # a verdict when it falls under `hard_floor_factor` (0.7, the
            # built-in _HARD_FLOOR_FACTOR, unless a policy sets its own) of the
            # threshold; above that the composite risk score decides. Publish the
            # number actually applied so the verdict is explicable from its own
            # snapshot instead of appearing to contradict it.
            "no_go_floor_pct": round(threshold * hard_floor_factor, 2),
            "hard_floor_factor": hard_floor_factor,
            # D-104: the two risk lines too, so the gate card can state every
            # number the verdict used.
            "go_threshold": float(thresholds["go_threshold"]),
            "no_go_threshold": float(thresholds["no_go_threshold"]),
            # Policy rules whose inputs a quick look does not have (flaky
            # counts, duration regressions, per-test dimensions). Named, not
            # passed as zeros: an unmeasured input must not read as a pass.
            "rules_not_checked": rules_not_checked,
            "verdict_driver": (
                "pass_rate_floor"
                if pass_rate < threshold * hard_floor_factor
                # Above the floor but under the configured bar -> CONDITIONAL_GO.
                # Named distinctly so the reader can tell "your own threshold
                # held this back" from "the risk model held this back".
                else "pass_rate_below_threshold"
                if pass_rate < threshold
                else "composite_risk"
            ),
        },
        cluster_insights=[],
        baseline_diff=None,
        open_defects_by_component=[],
        human_override=None,
        override_audit=[],
        pass_rate=pass_rate,
        build_number=run.build_number,
        # The policy this verdict was evaluated with (D-142): "hardcoded"
        # only when no policy is active, and then the band fields stay None.
        policy_id=evaluation.policy_id,
        policy_version=evaluation.policy_version,
        policy_level=policy_level,
        band_policy_id=band_policy[0] if band_policy else None,
        band_policy_version=band_policy[1] if band_policy else None,
        band_policy_level=band_policy[2] if band_policy else None,
        rule_evaluations=[
            RuleEvaluationResponse(
                rule_id=ev.rule_id,
                rule_name=ev.rule_name,
                rule_type=ev.rule_type,
                passed=ev.passed,
                action=ev.action,
                message=ev.message,
                actual_value=ev.actual_value,
                threshold_value=ev.threshold_value,
            )
            for ev in evaluation.rule_evaluations
        ],
        synthesized=True,
    )


# Policy rule types a quick look can check from the run's aggregates: the open
# defect count is the one rule input it has. Flaky counts, duration
# regressions and the per-test dimensions come from deep investigation.
_QUICK_LOOK_RULE_TYPES = frozenset({"open_defect_limit"})


def _quick_look_policy(
    resolved: Optional[tuple[dict, str, int, str]],
) -> tuple[Any, str, list[dict]]:
    """The policy the quick look evaluates, its level, and the rules it skips.

    ``resolved`` is ``_resolve_active_policy_for_project``'s answer. With no
    policy, a frozen copy of the built-in numbers (as the release-risk agent
    freezes its own fallback), so the verdict and score are exactly what the
    quick look produced before D-142. With a policy, its whole document minus
    the rules whose inputs a quick look does not have; those are returned so
    the page can say they were not checked.
    """
    from types import SimpleNamespace

    from app.services.criticality_service import _GO_THRESHOLD, _NO_GO_THRESHOLD, _weights

    if resolved is None:
        frozen = SimpleNamespace(
            id=None,
            version=None,
            rules={
                "thresholds": {
                    "go_threshold": float(_GO_THRESHOLD),
                    "no_go_threshold": float(_NO_GO_THRESHOLD),
                    "pass_rate_minimum": float(settings.RELEASE_PASS_RATE_THRESHOLD),
                    "pass_rate_hard_floor_factor": _HARD_FLOOR_FACTOR,
                },
                "dimension_weights": _weights(),
                "rules": [],
            },
        )
        return frozen, "hardcoded", []

    policy_doc, policy_id, policy_version, policy_level = resolved
    rules = [r for r in (policy_doc.get("rules") or []) if isinstance(r, dict)]
    checked = [r for r in rules if r.get("type") in _QUICK_LOOK_RULE_TYPES]
    not_checked = [
        {"id": r.get("id"), "name": r.get("name") or r.get("type"), "type": r.get("type")}
        for r in rules
        if r.get("enabled", True) and r.get("type") not in _QUICK_LOOK_RULE_TYPES
    ]
    policy = SimpleNamespace(
        id=policy_id,
        version=policy_version,
        rules={**policy_doc, "rules": checked},
    )
    return policy, policy_level, not_checked


async def get_release_council(
    run_id: uuid.UUID,
    db: AsyncSession,
    baseline_diff: Optional[BaselineDiff] = None,
) -> Optional[ReleaseCouncilResponse]:
    """
    Retrieve the release decision with full council context:
    dimension scores, linked cluster insights, baseline diff, open defects.

    When no persisted ``ReleaseDecision`` row exists (typically because
    deep investigation hasn't run yet), this function synthesises a
    deterministic quick-look decision from the run's aggregates so the
    /release-gate page can render something useful instead of a 404.
    The synthesised response carries ``synthesized=True`` so the UI can
    explain that richer insights (clusters, defect breakdown, override
    audit) need a deep investigation run.
    """
    result = await db.execute(
        select(ReleaseDecision).where(ReleaseDecision.test_run_id == run_id)
    )
    decision = result.scalar_one_or_none()
    if not decision:
        # Caller may still get None if the run itself doesn't exist —
        # the synth path needs the TestRun row to compute anything.
        return await _synthesize_release_council(run_id, db)

    run_result = await db.execute(select(TestRun).where(TestRun.id == run_id))
    run = run_result.scalar_one_or_none()

    # Build dimension scores
    dim_scores = _build_dimension_scores(decision.dimension_scores)

    # Build cluster insights
    cluster_result = await db.execute(
        select(FailureCluster)
        .where(FailureCluster.test_run_id == run_id, is_current_snapshot())
        .order_by(FailureCluster.size.desc())
    )
    clusters = cluster_result.scalars().all()
    total_analyses = sum(c.size for c in clusters) or 1

    cluster_insights = []
    for c in clusters:
        c_dim = score_cluster(
            cluster={},
            all_dim_scores=decision.dimension_scores or {},
            total_analyses=total_analyses,
            member_count=c.size,
        )
        c_dim_scores = _build_dimension_scores(c_dim) if c_dim else []
        cluster_insights.append(ClusterInsightResponse(
            id=str(c.id),
            cluster_id=c.cluster_id,
            label=c.label,
            size=c.size,
            representative_error=c.representative_error,
            member_test_ids=[str(m) for m in (c.member_test_ids or [])],
            cohesion_score=c.cohesion_score,
            criticality_level=c.regression_classification,
            dimension_scores=c_dim_scores,
        ))

    # Open defects by component
    project_id = run.project_id if run else None
    open_defects_by_component: list[dict] = []
    if project_id:
        defect_result = await db.execute(
            select(
                Defect.component,
                sa_func.count(Defect.id).label("count"),
            )
            .where(Defect.project_id == project_id)
            .where(Defect.resolution_status.in_(ACTIVE_STATUSES))
            .group_by(Defect.component)
        )
        open_defects_by_component = [
            {"component": row.component or "unassigned", "count": row.count}
            for row in defect_result.all()
        ]

    # Override audit trail
    raw_audit = decision.override_audit or []
    override_audit = []
    for entry in raw_audit:
        try:
            override_audit.append(OverrideAuditEntry(**entry))
        except Exception:
            continue

    # ── Policy context (ENT-02) ────────────────────────────────────────────
    policy_id = str(decision.policy_id) if decision.policy_id else None
    policy_version = None
    policy_level = None
    rule_evaluations: list[RuleEvaluationResponse] = []

    policy_eval = decision.policy_evaluation
    if policy_eval:
        policy_version = policy_eval.get("policy_version")
        policy_level = policy_eval.get("policy_level")
        for rev in policy_eval.get("rule_evaluations", []):
            try:
                rule_evaluations.append(RuleEvaluationResponse(
                    rule_id=rev.get("rule_id", ""),
                    rule_name=rev.get("rule_name", ""),
                    rule_type=rev.get("rule_type", ""),
                    passed=rev.get("passed", True),
                    action=rev.get("action", "INFO"),
                    message=rev.get("message", ""),
                    actual_value=rev.get("actual_value"),
                    threshold_value=rev.get("threshold_value"),
                ))
            except Exception:
                continue

    # Layer the active ReleaseGatePolicy's pass-rate bands over the persisted
    # recommendation, same fail-closed semantic as the synthesised path. A
    # human override is left untouched — override is a deliberate human
    # decision that should not be silently downgraded by automated bands.
    final_recommendation = decision.recommendation
    band: Optional[str] = None
    band_downgrades: list[str] = []
    # Initialised here, not only inside the branch below: an overridden
    # decision skips the band floor entirely, and the response reads this.
    band_policy: Optional[tuple[str, int, str]] = None
    bands_applied: Optional[dict] = None
    band_held_back = False
    if decision.human_override is None:
        # The open-CRITICAL defect count for the P0 hard cap is resolved
        # inside _apply_band_floor via the shared helper — the old inline
        # ``severity == "P0"`` count matched nothing, so the cap never fired.
        (
            final_recommendation, band, band_downgrades, band_policy, bands_applied,
        ) = await _band_floor_detail(
            db, project_id, decision.recommendation, run.pass_rate if run else 0.0,
        )
        band_held_back = band_policy is not None and _band_held_back(
            decision.recommendation, final_recommendation, band_downgrades,
        )

    return ReleaseCouncilResponse(
        run_id=str(run_id),
        pass_rate_bands=bands_applied,
        band_held_back=band_held_back,
        project_id=str(project_id) if project_id else None,
        recommendation=final_recommendation,
        risk_score=decision.risk_score,
        composite_risk=decision.composite_risk,
        dimension_scores=dim_scores,
        blocking_issues=decision.blocking_issues or [],
        conditions_for_go=decision.conditions_for_go or [],
        reasoning=decision.reasoning,
        score_model_version=decision.score_model_version,
        input_snapshot=decision.input_snapshot,
        cluster_insights=cluster_insights,
        baseline_diff=baseline_diff,
        open_defects_by_component=open_defects_by_component,
        human_override=decision.human_override,
        overridden_by=str(decision.overridden_by) if decision.overridden_by else None,
        # Preserve the originally-persisted recommendation alongside any
        # band-driven downgrade so the UI can show "GO → CONDITIONAL (band)".
        original_recommendation=decision.original_recommendation or (
            decision.recommendation if final_recommendation != decision.recommendation else None
        ),
        original_risk_score=decision.original_risk_score,
        override_audit=override_audit,
        pass_rate=run.pass_rate if run else None,
        build_number=run.build_number if run else None,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_level=policy_level,
        # The policy that produced ``release_readiness_band`` and may have
        # downgraded ``recommendation`` above. Distinct from ``policy_*``,
        # which describes the STORED decision as the agent wrote it.
        band_policy_id=band_policy[0] if band_policy else None,
        band_policy_version=band_policy[1] if band_policy else None,
        band_policy_level=band_policy[2] if band_policy else None,
        release_readiness_band=band,
        band_downgrades=band_downgrades,
        rule_evaluations=rule_evaluations,
        # HON-06: the stored row's own times, so the gate's timeline dates the
        # decision instead of inventing one. Both the GET and the override
        # response come through here; a synthesised view never does.
        created_at=getattr(decision, "created_at", None),
        updated_at=getattr(decision, "updated_at", None),
    )


# A person set a test's category: one correction (``human_correction``, also
# written when a correction is edited) or a bulk reclassify of uncategorised
# failures (``bulk_reclassify``). ``ai_history_core.TEST_TRIGGERS`` names both.
_HUMAN_CATEGORY_TRIGGERS = ("human_correction", "bulk_reclassify")
# The pipeline stage that reads a run's analyses and human corrections for the
# decision (``AnalysisAgent.stage_name``).
_ANALYSIS_READ_STAGE = "root_cause_analysis"


def decision_evaluated_at(council: ReleaseCouncilResponse) -> Optional[datetime]:
    """The fallback time a stored decision was evaluated, or None.

    ``input_snapshot.assembled_at`` is written each time the release-risk agent
    evaluates (a re-run rewrites it, an override does not). It is NOT when the
    decision's analyses were read: the root-cause stage read them (and the
    corrections) minutes earlier, so ``analysis_changed_since`` prefers that
    stage's start and uses this only when the pipeline has no such row (review
    F3). A row without ``assembled_at`` falls back to ``created_at``;
    ``updated_at`` is not used because an override moves it.
    """
    snapshot = council.input_snapshot if isinstance(council.input_snapshot, dict) else {}
    raw = snapshot.get("assembled_at")
    when: Optional[datetime] = None
    if isinstance(raw, str):
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            when = None
    if when is None:
        when = council.created_at
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


async def analysis_changed_since(
    db: AsyncSession,
    run_id: uuid.UUID,
    decided_at: datetime,
    pipeline_run_id: Optional[uuid.UUID] = None,
) -> Optional[datetime]:
    """The newest human category change in ``run_id`` saved after the stored
    decision's analyses were read.

    The read time is the start of ``pipeline_run_id``'s root-cause stage (that
    stage loads the analyses and the human corrections the decision scores,
    ``analysis_agent``), capped at ``decided_at``; with no such stage row it is
    ``decided_at`` itself (``decision_evaluated_at``). A correction saved while
    the deep run was in flight is therefore reported (review F3).

    D-97: a correction now counts in the score, but a decision stored before
    the correction still reads the old category, and a signed decision must not
    be recomputed silently. This only reports that the run's analysis changed,
    so the gate can say "re-evaluate"; the stored row is not touched.

    Two records, either enough: the analysis version a correction or bulk
    reclassify stages (it also covers an edited correction), and the
    correction's own ``ai_feedback`` row (it survives a history write that
    failed, which never fails the correction). Read-only; one statement.
    """
    from sqlalchemy import DateTime, bindparam

    from app.models.postgres import (
        AgentStageResult,
        AIAnalysisVersion,
        AIFeedback,
        FeedbackRating,
        TestCase,
    )

    fallback = bindparam("decided_at", decided_at, type_=DateTime(timezone=True))
    if pipeline_run_id is not None:
        stage_start = (
            select(sa_func.min(AgentStageResult.started_at))
            .where(
                AgentStageResult.pipeline_run_id == pipeline_run_id,
                AgentStageResult.stage_name == _ANALYSIS_READ_STAGE,
            )
            .scalar_subquery()
        )
        # LEAST skips a NULL (no stage row, or a stage never started) on
        # PostgreSQL, so this is the stage start when known, else decided_at.
        read_at: Any = sa_func.least(stage_start, fallback)
    else:
        read_at = fallback

    versions = (
        select(sa_func.max(AIAnalysisVersion.created_at))
        .where(
            AIAnalysisVersion.test_run_id == run_id,
            AIAnalysisVersion.trigger.in_(_HUMAN_CATEGORY_TRIGGERS),
            AIAnalysisVersion.created_at > read_at,
        )
        .scalar_subquery()
    )
    corrections = (
        select(sa_func.max(AIFeedback.created_at))
        .select_from(AIFeedback)
        .join(TestCase, TestCase.id == AIFeedback.test_case_id)
        .where(
            TestCase.test_run_id == run_id,
            AIFeedback.rating == FeedbackRating.INCORRECT.value,
            AIFeedback.corrected_category.isnot(None),
            AIFeedback.created_at > read_at,
        )
        .scalar_subquery()
    )
    # GREATEST skips NULLs on PostgreSQL: NULL only when neither found one.
    value = (await db.execute(select(sa_func.greatest(versions, corrections)))).scalar_one_or_none()
    return value if isinstance(value, datetime) else None


async def apply_override(
    run_id: uuid.UUID,
    override_recommendation: str,
    reason: str,
    actor: Any,
    db: AsyncSession,
) -> Optional[ReleaseCouncilResponse]:
    """
    Override the release decision and persist an auditable before/after record.
    """
    result = await db.execute(
        select(ReleaseDecision)
        .where(ReleaseDecision.test_run_id == run_id)
        # The JSON audit list is read, appended, and written back. Serialize
        # concurrent overrides so a stale reader cannot overwrite another QA
        # lead's decision and silently erase its immutable audit entry.
        .with_for_update()
    )
    decision = result.scalar_one_or_none()
    if not decision:
        return None

    # ── Policy override constraints (ENT-02) ─────────────────────────────
    from app.services.policy_evaluator_service import check_override_constraints, resolve_effective_policy

    # Resolve project_id (and the pass rate the band floor reads) from the run
    run_result = await db.execute(
        select(TestRun.project_id, TestRun.pass_rate).where(TestRun.id == run_id)
    )
    run_row = run_result.one_or_none()
    project_id = run_row[0] if run_row else None
    pass_rate = run_row[1] if run_row else None

    # S-B-6: the constraints and the audit's "before" use the recommendation
    # the gate page SHOWS, not the stored one. ``get_release_council`` layers
    # the active policy's band floor over a decision nobody has overridden yet,
    # so a stored GO can read No-Go on screen; checking the stored GO let a
    # policy that forbids No-Go -> Go be bypassed.
    displayed_recommendation = decision.recommendation
    if decision.human_override is None:
        displayed_recommendation, _band, _downgrades, _band_policy = await _apply_band_floor(
            db, project_id, decision.recommendation, pass_rate or 0.0,
        )

    policy, policy_level = await resolve_effective_policy(project_id, db)
    allowed, constraint_error = check_override_constraints(
        policy, displayed_recommendation, override_recommendation, reason,
    )
    if not allowed:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=constraint_error,
        )

    # Record original values on first override
    if decision.original_recommendation is None:
        decision.original_recommendation = decision.recommendation
        decision.original_risk_score = decision.risk_score

    # Build audit entry
    audit_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_id": str(actor.id) if actor else None,
        # D-101: the history names a person; ``actor_id`` keeps the identity.
        "actor_name": getattr(actor, "full_name", None) or getattr(actor, "username", None),
        "before_recommendation": displayed_recommendation,
        "before_risk_score": decision.risk_score,
        "after_recommendation": override_recommendation,
        "reason": reason,
        "policy_id": str(policy.id) if policy else None,
        "policy_version": policy.version if policy else None,
    }

    # Append to audit trail
    existing_audit = list(decision.override_audit or [])
    existing_audit.append(audit_entry)
    decision.override_audit = existing_audit

    # Apply override. Transaction is owned by the handler — ``apply_override``
    # only stages the mutation so the override row, any follow-up
    # ``mark_stale`` on the intelligence snapshot, and the metrics
    # counter can all land atomically under one commit.
    before_rec = displayed_recommendation
    decision.recommendation = override_recommendation
    decision.human_override = reason
    decision.overridden_by = actor.id if actor else None

    await db.flush()
    await db.refresh(decision)

    # Track metric
    release_overrides_total.labels(
        from_recommendation=before_rec,
        to_recommendation=override_recommendation,
    ).inc()

    logger.info(
        "Release decision overridden for run %s by %s: %s → %s",
        run_id,
        getattr(actor, "username", "unknown"),
        audit_entry["before_recommendation"],
        override_recommendation,
    )

    return await get_release_council(run_id, db)
