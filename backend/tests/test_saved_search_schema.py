"""Saved searches are ``saved_views`` rows (W12 SRCH-03): the schema rules.

``page = "search"`` joins ``SAVED_VIEW_PAGES`` (append-only) and ``"search"``
joins ``SAVED_VIEW_KINDS``. For ``filters.kind == "search"`` ONLY, the
filters are exactly ``{kind, q, scope, mode}`` (``q`` stripped, 1-500
characters; ``scope`` / ``mode`` from the search vocabularies), the page must
be ``search``, and a search saved for All Projects cannot be shared. Each
refusal's message is its sentence exactly (the page renders the 422's
message). Every other kind validates exactly as before.
"""
from __future__ import annotations

import uuid
from typing import get_args

import pytest
from pydantic import ValidationError

from app.core.search_vocab import (
    SAVED_SEARCH_KEYS_SENTENCE,
    SAVED_SEARCH_MODE_SENTENCE,
    SAVED_SEARCH_PAGE_SENTENCE,
    SAVED_SEARCH_Q_SENTENCE,
    SAVED_SEARCH_SCOPE_SENTENCE,
    SAVED_SEARCH_SHARE_NEEDS_PROJECT,
    SEARCH_MODES,
    SEARCH_SCOPES,
)
from app.models.schemas import (
    SAVED_VIEW_KINDS,
    SAVED_VIEW_PAGES,
    SavedViewCreate,
    SavedViewUpdate,
)

PROJECT = uuid.uuid4()


def _search(**filters) -> dict:
    return {"kind": "search", "q": "checkout", "scope": "tests", "mode": "keyword", **filters}


def _create(**overrides) -> SavedViewCreate:
    payload = {"name": "Checkout failures", "page": "search", "project_id": PROJECT, "filters": _search()}
    payload.update(overrides)
    return SavedViewCreate(**payload)


def _message(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    (error,) = exc.value.errors()
    return error["msg"]


def test_the_page_literal_grows_append_only():
    # Append-only: every value up to W12's "search" keeps its place; later waves may only add after it
    # (W13 appended "test_management" / "test_case_view").
    pages = get_args(SAVED_VIEW_PAGES)
    assert pages[:8] == (
        "dashboard", "trends", "coverage", "defects", "failures", "summary_report", "explore", "search",
    ), "never remove or reorder a value: one stored row with a retired value 422s the list"
    assert SAVED_VIEW_KINDS[:2] == ("report_view", "search")


def test_a_saved_search_round_trips_with_q_stripped():
    view = _create(filters=_search(q="  status:failed checkout  ", scope="all", mode="semantic"))
    assert view.filters == {"kind": "search", "q": "status:failed checkout", "scope": "all", "mode": "semantic"}
    assert view.page == "search"


@pytest.mark.parametrize("scope", SEARCH_SCOPES)
@pytest.mark.parametrize("mode", SEARCH_MODES)
def test_every_scope_and_mode_is_accepted(scope, mode):
    assert _create(filters=_search(scope=scope, mode=mode)).filters["scope"] == scope


def test_a_global_unshared_saved_search_is_accepted():
    view = _create(project_id=None, is_shared=False)
    assert view.project_id is None and view.is_shared is False


@pytest.mark.parametrize(
    ("filters", "sentence"),
    [
        (_search(extra="x"), SAVED_SEARCH_KEYS_SENTENCE),
        (_search(widgets=["w1"]), SAVED_SEARCH_KEYS_SENTENCE),
        (_search(release_ids=["r1"]), SAVED_SEARCH_KEYS_SENTENCE),
        ({"kind": "search", "scope": "tests", "mode": "keyword"}, SAVED_SEARCH_Q_SENTENCE),
        (_search(q=""), SAVED_SEARCH_Q_SENTENCE),
        (_search(q="   "), SAVED_SEARCH_Q_SENTENCE),
        (_search(q="x" * 501), SAVED_SEARCH_Q_SENTENCE),
        (_search(q=42), SAVED_SEARCH_Q_SENTENCE),
        (_search(scope="everything"), SAVED_SEARCH_SCOPE_SENTENCE),
        ({"kind": "search", "q": "x", "mode": "keyword"}, SAVED_SEARCH_SCOPE_SENTENCE),
        (_search(mode="fuzzy"), SAVED_SEARCH_MODE_SENTENCE),
        (_search(mode=None), SAVED_SEARCH_MODE_SENTENCE),
    ],
)
def test_each_filters_rule_refuses_with_its_sentence(filters, sentence):
    with pytest.raises(ValidationError) as exc:
        _create(filters=filters)
    assert _message(exc) == sentence
    # PATCH validates the filters the same way.
    with pytest.raises(ValidationError) as exc:
        SavedViewUpdate(filters=filters)
    assert _message(exc) == sentence


def test_a_500_character_query_is_the_longest_accepted():
    assert len(_create(filters=_search(q="q" * 500)).filters["q"]) == 500
    assert _create(filters=_search(q=" " + "q" * 500 + " ")).filters["q"] == "q" * 500


@pytest.mark.parametrize("page", [None, "dashboard", "explore"])
def test_a_saved_search_belongs_to_the_search_page(page):
    with pytest.raises(ValidationError) as exc:
        _create(page=page)
    assert _message(exc) == SAVED_SEARCH_PAGE_SENTENCE


def test_sharing_needs_a_project():
    with pytest.raises(ValidationError) as exc:
        _create(project_id=None, is_shared=True)
    assert _message(exc) == SAVED_SEARCH_SHARE_NEEDS_PROJECT
    assert _create(is_shared=True).is_shared is True


def test_the_422_message_is_the_sentence_through_the_app():
    """What the page renders: FastAPI's 422 for a body built from this model
    carries the sentence as its message, with no "Value error, " prefix."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/views")
    async def create(view: SavedViewCreate) -> dict:
        return {"ok": True}

    client = TestClient(app)
    body = {"name": "Mine", "page": "search", "filters": _search(), "is_shared": True}
    response = client.post("/views", json=body)
    assert response.status_code == 422
    assert [d["msg"] for d in response.json()["detail"]] == [SAVED_SEARCH_SHARE_NEEDS_PROJECT]
    response = client.post("/views", json={**body, "is_shared": False, "filters": _search(scope="nope")})
    assert [d["msg"] for d in response.json()["detail"]] == [SAVED_SEARCH_SCOPE_SENTENCE]


# ── every other kind is untouched ────────────────────────────────────────────


def test_a_report_view_validates_exactly_as_before():
    filters = {"kind": "report_view", "release_ids": ["r1"], "suites": ["API"], "window": 30, "severity": "high"}
    view = SavedViewCreate(name="Weekly", page="trends", filters=dict(filters), is_shared=True)
    assert view.filters == filters, "returned as given: no key dropped, none normalised"
    # A report view needs no page and may be shared without a project, as before.
    assert SavedViewCreate(name="Mine", filters={"kind": "report_view"}, is_shared=True).is_shared is True
    with pytest.raises(ValidationError) as exc:
        SavedViewCreate(name="Mine", filters={"kind": "report_view", "window": 0})
    assert "filters.window must be a whole number of days, 1 to 365" in _message(exc)


def test_a_layout_row_and_legacy_filters_keep_their_free_form_keys():
    filters = {"severity": "critical", "date_range": 7, "q": "anything", "scope": "x", "mode": "y"}
    view = SavedViewCreate(name="Legacy", page="dashboard", filters=dict(filters))
    assert view.filters == filters, "q / scope / mode are only checked for kind 'search'"


def test_an_unknown_kind_is_still_refused():
    with pytest.raises(ValidationError) as exc:
        SavedViewCreate(name="Odd", filters={"kind": "digest"})
    assert "filters.kind must be one of report_view, search" in _message(exc)
