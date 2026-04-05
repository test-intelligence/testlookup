"""
Strict LLM JSON parser with schema validation.

Replaces the fragile greedy `re.search(r"\\{.*\\}")` extraction used across
multiple agents. Provides:
  1. Markdown fence stripping
  2. Non-greedy JSON extraction (innermost braces first)
  3. Schema validation against expected keys
  4. Structured fallback with reason tracking

Used by: summary_agent, release_risk_agent, regression_watchman, and any
agent that expects JSON from an LLM response.
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger("services.llm_json_parser")

# Regex to strip markdown code fences: ```json ... ``` or ``` ... ```
_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)

# Non-greedy JSON object extraction: finds the outermost balanced braces
_JSON_BLOCK_PATTERN = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def parse_llm_json(
    raw: str,
    expected_keys: list[str] | None = None,
    fallback: dict | None = None,
    context: str = "",
) -> tuple[dict[str, Any], str | None]:
    """
    Parse a JSON object from LLM output.

    Args:
        raw: Raw LLM response text (may contain markdown fences, prose, etc.)
        expected_keys: If provided, validates that all keys are present in the parsed dict
        fallback: Default dict to return on parse failure
        context: Label for logging (e.g. "summary_layer2", "release_risk_reasoning")

    Returns:
        (parsed_dict, error_reason)
        - On success: (parsed_dict, None)
        - On failure: (fallback or {}, "reason string")
    """
    if not raw or not raw.strip():
        reason = "empty_response"
        logger.warning("[%s] LLM returned empty response", context)
        return fallback or {}, reason

    cleaned = raw.strip()

    # Step 1: Extract from markdown fences if present
    fence_match = _FENCE_PATTERN.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Step 2: If it starts with '{', try direct parse
    if cleaned.startswith("{"):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return _validate_schema(parsed, expected_keys, context, fallback)
        except json.JSONDecodeError:
            pass

    # Step 3: Find JSON blocks in the text (non-greedy)
    # Try multiple extraction strategies, from most specific to least
    candidates = _extract_json_candidates(cleaned)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return _validate_schema(parsed, expected_keys, context, fallback)
        except json.JSONDecodeError:
            continue

    reason = "invalid_json"
    logger.warning("[%s] Could not extract valid JSON from LLM response (len=%d)", context, len(raw))
    return fallback or {}, reason


def _extract_json_candidates(text: str) -> list[str]:
    """
    Extract potential JSON object strings from text.
    Returns candidates ordered by likelihood of being valid.
    """
    candidates: list[str] = []

    # Strategy 1: Find balanced brace blocks
    matches = _JSON_BLOCK_PATTERN.findall(text)
    for m in matches:
        candidates.append(m)

    # Strategy 2: Try from last '{' to last '}' (handles nested objects)
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace:last_brace + 1])

    return candidates


def _validate_schema(
    parsed: dict,
    expected_keys: list[str] | None,
    context: str,
    fallback: dict | None,
) -> tuple[dict[str, Any], str | None]:
    """Validate parsed dict against expected schema keys."""
    if expected_keys is None:
        return parsed, None

    missing = [k for k in expected_keys if k not in parsed]
    if not missing:
        return parsed, None

    # Partial match: fill in missing keys with defaults
    if len(missing) <= len(expected_keys) // 2:
        for key in missing:
            parsed[key] = _default_for_key(key)
        logger.info(
            "[%s] Schema partial match: filled %d missing keys: %s",
            context, len(missing), missing,
        )
        return parsed, None

    # Too many keys missing: schema mismatch
    reason = "schema_mismatch"
    logger.warning(
        "[%s] Schema mismatch: missing keys %s (of %d expected)",
        context, missing, len(expected_keys),
    )
    return fallback or parsed, reason


def _default_for_key(key: str) -> Any:
    """Provide a sensible default for a missing schema key."""
    key_lower = key.lower()
    if any(word in key_lower for word in ("list", "issues", "steps", "actions", "traces", "ids", "sources", "failures", "recommendations")):
        return []
    if any(word in key_lower for word in ("count", "score", "rate", "total")):
        return 0
    if any(word in key_lower for word in ("breakdown",)):
        return {}
    return ""
