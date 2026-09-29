"""The suites catalogue's order is total: size first, then name.

Regression for a random CI failure (PR #165): ``GET /test-management/suites``
sorted on ``test_count`` alone, so suites of the same size kept the order
Postgres happened to return them in, and the parity golden in
``tests/integration/test_analytics_envelope_postgres.py`` failed when that
order changed.
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("fastapi")


def _suite(name, count):
    return {"suite_name": name, "test_count": count}


def test_tied_suites_come_back_by_name_whatever_order_they_arrive_in():
    from app.routers.test_management_exports import order_suites

    rows = [
        _suite("CheckoutSuite", 25),
        _suite("InventorySuite", 5),
        _suite("NotificationSuite", 4),
        _suite("ReportingSuite", 4),
        _suite("LegacyBatchSuite", 3),
        _suite("QuarantinedSuite", 3),
    ]
    expected = [row["suite_name"] for row in rows]
    # Every arrival order the database could produce gives the same answer.
    for arrival in itertools.permutations(rows):
        assert [row["suite_name"] for row in order_suites(arrival)] == expected


def test_a_suite_without_a_name_does_not_break_the_order():
    from app.routers.test_management_exports import order_suites

    ordered = order_suites([_suite("b", 1), _suite(None, 1), _suite("a", 2)])
    assert [row["suite_name"] for row in ordered] == ["a", None, "b"]
