"""
Unit tests for ``services.llm_cost_budget`` — pure logic only (period
bounds + CapDecision evaluation). The metering path writes to PG and is
covered by integration tests in ``tests/integration/test_billing_api.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


# ── check_and_apply_cap enforcement integrity ───────────────────────────────
#
# Regression (review/llm-cost-budget, 2026-06-02):
#   The post-decision cap-hit increment ran inside check_and_apply_cap's
#   fail-open try/except. A failure there (transient commit error, or a
#   unique-key race seeding the zero usage row) would bubble to the fail-open
#   handler and silently convert an already-computed HARD_BLOCK into UNLIMITED
#   — tearing down the hard cap because a telemetry counter write failed.
#   Fix: _increment_cap_hit swallows its own errors so the enforcement
#   decision always survives.


class _FailingCommitSession:
    """Async-context session whose commit always raises (mimics a DB blip
    during the cap-hit bookkeeping write)."""

    def __init__(self):
        self.added = []
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_calls += 1
        raise RuntimeError("db down during cap_hit increment")


@pytest.mark.asyncio
async def test_hard_block_survives_cap_hit_increment_failure(monkeypatch):
    import uuid

    session = _FailingCommitSession()
    quota = SimpleNamespace(
        enabled=True,
        hard_cap_usd=10.0,
        soft_warn_threshold_pct=80,
        at_cap_action="HARD_BLOCK",
    )
    usage = SimpleNamespace(total_cost_usd=15.0, cap_hits=0)  # 150% — over cap

    monkeypatch.setattr(cost_service, "_feature_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(cost_service, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(cost_service, "_load_quota", AsyncMock(return_value=quota))
    monkeypatch.setattr(cost_service, "_load_usage_row", AsyncMock(return_value=usage))
    # The hard-block branch best-effort-emits a quota.exceeded webhook; keep it
    # hermetic.
    import app.services.webhook_service as wh
    monkeypatch.setattr(wh, "emit_event", AsyncMock(return_value=0))

    decision = await cost_service.check_and_apply_cap(uuid.uuid4())

    # The increment commit was attempted and failed...
    assert session.commit_calls == 1
    # ...but the hard block is preserved (not downgraded to UNLIMITED).
    assert decision.block is True
    assert decision.action == "HARD_BLOCK"
    assert decision.utilization_pct == pytest.approx(150.0)
