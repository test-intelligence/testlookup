"""``items[].kind`` must not contradict ``by_kind`` in the same response.

Closes F-015. ``GET /analytics/failure-categories`` returned, in ONE payload::

    items:   [{category: "UNKNOWN", count: 11, kind: "unknown"}]
    by_kind: [... {kind: "infrastructure", count: 2} ...]

The 2 BROKEN ``test_currency_rounding`` executions are correctly
``infrastructure`` in ``by_kind`` and were silently folded into ``unknown`` in
``items``.

Cause: items were aggregated per *category*, then labelled with
``failure_kind(category, None)`` — a category-only derivation that cannot apply
the BROKEN nudge, because the nudge needs the per-row status the aggregate had
already discarded.

**Why it mattered.** ``FailureAnalysisPage`` FILTERS the category distribution
card on ``item.kind`` (``kindFilteredCategories``), so selecting
"infrastructure" silently missed rows that genuinely were infrastructure
failures — and infra failures are precisely what must not be triaged as product
bugs. The page's client-side fallback for ``by_kind`` also sums ``item.count``
per ``item.kind``, so it inherited the same error.

Fix: group by (category, kind). Counts stay exact; a category may appear once
per kind, which is faithful — a category legitimately contains failures of more
than one kind.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.services import analytics_service  # noqa: E402
from app.services.failure_kind import failure_kind  # noqa: E402


def _src() -> str:
    """Executable source only — comments stripped.

    The fix's comment names ``by_kind``, ``infrastructure`` and ``item.kind``
    while explaining the bug. Prose has satisfied structural assertions three
    times in this session; strip it before reasoning.
    """
    src = inspect.getsource(analytics_service.failure_categories)
    kept = [ln for ln in src.split("\n") if not ln.strip().startswith("#")]
    return "\n".join(kept)


class TestItemsCarryATruthfulKind:
    def test_kind_is_derived_with_the_row_status(self):
        src = _src()
        assert "failure_kind(row[\"category\"], row[\"status\"])" in src, (
            "item kind is still derived from the category alone, so the BROKEN "
            "nudge cannot apply and items contradict by_kind"
        )

    def test_the_category_only_derivation_is_gone(self):
        src = _src()
        assert "failure_kind(category, None)" not in src, (
            "found the category-only derivation that produced the contradiction"
        )

    def test_items_are_grouped_by_category_and_kind(self):
        src = _src()
        assert re.search(r"by_cat_kind", src), "expected a (category, kind) grouping"
        assert '"kind": kind' in src, "item kind must come from the grouping key"


class TestCountsRemainExact:
    """Splitting must not lose or duplicate failures."""

    def test_split_preserves_the_total(self):
        rows = [
            {"category": "UNKNOWN", "status": "FAILED", "count": 9},
            {"category": "UNKNOWN", "status": "BROKEN", "count": 2},
        ]
        grouped: dict[tuple[str, str], int] = {}
        for r in rows:
            key = (r["category"], failure_kind(r["category"], r["status"]))
            grouped[key] = grouped.get(key, 0) + r["count"]
        assert sum(grouped.values()) == 11, "total changed while splitting"

    def test_the_homelab_case_splits_into_two_kinds(self):
        """9 FAILED + 2 BROKEN under one category must not collapse to one kind."""
        rows = [
            {"category": "UNKNOWN", "status": "FAILED", "count": 9},
            {"category": "UNKNOWN", "status": "BROKEN", "count": 2},
        ]
        kinds = {failure_kind(r["category"], r["status"]) for r in rows}
        assert len(kinds) == 2, (
            f"BROKEN and FAILED resolved to the same kind ({kinds}); the nudge "
            "that distinguishes infrastructure from product is not firing"
        )

    def test_broken_resolves_to_infrastructure(self):
        assert failure_kind("UNKNOWN", "BROKEN") == "infrastructure"

    def test_failed_does_not_resolve_to_infrastructure(self):
        assert failure_kind("UNKNOWN", "FAILED") != "infrastructure"


class TestByKindUntouched:
    def test_by_kind_still_computed_from_raw_rows(self):
        src = _src()
        assert "kind_counts(" in src, "the parallel by_kind aggregation was lost"
        assert 'row["status"]' in src
