"""Row-level persistence helpers for the Investigator (Agentic plan AI-1).

The investigation row (``agent_investigations``) is the progress surface the
UI polls, so hypothesis nodes persist their result the moment they finish.
Parallel nodes each open their own session; the read-modify-write on the
``hypotheses`` JSONB list is serialized with ``SELECT ... FOR UPDATE`` so two
concurrently-finishing hypotheses never clobber each other's element.

No classes here on purpose — this is a support module (like the chassis
``workflow.py``), not an agent.
"""
from __future__ import annotations

import uuid
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

import structlog
from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AgentInvestigation

logger = structlog.get_logger("agents.investigator.persistence")

BudgetStopReason = Literal[
    "llm_call_budget_exhausted",
    "token_budget_exhausted",
    "cost_budget_exhausted",
    "investigation_not_found",
    "duplicate_reservation",
    "budget_ledger_invalid",
    "budget_reservation_identity_mismatch",
    "budget_settlement_failed",
    "budget_overrun",
    "reservation_lease_expired",
]

_BUDGET_LEDGER_VERSION = 2
_RESERVATION_LEASE_SECONDS = 300
_MAX_RECEIPTS = 100
_MAX_ACTIVE_RESERVATIONS = 20
_MAX_ACCOUNTING_UNITS = 1_000_000_000
_BINDING_KEYS = (
    "investigation_id", "project_id", "run_id", "pipeline_run_id", "stage_name"
)


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    allowed: bool
    reserved_llm_calls: int = 0
    reserved_tokens: int = 0
    stop_reason: BudgetStopReason | None = None
    binding: tuple[tuple[str, str], ...] = ()
    reserved_cost_usd: float = 0.0
    cost_source: str = "unknown"


def _bounded_nonnegative(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def bounded_accounting_int(value: Any) -> int:
    """Never-raise projection helper; authority validation happens separately."""
    return _bounded_nonnegative(value)


def bounded_accounting_cost(value: Any) -> float:
    parsed = _strict_cost(value)
    return parsed if parsed is not None else 0.0


def _binding_dict(binding: Optional[dict[str, Any]]) -> dict[str, str]:
    raw = binding or {}
    return {key: str(raw.get(key) or "") for key in _BINDING_KEYS}


def _append_reason(spend: dict[str, Any], reason: BudgetStopReason) -> None:
    raw = spend.get("budget_stop_reasons")
    reasons = [str(item) for item in raw] if isinstance(raw, list) else []
    if reason not in reasons:
        reasons.append(reason)
    spend["budget_stop_reasons"] = reasons[-20:]
    spend["last_stop_reason"] = reason


def _strict_nonnegative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > _MAX_ACCOUNTING_UNITS:
        return None
    return value


def _strict_cost(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0 or parsed > 1_000_000:
        return None
    return parsed


def _valid_iso(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.tzinfo is not None
    except (TypeError, ValueError):
        return False


def _valid_receipt_map(
    value: Any, *, completed: bool = False
) -> Optional[dict[str, dict[str, Any]]]:
    if value is None:
        return {}
    limit = _MAX_RECEIPTS if completed else _MAX_ACTIVE_RESERVATIONS
    if not isinstance(value, dict) or len(value) > limit:
        return None
    allowed_statuses = (
        {"settled", "lease_expired_reconciled", "accounting_reconciled_at_finalize"}
        if completed else {"active"}
    )
    validated: dict[str, dict[str, Any]] = {}
    authority_keys = {
        "llm_calls", "tokens", "reserved_cost_usd", "cost_source", "binding",
        "status", "reserved_at", "expires_at",
    }
    completed_keys = authority_keys | {
        "actual_llm_calls", "actual_tokens", "actual_cost_usd",
        "actual_cost_source", "settled_at",
    }
    for key, raw in value.items():
        if not isinstance(raw, dict):
            return None
        if set(raw) != (completed_keys if completed else authority_keys):
            return None
        binding = raw.get("binding")
        if (
            set(binding) != set(_BINDING_KEYS)
            if isinstance(binding, dict)
            else True
        ):
            return None
        if not all(isinstance(binding[name], str) for name in _BINDING_KEYS):
            return None
        if not all(len(binding[name]) <= 200 for name in _BINDING_KEYS):
            return None
        if _strict_nonnegative_int(raw.get("llm_calls")) is None:
            return None
        if _strict_nonnegative_int(raw.get("tokens")) is None:
            return None
        if _strict_cost(raw.get("reserved_cost_usd", 0.0)) is None:
            return None
        if not isinstance(raw.get("cost_source"), str) or not (1 <= len(raw["cost_source"]) <= 40):
            return None
        if raw.get("status") not in allowed_statuses or not _valid_iso(raw.get("reserved_at")):
            return None
        if not completed and not _valid_iso(raw.get("expires_at")):
            return None
        if completed and not _valid_iso(raw.get("settled_at")):
            return None
        if completed and _strict_nonnegative_int(raw.get("actual_llm_calls")) is None:
            return None
        if completed and _strict_nonnegative_int(raw.get("actual_tokens")) is None:
            return None
        if completed and _strict_cost(raw.get("actual_cost_usd")) is None:
            return None
        if completed and (
            not isinstance(raw.get("actual_cost_source"), str)
            or not (1 <= len(raw["actual_cost_source"]) <= 40)
        ):
            return None
        validated[str(key)] = dict(raw)
    return validated


def _expired(receipt: dict[str, Any], now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(str(receipt.get("expires_at")))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= now
    except (TypeError, ValueError):
        return True


def _receipt_matches(
    receipt: dict[str, Any], *, calls: int, tokens: int,
    binding: dict[str, str], reserved_cost_usd: float, cost_source: str,
) -> bool:
    return (
        _bounded_nonnegative(receipt.get("llm_calls")) == calls
        and _bounded_nonnegative(receipt.get("tokens")) == tokens
        and receipt.get("binding") == binding
        and float(receipt.get("reserved_cost_usd") or 0.0) == reserved_cost_usd
        and receipt.get("cost_source") == cost_source
    )


def _recompute_reserved(spend: dict[str, Any], reservations: dict[str, dict[str, Any]]) -> None:
    spend["reservations"] = reservations
    spend["reserved_llm_calls"] = sum(_bounded_nonnegative(item.get("llm_calls")) for item in reservations.values())
    spend["reserved_tokens"] = sum(_bounded_nonnegative(item.get("tokens")) for item in reservations.values())
    spend["reserved_cost_usd"] = round(
        sum(float(item.get("reserved_cost_usd") or 0.0) for item in reservations.values()), 8
    )


def _reserve_budget_ledger(
    *,
    budget: dict[str, Any],
    spend: dict[str, Any],
    reservation_id: str,
    llm_calls: int,
    tokens: int,
    reserved_cost_usd: float = 0.0,
    cost_source: str = "unknown",
    binding: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> tuple[BudgetReservation, dict[str, Any]]:
    """Pure ledger transition used inside the row lock and in unit tests."""
    next_spend = dict(spend) if isinstance(spend, dict) else {}
    original_version = next_spend.get("ledger_version")
    has_receipts = bool(next_spend.get("reservations") or next_spend.get("completed_reservations"))
    next_spend["ledger_version"] = _BUDGET_LEDGER_VERSION
    reservations = _valid_receipt_map(next_spend.get("reservations"))
    completed = _valid_receipt_map(next_spend.get("completed_reservations"), completed=True)
    requested_calls = _strict_nonnegative_int(llm_calls)
    requested_tokens = _strict_nonnegative_int(tokens)
    requested_cost = _strict_cost(reserved_cost_usd)
    aggregate_cost = _strict_cost(next_spend.get("cost_usd", 0.0))
    normalized_binding = _binding_dict(binding)
    normalized_cost_source = str(cost_source)
    if (
        reservations is None
        or completed is None
        or requested_cost is None
        or requested_calls is None
        or requested_tokens is None
        or aggregate_cost is None
        or not (1 <= len(reservation_id) <= 300)
        or not (1 <= len(normalized_cost_source) <= 40)
        or any(len(value) > 200 for value in normalized_binding.values())
        or _strict_nonnegative_int(next_spend.get("llm_calls", 0)) is None
        or _strict_nonnegative_int(next_spend.get("tokens", 0)) is None
        or (has_receipts and original_version != _BUDGET_LEDGER_VERSION)
    ):
        _append_reason(next_spend, "budget_ledger_invalid")
        return BudgetReservation(reservation_id, False, stop_reason="budget_ledger_invalid"), next_spend

    current_time = now or datetime.now(timezone.utc)
    for expired_id, receipt in list(reservations.items()):
        if not _expired(receipt, current_time):
            continue
        next_spend["llm_calls"] = _bounded_nonnegative(next_spend.get("llm_calls")) + _bounded_nonnegative(receipt.get("llm_calls"))
        next_spend["tokens"] = _bounded_nonnegative(next_spend.get("tokens")) + _bounded_nonnegative(receipt.get("tokens"))
        next_spend["cost_usd"] = aggregate_cost + float(receipt["reserved_cost_usd"])
        aggregate_cost = next_spend["cost_usd"]
        receipt["status"] = "lease_expired_reconciled"
        receipt["actual_llm_calls"] = receipt["llm_calls"]
        receipt["actual_tokens"] = receipt["tokens"]
        receipt["actual_cost_usd"] = receipt["reserved_cost_usd"]
        receipt["actual_cost_source"] = receipt["cost_source"]
        receipt["settled_at"] = current_time.isoformat()
        completed[expired_id] = receipt
        reservations.pop(expired_id, None)
        _append_reason(next_spend, "reservation_lease_expired")
    existing = reservations.get(reservation_id)
    if existing is not None:
        reason: BudgetStopReason = (
            "duplicate_reservation"
            if _receipt_matches(existing, calls=requested_calls, tokens=requested_tokens, binding=normalized_binding, reserved_cost_usd=requested_cost, cost_source=normalized_cost_source)
            else "budget_reservation_identity_mismatch"
        )
        _append_reason(next_spend, reason)
        _recompute_reserved(next_spend, reservations)
        next_spend["completed_reservations"] = completed
        return BudgetReservation(
            reservation_id=reservation_id,
            allowed=False,
            stop_reason=reason,
        ), next_spend
    settled = completed.get(reservation_id)
    if settled is not None:
        reason = (
            "duplicate_reservation"
            if _receipt_matches(settled, calls=requested_calls, tokens=requested_tokens, binding=normalized_binding, reserved_cost_usd=requested_cost, cost_source=normalized_cost_source)
            else "budget_reservation_identity_mismatch"
        )
        _append_reason(next_spend, reason)
        _recompute_reserved(next_spend, reservations)
        next_spend["completed_reservations"] = completed
        return BudgetReservation(reservation_id, False, stop_reason=reason), next_spend
    actual_calls = _bounded_nonnegative(next_spend.get("llm_calls"))
    actual_tokens = _bounded_nonnegative(next_spend.get("tokens"))
    reserved_calls = sum(_bounded_nonnegative(item.get("llm_calls")) for item in reservations.values())
    reserved_tokens = sum(_bounded_nonnegative(item.get("tokens")) for item in reservations.values())
    reserved_cost = sum(float(item.get("reserved_cost_usd") or 0.0) for item in reservations.values())
    max_calls = _bounded_nonnegative(budget.get("max_llm_calls"))
    max_tokens = _bounded_nonnegative(budget.get("max_tokens"))
    max_cost = _strict_cost(budget.get("max_cost_usd")) if "max_cost_usd" in budget else None

    stop_reason: BudgetStopReason | None = None
    if actual_calls + reserved_calls + requested_calls > max_calls:
        stop_reason = "llm_call_budget_exhausted"
    elif actual_tokens + reserved_tokens + requested_tokens > max_tokens:
        stop_reason = "token_budget_exhausted"
    elif max_cost is not None and aggregate_cost + reserved_cost + requested_cost > max_cost:
        stop_reason = "cost_budget_exhausted"

    if stop_reason is not None:
        _append_reason(next_spend, stop_reason)
        return BudgetReservation(
            reservation_id=reservation_id,
            allowed=False,
            stop_reason=stop_reason,
        ), next_spend

    reservations[reservation_id] = {
        "llm_calls": requested_calls,
        "tokens": requested_tokens,
        "reserved_cost_usd": requested_cost,
        "cost_source": normalized_cost_source,
        "binding": normalized_binding,
        "status": "active",
        "reserved_at": current_time.isoformat(),
        "expires_at": (current_time + timedelta(seconds=_RESERVATION_LEASE_SECONDS)).isoformat(),
    }
    _recompute_reserved(next_spend, reservations)
    next_spend["completed_reservations"] = completed
    return BudgetReservation(
        reservation_id=reservation_id,
        allowed=True,
        reserved_llm_calls=requested_calls,
        reserved_tokens=requested_tokens,
        binding=tuple(normalized_binding.items()),
        reserved_cost_usd=requested_cost,
        cost_source=normalized_cost_source,
    ), next_spend


def _settle_budget_ledger(
    spend: dict[str, Any],
    reservation: BudgetReservation,
    *,
    actual_llm_calls: int,
    actual_tokens: int,
    actual_cost_usd: float = 0.0,
    cost_source: str = "unknown",
) -> tuple[dict[str, Any], bool]:
    """Settle one active receipt and retain its exactly-once tombstone."""
    next_spend = dict(spend) if isinstance(spend, dict) else {}
    if next_spend.get("ledger_version") != _BUDGET_LEDGER_VERSION:
        _append_reason(next_spend, "budget_ledger_invalid")
        return next_spend, False
    reservations = _valid_receipt_map(next_spend.get("reservations"))
    completed = _valid_receipt_map(next_spend.get("completed_reservations"), completed=True)
    if reservations is None or completed is None:
        _append_reason(next_spend, "budget_ledger_invalid")
        return next_spend, False
    current = reservations.get(reservation.reservation_id)
    if current is None or not _receipt_matches(
        current,
        calls=reservation.reserved_llm_calls,
        tokens=reservation.reserved_tokens,
        binding=dict(reservation.binding),
        reserved_cost_usd=reservation.reserved_cost_usd,
        cost_source=reservation.cost_source,
    ):
        _append_reason(next_spend, "budget_reservation_identity_mismatch")
        return next_spend, False
    reservations.pop(reservation.reservation_id)
    calls = _strict_nonnegative_int(actual_llm_calls)
    tokens = _strict_nonnegative_int(actual_tokens)
    observed_cost = _strict_cost(actual_cost_usd)
    observed_cost_source = str(cost_source)
    aggregate_cost = _strict_cost(next_spend.get("cost_usd", 0.0))
    if (
        calls is None or tokens is None or observed_cost is None
        or aggregate_cost is None or not (1 <= len(observed_cost_source) <= 40)
    ):
        _append_reason(next_spend, "budget_ledger_invalid")
        return next_spend, False
    next_spend["llm_calls"] = _bounded_nonnegative(next_spend.get("llm_calls")) + calls
    next_spend["tokens"] = _bounded_nonnegative(next_spend.get("tokens")) + tokens
    next_spend["cost_usd"] = aggregate_cost + observed_cost
    if (
        calls > reservation.reserved_llm_calls
        or tokens > reservation.reserved_tokens
        or observed_cost > reservation.reserved_cost_usd
    ):
        _append_reason(next_spend, "budget_overrun")
    current.update({
        "status": "settled",
        "actual_llm_calls": calls,
        "actual_tokens": tokens,
        "actual_cost_usd": observed_cost,
        "actual_cost_source": observed_cost_source,
        "settled_at": utcnow_iso(),
    })
    completed[reservation.reservation_id] = current
    while len(completed) > _MAX_RECEIPTS:
        completed.pop(next(iter(completed)))
    next_spend["completed_reservations"] = completed
    _recompute_reserved(next_spend, reservations)
    return next_spend, True


def reconcile_outstanding_budget_ledger(
    spend: dict[str, Any], *, settled_at: Optional[datetime] = None
) -> dict[str, Any]:
    """Conservatively charge valid active receipts during terminal recovery."""
    next_spend = dict(spend) if isinstance(spend, dict) else {}
    reservations = _valid_receipt_map(next_spend.get("reservations"))
    completed = _valid_receipt_map(
        next_spend.get("completed_reservations"), completed=True
    )
    if (
        next_spend.get("ledger_version") != _BUDGET_LEDGER_VERSION
        or reservations is None
        or completed is None
        or _strict_nonnegative_int(next_spend.get("llm_calls", 0)) is None
        or _strict_nonnegative_int(next_spend.get("tokens", 0)) is None
        or _strict_cost(next_spend.get("cost_usd", 0.0)) is None
    ):
        _append_reason(next_spend, "budget_ledger_invalid")
        return next_spend
    terminal_time = settled_at or datetime.now(timezone.utc)
    for reservation_id, receipt in reservations.items():
        next_spend["llm_calls"] += receipt["llm_calls"]
        next_spend["tokens"] += receipt["tokens"]
        next_spend["cost_usd"] = float(next_spend.get("cost_usd") or 0.0) + float(
            receipt.get("reserved_cost_usd") or 0.0
        )
        completed[reservation_id] = {
            **receipt,
            "status": "accounting_reconciled_at_finalize",
            "actual_llm_calls": receipt["llm_calls"],
            "actual_tokens": receipt["tokens"],
            "actual_cost_usd": receipt["reserved_cost_usd"],
            "actual_cost_source": receipt["cost_source"],
            "settled_at": terminal_time.isoformat(),
        }
    if reservations:
        _append_reason(next_spend, "budget_settlement_failed")
    next_spend["completed_reservations"] = dict(list(completed.items())[-_MAX_RECEIPTS:])
    _recompute_reserved(next_spend, {})
    return next_spend


async def reserve_investigation_budget(
    investigation_id: str,
    *,
    reservation_id: str,
    llm_calls: int,
    tokens: int,
    reserved_cost_usd: float = 0.0,
    cost_source: str = "unknown",
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    stage_name: Optional[str] = None,
) -> BudgetReservation:
    """Atomically reserve aggregate budget before a parallel model call."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentInvestigation)
            .where(AgentInvestigation.id == uuid.UUID(investigation_id))
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            return BudgetReservation(
                reservation_id=reservation_id,
                allowed=False,
                stop_reason="investigation_not_found",
            )
        if project_id and str(row.project_id) != project_id:
            return BudgetReservation(reservation_id, False, stop_reason="budget_reservation_identity_mismatch")
        if run_id and str(row.run_id) != run_id:
            return BudgetReservation(reservation_id, False, stop_reason="budget_reservation_identity_mismatch")
        binding = {
            "investigation_id": investigation_id,
            "project_id": project_id,
            "run_id": run_id,
            "pipeline_run_id": pipeline_run_id,
            "stage_name": stage_name,
        }
        reservation, next_spend = _reserve_budget_ledger(
            budget=dict(row.budget or {}),
            spend=dict(row.spend or {}),
            reservation_id=reservation_id,
            llm_calls=llm_calls,
            tokens=tokens,
            reserved_cost_usd=reserved_cost_usd,
            cost_source=cost_source,
            binding=binding,
        )
        row.spend = next_spend
        await db.commit()
        return reservation


async def settle_investigation_budget(
    investigation_id: str,
    reservation: BudgetReservation,
    *,
    actual_llm_calls: int,
    actual_tokens: int,
    actual_cost_usd: float = 0.0,
    cost_source: str = "unknown",
) -> bool:
    """Replace a reservation with bounded observed usage under the same lock."""
    if not reservation.allowed:
        return False
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentInvestigation)
            .where(AgentInvestigation.id == uuid.UUID(investigation_id))
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        next_spend, settled = _settle_budget_ledger(
            dict(row.spend or {}), reservation,
            actual_llm_calls=actual_llm_calls,
            actual_tokens=actual_tokens,
            actual_cost_usd=actual_cost_usd,
            cost_source=cost_source,
        )
        row.spend = next_spend
        await db.commit()
        if not settled:
            logger.warning("investigation_budget_settlement_rejected", investigation_id=investigation_id, reservation_id=reservation.reservation_id)
        return settled


async def is_cancel_requested(investigation_id: str) -> bool:
    """Cooperative-cancel check, called between nodes. Fail-safe: a broken
    read returns False (keep running) rather than killing the graph."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentInvestigation.cancel_requested).where(
                    AgentInvestigation.id == uuid.UUID(investigation_id)
                )
            )
            return bool(result.scalar_one_or_none())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cancel_check_failed",
            investigation_id=investigation_id,
            error_type=type(exc).__name__,
        )
        return False


async def persist_hypothesis(investigation_id: str, hypothesis: dict[str, Any]) -> None:
    """Replace the hypothesis element (matched by ``id``) on the row's
    ``hypotheses`` JSONB list. FOR UPDATE serializes parallel finishers.
    Best-effort: a write fault must not fail the node — the final state is
    persisted again by the workflow runner at completion."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentInvestigation)
                .where(AgentInvestigation.id == uuid.UUID(investigation_id))
                .with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            existing = list(row.hypotheses or [])
            replaced = False
            for i, entry in enumerate(existing):
                if entry.get("id") == hypothesis.get("id"):
                    existing[i] = hypothesis
                    replaced = True
                    break
            if not replaced:
                existing.append(hypothesis)
            row.hypotheses = existing
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hypothesis_persist_failed",
            investigation_id=investigation_id,
            hypothesis_id=hypothesis.get("id"),
            error_type=type(exc).__name__,
        )


async def set_investigation_status(
    investigation_id: str,
    status: str,
    *,
    started_at: Optional[datetime] = None,
) -> None:
    """Advance the lifecycle status (queued→running→synthesizing). Terminal
    states are written by the workflow runner's finalizer, not here."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgentInvestigation).where(
                    AgentInvestigation.id == uuid.UUID(investigation_id)
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.status = status
            if started_at is not None and row.started_at is None:
                row.started_at = started_at
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "investigation_status_write_failed",
            investigation_id=investigation_id,
            status=status,
            error_type=type(exc).__name__,
        )


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
