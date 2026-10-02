"""VIZ-206 -- the coverage map, everything a database cannot answer.

The golden numbers, the never-executed union, the recency states on real
foreign keys and the cache live in ``tests/integration/test_coverage_map_postgres.py``.
Here:

1. **Parsing.** ``depth`` is an allow-list; ``suite`` / ``class_key`` are
   bounded, NUL-free, required where read and refused where not, and never
   echoed in a refusal.
2. **Nothing from the request reaches SQL.** The keys are binds: the statement
   text is byte-identical whatever their values, hostile or not.
3. **The tenant rule on both populations.** The executed rows AND the canonical
   (idle) rows carry the ``is_active`` project guard; deleted canonicals are out.
4. **The assembly.** Ids, labels, the root-plus-children shape, the Other
   roll-up at the 499-child cap, ``nodes: []`` for no data, the null-not-zero
   rules and the recency pairing -- every payload validated by the C3 validator.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.analytics_errors import AnalyticsQueryError
from app.models.viz_contracts import MAX_TREE_NODES, validate_contract
from app.services import coverage_map_service as svc
from app.services.analytics_scope import AnalyticsScope

FROZEN = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)

HOSTILE = (
    "x'; DROP TABLE test_runs; --",
    "x' OR '1'='1",
    "x\") UNION SELECT NULL --",
    "x/*c*/",
    ":cm_suite",
    "ｘ",
    "x‮",
    "constructor",
    "__proto__",
    "<img src=x onerror=alert(1)>",
    "s" * 500,
)


def _scope(**kwargs) -> AnalyticsScope:
    base = dict(
        project_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        allowed_project_ids=None,
        release_ids=(),
        suite_names=(),
        days=30,
    )
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def _row(part="child", rn=1, key="k", label="K", parent_label=None, *, tests=1,
         executions=0, passed=0, failed=0, broken=0, skipped=0, unknown=0,
         flaky=0, last_at=None, unknown_tests=0, merged=1, child_total=1):
    return SimpleNamespace(
        part=part, rn=rn, child_key=key, child_label=label, parent_label=parent_label,
        test_count=tests, executions=executions, passed=passed, failed=failed,
        broken=broken, skipped=skipped, unknown=unknown, flaky_count=flaky,
        last_at=last_at, unknown_tests=unknown_tests, merged=merged,
        child_total=child_total,
    )


def _root_of(children, *, parent_label=None):
    """The root row the statement computes: sums of every child."""
    def total(name):
        return sum(getattr(c, name) for c in children)

    dates = [c.last_at for c in children if c.last_at is not None]
    return _row(
        "root", None, None, None, parent_label,
        tests=total("test_count"), executions=total("executions"),
        passed=total("passed"), failed=total("failed"), broken=total("broken"),
        skipped=total("skipped"), unknown=total("unknown"), flaky=total("flaky_count"),
        last_at=max(dates) if dates else None, unknown_tests=total("unknown_tests"),
        merged=len(children), child_total=len(children),
    )


def _valid(payload: dict) -> None:
    body = {key: value for key, value in payload.items() if key in ("kind", "nodes")}
    validate_contract("chart_series", body)


# ── 1. parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("depth", [0, 4, -1, True, "1", 1.0, None])
def test_depth_outside_the_allow_list_is_a_422(depth):
    with pytest.raises(AnalyticsQueryError) as err:
        svc.parse_level(depth)
    assert err.value.code == "depth_enum"
    assert err.value.param == "depth"
    assert err.value.allowed == [1, 2, 3]


def test_the_three_levels_parse():
    assert svc.parse_level(1) == svc.CoverageLevel(1)
    assert svc.parse_level(2, "payments") == svc.CoverageLevel(2, "payments")
    assert svc.parse_level(3, "payments", "__none__") == svc.CoverageLevel(
        3, "payments", "__none__"
    )


@pytest.mark.parametrize(
    "depth, suite, class_key, param",
    [(2, None, None, "suite"), (2, "", None, "suite"), (3, None, "c", "suite"),
     (3, "s", None, "class_key"), (3, "s", "", "class_key")],
)
def test_a_missing_parent_is_a_422(depth, suite, class_key, param):
    with pytest.raises(AnalyticsQueryError) as err:
        svc.parse_level(depth, suite, class_key)
    assert (err.value.code, err.value.param) == ("parent_required", param)


@pytest.mark.parametrize(
    "depth, suite, class_key, param",
    [(1, "s", None, "suite"), (1, None, "c", "class_key"), (2, "s", "c", "class_key")],
)
def test_a_parent_the_depth_does_not_read_is_refused_not_dropped(depth, suite, class_key, param):
    with pytest.raises(AnalyticsQueryError) as err:
        svc.parse_level(depth, suite, class_key)
    assert (err.value.code, err.value.param) == ("parent_unexpected", param)


def test_a_key_over_500_characters_is_a_422_and_500_is_fine():
    assert svc.parse_level(2, "s" * 500).suite == "s" * 500
    with pytest.raises(AnalyticsQueryError) as err:
        svc.parse_level(2, "s" * 501)
    assert err.value.code == "parent_length" and err.value.allowed == {"max": 500}
    with pytest.raises(AnalyticsQueryError) as err:
        svc.parse_level(3, "s", "c" * 501)
    assert (err.value.code, err.value.param) == ("parent_length", "class_key")


def test_a_nul_in_a_key_is_a_422_not_a_driver_500():
    with pytest.raises(AnalyticsQueryError) as err:
        svc.parse_level(2, "pay\x00ments")
    assert err.value.code == "parent_format"


@pytest.mark.parametrize("value", [*HOSTILE, "s" * 501, "a\x00b"])
def test_a_refusal_never_echoes_the_key(value):
    for args in ((2, value), (3, "s", value), (1, value)):
        try:
            svc.parse_level(*args)
        except AnalyticsQueryError as err:
            assert value not in err.message
            assert value not in str(err)


@pytest.mark.parametrize("value", HOSTILE)
def test_hostile_keys_are_accepted_as_data(value):
    """A suite or class may really be called ``__proto__``: it is a key, not code."""
    assert svc.parse_level(3, value, value) == svc.CoverageLevel(3, value, value)


# ── 2. nothing from the request reaches SQL ────────────────────────────────


@pytest.mark.parametrize("value", HOSTILE)
def test_a_key_is_a_bind_and_never_reaches_the_sql(value):
    reference, _ = svc.build_statement(svc.CoverageLevel(3, "s", "c"), _scope(), now=FROZEN)
    sql, params = svc.build_statement(svc.CoverageLevel(3, value, value), _scope(), now=FROZEN)
    assert sql == reference, "the statement text changed with a key's VALUE"
    assert params["cm_suite"] == value and params["cm_class"] == value


def test_each_depth_selects_its_own_hard_coded_child_key():
    sqls = {
        depth: svc.build_statement(level, _scope(), now=FROZEN)[0]
        for depth, level in (
            (1, svc.CoverageLevel(1)),
            (2, svc.CoverageLevel(2, "s")),
            (3, svc.CoverageLevel(3, "s", "c")),
        )
    }
    assert "SELECT suite_key AS child_key" in sqls[1]
    assert "SELECT COALESCE(class_name, '__none__') AS child_key" in sqls[2]
    assert "SELECT fp AS child_key" in sqls[3]
    assert ":cm_suite" not in sqls[1] and ":cm_class" not in sqls[1]
    assert ":cm_suite" in sqls[2] and ":cm_class" not in sqls[2]
    assert ":cm_suite" in sqls[3] and ":cm_class" in sqls[3]


def test_the_drill_uses_the_parents_own_key_expression_on_both_populations():
    """The level-2 predicate is the expression that made the level-1 id, so a
    level partitions its parent -- including the ``(none)`` bucket that a
    ``LOWER(effective suite) = :x`` filter could never select."""
    from app.services.chart_data_service import DIMENSIONS

    sql, _ = svc.build_statement(svc.CoverageLevel(2, "(none)"), _scope(), now=FROZEN)
    assert f"AND {DIMENSIONS['suite'].sql} = :cm_suite" in sql
    assert (
        "AND LOWER(COALESCE(NULLIF(TRIM(ts.name), ''), '(none)')) = :cm_suite" in sql
    )


def test_both_populations_carry_the_tenant_and_is_active_guard():
    sql, params = svc.build_statement(svc.CoverageLevel(1), _scope(), now=FROZEN)
    assert "AND tr.project_id = :project_id" in sql
    assert "AND c.project_id = :project_id" in sql
    assert "tr.project_id IN (SELECT id FROM projects WHERE is_active)" in sql
    assert "c.project_id IN (SELECT id FROM projects WHERE is_active)" in sql
    assert params["project_id"] == "11111111-1111-1111-1111-111111111111"


def test_deleted_canonicals_are_excluded_and_the_window_is_a_bind():
    sql, params = svc.build_statement(svc.CoverageLevel(1), _scope(days=7), now=FROZEN)
    assert "WHERE c.status <> 'deleted'" in sql
    assert "tr.created_at >= :period_start" in sql
    assert params["period_start"] == datetime(2026, 9, 15, tzinfo=timezone.utc)


def test_level_one_without_a_suite_filter_reuses_the_executed_scan():
    sql, _ = svc.build_statement(svc.CoverageLevel(1), _scope(), now=FROZEN)
    assert "seen AS (SELECT fp FROM ex" in sql
    assert sql.count("FROM test_cases tc JOIN test_runs tr") == 1


@pytest.mark.parametrize(
    "level, scope",
    [(svc.CoverageLevel(1), _scope(suite_names=("Payments",))),
     (svc.CoverageLevel(2, "payments"), _scope()),
     (svc.CoverageLevel(3, "payments", "c"), _scope())],
)
def test_idle_is_judged_against_every_execution_in_scope_not_the_level(level, scope):
    """A test that ran under ANOTHER suite is not idle: the anti-join's own scan
    has the window, project and releases but no suite predicate."""
    sql, _ = svc.build_statement(level, scope, now=FROZEN)
    seen = sql.split("seen AS (", 1)[1].split("), idle AS (", 1)[0]
    assert "SELECT DISTINCT tc.test_fingerprint AS fp" in seen
    assert ":cm_suite" not in seen and ":suite_name" not in seen
    assert "tr.project_id = :project_id" in seen


def test_the_scope_suite_filter_comes_from_analytics_scope_on_both_sides():
    sql, params = svc.build_statement(
        svc.CoverageLevel(1), _scope(suite_names=("Payments", "Orders")), now=FROZEN
    )
    assert "IN :suite_names" in sql  # suite_filter_sql, rows by effective suite
    assert "LOWER(TRIM(coalesce(ts.name, ''))) IN (LOWER(TRIM(:suite_name_0))" in sql
    assert params["suite_names"] == ["payments", "orders"]


def test_a_release_filter_bounds_executions_and_the_idle_judgement():
    rid = str(uuid.uuid4())
    sql, params = svc.build_statement(
        svc.CoverageLevel(2, "s"), _scope(release_ids=(rid,)), now=FROZEN
    )
    assert sql.count("AND tr.primary_release_id = :release_id") == 2
    assert params["release_id"] == rid


def test_the_cap_and_the_fold_are_binds_derived_from_the_contract():
    _, params = svc.build_statement(svc.CoverageLevel(1), _scope(), now=FROZEN)
    assert params["cm_cap"] == MAX_TREE_NODES - 1 == svc.MAX_CHILDREN
    assert params["cm_keep"] == MAX_TREE_NODES - 2 == svc.KEPT_WHEN_FOLDED


# ── 3. stats: null, never zero; recency pairing ────────────────────────────


def test_no_executions_is_a_null_pass_rate_never_zero():
    stats = svc.node_stats(_row(tests=3), FROZEN)
    assert stats["executions"] == 0 and stats["pass_rate"] is None


def test_only_skipped_executions_is_a_null_pass_rate():
    stats = svc.node_stats(_row(tests=1, executions=4, skipped=3, unknown=1), FROZEN)
    assert stats["executions"] == 4 and stats["pass_rate"] is None


def test_pass_rate_excludes_skipped_from_the_denominator():
    stats = svc.node_stats(
        _row(tests=2, executions=10, passed=6, failed=1, broken=1, skipped=2), FROZEN
    )
    assert stats["pass_rate"] == 75.0


def test_flaky_share_is_null_with_no_tests_and_a_ratio_otherwise():
    assert svc.node_stats(_row(tests=0), FROZEN)["flaky_share"] is None
    stats = svc.node_stats(_row(tests=3, flaky=1), FROZEN)
    assert stats["flaky_count"] == 1 and stats["flaky_share"] == 0.3333


def test_a_dated_node_is_seen_with_its_calendar_day_staleness():
    last = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)
    stats = svc.node_stats(_row(last_at=last), FROZEN)
    assert stats["recency"] == "seen"
    assert stats["last_executed_at"] == "2026-09-11T18:00:00+00:00"
    # 9 days 18 hours elapsed, 10 UTC calendar days: the definition says days.
    assert stats["staleness_days"] == 10


def test_staleness_reads_the_runs_date_against_the_request_clock():
    """M-206c: computed from ``now`` minus the RUN's date, not 0 for "seen"."""
    last = FROZEN - timedelta(days=3)
    assert svc.node_stats(_row(last_at=last), FROZEN)["staleness_days"] == 3
    assert svc.node_stats(_row(last_at=last), FROZEN + timedelta(days=1))["staleness_days"] == 4


def test_a_future_dated_run_is_zero_days_stale_not_negative():
    assert svc.staleness_days(FROZEN + timedelta(days=2), FROZEN) == 0


def test_a_lost_last_run_is_unknown_not_never():
    """M-206b: a cleared ``last_seen_run_id`` must not read as "never run"."""
    stats = svc.node_stats(_row(tests=2, unknown_tests=1), FROZEN)
    assert stats["recency"] == "unknown"
    assert stats["last_executed_at"] is None and stats["staleness_days"] is None


def test_no_date_and_nothing_lost_is_never():
    stats = svc.node_stats(_row(tests=2), FROZEN)
    assert stats["recency"] == "never"
    assert stats["last_executed_at"] is None and stats["staleness_days"] is None


def test_any_dated_test_makes_the_node_seen():
    stats = svc.node_stats(_row(tests=3, unknown_tests=2, last_at=FROZEN), FROZEN)
    assert stats["recency"] == "seen" and stats["staleness_days"] == 0


def test_every_stats_key_is_always_present():
    for row in (_row(), _row(last_at=FROZEN), _row(unknown_tests=1)):
        assert set(svc.node_stats(row, FROZEN)) == {
            "test_count", "executions", "pass_rate", "flaky_count", "flaky_share",
            "last_executed_at", "staleness_days", "recency",
        }


# ── 4. assembly ────────────────────────────────────────────────────────────


def test_level_one_is_the_all_suites_root_and_its_suites():
    children = [
        _row(rn=1, key="payments", label="Payments", tests=4, executions=8, passed=6,
             failed=2, last_at=FROZEN - timedelta(days=1), flaky=1),
        _row(rn=2, key="(none)", label="(none)", tests=1),
    ]
    payload = svc.assemble(svc.CoverageLevel(1), [_root_of(children), *children], FROZEN)
    _valid(payload)
    root, pay, none = payload["nodes"]
    assert root == {
        "id": "all", "parent_id": None, "label": "All suites", "value": 5,
        "measure": 75.0, "stats": root["stats"],
    }
    assert (pay["id"], pay["parent_id"], pay["label"], pay["value"]) == (
        "s:payments", "all", "Payments", 4,
    )
    assert pay["measure"] == pay["stats"]["pass_rate"] == 75.0
    assert none["id"] == "s:(none)" and none["measure"] is None
    assert payload["truncated"] is False and payload["truncated_total"] is None


def test_level_two_root_is_the_suite_and_children_are_class_keys():
    children = [
        _row(rn=1, key="tests/api/test_pay.py", label="tests/api/test_pay.py", tests=2),
        _row(rn=2, key="__none__", label=None, tests=1),
    ]
    level = svc.CoverageLevel(2, "payments")
    payload = svc.assemble(level, [_root_of(children, parent_label="Payments"), *children], FROZEN)
    _valid(payload)
    root, file_node, ungrouped = payload["nodes"]
    assert (root["id"], root["label"], root["value"]) == ("s:payments", "Payments", 3)
    sep = svc.KEY_SEPARATOR
    assert file_node["id"] == f"c:payments{sep}tests/api/test_pay.py"
    assert file_node["label"] == "tests/api/test_pay.py"
    assert (ungrouped["id"], ungrouped["label"]) == (f"c:payments{sep}__none__", "(ungrouped)")


def test_level_three_root_is_the_class_and_children_are_tests():
    children = [_row(rn=1, key="fp1", label="test_refund", tests=1)]
    level = svc.CoverageLevel(3, "payments", "__none__")
    payload = svc.assemble(level, [_root_of(children), *children], FROZEN)
    _valid(payload)
    root, test = payload["nodes"]
    assert (root["id"], root["label"]) == (f"c:payments{svc.KEY_SEPARATOR}__none__", "(ungrouped)")
    assert (test["id"], test["label"], test["parent_id"]) == ("t:fp1", "test_refund", root["id"])


def test_a_child_id_strips_back_to_the_key_the_next_request_sends():
    """The keys are opaque; a reader strips the prefix it knows."""
    key = f"we{svc.KEY_SEPARATOR}ird"  # a key may even contain the separator
    child = _row(rn=1, key=key, label=key)
    payload = svc.assemble(svc.CoverageLevel(2, "s"), [_root_of([child]), child], FROZEN)
    node_id = payload["nodes"][1]["id"]
    prefix = f"c:s{svc.KEY_SEPARATOR}"
    assert node_id.startswith(prefix) and node_id[len(prefix):] == key


def test_level_two_root_falls_back_to_the_key_when_no_label_came_back():
    child = _row(rn=1, key="__none__")
    payload = svc.assemble(svc.CoverageLevel(2, "payments"), [_root_of([child]), child], FROZEN)
    assert payload["nodes"][0]["label"] == "payments"


def test_no_children_is_an_empty_tree_not_a_zero_root():
    payload = svc.assemble(svc.CoverageLevel(2, "gone"), [], FROZEN)
    assert payload["nodes"] == [] and payload["truncated"] is False
    _valid(payload)


def test_children_keep_the_statement_order_and_the_root_comes_first():
    children = [_row(rn=n, key=f"k{n}", label=f"K{n}", tests=10 - n) for n in (3, 1, 2)]
    payload = svc.assemble(svc.CoverageLevel(1), [_root_of(children), *children], FROZEN)
    assert [n["id"] for n in payload["nodes"]] == ["all", "s:k1", "s:k2", "s:k3"]


def test_other_folds_the_tail_without_a_pass_rate_and_reports_the_true_count():
    kept = [
        _row(rn=n, key=f"k{n:04d}", label=f"k{n}", tests=2, executions=2, passed=2,
             child_total=1200)
        for n in range(1, svc.KEPT_WHEN_FOLDED + 1)
    ]
    other = _row("other", None, None, None, tests=702, executions=900, passed=800,
                 failed=100, merged=702, child_total=1200, last_at=FROZEN)
    root = _row("root", None, None, None, tests=2 * len(kept) + 702,
                executions=2 * len(kept) + 900, passed=2 * len(kept) + 800, failed=100,
                merged=1200, child_total=1200, last_at=FROZEN)
    payload = svc.assemble(svc.CoverageLevel(1), [root, *kept, other], FROZEN)
    _valid(payload)
    nodes = payload["nodes"]
    assert len(nodes) == MAX_TREE_NODES
    tail = nodes[-1]
    assert tail["id"] == "other:all" and tail["label"] == "Other (702)"
    assert tail["value"] == 702 and tail["measure"] is None
    assert tail["stats"]["pass_rate"] is None and tail["stats"]["executions"] == 900
    assert payload["truncated"] is True and payload["truncated_total"] == 1200
    # The root still sums every child, folded or not.
    assert nodes[0]["value"] == sum(n["value"] for n in nodes[1:])


def test_hostile_labels_round_trip_as_data():
    children = [
        _row(rn=i + 1, key=name, label=name)
        for i, name in enumerate(("constructor", "__proto__", "<img src=x onerror=alert(1)>",
                                  "L" * 1000))
    ]
    payload = svc.assemble(svc.CoverageLevel(1), [_root_of(children), *children], FROZEN)
    _valid(payload)
    labels = [n["label"] for n in payload["nodes"][1:]]
    assert labels == ["constructor", "__proto__", "<img src=x onerror=alert(1)>", "L" * 1000]
    assert [n["id"] for n in payload["nodes"][1:]] == [f"s:{label}" for label in labels]


def test_a_mixed_population_validates_under_every_null_rule():
    """Seen, unknown and never children side by side, executions 0 and > 0."""
    children = [
        _row(rn=1, key="a", label="a", tests=3, executions=5, passed=5, last_at=FROZEN),
        _row(rn=2, key="b", label="b", tests=2, unknown_tests=2),
        _row(rn=3, key="c", label="c", tests=1),
        _row(rn=4, key="d", label="d", tests=1, executions=2, skipped=2, last_at=FROZEN),
    ]
    payload = svc.assemble(svc.CoverageLevel(1), [_root_of(children), *children], FROZEN)
    _valid(payload)
    recency = [n["stats"]["recency"] for n in payload["nodes"]]
    assert recency == ["seen", "seen", "unknown", "never", "seen"]


def test_definitions_say_what_the_numbers_are_not():
    defs = svc.definitions(svc.CoverageLevel(2, "s"), FROZEN)
    assert defs["coverage"] == "test_execution"
    assert defs["level"] == "class" and defs["depth"] == 2
    assert "not code coverage" in defs["coverage_note"]
    assert "unknown" in defs["recency"] and "never" in defs["recency"]
    assert "created_at" in defs["window_clock"]


async def test_a_denied_scope_never_touches_the_database():
    class _NoDb:
        async def execute(self, *_a, **_k):  # pragma: no cover - must not run
            raise AssertionError("queried a denied scope")

    payload = await svc.build_coverage_map(
        _NoDb(), _scope(denied=True), svc.CoverageLevel(1), now=FROZEN
    )
    assert payload["nodes"] == [] and payload["definitions"]["level"] == "suite"
