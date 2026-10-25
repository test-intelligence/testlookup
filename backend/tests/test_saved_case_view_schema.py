"""Saved case views are ``saved_views`` rows (W13 TCL-01): the schema rules.

``page = "test_management"`` joins ``SAVED_VIEW_PAGES`` and ``"test_case_view"``
joins ``SAVED_VIEW_KINDS`` (both append-only). For ``filters.kind ==
"test_case_view"`` ONLY, the filters' keys are within ``CASE_VIEW_KEYS`` and
each value is checked (``_validate_case_view_filters``); the result is
normalised (strings stripped, lists de-duplicated with their order kept, empty
lists dropped); the page must be ``test_management`` and a shared view needs a
project. Each refusal's message is its sentence exactly (the page renders the
422's message). A report view and a saved search validate exactly as before.
"""
from __future__ import annotations

import uuid
from typing import get_args

import pytest
from pydantic import ValidationError

from app.core.test_library_vocab import (
    CASE_PRESETS,
    CASE_VIEW_KEYS_SENTENCE,
    CASE_VIEW_PAGE_SENTENCE,
    CASE_VIEW_SHARE_NEEDS_PROJECT,
    CASE_VIEW_VALUE_SENTENCE,
    LIFECYCLE_STATES,
)
from app.models.schemas import SAVED_VIEW_KINDS, SAVED_VIEW_PAGES, SavedViewCreate, SavedViewUpdate

PROJECT = uuid.uuid4()
USER = uuid.uuid4()


def _view(**filters) -> dict:
    return {"kind": "test_case_view", **filters}


def _create(**overrides) -> SavedViewCreate:
    payload = {"name": "My open drafts", "page": "test_management", "project_id": PROJECT, "filters": _view()}
    payload.update(overrides)
    return SavedViewCreate(**payload)


def _message(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    (error,) = exc.value.errors()
    return error["msg"]


def test_the_page_and_kind_grow_append_only():
    pages = get_args(SAVED_VIEW_PAGES)
    assert pages[:8] == ("dashboard", "trends", "coverage", "defects", "failures", "summary_report", "explore", "search")
    assert pages[8:] == ("test_management",)
    assert SAVED_VIEW_KINDS == ("report_view", "search", "test_case_view")


def test_a_valid_view_is_normalised():
    view = _create(filters=_view(
        statuses=["draft", "review_requested", "draft"],
        priorities=[" critical ", "high", "critical"],
        types=["e2e", "smoke", "e2e"],
        owner="me",
        automation="not_automated",
        suite="  Checkout  ",
        q="  login  ",
        ai_generated=True,
        requirement=" REQ-PAY-07 ",
    ))
    assert view.filters == {
        "kind": "test_case_view",
        "statuses": ["draft", "review_requested"],
        "priorities": ["critical", "high"],
        "types": ["e2e", "smoke"],
        "owner": "me",
        "automation": "not_automated",
        "suite": "Checkout",
        "q": "login",
        "ai_generated": True,
        "requirement": "REQ-PAY-07",
    }


def test_empty_lists_are_dropped_and_none_values_kept():
    view = _create(filters=_view(statuses=[], priorities=[], types=[], owner=None, automation=None, q=None))
    assert view.filters == {"kind": "test_case_view", "owner": None, "automation": None, "q": None}
    assert _create().filters == {"kind": "test_case_view"}


def test_an_owner_user_id_is_stored_in_its_canonical_form():
    assert _create(filters=_view(owner=str(USER).upper())).filters["owner"] == str(USER)


def test_every_state_and_each_preset_is_a_valid_view():
    assert _create(filters=_view(statuses=list(LIFECYCLE_STATES))).filters["statuses"] == list(LIFECYCLE_STATES)
    for preset in CASE_PRESETS.values():
        assert _create(filters=_view(**preset)).filters == {"kind": "test_case_view", **preset}


@pytest.mark.parametrize("extra", ["release_ids", "window", "charts", "scope", "widgets", "severity"])
def test_any_other_key_is_refused(extra):
    with pytest.raises(ValidationError) as exc:
        _create(filters=_view(**{extra: ["x"]}))
    assert _message(exc) == CASE_VIEW_KEYS_SENTENCE


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("statuses", ["bogus"]),
        ("statuses", "draft"),
        ("statuses", ["draft"] * 21),
        ("statuses", [1]),
        ("priorities", ["p0"]),
        ("types", [""]),
        ("types", ["x" * 51]),
        ("types", [" "]),
        ("owner", "bob"),
        ("owner", 7),
        ("automation", "yes"),
        ("automation", True),
        ("suite", ""),
        ("suite", "s" * 501),
        ("q", "   "),
        ("q", "q" * 201),
        ("requirement", "r" * 201),
        ("requirement", 12),
        ("ai_generated", "true"),
        ("ai_generated", 1),
    ],
)
def test_each_value_rule_refuses_with_its_sentence(key, value):
    with pytest.raises(ValidationError) as exc:
        _create(filters=_view(**{key: value}))
    assert _message(exc) == CASE_VIEW_VALUE_SENTENCE.format(key=key)
    # PATCH validates the filters the same way.
    with pytest.raises(ValidationError) as exc:
        SavedViewUpdate(filters=_view(**{key: value}))
    assert _message(exc) == CASE_VIEW_VALUE_SENTENCE.format(key=key)


def test_the_bounds_are_inclusive():
    view = _create(filters=_view(types=["t" * 50], suite="s" * 500, q="q" * 200, requirement="r" * 200,
                                 statuses=list(LIFECYCLE_STATES) * 2))
    assert len(view.filters["types"][0]) == 50 and len(view.filters["suite"]) == 500
    assert len(view.filters["q"]) == 200 and len(view.filters["requirement"]) == 200


def test_the_statuses_sentence_names_its_key():
    with pytest.raises(ValidationError) as exc:
        _create(filters=_view(statuses=["bogus"]))
    assert _message(exc) == "The saved view's statuses is not valid."


@pytest.mark.parametrize("page", [None, "search", "dashboard", "explore"])
def test_a_saved_case_view_belongs_to_the_test_management_page(page):
    with pytest.raises(ValidationError) as exc:
        _create(page=page)
    assert _message(exc) == CASE_VIEW_PAGE_SENTENCE


def test_sharing_needs_a_project():
    with pytest.raises(ValidationError) as exc:
        _create(project_id=None, is_shared=True)
    assert _message(exc) == CASE_VIEW_SHARE_NEEDS_PROJECT
    assert _create(is_shared=True).is_shared is True
    assert _create(project_id=None, is_shared=False).project_id is None, "a private All-Projects view is fine"


def test_the_422_message_is_the_sentence_through_the_app():
    """What the Save view form renders: FastAPI's 422 for a body built from
    this model carries the sentence as its message."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/views")
    async def create(view: SavedViewCreate) -> dict:
        return {"filters": view.filters}

    client = TestClient(app)
    body = {"name": "Mine", "page": "test_management", "filters": _view(statuses=["draft"]), "is_shared": True}
    response = client.post("/views", json=body)
    assert response.status_code == 422
    assert [d["msg"] for d in response.json()["detail"]] == [CASE_VIEW_SHARE_NEEDS_PROJECT]
    response = client.post("/views", json={**body, "is_shared": False, "filters": _view(owner="bob")})
    assert [d["msg"] for d in response.json()["detail"]] == [CASE_VIEW_VALUE_SENTENCE.format(key="owner")]
    response = client.post("/views", json={**body, "project_id": str(PROJECT), "filters": _view(statuses=["draft", "draft"])})
    assert response.status_code == 200
    assert response.json() == {"filters": {"kind": "test_case_view", "statuses": ["draft"]}}


# ── every other kind is untouched ────────────────────────────────────────────


def test_a_report_view_and_a_layout_row_validate_exactly_as_before():
    filters = {"kind": "report_view", "release_ids": ["r1"], "suites": ["API"], "window": 30, "statuses": ["x"]}
    view = SavedViewCreate(name="Weekly", page="trends", filters=dict(filters), is_shared=True)
    assert view.filters == filters, "returned as given: the case-view rules apply to their kind only"
    layout = {"page": "test_management", "instances": [], "owner": "anyone", "q": 5}
    assert SavedViewCreate(name="Layout", page="test_management", filters=dict(layout)).filters == layout
    with pytest.raises(ValidationError) as exc:
        SavedViewCreate(name="Mine", filters={"kind": "report_view", "window": 0})
    assert "filters.window must be a whole number of days, 1 to 365" in _message(exc)


def test_a_saved_search_validates_exactly_as_before():
    search = {"kind": "search", "q": "  checkout  ", "scope": "tests", "mode": "keyword"}
    view = SavedViewCreate(name="Checkout", page="search", project_id=PROJECT, filters=search)
    assert view.filters == {"kind": "search", "q": "checkout", "scope": "tests", "mode": "keyword"}


def test_an_unknown_kind_is_still_refused():
    with pytest.raises(ValidationError) as exc:
        SavedViewCreate(name="Odd", page="test_management", filters={"kind": "case_view"})
    assert "filters.kind must be one of report_view, search, test_case_view" in _message(exc)
