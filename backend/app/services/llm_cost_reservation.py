"""Atomic per-call reservation against the monthly LLM cost cap (re-audit M13).

``llm_cost_budget.check_and_apply_cap`` reads the period's spend and compares
it with the cap once per stage or report; spend is recorded afterwards, per
stage. Between the read and the record nothing is held, so N workers that each
read "$0.90 of $1.00" all proceed, and the month closes well past the cap. The
hard cap was a wall with a door in it.

This module puts the wall at the invocation boundary
(``llm_factory.BudgetedLLM``), and makes check-and-reserve one atomic step:

* The call's worst case is priced before it is made (prompt tokens plus the
  client's output ceiling) with ``llm_pricing.estimate_cost``.
* One Redis Lua script -- atomic by construction -- takes
  ``max(counter, committed_spend_from_postgres)``, refuses if that plus the
  estimate would pass the cap, and otherwise adds the estimate. Concurrent
  callers are serialised by Redis, not by luck.
* After the call the actual cost is settled: ``actual - estimate`` is added,
  which releases the unused part (all of it when the call failed).

The counter holds committed spend plus in-flight reservations for one
project-month. Seeding it with ``max(counter, postgres)`` on every reserve means
an evicted or expired key heals from the durable meter on the next call.

Fails CLOSED: with the ``llm_cost_budget`` flag on and a cap configured, a
priced call whose reservation cannot be made (Redis or Postgres unreachable)
is refused. A cap that could not be checked is not a cap.

Scope: a reservation needs to know whose budget to charge. Pipelines set it for
the whole graph with :func:`cost_budget_scope`; the run-compare report does the
same. An LLM call made outside any scope (chat, investigator) is not charged to
a project here, as it was not before -- the per-stage meter still records what
the pipelines spend. Self-hosted and unpriced models reserve nothing: there is
no dollar figure to hold.
"""
from __future__ import annotations

import contextlib
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import structlog

logger = structlog.get_logger("services.llm_cost_reservation")

#: Redis key prefix; tests point it at a unique namespace.
KEY_PREFIX = "tl:llmcap"


class CostCapExceeded(RuntimeError):
    """Raised BEFORE a provider call when the monthly cap cannot absorb it."""


_COST_SCOPE: ContextVar[Optional[str]] = ContextVar("llm_cost_scope", default=None)


@contextlib.contextmanager
def cost_budget_scope(project_id: Any) -> Iterator[None]:
    """Charge every LLM call made inside this block to ``project_id``.

    A ContextVar, so it follows the graph into every task it spawns.
    """
    token = _COST_SCOPE.set(str(project_id) if project_id else None)
    try:
        yield
    finally:
        _COST_SCOPE.reset(token)


def current_cost_scope() -> Optional[str]:
    return _COST_SCOPE.get()


# KEYS[1] counter; ARGV: committed_usd, estimate_usd, cap_usd, ttl_seconds.
# Returns {1, new_total} when reserved, {0, current_total} when refused.
_RESERVE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local committed = tonumber(ARGV[1])
if committed > current then current = committed end
local estimate = tonumber(ARGV[2])
local cap = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
if current + estimate > cap then
  redis.call('SET', KEYS[1], string.format('%.10f', current), 'EX', ttl)
  return {0, string.format('%.10f', current)}
end
local total = current + estimate
redis.call('SET', KEYS[1], string.format('%.10f', total), 'EX', ttl)
return {1, string.format('%.10f', total)}
"""

# KEYS[1] counter; ARGV: delta_usd, ttl_seconds. Never below zero.
_SETTLE_LUA = """
local total = tonumber(redis.call('GET', KEYS[1]) or '0') + tonumber(ARGV[1])
if total < 0 then total = 0 end
redis.call('SET', KEYS[1], string.format('%.10f', total), 'EX', tonumber(ARGV[2]))
return string.format('%.10f', total)
"""


@dataclass
class Reservation:
    key: str
    project_id: str
    estimated_usd: float
    ttl_seconds: int
    settled: bool = False


def _redis() -> Any:
    from app.db.redis_client import get_redis

    return get_redis()


def counter_key(project_id: uuid.UUID, period_start: datetime) -> str:
    return f"{KEY_PREFIX}:{project_id}:{period_start:%Y%m}"


async def _cap_context(project_id: uuid.UUID) -> Optional[tuple[float, float]]:
    """``(hard_cap_usd, committed_usd)`` when a cap applies, else ``None``.

    Raises on any store error -- the caller fails closed. Unlike
    ``llm_cost_budget._feature_enabled`` (which reads an outage as "flag
    off"), the flag is read directly here, so an outage cannot open the cap.
    """
    from app.db.postgres import AsyncSessionLocal
    from app.services.feature_flags import is_enabled
    from app.services.llm_cost_budget import (
        _load_quota,
        _load_usage_row,
        current_period_bounds,
    )

    if not await is_enabled("llm_cost_budget"):
        return None
    async with AsyncSessionLocal() as db:
        quota = await _load_quota(db, project_id)
        if quota is None or not quota.enabled or float(quota.hard_cap_usd or 0) <= 0:
            return None
        period_start, _ = current_period_bounds()
        usage = await _load_usage_row(db, project_id, period_start)
        committed = float(usage.total_cost_usd) if usage else 0.0
        return float(quota.hard_cap_usd), committed


def estimate_input_tokens(args: tuple, kwargs: dict) -> int:
    """A deliberately generous prompt-size estimate (about 4 characters a
    token over the rendered arguments, which include structure overhead). A
    reservation that is too big is released on settle; one that is too small
    lets the cap be passed."""
    text = repr(args) + repr(kwargs)
    return max(1, len(text) // 4)


def price(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollar cost for a priced model; ``0.0`` for self-hosted or unpriced."""
    from app.services.llm_pricing import estimate_cost

    estimate = estimate_cost(
        provider, model, input_tokens=input_tokens, output_tokens=output_tokens,
    )
    return float(estimate.cost_usd) if estimate.source == "priced" else 0.0


def _period_ttl(now: datetime, period_end: datetime) -> int:
    # Outlive the period by a day so late settles land on the right month.
    return max(60, int((period_end - now).total_seconds()) + 86_400)


async def reserve(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    max_output_tokens: int,
) -> Optional[Reservation]:
    """Reserve this call's worst-case cost, or raise :class:`CostCapExceeded`.

    Returns ``None`` when nothing needs holding: no project scope, a
    self-hosted/unpriced model, the flag off, or no cap configured.
    """
    scope = current_cost_scope()
    if not scope:
        return None
    try:
        project_id = uuid.UUID(scope)
    except ValueError:
        return None
    estimate_usd = price(provider, model, input_tokens, max_output_tokens)
    if estimate_usd <= 0:
        return None

    try:
        cap = await _cap_context(project_id)
    except Exception as exc:  # noqa: BLE001 -- fail closed on any store error
        logger.warning("llm_cost_cap_unavailable", project_id=scope, error=str(exc)[:200])
        raise CostCapExceeded(
            "LLM cost cap could not be checked (budget store unavailable); refusing the call"
        ) from exc
    if cap is None:
        return None
    hard_cap_usd, committed_usd = cap

    from app.services.llm_cost_budget import current_period_bounds

    now = datetime.now(timezone.utc)
    period_start, period_end = current_period_bounds(now)
    key = counter_key(project_id, period_start)
    ttl = _period_ttl(now, period_end)
    try:
        allowed, total = await _redis().eval(
            _RESERVE_LUA, 1, key,
            repr(committed_usd), repr(estimate_usd), repr(hard_cap_usd), ttl,
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed
        logger.warning("llm_cost_reservation_store_unavailable", project_id=scope, error=str(exc)[:200])
        raise CostCapExceeded(
            "LLM cost cap could not be checked (reservation store unavailable); refusing the call"
        ) from exc
    if int(allowed) != 1:
        logger.warning(
            "llm_cost_cap_refused_call",
            project_id=scope,
            committed_or_reserved_usd=float(total),
            estimate_usd=estimate_usd,
            cap_usd=hard_cap_usd,
        )
        raise CostCapExceeded(
            f"LLM cost budget: ${float(total):.4f} spent or reserved this month, plus up to "
            f"${estimate_usd:.4f} for this call, would pass the ${hard_cap_usd:.2f} hard cap"
        )
    return Reservation(key=key, project_id=scope, estimated_usd=estimate_usd, ttl_seconds=ttl)


async def settle(reservation: Optional[Reservation], actual_usd: float) -> None:
    """Replace the reservation with the actual cost (``0`` releases it all).

    Best-effort: if the store is unreachable the reservation stays counted
    until the period ends -- an over-count that can refuse a call early,
    never an under-count that lets the cap be passed.
    """
    if reservation is None or reservation.settled:
        return
    reservation.settled = True
    delta = max(0.0, float(actual_usd or 0.0)) - reservation.estimated_usd
    try:
        await _redis().eval(_SETTLE_LUA, 1, reservation.key, repr(delta), reservation.ttl_seconds)
    except Exception as exc:  # noqa: BLE001 -- see docstring
        logger.warning(
            "llm_cost_settle_failed",
            project_id=reservation.project_id,
            delta_usd=delta,
            error=str(exc)[:200],
        )


def usage_tokens(result: Any) -> Optional[tuple[int, int]]:
    """``(input, output)`` tokens a provider reported, or ``None``."""
    usage = getattr(result, "usage_metadata", None)
    if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
        return int(usage["input_tokens"]), int(usage.get("output_tokens") or 0)
    response = getattr(result, "response_metadata", None)
    if isinstance(response, dict):
        token_usage = response.get("token_usage") or response.get("usage") or {}
        if isinstance(token_usage, dict) and isinstance(token_usage.get("prompt_tokens"), int):
            return int(token_usage["prompt_tokens"]), int(token_usage.get("completion_tokens") or 0)
    return None


def output_ceiling(model: Any, default: int) -> int:
    """The client's configured output ceiling (the worst case to reserve)."""
    for candidate in (model, getattr(model, "bound", None)):
        if candidate is None:
            continue
        for name in ("max_tokens", "num_predict", "max_output_tokens"):
            value = getattr(candidate, name, None)
            if isinstance(value, int) and value > 0:
                return value
    return max(1, int(default or 1))
