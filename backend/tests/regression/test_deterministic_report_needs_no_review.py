"""A run whose report no model wrote reads as not AI-generated, not pending review (D-45).

The pipeline no longer stages a review request for a fully deterministic report
and records ``review_policy = not_applicable`` instead. Without a matching read
the run's envelope said ``pending_review`` with no request anyone could accept,
so an enforced review gate would withhold its exports and notifications for good.
"""
from __future__ import annotations

import uuid

import pytest

from app.services import review_envelope


class _Scalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _Scalars(self._value)


class _DB:
    """Answers the review-request read, then the newest pipeline's review policy."""

    def __init__(self, review_row, review_policy):
        self._answers = [review_row, review_policy]

    async def execute(self, _statement):
        return _Result(self._answers.pop(0))


@pytest.mark.asyncio
async def test_a_deterministic_report_reads_not_ai_generated():
    envelope = await review_envelope.review_envelope_for_run(_DB(None, "not_applicable"), uuid.uuid4())

    assert envelope.ai_generated is False
    assert envelope.state != "pending_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [None, "required", "advisory"])
async def test_a_model_written_report_without_a_review_still_reads_pending(policy):
    envelope = await review_envelope.review_envelope_for_run(_DB(None, policy), uuid.uuid4())

    assert envelope.ai_generated is True
    assert envelope.state == "pending_review"
