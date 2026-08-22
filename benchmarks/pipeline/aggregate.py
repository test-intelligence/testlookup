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
            degraded += 1
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


# Metrics the review asks for that this input genuinely cannot answer. Listed
# explicitly so the gap is visible in the artifact itself rather than being
# mistaken for a zero.
NOT_MEASURED: tuple[dict[str, str], ...] = (
    {
        "metric": "citation_coverage",
        "why": (
            "Citations live on the persisted summary document, not on "
            "agent_stage_results. Deriving them needs the report store, which "
            "this collector does not read yet."
        ),
        "blocked_on": "finding F-3 / requirement G.2",
    },
    {
        "metric": "unsupported_claim_rate",
        "why": "No component classifies a claim as supported today.",
        "blocked_on": "requirement G.3 (narrative critic)",
    },
    {
        "metric": "failure_category_macro_f1",
        "why": (
            "Needs labelled outcomes joined to analyses; ai_eval_service "
            "computes this on demand but nothing schedules it."
        ),
        "blocked_on": "requirement I.1 (nightly evaluation)",
    },
)


def build_baseline(runs: Iterable[Any], *, window: Optional[dict] = None) -> dict[str, Any]:
    """Aggregate normalized pipeline runs into one comparable baseline document.

    ``runs`` are dicts shaped by ``collect.py``. Anything that is not a dict is
    dropped rather than raising -- see the module docstring.
    """
    clean = [_as_dict(r) for r in runs if isinstance(r, dict)]

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
        "not_measured": [dict(item) for item in NOT_MEASURED],
    }
