
from datetime import datetime, timezone

import pytest

from app.services.llm_factory import BudgetedLLM, PipelineBudgetExceeded
from app.services.pipeline_budget_service import (
    append_provider_policy_audit,
    get_pipeline_budget_context,
    reconcile_pipeline_budget,
    reconcile_reaped_pipeline_metadata,
    remaining_cost_usd,
    reset_pipeline_budget_context,
    reserve_stage_in_metadata,
    set_pipeline_budget_context,
    settle_stage_in_metadata,
    queue_stage_settlement,
    retry_pending_stage_settlements,
)


def test_provider_policy_audit_is_bounded_and_secret_free():
    metadata = {}
    events = [
        {
            "provider": "ollama",
            "model": "model-token=sk-secret-value",
            "redacted_strings": 2,
        }
        for _ in range(150)
    ]

    append_provider_policy_audit(metadata, events)

    assert len(metadata["provider_policy_audit"]) == 100
    assert metadata["provider_policy_audit"][-1]["provider"] == "ollama"
    assert "sk-secret-value" not in str(metadata)


def test_provider_policy_audit_bounds_legacy_and_context_inputs_before_merge():
    metadata = {
        "provider_policy_audit": [
            {"provider": "ollama", "model": str(index), "redacted_strings": 0}
            for index in range(10_000)
        ]
    }
    events = [
        {"provider": "ollama", "model": str(index), "redacted_strings": 0}
        for index in range(10_000)
    ]

    append_provider_policy_audit(metadata, events)

    assert len(metadata["provider_policy_audit"]) == 100
    assert metadata["provider_policy_audit"][0]["model"] == "9900"
    assert metadata["provider_policy_audit"][-1]["model"] == "9999"


def test_provider_policy_audit_rejects_malformed_events_and_preserves_tail():
    metadata = {"provider_policy_audit": [{"provider": "ollama", "model": "m", "redacted_strings": 0}]}

    append_provider_policy_audit(
        metadata,
        [
            None,
            {"provider": "", "model": "m", "redacted_strings": 0},
            {"provider": "openai", "model": "gpt", "redacted_strings": True},
            {"provider": "openai", "model": "gpt", "redacted_strings": 3},
        ],
    )

    assert metadata["provider_policy_audit"] == [
        {"provider": "ollama", "model": "m", "redacted_strings": 0},
        {"provider": "openai", "model": "gpt", "redacted_strings": 3},
    ]


def _metadata():
    return {
        "run_budget": {
            "max_llm_calls": 2,
            "max_tokens": 100,
            "max_cost_usd": 1.0,
            "max_seconds": 30,
        },
        "budget_spend": {
            "llm_calls": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "reserved_llm_calls": 0,
            "reserved_tokens": 0,
            "reserved_cost_usd": 0.0,
            "reservations": {},
            "completed_reservations": {},
            "budget_stop_reasons": [],
        },
    }


def test_remaining_cost_subtracts_spent_and_reserved_dollars():
    metadata = _metadata()
    metadata["budget_spend"]["cost_usd"] = 0.2
    metadata["budget_spend"]["reserved_cost_usd"] = 0.35

    assert remaining_cost_usd(metadata) == 0.45
    assert remaining_cost_usd({}) is None
    assert remaining_cost_usd({"budget_authority": "investigator_ledger"}) is None
    metadata["budget_spend"]["cost_usd"] = "invalid"
    assert remaining_cost_usd(metadata) == 0.0


def test_reservations_enforce_aggregate_cap_before_provider_calls():
    metadata = _metadata()
    first = reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-a",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=60,
        cost_usd=0.60,
    )
    second = reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-b",
        stage_name="release_risk",
        attempt=1,
        llm_calls=1,
        tokens=40,
        cost_usd=0.40,
    )
    denied = reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-c",
        stage_name="decision_report",
        attempt=1,
        llm_calls=1,
        tokens=1,
        cost_usd=0.01,
    )

    assert first.allowed and second.allowed
    assert denied.allowed is False
    assert denied.stop_reason == "llm_call_budget_exhausted"
    assert len(metadata["budget_spend"]["reservations"]) == 2


def test_replay_and_malformed_ledger_fail_closed_without_mutation():
    metadata = _metadata()
    assert reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-a",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=10,
        cost_usd=0.1,
    ).allowed
    before = dict(metadata["budget_spend"]["reservations"]["stage-a"])
    replay = reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-a",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=10,
        cost_usd=0.1,
    )
    assert replay.stop_reason == "budget_reservation_replay"

    metadata["budget_spend"]["cost_usd"] = "forged"
    malformed = reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-b",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=1,
        cost_usd=0.01,
    )
    assert malformed.stop_reason == "budget_ledger_invalid"
    assert metadata["budget_spend"]["reservations"]["stage-a"] == before


def test_settlement_records_actual_overrun_and_completed_receipt():
    metadata = _metadata()
    assert reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-a",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=10,
        cost_usd=0.1,
    ).allowed
    reason = settle_stage_in_metadata(
        metadata,
        reservation_id="stage-a",
        actual_llm_calls=2,
        actual_tokens=120,
        actual_cost_usd=1.2,
    )

    spend = metadata["budget_spend"]
    assert reason == "budget_overrun"
    assert spend["llm_calls"] == 2
    assert spend["tokens"] == 120
    assert spend["cost_usd"] == 1.2
    assert spend["reservations"] == {}
    assert spend["completed_reservations"]["stage-a"]["overrun"] is True


def test_malformed_completed_receipt_fails_closed_without_new_reservation():
    metadata = _metadata()
    metadata["budget_spend"]["completed_reservations"] = {
        "old-stage": {
            "stage_name": "summary",
            "attempt": 1,
            "llm_calls": 1,
            "tokens": 10,
            "cost_usd": 0.1,
            "overrun": False,
            "unexpected": "forged",
        }
    }

    result = reserve_stage_in_metadata(
        metadata,
        reservation_id="new-stage",
        stage_name="release_risk",
        attempt=1,
        llm_calls=1,
        tokens=1,
        cost_usd=0.01,
    )

    assert result.allowed is False
    assert result.stop_reason == "budget_ledger_invalid"
    assert "new-stage" not in metadata["budget_spend"]["reservations"]


def test_completed_receipt_count_is_bounded():
    metadata = _metadata()
    metadata["budget_spend"]["completed_reservations"] = {
        f"old-{index}": {
            "stage_name": "summary",
            "attempt": 1,
            "llm_calls": 1,
            "tokens": 10,
            "cost_usd": 0.1,
            "overrun": False,
        }
        for index in range(101)
    }

    result = reserve_stage_in_metadata(
        metadata,
        reservation_id="new-stage",
        stage_name="release_risk",
        attempt=1,
        llm_calls=1,
        tokens=1,
        cost_usd=0.01,
    )

    assert result.allowed is False
    assert result.stop_reason == "budget_ledger_invalid"


def test_reconcile_conservatively_charges_outstanding_envelopes():
    metadata = _metadata()
    reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-a",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=10,
        cost_usd=0.1,
    )
    reconcile_pipeline_budget(metadata, reason="pipeline_failed")
    spend = metadata["budget_spend"]
    assert spend["reservations"] == {}
    assert spend["reserved_tokens"] == 0
    assert spend["tokens"] == 10
    assert "pipeline_failed" in spend["budget_stop_reasons"]



def test_pending_settlement_retries_and_clears_receipt():
    metadata = _metadata()
    reservation = reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-pending",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=10,
        cost_usd=0.1,
    )
    assert reservation.allowed
    assert queue_stage_settlement(
        metadata,
        reservation_id="stage-pending",
        stage_name="summary",
        attempt=1,
        actual_llm_calls=1,
        actual_tokens=8,
        actual_cost_usd=0.08,
    ) == "budget_settlement_pending"

    results = retry_pending_stage_settlements(metadata)

    assert results == {"stage-pending": "settled"}
    assert metadata["pending_settlements"] == {}
    assert metadata["budget_spend"]["reservations"] == {}
    assert metadata["budget_spend"]["tokens"] == 8


def test_pending_settlement_is_reconciled_before_reaper_charge():
    metadata = _metadata()
    reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-reaped",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=10,
        cost_usd=0.1,
    )
    queue_stage_settlement(
        metadata,
        reservation_id="stage-reaped",
        stage_name="summary",
        attempt=1,
        actual_llm_calls=1,
        actual_tokens=9,
        actual_cost_usd=0.09,
    )

    metadata = reconcile_reaped_pipeline_metadata(metadata)

    assert metadata["budget_spend"]["tokens"] == 9
    assert metadata["budget_spend"]["reservations"] == {}
    assert metadata["pending_settlements"] == {}
def test_reaper_reconciliation_clears_outstanding_graph_receipt():
    metadata = _metadata()
    reservation = reserve_stage_in_metadata(
        metadata,
        reservation_id="stage-reaper",
        stage_name="summary",
        attempt=1,
        llm_calls=1,
        tokens=10,
        cost_usd=0.1,
    )
    assert reservation.allowed

    metadata = reconcile_reaped_pipeline_metadata(metadata, reconciled_at=datetime(2026, 8, 12, tzinfo=timezone.utc))

    spend = metadata["budget_spend"]
    assert spend["reservations"] == {}
    assert spend["reserved_llm_calls"] == 0
    assert spend["tokens"] == 10
    assert "pipeline_reaped" in spend["budget_stop_reasons"]

def test_legacy_metadata_is_explicitly_unbounded():
    reservation = reserve_stage_in_metadata(
        {},
        reservation_id="legacy",
        stage_name="summary",
        attempt=1,
        llm_calls=10_000,
        tokens=10_000_000,
        cost_usd=999.0,
    )
    assert reservation.allowed is True
    assert reservation.legacy_unbounded is True


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, value):
        self.calls += 1
        return value

    def invoke(self, value):
        self.calls += 1
        return value


@pytest.mark.asyncio
async def test_budgeted_llm_blocks_provider_at_invocation_boundary():
    fake = _FakeLLM()
    model = BudgetedLLM(fake)
    token = set_pipeline_budget_context(
        pipeline_run_id="p",
        stage_name="summary",
        blocked=True,
        stop_reason="token_budget_exhausted",
    )
    try:
        with pytest.raises(PipelineBudgetExceeded, match="token_budget_exhausted"):
            await model.ainvoke("secret prompt")
        with pytest.raises(PipelineBudgetExceeded):
            model.invoke("secret prompt")
        assert fake.calls == 0
    finally:
        reset_pipeline_budget_context(token)
        assert get_pipeline_budget_context() is None


def test_budgeted_llm_preserves_provider_when_allowed():
    fake = _FakeLLM()
    model = BudgetedLLM(fake)
    token = set_pipeline_budget_context(blocked=False)
    try:
        assert model.invoke("ok") == "ok"
        assert fake.calls == 1
    finally:
        reset_pipeline_budget_context(token)

def test_forged_active_receipt_is_rejected_without_consuming_it():
    metadata = _metadata()
    metadata["budget_spend"]["reservations"] = {
        "forged": {
            "stage_name": "summary",
            "attempt": 1,
            "llm_calls": 1,
            "tokens": 1,
            "cost_usd": 0.01,
            "reserved_at": "2026-08-12T00:00:00+00:00",
            "unexpected": "forged",
        }
    }
    result = reserve_stage_in_metadata(
        metadata,
        reservation_id="next",
        stage_name="release_risk",
        attempt=1,
        llm_calls=1,
        tokens=1,
        cost_usd=0.01,
    )
    assert result.allowed is False
    assert result.stop_reason == "budget_ledger_invalid"
    assert "forged" in metadata["budget_spend"]["reservations"]


def test_reconcile_malformed_receipt_fails_closed_without_raising():
    metadata = _metadata()
    metadata["budget_spend"]["reservations"] = {"forged": {"tokens": "evil"}}
    reconcile_pipeline_budget(metadata, reason="pipeline_failed")
    assert metadata["budget_spend"]["reservations"] == {}
    assert "budget_ledger_invalid" in metadata["budget_spend"]["budget_stop_reasons"]

def test_zero_call_or_token_envelope_is_hard_disabled():
    metadata = _metadata()
    result = reserve_stage_in_metadata(
        metadata,
        reservation_id="zero",
        stage_name="summary",
        attempt=1,
        llm_calls=0,
        tokens=100,
        cost_usd=0.1,
    )
    assert result.allowed is False
    assert result.stop_reason == "llm_call_budget_exhausted"
    assert metadata["budget_spend"]["reservations"] == {}
