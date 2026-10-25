"""Shadow-tier pairs: the labelling queue, labels, golden match and expiry (W16 AGT-13).

A shadow pair is two live outputs for one subject (``root_cause:<test_case_id>`` / ``summary:<test_run_id>``)
written by ``tier_comparison_service.store_shadow_pair`` while an agent runs in shadow mode. This module is the
read side and the bookkeeping:

- ``list_pairs`` — the project's queue page with ``total`` (``count(*) OVER ()``), the project's
  ``pending_total`` and ``live_tier_pairs_seen``, each pair's source resolved INSIDE the project (a test case of
  another project, or a purged one, is ``source: None`` — never a lookup by id alone), the outputs re-sanitized
  on READ (they are stored sanitized too). It writes nothing.
- ``label_pair`` — a person's verdict; final (a conditional UPDATE: a second label of the same pair is refused).
- ``golden_match_batch`` — a pending root-cause pair whose test case carries a usable human ``AIFeedback`` is
  labelled from that feedback (summaries are never auto-labelled: a person decides narrative quality).
- ``expire_unlabelled_batch`` — pending pairs older than ``SHADOW_PAIR_RETENTION_DAYS`` are deleted; labelled
  pairs are G2 evidence and are kept (they go with their project's CASCADE).
- ``labelled_pairs_for`` — the labelled pairs of a tier comparison (``compare_tier_outputs(labelled_pairs=)``).

Stage-only (``STAGE_ONLY_SERVICES``): every function flushes at most; routers and workers commit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import String, and_, case, cast, delete, exists, func, literal, null, or_, select, true, union_all, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIAnalysis, AIEvalShadowPair, AIFeedback, FailureCategory, TestCase, TestRun, User
from app.models.schemas import ShadowPairLabelRequest, ShadowPairListResponse, ShadowPairRead, ShadowPairSource
from app.services.ai_eval_vocab import (
    GOLDEN_MATCH_BATCH,
    SHADOW_AGENT_LABELS,
    SHADOW_LABEL_FIELDS,
    SHADOW_PAIR_RETENTION_DAYS,
    SHADOW_SWEEP_BATCH,
    SHADOW_VERDICTS,
)
from app.services.evidence_sanitizer import sanitize_persistence_payload
from app.services.privacy_service import sanitize_for_llm
from app.services.tier_comparison_service import LabelledPair

#: The two subject kinds a producer writes (``analysis_router`` / ``summary_agent``).
_SAMPLE_KINDS = {"root_cause": "test_case", "summary": "test_run"}
#: A canonical UUID after ``root_cause:`` — the only sample keys the golden-match statement casts.
_ROOT_CAUSE_KEY_RE = r"^root_cause:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_ROOT_CAUSE_PREFIX_LEN = len("root_cause:")
_FAILURE_CATEGORIES = tuple(category.value for category in FailureCategory)
_LABELLED_STATUSES = ("human_labelled", "golden_match")
MAX_LIVE_LABELLED_PAIRS = 1000
_NOTES_MAX = 500
_SOURCE_LABEL_MAX = 300

#: The refusal sentences (the router answers ``{"code", "message"}``; the codes are the contract's).
LABEL_ERROR_SENTENCES: dict[str, str] = {
    "shadow_pair_already_labelled": "This pair is already labelled ({label_status}).",
    "verdict_invalid": "verdict must be incumbent, candidate, both or neither.",
    "failure_category_invalid": (
        "failure_category must be one of PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY, UNKNOWN."
    ),
    "failure_category_not_applicable": "failure_category applies to root-cause pairs only.",
}
LABEL_ERROR_STATUS: dict[str, int] = {
    "shadow_pair_already_labelled": 409,
    "verdict_invalid": 422,
    "failure_category_invalid": 422,
    "failure_category_not_applicable": 422,
}


class ShadowPairLabelError(ValueError):
    """A refused label: ``str(exc)`` is the code; ``message`` the sentence; ``status_code`` 409 / 422."""

    def __init__(self, code: str, **fmt: str) -> None:
        self.code = code
        self.message = LABEL_ERROR_SENTENCES[code].format(**fmt)
        self.status_code = LABEL_ERROR_STATUS[code]
        super().__init__(code)


def unlabelled_cutoff(now: datetime) -> datetime:
    """A pending pair created before this instant is past its 30 days (the sweep and the retention class)."""
    return now - timedelta(days=SHADOW_PAIR_RETENTION_DAYS)


def parse_sample_key(sample_key: str) -> Optional[tuple[str, uuid.UUID]]:
    """``root_cause:<uuid>`` → ``("test_case", id)``; ``summary:<uuid>`` → ``("test_run", id)``; else None."""
    prefix, _, raw = str(sample_key or "").partition(":")
    kind = _SAMPLE_KINDS.get(prefix)
    if kind is None:
        return None
    try:
        return kind, uuid.UUID(raw)
    except ValueError:
        return None


def _clean(value: Any) -> dict[str, Any]:
    """Re-sanitize a stored output on READ; a non-mapping is wrapped, an unreadable one is empty (fail closed)."""
    try:
        safe, _ = sanitize_persistence_payload(value)
    except TypeError:
        return {}
    if isinstance(safe, dict):
        return safe
    return {} if safe is None else {"value": safe}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def serialize_pair(
    pair: AIEvalShadowPair, *, source: Optional[ShadowPairSource], labelled_by_name: Optional[str]
) -> ShadowPairRead:
    """The wire row: outputs and label re-sanitized, ``expires_at`` on a pending pair only."""
    created_at = pair.created_at
    pending = pair.label_status == "pending"
    return ShadowPairRead(
        id=str(pair.id),
        project_id=str(pair.project_id),
        agent_id=pair.agent_id,
        agent_label=SHADOW_AGENT_LABELS.get(pair.agent_id, pair.agent_id),
        sample_key=pair.sample_key,
        incumbent_tier=pair.incumbent_tier,
        candidate_tier=pair.candidate_tier,
        incumbent_output=_clean(pair.incumbent_output),
        candidate_output=_clean(pair.candidate_output),
        incumbent_tokens=int(pair.incumbent_tokens or 0),
        candidate_tokens=int(pair.candidate_tokens or 0),
        label_status=pair.label_status,
        label=_clean(pair.label) if pair.label is not None else None,
        labelled_by_name=labelled_by_name,
        labelled_at=_iso(pair.labelled_at),
        created_at=_iso(created_at),
        source=source,
        expires_at=_iso(created_at + timedelta(days=SHADOW_PAIR_RETENTION_DAYS))
        if pending and created_at is not None else None,
    )


async def resolve_sources(
    db: AsyncSession, *, project_id: uuid.UUID, pairs: Iterable[AIEvalShadowPair]
) -> dict[uuid.UUID, Optional[ShadowPairSource]]:
    """Each pair's subject, in ONE statement FILTERED BY the project (test cases ⨝ test_runs UNION ALL test runs).

    A key that names another project's object, a purged one or nothing parseable maps to ``None``. ``href`` is
    built from ids only (``/runs/{run_id}?tab=tests`` for a test case, ``/runs/{run_id}`` for a run).
    """
    wanted: dict[uuid.UUID, tuple[str, uuid.UUID]] = {}
    for pair in pairs:
        parsed = parse_sample_key(pair.sample_key)
        if parsed is not None:
            wanted[pair.id] = parsed
    case_ids = sorted({obj_id for kind, obj_id in wanted.values() if kind == "test_case"}, key=str)
    run_ids = sorted({obj_id for kind, obj_id in wanted.values() if kind == "test_run"}, key=str)
    branches = []
    if case_ids:
        branches.append(
            select(
                literal("test_case").label("kind"),
                TestCase.id.label("id"),
                cast(TestCase.test_name, String).label("label"),
                TestRun.id.label("run_id"),
                TestRun.build_number.label("build_number"),
            )
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestCase.id.in_(case_ids), TestRun.project_id == project_id)
        )
    if run_ids:
        branches.append(
            select(
                literal("test_run").label("kind"),
                TestRun.id.label("id"),
                cast(null(), String).label("label"),
                TestRun.id.label("run_id"),
                TestRun.build_number.label("build_number"),
            ).where(TestRun.id.in_(run_ids), TestRun.project_id == project_id)
        )
    found: dict[tuple[str, uuid.UUID], ShadowPairSource] = {}
    if branches:
        statement = branches[0] if len(branches) == 1 else union_all(*branches)
        for kind, obj_id, label, run_id, build_number in (await db.execute(statement)).all():
            build = str(build_number) if build_number is not None else None
            if kind == "test_case":
                text = (str(label) if label else "")[:_SOURCE_LABEL_MAX] or None
                href = f"/runs/{run_id}?tab=tests"
            else:
                text = f"Run #{build}" if build else None
                href = f"/runs/{run_id}"
            found[(str(kind), uuid.UUID(str(obj_id)))] = ShadowPairSource(
                kind=str(kind), id=str(obj_id), label=text, run_id=str(run_id), build_number=build, href=href,
            )
    return {pair_id: found.get(key) for pair_id, key in wanted.items()}


async def list_pairs(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    agent_id: Optional[str] = None,
    label_status: Optional[str] = "pending",
    limit: int = 50,
    offset: int = 0,
) -> ShadowPairListResponse:
    """One page of the project's pairs, newest first; every filter applied INSIDE ``project_id = :p``.

    Three statements (four only when ``offset`` passes the end): the page with ``count(*) OVER ()`` and the
    labeller's name; the project's pending count + ``EXISTS`` any pair; the page's sources. Writes nothing.
    """
    where = [AIEvalShadowPair.project_id == project_id]
    if agent_id:
        where.append(AIEvalShadowPair.agent_id == agent_id)
    if label_status and label_status != "all":
        where.append(AIEvalShadowPair.label_status == label_status)
    page = (
        select(
            AIEvalShadowPair,
            func.count().over().label("total"),
            func.coalesce(User.full_name, User.username).label("labelled_by_name"),
        )
        .outerjoin(User, User.id == AIEvalShadowPair.labelled_by)
        .where(*where)
        .order_by(AIEvalShadowPair.created_at.desc(), AIEvalShadowPair.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(page)).all()
    total = int(rows[0][1]) if rows else 0
    if not rows and offset > 0:
        total = int(await db.scalar(select(func.count()).select_from(AIEvalShadowPair).where(*where)) or 0)

    project_pairs = AIEvalShadowPair.project_id == project_id
    pending_count = (
        select(func.count())
        .select_from(AIEvalShadowPair)
        .where(project_pairs, AIEvalShadowPair.label_status == "pending")
        .scalar_subquery()
    )
    seen = exists().where(project_pairs)
    pending_total, any_seen = (await db.execute(select(pending_count.label("pending"), seen.label("seen")))).one()

    pairs = [row[0] for row in rows]
    sources = await resolve_sources(db, project_id=project_id, pairs=pairs) if pairs else {}
    items = [
        serialize_pair(pair, source=sources.get(pair.id), labelled_by_name=name or None)
        for pair, _total, name in rows
    ]
    return ShadowPairListResponse(
        items=items, total=total, pending_total=int(pending_total or 0), live_tier_pairs_seen=bool(any_seen),
    )


async def get_pair(db: AsyncSession, pair_id: uuid.UUID) -> Optional[AIEvalShadowPair]:
    """The pair by id (the caller then checks ITS project — a pair of another project is the same 404)."""
    result = await db.execute(select(AIEvalShadowPair).where(AIEvalShadowPair.id == pair_id))
    return result.scalar_one_or_none()


def _label_from_request(pair: AIEvalShadowPair, body: ShadowPairLabelRequest) -> dict[str, Any]:
    if pair.label_status != "pending":
        raise ShadowPairLabelError("shadow_pair_already_labelled", label_status=str(pair.label_status))
    verdict = str(body.verdict or "").strip().lower()
    if verdict not in SHADOW_VERDICTS:
        raise ShadowPairLabelError("verdict_invalid")
    label: dict[str, Any] = {"verdict": verdict}
    category = str(body.failure_category or "").strip().upper()
    if category:
        if "failure_category" not in SHADOW_LABEL_FIELDS.get(pair.agent_id, ()):
            raise ShadowPairLabelError("failure_category_not_applicable")
        if category not in _FAILURE_CATEGORIES:
            raise ShadowPairLabelError("failure_category_invalid")
        label["failure_category"] = category
    notes = str(body.notes or "").strip()
    if notes:
        label["notes"] = sanitize_for_llm(notes)[:_NOTES_MAX]
    label["source"] = "human"
    return label


async def label_pair(
    db: AsyncSession, *, pair: AIEvalShadowPair, body: ShadowPairLabelRequest, actor: User, now: datetime
) -> AIEvalShadowPair:
    """A person's verdict on a pending pair. Final: the UPDATE only takes a row still ``pending``.

    Raises ``ShadowPairLabelError`` (a ``ValueError`` whose ``str`` is the code): ``shadow_pair_already_labelled``
    (409, also when a concurrent label won the row), ``verdict_invalid`` / ``failure_category_invalid`` /
    ``failure_category_not_applicable`` (422). Flushes, then refreshes the row before it is serialised.
    """
    label = _label_from_request(pair, body)
    result = await db.execute(
        update(AIEvalShadowPair)
        .where(AIEvalShadowPair.id == pair.id, AIEvalShadowPair.label_status == "pending")
        .values(label=label, label_status="human_labelled", labelled_by=actor.id, labelled_at=now)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        # A concurrent label (or the golden-match job) took the row first.
        current = await db.scalar(select(AIEvalShadowPair.label_status).where(AIEvalShadowPair.id == pair.id))
        raise ShadowPairLabelError("shadow_pair_already_labelled", label_status=str(current or "removed"))
    await db.flush()
    await db.refresh(pair)
    return pair


def _tier_category(output: Any) -> Optional[str]:
    if not isinstance(output, Mapping):
        return None
    value = output.get("failure_category")
    return str(value).strip().upper() if value else None


def golden_verdict(truth: str, incumbent_output: Any, candidate_output: Any) -> str:
    """Which tier's ``failure_category`` equals the human truth: incumbent / candidate / both / neither."""
    incumbent = _tier_category(incumbent_output) == truth
    candidate = _tier_category(candidate_output) == truth
    if incumbent and candidate:
        return "both"
    return "incumbent" if incumbent else "candidate" if candidate else "neither"


def _golden_candidates(limit: int):
    """ONE statement: pending root-cause pair → its test case → that case's run IN THE PAIR'S PROJECT → its
    analysis (one per test case) → the analysis' newest usable feedback (the ``get_label_health`` rule).

    Only rows with a human truth are selected, so every row the batch reads is labelled (an empty batch means
    none is left, never "the oldest 500 had no feedback"). The sample key is cast to a UUID only when it is a
    canonical one (a malformed key never fails the statement). ``FOR UPDATE OF`` the pair, ``SKIP LOCKED``.
    """
    usable = or_(
        AIFeedback.rating == "correct",
        and_(AIFeedback.rating == "incorrect", AIFeedback.corrected_category.is_not(None)),
    )
    feedback = (
        select(
            AIFeedback.id.label("feedback_id"),
            AIFeedback.rating.label("rating"),
            AIFeedback.corrected_category.label("corrected_category"),
        )
        .where(AIFeedback.analysis_id == AIAnalysis.id, usable)
        .order_by(AIFeedback.created_at.desc(), AIFeedback.id.desc())
        .limit(1)
        .lateral("newest_feedback")
    )
    case_id = cast(
        case(
            (
                AIEvalShadowPair.sample_key.op("~")(_ROOT_CAUSE_KEY_RE),
                func.substr(AIEvalShadowPair.sample_key, _ROOT_CAUSE_PREFIX_LEN + 1),
            ),
            else_=null(),
        ),
        PG_UUID(as_uuid=True),
    )
    truth = case(
        (feedback.c.rating == "incorrect", feedback.c.corrected_category),
        else_=AIAnalysis.failure_category,
    )
    return (
        select(AIEvalShadowPair, truth.label("truth"), feedback.c.feedback_id)
        .join(TestCase, TestCase.id == case_id)
        .join(TestRun, and_(TestRun.id == TestCase.test_run_id, TestRun.project_id == AIEvalShadowPair.project_id))
        .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
        .join(feedback, true())
        .where(
            AIEvalShadowPair.label_status == "pending",
            AIEvalShadowPair.sample_key.like("root_cause:%"),
            truth.is_not(None),
        )
        .order_by(AIEvalShadowPair.created_at, AIEvalShadowPair.id)
        .limit(limit)
        .with_for_update(of=AIEvalShadowPair, skip_locked=True)
    )


async def golden_match_batch(db: AsyncSession, *, now: datetime, limit: int = GOLDEN_MATCH_BATCH) -> int:
    """Label up to ``limit`` pending root-cause pairs from their test case's human feedback; returns how many.

    ``label = {verdict, failure_category, source: "feedback", feedback_id}``, ``label_status="golden_match"``,
    ``labelled_by`` NULL. ``summary:`` pairs are never auto-labelled. Flushes; the worker commits per batch.
    """
    rows = (await db.execute(_golden_candidates(limit))).all()
    for pair, truth, feedback_id in rows:
        category = str(getattr(truth, "value", truth)).strip().upper()
        pair.label = {
            "verdict": golden_verdict(category, pair.incumbent_output, pair.candidate_output),
            "failure_category": category,
            "source": "feedback",
            "feedback_id": str(feedback_id),
        }
        pair.label_status = "golden_match"
        pair.labelled_by = None
        pair.labelled_at = now
    if rows:
        await db.flush()
    return len(rows)


async def expire_unlabelled_batch(db: AsyncSession, *, now: datetime, limit: int = SHADOW_SWEEP_BATCH) -> int:
    """Delete up to ``limit`` PENDING pairs older than 30 days (deployment-wide, oldest first); returns how many.

    Labelled pairs are G2 evidence and are kept. Flushes nothing it reads back; the worker commits per batch.
    """
    doomed = (
        select(AIEvalShadowPair.id)
        .where(AIEvalShadowPair.label_status == "pending", AIEvalShadowPair.created_at < unlabelled_cutoff(now))
        .order_by(AIEvalShadowPair.created_at)
        .limit(limit)
    )
    result = await db.execute(
        delete(AIEvalShadowPair)
        .where(AIEvalShadowPair.id.in_(doomed))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def labelled_pairs_for(
    db: AsyncSession, *, project_id: uuid.UUID, agent_id: str, incumbent_tier: str, candidate_tier: str
) -> list[LabelledPair]:
    """The project's labelled pairs of one agent and tier pair (newest ≤ 1,000), outputs re-sanitized."""
    rows = (
        await db.execute(
            select(AIEvalShadowPair)
            .where(
                AIEvalShadowPair.project_id == project_id,
                AIEvalShadowPair.agent_id == agent_id,
                AIEvalShadowPair.incumbent_tier == incumbent_tier,
                AIEvalShadowPair.candidate_tier == candidate_tier,
                AIEvalShadowPair.label_status.in_(_LABELLED_STATUSES),
            )
            .order_by(AIEvalShadowPair.created_at.desc(), AIEvalShadowPair.id.desc())
            .limit(MAX_LIVE_LABELLED_PAIRS)
        )
    ).scalars().all()
    labelled: list[LabelledPair] = []
    for pair in rows:
        verdict = (pair.label or {}).get("verdict") if isinstance(pair.label, Mapping) else None
        if verdict not in SHADOW_VERDICTS:
            continue  # a label without a verdict is no evidence (never guessed)
        labelled.append(LabelledPair(
            pair_id=str(pair.id),
            verdict=str(verdict),
            label_status=str(pair.label_status),
            incumbent_output=_clean(pair.incumbent_output),
            candidate_output=_clean(pair.candidate_output),
            incumbent_tokens=int(pair.incumbent_tokens or 0),
            candidate_tokens=int(pair.candidate_tokens or 0),
        ))
    return labelled


__all__ = [
    "LABEL_ERROR_SENTENCES",
    "ShadowPairLabelError",
    "expire_unlabelled_batch",
    "get_pair",
    "golden_match_batch",
    "golden_verdict",
    "label_pair",
    "labelled_pairs_for",
    "list_pairs",
    "parse_sample_key",
    "resolve_sources",
    "serialize_pair",
    "unlabelled_cutoff",
]
