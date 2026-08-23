"""Regression guard: classifier provenance must not read as evidence loss.

The defect
----------
F-17 gave the fast classifier a provenance trail -- the error text a rule
matched on, attached to each analysis as a ``classifier_input`` reference. The
capture loop skips that kind by design: it is not an attested tool observation,
so it never applies for authorization and no authorization error is recorded.

The evidence bundle carries an independent fail-closed guard::

    if candidate_count and not authorized and not invalid:
        invalid = 1          # candidates went in, nothing came out, no reason

It counted **every** reference as a candidate. So on any run whose failures
were all fast-classified, that guard saw N candidates, zero artifacts and zero
errors, and reported ``evidence_reference_invalid``. The critic's
``metric_data_quality`` check failed, verification failed closed, and the
decision report never published.

Measured on the homelab: 31 of 31 recorded attempts failed on this single
check, every one of them beginning ~20 minutes after the F-17 image rolled out.
The run inspected carried 4 references, all ``classifier_input``, and zero
``tool_observation``.

The real defect was two modules each defining "what applies for authorization"
and disagreeing. Both now import one predicate.

What is guarded
---------------
* classifier-only evidence no longer trips the guard;
* the guard still fires for a reference that genuinely vanished, and for a
  malformed one -- this must not become a blanket suppression;
* both call sites share one definition, so a future local copy cannot drift.
"""
from __future__ import annotations

import pytest

pytest.importorskip("asyncpg")

from app.services import classifier_evidence, evidence_artifact_service  # noqa: E402
from app.services.classifier_evidence import (  # noqa: E402
    CLASSIFIER_EVIDENCE_KIND,
    applies_for_authorization,
)
from app.services.run_evidence_bundle import build_run_evidence_bundle  # noqa: E402

_RUN = "11111111-1111-1111-1111-111111111111"
_TEST = "22222222-2222-2222-2222-222222222222"
_PIPELINE = "55555555-5555-5555-5555-555555555555"
_PROJECT = "33333333-3333-3333-3333-333333333333"


def _classifier_ref(excerpt: str = "payments.internal") -> dict:
    """Exactly the shape F-17 attaches, as stored on the deployment."""
    return {
        "kind": CLASSIFIER_EVIDENCE_KIND,
        "basis": "matched_error_text",
        "source": "fast_classifier",
        "excerpt": excerpt,
        "rule_id": None,
        "test_name": "test_error_1",
    }


def _tool_ref() -> dict:
    return {
        "kind": "tool_observation",
        "source": "get_test_failure_details",
        "excerpt": "AssertionError: expected 200",
    }


def _authorized_artifact() -> dict:
    """A captured, attested artifact -- EvidenceReferenceV2 shape."""
    return {
        "schema_version": 2,
        "artifact_id": "44444444-4444-4444-4444-444444444444",
        "evidence_id": "a" * 64,
        "kind": "tool_observation",
        "source": "get_test_failure_details",
        "scope": {
            "project_id": _PROJECT,
            "test_run_id": _RUN,
            "test_case_id": _TEST,
        },
        "producer_pipeline_run_id": _PIPELINE,
        "checksum_sha256": "b" * 64,
        "freshness": "current_run",
        "sensitivity": "restricted",
        "authorization_status": "tenant_run_pipeline_verified",
        "uri_or_ref": None,
        "excerpt": "AssertionError: expected 200",
    }


def _state(references: list, *, authorized: list | None = None) -> dict:
    """A one-failure run whose capture step has already run."""
    return {
        "test_run_id": _RUN,
        "pipeline_run_id": _PIPELINE,
        "project_id": _PROJECT,
        "test_run_data": {
            "total_tests": 2,
            "passed_tests": 1,
            "failed_tests": 1,
            "skipped_tests": 0,
            "broken_tests": 0,
            "unknown_tests": 0,
        },
        "failed_test_ids": [_TEST],
        "analyses": {_TEST: {"evidence_references": references}},
        # Present => the authorized branch. Empty => nothing was authorized.
        "authorized_evidence_artifacts": authorized if authorized is not None else [],
        "evidence_authorization_errors": [],
    }


def _flag_codes(state: dict) -> list[str]:
    bundle = build_run_evidence_bundle(state)
    return [flag["code"] for flag in bundle.get("quality_flags", [])]


# ── The regression ───────────────────────────────────────────────────────────


def test_classifier_only_evidence_does_not_report_invalid_references():
    """The exact shape that failed 31 of 31 attempts on the homelab."""
    codes = _flag_codes(_state([_classifier_ref(), _classifier_ref("db timeout")]))

    assert "evidence_reference_invalid" not in codes, (
        "classifier provenance never applies for authorization, so producing "
        "no artifact for it is not evidence loss"
    )


def test_a_mixed_run_is_unaffected():
    """A tool observation that WAS authorized, alongside classifier refs."""
    codes = _flag_codes(_state(
        [_classifier_ref(), _tool_ref()],
        authorized=[_authorized_artifact()],
    ))

    assert "evidence_reference_invalid" not in codes


# ── The guard is not weakened ────────────────────────────────────────────────


def test_a_genuinely_lost_reference_still_fails_closed():
    """A real candidate produced no artifact and no error -- still a defect."""
    codes = _flag_codes(_state([_tool_ref()]))

    assert "evidence_reference_invalid" in codes, (
        "the silent-loss guard must survive this fix"
    )


def test_a_malformed_reference_still_counts_as_a_candidate():
    """Skipping non-dicts here would hide them; the capture loop reports them."""
    codes = _flag_codes(_state(["not-a-dict"]))

    assert "evidence_reference_invalid" in codes


def test_a_classifier_ref_alongside_a_lost_one_still_fails_closed():
    """The classifier ref must not mask a genuine loss in the same run."""
    codes = _flag_codes(_state([_classifier_ref(), _tool_ref()]))

    assert "evidence_reference_invalid" in codes


def test_a_run_with_no_references_at_all_is_clean():
    codes = _flag_codes(_state([]))

    assert "evidence_reference_invalid" not in codes


# ── One definition, not two ──────────────────────────────────────────────────


def test_both_call_sites_share_one_definition():
    """Two local copies of this rule is what broke verification."""
    assert (
        evidence_artifact_service.applies_for_authorization
        is classifier_evidence.applies_for_authorization
    )
    assert not hasattr(evidence_artifact_service, "_CLASSIFIER_EVIDENCE_KIND"), (
        "the duplicated constant is what allowed the two rules to drift"
    )


def test_the_predicate_treats_malformed_input_as_a_candidate():
    assert applies_for_authorization("not-a-dict") is True
    assert applies_for_authorization(None) is True
    assert applies_for_authorization({"kind": "tool_observation"}) is True
    assert applies_for_authorization(_classifier_ref()) is False
