"""
Criticality Service — shared risk scoring model.

Single source of truth for:
  - 7-dimension weights (driven by settings for runtime configurability)
  - Composite risk computation
  - Recommendation thresholds
  - Cluster-level proportional contribution scoring
  - Scoring model metadata (served via GET /api/v1/scoring-model)

Replaces the inline _WEIGHTS + _compute_dimension_scores in release_risk_agent.py.
"""
from __future__ import annotations

from app.models.postgres import FailureCategory

SCORE_MODEL_VERSION = 1

# Thresholds for composite risk → recommendation
_GO_THRESHOLD = 20    # composite_risk < 20  → GO
_NO_GO_THRESHOLD = 55  # composite_risk ≥ 55 → NO_GO


def _weights() -> dict[str, float]:
    """Return 7-dimension weights driven by settings (runtime-configurable)."""
    # Late import to avoid circular deps at module load time
    from app.core.config import settings  # noqa: PLC0415

    return {
        "user_impact":       settings.RISK_WEIGHT_USER_IMPACT,
        "env_sensitivity":   settings.RISK_WEIGHT_ENV_SENSITIVITY,
        "reproducibility":   settings.RISK_WEIGHT_REPRODUCIBILITY,
        "regression_likely": settings.RISK_WEIGHT_REGRESSION_LIKELY,
        "hist_recurrence":   settings.RISK_WEIGHT_HIST_RECURRENCE,
        "blast_radius":      settings.RISK_WEIGHT_BLAST_RADIUS,
        "diagnosis_conf":    settings.RISK_WEIGHT_DIAGNOSIS_CONF,
    }


# Per-dimension human-readable description — served by GET /api/v1/scoring-model
# and consumed by the frontend CriticalityMatrix "Why this score?" tooltip.
_DIMENSION_DESCRIPTIONS: dict[str, str] = {
    "user_impact": (
        "Proportion of PRODUCT_BUG failures multiplied by open defect pressure. "
        "High when core user-visible features are broken."
    ),
    "env_sensitivity": (
        "Infrastructure failure rate plus severe anomaly count. "
        "High when the environment itself is unstable rather than the application code."
    ),
    "reproducibility": (
        "Fraction of non-flaky failures multiplied by (1 − pass_rate). "
        "High when failures are reliably reproduced, not intermittent."
    ),
    "regression_likely": (
        "New regressions detected this run vs recent baselines times regression severity. "
        "High when fresh code changes introduced failures."
    ),
    "hist_recurrence": (
        "Ratio of low-confidence analyses — tests that have been seen failing before. "
        "High when known issues are recurring without resolution."
    ),
    "blast_radius": (
        "Clusters spanning more than 3 tests plus overall failure breadth. "
        "High when failures spread across many services or test suites."
    ),
    "diagnosis_conf": (
        "Inverted average AI confidence score. "
        "High when the AI is uncertain about root-cause — signals manual review may be needed."
    ),
}


def compute_dimension_scores(
    analyses: dict,
    anomalies: list[dict],
    pass_rate: float,
    is_regression: bool,
    regression_tests: list[str],
    failure_clusters: list[dict],
    open_defects: int,
) -> dict[str, float]:
    """
    Compute all 7 risk dimensions from run-level pipeline data.

    Each dimension is 0–100 (raw, before weighting).
    """
    total_analysed = len(analyses) or 1

    product_bugs = [
        a for a in analyses.values()
        if a.get("failure_category") == FailureCategory.PRODUCT_BUG and not a.get("is_flaky")
    ]
    infra_failures = [
        a for a in analyses.values()
        if a.get("failure_category") == FailureCategory.INFRASTRUCTURE
    ]
    flaky_count = sum(1 for a in analyses.values() if a.get("is_flaky"))
    non_flaky = total_analysed - flaky_count

    # 1. User impact: product bug rate + open defect pressure
    user_impact = min(100.0, (len(product_bugs) / total_analysed) * 80 + open_defects * 2)

    # 2. Env sensitivity: infra failures + severe anomaly count
    severe_anomalies = sum(1 for a in anomalies if a.get("severity") == "HIGH")
    env_sensitivity = min(100.0, (len(infra_failures) / total_analysed) * 60 + severe_anomalies * 10)

    # 3. Reproducibility: non-flaky rate × failure depth
    reproducibility = min(100.0, (non_flaky / total_analysed) * (100 - pass_rate))

    # 4. Regression likelihood: new regressions this run
    regression_likely = min(100.0, len(regression_tests) * 15 + (50 if is_regression else 0))

    # 5. Historical recurrence: low-confidence analyses = previously seen failures
    low_conf = sum(1 for a in analyses.values() if a.get("confidence_score", 100) < 40)
    hist_recurrence = min(100.0, (low_conf / total_analysed) * 80)

    # 6. Blast radius: clusters spanning multiple test cases
    multi_suite_clusters = sum(1 for c in failure_clusters if c.get("size", 1) > 3)
    blast_radius = min(100.0, multi_suite_clusters * 20 + (1 - pass_rate / 100) * 40)

    # 7. Diagnosis confidence (inverted): low avg confidence = uncertain risk
    avg_conf = (
        sum(a.get("confidence_score", 0) for a in analyses.values()) / total_analysed
        if analyses else 0
    )
    diagnosis_conf = max(0.0, 100.0 - avg_conf)

    return {
        "user_impact":       round(user_impact, 1),
        "env_sensitivity":   round(env_sensitivity, 1),
        "reproducibility":   round(reproducibility, 1),
        "regression_likely": round(regression_likely, 1),
        "hist_recurrence":   round(hist_recurrence, 1),
        "blast_radius":      round(blast_radius, 1),
        "diagnosis_conf":    round(diagnosis_conf, 1),
    }


def compute_composite(
    dim_scores: dict[str, float],
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted sum of dimension scores, clamped to [0, 100].

    When *weights* is None the global defaults from config are used.
    Policy-based release gates pass custom weights from the active policy.
    """
    w = weights or _weights()
    composite = sum(dim_scores.get(k, 0.0) * w.get(k, 0.0) for k in w)
    return round(max(0.0, min(100.0, composite)), 1)


def score_to_recommendation(
    composite: float,
    pass_rate: float,
    threshold: float,
    go_threshold: float = _GO_THRESHOLD,
    no_go_threshold: float = _NO_GO_THRESHOLD,
    hard_floor_factor: float = 0.7,
) -> str:
    """Map composite score + pass-rate to GO / CONDITIONAL_GO / NO_GO.

    All threshold parameters accept policy-driven overrides for ENT-02.
    Existing callers that pass only positional args see identical behavior.
    """
    if pass_rate < threshold * hard_floor_factor:
        return "NO_GO"
    if composite >= no_go_threshold:
        return "NO_GO"
    if composite >= go_threshold:
        return "CONDITIONAL_GO"
    return "GO"


def score_cluster(
    cluster: dict,
    all_dim_scores: dict[str, float],
    total_analyses: int,
    member_count: int,
) -> dict[str, float]:
    """
    Return each dimension's proportional contribution for a single cluster.

    Uses the full run-level dimension scores and scales them by the fraction of
    the cluster's members vs all analysed tests.  This gives each cluster a
    "share" of the overall risk, proportional to how many failing tests it owns.

    Returns: {dim_name: scaled_score (0-100)}
    """
    if not all_dim_scores or total_analyses == 0 or member_count == 0:
        return {}

    fraction = member_count / total_analyses
    return {
        dim: round(score * fraction, 1)
        for dim, score in all_dim_scores.items()
    }


def get_scoring_model_info() -> dict:
    """
    Return the full scoring model metadata.

    Served by GET /api/v1/scoring-model.
    Consumed by the frontend CriticalityMatrix "Why this score?" tooltip.
    """
    weights = _weights()
    return {
        "version": SCORE_MODEL_VERSION,
        "go_threshold": _GO_THRESHOLD,
        "no_go_threshold": _NO_GO_THRESHOLD,
        "dimensions": [
            {
                "name": dim,
                "weight": weight,
                "description": _DIMENSION_DESCRIPTIONS.get(dim, ""),
            }
            for dim, weight in weights.items()
        ],
    }
