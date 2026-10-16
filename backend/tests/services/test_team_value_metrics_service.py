"""Unit tests for ``services.team_value_metrics_service`` (W08 TRND-08): the
pure attribution. The fnmatch ``_match_owner`` these tests used to pin is gone
-- a test's team is the shared resolver's -- and the database half (grouped
SQL, the sum check, the 60 000-execution project) is
``tests/integration/test_team_value_metrics_postgres.py``.
"""
from __future__ import annotations

import itertools
import uuid
from types import SimpleNamespace

import pytest

from app.services import team_value_metrics_service as svc


def _rule(match_type: str, pattern: str, team: str, priority: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), match_type=match_type, match_pattern=pattern, service_name=team,
        team_name=team, team_contact=None, priority=priority, is_active=True,
    )


def _test(suite="checkout", **over) -> dict:
    row = {"suite_name": suite, "class_name": None, "package_name": None, "full_name": None,
           "own_label": None, "own_owner": None, "test_fingerprint": "fp"}
    row.update(over)
    return row


# ── the team of a test ─────────────────────────────────────────────────────


def test_the_first_matching_rule_names_the_team():
    rules = [_rule("suite_name", "pay*", "Payments", 10), _rule("suite_name", "*", "Platform", 0)]
    assert svc.team_of(rules, None, _test("payments-api")) == "Payments"
    assert svc.team_of(rules, None, _test("search")) == "Platform"


def test_rules_read_the_resolver_s_attributes_not_a_suite_and_name_haystack():
    """``component`` is the class, ``path`` the full name: the attributes every
    other surface resolves on (``failure_detail_service._rule_owner_of``)."""
    rules = [_rule("component", "com.acme.checkout.*", "Checkout")]
    assert svc.team_of(rules, None, _test("smoke", class_name="com.acme.checkout.CartTest")) == "Checkout"
    assert svc.team_of(rules, None, _test("smoke", class_name="com.acme.search.Q")) == svc.UNASSIGNED
    rules = [_rule("path", "*refund*", "Refunds")]
    assert svc.team_of(rules, None, _test(full_name="tests/test_refund.py::test_full")) == "Refunds"


def test_the_component_map_resolves_when_no_rule_matches():
    cmap = {"checkout*": {"team": "Checkout"}, "default": {"team": "Platform"}}
    assert svc.team_of([], cmap, _test("checkout-ui")) == "Checkout"
    assert svc.team_of([], cmap, _test("other")) == "Platform"


@pytest.mark.parametrize("team", ["", "  ", "unknown", "Unknown"])
def test_a_blank_or_unknown_team_is_unassigned(team):
    assert svc.team_of([_rule("suite_name", "*", team)], None, _test()) == svc.UNASSIGNED


def test_no_match_and_no_map_is_unassigned():
    assert svc.team_of([_rule("suite_name", "pay*", "Payments")], None, _test("search")) == svc.UNASSIGNED


# ── the clusters ───────────────────────────────────────────────────────────


def test_a_cluster_spanning_two_teams_is_split_by_member():
    """Size 3, three members: two of A, one of B."""
    out = svc.attribute_clusters([("c1", 3, "A"), ("c1", 3, "A"), ("c1", 3, "B")])
    assert out["A"] == pytest.approx((2.0, 4 / 3))
    assert out["B"] == pytest.approx((1.0, 2 / 3))


def test_a_cluster_with_no_member_found_is_all_unassigned():
    out = svc.attribute_clusters([("c1", 4, None)])
    assert out == {svc.UNASSIGNED: (4.0, 3.0)}


def test_the_team_sums_are_the_headline_s_leg_counts():
    """Whatever the member counts, Σ clustered = Σ size and Σ absorbed = Σ (size − 1)."""
    clusters = [("c1", 3, ["A", "A", "B"]), ("c2", 2, ["C"]), ("c3", 5, []), ("c4", 7, ["A", "B", "C", "A"])]
    members = list(itertools.chain.from_iterable(
        [(cid, size, team) for team in teams] or [(cid, size, None)] for cid, size, teams in clusters
    ))
    out = svc.attribute_clusters(members)
    assert sum(v[0] for v in out.values()) == pytest.approx(sum(size for _, size, _ in clusters))
    assert sum(v[1] for v in out.values()) == pytest.approx(sum(size - 1 for _, size, _ in clusters))


# ── the rows ───────────────────────────────────────────────────────────────


def _tally(team, **over) -> svc.TeamTally:
    tally = svc.TeamTally(team=team)
    for key, value in over.items():
        setattr(tally, key, value)
    return tally


def test_a_team_row_states_its_hours_from_the_rate_card():
    rows = svc.assemble_teams(
        {"A": _tally("A", tests=2, executions=10, evaluated=8, passed=6, clustered=2.0, absorbed=4 / 3)},
        triage_minutes=15, filing_minutes=30,
    )
    (row,) = rows
    assert row["hours"] == {"triage": 0.5, "dedup": 0.67}
    assert row["hours_saved"] == 1.17
    assert row["pass_rate"] == 75.0
    assert set(row) == {
        "team", "tests", "executions", "evaluated", "passed", "pass_rate", "flaky_tests",
        "defects", "mttr_hours", "hours_saved", "hours",
    }


def test_nothing_evaluated_is_a_null_pass_rate_and_no_resolved_defect_a_null_mttr():
    (row,) = svc.assemble_teams(
        {"A": _tally("A", tests=1, executions=3, evaluated=0, passed=0, defects=2)},
        triage_minutes=15, filing_minutes=30,
    )
    assert row["pass_rate"] is None and row["mttr_hours"] is None


def test_mttr_is_the_mean_of_the_resolved_defects():
    (row,) = svc.assemble_teams(
        {"A": _tally("A", defects=3, resolved_hours=[1.0, 2.0, 4.5])},
        triage_minutes=15, filing_minutes=30,
    )
    assert row["mttr_hours"] == 2.5


def test_order_is_hours_then_tests_then_name_with_unassigned_last():
    tallies = {
        svc.UNASSIGNED: _tally(svc.UNASSIGNED, tests=99, clustered=50.0),
        "B": _tally("B", tests=5, clustered=4.0),
        "A": _tally("A", tests=5, clustered=4.0),
        "C": _tally("C", tests=9, clustered=4.0),
        "D": _tally("D", tests=1, clustered=8.0),
    }
    rows = svc.assemble_teams(tallies, triage_minutes=15, filing_minutes=30)
    assert [r["team"] for r in rows] == ["D", "C", "A", "B", svc.UNASSIGNED]


def test_a_team_with_nothing_to_show_is_left_out():
    rows = svc.assemble_teams(
        {svc.UNASSIGNED: _tally(svc.UNASSIGNED), "A": _tally("A", tests=1)},
        triage_minutes=15, filing_minutes=30,
    )
    assert [r["team"] for r in rows] == ["A"]


def test_the_old_matcher_is_gone():
    assert not hasattr(svc, "_match_owner")
