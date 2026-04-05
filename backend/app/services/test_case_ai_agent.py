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

logger = logging.getLogger(__name__)


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)


# ── Tool definitions ──────────────────────────────────────────────────────────

@tool
def generate_test_cases_tool(requirements: str) -> str:
    """Generate test cases from a requirements or feature description.

    Args:
        requirements: Natural language description of the feature/requirement to test.

    Returns:
        JSON string with a list of test case objects.
    """
    llm = get_llm()
    system = SystemMessage(content="""You are an expert QA engineer. Given a requirements description,
generate comprehensive test cases following best practices. Return ONLY valid JSON with this structure:
{
  "test_cases": [
    {
      "title": "string - concise test case title",
      "objective": "string - what this test verifies",
      "preconditions": "string - what must be true before running",
      "steps": [
        {"step_number": 1, "action": "string", "expected_result": "string"}
      ],
      "expected_result": "string - overall expected outcome",
      "test_data": "string - required test data",
      "test_type": "unit|integration|e2e|smoke|regression|security|performance",
      "priority": "critical|high|medium|low",
      "severity": "blocker|critical|major|minor|trivial",
      "feature_area": "string",
      "tags": ["tag1", "tag2"],
      "estimated_duration_minutes": 5
    }
  ],
  "coverage_summary": "string - what areas are covered",
  "gaps_noted": ["string - any areas that couldn't be covered from the description"]
}
Generate 3-8 test cases covering: happy path, edge cases, error conditions, boundary values.""")

    human = HumanMessage(content=f"Generate test cases for:\n\n{requirements}")
    try:
        response = llm.invoke([system, human])
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
def review_test_quality_tool(test_case_json: str) -> str:
    """Review a test case for quality, completeness, and best practices.

    Args:
        test_case_json: JSON string of the test case to review.

    Returns:
        JSON string with quality assessment and improvement suggestions.
    """
    llm = get_llm()
    system = SystemMessage(content="""You are a senior QA architect reviewing test cases for quality.
Evaluate the test case and return ONLY valid JSON:
{
  "quality_score": 85,
  "grade": "B+",
  "summary": "string - 1-2 sentence overall assessment",
  "score_breakdown": {
    "clarity": 90,
    "completeness": 80,
    "atomicity": 85,
    "maintainability": 80,
    "coverage": 75
  },
  "issues": [
    {"severity": "high|medium|low", "category": "string", "description": "string", "step": null}
  ],
  "suggestions": [
    {"field": "steps|title|preconditions|expected_result|test_data", "suggestion": "string"}
  ],
  "best_practices_violations": ["string"],
  "coverage_gaps": ["string - what edge cases or scenarios are missing"],
  "positive_aspects": ["string - what is done well"]
}
Criteria: Clear title, measurable steps, single responsibility, explicit expected results,
proper test data definition, no UI-dependency in unit tests, reproducible.""")

    human = HumanMessage(content=f"Review this test case:\n\n{test_case_json}")
    try:
        response = llm.invoke([system, human])
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
def analyze_coverage_gaps_tool(requirements: str, existing_tests_summary: str) -> str:
    """Identify coverage gaps between requirements and existing test cases.

    Args:
        requirements: Requirements or feature description.
        existing_tests_summary: Summary of existing test cases (titles and objectives).

    Returns:
        JSON string with coverage analysis and gap report.
    """
    llm = get_llm()
    system = SystemMessage(content="""You are a QA coverage analyst. Analyze requirements vs existing tests.
Return ONLY valid JSON:
{
  "coverage_score": 72,
  "covered_areas": ["string - requirements fully covered"],
  "partial_coverage": [{"area": "string", "missing": "string"}],
  "uncovered_areas": ["string - requirements with no test coverage"],
  "recommended_new_tests": [
    {"title": "string", "priority": "high|medium|low", "rationale": "string"}
  ],
  "risk_assessment": "string - what risks exist due to coverage gaps",
  "summary": "string"
}""")

    human = HumanMessage(content=f"Requirements:\n{requirements}\n\nExisting tests:\n{existing_tests_summary}")
    try:
        response = llm.invoke([system, human])
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
def generate_test_strategy_tool(project_context: str) -> str:
    """Generate a comprehensive test strategy document for a project.

    Args:
        project_context: Description of the project, tech stack, and testing goals.

    Returns:
        JSON string with complete test strategy sections.
    """
    llm = get_llm()
    system = SystemMessage(content="""You are a QA Director creating a test strategy document.
Return ONLY valid JSON:
{
  "objective": "string - overall testing objective",
  "scope": "string - what is in scope",
  "out_of_scope": "string - what is explicitly excluded",
  "test_approach": "string - high-level testing approach and philosophy",
  "test_types": [
    {"type": "string", "priority": "high|medium|low", "tools": ["string"], "coverage_target_pct": 80, "rationale": "string"}
  ],
  "risk_assessment": [
    {"risk": "string", "likelihood": "high|medium|low", "impact": "high|medium|low", "mitigation": "string"}
  ],
  "entry_criteria": ["string - conditions before testing begins"],
  "exit_criteria": ["string - conditions for testing to be considered complete"],
  "environments": [
    {"name": "string", "type": "dev|staging|prod|local", "purpose": "string"}
  ],
  "automation_approach": "string - automation strategy and framework recommendations",
  "defect_management": "string - how defects are tracked, prioritized, and resolved",
  "metrics": ["string - key quality metrics to track"],
  "summary": "string - executive summary"
}""")

    human = HumanMessage(content=f"Create a test strategy for:\n\n{project_context}")
    try:
        response = llm.invoke([system, human])
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
def optimize_test_plan_tool(test_cases_json: str, constraints: str) -> str:
    """Optimize test case ordering and prioritization for a test plan.

    Args:
        test_cases_json: JSON array of test case titles with priority/type/estimated_duration.
        constraints: Constraints like time_budget, focus_area, team_size.

    Returns:
        JSON string with optimized execution order and rationale.
    """
    llm = get_llm()
    system = SystemMessage(content="""You are a test planning optimizer. Given test cases and constraints,
provide an optimized execution plan. Return ONLY valid JSON:
{
  "optimized_order": [
    {"title": "string", "execution_order": 1, "rationale": "string", "estimated_duration_minutes": 5}
  ],
  "execution_phases": [
    {"phase": "string", "description": "string", "test_titles": ["string"]}
  ],
  "total_estimated_duration_minutes": 120,
  "parallel_execution_possible": true,
  "parallel_groups": [["title1", "title2"], ["title3"]],
  "risk_areas_first": true,
  "optimization_notes": "string"
}
Prioritize: smoke tests first, critical path second, regression last. Group by feature area for parallel execution.""")

    human = HumanMessage(content=f"Test cases:\n{test_cases_json}\n\nConstraints:\n{constraints}")
    try:
        response = llm.invoke([system, human])
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
    import asyncio
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: generate_test_cases_tool.invoke({"requirements": requirements}))
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"test_cases": [], "error": "Failed to parse AI response"}


async def ai_review_test_case(test_case: dict[str, Any]) -> dict[str, Any]:
    """AI quality review of a test case dict. Returns review result."""
    import asyncio
    tc_json = json.dumps(test_case, indent=2)
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: review_test_quality_tool.invoke({"test_case_json": tc_json}))
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"quality_score": 0, "error": "Failed to parse AI response"}


async def ai_analyze_coverage(requirements: str, existing_tests: list[dict]) -> dict[str, Any]:
    """Analyze coverage gaps. existing_tests is list of {title, objective} dicts."""
    import asyncio
    summary = "\n".join(f"- {t.get('title', '')}: {t.get('objective', '')}" for t in existing_tests)
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: analyze_coverage_gaps_tool.invoke({
        "requirements": requirements,
        "existing_tests_summary": summary or "No existing test cases yet.",
    }))
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"coverage_score": 0, "error": "Failed to parse AI response"}


async def ai_generate_strategy(project_context: str) -> dict[str, Any]:
    """Generate test strategy for a project context description."""
    import asyncio
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: generate_test_strategy_tool.invoke({"project_context": project_context}))
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"error": "Failed to parse AI response"}


async def ai_optimize_plan(test_cases: list[dict], constraints: str = "") -> dict[str, Any]:
    """Optimize test plan execution order."""
    import asyncio
    tc_json = json.dumps([{
        "title": t.get("title", ""),
        "priority": t.get("priority", "medium"),
        "test_type": t.get("test_type", "functional"),
        "estimated_duration_minutes": t.get("estimated_duration_minutes", 5),
    } for t in test_cases], indent=2)
    constraints_text = constraints or "No specific constraints. Optimize for maximum risk coverage."
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: optimize_test_plan_tool.invoke({
        "test_cases_json": tc_json,
        "constraints": constraints_text,
    }))
    try:
        return cast(dict[str, Any], json.loads(raw)) if isinstance(raw, str) else cast(dict[str, Any], raw)
    except json.JSONDecodeError:
        return {"error": "Failed to parse AI response"}
