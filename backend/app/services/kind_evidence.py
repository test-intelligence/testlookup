"""Evidence-checklist enrichment for the derived failure kind (AI-4).

US-9.1 derived a per-failure *kind* (product / test_code / infrastructure /
unknown) as a pure function of the classifier verdict. This module upgrades
that single-signal derivation into an auditable **evidence checklist**: for
one analyzed failure it weighs every signal the platform already has and
emits a compact record

    {
        "kind": "infrastructure",
        "confidence": 78,
        "confidence_basis": "heuristic_estimate" | "human_corrected",
        "checks": [{"check": ..., "verdict": ..., "detail": ...}, ...],
        ...
    }

**Deterministic — NO new LLM call.** The checklist re-weighs signals that
already exist (classifier verdict, human corrections, infra-shape pattern
match, fingerprint history, execution status), so the resulting confidence is
honestly labeled ``heuristic_estimate`` — unless an authoritative human
correction pins the kind, in which case the basis is ``human_corrected``
(added to the shared basis vocabulary in ``services/confidence_bands.py``).

The five checks, in fixed order:

  ``memory_recall``    prior human corrections (authoritative — pins the
                       confidence when the correction maps to the derived
                       kind) and prior AI analyses of the same fingerprint.
  ``infra_shape``      does the error text match one of the rules-engine
                       keyword patterns, and does that pattern's category map
                       to the same kind?
  ``history_pattern``  fingerprint pass/fail history: intermittent (flaky
                       shape), chronic (never passes), or new-vs-passing.
  ``status_signal``    BROKEN (unexpected error → infrastructure-leaning) vs
                       FAILED (checked assertion → product-leaning).
  ``classifier``       the underlying engine verdict the kind derives from,
                       with its own confidence + basis.

Verdict vocabulary per check: ``supports`` / ``contradicts`` / ``neutral`` /
``unavailable``. Weighing is deliberately modest and fully pinned by
``tests/services/test_kind_evidence.py``:

  * human correction agreeing with the derived kind → confidence pinned to
    ``PINNED_CONFIDENCE`` (= the learning-loop's correction confidence),
    basis ``human_corrected``;
  * otherwise start from the classifier's own confidence_score, then
    ``+SUPPORT_BONUS`` per supporting check and ``-CONTRADICT_PENALTY`` per
    contradicting check (the ``classifier`` check itself never adjusts — it
    IS the base), clamped to ``[0, ADJUSTED_CAP]``.

Persistence (AI-F4 pattern — NO migration): the pipeline stores the record at
``AIAnalysis.routing_metadata["kind_evidence"]`` (hooked where the per-test
``_audit`` block is assembled in ``agents/analysis_agent.py``). Reads compute
on demand via :func:`compute_kind_evidence_for_test_case` when the stored
blob is absent — there is no backfill.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.confidence_bands import BASIS_HEURISTIC, BASIS_HUMAN_CORRECTED
from app.services.failure_kind import KIND_UNKNOWN, failure_kind

logger = logging.getLogger("services.kind_evidence")

# ── Vocabulary + weights (pinned by tests) ───────────────────────────────────

CHECK_MEMORY_RECALL = "memory_recall"
CHECK_INFRA_SHAPE = "infra_shape"
CHECK_HISTORY_PATTERN = "history_pattern"
CHECK_STATUS_SIGNAL = "status_signal"
CHECK_CLASSIFIER = "classifier"

EVIDENCE_CHECKS: tuple[str, ...] = (
    CHECK_MEMORY_RECALL,
    CHECK_INFRA_SHAPE,
    CHECK_HISTORY_PATTERN,
    CHECK_STATUS_SIGNAL,
    CHECK_CLASSIFIER,
)

VERDICT_SUPPORTS = "supports"
VERDICT_CONTRADICTS = "contradicts"
VERDICT_NEUTRAL = "neutral"
VERDICT_UNAVAILABLE = "unavailable"

# A human correction pins the kind confidence to the same value the
# learning-loop short-circuit assigns (analysis_corrections._HUMAN_CORRECTION_CONFIDENCE).
PINNED_CONFIDENCE = 95
SUPPORT_BONUS = 5
CONTRADICT_PENALTY = 15
# Un-pinned checklist confidence can never exceed this — only a human
# correction reaches PINNED_CONFIDENCE.
ADJUSTED_CAP = 90

# Display floor for decorating EXTERNAL surfaces (PR comments, check-run
# annotations): below this, no kind label at all — a low-confidence guess is
# noise, not signal. Internal surfaces (the /failures popover) always show
# the full checklist instead.
KIND_DISPLAY_CONFIDENCE_FLOOR = 60

SCHEMA_VERSION = 1


def _check(check: str, verdict: str, detail: str) -> dict[str, str]:
    return {"check": check, "verdict": verdict, "detail": detail}


def _clamp(value: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, value))


def _category_of(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


# ── Individual checks ────────────────────────────────────────────────────────


def _memory_recall_check(
    kind: str,
    correction: Optional[dict],
    prior_analyses: Optional[list[dict]],
) -> tuple[dict[str, str], bool]:
    """Returns (check_row, pinned). A human correction is authoritative:
    when its category maps to the derived kind the confidence is pinned."""
    if correction and correction.get("corrected_category"):
        corrected_kind = failure_kind(correction["corrected_category"], None)
        when = str(correction.get("corrected_at") or "")[:10] or "unknown-date"
        if corrected_kind == kind:
            return _check(
                CHECK_MEMORY_RECALL,
                VERDICT_SUPPORTS,
                f"Human corrected this test to {correction['corrected_category']} "
                f"({corrected_kind}) on {when} — authoritative, confidence pinned.",
            ), True
        return _check(
            CHECK_MEMORY_RECALL,
            VERDICT_CONTRADICTS,
            f"Human corrected this test to {correction['corrected_category']} "
            f"({corrected_kind}) on {when} — disagrees with derived kind {kind}.",
        ), False

    priors = [p for p in (prior_analyses or []) if p.get("failure_category")]
    if not priors:
        return _check(
            CHECK_MEMORY_RECALL,
            VERDICT_UNAVAILABLE,
            "No prior human corrections or analyses recalled for this fingerprint.",
        ), False

    prior_kinds = [failure_kind(p["failure_category"], None) for p in priors]
    agree = sum(1 for k in prior_kinds if k == kind)
    if agree == len(prior_kinds):
        return _check(
            CHECK_MEMORY_RECALL,
            VERDICT_SUPPORTS,
            f"{agree}/{len(prior_kinds)} prior analyses of this fingerprint "
            f"map to the same kind ({kind}).",
        ), False
    if agree == 0:
        return _check(
            CHECK_MEMORY_RECALL,
            VERDICT_CONTRADICTS,
            f"0/{len(prior_kinds)} prior analyses map to {kind} "
            f"(prior kinds: {', '.join(sorted(set(prior_kinds)))}).",
        ), False
    return _check(
        CHECK_MEMORY_RECALL,
        VERDICT_NEUTRAL,
        f"{agree}/{len(prior_kinds)} prior analyses agree with kind {kind} — mixed history.",
    ), False


def match_error_shape(error_message: Optional[str]) -> Optional[tuple[str, str]]:
    """First rules-engine keyword pattern matching the error text, as
    ``(rule_id, category)``. Reuses the exact ``_PATTERNS`` table the rules
    engine classifies with (one source of truth for infra shapes)."""
    text = (error_message or "").lower()
    if not text.strip():
        return None
    from app.services.rules_engine import _PATTERNS

    for rule_id, keywords, category, _summary in _PATTERNS:
        if any(kw in text for kw in keywords):
            return rule_id, category
    return None


def _infra_shape_check(kind: str, error_message: Optional[str]) -> dict[str, str]:
    if not (error_message or "").strip():
        return _check(
            CHECK_INFRA_SHAPE, VERDICT_UNAVAILABLE,
            "No error message captured — shape matching not possible.",
        )
    match = match_error_shape(error_message)
    if match is None:
        return _check(
            CHECK_INFRA_SHAPE, VERDICT_NEUTRAL,
            "Error text matches no known failure-shape pattern.",
        )
    rule_id, category = match
    pattern_kind = failure_kind(category, None)
    if pattern_kind == kind:
        return _check(
            CHECK_INFRA_SHAPE, VERDICT_SUPPORTS,
            f"Error text matches {rule_id} ({category}) — same kind ({kind}).",
        )
    return _check(
        CHECK_INFRA_SHAPE, VERDICT_CONTRADICTS,
        f"Error text matches {rule_id} ({category} → {pattern_kind}) — "
        f"disagrees with derived kind {kind}.",
    )


def _history_pattern_check(
    kind: str, category: str, history: Optional[dict],
) -> dict[str, str]:
    pass_count = int((history or {}).get("pass_count") or 0)
    fail_count = int((history or {}).get("fail_count") or 0)
    total = pass_count + fail_count
    if total == 0:
        return _check(
            CHECK_HISTORY_PATTERN, VERDICT_UNAVAILABLE,
            "No pass/fail history recorded for this fingerprint.",
        )
    fail_rate = fail_count / total
    if pass_count > 0 and fail_count > 0 and 0.10 <= fail_rate <= 0.90:
        detail = (
            f"Intermittent history: {fail_count}/{total} failures "
            f"({fail_rate:.0%}) — flaky shape."
        )
        if kind == "test_code":
            return _check(CHECK_HISTORY_PATTERN, VERDICT_SUPPORTS, detail)
        if kind == "product":
            return _check(
                CHECK_HISTORY_PATTERN, VERDICT_CONTRADICTS,
                detail + " An intermittent pattern weakens a product-regression verdict.",
            )
        return _check(CHECK_HISTORY_PATTERN, VERDICT_NEUTRAL, detail)
    if pass_count == 0 and fail_count >= 3:
        detail = f"Chronic failure: {fail_count} recorded failures, never passed."
        if kind == "test_code" and category == "FLAKY":
            return _check(
                CHECK_HISTORY_PATTERN, VERDICT_CONTRADICTS,
                detail + " A never-passing test cannot be flaky.",
            )
        return _check(CHECK_HISTORY_PATTERN, VERDICT_NEUTRAL, detail)
    if pass_count > 0 and fail_rate < 0.10:
        detail = (
            f"New/rare failure against a passing history "
            f"({pass_count} passes, {fail_count} failures) — regression shape."
        )
        if kind == "product":
            return _check(CHECK_HISTORY_PATTERN, VERDICT_SUPPORTS, detail)
        return _check(CHECK_HISTORY_PATTERN, VERDICT_NEUTRAL, detail)
    return _check(
        CHECK_HISTORY_PATTERN, VERDICT_NEUTRAL,
        f"History: {pass_count} passes / {fail_count} failures — no strong pattern.",
    )


def _status_signal_check(kind: str, status: Optional[str]) -> dict[str, str]:
    normalized = str(status or "").strip().upper()
    if not normalized:
        return _check(
            CHECK_STATUS_SIGNAL, VERDICT_UNAVAILABLE,
            "Execution status not available on this record.",
        )
    if normalized == "BROKEN":
        detail = "Status BROKEN — unexpected error outside an assertion (environment-leaning)."
        return _check(
            CHECK_STATUS_SIGNAL,
            VERDICT_SUPPORTS if kind == "infrastructure" else VERDICT_NEUTRAL,
            detail,
        )
    if normalized == "FAILED":
        detail = "Status FAILED — an assertion was checked and did not hold (product-leaning)."
        return _check(
            CHECK_STATUS_SIGNAL,
            VERDICT_SUPPORTS if kind == "product" else VERDICT_NEUTRAL,
            detail,
        )
    return _check(
        CHECK_STATUS_SIGNAL, VERDICT_NEUTRAL, f"Status {normalized} carries no kind signal.",
    )


def _classifier_check(kind: str, analysis: dict) -> dict[str, str]:
    category = _category_of(analysis.get("failure_category")) or "UNKNOWN"
    engine = (
        analysis.get("classified_by")
        or ((analysis.get("_routing") or {}).get("mode_used"))
        or ((analysis.get("_audit") or {}).get("analysis_mode"))
        or "unknown-engine"
    )
    confidence = analysis.get("confidence_score")
    basis = analysis.get("confidence_basis")
    detail = f"{engine} classified {category} (confidence {confidence if confidence is not None else '?'}"
    detail += f", basis {basis})." if basis else ")."
    if kind == KIND_UNKNOWN or category in ("", "UNKNOWN"):
        return _check(
            CHECK_CLASSIFIER, VERDICT_NEUTRAL,
            detail + " Classifier could not decide — kind is unknown.",
        )
    return _check(CHECK_CLASSIFIER, VERDICT_SUPPORTS, detail)


# ── Assembly ─────────────────────────────────────────────────────────────────


def assemble_kind_evidence(
    analysis: dict,
    *,
    status: Optional[str] = None,
    error_message: Optional[str] = None,
    history: Optional[dict] = None,
    correction: Optional[dict] = None,
    prior_analyses: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Assemble the evidence-checklist record for one analyzed failure.

    Pure and deterministic (no DB, no LLM) — every input is an
    already-available signal. Never raises on malformed inputs; each check
    degrades to ``unavailable``/``neutral``.
    """
    kind = failure_kind(analysis.get("failure_category"), status)

    recall_row, pinned = _memory_recall_check(kind, correction, prior_analyses)
    checks = [
        recall_row,
        _infra_shape_check(kind, error_message),
        _history_pattern_check(kind, _category_of(analysis.get("failure_category")), history),
        _status_signal_check(kind, status),
        _classifier_check(kind, analysis),
    ]

    try:
        base = _clamp(int(analysis.get("confidence_score") or 0))
    except (TypeError, ValueError):
        base = 0

    if pinned:
        confidence = PINNED_CONFIDENCE
        basis = BASIS_HUMAN_CORRECTED
    else:
        adjusted = base
        for row in checks:
            if row["check"] == CHECK_CLASSIFIER:
                continue  # the classifier verdict IS the base — never re-counted
            if row["verdict"] == VERDICT_SUPPORTS:
                adjusted += SUPPORT_BONUS
            elif row["verdict"] == VERDICT_CONTRADICTS:
                adjusted -= CONTRADICT_PENALTY
        confidence = _clamp(adjusted, 0, ADJUSTED_CAP)
        # Honest basis: the checklist only re-weighs existing signals — it is
        # an engineering heuristic, never an empirical calibration.
        basis = BASIS_HEURISTIC

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "confidence": confidence,
        "confidence_basis": basis,
        "pinned_by_human_correction": pinned,
        "classifier_confidence": base,
        "checks": checks,
    }


# ── Consumers' helpers ───────────────────────────────────────────────────────


def kind_confidence_of(analysis: Any) -> Optional[int]:
    """Per-failure kind confidence for gate/display consumers.

    Prefers the checklist confidence (``_audit.kind_evidence`` on in-flight
    dicts, ``kind_evidence`` on persisted routing_metadata blobs), falling
    back to the plain classifier ``confidence_score``. None when neither is
    known (the conservative gate floor treats None as below-floor).
    """
    if not isinstance(analysis, dict):
        return None
    for container in (analysis.get("_audit"), analysis):
        if isinstance(container, dict):
            evidence = container.get("kind_evidence")
            if isinstance(evidence, dict) and evidence.get("confidence") is not None:
                try:
                    return _clamp(int(evidence["confidence"]))
                except (TypeError, ValueError):
                    pass
    raw = analysis.get("confidence_score")
    if raw is None:
        return None
    try:
        return _clamp(int(raw))
    except (TypeError, ValueError):
        return None


def kind_label_for_display(
    kind: Optional[str], confidence: Optional[int],
) -> Optional[str]:
    """Human-readable kind label for EXTERNAL surfaces (PR comment rows,
    check-run annotations) — or None when the label would be noise:
    unknown kind, unknown confidence, or confidence below the display floor
    (``KIND_DISPLAY_CONFIDENCE_FLOOR``)."""
    if not kind or kind == KIND_UNKNOWN:
        return None
    if confidence is None or confidence < KIND_DISPLAY_CONFIDENCE_FLOOR:
        return None
    labels = {
        "product": "Product",
        "test_code": "Test code",
        "infrastructure": "Infrastructure",
    }
    label = labels.get(kind)
    if label is None:
        return None
    return f"{label} ({confidence}% conf, AI-classified)"


async def kind_labels_for_test_cases(
    db: AsyncSession, test_case_ids: list,
) -> dict[str, str]:
    """Display labels for external surfaces (PR comment rows, check-run
    annotations), keyed by ``str(test_case_id)``.

    Only test cases with a stored analysis whose kind confidence meets
    ``KIND_DISPLAY_CONFIDENCE_FLOOR`` get an entry — below the floor the
    surface shows no kind label at all (don't decorate with noise). Uses the
    persisted ``routing_metadata.kind_evidence`` confidence when present,
    falling back to the classifier confidence. Never raises; failures
    degrade to an empty map (label-less rows).
    """
    if not test_case_ids:
        return {}
    try:
        from app.models.postgres import AIAnalysis, TestCase

        rows = (
            await db.execute(
                select(
                    AIAnalysis.test_case_id,
                    AIAnalysis.failure_category,
                    AIAnalysis.confidence_score,
                    AIAnalysis.routing_metadata,
                    TestCase.status,
                )
                .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
                .where(AIAnalysis.test_case_id.in_(list(test_case_ids)))
            )
        ).all()
    except Exception as exc:  # noqa: BLE001 — labels are decoration, never blocking
        logger.debug("kind labels lookup failed: %s", exc)
        return {}

    labels: dict[str, str] = {}
    for row in rows:
        kind = failure_kind(
            row.failure_category, getattr(row.status, "value", row.status),
        )
        confidence = kind_confidence_of({
            "confidence_score": row.confidence_score,
            "kind_evidence": (row.routing_metadata or {}).get("kind_evidence"),
        })
        label = kind_label_for_display(kind, confidence)
        if label:
            labels[str(row.test_case_id)] = label
    return labels


# ── On-demand read path (no backfill) ────────────────────────────────────────


async def compute_kind_evidence_for_test_case(
    db: AsyncSession, test_case_id: uuid.UUID,
) -> Optional[dict[str, Any]]:
    """Evidence record for a stored analysis — stored blob when present,
    computed on demand otherwise. Returns None when the test case has no
    analysis (or on any lookup failure — never raises)."""
    try:
        from app.models.postgres import AIAnalysis, TestCase, TestRun

        row = (
            await db.execute(
                select(AIAnalysis, TestCase, TestRun.project_id)
                .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                .where(AIAnalysis.test_case_id == test_case_id)
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        ai, tc, project_id = row

        stored = (ai.routing_metadata or {}).get("kind_evidence")
        if isinstance(stored, dict) and stored.get("kind"):
            return stored

        correction: Optional[dict] = None
        prior_analyses: list[dict] = []
        history: Optional[dict] = None
        fingerprint = tc.test_fingerprint
        if fingerprint and project_id is not None:
            correction = await _fetch_correction(db, project_id, fingerprint)
            prior_analyses = await _fetch_prior_analyses(
                db, project_id, fingerprint, exclude_analysis_id=ai.id,
            )
            history = await _fetch_history(db, project_id, fingerprint)

        analysis_dict = {
            "failure_category": ai.failure_category,
            "confidence_score": ai.confidence_score,
            "classified_by": None,
            "_audit": ai.routing_metadata or {},
            "confidence_basis": (ai.routing_metadata or {}).get("confidence_basis"),
        }
        return assemble_kind_evidence(
            analysis_dict,
            status=getattr(tc.status, "value", tc.status),
            error_message=tc.error_message,
            history=history,
            correction=correction,
            prior_analyses=prior_analyses,
        )
    except Exception as exc:  # noqa: BLE001 — read enrichment must never 500 a page
        logger.debug("kind evidence on-demand compute failed: %s", exc)
        return None


async def _fetch_correction(
    db: AsyncSession, project_id: uuid.UUID, fingerprint: str,
) -> Optional[dict]:
    try:
        from app.services.analysis_corrections import get_corrections_for_fingerprints

        corrections = await get_corrections_for_fingerprints(db, project_id, [fingerprint])
        return corrections.get(fingerprint)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kind evidence correction fetch failed: %s", exc)
        return None


async def _fetch_prior_analyses(
    db: AsyncSession,
    project_id: uuid.UUID,
    fingerprint: str,
    *,
    exclude_analysis_id: Optional[uuid.UUID] = None,
    limit: int = 3,
) -> list[dict]:
    """Latest prior analyses of the same fingerprint (project-scoped),
    excluding the analysis under evaluation so it can't vouch for itself."""
    try:
        from app.models.postgres import AIAnalysis, TestCase, TestRun

        stmt = (
            select(AIAnalysis.id, AIAnalysis.failure_category, AIAnalysis.created_at)
            .join(TestCase, AIAnalysis.test_case_id == TestCase.id)
            .join(TestRun, TestCase.test_run_id == TestRun.id)
            .where(
                TestRun.project_id == project_id,
                TestCase.test_fingerprint == fingerprint,
            )
            .order_by(AIAnalysis.created_at.desc())
            .limit(limit + 1)
        )
        rows = (await db.execute(stmt)).all()
        return [
            {"failure_category": _category_of(r.failure_category), "created_at": r.created_at}
            for r in rows
            if exclude_analysis_id is None or r.id != exclude_analysis_id
        ][:limit]
    except Exception as exc:  # noqa: BLE001
        logger.debug("kind evidence prior-analyses fetch failed: %s", exc)
        return []


async def _fetch_history(
    db: AsyncSession, project_id: uuid.UUID, fingerprint: str,
) -> Optional[dict]:
    try:
        from app.models.postgres import TestCase, TestRun, TestStatus

        rows = (
            await db.execute(
                select(TestCase.status, func.count(TestCase.id))
                .join(TestRun, TestCase.test_run_id == TestRun.id)
                # Fingerprint queries are project-scoped (tenant isolation).
                .where(
                    TestRun.project_id == project_id,
                    TestCase.test_fingerprint == fingerprint,
                )
                .group_by(TestCase.status)
            )
        ).all()
        stats = {"pass_count": 0, "fail_count": 0}
        for status_value, count in rows:
            if status_value in (TestStatus.FAILED.value, TestStatus.BROKEN.value):
                stats["fail_count"] += count
            elif status_value == TestStatus.PASSED.value:
                stats["pass_count"] += count
        return stats
    except Exception as exc:  # noqa: BLE001
        logger.debug("kind evidence history fetch failed: %s", exc)
        return None
