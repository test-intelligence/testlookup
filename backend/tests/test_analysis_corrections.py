"""Root-cause correction learning loop (#6).

Human corrections captured via AIFeedback are now APPLIED on re-analysis of the
same fingerprint (so a fixed mistake isn't repeated) and INVALIDATE the semantic
analysis cache (so the stale verdict isn't re-served).

Covers the pure builder, the batched most-recent-per-fingerprint resolver
(fake DB), the semantic-cache eviction, and the feedback_service wiring.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import FailureCategory, FeedbackRating  # noqa: E402
from app.services.analysis_corrections import (  # noqa: E402
    build_corrected_analysis,
    get_corrections_for_fingerprints,
)


# ── build_corrected_analysis (pure) ───────────────────────────────────────

def test_build_corrected_analysis_shape():
    out = build_corrected_analysis({
        "corrected_category": FailureCategory.PRODUCT_BUG.value,
        "corrected_root_cause": "Null pointer in checkout",
        "feedback_id": "fb-1",
        "corrected_at": "2026-07-02T00:00:00Z",
    })
    assert out["failure_category"] == FailureCategory.PRODUCT_BUG.value
    assert out["root_cause_summary"] == "Null pointer in checkout"
    assert out["confidence_score"] == 95
    assert out["is_flaky"] is False
    assert out["requires_human_review"] is False
    assert out["human_corrected"] is True
    assert out["classified_by"] == "human_correction"
    assert out["correction_feedback_id"] == "fb-1"
    assert out["_routing"]["mode_used"] == "human_corrected"


def test_build_corrected_analysis_flaky_sets_is_flaky():
    out = build_corrected_analysis({
        "corrected_category": FailureCategory.FLAKY.value,
        "corrected_root_cause": None,
        "feedback_id": "fb-2",
        "corrected_at": None,
    })
    assert out["is_flaky"] is True
    # A missing root cause still yields a sensible human-review summary.
    assert "human review" in out["root_cause_summary"].lower()


# ── get_corrections_for_fingerprints (batched, most-recent per fp) ─────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows
        self.executed = 0

    async def execute(self, _stmt):
        self.executed += 1
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_get_corrections_dedups_to_most_recent_per_fingerprint():
    """Rows arrive ordered (fp, created_at DESC); the first row per fp wins."""
    import datetime as _dt
    newer = _dt.datetime(2026, 7, 2, tzinfo=_dt.timezone.utc)
    older = _dt.datetime(2026, 6, 1, tzinfo=_dt.timezone.utc)
    rows = [
        # fpA: two corrections — the newer (PRODUCT_BUG) must win.
        ("fpA", FailureCategory.PRODUCT_BUG.value, "real bug", uuid.uuid4(), newer),
        ("fpA", FailureCategory.INFRASTRUCTURE.value, "old call", uuid.uuid4(), older),
        # fpB: one correction.
        ("fpB", FailureCategory.FLAKY.value, "race", uuid.uuid4(), newer),
    ]
    db = _FakeDB(rows)
    out = await get_corrections_for_fingerprints(db, uuid.uuid4(), ["fpA", "fpB", "fpC"])

    assert set(out) == {"fpA", "fpB"}  # fpC had no correction
    assert out["fpA"]["corrected_category"] == FailureCategory.PRODUCT_BUG.value
    assert out["fpA"]["corrected_root_cause"] == "real bug"
    assert out["fpB"]["corrected_category"] == FailureCategory.FLAKY.value


@pytest.mark.asyncio
async def test_get_corrections_empty_short_circuits_without_query():
    db = _FakeDB([])
    out = await get_corrections_for_fingerprints(db, uuid.uuid4(), [])
    assert out == {}
    assert db.executed == 0  # no fingerprints → no query issued


@pytest.mark.asyncio
async def test_get_corrections_drops_falsy_fingerprints():
    db = _FakeDB([])
    out = await get_corrections_for_fingerprints(db, uuid.uuid4(), ["", None])
    assert out == {}
    assert db.executed == 0


# ── semantic_cache_invalidate ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_semantic_cache_invalidate_deletes_matching_doc_id():
    from app.services import semantic_cache as sc

    fake_collection = MagicMock()
    fake_collection.delete = MagicMock()

    async def _fake_get_collection(_project_id=None):
        return fake_collection

    with patch.object(sc, "_get_or_create_collection", _fake_get_collection):
        await sc.semantic_cache_invalidate(
            test_name="t", error_message="boom", stack_trace="tb", project_id="p1",
        )

    # Deleted the exact id semantic_cache_store would have written for this
    # signature (same _build_signature + sha256[:32]).
    import hashlib
    expected = hashlib.sha256(sc._build_signature("t", "boom", "tb").encode()).hexdigest()[:32]
    fake_collection.delete.assert_called_once_with(ids=[expected])


@pytest.mark.asyncio
async def test_semantic_cache_invalidate_noops_without_signal():
    from app.services import semantic_cache as sc
    # No error/stack → nothing to invalidate, must not touch the collection.
    called = {"n": 0}

    async def _fake_get_collection(_project_id=None):
        called["n"] += 1
        return MagicMock()

    with patch.object(sc, "_get_or_create_collection", _fake_get_collection):
        await sc.semantic_cache_invalidate("t", "", "", project_id="p1")
    assert called["n"] == 0


# ── feedback_service wires invalidation on an INCORRECT correction ─────────

@pytest.mark.asyncio
async def test_submit_feedback_invalidates_cache_on_incorrect_correction():
    from app.services import feedback_service as fs

    analysis = SimpleNamespace(
        id=uuid.uuid4(), test_case_id=uuid.uuid4(),
        failure_category=None, root_cause_summary=None, requires_human_review=True,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=MagicMock(return_value=analysis),
    ))
    body = SimpleNamespace(
        rating=FeedbackRating.INCORRECT,
        corrected_category=FailureCategory.PRODUCT_BUG.value,
        corrected_root_cause="real bug",
        comment=None,
    )
    user = SimpleNamespace(id=uuid.uuid4())

    # ``submit_feedback`` now resolves the analysis' owning project and checks
    # membership before mutating it. That is a second ``db.execute`` -- and this
    # mock returns the same canned value for every query -- so the tenant check
    # is stubbed out here. It has its own coverage in
    # ``tests/regression/test_remaining_idors_closed.py``; this test is about
    # the correction back-propagating, not about authorization.
    with patch.object(fs, "_require_analysis_access", new=AsyncMock()):
        await fs.submit_feedback(db, analysis.id, body, user)

    # The corrected category is applied to the current analysis row too.
    assert analysis.failure_category == FailureCategory.PRODUCT_BUG.value
    assert analysis.requires_human_review is False

    # Eviction moved OUT of the service (2026-09-20). It is now
    # ``evict_corrected_analysis_cache``, called by the router AFTER its commit:
    # evicting from inside the staged service lets a concurrent reader
    # re-populate the cache from the old committed row, which then stands for
    # the full TTL. The behaviour this test names is unchanged -- an INCORRECT
    # correction still drops the cached verdict -- so it is asserted at the new
    # seam rather than deleted.
    with patch.object(fs, "_invalidate_analysis_cache_for", new=AsyncMock()) as inv:
        await fs.evict_corrected_analysis_cache(db, analysis.id, body)
    inv.assert_awaited_once()
