"""
Search Ranking — pure scoring functions for hybrid search.

compute_hybrid_score: blends keyword, semantic, recency, recurrence, status signals.
build_match_reasons:  human-readable list of why a result matched.
"""
from __future__ import annotations
from datetime import datetime, timezone


def compute_hybrid_score(
    has_keyword_match: bool,
    semantic_score: float,
    last_run_date: str | None,
    failure_count: int,
    status: str,
) -> float:
    """
    Weighted composite:
      keyword_match  0.30 * (1.0 if keyword hit else 0.0)
      semantic       0.30 * semantic_score (0.0-1.0)
      recency        0.20 * recency_factor (1.0 for today, decays over 90 days)
      recurrence     0.10 * min(failure_count / 10.0, 1.0)
      status_weight  0.10 * (1.0 if FAILED, 0.5 if BROKEN, 0.0 otherwise)
    """
    kw = 1.0 if has_keyword_match else 0.0
    sem = max(0.0, min(1.0, semantic_score))

    # Recency: days since last run, decayed over 90 days
    recency = 0.0
    if last_run_date:
        try:
            dt = datetime.fromisoformat(last_run_date.replace("Z", "+00:00"))
            days_ago = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
            recency = max(0.0, 1.0 - days_ago / 90.0)
        except (ValueError, TypeError):
            pass

    recurrence = min(max(failure_count, 0) / 10.0, 1.0)

    status_upper = (status or "").upper()
    status_w = 1.0 if status_upper == "FAILED" else (0.5 if status_upper == "BROKEN" else 0.0)

    return round(0.30 * kw + 0.30 * sem + 0.20 * recency + 0.10 * recurrence + 0.10 * status_w, 4)


def build_match_reasons(
    has_keyword_match: bool,
    semantic_score: float,
    failure_count: int,
    status: str,
    source_mode: str = "hybrid",
) -> list[str]:
    """Build human-readable match explanation list."""
    reasons: list[str] = []
    if has_keyword_match:
        reasons.append("Keyword match on test name or error message")
    if semantic_score > 0.0:
        reasons.append(f"Semantic similarity: {semantic_score:.0%}")
    if failure_count > 3:
        reasons.append(f"Recurring failure ({failure_count} occurrences)")
    status_upper = (status or "").upper()
    if status_upper == "FAILED":
        reasons.append("Currently failing")
    elif status_upper == "BROKEN":
        reasons.append("Currently broken")
    if not reasons:
        reasons.append(f"Matched via {source_mode} search")
    return reasons
