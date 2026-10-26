"""The candidate REPLAY runner (W16 AGT-09 b).

A candidate is a TIER (``rules`` / ``ml`` / ``llm``) under the deployment's
offline ceiling; its model and prompt version are ASSERTIONS about the
deployment, checked at submit and again when the run starts, never injected
(``llm_factory`` / ``model_router`` carry the eval attestation and are not
touched). Each dataset item is replayed through the REAL code path:

* classification / root cause -> ``analysis_router.classify_test(test_case,
  mode=<tier>)`` (never an engine directly: the ``backend.analysis-router``
  guard). The test case carries ONLY text (``test_name`` / ``error_message`` /
  ``stack_trace``) and NO project or run id: no analysis-cache key exists
  without a project, so the exact / semantic caches are neither read nor
  written, and the ReAct tools have no investigation context -- every one of
  them answers "unavailable" instead of project data. The LLM spend is still
  charged to the run's project (``cost_budget_scope``).
* duplicate detection -> ``defect_promotion_service.title_duplicate_ratio``
  (the detector's pure decision core; its ChromaDB upsert is never reached).
* release decision -> ``policy_evaluator_service.evaluate_policy`` with the
  project's policy as an OVERRIDE (level ``simulated``: no live
  ``release_decisions_total`` is counted, nothing is read).

Fail closed: a timeout, an exception, or an answer from a FALLBACK engine is an
``error`` item (never ``correct``); an item without an expected label is
``unscored``. Outputs are redacted (``sanitize_persistence_payload``) before
they are stored.

Stage-only (``STAGE_ONLY_SERVICES``): every function here flushes or executes
and returns; the Celery task in ``worker/tasks.py`` owns every commit, and no
session is held across a model call (``replay_item`` takes none).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import structlog
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIEvalDataset, AIEvalItemOutput, AIEvalRun, Project, ReleaseGatePolicy
from app.models.schemas import (
    CandidateOptionsResponse,
    CandidateTierOption,
    EvalCandidateIn,
    EvalCandidateResolved,
)
from app.services import ai_eval_scorers as scorers
from app.services.ai_eval_vocab import (
    CANDIDATE_TIERS,
    ITEM_ERROR_SENTENCES,
    ITEM_TIMEOUT_FLOOR_SECONDS,
    MAX_CANDIDATE_ITEMS,
    RUN_HEARTBEAT_STALE_SECONDS,
    TASK_TIERS,
    TIER_UNAVAILABLE,
    current_prompt_tag,
)
from app.services.step_llm_budget import StepLLMBudget

logger = structlog.get_logger("services.ai_eval_runner")

#: The agent whose project budget bounds an ``llm`` replay.
BUDGET_AGENT_ID = "agent.root_cause_analysis.v1"
ITEM_TIMEOUT_CAP_SECONDS = 600
#: A run-level failure the vocab names no narrower code for.
RUN_ERROR_CODE = "run_error"
#: The AI configuration no longer matches the candidate the run was queued with.
CANDIDATE_CHANGED_CODE = "candidate_changed"
#: The stub ``agent._fallback_analysis`` writes when no engine produced an answer.
_CANNED_FALLBACK_PREFIX = "AI analysis could not complete."
#: Only the text of an item is replayed: a dataset input that carries a
#: prediction (``failure_category`` / ``confidence_score`` / ``is_flaky``) or a
#: project's hints would make the replay score itself.
_TEXT_FIELDS = ("test_name", "error_message", "stack_trace")

#: The refusals of ``resolve_candidate`` (status 422), one sentence each (§3).
CANDIDATE_REFUSALS: dict[str, str] = {
    "candidate_tier_invalid": "tier must be rules, ml or llm.",
    "candidate_tier_unsupported": "{task} replays the {allowed} tier only.",
    "candidate_offline_blocked": (
        "Offline mode is on and {provider} is not a local provider: only rules, ML or a local "
        "model can be evaluated."
    ),
    "candidate_model_not_configured": (
        "Only the configured model ({model}) can be evaluated; configure {requested} under "
        "Settings › AI first."
    ),
    "candidate_prompt_not_current": "Only the current prompt version ({current}) can be replayed.",
}


class CandidateRefused(Exception):
    """A candidate the deployment cannot replay; ``code`` / ``message`` are the 422 body."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunSettled(Exception):
    """The run is no longer ``running`` (reconciled or deleted): stop, write nothing."""


@dataclass
class ItemOutcome:
    """What one replayed item produced (stored as an ``ai_eval_item_outputs`` row)."""

    status: str
    output: dict[str, Any] | None = None
    score: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_text: str | None = None
    latency_ms: int | None = None
    tokens: int | None = None
    mode_used: str | None = None


@dataclass(frozen=True)
class ReleasePolicyContext:
    """The project's release policy, resolved ONCE per run (a transient copy)."""

    policy: ReleaseGatePolicy | None
    level: str
    version: int | None = None


@dataclass
class RunPlan:
    """Everything the task needs to replay a claimed run, loaded in one session."""

    run_id: uuid.UUID
    task_type: str
    candidate: EvalCandidateResolved
    project_id: uuid.UUID
    items: list[tuple[int, Mapping[str, Any]]]
    stored: set[int]
    budget: StepLLMBudget
    timeout_s: float
    policy_context: ReleasePolicyContext | None = None
    ai_scope: Mapping[str, Any] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error(code: str, *, latency_ms: int | None = None, output: dict[str, Any] | None = None,
           mode_used: str | None = None, **params: Any) -> ItemOutcome:
    text: str | None
    try:
        text = ITEM_ERROR_SENTENCES[code].format(**params)
    except (KeyError, IndexError):
        text = ITEM_ERROR_SENTENCES.get(code)
    return ItemOutcome(
        status="error", error_code=code, error_text=text, latency_ms=latency_ms,
        output=output, mode_used=mode_used, score={"error_text": text} if text else {},
    )


# ── the candidate: what the deployment can replay ───────────────────────────


def _ml_unavailable() -> str | None:
    """The ``TIER_UNAVAILABLE`` sentence when no ML model is trained (a file read)."""
    try:
        from app.services.ml.classifier import MLClassifier  # noqa: PLC0415

        info = MLClassifier.get_model_info()
    except Exception:  # noqa: BLE001 — an unreadable model is no model
        return TIER_UNAVAILABLE["ml_not_trained"]
    if not isinstance(info, Mapping) or info.get("status") == "not_trained":
        return TIER_UNAVAILABLE["ml_not_trained"]
    return None


def _llm_endpoint(ai_config: Mapping[str, Any]) -> tuple[str, str, bool]:
    provider = str(ai_config.get("provider") or "").strip()
    model = str(ai_config.get("model") or "").strip()
    return provider, model, bool(ai_config.get("offline_mode"))


def _llm_offline_blocked(provider: str, offline: bool) -> bool:
    from app.services.llm_policy_service import LOCAL_PROVIDERS  # noqa: PLC0415

    return offline and provider.lower() not in LOCAL_PROVIDERS


def _llm_unavailable(ai_config: Mapping[str, Any]) -> str | None:
    provider, model, offline = _llm_endpoint(ai_config)
    if not provider or not model:
        return TIER_UNAVAILABLE["llm_not_configured"]
    if _llm_offline_blocked(provider, offline):
        return TIER_UNAVAILABLE["llm_offline_cloud"].format(provider=provider)
    return None


async def _effective_ai_config(db: AsyncSession) -> dict[str, Any]:
    from app.services.ai_config_resolver import get_effective_ai_config  # noqa: PLC0415

    return dict(await get_effective_ai_config(db=db))


def check_candidate(
    *, task_type: str, candidate: EvalCandidateIn, ai_config: Mapping[str, Any],
) -> EvalCandidateResolved:
    """Judge a candidate against an effective (ceilinged) AI config; pure.

    Raises :class:`CandidateRefused`. ``model`` / ``prompt_version`` are
    assertions about the ``llm`` tier only (rules and ML use no prompt and no
    model): a false assertion is refused, never injected.
    """
    tier = candidate.tier
    if tier not in CANDIDATE_TIERS:
        raise CandidateRefused("candidate_tier_invalid", CANDIDATE_REFUSALS["candidate_tier_invalid"])
    allowed = TASK_TIERS.get(task_type, ())
    if tier not in allowed:
        raise CandidateRefused(
            "candidate_tier_unsupported",
            CANDIDATE_REFUSALS["candidate_tier_unsupported"].format(
                task=task_type, allowed=" or ".join(allowed) or "no",
            ),
        )
    provider, model, offline = _llm_endpoint(ai_config)
    if tier == "ml":
        reason = _ml_unavailable()
        if reason is not None:
            raise CandidateRefused("candidate_tier_unavailable", reason)
    if tier != "llm":
        return EvalCandidateResolved(tier=tier, offline_mode=offline, requested=candidate)
    if not provider or not model:
        raise CandidateRefused("candidate_tier_unavailable", TIER_UNAVAILABLE["llm_not_configured"])
    if _llm_offline_blocked(provider, offline):
        raise CandidateRefused(
            "candidate_offline_blocked",
            CANDIDATE_REFUSALS["candidate_offline_blocked"].format(provider=provider),
        )
    if candidate.model is not None and candidate.model.strip() != model:
        raise CandidateRefused(
            "candidate_model_not_configured",
            CANDIDATE_REFUSALS["candidate_model_not_configured"].format(
                model=model, requested=candidate.model.strip(),
            ),
        )
    current = current_prompt_tag()
    if candidate.prompt_version is not None and candidate.prompt_version.strip() != current:
        raise CandidateRefused(
            "candidate_prompt_not_current",
            CANDIDATE_REFUSALS["candidate_prompt_not_current"].format(current=current),
        )
    return EvalCandidateResolved(
        tier=tier, model=model, provider=provider, prompt_version=current,
        offline_mode=offline, requested=candidate,
    )


async def resolve_candidate(
    db: AsyncSession, *, task_type: str, candidate: EvalCandidateIn, project_id: uuid.UUID | str | None,
) -> EvalCandidateResolved:
    """What a submitted candidate will ACTUALLY replay; raises :class:`CandidateRefused`.

    Runs on the CALLER's session (the POST route, in-request): the effective AI
    config is read through ``get_effective_ai_config(db=db)``. ``project_id``
    names the budget / policy the run will use; the worker resolves those.
    """
    del project_id  # the run's project is checked by the route; its budget is read by the worker
    return check_candidate(task_type=task_type, candidate=candidate, ai_config=await _effective_ai_config(db))


async def candidate_options(db: AsyncSession) -> CandidateOptionsResponse:
    """The ``GET /ai-eval/candidate-options`` answer; reads, writes nothing."""
    ai_config = await _effective_ai_config(db)
    provider, model, offline = _llm_endpoint(ai_config)
    ml_reason = _ml_unavailable()
    llm_reason = _llm_unavailable(ai_config)
    return CandidateOptionsResponse(
        tiers=[
            CandidateTierOption(tier="rules", available=True),
            CandidateTierOption(tier="ml", available=ml_reason is None, reason=ml_reason),
            CandidateTierOption(
                tier="llm", available=llm_reason is None, reason=llm_reason,
                model=model or None, provider=provider or None,
            ),
        ],
        prompt_version=current_prompt_tag(),
        offline_mode=offline,
        max_items=MAX_CANDIDATE_ITEMS,
        supported_tiers_by_task={task: list(tiers) for task, tiers in TASK_TIERS.items()},
    )


def item_timeout_seconds(ai_config: Mapping[str, Any]) -> float:
    """Per item: ``max(floor, effective AI timeout + 15)``, capped at 600 s."""
    try:
        configured = int(ai_config.get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        configured = 0
    return float(min(ITEM_TIMEOUT_CAP_SECONDS, max(ITEM_TIMEOUT_FLOOR_SECONDS, configured + 15)))


# ── one item through the real engine ────────────────────────────────────────


def _str_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def replay_test_case(run_id: Any, index: int, item_input: Mapping[str, Any],
                     metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """The test case a classification / root-cause item is replayed as.

    Text only, and no ``project_id`` / ``run_id``: no cache key, no
    investigation context, no project hint (the module docstring).
    """
    label = _str_or_none((metadata or {}).get("label"))
    return {
        "test_case_id": f"ai-eval:{run_id}:{index}",
        "test_name": (_str_or_none(item_input.get("test_name")) or label or f"ai-eval item {index}")[:500],
        "error_message": _str_or_none(item_input.get("error_message"))
        or _str_or_none(item_input.get("root_cause_summary")),
        "stack_trace": _str_or_none(item_input.get("stack_trace")),
    }


def _engine_fallback(raw: Mapping[str, Any], tier: str) -> str | None:
    """Who answered INSTEAD of ``tier``, or ``None`` when ``tier`` itself did.

    ``classify_test`` records an ML / LLM fallback in ``_routing.mode_used``.
    The ReAct triage's OWN fallbacks (model not installed -> rules; a crash or
    an unparseable answer -> a canned stub) are invisible there -- the router
    overwrites the triage's ``_routing`` -- so they are read off the answer.
    """
    routing = raw.get("_routing") if isinstance(raw.get("_routing"), Mapping) else {}
    used = cast(Mapping[str, Any], routing).get("mode_used")
    if used != tier:
        return str(used) if used else "the canned fallback"
    if tier == "llm":
        if raw.get("analysis_engine") == "rules" or raw.get("llm_unavailable_reason"):
            return "rules"
        summary = raw.get("root_cause_summary")
        if raw.get("schema_validated") is False or (
            isinstance(summary, str) and summary.startswith(_CANNED_FALLBACK_PREFIX)
        ):
            return "the canned fallback"
    return None


class _NoSession:
    """Stands in for a session where the code path must read nothing."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("a replay reads nothing through the session")


async def _classify(test_case: dict[str, Any], tier: str, project_id: Any,
                    ai_scope: Mapping[str, Any] | None) -> dict[str, Any]:
    from app.services import analysis_router  # noqa: PLC0415
    from app.services.llm_cost_reservation import cost_budget_scope  # noqa: PLC0415
    from app.services.pipeline_ai_scope import pipeline_ai_scope  # noqa: PLC0415

    with cost_budget_scope(project_id), pipeline_ai_scope(ai_scope):
        return await analysis_router.classify_test(test_case, mode=tier)


async def _duplicate(item_input: Mapping[str, Any]) -> dict[str, Any]:
    from app.services.defect_promotion_service import (  # noqa: PLC0415
        DUPLICATE_TITLE_RATIO,
        title_duplicate_ratio,
    )

    ratio = title_duplicate_ratio(str(item_input["title_a"]), str(item_input["title_b"]))
    return {"ratio": ratio, "predicted_duplicate": ratio > DUPLICATE_TITLE_RATIO, "detector_path": "title_ratio"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


async def _release(item_input: Mapping[str, Any], project_id: Any,
                   policy_context: ReleasePolicyContext | None) -> dict[str, Any]:
    from app.services.policy_evaluator_service import _extract_weights, evaluate_policy  # noqa: PLC0415

    context = policy_context or ReleasePolicyContext(policy=None, level="hardcoded")
    policy = context.policy or ReleaseGatePolicy(rules={})
    given = item_input.get("dim_scores")
    if isinstance(given, Mapping) and given:
        dim_scores = {str(k): float(v) for k, v in given.items() if _number(v) is not None}
    else:
        # The golden items carry the composite only: every dimension at it.
        risk = cast(float, _number(item_input.get("risk_score")))
        dim_scores = {dim: risk for dim in _extract_weights(context.policy)}
    facts = {k: item_input.get(k, 0) for k in
             ("flaky_count", "critical_failures", "regression_count", "failed_tests", "total_tests")}
    result = await evaluate_policy(
        project_id, dim_scores, cast(float, _number(item_input.get("pass_rate"))), facts,
        cast(Any, _NoSession()), policy_override=policy,
    )
    raw = result.to_dict()
    raw["policy_level"] = context.level
    raw["policy_version"] = context.version
    return raw


def _input_missing(task_type: str, item_input: Mapping[str, Any], test_case: Mapping[str, Any] | None) -> bool:
    if task_type in ("classification", "root_cause"):
        return test_case is None or not (test_case.get("error_message") or test_case.get("stack_trace"))
    if task_type == "duplicate_detection":
        return not (_str_or_none(item_input.get("title_a")) and _str_or_none(item_input.get("title_b")))
    if task_type == "release_decision":
        has_dims = isinstance(item_input.get("dim_scores"), Mapping) and bool(item_input.get("dim_scores"))
        return _number(item_input.get("pass_rate")) is None or (
            not has_dims and _number(item_input.get("risk_score")) is None
        )
    return True


async def replay_item(
    *,
    task_type: str,
    item: Mapping[str, Any],
    index: int,
    candidate: EvalCandidateResolved,
    project_id: uuid.UUID | str,
    policy_context: ReleasePolicyContext | None,
    budget: StepLLMBudget,
    timeout_s: float,
    run_id: uuid.UUID | str = "replay",
    ai_scope: Mapping[str, Any] | None = None,
) -> ItemOutcome:
    """Replay ONE dataset item through the real engine; no session is used.

    Every engine call runs under ``asyncio.wait_for(timeout_s)``: a timeout is
    ``error`` / ``item_timeout``, any other exception ``error`` /
    ``engine_error``, an answer from another engine ``error`` /
    ``tier_fallback`` -- never ``correct``. An ``llm`` item consumes one unit
    of ``budget`` BEFORE its call; an exhausted budget is ``error`` /
    ``budget_exhausted`` and nothing is called. ``asyncio.CancelledError``
    (the worker is going away) is not caught: the run stays ``running`` and the
    reconcile sweep settles it.
    """
    item_input = item.get("input") if isinstance(item.get("input"), Mapping) else {}
    item_input = cast(Mapping[str, Any], item_input)
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else None
    tier = candidate.tier
    test_case = (
        replay_test_case(run_id, index, item_input, cast(Mapping[str, Any] | None, metadata))
        if task_type in ("classification", "root_cause") else None
    )
    if _input_missing(task_type, item_input, test_case):
        return _error("input_missing")
    if tier == "llm" and not budget.consume(f"ai_eval_replay:{index}"):
        return _error("budget_exhausted")

    if test_case is not None:
        call = _classify(test_case, tier, project_id, ai_scope)
    elif task_type == "duplicate_detection":
        call = _duplicate(item_input)
    else:
        call = _release(item_input, project_id, policy_context)

    started = time.monotonic()
    try:
        raw = await asyncio.wait_for(call, timeout=timeout_s)
    except TimeoutError:
        return _error("item_timeout", latency_ms=_elapsed_ms(started), seconds=int(timeout_s))
    except Exception as exc:  # noqa: BLE001 — fail closed: the item is an error, the run goes on
        logger.warning("ai_eval_item_engine_error", run_id=str(run_id), index=index, error_type=type(exc).__name__)
        return _error("engine_error", latency_ms=_elapsed_ms(started), error_type=type(exc).__name__)
    latency = _elapsed_ms(started)
    output = scorers.project_output(task_type, raw)
    if test_case is not None:
        used = _engine_fallback(raw, tier)
        if used is not None:
            mode = output.get("mode_used") if output.get("mode_used") in CANDIDATE_TIERS else None
            return _error("tier_fallback", latency_ms=latency, output=output, mode_used=mode, tier=tier, used=used)
    expected = item.get("expected_output") if isinstance(item.get("expected_output"), Mapping) else {}
    status, score = scorers.score_item(
        task_type, expected=cast(Mapping[str, Any], expected), output=output, item_input=item_input,
    )
    return ItemOutcome(
        status=status, output=output, score=score, latency_ms=latency,
        mode_used=tier if test_case is not None else "rules",
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ── the run row: claim, plan, stage, settle (stage-only; the task commits) ──


def _run_id(run: Any) -> uuid.UUID:
    raw = getattr(run, "id", run)
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


def _safe(value: Any) -> Any:
    from app.services.evidence_sanitizer import sanitize_persistence_payload  # noqa: PLC0415

    return sanitize_persistence_payload(value)[0]


def item_key(item: Mapping[str, Any]) -> str | None:
    metadata = item.get("metadata")
    label = metadata.get("label") if isinstance(metadata, Mapping) else None
    return label[:160] if isinstance(label, str) and label else None


async def claim_run(db: AsyncSession, *, run_id: uuid.UUID, now: datetime) -> bool:
    """``queued -> running``; False when the run is not queued (a redelivery never replays twice)."""
    result = await db.execute(
        update(AIEvalRun)
        .where(AIEvalRun.id == run_id, AIEvalRun.status == "queued", AIEvalRun.metric_kind == "replay")
        .values(status="running", started_at=now, heartbeat_at=now)
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))


async def stage_item_output(
    db: AsyncSession, *, run: Any, index: int, item: Mapping[str, Any], outcome: ItemOutcome,
    now: datetime | None = None,
) -> bool:
    """Store one item (``INSERT … ON CONFLICT (eval_run_id, item_index) DO NOTHING``).

    Bumps the run's counters and heartbeat FIRST, conditionally on the run
    still being ``running``: a reconciled or deleted run raises
    :class:`RunSettled` and nothing is written. Returns whether a row was
    inserted (False = already stored). ``expected`` / ``output`` / ``score`` are
    redacted before they are stored.
    """
    rid = _run_id(run)
    status = outcome.status
    bumped = await db.execute(
        update(AIEvalRun)
        .where(AIEvalRun.id == rid, AIEvalRun.status == "running")
        .values(
            heartbeat_at=now or _now(),
            scored_items=AIEvalRun.scored_items + int(status in ("correct", "incorrect")),
            unscored_items=AIEvalRun.unscored_items + int(status == "unscored"),
            error_items=AIEvalRun.error_items + int(status == "error"),
            correct_items=AIEvalRun.correct_items + int(status == "correct"),
        )
        .returning(AIEvalRun.id)
        .execution_options(synchronize_session=False)
    )
    if bumped.scalar_one_or_none() is None:
        raise RunSettled(str(rid))
    expected = item.get("expected_output") if isinstance(item.get("expected_output"), Mapping) else {}
    inserted = await db.execute(
        pg_insert(AIEvalItemOutput)
        .values(
            id=uuid.uuid4(), eval_run_id=rid, item_index=index, item_key=item_key(item),
            status=status, error_code=outcome.error_code, expected=_safe(dict(cast(Mapping, expected))),
            output=_safe(outcome.output) if outcome.output is not None else None,
            score=_safe(outcome.score or {}), latency_ms=outcome.latency_ms, tokens=outcome.tokens,
            mode_used=outcome.mode_used,
        )
        .on_conflict_do_nothing(index_elements=["eval_run_id", "item_index"])
        .returning(AIEvalItemOutput.id)
    )
    return inserted.scalar_one_or_none() is not None


async def stage_budget_exhausted(
    db: AsyncSession, *, run: Any, items: Sequence[tuple[int, Mapping[str, Any]]], now: datetime | None = None,
) -> int:
    """Stage every remaining item as ``error`` / ``budget_exhausted`` (one session)."""
    staged = 0
    for index, item in items:
        staged += int(await stage_item_output(db, run=run, index=index, item=item,
                                              outcome=_error("budget_exhausted"), now=now))
    return staged


async def stored_indexes(db: AsyncSession, run_id: uuid.UUID) -> set[int]:
    rows = await db.execute(select(AIEvalItemOutput.item_index).where(AIEvalItemOutput.eval_run_id == run_id))
    return {int(i) for i in rows.scalars().all()}


async def _settle(
    db: AsyncSession, *, run_id: uuid.UUID, task_type: str, now: datetime,
    status: str | None, error_code: str | None, from_statuses: tuple[str, ...] = ("running",),
) -> str | None:
    """Aggregate the STORED rows (never memory) onto the run; ``None`` when it was not in ``from_statuses``.

    ``status=None`` decides it: ``partial`` when any item ran out of budget,
    else ``completed``. Writes the existing metric columns too, so the
    dashboard and the drift readers see a replay's accuracy / F1.
    """
    rows = (await db.execute(
        select(AIEvalItemOutput.status, AIEvalItemOutput.score, AIEvalItemOutput.error_code)
        .where(AIEvalItemOutput.eval_run_id == run_id)
    )).all()
    pairs = [(str(r[0]), r[1] if isinstance(r[1], Mapping) else {}) for r in rows]
    metrics = scorers.aggregate(task_type, pairs)
    if status is None:
        status = "partial" if any(r[2] == "budget_exhausted" for r in rows) else "completed"
        error_code = "budget_exhausted" if status == "partial" else None
    counts = {s: sum(1 for p in pairs if p[0] == s) for s in ("correct", "incorrect", "unscored", "error")}
    result = await db.execute(
        update(AIEvalRun)
        .where(AIEvalRun.id == run_id, AIEvalRun.status.in_(from_statuses))
        .values(
            status=status, error_code=error_code, completed_at=now, heartbeat_at=now,
            metrics=[m.model_dump() for m in metrics],
            accuracy=scorers.metric_value(metrics, "accuracy"),
            precision=scorers.metric_value(metrics, "precision"),
            recall=scorers.metric_value(metrics, "recall"),
            f1_score=scorers.metric_value(metrics, "f1_score"),
            correct_items=counts["correct"],
            scored_items=counts["correct"] + counts["incorrect"],
            unscored_items=counts["unscored"],
            error_items=counts["error"],
        )
        .returning(AIEvalRun.id)
        .execution_options(synchronize_session=False)
    )
    return status if result.scalar_one_or_none() is not None else None


async def finish_run(db: AsyncSession, *, run: Any, now: datetime, task_type: str | None = None) -> str | None:
    """Settle a run whose loop ended: ``completed`` or ``partial`` (budget); ``None`` if settled elsewhere."""
    rid = _run_id(run)
    kind = task_type or getattr(run, "task_type", None) or await _task_type(db, rid)
    return await _settle(db, run_id=rid, task_type=str(kind), now=now, status=None, error_code=None)


async def fail_run(db: AsyncSession, *, run: Any, now: datetime, error_code: str,
                   task_type: str | None = None) -> str | None:
    """``failed`` with ``error_code``; the items already stored are aggregated and kept."""
    rid = _run_id(run)
    kind = task_type or await _task_type(db, rid)
    if kind is None:
        return None
    return await _settle(db, run_id=rid, task_type=kind, now=now, status="failed",
                         error_code=error_code, from_statuses=("queued", "running"))


async def _task_type(db: AsyncSession, run_id: uuid.UUID) -> str | None:
    value = (await db.execute(select(AIEvalRun.task_type).where(AIEvalRun.id == run_id))).scalar_one_or_none()
    return str(value) if value is not None else None


async def reconcile_stale_runs(
    db: AsyncSession, *, now: datetime, stale_seconds: int = RUN_HEARTBEAT_STALE_SECONDS, limit: int = 100,
) -> int:
    """Settle replays whose worker went silent; NEVER re-dispatch them.

    ``running`` with no heartbeat for ``stale_seconds`` -> ``partial`` /
    ``worker_lost`` (the stored items aggregated, the rest counted as not run);
    ``queued`` older than that -> ``failed`` / ``worker_lost`` (the broker
    never delivered it). At most ``limit`` runs a call; returns how many.
    """
    cutoff = now - timedelta(seconds=stale_seconds)
    stale = (await db.execute(
        select(AIEvalRun.id, AIEvalRun.task_type, AIEvalRun.status)
        .where(
            AIEvalRun.metric_kind == "replay",
            or_(
                and_(AIEvalRun.status == "running",
                     or_(AIEvalRun.heartbeat_at < cutoff,
                         and_(AIEvalRun.heartbeat_at.is_(None), AIEvalRun.evaluated_at < cutoff))),
                and_(AIEvalRun.status == "queued", AIEvalRun.evaluated_at < cutoff),
            ),
        )
        .order_by(AIEvalRun.evaluated_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )).all()
    settled = 0
    for run_id, task_type, status in stale:
        if status == "running":
            done = await _settle(db, run_id=run_id, task_type=str(task_type), now=now,
                                 status="partial", error_code="worker_lost")
        else:
            done = await _settle(db, run_id=run_id, task_type=str(task_type), now=now,
                                 status="failed", error_code="worker_lost", from_statuses=("queued",))
        settled += int(done is not None)
    if settled:
        logger.info("ai_eval_runs_reconciled", settled=settled)
    return settled


async def load_release_policy_context(db: AsyncSession, project_id: uuid.UUID) -> ReleasePolicyContext:
    """The project's effective release policy, ONCE per run, as a transient copy."""
    from app.services.policy_evaluator_service import resolve_effective_policy  # noqa: PLC0415

    policy, level = await resolve_effective_policy(project_id, db)
    if policy is None:
        return ReleasePolicyContext(policy=None, level=level)
    copy = ReleaseGatePolicy(id=policy.id, version=policy.version, rules=deepcopy(policy.rules or {}))
    return ReleasePolicyContext(policy=copy, level=level, version=policy.version)


def _candidate_changed(candidate: EvalCandidateResolved, ai_config: Mapping[str, Any]) -> bool:
    """The config no longer replays what the run was queued with (an ``llm`` run only)."""
    if candidate.tier != "llm":
        return False
    provider, model, offline = _llm_endpoint(ai_config)
    return (
        provider != (candidate.provider or "")
        or model != (candidate.model or "")
        or _llm_offline_blocked(provider, offline)
        or (candidate.prompt_version or "") != current_prompt_tag()
    )


async def _llm_call_limit(db: AsyncSession, project_id: uuid.UUID, ai_config: Mapping[str, Any]) -> int:
    from app.services.agent_config_resolver import resolve_for_project  # noqa: PLC0415

    resolved = await resolve_for_project(db, project_id, BUDGET_AGENT_ID, global_ai_config=ai_config)
    return int(resolved.config.budget.max_llm_calls_per_run)


async def load_plan(db: AsyncSession, *, run_id: uuid.UUID) -> RunPlan | str:
    """Everything a CLAIMED run replays, read in one session; or a run error code.

    ``dataset_missing`` / ``project_missing`` / ``candidate_changed`` (the AI
    configuration no longer matches an ``llm`` candidate: the run would replay
    a model it does not name) / ``run_error`` (an unreadable candidate).
    """
    from app.services.pipeline_ai_scope import build_ai_config_snapshot  # noqa: PLC0415

    run = (await db.execute(select(AIEvalRun).where(AIEvalRun.id == run_id))).scalar_one_or_none()
    if run is None:
        return RUN_ERROR_CODE
    dataset = (await db.execute(
        select(AIEvalDataset).where(AIEvalDataset.id == run.dataset_id)
    )).scalar_one_or_none()
    if dataset is None or not dataset.is_active:
        return "dataset_missing"
    project_exists = run.project_id is not None and (await db.execute(
        select(Project.id).where(Project.id == run.project_id)
    )).scalar_one_or_none() is not None
    if not project_exists or run.project_id is None:
        return "project_missing"
    try:
        candidate = EvalCandidateResolved.model_validate(run.candidate or {})
    except ValueError:
        return RUN_ERROR_CODE
    ai_config = await _effective_ai_config(db)
    if _candidate_changed(candidate, ai_config):
        return CANDIDATE_CHANGED_CODE
    items = [(i, it) for i, it in enumerate(list(dataset.items or [])) if isinstance(it, Mapping)]
    items = items[: max(0, int(run.total_items or 0))]
    limit = len(items)
    if candidate.tier == "llm":
        limit = min(limit, MAX_CANDIDATE_ITEMS, await _llm_call_limit(db, run.project_id, ai_config))
    policy_context = (
        await load_release_policy_context(db, run.project_id) if run.task_type == "release_decision" else None
    )
    return RunPlan(
        run_id=run.id, task_type=str(run.task_type), candidate=candidate, project_id=run.project_id,
        items=items, stored=await stored_indexes(db, run.id), budget=StepLLMBudget(limit=max(0, limit)),
        timeout_s=item_timeout_seconds(ai_config), policy_context=policy_context,
        ai_scope=cast(Mapping[str, Any], build_ai_config_snapshot(ai_config)),
    )


__all__ = [
    "CANDIDATE_REFUSALS",
    "CandidateRefused",
    "ItemOutcome",
    "ReleasePolicyContext",
    "RunPlan",
    "RunSettled",
    "candidate_options",
    "check_candidate",
    "claim_run",
    "fail_run",
    "finish_run",
    "item_timeout_seconds",
    "load_plan",
    "load_release_policy_context",
    "reconcile_stale_runs",
    "replay_item",
    "replay_test_case",
    "resolve_candidate",
    "stage_budget_exhausted",
    "stage_item_output",
]
