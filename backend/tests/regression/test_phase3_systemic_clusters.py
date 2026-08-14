"""Phase 3: a cluster must be real, or absent.

Roadmap Phase 3 (``architecture/TEST_INTELLIGENCE_PLAN.md``).

The dominant failure mode here is the opposite of missing a pattern: it is
*manufacturing* one. In the source study only 10 of 22 projects containing flaky
tests contained any co-failure cluster at all, so an empty result is the common
correct answer. A weak grouping presented as a cluster sends an engineer hunting
something that is not there — strictly worse than saying nothing.

So every test below pins either *"this genuinely co-failing group is found"* or
*"this non-pattern is NOT reported"*.
"""
from __future__ import annotations

import pytest

from app.services.systemic_cluster_service import (
    CAUSE_EXTERNAL_DEPENDENCY,
    CAUSE_FILESYSTEM,
    CAUSE_NETWORKING,
    CAUSE_TIMEOUT,
    CAUSE_UNKNOWN,
    MIN_CLUSTER_SIZE,
    MIN_FAILURE_RUNS,
    MIN_SILHOUETTE,
    build_clusters,
    classify_cause,
    jaccard_distance,
)


# ── The distance metric ──────────────────────────────────────────────────────

def test_identical_failure_sets_are_distance_zero():
    assert jaccard_distance({"r1", "r2"}, {"r1", "r2"}) == 0.0


def test_disjoint_failure_sets_are_maximally_distant():
    assert jaccard_distance({"r1"}, {"r2"}) == 1.0


def test_two_empty_sets_are_distant_not_identical():
    """THE trap in this metric. Jaccard of two empty sets is 0/0; treating that
    as 'identical' would cluster every healthy test in the project into one
    enormous fictional group."""
    assert jaccard_distance(set(), set()) == 1.0
    assert jaccard_distance({"r1"}, set()) == 1.0


def test_partial_overlap_is_between():
    d = jaccard_distance({"r1", "r2", "r3"}, {"r2", "r3", "r4"})
    assert 0.0 < d < 1.0


# ── Finding real clusters ────────────────────────────────────────────────────

def test_tests_that_always_fail_together_are_clustered():
    clusters = build_clusters({
        "a": {"r1", "r2", "r3"},
        "b": {"r1", "r2", "r3"},
        "c": {"r1", "r2", "r3"},
        # An unrelated test failing in completely different runs.
        "z": {"r9", "r8", "r7"},
    })
    assert len(clusters) >= 1
    biggest = clusters[0]
    assert set(biggest.members) == {"a", "b", "c"}
    assert "z" not in biggest.members


def test_cluster_reports_the_runs_its_members_actually_co_failed_in():
    """The observation count behind the claim. A cluster asserted without one
    is unfalsifiable."""
    clusters = build_clusters({
        "a": {"r1", "r2", "r3"},
        "b": {"r1", "r2", "r3"},
        "z": {"r7", "r8", "r9"},
    })
    assert clusters[0].co_failure_runs == 3


def test_cluster_keys_are_stable_and_ordered_by_size():
    clusters = build_clusters({
        "a": {"r1", "r2"}, "b": {"r1", "r2"}, "c": {"r1", "r2"},
        "x": {"r5", "r6"}, "y": {"r5", "r6"},
    })
    assert [c.cluster_key for c in clusters] == [
        f"sfc_{i + 1:03d}" for i in range(len(clusters))
    ]
    sizes = [c.size for c in clusters]
    assert sizes == sorted(sizes, reverse=True)


# ── Refusing to manufacture clusters ─────────────────────────────────────────

def test_unrelated_failures_produce_no_clusters():
    """The common case. Tests failing in disjoint runs share nothing."""
    assert build_clusters({
        "a": {"r1", "r2"},
        "b": {"r3", "r4"},
        "c": {"r5", "r6"},
    }) == []


def test_a_single_test_is_never_a_cluster():
    assert build_clusters({"only": {"r1", "r2", "r3"}}) == []


def test_empty_input_is_empty_output_not_an_error():
    assert build_clusters({}) == []
    assert build_clusters(None) == []


def test_a_test_that_failed_once_cannot_evidence_co_failure():
    """One shared failure is a coincidence, not a pattern."""
    assert build_clusters({
        "a": {"r1"},
        "b": {"r1"},
    }) == []
    assert MIN_FAILURE_RUNS >= 2


def test_weak_cohesion_is_discarded_rather_than_reported():
    """A cluster below the silhouette bar must vanish, not be shown with a low
    score. Presenting it invites an investigation into a non-pattern."""
    clusters = build_clusters(
        {"a": {"r1", "r2"}, "b": {"r1", "r2"}, "c": {"r1", "r2"}},
        min_silhouette=1.01,   # unreachable — nothing may qualify
    )
    assert clusters == []


def test_every_returned_cluster_meets_the_cohesion_bar():
    clusters = build_clusters({
        "a": {"r1", "r2", "r3"}, "b": {"r1", "r2", "r3"},
        "x": {"r7", "r8", "r9"}, "y": {"r7", "r8", "r9"},
    })
    assert clusters, "this input genuinely co-fails and should cluster"
    for cluster in clusters:
        assert cluster.cohesion >= MIN_SILHOUETTE
        assert cluster.size >= MIN_CLUSTER_SIZE


def test_build_clusters_never_raises_on_malformed_input():
    junk = {None: {"r1"}, "": {"r1"}, "ok": None, "fine": {"r1", "r2"}}
    assert isinstance(build_clusters(junk), list)


# ── Naming the cause ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("java.net.UnknownHostException: api.example.com", CAUSE_NETWORKING),
    ("Error: connect ECONNREFUSED 127.0.0.1:5432", CAUSE_NETWORKING),
    ("HTTP 503 Service Unavailable from upstream", CAUSE_EXTERNAL_DEPENDENCY),
    ("FileNotFoundError: no such file or directory", CAUSE_FILESYSTEM),
    ("Timed out after 30000ms waiting for selector", CAUSE_TIMEOUT),
])
def test_cause_families_are_named_from_the_failure_text(text, expected):
    assert classify_cause([text]) == expected


def test_unrecognised_text_is_unknown_not_a_guess():
    """A cluster is still actionable without a named cause. Inventing one is
    worse than admitting we cannot tell."""
    assert classify_cause(["assert 1 == 2"]) == CAUSE_UNKNOWN
    assert classify_cause([]) == CAUSE_UNKNOWN
    assert classify_cause([None, ""]) == CAUSE_UNKNOWN


def test_networking_outranks_the_generic_timeout_marker():
    """A socket timeout is a networking failure, not a bare timeout — the more
    diagnostic family must win."""
    assert classify_cause(["java.net.SocketTimeoutException: connect timed out"]) == (
        CAUSE_NETWORKING
    )


# ── The entity boundary ──────────────────────────────────────────────────────

def test_systemic_clusters_do_not_reuse_the_per_run_failure_cluster_table():
    """Guard the DESIGN, not just the code. ``FailureCluster`` is keyed
    test_run_id and built by semantic message similarity — within-run and by
    what failures *say*. Systemic clustering is across runs and by literal
    co-failure. Collapsing them would silently destroy one of the two."""
    from app.models.postgres import FailureCluster, SystemicFlakeCluster

    assert SystemicFlakeCluster.__tablename__ == "systemic_flake_cluster"
    assert FailureCluster.__tablename__ == "failure_clusters"
    assert hasattr(FailureCluster, "test_run_id"), "per-run cluster is run-scoped"
    assert not hasattr(SystemicFlakeCluster, "test_run_id"), (
        "a systemic cluster spans runs; binding it to one would destroy the pattern"
    )
    assert hasattr(SystemicFlakeCluster, "project_id")


def test_cluster_membership_is_by_fingerprint_not_per_run_row():
    """Membership is about the TEST across runs. Keying it to a per-run
    test_case_id would make a cluster expire with a single run."""
    from app.models.postgres import SystemicFlakeClusterMember

    assert hasattr(SystemicFlakeClusterMember, "test_fingerprint")
    assert not hasattr(SystemicFlakeClusterMember, "test_case_id")


def test_endpoint_states_that_no_clusters_is_a_normal_answer():
    """Most projects have none. An empty list must not read as a bug."""
    import inspect

    from app.routers.analytics import systemic_clusters

    source = inspect.getsource(systemic_clusters)
    assert "empty_is_normal" in source


# ── Review finding: the algorithm must stay bounded ──────────────────────────

def test_clustering_input_is_capped():
    """Agglomerative average-linkage recomputes every pairwise cluster distance
    per merge, so cost grows ~cubically once merges are admissible. Measured on
    this implementation: 200 co-failing fingerprints 0.4s, 400 → 6s, 800 → 85s.

    The blow-up case is exactly what this feature targets — a wide outage makes
    hundreds of tests co-fail, every pair becomes mergeable, and the nightly
    sweep would stall on the project that most needed the answer.
    """
    from app.services.systemic_cluster_service import MAX_CLUSTERED_FINGERPRINTS

    shared = {f"r{r}" for r in range(10)}
    oversized = {f"fp{i}": set(shared) for i in range(MAX_CLUSTERED_FINGERPRINTS * 3)}
    clusters = build_clusters(oversized)
    total_members = sum(c.size for c in clusters)
    assert total_members <= MAX_CLUSTERED_FINGERPRINTS


def test_the_cap_keeps_the_most_failing_tests():
    """A co-failure pattern lives among tests that actually fail often, so the
    long tail is what should be dropped — not the signal."""
    noisy = {f"noisy{i}": {f"r{r}" for r in range(30)} for i in range(3)}
    quiet = {f"quiet{i}": {"r90", "r91"} for i in range(20)}
    clusters = build_clusters({**noisy, **quiet}, max_fingerprints=3)
    kept = {m for c in clusters for m in c.members}
    assert not any(m.startswith("quiet") for m in kept)


def test_truncation_is_logged_not_silent():
    """Silently clustering a subset would report a partial pattern as whole."""
    import inspect

    from app.services.systemic_cluster_service import build_clusters as fn

    assert "systemic_cluster_input_truncated" in inspect.getsource(fn)
