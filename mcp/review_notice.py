"""Review status for MCP tool results (architecture E8.4, section 8.3).

Report routes carry a ``review`` block (E8.3): whether a person has accepted
the AI content. An agent reading a tool result must see that state, not just
the content, so every tool that returns an AI report appends these lines.

A backend that predates the envelope yields ``review_state: unknown`` with an
explicit warning. A missing state must never read as reviewed.
"""
from __future__ import annotations

from typing import Any

UNKNOWN_STATE = "unknown"
UNKNOWN_WARNING = (
    "_Review status unavailable: treat any AI content above as an unreviewed draft._"
)


def review_state(data: Any) -> str:
    """The report's review state, or ``unknown`` when the payload has none."""
    if isinstance(data, dict):
        review = data.get("review")
        if isinstance(review, dict) and review.get("state"):
            return str(review["state"])
    return UNKNOWN_STATE


def review_lines(data: Any) -> list[str]:
    """``review_state``, the review message and the AI disclaimer, as markdown lines."""
    state = review_state(data)
    lines = ["", f"**review_state:** `{state}`"]
    if state == UNKNOWN_STATE:
        lines.append(UNKNOWN_WARNING)
        return lines
    review = data["review"]
    if review.get("message"):
        lines.append(f"_{review['message']}_")
    if data.get("ai_disclaimer"):
        lines.append(f"> {data['ai_disclaimer']}")
    return lines
