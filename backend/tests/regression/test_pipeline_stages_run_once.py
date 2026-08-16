"""Regression: no pipeline node may be reachable at two different depths.

Bug (homelab, 2026-08-16, AI-010): the deep pipeline ran `summary` — and every
stage after it — **twice**, because three branches fanned into `summary` at
unequal path lengths::

    anomaly_detection          -> summary      1 hop from ingestion
    root_cause_analysis        -> summary      1 hop
    failure_clustering -> dispatch -> join -> summary   3 hops

LangGraph executes a node once per superstep in which any predecessor
completed. Two branches landed in the first superstep and the clustering branch
in a later one, so `summary` fired twice and dragged the whole specialist chain
with it.

Observed consequences in one live run:

* `route_after_summary_deep` logged twice (summary's own outgoing edge),
* every skipped specialist stage recorded twice — 12 entries for 6 stages,
* `decision_report_verification` logged BOTH ``passed`` and ``failed``, 464 ms
  apart, and the failing pass won. Either an unverified report would have
  shipped, or a good one was withheld.

It stayed invisible because `completed_stages` uses a de-duplicating reducer
while `skipped_stages` does not (`agents/state.py`) — the repetition was hidden
on the list people read and visible only on the one they don't.

**Why this is a topology test.** Nothing about the graph *fails*; every stage
completes. A behavioural test asserting "the pipeline succeeds" passes happily
while every node runs twice. The defect lives in the shape of the graph, so
that is what gets asserted.
"""
from __future__ import annotations

import pytest


def _edges(graph) -> tuple[set[tuple[str, str]], dict]:
    """Static edges and conditional branch targets from a built StateGraph."""
    static = {(str(a), str(b)) for a, b in getattr(graph, "edges", set())}
    return static, getattr(graph, "branches", {})


def _all_targets(graph) -> set[tuple[str, str]]:
    """Every (src, dst) the graph can traverse, conditional targets included."""
    static, branches = _edges(graph)
    edges = set(static)
    for src, branch_map in (branches or {}).items():
        for branch in branch_map.values():
            ends = getattr(branch, "ends", None) or {}
            for dst in ends.values():
                edges.add((str(src), str(dst)))
    return edges


def _scenarios(graph) -> list[set[tuple[str, str]]]:
    """Edge sets for each combination of conditional choices.

    Conditional branches are MUTUALLY EXCLUSIVE — exactly one target fires at
    runtime — so they must not be treated as simultaneously active. Only
    unconditional `add_edge` fan-out runs in parallel, and only that can make a
    node reconverge at two depths.

    (The first version of this guard ignored the distinction and flagged the
    fixed graph: `failure_clustering` looked reachable at depths 3 and 4 via the
    mutually-exclusive triage / direct routes. An over-strict guard that fails
    on correct code is as useless as one that passes on broken code.)
    """
    import itertools

    static, branches = _edges(graph)
    choice_sets = []
    for src, branch_map in (branches or {}).items():
        for branch in branch_map.values():
            ends = getattr(branch, "ends", None) or {}
            targets = [(str(src), str(dst)) for dst in ends.values()]
            if targets:
                choice_sets.append(targets)
    if not choice_sets:
        return [set(static)]
    return [set(static) | set(combo) for combo in itertools.product(*choice_sets)]


def _depths(edges: set[tuple[str, str]], start: str) -> dict[str, set[int]]:
    """Every path length at which each node is reachable from ``start``.

    Within one scenario (conditional choices fixed), a node reachable at more
    than one depth is one LangGraph can schedule in more than one superstep.
    """
    depths: dict[str, set[int]] = {start: {0}}
    frontier = [(start, 0)]
    seen: set[tuple[str, int]] = {(start, 0)}
    while frontier:
        node, d = frontier.pop()
        if d > 40:  # cycle guard; these graphs are acyclic by design
            continue
        for src, dst in edges:
            if src != node:
                continue
            depths.setdefault(dst, set()).add(d + 1)
            if (dst, d + 1) not in seen:
                seen.add((dst, d + 1))
                frontier.append((dst, d + 1))
    return depths


def _build(kind: str):
    from app.agents import workflow

    return {
        "offline": workflow._build_offline_graph,
        "deep": workflow._build_deep_graph,
        "live": workflow._build_live_graph,
    }[kind]()


@pytest.mark.parametrize("kind", ["offline", "deep", "live"])
def test_the_graph_was_actually_built(kind):
    """Every assertion below reads these edges. An empty graph passes vacuously."""
    edges = _all_targets(_build(kind))
    assert len(edges) >= 3, f"{kind} graph produced no usable edges: {edges}"


@pytest.mark.parametrize("kind", ["offline", "deep", "live"])
def test_no_node_is_reachable_at_two_different_depths(kind):
    """The regression itself, stated as the property that broke.

    Two depths means two supersteps means the node — and everything downstream
    of it — executes twice.
    """
    graph = _build(kind)
    for scenario in _scenarios(graph):
        depths = _depths(scenario, "ingestion")
        multi = {
            node: sorted(ds)
            for node, ds in depths.items()
            if node != "__end__" and len(ds) > 1
        }
        assert not multi, (
            f"{kind} graph: with one fixed set of conditional choices, these "
            f"nodes are reachable at more than one path length from ingestion. "
            f"LangGraph schedules them in multiple supersteps, so they and "
            f"everything downstream run more than once: {multi}"
        )


def test_the_cluster_branch_does_not_fan_into_summary():
    """The specific edge that caused it. Pinned by name as well as by the
    generic property above, because this one has a tempting-looking reason to
    exist (clustering finishing before the summary is written) — and
    SummaryAgent does not read cluster output at all: it passes a literal
    cluster_count=0."""
    edges = _all_targets(_build("deep"))
    assert ("cluster_investigation_join", "summary") not in edges, (
        "the clustering branch fans back into summary. It reaches summary three "
        "hops from ingestion while the analysis branches reach it in one, so "
        "summary runs twice"
    )


def test_the_green_path_does_not_shortcut_to_summary():
    """The second instance of the same defect, on the all-green path.

    `add_edge(ingestion, root_cause_analysis)` fires unconditionally, so a
    conditional ingestion->summary edge arrives a superstep earlier and makes
    summary run twice on runs with no failures. analysis_node early-returns when
    there is nothing to analyse, so routing green runs through it is free.
    """
    for kind in ("offline", "deep"):
        edges = _all_targets(_build(kind))
        assert ("ingestion", "summary") not in edges, (
            f"{kind} graph routes green runs straight from ingestion to summary "
            f"while root_cause_analysis also feeds summary unconditionally — "
            f"summary would run twice on an all-green run"
        )


def test_every_stage_still_reachable_in_the_deep_pipeline():
    """The fix re-sequenced clustering; it must not have orphaned anything.

    A graph with no duplicate-depth nodes is trivially achievable by deleting
    edges, so assert coverage too.
    """
    from app.agents.workflow import _DEEP_PIPELINE_STAGES

    edges = _all_targets(_build("deep"))
    reachable = set(_depths(edges, "ingestion"))
    missing = [s for s in _DEEP_PIPELINE_STAGES if s not in reachable]
    assert not missing, (
        f"deep pipeline stages unreachable from ingestion after the "
        f"re-sequencing: {missing}"
    )
