"""Synthesis node of the Investigator (Agentic plan AI-1).

Turns the five hypothesis results into ONE verdict using documented,
deterministic precedence rules; the narrative is the only LLM-touchable
part (one bounded call through the registry prompt
``investigator_synthesis_narrative``), with a deterministic template as the
offline/failure fallback.

## Precedence rules (deterministic — pin by test)

1. Only ``validated`` hypotheses compete for ``primary_cause``.
2. The highest-confidence validated hypothesis wins.
3. **Conflict rule**: when the top two validated hypotheses are within
   ``CONFLICT_MARGIN`` (10) confidence points of each other AND belong to
   different *explanation families*, the verdict is ``unknown`` with an
   honest narrative naming both. Families:
     - external:  infra, environment       (the platform failed)
     - test_side: known_flaky              (the tests are unreliable)
     - code:      commit, regression       (the product changed)
   Same-family near-ties are NOT conflicts — the family tie-break order
   picks the more specific member: ``infra`` > ``environment`` (a concrete
   infra signal beats generic drift) and ``commit`` > ``regression`` (an
   onset-aligned commit is the more actionable statement of the same
   regression).
4. No validated hypotheses → ``unknown``.
5. The verdict confidence is the winner's confidence; for ``unknown`` it is
   the complement signal — low (25) so consumers never treat an unknown as
   actionable.

``recommended_actions`` are TEXT ONLY, mapped from the primary cause —
shadow and suggest modes are identical this slice and ``act`` is reserved:
the Investigator never executes anything.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from app.agents.base import BaseAgent
from app.agents.investigator.persistence import is_cancel_requested
from app.core.config import settings
from app.models.agent_contracts import (
    InvestigatorSynthesisOutput,
    validate_agent_contract,
)

CONFLICT_MARGIN = 10
UNKNOWN_CONFIDENCE = 25

# Explanation families (rule 3).
FAMILY_BY_HYPOTHESIS: dict[str, str] = {
    "infra": "external",
    "environment": "external",
    "known_flaky": "test_side",
    "commit": "code",
    "regression": "code",
}

# Same-family tie-break: lower index wins (more specific/actionable member).
FAMILY_TIE_BREAK_ORDER: tuple[str, ...] = (
    "infra", "environment", "commit", "regression", "known_flaky",
)

# Text-only recommended actions per primary cause. NO actions are taken.
RECOMMENDED_ACTIONS: dict[str, list[str]] = {
    "infra": [
        "Check runner/service health and recent platform changes (infra runbook).",
        "Re-run the failed suites after the environment recovers before triaging individual tests.",
    ],
    "commit": [
        "Review the diff between the run commit and the baseline commit for the failing areas.",
        "Open the CI run and bisect the commit range if multiple changes landed together.",
    ],
    "environment": [
        "Compare the run's environment configuration (namespace/node/branch) against the baseline.",
        "Verify the deployment target and test-data state match the baseline run.",
    ],
    "known_flaky": [
        "Propose quarantine for the recurring flaky offenders (Flaky Quarantine workflow).",
        "Review the offenders' flip history in the Flaky Coach before treating this run as a regression.",
    ],
    "regression": [
        "Triage the non-flaky newly-failed core as a probable product regression.",
        "File a defect for the concentrated failure cluster and assign the owning team.",
    ],
    "unknown": [
        "Manual triage recommended — the evidence is conflicting or insufficient for an automated verdict.",
    ],
}

_LLM_CALL_TIMEOUT_S = 60


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def pick_primary_cause(hypotheses: list[dict[str, Any]]) -> tuple[str, int, Optional[str]]:
    """Apply the precedence rules. Returns (primary_cause, confidence,
    conflict_note). Pure — pinned by tests."""
    validated = sorted(
        (h for h in hypotheses if h.get("status") == "validated"),
        key=lambda h: (-int(h.get("confidence") or 0),
                       FAMILY_TIE_BREAK_ORDER.index(h.get("id"))
                       if h.get("id") in FAMILY_TIE_BREAK_ORDER else 99),
    )
    if not validated:
        return "unknown", UNKNOWN_CONFIDENCE, None
    top = validated[0]
    if len(validated) >= 2:
        second = validated[1]
        gap = int(top.get("confidence") or 0) - int(second.get("confidence") or 0)
        top_family = FAMILY_BY_HYPOTHESIS.get(str(top.get("id")))
        second_family = FAMILY_BY_HYPOTHESIS.get(str(second.get("id")))
        if gap < CONFLICT_MARGIN and top_family != second_family:
            note = (
                f"Conflicting validated hypotheses: '{top.get('id')}' "
                f"({top.get('confidence')}) vs '{second.get('id')}' "
                f"({second.get('confidence')}) point at different explanation "
                "families — no single primary cause can be honestly asserted."
            )
            return "unknown", UNKNOWN_CONFIDENCE, note
    return str(top.get("id")), int(top.get("confidence") or 0), None


def deterministic_narrative(
    primary_cause: str,
    hypotheses: list[dict[str, Any]],
    conflict_note: Optional[str],
    budget_note: Optional[str],
) -> str:
    """Offline narrative template — plain, honest, grounded in the results."""
    by_id = {h.get("id"): h for h in hypotheses}
    parts: list[str] = []
    if primary_cause == "unknown":
        parts.append(
            conflict_note
            or "No hypothesis was validated by the available evidence — the cause of this run's failures is unknown."
        )
    else:
        winner = by_id.get(primary_cause, {})
        parts.append(
            f"Primary cause: {primary_cause} — {winner.get('summary') or winner.get('title') or ''}"
        )
        others = [
            f"{h.get('id')}: {h.get('status')} ({h.get('confidence')})"
            for h in hypotheses
            if h.get("id") != primary_cause and h.get("status") in ("validated", "invalidated", "inconclusive")
        ]
        if others:
            parts.append("Other hypotheses — " + "; ".join(others) + ".")
    if budget_note:
        parts.append(budget_note)
    return " ".join(p.strip() for p in parts if p and p.strip())


class SynthesisAgent(BaseAgent):
    """Fan-in node: hypotheses → verdict (+ ONE optional narrative LLM call)."""

    stage_name = "investigator_synthesis"

    async def _narrative_with_llm(
        self, primary_cause: str, hypotheses: list[dict[str, Any]], state: dict[str, Any]
    ) -> tuple[Optional[str], int]:
        """One bounded narrative call. Returns (narrative|None, tokens)."""
        if settings.AI_OFFLINE_MODE:
            return None, 0
        budget = state.get("budget") or {}
        # Respect the LLM-call budget across the whole investigation: the five
        # hypothesis nodes used at most one call each.
        used = int(state.get("spend_llm_calls") or 0)
        if used + 1 > int(budget.get("max_llm_calls", 0)):
            return None, 0
        if int(state.get("spend_tokens") or 0) >= int(budget.get("max_tokens", 0)):
            return None, 0
        remaining = float(state.get("deadline_ts", 0)) - time.monotonic()
        if remaining <= 5:
            return None, 0
        try:
            from app.services.llm_factory import get_llm
            from app.services.prompt_registry import get_prompt_text

            bundle = state.get("bundle") or {}
            run = bundle.get("run") or {}
            prompt = get_prompt_text("investigator_synthesis_narrative").format(
                primary_cause=primary_cause,
                hypotheses_json=json.dumps(
                    [
                        {k: h.get(k) for k in ("id", "status", "confidence", "summary")}
                        for h in hypotheses
                    ],
                    sort_keys=True,
                    default=str,
                ),
                run_context=json.dumps(
                    {
                        "build_number": run.get("build_number"),
                        "branch": run.get("branch"),
                        "failed_tests": run.get("failed_tests"),
                        "broken_tests": run.get("broken_tests"),
                        "total_tests": run.get("total_tests"),
                    },
                    sort_keys=True,
                    default=str,
                ),
            )
            llm = await get_llm(temperature=0.0)
            response = await asyncio.wait_for(
                llm.ainvoke(prompt), timeout=min(_LLM_CALL_TIMEOUT_S, remaining),
            )
            usage = getattr(response, "usage_metadata", None) or {}
            tokens = int(usage.get("total_tokens") or 0) or (
                _estimate_tokens(prompt) + _estimate_tokens(str(getattr(response, "content", "")))
            )
            text = str(getattr(response, "content", "") or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(text[start : end + 1])
                    narrative = str(parsed.get("narrative") or "").strip()
                    if narrative:
                        return narrative[:2000], tokens
                except (ValueError, TypeError):
                    pass
            return None, tokens
        except Exception as exc:  # noqa: BLE001 — narrative falls back to template
            self.logger.debug("synthesis_llm_narrative_failed", error=str(exc))
            return None, 0

    async def run(self, state: dict) -> dict:
        pipeline_run_id = state.get("pipeline_run_id", "")
        investigation_id = state.get("investigation_id", "")
        await self.mark_stage_running(pipeline_run_id, input_keys=["hypotheses"])

        cancelled = state.get("cancelled") or await is_cancel_requested(investigation_id)
        if cancelled:
            await self.log_decision(
                pipeline_run_id,
                "synthesis_skip_cancelled",
                chosen="skip",
                rationale="cancel requested — no verdict synthesized",
            )
            await self.mark_stage_done(
                pipeline_run_id,
                result_data={"skipped": "cancelled"},
                project_id=state.get("project_id"),
            )
            return {"verdict": None, "model_info": None, "errors": []}

        hypotheses = list(state.get("hypotheses") or [])
        primary_cause, confidence, conflict_note = pick_primary_cause(hypotheses)

        # Budget honesty: leftovers that never ran (or were timeboxed out)
        # are named in the narrative instead of being silently dropped.
        exhausted = [
            h.get("id")
            for h in hypotheses
            if h.get("status") == "inconclusive" and "budget exhausted" in str(h.get("summary", "")).lower()
        ]
        over_deadline = time.monotonic() >= float(state.get("deadline_ts", float("inf")))
        budget_note: Optional[str] = None
        if exhausted or over_deadline:
            named = f" ({', '.join(str(e) for e in exhausted)})" if exhausted else ""
            budget_note = (
                "Note: the investigation hit its budget — "
                f"{len(exhausted)} hypothesis(es) were left inconclusive{named}."
            )

        narrative, tokens = await self._narrative_with_llm(primary_cause, hypotheses, state)
        llm_calls = 1 if tokens else 0
        if narrative is not None and budget_note:
            narrative = f"{narrative} {budget_note}"
        if narrative is None:
            narrative = deterministic_narrative(
                primary_cause, hypotheses, conflict_note, budget_note
            )

        verdict = {
            "primary_cause": primary_cause,
            "narrative": narrative,
            "confidence": confidence,
            "recommended_actions": list(
                RECOMMENDED_ACTIONS.get(primary_cause, RECOMMENDED_ACTIONS["unknown"])
            ),
        }

        # {"provider","model"} once ANY LLM weighing happened this run.
        llm_engaged = llm_calls > 0 or any(
            h.get("confidence_basis") == "llm_weighted" for h in hypotheses
        )
        model_info = (
            {"provider": settings.LLM_PROVIDER, "model": settings.LLM_MODEL}
            if llm_engaged
            else None
        )

        await self.log_decision(
            pipeline_run_id,
            "synthesis_primary_cause",
            chosen=primary_cause,
            rationale=(conflict_note or f"highest-confidence validated hypothesis at {confidence}"),
            alternatives=[
                str(h.get("id")) for h in hypotheses
                if h.get("status") == "validated" and h.get("id") != primary_cause
            ],
            context={
                "confidence": confidence,
                "validated": [h.get("id") for h in hypotheses if h.get("status") == "validated"],
                "budget_note": budget_note,
            },
        )

        contracted = validate_agent_contract(
            InvestigatorSynthesisOutput,
            {"verdict": verdict},
            agent_name="investigator.synthesis",
            confidence=confidence,
            evidence_refs=[
                {"type": "hypothesis", "id": str(h.get("id")), "status": h.get("status")}
                for h in hypotheses
            ],
            decision_reason=narrative[:500],
        )
        final_verdict = contracted.get("verdict", verdict)

        await self.mark_stage_done(
            pipeline_run_id,
            result_data={"primary_cause": primary_cause, "confidence": confidence},
            input_tokens=tokens,
            llm_calls_count=llm_calls,
            confidence_score=confidence,
            evidence_count=len(hypotheses),
            analysis_mode="investigator",
            project_id=state.get("project_id"),
        )
        await self.broadcast_progress(
            state.get("project_id", ""),
            {
                "investigation_id": investigation_id,
                "stage": "synthesis",
                "primary_cause": primary_cause,
            },
        )
        return {
            "verdict": final_verdict,
            "model_info": model_info,
            "spend_llm_calls": llm_calls,
            "spend_tokens": tokens,
            "spend_cost_usd": 0.0,
            "errors": [],
        }
