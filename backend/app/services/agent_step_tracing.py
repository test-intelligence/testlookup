"""Canonical OpenTelemetry spans for agent workflow steps.

Every LangGraph node enters one span through :func:`trace_agent_step`.  The
shared LLM wrapper enriches that active span at the provider boundary, where
the provider, model, and reported usage are authoritative.  Context variables
keep parallel graph branches isolated without adding telemetry fields to the
workflow's persisted contract.
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from app.core.metrics import agent_invocations_total
from app.core.tracing import get_tracer

logger = logging.getLogger(__name__)


class _NoopSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass
class _StepObservation:
    span: Any
    tier: str
    model: str = "deterministic"
    provider: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    outcome: str = "success"


_CURRENT_STEP: ContextVar[_StepObservation | None] = ContextVar(
    "testlookup_agent_step_trace", default=None
)


def _positive_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default


def _set_attribute(span: Any, name: str, value: Any) -> None:
    try:
        span.set_attribute(name, value)
    except Exception:  # noqa: BLE001 -- telemetry must never break a workflow
        logger.debug("agent_step_trace_attribute_failed", exc_info=True)


def record_gen_ai_request(*, provider: str, model: str) -> None:
    """Record one logical provider request on the active workflow-step span."""
    observation = _CURRENT_STEP.get()
    if observation is None:
        return
    observation.provider = str(provider or "unknown")[:200]
    observation.model = str(model or "unknown")[:500]
    observation.llm_calls += 1


def record_gen_ai_usage(*, input_tokens: int, output_tokens: int) -> None:
    """Add provider-reported token usage to the active workflow-step span."""
    observation = _CURRENT_STEP.get()
    if observation is None:
        return
    observation.input_tokens += _positive_int(input_tokens)
    observation.output_tokens += _positive_int(output_tokens)


@contextmanager
def trace_agent_step(stage_name: str, state: Mapping[str, Any]) -> Iterator[None]:
    """Make one current span cover every exit path of a workflow step."""
    try:
        from app.services.agent_capability_registry import DEFAULT_TIERS, get_capability

        capability_id = get_capability(stage_name).capability_id
        tier = DEFAULT_TIERS.get(stage_name, "deterministic")
    except Exception:  # noqa: BLE001 -- unknown stages still get a trace
        capability_id = f"agent.{stage_name}.unknown"
        tier = "deterministic"
    attempt = max(1, _positive_int(state.get("_attempt"), 1))
    attributes = {
        "gen_ai.operation.name": "agent_step",
        "testlookup.agent_id": capability_id,
        "testlookup.stage.name": stage_name,
        "testlookup.pipeline_run_id": str(state.get("pipeline_run_id") or ""),
        "tier": tier,
        "attempt": attempt,
    }

    try:
        manager = get_tracer("testlookup.agents.workflow").start_as_current_span(
            "testlookup.agent.step", attributes=attributes
        )
        span = manager.__enter__()
    except Exception:  # noqa: BLE001 -- inference continues without telemetry
        logger.debug("agent_step_trace_start_failed", exc_info=True)
        manager = nullcontext(_NoopSpan())
        span = manager.__enter__()

    observation = _StepObservation(span=span, tier=tier)
    token = _CURRENT_STEP.set(observation)
    exc_info: tuple[Any, Any, Any] = (None, None, None)
    try:
        yield
    except BaseException:
        exc_info = sys.exc_info()
        observation.outcome = "error"
        raise
    finally:
        try:
            agent_invocations_total.labels(
                agent=capability_id,
                tier=observation.tier,
                status=observation.outcome,
            ).inc()
        except Exception:  # noqa: BLE001 -- telemetry must never break a workflow
            logger.debug("agent_step_invocation_metric_failed", exc_info=True)
        _set_attribute(span, "gen_ai.request.model", observation.model)
        _set_attribute(span, "gen_ai.usage.input_tokens", observation.input_tokens)
        _set_attribute(span, "gen_ai.usage.output_tokens", observation.output_tokens)
        _set_attribute(span, "testlookup.llm_calls", observation.llm_calls)
        _set_attribute(span, "testlookup.step.outcome", observation.outcome)
        _set_attribute(span, "tier", observation.tier)
        if observation.provider is not None:
            _set_attribute(span, "gen_ai.provider.name", observation.provider)
        _CURRENT_STEP.reset(token)
        try:
            manager.__exit__(*exc_info)
        except Exception:  # noqa: BLE001 -- export failures are non-fatal
            logger.debug("agent_step_trace_finish_failed", exc_info=True)


__all__ = [
    "record_gen_ai_request",
    "record_gen_ai_usage",
    "trace_agent_step",
]
