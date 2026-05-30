"""
RAG faithfulness guardrails — Tier 2 item 9.

Gates RAG-generated test cases against a faithfulness score so the
auto-accept path in ``rag_review_service`` refuses to ship cases the
evaluator isn't confident were actually grounded in their citations.
Failing cases land on the "Needs review" queue with a human-readable
reason surfaced in the UI.

Design
------

The evaluator is **pluggable** via the ``rag_faithfulness_evaluator``
app setting. Two backends ship out of the box:

* ``"ollama"`` (default, offline-safe) — Uses the already-configured
  Ollama LLM to answer a single yes/no question about each generated
  case: "do the citations entailed-or-support the content?" The
  answer is parsed into a 0.0-1.0 score. Works in air-gapped mode.

* ``"ragas"`` (cloud) — Delegates to the Ragas library's
  ``faithfulness`` metric which calls a hosted LLM. Only used when
  ``AI_OFFLINE_MODE=false`` — the service degrades to ``ollama`` when
  offline mode is on, regardless of the setting.

Both backends return a ``(score, reason)`` tuple so the caller can
surface a human-readable explanation when the gate fires.

The entire subsystem is gated behind the ``rag_faithfulness_gate``
feature flag. When off, ``evaluate`` is a no-op and ``gate_accept``
lets every case through so existing deployments see zero change.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import ManagedTestCase

logger = structlog.get_logger("services.rag_faithfulness")


# Default threshold — a case needs at least this score to be eligible
# for auto-accept. The router exposes an override per review action for
# QA leads who want to relax the gate on a case-by-case basis.
DEFAULT_THRESHOLD = 0.70


# ── Feature flag gate ─────────────────────────────────────────────────────


async def _feature_enabled(db: Optional[AsyncSession] = None) -> bool:
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled("rag_faithfulness_gate", db=db)
    except Exception as exc:
        logger.debug("rag_faithfulness_gate check failed", error=str(exc))
        return False


# ── Evaluator dispatch ────────────────────────────────────────────────────


async def _resolve_backend() -> str:
    """Return the name of the evaluator backend to use for this request.

    Offline-mode customers are always routed to ``ollama`` so an
    operator enabling the Ragas backend in a secondary workspace
    can't accidentally egress traffic from an air-gapped
    deployment.
    """
    if settings.AI_OFFLINE_MODE:
        return "ollama"
    # Let the AI config resolver pick — it already reads DB → secrets →
    # env in the right order for the LLM provider. The evaluator
    # backend is an extension of that same config namespace.
    try:
        from app.services.ai_config_resolver import resolve_ai_config
        cfg = await resolve_ai_config()
        backend = (cfg.get("rag_faithfulness_evaluator") or "ollama").lower()
        if backend in ("ollama", "ragas"):
            return backend
    except Exception:
        pass
    return "ollama"


async def _evaluate_via_ollama(
    generated_content: str,
    citations: list[str],
) -> tuple[float, str]:
    """Ask Ollama a faithfulness yes/no and convert the answer into a score.

    Prompt is intentionally short to keep token cost bounded — we're
    not producing a full LLM-as-judge rubric here, just a cheap
    "does the citation support the claim" check that works for the
    vast majority of generated cases.
    """
    if not citations:
        return (0.0, "no citations attached to the generated case")
    joined_citations = "\n\n---\n\n".join(citations[:5])
    prompt = (
        "You are evaluating whether a generated test case is grounded in "
        "the source material it cites.\n\n"
        f"CITATIONS:\n{joined_citations}\n\n"
        f"GENERATED TEST CASE:\n{generated_content}\n\n"
        "Question: Is every claim in the generated test case supported "
        "by the citations above? Answer with a JSON object containing "
        "``score`` (float 0.0-1.0 where 1.0 = fully supported) and "
        "``reason`` (one-line explanation). Example: "
        '{"score": 0.75, "reason": "two of three assertions cite the source"}'
    )

    try:
        from app.services.llm_factory import get_llm
        llm = await get_llm()
        response = await llm.ainvoke(prompt)
        text = getattr(response, "content", None) or str(response)
    except Exception as exc:
        logger.warning("ollama faithfulness call failed", error=str(exc))
        return (0.0, f"evaluator error: {exc}")

    return _parse_score(text)


def _parse_score(raw: str) -> tuple[float, str]:
    """Robust score extraction — handles a well-formed JSON object OR
    a naked score like ``0.8`` OR a plain-English answer with a number.

    Returns ``(score, reason)``. The reason is lifted from the JSON
    when available, otherwise a generic "parsed from model output"
    message is used.
    """
    # First try to pull a JSON object from the text — LLMs often wrap
    # their response in preamble/postscript.
    match = re.search(r"\{[^{}]*\}", raw, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            score = float(parsed.get("score", 0.0))
            reason = str(parsed.get("reason") or "parsed from JSON response")
            return (max(0.0, min(1.0, score)), reason)
        except (ValueError, TypeError):
            pass

    # Fallback: find the first floating-point number in the text.
    num_match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if num_match:
        try:
            value = float(num_match.group(1))
            # Sometimes the model writes "80" meaning 80% — normalize
            # anything > 1.0 by assuming it's a percentage.
            if value > 1.0:
                value = value / 100.0
            return (
                max(0.0, min(1.0, value)),
                "parsed from model output (fallback numeric extraction)",
            )
        except ValueError:
            pass

    return (0.0, "could not parse evaluator output")


async def _evaluate_via_ragas(
    generated_content: str,
    citations: list[str],
) -> tuple[float, str]:
    """Ragas hosted evaluator. Imported lazily so the ragas dependency
    remains optional for air-gapped customers."""
    if not citations:
        return (0.0, "no citations attached to the generated case")
    try:
        from ragas.metrics import faithfulness  # type: ignore[import]
        from ragas import evaluate as ragas_evaluate  # type: ignore[import]
        from datasets import Dataset  # type: ignore[import]

        ds = Dataset.from_dict({
            "question": ["Is the generated test case grounded in the citations?"],
            "answer": [generated_content],
            "contexts": [citations],
        })
        result = ragas_evaluate(ds, metrics=[faithfulness])
        score = float(result.get("faithfulness", 0.0))
        return (
            max(0.0, min(1.0, score)),
            f"ragas faithfulness metric (sample size {len(citations)} citations)",
        )
    except ImportError:
        logger.debug("ragas not installed — falling back to ollama")
        return await _evaluate_via_ollama(generated_content, citations)
    except Exception as exc:
        logger.warning("ragas evaluator failed", error=str(exc))
        return (0.0, f"ragas error: {exc}")


# ── Public API ────────────────────────────────────────────────────────────


async def evaluate(
    generated_content: str,
    citations: list[str],
    *,
    db: Optional[AsyncSession] = None,
) -> Optional[dict[str, Any]]:
    """Score a single generated case for faithfulness.

    Returns ``None`` when the feature flag is off (no-op). Otherwise
    returns a dict with ``score``, ``evaluator``, ``reason`` so the
    caller can persist it on the ``managed_test_cases`` row.
    """
    if not await _feature_enabled(db):
        return None
    backend = await _resolve_backend()
    try:
        if backend == "ragas":
            score, reason = await _evaluate_via_ragas(generated_content, citations)
        else:
            score, reason = await _evaluate_via_ollama(generated_content, citations)
    except Exception as exc:
        logger.warning("faithfulness evaluator crashed", error=str(exc))
        return {
            "score": 0.0,
            "evaluator": backend,
            "reason": f"evaluator crashed: {exc}",
        }
    return {"score": score, "evaluator": backend, "reason": reason}


async def gate_accept(
    case_id: uuid.UUID,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Called by the RAG review workflow before accepting a case.

    Returns ``{"allow": True}`` when the flag is off, when the case
    was never evaluated, or when the score meets the threshold.
    Returns ``{"allow": False, "reason": "..."}`` when the score is
    under threshold so the caller can leave the case in ``needs_review``
    state with the reason attached.
    """
    if not await _feature_enabled():
        return {"allow": True, "reason": "faithfulness gate disabled"}

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ManagedTestCase).where(ManagedTestCase.id == case_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return {"allow": True, "reason": "case not found (letting caller 404)"}

        if row.faithfulness_score is None:
            # Case was never evaluated — the flag was toggled on after
            # the case was generated. Let it through but log; a
            # background task can backfill evaluations later.
            return {
                "allow": True,
                "reason": "case predates faithfulness evaluator",
            }

        score = float(row.faithfulness_score)
        if score >= threshold:
            return {"allow": True, "reason": f"score {score:.2f} >= threshold {threshold:.2f}"}

        # Blocked — record the reason on the row so the UI surfaces it.
        row.needs_review_reason = (
            f"Faithfulness score {score:.2f} below threshold {threshold:.2f} "
            f"(evaluator: {row.faithfulness_evaluator or 'unknown'})"
        )
        await db.commit()
        return {
            "allow": False,
            "reason": row.needs_review_reason,
            "score": score,
            "threshold": threshold,
        }


async def persist_evaluation(
    case_id: uuid.UUID,
    evaluation: dict[str, Any],
) -> None:
    """Write the evaluation result onto the ``managed_test_cases`` row.

    Best-effort — never raises. Called from
    ``rag_generation_service`` immediately after a new batch of cases
    is persisted so subsequent accept decisions can consult the score.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ManagedTestCase).where(ManagedTestCase.id == case_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.faithfulness_score = float(evaluation.get("score") or 0.0)
        row.faithfulness_evaluator = str(evaluation.get("evaluator") or "unknown")[:30]
        row.faithfulness_evaluated_at = datetime.now(timezone.utc)
        if row.faithfulness_score < DEFAULT_THRESHOLD:
            row.needs_review_reason = (
                f"Faithfulness {row.faithfulness_score:.2f} "
                f"< threshold {DEFAULT_THRESHOLD:.2f}: "
                f"{evaluation.get('reason') or ''}"
            )[:500]
        await db.commit()


async def list_needs_review(
    db: AsyncSession,
    project_id: uuid.UUID,
    limit: int = 100,
) -> list[ManagedTestCase]:
    """Return cases in the needs-review queue ordered by lowest score first."""
    result = await db.execute(
        select(ManagedTestCase)
        .where(
            ManagedTestCase.project_id == project_id,
            ManagedTestCase.needs_review_reason.is_not(None),
        )
        .order_by(ManagedTestCase.faithfulness_score.asc().nulls_last())
        .limit(min(max(1, limit), 500))
    )
    return list(result.scalars().all())
