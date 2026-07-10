"""Regression matrix for the derived failure-kind triad (PMF US-9.1/US-9.4).

Pins:
* the exhaustive FailureCategory → kind mapping (a new enum member must be
  added to the matrix here AND to ``KIND_BY_CATEGORY`` — the exhaustiveness
  test fails otherwise),
* the BROKEN-status nudge (uncategorised BROKEN rows → infrastructure,
  real categories always win over the nudge),
* alias / drift normalisation reuse (LLM aliases, lowercase drift),
* the by-kind aggregation shape served by
  ``analytics_service.failure_categories`` (zero counts included, fixed
  kind order),
* the ``TestCaseSummary.failure_kind`` computed field on the run's
  failed-tests listing,
* the US-9.4 infra rule-pack additions (disk full / unreachable network /
  fd exhaustion → INFRASTRUCTURE).
"""
from __future__ import annotations

import pytest

from app.models.postgres import FailureCategory
from app.services.failure_kind import (
    FAILURE_KINDS,
    KIND_BY_CATEGORY,
    failure_kind,
    kind_counts,
)


# ── Mapping matrix — every FailureCategory member ──────────────────────────

EXPECTED_KIND_BY_MEMBER = {
    FailureCategory.PRODUCT_BUG: "product",
    FailureCategory.INFRASTRUCTURE: "infrastructure",
    FailureCategory.TEST_DATA: "test_code",
    FailureCategory.AUTOMATION_DEFECT: "test_code",
    FailureCategory.FLAKY: "test_code",
    FailureCategory.UNKNOWN: "unknown",
}


def test_mapping_is_exhaustive_over_the_enum():
    """A new FailureCategory member must be mapped deliberately — both in
    the module and in this test's expectations — never silently unknown."""
    assert set(KIND_BY_CATEGORY) == {c.value for c in FailureCategory}
    assert set(EXPECTED_KIND_BY_MEMBER) == set(FailureCategory)


@pytest.mark.parametrize("member,expected", sorted(
    EXPECTED_KIND_BY_MEMBER.items(), key=lambda kv: kv[0].value,
))
def test_every_category_member_maps_to_its_kind(member, expected):
    assert failure_kind(member.value, "FAILED") == expected
    # Enum instances are accepted as well as raw strings.
    assert failure_kind(member, "FAILED") == expected


def test_all_kinds_are_valid_triad_values():
    assert set(EXPECTED_KIND_BY_MEMBER.values()) <= set(FAILURE_KINDS)
    assert FAILURE_KINDS == ("product", "test_code", "infrastructure", "unknown")


# ── BROKEN nudge ────────────────────────────────────────────────────────────

def test_none_category_with_broken_status_nudges_to_infrastructure():
    assert failure_kind(None, "BROKEN") == "infrastructure"


def test_unknown_category_with_broken_status_nudges_to_infrastructure():
    assert failure_kind("UNKNOWN", "BROKEN") == "infrastructure"


def test_unrecognised_category_with_broken_status_nudges_to_infrastructure():
    assert failure_kind("SOMETHING_THE_LLM_MADE_UP", "BROKEN") == "infrastructure"


def test_real_category_beats_the_broken_nudge():
    assert failure_kind("PRODUCT_BUG", "BROKEN") == "product"
    assert failure_kind("AUTOMATION_DEFECT", "BROKEN") == "test_code"


def test_none_none_is_unknown():
    assert failure_kind(None, None) == "unknown"


def test_empty_strings_are_unknown():
    assert failure_kind("", "FAILED") == "unknown"
    assert failure_kind("  ", "failed") == "unknown"


def test_broken_status_is_case_insensitive():
    assert failure_kind(None, "broken") == "infrastructure"


# ── Alias / drift normalisation ─────────────────────────────────────────────

def test_llm_aliases_resolve_through_the_normalizer_map():
    # Aliases from services/category_normalizer.py — a representative per kind.
    assert failure_kind("TEST_CODE", None) == "test_code"        # → AUTOMATION_DEFECT
    assert failure_kind("ENV", None) == "infrastructure"          # → INFRASTRUCTURE
    assert failure_kind("BUG", None) == "product"                 # → PRODUCT_BUG
    assert failure_kind("INTERMITTENT", None) == "test_code"      # → FLAKY
    assert failure_kind("FIXTURE", None) == "test_code"           # → TEST_DATA


def test_lowercase_drift_is_normalised():
    assert failure_kind("product_bug", "FAILED") == "product"
    assert failure_kind("infrastructure", "FAILED") == "infrastructure"


# ── kind_counts aggregation ─────────────────────────────────────────────────

def test_kind_counts_includes_zero_kinds_in_fixed_order():
    rows = [
        ("PRODUCT_BUG", "FAILED", 3),
        ("UNKNOWN", "BROKEN", 2),      # nudged → infrastructure
        ("UNKNOWN", "FAILED", 1),      # stays unknown
    ]
    result = kind_counts(rows)
    assert [i["kind"] for i in result] == list(FAILURE_KINDS)
    counts = {i["kind"]: i["count"] for i in result}
    assert counts == {
        "product": 3,
        "test_code": 0,
        "infrastructure": 2,
        "unknown": 1,
    }


def test_kind_counts_handles_empty_input():
    result = kind_counts([])
    assert [i["count"] for i in result] == [0, 0, 0, 0]


# ── failure_categories endpoint response shape ──────────────────────────────

class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows):
        self._rows = [_FakeRow(r) for r in rows]

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, query, params=None):
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_failure_categories_serves_items_and_by_kind():
    pytest.importorskip("sqlalchemy")
    from app.services.analytics_service import failure_categories

    db = _FakeSession([
        {"category": "PRODUCT_BUG", "status": "FAILED", "count": 5},
        {"category": "UNKNOWN", "status": "BROKEN", "count": 2},
        {"category": "UNKNOWN", "status": "FAILED", "count": 1},
    ])
    result = await failure_categories(db, "proj-1", 30)

    # Historical per-category items preserved (re-aggregated over status),
    # each carrying its category-only derived kind.
    assert result["period_days"] == 30
    assert result["items"] == [
        {"category": "PRODUCT_BUG", "count": 5, "kind": "product"},
        {"category": "UNKNOWN", "count": 3, "kind": "unknown"},
    ]

    # Parallel by-kind aggregation applies the BROKEN nudge per (cat, status).
    by_kind = {i["kind"]: i["count"] for i in result["by_kind"]}
    assert by_kind == {
        "product": 5,
        "test_code": 0,
        "infrastructure": 2,
        "unknown": 1,
    }


@pytest.mark.asyncio
async def test_top_failing_tests_attaches_failure_kind_per_item():
    pytest.importorskip("sqlalchemy")
    from app.services.analytics_service import top_failing_tests

    db = _FakeSession([
        {
            "test_fingerprint": "fp-1",
            "test_name": "t1",
            "suite_name": "S",
            "class_name": "C",
            "failure_category": "AUTOMATION_DEFECT",
            "fail_count": 4,
            "last_failed": None,
        },
        {
            "test_fingerprint": "fp-2",
            "test_name": "t2",
            "suite_name": "S",
            "class_name": "C",
            "failure_category": None,
            "fail_count": 2,
            "last_failed": None,
        },
    ])
    # project_id=None skips the failure-step enrichment (no extra queries).
    result = await top_failing_tests(db, None, 30, 15)

    kinds = {i["test_fingerprint"]: i["failure_kind"] for i in result["items"]}
    assert kinds == {"fp-1": "test_code", "fp-2": "unknown"}


# ── TestCaseSummary.failure_kind computed field ─────────────────────────────

def _summary(**overrides):
    import uuid
    from datetime import datetime, timezone

    from app.models.schemas import TestCaseSummary

    base = {
        "id": uuid.uuid4(),
        "test_run_id": uuid.uuid4(),
        "test_name": "t",
        "status": "FAILED",
        "created_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return TestCaseSummary.model_validate(base)


def test_test_case_summary_failed_row_gets_category_kind():
    row = _summary(status="FAILED", failure_category="INFRASTRUCTURE")
    assert row.failure_kind == "infrastructure"
    # Serialised into API responses (computed_field lands in model_dump).
    assert row.model_dump()["failure_kind"] == "infrastructure"


def test_test_case_summary_broken_row_without_category_nudges_infra():
    row = _summary(status="BROKEN", failure_category=None)
    assert row.failure_kind == "infrastructure"


def test_test_case_summary_passed_row_has_no_kind():
    row = _summary(status="PASSED", failure_category="PRODUCT_BUG")
    assert row.failure_kind is None


def test_test_case_summary_failed_uncategorised_is_unknown():
    row = _summary(status="FAILED", failure_category=None)
    assert row.failure_kind == "unknown"


# ── US-9.4 infra rule-pack additions ────────────────────────────────────────

@pytest.mark.parametrize("error_message", [
    "OSError: [Errno 28] No space left on device: '/tmp/artifacts'",
    "IOError: disk quota exceeded",
    "write failed: ENOSPC",
    "requests.exceptions.ConnectionError: [Errno 101] Network is unreachable",
    "curl: (7) no route to host",
    "socket.error: EHOSTUNREACH",
    "OSError: [Errno 24] Too many open files",
    "fork failed: Resource temporarily unavailable",
    "OSError: [Errno 12] Cannot allocate memory",
    "getaddrinfo failed: EAI_AGAIN",
])
def test_rules_engine_classifies_classic_infra_shapes(error_message):
    from app.services.rules_engine import RulesEngine

    result = RulesEngine.classify_test(error_message=error_message)
    assert result["failure_category"] == "INFRASTRUCTURE", error_message
    # And the derived kind agrees.
    assert failure_kind(result["failure_category"], "BROKEN") == "infrastructure"


def test_rules_engine_assertion_still_maps_to_product_kind():
    from app.services.rules_engine import RulesEngine

    result = RulesEngine.classify_test(
        error_message="AssertionError: expected 200 but was 500",
    )
    assert result["failure_category"] == "PRODUCT_BUG"
    assert failure_kind(result["failure_category"], "FAILED") == "product"
