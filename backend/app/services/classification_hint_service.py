"""FAIL-10: project classification hints -- a QA lead's "<field> contains <text> -> <category>".

A team usually knows its recurring failures better than any model does:
``ECONNREFUSED`` in the error message of a checkout test is the payments
sandbox being down, every time. A hint says so once, for one project, and from
the next analysis on a failing test it matches is classified by the hint
BEFORE any engine runs (rules, ML or LLM). Only a human correction of that
same test outranks it. Existing analyses are not re-classified.

Matching is a case-insensitive SUBSTRING (``str.casefold``), never a user
regex: no catastrophic backtracking on CI text, and a QA lead can predict what
it matches. The oldest enabled hint wins when several match (``(created_at,
id)`` order); at most :data:`MAX_HINTS_PER_PROJECT` per project and one per
(field, lower(text)) (the unique index of migration 0216).

This module owns the vocabularies (:data:`MATCH_FIELDS`, :data:`HINT_CATEGORIES`;
migration 0216 and the ORM CHECKs hold frozen copies, one test keeps the three
equal). Services flush; the router commits and stages the ledger rows. No
analytics epoch is bumped: no cached analytics read reads hints.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Mapping, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import ClassificationHint, TestCase, TestRun, User
from app.models.schemas import ClassificationHintCreate, ClassificationHintUpdate

MATCH_FIELDS: Final = ("error_message", "test_name", "suite_name", "class_name")
#: ``FailureCategory`` minus UNKNOWN: a hint that says "unknown" says nothing.
HINT_CATEGORIES: Final = ("PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY")
MAX_HINTS_PER_PROJECT: Final = 50
PATTERN_MIN: Final = 3
PATTERN_MAX: Final = 200

HINT_NOT_FOUND: Final = "Classification hint not found in this project."
MATCH_FIELD_INVALID: Final = "match_field must be one of error_message, test_name, suite_name, class_name."
CATEGORY_INVALID: Final = "category must be one of PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY."
PATTERN_INVALID: Final = "pattern must be 3-200 characters."
TOO_MANY_HINTS: Final = f"This project already has {MAX_HINTS_PER_PROJECT} classification hints."
DUPLICATE_HINT: Final = "A hint with this field and text already exists."
NOTHING_TO_CHANGE: Final = "Nothing to change."
#: The unique index a duplicate (project, field, lower(text)) violates.
DUPLICATE_CONSTRAINT: Final = "uq_classification_hints_project_rule"

#: The preview reads the project's newest failing rows of this window.
PREVIEW_SAMPLE: Final = 200
PREVIEW_LOOKBACK_DAYS: Final = 30
PREVIEW_EXAMPLES: Final = 5
PREVIEW_VALUE_CHARS: Final = 160

#: How a hint reads in the UI, the ledger and the decision trail.
FIELD_LABELS: Final[Mapping[str, str]] = {
    "error_message": "error message",
    "test_name": "test name",
    "suite_name": "suite",
    "class_name": "class",
}
CATEGORY_LABELS: Final[Mapping[str, str]] = {
    "PRODUCT_BUG": "Product bug",
    "INFRASTRUCTURE": "Infrastructure",
    "TEST_DATA": "Test data",
    "AUTOMATION_DEFECT": "Automation defect",
    "FLAKY": "Flaky",
}
#: The ``_routing`` keys a hint verdict carries for provenance
#: (``AnalysisProvenance.hint_*``, read verbatim by ``routers/analyze.py``).
HINT_ROUTING_KEYS: Final = ("hint_id", "hint_match_field", "hint_pattern", "hint_set_by")
#: The confidence a hint verdict asserts: a QA lead's rule, below a human
#: correction of the same test (95).
HINT_CONFIDENCE: Final = 90

_WHITESPACE = re.compile(r"\s+")


# ── validation ───────────────────────────────────────────────────────────────


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def validate_match_field(value: Any) -> str:
    if not isinstance(value, str) or value not in MATCH_FIELDS:
        raise _unprocessable(MATCH_FIELD_INVALID)
    return value


def validate_category(value: Any) -> str:
    category = value.strip().upper() if isinstance(value, str) else None
    if category not in HINT_CATEGORIES:
        raise _unprocessable(CATEGORY_INVALID)
    return category


def validate_pattern(value: Any) -> str:
    pattern = value.strip() if isinstance(value, str) else ""
    if not PATTERN_MIN <= len(pattern) <= PATTERN_MAX:
        raise _unprocessable(PATTERN_INVALID)
    return pattern


def _clean_note(value: Optional[str]) -> Optional[str]:
    note = (value or "").strip()
    return note or None


# ── reading ──────────────────────────────────────────────────────────────────


def _name_of(full_name: Optional[str], username: Optional[str]) -> Optional[str]:
    return (full_name or "").strip() or (username or "").strip() or None


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def hint_payload(hint: ClassificationHint, created_by_name: Optional[str]) -> dict[str, Any]:
    """The ``ClassificationHintResponse`` shape of one hint."""
    return {
        "id": str(hint.id),
        "project_id": str(hint.project_id),
        "match_field": hint.match_field,
        "pattern": hint.pattern,
        "category": hint.category,
        "note": hint.note,
        "enabled": bool(hint.enabled),
        "created_by_name": created_by_name,
        "created_at": _iso(hint.created_at),
        "updated_at": _iso(hint.updated_at),
    }


def hint_label(match_field: str, pattern: str, category: str) -> str:
    """``error message contains "ECONNREFUSED" → Infrastructure`` (the ledger's entity label)."""
    field = FIELD_LABELS.get(match_field, match_field)
    return f'{field} contains "{pattern[:60]}" → {CATEGORY_LABELS.get(category, category)}'


def _with_creator():
    return (
        select(ClassificationHint, User.full_name, User.username)
        .outerjoin(User, User.id == ClassificationHint.created_by)
    )


async def list_hints(db: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every hint of the project, enabled or not, in precedence order (``created_at, id``)."""
    rows = (
        await db.execute(
            _with_creator()
            .where(ClassificationHint.project_id == project_id)
            .order_by(ClassificationHint.created_at, ClassificationHint.id)
        )
    ).all()
    return [hint_payload(hint, _name_of(full, user)) for hint, full, user in rows]


async def _load(
    db: AsyncSession, project_id: uuid.UUID, hint_id: uuid.UUID,
) -> tuple[ClassificationHint, Optional[str]]:
    """The hint ``WHERE id AND project_id``: another project's hint and an
    unknown id are the same 404 (no oracle)."""
    row = (
        await db.execute(
            _with_creator().where(
                ClassificationHint.id == hint_id,
                ClassificationHint.project_id == project_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=HINT_NOT_FOUND)
    hint, full, user = row
    return hint, _name_of(full, user)


async def load_enabled_hints(db: AsyncSession, project_id: uuid.UUID | str) -> list[dict[str, Any]]:
    """The project's ENABLED hints as plain dicts for ``run_context`` /
    pipeline state, precedence order. One statement
    (``ix_classification_hints_project_enabled``)."""
    pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    rows = (
        await db.execute(
            _with_creator()
            .where(ClassificationHint.project_id == pid, ClassificationHint.enabled.is_(True))
            .order_by(ClassificationHint.created_at, ClassificationHint.id)
        )
    ).all()
    return [
        {
            "id": str(hint.id),
            "project_id": str(hint.project_id),
            "match_field": hint.match_field,
            "pattern": hint.pattern,
            "category": hint.category,
            "note": hint.note,
            "enabled": True,
            "created_by_name": _name_of(full, user),
            "created_at": _iso(hint.created_at),
        }
        for hint, full, user in rows
    ]


# ── writing (flush only; the router commits) ─────────────────────────────────


async def create_hint(
    db: AsyncSession, project_id: uuid.UUID, actor: User, body: ClassificationHintCreate,
) -> dict[str, Any]:
    """Validate, enforce the per-project cap, add and flush. A duplicate
    (field, lower(text)) surfaces as the unique index's ``IntegrityError``
    at the flush; the router maps it to 409."""
    match_field = validate_match_field(body.match_field)
    pattern = validate_pattern(body.pattern)
    category = validate_category(body.category)
    count = (
        await db.execute(
            select(func.count()).select_from(ClassificationHint).where(ClassificationHint.project_id == project_id)
        )
    ).scalar_one()
    if int(count or 0) >= MAX_HINTS_PER_PROJECT:
        raise _unprocessable(TOO_MANY_HINTS)
    hint = ClassificationHint(
        id=uuid.uuid4(),
        project_id=project_id,
        match_field=match_field,
        pattern=pattern,
        category=category,
        note=_clean_note(body.note),
        enabled=bool(body.enabled),
        created_by=getattr(actor, "id", None),
    )
    db.add(hint)
    await db.flush()
    # Server defaults (created_at, updated_at) are not on the instance after a
    # flush; reading them unrefreshed is a lazy load (MissingGreenlet).
    await db.refresh(hint)
    return hint_payload(hint, _name_of(getattr(actor, "full_name", None), getattr(actor, "username", None)))


async def update_hint(
    db: AsyncSession, project_id: uuid.UUID, hint_id: uuid.UUID, actor: User, body: ClassificationHintUpdate,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply only the fields SENT (``model_fields_set``). Returns ``(payload,
    before, after)``: ``before`` / ``after`` hold the old and new VALUES of the
    fields that really changed (both empty when nothing did -- no ledger row).
    An empty body is a 422 ``Nothing to change.``; ``enabled: null`` is not a
    change."""
    sent = {name for name in body.model_fields_set if not (name == "enabled" and body.enabled is None)}
    if not sent:
        raise _unprocessable(NOTHING_TO_CHANGE)
    hint, created_by_name = await _load(db, project_id, hint_id)
    proposed: dict[str, Any] = {}
    if "match_field" in sent:
        proposed["match_field"] = validate_match_field(body.match_field)
    if "pattern" in sent:
        proposed["pattern"] = validate_pattern(body.pattern)
    if "category" in sent:
        proposed["category"] = validate_category(body.category)
    if "note" in sent:
        proposed["note"] = _clean_note(body.note)
    if "enabled" in sent:
        proposed["enabled"] = bool(body.enabled)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field in ("match_field", "pattern", "category", "note", "enabled"):
        if field in proposed and getattr(hint, field) != proposed[field]:
            before[field] = getattr(hint, field)
            after[field] = proposed[field]
            setattr(hint, field, proposed[field])
    if after:
        await db.flush()
        await db.refresh(hint)
    return hint_payload(hint, created_by_name), before, after


async def delete_hint(
    db: AsyncSession, project_id: uuid.UUID, hint_id: uuid.UUID, actor: User,
) -> dict[str, Any]:
    """Delete and flush; returns the deleted hint's payload (for the ledger row)."""
    hint, created_by_name = await _load(db, project_id, hint_id)
    payload = hint_payload(hint, created_by_name)
    await db.delete(hint)
    await db.flush()
    return payload


# ── preview (reads only) ────────────────────────────────────────────────────


def _field_value(test: Mapping[str, Any], field: str) -> Optional[str]:
    value = test.get(field)
    return value if isinstance(value, str) else None


def _example_value(value: str, pattern: str) -> str:
    """At most :data:`PREVIEW_VALUE_CHARS` of the value, whitespace collapsed,
    around the match so the reader sees WHY it matched; redacted."""
    from app.services.redaction_service import redact_log_message

    flat = _WHITESPACE.sub(" ", value).strip()
    if len(flat) > PREVIEW_VALUE_CHARS:
        at = flat.casefold().find(pattern.casefold())
        start = max(0, min(at - 40, len(flat) - PREVIEW_VALUE_CHARS)) if at >= 0 else 0
        flat = flat[start:start + PREVIEW_VALUE_CHARS]
    return redact_log_message(flat)[:PREVIEW_VALUE_CHARS]


async def preview_hint(db: AsyncSession, project_id: uuid.UUID, match_field: str, pattern: str) -> dict[str, Any]:
    """How many of the project's newest :data:`PREVIEW_SAMPLE` FAILED/BROKEN
    rows of the last :data:`PREVIEW_LOOKBACK_DAYS` days the rule would match,
    with up to :data:`PREVIEW_EXAMPLES` examples. The SAME matcher the
    pipeline uses (:func:`match_hint`). Writes nothing."""
    field = validate_match_field(match_field)
    text = validate_pattern(pattern)
    column = getattr(TestCase, field)
    rows = (
        await db.execute(
            select(TestCase.test_name, column.label("value"))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(
                TestRun.project_id == project_id,
                TestCase.status.in_(("FAILED", "BROKEN")),
                TestCase.created_at >= datetime.now(timezone.utc) - timedelta(days=PREVIEW_LOOKBACK_DAYS),
            )
            .order_by(TestCase.created_at.desc(), TestCase.id.desc())
            .limit(PREVIEW_SAMPLE)
        )
    ).all()
    probe = [{"id": "preview", "match_field": field, "pattern": text, "enabled": True}]
    matched = 0
    examples: list[dict[str, str]] = []
    for test_name, value in rows:
        if match_hint({field: value}, probe) is None:
            continue
        matched += 1
        if len(examples) < PREVIEW_EXAMPLES:
            examples.append({"test_name": str(test_name or "")[:PREVIEW_VALUE_CHARS],
                             "value": _example_value(str(value), text)})
    return {"matched": matched, "sampled": len(rows), "lookback_days": PREVIEW_LOOKBACK_DAYS, "examples": examples}


# ── matching and the verdict (pure) ──────────────────────────────────────────


def match_hint(test: Mapping[str, Any], hints: Optional[Sequence[Mapping[str, Any]]]) -> Optional[dict]:
    """The first ENABLED hint, in ``(created_at, id)`` order, whose
    ``test[match_field]`` (a str; anything else is skipped) contains its
    ``pattern`` under ``casefold()``; ``None`` when none does."""
    if not hints:
        return None
    ordered = sorted(hints, key=lambda h: (str(h.get("created_at") or ""), str(h.get("id") or "")))
    for hint in ordered:
        if hint.get("enabled") is False:
            continue
        pattern = hint.get("pattern")
        field = hint.get("match_field")
        if not isinstance(pattern, str) or not pattern or field not in MATCH_FIELDS:
            continue
        value = _field_value(test, field)
        if value is not None and pattern.casefold() in value.casefold():
            return dict(hint)
    return None


def hint_rationale(hint: Mapping[str, Any]) -> str:
    """The decision-trail line: ``Matched project hint (set by Priya Nair):
    error message contains "ECONNREFUSED" → Infrastructure``."""
    who = hint.get("created_by_name") or "a QA lead"
    field = FIELD_LABELS.get(str(hint.get("match_field")), str(hint.get("match_field")))
    category = CATEGORY_LABELS.get(str(hint.get("category")), str(hint.get("category")))
    return f'Matched project hint (set by {who}): {field} contains "{hint.get("pattern")}" → {category}'


def build_hint_analysis(hint: Mapping[str, Any], test: Optional[Mapping[str, Any]] = None) -> dict:
    """Pure: the analysis dict a matching hint produces (the
    ``analysis_corrections.build_corrected_analysis`` shape), with a ``_routing``
    record (engine ``hint``, source ``project_hint``) and the four ``hint_*``
    provenance keys. Never built through ``rules_engine._build_result``
    (its ``confidence_bands.get_band`` raises for a rule id it does not know).

    The evidence is the input the rule matched on, marked as classifier input
    (``classifier_evidence.CLASSIFIER_EVIDENCE_KIND``: never an attested
    artifact), so the confidence policy reads a cited verdict rather than an
    evidence-free one.
    """
    from app.services.classifier_evidence import CLASSIFIER_EVIDENCE_KIND

    hint_id = str(hint.get("id") or "")
    field = str(hint.get("match_field") or "")
    pattern = str(hint.get("pattern") or "")
    category = str(hint.get("category") or "")
    note = (hint.get("note") or "").strip() if isinstance(hint.get("note"), str) else ""
    summary = f'Classified by a project hint: {FIELD_LABELS.get(field, field)} contains "{pattern}".'
    if note:
        summary += f" Note: {note}"
    matched_value = _field_value(test or {}, field) if test else None
    excerpt = matched_value if matched_value is not None else pattern
    try:
        from app.services.evidence_sanitizer import sanitize_reference_text

        excerpt, _, _ = sanitize_reference_text(excerpt, limit=300)
    except Exception:  # noqa: BLE001 -- provenance must never lose the classification
        excerpt = excerpt[:300]
    evidence = [{
        "source": "project_hint",
        "kind": CLASSIFIER_EVIDENCE_KIND,
        "excerpt": excerpt,
        "rule_id": f"project_hint.{hint_id}",
        "test_name": str((test or {}).get("test_name") or "")[:200] or None,
        "basis": "matched_project_hint",
    }]
    return {
        "failure_category": category,
        "root_cause_summary": summary,
        "confidence_score": HINT_CONFIDENCE,
        "is_flaky": category == "FLAKY",
        # A hint looked at no logs or pods: it found neither (the
        # ``AnalysisResponse`` fields every engine's verdict carries).
        "backend_error_found": False,
        "pod_issue_found": False,
        "requires_human_review": False,
        "recommended_actions": [],
        "evidence_references": evidence,
        "classified_by": "project_hint",
        "confidence_basis": "project_hint",
        "confidence_rule_id": f"project_hint.{hint_id}",
        "_routing": {
            "mode_requested": None,
            "mode_resolved": "hint",
            "mode_used": "hint",
            "fallback_from": None,
            "fallback_reason": None,
            "source": "project_hint",
            "hint_id": hint_id,
            "hint_match_field": field,
            "hint_pattern": pattern,
            "hint_set_by": hint.get("created_by_name"),
        },
    }
