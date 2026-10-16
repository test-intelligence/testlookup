"""FAIL-10: the classification-hint vocabularies, matcher, verdict and validation (pure).

A hint is "<field> contains <text> -> <category>" for one project: a
case-insensitive substring, never a regex; the oldest enabled matching hint
wins. The routes and the per-project rules are in ``test_classification_hint_routes.py``
and ``integration/test_classification_hints_postgres.py``; precedence over the
engines in ``test_analysis_hint_precedence.py``.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import classification_hint_service as svc


def _hint(hid="h1", field="error_message", pattern="ECONNREFUSED", category="INFRASTRUCTURE",
          created_at="2026-10-01T00:00:00+00:00", **extra):
    return {"id": hid, "match_field": field, "pattern": pattern, "category": category,
            "created_at": created_at, "enabled": True, "created_by_name": "Priya Nair", **extra}


# ── vocabularies ────────────────────────────────────────────────────────────


def test_the_vocabularies_are_the_contracts_and_match_the_orm_copies():
    from app.models import postgres

    assert svc.MATCH_FIELDS == ("error_message", "test_name", "suite_name", "class_name")
    assert svc.HINT_CATEGORIES == postgres._HINT_CATEGORIES
    assert svc.MATCH_FIELDS == postgres._HINT_MATCH_FIELDS
    # FailureCategory minus UNKNOWN, in the enum's order.
    assert svc.HINT_CATEGORIES == tuple(c.value for c in postgres.FailureCategory if c.value != "UNKNOWN")
    assert (svc.MAX_HINTS_PER_PROJECT, svc.PATTERN_MIN, svc.PATTERN_MAX) == (50, 3, 200)
    assert svc.HINT_NOT_FOUND == "Classification hint not found in this project."
    assert set(svc.FIELD_LABELS) == set(svc.MATCH_FIELDS)
    assert set(svc.CATEGORY_LABELS) == set(svc.HINT_CATEGORIES)


# ── 3. matching ─────────────────────────────────────────────────────────────


def test_a_case_insensitive_substring_matches():
    test = {"error_message": "requests: connect ECONNREFUSED 10.0.0.4:5432"}
    assert svc.match_hint(test, [_hint(pattern="econnrefused")])["id"] == "h1"
    assert svc.match_hint({"error_message": "Connection reset"}, [_hint()]) is None


def test_a_field_that_is_not_text_is_skipped():
    hints = [_hint(field="class_name", pattern="Checkout")]
    assert svc.match_hint({"class_name": None}, hints) is None
    assert svc.match_hint({}, hints) is None
    assert svc.match_hint({"class_name": 42}, hints) is None
    assert svc.match_hint({"class_name": "com.acme.CheckoutTest"}, hints) is not None


def test_the_oldest_of_two_matching_hints_wins_whatever_the_list_order():
    newer = _hint("h-new", pattern="refused", category="PRODUCT_BUG", created_at="2026-10-02T00:00:00+00:00")
    older = _hint("h-old", pattern="ECONN", category="INFRASTRUCTURE", created_at="2026-10-01T00:00:00+00:00")
    test = {"error_message": "ECONNREFUSED"}
    assert svc.match_hint(test, [newer, older])["id"] == "h-old"
    # Same instant: the id breaks the tie (the list endpoint's order).
    a = _hint("a", pattern="ECONN", created_at="2026-10-01T00:00:00+00:00")
    b = _hint("b", pattern="REFUSED", created_at="2026-10-01T00:00:00+00:00")
    assert svc.match_hint(test, [b, a])["id"] == "a"


def test_a_disabled_hint_is_ignored():
    assert svc.match_hint({"error_message": "ECONNREFUSED"}, [_hint(enabled=False)]) is None
    assert svc.match_hint({"error_message": "x"}, []) is None
    assert svc.match_hint({"error_message": "x"}, None) is None


def test_each_field_matches_its_own_value_only():
    test = {"test_name": "test_pays", "suite_name": "Checkout API", "class_name": "PayTest",
            "error_message": "boom"}
    assert svc.match_hint(test, [_hint(field="suite_name", pattern="checkout")]) is not None
    assert svc.match_hint(test, [_hint(field="test_name", pattern="checkout")]) is None


# ── the verdict ─────────────────────────────────────────────────────────────


def test_the_hint_verdict_carries_its_category_provenance_and_evidence():
    test = {"test_name": "test_pays", "error_message": "connect ECONNREFUSED payments-sandbox"}
    out = svc.build_hint_analysis(_hint(note="payments sandbox is down"), test)
    assert out["failure_category"] == "INFRASTRUCTURE"
    assert out["root_cause_summary"] == (
        'Classified by a project hint: error message contains "ECONNREFUSED". Note: payments sandbox is down'
    )
    assert (out["confidence_score"], out["is_flaky"], out["requires_human_review"]) == (90, False, False)
    assert (out["classified_by"], out["confidence_basis"], out["confidence_rule_id"]) == (
        "project_hint", "project_hint", "project_hint.h1",
    )
    assert out["recommended_actions"] == []
    assert out["_routing"] == {
        "mode_requested": None, "mode_resolved": "hint", "mode_used": "hint",
        "fallback_from": None, "fallback_reason": None, "source": "project_hint",
        "hint_id": "h1", "hint_match_field": "error_message", "hint_pattern": "ECONNREFUSED",
        "hint_set_by": "Priya Nair",
    }
    [evidence] = out["evidence_references"]
    assert evidence["kind"] == "classifier_input"  # never an attested artifact
    assert evidence["excerpt"] == "connect ECONNREFUSED payments-sandbox"
    assert evidence["basis"] == "matched_project_hint"


def test_a_flaky_hint_says_flaky_and_no_note_adds_no_sentence():
    out = svc.build_hint_analysis(_hint(category="FLAKY", pattern="retry budget"))
    assert out["is_flaky"] is True
    assert out["root_cause_summary"] == 'Classified by a project hint: error message contains "retry budget".'


def test_a_classifier_input_reference_is_not_an_artifact_candidate():
    from app.services.classifier_evidence import applies_for_authorization

    ref = svc.build_hint_analysis(_hint(), {"error_message": "ECONNREFUSED"})["evidence_references"][0]
    assert applies_for_authorization(ref) is False


def test_the_labels_read_as_the_ui_does():
    assert svc.hint_label("error_message", "ECONNREFUSED", "INFRASTRUCTURE") == (
        'error message contains "ECONNREFUSED" → Infrastructure'
    )
    assert svc.hint_label("class_name", "x" * 100, "TEST_DATA").endswith('"' + "x" * 60 + '" → Test data')
    assert svc.hint_rationale(_hint()) == (
        'Matched project hint (set by Priya Nair): error message contains "ECONNREFUSED" → Infrastructure'
    )
    assert svc.hint_rationale(_hint(created_by_name=None)).startswith("Matched project hint (set by a QA lead)")


# ── 4. validation texts ─────────────────────────────────────────────────────


def _detail(fn, value):
    with pytest.raises(HTTPException) as caught:
        fn(value)
    assert caught.value.status_code == 422
    return caught.value.detail


def test_each_refusal_has_its_one_text():
    assert _detail(svc.validate_match_field, "stack_trace") == (
        "match_field must be one of error_message, test_name, suite_name, class_name."
    )
    assert _detail(svc.validate_match_field, None) == svc.MATCH_FIELD_INVALID
    assert _detail(svc.validate_category, "UNKNOWN") == (
        "category must be one of PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY."
    )
    for bad in ("ab", "  ab  ", "", "x" * 201, None):
        assert _detail(svc.validate_pattern, bad) == "pattern must be 3-200 characters."
    assert svc.TOO_MANY_HINTS == "This project already has 50 classification hints."
    assert svc.DUPLICATE_HINT == "A hint with this field and text already exists."


def test_valid_values_are_normalised():
    assert svc.validate_category(" infrastructure ") == "INFRASTRUCTURE"
    assert svc.validate_pattern("  ECONNREFUSED  ") == "ECONNREFUSED"
    assert svc.validate_pattern("x" * 200) == "x" * 200
    assert svc.validate_match_field("class_name") == "class_name"


# ── the preview's example text ──────────────────────────────────────────────


def test_a_preview_example_is_at_most_160_characters_around_the_match_and_redacted():
    value = "A" * 300 + " ECONNREFUSED " + "B" * 300 + "\n password=hunter2"
    example = svc._example_value(value, "econnrefused")
    assert len(example) <= 160 and "ECONNREFUSED" in example
    short = svc._example_value("login failed\n  password=hunter2", "login")
    assert short.startswith("login failed password=") and "hunter2" not in short
