"""W16 AGT-09 (b): the replay scorers (``services/ai_eval_scorers.py``).

Pure functions: a stored engine output -> item status -> run metrics. The
legacy label-agreement functions of ``ai_eval_service`` are NOT these, and one
case below pins that they still disagree on purpose.
"""
from __future__ import annotations

import pytest

from app.services import ai_eval_scorers as scorers
from app.services.ai_eval_service import compute_root_cause_metrics
from app.services.golden_datasets import get_golden_root_cause_items


def _metrics(task_type, rows):
    return {m.name: m for m in scorers.aggregate(task_type, rows)}


# ── classification ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "expected,output,status",
    [
        ({"failure_category": "PRODUCT_BUG"}, {"failure_category": "product_bug"}, "correct"),
        ({"failure_category": "PRODUCT_BUG"}, {"failure_category": "INFRASTRUCTURE"}, "incorrect"),
        ({"correct": True}, {"failure_category": "PRODUCT_BUG"}, "unscored"),
        ({"failure_category": "FLAKY"}, {"confidence_score": 90}, "incorrect"),
        ({"failure_category": "FLAKY"}, None, "incorrect"),
    ],
    ids=["correct", "incorrect", "unscored", "output-missing-field", "no-output"],
)
def test_classification_item_status(expected, output, status):
    got, _ = scorers.score_item("classification", expected=expected, output=output, item_input={})
    assert got == status


def test_a_leaked_input_label_is_never_read():
    """The golden classification items carry the PREDICTION in their input;
    a scorer that read it would score 100 %. The output decides."""
    leaked = {"failure_category": "PRODUCT_BUG", "confidence_score": 99}
    status, score = scorers.score_item(
        "classification",
        expected={"failure_category": "PRODUCT_BUG"},
        output={"failure_category": "INFRASTRUCTURE"},
        item_input=leaked,
    )
    assert status == "incorrect"
    assert score == {"expected": "PRODUCT_BUG", "predicted": "INFRASTRUCTURE"}


def test_classification_macro_metrics_over_scored_items_only():
    rows = [
        ("correct", {"expected": "PRODUCT_BUG", "predicted": "PRODUCT_BUG"}),
        ("correct", {"expected": "FLAKY", "predicted": "FLAKY"}),
        ("incorrect", {"expected": "FLAKY", "predicted": "PRODUCT_BUG"}),
        ("unscored", {"reason": "expected_label_missing"}),
        ("error", {}),
    ]
    m = _metrics("classification", rows)
    assert m["accuracy"].value == pytest.approx(0.6667, abs=1e-4)
    assert m["accuracy"].n == 3
    # PRODUCT_BUG: P = 1/2, R = 1; FLAKY: P = 1, R = 1/2 -> macro 0.75 / 0.75.
    assert m["precision"].value == 0.75
    assert m["recall"].value == 0.75
    assert m["f1_score"].value == 0.75
    assert all(metric.kind == "score" and metric.measured for metric in m.values())


# ── root cause ──────────────────────────────────────────────────────────────

_RC_EXPECTED = {"has_root_cause": True, "category": "PRODUCT_BUG", "mentions_file": True,
                "mentions_line": True, "actionable": True}
_RC_INPUT = {"stack_trace": "at PaymentService.processRefund(PaymentService.java:142)"}


def test_root_cause_correct_with_sub_checks():
    output = {
        "root_cause_summary": "PaymentService.java:142 dereferences a null account.",
        "failure_category": "PRODUCT_BUG",
        "recommended_actions": ["Add a null check"],
    }
    status, score = scorers.score_item("root_cause", expected=_RC_EXPECTED, output=output, item_input=_RC_INPUT)
    assert status == "correct"
    assert score["mentions_file"] is True and score["mentions_line"] is True and score["actionable"] is True


@pytest.mark.parametrize(
    "output,status",
    [
        ({"root_cause_summary": "Config drift.", "failure_category": "INFRASTRUCTURE"}, "incorrect"),
        ({"root_cause_summary": "   ", "failure_category": "PRODUCT_BUG"}, "incorrect"),
        ({"failure_category": "PRODUCT_BUG"}, "incorrect"),
        (None, "incorrect"),
    ],
    ids=["wrong-category", "empty-summary", "summary-missing", "no-output"],
)
def test_root_cause_incorrect(output, status):
    got, _ = scorers.score_item("root_cause", expected=_RC_EXPECTED, output=output, item_input=_RC_INPUT)
    assert got == status


def test_a_missing_expected_category_is_unscored_where_the_legacy_metric_counted_it_correct():
    """The legacy bug pinned by name: ``compute_root_cause_metrics`` takes
    ``else: correct += 1`` when the item has no expected category. The replay
    scorer refuses to score it -- and the two are deliberately different."""
    item = {"input": {"error_message": "boom"}, "expected_output": {"has_root_cause": True}}
    status, _ = scorers.score_item(
        "root_cause",
        expected=item["expected_output"],
        output={"root_cause_summary": "Anything.", "failure_category": "PRODUCT_BUG"},
        item_input=item["input"],
    )
    assert status == "unscored"
    legacy = compute_root_cause_metrics([item])
    assert legacy["correct"] == 1 and legacy["accuracy"] == 1.0  # the legacy number, unchanged
    assert _metrics("root_cause", [(status, {})])["accuracy"].value is None  # the replay's: not measured


def test_root_cause_sub_checks_agree_with_the_expected_flags():
    items = get_golden_root_cause_items()
    no_trace = next(i for i in items if i["metadata"]["label"] == "insufficient_evidence")
    status, score = scorers.score_item(
        "root_cause",
        expected=no_trace["expected_output"],
        output={"root_cause_summary": "", "failure_category": "UNKNOWN", "recommended_actions": []},
        item_input=no_trace["input"],
    )
    assert status == "correct"
    m = _metrics("root_cause", [(status, score)])
    assert {k: (v.value, v.kind) for k, v in m.items() if v.kind == "sub_check"} == {
        "mentions_file": (1.0, "sub_check"),
        "mentions_line": (1.0, "sub_check"),
        "actionable": (1.0, "sub_check"),
    }
    assert m["precision"].value == m["recall"].value == m["f1_score"].value == m["accuracy"].value == 1.0


def test_stack_file_tokens():
    assert scorers.stack_file_tokens("at LoginPage.clickLogin(LoginPage.ts:25)") == [("LoginPage.ts", "25")]
    assert scorers.stack_file_tokens("(no trace available)") == []
    assert scorers.stack_file_tokens(None) == []


# ── duplicate detection / release decision ─────────────────────────────────


@pytest.mark.parametrize(
    "expected,output,status",
    [
        ({"is_duplicate": True}, {"predicted_duplicate": True, "ratio": 0.81}, "correct"),
        ({"is_duplicate": False}, {"predicted_duplicate": True, "ratio": 0.75}, "incorrect"),
        ({"similarity_threshold": 0.7}, {"predicted_duplicate": True}, "unscored"),
        ({"is_duplicate": False}, {"ratio": 0.2}, "incorrect"),
        ({"is_duplicate": False}, None, "incorrect"),
    ],
    ids=["correct", "incorrect", "unscored", "output-missing-field", "no-output"],
)
def test_duplicate_item_status(expected, output, status):
    got, _ = scorers.score_item("duplicate_detection", expected=expected, output=output, item_input={})
    assert got == status


@pytest.mark.parametrize(
    "expected,output,status",
    [
        ({"recommendation": "NO_GO"}, {"recommendation": "NO_GO"}, "correct"),
        ({"recommendation": "GO"}, {"recommendation": "CONDITIONAL_GO"}, "incorrect"),
        ({"max_acceptable_risk": 20}, {"recommendation": "GO"}, "unscored"),
        ({"recommendation": "GO"}, {"overall_result": "PASS"}, "incorrect"),
        ({"recommendation": "GO"}, None, "incorrect"),
    ],
    ids=["correct", "incorrect", "unscored", "output-missing-field", "no-output"],
)
def test_release_item_status(expected, output, status):
    got, _ = scorers.score_item("release_decision", expected=expected, output=output, item_input={})
    assert got == status


def test_duplicate_binary_and_release_macro_metrics():
    dup = _metrics("duplicate_detection", [
        ("correct", {"expected": True, "predicted": True}),
        ("incorrect", {"expected": False, "predicted": True}),
        ("incorrect", {"expected": True, "predicted": False}),
        ("correct", {"expected": False, "predicted": False}),
    ])
    assert (dup["accuracy"].value, dup["precision"].value, dup["recall"].value) == (0.5, 0.5, 0.5)
    rel = _metrics("release_decision", [
        ("correct", {"expected": "GO", "predicted": "GO"}),
        ("correct", {"expected": "NO_GO", "predicted": "NO_GO"}),
        ("incorrect", {"expected": "CONDITIONAL_GO", "predicted": "NO_GO"}),
    ])
    # GO 1/1, CONDITIONAL_GO 0/0 -> 0, NO_GO 1/2 ; recalls 1, 0, 1.
    assert rel["precision"].value == 0.5
    assert rel["recall"].value == pytest.approx(0.6667, abs=1e-4)


@pytest.mark.parametrize("task_type", ["classification", "root_cause", "duplicate_detection", "release_decision"])
def test_nothing_scored_is_not_measured_never_zero(task_type):
    for metric in scorers.aggregate(task_type, [("unscored", {}), ("error", {})]):
        assert metric.value is None
        assert metric.measured is False
        assert metric.n == 0
    assert scorers.metric_value(scorers.aggregate(task_type, []), "accuracy") is None


def test_project_output_keeps_the_scored_fields_only():
    raw = {
        "failure_category": "PRODUCT_BUG", "confidence_score": 82, "root_cause_summary": "x" * 900,
        "role_actions": {"qa": "rerun"}, "tools_used": ["check_test_flakiness"],
        "_routing": {"mode_used": "rules", "fallback_reason": None, "auto_probe": {}},
    }
    out = scorers.project_output("classification", raw)
    assert set(out) == {"failure_category", "confidence_score", "root_cause_summary", "mode_used", "fallback_reason"}
    assert len(out["root_cause_summary"]) == 500 and out["mode_used"] == "rules"
    rc = scorers.project_output("root_cause", {**raw, "recommended_actions": list("abcdefg")})
    assert rc["recommended_actions"] == ["a", "b", "c", "d", "e"]
    assert scorers.project_output("duplicate_detection", {"ratio": 0.712345, "predicted_duplicate": True}) == {
        "ratio": 0.7123, "predicted_duplicate": True, "detector_path": "title_ratio",
    }
    release = scorers.project_output("release_decision", {
        "recommendation": "NO_GO", "overall_result": "BLOCK", "effective_composite": 12.0,
        "policy_level": "project", "policy_version": 3, "rule_evaluations": [
            {"rule_type": "critical_failures", "passed": False, "action": "BLOCK", "message": "m"},
        ],
    })
    assert release["rule_results"] == [{"type": "critical_failures", "passed": False, "action": "BLOCK"}]
    with pytest.raises(ValueError):
        scorers.project_output("nonsense", {})
