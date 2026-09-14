"""Versioned prompt registry — the single source of truth for LLM prompts (AI-F2).

Every prompt that reaches an LLM is registered here as a ``PromptDef(id,
version, text)``. Call sites import ``get_prompt_text("<id>")`` instead of
holding their own module constant, so a prompt edit is always visible as a
diff in ONE file — and is enforced by two companion artifacts next to this
module:

* ``prompt_manifest.json`` — pins ``sha256(text)[:12]`` + version per prompt
  id. The ``ai.prompt-manifest-sync`` quality gate (scripts/quality_gate.py)
  recomputes hashes from source (this file plus ``mcp/prompts/templates.py``)
  and fails CI on any drift, forcing a deliberate manifest bump.
* ``prompt_manifest_eval.json`` — the eval-gate attestation for the CURRENT
  manifest digest. The same gate fails when the manifest changed without a
  fresh attestation, so a prompt change cannot ship without an eval run.

Workflow for changing a prompt (see architecture/AI_EVALUATION.md §"Prompt
registry"):

    1. Edit the text below and bump its ``version``.
    2. ``python -m app.services.prompt_registry --write-manifest``
    3. ``python -m app.services.prompt_registry --attest <change-id>``
       (runs the eval gate via ``eval_gate_service``; use ``--offline`` to
       score against the in-repo golden datasets when no DB is reachable)
    4. Commit the prompt edit + both JSON files together.

MCP prompt templates (``mcp/prompts/templates.py::PROMPT_TEMPLATES``) join the
same manifest hash-pinned under ``mcp.*`` ids. They are parsed via ``ast`` —
never imported (the local ``mcp/`` server tree is not a backend dependency and
is absent from the backend container image; entries are skipped at runtime
when the source is missing). Their eval story is thinner: MCP prompts steer an
external assistant's tool calls rather than a scored model output, so the
attestation covers them only as "hash-pinned + reviewed", not metric-gated.

Runtime stamping: ``registry_versions()`` / ``prompt_versions_used()`` expose
``v<version>:<hash12>`` tags recorded in pipeline version snapshots
(``workflow._runtime_version_snapshot``), per-test ``_audit`` blocks
(analysis_agent), and ``_routing`` decision records (analysis_router).
``registry_digest()`` is a stable digest over the backend prompt set.

Texts were moved here byte-identically from their original modules on
2026-07-15 (AI-F2); the manifest hashes double as the byte-identity pin.
``react_triage`` is the one deliberate exception — it was bumped to v2 in the
same change to teach the ReAct loop about the ``recall_similar_failures``
memory tool (AI-F3).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.services.eval_verdict import EvalVerdict

MANIFEST_SCHEMA_VERSION = 1
PROMPT_HASH_LENGTH = 12

_MODULE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = _MODULE_DIR / "prompt_manifest.json"
ATTESTATION_PATH = _MODULE_DIR / "prompt_manifest_eval.json"
_REPO_ROOT = _MODULE_DIR.parents[2]
EVAL_ATTESTATION_WATCHED_PATHS: tuple[str, ...] = (
    "backend/app/services/llm_factory.py",
    "backend/app/services/model_router.py",
    "backend/app/services/agent_capability_registry.py",
    "backend/app/agents/reviewer_agent.py",
)
# repo_root/mcp/prompts/templates.py — present in the repo checkout, absent in
# the backend container image (the mcp server ships separately).
MCP_TEMPLATES_PATH = _MODULE_DIR.parents[2] / "mcp" / "prompts" / "templates.py"


def content_hash(text: str) -> str:
    """Short, stable content hash for a prompt text (sha256 hex prefix)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:PROMPT_HASH_LENGTH]


@dataclass(frozen=True)
class PromptDef:
    """One versioned prompt. ``version`` is bumped on ANY text change."""

    id: str
    version: int
    text: str

    @property
    def content_hash(self) -> str:
        return content_hash(self.text)

    @property
    def version_tag(self) -> str:
        """Stable ``v<version>:<hash12>`` tag for decision/audit records."""
        return f"v{self.version}:{self.content_hash}"


_PROMPTS: dict[str, PromptDef] = {}


def _register(prompt_id: str, version: int, text: str) -> None:
    if prompt_id in _PROMPTS:  # pragma: no cover — programming error
        raise ValueError(f"duplicate prompt id: {prompt_id}")
    _PROMPTS[prompt_id] = PromptDef(id=prompt_id, version=version, text=text)


# ═════════════════════════════════════════════════════════════════════════════
# Registered prompts. Keep texts byte-identical to what call sites render —
# the manifest + quality gate pin every byte. NOTE for the quality gate's AST
# parser: each registration must stay a literal `_register("id", N, <str>)`
# call (no dynamic construction).
# ═════════════════════════════════════════════════════════════════════════════

# services/agent.py SYSTEM_PROMPT (ReAct triage loop).
# v3: citation identifiers are server-derived from executed tool observations;
# the model must leave evidence_references empty.
_register(
    "react_triage",
    3,
    """\
You are an expert Software Quality Assurance Architect and Site Reliability Engineer.
Your objective is to analyse failed automated test cases, identify the root cause, and produce
a clear, structured, actionable defect analysis.

You have access to six investigation tools:
{tools}

STRICT RULES:
1. Base ALL conclusions strictly on data returned by your tools. NEVER guess or hallucinate root causes.
2. If tool responses are empty or inconclusive, state: "Insufficient telemetry to determine root cause."
3. Always check for infrastructure/environment issues BEFORE assuming the application code is broken.
4. Check for flakiness history before classifying a failure as a product bug.
5. You MUST use at least the stack trace tool before forming any conclusion.
6. If this failure may have happened before (recurring test, familiar error signature), use the
   recall_similar_failures tool to consult prior analyses, human corrections, and quarantine history
   for this test. A prior HUMAN CORRECTION is authoritative — weigh it above your own fresh
   classification. The server records tool observations as citations; never
   invent citation identifiers or external references.

After completing your investigation, return ONLY a valid JSON object with this exact schema:
{{
  "root_cause_summary": "string (2-4 sentences explaining the root cause in plain English)",
  "failure_category": "PRODUCT_BUG | INFRASTRUCTURE | TEST_DATA | AUTOMATION_DEFECT | FLAKY",
  "backend_error_found": true | false,
  "pod_issue_found": true | false,
  "is_flaky": true | false,
  "confidence_score": integer 0-100,
  "recommended_actions": ["action 1", "action 2", "action 3"],
  "role_actions": {{
    "qa": "1 sentence: what the QA engineer should do next",
    "developer": "1 sentence: what the developer responsible for this code should do",
    "sre": "1 sentence: what the SRE/platform team should investigate or monitor",
    "release_manager": "1 sentence: release gate recommendation (hold / proceed with conditions / clear to release)"
  }},
  "evidence_references": []
}}

The evidence_references field MUST be an empty array. Citation metadata is
derived server-side from the tools that actually executed.

Available tools: {tool_names}

Use the following format:
Thought: your reasoning about what to investigate next
Action: the tool name to use
Action Input: the input to the tool
Observation: the tool's output
... (repeat Thought/Action/Observation as needed)
Thought: I now have enough information to form a conclusion
Final Answer: {{valid JSON object as specified above}}

Begin!

Question: {input}
Thought: {agent_scratchpad}""",
)

# services/training/classifier.py _CLASSIFIER_SYSTEM
_register(
    "fast_classifier_system",
    1,
    """\
You are a test failure classifier for an automated QA system.

Classify the failing test into exactly one category:
  PRODUCT_BUG         — application code is broken (assertion failed on business logic)
  INFRASTRUCTURE      — environment/infra issue (timeouts, 5xx, pod OOMKilled, DB unreachable)
  TEST_DATA           — missing/stale/wrong test data (404 on resource, setup failed)
  AUTOMATION_DEFECT   — test code is broken (NullPointerException in test class, locator changed)
  FLAKY               — intermittent / non-deterministic failure (race condition, async timing)
  UNKNOWN             — insufficient information to classify

Return ONLY a JSON object:
{"category": "CATEGORY", "confidence": 0-100, "reasoning": "1-2 sentences"}""",
)

# agents/conversation.py _SYSTEM_TEMPLATE
# v2 (AI-6): chat gained a tool-using copilot loop whose research is shown to
# the user as a "How I looked this up" trace — the single-shot path must never
# fabricate tool activity it did not perform.
_register(
    "chat_system",
    2,
    """\
You are TestLookup, an expert assistant for software quality analysis embedded in a CI/CD testing platform.

**Session context:**
- Current date/time (UTC): {now}
- Project scope: {project_scope}
- Query focus: {intent_label}

**Available data:**
You have access to structured test execution data:
- Test run history: build numbers, branches, pass rates, failure counts, timestamps
- AI root-cause analyses: per-test failure categories, confidence scores, recommended actions
- Run summaries: AI-generated executive and detailed markdown reports
- Historical flakiness data: stability rates across recent runs
- Defect triage records: Jira ticket references, resolution status

**Instructions:**
- Ground every answer in the retrieved context below — do not invent metrics or test names
- Quote specific values (build numbers, pass rates, test names) directly from the context
- For trend questions, compute and state actual deltas (e.g., "pass rate dropped from 87% → 71%")
- For comparison questions, use a markdown table
- If the context lacks data for a precise answer, say exactly what is missing
- Keep responses concise and actionable for a QA engineer audience
- Some answers on this platform are researched with live read-only data tools and show the user a "How I looked this up" trace. In this response you have NO tools — never claim to have checked, queried, or looked anything up beyond the retrieved context above
""",
)

# agents/conversation.py chat copilot ReAct loop (AI-6). Bounded: the executor
# caps iterations at 6 and wall-clock at AI_TIMEOUT_SECONDS; tool outputs are
# token-budgeted server-side. Tools are read-only and project-scoped via a
# server-side ContextVar — the model never supplies identifiers.
_register(
    "chat_copilot_react",
    1,
    """\
You are TestLookup, an expert QA analysis copilot embedded in a CI/CD testing platform.
Answer the user's question by investigating with your read-only data tools, then give a
concise, grounded answer for a QA engineer audience.

Session context:
- Current date/time (UTC): {now}
- Project scope: {project_scope}

Recent conversation:
{history}

You have access to these read-only tools:
{tools}

STRICT RULES:
1. Base ALL statements strictly on tool observations. NEVER invent metrics, build numbers, or test names.
2. Use at most a few tool calls — stop investigating as soon as you can answer.
3. If a tool returns no data or says it is unavailable, say what is missing instead of guessing.
4. If a tool says the budget is exhausted, answer immediately with what you have.
5. Quote specific values (build numbers, pass rates, test names) directly from observations.
6. The final answer is plain markdown prose (tables welcome) — not JSON, no tool syntax.

Available tools: {tool_names}

Use the following format:
Thought: your reasoning about what to look up next
Action: the tool name to use
Action Input: the input to the tool
Observation: the tool's output
... (repeat Thought/Action/Observation as needed)
Thought: I now have enough information to answer
Final Answer: your grounded markdown answer to the user

Begin!

Question: {input}
Thought: {agent_scratchpad}""",
)

# agents/conversation.py history-compression prompt
_register(
    "chat_compression",
    1,
    """\
Summarise the following QA analysis chat conversation in 4-6 bullet points. Preserve: specific test names, build numbers, pass rates, failure categories, key findings, and any decisions or actions discussed. Be concise.

{transcript}""",
)

# agents/anomaly_agent.py narrative summary prompt
_register(
    "anomaly_narrative",
    1,
    """\
Summarise these test anomalies in 2-3 sentences for an engineering team.
Pass rate: {pass_rate:.1f}%  |  Total tests: {total_tests}
Findings:
{descriptions}

Mention the most critical finding first and suggest a concrete next action.""",
)

# agents/summary_agent.py _SYSTEM_PROMPT
_register(
    "summary_system",
    1,
    """\
You are a QA Engineering Lead writing a structured post-run analysis report.
Be factual, direct, and actionable. Focus on failures and risks.
Do not pad the report. Base every statement strictly on the data provided.

GROUNDING RULES:
- If data is missing or unavailable, state "Insufficient data" — never fabricate details.
- If pass rate is not provided, do not guess a number.
- Only reference test names, error messages, and stack traces that appear in the data.
- Use exact numbers from the data (pass rates, failure counts) — never approximate.
""",
)

# agents/summary_agent.py _EXEC_SUMMARY_PROMPT
_register(
    "summary_executive",
    1,
    """\
{system}

Write EXACTLY 3 sentences summarising this test run for an engineering manager.
Include: pass rate, most critical failure category, and release readiness signal.

Data:
{context}

Return ONLY the 3-sentence paragraph. No bullet points, no headers.""",
)

# agents/summary_agent.py _INCIDENT_VIEW_PROMPT
_register(
    "summary_incident_view",
    2,
    """\
{system}

From the test run data below, produce a structured incident view.
Respond ONLY with a valid JSON object (no markdown fences):

{{
  "what_failed": "concise description of what components/flows failed",
  "likely_cause": "the most probable root cause (1 sentence)",
  "scope": "affected services / suites / environments",
  "criticality": "CRITICAL | HIGH | MEDIUM | LOW",
  "release_impact": "GO | CONDITIONAL_GO | NO_GO",
  "evidence_ids": ["E1", "E3"],
  "failure_breakdown": {{
    "product_bugs": 0,
    "infrastructure": 0,
    "test_data": 0,
    "automation_defect": 0,
    "flaky": 0,
    "unknown": 0
  }}
}}

Cite evidence by id from the Evidence list in the data below. Use ONLY
ids that appear there; omit evidence_ids entirely if nothing supports
this. Never invent an id.

Data:
{context}""",
)

# agents/summary_agent.py _EVIDENCE_PACK_PROMPT
_register(
    "summary_evidence_pack",
    2,
    """\
{system}

Extract an evidence pack from the analysis data below.
Respond ONLY with a valid JSON object (no markdown fences):

{{
  "top_stack_traces": ["excerpt 1", "excerpt 2"],
  "log_anomalies": ["anomaly description 1"],
  "flaky_test_ids": ["test_id_1"],
  "similar_historical_failures": ["description of past similar failure"],
  "data_sources_used": ["stacktrace", "splunk", "flakiness_db", "ocp_events"],
  "evidence_ids": ["E1", "E2"]
}}

Only include items that are present in the analysis data. Use empty arrays if none.

Cite evidence by id from the Evidence list in the data below. Use ONLY
ids that appear there; omit evidence_ids entirely if nothing supports
this. Never invent an id.


Data:
{context}""",
)

# agents/summary_agent.py _ACTION_PLAN_PROMPT
_register(
    "summary_action_plan",
    2,
    """\
{system}

Based on this test run analysis, produce a concrete action plan.
Respond ONLY with a valid JSON object (no markdown fences):

{{
  "immediate_mitigation": "what to do right now to unblock the team",
  "fix_recommendations": ["specific fix 1", "specific fix 2"],
  "validation_steps": ["how to verify the fix", "regression test to run"],
  "rollback_guidance": "when and how to rollback if needed",
  "owner_hints": {{
    "qa": "what QA should do",
    "developer": "what the developer should do",
    "sre": "what SRE/ops should do",
    "release_manager": "what release manager should decide"
  }},
  "evidence_ids": ["E1", "E3"]
}}

Cite evidence by id from the Evidence list in the data below. Use ONLY
ids that appear there; omit evidence_ids entirely if nothing supports
this. Never invent an id.

Data:
{context}""",
)

# services/summary_renderer.py _SYSTEM_PROMPT
_register(
    "summary_renderer_system",
    1,
    """\
You are a QA Engineering Lead writing a structured post-run analysis report.
Be factual, direct, and actionable. Focus on failures and risks.
Do not pad the report. Base every statement strictly on the data provided.
""",
)

# services/summary_renderer.py _DEVELOPER_PROMPT
_register(
    "summary_renderer_developer",
    1,
    """\
{system}

You are explaining a test run failure to the engineering team who will fix it.
Produce a developer-focused summary in JSON (no markdown fences):

{{
  "headline": "one-sentence summary of the dominant failure",
  "root_cause_analysis": "2-3 sentences on the likely root cause with technical detail",
  "evidence_highlights": ["key evidence item 1", "key evidence item 2"],
  "fix_recommendations": ["specific fix 1", "specific fix 2", "specific fix 3"],
  "validation_steps": ["how to verify the fix"],
  "similar_historical_context": "mention if similar failures occurred recently (or 'No similar historical failures found')"
}}

Data:
{context}""",
)

# services/summary_renderer.py _MANAGER_PROMPT
_register(
    "summary_renderer_manager",
    1,
    """\
{system}

You are summarising a test run for an engineering manager or release manager.
Produce a manager-focused summary in JSON (no markdown fences):

{{
  "executive_summary": "2-3 sentences: business impact, release signal, next action",
  "release_recommendation": "GO | CONDITIONAL_GO | NO_GO — and why in one sentence",
  "scope_of_impact": "which features/services are affected",
  "key_risks": ["top risk 1", "top risk 2"],
  "recommended_decisions": ["decision needed from manager 1", "decision needed 2"],
  "timeline_guidance": "urgency — e.g. 'Fix required before this sprint release'"
}}

Data:
{context}""",
)

# agents/run_compare_agent.py _SYSTEM_PROMPT
_register(
    "run_compare_system",
    1,
    """\
You are a QA regression analyst comparing two test runs.
Use only the deterministic comparison data provided. Do not invent root causes.
If evidence is missing, say "Insufficient AI analysis evidence".
Return only valid JSON.
""",
)

# agents/run_compare_agent.py _REPORT_PROMPT
_register(
    "run_compare_report",
    1,
    """\
{system}

Comparison data:
{context}

Return this JSON shape:
{{
  "executive_summary": "2-4 sentence summary with exact counts",
  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
  "key_differences": ["specific difference"],
  "new_risks": ["new risk"],
  "resolved_risks": ["resolved risk"],
  "duration_concerns": ["duration concern"],
  "recommended_actions": ["action"],
  "confidence": 0,
  "confidence_reason": "why this confidence"
}}""",
)

# agents/release_risk_agent.py _REASONING_PROMPT
_register(
    "release_risk_reasoning",
    1,
    """\
You are a QA release gate analyst. The automated risk scoring model produced these results:

Deterministic Risk Scores (0-100 each):
{scores_json}

Composite Risk Score: {composite}/100
Automated Recommendation: {recommendation}

Test Run Context:
{summary}

Failure Details:
{failures}

GROUNDING RULES:
- The recommendation field ({recommendation}) is DETERMINISTIC — do not override or contradict it.
- Your job is to EXPLAIN the score, not re-evaluate it.
- If failure details are empty, state "No failures detected" — never fabricate failure descriptions.
- Only list blocking_issues that are directly supported by the failure data above.
- List conditions_for_go ONLY when recommendation is CONDITIONAL_GO; leave empty for GO or NO_GO.

EXAMPLE (CONDITIONAL_GO with blocking issues):
{{
  "reasoning": "Composite risk score of 38/100 driven primarily by 3 product bugs in the checkout flow and an elevated regression signal. Pass rate of 91% is above threshold but the checkout failures affect a critical user journey.",
  "blocking_issues": ["Checkout payment validation fails on amounts > $999", "Cart total mismatch after coupon removal"],
  "conditions_for_go": ["Fix both checkout bugs and rerun the e2e-checkout suite"]
}}

EXAMPLE (GO with no issues):
{{
  "reasoning": "Composite risk score of 12/100 with all dimensions in the green zone. 98.5% pass rate with only minor flaky test recurrences. No new regressions detected.",
  "blocking_issues": [],
  "conditions_for_go": []
}}

Respond ONLY with a valid JSON object:
{{
  "reasoning": "...",
  "blocking_issues": ["issue1", "issue2"],
  "conditions_for_go": ["condition1"]
}}""",
)

# agents/regression_watchman.py _CLASSIFY_PROMPT
_register(
    "regression_watchman_classify",
    1,
    """\
You are a QA regression analyst. For each failure cluster, classify it as one of:
  - "new_regression": first-time failure, not seen in recent baseline runs
  - "known_flaky_recurrence": test has flaked before, not caused by code changes
  - "environmental_anomaly": failure caused by infra/environment, not the application

GROUNDING RULES:
- If cluster history is empty or insufficient, state "Insufficient baseline data" in evidence — never guess.
- Confidence must reflect actual evidence strength: <40 if no history, 40-70 if partial, >70 only with clear signals.
- Do NOT override the deterministic pre-classification unless you have strong contradictory evidence.

EXAMPLE (good output):
{{
  "cl_001": {{
    "classification": "new_regression",
    "confidence": 82,
    "evidence": "3 tests failed for the first time; none appeared in the last 10 baseline runs."
  }}
}}

EXAMPLE (missing data):
{{
  "cl_002": {{
    "classification": "new_regression",
    "confidence": 30,
    "evidence": "Insufficient baseline data — only 1 historical run available."
  }}
}}

Cluster data:
{clusters_json}

Historical context:
{history_json}

Respond ONLY with a JSON object mapping cluster_id to a classification:
{{
  "cl_001": {{
    "classification": "new_regression" | "known_flaky_recurrence" | "environmental_anomaly",
    "confidence": 0-100,
    "evidence": "one sentence explaining why"
  }}
}}""",
)

# services/defect_promotion_service.py _DEFECT_PROMPT
_register(
    "defect_promotion_ticket",
    1,
    """\
You are a senior QA lead promoting a failure cluster to a defect ticket.

Cluster Information:
{cluster_json}

Root Cause Analyses for member tests:
{analyses_json}

Evidence:
{evidence_json}

Produce a Jira-ready defect in JSON format:
{{
  "title": "concise defect title (max 80 chars)",
  "description": "structured defect description with: What/Steps to reproduce/Expected/Actual/Environment",
  "severity": "CRITICAL | HIGH | MEDIUM | LOW",
  "component": "affected component or service name",
  "owner_team": "probable team responsible (e.g. payments-backend, auth-service, frontend)",
  "labels": ["regression", "automated-test", "cluster-promoted"],
  "duplicate_hint": "brief description to help detect similar open tickets (for dedup query)"
}}""",
)

# services/training/exporter.py _REASONING_SYSTEM_PROMPT
_register(
    "finetune_reasoning_system",
    1,
    """\
You are an expert QA/SRE analyst. Use your investigation tools to determine the root cause of the failing test and return a structured JSON analysis.""",
)

# services/test_case_ai_agent.py generate_test_cases system prompt
_register(
    "test_case_generate",
    1,
    """\
You are an expert QA engineer. Given a requirements description,
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
Generate 3-8 test cases covering: happy path, edge cases, error conditions, boundary values.""",
)

# services/test_case_ai_agent.py review_test_case system prompt
_register(
    "test_case_review",
    1,
    """\
You are a senior QA architect reviewing test cases for quality.
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
proper test data definition, no UI-dependency in unit tests, reproducible.""",
)

# services/test_case_ai_agent.py coverage_analysis system prompt
_register(
    "test_case_coverage",
    1,
    """\
You are a QA coverage analyst. Analyze requirements vs existing tests.
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
}""",
)

# services/test_case_ai_agent.py test_strategy system prompt
_register(
    "test_case_strategy",
    1,
    """\
You are a QA Director creating a test strategy document.
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
}""",
)

# services/test_case_ai_agent.py plan_optimizer system prompt
_register(
    "test_case_plan_optimizer",
    1,
    """\
You are a test planning optimizer. Given test cases and constraints,
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
Prioritize: smoke tests first, critical path second, regression last. Group by feature area for parallel execution.""",
)

# agents/investigator/hypotheses.py — shared evidence-weighing prompt for the
# five hypothesis sub-agents (Wave B, AI-1). Bounded: AT MOST one call per
# hypothesis per investigation; the deterministic verdict is the fallback.
_register(
    "investigator_hypothesis_weigh",
    1,
    """\
You are a QA failure investigator weighing evidence for ONE hypothesis about
why a test run failed.

Hypothesis under evaluation: {hypothesis_id} — {hypothesis_title}

Deterministic signals (computed from the run's data — treat as ground truth):
{signals_json}

Evidence lines:
{evidence_lines}

Deterministic pre-verdict: status={det_status}, confidence={det_confidence}.

GROUNDING RULES:
- Base your judgement ONLY on the signals and evidence above. Never invent
  test names, counts, or causes.
- Do not flip a deterministic "validated" or "invalidated" pre-verdict unless
  the evidence lines clearly contradict it; prefer adjusting confidence.
- If the evidence is thin or contradictory, return "inconclusive" with a low
  confidence and say what is missing.

Respond ONLY with a valid JSON object (no markdown fences):
{{
  "status": "validated" | "invalidated" | "inconclusive",
  "confidence": 0-100,
  "summary": "1-2 sentences explaining the verdict, citing specific evidence"
}}""",
)

# agents/investigator/synthesis.py — verdict narrative (Wave B, AI-1). One
# call per investigation; a deterministic template renders the narrative when
# no LLM is configured.
_register(
    "investigator_synthesis_narrative",
    1,
    """\
You are a QA failure investigator writing the final verdict narrative for an
automated investigation of a failing test run.

Primary cause chosen by deterministic precedence rules (do NOT override it):
{primary_cause}

Hypothesis results:
{hypotheses_json}

Run context:
{run_context}

GROUNDING RULES:
- The primary cause is already decided — explain it, do not re-adjudicate.
- Cite only counts, test names, and evidence that appear in the data above.
- If the primary cause is "unknown", honestly state why the evidence was
  conflicting or insufficient.
- 2-4 sentences, plain English, for a QA engineer audience.

Respond ONLY with a valid JSON object (no markdown fences):
{{
  "narrative": "2-4 sentence explanation of the verdict"
}}""",
)

# agents/fixer/generation.py — the Fixer's single fix-generation prompt (AI-2).
# Test-code-only: the model may ONLY edit the failing test's own code to remove
# non-determinism; touching product/source files is structurally rejected
# before any execution, so the prompt is emphatic about the boundary.
_register(
    "fixer_generate_patch",
    1,
    """\
You are a senior test-automation engineer fixing a FLAKY test. A flaky test
passes and fails non-deterministically without any product change. Your job is
to make ONLY the test's own code deterministic — never to change product code.

Failing test:
{test_name}

Diagnosis (from prior investigation / flip history / memory recall):
{diagnosis}

Current test source (the ONLY file you may edit):
{test_source}

STRICT RULES:
1. Edit ONLY the test file shown above. NEVER modify application/product code,
   configuration, CI files, or dependencies. A diff touching anything outside
   the test globs is rejected and wasted.
2. Fix the flakiness at its source: replace fixed sleeps with explicit waits,
   remove order/timing dependence, seed randomness, stabilise fixtures, make
   assertions robust to benign ordering. Do NOT weaken the assertion's intent
   or skip/xfail the test to make it "pass".
3. Keep the change minimal and self-contained.
4. If you cannot fix it from the test code alone, say so — do not guess.

Respond ONLY with a valid JSON object (no markdown fences):
{{
  "can_fix": true | false,
  "reasoning": "2-3 sentences: the flake source and your fix (or why test-only cannot fix it)",
  "patch": "a unified diff (git format, ---/+++ headers) editing ONLY the test file, or empty string when can_fix is false"
}}""",
)

# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════


def get_prompt(prompt_id: str) -> PromptDef:
    """Return the registered PromptDef, or raise KeyError with the known ids."""
    try:
        return _PROMPTS[prompt_id]
    except KeyError:
        known = ", ".join(sorted(_PROMPTS))
        raise KeyError(
            f"unknown prompt id {prompt_id!r} — registered ids: {known}"
        ) from None


def get_prompt_text(prompt_id: str) -> str:
    return get_prompt(prompt_id).text


def registry_prompt_ids() -> list[str]:
    return sorted(_PROMPTS)


def prompt_versions_used(*prompt_ids: str) -> dict[str, str]:
    """``{id: "v<version>:<hash12>"}`` for the prompts a code path actually used.

    Recorded in decision trails / ``_audit`` blocks so every verdict can be
    traced back to the exact prompt bytes that produced it.
    """
    return {pid: get_prompt(pid).version_tag for pid in prompt_ids}


def registry_versions() -> dict[str, str]:
    """Version tags for every backend-registered prompt."""
    return {pid: p.version_tag for pid, p in sorted(_PROMPTS.items())}


def registry_digest() -> str:
    """Stable digest over the backend prompt set (id → version/hash).

    Container-safe: does not require the manifest file or the mcp/ tree.
    Stamped into pipeline version snapshots.
    """
    prompts = {
        pid: {"version": p.version, "content_hash": p.content_hash}
        for pid, p in _PROMPTS.items()
    }
    return manifest_digest(prompts)


# ── MCP templates (hash-pinned, parsed — never imported) ─────────────────────


def _load_mcp_templates(path: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Parse ``PROMPT_TEMPLATES`` (+ optional ``PROMPT_TEMPLATE_VERSIONS``)
    out of the MCP templates module via ``ast``.

    Returns ``{id: {"version": int, "content_hash": str}}`` — empty when the
    source file is absent (backend container) or unparseable.
    """
    src_path = path or MCP_TEMPLATES_PATH
    try:
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    templates: dict[str, str] = {}
    versions: dict[str, int] = {}
    for node in tree.body:
        # Both plain and annotated module-level assignments.
        value: Optional[ast.expr]
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        else:
            continue
        if value is None:
            continue
        try:
            if "PROMPT_TEMPLATES" in names:
                templates = dict(ast.literal_eval(value))
            elif "PROMPT_TEMPLATE_VERSIONS" in names:
                versions = dict(ast.literal_eval(value))
        except (ValueError, TypeError):
            return {}
    return {
        pid: {
            "version": int(versions.get(pid, 1)),
            "content_hash": content_hash(str(text)),
        }
        for pid, text in templates.items()
    }


# ── Manifest + attestation ───────────────────────────────────────────────────


def build_manifest_prompts(
    mcp_templates_path: Optional[Path] = None,
) -> dict[str, dict[str, Any]]:
    """Current ``{id: {"version", "content_hash"}}`` map from source."""
    prompts: dict[str, dict[str, Any]] = {
        pid: {"version": p.version, "content_hash": p.content_hash}
        for pid, p in _PROMPTS.items()
    }
    prompts.update(_load_mcp_templates(mcp_templates_path))
    return dict(sorted(prompts.items()))


def manifest_digest(prompts: dict[str, dict[str, Any]]) -> str:
    """Canonical digest of a manifest ``prompts`` map.

    MUST stay in sync with ``_prompt_manifest_digest`` in
    ``scripts/quality_gate.py`` (stdlib-only mirror).
    """
    canonical = json.dumps(prompts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def attestation_watched_sources(repo_root: Optional[Path] = None) -> dict[str, str]:
    """Hash watched source text with platform line endings normalized."""
    root = repo_root or _REPO_ROOT
    watched: dict[str, str] = {}
    for relative in EVAL_ATTESTATION_WATCHED_PATHS:
        path = root / relative
        watched[relative] = (
            hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if path.exists()
            else "missing"
        )
    return watched


def load_manifest(path: Optional[Path] = None) -> dict[str, Any]:
    data = json.loads((path or MANIFEST_PATH).read_text(encoding="utf-8"))
    return dict(data)


def load_attestation(path: Optional[Path] = None) -> dict[str, Any]:
    data = json.loads((path or ATTESTATION_PATH).read_text(encoding="utf-8"))
    return dict(data)


def check_manifest(
    manifest_path: Optional[Path] = None,
    mcp_templates_path: Optional[Path] = None,
) -> list[str]:
    """Compare registered prompts against the pinned manifest.

    Returns a list of human-readable problems (empty = in sync). ``mcp.*``
    manifest entries are only verified when the MCP source file is present.
    """
    problems: list[str] = []
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        return [f"prompt_manifest.json unreadable: {exc}"]
    pinned = manifest.get("prompts")
    if not isinstance(pinned, dict):
        return ["prompt_manifest.json has no 'prompts' map"]

    current = build_manifest_prompts(mcp_templates_path)
    mcp_available = bool(_load_mcp_templates(mcp_templates_path))

    for pid, entry in sorted(current.items()):
        pin = pinned.get(pid)
        if pin is None:
            problems.append(
                f"prompt {pid!r} is registered but missing from the manifest"
            )
            continue
        if pin.get("content_hash") != entry["content_hash"]:
            problems.append(
                f"prompt {pid!r} text drifted: hash {entry['content_hash']} != "
                f"pinned {pin.get('content_hash')} (bump version + rewrite manifest)"
            )
        if pin.get("version") != entry["version"]:
            problems.append(
                f"prompt {pid!r} version {entry['version']} != pinned {pin.get('version')}"
            )
    for pid in sorted(pinned):
        if pid in current:
            continue
        if pid.startswith("mcp.") and not mcp_available:
            continue  # backend container: mcp source not shipped — guard covers it in CI
        problems.append(f"manifest entry {pid!r} has no registered prompt (stale)")
    return problems


def check_attestation(
    manifest_path: Optional[Path] = None,
    attestation_path: Optional[Path] = None,
) -> list[str]:
    """Verify the eval-gate attestation covers the CURRENT manifest digest."""
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        return [f"prompt_manifest.json unreadable: {exc}"]
    digest = manifest_digest(manifest.get("prompts") or {})
    try:
        attestation = load_attestation(attestation_path)
    except (OSError, ValueError) as exc:
        return [
            f"prompt_manifest_eval.json unreadable ({exc}) — run "
            "`python -m app.services.prompt_registry --attest <change-id>`"
        ]
    problems: list[str] = []
    if attestation.get("manifest_digest") != digest:
        problems.append(
            "manifest changed without a fresh eval-gate attestation "
            f"(attested digest {str(attestation.get('manifest_digest'))[:12]}… != "
            f"current {digest[:12]}…) — run "
            "`python -m app.services.prompt_registry --attest <change-id>`"
        )
    current_watched = attestation_watched_sources()
    if attestation.get("watched_sources") != current_watched:
        problems.append(
            "model routing or reviewer sources changed without a fresh eval-gate "
            "attestation — run `python -m app.services.prompt_registry --attest <change-id>`"
        )
    if attestation.get("verdict") != EvalVerdict.PASS.value:
        problems.append(
            f"attested eval-gate verdict is {attestation.get('verdict')!r}, not pass — "
            "the prompt change did not clear the eval gate"
        )
    return problems


# ── CLI: --check / --write-manifest / --attest ───────────────────────────────


def _echo(message: str) -> None:
    """CLI output. Plain stdout on purpose — this is developer tooling, not
    app runtime (the backend.no-print gate reserves print/log paths for
    structlog)."""
    sys.stdout.write(message + "\n")


def _write_manifest() -> None:
    prompts = build_manifest_prompts()
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "hash_algorithm": f"sha256[:{PROMPT_HASH_LENGTH}]",
        "prompts": prompts,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _echo(f"wrote {MANIFEST_PATH} ({len(prompts)} prompts, "
          f"digest {manifest_digest(prompts)[:12]}…)")


# Offline gates whose golden-set scorer is a prompt-independent deterministic
# heuristic (no prompt text reaches it), so a prompt change cannot regress it.
# duplicate_detection's shipped scorer is a lexical word-overlap simulation
# whose golden set intentionally contains paraphrase duplicates it cannot
# catch (accuracy 0.5 by construction) — it is recorded in the attestation as
# insufficient rather than passing the PROMPT gate. The full DB-backed gate
# (``--attest`` without ``--offline``) still evaluates it against its
# baseline like everything else.
_OFFLINE_INSUFFICIENT_GATES = frozenset({"duplicate_detection"})


def _offline_gate_results() -> tuple[EvalVerdict, list[dict[str, Any]]]:
    """Score the default agent-stack gates against the in-repo golden datasets.

    No DB / no baselines: applies ``eval_gate_service._evaluate_rules`` with
    ``baseline=None`` (default thresholds). Honest about its mode — the
    attestation records ``offline_golden`` so reviewers can tell it apart
    from a full baseline-compared gate run.
    """
    from app.services.ai_eval_service import compute_metrics_for_task_type
    from app.services.eval_gate_service import (
        DEFAULT_AGENT_STACK_GATES,
        _evaluate_rules,
    )
    from app.services.golden_datasets import GOLDEN_DATASETS

    results: list[dict[str, Any]] = []
    all_passed = True
    has_insufficient = False
    for gate in DEFAULT_AGENT_STACK_GATES:
        task_type = gate["task_type"]
        spec = GOLDEN_DATASETS.get(task_type)
        items = spec["get_items"]() if spec else []
        current = compute_metrics_for_task_type(task_type, items) if items else {}
        rules = _evaluate_rules(current, None) if items else [
            {"rule": "dataset_exists", "passed": False, "detail": "no golden dataset"}
        ]
        passed = bool(items) and all(r["passed"] for r in rules)
        is_insufficient = task_type in _OFFLINE_INSUFFICIENT_GATES
        if is_insufficient:
            has_insufficient = True
        else:
            all_passed = all_passed and passed
        results.append({
            "task_type": task_type,
            "agent_name": gate["agent_name"],
            "status": (
                EvalVerdict.INSUFFICIENT_SAMPLES
                if is_insufficient
                else EvalVerdict.PASS if passed else EvalVerdict.FAIL
            ).value,
            "metrics": {
                k: current.get(k) for k in ("accuracy", "f1_score", "total")
            },
            "rule_results": rules,
        })
    from app.services.prompt_eval_recordings import check_recordings, recordings_verdict

    recording_problems, insufficient_prompts = check_recordings()
    recorded_verdict = recordings_verdict(recording_problems, insufficient_prompts)
    if recorded_verdict is not EvalVerdict.PASS:
        return recorded_verdict, results
    if has_insufficient:
        return EvalVerdict.INSUFFICIENT_SAMPLES, results
    return (EvalVerdict.PASS if all_passed else EvalVerdict.FAIL), results


async def _online_gate_result(change_id: str) -> dict[str, Any]:
    from app.db.postgres import AsyncSessionLocal
    from app.services.eval_gate_service import evaluate_agent_stack_release_gate

    async with AsyncSessionLocal() as db:
        result = await evaluate_agent_stack_release_gate(
            db,
            change_id=change_id,
            prompt_versions=registry_versions(),
        )
        await db.commit()  # CLI owns its session — persist the gate run row
        return result


def _attest(change_id: str, offline: bool, notes: str) -> int:
    from datetime import datetime, timezone

    problems = check_manifest()
    if problems:
        _echo("manifest is out of sync with the registry — fix first:")
        for p in problems:
            _echo(f"  - {p}")
        return 1

    if offline:
        verdict, gate_results = _offline_gate_results()
        mode = "offline_golden"
        gate_run_id = None
    else:
        import asyncio

        try:
            result = asyncio.run(_online_gate_result(change_id))
        except Exception as exc:  # noqa: BLE001 — CLI boundary
            _echo(f"eval gate run failed ({exc!r}); retry with --offline "
                  "to score against the in-repo golden datasets")
            return 1
        raw_verdict = result.get("status")
        verdict = raw_verdict if isinstance(raw_verdict, EvalVerdict) else EvalVerdict(raw_verdict)
        gate_results = [
            {
                "task_type": g.get("task_type"),
                "agent_name": g.get("agent_name"),
                "status": g.get("status"),
            }
            for g in result.get("gate_results", [])
        ]
        mode = "eval_gate_service"
        gate_run_id = result.get("gate_run_id")

    if not isinstance(verdict, EvalVerdict):
        verdict = EvalVerdict(str(verdict).lower())

    manifest = load_manifest()
    attestation = {
        "schema_version": 1,
        "change_id": change_id,
        "manifest_digest": manifest_digest(manifest.get("prompts") or {}),
        "verdict": verdict.value,
        "mode": mode,
        "eval_gate_run_id": gate_run_id,
        "gate_results": gate_results,
        "prompt_versions": registry_versions(),
        "watched_sources": attestation_watched_sources(),
        "attested_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    ATTESTATION_PATH.write_text(
        json.dumps(attestation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _echo(f"wrote {ATTESTATION_PATH} (verdict={verdict.value}, mode={mode})")
    return 0 if verdict is EvalVerdict.PASS else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.prompt_registry",
        description="Prompt manifest tooling (AI-F2). See module docstring.",
    )
    parser.add_argument("--check", action="store_true",
                        help="Verify registry ⇄ manifest ⇄ attestation sync.")
    parser.add_argument("--write-manifest", action="store_true",
                        help="Regenerate prompt_manifest.json from source.")
    parser.add_argument("--attest", metavar="CHANGE_ID",
                        help="Run the eval gate and record the attestation "
                             "for the current manifest digest.")
    parser.add_argument("--offline", action="store_true",
                        help="With --attest: score against in-repo golden "
                             "datasets (no DB / baselines required).")
    parser.add_argument("--notes", default="",
                        help="With --attest: free-text note stored in the attestation.")
    args = parser.parse_args(argv)

    if args.write_manifest:
        _write_manifest()
        return 0
    if args.attest:
        return _attest(args.attest, offline=args.offline, notes=args.notes)

    problems = check_manifest() + check_attestation()
    if problems:
        for p in problems:
            _echo(f"  - {p}")
        return 1
    _echo(f"prompt registry in sync ({len(build_manifest_prompts())} prompts)")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    sys.exit(main())
