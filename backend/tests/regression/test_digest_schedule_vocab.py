"""The digest create-schema must accept every schedule the ORM defines.

``WEEKLY_RETRO`` was added to the ``DigestSchedule`` enum, the Celery beat
learned to dispatch it, ``digest_content_service`` learned to render it — and
the request schema's regex was never widened. So the one path a user could
take to create the subscription rejected it with a 422.

This is the repo's recurring **producer/consumer vocab drift** class, in its
most expensive form: the vocabulary lives in three places (ORM enum, request
validator, UI selector) and nothing forced them to agree. A test for
``WEEKLY_RETRO`` alone would pass and still leave the next enum member to
repeat the bug, so the assertion is derived from the enum itself.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("app.models.schemas")

from app.models.postgres import DigestSchedule  # noqa: E402
from app.models.schemas import DigestSubscriptionCreate  # noqa: E402

pytestmark = pytest.mark.regression


def _schedule_pattern() -> str:
    field = DigestSubscriptionCreate.model_fields["schedule"]
    for meta in field.metadata:
        pattern = getattr(meta, "pattern", None)
        if pattern:
            return pattern
    raise AssertionError("schedule field no longer carries a pattern constraint")


@pytest.mark.parametrize("schedule", [s.value for s in DigestSchedule])
def test_every_orm_schedule_is_accepted_by_the_request_schema(schedule: str):
    """Derived from the enum, so a new member fails here until it is wired."""
    assert re.match(_schedule_pattern(), schedule), (
        f"DigestSchedule.{schedule} exists in the ORM but the create-schema "
        f"regex {_schedule_pattern()!r} rejects it — the API 422s the only "
        f"request that could create such a subscription"
    )
    model = DigestSubscriptionCreate(name="regression", schedule=schedule)
    assert model.schedule == schedule


def test_the_pattern_still_rejects_nonsense():
    """Guards the test above: a regex widened to `.*` would pass it vacuously."""
    with pytest.raises(ValueError):
        DigestSubscriptionCreate(name="regression", schedule="NOT_A_SCHEDULE")
