"""
Unit tests for ``services.team_value_metrics_service`` — the
``_match_owner`` glob matcher is the only piece of pure logic worth
isolating; the DB-bound aggregator is covered by an integration test.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import team_value_metrics_service as svc


def _rule(pattern: str, owner: str, priority: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        match_pattern=pattern,
        service_name=owner,
        team_name=owner,
        priority=priority,
        is_active=True,
    )


def test_match_owner_first_rule_wins():
    rules = [
        _rule("payments/*", "payments-team", priority=10),
        _rule("*", "default-team", priority=0),
    ]
    assert svc._match_owner("payments/checkout", "test_pay", rules) == "payments-team"


def test_match_owner_falls_through_to_catchall():
    rules = [
        _rule("payments/*", "payments-team", priority=10),
        _rule("*", "default-team", priority=0),
    ]
    assert svc._match_owner("inventory/stock", "test_stock", rules) == "default-team"


def test_match_owner_returns_none_without_any_match():
    rules = [_rule("payments/*", "payments-team", priority=10)]
    assert svc._match_owner("inventory/stock", "test_stock", rules) is None


def test_match_owner_handles_none_suite_gracefully():
    rules = [_rule("*test_pay*", "payments-team", priority=10)]
    assert svc._match_owner(None, "test_pay_flow", rules) == "payments-team"


def test_match_owner_ignores_empty_pattern():
    rules = [_rule("", "noop-team", priority=10), _rule("*", "default", priority=0)]
    assert svc._match_owner("foo", "test_foo", rules) == "default"
