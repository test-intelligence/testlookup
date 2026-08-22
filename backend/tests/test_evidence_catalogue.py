"""Tests for the server-owned evidence catalogue (F-3 / G.2).

The property that matters is not "citations appear" — it is that a citation
can only ever point at evidence the server put in front of the model. A model
id is a token to be matched, never an identifier to be trusted.
"""
from __future__ import annotations

import pytest

from app.services.evidence_catalogue import (
    CATALOGUE_LIMIT,
    build_evidence_catalogue,
    citable_layers,
    coverage_summary,
    ground_layer,
    render_catalogue,
    resolve_evidence_ids,
)


def _analysis(name: str, *excerpts: str, source: str = "stacktrace"):
    return {
        "test_name": name,
        "evidence_references": [
            {"source": source, "kind": "tool_observation", "excerpt": e}
            for e in excerpts
        ],
    }


# ── The catalogue is the server's, not the model's ───────────────────────────


def test_ids_are_assigned_by_the_server_in_stable_order():
    analyses = {
        "tc-b": _analysis("checkout", "NullPointerException at Checkout.java:42"),
        "tc-a": _analysis("payment", "upstream timeout after 30s"),
    }
    first = build_evidence_catalogue(analyses)
    again = build_evidence_catalogue(analyses)

    assert [e["ev_id"] for e in first] == ["E1", "E2"]
    # Layers are three separate LLM calls; an id that drifted between them
    # would silently mis-cite.
    assert first == again
    # Sorted by test id, so tc-a comes first regardless of dict order.
    assert first[0]["test_id"] == "tc-a"


def test_a_fabricated_id_resolves_to_nothing():
    catalogue = build_evidence_catalogue({"tc-1": _analysis("checkout", "boom")})
    citations, unresolved = resolve_evidence_ids(["E1", "E99", "../etc/passwd"], catalogue)

    assert [c["ev_id"] for c in citations] == ["E1"]
    assert unresolved == ["E99", "../etc/passwd"]


def test_resolution_is_case_insensitive_and_deduplicated():
    catalogue = build_evidence_catalogue({"tc-1": _analysis("checkout", "boom")})
    citations, unresolved = resolve_evidence_ids(["e1", "E1", " E1 "], catalogue)

    assert len(citations) == 1
    assert unresolved == []


def test_citation_carries_the_servers_test_id_not_the_models():
    """agents/consistency.py checks citation test_ids against the analysed
    universe. Sourcing them from the catalogue makes that check structural."""
    catalogue = build_evidence_catalogue({"tc-real": _analysis("checkout", "boom")})
    citations, _ = resolve_evidence_ids(["E1"], catalogue)

    assert citations[0]["test_id"] == "tc-real"


# ── Bounds ───────────────────────────────────────────────────────────────────


def test_one_noisy_test_cannot_fill_the_catalogue():
    analyses = {
        "tc-noisy": _analysis("noisy", *[f"excerpt {i}" for i in range(20)]),
        "tc-quiet": _analysis("quiet", "the interesting one"),
    }
    catalogue = build_evidence_catalogue(analyses)

    per_test = [e for e in catalogue if e["test_id"] == "tc-noisy"]
    assert len(per_test) == 2
    assert any(e["test_id"] == "tc-quiet" for e in catalogue)


def test_catalogue_is_bounded():
    analyses = {
        f"tc-{i}": _analysis(f"t{i}", "a", "b") for i in range(40)
    }
    assert len(build_evidence_catalogue(analyses)) == CATALOGUE_LIMIT


def test_evidence_without_an_excerpt_is_not_catalogued():
    analyses = {"tc-1": {"evidence_references": [{"source": "splunk", "excerpt": ""}]}}
    assert build_evidence_catalogue(analyses) == []


# ── Rendering ────────────────────────────────────────────────────────────────


def test_render_includes_every_id():
    catalogue = build_evidence_catalogue({
        "tc-1": _analysis("checkout", "NullPointerException"),
        "tc-2": _analysis("payment", "timeout", source="splunk"),
    })
    rendered = render_catalogue(catalogue)

    assert "[E1]" in rendered and "[E2]" in rendered
    assert "checkout" in rendered and "splunk" in rendered


def test_an_empty_catalogue_renders_nothing():
    """A header with no items under it invites the model to invent ids."""
    assert render_catalogue([]) == ""


# ── Grounding a layer ────────────────────────────────────────────────────────


def test_ground_layer_replaces_ids_with_resolved_citations():
    catalogue = build_evidence_catalogue({"tc-1": _analysis("checkout", "boom")})
    result = ground_layer(
        {"what_failed": "Checkout broke", "evidence_ids": ["E1"]},
        catalogue,
        layer_name="layer2_incident_view",
    )

    assert result["payload"]["what_failed"] == "Checkout broke"
    assert result["payload"]["citations"][0]["ev_id"] == "E1"
    # The raw model field does not survive into the stored report.
    assert "evidence_ids" not in result["payload"]
    assert result["grounding"]["resolved"] == 1
    assert result["grounding"]["cited"] is True


def test_an_uncited_layer_is_recorded_not_rejected():
    """"Nothing here supports this" is a legitimate answer; the metric is what
    makes its frequency visible."""
    result = ground_layer({"what_failed": "Checkout broke"}, [], layer_name="l2")

    assert result["payload"]["citations"] == []
    assert result["grounding"]["cited"] is False
    assert result["grounding"]["claimed_ids"] == 0


def test_fabricated_ids_are_counted_not_silently_dropped():
    catalogue = build_evidence_catalogue({"tc-1": _analysis("checkout", "boom")})
    result = ground_layer({"evidence_ids": ["E1", "E7"]}, catalogue, layer_name="l3")

    assert result["grounding"]["resolved"] == 1
    assert result["grounding"]["unresolved"] == ["E7"]


def test_ground_layer_does_not_mutate_its_input():
    catalogue = build_evidence_catalogue({"tc-1": _analysis("checkout", "boom")})
    payload = {"what_failed": "x", "evidence_ids": ["E1"]}
    ground_layer(payload, catalogue)

    assert payload["evidence_ids"] == ["E1"]


# ── Coverage ─────────────────────────────────────────────────────────────────


def test_coverage_summary_separates_claimed_from_resolved():
    records = [
        {"layer": "l2", "claimed_ids": 2, "resolved": 2, "unresolved": [], "cited": True},
        {"layer": "l3", "claimed_ids": 3, "resolved": 1, "unresolved": ["E8", "E9"], "cited": True},
        {"layer": "l4", "claimed_ids": 0, "resolved": 0, "unresolved": [], "cited": False},
    ]
    summary = coverage_summary(records)

    assert summary["layers_grounded"] == 3
    assert summary["layers_cited"] == 2
    assert summary["ids_claimed"] == 5
    assert summary["ids_resolved"] == 3
    # The signal that the model is inventing identifiers.
    assert summary["ids_unresolved"] == 2
    assert summary["id_resolution_rate"] == 0.6


def test_coverage_of_nothing_is_none_not_zero():
    summary = coverage_summary([])
    assert summary["layer_citation_rate"] is None
    assert summary["id_resolution_rate"] is None


def test_three_layers_become_citable():
    """Was one (layer 3, and only on verbatim reproduction)."""
    assert citable_layers() == [
        "layer2_incident_view", "layer3_evidence_pack", "layer4_action_plan",
    ]


# ── Never raises ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("garbage", [
    None, "analyses", 7, [],
    {"tc-1": None},
    {"tc-1": {"evidence_references": "nope"}},
    {"tc-1": {"evidence_references": [None, 3, {"excerpt": None}]}},
])
def test_build_never_raises(garbage):
    assert isinstance(build_evidence_catalogue(garbage), list)


@pytest.mark.parametrize("garbage", [None, "ids", 7, {}, [None, {}, []]])
def test_resolve_never_raises(garbage):
    citations, unresolved = resolve_evidence_ids(garbage, [])
    assert isinstance(citations, list) and isinstance(unresolved, list)


@pytest.mark.parametrize("garbage", [None, "payload", 7, []])
def test_ground_layer_never_raises(garbage):
    assert ground_layer(garbage, [])["payload"]["citations"] == []
