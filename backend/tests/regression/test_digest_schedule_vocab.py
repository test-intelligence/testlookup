"""Every digest request schema must accept every schedule the ORM defines.

``WEEKLY_RETRO`` was added to the ``DigestSchedule`` enum, the Celery beat
learned to dispatch it, ``digest_content_service`` learned to render it — and
the request schema's regex was never widened. So the one path a user could
take to create the subscription rejected it with a 422.

This is the repo's recurring **producer/consumer vocab drift** class, in its
most expensive form: the vocabulary lives in several places (ORM enum, create
validator, **update validator**, UI selector) and nothing forced them to agree.
A test for ``WEEKLY_RETRO`` alone would pass and still leave the next enum
member to repeat the bug, so the assertion is derived from the enum itself.

**This file previously checked the create schema only, and the drift promptly
reappeared in the update schema.** ``DigestSubscriptionUpdate.schedule`` stayed
at ``^(DAILY|WEEKLY)$``, so measured against the live deployment:

======================================  ========
PATCH ``schedule`` to…                  result
======================================  ========
``DAILY`` / ``WEEKLY``                   200
``WEEKLY_RETRO``                         **422**
``PER_RUN`` / ``PER_RELEASE`` / ``PER_SUITE``  **422**
======================================  ========

A subscription could be *created* as ``WEEKLY_RETRO`` but never *changed* to
one — and once PATCHed to ``DAILY`` it could not be put back, because create was
the only route to those values. The remedy was delete-and-recreate.

The parametrisation now covers **every request schema carrying a schedule
pattern**, discovered by inspection rather than named, so a third schema added
later is covered without editing this file.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("app.models.schemas")

from app.models.postgres import DigestSchedule  # noqa: E402
from app.models import schemas as schema_module  # noqa: E402
from app.models.schemas import (  # noqa: E402
    DigestSubscriptionCreate,
    DigestSubscriptionUpdate,
)

pytestmark = pytest.mark.regression


def _schedule_pattern(model) -> str:
    field = model.model_fields["schedule"]
    for meta in field.metadata:
        pattern = getattr(meta, "pattern", None)
        if pattern:
            return pattern
    raise AssertionError(
        f"{model.__name__}.schedule no longer carries a pattern constraint"
    )


def _schemas_with_a_schedule_pattern() -> list[type]:
    """Every request schema that constrains ``schedule``.

    Discovered rather than listed: the original bug was one schema being
    forgotten, so naming them here would rebuild the same trap.
    """
    found = []
    for name in dir(schema_module):
        obj = getattr(schema_module, name)
        fields = getattr(obj, "model_fields", None)
        if not isinstance(fields, dict) or "schedule" not in fields:
            continue
        try:
            _schedule_pattern(obj)
        except AssertionError:
            continue
        found.append(obj)
    return found


def test_both_known_digest_schemas_are_discovered():
    """Guards the discovery helper from silently finding nothing."""
    found = _schemas_with_a_schedule_pattern()
    assert DigestSubscriptionCreate in found
    assert DigestSubscriptionUpdate in found


@pytest.mark.parametrize("schedule", [s.value for s in DigestSchedule])
@pytest.mark.parametrize(
    "model", _schemas_with_a_schedule_pattern(), ids=lambda m: m.__name__
)
def test_every_orm_schedule_is_accepted(model, schedule: str):
    """Derived from the enum × every schedule-carrying schema."""
    pattern = _schedule_pattern(model)
    assert re.match(pattern, schedule), (
        f"DigestSchedule.{schedule} exists in the ORM but {model.__name__}'s "
        f"regex {pattern!r} rejects it — the API 422s a request that should be "
        f"valid, and for the update schema that means the value is reachable "
        f"only by deleting and recreating the subscription"
    )


@pytest.mark.parametrize(
    "model", _schemas_with_a_schedule_pattern(), ids=lambda m: m.__name__
)
def test_the_pattern_still_rejects_nonsense(model):
    """Guards the test above: a regex widened to `.*` would pass vacuously."""
    with pytest.raises(ValueError):
        model(name="regression", schedule="NOT_A_SCHEDULE")


def test_create_and_update_share_one_vocabulary():
    """The two must not drift apart again — that is the whole bug."""
    assert _schedule_pattern(DigestSubscriptionCreate) == _schedule_pattern(
        DigestSubscriptionUpdate
    ), (
        "the create and update schedule patterns differ; a schedule you can "
        "create but not switch to is the defect this file exists to prevent"
    )


#: Settable at create and stored on the row, so they must be changeable after.
#: ``project_id`` is excluded ON PURPOSE — see below.
_MUTABLE_AFTER_CREATE = ("scope_type", "scope_value", "trigger_filter")


@pytest.mark.parametrize("field", _MUTABLE_AFTER_CREATE)
def test_fields_settable_at_create_are_also_updatable(field: str):
    """A field you can set once and never change is a trap, not a design.

    Measured live before the fix: a PATCH carrying ``trigger_filter="all"``
    returned **200** with the value still ``failed_only``. Pydantic drops
    undeclared body fields, so nothing reported the loss — and the UI's own
    ``updateSubscription`` is typed to send all three.
    """
    assert field in DigestSubscriptionCreate.model_fields
    assert field in DigestSubscriptionUpdate.model_fields, (
        f"`{field}` can be set at create but not changed afterwards; a PATCH "
        f"carrying it is silently ignored and returns 200"
    )


def test_project_id_stays_immutable():
    """The one field that must NOT become updatable.

    ``project_id`` is authorization-checked once in ``create_subscription``,
    and the delivery task reads it off the row without re-checking membership.
    Allowing it on update would let a caller create a subscription against
    their own project, then re-point it at another tenant's — turning a
    scheduled digest into a standing cross-tenant leak.
    """
    assert "project_id" in DigestSubscriptionCreate.model_fields
    assert "project_id" not in DigestSubscriptionUpdate.model_fields, (
        "project_id became updatable — the create-time access check is then "
        "bypassable by PATCHing the row after creation"
    )
