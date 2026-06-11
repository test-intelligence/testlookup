"""Phase 4 — duplicate-candidate review router (``app.routers.duplicates``).

Thin router over ``services.duplicate_detection_service``. These tests pin the
HTTP-layer behaviour the service tests don't cover:

  * Authorisation / IDOR: ``require_project_access()`` reads the PROVIDED
    ``project_id`` from the path and 403s a non-member, regardless of the
    candidate's own project — a caller can't reach another project's candidate.
  * Detection endpoint: stages via the service then OWNS the commit; passes the
    service envelope through as ``DuplicateDetectionRunResponse`` (the extra
    ``semantic_used`` key is ignored).
  * Dismiss: flips status to ``dismissed`` AND writes the suppression record;
    router commits.
  * Merge is NON-DESTRUCTIVE: status → ``merged``, the loser is only
    soft-deprecated (status flag), BOTH cases still exist; path/body
    candidate_id mismatch 400s; a bad keep_case_id 400s; a cross-project
    candidate 404s. Router commits.
  * List: filters by band/status, paginates, joins the two case refs, and
    reports total + open_count.

The router handlers are awaited directly with a content-dispatching fake async
session (mirrors ``test_summary_report_router`` / ``test_duplicate_detection``)
so no live Postgres is needed. The ``require_project_access`` IDOR test drives
the real dependency ``_check`` closure.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.routers import duplicates as router_mod  # noqa: E402
from app.models.postgres import (  # noqa: E402
    DismissedDuplicatePair,
    DuplicateTestCaseCandidate,
    ManagedTestCase,
)


# ─────────────────────────── Fakes ───────────────────────────


def _case(project_id, *, title="Login works", suite_name="auth", status="active", cid=None):
    return ManagedTestCase(
        id=cid or uuid.uuid4(),
        project_id=project_id,
        title=title,
        suite_name=suite_name,
        status=status,
    )


def _candidate(project_id, a, b, *, band="strong", score=0.9, status="open", method="structural", cid=None):
    ca, cb = (a, b) if a < b else (b, a)
    return DuplicateTestCaseCandidate(
        id=cid or uuid.uuid4(),
        project_id=project_id,
        case_a_id=ca,
        case_b_id=cb,
        band=band,
        score=score,
        reason="title 0.92, steps 0.88",
        method=method,
        component_scores={"title": 0.92, "steps": 0.88},
        status=status,
        detected_at=datetime.now(timezone.utc),
    )


class _Result:
    def __init__(self, *, scalars=None, scalar=None, _all=None):
        self._scalars = scalars
        self._scalar = scalar
        self._all = _all

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars or []))

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._all or [])


class _FakeSession:
    """Content-dispatching async session for the router's query surface.

    Routes the SELECTs the list endpoint issues (count, open_count, candidate
    page, case refs) and records get/add/commit/flush for the mutation paths.
    """

    def __init__(self, *, candidates=None, cases=None, gettable=None):
        self._candidates = list(candidates or [])
        self._cases = list(cases or [])
        self._gettable = dict(gettable or {})
        self.added = []
        self.committed = False
        self.flushed = False
        self._count_calls = 0

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "count(" in text:
            self._count_calls += 1
            # First count() in list endpoint = filtered total; second = open_count.
            if self._count_calls == 1:
                return _Result(scalar=len(self._candidates))
            open_n = len([c for c in self._candidates if c.status == "open"])
            return _Result(scalar=open_n)
        if "managed_test_cases" in text:
            return _Result(scalars=self._cases)
        if "duplicate_test_case_candidates" in text:
            return _Result(scalars=self._candidates)
        if "dismissed_duplicate_pairs" in text:
            return _Result(scalar=None)  # nothing suppressed yet
        return _Result(scalars=[])

    async def get(self, model, pk):
        return self._gettable.get(pk)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


# ─────────────────────── IDOR / authorization ───────────────────────


@pytest.mark.asyncio
async def test_require_project_access_rejects_non_member():
    """A non-admin who is not a member of the PROVIDED project_id is 403'd by the
    dependency that every duplicates route depends on — even though the candidate
    they target lives in a DIFFERENT project they own."""
    from app.core.deps import require_project_access
    from app.models.postgres import UserRole

    other_project = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.TESTER)

    class _MembershipDenyResult:
        def scalar_one_or_none(self):
            return None  # no ProjectMember row → not a member

    class _DB:
        async def execute(self, stmt):
            return _MembershipDenyResult()

    request = SimpleNamespace(path_params={"project_id": str(other_project)})
    check = require_project_access()

    with pytest.raises(HTTPException) as exc:
        await check(request=request, db=_DB(), current_user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_project_access_allows_member():
    from app.core.deps import require_project_access
    from app.models.postgres import UserRole

    project = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.TESTER)

    class _MembershipAllowResult:
        def scalar_one_or_none(self):
            return uuid.uuid4()  # a ProjectMember.id → is a member

    class _DB:
        async def execute(self, stmt):
            return _MembershipAllowResult()

    request = SimpleNamespace(path_params={"project_id": str(project)})
    check = require_project_access()
    result = await check(request=request, db=_DB(), current_user=user)
    assert result is user


# ─────────────────────────── detect ───────────────────────────


@pytest.mark.asyncio
async def test_detect_passes_envelope_and_commits(monkeypatch):
    project_id = uuid.uuid4()
    db = _FakeSession()

    async def _fake_detect(_db, _pid, *, enable_semantic=True):
        assert _pid == project_id
        return {
            "project_id": project_id,
            "candidates_created": 3,
            "candidates_total": 5,
            "cases_scanned": 12,
            "sampled": False,
            "note": None,
            "semantic_used": False,  # extra key — must be ignored by the model
        }

    monkeypatch.setattr(router_mod.dup_svc, "detect_duplicates_for_project", _fake_detect)

    resp = await router_mod.run_duplicate_detection(
        project_id=project_id, enable_semantic=True, db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert resp.project_id == project_id
    assert resp.candidates_created == 3
    assert resp.candidates_total == 5
    assert resp.cases_scanned == 12
    assert resp.sampled is False
    assert db.committed is True  # router owns the commit


@pytest.mark.asyncio
async def test_detect_surfaces_sampled_note(monkeypatch):
    project_id = uuid.uuid4()
    db = _FakeSession()

    async def _fake_detect(_db, _pid, *, enable_semantic=True):
        return {
            "project_id": project_id, "candidates_created": 0, "candidates_total": 0,
            "cases_scanned": 20000, "sampled": True,
            "note": "project capped at 20000 cases", "semantic_used": False,
        }

    monkeypatch.setattr(router_mod.dup_svc, "detect_duplicates_for_project", _fake_detect)
    resp = await router_mod.run_duplicate_detection(
        project_id=project_id, enable_semantic=False, db=db,
        current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert resp.sampled is True
    assert "capped" in (resp.note or "")


# ─────────────────────────── dismiss ───────────────────────────


@pytest.mark.asyncio
async def test_dismiss_flips_status_and_writes_suppression_and_commits():
    project_id = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    cand = _candidate(project_id, a, b, status="open")
    db = _FakeSession(candidates=[cand], gettable={cand.id: cand})
    user = SimpleNamespace(id=uuid.uuid4())

    resp = await router_mod.dismiss_duplicate_candidate(
        project_id=project_id, candidate_id=cand.id, db=db, current_user=user, _=None,
    )
    assert resp.status == "dismissed"
    assert cand.status == "dismissed"
    # A DismissedDuplicatePair suppression row was staged (service did db.add).
    assert any(isinstance(o, DismissedDuplicatePair) for o in db.added)
    supp = next(o for o in db.added if isinstance(o, DismissedDuplicatePair))
    assert supp.project_id == project_id
    assert (supp.case_a_id, supp.case_b_id) == (cand.case_a_id, cand.case_b_id)
    assert db.committed is True


@pytest.mark.asyncio
async def test_dismiss_cross_project_candidate_404s():
    project_id = uuid.uuid4()
    foreign = _candidate(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())  # belongs to ANOTHER project
    db = _FakeSession(gettable={foreign.id: foreign})

    with pytest.raises(HTTPException) as exc:
        await router_mod.dismiss_duplicate_candidate(
            project_id=project_id, candidate_id=foreign.id, db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
        )
    assert exc.value.status_code == 404
    assert db.committed is False


# ─────────────────────────── merge ───────────────────────────


@pytest.mark.asyncio
async def test_merge_is_non_destructive_and_deprecates_loser():
    project_id = uuid.uuid4()
    keep = _case(project_id, title="Login works", cid=uuid.uuid4())
    loser = _case(project_id, title="Login functions", cid=uuid.uuid4())
    cand = _candidate(project_id, keep.id, loser.id, status="open")
    db = _FakeSession(gettable={cand.id: cand, keep.id: keep, loser.id: loser})

    from app.models.schemas import DuplicateMergeRequest

    resp = await router_mod.merge_duplicate_candidate(
        project_id=project_id, candidate_id=cand.id,
        payload=DuplicateMergeRequest(candidate_id=cand.id, keep_case_id=keep.id, deprecate_loser=True),
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert resp.status == "merged"
    assert resp.deprecated_case_id == loser.id
    # NON-DESTRUCTIVE: both cases still exist; the loser is only soft-deprecated.
    assert keep.status == "active"
    assert loser.status == "deprecated"
    assert loser.is_stale is True
    assert db._gettable[keep.id] is keep  # not deleted
    assert db._gettable[loser.id] is loser  # not deleted
    assert db.committed is True


@pytest.mark.asyncio
async def test_merge_without_deprecate_keeps_loser_active():
    project_id = uuid.uuid4()
    keep = _case(project_id, cid=uuid.uuid4())
    loser = _case(project_id, cid=uuid.uuid4())
    cand = _candidate(project_id, keep.id, loser.id, status="open")
    db = _FakeSession(gettable={cand.id: cand, keep.id: keep, loser.id: loser})

    from app.models.schemas import DuplicateMergeRequest

    resp = await router_mod.merge_duplicate_candidate(
        project_id=project_id, candidate_id=cand.id,
        payload=DuplicateMergeRequest(candidate_id=cand.id, keep_case_id=keep.id, deprecate_loser=False),
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert resp.status == "merged"
    assert resp.deprecated_case_id is None
    assert loser.status == "active"  # untouched


@pytest.mark.asyncio
async def test_merge_body_path_mismatch_400s():
    project_id = uuid.uuid4()
    cand_id = uuid.uuid4()
    db = _FakeSession()
    from app.models.schemas import DuplicateMergeRequest

    with pytest.raises(HTTPException) as exc:
        await router_mod.merge_duplicate_candidate(
            project_id=project_id, candidate_id=cand_id,
            payload=DuplicateMergeRequest(
                candidate_id=uuid.uuid4(), keep_case_id=uuid.uuid4()
            ),  # body id != path id
            db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
        )
    assert exc.value.status_code == 400
    assert db.committed is False


@pytest.mark.asyncio
async def test_merge_bad_keep_case_id_400s():
    project_id = uuid.uuid4()
    keep = _case(project_id, cid=uuid.uuid4())
    loser = _case(project_id, cid=uuid.uuid4())
    cand = _candidate(project_id, keep.id, loser.id, status="open")
    db = _FakeSession(gettable={cand.id: cand, keep.id: keep, loser.id: loser})
    from app.models.schemas import DuplicateMergeRequest

    with pytest.raises(HTTPException) as exc:
        await router_mod.merge_duplicate_candidate(
            project_id=project_id, candidate_id=cand.id,
            payload=DuplicateMergeRequest(
                candidate_id=cand.id, keep_case_id=uuid.uuid4()  # not in the pair
            ),
            db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
        )
    assert exc.value.status_code == 400
    assert db.committed is False


@pytest.mark.asyncio
async def test_merge_cross_project_candidate_404s():
    project_id = uuid.uuid4()
    foreign = _candidate(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    db = _FakeSession(gettable={foreign.id: foreign})
    from app.models.schemas import DuplicateMergeRequest

    with pytest.raises(HTTPException) as exc:
        await router_mod.merge_duplicate_candidate(
            project_id=project_id, candidate_id=foreign.id,
            payload=DuplicateMergeRequest(
                candidate_id=foreign.id, keep_case_id=foreign.case_a_id
            ),
            db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
        )
    assert exc.value.status_code == 404


# ─────────────────────────── list ───────────────────────────


@pytest.mark.asyncio
async def test_list_joins_case_refs_and_counts():
    project_id = uuid.uuid4()
    keep = _case(project_id, title="Login works", suite_name="auth", cid=uuid.uuid4())
    loser = _case(project_id, title="Login functions", suite_name="auth", cid=uuid.uuid4())
    cand = _candidate(project_id, keep.id, loser.id, band="strong", score=0.91, status="open")
    db = _FakeSession(candidates=[cand], cases=[keep, loser])

    resp = await router_mod.list_duplicate_candidates(
        project_id=project_id, band=None, status_filter="open", page=1, size=50,
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert resp.total == 1
    assert resp.open_count == 1
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.band == "strong"
    assert item.score == 0.91
    assert item.reason == "title 0.92, steps 0.88"
    assert {item.case_a.title, item.case_b.title} == {"Login works", "Login functions"}
    assert item.case_a.suite_name == "auth"


@pytest.mark.asyncio
async def test_list_missing_case_ref_is_tolerated():
    """If a referenced case row vanished, the ref falls back to id + placeholder
    rather than 500-ing the whole list."""
    project_id = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    cand = _candidate(project_id, a, b)
    db = _FakeSession(candidates=[cand], cases=[])  # no case rows resolvable

    resp = await router_mod.list_duplicate_candidates(
        project_id=project_id, band=None, status_filter="open", page=1, size=50,
        db=db, current_user=SimpleNamespace(id=uuid.uuid4()), _=None,
    )
    assert len(resp.items) == 1
    assert resp.items[0].case_a.title == "(deleted)"
    assert resp.items[0].case_b.title == "(deleted)"
