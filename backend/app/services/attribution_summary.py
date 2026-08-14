"""Turn per-failure verdicts into the one line a PR reviewer reads.

Phase 5 (P5-A) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

## Why the payload, not the plumbing

The PR-comment and commit-status plumbing already existed. What it delivered was
raw counts — *"Failed: 7"* — which is exactly the signal that makes people stop
reading: at Google roughly 84% of pass→fail transitions involve a flaky test, so
a bare failure count is mostly noise, and the surveyed adoption gap says the
verdict has to arrive **before** a human is paged, not in a dashboard they must
remember to open.

So the change is one line:

    7 failures: 2 attributable to this change, 4 likely infrastructure
    (cluster sfc_001 — networking), 1 uncertain

## What this deliberately does not do

It does not hide anything, reorder the failure list, or mark a check green
because the failures looked flaky. A newly-flaky test reflects a real bug often
enough that suppression is the one irreversible mistake available here, so this
module only ever *describes* the failures the comment already lists.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Optional

import structlog

from app.services.failure_attribution_service import (
    VERDICT_LABELS,
    AttributionVerdict,
)

logger = structlog.get_logger("services.attribution_summary")

# Order the clauses so the most actionable reads first. A reviewer wants "is
# any of this mine?" answered before anything else.
_CLAUSE_ORDER: tuple[AttributionVerdict, ...] = (
    AttributionVerdict.LIKELY_YOUR_CHANGE,
    AttributionVerdict.LIKELY_INFRA,
    AttributionVerdict.LIKELY_FLAKY,
    AttributionVerdict.UNCERTAIN,
)

_CLAUSE_PHRASES: dict[AttributionVerdict, str] = {
    AttributionVerdict.LIKELY_YOUR_CHANGE: "attributable to this change",
    AttributionVerdict.LIKELY_INFRA: "likely infrastructure",
    AttributionVerdict.LIKELY_FLAKY: "known-flaky",
    AttributionVerdict.UNCERTAIN: "uncertain",
}


def _cluster_note(attributions: Iterable[Any]) -> Optional[str]:
    """Name the dominant cluster behind the infrastructure verdicts.

    A reviewer can check one shared dependency; they cannot check "infra".
    """
    keys: Counter = Counter()
    causes: dict[str, str] = {}
    for item in attributions:
        verdict = getattr(item, "verdict", None)
        if verdict is not AttributionVerdict.LIKELY_INFRA:
            continue
        inputs = getattr(item, "inputs", None)
        key = getattr(inputs, "cluster_key", None)
        if key:
            keys[key] += 1
            causes[key] = (getattr(inputs, "cluster_cause_family", None) or "unknown")
    if not keys:
        return None
    top, _count = keys.most_common(1)[0]
    cause = causes.get(top, "unknown").replace("_", " ")
    return f"cluster {top} — {cause}"


def summarize(attributions: Iterable[Any]) -> str:
    """One sentence describing what the failures are, or "" when there are none.

    Never raises; an unrecognised verdict is counted as uncertain rather than
    dropped, because a silently-missing failure is the one outcome worse than
    an unhelpfully-labelled one.
    """
    counts: Counter = Counter()
    items = list(attributions or [])
    for item in items:
        verdict = getattr(item, "verdict", None)
        if not isinstance(verdict, AttributionVerdict):
            verdict = AttributionVerdict.UNCERTAIN
        counts[verdict] += 1

    total = sum(counts.values())
    if total == 0:
        return ""

    clauses: list[str] = []
    for verdict in _CLAUSE_ORDER:
        count = counts.get(verdict, 0)
        if not count:
            continue
        phrase = _CLAUSE_PHRASES[verdict]
        if verdict is AttributionVerdict.LIKELY_INFRA:
            note = _cluster_note(items)
            if note:
                phrase = f"{phrase} ({note})"
        clauses.append(f"{count} {phrase}")

    # Every counted failure must appear in the sentence. If a verdict member is
    # ever added without a clause phrase, the arithmetic below catches it
    # rather than letting failures vanish from the summary.
    described = sum(counts.get(v, 0) for v in _CLAUSE_ORDER)
    if described != total:  # pragma: no cover - guarded by test
        logger.warning(
            "attribution_summary_incomplete",
            described=described,
            total=total,
        )
        clauses.append(f"{total - described} unclassified")

    noun = "failure" if total == 1 else "failures"
    return f"{total} {noun}: " + ", ".join(clauses)


def verdict_breakdown(attributions: Iterable[Any]) -> dict[str, int]:
    """Machine-readable counts, keyed by verdict value.

    Every verdict member appears, including zeros, so a consumer rendering the
    breakdown cannot silently omit a category it has never seen.
    """
    counts = {verdict.value: 0 for verdict in AttributionVerdict}
    for item in attributions or []:
        verdict = getattr(item, "verdict", None)
        if not isinstance(verdict, AttributionVerdict):
            verdict = AttributionVerdict.UNCERTAIN
        counts[verdict.value] += 1
    return counts


def summary_labels() -> dict[str, str]:
    """The human label for each verdict, for a consumer rendering the counts."""
    return {verdict.value: VERDICT_LABELS[verdict] for verdict in AttributionVerdict}
