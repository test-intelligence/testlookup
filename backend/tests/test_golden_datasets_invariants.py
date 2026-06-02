"""Invariant guard for the golden evaluation datasets.

``golden_datasets.py`` is pure static fixtures consumed by the eval gate
(``eval_gate_service`` / ``ai_eval_service.compute_metrics_for_task_type``) and
seeded via ``POST /ai-eval/golden-datasets/seed``. There is no DB / tenant /
network surface, so the only real risk is silent **drift**: an item added with a
typo'd category, a description whose stated count no longer matches the list, or
a label that doesn't exist in the canonical ``FailureCategory`` enum (which would
make the eval measure against a label the classifier never emits).

These tests lock in the properties verified during review (2026-06-02) so future
edits can't drift unnoticed.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import FailureCategory  # noqa: E402
from app.services.golden_datasets import (  # noqa: E402
    GOLDEN_DATASETS,
    get_all_golden_datasets,
)

_VALID_CATEGORIES = {c.value for c in FailureCategory}
_VALID_RECOMMENDATIONS = {"GO", "NO_GO", "CONDITIONAL_GO"}


def test_registry_keys_match_task_type():
    for key, spec in GOLDEN_DATASETS.items():
        assert spec["task_type"] == key
        assert callable(spec["get_items"])
        assert spec["get_items"](), f"{key} golden dataset is empty"


def test_item_count_and_description_match_actual_length():
    for ds in get_all_golden_datasets():
        items = ds["items"]
        # item_count is computed — must equal the real length.
        assert ds["item_count"] == len(items)
        # The human description states "(N labeled items)" — keep it honest so
        # nobody adds an item and forgets to update the count.
        m = re.search(r"(\d+)\s+labeled items", ds["description"])
        assert m, f"{ds['name']} description missing the '(N labeled items)' count"
        assert int(m.group(1)) == len(items), (
            f"{ds['name']}: description says {m.group(1)} but has {len(items)} items"
        )


def test_classification_categories_are_canonical_and_correct_flag_is_consistent():
    items = GOLDEN_DATASETS["classification"]["get_items"]()
    for item in items:
        inp_cat = item["input"]["failure_category"]
        exp = item["expected_output"]
        assert inp_cat in _VALID_CATEGORIES, f"non-canonical input category {inp_cat!r}"
        assert exp["failure_category"] in _VALID_CATEGORIES, (
            f"non-canonical expected category {exp['failure_category']!r}"
        )
        # ``correct`` must agree with whether the supplied category matches the
        # expected one — the metric reads this flag directly for accuracy.
        assert exp["correct"] == (inp_cat == exp["failure_category"]), (
            f"correct flag inconsistent for {item['metadata']['label']}"
        )


def test_root_cause_categories_are_canonical():
    items = GOLDEN_DATASETS["root_cause"]["get_items"]()
    for item in items:
        cat = item["expected_output"].get("category")
        assert cat in _VALID_CATEGORIES, f"non-canonical root_cause category {cat!r}"


def test_release_decision_recommendations_are_valid():
    items = GOLDEN_DATASETS["release_decision"]["get_items"]()
    for item in items:
        rec = item["expected_output"]["recommendation"]
        assert rec in _VALID_RECOMMENDATIONS, f"invalid recommendation {rec!r}"
        assert isinstance(item["input"]["risk_score"], (int, float))


def test_duplicate_detection_shape():
    items = GOLDEN_DATASETS["duplicate_detection"]["get_items"]()
    for item in items:
        exp = item["expected_output"]
        assert isinstance(exp["is_duplicate"], bool)
        assert isinstance(exp["similarity_threshold"], (int, float))
