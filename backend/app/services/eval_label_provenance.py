"""Leakage-safe provenance rules for feedback and review eval labels (E9.10)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

EVAL_LABEL_HOLDOUT_DAYS = 14
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_LABEL_SOURCES = frozenset({"feedback", "review"})


def valid_manifest_checksum(value: Any) -> str | None:
    """Return a normalized manifest checksum, or ``None`` without guessing."""
    return value if isinstance(value, str) and _CHECKSUM.fullmatch(value) else None


def checksum_from_execution_metadata(metadata: Any) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    return valid_manifest_checksum(metadata.get("eval_manifest_checksum"))


def checksum_from_analysis(analysis: Any) -> str | None:
    """Read the manifest frozen into an analysis' routing audit."""
    return checksum_from_execution_metadata(getattr(analysis, "routing_metadata", None))


def _label_created_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_label_derived(metadata: Mapping[str, Any]) -> bool:
    return (
        metadata.get("label_source") in _LABEL_SOURCES
        or "feedback_id" in metadata
        or "review_id" in metadata
    )


def gate_eligible_items(
    items: Sequence[dict[str, Any]],
    *,
    gate_manifest_checksum: str,
    evaluated_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Exclude leaked or too-recent human labels from a release gate.

    Golden/static samples carry no feedback/review marker and remain eligible.
    Label-derived samples fail closed when their provenance or timestamp is
    absent, malformed, from the candidate manifest, or inside the holdout.
    """
    candidate = valid_manifest_checksum(gate_manifest_checksum)
    if candidate is None:
        raise ValueError("gate_manifest_checksum must be a 64-character lowercase hex digest")
    now = (evaluated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now - timedelta(days=EVAL_LABEL_HOLDOUT_DAYS)
    eligible: list[dict[str, Any]] = []
    for item in items:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, Mapping) or not _is_label_derived(metadata):
            eligible.append(item)
            continue
        source_checksum = valid_manifest_checksum(metadata.get("eval_manifest_checksum"))
        created_at = _label_created_at(metadata.get("label_created_at"))
        if source_checksum is None or created_at is None:
            continue
        if source_checksum == candidate or created_at > cutoff:
            continue
        eligible.append(item)
    return eligible


__all__ = [
    "EVAL_LABEL_HOLDOUT_DAYS",
    "checksum_from_analysis",
    "checksum_from_execution_metadata",
    "gate_eligible_items",
    "valid_manifest_checksum",
]
