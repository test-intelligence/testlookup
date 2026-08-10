"""``page`` on a saved view was accepted, discarded, and unfilterable.

Two halves of one broken field, found by sweeping every MCP tool's query params
against the params its endpoint actually declares.

**1. ``POST /api/v1/saved-views`` silently discarded ``page``.** The handler
built the row from a hand-written field list that omitted it::

    view = SavedView(
        user_id=..., project_id=..., name=..., description=...,
        filters=..., is_shared=..., is_default=...,   # no page
    )

`SavedViewCreate` declares ``page``, `SavedViewResponse` returns it, the column
stores it, and ``PATCH`` sets it — because PATCH ``setattr``s over the payload
instead of naming fields. So the caller got a **201** and a response whose
``page`` was ``null``, having just supplied one. Measured live:

=================================  ==============
action                             stored ``page``
=================================  ==============
``POST`` with ``page="coverage"``  ``None``
``PATCH`` with ``page="trends"``   ``'trends'``
=================================  ==============

**2. ``GET /api/v1/saved-views`` had no ``page`` filter.** The MCP tool
``list_saved_views`` advertises *"page: Optional — restrict to a specific
dashboard page"* and passed it as a query param. FastAPI ignores undeclared
query params, so it was dropped and the tool returned everything while its own
docstring promised scoping. Measured live before the fix, with two views
present::

    (no filter)              -> 2 rows
    ?page=trends             -> 2 rows
    ?page=zzz-no-such-page   -> 2 rows

That is the silent-wrong-answer half of the digests bug (F-033/#534), where a
404 was masking a filter that never applied. Here there was no 404 to notice.

Half 2 alone would have been cosmetic — you cannot usefully filter by a field
nothing persists — so both are fixed together.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.models.schemas import (  # noqa: E402
    SavedViewCreate,
    SavedViewResponse,
    SavedViewUpdate,
)
from app.routers import saved_views  # noqa: E402

pytestmark = pytest.mark.regression

CREATE_SRC = inspect.getsource(saved_views.create_saved_view)
LIST_SRC = inspect.getsource(saved_views.list_saved_views)


class TestCreatePersistsPage:
    def test_the_create_handler_passes_page_to_the_row(self):
        assert "page=payload.page" in CREATE_SRC, (
            "POST /saved-views drops `page`: the caller supplies it, gets a "
            "201, and the stored row has page=None"
        )

    def test_the_field_is_part_of_the_contract(self):
        """If any of these lost `page`, the field would be dead rather than
        broken, and the fix above would be wrong."""
        assert "page" in SavedViewCreate.model_fields
        assert "page" in SavedViewResponse.model_fields
        assert "page" in SavedViewUpdate.model_fields


class TestListFiltersByPage:
    def test_the_endpoint_declares_page(self):
        """FastAPI ignores undeclared query params — an undeclared filter is
        dropped in silence, which is worse than rejecting it."""
        params = inspect.signature(saved_views.list_saved_views).parameters
        assert "page" in params, (
            "GET /saved-views does not declare `page`, so the MCP tool's "
            "documented page filter is silently ignored"
        )

    def test_the_filter_is_applied_to_the_query(self):
        assert "SavedView.page == page" in LIST_SRC, (
            "`page` is declared but never applied — the parameter would be "
            "accepted and still ignored"
        )

    def test_it_remains_optional(self):
        """An unfiltered call must keep returning everything; making the filter
        mandatory would break every existing caller."""
        params = inspect.signature(saved_views.list_saved_views).parameters
        assert params["page"].default is None

    def test_project_scoping_is_untouched(self):
        """The page filter is additional. This endpoint carries an F-042
        authorization fix; losing it would trade a dropped filter for a leak."""
        assert "resolve_project_scope" in LIST_SRC
        assert "SavedView.project_id == project_id" in LIST_SRC
