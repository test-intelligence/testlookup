"""D-0018 (GPT-6): one anonymous report-share view appeared twice in the audit dashboard's "All categories" feed.

``query_unified_audit`` read every ``AccessAuditLog`` row as an **access** event and the ``report_*`` rows again as
**report** events, so under All each report row came back twice and ``total`` (the list length) counted it twice.
Each stored row is now in exactly one category (owner decision Q2): an action starting with the literal
``report_`` is a report event, any other an access event; All is their disjoint union, and ``total`` is the real
number of matching rows, with complete pages. The report branch honours the actor filter and redacts, like access.

Drives the real ``query_unified_audit`` against the SQLite world of ``test_subject_erasure_service.py``. The
Postgres twin (production LIKE semantics) is ``tests/integration/test_audit_unified_feed_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.postgres import Base
from app.services.audit_dashboard_service import query_unified_audit
from tests.test_subject_erasure_service import maker as _sqlite_maker

pytestmark = pytest.mark.regression

maker = pytest.fixture(name="maker")(_sqlite_maker.__wrapped__)  # type: ignore[attr-defined]


@pytest.fixture
async def ledger(maker: Any) -> SimpleNamespace:
    """Member project P and non-member Q. In P, now: one anonymous share view and one membership change by A.
    Controls: a share view in Q, and one in P 15 days old."""
    assert "access_audit_logs" in maker.created
    now = datetime.now(timezone.utc)
    w = SimpleNamespace(P=uuid.uuid4(), Q=uuid.uuid4(), A=uuid.uuid4(), B=uuid.uuid4(), run=uuid.uuid4(), now=now)
    w.rows = []

    async def add(action, *, project=None, actor=None, actor_name="Ann Example", at=None, after=None):
        values = {
            "id": uuid.uuid4(), "action": action, "project_id": project or w.P, "actor_user_id": actor,
            "actor_name": actor_name, "after_value": after, "created_at": at or now,
        }
        async with maker.session() as db:
            await db.execute(Base.metadata.tables["access_audit_logs"].insert().values(**values))
            await db.commit()
        return values["id"]

    w.add = add
    await add("report_share_accessed", actor_name="anonymous_share",
              after={"run_id": str(w.run), "token_hint": "abcd1234..."}, at=now - timedelta(minutes=5))
    await add("membership_added", actor=w.A, at=now - timedelta(minutes=4))
    await add("report_share_accessed", project=w.Q, actor_name="anonymous_share", at=now - timedelta(minutes=3))
    await add("report_share_accessed", actor_name="anonymous_share", at=now - timedelta(days=15))
    return w


async def _feed(maker: Any, w: SimpleNamespace, **kw: Any) -> dict:
    kw.setdefault("allowed_project_ids", {w.P})
    kw.setdefault("days", 14)
    kw.setdefault("page_size", 50)
    async with maker.session() as db:
        return await query_unified_audit(db, **kw)


def _actions(answer: dict) -> list[tuple[str, str]]:
    return [(item["action"], item["source"]) for item in answer["items"]]


async def test_all_lists_a_share_view_once_and_counts_each_event_once(maker, ledger):
    w = ledger
    everything = await _feed(maker, w)
    assert _actions(everything) == [("membership_added", "access"), ("report_share_accessed", "report")]
    assert everything["total"] == 2

    report = await _feed(maker, w, category="report")
    assert _actions(report) == [("report_share_accessed", "report")] and report["total"] == 1
    access = await _feed(maker, w, category="access")
    assert _actions(access) == [("membership_added", "access")] and access["total"] == 1
    # The categories add up to All, and asking again changes nothing.
    assert report["total"] + access["total"] == everything["total"]
    assert await _feed(maker, w) == everything


async def test_two_views_are_two_events(maker, ledger):
    """The fix partitions; it does not merge look-alike rows."""
    w = ledger
    await w.add("report_share_accessed", actor_name="anonymous_share", at=w.now - timedelta(minutes=2))
    everything = await _feed(maker, w)
    assert [a for a, _ in _actions(everything)].count("report_share_accessed") == 2
    assert everything["total"] == 3


async def test_the_report_category_honours_the_actor_filter_and_redacts(maker, ledger):
    w = ledger
    await w.add("report_export_pdf", actor=w.B, actor_name="Bea Example", after={"token": "s3cr3t"})
    for category in (None, "report"):
        mine = await _feed(maker, w, category=category, actor_id=w.A)
        assert "report_export_pdf" not in [a for a, _ in _actions(mine)]
    theirs = await _feed(maker, w, category="report", actor_id=w.B)
    assert _actions(theirs) == [("report_export_pdf", "report")]
    assert theirs["items"][0]["detail"] == {"token": "[REDACTED]"}


async def test_the_report_prefix_is_literal(maker, ledger):
    """``report_`` is a prefix, not ``report`` plus any one character."""
    w = ledger
    await w.add("reportx_probe", actor=w.A)
    assert "reportx_probe" in [a for a, _ in _actions(await _feed(maker, w, category="access"))]
    assert "reportx_probe" not in [a for a, _ in _actions(await _feed(maker, w, category="report"))]


async def test_total_is_every_matching_row_and_pages_are_complete(maker, ledger):
    w = ledger
    for minute in range(10, 13):
        await w.add("role_changed", actor=w.A, at=w.now - timedelta(minutes=minute))
    for minute in range(20, 22):
        await w.add("report_share_created", actor=w.A, at=w.now - timedelta(minutes=minute))
    # 2 from the fixture + 3 access + 2 report = 7 events, two per page.
    pages = [await _feed(maker, w, page=page, page_size=2) for page in (1, 2, 3, 4, 5)]
    assert {answer["total"] for answer in pages} == {7}
    seen = [item["created_at"] + item["action"] for answer in pages for item in answer["items"]]
    assert len(seen) == 7 and len(set(seen)) == 7
    assert pages[4]["items"] == []
