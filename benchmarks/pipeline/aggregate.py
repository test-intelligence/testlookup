"""Pure aggregation for the AI pipeline baseline (O-0).

This module turns rows the pipeline ALREADY records into a comparable baseline
document. It adds no instrumentation: ``agent_stage_results`` has carried
per-stage tokens, cost, LLM-call count, fallback flags and timings since Phase
6, and ``agent_pipeline_runs`` carries end-to-end timings. The reason no
baseline existed is that nothing ever aggregated them.

Design rules, mirroring ``app/services/agent_eval_harness.py``:

* **Pure.** No database, no HTTP, no imports from ``app``. It consumes plain
  dicts so it can be unit-tested without a stack -- which matters, because the
  environments that HAVE pipeline data (homelab, dev compose) are exactly the
  ones a contributor may not be able to run.
* **Never raises.** Malformed rows degrade to "not counted", never to a
  traceback. A measurement harness that dies on one bad row measures nothing.
* **Never invents a number.** A metric that cannot be derived from the input is
  reported in ``not_measured`` with the reason. Emitting 0.0 for "we did not
  look" is how a baseline becomes a lie that later work is compared against.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1

# Below this many runs a percentile is arithmetic, not evidence. Reported
# rather than suppressed, but flagged so nobody quotes a p95 over three runs.
MIN_SAMPLES = 5

# Share of all degraded runs falling on one day, above which the aggregate
# degraded rate is describing an incident rather than a steady state.
CONCENTRATION_THRESHOLD = 0.5

# Failed-test count -> band. The bands exist because pipeline cost is driven by
# the size of the failure set, so a single global average hides the only
# dimension that matters.
BANDS: tuple[tuple[str, int, Optional[int]], ...] = (
    ("green", 0, 0),        # no failures — the all-green fast path
    ("small", 1, 9),
    ("medium", 10, 49),
    ("large", 50, None),
)

# decision_log decision_points that record an LLM output failing its schema.
# These are the parse failures F-4/G.1 will move: today every structured result
# is recovered from free text with a regex.
_PARSE_FAILURE_POINTS = frozenset({"summary_schema_validation"})

# ExecutionPath values where a skipped stage means the pipeline routed
# correctly, not that it lost something. The all-green fast path skips analysis
# because there is nothing to analyse; the planner and confidence routers skip
# stages that do not apply. Counting these as degradation reports a perfectly
# healthy green run as 100% degraded -- which is what a first draft of this
# harness did, and exactly the kind of false signal that makes a baseline
# worthless.
#
# A skipped stage with NO execution_path is counted as degraded: every current
# skip path records one, so its absence means an older row we cannot vouch for,
# and over-reporting degradation is the safer error.
_BENIGN_SKIP_PATHS = frozenset({
    "all_green_skip", "conditional_skip", "low_confidence_skip",
})


def _num(value: Any) -> Optional[float]:
    """Coerce to float, returning None for anything that is not a real number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def percentile(values: Iterable[Any], p: float) -> Optional[float]:
    """Linear-interpolated percentile over the numeric members of ``values``.

    Returns None for an empty sample rather than 0.0 -- "no data" and "zero"
    are different answers and the baseline must not conflate them.
    """
    nums = sorted(v for v in (_num(x) for x in values) if v is not None)
    if not nums:
        return None
    if len(nums) == 1:
        return round(nums[0], 4)
    rank = (len(nums) - 1) * max(0.0, min(1.0, p))
    low = int(rank)
    high = min(low + 1, len(nums) - 1)
    weight = rank - low
    return round(nums[low] * (1 - weight) + nums[high] * weight, 4)


def _distribution(values: Iterable[Any], *, unit: str) -> dict[str, Any]:
    nums = [v for v in (_num(x) for x in values) if v is not None]
    return {
        "unit": unit,
        "n": len(nums),
        "sufficient_samples": len(nums) >= MIN_SAMPLES,
        "p50": percentile(nums, 0.50),
        "p95": percentile(nums, 0.95),
        "max": round(max(nums), 4) if nums else None,
        "total": round(sum(nums), 4) if nums else None,
    }


def _duration_seconds(row: Any) -> Optional[float]:
    """Wall-clock seconds between started_at and completed_at, if both exist."""
    row = _as_dict(row)
    started, completed = row.get("started_at"), row.get("completed_at")
    if started is None or completed is None:
        return None
    try:
        delta = (completed - started).total_seconds()
    except (TypeError, AttributeError):
        return None
    # A negative duration means clock skew or a bad row; counting it would drag
    # a p50 toward zero and quietly flatter the baseline.
    return delta if delta >= 0 else None


def failed_test_count(run: Any) -> int:
    """The failure workload the pipeline actually faced for this run.

    Read from the analysis stage's own result_data rather than the run table,
    because that is the number the stage was asked to process -- including the
    tests it recorded as unanalysed when the wall-clock budget ran out.
    """
    for stage in _as_list(_as_dict(run).get("stages")):
        stage = _as_dict(stage)
        if stage.get("stage_name") != "root_cause_analysis":
            continue
        data = _as_dict(stage.get("result_data"))
        analysed = _num(data.get("analysed")) or 0.0
        skipped = _num(data.get("budget_skipped")) or 0.0
        return int(analysed + skipped)
    return 0


def band_for(failed_tests: int) -> str:
    for name, low, high in BANDS:
        if failed_tests >= low and (high is None or failed_tests <= high):
            return name
    return "large"


def _run_is_degraded(run: Any) -> bool:
    """True when any stage in the run failed or was skipped non-deliberately."""
    return any(_is_degradation(s) for s in _as_list(_as_dict(run).get("stages")))


def _day_of(run: Any) -> Optional[str]:
    """The UTC date a run started, as ``YYYY-MM-DD``; None if unusable."""
    started = _as_dict(run).get("started_at")
    try:
        return started.date().isoformat()
    except (AttributeError, TypeError, ValueError):
        return None


def summarize_temporal(runs: Iterable[Any]) -> dict[str, Any]:
    """Show whether degradation is chronic or a single bad day.

    This exists because the first real baseline reported "86% of runs degraded"
    when 747 of 765 failures had happened in ONE HOUR three weeks earlier. Over
    an all-time window a short incident is arithmetically indistinguishable
    from a permanent condition, and the aggregate number reads as current
    state. Anyone acting on it would have been fixing the wrong thing, or
    fixing nothing at all because it had already stopped.

    ``degraded_rate_excluding_peak_day`` is the number to compare against
    ``bands[*].degraded_rate``: when they diverge sharply the aggregate is
    being driven by one date, which is named.
    """
    clean = [_as_dict(r) for r in runs if isinstance(r, dict)]
    runs_by_day: dict[str, int] = {}
    degraded_by_day: dict[str, int] = {}
    undated = 0

    for run in clean:
        day = _day_of(run)
        if day is None:
            undated += 1
            continue
        runs_by_day[day] = runs_by_day.get(day, 0) + 1
        if _run_is_degraded(run):
            degraded_by_day[day] = degraded_by_day.get(day, 0) + 1

    total_degraded = sum(degraded_by_day.values())
    peak_day = max(degraded_by_day, key=lambda d: degraded_by_day[d]) if degraded_by_day else None
    peak_count = degraded_by_day.get(peak_day, 0) if peak_day else 0
    peak_share = round(peak_count / total_degraded, 4) if total_degraded else None

    dated = [r for r in clean if _day_of(r) is not None]
    off_peak = [r for r in dated if _day_of(r) != peak_day]
    off_peak_degraded = sum(1 for r in off_peak if _run_is_degraded(r))

    # "Concentrated" needs BOTH a dominant day and more than one day observed:
    # a single-day corpus is trivially 100% concentrated and says nothing.
    concentrated = bool(
        peak_share is not None
        and peak_share >= CONCENTRATION_THRESHOLD
        and len(runs_by_day) > 1
        and total_degraded > 0
    )

    return {
        "days_observed": len(runs_by_day),
        "first_day": min(runs_by_day) if runs_by_day else None,
        "last_day": max(runs_by_day) if runs_by_day else None,
        "runs_without_a_date": undated,
        "degraded_total": total_degraded,
        "peak_degraded_day": peak_day,
        "peak_degraded_count": peak_count,
        "peak_day_share_of_degraded": peak_share,
        # The honest comparison: what the degraded rate looks like once the
        # worst single day is set aside.
        "degraded_rate_excluding_peak_day": (
            round(off_peak_degraded / len(off_peak), 4) if off_peak else None
        ),
        "degradation_is_concentrated": concentrated,
        "concentration_note": (
            f"{peak_count} of {total_degraded} degraded runs "
            f"({peak_share:.0%}) fall on {peak_day} — the aggregate degraded "
            "rate describes that day, not current state."
            if concentrated and peak_share is not None else None
        ),
    }


def _is_degradation(stage: Any) -> bool:
    """True when a stage's outcome means the run lost something.

    A failure always counts. A skip counts only when it was NOT deliberate
    routing — see ``_BENIGN_SKIP_PATHS``.
    """
    stage = _as_dict(stage)
    status = stage.get("status")
    if status == "failed":
        return True
    if status != "skipped":
        return False
    path = stage.get("execution_path")
    return str(path) not in _BENIGN_SKIP_PATHS


def _parse_failures(stage: dict) -> int:
    count = 0
    for entry in _as_list(stage.get("decision_log")):
        entry = _as_dict(entry)
        if entry.get("decision_point") in _PARSE_FAILURE_POINTS:
            count += 1
    return count


def summarize_stage(stage_name: str, stages: list[dict]) -> dict[str, Any]:
    """Per-stage rollup across every run in the window."""
    executed = [s for s in stages if s.get("status") == "completed"]
    llm_calls = [_num(s.get("llm_calls_count")) or 0.0 for s in stages]
    total_llm_calls = sum(llm_calls)
    parse_failures = sum(_parse_failures(s) for s in stages)

    modes: dict[str, int] = {}
    for stage in stages:
        mode = stage.get("analysis_mode")
        if mode:
            modes[str(mode)] = modes.get(str(mode), 0) + 1

    fallbacks = [s for s in stages if s.get("fallback_used") is True]
    fallback_reasons: dict[str, int] = {}
    for stage in fallbacks:
        reason = str(stage.get("fallback_reason") or "unspecified")[:60]
        fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1

    return {
        "stage_name": stage_name,
        "observations": len(stages),
        "completed": len(executed),
        "failed": sum(1 for s in stages if s.get("status") == "failed"),
        "skipped": sum(1 for s in stages if s.get("status") == "skipped"),
        "latency_seconds": _distribution(
            (_duration_seconds(s) for s in executed), unit="seconds"
        ),
        "total_tokens": _distribution(
            (s.get("total_tokens") for s in stages), unit="tokens"
        ),
        "cost_usd": _distribution((s.get("cost_usd") for s in stages), unit="usd"),
        "llm_calls": _distribution(
            (s.get("llm_calls_count") for s in stages), unit="calls"
        ),
        "fallback_rate": round(len(fallbacks) / len(stages), 4) if stages else None,
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        # Parse failures per LLM call, not per stage: a stage that makes four
        # calls has four chances to fail its schema.
        "parse_failures": parse_failures,
        "parse_failure_rate_per_llm_call": (
            round(parse_failures / total_llm_calls, 4) if total_llm_calls else None
        ),
        "analysis_modes": dict(sorted(modes.items())),
    }


def summarize_band(band: str, runs: list[dict]) -> dict[str, Any]:
    """Per-band rollup: what one run of this size costs, end to end."""
    def _run_total(run: dict, field: str) -> float:
        return sum(
            _num(_as_dict(s).get(field)) or 0.0 for s in _as_list(run.get("stages"))
        )

    degraded = 0
    budget_skipped_runs = 0
    for run in runs:
        stages = [_as_dict(s) for s in _as_list(run.get("stages"))]
        if any(_is_degradation(s) for s in stages):
            degraded += 1  # see _run_is_degraded — same rule, per-run
        for stage in stages:
            if (_num(_as_dict(stage.get("result_data")).get("budget_skipped")) or 0) > 0:
                budget_skipped_runs += 1
                break

    return {
        "band": band,
        "runs": len(runs),
        "sufficient_samples": len(runs) >= MIN_SAMPLES,
        "end_to_end_seconds": _distribution(
            (_duration_seconds(r) for r in runs), unit="seconds"
        ),
        "tokens_per_run": _distribution(
            (_run_total(r, "total_tokens") for r in runs), unit="tokens"
        ),
        "cost_per_run_usd": _distribution(
            (_run_total(r, "cost_usd") for r in runs), unit="usd"
        ),
        "llm_calls_per_run": _distribution(
            (_run_total(r, "llm_calls_count") for r in runs), unit="calls"
        ),
        # A run is "degraded" when any stage failed or was skipped. This is the
        # number the F-2 wall-clock budget is expected to move: it converts
        # whole-pipeline retries into disclosed partial reports.
        "degraded_rate": round(degraded / len(runs), 4) if runs else None,
        "budget_skipped_rate": (
            round(budget_skipped_runs / len(runs), 4) if runs else None
        ),
    }


# ── Grounding: how well claims and narrative are tied to evidence ────────────
#
# Two different mechanisms, measured separately because they fail differently:
#
#   * typed claims in the decision report carry an ``evidence`` list built
#     server-side, so the question is whether that evidence is CLAIM-SPECIFIC
#     or one shared bundle stamped onto every claim (finding F-16);
#   * the summary narrative gets citations only from a verbatim 40-character
#     match against evidence excerpts, applied to ONE layer, so the question is
#     how often that can fire at all (finding F-3).

# Summary layers that can carry a citation. Since F-3 each claim-bearing layer
# returns ``evidence_ids`` resolved against a server-built catalogue, so all
# three carry citations. Layer 1 is free prose (a three-sentence synthesis) and
# is deliberately still uncitable -- reported rather than quietly omitted.
#
# Baselines taken BEFORE F-3 will show only layer 3 populated. That is a real
# difference between corpora, not a bug in the harness, and it is exactly the
# delta the fix is meant to produce.
_CITABLE_LAYERS = (
    "layer2_incident_view",
    "layer3_evidence_pack",
    "layer4_action_plan",
)
_ALL_LAYERS = (
    "layer1_executive_summary",
    "layer2_incident_view",
    "layer3_evidence_pack",
    "layer4_action_plan",
)


def _evidence_signature(claim: Any) -> str:
    """A stable identity for a claim's evidence list, order-insensitive."""
    refs = _as_list(_as_dict(claim).get("evidence"))
    parts = []
    for ref in refs:
        ref = _as_dict(ref)
        parts.append("|".join(str(ref.get(k) or "") for k in ("type", "id", "evidence_id")))
    return "&".join(sorted(parts))


def summarize_report_grounding(reports: Iterable[Any]) -> dict[str, Any]:
    """Claim-level evidence coverage across published decision reports."""
    clean = [_as_dict(r) for r in reports if isinstance(r, dict)]
    claims_total = 0
    claims_with_evidence = 0
    claims_by_kind: dict[str, int] = {}
    refs_per_claim: list[int] = []
    shared_bundle_reports = 0
    multi_claim_reports = 0

    for report in clean:
        claims = [_as_dict(c) for c in _as_list(report.get("claims"))]
        if not claims:
            continue
        signatures = [_evidence_signature(c) for c in claims]
        for claim, signature in zip(claims, signatures):
            claims_total += 1
            refs = _as_list(claim.get("evidence"))
            refs_per_claim.append(len(refs))
            if refs:
                claims_with_evidence += 1
            kind = str(claim.get("kind") or "unknown")
            claims_by_kind[kind] = claims_by_kind.get(kind, 0) + 1
        # The F-16 question. With one claim there is nothing to share WITH, so
        # such reports are excluded from the denominator rather than counted as
        # evidence of good behaviour.
        if len(claims) > 1:
            multi_claim_reports += 1
            if len(set(signatures)) == 1:
                shared_bundle_reports += 1

    return {
        "reports": len(clean),
        "claims_total": claims_total,
        "claims_with_evidence": claims_with_evidence,
        "claims_with_evidence_rate": (
            round(claims_with_evidence / claims_total, 4) if claims_total else None
        ),
        "evidence_refs_per_claim": _distribution(refs_per_claim, unit="refs"),
        "claims_by_kind": dict(sorted(claims_by_kind.items())),
        # Reports where EVERY claim carries a byte-identical evidence list.
        # A high rate means the claim-evidence drawer is showing bundle-level
        # provenance as though it were claim-level (finding F-16).
        "multi_claim_reports": multi_claim_reports,
        "reports_sharing_one_evidence_set": shared_bundle_reports,
        "shared_evidence_bundle_rate": (
            round(shared_bundle_reports / multi_claim_reports, 4)
            if multi_claim_reports else None
        ),
    }


def summarize_narrative_citations(summaries: Iterable[Any]) -> dict[str, Any]:
    """How often the generated narrative carries a citation at all."""
    clean = [_as_dict(s) for s in summaries if isinstance(s, dict)]
    citations_total = 0
    summaries_with_citations = 0
    layers_present = 0
    ids_unresolved = 0
    summaries_with_fabricated_ids = 0

    for summary in clean:
        cited = 0
        for layer_key in _CITABLE_LAYERS:
            layer = _as_dict(summary.get(layer_key))
            cited += len(_as_list(layer.get("citations")))
        citations_total += cited
        if cited:
            summaries_with_citations += 1
        layers_present += sum(
            1 for key in _ALL_LAYERS if summary.get(key) not in (None, "", {}, [])
        )
        # Post-F-3 summaries carry the agent's own grounding record. An id the
        # model supplied that was NOT in the catalogue it was served is a
        # fabrication -- the number that says whether the contract is holding,
        # as distinct from whether citations merely exist.
        coverage = _as_dict(summary.get("citation_coverage"))
        unresolved = int(_num(coverage.get("ids_unresolved")) or 0)
        ids_unresolved += unresolved
        if unresolved:
            summaries_with_fabricated_ids += 1

    return {
        "summaries": len(clean),
        "summaries_with_any_citation": summaries_with_citations,
        "citation_rate": (
            round(summaries_with_citations / len(clean), 4) if clean else None
        ),
        "citations_total": citations_total,
        # Structural, not observed: which layers CAN carry a citation at all.
        # Layer 1 is free prose and remains uncitable by design.
        "citable_layers": list(_CITABLE_LAYERS),
        "uncitable_layers": [k for k in _ALL_LAYERS if k not in _CITABLE_LAYERS],
        "layers_rendered": layers_present,
        # Fabricated ids: supplied by the model, absent from the catalogue the
        # server served it. Non-zero means the contract is being violated, which
        # is a different problem from citations being scarce.
        "ids_unresolved": ids_unresolved,
        "summaries_with_fabricated_ids": summaries_with_fabricated_ids,
        "fabrication_rate": (
            round(summaries_with_fabricated_ids / len(clean), 4) if clean else None
        ),
    }


# Metrics the review asks for that the supplied input genuinely cannot answer.
# Built per-run rather than as a constant, because what is derivable depends on
# which stores the collector was pointed at.
def _not_measured(*, have_reports: bool, have_summaries: bool) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    if not have_reports:
        gaps.append({
            "metric": "claim_evidence_coverage",
            "why": (
                "No decision reports supplied. Pass --mongo-uri so the "
                "collector can read the published report store."
            ),
            "blocked_on": "collector input",
        })
    if not have_summaries:
        gaps.append({
            "metric": "citation_coverage",
            "why": (
                "No run summaries supplied. Pass --mongo-uri so the collector "
                "can read the narrative store."
            ),
            "blocked_on": "collector input",
        })
    gaps.append({
        "metric": "unsupported_claim_rate",
        "why": (
            "Coverage says whether a claim cites evidence, not whether the "
            "evidence supports it. Nothing classifies support today."
        ),
        "blocked_on": "requirement G.3 (narrative critic)",
    })
    gaps.append({
        "metric": "failure_category_macro_f1",
        "why": (
            "Needs labelled outcomes joined to analyses; ai_eval_service "
            "computes this on demand but nothing schedules it."
        ),
        "blocked_on": "requirement I.1 (nightly evaluation)",
    })
    return gaps


def build_baseline(
    runs: Iterable[Any],
    *,
    window: Optional[dict] = None,
    reports: Optional[Iterable[Any]] = None,
    summaries: Optional[Iterable[Any]] = None,
) -> dict[str, Any]:
    """Aggregate normalized pipeline runs into one comparable baseline document.

    ``runs`` are dicts shaped by ``collect.py``. ``reports`` and ``summaries``
    are the optional Mongo-backed grounding inputs; when either is omitted the
    corresponding metric is DECLARED unmeasured rather than reported as zero,
    because "we did not look" and "there were none" are different findings.

    Anything that is not a dict is dropped rather than raising -- see the
    module docstring.
    """
    clean = [_as_dict(r) for r in runs if isinstance(r, dict)]
    reports_list = None if reports is None else [r for r in reports]
    summaries_list = None if summaries is None else [s for s in summaries]

    banded: dict[str, list[dict]] = {name: [] for name, _, _ in BANDS}
    by_stage: dict[str, list[dict]] = {}
    for run in clean:
        banded.setdefault(band_for(failed_test_count(run)), []).append(run)
        for stage in _as_list(run.get("stages")):
            stage = _as_dict(stage)
            name = str(stage.get("stage_name") or "unknown")
            by_stage.setdefault(name, []).append(stage)

    workflow_types: dict[str, int] = {}
    for run in clean:
        key = str(run.get("workflow_type") or "unknown")
        workflow_types[key] = workflow_types.get(key, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "window": dict(window or {}),
        "runs_observed": len(clean),
        "workflow_types": dict(sorted(workflow_types.items())),
        # Stated up front: a baseline built on too few runs is a starting point
        # for collection, not a number to hold later work against.
        "sufficient_samples": len(clean) >= MIN_SAMPLES,
        "bands": {
            name: summarize_band(name, banded.get(name, []))
            for name, _, _ in BANDS
        },
        "stages": {
            name: summarize_stage(name, stages)
            for name, stages in sorted(by_stage.items())
        },
        "temporal": summarize_temporal(clean),
        "grounding": {
            "claims": (
                summarize_report_grounding(reports_list)
                if reports_list is not None else None
            ),
            "narrative": (
                summarize_narrative_citations(summaries_list)
                if summaries_list is not None else None
            ),
        },
        "not_measured": _not_measured(
            have_reports=reports_list is not None,
            have_summaries=summaries_list is not None,
        ),
    }
