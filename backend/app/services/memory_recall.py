"""Memory recall for the reasoning path (AI-F3).

``agent_memory_service`` was write-only: pipelines persisted memory entries but
nothing in the triage loop ever read them back. This module is the READ side —
one project-scoped service function that gathers everything the platform
already knows about a failing test and renders it as compact, citation-style
lines an LLM (or a chat context block) can consume:

  (a) prior HUMAN CORRECTIONS for the fingerprint (``analysis_corrections`` —
      the same source the analysis agent uses for its learning-loop
      short-circuit; a correction here is authoritative),
  (b) prior AI analyses of the same fingerprint (latest 3: category /
      confidence / date),
  (c) semantically similar past failures via the existing per-project
      ChromaDB memory index (``agent_memory_service.recall_similar`` — the
      project_id filter there is load-bearing tenant isolation),
  (d) a quarantine / flip-history one-liner (``flaky_quarantine_requests``).

Contract: **never raises**. Every sub-fetch is individually guarded; a broken
store degrades to an empty section, and a test with no history returns
``has_history=False`` plus a graceful "no history" line. Output is bounded —
this text lands inside an LLM context window.

Consumed by ``app.tools.recall_memory.recall_similar_failures`` (the ReAct
tool) and ``agents/conversation.py`` (chat retrieval for a referenced test).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("services.memory_recall")

# Bounds — recall output is LLM context, keep it compact.
MAX_PRIOR_ANALYSES = 3
MAX_SIMILAR_FAILURES = 3
_LINE_EXCERPT_CHARS = 160
MAX_REPORT_CHARS = 1800

NO_HISTORY_LINE = "No prior history found for this test in this project."


def _date_of(value: Any) -> str:
    """YYYY-MM-DD from a datetime/ISO string, or 'unknown-date'."""
    if value is None:
        return "unknown-date"
    iso = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return iso[:10] or "unknown-date"


def _excerpt(value: Any, limit: int = _LINE_EXCERPT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


# ── Sub-fetchers (each guarded, each project-scoped) ─────────────────────────


async def _fetch_corrections(
    db: AsyncSession, project_id: uuid.UUID, fingerprint: str,
) -> list[dict[str, Any]]:
    try:
        from app.services.analysis_corrections import get_corrections_for_fingerprints

        corrections = await get_corrections_for_fingerprints(
            db, project_id, [fingerprint],
        )
        correction = corrections.get(fingerprint)
        return [correction] if correction else []
    except Exception as exc:
        logger.debug("recall corrections fetch failed (non-critical): %s", exc)
        return []


async def _fetch_prior_analyses(
    db: AsyncSession, project_id: uuid.UUID, fingerprint: str,
) -> list[dict[str, Any]]:
    try:
        from app.models.postgres import AIAnalysis, TestCase, TestRun

        rows = (
            await db.execute(
                select(
                    AIAnalysis.failure_category,
                    AIAnalysis.confidence_score,
                    AIAnalysis.root_cause_summary,
                    AIAnalysis.created_at,
                )
                .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                .where(
                    TestRun.project_id == project_id,
                    TestCase.test_fingerprint == fingerprint,
                )
                .order_by(AIAnalysis.created_at.desc())
                .limit(MAX_PRIOR_ANALYSES)
            )
        ).all()
        return [
            {
                "failure_category": str(getattr(r.failure_category, "value", r.failure_category) or "UNKNOWN"),
                "confidence_score": r.confidence_score,
                "root_cause_summary": r.root_cause_summary,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.debug("recall prior-analyses fetch failed (non-critical): %s", exc)
        return []


async def _fetch_similar(
    db: AsyncSession, project_id: uuid.UUID, signature: str, limit: int,
) -> list[dict[str, Any]]:
    if not signature.strip():
        return []
    try:
        from app.services.agent_memory_service import recall_similar

        matches = await recall_similar(
            db, project_id, signature, entity_type="analysis", limit=limit,
        )
        out: list[dict[str, Any]] = []
        for match in matches[:limit]:
            entry = match.get("memory")
            if entry is None:
                continue
            out.append({
                "similarity": match.get("similarity"),
                "failure_category": getattr(entry, "failure_category", None),
                "root_cause_summary": getattr(entry, "root_cause_summary", None),
                "error_signature": getattr(entry, "error_signature", None),
                "created_at": getattr(entry, "created_at", None),
                "entity_id": str(getattr(entry, "entity_id", "") or ""),
            })
        return out
    except Exception as exc:
        logger.debug("recall similar-failures fetch failed (non-critical): %s", exc)
        return []


async def _fetch_quarantine(
    db: AsyncSession, project_id: uuid.UUID, fingerprint: str,
) -> Optional[dict[str, Any]]:
    try:
        from app.models.postgres import FlakyQuarantineRequest

        row = (
            await db.execute(
                select(FlakyQuarantineRequest)
                .where(
                    FlakyQuarantineRequest.project_id == project_id,
                    FlakyQuarantineRequest.test_fingerprint == fingerprint,
                )
                .order_by(FlakyQuarantineRequest.detected_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "status": row.status,
            "flip_rate": row.flip_rate,
            "detected_at": row.detected_at,
            "pass_count": row.pass_count,
            "fail_count": row.fail_count,
        }
    except Exception as exc:
        logger.debug("recall quarantine fetch failed (non-critical): %s", exc)
        return None


# ── Public API ───────────────────────────────────────────────────────────────


async def recall_failure_history(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    test_fingerprint: Optional[str] = None,
    error_text: Optional[str] = None,
    test_name: Optional[str] = None,
    max_similar: int = MAX_SIMILAR_FAILURES,
) -> dict[str, Any]:
    """Gather prior knowledge about a failing test. Never raises.

    ``project_id`` scopes EVERY source — corrections, analyses, the ChromaDB
    similarity index, and quarantine history. Fingerprint-keyed sections are
    skipped when no fingerprint is known; semantic recall runs off
    ``error_text`` (falling back to ``test_name``).
    """
    fingerprint = (test_fingerprint or "").strip() or None
    signature = (error_text or "").strip() or (test_name or "").strip()

    corrections: list[dict[str, Any]] = []
    prior_analyses: list[dict[str, Any]] = []
    quarantine: Optional[dict[str, Any]] = None
    if fingerprint:
        corrections = await _fetch_corrections(db, project_id, fingerprint)
        prior_analyses = await _fetch_prior_analyses(db, project_id, fingerprint)
        quarantine = await _fetch_quarantine(db, project_id, fingerprint)
    similar = await _fetch_similar(
        db, project_id, signature, max(0, int(max_similar)),
    )

    return {
        "project_id": str(project_id),
        "test_fingerprint": fingerprint,
        "test_name": test_name,
        "has_history": bool(corrections or prior_analyses or similar or quarantine),
        "human_corrections": corrections,
        "prior_analyses": prior_analyses,
        "similar_failures": similar,
        "quarantine": quarantine,
    }


def format_recall_lines(recall: dict[str, Any]) -> list[str]:
    """Compact citation-style lines ("2026-06-14: human corrected to …")."""
    if not isinstance(recall, dict):
        return [NO_HISTORY_LINE]
    lines: list[str] = []

    for c in recall.get("human_corrections") or []:
        line = (
            f"{_date_of(c.get('corrected_at'))}: human corrected to "
            f"{c.get('corrected_category')}"
        )
        if c.get("corrected_root_cause"):
            line += f" — {_excerpt(c['corrected_root_cause'])}"
        line += " [HUMAN CORRECTION — authoritative]"
        lines.append(line)

    for a in recall.get("prior_analyses") or []:
        line = (
            f"{_date_of(a.get('created_at'))}: prior analysis → "
            f"{a.get('failure_category')} "
            f"(confidence {a.get('confidence_score') if a.get('confidence_score') is not None else '?'})"
        )
        if a.get("root_cause_summary"):
            line += f" — {_excerpt(a['root_cause_summary'])}"
        lines.append(line)

    for s in recall.get("similar_failures") or []:
        sim = s.get("similarity")
        sim_txt = f"{float(sim):.0%}" if isinstance(sim, (int, float)) else "?"
        line = (
            f"{_date_of(s.get('created_at'))}: similar past failure "
            f"({sim_txt} match) → {s.get('failure_category') or 'UNKNOWN'}"
        )
        detail = s.get("root_cause_summary") or s.get("error_signature")
        if detail:
            line += f" — {_excerpt(detail)}"
        lines.append(line)

    q = recall.get("quarantine")
    if q:
        flip = q.get("flip_rate")
        flip_txt = f", flip rate {float(flip):.0%}" if isinstance(flip, (int, float)) else ""
        lines.append(
            f"Quarantine history: {q.get('status')} since "
            f"{_date_of(q.get('detected_at'))}{flip_txt}."
        )

    return lines or [NO_HISTORY_LINE]


def render_recall_report(
    recall: dict[str, Any], *, max_chars: int = MAX_REPORT_CHARS,
) -> str:
    """One bounded text block for tool output / retrieved chat context."""
    header = "=== Memory recall"
    name = recall.get("test_name") if isinstance(recall, dict) else None
    if name:
        header += f": {_excerpt(name, 120)}"
    header += " ==="
    body = "\n".join(f"- {line}" for line in format_recall_lines(recall))
    report = f"{header}\n{body}"
    if len(report) > max_chars:
        report = report[: max_chars - 1] + "…"
    return report
