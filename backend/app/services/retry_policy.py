"""One retry policy for agent pipeline runs (architecture E7.2).

Before this module the pipeline task retried through Celery's own
``self.retry`` with ``max_retries=2`` hard-coded on the decorator, a one-sided
jitter helper, and no record on the pipeline row of how many attempts had been
spent. Requirement 8 asks for a configurable limit (default 5), exponential
backoff, and a manual retry a user can see. That needs the attempt count and
the next retry time to live on the row (columns added by migration 0173) and a
single place that computes delays and decides retryability.

Two consumers share this object:

* the pipeline task (``worker.tasks.run_agent_pipeline``): on a retryable
  failure it moves the row ``failed -> retry_wait``, stamps ``next_retry_at``,
  and enqueues a same-id resume with ``countdown = delay(attempt)``;
* step-level LLM/tool retries inside a node (E7.2 wires the object; the nodes
  adopt it as they are touched).

Delays are full-jitter symmetric (``raw * (1 - j .. 1 + j)``). The previous
``_exponential_backoff`` added only ``+0..20%``; that helper now delegates here
so every Celery caller shares one implementation.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "DEFAULT_RETRYABLE",
    "NON_RETRYABLE",
    "RetryPolicy",
    "pipeline_retry_policy",
]

# Error codes (``AgentError.code`` in the architecture) that a retry can fix.
DEFAULT_RETRYABLE: frozenset[str] = frozenset(
    {"model_unavailable", "timeout", "tool_error", "lease_expired", "lease_lost", "unknown"}
)
# Codes a retry can never fix: the same input would fail the same way.
NON_RETRYABLE: frozenset[str] = frozenset(
    {"validation_failed", "policy_denied", "budget_exceeded", "review_rejected", "cancelled"}
)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with symmetric jitter and a bounded attempt count.

    ``attempt`` everywhere in this module is **1-based and counts the attempt
    that just failed**: ``delay(1)`` is the wait after the first attempt.
    """

    max_attempts: int = 5
    base_seconds: float = 30.0
    cap_seconds: float = 600.0
    jitter: float = 0.2
    retry_on: frozenset[str] = field(default_factory=lambda: DEFAULT_RETRYABLE)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_seconds <= 0 or self.cap_seconds <= 0:
            raise ValueError("base_seconds and cap_seconds must be > 0")
        if not 0 <= self.jitter < 1:
            raise ValueError("jitter must be in [0, 1)")

    def raw_delay(self, attempt: int) -> float:
        """Un-jittered delay after ``attempt`` (1-based): ``min(base * 2**(n-1), cap)``."""
        n = max(1, int(attempt))
        return min(self.base_seconds * (2 ** (n - 1)), self.cap_seconds)

    def delay(self, attempt: int, *, rng: Optional[random.Random] = None) -> float:
        """Jittered delay in seconds after ``attempt`` (1-based)."""
        raw = self.raw_delay(attempt)
        r = (rng or random).random()
        return raw * (1 - self.jitter + 2 * self.jitter * r)

    def bounds(self, attempt: int) -> tuple[float, float]:
        """The closed interval ``delay(attempt)`` can fall in (for tests and UI)."""
        raw = self.raw_delay(attempt)
        return raw * (1 - self.jitter), raw * (1 + self.jitter)

    def can_retry(self, attempt: int) -> bool:
        """True when another attempt is allowed after ``attempt`` failed."""
        return int(attempt) < self.max_attempts

    def is_retryable(self, error_code: Optional[str]) -> bool:
        code = (error_code or "unknown").strip().lower()
        if code in NON_RETRYABLE:
            return False
        return code in self.retry_on

    def with_max_attempts(self, max_attempts: int, *, ceiling: Optional[int] = None) -> "RetryPolicy":
        """A copy with a tighter attempt count. Never loosens past ``ceiling``."""
        value = int(max_attempts)
        if ceiling is not None:
            value = min(value, int(ceiling))
        return RetryPolicy(
            max_attempts=max(1, value),
            base_seconds=self.base_seconds,
            cap_seconds=self.cap_seconds,
            jitter=self.jitter,
            retry_on=self.retry_on,
        )


def pipeline_retry_policy(max_attempts: Optional[int] = None) -> RetryPolicy:
    """The policy for agent pipeline runs, from settings, clamped by the env ceiling.

    ``AGENT_PIPELINE_MAX_ATTEMPTS`` (default 5) is the requirement-8 default;
    ``AGENT_MAX_ATTEMPTS_CEILING`` (default 10) is the environment ceiling a
    project or a row may never exceed. A per-row ``max_attempts`` (the column
    from migration 0173) may only tighten.
    """
    from app.core.config import settings  # noqa: PLC0415

    configured = int(getattr(settings, "AGENT_PIPELINE_MAX_ATTEMPTS", 5))
    ceiling = int(getattr(settings, "AGENT_MAX_ATTEMPTS_CEILING", 10))
    policy = RetryPolicy(
        max_attempts=max(1, min(configured, ceiling)),
        base_seconds=float(getattr(settings, "AGENT_RETRY_BASE_SECONDS", 30)),
        cap_seconds=float(getattr(settings, "AGENT_RETRY_CAP_SECONDS", 600)),
    )
    if max_attempts is not None:
        policy = policy.with_max_attempts(max_attempts, ceiling=ceiling)
    return policy
