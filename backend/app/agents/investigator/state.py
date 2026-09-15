"""LangGraph state for the hypothesis-loop Investigator (Agentic plan AI-1).

The graph is: plan → parallel fan-out to five hypothesis nodes → synthesis.
Parallel nodes write to non-overlapping scalar keys plus three Annotated
reducer keys (``hypotheses`` list-concat, ``spend_*`` additive counters),
mirroring the chassis conventions in ``app/agents/state.py``.
"""
from __future__ import annotations

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict


def _concat_lists(a: list, b: list) -> list:
    return a + b


def _add_int(a: int, b: int) -> int:
    return a + b


def _add_float(a: float, b: float) -> float:
    return a + b


class InvestigationState(TypedDict):
    """Shared state across the Investigator's LangGraph nodes."""

    # Identity
    investigation_id: str
    pipeline_run_id: str        # AgentPipelineRun row backing BaseAgent stage tracking
    run_id: str
    project_id: str
    build_number: str
    mode: str                   # shadow | suggest | act (behaviour identical this slice)
    triggered_by: str

    # Budgets (from the Investigator AgentConfig at trigger time)
    budget: dict[str, int]      # {"max_llm_calls", "max_tokens", "max_seconds"}
    deadline_ts: float          # epoch seconds — wall-clock budget checkpoint

    # Deterministic evidence bundle gathered ONCE by the plan node; hypothesis
    # nodes only read it (small, fast queries happen up front).
    bundle: dict[str, Any]

    # Cooperative cancel — plan node snapshot; hypothesis nodes re-check the
    # DB flag themselves between stages.
    cancelled: bool

    # Parallel-node outputs (reducers)
    hypotheses: Annotated[list[dict[str, Any]], _concat_lists]
    spend_llm_calls: Annotated[int, _add_int]
    spend_tokens: Annotated[int, _add_int]
    spend_cost_usd: Annotated[float, _add_float]
    errors: Annotated[list[str], _concat_lists]

    # Synthesis output
    verdict: Optional[dict[str, Any]]
    # {"provider": str, "model": str} once any LLM call succeeded, else None.
    model_info: Optional[dict[str, str]]

    # Resume-only authority. These keys are populated by the durable
    # same-investigation retry path and are intentionally separate from the
    # reducer-backed outputs so completed hypothesis results are not appended
    # twice when the LangGraph fan-out runs again.
    resume_hypotheses: list[dict[str, Any]]
    resume_completed_hypotheses: set[str]
    resume_completed_stages: set[str]
    resume_verdict: Optional[dict[str, Any]]
