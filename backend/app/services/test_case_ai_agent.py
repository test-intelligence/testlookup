"""AI Agent for Test Case Management workflows.

Provides 5 specialized tools:
  1. generate_test_cases  - Create test cases from requirements description
  2. review_test_quality  - Analyze test case for quality, completeness, best practices
  3. analyze_coverage     - Compare test cases against requirements for gap analysis
  4. generate_strategy    - Produce a structured test strategy document
  5. optimize_test_plan   - Order and prioritize a set of test cases

The agent uses LangChain's structured JSON output mode for deterministic responses.
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage

from app.services.llm_factory import get_llm
from app.services.prompt_registry import get_prompt_text

logger = logging.getLogger(__name__)


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def _redact_for_llm(text: str) -> str:
    """Strip secrets/PII from caller-supplied content before the LLM call.

    ``get_llm()`` resolves to a HOSTED provider (OpenAI/Gemini/Anthropic) when
    ``AI_OFFLINE_MODE`` is off, so requirements text, test-case bodies, and
    project context must not carry raw secrets across that boundary — the same
    RAG-13 rule the grounded-generation path already applies via
    ``redact_prompt``. Redaction is idempotent, so callers that already redacted
    (e.g. rag_generation_service) are unaffected.
    """
    from app.services.rag_redaction_service import redact_prompt

    redacted, _ = redact_prompt(text or "")
    return redacted


# ── Tool definitions ──────────────────────────────────────────────────────────

@tool
async def generate_test_cases_tool(requirements: str) -> str:
    """Generate test cases from a requirements or feature description.

    Args:
        requirements: Natural language description of the feature/requirement to test.

    Returns:
        JSON string with a list of test case objects.
    """
    llm = await get_llm()
    system = SystemMessage(content=get_prompt_text("test_case_generate"))

    human = HumanMessage(content=f"Generate test cases for:\n\n{_redact_for_llm(requirements)}")
    try:
        response = await llm.ainvoke([system, human])
        content = _content_to_text(response.content if hasattr(response, "content") else response)
        # Extract JSON from response
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            return content[start:end]
        return content
    except Exception as e:
        logger.error("generate_test_cases_tool failed", exc_info=True)
        return json.dumps({"error": str(e), "test_cases": []})


@tool
async def review_test_quality_tool(test_case_json: str) -> str:
    """Review a test case for quality, completeness, and best practices.

    Args:
        test_case_json: JSON string of the test case to review.

    Returns:
        JSON string with quality assessment and improvement suggestions.
    """
    llm = await get_llm()
    system = SystemMessage(content=get_prompt_text("test_case_review"))

    human = HumanMessage(content=f"Review this test case:\n\n{_redact_for_llm(test_case_json)}")
    try:
        response = await llm.ainvoke([system, human])
        content = _content_to_text(response.content if hasattr(response, "content") else response)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            return content[start:end]
        return content
    except Exception as e:
        logger.error("review_test_quality_tool failed", exc_info=True)
        return json.dumps({"error": str(e), "quality_score": 0})


@tool
async def analyze_coverage_gaps_tool(requirements: str, existing_tests_summary: str) -> str:
    """Identify coverage gaps between requirements and existing test cases.

    Args:
        requirements: Requirements or feature description.
        existing_tests_summary: Summary of existing test cases (titles and objectives).

    Returns:
        JSON string with coverage analysis and gap report.
    """
    llm = await get_llm()
    system = SystemMessage(content=get_prompt_text("test_case_coverage"))

    human = HumanMessage(
        content=f"Requirements:\n{_redact_for_llm(requirements)}\n\n"
        f"Existing tests:\n{_redact_for_llm(existing_tests_summary)}"
    )
    try:
        response = await llm.ainvoke([system, human])
        content = _content_to_text(response.content if hasattr(response, "content") else response)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            return content[start:end]
        return content
    except Exception as e:
        logger.error("analyze_coverage_gaps_tool failed", exc_info=True)
        return json.dumps({"error": str(e), "coverage_score": 0})


@tool
async def generate_test_strategy_tool(project_context: str) -> str:
    """Generate a comprehensive test strategy document for a project.

    Args:
        project_context: Description of the project, tech stack, and testing goals.

    Returns:
        JSON string with complete test strategy sections.
    """
    llm = await get_llm()
    system = SystemMessage(content=get_prompt_text("test_case_strategy"))

    human = HumanMessage(content=f"Create a test strategy for:\n\n{_redact_for_llm(project_context)}")
    try:
        response = await llm.ainvoke([system, human])
        content = _content_to_text(response.content if hasattr(response, "content") else response)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            return content[start:end]
        return content
    except Exception as e:
        logger.error("generate_test_strategy_tool failed", exc_info=True)
        return json.dumps({"error": str(e)})


@tool
async def optimize_test_plan_tool(test_cases_json: str, constraints: str) -> str:
    """Optimize test case ordering and prioritization for a test plan.

    Args:
        test_cases_json: JSON array of test case titles with priority/type/estimated_duration.
        constraints: Constraints like time_budget, focus_area, team_size.

    Returns:
        JSON string with optimized execution order and rationale.
    """
    llm = await get_llm()
    system = SystemMessage(content=get_prompt_text("test_case_plan_optimizer"))

    human = HumanMessage(
        content=f"Test cases:\n{_redact_for_llm(test_cases_json)}\n\n"
        f"Constraints:\n{_redact_for_llm(constraints)}"
    )
    try:
        response = await llm.ainvoke([system, human])
        content = _content_to_text(response.content if hasattr(response, "content") else response)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            return content[start:end]
        return content
    except Exception as e:
        logger.error("optimize_test_plan_tool failed", exc_info=True)
        return json.dumps({"error": str(e)})


# ── High-level service functions ──────────────────────────────────────────────

async def ai_generate_test_cases(requirements: str) -> dict[str, Any]:
    """Generate test cases from requirements text. Returns parsed dict."""
    raw = await generate_test_cases_tool.ainvoke({"requirements": requirements})
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"test_cases": [], "error": "Failed to parse AI response"}


async def ai_review_test_case(test_case: dict[str, Any]) -> dict[str, Any]:
    """AI quality review of a test case dict. Returns review result."""
    tc_json = json.dumps(test_case, indent=2)
    raw = await review_test_quality_tool.ainvoke({"test_case_json": tc_json})
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"quality_score": 0, "error": "Failed to parse AI response"}


async def ai_analyze_coverage(requirements: str, existing_tests: list[dict]) -> dict[str, Any]:
    """Analyze coverage gaps. existing_tests is list of {title, objective} dicts."""
    summary = "\n".join(f"- {t.get('title', '')}: {t.get('objective', '')}" for t in existing_tests)
    raw = await analyze_coverage_gaps_tool.ainvoke({
        "requirements": requirements,
        "existing_tests_summary": summary or "No existing test cases yet.",
    })
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"coverage_score": 0, "error": "Failed to parse AI response"}


async def ai_generate_strategy(project_context: str) -> dict[str, Any]:
    """Generate test strategy for a project context description."""
    raw = await generate_test_strategy_tool.ainvoke({"project_context": project_context})
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"error": "Failed to parse AI response"}


async def ai_optimize_plan(test_cases: list[dict], constraints: str = "") -> dict[str, Any]:
    """Optimize test plan execution order."""
    tc_json = json.dumps([{
        "title": t.get("title", ""),
        "priority": t.get("priority", "medium"),
        "test_type": t.get("test_type", "functional"),
        "estimated_duration_minutes": t.get("estimated_duration_minutes", 5),
    } for t in test_cases], indent=2)
    constraints_text = constraints or "No specific constraints. Optimize for maximum risk coverage."
    raw = await optimize_test_plan_tool.ainvoke({
        "test_cases_json": tc_json,
        "constraints": constraints_text,
    })
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"error": "Failed to parse AI response"}
