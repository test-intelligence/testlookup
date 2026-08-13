"""Durable run-level budget authority for decision-graph LLM stages."""
from __future__ import annotations

import math
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


_PIPELINE_BUDGET_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "pipeline_budget_context", default=None
)
_RECEIPT_KEYS = frozenset({
    "stage_name", "attempt", "llm_calls", "tokens", "cost_usd", "reserved_at",
})
_COMPLETED_RECEIPT_KEYS = frozenset({
    "stage_name", "attempt", "llm_calls", "tokens", "cost_usd", "overrun",
})
_MAX_COMPLETED_RECEIPTS = 100
_MAX_PROVIDER_POLICY_EVENTS = 100
_MAX_PROVIDER_NAME = 40
_MAX_MODEL_NAME = 120


@dataclass(frozen=True)
class PipelineBudgetReservation:
    reservation_id: str | None
    allowed: bool
    stop_reason: str | None = None
    legacy_unbounded: bool = False


def set_pipeline_budget_context(**values: Any):
    return _PIPELINE_BUDGET_CONTEXT.set(dict(values))


def reset_pipeline_budget_context(token: Any) -> None:
    _PIPELINE_BUDGET_CONTEXT.reset(token)


def get_pipeline_budget_context() -> dict[str, Any] | None:
    return _PIPELINE_BUDGET_CONTEXT.get()


def append_provider_policy_audit(
    metadata: dict[str, Any], events: Any
) -> dict[str, Any]:
    """Persist a bounded, secret-free provider-policy audit projection.

    Invocation prompts never belong in durable pipeline metadata.  The model
    boundary records only provider/model identifiers and a redaction count in
    the active context; this helper validates that narrow shape and retains a
    bounded tail so a chatty stage cannot grow JSONB without limit.
    """
    if not isinstance(metadata, dict) or not isinstance(events, list):
        return metadata
    from app.services.privacy_service import sanitize_for_llm

    existing = metadata.get("provider_policy_audit")
    if not isinstance(existing, list):
        existing = []
    # Bound each input before combining them; persisted JSON may be legacy or
    # corrupted, and expanding an attacker-sized list defeats the tail cap.
    safe: list[dict[str, Any]] = []
    for event in [*existing[-_MAX_PROVIDER_POLICY_EVENTS:], *events[-_MAX_PROVIDER_POLICY_EVENTS:]]:
        if not isinstance(event, dict):
            continue
        provider = event.get("provider")
        model = event.get("model")
        redacted = event.get("redacted_strings", 0)
        if not isinstance(provider, str) or not provider.strip():
            continue
        if not isinstance(model, str) or not model.strip():
            continue
        if isinstance(redacted, bool) or not isinstance(redacted, int) or redacted < 0:
            continue
        safe.append({
            "provider": sanitize_for_llm(provider.strip())[:_MAX_PROVIDER_NAME],
            "model": sanitize_for_llm(model.strip())[:_MAX_MODEL_NAME],
            "redacted_strings": min(redacted, 1_000_000),
        })
    metadata["provider_policy_audit"] = safe[-_MAX_PROVIDER_POLICY_EVENTS:]
    return metadata


def _finite_nonnegative(value: Any, *, integer: bool = False) -> int | float:
    if isinstance(value, bool):
        raise ValueError("budget value must be numeric")
    if integer:
        if not isinstance(value, int) or value < 0:
            raise ValueError("budget value must be a nonnegative integer")
        return value
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("budget value must be finite")
    if float(value) < 0:
        raise ValueError("budget value must be nonnegative")
    return float(value)


def _normalise_budget(value: Any) -> dict[str, int | float]:
    if not isinstance(value, dict):
        raise ValueError("run_budget_missing")
    return {
        "max_llm_calls": int(_finite_nonnegative(value.get("max_llm_calls"), integer=True)),
        "max_tokens": int(_finite_nonnegative(value.get("max_tokens"), integer=True)),
        "max_cost_usd": float(_finite_nonnegative(value.get("max_cost_usd"))),
        "max_seconds": int(_finite_nonnegative(value.get("max_seconds"), integer=True)),
    }


def _validated_receipt(receipt: Any) -> tuple[str, int, int, int, float]:
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
        raise ValueError("budget_receipt_invalid")
    stage_name = receipt.get("stage_name")
    if not isinstance(stage_name, str) or not stage_name or len(stage_name) > 80:
        raise ValueError("budget_receipt_invalid")
    attempt = _finite_nonnegative(receipt.get("attempt"), integer=True)
    if attempt < 1:
        raise ValueError("budget_receipt_invalid")
    llm_calls = _finite_nonnegative(receipt.get("llm_calls"), integer=True)
    tokens = _finite_nonnegative(receipt.get("tokens"), integer=True)
    cost_usd = _finite_nonnegative(receipt.get("cost_usd"))
    reserved_at = receipt.get("reserved_at")
    if not isinstance(reserved_at, str) or len(reserved_at) > 64:
        raise ValueError("budget_receipt_invalid")
    return stage_name, int(attempt), int(llm_calls), int(tokens), float(cost_usd)


def _validated_completed_receipt(
    receipt: Any,
) -> tuple[str, int, int, int, float, bool]:
    """Validate the immutable tombstone retained for a settled stage."""
    if not isinstance(receipt, dict) or set(receipt) != _COMPLETED_RECEIPT_KEYS:
        raise ValueError("budget_completed_receipt_invalid")
    stage_name = receipt.get("stage_name")
    if not isinstance(stage_name, str) or not stage_name or len(stage_name) > 80:
        raise ValueError("budget_completed_receipt_invalid")
    attempt = _finite_nonnegative(receipt.get("attempt"), integer=True)
    if attempt < 1:
        raise ValueError("budget_completed_receipt_invalid")
    llm_calls = _finite_nonnegative(receipt.get("llm_calls"), integer=True)
    tokens = _finite_nonnegative(receipt.get("tokens"), integer=True)
    cost_usd = _finite_nonnegative(receipt.get("cost_usd"))
    if not isinstance(receipt.get("overrun"), bool):
        raise ValueError("budget_completed_receipt_invalid")
    return (
        stage_name,
        int(attempt),
        int(llm_calls),
        int(tokens),
        float(cost_usd),
        receipt["overrun"],
    )


def _ledger(metadata: dict[str, Any]) -> tuple[dict[str, int | float], dict[str, Any]]:
    budget = _normalise_budget(metadata.get("run_budget"))
    spend = metadata.get("budget_spend")
    if not isinstance(spend, dict):
        spend = {}
    spend = dict(spend)
    integer_keys = {"llm_calls", "tokens", "reserved_llm_calls", "reserved_tokens"}
    for key, default in (
        ("llm_calls", 0), ("tokens", 0), ("cost_usd", 0.0),
        ("reserved_llm_calls", 0), ("reserved_tokens", 0),
        ("reserved_cost_usd", 0.0),
    ):
        raw = spend.get(key, default)
        spend[key] = _finite_nonnegative(raw, integer=key in integer_keys)
    reservations = spend.get("reservations")
    if not isinstance(reservations, dict):
        reservations = {}
    validated_reservations: dict[str, dict[str, Any]] = {}
    for reservation_id, receipt in reservations.items():
        if not isinstance(reservation_id, str) or not reservation_id or len(reservation_id) > 128:
            raise ValueError("budget_receipt_invalid")
        _validated_receipt(receipt)
        validated_reservations[reservation_id] = dict(receipt)
    spend["reservations"] = validated_reservations
    completed = spend.get("completed_reservations")
    if completed is None:
        completed = {}
    if not isinstance(completed, dict) or len(completed) > _MAX_COMPLETED_RECEIPTS:
        raise ValueError("budget_completed_receipts_invalid")
    validated_completed: dict[str, dict[str, Any]] = {}
    for reservation_id, receipt in completed.items():
        if (
            not isinstance(reservation_id, str)
            or not reservation_id
            or len(reservation_id) > 128
        ):
            raise ValueError("budget_completed_receipt_invalid")
        _validated_completed_receipt(receipt)
        validated_completed[reservation_id] = dict(receipt)
    spend["completed_reservations"] = validated_completed
    reasons = spend.get("budget_stop_reasons")
    spend["budget_stop_reasons"] = (
        [str(item)[:100] for item in reasons if item is not None]
        if isinstance(reasons, list) else []
    )
    return budget, spend


def reserve_stage_in_metadata(
    metadata: dict[str, Any],
    *,
    reservation_id: str,
    stage_name: str,
    attempt: int,
    llm_calls: int,
    tokens: int,
    cost_usd: float,
) -> PipelineBudgetReservation:
    """Mutate a locked pipeline metadata dict and admit one stage envelope."""
    if metadata.get("budget_authority") == "investigator_ledger":
        return PipelineBudgetReservation(None, True, legacy_unbounded=True)
    if not isinstance(metadata.get("run_budget"), dict):
        return PipelineBudgetReservation(None, True, legacy_unbounded=True)
    try:
        if not isinstance(reservation_id, str) or not reservation_id or len(reservation_id) > 128:
            raise ValueError("reservation_id_invalid")
        if not isinstance(stage_name, str) or not stage_name or len(stage_name) > 80:
            raise ValueError("stage_name_invalid")
        budget, spend = _ledger(metadata)
        llm_calls = int(_finite_nonnegative(llm_calls, integer=True))
        tokens = int(_finite_nonnegative(tokens, integer=True))
        cost_usd = float(_finite_nonnegative(cost_usd))
    except (TypeError, ValueError, OverflowError):
        return PipelineBudgetReservation(None, False, "budget_ledger_invalid")

    if reservation_id in spend["reservations"]:
        return PipelineBudgetReservation(None, False, "budget_reservation_replay")

    if llm_calls < 1 or tokens < 1:
        if "llm_call_budget_exhausted" not in spend["budget_stop_reasons"]:
            spend["budget_stop_reasons"].append("llm_call_budget_exhausted")
        metadata["budget_spend"] = spend
        return PipelineBudgetReservation(None, False, "llm_call_budget_exhausted")
    used_calls = int(spend["llm_calls"]) + int(spend["reserved_llm_calls"])
    used_tokens = int(spend["tokens"]) + int(spend["reserved_tokens"])
    used_cost = float(spend["cost_usd"]) + float(spend["reserved_cost_usd"])
    if (
        used_calls + llm_calls > int(budget["max_llm_calls"])
        or used_tokens + tokens > int(budget["max_tokens"])
        or used_cost + cost_usd > float(budget["max_cost_usd"]) + 1e-9
    ):
        reasons = spend["budget_stop_reasons"]
        reason = (
            "llm_call_budget_exhausted"
            if used_calls + llm_calls > int(budget["max_llm_calls"])
            else (
                "token_budget_exhausted"
                if used_tokens + tokens > int(budget["max_tokens"])
                else "cost_budget_exhausted"
            )
        )
        if reason not in reasons:
            reasons.append(reason)
        metadata["budget_spend"] = spend
        return PipelineBudgetReservation(None, False, reason)

    spend["reservations"][reservation_id] = {
        "stage_name": stage_name,
        "attempt": max(int(attempt), 1),
        "llm_calls": llm_calls,
        "tokens": tokens,
        "cost_usd": cost_usd,
        "reserved_at": datetime.now(timezone.utc).isoformat(),
    }
    spend["reserved_llm_calls"] = int(spend["reserved_llm_calls"]) + llm_calls
    spend["reserved_tokens"] = int(spend["reserved_tokens"]) + tokens
    spend["reserved_cost_usd"] = round(float(spend["reserved_cost_usd"]) + cost_usd, 6)
    metadata["budget_spend"] = spend
    return PipelineBudgetReservation(reservation_id, True)


def queue_stage_settlement(
    metadata: dict[str, Any],
    *,
    reservation_id: str,
    stage_name: str,
    attempt: int,
    actual_llm_calls: int,
    actual_tokens: int,
    actual_cost_usd: float,
) -> str | None:
    """Persist observed stage usage for a separate settlement transaction."""
    if not isinstance(metadata.get("run_budget"), dict):
        return None
    try:
        _, spend = _ledger(metadata)
        if reservation_id not in spend["reservations"]:
            return "budget_reservation_missing"
        if not isinstance(stage_name, str) or not stage_name or len(stage_name) > 80:
            raise ValueError("stage_name_invalid")
        calls = int(_finite_nonnegative(actual_llm_calls, integer=True))
        tokens = int(_finite_nonnegative(actual_tokens, integer=True))
        cost = float(_finite_nonnegative(actual_cost_usd))
    except (TypeError, ValueError, OverflowError):
        return "budget_ledger_invalid"
    pending = metadata.get("pending_settlements")
    if not isinstance(pending, dict):
        pending = {}
    if reservation_id in pending:
        return "budget_settlement_pending"
    pending[reservation_id] = {
        "reservation_id": reservation_id,
        "stage_name": stage_name,
        "attempt": max(int(attempt), 1),
        "actual_llm_calls": calls,
        "actual_tokens": tokens,
        "actual_cost_usd": cost,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "settlement_attempts": 0,
    }
    metadata["pending_settlements"] = dict(list(pending.items())[-100:])
    return "budget_settlement_pending"


def retry_pending_stage_settlements(
    metadata: dict[str, Any],
    *,
    reservation_id: str | None = None,
) -> dict[str, str]:
    """Retry pending receipts in-memory; caller owns durable commit."""
    pending = metadata.get("pending_settlements")
    if not isinstance(pending, dict):
        return {}
    results: dict[str, str] = {}
    for rid, receipt in list(pending.items()):
        if reservation_id is not None and rid != reservation_id:
            continue
        if not isinstance(receipt, dict):
            results[rid] = "budget_ledger_invalid"
            continue
        receipt["settlement_attempts"] = int(receipt.get("settlement_attempts") or 0) + 1
        reason = settle_stage_in_metadata(
            metadata,
            reservation_id=rid,
            actual_llm_calls=receipt.get("actual_llm_calls"),
            actual_tokens=receipt.get("actual_tokens"),
            actual_cost_usd=receipt.get("actual_cost_usd"),
        )
        if reason in (None, "budget_overrun", "budget_reservation_missing"):
            pending.pop(rid, None)
            results[rid] = reason or "settled"
        else:
            results[rid] = reason
    metadata["pending_settlements"] = dict(list(pending.items())[-100:])
    if any(value not in ("settled", "budget_overrun", "budget_reservation_missing") for value in results.values()):
        spend = metadata.get("budget_spend")
        if isinstance(spend, dict):
            reasons = spend.setdefault("budget_stop_reasons", [])
            if "budget_settlement_failed" not in reasons:
                reasons.append("budget_settlement_failed")
    return results

def settle_stage_in_metadata(
    metadata: dict[str, Any],
    *,
    reservation_id: str,
    actual_llm_calls: int,
    actual_tokens: int,
    actual_cost_usd: float,
) -> str | None:
    """Settle a stage envelope and retain a bounded completed receipt."""
    if metadata.get("budget_authority") == "investigator_ledger":
        return None
    if not isinstance(metadata.get("run_budget"), dict):
        return None
    try:
        _, spend = _ledger(metadata)
        actual_llm_calls = int(_finite_nonnegative(actual_llm_calls, integer=True))
        actual_tokens = int(_finite_nonnegative(actual_tokens, integer=True))
        actual_cost_usd = float(_finite_nonnegative(actual_cost_usd))
    except (TypeError, ValueError, OverflowError):
        return "budget_ledger_invalid"

    receipt = spend["reservations"].get(reservation_id)
    if not isinstance(receipt, dict):
        return "budget_reservation_missing"
    try:
        stage_name, attempt, reserved_calls, reserved_tokens, reserved_cost = _validated_receipt(receipt)
    except (TypeError, ValueError, OverflowError):
        return "budget_ledger_invalid"

    spend["reserved_llm_calls"] = max(
        0, int(spend["reserved_llm_calls"]) - reserved_calls
    )
    spend["reserved_tokens"] = max(
        0, int(spend["reserved_tokens"]) - reserved_tokens
    )
    spend["reserved_cost_usd"] = max(
        0.0, round(float(spend["reserved_cost_usd"]) - reserved_cost, 6)
    )
    spend["llm_calls"] = int(spend["llm_calls"]) + actual_llm_calls
    spend["tokens"] = int(spend["tokens"]) + actual_tokens
    spend["cost_usd"] = round(float(spend["cost_usd"]) + actual_cost_usd, 6)
    overrun = (
        actual_llm_calls > reserved_calls
        or actual_tokens > reserved_tokens
        or actual_cost_usd > reserved_cost + 1e-9
    )
    if overrun and "budget_overrun" not in spend["budget_stop_reasons"]:
        spend["budget_stop_reasons"].append("budget_overrun")
    completed = spend.get("completed_reservations")
    if not isinstance(completed, dict):
        completed = {}
    completed = dict(completed)
    completed[reservation_id] = {
        "stage_name": stage_name,
        "attempt": attempt,
        "llm_calls": actual_llm_calls,
        "tokens": actual_tokens,
        "cost_usd": actual_cost_usd,
        "overrun": overrun,
    }
    spend["completed_reservations"] = dict(list(completed.items())[-100:])
    del spend["reservations"][reservation_id]
    metadata["budget_spend"] = spend
    return "budget_overrun" if overrun else None


def reconcile_reaped_pipeline_metadata(
    metadata: dict[str, Any],
    *,
    reconciled_at: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile graph receipts while a stale pipeline is terminalized."""
    result = dict(metadata or {})
    if not isinstance(result.get("run_budget"), dict):
        return result
    retry_pending_stage_settlements(result)
    reconcile_pipeline_budget(result, reason="pipeline_reaped")
    result["budget_reconciled_at"] = (reconciled_at or datetime.now(timezone.utc)).isoformat()
    result["budget_reconciliation_reason"] = "pipeline_reaped"
    return result

def reconcile_pipeline_budget(
    metadata: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Conservatively settle outstanding envelopes when a pipeline aborts."""
    if metadata.get("budget_authority") == "investigator_ledger":
        return metadata
    if not isinstance(metadata.get("run_budget"), dict):
        return metadata
    try:
        _, spend = _ledger(metadata)
    except (TypeError, ValueError, OverflowError):
        metadata["budget_spend"] = {
            "budget_stop_reasons": ["budget_ledger_invalid"],
            "reservations": {},
        }
        return metadata
    for receipt in list(spend["reservations"].values()):
        try:
            _, _, reserved_calls, reserved_tokens, reserved_cost = _validated_receipt(receipt)
        except (TypeError, ValueError, OverflowError):
            if "budget_ledger_invalid" not in spend["budget_stop_reasons"]:
                spend["budget_stop_reasons"].append("budget_ledger_invalid")
            continue
        spend["llm_calls"] += reserved_calls
        spend["tokens"] += reserved_tokens
        spend["cost_usd"] = round(float(spend["cost_usd"]) + reserved_cost, 6)
        if reason not in spend["budget_stop_reasons"]:
            spend["budget_stop_reasons"].append(reason)
    spend["reservations"] = {}
    spend["reserved_llm_calls"] = 0
    spend["reserved_tokens"] = 0
    spend["reserved_cost_usd"] = 0.0
    metadata["budget_spend"] = spend
    return metadata
