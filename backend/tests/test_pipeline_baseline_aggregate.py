"""Tests for the AI-pipeline baseline aggregator (O-0).

The harness exists so that later AI work can be compared against something
real. Two properties therefore matter more than any individual statistic:

* it must never invent a number -- an absent measurement stays absent, and the
  gaps are declared rather than rendered as zeros;
* it must never raise -- a measurement harness that dies on one malformed row
  measures nothing at all, which is the failure mode it was built to end.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "pipeline"
sys.path.insert(0, str(_BENCH))

from aggregate import (  # noqa: E402
    MIN_SAMPLES,
    band_for,
    build_baseline,
    failed_test_count,
    percentile,
    summarize_band,
    summarize_narrative_citations,
    summarize_report_grounding,
    summarize_stage,
    summarize_temporal,
)

T0 = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


def _stage(name: str, *, seconds: float = 1.0, status: str = "completed", **extra):
    stage = {
        "stage_name": name,
        "status": status,
        "started_at": T0,
        "completed_at": T0 + timedelta(seconds=seconds),
    }
    stage.update(extra)
    return stage


def _run(*stages, seconds: float = 10.0, workflow_type: str = "deep"):
    return {
        "pipeline_run_id": "run-1",
        "workflow_type": workflow_type,
        "status": "completed",
        "started_at": T0,
        "completed_at": T0 + timedelta(seconds=seconds),
        "stages": list(stages),
    }


# ── Never invent a number ────────────────────────────────────────────────────


def test_percentile_of_nothing_is_none_not_zero():
    assert percentile([], 0.5) is None
    assert percentile([None, "x", {}], 0.95) is None


def test_percentile_interpolates():
    assert percentile([0, 10], 0.5) == 5.0
    assert percentile([1, 2, 3, 4], 0.0) == 1.0
    assert percentile([1, 2, 3, 4], 1.0) == 4.0


def test_absent_cost_is_not_reported_as_free():
    """A stage with no recorded cost must not read as costing $0.00."""
    stage = summarize_stage("summary", [_stage("summary", cost_usd=None)])
    assert stage["cost_usd"]["p50"] is None
    assert stage["cost_usd"]["total"] is None


def test_gaps_are_declared_rather_than_zeroed():
    baseline = build_baseline([_run(_stage("summary"))])
    declared = {item["metric"] for item in baseline["not_measured"]}
    assert "citation_coverage" in declared
    # ...and each one says why, so the gap is actionable rather than mysterious.
    for item in baseline["not_measured"]:
        assert item["why"]
        assert item["blocked_on"]


def test_not_looking_and_finding_none_are_different_answers():
    """Omitting the report store must not read as 'zero grounding found'."""
    unread = build_baseline([_run(_stage("summary"))])
    assert unread["grounding"]["claims"] is None
    assert unread["grounding"]["narrative"] is None
    assert "citation_coverage" in {i["metric"] for i in unread["not_measured"]}

    looked = build_baseline([_run(_stage("summary"))], reports=[], summaries=[])
    assert looked["grounding"]["claims"]["reports"] == 0
    assert looked["grounding"]["narrative"]["summaries"] == 0
    assert "citation_coverage" not in {i["metric"] for i in looked["not_measured"]}


def test_small_samples_are_flagged_not_hidden():
    baseline = build_baseline([_run(_stage("summary"))])
    assert baseline["runs_observed"] == 1
    assert baseline["sufficient_samples"] is False

    many = build_baseline([_run(_stage("summary")) for _ in range(MIN_SAMPLES)])
    assert many["sufficient_samples"] is True


def test_negative_duration_is_dropped_not_counted():
    """Clock skew must not drag a p50 toward zero and flatter the baseline."""
    backwards = {
        "stage_name": "summary",
        "status": "completed",
        "started_at": T0,
        "completed_at": T0 - timedelta(seconds=5),
    }
    stage = summarize_stage("summary", [backwards])
    assert stage["latency_seconds"]["n"] == 0
    assert stage["latency_seconds"]["p50"] is None


# ── Never raise ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("garbage", [
    None, "run", 42, [], {"stages": "not-a-list"},
    {"stages": ["not-a-dict"]},
    {"stages": [{"stage_name": None, "started_at": "yesterday"}]},
])
def test_malformed_rows_never_raise(garbage):
    baseline = build_baseline([garbage])
    assert baseline["schema_version"] == 1


def test_one_bad_row_does_not_lose_the_good_ones():
    baseline = build_baseline([_run(_stage("summary")), "garbage", None])
    assert baseline["runs_observed"] == 1


# ── The measurements themselves ──────────────────────────────────────────────


@pytest.mark.parametrize("failed,expected", [
    (0, "green"), (1, "small"), (9, "small"),
    (10, "medium"), (49, "medium"), (50, "large"), (5000, "large"),
])
def test_bands_split_on_failure_count(failed, expected):
    assert band_for(failed) == expected


def test_failed_count_includes_tests_the_budget_skipped():
    """Unanalysed-for-budget tests are still workload the pipeline faced."""
    run = _run(_stage(
        "root_cause_analysis",
        result_data={"analysed": 12, "budget_skipped": 8},
    ))
    assert failed_test_count(run) == 20
    assert band_for(failed_test_count(run)) == "medium"


def test_stage_rollup_reports_fallback_and_parse_failures():
    stages = [
        _stage("summary", llm_calls_count=4, decision_log=[
            {"decision_point": "summary_schema_validation", "chosen": "parse_fallback"},
            {"decision_point": "route_analysis_mode", "chosen": "llm"},
        ]),
        _stage("summary", llm_calls_count=4, fallback_used=True,
               fallback_reason="llm_model_not_available"),
    ]
    rollup = summarize_stage("summary", stages)

    assert rollup["observations"] == 2
    assert rollup["fallback_rate"] == 0.5
    assert rollup["fallback_reasons"] == {"llm_model_not_available": 1}
    # Rated per LLM call, not per stage: 8 calls, 1 schema failure.
    assert rollup["parse_failures"] == 1
    assert rollup["parse_failure_rate_per_llm_call"] == 0.125


def test_a_budget_truncated_run_is_degraded():
    healthy = _run(_stage("summary"), _stage("release_risk"))
    truncated = _run(_stage("summary"), _stage(
        "release_risk", status="skipped", execution_path="deadline_skip"
    ))
    rollup = summarize_band("small", [healthy, truncated])

    assert rollup["runs"] == 2
    assert rollup["degraded_rate"] == 0.5


@pytest.mark.parametrize("benign", [
    "all_green_skip", "conditional_skip", "low_confidence_skip",
])
def test_deliberate_routing_skips_are_not_degradation(benign):
    """A green run skips analysis by design; calling that degraded is a lie.

    The first draft of this harness counted every skipped stage, which reported
    healthy all-green runs as 100% degraded — a false signal that would have
    made the whole baseline untrustworthy.
    """
    green = _run(
        _stage("root_cause_analysis", status="skipped", execution_path=benign),
        _stage("summary"),
    )
    assert summarize_band("green", [green])["degraded_rate"] == 0.0


def test_a_skip_with_no_recorded_path_counts_as_degraded():
    """Unknown provenance errs toward over-reporting, never under-reporting."""
    unknown = _run(_stage("release_risk", status="skipped"))
    assert summarize_band("small", [unknown])["degraded_rate"] == 1.0


def test_a_failed_stage_is_always_degraded():
    failed = _run(_stage("summary", status="failed", execution_path="all_green_skip"))
    assert summarize_band("small", [failed])["degraded_rate"] == 1.0


def test_band_rollup_tracks_the_wall_clock_budget_skips():
    """The number the F-2 deadline is expected to move."""
    over_budget = _run(_stage(
        "root_cause_analysis", result_data={"analysed": 3, "budget_skipped": 7}
    ))
    rollup = summarize_band("small", [over_budget])
    assert rollup["budget_skipped_rate"] == 1.0


def test_costs_sum_across_stages_within_a_run():
    run = _run(
        _stage("root_cause_analysis", cost_usd=0.05, total_tokens=1000),
        _stage("summary", cost_usd=0.02, total_tokens=500),
    )
    rollup = summarize_band("small", [run])
    assert rollup["cost_per_run_usd"]["p50"] == pytest.approx(0.07)
    assert rollup["tokens_per_run"]["p50"] == pytest.approx(1500)


def test_baseline_groups_by_band_and_stage():
    baseline = build_baseline([
        _run(_stage("root_cause_analysis", result_data={"analysed": 0})),
        _run(_stage("root_cause_analysis", result_data={"analysed": 60})),
    ])
    assert baseline["bands"]["green"]["runs"] == 1
    assert baseline["bands"]["large"]["runs"] == 1
    assert baseline["stages"]["root_cause_analysis"]["observations"] == 2
    assert baseline["workflow_types"] == {"deep": 2}


# ── Grounding: claim evidence (F-16) and narrative citations (F-3) ───────────


def _claim(claim_id: str, kind: str = "fact", evidence=None):
    return {"claim_id": claim_id, "kind": kind, "evidence": evidence or []}


_SHARED = [{"type": "decision_evidence", "id": "sha-1"},
           {"type": "metric", "id": "metric_snapshot"}]


def test_shared_evidence_bundle_is_detected():
    """The F-16 shape: every claim stamped with one identical evidence list.

    Coverage looks perfect — 100% of claims 'have evidence' — while no claim
    carries evidence selected for it. The metric has to separate those, or the
    fix for F-16 would show no movement.
    """
    report = {"claims": [
        _claim("fact.metrics", evidence=list(_SHARED)),
        _claim("inference.release", "inference", evidence=list(_SHARED)),
        _claim("unknown.specialists", "unknown", evidence=list(_SHARED)),
    ]}
    rollup = summarize_report_grounding([report])

    assert rollup["claims_total"] == 3
    assert rollup["claims_with_evidence_rate"] == 1.0   # looks perfect...
    assert rollup["shared_evidence_bundle_rate"] == 1.0  # ...and is bundle-level
    assert rollup["claims_by_kind"] == {"fact": 1, "inference": 1, "unknown": 1}


def test_claim_specific_evidence_is_not_flagged_as_shared():
    report = {"claims": [
        _claim("a", evidence=[{"type": "artifact", "id": "art-1"}]),
        _claim("b", evidence=[{"type": "artifact", "id": "art-2"}]),
    ]}
    assert summarize_report_grounding([report])["shared_evidence_bundle_rate"] == 0.0


def test_single_claim_reports_are_excluded_from_the_shared_denominator():
    """One claim has nothing to share WITH; counting it would flatter the rate."""
    rollup = summarize_report_grounding([{"claims": [_claim("only", evidence=_SHARED)]}])
    assert rollup["multi_claim_reports"] == 0
    assert rollup["shared_evidence_bundle_rate"] is None


def test_claims_without_evidence_are_counted():
    rollup = summarize_report_grounding([{"claims": [
        _claim("a", evidence=_SHARED), _claim("b"),
    ]}])
    assert rollup["claims_with_evidence"] == 1
    assert rollup["claims_with_evidence_rate"] == 0.5


def test_narrative_citation_rate_is_measured():
    """The F-3 shape: paraphrased layers carry nothing."""
    cited = {"layer3_evidence_pack": {"citations": [{"source": "stacktrace"}]},
             "layer1_executive_summary": "Two suites failed."}
    uncited = {"layer3_evidence_pack": {"citations": []},
               "layer1_executive_summary": "Two suites failed."}
    rollup = summarize_narrative_citations([cited, uncited, uncited])

    assert rollup["summaries"] == 3
    assert rollup["summaries_with_any_citation"] == 1
    assert rollup["citation_rate"] == pytest.approx(0.3333, abs=1e-4)
    assert rollup["citations_total"] == 1


def test_uncitable_layers_are_reported_structurally():
    """Which layers CAN be cited is a property of the code, not of a corpus —
    so it is reported even for an empty one, where a rate would be meaningless.

    Since F-3 the three claim-bearing layers carry evidence_ids; layer 1 is free
    prose and stays uncitable by design.
    """
    rollup = summarize_narrative_citations([])
    assert rollup["citable_layers"] == [
        "layer2_incident_view", "layer3_evidence_pack", "layer4_action_plan",
    ]
    assert rollup["uncitable_layers"] == ["layer1_executive_summary"]


def test_fabricated_ids_are_reported_separately_from_scarce_citations():
    """A model inventing ids is a different problem from citing little."""
    honest = {"layer3_evidence_pack": {"citations": [{"ev_id": "E1"}]},
              "citation_coverage": {"ids_unresolved": 0}}
    fabricating = {"layer3_evidence_pack": {"citations": [{"ev_id": "E1"}]},
                   "citation_coverage": {"ids_unresolved": 3}}
    rollup = summarize_narrative_citations([honest, fabricating])

    assert rollup["citation_rate"] == 1.0          # both cite something...
    assert rollup["ids_unresolved"] == 3           # ...but one invented ids
    assert rollup["summaries_with_fabricated_ids"] == 1
    assert rollup["fabrication_rate"] == 0.5


@pytest.mark.parametrize("garbage", [None, "report", 7, {"claims": "nope"}, {"claims": [None]}])
def test_grounding_never_raises_on_malformed_input(garbage):
    assert summarize_report_grounding([garbage])["reports"] >= 0
    assert summarize_narrative_citations([garbage])["summaries"] >= 0


# ── Temporal structure: an incident must not read as a steady state ─────────


def _run_on(day: int, *, degraded: bool):
    """A run started on 2026-08-<day>, optionally with a failed stage."""
    started = datetime(2026, 8, day, 12, 0, 0, tzinfo=timezone.utc)
    stage = {
        "stage_name": "summary",
        "status": "failed" if degraded else "completed",
        "started_at": started,
        "completed_at": started + timedelta(seconds=2),
    }
    return {
        "pipeline_run_id": f"r{day}", "workflow_type": "deep", "status": "completed",
        "started_at": started, "completed_at": started + timedelta(seconds=10),
        "stages": [stage],
    }


def test_a_one_day_incident_is_named_not_averaged_away():
    """The failure this exists to prevent.

    The first real baseline reported "86% of runs degraded" when 747 of 765
    failures had happened in a single hour three weeks earlier. Over a long
    window an incident is arithmetically identical to a chronic condition.
    """
    runs = [_run_on(8, degraded=True) for _ in range(40)]
    runs += [_run_on(d, degraded=False) for d in range(9, 22)]
    temporal = summarize_temporal(runs)

    assert temporal["degradation_is_concentrated"] is True
    assert temporal["peak_degraded_day"] == "2026-08-08"
    assert temporal["peak_day_share_of_degraded"] == 1.0
    # The number a reader should act on: near zero once the bad day is set aside.
    assert temporal["degraded_rate_excluding_peak_day"] == 0.0
    assert "2026-08-08" in temporal["concentration_note"]


def test_chronic_degradation_is_not_flagged_as_concentrated():
    """Spread-out failures are a real ongoing problem and must not be excused."""
    runs = [_run_on(d, degraded=True) for d in range(8, 20)]
    runs += [_run_on(d, degraded=False) for d in range(8, 20)]
    temporal = summarize_temporal(runs)

    assert temporal["degradation_is_concentrated"] is False
    assert temporal["concentration_note"] is None
    assert temporal["degraded_rate_excluding_peak_day"] > 0.4


def test_a_single_day_corpus_is_never_called_concentrated():
    """Trivially 100% concentrated, and therefore says nothing."""
    runs = [_run_on(8, degraded=True) for _ in range(10)]
    temporal = summarize_temporal(runs)

    assert temporal["days_observed"] == 1
    assert temporal["degradation_is_concentrated"] is False


def test_a_clean_corpus_reports_no_peak():
    temporal = summarize_temporal([_run_on(d, degraded=False) for d in range(8, 15)])
    assert temporal["degraded_total"] == 0
    assert temporal["peak_degraded_day"] is None
    assert temporal["degradation_is_concentrated"] is False


def test_undated_runs_are_counted_not_dropped_silently():
    runs = [_run_on(8, degraded=True), {"stages": [], "started_at": "yesterday"}]
    temporal = summarize_temporal(runs)
    assert temporal["runs_without_a_date"] == 1


def test_temporal_block_is_part_of_the_baseline():
    baseline = build_baseline([_run_on(8, degraded=True), _run_on(9, degraded=False)])
    assert "temporal" in baseline
    assert baseline["temporal"]["first_day"] == "2026-08-08"
    assert baseline["temporal"]["last_day"] == "2026-08-09"


@pytest.mark.parametrize("garbage", [None, "run", 7, {"started_at": object()}])
def test_temporal_never_raises(garbage):
    assert isinstance(summarize_temporal([garbage]), dict)
