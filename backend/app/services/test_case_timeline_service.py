"""One timeline per authored test case (W13 TCL-05).

Eight kinds from seven tables, newest first, keyset-paged:
``(occurred_at DESC, kind ASC by TIMELINE_KINDS rank, id DESC)``. The cursor
names the last entry ``(t, k, i)`` AND the case; each source of kind ``K``
reads ``LIMIT limit + 1`` rows strictly after it (``K`` ranked after ``k``:
``ts <= t``; ``K == k``: ``(ts, id) < (t, i)``; ``K`` before ``k``: ``ts < t``),
and the union is merged in Python with the same key (``uuid.UUID`` order is
PostgreSQL's ``uuid`` order). One bounded statement per requested kind, plus
one for the automation identity when an automation kind is asked.

Every source is the case's own: the version / review / comment rows by its id;
plan executions through plans of the case's project; automated runs and
defects through its automation identity INSIDE its project (fingerprints are
not project-salted; ``test_case_history`` has no project column and is never
read); requirement events from the case's project ledger. Commit-free and
read-only.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import datetime
from typing import Any, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import and_, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.test_library_vocab import (
    TIMELINE_CURSOR_INVALID,
    TIMELINE_DETAIL_CHARS,
    TIMELINE_KINDS,
    timeline_title,
)
from app.models.postgres import (
    Defect,
    ManagedTestCase,
    ProjectActivityEvent,
    TestCase,
    TestCaseAuditLog,
    TestCaseComment,
    TestCaseReview,
    TestCaseVersion,
    TestPlan,
    TestPlanItem,
    TestRun,
    User,
)
from app.services.case_automation_identity import automation_fingerprint

AUTOMATION_KINDS = frozenset({"automated_execution", "defect"})
REQUIREMENT_EVENTS = ("test_case.requirement_linked", "test_case.requirement_unlinked")
_RANK = {kind: index for index, kind in enumerate(TIMELINE_KINDS)}
_SUFFIXED = {"review_requested": ":requested", "review_decided": ":decided"}
_RESTORED_FROM = re.compile(r"^Restored from v(\d+)")


# ── the cursor ────────────────────────────────────────────────────────────────


def _row_uuid(kind: str, entry_id: str) -> uuid.UUID:
    """The source row's uuid behind an entry id (review ids carry a suffix)."""
    suffix = _SUFFIXED.get(kind)
    if suffix is not None:
        if not entry_id.endswith(suffix):
            raise ValueError("review entry id without its suffix")
        entry_id = entry_id[: -len(suffix)]
    return uuid.UUID(entry_id)


def encode_cursor(occurred_at: datetime, kind: str, entry_id: str, case_id: uuid.UUID) -> str:
    """Urlsafe base64 of JSON ``{t, k, i, c}`` (no padding)."""
    raw = json.dumps({"t": occurred_at.isoformat(), "k": kind, "i": entry_id, "c": str(case_id)},
                     separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, case_id: uuid.UUID) -> tuple[datetime, str, str]:
    """``(t, k, i)`` of a cursor issued for THIS case; anything else is 422 ``TIMELINE_CURSOR_INVALID``."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(data, dict) or str(data.get("c")) != str(case_id):
            raise ValueError("cursor of another case")
        kind, entry_id = data["k"], data["i"]
        if kind not in _RANK or not isinstance(entry_id, str):
            raise ValueError("cursor kind")
        _row_uuid(kind, entry_id)
        occurred_at = datetime.fromisoformat(data["t"])
        if occurred_at.tzinfo is None:
            raise ValueError("naive cursor time")
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error):
        raise HTTPException(status_code=422, detail=TIMELINE_CURSOR_INVALID) from None
    return occurred_at, kind, entry_id


def _after(kind: str, ts_col: Any, id_col: Any, cursor: Optional[tuple[datetime, str, str]]) -> list[Any]:
    """The keyset predicate of a source of ``kind`` after the cursor (nothing without one)."""
    if cursor is None:
        return []
    t, k, i = cursor
    if _RANK[kind] > _RANK[k]:
        return [ts_col <= t]
    if kind == k:
        return [tuple_(ts_col, id_col) < tuple_(literal(t, ts_col.type), literal(_row_uuid(k, i), id_col.type))]
    return [ts_col < t]


def merge_entries(entries: Sequence[dict], limit: int) -> tuple[list[dict], bool]:
    """Sort the union by ``(occurred_at DESC, rank ASC, id DESC)``; keep ``limit``; say if any was left."""
    ordered = sorted(
        entries,
        key=lambda e: (e["occurred_at"], -_RANK[e["kind"]], _row_uuid(e["kind"], e["id"])),
        reverse=True,
    )
    return ordered[:limit], len(ordered) > limit


def _cut(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= TIMELINE_DETAIL_CHARS else text[: TIMELINE_DETAIL_CHARS - 1] + "…"


def _name(full_name: Optional[str], username: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
    return (full_name or "").strip() or (username or "").strip() or (fallback or "").strip() or None


def _entry(kind: str, entry_id: str, occurred_at: datetime, *, title: str, actor_id: Any = None,
           actor_name: Optional[str] = None, actor_source: Optional[str] = None, detail: Optional[str] = None,
           status: Optional[str] = None, version: Optional[int] = None, link: Optional[dict] = None) -> dict:
    return {"id": entry_id, "kind": kind, "occurred_at": occurred_at, "actor_id": actor_id,
            "actor_name": actor_name, "actor_source": actor_source, "title": title, "detail": _cut(detail),
            "status": status, "version": version, "link": link}


# ── the sources (one bounded statement each) ──────────────────────────────────


async def _versions(db: AsyncSession, case: ManagedTestCase, cursor: Any, n: int) -> list[dict]:
    v = TestCaseVersion
    stmt = (
        select(v.id, v.created_at, v.version, v.status, v.change_type, v.change_summary, v.source,
               v.changed_by_id, User.full_name, User.username)
        .outerjoin(User, User.id == v.changed_by_id)
        .where(v.test_case_id == case.id, *_after("version", v.created_at, v.id, cursor))
        .order_by(v.created_at.desc(), v.id.desc())
        .limit(n)
    )
    out = []
    for r in (await db.execute(stmt)).all():
        source = r.source or ("ai" if case.ai_generated and r.version == 1 else None)
        restored = _RESTORED_FROM.match(r.change_summary or "") if r.change_type == "restored" else None
        title = timeline_title("version", change_type=r.change_type, version=r.version, status=r.status,
                               restored_from=int(restored.group(1)) if restored else None, source=source)
        out.append(_entry("version", str(r.id), r.created_at, title=title, actor_id=r.changed_by_id,
                          actor_name=_name(r.full_name, r.username), actor_source=source,
                          detail=r.change_summary, status=r.status, version=r.version))
    return out


async def _reviews_requested(db: AsyncSession, case: ManagedTestCase, cursor: Any, n: int) -> list[dict]:
    r_ = TestCaseReview
    stmt = (
        select(r_.id, r_.created_at, r_.requested_by_id, User.full_name, User.username)
        .outerjoin(User, User.id == r_.requested_by_id)
        .where(r_.test_case_id == case.id, *_after("review_requested", r_.created_at, r_.id, cursor))
        .order_by(r_.created_at.desc(), r_.id.desc())
        .limit(n)
    )
    return [
        _entry("review_requested", f"{r.id}:requested", r.created_at, title=timeline_title("review_requested"),
               actor_id=r.requested_by_id, actor_name=_name(r.full_name, r.username))
        for r in (await db.execute(stmt)).all()
    ]


async def _reviews_decided(db: AsyncSession, case: ManagedTestCase, cursor: Any, n: int) -> list[dict]:
    r_ = TestCaseReview
    stmt = (
        select(r_.id, r_.reviewed_at, r_.status, r_.human_notes, r_.reviewer_id, User.full_name, User.username)
        .outerjoin(User, User.id == r_.reviewer_id)
        .where(r_.test_case_id == case.id, r_.reviewed_at.isnot(None),
               *_after("review_decided", r_.reviewed_at, r_.id, cursor))
        .order_by(r_.reviewed_at.desc(), r_.id.desc())
        .limit(n)
    )
    return [
        _entry("review_decided", f"{r.id}:decided", r.reviewed_at,
               title=timeline_title("review_decided", status=r.status), actor_id=r.reviewer_id,
               actor_name=_name(r.full_name, r.username), detail=r.human_notes, status=r.status)
        for r in (await db.execute(stmt)).all()
    ]


async def _comments(db: AsyncSession, case: ManagedTestCase, cursor: Any, n: int) -> list[dict]:
    c = TestCaseComment
    stmt = (
        select(c.id, c.created_at, c.content, c.comment_type, c.author_id, User.full_name, User.username)
        .outerjoin(User, User.id == c.author_id)
        .where(c.test_case_id == case.id, *_after("comment", c.created_at, c.id, cursor))
        .order_by(c.created_at.desc(), c.id.desc())
        .limit(n)
    )
    return [
        _entry("comment", str(r.id), r.created_at, title=timeline_title("comment", comment_type=r.comment_type),
               actor_id=r.author_id, actor_name=_name(r.full_name, r.username), detail=r.content)
        for r in (await db.execute(stmt)).all()
    ]


def _same_instant(recorded: Any, executed_at: Optional[datetime]) -> bool:
    """Whether an audit row's ``new_values.executed_at`` is the item's latest execution."""
    if executed_at is None or not isinstance(recorded, str):
        return False
    try:
        return datetime.fromisoformat(recorded) == executed_at
    except ValueError:
        return False


async def _plan_executions(db: AsyncSession, case: ManagedTestCase, cursor: Any, n: int) -> list[dict]:
    a = TestCaseAuditLog
    stmt = (
        select(a.id, a.created_at, a.actor_id, a.actor_name, a.new_values, TestPlan.id.label("plan_id"),
               TestPlan.name.label("plan_name"), TestPlanItem.executed_at, TestPlanItem.executed_case_version,
               User.full_name, User.username)
        .join(TestPlanItem, and_(TestPlanItem.id == a.entity_id, TestPlanItem.test_case_id == case.id))
        .join(TestPlan, and_(TestPlan.id == TestPlanItem.plan_id, TestPlan.project_id == case.project_id))
        .outerjoin(User, User.id == a.actor_id)
        .where(a.entity_type == "test_plan_item", a.action == "executed", a.project_id == case.project_id,
               *_after("plan_execution", a.created_at, a.id, cursor))
        .order_by(a.created_at.desc(), a.id.desc())
        .limit(n)
    )
    out = []
    for r in (await db.execute(stmt)).all():
        values = r.new_values if isinstance(r.new_values, dict) else {}
        status = values.get("execution_status")
        latest = _same_instant(values.get("executed_at"), r.executed_at)
        version = r.executed_case_version if latest else None
        out.append(_entry(
            "plan_execution", str(r.id), r.created_at,
            title=timeline_title("plan_execution", plan_name=r.plan_name, status=status, version=version),
            actor_id=r.actor_id, actor_name=_name(r.full_name, r.username, r.actor_name),
            detail=values.get("execution_notes"), status=status, version=version,
            link={"type": "plan", "plan_id": r.plan_id},
        ))
    return out


async def _automated_runs(db: AsyncSession, case: ManagedTestCase, fingerprint: str, cursor: Any,
                          n: int) -> list[dict]:
    stmt = (
        select(TestCase.id, TestCase.status, TestRun.id.label("run_id"), TestRun.created_at,
               TestRun.build_number, TestRun.branch)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.test_fingerprint == fingerprint, TestRun.project_id == case.project_id,
               *_after("automated_execution", TestRun.created_at, TestCase.id, cursor))
        .order_by(TestRun.created_at.desc(), TestCase.id.desc())
        .limit(n)
    )
    out = []
    for r in (await db.execute(stmt)).all():
        status = str(getattr(r.status, "value", r.status) or "") or None
        detail = f"Build {r.build_number}" + (f" · {r.branch}" if r.branch else "")
        out.append(_entry(
            "automated_execution", str(r.id), r.created_at,
            title=timeline_title("automated_execution", status=status), actor_source="automation",
            detail=detail, status=status,
            link={"type": "run_result", "run_id": r.run_id, "test_case_id": r.id},
        ))
    return out


async def _defects(db: AsyncSession, case: ManagedTestCase, fingerprint: str, cursor: Any, n: int) -> list[dict]:
    stmt = (
        select(Defect.id, Defect.created_at, Defect.jira_ticket_id, Defect.resolution_status, Defect.title,
               TestCase.id.label("result_id"), TestCase.test_run_id)
        .join(TestCase, TestCase.id == Defect.test_case_id)
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(Defect.project_id == case.project_id, TestRun.project_id == case.project_id,
               TestCase.test_fingerprint == fingerprint,
               *_after("defect", Defect.created_at, Defect.id, cursor))
        .order_by(Defect.created_at.desc(), Defect.id.desc())
        .limit(n)
    )
    return [
        _entry("defect", str(r.id), r.created_at, title=timeline_title("defect", jira_key=r.jira_ticket_id),
               detail=r.title, status=r.resolution_status,
               link={"type": "defect", "defect_id": r.id, "jira_key": r.jira_ticket_id,
                     "run_id": r.test_run_id, "test_case_id": r.result_id})
        for r in (await db.execute(stmt)).all()
    ]


async def _requirement_events(db: AsyncSession, case: ManagedTestCase, cursor: Any, n: int) -> list[dict]:
    e = ProjectActivityEvent
    stmt = (
        select(e.id, e.occurred_at, e.event_type, e.actor_id, e.actor_name, e.context)
        .where(e.project_id == case.project_id, e.entity_type == "test_case", e.entity_id == str(case.id),
               e.event_type.in_(REQUIREMENT_EVENTS), *_after("requirement", e.occurred_at, e.id, cursor))
        .order_by(e.occurred_at.desc(), e.id.desc())
        .limit(n)
    )
    out = []
    for r in (await db.execute(stmt)).all():
        context = r.context if isinstance(r.context, dict) else {}
        key, link_type = str(context.get("requirement_key") or ""), context.get("link_type")
        linked = r.event_type == REQUIREMENT_EVENTS[0]
        out.append(_entry(
            "requirement", str(r.id), r.occurred_at,
            title=timeline_title("requirement", requirement_key=key, link_type=link_type, linked=linked),
            actor_id=r.actor_id, actor_name=_name(None, None, r.actor_name),
            status=str(link_type) if link_type else None,
        ))
    return out


_CASE_SOURCES = {
    "version": _versions, "review_requested": _reviews_requested, "review_decided": _reviews_decided,
    "comment": _comments, "plan_execution": _plan_executions, "requirement": _requirement_events,
}
_AUTOMATION_SOURCES = {"automated_execution": _automated_runs, "defect": _defects}


async def case_timeline(db: AsyncSession, case: ManagedTestCase, *, kinds: Sequence[str], cursor: Optional[str],
                        limit: int) -> dict:
    """A ``TestCaseTimelineResponse`` body: one bounded statement per requested kind, merged in Python."""
    wanted = [k for k in TIMELINE_KINDS if k in set(kinds)] if kinds else list(TIMELINE_KINDS)
    after = decode_cursor(cursor, case.id) if cursor else None
    n = limit + 1
    fingerprint: Optional[str] = None
    if AUTOMATION_KINDS & set(wanted):
        fingerprint = await automation_fingerprint(db, case)
    entries: list[dict] = []
    for kind in wanted:
        if kind in _CASE_SOURCES:
            entries += await _CASE_SOURCES[kind](db, case, after, n)
        elif fingerprint is not None:
            entries += await _AUTOMATION_SOURCES[kind](db, case, fingerprint, after, n)
    kept, more = merge_entries(entries, limit)
    last = kept[-1] if kept else None
    return {
        "case_id": case.id,
        "kinds": wanted,
        "items": kept,
        "next_cursor": encode_cursor(last["occurred_at"], last["kind"], last["id"], case.id) if more and last else None,
        "automation_linked": fingerprint is not None,
    }
