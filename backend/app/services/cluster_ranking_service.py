"""
Cluster Ranking Service — ranks failure clusters by impact for triage prioritization.

Ranking formula:
  impact_score = (
    size_weight * normalized_size +
    severity_weight * severity_score +
    regression_weight * regression_bonus +
    confidence_weight * avg_confidence
  )

Where:
  - normalized_size: cluster.size / max_cluster_size (bigger clusters = more impact)
  - severity_score: 1.0 for CRITICAL, 0.75 for HIGH, 0.5 for MEDIUM, 0.25 for LOW
  - regression_bonus: 1.0 for new_regression, 0.5 for product_bug, 0.2 for others
  - avg_confidence: average AI confidence of member analyses / 100
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("services.cluster_ranking")

# Weights for ranking formula
_SIZE_WEIGHT = 0.30
_SEVERITY_WEIGHT = 0.25
_REGRESSION_WEIGHT = 0.25
_CONFIDENCE_WEIGHT = 0.20

_SEVERITY_SCORES = {
    "CRITICAL": 1.0,
    "HIGH": 0.75,
    "MEDIUM": 0.5,
    "LOW": 0.25,
}

_REGRESSION_SCORES = {
    "new_regression": 1.0,
    "product_bug": 0.75,
    "environmental": 0.5,
    "known_flaky": 0.2,
    "infrastructure": 0.4,
    "unclassified": 0.3,
}


def rank_clusters(
    clusters: list[dict],
    analyses_by_test: Optional[dict] = None,
) -> list[dict]:
    """
    Rank clusters by impact score (descending).

    Each cluster dict should have:
      - cluster_id, label, size
      - criticality_level (optional)
      - regression_classification (optional, from cluster or run-level)
      - member_test_ids (optional, for confidence lookup)

    Returns the same list with 'impact_rank' and 'impact_score' added.
    """
    if not clusters:
        return []

    analyses_by_test = analyses_by_test or {}
    max_size = max(c.get("size", 1) for c in clusters) or 1

    scored = []
    for cluster in clusters:
        size = cluster.get("size", 1)
        criticality = cluster.get("criticality_level", "MEDIUM")
        regression = cluster.get("regression_classification") or "unclassified"

        # Compute average confidence from member analyses
        member_ids = cluster.get("member_test_ids", [])
        confidences = [
            analyses_by_test.get(mid, {}).get("confidence_score", 50)
            for mid in member_ids
            if mid in analyses_by_test
        ]
        avg_conf = sum(confidences) / len(confidences) / 100 if confidences else 0.5

        impact = (
            _SIZE_WEIGHT * (size / max_size) +
            _SEVERITY_WEIGHT * _SEVERITY_SCORES.get(criticality, 0.5) +
            _REGRESSION_WEIGHT * _REGRESSION_SCORES.get(regression, 0.3) +
            _CONFIDENCE_WEIGHT * avg_conf
        )

        scored.append({
            **cluster,
            "impact_score": round(impact, 3),
        })

    # Sort descending by impact
    scored.sort(key=lambda c: c["impact_score"], reverse=True)

    # Add rank
    for i, c in enumerate(scored):
        c["impact_rank"] = i + 1

    return scored


def compute_cluster_impact_score(
    size: int,
    max_size: int,
    criticality: str = "MEDIUM",
    regression_classification: str = "unclassified",
    avg_confidence: float = 0.5,
) -> float:
    """Compute impact score for a single cluster (utility for tests)."""
    return round(
        _SIZE_WEIGHT * (size / max(max_size, 1)) +
        _SEVERITY_WEIGHT * _SEVERITY_SCORES.get(criticality, 0.5) +
        _REGRESSION_WEIGHT * _REGRESSION_SCORES.get(regression_classification, 0.3) +
        _CONFIDENCE_WEIGHT * avg_confidence,
        3,
    )
