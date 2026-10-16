"""
RAG faithfulness guardrails — Tier 2 item 9.

Gates RAG-generated test cases against a faithfulness score so the
auto-accept path in ``rag_review_service`` refuses to ship cases the
evaluator isn't confident were actually grounded in their citations.
Failing cases land on the "Needs review" queue with a human-readable
reason surfaced in the UI.

Design
------

The evaluator is **pluggable** via the ``rag_faithfulness_evaluator`` key
of the ``ai_config`` app setting, read through ``get_effective_ai_config``.
Two backends ship out of the box:

* ``"ollama"`` (default, offline-safe) — Uses the already-configured
  Ollama LLM to answer a single yes/no question about each generated
  case: "do the citations entailed-or-support the content?" The
  answer is parsed into a 0.0-1.0 score. Works in air-gapped mode.

* ``"ragas"`` (cloud) — Delegates to the Ragas library's
  ``faithfulness`` metric, judged by the same configured LLM (``get_llm``),
  inside the same provider policy and cost cap. Only used when the
  effective offline mode is off — the service degrades to ``ollama`` when
  ``AI_OFFLINE_MODE`` or the stored override turns it on, regardless of
  the setting, and when the ``ragas`` package is not installed (it is in
  requirements-ragas.txt, which the default image does not install).
  ``effective_evaluator`` decides; the AI settings page reports its answer.

Both backends return a ``(score, reason)`` tuple so the caller can
surface a human-readable explanation when the gate fires.

The entire subsystem is gated behind the ``rag_faithfulness_gate``
feature flag. When off, ``evaluate`` is a no-op and ``gate_accept``
lets every case through so existing deployments see zero change.
"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import ManagedTestCase, User

logger = structlog.get_logger("services.rag_faithfulness")


# Default threshold — a case needs at least this score to be eligible
# for auto-accept. The router exposes an override per review action for
# QA leads who want to relax the gate on a case-by-case basis.
DEFAULT_THRESHOLD = 0.70


# ── Feature flag gate ─────────────────────────────────────────────────────


async def _feature_enabled(
    db: Optional[AsyncSession] = None,
    *,
    project_id: Any = None,
    user: Optional[User] = None,
    any_scope: bool = False,
) -> bool:
    """``rag_faithfulness_gate`` for the case's project and the person acting.

    Scoring happens at generation and the gate at accept, both for one
    project's case by one person. Asked without them, a project or role
    allow-list on the flag turned the gate off everywhere.
    """
    try:
        from app.services.feature_flags import is_enabled
        return await is_enabled(
            "rag_faithfulness_gate", db=db, project_id=project_id, user=user,
            any_scope=any_scope,
        )
    except Exception as exc:
        logger.debug("rag_faithfulness_gate check failed", error=str(exc))
        return False


# ── Evaluator dispatch ────────────────────────────────────────────────────

EVALUATOR_STRATEGIES = ("ollama", "ragas")

# Why the strategy that runs is what it is (``effective_evaluator``).
EVALUATOR_REASON_CONFIGURED = "configured"
EVALUATOR_REASON_OFFLINE = "offline_mode"
EVALUATOR_REASON_RAGAS_MISSING = "ragas_not_installed"


def normalize_evaluator(value: Any) -> str:
    """A stored strategy, or ``"ollama"`` for a missing or unknown one."""
    choice = str(value or "").strip().lower()
    return choice if choice in EVALUATOR_STRATEGIES else "ollama"


def ragas_installed() -> bool:
    """Ragas and its ``datasets`` dependency are importable.

    Neither is in requirements.txt: an image runs the Ragas strategy only when
    built with ``INSTALL_RAGAS=1`` (requirements-ragas.txt). Checked without
    importing either package.
    """
    from importlib.util import find_spec

    return find_spec("ragas") is not None and find_spec("datasets") is not None


def effective_evaluator(configured: Any, *, offline: bool) -> tuple[str, str]:
    """The strategy the gate runs for a stored choice, and why.

    The gate and the AI settings page both call this, so the page cannot
    report Ragas while the gate runs the single-prompt judge.
    """
    if normalize_evaluator(configured) != "ragas":
        return ("ollama", EVALUATOR_REASON_CONFIGURED)
    if offline:
        # The judge is the configured LLM, but Ragas can open connections of
        # its own (usage telemetry, URLs in multi-modal contexts). Offline mode
        # keeps third-party egress paths off.
        return ("ollama", EVALUATOR_REASON_OFFLINE)
    if not ragas_installed():
        return ("ollama", EVALUATOR_REASON_RAGAS_MISSING)
    return ("ragas", EVALUATOR_REASON_CONFIGURED)


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
        from app.services.ai_config_resolver import get_effective_ai_config
        cfg = await get_effective_ai_config()
        # The effective flag, not just the env var: a stored override can make
        # the deployment offline too, and offline mode keeps Ragas off.
        backend, _reason = effective_evaluator(
            cfg.get("rag_faithfulness_evaluator"),
            offline=bool(cfg.get("offline_mode")),
        )
        return backend
    except Exception as exc:
        logger.debug("evaluator backend resolve failed", error=str(exc))
    return "ollama"


async def _active_llm_provider() -> str:
    """The provider that will actually judge — for the stored label.

    The ``ollama`` strategy name describes a code path, not a vendor: it calls
    ``get_llm()``, which resolves to whatever provider the deployment has
    configured. On a deployment running OpenRouter, a case judged by
    mistral-nemo was being recorded, and shown to the user, as judged by
    "ollama" (homelab, 2026-08-16). Provenance on a gating decision has to name
    the thing that made it — "which model refused my test case" is the first
    question an operator asks.

    Kept to the column width (String(30)); the model id goes in the reason.
    """
    provider = ""
    try:
        from app.services.ai_config_resolver import get_effective_ai_config  # noqa: PLC0415
        cfg = await get_effective_ai_config()
        provider = str(cfg.get("provider") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("provider resolve failed for evaluator label", error=str(exc))
    provider = (provider or str(getattr(settings, "LLM_PROVIDER", "") or "")).strip().lower()
    return (provider or "unknown")[:30]


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


class RagasUnavailable(RuntimeError):
    """The ``ragas`` package could not be imported.

    ``ragas_installed`` only finds the package; an install with a missing or
    incompatible dependency fails here, at import.
    """


async def _evaluate_via_ragas(
    generated_content: str,
    citations: list[str],
) -> tuple[float, str]:
    """Score with Ragas's faithfulness metric, judged by the configured LLM.

    Ragas is handed ``get_llm()``'s model, so its calls take the path every
    other AI call takes: the offline ceiling and the provider allowlist while
    the model is built, then per call the reservation against the project's
    cost cap (``cost_budget_scope`` in ``evaluate``), the circuit breaker and a
    cluster slot. Given no model, Ragas judged with its own default (OpenAI,
    from ``OPENAI_API_KEY``), outside all of them.

    The metric is scored with its async per-sample API, in this task. The
    synchronous ``ragas.evaluate`` blocked the event loop and, from inside a
    running one, patched it with nest_asyncio.

    Requires ragas 0.2 or later (``requirements-ragas.txt``). Raises
    :class:`RagasUnavailable` when it cannot be imported.
    """
    if not citations:
        return (0.0, "no citations attached to the generated case")
    # Ragas posts usage events to its vendor unless this is set. Read once,
    # on its first event, so it is set before the import.
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    try:
        from ragas.dataset_schema import SingleTurnSample
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import Faithfulness
        from ragas.run_config import RunConfig
    except ImportError as exc:
        raise RagasUnavailable(str(exc)) from exc

    try:
        from app.services.llm_factory import get_llm

        llm: Any = await get_llm()
        # One attempt per call: BudgetedLLM and the provider SDK already retry
        # what is safe to retry, and Ragas's default (10 attempts, any
        # exception) would retry a cost-cap refusal too.
        judge = LangchainLLMWrapper(llm.as_chat_model(), run_config=RunConfig(max_retries=1))
        sample = SingleTurnSample(
            user_input="Is the generated test case grounded in the citations?",
            response=generated_content,
            retrieved_contexts=list(citations),
        )
        score = float(await Faithfulness(llm=judge).single_turn_ascore(sample))
        if math.isnan(score):
            # ragas records a failed judgement as NaN, which the clamp below
            # would turn into 1.0 — a pass.
            return (0.0, "ragas returned no faithfulness score")
        return (
            max(0.0, min(1.0, score)),
            f"ragas faithfulness metric (sample size {len(citations)} citations)",
        )
    except Exception as exc:
        logger.warning("ragas evaluator failed", error=str(exc))
        return (0.0, f"ragas error: {exc}")


# ── Public API ────────────────────────────────────────────────────────────


async def evaluate(
    generated_content: str,
    citations: list[str],
    *,
    db: Optional[AsyncSession] = None,
    project_id: Any = None,
    user: Optional[User] = None,
) -> Optional[dict[str, Any]]:
    """Score a single generated case for faithfulness.

    ``project_id``: every judge call reserves against that project's monthly
    LLM cap (re-audit R-B45-1). Both strategies judge with ``get_llm()``'s
    model; Ragas makes several calls per case.

    Returns ``None`` when the feature flag is off (no-op). Otherwise
    returns a dict with ``score``, ``evaluator``, ``reason`` so the
    caller can persist it on the ``managed_test_cases`` row.
    """
    if not await _feature_enabled(db, project_id=project_id, user=user):
        return None

    # Redact before any LLM sees the content — the privacy invariant requires
    # scrubbing at every LLM boundary, matching rag_generation's redact_prompt
    # (RAG-13). The evaluator backend may be hosted (Ragas, or get_llm() with a
    # cloud provider when AI_OFFLINE_MODE=false), so the generated case + its
    # citations must not egress unredacted. Redaction is applied consistently
    # to both sides, so faithfulness of non-PII claims is still evaluable.
    from app.services.rag_redaction_service import redact_prompt
    safe_content = redact_prompt(generated_content or "")[0]
    safe_citations = [redact_prompt(c or "")[0] for c in (citations or [])]

    # No citations means no model is consulted at all — the score is a rule,
    # not a judgement. Decided here rather than inside a backend so the stored
    # label cannot claim a model rejected something it never saw.
    if not safe_citations:
        return {
            "score": 0.0,
            "evaluator": "no-citations",
            "reason": "no citations attached to the generated case",
        }

    backend = await _resolve_backend()
    # `backend` is the STRATEGY (ollama-style single-prompt, or ragas). The
    # label records what actually judged: the ragas strategy is its own thing,
    # the other one is whatever provider get_llm() resolves to.
    evaluator = "ragas" if backend == "ragas" else await _active_llm_provider()
    try:
        from app.services.llm_cost_reservation import cost_budget_scope

        with cost_budget_scope(project_id):
            if backend == "ragas":
                try:
                    score, reason = await _evaluate_via_ragas(safe_content, safe_citations)
                except RagasUnavailable as exc:
                    # Found but not importable. The single-prompt judge runs,
                    # and the label names the provider that judged.
                    logger.warning("ragas import failed; single-prompt judge used", error=str(exc))
                    evaluator = await _active_llm_provider()
                    score, reason = await _evaluate_via_ollama(safe_content, safe_citations)
            else:
                score, reason = await _evaluate_via_ollama(safe_content, safe_citations)
    except Exception as exc:
        logger.warning("faithfulness evaluator crashed", error=str(exc))
        return {
            "score": 0.0,
            "evaluator": evaluator,
            "reason": f"evaluator crashed: {exc}",
        }
    return {"score": score, "evaluator": evaluator, "reason": reason}


def apply_evaluation(case: ManagedTestCase, evaluation: dict[str, Any]) -> None:
    """Stage an evaluation onto an already-loaded case. Owns no transaction.

    This is the in-request form. ``persist_evaluation`` below is the
    background form that opens its own session and delegates here, so the
    threshold/reason wording lives in exactly one place.

    Separate entry points are necessary, not tidiness: a service that opens
    its own session cannot be called from inside a request that has only
    ``flush()``ed the row — the second session would not see it and would
    silently do nothing. That is how a "wired up" gate can score zero cases
    and still look healthy.
    """
    case.faithfulness_score = float(evaluation.get("score") or 0.0)
    case.faithfulness_evaluator = str(evaluation.get("evaluator") or "unknown")[:30]
    case.faithfulness_evaluated_at = datetime.now(timezone.utc)
    if case.faithfulness_score < DEFAULT_THRESHOLD:
        case.needs_review_reason = (
            f"Faithfulness {case.faithfulness_score:.2f} "
            f"< threshold {DEFAULT_THRESHOLD:.2f}: "
            f"{evaluation.get('reason') or ''}"
        )[:500]


async def check_accept(
    case: ManagedTestCase,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    db: Optional[AsyncSession] = None,
    user: Optional[User] = None,
) -> dict[str, Any]:
    """Decide whether an already-loaded case may be accepted. Stages only.

    In-request counterpart to :func:`gate_accept`. Mutates
    ``needs_review_reason`` on the passed row when it blocks, and leaves the
    commit to the router handler that owns the transaction. The flag is asked
    for the case's project and the ``user`` accepting it.
    """
    if not await _feature_enabled(db, project_id=case.project_id, user=user):
        return {"allow": True, "reason": "faithfulness gate disabled"}

    if case.faithfulness_score is None:
        # Generated before the flag was turned on. Let it through and say so
        # rather than blocking work on evidence that was never collected.
        return {"allow": True, "reason": "case predates faithfulness evaluator"}

    score = float(case.faithfulness_score)
    if score >= threshold:
        return {"allow": True, "reason": f"score {score:.2f} >= threshold {threshold:.2f}"}

    case.needs_review_reason = (
        f"Faithfulness score {score:.2f} below threshold {threshold:.2f} "
        f"(evaluator: {case.faithfulness_evaluator or 'unknown'})"
    )[:500]
    return {
        "allow": False,
        "reason": case.needs_review_reason,
        "score": score,
        "threshold": threshold,
    }


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
    # Off for everyone: skip the session. ``check_accept`` asks again for the
    # case's own project once the row is loaded.
    if not await _feature_enabled(any_scope=True):
        return {"allow": True, "reason": "faithfulness gate disabled"}

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ManagedTestCase).where(ManagedTestCase.id == case_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return {"allow": True, "reason": "case not found (letting caller 404)"}

        decision = await check_accept(row, threshold=threshold, db=db)
        if not decision.get("allow"):
            # This entry point owns its session, so it commits the staged
            # reason. The in-request path leaves that to the router.
            await db.commit()
        return decision


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
        apply_evaluation(row, evaluation)
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
