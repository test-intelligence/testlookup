"""Editing a correction must reach the product, not just the training label.

``POST /feedback/{analysis_id}`` with ``rating=INCORRECT`` and a
``corrected_category`` applies that category to the ``AIAnalysis`` row — which
is what ``/analyze``, the run-intelligence snapshot, the shared report and the
compliance bundle all render.

``PUT /feedback/{analysis_id}`` wrote **only** the ``AIFeedback`` row. It never
loaded the analysis, never applied the revised category, never cleared
``requires_human_review`` and never evicted the semantic cache. So editing a
correction moved the exported training label and nothing else: every product
surface kept the value from the first submission, and the label disagreed with
all of them.

Two further properties pinned here:

* **the edit path needs its own access check.** Matching the feedback row on
  ``user_id`` proves who authored it, never that the author may still touch
  that project — membership can be revoked after the fact. The moment this path
  began writing to ``AIAnalysis`` it became the same cross-tenant write the
  submit path was fixed for.
* **the cache is evicted after the commit.** Evicting inside the staged service
  lets a concurrent reader re-populate it from the old *committed* row, which
  then stands for the full TTL. This codebase measured that live on the
  feature-flag caches and fixed it the same way.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest

from app.models.postgres import FeedbackRating
from app.services import feedback_service as svc

ANALYSIS = uuid.uuid4()
TEST_CASE = uuid.uuid4()


def _body(*, rating=FeedbackRating.INCORRECT, category="infrastructure", root_cause=None):
    return SimpleNamespace(
        rating=rating,
        corrected_category=category,
        corrected_root_cause=root_cause,
        comment=None,
    )


def _analysis(**over):
    row = SimpleNamespace(
        id=ANALYSIS,
        test_case_id=TEST_CASE,
        failure_category="product_bug",
        root_cause_summary="the original AI summary",
        requires_human_review=True,
    )
    for k, v in over.items():
        setattr(row, k, v)
    return row


class TestOneRuleForBothPaths:
    """The submit and edit paths disagreed because each spelled the rule out
    itself. They now share a predicate and an applier."""

    def test_both_paths_apply_through_the_same_helper(self):
        submit = inspect.getsource(svc.submit_feedback)
        update = inspect.getsource(svc.update_feedback)
        for name, src in (("submit_feedback", submit), ("update_feedback", update)):
            assert "_is_correction(body)" in src, name
            assert "_apply_correction(analysis, body)" in src, name

    def test_neither_path_hand_rolls_the_condition(self):
        for fn in (svc.submit_feedback, svc.update_feedback):
            src = inspect.getsource(fn)
            assert "FeedbackRating.INCORRECT" not in src, (
                f"{fn.__name__} spells the correction rule out again instead of "
                "using _is_correction"
            )


class TestTheCorrectionPredicate:
    def test_an_incorrect_rating_with_a_category_is_a_correction(self):
        assert svc._is_correction(_body()) is True

    def test_a_correct_rating_is_not_a_correction_even_with_a_category(self):
        assert svc._is_correction(_body(rating=FeedbackRating.CORRECT)) is False

    def test_an_incorrect_rating_with_no_category_is_not_a_correction(self):
        # Nothing to apply: "this is wrong" without saying what it should be.
        assert svc._is_correction(_body(category=None)) is False
        assert svc._is_correction(_body(category="")) is False


class TestApplyingACorrection:
    def test_it_writes_every_field_the_product_reads(self):
        analysis = _analysis()
        svc._apply_correction(analysis, _body(category="flaky_test"))
        assert analysis.failure_category == "flaky_test"
        assert analysis.requires_human_review is False

    def test_the_root_cause_is_replaced_only_when_supplied(self):
        # An edit that revises the category but not the prose must not blank
        # the summary the report renders.
        analysis = _analysis()
        svc._apply_correction(analysis, _body(root_cause=None))
        assert analysis.root_cause_summary == "the original AI summary"

        svc._apply_correction(analysis, _body(root_cause="a human explanation"))
        assert analysis.root_cause_summary == "a human explanation"


class TestTheEditPathIsAuthorized:
    def test_it_verifies_project_access_before_mutating(self):
        src = inspect.getsource(svc.update_feedback)
        assert "_require_analysis_access" in src, (
            "the edit path writes to AIAnalysis with no membership check; "
            "matching on user_id proves authorship, not current access"
        )
        guard_at = src.index("_require_analysis_access")
        assert src.index("_apply_correction(analysis, body)") > guard_at, (
            "the analysis is overwritten before access is verified"
        )

    def test_a_missing_analysis_is_a_404_not_an_attribute_error(self):
        src = inspect.getsource(svc.update_feedback)
        assert "Analysis not found" in src


class TestEvictionHappensAfterTheCommit:
    def test_the_service_no_longer_evicts_inline(self):
        for fn in (svc.submit_feedback, svc.update_feedback):
            src = inspect.getsource(fn)
            # The CALL, not the name: submit_feedback's access-check comment
            # mentions the evictor by name, and matching that found the prose.
            assert "await _invalidate_analysis_cache_for(" not in src, (
                f"{fn.__name__} evicts mid-transaction; a concurrent reader "
                "re-caches the old committed row for the full TTL"
            )

    def test_both_routers_evict_after_their_commit(self):
        from app.routers import feedback as router

        for fn in (router.submit_feedback, router.update_feedback):
            src = inspect.getsource(fn)
            assert "evict_corrected_analysis_cache" in src, fn.__name__
            commit_at = src.index("await db.commit()")
            evict_at = src.index("evict_corrected_analysis_cache")
            assert evict_at > commit_at, (
                f"{fn.__name__} evicts before committing"
            )

    @pytest.mark.asyncio
    async def test_a_non_correction_evicts_nothing(self):
        # No verdict changed, so nothing in the cache is now wrong.
        calls = []

        class _Db:
            async def execute(self, _s):
                calls.append(1)
                raise AssertionError("should not have queried")

        await svc.evict_corrected_analysis_cache(
            _Db(), ANALYSIS, _body(rating=FeedbackRating.CORRECT)
        )
        assert calls == []

    @pytest.mark.asyncio
    async def test_a_failing_eviction_does_not_raise(self, monkeypatch):
        # The correction is already committed; a cache that will not drop must
        # not turn an accepted edit into an error.
        class _Db:
            async def execute(self, _s):
                raise RuntimeError("redis down")

        await svc.evict_corrected_analysis_cache(_Db(), ANALYSIS, _body())


class TestARetractionIsDeliberatelyLeftAlone:
    def test_the_limitation_is_recorded_where_someone_will_read_it(self):
        """``AIAnalysis`` keeps no pre-correction original.

        Once a correction overwrites ``failure_category`` the AI's first verdict
        is gone, so an edit that retracts the correction cannot restore it.
        Inventing a revert would write a value nobody supplied. The constraint
        is stated at the call site rather than left for the next reader to
        rediscover; restoring it properly needs a new column.
        """
        src = inspect.getsource(svc.update_feedback)
        assert "retraction" in src.lower()

    def test_the_model_really_has_no_original_column(self):
        # If this ever fails, the limitation above is obsolete and a retraction
        # SHOULD restore the original.
        from app.models.postgres import AIAnalysis

        columns = {c.name for c in AIAnalysis.__table__.columns}
        assert not any("original" in c for c in columns)
