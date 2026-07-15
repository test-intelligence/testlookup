"""LangChain tool: recall prior history for the failing test (AI-F3).

Tenant isolation is load-bearing here: the project/fingerprint context is set
SERVER-SIDE by ``run_triage_agent`` via a ``ContextVar`` before the ReAct loop
starts — the LLM's tool input is treated as an optional free-text query only,
never as an identifier. Without project context the tool answers "unavailable"
rather than falling back to an unscoped lookup.

Never raises into the agent loop: every failure path returns a string.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar, Token
from typing import Any, Optional

from langchain_core.tools import tool

logger = logging.getLogger("tools.recall_memory")

_RECALL_CONTEXT: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "recall_memory_context", default=None,
)


def set_recall_context(
    *,
    project_id: Optional[str],
    test_fingerprint: Optional[str] = None,
    test_name: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Token:
    """Bind the current investigation's identity for the recall tool.

    Called by ``run_triage_agent`` before the ReAct executor starts; the
    returned token MUST be passed to :func:`reset_recall_context` in a
    ``finally`` block so concurrent triages never see each other's context.
    """
    return _RECALL_CONTEXT.set({
        "project_id": project_id,
        "test_fingerprint": test_fingerprint,
        "test_name": test_name,
        "error_message": error_message,
    })


def reset_recall_context(token: Token) -> None:
    try:
        _RECALL_CONTEXT.reset(token)
    except Exception:  # pragma: no cover — token from another context
        _RECALL_CONTEXT.set(None)


@tool
async def recall_similar_failures(query: str = "") -> str:
    """
    Recall what this platform already knows about the failing test under
    investigation: prior human corrections of its classification (these are
    authoritative), earlier AI root-cause analyses of the same test, the top
    semantically similar past failures in this project, and its quarantine /
    flakiness-flip history.

    Args:
        query: Optional extra context (e.g. the error text) to refine the
            similarity search. The test identity itself comes from the
            investigation context, not from this input.

    Returns:
        Compact, citation-style history lines, or a "no history" note.
    """
    ctx = _RECALL_CONTEXT.get() or {}
    project_id = ctx.get("project_id")
    if not project_id:
        return (
            "Memory recall unavailable: no project context bound to this "
            "investigation (recall is project-scoped)."
        )
    try:
        project_uuid = uuid.UUID(str(project_id))
    except (TypeError, ValueError):
        return "Memory recall unavailable: invalid project context."

    try:
        from app.db.postgres import AsyncSessionLocal
        from app.services.memory_recall import (
            recall_failure_history,
            render_recall_report,
        )

        error_text = ctx.get("error_message") or (query or "").strip() or None
        async with AsyncSessionLocal() as db:
            recall = await recall_failure_history(
                db,
                project_uuid,
                test_fingerprint=ctx.get("test_fingerprint"),
                error_text=error_text,
                test_name=ctx.get("test_name"),
            )
        return render_recall_report(recall)
    except Exception as exc:  # noqa: BLE001 — must never raise into the loop
        logger.debug("recall tool degraded (non-critical): %s", exc)
        return f"Memory recall unavailable: {str(exc)[:200]}"
