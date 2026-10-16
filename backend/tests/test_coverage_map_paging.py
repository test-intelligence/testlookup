"""W08 TRND-12 -- the coverage map's "Other (n)" opens, and nodes count the
tests the last suite sync marked for review.

The database half (503 suites over three pages, seeded ``suite_memberships``)
is ``tests/integration/test_coverage_map_postgres.py``. Here: the parsing, the
statement's binds and fragments, the assembly of a later page, and the tree
contract with and without ``needs_review_count``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.models.viz_contracts import MAX_TREE_NODES, validate_contract
from app.services import coverage_map_service as svc
from app.services.analytics_scope import AnalyticsScope

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
PROJECT = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(
        project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30,
    )
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def _row(part="child", rn=1, key="k", *, tests=1, merged=1, child_total=1, needs_review_count=None):
    return SimpleNamespace(
        part=part, rn=rn, child_key=key, child_label=key, parent_label=None,
        test_count=tests, executions=0, passed=0, failed=0, broken=0, skipped=0, unknown=0,
        flaky_count=0, last_at=None, unknown_tests=0, merged=merged, child_total=child_total,
        needs_review_count=needs_review_count,
    )


# ── parsing ─────────────────────────────────────────────────────────────────


def test_no_other_page_is_page_one_with_no_offset():
    for level in (svc.parse_level(1), svc.parse_level(2, "s"), svc.parse_level(3, "s", "c")):
        assert level.other_page == 1 and level.children_offset == 0
    assert svc.CoverageLevel(1) == svc.parse_level(1)


@pytest.mark.parametrize("page", [2, 3, svc.OTHER_PAGE_MAX])
def test_a_page_from_two_to_fifty_parses_at_every_depth(page):
    for level in (
        svc.parse_level(1, other_page=page),
        svc.parse_level(2, "s", other_page=page),
        svc.parse_level(3, "s", "c", other_page=page),
    ):
        assert level.other_page == page
        assert level.children_offset == (page - 1) * svc.KEPT_WHEN_FOLDED


@pytest.mark.parametrize("page", [1, 0, -1, 51, True, "2", 2.0])
def test_a_page_outside_two_to_fifty_is_a_422(page):
    with pytest.raises(AnalyticsQueryError) as err:
        svc.parse_level(1, other_page=page)
    assert err.value.code == "other_page_range"
    assert err.value.param == "other_page"
    assert err.value.allowed == {"min": 2, "max": 50}


def test_the_constants_follow_the_500_node_contract():
    assert svc.OTHER_PAGE_MAX == 50
    assert svc.KEPT_WHEN_FOLDED == MAX_TREE_NODES - 2 == 498


def test_the_route_declares_the_same_bounds():
    """``other_page=1`` / ``51`` are refused before the service sees them."""
    from fastapi.dependencies.utils import get_flat_dependant

    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/v1/analytics/coverage-map")
    param = next(p for p in get_flat_dependant(route.dependant).query_params if p.alias == "other_page")
    bounds = {type(m).__name__: m for m in param.field_info.metadata}
    assert bounds["Ge"].ge == 2 and bounds["Le"].le == svc.OTHER_PAGE_MAX
    assert param.field_info.default is None


# ── the statement ───────────────────────────────────────────────────────────


def test_the_offset_is_a_bind_and_page_one_s_is_zero():
    sql, params = svc.build_statement(svc.parse_level(1), _scope(), now=FROZEN)
    assert params["cm_offset"] == 0
    assert "rn > :cm_offset" in sql
    assert "child_total - :cm_offset <= :cm_cap OR rn <= :cm_offset + :cm_keep" in sql
    assert "child_total - :cm_offset > :cm_cap AND rn > :cm_offset + :cm_keep" in sql
    _, later = svc.build_statement(svc.parse_level(2, "s", other_page=3), _scope(), now=FROZEN)
    assert later["cm_offset"] == 996


def test_the_page_never_changes_the_root_row():
    """The root sums every child of the parent, so its totals are the same on every page."""
    one, _ = svc.build_statement(svc.parse_level(1), _scope(), now=FROZEN)
    two, _ = svc.build_statement(svc.parse_level(1, other_page=2), _scope(), now=FROZEN)
    assert one == two  # the page is a bind; the text does not move
    root = one[one.index("'root'"):]
    assert "cm_offset" not in root


def test_one_project_counts_needs_review_from_suite_memberships():
    sql, params = svc.build_statement(svc.parse_level(1), _scope(), now=FROZEN)
    assert params["cm_project"] == PROJECT
    assert "FROM suite_memberships sm" in sql
    assert "sm.project_id = :cm_project" in sql
    assert "sm.status = 'needs_review'" in sql
    assert "COUNT(*) FILTER (WHERE needs_review) AS needs_review_count" in sql
    assert "SUM(needs_review_count) AS needs_review_count" in sql


def test_outside_one_project_the_count_is_null_and_nothing_is_read():
    scope = _scope(project_id=None, allowed_project_ids=frozenset({PROJECT, uuid.uuid4()}))
    sql, params = svc.build_statement(svc.parse_level(1), scope, now=FROZEN)
    assert "suite_memberships" not in sql and "cm_project" not in params
    assert "CAST(NULL AS BIGINT) AS needs_review_count" in sql


# ── the assembly ────────────────────────────────────────────────────────────


def test_needs_review_count_stays_null_and_becomes_an_int():
    assert svc.node_stats(_row(), FROZEN)["needs_review_count"] is None
    assert svc.node_stats(_row(needs_review_count=0), FROZEN)["needs_review_count"] == 0
    stats = svc.node_stats(_row(needs_review_count=Decimal(3)), FROZEN)
    assert stats["needs_review_count"] == 3 and type(stats["needs_review_count"]) is int


def test_a_row_without_the_column_fails_loudly():
    row = _row()
    del row.needs_review_count
    with pytest.raises(AttributeError):
        svc.node_stats(row, FROZEN)


def test_a_last_page_has_its_children_and_no_other():
    level = svc.parse_level(1, other_page=2)
    children = [_row(rn=498 + i, key=f"s{i}", child_total=503, needs_review_count=i % 2) for i in range(1, 6)]
    root = _row("root", None, None, tests=503, merged=503, child_total=503, needs_review_count=2)
    payload = svc.assemble(level, [root, *children], FROZEN)
    validate_contract("chart_series", {"kind": payload["kind"], "nodes": payload["nodes"]})
    root_node, *rest = payload["nodes"]
    assert root_node["id"] == "all" and root_node["value"] == 503
    assert root_node["stats"]["needs_review_count"] == 2
    assert [n["id"] for n in rest] == [f"s:s{i}" for i in range(1, 6)]
    assert payload["truncated"] is False


def test_a_middle_page_keeps_its_own_other():
    level = svc.parse_level(1, other_page=2)
    children = [_row(rn=498 + i, key=f"s{i:04d}", child_total=1200) for i in range(1, 499)]
    other = _row("other", None, None, tests=204, merged=204, child_total=1200, needs_review_count=7)
    root = _row("root", None, None, tests=1200, merged=1200, child_total=1200)
    payload = svc.assemble(level, [root, *children, other], FROZEN)
    assert len(payload["nodes"]) == MAX_TREE_NODES
    assert payload["nodes"][-1]["label"] == "Other (204)"
    assert payload["nodes"][-1]["stats"]["needs_review_count"] == 7
    assert payload["truncated"] is True and payload["truncated_total"] == 1200


def test_definitions_state_the_page_and_the_offset():
    one = svc.definitions(svc.parse_level(1), FROZEN)
    three = svc.definitions(svc.parse_level(2, "s", other_page=3), FROZEN)
    assert (one["page"], one["children_offset"]) == (1, 0)
    assert (three["page"], three["children_offset"]) == (3, 996)
    assert "other_page=2" in one["other"] and "does not expand" not in one["other"]
    assert "suite_memberships" in one["needs_review"] and "null otherwise" in one["needs_review"]


# ── the tree contract ───────────────────────────────────────────────────────


def _tree(**stats_extra):
    stats = {
        "test_count": 3, "executions": 0, "pass_rate": None, "flaky_count": 0,
        "flaky_share": 0, "last_executed_at": None, "staleness_days": None,
        "recency": "never", **stats_extra,
    }
    return {
        "kind": "tree",
        "nodes": [{"id": "all", "parent_id": None, "label": "All suites", "value": 3,
                   "measure": None, "stats": stats}],
    }


@pytest.mark.parametrize("extra", [{}, {"needs_review_count": None}, {"needs_review_count": 0},
                                   {"needs_review_count": 3}])
def test_the_tree_contract_accepts_the_count_absent_null_or_a_count(extra):
    validate_contract("chart_series", _tree(**extra))


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True])
def test_the_tree_contract_refuses_a_count_that_is_not_one(bad):
    with pytest.raises(Exception):
        validate_contract("chart_series", _tree(needs_review_count=bad))
