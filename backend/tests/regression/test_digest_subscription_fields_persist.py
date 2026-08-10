"""Creating a digest subscription must not discard its own fields.

``POST /api/v1/digests/subscriptions`` built the row from a hand-written keyword
list that omitted ``scope_type``, ``scope_value`` and ``trigger_filter`` —
though ``DigestSubscriptionCreate`` declares all three (with patterns and
defaults), ``DigestSubscriptionResponse`` returns them, and each has its own
column. Measured live:

=================================  ==============
sent                               stored
=================================  ==============
``scope_type="suite"``             ``'project'``
``scope_value="api"``              ``None``
``trigger_filter="failed_only"``   ``'all'``
=================================  ==============

…all behind a **201**, with the response echoing the wrong values back.

**``trigger_filter`` is not cosmetic.** The delivery task gates on it::

    if sub.trigger_filter == "failed_only" and _failed_tests == 0:   # skip
    if sub.trigger_filter == "degraded_only" and _pass_rate >= 90:   # skip

So a user who subscribed to *failures only* was stored as *all* and received
every all-green digest they had explicitly opted out of, on a schedule. That is
the concrete harm here; ``scope_type``/``scope_value`` currently have **no
reader anywhere in the backend**, so they are inert — recorded honestly rather
than inflated, but fixed alongside since the API accepts and echoes them.

Second instance of the shape found by a request-body contract sweep; the first
was ``page`` on saved views (F-063/#544). Both are hand-written ORM field lists
drifting from the schema they claim to accept. Handlers that build the row from
``payload.model_dump()`` cannot have this bug — ``update_ai_config`` was
checked and is safe for exactly that reason.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import DigestSubscription  # noqa: E402
from app.models.schemas import (  # noqa: E402
    DigestSubscriptionCreate,
    DigestSubscriptionResponse,
)
from app.routers import digests  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(digests.create_subscription)

#: Declared by the create schema, stored by a column, returned by the response —
#: and previously dropped between all three.
DROPPED = ("scope_type", "scope_value", "trigger_filter")


@pytest.mark.parametrize("field", DROPPED)
def test_the_create_handler_passes_the_field(field: str):
    assert f"{field}=payload.{field}" in SOURCE, (
        f"POST /digests/subscriptions drops `{field}`: the caller supplies it, "
        f"gets a 201, and the stored row falls back to the column default"
    )


@pytest.mark.parametrize("field", DROPPED)
def test_the_field_is_really_part_of_the_contract(field: str):
    """If any of these were absent, the field would be dead rather than
    dropped, and passing it through would be the wrong fix."""
    assert field in DigestSubscriptionCreate.model_fields
    assert field in DigestSubscriptionResponse.model_fields
    assert hasattr(DigestSubscription, field)


def test_trigger_filter_still_gates_delivery():
    """The reason this is more than cosmetic.

    Pinned from the worker source: if the gate stops reading the column, a
    dropped value would go back to being invisible.
    """
    from app.worker import tasks

    src = inspect.getsource(tasks)
    assert 'sub.trigger_filter == "failed_only"' in src, (
        "the digest delivery gate no longer reads trigger_filter — if that is "
        "intentional, this regression's severity needs re-stating"
    )


def test_existing_fields_were_not_displaced():
    """The fix adds keywords to a constructor; losing one would trade a
    dropped filter for a dropped name or schedule."""
    for field in ("name", "schedule", "channel", "send_when_unchanged"):
        assert f"{field}=payload.{field}" in SOURCE
