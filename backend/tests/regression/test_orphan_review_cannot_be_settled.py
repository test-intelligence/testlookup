"""A review whose pipeline run was deleted must not be offered, nor settled silently.

BUG-012, reported by the user as "accepted in /reviews, /agents still shows
awaiting review". The accept path is correct — it transitions
``agent_pipeline_runs.status`` and ``/agents`` reads that same column. The
defect is that the subject can be *gone*:

``review_requests.pipeline_run_id`` is an FK with ``ON DELETE SET NULL``, while
``subject_id`` is a plain varchar with no FK. Deleting a pipeline run therefore
nulls the first, leaves a dangling id in the second, and the review row
survives in the queue.

``settle_review`` guarded the transition with a bare
``if review.pipeline_run_id is not None``, so settling such a review skipped the
transition entirely and reported success having changed nothing. The page
promises "Accepting marks the pipeline run passed"; there was no run to mark.

Measured on the homelab deployment before the fix::

    pipeline_run_id IS NULL | subject is a real pipeline | count
    false                   | yes                        |  106
    true                    | no                         |   12

Not one exception either way, and the single review a human had accepted was
one of the twelve.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.review_request_service import ReviewDecisionRefused, settle_review


def _reviewer():
    """An interactive human reviewer.

    ``settle_review`` refuses API-key credentials before it reaches the subject
    check, and ``credential_kind`` reads a private attribute set by the auth
    dependency — an unknown credential is NOT treated as a login.
    """
    user = SimpleNamespace(id=uuid.uuid4(), is_synthetic=False)
    setattr(user, "_testlookup_credential_kind", "jwt")
    return user


def _orphan(**over):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        subject_type="pipeline_run",
        subject_id=str(uuid.uuid4()),
        pipeline_run_id=None,      # the FK the delete nulled
        test_run_id=None,
        state="pending_review",
        requested_by=None,
        kind="report",
        notes=None,
        reason_code=None,
        reviewed_by=None,
        reviewed_at=None,
    )
    for k, v in over.items():
        setattr(row, k, v)
    return row


class TestSettlingAnOrphanIsRefused:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "decision,reason_code",
        [("accepted", None), ("rejected", "stale_data")],
        ids=["accept", "reject"],
    )
    async def test_it_refuses_instead_of_silently_succeeding(self, decision, reason_code):
        # Both directions: rejecting an orphan was equally a no-op, and the
        # page promises rejection marks the run failed.
        review = _orphan()
        with pytest.raises(ReviewDecisionRefused) as exc:
            await settle_review(
                db=None, review=review, reviewer=_reviewer(),
                decision=decision, reason_code=reason_code, notes=None,
            )
        assert exc.value.status_code == 409
        assert exc.value.code == "subject_run_deleted"

    @pytest.mark.asyncio
    async def test_the_review_is_not_marked_settled(self):
        # The whole defect was a state change that claimed an effect it never
        # had. Refusing must leave the row untouched.
        review = _orphan()
        with pytest.raises(ReviewDecisionRefused):
            await settle_review(
                db=None, review=review, reviewer=_reviewer(),
                decision="accepted", reason_code=None, notes=None,
            )
        assert review.state == "pending_review"
        assert review.reviewed_by is None
        assert review.reviewed_at is None


class TestTheQueueDoesNotOfferOrphans:
    def test_the_listing_excludes_a_null_pipeline_run_id(self):
        # Source-level: the filter must be on the statement, not applied in
        # Python after the fact, or ``limit`` would return fewer rows than
        # asked while orphans still consume slots.
        import inspect

        from app.routers import reviews

        src = inspect.getsource(reviews.list_reviews)
        assert "pipeline_run_id.is_(None)" in src, (
            "list_reviews no longer filters orphan reviews out of the queue"
        )
        assert 'subject_type == "pipeline_run"' in src
