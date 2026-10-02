"""VIZ-207 -- failure groups, without a database.

The statement text (injection: every value is a bind), the include parsing,
the Jaccard edges by hand, the share maths, the roll-ups, the category fold,
the trend axis and the contract of the assembled body. The real statement is
proved against PostgreSQL in ``tests/integration/test_failure_groups_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.models.viz_contracts import validate_contract
from app.services import failure_groups_service as svc
from app.services.analytics_scope import AnalyticsScope
from app.services.failure_signature import NO_MESSAGE_ID, SINGLETONS_ID

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)
HOSTILE = (
    "'; DROP TABLE test_cases; --",
    '<img src=x onerror="window.__xss=1">',
    "constructor",
    "__proto__",
    ":fg_injected_bind",
    "x" * 2000,
)


def _scope(**kw) -> AnalyticsScope:
    base = dict(
        project_id=uuid.uuid4(), allowed_project_ids=None, release_ids=(), suite_names=(), days=30
    )
    base.update(kw)
    return AnalyticsScope(**base)


# ── include ─────────────────────────────────────────────────────────────────


def test_include_accepts_edges_and_nothing():
    assert svc.parse_include(None) == frozenset()
    assert svc.parse_include([]) == frozenset()
    assert svc.parse_include(["edges"]) == frozenset({"edges"})
    assert svc.parse_include(["edges", "edges"]) == frozenset({"edges"})
    assert svc.parse_include("edges") == frozenset({"edges"})


@pytest.mark.parametrize("token", ["Edges", "nodes", "", "edges ", *HOSTILE, 3])
def test_an_unknown_include_is_a_422_that_echoes_nothing(token):
    with pytest.raises(AnalyticsQueryError) as caught:
        svc.parse_include(["edges", token])
    err = caught.value
    assert err.code == "include_enum" and err.param == "include" and err.status_code == 422
    assert err.allowed == ["edges"]
    if isinstance(token, str) and token:
        assert token not in err.message


# ── the statement ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", HOSTILE)
def test_scope_values_are_binds_never_text(value):
    scope = _scope(suite_names=(value, "Checkout"), release_ids=("unattributed",))
    sql, params = svc.build_statement(scope, include_edges=True, now=NOW)
    assert value not in sql
    assert "DROP TABLE" not in sql
    assert value.strip().lower() in str(params.values()) or value in str(params.values())


def test_the_statement_carries_the_tenant_guard_and_the_failing_statuses():
    sql, params = svc.build_statement(_scope(), include_edges=False, now=NOW)
    assert "is_active" in sql  # the life-cycle guard of the shared tenant helper
    assert "tc.status IN ('FAILED', 'BROKEN')" in sql
    assert "tr.created_at >= :period_start" in sql
    assert params["period_start"] == datetime(2026, 8, 23, tzinfo=timezone.utc)
    # The signature patterns are binds, merged in.
    assert {"fsig_space", "fsig_first_line", "fsig_noise_template", "fsig_word", "fsig_digit",
            "fsig_noise_ascii"} <= set(params)
    assert "'pair'" not in sql


def test_edges_add_the_pair_part_only_when_asked():
    with_edges, _ = svc.build_statement(_scope(), include_edges=True, now=NOW)
    assert "'pair'" in with_edges


def test_all_projects_member_scope_is_fail_closed_when_empty():
    sql, _ = svc.build_statement(
        _scope(project_id=None, allowed_project_ids=frozenset()), include_edges=False, now=NOW
    )
    assert "AND FALSE" in sql


@pytest.mark.parametrize("days,grain", [(7, "day"), (90, "day"), (91, "week"), (365, "week")])
def test_trend_grain_follows_the_window(days, grain):
    sql, _ = svc.build_statement(_scope(days=days), include_edges=False, now=NOW)
    assert f"date_trunc('{grain}'" in sql
    assert svc.trend_grain(days) == grain


# ── the trend axis ──────────────────────────────────────────────────────────


def test_a_daily_axis_is_every_utc_day_of_the_window():
    axis = svc.trend_axis(7, NOW)
    assert axis == [f"2026-09-{d:02d}" for d in range(15, 22)]


def test_a_weekly_axis_is_every_monday():
    axis = svc.trend_axis(120, NOW)
    days = [datetime.fromisoformat(x) for x in axis]
    assert all(d.weekday() == 0 for d in days)
    assert axis[-1] == "2026-09-21"  # NOW is a Monday
    assert days[0] <= datetime(2026, 5, 25) and len(axis) == 18


# ── edges (M-207e: a pair with Jaccard 0 is no edge) ───────────────────────


def test_jaccard_by_hand():
    tests = {"a": 4, "b": 4, "c": 10, "d": 3}
    edges = svc.edges_from_pairs(
        [("a", "b", 2), ("a", "c", 2), ("b", "d", 0), ("c", "d", 3)], tests
    )
    # a-b: 2 / (4+4-2) = 0.3333; a-c: 2 / (4+10-2) = 0.1667 (< 0.2, dropped);
    # b-d: nothing shared (no edge); c-d: 3 / (10+3-3) = 0.3
    assert edges == [
        {"source": "a", "target": "b", "weight": 0.3333},
        {"source": "c", "target": "d", "weight": 0.3},
    ]


def test_the_threshold_is_inclusive_and_unknown_groups_are_ignored():
    tests = {"a": 5, "b": 5, "x": 1}
    # 2/(5+5-2) = 0.25 kept; 1/(5+1-1) = 0.2 kept (inclusive); "zz" is not a node.
    edges = svc.edges_from_pairs([("a", "b", 2), ("x", "a", 1), ("a", "zz", 5)], tests)
    assert [(e["source"], e["target"], e["weight"]) for e in edges] == [
        ("a", "b", 0.25), ("a", "x", 0.2)
    ]


def test_at_most_max_edges_strongest_first_ties_by_code_point():
    tests = {f"g{i:03d}": 2 for i in range(40)}
    pairs = [(f"g{i:03d}", f"g{j:03d}", 1 + (i + j) % 2) for i in range(40) for j in range(i + 1, 40)]
    edges = svc.edges_from_pairs(pairs, tests)
    assert len(edges) == svc.MAX_EDGES
    weights = [e["weight"] for e in edges]
    assert weights == sorted(weights, reverse=True)
    full = [e for e in edges if e["weight"] == 1.0]
    assert full == sorted(full, key=lambda e: (e["source"], e["target"]))


# ── assembly ────────────────────────────────────────────────────────────────


def _row(part, **kw):
    base = dict(part=part, rn=None, sig=None, k=None, k2=None, label=None, n1=None, n2=None,
                n3=None, n4=None, t1=None, t2=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _group(rn, sig, n, *, tests=1, runs=1, lines=1, label=None, at=NOW):
    return _row("group", rn=rn, sig=sig, label=label or f"Raw {sig} 1", n1=n, n2=tests,
                n3=runs, n4=lines, t1=at - timedelta(days=2), t2=at)


def _rows(*, total, no_message=0, singletons=(0, 0), groups=(), group_total=None,
          grouped=None, extra=()):
    groups = list(groups)
    return [
        _row("totals", n1=total, n2=no_message, n3=no_message, n4=no_message),
        _row("rollup", n1=singletons[0], n2=singletons[1],
             n3=len(groups) if group_total is None else group_total,
             n4=sum(g.n1 for g in groups) if grouped is None else grouped),
        *groups,
        *extra,
    ]


def test_shares_have_one_denominator_and_sum_to_one_untruncated():
    rows = _rows(
        total=20, no_message=3, singletons=(2, 2),
        groups=[_group(1, "timeout #", 10), _group(2, "assert #", 5)],
    )
    body = svc.assemble(rows, days=30, now=NOW, include_edges=False)
    shares = [g["share_of_failures"] for g in body["groups"]]
    assert shares == [0.5, 0.25]
    total = (
        sum(shares) + body["no_message"]["share_of_failures"]
        + body["singletons"]["share_of_failures"] + body["omitted"]["share_of_failures"]
    )
    assert total == pytest.approx(1.0)
    assert body["truncated"] is False and body["truncated_total"] is None
    assert body["omitted"] == {"group_count": 0, "failure_count": 0, "share_of_failures": 0.0}
    assert body["no_message"]["id"] == NO_MESSAGE_ID and body["singletons"]["id"] == SINGLETONS_ID


def test_beyond_the_cap_the_rest_is_omitted_and_declared():
    rows = _rows(total=30, groups=[_group(1, "a", 10), _group(2, "b", 8)],
                 group_total=5, grouped=30)
    body = svc.assemble(rows, days=30, now=NOW, include_edges=False)
    assert body["truncated"] is True and body["truncated_total"] == 5
    assert body["omitted"] == {"group_count": 3, "failure_count": 12, "share_of_failures": 0.4}
    shown = sum(g["share_of_failures"] for g in body["groups"])
    assert shown == pytest.approx(0.6) and shown <= 1


def test_no_failure_means_null_shares_not_zero():
    body = svc.assemble(_rows(total=0), days=30, now=NOW, include_edges=False)
    assert body["groups"] == [] and body["nodes"] == [] and body["edges"] == []
    assert body["total_failures"] == 0
    for key in ("no_message", "singletons", "omitted"):
        assert body[key]["share_of_failures"] is None
    validate_contract("chart_series", body)


def test_categories_fold_beyond_eight_and_name_the_dominant_one():
    cats = [_row("category", rn=1, sig="a", k=f"c{i}", n1=10 - i) for i in range(10)]
    body = svc.assemble(_rows(total=55, groups=[_group(1, "a", 55)], extra=cats),
                        days=30, now=NOW, include_edges=False)
    group = body["groups"][0]
    names = [c["category"] for c in group["categories"]]
    assert len(names) == 8 and "other" in names
    assert {c["category"]: c["count"] for c in group["categories"]}["other"] == 3 + 2 + 1
    assert group["dominant_category"] == "c0"
    assert body["nodes"][0]["group"] == "c0"


def test_a_node_without_a_category_omits_group_never_null():
    body = svc.assemble(_rows(total=4, groups=[_group(1, "a", 4)]), days=30, now=NOW,
                        include_edges=False)
    assert "group" not in body["nodes"][0]
    validate_contract("chart_series", body)


def test_the_trend_is_zero_filled_over_the_window():
    extra = [_row("trend", rn=1, sig="a", k="2026-09-19", n1=3),
             _row("trend", rn=1, sig="a", k="2026-09-21", n1=1)]
    body = svc.assemble(_rows(total=4, groups=[_group(1, "a", 4)], extra=extra),
                        days=7, now=NOW, include_edges=False)
    trend = body["groups"][0]["trend"]
    assert [p["x"] for p in trend] == svc.trend_axis(7, NOW)
    assert [p["y"] for p in trend] == [0, 0, 0, 0, 3, 0, 1]
    assert body["trend_grain"] == "day"


def test_top_tests_rank_by_count_then_fingerprint():
    extra = [_row("test", rn=1, sig="a", k=fp, label=f"t_{fp}", n1=n)
             for fp, n in (("f3", 2), ("f1", 2), ("f2", 5))]
    body = svc.assemble(_rows(total=9, groups=[_group(1, "a", 9)], extra=extra),
                        days=30, now=NOW, include_edges=False)
    assert [t["fingerprint"] for t in body["groups"][0]["top_tests"]] == ["f2", "f1", "f3"]


P_A, P_B = str(uuid.UUID(int=1)), str(uuid.UUID(int=2))


def test_one_fingerprint_in_two_projects_is_two_top_tests():
    """R1-1: a fingerprint is not project-unique. Under All Projects the same
    test name in staging and prod is two tests, each named with its project."""
    extra = [_row("test", rn=1, sig="a", k="fx", k2=pid, label="test_fx", n1=n)
             for pid, n in ((P_A, 2), (P_B, 3))]
    body = svc.assemble(_rows(total=5, groups=[_group(1, "a", 5, tests=2)], extra=extra),
                        days=30, now=NOW, include_edges=False)
    group = body["groups"][0]
    assert group["affected_tests"] == 2
    assert group["top_tests"] == [
        {"fingerprint": "fx", "project_id": P_B, "name": "test_fx", "count": 3},
        {"fingerprint": "fx", "project_id": P_A, "name": "test_fx", "count": 2},
    ]
    validate_contract("chart_series", body)


def test_top_test_ties_break_by_fingerprint_then_project():
    extra = [_row("test", rn=1, sig="a", k=fp, k2=pid, label="t", n1=2)
             for fp, pid in (("fx", P_B), ("fx", P_A), ("fa", P_B))]
    body = svc.assemble(_rows(total=6, groups=[_group(1, "a", 6, tests=3)], extra=extra),
                        days=30, now=NOW, include_edges=False)
    assert [(t["fingerprint"], t["project_id"]) for t in body["groups"][0]["top_tests"]] == [
        ("fa", P_B), ("fx", P_A), ("fx", P_B)]


def test_the_statement_keys_tests_on_project_and_fingerprint():
    """R1-1 (text half; the PG test proves the numbers): every per-test count
    and the edge join carry the project."""
    sql, _ = svc.build_statement(_scope(project_id=None), include_edges=True, now=NOW)
    assert "COUNT(DISTINCT fp)" not in sql
    assert sql.count("COUNT(DISTINCT (project_id, fp))") == 2  # no_message + group
    assert "b.project_id = a.project_id" in sql
    assert "GROUP BY sig, project_id, fp" in sql


# ── R1-9: a future-dated failure ────────────────────────────────────────────


def test_a_future_failure_is_counted_and_declared_outside_the_window():
    """The heatmap's rule: a clock-skewed run stays in the counts and is named
    in ``outside_window``, so a sparkline that sums short is never silent."""
    extra = [_row("trend", rn=1, sig="a", k="2026-09-21", n1=1),
             _row("trend", rn=1, sig="a", k="2026-09-23", n1=2),
             _row("trend", rn=2, sig="b", k="2026-09-25", n1=1),
             _row("trend", rn=2, sig="b", k="2026-09-20", n1=1)]
    body = svc.assemble(
        _rows(total=5, groups=[_group(1, "a", 3), _group(2, "b", 2)], extra=extra),
        days=7, now=NOW, include_edges=False,
    )
    a, b = body["groups"]
    assert a["failure_count"] == 3 and sum(p["y"] for p in a["trend"]) == 1
    assert b["failure_count"] == 2 and sum(p["y"] for p in b["trend"]) == 1
    assert body["outside_window"] == {
        "buckets": 2, "executions": 3, "first": "2026-09-23", "last": "2026-09-25",
    }
    assert sum(p["y"] for g in body["groups"] for p in g["trend"]) + 3 == 5
    from app.models.viz_contracts import OutsideWindow

    OutsideWindow.model_validate(body["outside_window"])
    assert "outside_window" in svc.ENVELOPE_KEYS


def test_nothing_outside_the_window_is_none_not_a_zero():
    extra = [_row("trend", rn=1, sig="a", k="2026-09-21", n1=2)]
    body = svc.assemble(_rows(total=2, groups=[_group(1, "a", 2)], extra=extra),
                        days=7, now=NOW, include_edges=False)
    assert body["outside_window"] is None


@pytest.mark.parametrize("label", HOSTILE)
def test_hostile_labels_round_trip_as_data_and_are_capped(label):
    body = svc.assemble(
        _rows(total=2, groups=[_group(1, "sig", 2, label=label)]),
        days=30, now=NOW, include_edges=False,
    )
    out = body["groups"][0]["label"]
    assert body["nodes"][0]["label"] == out
    if len(label) <= svc.LABEL_LENGTH:
        assert out == label
    else:
        assert len(out) == svc.LABEL_LENGTH and out.endswith("…")
        assert out[:-1] == label[: svc.LABEL_LENGTH - 1]
    validate_contract("chart_series", body)


def test_groups_follow_rank_and_edges_need_include():
    pairs = [_row("pair", sig="a", k="b", n1=2)]
    rows = _rows(total=9, groups=[_group(2, "b", 4, tests=2), _group(1, "a", 5, tests=3)],
                 extra=pairs)
    plain = svc.assemble(rows, days=30, now=NOW, include_edges=False)
    assert [g["id"] for g in plain["groups"]] == ["a", "b"]
    assert plain["edges"] == []
    linked = svc.assemble(rows, days=30, now=NOW, include_edges=True)
    assert linked["edges"] == [{"source": "a", "target": "b", "weight": round(2 / 3, 4)}]
    validate_contract("chart_series", linked)


def test_instants_are_utc_rfc3339():
    body = svc.assemble(_rows(total=2, groups=[_group(1, "a", 2)]), days=30, now=NOW,
                        include_edges=False)
    assert body["groups"][0]["last_seen"] == "2026-09-21T12:00:00+00:00"


def test_definitions_say_what_is_not_counted_and_that_no_model_is_involved():
    d = svc.definitions(30, include_edges=False)
    assert "skipped" in d["population"] and "not requested" in d["edges"]
    assert "no model" in d["ai"]
    assert "ISO week" in svc.definitions(120, include_edges=True)["trend"]


def test_definitions_say_first_and_last_seen_are_the_window_s():
    """R1-2: a group failing for a year shows the window start as first_seen;
    the definition says so, or a "First seen" column reads as a new failure."""
    d = svc.definitions(30, include_edges=False)
    for key in ("first_seen", "last_seen"):
        assert "in the window" in d[key] and "not" in d[key]
    assert "project" in d["affected_tests"]
    assert "outside_window" in d["outside_window"] and "failure_count" in d["outside_window"]

