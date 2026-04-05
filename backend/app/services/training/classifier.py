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
from app.services.model_registry import ModelRegistry

logger = logging.getLogger("training.classifier")

_CLASSIFIER_SYSTEM = """You are a test failure classifier for an automated QA system.

Classify the failing test into exactly one category:
  PRODUCT_BUG         — application code is broken (assertion failed on business logic)
  INFRASTRUCTURE      — environment/infra issue (timeouts, 5xx, pod OOMKilled, DB unreachable)
  TEST_DATA           — missing/stale/wrong test data (404 on resource, setup failed)
  AUTOMATION_DEFECT   — test code is broken (NullPointerException in test class, locator changed)
  FLAKY               — intermittent / non-deterministic failure (race condition, async timing)
  UNKNOWN             — insufficient information to classify

Return ONLY a JSON object:
{"category": "CATEGORY", "confidence": 0-100, "reasoning": "1-2 sentences"}"""


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
        """
        Attempt fast classification.
        Returns a partial analysis dict on success, None if confidence < threshold.
        """
        # Determine which model to use
        fine_tuned = await ModelRegistry.get_active_model("classifier")
        model_name = fine_tuned or settings.CLASSIFIER_MODEL or settings.LLM_MODEL

        llm = await get_llm(model=model_name, temperature=0.0)

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
            logger.debug("FastClassifier failed: %s", exc)
            return None

        if result is None:
            return None

        confidence = result.get("confidence", 0)
        if confidence < settings.CLASSIFIER_CONFIDENCE_THRESHOLD:
            logger.debug(
                "FastClassifier confidence too low (%d < %d) for %s — falling back to ReAct",
                confidence, settings.CLASSIFIER_CONFIDENCE_THRESHOLD, test_name,
            )
            return None

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
            "llm_provider": settings.LLM_PROVIDER,
            "llm_model": model_name,
            "requires_human_review": confidence < 90,
            "classified_by": "fast_classifier",
        }


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
