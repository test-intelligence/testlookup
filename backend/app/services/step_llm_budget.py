"""Shared, serialisable LLM-call budget for loops, escalations, and reviews."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepLLMBudget:
    """One counter shared by every extra model call for a workflow step."""

    limit: int
    used: int = 0
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        max_escalations: int,
        max_iterations: int,
        run_llm_calls_remaining: int,
    ) -> "StepLLMBudget":
        limit = min(
            max(0, int(run_llm_calls_remaining)),
            max(0, int(max_escalations)) + max(0, int(max_iterations)),
        )
        return cls(limit=limit)

    @classmethod
    def from_state(cls, value: Any) -> "StepLLMBudget":
        if not isinstance(value, dict):
            raise ValueError("step_llm_budget must be an object")
        limit = value.get("limit")
        used = value.get("used", 0)
        reasons = value.get("reasons", [])
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 0
            or isinstance(used, bool)
            or not isinstance(used, int)
            or used < 0
            or used > limit
            or not isinstance(reasons, list)
            or any(not isinstance(item, str) or not item for item in reasons)
        ):
            raise ValueError("invalid step_llm_budget")
        return cls(limit=limit, used=used, reasons=list(reasons)[:100])

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume(self, reason: str) -> bool:
        if not reason:
            raise ValueError("budget consumption requires a reason")
        if self.remaining == 0:
            return False
        self.used += 1
        self.reasons.append(reason[:80])
        self.reasons = self.reasons[-100:]
        return True

    def as_state(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "reasons": list(self.reasons),
        }


__all__ = ["StepLLMBudget"]
