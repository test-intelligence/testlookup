"""AI history READS (AIH-02, AIH-03, AIH-05, AIH-06; W03 contract §5.3, §6, §7).

Every function here only reads. Nothing in this module adds, flushes or commits
a Postgres row, and nothing writes to Mongo: the writers are the pipeline, the
four ``ai_analysis`` writers, the summary agent, the one-shot backfill task and
the retention sweep (``ai_history_service`` / ``ai_history_retention``). The
one write a route of this family performs -- the E8.4 distribution-audit row of
the export -- is staged by the router, not here.

Scoping, because a guard blind to query params caused nine IDORs here:

* every Mongo read of ``ai_run_versions`` carries ``test_run_id`` of the run
  the caller was authorised for, so a version id of another run -- same project
  or not -- reads as "not found";
* a baseline run is loaded with ``project_id == run.project_id``;
* a per-test read first proves the test belongs to the run.

Mongo failures surface as ``AIHistoryUnavailable`` (routers answer 503
``ai_history_unavailable``); a LIVE digest degrades instead (its decision-report
read becomes ``provenance.partial``), so live comparisons keep working while
Mongo is down.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import structlog
from sqlalchemy import func, select

from app.db.mongo import Collections
from app.models.postgres import (
    AIAnalysis,
    AIAnalysisVersion,
    LaunchStatus,
    ReviewRequest,
    RunComparisonReport,
    TestCase,
    TestRun,
    User,
)
from app.services import ai_history_core as core
from app.services.ai_history_service import AIHistoryUnavailable, build_digest
from app.services.review_envelope import (
    _unreviewed,
    envelope_from_review,
    review_envelope_for_run,
)
from app.services.analytics_scope import run_touches_suite_clause
from app.services.run_compare_service import ensure_run_has_suite, normalize_suite_name

logger = structlog.get_logger("services.ai_history_read")

__all__ = [
    "AIHistoryEmpty",
    "AIHistoryUnavailable",
    "AIVersionNotFound",
    "BaselineRunNotFound",
    "PreviousRun",
    "canonical_runs_ai",
    "compare_brief",
    "compare_runs_ai",
    "diff_versions",
    "export_bundle",
    "get_version",
    "get_version_detail",
    "history_ai_changes",
    "list_test_versions",
    "list_versions",
    "narrative_for",
    "previous_run_body",
    "resolve_digest_ref",
    "resolve_previous_run",
    "review_states",
    "test_history_ai",
]

#: The canonical page gives ``ai`` to the newest rows only; the lookup reads this many ids a query.
CANONICAL_AI_ROWS = 2000
CANONICAL_AI_CHUNK = 500

NO_SUITE_MESSAGE = "This run has no suite attribution, so there is no previous run of the same suite."
DEFAULT_SELECTION_REASON = "Same suite and branch, newest completed run before this one."
EXPLICIT_SELECTION_REASON = "You chose this baseline."
PREVIOUS_RUN_RULE = {
    "scope": "project+primary_suite+branch",
    "candidate_status": "not IN_PROGRESS (any outcome)",
    "order": "finished before this run, newest first",
}


class AIVersionNotFound(LookupError):
    """A version id that is not a version of this run (any reason, one answer). -> 404."""

    detail = "AI version not found"

    def __init__(self, detail: str | None = None) -> None:
        if detail:
            self.detail = detail
        super().__init__(self.detail)


class AIHistoryEmpty(AIVersionNotFound):
    """``to=latest`` on a run with no stored digest version. -> 404."""

    detail = "AI history is empty for this run"


class BaselineRunNotFound(LookupError):
    """``baseline_run_id`` names no run of THIS project (missing or foreign: one answer). -> 404."""

    detail = "Baseline run not found"

    def __init__(self) -> None:
        super().__init__(self.detail)


# ── Small coercions (older stored shapes must never 500 a read) ──────────────


def _utc(value: Any) -> Any:
    """Mongo hands back naive UTC datetimes; make them explicit instants."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw)
    return text if text else None


def _mapping(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _status(value: Any) -> Optional[str]:
    return _str_or_none(value)


def _canonical_uuid(value: Any) -> Optional[str]:
    """``str(uuid)`` in canonical lowercase form, or None for anything else."""
    if value is None:
        return None
    try:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def excerpt(text: Optional[str]) -> str:
    """Root-cause excerpt exactly as the digest builds it: sanitized, whitespace-normalised,
    cut at ``EXCERPT_CHARS`` (``ai_history_core.clean_text``)."""
    return core.clean_text(text, core.EXCERPT_CHARS)


def root_cause_sha(text: Optional[str]) -> Optional[str]:
    """The digest's root-cause hash (same function, same input), or None when empty."""
    return core.root_cause_hash(text)


def _jsonable(value: Any) -> Any:
    """Pure JSON (datetimes as ISO instants) for the export bundle."""

    def _default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return _utc(obj).isoformat()
        return str(obj)

    return json.loads(json.dumps(value, default=_default))


# ── Mongo access (every query carries test_run_id) ───────────────────────────


def _versions(mongo: Any) -> Any:
    if mongo is None:
        raise AIHistoryUnavailable("mongo is not configured")
    return mongo[Collections.AI_RUN_VERSIONS]


async def _find_one(mongo: Any, query: dict, projection: dict | None = None, sort: list | None = None) -> dict | None:
    try:
        kwargs: dict[str, Any] = {}
        if sort:
            kwargs["sort"] = sort
        doc = await _versions(mongo).find_one(query, projection or {"_id": 0}, **kwargs)
        return dict(doc) if isinstance(doc, Mapping) else None
    except AIHistoryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 -- any store failure is "unavailable", never a 500
        logger.warning("ai_history_read_failed", op="find_one", error_type=type(exc).__name__)
        raise AIHistoryUnavailable(type(exc).__name__) from exc


async def _find(
    mongo: Any, query: dict, projection: dict, *, sort: list, limit: int,
) -> list[dict]:
    try:
        cursor = _versions(mongo).find(query, projection).sort(sort).limit(limit)
        return [dict(doc) for doc in await cursor.to_list(length=limit) if isinstance(doc, Mapping)]
    except AIHistoryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_history_read_failed", op="find", error_type=type(exc).__name__)
        raise AIHistoryUnavailable(type(exc).__name__) from exc


async def _count(mongo: Any, query: dict) -> int:
    try:
        return int(await _versions(mongo).count_documents(query))
    except AIHistoryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_history_read_failed", op="count", error_type=type(exc).__name__)
        raise AIHistoryUnavailable(type(exc).__name__) from exc


def _payload(doc: Mapping) -> dict:
    return _mapping(doc.get("payload"))


# ── Review states (ONE query for every pipeline of a list) ───────────────────


async def review_states(db: Any, pipeline_ids: Sequence[Any]) -> dict[str, dict]:
    """``{pipeline_run_id: ReviewBlock}`` in ONE ``review_requests`` query.

    Same subject rule as ``review_envelope_for_pipeline_subject`` (the newest
    report review of that exact pipeline, superseded included: an immutable
    version keeps its terminal state). A pipeline with no review row reads
    ``pending_review`` (``_unreviewed``): absence of a review is not a review.
    """
    keys = sorted({key for key in (_canonical_uuid(p) for p in pipeline_ids) if key})
    out: dict[str, dict] = {}
    if keys:
        rows = (
            await db.execute(
                select(ReviewRequest)
                .where(
                    ReviewRequest.pipeline_run_id.in_([uuid.UUID(k) for k in keys]),
                    ReviewRequest.kind == "report",
                )
                .order_by(ReviewRequest.created_at.desc())
            )
        ).scalars().all()
        for row in rows:
            out.setdefault(str(row.pipeline_run_id), envelope_from_review(row).block())
    for key in keys:
        out.setdefault(key, _unreviewed().block())
    return out


# ── Version summaries ────────────────────────────────────────────────────────


def _report_ref(value: Any) -> Optional[dict]:
    ref = _mapping(value)
    report_id = ref.get("report_id")
    report_version = _int_or_none(ref.get("report_version"))
    if not report_id or report_version is None:
        return None
    return {"report_id": str(report_id), "report_version": report_version}


def version_summary(
    doc: Mapping,
    *,
    latest_version: Optional[int] = None,
    review: Optional[dict] = None,
    decision_report_ref: Optional[dict] = None,
) -> dict:
    """An ``AIVersionSummary`` body from a stored doc's ``meta`` + ``provenance`` (never its payload)."""
    meta = _mapping(doc.get("meta"))
    provenance = _mapping(doc.get("provenance"))
    version = _int_or_none(doc.get("version")) or 0
    categories = meta.get("categories")
    headline = _str_or_none(meta.get("headline"))
    return {
        "version_id": str(doc.get("version_id") or ""),
        "kind": str(doc.get("kind") or ""),
        "version": version,
        "trigger": str(doc.get("trigger") or "pipeline"),
        "pipeline_run_id": _str_or_none(doc.get("pipeline_run_id")),
        "generated_at": _utc(doc.get("generated_at")),
        "created_at": _utc(doc.get("created_at")),
        "analysis_mode": _str_or_none(provenance.get("analysis_mode")),
        "llm_provider": _str_or_none(provenance.get("llm_provider")),
        "llm_model": _str_or_none(provenance.get("llm_model")),
        "fallback_used": bool(provenance.get("fallback_used")),
        "deterministic": bool(provenance.get("deterministic")),
        "verdict": _str_or_none(meta.get("verdict")),
        "risk_score": _int_or_none(meta.get("risk_score")),
        "failing_tests": _int_or_none(meta.get("analysed_tests")),
        "categories": (
            {str(k): int(v) for k, v in categories.items() if _int_or_none(v) is not None}
            if isinstance(categories, Mapping) else None
        ),
        "cluster_count": _int_or_none(meta.get("cluster_count")),
        "headline": headline[:200] if headline else None,
        "content_sha256": str(doc.get("content_sha256") or ""),
        "decision_report_ref": decision_report_ref or _report_ref(meta.get("decision_report_ref")),
        "summary_version": _int_or_none(meta.get("summary_version")),
        "review": review,
        "is_latest": latest_version is not None and version == latest_version,
    }


async def _decision_report_refs(mongo: Any, run_key: str, docs: Sequence[Mapping]) -> dict[str, dict]:
    """``{pipeline_run_id: {report_id, report_version}}`` for digest rows stored without a ref
    (backfill rows), from ONE ``list_decision_report_versions`` call. Best-effort: {} on failure."""
    wanted = {
        _str_or_none(doc.get("pipeline_run_id"))
        for doc in docs
        if doc.get("kind") == core.KIND_DIGEST
        and doc.get("pipeline_run_id")
        and not _report_ref(_mapping(doc.get("meta")).get("decision_report_ref"))
    }
    wanted.discard(None)
    if not wanted:
        return {}
    from app.services.decision_report_service import list_decision_report_versions

    try:
        reports = await list_decision_report_versions(mongo, run_key, limit=100)
    except Exception as exc:  # noqa: BLE001 -- a missing link is not worth a 503
        logger.warning("ai_history_report_refs_unavailable", error_type=type(exc).__name__)
        return {}
    refs: dict[str, dict] = {}
    for report in reports:  # newest first: the highest version of a pipeline wins
        pipeline = _str_or_none(report.get("pipeline_run_id"))
        ref = _report_ref(report)
        if pipeline is not None and pipeline in wanted and ref and pipeline not in refs:
            refs[pipeline] = ref
    return refs


async def _summaries(db: Any, mongo: Any, run_key: str, docs: Sequence[Mapping], *, latest_version: Optional[int]) -> list[dict]:
    reviews = await review_states(db, [doc.get("pipeline_run_id") for doc in docs])
    refs = await _decision_report_refs(mongo, run_key, docs)
    out = []
    for doc in docs:
        pipeline = _canonical_uuid(doc.get("pipeline_run_id"))
        out.append(version_summary(
            doc,
            latest_version=latest_version,
            review=reviews.get(pipeline) if pipeline else _unreviewed().block(),
            decision_report_ref=refs.get(_str_or_none(doc.get("pipeline_run_id")) or ""),
        ))
    return out


async def live_available(db: Any, mongo: Any, run_id: uuid.UUID) -> bool:
    """The run has >= 1 ``ai_analysis`` row, or a published decision report."""
    has_analysis = (
        await db.execute(
            select(AIAnalysis.id)
            .join(TestCase, TestCase.id == AIAnalysis.test_case_id)
            .where(TestCase.test_run_id == run_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_analysis is not None:
        return True
    try:
        report = await mongo[Collections.DECISION_REPORTS].find_one(
            {"test_run_id": str(run_id), "status": "published"}, {"_id": 1},
        )
    except Exception:  # noqa: BLE001 -- "unknown" reads as not available, never a 500
        return False
    return report is not None


async def list_versions(db: Any, mongo: Any, *, run: TestRun, kind: str, limit: int) -> dict:
    """R1 body (``AIHistoryListResponse`` minus the envelope). Reads meta/provenance only, never payload."""
    run_key = str(run.id)
    query = {"test_run_id": run_key, "kind": kind}
    docs = await _find(mongo, query, {"_id": 0, "payload": 0}, sort=[("version", -1)], limit=limit)
    total = await _count(mongo, query)
    latest = _int_or_none(docs[0].get("version")) if docs else None
    versions = await _summaries(db, mongo, run_key, docs, latest_version=latest)
    return {
        "run_id": run_key,
        "kind": kind,
        "total": total,
        "limit": limit,
        "truncated": total > len(versions),
        "latest_version": latest,
        "live": {"available": await live_available(db, mongo, run.id)},
        "versions": versions,
    }


async def get_version(
    db: Any, mongo: Any, *, run_id: uuid.UUID, version_id: str, kind: str | None = None,
) -> dict:
    """The stored doc (minus ``_id``) of ``version_id`` IF it is a version of ``run_id``.

    The query ALWAYS carries ``test_run_id``: another run's id -- same project or
    not -- is indistinguishable from an id that never existed.
    """
    key = _canonical_uuid(version_id)
    if key is None:
        raise AIVersionNotFound()
    query: dict[str, Any] = {"test_run_id": str(run_id), "version_id": key}
    if kind is not None:
        query["kind"] = kind
    doc = await _find_one(mongo, query)
    if doc is None:
        raise AIVersionNotFound()
    return dict(doc)


async def _latest_doc(mongo: Any, run_key: str, kind: str, projection: dict | None = None) -> dict | None:
    return await _find_one(
        mongo, {"test_run_id": run_key, "kind": kind}, projection, sort=[("version", -1)],
    )


async def get_version_detail(db: Any, mongo: Any, *, run: TestRun, version_id: str) -> dict:
    """R4 body: ``AIVersionSummary`` + ``payload`` (the envelope is the router's)."""
    run_key = str(run.id)
    doc = await get_version(db, mongo, run_id=run.id, version_id=version_id)
    latest = await _latest_doc(mongo, run_key, str(doc.get("kind") or ""), {"_id": 0, "version": 1})
    latest_version = _int_or_none(latest.get("version")) if latest else None
    summary = (await _summaries(db, mongo, run_key, [doc], latest_version=latest_version))[0]
    return {**summary, "payload": _payload(doc)}


# ── Diff of two versions of one run (R2) ─────────────────────────────────────


def _stored_ref(run: TestRun, doc: Mapping) -> dict:
    provenance = _mapping(doc.get("provenance"))
    return {
        "ref": str(doc.get("version_id") or ""),
        "source": "stored",
        "version": _int_or_none(doc.get("version")),
        "kind": str(doc.get("kind") or core.KIND_DIGEST),
        "generated_at": _utc(doc.get("generated_at")),
        "pipeline_run_id": _str_or_none(doc.get("pipeline_run_id")),
        "content_sha256": _str_or_none(doc.get("content_sha256")),
        "analysis_mode": _str_or_none(provenance.get("analysis_mode")),
        "llm_model": _str_or_none(provenance.get("llm_model")),
        "run_id": str(run.id),
        "build_number": run.build_number,
    }


def _live_ref(run: TestRun, digest: Mapping) -> dict:
    provenance = _mapping(digest.get("provenance"))
    return {
        "ref": "live",
        "source": "live",
        "version": None,
        "kind": core.KIND_DIGEST,
        "generated_at": None,
        "pipeline_run_id": _str_or_none(provenance.get("pipeline_run_id")),
        "content_sha256": _str_or_none(digest.get("content_sha256")),
        "analysis_mode": _str_or_none(provenance.get("engine")),
        "llm_model": _str_or_none(provenance.get("llm_model")),
        "run_id": str(run.id),
        "build_number": run.build_number,
    }


async def resolve_digest_ref(db: Any, mongo: Any, *, run: TestRun, ref: str) -> tuple[dict, dict]:
    """``ref`` = a digest version id of ``run`` | ``"latest"`` (highest stored) | ``"live"`` (built now)."""
    if ref == "live":
        digest = await build_digest(db, mongo, run_id=run.id)
        return digest, _live_ref(run, digest)
    if ref == "latest":
        doc = await _latest_doc(mongo, str(run.id), core.KIND_DIGEST)
        if doc is None:
            raise AIHistoryEmpty()
    else:
        doc = await get_version(db, mongo, run_id=run.id, version_id=ref, kind=core.KIND_DIGEST)
    return _payload(doc), _stored_ref(run, doc)


async def diff_versions(db: Any, mongo: Any, *, run: TestRun, from_id: str, to_ref: str) -> dict:
    """R2 body: ``{run_id, from, to, diff}`` (mode ``versions``); ``from`` must be a stored digest of ``run``."""
    from_doc = await get_version(db, mongo, run_id=run.id, version_id=from_id, kind=core.KIND_DIGEST)
    to_digest, to_block = await resolve_digest_ref(db, mongo, run=run, ref=to_ref)
    diff = core.diff_digests(_payload(from_doc), to_digest, mode="versions")
    return {"run_id": str(run.id), "from": _stored_ref(run, from_doc), "to": to_block, "diff": diff}


# ── Per-test versions (R5) ───────────────────────────────────────────────────


async def _actor_names(db: Any, user_ids: Sequence[Any]) -> dict[str, Optional[str]]:
    ids = sorted({key for key in (_canonical_uuid(u) for u in user_ids) if key})
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(User.id, User.full_name, User.username).where(User.id.in_([uuid.UUID(i) for i in ids]))
        )
    ).all()
    return {str(row.id): (row.full_name or row.username) for row in rows}


def _test_version(row: Any, actors: Mapping[str, Optional[str]]) -> dict:
    actor_id = _canonical_uuid(getattr(row, "actor_user_id", None))
    return {
        "version_id": str(row.id),
        "version": int(row.version),
        "trigger": str(row.trigger),
        "pipeline_run_id": _str_or_none(row.pipeline_run_id),
        "actor": {"user_id": actor_id, "name": actors.get(actor_id)} if actor_id else None,
        "failure_category": _str_or_none(row.failure_category),
        "confidence_score": _int_or_none(row.confidence_score),
        "analysis_mode": _str_or_none(row.analysis_mode),
        "llm_provider": _str_or_none(row.llm_provider),
        "llm_model": _str_or_none(row.llm_model),
        "is_flaky": bool(row.is_flaky),
        "requires_human_review": bool(row.requires_human_review),
        "root_cause_excerpt": excerpt(row.root_cause_summary),
        "content_sha256": str(row.content_sha256 or ""),
        "created_at": row.created_at,
    }


def _current_version(row: AIAnalysis, latest_version: int) -> dict:
    routing = _mapping(row.routing_metadata)
    mode = _str_or_none(routing.get("analysis_mode"))
    category = _str_or_none(row.failure_category)
    return {
        "version_id": str(row.id),
        "version": latest_version,
        "trigger": "current",
        "pipeline_run_id": None,
        "actor": None,
        "failure_category": category,
        "confidence_score": _int_or_none(row.confidence_score),
        "analysis_mode": mode,
        "llm_provider": _str_or_none(row.llm_provider),
        "llm_model": _str_or_none(row.llm_model),
        "is_flaky": bool(row.is_flaky),
        "requires_human_review": bool(row.requires_human_review),
        "root_cause_excerpt": excerpt(row.root_cause_summary),
        "content_sha256": core.analysis_content_sha256(
            failure_category=category,
            confidence_score=_int_or_none(row.confidence_score),
            is_flaky=bool(row.is_flaky),
            requires_human_review=bool(row.requires_human_review),
            backend_error_found=bool(row.backend_error_found),
            pod_issue_found=bool(row.pod_issue_found),
            llm_provider=row.llm_provider,
            llm_model=row.llm_model,
            analysis_mode=mode,
            root_cause_summary=row.root_cause_summary,
        ),
        "created_at": row.created_at,
    }


async def list_test_versions(
    db: Any, *, run_id: uuid.UUID, test_id: uuid.UUID, limit: int = 20,
) -> dict | None:
    """R5 body, or ``None`` when ``test_id`` is not a test of ``run_id`` (-> 404).

    Excerpts only: evidence, role actions and recommended actions stay behind
    ``GET /analyze/{id}`` (QA_ENGINEER-gated). No Mongo.
    """
    test = (
        await db.execute(
            select(TestCase.id, TestCase.test_fingerprint, TestCase.test_name).where(
                TestCase.id == test_id, TestCase.test_run_id == run_id,
            )
        )
    ).first()
    if test is None:
        return None
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(AIAnalysisVersion).where(AIAnalysisVersion.test_case_id == test_id)
            )
        ).scalar_one()
        or 0
    )
    rows = list(
        (
            await db.execute(
                select(AIAnalysisVersion)
                .where(AIAnalysisVersion.test_case_id == test_id)
                .order_by(AIAnalysisVersion.version.desc())
                .limit(limit)
            )
        ).scalars().all()
    )
    current_row = (
        await db.execute(select(AIAnalysis).where(AIAnalysis.test_case_id == test_id))
    ).scalar_one_or_none()
    actors = await _actor_names(db, [row.actor_user_id for row in rows])
    latest = int(rows[0].version) if rows else 0
    return {
        "run_id": str(run_id),
        "test_id": str(test_id),
        "test_fingerprint": test.test_fingerprint,
        "test_name": test.test_name,
        "current": _current_version(current_row, latest) if current_row is not None else None,
        "versions": [_test_version(row, actors) for row in rows],
        "total": total,
    }


# ── The previous run (D42; R6) ───────────────────────────────────────────────


@dataclass
class PreviousRun:
    baseline: Optional[TestRun]
    reason: Optional[str]
    message: Optional[str]
    suite_name: Optional[str]


async def resolve_previous_run(db: Any, *, run: TestRun, suite_name: str | None = None) -> PreviousRun:
    """D42, ONE implementation: same project, same suite (the run's primary suite or ``suite_name``),
    same branch (NULL = NULL), not IN_PROGRESS (any outcome), finished before this run, newest first.

    The suite match is ``resolve_suite_pair_for_run``'s (primary suite OR a test
    of that suite); the parity integration test pins the two together. The
    CURRENT run may itself be in progress.
    """
    requested = (suite_name or "").strip() or None
    suite = requested or (run.primary_suite_name or "").strip() or None
    suite_key = normalize_suite_name(suite)
    if not suite_key:
        return PreviousRun(None, "no_primary_suite", NO_SUITE_MESSAGE, None)
    if requested is not None and normalize_suite_name(run.primary_suite_name) != suite_key:
        if not await ensure_run_has_suite(db, run.id, requested):
            return PreviousRun(
                None,
                "run_lacks_suite",
                f'This run has no tests in suite "{requested}", so there is no previous run of it to compare with.',
                requested,
            )
    branch_label = run.branch or "no branch"
    no_previous = PreviousRun(
        None, "no_previous_run", f'No earlier completed run of "{suite}" on branch "{branch_label}" found.', suite,
    )
    right_time = run.end_time or run.created_at
    if right_time is None:
        return no_previous

    # The run's primary suite or one of its tests is the suite: analytics_scope's builder (the same
    # strip+lower key as normalize_suite_name), not a clause spelled here (the scope ratchet).
    touches_suite = run_touches_suite_clause(suite_key)
    finished = func.coalesce(TestRun.end_time, TestRun.created_at)
    branch_filter = TestRun.branch.is_(None) if run.branch is None else TestRun.branch == run.branch
    baseline = (
        await db.execute(
            select(TestRun)
            .where(
                TestRun.project_id == run.project_id,
                TestRun.id != run.id,
                TestRun.status != LaunchStatus.IN_PROGRESS,
                branch_filter,
                touches_suite,
                finished < right_time,
            )
            .order_by(finished.desc(), TestRun.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if baseline is None:
        return no_previous
    return PreviousRun(baseline, None, None, suite)


async def _seq_map(db: Any, run_ids: Sequence[Any]) -> dict[str, int]:
    """Human "Run #N" labels, best-effort (null on failure, like the history timeline)."""
    ids = [rid for rid in run_ids if rid is not None]
    if not ids:
        return {}
    try:
        from app.services.runs_service import fetch_run_seq_map

        return await fetch_run_seq_map(db, ids)
    except Exception as exc:  # noqa: BLE001 -- a label is never worth failing the read
        logger.warning("ai_history_run_seq_unavailable", error_type=type(exc).__name__)
        return {}


def run_brief(run: TestRun, seq_map: Mapping[str, int]) -> dict:
    return {
        "run_id": str(run.id),
        "build_number": run.build_number,
        "run_seq": seq_map.get(str(run.id)),
        "branch": run.branch,
        "primary_suite_name": run.primary_suite_name,
        "status": _status(run.status),
        "created_at": run.created_at,
        "finished_at": run.end_time or run.created_at,
    }


async def previous_run_body(db: Any, *, run: TestRun, suite_name: str | None = None) -> dict:
    """R6 body (``AIPreviousRunResponse``)."""
    previous = await resolve_previous_run(db, run=run, suite_name=suite_name)
    seq = await _seq_map(db, [run.id, previous.baseline.id if previous.baseline else None])
    return {
        "run_id": str(run.id),
        "rule": {**PREVIOUS_RUN_RULE, "suite_name": previous.suite_name, "branch": run.branch},
        "current": run_brief(run, seq),
        "baseline": run_brief(previous.baseline, seq) if previous.baseline is not None else None,
        "reason": previous.reason,
        "message": previous.message,
    }


# ── Cross-run AI compare (R7) and the compare-payload brief (§7.1) ───────────


def _run_label(run: TestRun, seq: Mapping[str, int]) -> str:
    number = seq.get(str(run.id))
    if number is not None:
        return f"#{number}"
    return f"build {run.build_number}" if run.build_number else f"run {str(run.id)[:8]}"


def deterministic_sentence(diff: Mapping) -> str:
    """One ASCII sentence over a cross-run diff, for when no LLM narrative exists (D43)."""
    if not diff.get("has_changes"):
        return "No AI analysis changes since the baseline run."
    parts: list[str] = []
    verdict = _mapping(diff.get("verdict"))
    if verdict.get("changed"):
        parts.append(f"verdict {verdict.get('from') or 'unknown'} -> {verdict.get('to') or 'unknown'}")
    elif verdict.get("to"):
        parts.append(f"verdict {verdict.get('to')} (unchanged)")
    clusters = _mapping(_mapping(diff.get("clusters")).get("counts"))
    if clusters.get("added"):
        parts.append(f"{clusters['added']} new root cause(s)")
    if clusters.get("removed"):
        parts.append(f"{clusters['removed']} resolved")
    tests = _mapping(_mapping(diff.get("tests")).get("counts"))
    if tests.get("material_changed"):
        parts.append(f"{tests['material_changed']} test(s) changed category, confidence or flakiness")
    if tests.get("added"):
        parts.append(f"{tests['added']} new failing test(s)")
    if tests.get("removed"):
        parts.append(f"{tests['removed']} resolved test(s)")
    if _mapping(diff.get("provenance")).get("engine_only"):
        parts.append("only the analysis engine changed")
    if not parts:
        parts.append("root-cause wording changed")
    return "Compared with the baseline run: " + ", ".join(parts) + "."


async def narrative_for(
    db: Any,
    *,
    project_id: Any,
    baseline_id: Any,
    run_id: Any,
    suite_name: str | None,
    diff: Mapping,
) -> dict:
    """``AICompareNarrative``: the cached ``run_compare_v2`` report when ready, else a deterministic
    summary built here. Reads one row; NEVER calls an LLM and NEVER writes (D43: the pipeline hook
    produces the narrative; this GET only reports what exists)."""
    from app.agents.run_compare_agent import PROMPT_VERSION

    row = (
        await db.execute(
            select(RunComparisonReport).where(
                RunComparisonReport.project_id == project_id,
                RunComparisonReport.left_run_id == baseline_id,
                RunComparisonReport.right_run_id == run_id,
                RunComparisonReport.suite_name_normalized == normalize_suite_name(suite_name),
                RunComparisonReport.prompt_version == PROMPT_VERSION,
            )
        )
    ).scalar_one_or_none()
    report = _mapping(row.ai_report) if row is not None else {}
    if row is not None and row.status == "ready" and report:
        fallback = bool(report.get("fallback_used") or row.fallback_used)
        changes = report.get("ai_changes")
        return {
            "status": "ready",
            "source": "deterministic" if fallback else "llm",
            "label": "deterministic summary" if fallback else "AI narrative",
            "fallback_used": fallback,
            "executive_summary": str(report.get("executive_summary") or ""),
            "ai_changes": (
                [str(item) for item in changes] if isinstance(changes, list) and changes
                else core.build_ai_changes(diff)
            ),
            "risk_level": _str_or_none(report.get("risk_level")),
            "prompt_version": PROMPT_VERSION,
            "model_name": row.model_name,
            "generated_at": row.updated_at or row.created_at,
            "report_status": "ready",
        }
    return {
        "status": "deterministic",
        "source": "deterministic",
        "label": "deterministic summary",
        "fallback_used": True,
        "executive_summary": deterministic_sentence(diff),
        "ai_changes": core.build_ai_changes(diff),
        "risk_level": None,
        "prompt_version": PROMPT_VERSION,
        "model_name": None,
        "generated_at": None,
        "report_status": str(row.status) if row is not None and row.status in ("queued", "failed") else "absent",
    }


def _side(digest: Mapping, generated_at: Any) -> dict:
    side = dict(core.digest_side(digest))
    side["generated_at"] = _utc(generated_at)
    return side


async def compare_runs_ai(
    db: Any,
    mongo: Any,
    *,
    run: TestRun,
    baseline_run_id: uuid.UUID | None,
    suite_name: str | None,
    version_id: str | None = None,
    baseline_version_id: str | None = None,
) -> dict:
    """R7 body (``AICompareResponse`` minus the envelope).

    Default sides are LIVE (current state, human corrections included); pinning
    ``version_id`` / ``baseline_version_id`` compares what a pipeline produced.
    Raises ``BaselineRunNotFound`` (missing or another project), ``AIVersionNotFound``
    (a version id of another run) and ``AIHistoryUnavailable`` (a pinned version
    while Mongo is down). The caller rejects ``baseline_run_id == run.id`` first.
    """
    explicit = baseline_run_id is not None
    if explicit:
        baseline = (
            await db.execute(
                select(TestRun).where(TestRun.id == baseline_run_id, TestRun.project_id == run.project_id)
            )
        ).scalar_one_or_none()
        if baseline is None:
            raise BaselineRunNotFound()
        reason = message = None
    else:
        previous = await resolve_previous_run(db, run=run, suite_name=suite_name)
        baseline, reason, message = previous.baseline, previous.reason, previous.message

    current_doc = (
        await get_version(db, mongo, run_id=run.id, version_id=version_id, kind=core.KIND_DIGEST)
        if version_id is not None else None
    )
    body: dict[str, Any] = {
        "run_id": str(run.id),
        "project_id": str(run.project_id),
        "baseline": None,
        "reason": reason,
        "message": message,
        "current_ref": None,
        "baseline_ref": None,
        "sides": None,
        "diff": None,
        "narrative": None,
    }
    if baseline is None:
        return body
    baseline_doc = (
        await get_version(db, mongo, run_id=baseline.id, version_id=baseline_version_id, kind=core.KIND_DIGEST)
        if baseline_version_id is not None else None
    )

    seq = await _seq_map(db, [run.id, baseline.id])
    body["baseline"] = {
        **run_brief(baseline, seq),
        "selection": "explicit" if explicit else "default",
        "selection_reason": EXPLICIT_SELECTION_REASON if explicit else DEFAULT_SELECTION_REASON,
        "branch_mismatch": baseline.branch != run.branch,
    }
    if current_doc is not None:
        current_digest, body["current_ref"] = _payload(current_doc), _stored_ref(run, current_doc)
    else:
        current_digest = await build_digest(db, mongo, run_id=run.id)
        body["current_ref"] = _live_ref(run, current_digest)
    if baseline_doc is not None:
        baseline_digest, body["baseline_ref"] = _payload(baseline_doc), _stored_ref(baseline, baseline_doc)
    else:
        baseline_digest = await build_digest(db, mongo, run_id=baseline.id)
        body["baseline_ref"] = _live_ref(baseline, baseline_digest)
    body["sides"] = {
        "baseline": _side(baseline_digest, baseline_doc.get("generated_at") if baseline_doc else None),
        "current": _side(current_digest, current_doc.get("generated_at") if current_doc else None),
    }
    if core.is_not_analysed(current_digest):
        body["reason"] = "current_not_analysed"
        body["message"] = "This run has no AI analysis yet, so there is nothing to compare."
        return body
    if core.is_not_analysed(baseline_digest):
        body["reason"] = "baseline_not_analysed"
        body["message"] = f"{_run_label(baseline, seq)} has no AI analysis to compare."
        return body
    diff = core.diff_digests(baseline_digest, current_digest, mode="cross_run")
    body["diff"] = diff
    body["narrative"] = await narrative_for(
        db,
        project_id=run.project_id,
        baseline_id=baseline.id,
        run_id=run.id,
        suite_name=(suite_name or "").strip() or run.primary_suite_name,
        diff=diff,
    )
    return body


async def compare_brief(db: Any, mongo: Any, *, baseline: TestRun, run: TestRun) -> dict:
    """``AIAnalysisDiffBrief`` for the Run Compare payload (left = baseline, right = current).

    NEVER raises: a compare must never fail because of AI history. The digests
    are read inside a SAVEPOINT so a failed statement cannot poison the caller's
    session (the compare report is written on it afterwards).
    """
    if getattr(baseline, "project_id", None) != getattr(run, "project_id", None):
        return {"available": False, "reason": "different_projects"}
    try:
        async with db.begin_nested():
            baseline_digest = await build_digest(db, mongo, run_id=baseline.id)
            current_digest = await build_digest(db, mongo, run_id=run.id)
        # Same order as R7: the run being looked at first.
        if core.is_not_analysed(current_digest):
            return {"available": False, "reason": "current_not_analysed"}
        if core.is_not_analysed(baseline_digest):
            return {"available": False, "reason": "baseline_not_analysed"}
        diff = core.diff_digests(baseline_digest, current_digest, mode="cross_run")
        brief = dict(core.brief_from_diff(diff))
    except Exception as exc:  # noqa: BLE001 -- the compare itself must still answer
        logger.warning("ai_compare_brief_unavailable", error_type=type(exc).__name__)
        return {"available": False, "reason": "ai_history_unavailable"}
    brief.update({"available": True, "reason": None, "left_ref": "live", "right_ref": "live"})
    return brief


# ── Export bundle (R3; also the compliance pack's ai_history.json) ───────────


async def export_bundle(
    db: Any, mongo: Any, *, run: TestRun, max_versions: int = 100, max_test_rows: int = 1000,
) -> dict:
    """``AIHistoryExportBundle``: every stored version (oldest -> newest, newest ``max_versions`` per
    kind), the N-1 consecutive diffs, the per-test versions (<= ``max_test_rows``) and the
    review-envelope fields. Pure JSON. Raises ``AIHistoryUnavailable`` when Mongo is down."""
    run_key = str(run.id)
    digests = list(reversed(await _find(
        mongo, {"test_run_id": run_key, "kind": core.KIND_DIGEST}, {"_id": 0},
        sort=[("version", -1)], limit=max_versions,
    )))
    summaries = list(reversed(await _find(
        mongo, {"test_run_id": run_key, "kind": core.KIND_SUMMARY}, {"_id": 0},
        sort=[("version", -1)], limit=max_versions,
    )))
    digest_items = await _summaries(
        db, mongo, run_key, digests,
        latest_version=_int_or_none(digests[-1].get("version")) if digests else None,
    )
    summary_items = await _summaries(
        db, mongo, run_key, summaries,
        latest_version=_int_or_none(summaries[-1].get("version")) if summaries else None,
    )
    diffs = [
        {
            "from_version": _int_or_none(older.get("version")),
            "to_version": _int_or_none(newer.get("version")),
            "diff": core.diff_digests(_payload(older), _payload(newer), mode="versions"),
        }
        for older, newer in zip(digests, digests[1:])
    ]

    total_rows = int(
        (
            await db.execute(
                select(func.count()).select_from(AIAnalysisVersion).where(AIAnalysisVersion.test_run_id == run.id)
            )
        ).scalar_one()
        or 0
    )
    rows = (
        await db.execute(
            select(AIAnalysisVersion, TestCase.test_name)
            .join(TestCase, TestCase.id == AIAnalysisVersion.test_case_id)
            .where(AIAnalysisVersion.test_run_id == run.id)
            .order_by(AIAnalysisVersion.test_fingerprint, AIAnalysisVersion.version)
            .limit(max_test_rows)
        )
    ).all()
    actors = await _actor_names(db, [row[0].actor_user_id for row in rows])
    per_test = [
        {**_test_version(version, actors), "test_fingerprint": version.test_fingerprint, "test_name": test_name}
        for version, test_name in rows
    ]
    envelope = await review_envelope_for_run(db, run.id)
    bundle = {
        "schema_version": core.SCHEMA_VERSION,
        "report_type": "ai_history",
        "exported_at": datetime.now(timezone.utc),
        "run": {
            "run_id": run_key,
            "project_id": str(run.project_id),
            "build_number": run.build_number,
            "branch": run.branch,
            "primary_suite_name": run.primary_suite_name,
            "status": _status(run.status),
        },
        "versions": [{**item, "payload": _payload(doc)} for item, doc in zip(digest_items, digests)],
        "run_summaries": [{**item, "payload": _payload(doc)} for item, doc in zip(summary_items, summaries)],
        "diffs": diffs,
        "per_test_versions": {
            "total": total_rows,
            "truncated": total_rows > len(per_test),
            "items": per_test,
        },
        "limits": {"max_versions": max_versions, "max_test_rows": max_test_rows},
        "partial": [],
        **envelope.fields(),
    }
    out: dict = _jsonable(bundle)
    return out


# ── AI in test history (AIH-05, §7.3) ────────────────────────────────────────


async def test_history_ai(db: Any, test_case_ids: Sequence[Any]) -> dict[Any, dict]:
    """``{test_case_id: TestHistoryAI}`` in TWO queries by unique/indexed key.

    Project scope is the CALLER's: the ids come from an already project-scoped
    query (the history timeline, a canonical's own cases). A test without an
    ``ai_analysis`` row has no entry (the caller renders ``ai: null``).
    """
    ids = list(dict.fromkeys(i for i in test_case_ids if i is not None))
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                AIAnalysis.test_case_id,
                AIAnalysis.failure_category,
                AIAnalysis.confidence_score,
                AIAnalysis.root_cause_summary,
                AIAnalysis.is_flaky,
                AIAnalysis.requires_human_review,
                AIAnalysis.llm_model,
                AIAnalysis.routing_metadata["analysis_mode"].astext.label("analysis_mode"),
            ).where(AIAnalysis.test_case_id.in_(ids))
        )
    ).all()
    if not rows:
        return {}
    counts = {
        row[0]: int(row[1] or 0)
        for row in (
            await db.execute(
                select(AIAnalysisVersion.test_case_id, func.count())
                .where(AIAnalysisVersion.test_case_id.in_([row.test_case_id for row in rows]))
                .group_by(AIAnalysisVersion.test_case_id)
            )
        ).all()
    }
    return {
        row.test_case_id: {
            "test_id": str(row.test_case_id),
            "category": _str_or_none(row.failure_category),
            "confidence": _int_or_none(row.confidence_score),
            "root_cause_excerpt": excerpt(row.root_cause_summary),
            "root_cause_sha": root_cause_sha(row.root_cause_summary),
            "analysis_mode": _str_or_none(row.analysis_mode),
            "model": _str_or_none(row.llm_model),
            "is_flaky": bool(row.is_flaky),
            "requires_human_review": bool(row.requires_human_review),
            "version_count": counts.get(row.test_case_id, 0),
        }
        for row in rows
    }


def history_ai_changes(points: Sequence[Mapping]) -> list[dict]:
    """Where the AI changed its mind along a history timeline (points newest first, as served).

    Walks oldest -> newest keeping the last point that HAS ``ai``; a point is a
    change when its category or root-cause hash differs from that one. ``[]``
    with fewer than two AI points.
    """
    changes: list[dict] = []
    previous: Optional[Mapping] = None
    for point in reversed(list(points)):
        ai = point.get("ai")
        if not isinstance(ai, Mapping):
            continue
        if previous is not None:
            kinds = []
            if ai.get("category") != previous.get("category"):
                kinds.append("category")
            if ai.get("root_cause_sha") != previous.get("root_cause_sha"):
                kinds.append("root_cause")
            if kinds:
                changes.append({
                    "run_id": _str_or_none(point.get("run_id")),
                    "run_label": str(point.get("run_label") or point.get("build_number") or ""),
                    "from_category": previous.get("category"),
                    "to_category": ai.get("category"),
                    "from_confidence": previous.get("confidence"),
                    "to_confidence": ai.get("confidence"),
                    "kinds": sorted(kinds),
                })
        previous = ai
    return changes


_CANONICAL_AI_KEYS = (
    "category", "confidence", "analysis_mode", "model", "requires_human_review", "root_cause_excerpt", "version_count",
)


async def canonical_runs_ai(db: Any, test_case_ids_newest_first: Sequence[Any]) -> tuple[dict[Any, dict], bool]:
    """The canonical page's ``ai`` per row: the newest ``CANONICAL_AI_ROWS`` ids, read
    ``CANONICAL_AI_CHUNK`` at a time. Returns ``(ai_by_case_id, truncated)``."""
    ids = list(dict.fromkeys(i for i in test_case_ids_newest_first if i is not None))
    head = ids[:CANONICAL_AI_ROWS]
    out: dict[Any, dict] = {}
    for start in range(0, len(head), CANONICAL_AI_CHUNK):
        found = await test_history_ai(db, head[start:start + CANONICAL_AI_CHUNK])
        for case_id, ai in found.items():
            out[case_id] = {key: ai.get(key) for key in _CANONICAL_AI_KEYS}
    return out, len(ids) > CANONICAL_AI_ROWS
