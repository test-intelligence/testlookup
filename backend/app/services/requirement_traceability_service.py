"""Requirement traceability "lite" (W13 TCL-06, owner decision D10).

Manual requirement keys on authored test cases, the project's KNOWN keys
(those links, the generation batches' ``requirement_coverage`` ids and the
active knowledge chunks' extracted ``requirement_id``s, all through ONE
normaliser), the uncovered-requirement gaps and the "Requirements verified N of
M" stat. No matrix, no export, no Jira, no automatic backfill.

Stage-only: the link writes ``add`` / ``delete`` / ``flush`` and record their
activity row on the request session; the router commits. Reads never write.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import and_, case as sql_case, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.test_library_vocab import (
    KNOWN_KEYS_CAP,
    REQUIREMENT_ALREADY_LINKED,
    REQUIREMENT_CASE_RETIRED,
    REQUIREMENT_GAP_REASONS,
    REQUIREMENT_KEY_CONTROL_RE,
    REQUIREMENT_KEY_INVALID,
    REQUIREMENT_KEY_MAX,
    REQUIREMENT_KEY_SOURCES,
    REQUIREMENT_LINK_NOT_FOUND,
    REQUIREMENT_LINK_TYPE_INVALID,
    REQUIREMENT_LINK_TYPES,
    REQUIREMENT_TITLE_MAX,
    REQUIREMENTS_UNMEASURED_SENTENCE,
    RETIRED_STATES,
    RUNNABLE_STATES,
    TRACEABILITY_FLAG,
    normalize_requirement_key,
)
from app.models.postgres import (
    KnowledgeChunk,
    ManagedTestCase,
    RequirementCoverage,
    TestCaseRequirementLink,
    User,
)
from app.services.activity.service import ActorRef, record as record_activity

CASES_WITHOUT_REQUIREMENTS_PREVIEW = 25
_L = TestCaseRequirementLink
_M = ManagedTestCase


async def traceability_enabled(db: AsyncSession, *, project_id: uuid.UUID, user: Any) -> bool:
    """``test_case_requirement_traceability`` for this project and caller (passes ``db``: no second session)."""
    from app.services import feature_flags

    return bool(await feature_flags.is_enabled(TRACEABILITY_FLAG, db=db, project_id=project_id, user=user))


def _name(full_name: Optional[str], username: Optional[str]) -> Optional[str]:
    return (full_name or "").strip() or (username or "").strip() or None


def link_read(link: TestCaseRequirementLink, created_by_name: Optional[str]) -> dict:
    """A ``RequirementLinkRead`` body."""
    return {
        "id": link.id, "test_case_id": link.test_case_id, "requirement_key": link.requirement_key,
        "normalized_key": link.requirement_key_norm, "requirement_title": link.requirement_title,
        "link_type": link.link_type, "created_by_name": created_by_name, "created_at": link.created_at,
    }


def validated_key(raw: Any) -> tuple[str, str]:
    """``(key as typed, normalised key)``; 422 ``REQUIREMENT_KEY_INVALID`` otherwise."""
    key = raw.strip() if isinstance(raw, str) else ""
    if not key or len(key) > REQUIREMENT_KEY_MAX or REQUIREMENT_KEY_CONTROL_RE.search(key):
        raise HTTPException(status_code=422, detail=REQUIREMENT_KEY_INVALID)
    normalized = normalize_requirement_key(key)
    if not normalized:
        raise HTTPException(status_code=422, detail=REQUIREMENT_KEY_INVALID)
    return key, normalized


def _clean_title(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    text = REQUIREMENT_KEY_CONTROL_RE.sub(" ", raw).strip()
    return text[:REQUIREMENT_TITLE_MAX] or None


def _refuse_retired(case: ManagedTestCase) -> None:
    if case.status in RETIRED_STATES:
        raise HTTPException(status_code=409, detail=REQUIREMENT_CASE_RETIRED)


async def list_links(db: AsyncSession, case: ManagedTestCase) -> list[dict]:
    """The case's links, oldest first, each with its author's name (one statement)."""
    stmt = (
        select(_L, User.full_name, User.username)
        .outerjoin(User, User.id == _L.created_by_id)
        .where(_L.test_case_id == case.id, _L.project_id == case.project_id)
        .order_by(_L.created_at.asc(), _L.id.asc())
    )
    return [link_read(link, _name(full, user)) for link, full, user in (await db.execute(stmt)).all()]


async def _existing_link_id(db: AsyncSession, case_id: uuid.UUID, normalized: str) -> Optional[uuid.UUID]:
    """The case's link of this normalised key, if any (the pre-check before the unique index)."""
    stmt = select(_L.id).where(_L.test_case_id == case_id, _L.requirement_key_norm == normalized).limit(1)
    return (await db.execute(stmt)).scalar()


async def add_link(db: AsyncSession, case: ManagedTestCase, payload: Any, actor: Any) -> TestCaseRequirementLink:
    """Stage one manual link (and its ``test_case.requirement_linked`` row); the router commits.

    422 key / link type; 409 a retired case; 409 the normalised key already on
    the case (checked first, and the unique index answers the same under a race).
    """
    key, normalized = validated_key(payload.requirement_key)
    link_type = (payload.link_type or "verifies").strip()
    if link_type not in REQUIREMENT_LINK_TYPES:
        raise HTTPException(status_code=422, detail=REQUIREMENT_LINK_TYPE_INVALID)
    _refuse_retired(case)
    already = REQUIREMENT_ALREADY_LINKED.format(key=normalized)
    if await _existing_link_id(db, case.id, normalized) is not None:
        raise HTTPException(status_code=409, detail=already)
    link = TestCaseRequirementLink(
        id=uuid.uuid4(), project_id=case.project_id, test_case_id=case.id, requirement_key=key,
        requirement_key_norm=normalized, requirement_title=_clean_title(payload.requirement_title),
        link_type=link_type, created_by_id=getattr(actor, "id", None),
    )
    try:
        async with db.begin_nested():
            db.add(link)
            await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail=already) from None
    await record_activity(
        db,
        project_id=case.project_id,
        event_type="test_case.requirement_linked",
        actor=ActorRef.from_user(actor),
        entity_id=case.id,
        entity_label=case.title,
        context={"requirement_key": key, "link_type": link_type},
    )
    return link


async def remove_link(db: AsyncSession, case: ManagedTestCase, link_id: uuid.UUID, actor: Any) -> None:
    """Stage the removal of one of THIS case's links (another case's, another project's
    and an unknown id are one 404, no oracle) and its ``test_case.requirement_unlinked`` row."""
    link = (
        await db.execute(select(_L).where(_L.id == link_id, _L.test_case_id == case.id))
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail=REQUIREMENT_LINK_NOT_FOUND)
    _refuse_retired(case)
    key, link_type = link.requirement_key, link.link_type
    await db.delete(link)
    await db.flush()
    await record_activity(
        db,
        project_id=case.project_id,
        event_type="test_case.requirement_unlinked",
        actor=ActorRef.from_user(actor),
        entity_id=case.id,
        entity_label=case.title,
        context={"requirement_key": key, "link_type": link_type},
    )


# ── the project's known keys, measured ───────────────────────────────────────


def known_key_statements(project_id: Any) -> tuple[Any, Any, Any]:
    """The three bounded reads of a project's known keys: its links, its batches' claims, its chunks' ids."""
    live = _M.status.notin_(RETIRED_STATES)
    links_stmt = (
        select(
            _L.requirement_key_norm,
            func.min(_L.requirement_key),
            func.bool_or(and_(_L.link_type == "verifies", live)),
            func.bool_or(and_(_L.link_type == "partially_verifies", live)),
            func.count(func.distinct(_M.id)).filter(live),
        )
        .select_from(_L)
        .join(_M, _M.id == _L.test_case_id)
        .where(_L.project_id == project_id)
        .group_by(_L.requirement_key_norm)
        .limit(KNOWN_KEYS_CAP + 1)
    )
    claims = RequirementCoverage.covered_by_case_ids
    batch_stmt = (
        select(
            RequirementCoverage.requirement_id,
            func.sum(sql_case((func.json_typeof(claims) == "array", func.json_array_length(claims)), else_=0)),
        )
        .where(RequirementCoverage.project_id == project_id)
        .group_by(RequirementCoverage.requirement_id)
        .limit(KNOWN_KEYS_CAP + 1)
    )
    chunk_stmt = (
        select(KnowledgeChunk.requirement_id)
        .where(KnowledgeChunk.project_id == project_id, KnowledgeChunk.is_active.is_(True),
               KnowledgeChunk.requirement_id.isnot(None), KnowledgeChunk.requirement_id != "")
        .distinct()
        .limit(KNOWN_KEYS_CAP + 1)
    )
    return links_stmt, batch_stmt, chunk_stmt


async def _known_keys(db: AsyncSession, project_id: Any) -> tuple[dict[str, dict], bool]:
    """Every key the project knows, by normalised key (three bounded statements), and ``truncated``."""
    links_stmt, batch_stmt, chunk_stmt = known_key_statements(project_id)
    links = (await db.execute(links_stmt)).all()
    batches = (await db.execute(batch_stmt)).all()
    chunks = (await db.execute(chunk_stmt)).all()
    truncated = any(len(rows) > KNOWN_KEYS_CAP for rows in (links, batches, chunks))
    known: dict[str, dict] = {}

    def entry(normalized: str, key: str) -> dict:
        return known.setdefault(normalized, {
            "key": key, "normalized_key": normalized, "sources": set(), "linked_cases": 0,
            "batch_claimed_cases": 0, "verified": False, "partly": False,
        })

    for normalized, typed, verified, partly, linked in links[:KNOWN_KEYS_CAP]:
        item = entry(normalized, typed)
        item.update(key=typed, verified=bool(verified), partly=bool(partly), linked_cases=int(linked or 0))
        item["sources"].add("link")
    for raw, claimed in batches[:KNOWN_KEYS_CAP]:
        normalized = normalize_requirement_key(raw or "")
        if normalized:
            item = entry(normalized, raw.strip())
            item["sources"].add("generation_batch")
            item["batch_claimed_cases"] += int(claimed or 0)
    for (raw,) in chunks[:KNOWN_KEYS_CAP]:
        normalized = normalize_requirement_key(raw or "")
        if normalized:
            entry(normalized, raw.strip())["sources"].add("knowledge_source")
    return known, truncated


def _gap_reason(item: dict) -> Optional[str]:
    if item["verified"]:
        return None
    if item["partly"]:
        return "partly_verified"
    return "related_only" if item["linked_cases"] > 0 else "no_link"


def _gaps(known: dict[str, dict]) -> list[dict]:
    """The unverified keys: ``no_link``, ``related_only``, ``partly_verified``, then by key."""
    rank = {reason: index for index, reason in enumerate(REQUIREMENT_GAP_REASONS)}
    rows = []
    for item in known.values():
        reason = _gap_reason(item)
        if reason is None:
            continue
        rows.append({
            "key": item["key"], "normalized_key": item["normalized_key"], "reason": reason,
            "sources": [s for s in REQUIREMENT_KEY_SOURCES if s in item["sources"]],
            "linked_cases": item["linked_cases"], "batch_claimed_cases": item["batch_claimed_cases"],
        })
    rows.sort(key=lambda r: (rank[r["reason"]], r["normalized_key"]))
    return rows


def _summary(known: dict[str, dict], truncated: bool, gaps: list[dict], top_gaps: int) -> dict:
    verified = sum(1 for item in known.values() if item["verified"])
    partly = sum(1 for item in known.values() if not item["verified"] and item["partly"])
    measured = bool(known)
    return {
        "measured": measured, "known": len(known), "verified": verified, "partly_verified": partly,
        "gaps": len(known) - verified, "truncated": truncated,
        "reason": None if measured else REQUIREMENTS_UNMEASURED_SENTENCE, "top_gaps": gaps[:top_gaps],
    }


async def requirement_summary(db: AsyncSession, *, project_id: uuid.UUID, top_gaps: int = 5) -> dict:
    """A ``RequirementCoverageSummary`` body (three statements). Never writes."""
    known, truncated = await _known_keys(db, project_id)
    return _summary(known, truncated, _gaps(known), top_gaps)


async def traceability_gaps(db: AsyncSession, *, project_id: uuid.UUID, page: int, size: int) -> dict:
    """A ``TraceabilityGapsResponse`` body: the summary, a page of gaps, and the approved / active
    cases that link no requirement at all (oldest first, at most 25, with their total). Never writes."""
    known, truncated = await _known_keys(db, project_id)
    gaps = _gaps(known)
    no_link = ~exists(select(_L.id).where(_L.test_case_id == _M.id))
    stmt = (
        select(_M.id, _M.title, _M.status, _M.updated_at, User.full_name, User.username,
               func.count().over().label("total"))
        .select_from(_M)
        .outerjoin(User, User.id == func.coalesce(_M.assignee_id, _M.author_id))
        .where(_M.project_id == project_id, _M.status.in_(RUNNABLE_STATES), no_link)
        .order_by(_M.created_at.asc(), _M.id.asc())
        .limit(CASES_WITHOUT_REQUIREMENTS_PREVIEW)
    )
    rows = (await db.execute(stmt)).all()
    now = datetime.now(timezone.utc)
    cases = [
        {"id": r.id, "title": r.title, "status": r.status, "owner": _name(r.full_name, r.username),
         "age_days": max(0, (now - r.updated_at).days) if r.updated_at else None, "detail": None}
        for r in rows
    ]
    start = (page - 1) * size
    return {
        "project_id": project_id,
        "summary": _summary(known, truncated, gaps, 0),
        "gaps": gaps[start:start + size],
        "gaps_total": len(gaps),
        "page": page,
        "size": size,
        "cases_without_requirements_total": int(rows[0].total) if rows else 0,
        "cases_without_requirements": cases,
    }
