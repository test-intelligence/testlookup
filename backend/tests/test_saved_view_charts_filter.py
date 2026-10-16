"""W08 TRND-10: a saved view's ``filters.charts`` (the customised charts of its page).

A Trends view stores ``filters.charts`` -- ``trends.suite-series`` and
``trends.compare`` -- only for charts that are not at their default, and omits
the key otherwise. The rule (``schemas._check_saved_view_charts``): an object
of at most 8 keys, each ``^[a-z][a-z0-9_.-]{0,63}$``, each value an object
with an integer ``version`` >= 1 and at most 2 KiB as compact JSON. One error
text per rule; the 32 KiB whole-filters cap and every other key are unchanged,
and a view without ``charts`` validates exactly as before.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.models import schemas
from app.models.schemas import SavedViewCreate, SavedViewUpdate, _validate_saved_view_filters

COMPARE = {"version": 1, "by": "release", "metric": "failures", "suites": ["checkout", "payments"]}
SERIES = {"version": 1, "title": "Pass rate by suite", "metric": "pass_rate", "topN": 5}


def _error(filters: dict) -> str:
    with pytest.raises(ValidationError) as caught:
        SavedViewCreate(name="My view", page="trends", filters=filters)
    return str(caught.value)


def test_a_valid_charts_object_is_accepted_on_create_and_update():
    filters = {"kind": "report_view", "window": 14, "charts": {"trends.compare": COMPARE, "trends.suite-series": SERIES}}
    assert SavedViewCreate(name="My view", page="trends", filters=filters).filters == filters
    assert SavedViewUpdate(filters=filters).filters == filters


@pytest.mark.parametrize("charts,text", [
    ({f"chart.{i}": {"version": 1} for i in range(9)}, "filters.charts holds at most 8 charts"),
    ({"Bad Key": {"version": 1}}, "filters.charts keys are 1-64 characters"),
    ({"9lives": {"version": 1}}, "filters.charts keys are 1-64 characters"),
    ({"a" * 65: {"version": 1}}, "filters.charts keys are 1-64 characters"),
    ({"trends.compare": {"metric": "failures"}}, "filters.charts values need a whole-number version of 1 or more"),
    ({"trends.compare": {"version": 0}}, "filters.charts values need a whole-number version of 1 or more"),
    ({"trends.compare": {"version": True}}, "filters.charts values need a whole-number version of 1 or more"),
    ({"trends.compare": {"version": "1"}}, "filters.charts values need a whole-number version of 1 or more"),
    ({"trends.compare": [1, 2]}, "filters.charts values must be objects"),
    ({"trends.compare": "failures"}, "filters.charts values must be objects"),
])
def test_each_rule_has_its_own_text(charts, text):
    assert text in _error({"charts": charts})


@pytest.mark.parametrize("charts", [[], "trends.compare", 3, None])
def test_charts_must_be_an_object(charts):
    assert "filters.charts must be an object of chart configurations" in _error({"charts": charts})


def test_eight_charts_and_a_64_character_key_are_the_limits():
    _validate_saved_view_filters({"charts": {f"chart.{i}": {"version": 1} for i in range(8)}})
    _validate_saved_view_filters({"charts": {"a" * 64: {"version": 2}}})


def _sized(n: int) -> dict:
    """A config of exactly ``n`` bytes as compact JSON."""
    base = {"version": 1, "title": ""}
    pad = n - len(json.dumps(base, separators=(",", ":")).encode("utf-8"))
    config = {"version": 1, "title": "x" * pad}
    assert len(json.dumps(config, separators=(",", ":")).encode("utf-8")) == n
    return config


def test_a_chart_config_is_at_most_two_kibibytes():
    _validate_saved_view_filters({"charts": {"trends.compare": _sized(schemas.MAX_SAVED_VIEW_CHART_BYTES)}})
    text = _error({"charts": {"trends.compare": _sized(schemas.MAX_SAVED_VIEW_CHART_BYTES + 1)}})
    assert "filters.charts values hold at most 2048 bytes each" in text


def test_the_whole_filters_cap_is_unchanged():
    assert schemas.MAX_SAVED_VIEW_FILTERS_BYTES == 32 * 1024
    big = {"notes": "x" * (32 * 1024)}
    assert "a saved view holds at most 32768" in _error(big)


def test_a_view_without_charts_validates_exactly_as_before():
    legacy = {"severity": "critical", "days": 7, "kind": "report_view", "suites": ["checkout"], "window": 30}
    assert _validate_saved_view_filters(dict(legacy)) == legacy
    assert "filters.window must be a whole number of days, 1 to 365" in _error({"window": 0})
