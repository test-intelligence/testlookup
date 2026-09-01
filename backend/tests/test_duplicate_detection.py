"""Phase 4 — duplicate authored-test-case detection (offline-first, per-project).

Covers ``services.duplicate_detection_service``:

  * Tier 0 fingerprint normalisation + exact match (whitespace/case/punctuation
    invariant; equal fingerprint → band ``exact`` score ``1.0`` method
    ``fingerprint``).
  * Tier 1 structural scoring: a near-duplicate pair lands in a strong/possible
    band while a clearly-distinct pair is dropped (None band).
  * Blocking limits comparisons: cases that share NO suite/tag/rare-title-token
    are never compared (no candidate emitted) even when textually unrelated.
  * Per-project scoping: a same-content case in another project never pairs
    cross-project (the loader filters on ``project_id``).
  * Dismissed-pair suppression: a pair in ``dismissed_duplicate_pairs`` is never
    re-staged on a subsequent detection run.
  * Tier 2 offline-skip: when the local embedder/ChromaDB is unavailable the run
    completes structural-only (no raise, ``semantic_used`` False).

The pure tiers (fingerprint / structural / blocking) are exercised directly. The
DB-touching orchestrator uses a small content-dispatching fake async session
that serves exactly the SELECT / get surface the service touches, project-scoped
so the cross-project test is meaningful.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.services import duplicate_detection_service as svc  # noqa: E402
from app.models.postgres import (  # noqa: E402
    DismissedDuplicatePair,
    DuplicateTestCaseCandidate,
)


# ─────────────────────────── Helpers ───────────────────────────


def _case(
    *,
    project_id,
    title,
    objective=None,
    expected_result=None,
    description=None,
    steps=None,
    suite_name=None,
    tags=None,
    status="active",
    cid=None,
):
    return SimpleNamespace(
        id=cid or uuid.uuid4(),
        project_id=project_id,
        title=title,
        objective=objective,
        expected_result=expected_result,
        description=description,
        steps=steps,
        suite_name=suite_name,
        tags=tags,
        status=status,
        dup_fingerprint=None,
        is_stale=False,
        stale_reason=None,
    )


# ─────────────────────────── Fake async DB ───────────────────────────


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _ExecResult:
    def __init__(self, rows, *, scalar_rows=None):
        self._rows = list(rows)
        self._scalar_rows = scalar_rows if scalar_rows is not None else list(rows)

    def all(self):
        return list(self._rows)

    def scalars(self):
        return _ScalarsResult(self._scalar_rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    """Content-dispatching fake async session.

    Dispatches each ``execute`` by inspecting the FROM/columns of the SQLAlchemy
    statement so the service's project-scoped SELECTs return the right fixtures.
    ``add`` collects staged rows; ``get`` resolves staged + seeded rows by id.
    """

    def __init__(self, *, cases, dismissed=None, existing=None):
        self._cases = list(cases)
        self._dismissed = list(dismissed or [])
        self._existing = list(existing or [])
        self.added = []
        self.flushed = 0
        # index for get()
        self._by_id = {c.id: c for c in self._cases}
        for row in self._existing:
            self._by_id[row.id] = row

    async def execute(self, stmt):
        # Identify the primary entity of the statement.
        desc = str(stmt)
        if "managed_test_cases" in desc and "duplicate_test_case_candidates" not in desc and "dismissed" not in desc:
            # _load_cases — select(ManagedTestCase) where project_id, status
            # Replicate the project + non-deprecated filter from the bound params.
            params = stmt.compile().params
            pid = next((v for k, v in params.items() if "project_id" in k), None)
            bound_values = {
                item
                for value in params.values()
                for item in (value if isinstance(value, (list, tuple, set)) else [value])
            }
            selects_one_id = "managed_test_cases.id =" in desc
            rows = [
                c for c in self._cases
                if (pid is None or c.project_id == pid)
                and c.status in svc.DUPLICATE_ELIGIBLE_STATES
                and (not selects_one_id or c.id in bound_values)
            ]
            return _ExecResult(rows, scalar_rows=rows)
        if "dismissed_duplicate_pairs" in desc:
            params = stmt.compile().params
            pid = next((v for k, v in params.items() if "project_id" in k), None)
            rows = [
                (d.case_a_id, d.case_b_id)
                for d in self._dismissed
                if pid is None or d.project_id == pid
            ]
            # _load_dismissed reads .all() of (a, b) tuples
            return _ExecResult(rows, scalar_rows=rows)
        if "duplicate_test_case_candidates" in desc:
            params = stmt.compile().params
            pid = next((v for k, v in params.items() if "project_id" in k), None)
            rows = [
                c for c in self._existing
                if pid is None or c.project_id == pid
            ]
            return _ExecResult(rows, scalar_rows=rows)
        return _ExecResult([], scalar_rows=[])

    def add(self, row):
        self.added.append(row)
        if getattr(row, "id", None) is None and isinstance(row, DuplicateTestCaseCandidate):
            row.id = uuid.uuid4()
        if getattr(row, "id", None) is not None:
            self._by_id[row.id] = row

    async def get(self, model, pk):
        row = self._by_id.get(pk)
        if row is None:
            return None
        # crude model-type guard
        return row

    async def flush(self):
        self.flushed += 1


# ═══════════════════════ Tier 0 — fingerprint ═══════════════════════


def test_fingerprint_normalisation_invariant():
    pid = uuid.uuid4()
    a = _case(project_id=pid, title="Login  works!", objective="Verify LOGIN.")
    b = _case(project_id=pid, title="login works", objective="verify login")
    assert svc.compute_dup_fingerprint(a) == svc.compute_dup_fingerprint(b)


def test_fingerprint_differs_on_content():
    pid = uuid.uuid4()
    a = _case(project_id=pid, title="Login works")
    b = _case(project_id=pid, title="Logout works")
    assert svc.compute_dup_fingerprint(a) != svc.compute_dup_fingerprint(b)


@pytest.mark.asyncio
async def test_exact_fingerprint_match_band_exact():
    pid = uuid.uuid4()
    a = _case(project_id=pid, title="User can log in", objective="auth",
              expected_result="redirected to dashboard", cid=uuid.uuid4())
    b = _case(project_id=pid, title="User  can LOG in!", objective="AUTH",
              expected_result="Redirected to dashboard.", cid=uuid.uuid4())
    db = FakeDB(cases=[a, b])
    result = await svc.detect_duplicates_for_project(db, pid, enable_semantic=False)

    assert result["cases_scanned"] == 2
    cands = [r for r in db.added if isinstance(r, DuplicateTestCaseCandidate)]
    assert len(cands) == 1
    c = cands[0]
    assert c.band == "exact"
    assert c.score == 1.0
    assert c.method == "fingerprint"
    assert c.reason == "identical normalized content"
    # canonical ordering
    assert c.case_a_id < c.case_b_id


# ═══════════════════════ Tier 1 — structural ═══════════════════════


@pytest.mark.asyncio
async def test_structural_near_duplicate_scored():
    pid = uuid.uuid4()
    a = _case(
        project_id=pid, suite_name="Auth",
        title="User can log in with valid credentials",
        steps=[{"step_number": 1, "action": "open login page", "expected_result": "form shown"},
               {"step_number": 2, "action": "enter valid user and pass", "expected_result": "submit"},
               {"step_number": 3, "action": "click login", "expected_result": "dashboard"}],
    )
    b = _case(
        project_id=pid, suite_name="Auth",
        title="User can login with valid credential",
        steps=[{"step_number": 1, "action": "open the login page", "expected_result": "form shown"},
               {"step_number": 2, "action": "enter valid username and password", "expected_result": "submit"},
               {"step_number": 3, "action": "click the login button", "expected_result": "dashboard"}],
    )
    db = FakeDB(cases=[a, b])
    await svc.detect_duplicates_for_project(db, pid, enable_semantic=False)

    cands = [r for r in db.added if isinstance(r, DuplicateTestCaseCandidate)]
    assert len(cands) == 1
    c = cands[0]
    assert c.method == "structural"
    assert c.band in ("strong", "possible")
    assert c.score >= svc.POSSIBLE_THRESHOLD
    assert "title" in (c.component_scores or {})
    # reason names the driving components
    assert any(tok in c.reason for tok in ("title", "steps", "text"))


@pytest.mark.asyncio
async def test_structural_distinct_pair_no_candidate():
    pid = uuid.uuid4()
    # Same suite (so they ARE blocked together) but totally different content.
    a = _case(project_id=pid, suite_name="Misc",
              title="Verify password reset email is delivered",
              steps=[{"step_number": 1, "action": "request reset", "expected_result": "email sent"}])
    b = _case(project_id=pid, suite_name="Misc",
              title="Export quarterly revenue report as CSV",
              steps=[{"step_number": 1, "action": "open reports", "expected_result": "csv downloaded"}])
    db = FakeDB(cases=[a, b])
    await svc.detect_duplicates_for_project(db, pid, enable_semantic=False)

    cands = [r for r in db.added if isinstance(r, DuplicateTestCaseCandidate)]
    assert cands == []


# ═══════════════════════ Scoring quality (review-finding regressions) ═══════════════════════


def _score_band(t1, t2, *, suite="Auth", steps_a=None, steps_b=None):
    """Helper: structural score + band for two titles (optionally with steps)."""
    pid = uuid.uuid4()
    a = svc._make_view(_case(project_id=pid, suite_name=suite, title=t1, steps=steps_a))
    b = svc._make_view(_case(project_id=pid, suite_name=suite, title=t2, steps=steps_b))
    score, comp, reason = svc._structural_score(a, b)
    tj = svc._jaccard(a.title_tokens, b.title_tokens)
    band = svc._band_for_score(
        score, title_jaccard=tj, discriminating=bool(comp.get("discriminating_tokens"))
    )
    return score, band, comp, reason


@pytest.mark.parametrize(
    "t1,t2",
    [
        ("Login with valid credentials", "Login with invalid credentials"),
        ("Test API returns 200", "Test API returns 404"),
        ("Verify user login", "Verify user logout"),
        ("Delete user", "Create user"),
        ("Add item to cart", "Add item to wishlist"),
    ],
)
def test_opposite_sense_titles_never_strong(t1, t2):
    """Opposite-sense / different-assertion titles must never reach ``strong``.

    Regression for the char-ratio FP class: ``valid``/``invalid``,
    ``200``/``404`` etc. used to score ~0.9-0.97 = STRONG. They are now demoted
    by the token-aware metric + discriminating-token band cap.
    """
    _score, band, _comp, _reason = _score_band(t1, t2)
    assert band != "strong"


def test_discriminating_tokens_surfaced_in_components_and_reason():
    """The differing salient tokens (valid/invalid) appear in the explainability."""
    _score, band, comp, reason = _score_band(
        "Login with valid credentials", "Login with invalid credentials"
    )
    disc = comp.get("discriminating_tokens")
    assert disc and set(disc) == {"valid", "invalid"}
    # Reason names BOTH what they share and how they differ (actionable).
    assert "share" in reason and "differ on" in reason
    assert "valid" in reason and "invalid" in reason
    assert band != "strong"


def test_numeric_difference_is_discriminating():
    _score, band, comp, _reason = _score_band(
        "Test API returns 200", "Test API returns 404"
    )
    assert set(comp.get("discriminating_tokens") or []) == {"200", "404"}
    assert band != "strong"


def test_reordered_steps_recovered_as_high():
    """Reordered-but-identical steps must score ~1.0 on the steps component.

    Regression for the order-sensitive false-negative: difflib over ordered step
    text rated identical-reordered steps ~0.46; the order-insensitive set overlap
    now recovers them.
    """
    s1 = [
        {"action": "enter username", "expected_result": ""},
        {"action": "enter password", "expected_result": ""},
        {"action": "click login", "expected_result": ""},
    ]
    s2 = list(reversed(s1))
    _score, _band, comp, _reason = _score_band(
        "Login flow", "Login flow", steps_a=s1, steps_b=s2
    )
    assert comp["steps"] >= 0.99


def test_text_component_excludes_title_body_only():
    """norm_text is BODY only — the title is not double-counted in ``text``."""
    pid = uuid.uuid4()
    # Same title, but bodies (objective) are completely different → text must be
    # driven by the body, NOT inherit the identical-title overlap.
    a = svc._make_view(
        _case(project_id=pid, title="Login works", objective="check the dashboard renders charts")
    )
    b = svc._make_view(
        _case(project_id=pid, title="Login works", objective="totally unrelated billing export csv")
    )
    assert "login" not in a.norm_text  # title excluded from body
    jacc = svc._jaccard(a.text_tokens, b.text_tokens)
    # Independent body signal: the two unrelated objectives share little.
    assert jacc < 0.3


@pytest.mark.asyncio
async def test_empty_bodied_cases_do_not_explode_tier0():
    """Many empty/whitespace-only cases share ONE degenerate fingerprint but must
    NOT stage an O(n^2) explosion of 'exact' candidates (finding: Tier-0 budget).
    """
    pid = uuid.uuid4()
    # 30 cases whose title/objective/steps/expected all normalise to empty.
    cases = [
        _case(project_id=pid, title="   ", objective="!!!", cid=uuid.uuid4())
        for _ in range(30)
    ]
    db = FakeDB(cases=cases)
    result = await svc.detect_duplicates_for_project(db, pid, enable_semantic=False)
    cands = [r for r in db.added if isinstance(r, DuplicateTestCaseCandidate)]
    # Degenerate empty payloads are skipped entirely — zero exact candidates,
    # not 30*29/2 = 435.
    assert cands == []
    assert result["cases_scanned"] == 30


@pytest.mark.asyncio
async def test_semantic_restricted_to_blocked_cases():
    """The semantic tier only receives cases that participate in a block — it
    never embeds the full project (finding: un-bounded O(n) semantic path).
    """
    pid = uuid.uuid4()
    # a & b share suite Auth (blocked together); c shares nothing (isolated).
    a = _case(project_id=pid, suite_name="Auth", title="alpha login flow", cid=uuid.uuid4())
    b = _case(project_id=pid, suite_name="Auth", title="beta login flow", cid=uuid.uuid4())
    c = _case(project_id=pid, suite_name="Lonely", title="orphan widget xyz", cid=uuid.uuid4())
    db = FakeDB(cases=[a, b, c])

    seen = {}

    async def _capture(views):
        seen["ids"] = {v.id for v in views}
        return {}

    with patch.object(svc, "_semantic_neighbours", _capture):
        await svc.detect_duplicates_for_project(db, pid, enable_semantic=True)

    # The isolated case c is NOT handed to the semantic tier.
    assert c.id not in seen["ids"]
    assert {a.id, b.id} <= seen["ids"]


@pytest.mark.asyncio
async def test_semantic_skipped_above_case_cap(monkeypatch):
    """Above _MAX_SEMANTIC_CASES blocked cases the semantic tier is skipped with
    a note, and never invokes the embedder."""
    pid = uuid.uuid4()
    monkeypatch.setattr(svc, "_MAX_SEMANTIC_CASES", 1)
    a = _case(project_id=pid, suite_name="Auth", title="alpha login flow", cid=uuid.uuid4())
    b = _case(project_id=pid, suite_name="Auth", title="beta login flow", cid=uuid.uuid4())
    db = FakeDB(cases=[a, b])

    called = {"n": 0}

    async def _should_not_run(views):
        called["n"] += 1
        return {}

    with patch.object(svc, "_semantic_neighbours", _should_not_run):
        result = await svc.detect_duplicates_for_project(db, pid, enable_semantic=True)

    assert called["n"] == 0  # embedder never invoked
    assert result["semantic_used"] is False
    assert result["note"] and "semantic tier skipped" in result["note"]


# ═══════════════════════ Blocking ═══════════════════════


def test_blocking_skips_unrelated_pairs():
    pid = uuid.uuid4()
    # No shared suite, no shared tag, no shared rare title token.
    a = _case(project_id=pid, suite_name="Alpha", tags=["x"],
              title="zebra crossing widget")
    b = _case(project_id=pid, suite_name="Beta", tags=["y"],
              title="quantum flux capacitor")
    views = [svc._make_view(a), svc._make_view(b)]
    res = svc._generate_candidate_pairs(views)
    assert res.pairs == set()


def test_blocking_pairs_on_shared_suite():
    pid = uuid.uuid4()
    a = _case(project_id=pid, suite_name="Shared", tags=["x"], title="alpha one")
    b = _case(project_id=pid, suite_name="Shared", tags=["y"], title="beta two")
    views = [svc._make_view(a), svc._make_view(b)]
    res = svc._generate_candidate_pairs(views)
    assert len(res.pairs) == 1
    pair = next(iter(res.pairs))
    assert pair[0] < pair[1]


def test_blocking_pairs_on_shared_tag():
    pid = uuid.uuid4()
    a = _case(project_id=pid, suite_name="A", tags=["smoke"], title="alpha")
    b = _case(project_id=pid, suite_name="B", tags=["smoke"], title="beta")
    views = [svc._make_view(a), svc._make_view(b)]
    res = svc._generate_candidate_pairs(views)
    assert len(res.pairs) == 1


# ═══════════════════════ Per-project scoping ═══════════════════════


@pytest.mark.asyncio
async def test_no_cross_project_pairs():
    p1 = uuid.uuid4()
    p2 = uuid.uuid4()
    # Identical content, different projects — must never pair.
    a = _case(project_id=p1, title="Identical case", objective="same")
    b = _case(project_id=p2, title="Identical case", objective="same")
    db = FakeDB(cases=[a, b])
    result = await svc.detect_duplicates_for_project(db, p1, enable_semantic=False)

    assert result["cases_scanned"] == 1  # only the p1 case loaded
    cands = [r for r in db.added if isinstance(r, DuplicateTestCaseCandidate)]
    assert cands == []


# ═══════════════════════ Dismissed-pair suppression ═══════════════════════


@pytest.mark.asyncio
async def test_dismissed_pair_suppressed():
    pid = uuid.uuid4()
    id_a = uuid.uuid4()
    id_b = uuid.uuid4()
    a = _case(project_id=pid, title="User can log in", objective="auth", cid=id_a)
    b = _case(project_id=pid, title="User can LOG in", objective="auth", cid=id_b)
    lo, hi = (id_a, id_b) if id_a < id_b else (id_b, id_a)
    dismissed = [SimpleNamespace(project_id=pid, case_a_id=lo, case_b_id=hi)]
    db = FakeDB(cases=[a, b], dismissed=dismissed)
    result = await svc.detect_duplicates_for_project(db, pid, enable_semantic=False)

    cands = [r for r in db.added if isinstance(r, DuplicateTestCaseCandidate)]
    assert cands == []  # exact match would have fired, but it's dismissed
    assert result["candidates_created"] == 0


# ═══════════════════════ Tier 2 — offline skip ═══════════════════════


@pytest.mark.asyncio
async def test_semantic_offline_skip_returns_dict_no_raise():
    """The semantic tier must NEVER raise out — any embedder/Chroma failure
    returns ``{}`` so the run is structural-only."""
    pid = uuid.uuid4()
    a = _case(project_id=pid, suite_name="Auth", title="User can log in cleanly")

    # Simulate the local embedder / ChromaDB being unavailable by forcing the
    # in-thread builder to raise. The function must swallow it and return {}.
    with patch(
        "app.services.duplicate_detection_service.asyncio.to_thread",
        side_effect=RuntimeError("no local embedder"),
    ):
        neighbours = await svc._semantic_neighbours([svc._make_view(a)])
    assert neighbours == {}


@pytest.mark.asyncio
async def test_detection_completes_when_semantic_skipped():
    pid = uuid.uuid4()
    a = _case(project_id=pid, suite_name="Auth", title="User can log in cleanly")
    b = _case(project_id=pid, suite_name="Auth", title="User logs in cleanly now")
    db = FakeDB(cases=[a, b])

    async def _no_neighbours(_views):
        return {}

    with patch.object(svc, "_semantic_neighbours", _no_neighbours):
        result = await svc.detect_duplicates_for_project(db, pid, enable_semantic=True)

    assert result["cases_scanned"] == 2
    assert result["semantic_used"] is False


# ═══════════════════════ Merge / dismiss actions ═══════════════════════


@pytest.mark.asyncio
async def test_merge_non_destructive_soft_deprecate():
    pid = uuid.uuid4()
    keep = _case(project_id=pid, title="Keep me", cid=uuid.uuid4())
    loser = _case(project_id=pid, title="Lose me", cid=uuid.uuid4())
    lo, hi = sorted([keep.id, loser.id])
    cand = DuplicateTestCaseCandidate(
        project_id=pid, case_a_id=lo, case_b_id=hi,
        band="exact", score=1.0, method="fingerprint", status="open",
    )
    cand.id = uuid.uuid4()
    db = FakeDB(cases=[keep, loser], existing=[cand])
    actor = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD")

    async def _governed_transition(_db, case_id, action, passed_actor, *, reason):
        assert case_id == loser.id
        assert action == svc.LifecycleAction.DEPRECATE
        assert passed_actor is actor
        assert str(cand.id) in reason
        loser.status = "deprecated"
        return SimpleNamespace(case=loser)

    with patch.object(
        svc, "transition", AsyncMock(side_effect=_governed_transition)
    ) as governed_transition:
        returned, deprecated_id = await svc.merge_candidate(
            db,
            pid,
            cand.id,
            keep_case_id=keep.id,
            actor=actor,
            deprecate_loser=True,
        )
    assert returned.status == "merged"
    assert deprecated_id == loser.id
    assert loser.status == "deprecated"  # soft-deprecated, NOT deleted
    assert loser.is_stale is True
    governed_transition.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_requires_a_governed_reviewer_role_before_any_mutation():
    pid = uuid.uuid4()
    keep = _case(project_id=pid, title="Keep", cid=uuid.uuid4())
    loser = _case(project_id=pid, title="Lose", cid=uuid.uuid4())
    lo, hi = sorted([keep.id, loser.id])
    cand = DuplicateTestCaseCandidate(
        project_id=pid,
        case_a_id=lo,
        case_b_id=hi,
        band="exact",
        score=1.0,
        method="fingerprint",
        status="open",
    )
    cand.id = uuid.uuid4()
    db = FakeDB(cases=[keep, loser], existing=[cand])

    with pytest.raises(HTTPException) as exc:
        await svc.merge_candidate(
            db,
            pid,
            cand.id,
            keep_case_id=keep.id,
            actor=SimpleNamespace(id=uuid.uuid4(), role="VIEWER"),
        )

    assert exc.value.status_code == 403
    assert cand.status == "open"
    assert loser.status == "active"


@pytest.mark.asyncio
async def test_dismiss_records_suppression():
    pid = uuid.uuid4()
    a = _case(project_id=pid, title="A", cid=uuid.uuid4())
    b = _case(project_id=pid, title="B", cid=uuid.uuid4())
    lo, hi = sorted([a.id, b.id])
    cand = DuplicateTestCaseCandidate(
        project_id=pid, case_a_id=lo, case_b_id=hi,
        band="possible", score=0.8, method="structural", status="open",
    )
    cand.id = uuid.uuid4()
    db = FakeDB(cases=[a, b], existing=[cand])

    returned = await svc.dismiss_candidate(db, pid, cand.id, dismissed_by_user_id=None)
    assert returned.status == "dismissed"
    suppressions = [r for r in db.added if isinstance(r, DismissedDuplicatePair)]
    assert len(suppressions) == 1
    assert (suppressions[0].case_a_id, suppressions[0].case_b_id) == (lo, hi)


@pytest.mark.asyncio
async def test_merge_replay_with_opposite_keeper_is_rejected_without_mutation():
    pid = uuid.uuid4()
    first = _case(project_id=pid, title="First", cid=uuid.uuid4())
    second = _case(project_id=pid, title="Second", cid=uuid.uuid4())
    lo, hi = sorted([first.id, second.id])
    cand = DuplicateTestCaseCandidate(
        project_id=pid,
        case_a_id=lo,
        case_b_id=hi,
        band="exact",
        score=1.0,
        method="fingerprint",
        status="open",
    )
    cand.id = uuid.uuid4()
    db = FakeDB(cases=[first, second], existing=[cand])
    actor = SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER")

    returned, deprecated_id = await svc.merge_candidate(
        db,
        pid,
        cand.id,
        keep_case_id=first.id,
        actor=actor,
        deprecate_loser=False,
    )
    assert returned.status == "merged"
    assert deprecated_id is None

    with pytest.raises(HTTPException) as exc:
        await svc.merge_candidate(
            db,
            pid,
            cand.id,
            keep_case_id=second.id,
            actor=actor,
            deprecate_loser=False,
        )

    assert exc.value.status_code == 409
    assert first.status == second.status == "active"


@pytest.mark.asyncio
async def test_merge_omission_defaults_to_disposition_only():
    pid = uuid.uuid4()
    keeper = _case(project_id=pid, title="Keeper", cid=uuid.uuid4())
    loser = _case(project_id=pid, title="Loser", cid=uuid.uuid4())
    lo, hi = sorted([keeper.id, loser.id])
    cand = DuplicateTestCaseCandidate(
        project_id=pid,
        case_a_id=lo,
        case_b_id=hi,
        band="exact",
        score=1.0,
        method="fingerprint",
        status="open",
    )
    cand.id = uuid.uuid4()
    db = FakeDB(cases=[keeper, loser], existing=[cand])

    with patch.object(svc, "transition", AsyncMock()) as governed_transition:
        returned, deprecated_id = await svc.merge_candidate(
            db,
            pid,
            cand.id,
            keep_case_id=keeper.id,
            actor=SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER"),
        )

    assert returned.status == "merged"
    assert deprecated_id is None
    assert keeper.status == loser.status == "active"
    governed_transition.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_action", ["merge", "dismiss"])
async def test_terminal_candidate_cannot_be_disposed_again(terminal_action):
    pid = uuid.uuid4()
    first = _case(project_id=pid, title="First", cid=uuid.uuid4())
    second = _case(project_id=pid, title="Second", cid=uuid.uuid4())
    lo, hi = sorted([first.id, second.id])
    cand = DuplicateTestCaseCandidate(
        project_id=pid,
        case_a_id=lo,
        case_b_id=hi,
        band="possible",
        score=0.8,
        method="structural",
        status="merged",
    )
    cand.id = uuid.uuid4()
    db = FakeDB(cases=[first, second], existing=[cand])

    with pytest.raises(HTTPException) as exc:
        if terminal_action == "dismiss":
            await svc.dismiss_candidate(db, pid, cand.id, uuid.uuid4())
        else:
            await svc.merge_candidate(
                db,
                pid,
                cand.id,
                keep_case_id=first.id,
                actor=SimpleNamespace(id=uuid.uuid4(), role="QA_ENGINEER"),
                deprecate_loser=False,
            )

    assert exc.value.status_code == 409
    assert cand.status == "merged"


def test_duplicate_eligibility_excludes_review_and_terminal_states():
    assert set(svc.DUPLICATE_ELIGIBLE_STATES) == {
        "draft",
        "rejected",
        "approved",
        "active",
        "needs_update",
    }
    assert not {
        "review_requested",
        "under_review",
        "deprecated",
        "archived",
    } & set(svc.DUPLICATE_ELIGIBLE_STATES)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["deprecated", "archived"])
async def test_merge_refuses_terminal_keeper_before_disposition(terminal_status):
    pid = uuid.uuid4()
    keeper = _case(
        project_id=pid,
        title="Terminal keeper",
        status=terminal_status,
        cid=uuid.uuid4(),
    )
    loser = _case(project_id=pid, title="Active loser", cid=uuid.uuid4())
    lo, hi = sorted([keeper.id, loser.id])
    cand = DuplicateTestCaseCandidate(
        project_id=pid,
        case_a_id=lo,
        case_b_id=hi,
        band="exact",
        score=1.0,
        method="fingerprint",
        status="open",
    )
    cand.id = uuid.uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        _ExecResult([cand]),
        _ExecResult([keeper, loser]),
    ]

    with pytest.raises(HTTPException) as exc:
        await svc.merge_candidate(
            db,
            pid,
            cand.id,
            keep_case_id=keeper.id,
            actor=SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD"),
            deprecate_loser=True,
        )

    assert exc.value.status_code == 409
    assert "keeper" in exc.value.detail
    assert terminal_status in exc.value.detail
    assert cand.status == "open"
    db.flush.assert_not_awaited()
