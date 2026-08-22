"""Regression guard: a deterministic verdict cites what it matched on (F-17).

The defect
----------
``rules_engine._build_result`` hardcoded ``"evidence_references": []``, and the
rules engine produces the large majority of analyses. Measured on the homelab
2026-08-22, after F-3's citation contract shipped:

    4,693 analyses, 3 carrying any evidence  (0.06%)
    rules 4,087 / llm 603
    narrative citations: 0 of 30

So the citation contract was correct and inert: ``build_evidence_catalogue``
had nothing to put in front of the model, ``render_catalogue`` returned an
empty string, and no citation could fire. F-3 fixed the wrong end of the chain
-- the binding constraint was evidence SUPPLY, not citation mechanism.

What is guarded
---------------
* a rules/ML verdict cites the input it matched on, so the catalogue is no
  longer empty;
* it never cites its own conclusion -- quoting ``root_cause_summary`` back as
  support would be circular and would inflate coverage while teaching nobody
  anything;
* it never fabricates: a verdict reached from nothing citable reports nothing;
* it never overwrites real tool observations, which are strictly better;
* classifier provenance does NOT become an attested artifact -- the HMAC-signed
  evidence store stays tool-observation-only, and the capture path skips these
  refs rather than flagging thousands of authorization errors.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.services.classifier_evidence import (  # noqa: E402
    CLASSIFIER_EVIDENCE_KIND,
    attach_classifier_evidence,
    build_classifier_evidence,
)

_RULES = {
    "classified_by": "rules_engine",
    "confidence_rule_id": "pattern.connection_refused",
    "root_cause_summary": "Backend refused the connection.",
    "evidence_references": [],
}
_CASE = {
    "test_name": "checkout_flow",
    "error_message": "java.net.ConnectException: Connection refused (localhost:8080)",
}


# ── The supply gap itself ────────────────────────────────────────────────────


def test_a_rules_verdict_cites_the_error_text_it_matched():
    evidence = build_classifier_evidence(_RULES, _CASE)

    assert len(evidence) == 1
    ref = evidence[0]
    assert ref["source"] == "rules_engine"
    assert "Connection refused" in ref["excerpt"]
    assert ref["rule_id"] == "pattern.connection_refused"
    assert ref["basis"] == "matched_error_text"


def test_an_ml_verdict_is_covered_too():
    ml = {"classified_by": "ml_classifier", "evidence_references": []}
    evidence = build_classifier_evidence(ml, _CASE)

    assert evidence and evidence[0]["source"] == "ml_classifier"


def test_a_history_rule_cites_the_record_it_counted():
    """Flakiness heuristics match on history, not on error text."""
    analysis = {
        "classified_by": "rules_engine",
        "confidence_rule_id": "heuristic.historical_flakiness",
        "evidence_references": [],
    }
    evidence = build_classifier_evidence(
        analysis, {"test_name": "flaky_login"}, {"pass_count": 8, "fail_count": 12}
    )

    assert evidence[0]["basis"] == "historical_record"
    assert "12 of 20 historical runs failed" in evidence[0]["excerpt"]


# ── Honesty constraints ──────────────────────────────────────────────────────


def test_it_never_cites_its_own_conclusion():
    """Quoting root_cause_summary back as support would be circular."""
    analysis = {
        "classified_by": "rules_engine",
        "root_cause_summary": "A very confident sounding conclusion.",
        "evidence_references": [],
    }
    # No error text and no history — nothing citable exists.
    assert build_classifier_evidence(analysis, {"test_name": "t"}) == []


def test_it_reports_nothing_rather_than_fabricating():
    assert build_classifier_evidence(
        {"classified_by": "rules_engine"}, {"error_message": ""}, {}
    ) == []


def test_it_never_overwrites_real_tool_observations():
    """An LLM verdict that gathered evidence has strictly better references."""
    with_tools = {
        "classified_by": "rules_engine",
        "evidence_references": [
            {"source": "query_splunk_logs", "kind": "tool_observation", "excerpt": "real"}
        ],
    }
    assert build_classifier_evidence(with_tools, _CASE) == []


def test_an_llm_verdict_is_left_alone():
    llm = {"classified_by": "react_agent", "llm_model": "qwen2.5:7b", "evidence_references": []}
    assert build_classifier_evidence(llm, _CASE) == []


# ── The authority boundary ───────────────────────────────────────────────────


def test_classifier_evidence_is_not_an_attested_artifact():
    """The signed evidence store stays tool-observation-only.

    Blurring this would let deterministic, unattested text into a store whose
    entire purpose is HMAC-bound provenance.
    """
    ref = build_classifier_evidence(_RULES, _CASE)[0]

    assert ref["kind"] == CLASSIFIER_EVIDENCE_KIND
    assert ref["kind"] != "tool_observation"
    assert "producer_attestation" not in ref


@pytest.mark.asyncio
async def test_capture_skips_classifier_evidence_without_flagging_it():
    """Flagging these would fill every report with authorization errors.

    They never applied for authorization, so 'unverified' is the wrong verdict.
    """
    from app.services.evidence_artifact_service import capture_authorized_evidence_artifacts

    state = {
        "analyses": {
            "11111111-1111-1111-1111-111111111111": {
                "evidence_references": build_classifier_evidence(_RULES, _CASE),
            }
        },
    }
    artifacts, errors = await capture_authorized_evidence_artifacts(state)

    assert artifacts == []
    assert "evidence_producer_unverified" not in errors


# ── attach_classifier_evidence ───────────────────────────────────────────────


def test_attach_mutates_only_when_there_is_evidence():
    analysis = dict(_RULES)
    attach_classifier_evidence(analysis, _CASE)
    assert analysis["evidence_references"][0]["basis"] == "matched_error_text"

    empty = {"classified_by": "rules_engine", "evidence_references": []}
    attach_classifier_evidence(empty, {"test_name": "t"})
    assert empty["evidence_references"] == []


@pytest.mark.parametrize("garbage", [None, "analysis", 7, []])
def test_never_raises_on_any_input_shape(garbage):
    assert build_classifier_evidence(garbage, garbage, garbage) == []
    assert attach_classifier_evidence(garbage, garbage) == garbage
