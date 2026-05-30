"""
Tag Utilities — shared normalization, provenance, and system-tag constants.

All tag operations across the system should use these helpers to ensure
consistent behavior for custom, ingested, and system-generated tags.

Usage:
    from app.services.tag_utils import normalize_tags, is_system_tag, merge_tags, SYSTEM_TAGS
"""
import re

# ── Tag type constants ────────────────────────────────────────────────────────

TAG_TYPE_CUSTOM = "custom"
TAG_TYPE_INGESTED = "ingested"
TAG_TYPE_SYSTEM = "system"

# ── Reserved system tags (cannot be created/edited by users) ──────────────────

SYSTEM_TAGS: frozenset[str] = frozenset({
    # Outcome tags (applied after ingestion)
    "passed", "failed", "skipped", "broken",
    # Signal tags (applied after AI analysis)
    "flaky", "regression", "duplicate",
    # Run-level summary tags
    "all_passed", "has_failures", "has_skips", "flaky_content",
    "regression_detected", "duplicate_content",
    # Suite traceability
    "needs_review",
})

# Regex: only allow alphanumeric, hyphens, underscores, dots
_TAG_PATTERN = re.compile(r"[^a-z0-9\-_.]")


def normalize_tag(tag: str) -> str:
    """Normalize a single tag: lowercase, strip, collapse whitespace, remove invalid chars."""
    cleaned = tag.strip().lower()
    cleaned = re.sub(r"\s+", "-", cleaned)  # spaces → hyphens
    cleaned = _TAG_PATTERN.sub("", cleaned)  # remove invalid chars
    return cleaned[:50]  # max 50 chars


def normalize_tags(tags: list[str]) -> list[str]:
    """Normalize and deduplicate a list of tags, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        normalized = normalize_tag(tag)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def is_system_tag(tag: str) -> bool:
    """Check if a tag is a reserved system tag."""
    return normalize_tag(tag) in SYSTEM_TAGS


def validate_custom_tags(tags: list[str]) -> list[str]:
    """Normalize tags and reject any reserved system tags. Returns clean list."""
    normalized = normalize_tags(tags)
    return [t for t in normalized if t not in SYSTEM_TAGS]


def merge_tags(
    existing: list[str] | None,
    new_tags: list[str],
) -> list[str]:
    """Merge new tags into existing, deduplicating. Preserves existing order."""
    existing = existing or []
    combined = list(existing)
    existing_set = set(existing)
    for tag in new_tags:
        if tag not in existing_set:
            combined.append(tag)
            existing_set.add(tag)
    return combined
