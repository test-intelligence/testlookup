"""Atomic aggregate budget tests for parallel Investigator sub-agents."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agents.investigator import persistence


def test_ledger_distinguishes_call_and_token_exhaustion_and_denies_duplicate_execution():
    spend = {"llm_calls": 0, "tokens": 0}
    first, spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 1, "max_tokens": 100},
        spend=spend,
        reservation_id="task-1",
        llm_calls=1,
        tokens=80,
    )
    repeated, repeated_spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 1, "max_tokens": 100},
        spend=spend,
        reservation_id="task-1",
        llm_calls=1,
        tokens=80,
    )
    denied, denied_spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 1, "max_tokens": 100},
        spend=spend,
        reservation_id="task-2",
        llm_calls=1,
        tokens=10,
    )

    assert first.allowed and not repeated.allowed
    assert repeated.stop_reason == "duplicate_reservation"
    assert repeated_spend["reserved_llm_calls"] == 1
    assert denied.allowed is False
    assert denied.stop_reason == "llm_call_budget_exhausted"
    assert denied_spend["budget_stop_reasons"] == ["llm_call_budget_exhausted"]

    token_denied, _ = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 5, "max_tokens": 50},
        spend={"llm_calls": 0, "tokens": 0},
        reservation_id="too-large",
        llm_calls=1,
        tokens=51,
    )
    assert token_denied.stop_reason == "token_budget_exhausted"


def test_settlement_charges_observed_overrun_and_tombstones_reservation():
    reservation, spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 2, "max_tokens": 1_000},
        spend={"llm_calls": 0, "tokens": 0},
        reservation_id="task-1",
        llm_calls=1,
        tokens=100,
    )
    settled, ok = persistence._settle_budget_ledger(
        spend,
        reservation,
        actual_llm_calls=1,
        actual_tokens=500,
    )

    assert ok is True
    assert settled["tokens"] == 500
    assert "budget_overrun" in settled["budget_stop_reasons"]
    assert settled["completed_reservations"]["task-1"]["actual_tokens"] == 500
    retry, _ = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 2, "max_tokens": 1_000},
        spend=settled,
        reservation_id="task-1",
        llm_calls=1,
        tokens=100,
    )
    assert retry.allowed is False
    assert retry.stop_reason == "duplicate_reservation"


def test_observed_cost_source_does_not_mutate_reserved_identity():
    reservation, spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 2, "max_tokens": 1_000},
        spend={"llm_calls": 0, "tokens": 0, "cost_usd": 0.0},
        reservation_id="task-cost-source", llm_calls=1, tokens=100,
        reserved_cost_usd=0.01, cost_source="reserved_priced",
    )
    settled, ok = persistence._settle_budget_ledger(
        spend, reservation,
        actual_llm_calls=1, actual_tokens=80,
        actual_cost_usd=0.009, cost_source="priced",
    )
    receipt = settled["completed_reservations"]["task-cost-source"]
    assert ok is True
    assert receipt["cost_source"] == "reserved_priced"
    assert receipt["actual_cost_source"] == "priced"
    retry, _ = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 2, "max_tokens": 1_000},
        spend=settled,
        reservation_id="task-cost-source", llm_calls=1, tokens=100,
        reserved_cost_usd=0.01, cost_source="reserved_priced",
    )
    assert retry.stop_reason == "duplicate_reservation"


def test_malformed_ledger_fails_closed_without_crashing():
    reservation, spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 10, "max_tokens": 10_000},
        spend={"reservations": "forged"},
        reservation_id="task-1",
        llm_calls=1,
        tokens=100,
    )
    assert reservation.allowed is False
    assert reservation.stop_reason == "budget_ledger_invalid"
    assert spend["last_stop_reason"] == "budget_ledger_invalid"


def test_expired_reservation_is_conservatively_charged_before_new_admission():
    now = datetime.now(timezone.utc)
    reservation, spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 1, "max_tokens": 100},
        spend={
            "ledger_version": 2,
            "llm_calls": 0,
            "tokens": 0,
            "reservations": {
                    "old": {
                        "llm_calls": 1,
                        "tokens": 80,
                        "reserved_cost_usd": 0.25,
                        "cost_source": "reserved_priced",
                        "binding": {
                            "investigation_id": "i",
                            "project_id": "p",
                            "run_id": "r",
                            "pipeline_run_id": "pipe",
                            "stage_name": "hypothesis_infra",
                        },
                        "status": "active",
                        "reserved_at": (now - timedelta(minutes=10)).isoformat(),
                        "expires_at": (now - timedelta(seconds=1)).isoformat(),
                }
            },
        },
        reservation_id="new",
        llm_calls=1,
        tokens=10,
        now=now,
    )
    assert reservation.allowed is False
    assert reservation.stop_reason == "llm_call_budget_exhausted"
    assert spend["llm_calls"] == 1
    assert spend["tokens"] == 80
    assert spend["cost_usd"] == pytest.approx(0.25)
    assert "reservation_lease_expired" in spend["budget_stop_reasons"]


def test_versionless_receipts_fail_closed_instead_of_being_trusted():
    reservation, _ = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 10, "max_tokens": 10_000},
        spend={
            "reservations": {
                "forged": {
                    "llm_calls": 0,
                    "tokens": 0,
                    "binding": {},
                    "expires_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        },
        reservation_id="forged",
        llm_calls=1,
        tokens=999,
    )
    assert reservation.allowed is False
    assert reservation.stop_reason == "budget_ledger_invalid"


@pytest.mark.parametrize(
    "receipt_override",
    [
        {"llm_calls": "evil"},
        {"tokens": -1},
        {"binding": {"investigation_id": "forged"}},
        {"status": "settled"},
        {"expires_at": "not-a-timestamp"},
        {"unexpected": "secret-bearing-field"},
    ],
)
def test_malformed_v2_receipt_fields_fail_closed(receipt_override):
    now = datetime.now(timezone.utc)
    receipt = {
        "llm_calls": 1,
        "tokens": 10,
        "reserved_cost_usd": 0.01,
        "cost_source": "reserved_priced",
        "binding": {
            "investigation_id": "i",
            "project_id": "p",
            "run_id": "r",
            "pipeline_run_id": "pipe",
            "stage_name": "hypothesis_infra",
        },
        "status": "active",
        "reserved_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        **receipt_override,
    }
    reservation, _ = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 10, "max_tokens": 10_000},
        spend={
            "ledger_version": 2,
            "llm_calls": 0,
            "tokens": 0,
            "reservations": {"forged": receipt},
        },
        reservation_id="new",
        llm_calls=1,
        tokens=100,
    )
    assert reservation.allowed is False
    assert reservation.stop_reason == "budget_ledger_invalid"


def test_terminal_reconciliation_preserves_malformed_receipt_and_fails_closed():
    malformed = {
        "ledger_version": 2,
        "llm_calls": 0,
        "tokens": 0,
        "cost_usd": 0.0,
        "reservations": {"forged": {"llm_calls": "evil", "tokens": "evil"}},
    }
    reconciled = persistence.reconcile_outstanding_budget_ledger(malformed)
    assert reconciled["last_stop_reason"] == "budget_ledger_invalid"
    assert reconciled["reservations"] == malformed["reservations"]
    assert reconciled["llm_calls"] == 0
    assert reconciled["tokens"] == 0


@pytest.mark.parametrize(
    ("actual_calls", "actual_tokens"),
    [("1", 1), (1, True), (-1, 1), (1, 1_000_000_001)],
)
def test_malformed_actual_usage_rejects_settlement_and_preserves_receipt(
    actual_calls, actual_tokens,
):
    reservation, spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 2, "max_tokens": 1_000},
        spend={"llm_calls": 0, "tokens": 0, "cost_usd": 0.0},
        reservation_id="task-1", llm_calls=1, tokens=100,
    )
    next_spend, settled = persistence._settle_budget_ledger(
        spend, reservation,
        actual_llm_calls=actual_calls,
        actual_tokens=actual_tokens,
    )
    assert settled is False
    assert "task-1" in next_spend["reservations"]
    assert next_spend["last_stop_reason"] == "budget_ledger_invalid"


def test_malformed_aggregate_cost_fails_closed_before_expiry_reconciliation():
    now = datetime.now(timezone.utc)
    reservation, spend = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 2, "max_tokens": 1_000},
        spend={
            "ledger_version": 2,
            "llm_calls": 0,
            "tokens": 0,
            "cost_usd": "evil",
            "reservations": {},
        },
        reservation_id="task-1", llm_calls=1, tokens=100, now=now,
    )
    assert reservation.allowed is False
    assert reservation.stop_reason == "budget_ledger_invalid"
    assert spend["cost_usd"] == "evil"


@pytest.mark.parametrize(
    ("calls", "tokens"),
    [("1", 100), (1, True), (-1, 100), (1, 1_000_000_001)],
)
def test_malformed_requested_usage_is_never_coerced_into_admission(calls, tokens):
    reservation, _ = persistence._reserve_budget_ledger(
        budget={"max_llm_calls": 10, "max_tokens": 10_000},
        spend={"llm_calls": 0, "tokens": 0, "cost_usd": 0.0},
        reservation_id="task-1", llm_calls=calls, tokens=tokens,
    )
    assert reservation.allowed is False
    assert reservation.stop_reason == "budget_ledger_invalid"


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _SharedStore:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.row = SimpleNamespace(
            budget={"max_llm_calls": 1, "max_tokens": 1_000},
            spend={"llm_calls": 0, "tokens": 0},
        )


class _LockedSession:
    def __init__(self, shared: _SharedStore):
        self.shared = shared
        self.locked = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        if self.locked:
            self.shared.lock.release()
            self.locked = False

    async def execute(self, _statement):
        await self.shared.lock.acquire()
        self.locked = True
        return _Result(self.shared.row)

    async def commit(self):
        if self.locked:
            self.shared.lock.release()
            self.locked = False


@pytest.mark.asyncio
async def test_five_parallel_reservations_cannot_overspend_one_call(monkeypatch):
    shared = _SharedStore()
    monkeypatch.setattr(
        persistence,
        "AsyncSessionLocal",
        lambda: _LockedSession(shared),
    )
    investigation_id = "11111111-1111-1111-1111-111111111111"

    results = await asyncio.gather(*[
        persistence.reserve_investigation_budget(
            investigation_id,
            reservation_id=f"hypothesis-{index}",
            llm_calls=1,
            tokens=100,
        )
        for index in range(5)
    ])

    allowed = [item for item in results if item.allowed]
    denied = [item for item in results if not item.allowed]
    assert len(allowed) == 1
    assert len(denied) == 4
    assert all(item.stop_reason == "llm_call_budget_exhausted" for item in denied)
    assert shared.row.spend["reserved_llm_calls"] == 1

    await persistence.settle_investigation_budget(
        investigation_id,
        allowed[0],
        actual_llm_calls=1,
        actual_tokens=37,
    )
    assert shared.row.spend["llm_calls"] == 1
    assert shared.row.spend["tokens"] == 37
    assert shared.row.spend["reserved_llm_calls"] == 0
    assert shared.row.spend["reserved_tokens"] == 0
