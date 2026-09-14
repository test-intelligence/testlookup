"""
FastClassifier — single LLM call to classify failure_category.

Used as the fast path in run_triage_agent(). When the classifier returns
a result with confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD, the full
ReAct agent is skipped (saving 10–30 seconds and several LLM round-trips).

The classifier uses a fine-tuned model if one is active in ModelRegistry,
otherwise falls back to the main LLM_MODEL.

Expected latency: ~50–200ms vs. 10–30s for the full ReAct agent.
"""
import json
import logging
import re
from typing import Any, Optional, cast

from app.core.config import settings
from app.services.llm_factory import get_llm
from app.services.agent_config_resolver import ResolvedEndpoint
from app.services.model_registry import ModelRegistry
from app.services.prompt_registry import get_prompt_text

logger = logging.getLogger("training.classifier")

# Prompt text lives in the prompt registry (AI-F2) — edit there, with a
# manifest bump + eval-gate attestation.
_CLASSIFIER_SYSTEM = get_prompt_text("fast_classifier_system")


class FastClassifier:
    """
    Lightweight failure classifier — runs before the full ReAct agent.
    Returns None if classifier is not confident enough (caller runs full agent instead).
    """

    @classmethod
    async def classify(
        cls,
        test_name: str,
        error_message: str,
        stack_trace: str = "",
    ) -> Optional[dict]:
        """Attempt fast classification, discarding why it declined.

        Kept for callers that only need the verdict. Prefer
        :meth:`classify_with_outcome` -- this wrapper is exactly the shape that
        made the classifier the last unmeasured LLM path in the pipeline.
        """
        result, _outcome = await cls.classify_with_outcome(
            test_name=test_name, error_message=error_message, stack_trace=stack_trace,
        )
        return result

    @classmethod
    async def classify_with_outcome(
        cls,
        test_name: str,
        error_message: str,
        stack_trace: str = "",
        *,
        endpoint: ResolvedEndpoint | None = None,
        accept_low_confidence: bool = False,
    ) -> tuple[Optional[dict], str]:
        """Classify, and say WHY when the answer is None.

        Returns ``(result, outcome)`` where outcome is one of:

        * ``classified``     -- a usable verdict
        * ``parse_failed``   -- the model answered, but not with JSON we could read
        * ``call_failed``    -- the provider call itself raised
        * ``low_confidence`` -- a deliberate abstention, **not a failure**

        That last distinction is the reason this exists. All four cases used to
        return a bare ``None``, so a model emitting unparseable output was
        indistinguishable from one correctly declining a hard case. The pipeline
        measured 0 parse failures for this path across 9,657 events -- not
        because there were none, but because nothing ever recorded one.
        """
        # Determine which model to use
        if endpoint is None:
            fine_tuned = await ModelRegistry.get_active_model("classifier")
            model_name = fine_tuned or settings.CLASSIFIER_MODEL or settings.LLM_MODEL
            llm = await get_llm(model=model_name, temperature=0.0)
        else:
            model_name = endpoint.model
            llm = await get_llm(
                endpoint=endpoint.model_copy(update={"temperature": 0.0})
            )

        user_content = (
            f"Test: {test_name}\n"
            f"Error: {error_message[:1500]}\n"
            + (f"Stack (first 500 chars): {stack_trace[:500]}" if stack_trace else "")
        ).strip()

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            response = await llm.ainvoke([
                SystemMessage(content=_CLASSIFIER_SYSTEM),
                HumanMessage(content=user_content),
            ])
            raw_content = response.content if hasattr(response, "content") else str(response)
            result = _parse_classifier_output(raw_content if isinstance(raw_content, str) else str(raw_content))
        except Exception as exc:
            logger.warning(
                "fast_classifier_call_failed",
                extra={"error_type": type(exc).__name__},
            )
            return None, "call_failed"

        if result is None:
            # The model answered; we could not read it. This is the case the
            # bare `return None` hid -- it looked identical to an abstention.
            logger.warning("fast_classifier_parse_failed test=%s", test_name[:80])
            return None, "parse_failed"

        confidence = result.get("confidence", 0)
        is_low_confidence = confidence < settings.CLASSIFIER_CONFIDENCE_THRESHOLD
        if is_low_confidence and not accept_low_confidence:
            logger.debug(
                "FastClassifier confidence too low (%d < %d) for %s — falling back to ReAct",
                confidence, settings.CLASSIFIER_CONFIDENCE_THRESHOLD, test_name,
            )
            # Working as designed: a hard case handed to the ReAct loop. Counting
            # this as a failure would misrepresent the classifier and inflate the
            # very metric this instrumentation exists to make trustworthy.
            return None, "low_confidence"

        category = result.get("category", "UNKNOWN")
        reasoning = result.get("reasoning", "")

        logger.info(
            "FastClassifier: test=%s category=%s confidence=%d model=%s",
            test_name, category, confidence, model_name,
        )

        return {
            "root_cause_summary": reasoning,
            "failure_category": category,
            "backend_error_found": category == "INFRASTRUCTURE",
            "pod_issue_found": False,
            "is_flaky": category == "FLAKY",
            "confidence_score": confidence,
            "recommended_actions": _default_actions(category),
            "evidence_references": [],
            "llm_provider": endpoint.provider if endpoint is not None else settings.LLM_PROVIDER,
            "llm_model": model_name,
            "requires_human_review": confidence < 90,
            "classified_by": "fast_classifier",
        }, "low_confidence" if is_low_confidence else "classified"


def _parse_classifier_output(raw: str) -> Optional[dict]:
    """Extract JSON from classifier output."""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return cast(dict[str, Any], json.loads(raw))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if match:
        try:
            return cast(dict[str, Any], json.loads(match.group()))
        except json.JSONDecodeError:
            pass
    return None


def _default_actions(category: str) -> list[str]:
    return {
        "PRODUCT_BUG":       ["Review application logs", "Check recent commits to the affected service", "Create defect ticket"],
        "INFRASTRUCTURE":    ["Check pod/container health", "Review resource limits", "Check network connectivity"],
        "TEST_DATA":         ["Verify test data setup", "Check data seeding scripts", "Confirm environment state"],
        "AUTOMATION_DEFECT": ["Review test code for locator changes", "Update test setup/teardown", "Check test framework version"],
        "FLAKY":             ["Re-run to confirm flakiness", "Add explicit waits or retry logic", "Tag test as flaky"],
        "UNKNOWN":           ["Review stack trace manually", "Check application logs", "Re-run test"],
    }.get(category, ["Manual investigation required"])
