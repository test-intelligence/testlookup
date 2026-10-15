"""AIC-06: "Included per month" and "Overage rate" compute an overage (display only).

``get_current_usage`` derives covered / overage / charge and a linear month-end
projection from the usage and quota rows it already loads (zero extra queries:
the workspace overview calls it once per project). Nothing is enforced: the
hard cap, soft-warn threshold and at-cap action, and so ``utilization_pct`` and
``status``, are exactly what they were.

DB-free: a fake session answers the two selects in the order the service makes
them (usage row, then quota row) and fails the test on a third.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import llm_cost_budget as svc

PROJECT = uuid.uuid4()
# September has 30 days: 10 of 30 days elapsed at Sept 11 00:00 UTC.
SEPT_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
TEN_DAYS_IN = SEPT_START + timedelta(days=10)

NEW_FIELDS = (
    "covered_usd",
    "overage_usd",
    "overage_charge_usd",
    "projected_period_cost_usd",
    "projected_overage_usd",
    "projected_overage_charge_usd",
    "projection_basis",
)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _Session:
    """Answers ``execute`` with the scripted rows, in order; never more."""

    def __init__(self, *rows: Any) -> None:
        self._rows = list(rows)
        self.statements: list[Any] = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        assert self._rows, "get_current_usage made an extra query"
        return _Result(self._rows.pop(0))


def _usage(spend: float) -> SimpleNamespace:
    return SimpleNamespace(
        total_cost_usd=spend, total_input_tokens=100, total_output_tokens=50,
        total_llm_calls=3, cap_hits=0,
    )


def _quota(*, included: float = 20.0, rate: float = 1.5, hard_cap: float = 0.0,
           threshold: int = 100, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled, included_usd=included, overage_rate_usd=rate,
        hard_cap_usd=hard_cap, soft_warn_threshold_pct=threshold,
    )


async def _current(spend: float | None, quota: Any, *, now: datetime = TEN_DAYS_IN) -> dict:
    session = _Session(_usage(spend) if spend is not None else None, quota)
    result = await svc.get_current_usage(session, PROJECT, now=now)  # type: ignore[arg-type]
    assert len(session.statements) == 2, "exactly the usage and quota selects"
    return result


# -- 1. covered / overage / charge --------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_above_the_allowance_is_overage_times_the_rate():
    usage = await _current(30.0, _quota(included=20.0, rate=1.5))
    assert (usage["covered_usd"], usage["overage_usd"], usage["overage_charge_usd"]) == (20.0, 10.0, 15.0)
    assert usage["overage_rate_usd"] == 1.5
    assert usage["included_usd"] == 20.0


@pytest.mark.asyncio
async def test_spend_inside_the_allowance_is_all_covered():
    usage = await _current(12.0, _quota(included=20.0, rate=1.5))
    assert (usage["covered_usd"], usage["overage_usd"], usage["overage_charge_usd"]) == (12.0, 0.0, 0.0)


# -- 2. no allowance, no quota, disabled quota ---------------------------------------------------


@pytest.mark.asyncio
async def test_no_included_allowance_computes_no_charge():
    """``included_usd = 0`` is the form default: a project that only set a cap
    must never be shown a charge for all of its spend."""
    usage = await _current(30.0, _quota(included=0.0, rate=1.5, hard_cap=100.0))
    assert {key: usage[key] for key in NEW_FIELDS} == dict.fromkeys(NEW_FIELDS)
    assert usage["overage_rate_usd"] == 1.5, "the rate is echoed for an enabled quota"
    assert usage["status"] == "OK" and usage["utilization_pct"] == 30.0


@pytest.mark.parametrize("quota", [None, _quota(enabled=False)])
@pytest.mark.asyncio
async def test_without_an_enabled_quota_every_figure_is_null(quota):
    usage = await _current(30.0, quota)
    assert usage["status"] == "UNLIMITED"
    for key in (*NEW_FIELDS, "overage_rate_usd", "included_usd", "hard_cap_usd", "utilization_pct"):
        assert usage[key] is None, key


# -- 3. the projection --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_linear_projection_from_the_elapsed_days():
    usage = await _current(30.0, _quota(included=20.0, rate=1.5))
    assert usage["projected_period_cost_usd"] == 90.0
    assert usage["projected_overage_usd"] == 70.0
    assert usage["projected_overage_charge_usd"] == 105.0
    assert usage["projection_basis"] == "linear_elapsed_days"


@pytest.mark.asyncio
async def test_half_a_day_in_is_too_early_to_project():
    usage = await _current(30.0, _quota(), now=SEPT_START + timedelta(hours=12))
    assert usage["projection_basis"] == "insufficient_elapsed_time"
    for key in ("projected_period_cost_usd", "projected_overage_usd", "projected_overage_charge_usd"):
        assert usage[key] is None
    # The period-to-date figures are still there.
    assert usage["overage_usd"] == 10.0


@pytest.mark.parametrize("spend", [None, 0.0])
@pytest.mark.asyncio
async def test_no_spend_yet_projects_nothing(spend):
    usage = await _current(spend, _quota())
    assert usage["projection_basis"] == "no_spend"
    assert usage["projected_period_cost_usd"] is None
    assert (usage["covered_usd"], usage["overage_usd"], usage["overage_charge_usd"]) == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_a_projection_under_the_allowance_has_no_projected_overage():
    usage = await _current(3.0, _quota(included=20.0, rate=1.5))
    assert usage["projected_period_cost_usd"] == 9.0
    assert usage["projected_overage_usd"] == 0.0
    assert usage["projected_overage_charge_usd"] == 0.0


# -- 4. rounding ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_money_has_no_float_tail():
    usage = await _current(0.1 + 0.2, _quota(included=0.1, rate=1.0))
    assert usage["overage_usd"] == 0.2
    assert repr(usage["overage_usd"]) == "0.2"
    assert repr(usage["overage_charge_usd"]) == "0.2"


# -- enforcement is untouched -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_utilisation_and_status_are_unchanged_for_the_same_inputs():
    usage = await _current(40.0, _quota(included=10.0, rate=1.0, hard_cap=50.0, threshold=80))
    assert usage["utilization_pct"] == 80.0
    assert usage["status"] == "SOFT_WARN"
    assert usage["hard_cap_usd"] == 50.0
    assert (usage["covered_usd"], usage["overage_usd"], usage["overage_charge_usd"]) == (10.0, 30.0, 30.0)
    capped = await _current(50.0, _quota(included=10.0, rate=1.0, hard_cap=50.0, threshold=80))
    assert capped["status"] == "CAPPED"


def test_the_overage_never_reaches_the_cap_gate():
    """Display only: ``check_and_apply_cap`` must not read the rate (or the
    included amount) — the hard cap and the at-cap action are the only limits."""
    import inspect

    source = inspect.getsource(svc.check_and_apply_cap)
    assert "overage" not in source
    assert "included_usd" not in source


def test_the_period_bounds_follow_now():
    start, end = svc.current_period_bounds(TEN_DAYS_IN)
    assert (start, end) == (SEPT_START, datetime(2026, 10, 1, tzinfo=timezone.utc))
