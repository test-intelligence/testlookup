"""Regression guard: a decision claim must cite evidence chosen for it.

The defect (F-16)
-----------------
``_build_typed_claims`` built ONE ``refs`` array -- the signed evidence hash,
the first five authorized artifacts, and the metric snapshot -- and attached the
identical array to every claim it emitted. Coverage therefore read *100% of
claims have evidence* while **no claim cited evidence chosen for it**, and the
claim-evidence drawer presented bundle-level provenance as claim-level.

Measured on the homelab 2026-08-22, before this fix: **10 of 10** published
reports had every claim sharing a byte-identical evidence list.

The subtlety that makes this worth a guard: the naive metric cannot see the
bug. "Claims with evidence" was already 100% and stayed 100%. Only comparing
claims *to each other* reveals it -- which is why
``benchmarks/pipeline/aggregate.py`` reports ``shared_evidence_bundle_rate``
separately from coverage.

What is guarded
---------------
* claims no longer share one evidence list;
* the signed decision-evidence hash IS still on every claim -- it is a
  provenance anchor ("computed from this bundle"), not support, and losing it
  would break traceability;
* each claim's supporting evidence names what a reader would need: the metric
  snapshot for a metric claim, the blockers for a blocker claim, the absent
  stages for a completeness claim;
* tool-observation artifacts are not stapled to aggregate claims they do not
  support.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.agents.decision_report_agent import _build_typed_claims  # noqa: E402

SHA = "a" * 64


def _artifacts(n: int = 3):
    return [
        {
            "artifact_id": f"art-{i}", "evidence_id": f"ev-{i}",
            "source": "query_splunk_logs", "kind": "tool_observation",
            "excerpt": f"log line {i}", "checksum_sha256": "b" * 64,
        }
        for i in range(n)
    ]


def _claims(**over):
    kwargs = {
        "metrics": {"total_tests": 100, "failed_tests": 7},
        "release": {"recommendation": "NO_GO", "blocking_issues": ["auth suite down", "checkout 500s"]},
        "quality": {"missing_or_failed_specialists": ["log_intelligence"], "contradictions": []},
        "source_stages": ["release_risk"],
        "evidence_sha": SHA,
        "evidence_refs": _artifacts(),
    }
    kwargs.update(over)
    return _build_typed_claims(**kwargs)


def _signature(claim):
    """Order-insensitive identity of a claim's evidence, as the harness computes it."""
    return "&".join(sorted(
        "|".join(str(ref.get(k) or "") for k in ("type", "id", "evidence_id"))
        for ref in claim["evidence"]
    ))


# ── The defect itself ────────────────────────────────────────────────────────


def test_claims_no_longer_share_one_evidence_list():
    claims = _claims()
    signatures = [_signature(c) for c in claims]

    assert len(claims) >= 3
    assert len(set(signatures)) == len(signatures), (
        "every claim carried a byte-identical evidence list — the F-16 shape"
    )


def test_coverage_alone_cannot_see_the_bug():
    """Pins why the harness reports shared_evidence_bundle_rate separately.

    'Claims with evidence' was 100% before the fix and is 100% after. A metric
    that only counted that would have shown this change doing nothing.
    """
    claims = _claims()
    assert all(c["evidence"] for c in claims)


# ── The anchor stays, the support differs ────────────────────────────────────


def test_every_claim_keeps_the_signed_provenance_anchor():
    """It says WHICH bundle produced the report; dropping it loses traceability."""
    for claim in _claims():
        anchors = [r for r in claim["evidence"] if r.get("type") == "decision_evidence"]
        assert len(anchors) == 1
        assert anchors[0]["id"] == SHA


def test_a_metric_claim_cites_the_metric_snapshot():
    claim = next(c for c in _claims() if c["claim_id"] == "fact.metrics.test_outcome")
    types = {r.get("type") for r in claim["evidence"]}

    assert "metric" in types
    # Five arbitrary tool observations do not support "7 of 100 tests failed".
    assert "artifact" not in types


def test_a_blocker_claim_cites_the_blockers():
    claim = next(c for c in _claims() if c["claim_id"] == "fact.release.blockers")
    blockers = [r for r in claim["evidence"] if r.get("type") == "release_blocker"]

    assert len(blockers) == 2
    assert any("auth suite down" in str(r.get("excerpt")) for r in blockers)


def test_a_completeness_claim_names_the_absent_stages():
    """"Some evidence is unavailable" is unactionable; naming it is not."""
    claim = next(c for c in _claims() if c["claim_id"] == "unknown.specialists.missing")
    missing = [r for r in claim["evidence"] if r.get("type") == "missing_specialist"]

    assert [r["id"] for r in missing] == ["log_intelligence"]


def test_a_release_inference_cites_the_policy_and_the_metrics():
    claim = next(c for c in _claims() if c["claim_id"] == "inference.release.recommendation")
    types = {r.get("type") for r in claim["evidence"]}

    assert {"policy", "metric"} <= types


# ── Artifacts get a claim they actually support ──────────────────────────────


def test_artifacts_get_their_own_claim_instead_of_being_stapled_on():
    claim = next(c for c in _claims() if c["claim_id"] == "fact.evidence.captured")
    artifacts = [r for r in claim["evidence"] if r.get("type") == "artifact"]

    assert len(artifacts) == 3
    assert "3 authorized evidence artifact(s)" in claim["text"]


def test_no_artifact_claim_when_there_are_no_artifacts():
    claim_ids = {c["claim_id"] for c in _claims(evidence_refs=[])}
    assert "fact.evidence.captured" not in claim_ids


# ── Bounds and robustness ────────────────────────────────────────────────────


def test_blocker_and_missing_evidence_are_bounded():
    claims = _claims(
        release={"recommendation": "NO_GO", "blocking_issues": [f"b{i}" for i in range(50)]},
        quality={"missing_or_failed_specialists": [f"s{i}" for i in range(50)], "contradictions": []},
    )
    blockers = next(c for c in claims if c["claim_id"] == "fact.release.blockers")
    missing = next(c for c in claims if c["claim_id"] == "unknown.specialists.missing")

    assert len([r for r in blockers["evidence"] if r.get("type") == "release_blocker"]) == 5
    assert len([r for r in missing["evidence"] if r.get("type") == "missing_specialist"]) == 8


def test_a_clean_run_still_produces_claims():
    claims = _claims(
        release={"recommendation": "GO", "blocking_issues": []},
        quality={"missing_or_failed_specialists": [], "contradictions": []},
        evidence_refs=[],
    )
    ids = {c["claim_id"] for c in claims}

    assert ids == {"fact.metrics.test_outcome", "inference.release.recommendation"}
    assert len({_signature(c) for c in claims}) == 2
