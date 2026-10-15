"""The ONE defect priority rule (W06 DEF-04 / D-6, contract §1.1).

``priority_of`` (Python) and ``priority_sql`` (Postgres) are the Defects page's
former ``classifySeverity`` moved server-side, so the table, the KPI totals and
the trend read one derivation. ``CASES`` is the shared case table: the SQL half
runs it on PostgreSQL in ``tests/integration/test_defect_trend_postgres.py``,
and G-C's ``DefectsPage.test.tsx`` runs the same rows through ``classifySeverity``.
"""
from __future__ import annotations

import re

import pytest

from app.services.defect_status import priority_of, priority_sql

#: (severity, failure_category, ai_confidence_score, expected priority)
CASES: tuple[tuple[str | None, str | None, int | None, str], ...] = (
    ("CRITICAL", "FLAKY", None, "P0"),          # a stored severity wins over the category
    ("high", None, None, "P1"),                 # case-insensitive
    (None, "PRODUCT_BUG", 80, "P0"),            # unrated product bug, confident
    (None, "PRODUCT_BUG", 79, "P1"),            # unrated product bug, below 80
    (None, "INFRASTRUCTURE", None, "P2"),
    (None, "RUNNER_ERROR", None, "P2"),
    (None, "CI_FAILURE", None, "P2"),
    (None, "FLAKY", None, "P3"),
    (None, "TEST_DATA", None, "P2"),            # nothing matches: P2
    (None, None, None, "P2"),                   # no category at all
    (None, "product bug", 95, "P0"),            # ``.?`` takes the space; upper-cased first
    (None, "PRODUCT_BUG", None, "P1"),          # NULL confidence reads 0
)


@pytest.mark.parametrize("severity,category,confidence,expected", CASES)
def test_priority_of(severity, category, confidence, expected):
    assert priority_of(severity, category, confidence) == expected


def test_the_table_has_the_twelve_contract_cases():
    assert len(CASES) == 12


def test_low_and_medium_map_too():
    assert priority_of("MEDIUM", "PRODUCT_BUG", 99) == "P2"
    assert priority_of("low", None, None) == "P3"
    # An unknown stored word falls through to the category rule.
    assert priority_of("BLOCKER", "FLAKY", None) == "P3"


def test_priority_sql_is_plain_text_without_binds_or_a_select():
    sql = priority_sql("d")
    assert ":" not in sql and "SELECT" not in sql.upper()
    assert sql.startswith("(CASE ") and sql.endswith(" END)")
    # The page's regexes, byte for byte, as Postgres ``~``.
    for pattern in ("'PRODUCT.?BUG'", "'INFRA|RUNNER|CI'", "'FLAKY'"):
        assert f"~ {pattern}" in sql
    assert "COALESCE(d.ai_confidence_score, 0) >= 80" in sql


def test_priority_sql_uses_the_alias_and_refuses_anything_else():
    assert "x.severity" in priority_sql("x") and "d.severity" not in priority_sql("x")
    for bad in ("d; DROP TABLE defects", "d.x", ""):
        with pytest.raises(ValueError):
            priority_sql(bad)


def test_python_and_sql_name_the_same_severity_words():
    sql = priority_sql("d")
    pairs = dict(re.findall(r"upper\(d\.severity\) = '(\w+)' THEN '(P\d)'", sql))
    assert pairs == {"CRITICAL": "P0", "HIGH": "P1", "MEDIUM": "P2", "LOW": "P3"}
    for word, priority in pairs.items():
        assert priority_of(word, None, None) == priority
