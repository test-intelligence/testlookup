"""
Unit tests for ``services.llm_cost_budget`` — pure logic only (period
bounds + CapDecision evaluation). The metering path writes to PG and is
covered by integration tests in ``tests/integration/test_billing_api.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services import llm_cost_budget as cost_service
from app.services.llm_cost_budget import CapDecision


# ── current_period_bounds ───────────────────────────────────────────────────


def test_period_bounds_mid_month_returns_month_start_and_next_month_start():
    start, end = cost_service.current_period_bounds(
        datetime(2026, 4, 15, 13, 45, tzinfo=timezone.utc)
    )
    assert start == datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 5, 1, tzinfo=timezone.utc)


def test_period_bounds_december_rolls_year():
    start, end = cost_service.current_period_bounds(
        datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
    )
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_period_bounds_are_timezone_aware():
    start, end = cost_service.current_period_bounds()
    assert start.tzinfo is not None
    assert end.tzinfo is not None


# ── CapDecision ─────────────────────────────────────────────────────────────


def test_cap_decision_is_capped_with_mode_override():
    d = CapDecision(action="AUTO_DOWNGRADE_TO_ML", mode_override="ml", rationale="x")
    assert d.is_capped() is True
    assert d.mode_override == "ml"
    assert d.block is False


def test_cap_decision_is_capped_with_hard_block():
    d = CapDecision(action="HARD_BLOCK", block=True, rationale="over cap")
    assert d.is_capped() is True
    assert d.block is True
    assert d.mode_override is None


def test_cap_decision_is_not_capped_when_under_threshold():
    d = CapDecision(action="UNLIMITED", rationale="")
    assert d.is_capped() is False
    assert d.mode_override is None
    assert d.block is False


def test_cap_decision_to_dict_shape():
    d = CapDecision(
        action="SOFT_WARN", rationale="at 90%", utilization_pct=90.5
    )
    out = d.to_dict()
    assert out == {
        "action": "SOFT_WARN",
        "mode_override": None,
        "block": False,
        "rationale": "at 90%",
        "utilization_pct": 90.5,
    }
