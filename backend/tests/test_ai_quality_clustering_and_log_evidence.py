"""AI-quality fixes — clustering neighbour cap (#2) + log evidence grading (#5).

#2 embed_and_cluster: the greedy neighbour lookup was capped at n_results=5, so
a single root cause hitting many tests could only ever link 4 others per seed
and fragmented into many clusters (defeating O(n)->O(k) and multiplying
downstream per-cluster LLM cost). The clustering loop is extracted into the pure
``_cluster_from_neighbours`` and the lookup cap raised to ``_MAX_NEIGHBOR_QUERY``.

#5 log_intelligence_agent: trace + anomaly evidence was emitted at a fixed
``medium``/80 regardless of the actual signal — even a "no anomaly detected"
null result. ``_grade_anomaly_evidence`` / ``_grade_trace_evidence`` now map
strength+contribution to the real spike ratio / error-chain depth.
"""
from __future__ import annotations

from app.tools.embed_and_cluster import (
    _MAX_NEIGHBOR_QUERY,
    _cluster_from_neighbours,
)
from app.agents.log_intelligence_agent import (
    _grade_anomaly_evidence,
    _grade_trace_evidence,
)


# ── #2 clustering neighbour cap ───────────────────────────────────────────

def _full_neighbours(ids, dist=0.1):
    """Every error is a near-neighbour of every other (one root cause)."""
    return [list(ids) for _ in ids], [[dist] * len(ids) for _ in ids]


def test_large_same_cause_set_collapses_into_one_cluster():
    """30 near-identical errors → ONE cluster when neighbours are complete."""
    ids = [f"e{i}" for i in range(30)]
    result_ids, result_dist = _full_neighbours(ids)
    clusters = _cluster_from_neighbours(ids, result_ids, result_dist)
    assert len(clusters) == 1
    assert len(clusters[0]) == 30


def test_truncated_neighbours_fragment_the_cluster():
    """The bug the cap caused: if each error only sees 5 neighbours, the same
    30-member root cause fragments into multiple clusters. This pins WHY the
    lookup size matters (and why the cap was raised)."""
    ids = [f"e{i}" for i in range(30)]
    # Each seed sees only the next 5 ids (a truncated neighbour window).
    result_ids = [[f"e{(i + k) % 30}" for k in range(5)] for i in range(30)]
    result_dist = [[0.1] * 5 for _ in range(30)]
    clusters = _cluster_from_neighbours(ids, result_ids, result_dist)
    assert len(clusters) > 1  # fragmented — not one clean cluster


def test_distinct_causes_stay_separate():
    """Two dissimilar groups (distance above threshold) stay in two clusters."""
    ids = ["a0", "a1", "b0", "b1"]
    # a* are close to each other and far from b*, and vice-versa.
    result_ids = [
        ["a0", "a1", "b0", "b1"],
        ["a1", "a0", "b0", "b1"],
        ["b0", "b1", "a0", "a1"],
        ["b1", "b0", "a0", "a1"],
    ]
    result_dist = [
        [0.0, 0.1, 0.9, 0.9],
        [0.0, 0.1, 0.9, 0.9],
        [0.0, 0.1, 0.9, 0.9],
        [0.0, 0.1, 0.9, 0.9],
    ]
    clusters = _cluster_from_neighbours(ids, result_ids, result_dist)
    assert len(clusters) == 2
    assert sorted(len(c) for c in clusters) == [2, 2]


def test_neighbour_cap_large_enough_to_avoid_realistic_fragmentation():
    assert _MAX_NEIGHBOR_QUERY >= 50


# ── #5 log evidence grading ───────────────────────────────────────────────

def test_no_anomaly_is_weak_not_medium():
    """A 'no anomaly detected' result is weak evidence (rules a cause OUT), not
    the old fixed medium/80."""
    strength, contribution = _grade_anomaly_evidence(
        {"anomaly_detected": False, "assessment": "normal"}
    )
    assert strength == "weak"
    assert contribution < 40


def test_severe_spike_is_strong():
    strength, contribution = _grade_anomaly_evidence(
        {"anomaly_detected": True, "levels": {"ERROR": {"ratio": 12.0}}}
    )
    assert strength == "strong"
    assert contribution >= 80


def test_moderate_spike_is_medium():
    strength, contribution = _grade_anomaly_evidence(
        {"anomaly_detected": True, "levels": {"ERROR": {"ratio": 4.0}}}
    )
    assert strength == "medium"
    assert 40 <= contribution < 80


def test_anomaly_grading_degrades_on_missing_fields():
    """Splunk-disabled / minimal payloads must not raise; they grade weak."""
    assert _grade_anomaly_evidence({}) == ("weak", 15)
    assert _grade_anomaly_evidence({"assessment": "disabled"}) == ("weak", 15)


def test_trace_with_error_chain_is_strong():
    strength, contribution = _grade_trace_evidence({
        "trace_steps": [
            {"level": "ERROR", "message": "x"},
            {"level": "ERROR", "message": "y"},
            {"level": "FATAL", "message": "z"},
        ],
    })
    assert strength == "strong"
    assert contribution >= 80


def test_trace_context_only_is_weak():
    strength, contribution = _grade_trace_evidence({
        "trace_steps": [{"level": "INFO", "message": "ok"}],
    })
    assert strength == "weak"
    assert contribution < 40


def test_trace_grading_degrades_on_empty():
    assert _grade_trace_evidence({}) == ("weak", 10)
