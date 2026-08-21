"""Regression guard: /rag/status counts only the caller's projects.

The defect (RAG-STATUS-001)
---------------------------
``get_rag_status`` ran three unscoped ``COUNT(*)`` queries::

    select(func.count(KnowledgeSource.id)).where(is_archived.is_(False))
    select(func.count(GenerationBatch.id))
    select(func.count(KnowledgeChunk.id)).where(is_active.is_(True))

The endpoint carries no role guard and no project guard — only
``get_current_active_user`` — so **any** authenticated user read the totals for
the entire install. Observed live on the deployment: a caller scoped to nothing
in particular got ``total_batches: 2`` while its own sources and chunks were 0.

Two problems, one fix. The cross-tenant disclosure, and three full-table counts
on every call — this endpoint is polled by ``KnowledgeGenerationTab``, which
(worth noting) only ever reads ``enabled``; the counts are declared in the TS
type and rendered nowhere.

``backend/CLAUDE.md`` states the invariant this broke: "``test_fingerprint``
queries need project scope; ChromaDB collections are per-project;
content-addressable caches are tenant-scoped."

The empty-set trap
------------------
``get_accessible_project_ids`` returns ``None`` for ADMIN (unrestricted) and a
set otherwise. A user who is a member of nothing yields an **empty set**, which
is falsy — so ``if accessible_project_ids:`` would skip the filter and hand
that user the whole install's totals, which is the very bug being fixed but
reachable by the least privileged account. The distinction is pinned below.
"""
from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.regression


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _RecordingDB:
    """Captures each compiled COUNT statement so the WHERE can be asserted.

    It also honours an impossible predicate by returning 0. A mock that
    returned the same number whatever the WHERE said would make the
    empty-scope assertion untestable — the value could never fall to zero, so
    the test would be asserting against the mock rather than the code.
    """

    #: How ``sqlalchemy.false()`` renders as a standalone WHERE term. Anchored
    #: on the preceding keyword on purpose: a bare "false" substring also
    #: matches ``is_archived IS false``, which is an ordinary predicate on a
    #: perfectly normal query, and treating that as impossible made the ADMIN
    #: case return 0 and the test fail for entirely the wrong reason.
    _IMPOSSIBLE = re.compile(r"\b(where|and)\s+(false\b|0\s*=\s*1)", re.I)

    def __init__(self, value=7):
        self.statements: list[str] = []
        self._value = value

    async def execute(self, stmt):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        self.statements.append(sql)
        if self._IMPOSSIBLE.search(sql):
            return _Result(0)
        return _Result(self._value)


async def _call(monkeypatch, scope):
    from app.services import rag_eval_service

    async def _fake_is_enabled(*_a, **_kw):
        return True

    monkeypatch.setattr(
        "app.services.feature_flags.is_enabled", _fake_is_enabled, raising=False
    )
    db = _RecordingDB()
    out = await rag_eval_service.get_rag_status(db, accessible_project_ids=scope)
    return db, out


@pytest.mark.asyncio
async def test_admin_scope_none_counts_everything(monkeypatch):
    """``None`` means ADMIN and must NOT filter by project."""
    db, out = await _call(monkeypatch, None)
    assert len(db.statements) == 3, f"expected 3 counts, got {len(db.statements)}"
    for sql in db.statements:
        assert "project_id IN" not in sql, (
            "ADMIN scope must not be filtered by project:\n" + sql
        )
    assert out["total_sources"] == 7


@pytest.mark.asyncio
async def test_member_scope_filters_every_count(monkeypatch):
    """A membership set must constrain ALL THREE counts, not just one."""
    scope = {uuid.uuid4(), uuid.uuid4()}
    db, _ = await _call(monkeypatch, scope)
    assert len(db.statements) == 3
    unfiltered = [s for s in db.statements if "project_id IN" not in s]
    assert not unfiltered, (
        "these counts are still unscoped, so they leak other tenants' totals:\n"
        + "\n".join(unfiltered)
    )


@pytest.mark.asyncio
async def test_empty_scope_counts_nothing_rather_than_everything(monkeypatch):
    """A user who is a member of NO project must see zeros.

    An empty set is falsy. A truthiness check would skip the filter here and
    hand the least privileged caller the entire install's totals.
    """
    db, out = await _call(monkeypatch, set())
    assert len(db.statements) == 3
    for sql in db.statements:
        assert "project_id IN" in sql or "false" in sql.lower(), (
            "empty scope must produce an impossible predicate, not an "
            "unfiltered count:\n" + sql
        )
    assert out["total_sources"] == 0, (
        "a member of nothing must count zero; got "
        f"{out['total_sources']} — the empty set was treated as 'no filter'."
    )


def test_the_router_passes_the_callers_scope():
    """The service can only scope what the router hands it.

    Fixing the service while the router keeps calling ``get_rag_status(db)``
    would leave the endpoint exactly as leaky as before, and every unit test
    above would still pass.
    """
    import inspect

    from app.routers import rag_generation

    src = inspect.getsource(rag_generation.rag_status)
    assert "get_accessible_project_ids" in src, (
        "the /rag/status route no longer resolves the caller's project scope"
    )
    assert "accessible_project_ids=" in src, (
        "the route resolves a scope but does not pass it to get_rag_status"
    )


@pytest.mark.asyncio
async def test_guard_actually_reads_the_count_sql(monkeypatch):
    """Fail-open: the assertions above search compiled SQL strings.

    If the recorder captured blank text — a compile failure swallowed, say —
    every ``"project_id IN" not in sql`` check would pass for the wrong reason
    and the guard would be inert. Pin that the captured text is real SQL naming
    the three tables it is supposed to be counting.
    """
    db, _ = await _call(monkeypatch, None)
    joined = " ".join(db.statements).lower()
    assert "count(" in joined, f"captured text is not COUNT SQL: {db.statements}"
    for table in ("knowledge_sources", "generation_batches", "knowledge_chunks"):
        assert table in joined, (
            f"{table} is missing from the captured SQL, so a scoping assertion "
            f"about it would be vacuous. Captured: {db.statements}"
        )
