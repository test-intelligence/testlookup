"""
Failure category normalization — single source of truth.

Converts raw LLM output strings to valid ``FailureCategory`` enum values
using a deterministic alias map.  No fuzzy matching — aliases are explicit
so results are reproducible across runs.
"""
from __future__ import annotations

import logging
from typing import Any

from app.models.postgres import FailureCategory

logger = logging.getLogger("services.category_normalizer")

# Canonical set for O(1) lookup
VALID_CATEGORIES: frozenset[str] = frozenset(cat.value for cat in FailureCategory)

# Deterministic alias map — covers common LLM output variations.
# Add new aliases here rather than scattering fuzzy logic across agents.
CATEGORY_ALIASES: dict[str, str] = {
    # PRODUCT_BUG aliases
    "BUG":              FailureCategory.PRODUCT_BUG.value,
    "PRODUCT":          FailureCategory.PRODUCT_BUG.value,
    "CODE_BUG":         FailureCategory.PRODUCT_BUG.value,
    "APPLICATION_BUG":  FailureCategory.PRODUCT_BUG.value,
    "SOFTWARE_BUG":     FailureCategory.PRODUCT_BUG.value,
    "CODE_DEFECT":      FailureCategory.PRODUCT_BUG.value,
    "APP_BUG":          FailureCategory.PRODUCT_BUG.value,
    "PRODUT_BUG":       FailureCategory.PRODUCT_BUG.value,  # common LLM typo
    # INFRASTRUCTURE aliases
    "INFRA":            FailureCategory.INFRASTRUCTURE.value,
    "ENV":              FailureCategory.INFRASTRUCTURE.value,
    "ENVIRONMENT":      FailureCategory.INFRASTRUCTURE.value,
    "NETWORK":          FailureCategory.INFRASTRUCTURE.value,
    "INFRA_ISSUE":      FailureCategory.INFRASTRUCTURE.value,
    "PLATFORM":         FailureCategory.INFRASTRUCTURE.value,
    # TEST_DATA aliases
    "DATA":             FailureCategory.TEST_DATA.value,
    "DATA_ISSUE":       FailureCategory.TEST_DATA.value,
    "SETUP":            FailureCategory.TEST_DATA.value,
    "TEST_SETUP":       FailureCategory.TEST_DATA.value,
    "FIXTURE":          FailureCategory.TEST_DATA.value,
    # AUTOMATION_DEFECT aliases
    "TEST_CODE":        FailureCategory.AUTOMATION_DEFECT.value,
    "AUTOMATION":       FailureCategory.AUTOMATION_DEFECT.value,
    "TEST_BUG":         FailureCategory.AUTOMATION_DEFECT.value,
    "SCRIPT_BUG":       FailureCategory.AUTOMATION_DEFECT.value,
    "TEST_DEFECT":      FailureCategory.AUTOMATION_DEFECT.value,
    # FLAKY aliases
    "INTERMITTENT":     FailureCategory.FLAKY.value,
    "RACE_CONDITION":   FailureCategory.FLAKY.value,
    "NONDETERMINISTIC": FailureCategory.FLAKY.value,
    "NON_DETERMINISTIC": FailureCategory.FLAKY.value,
    "TIMING":           FailureCategory.FLAKY.value,
    "UNSTABLE":         FailureCategory.FLAKY.value,
}


def normalize_category(raw: Any) -> str:
    """Normalize a raw category value to a valid ``FailureCategory`` string.

    Accepts enum instances, plain strings, or None.  Always returns a valid
    ``FailureCategory.value`` string — never raises.

    Args:
        raw: The value to normalize (str, FailureCategory, or None).

    Returns:
        A valid FailureCategory value string (e.g. ``"PRODUCT_BUG"``).
    """
    if raw is None:
        return FailureCategory.UNKNOWN.value

    # Handle enum instances directly
    if isinstance(raw, FailureCategory):
        return raw.value

    # Extract .value from enum-like objects
    raw_str = str(getattr(raw, "value", raw) or "").strip().upper()

    if not raw_str:
        return FailureCategory.UNKNOWN.value

    # Exact match
    if raw_str in VALID_CATEGORIES:
        return raw_str

    # Alias match
    resolved = CATEGORY_ALIASES.get(raw_str)
    if resolved:
        logger.info("Category normalized: '%s' → '%s' (alias)", raw_str, resolved)
        return resolved

    # Fallback
    logger.warning("Unrecognized failure category '%s' — falling back to UNKNOWN", raw_str)
    return FailureCategory.UNKNOWN.value


def normalize_category_in_analysis(analysis: dict) -> dict:
    """Normalize ``failure_category`` inside an analysis dict in-place.

    Adds ``_category_corrected_from`` metadata when normalization occurs.
    Returns the same dict for chaining.
    """
    raw = analysis.get("failure_category", "")
    normalized = normalize_category(raw)

    raw_str = str(getattr(raw, "value", raw) or "").strip().upper()
    if raw_str and raw_str != normalized:
        analysis["_category_corrected_from"] = raw_str

    analysis["failure_category"] = normalized
    return analysis
