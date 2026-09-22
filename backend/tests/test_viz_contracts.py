"""Visualization contracts: the backend half of "one set of fixtures, two validators".

``contracts/viz`` is the frozen seam between the analytics API and the chart
kit. Every fixture under it is validated here against
``app.models.viz_contracts`` and by ``frontend/src/lib/viz/contracts.test.ts``
against the TypeScript guards; a fixture only one side rejects is drift, and it
fails in the PR that caused it.

Pins:

* every ``valid/`` fixture is accepted and round-trips unchanged AS JSON TEXT
  (``3`` stays ``3``); every ``invalid/`` fixture is rejected with EXACTLY ONE
  error, and that error is its own rule's -- a fixture refused for an unrelated
  reason proves nothing about the rule it names (README change rule 4);
* counts, numbers, instants and whitespace follow change rules 3 and 7 field by
  field, and the payload-wide rules (change rule 8, plus ``payload_size``) reach
  every contract, unknown keys included, whether validated through
  ``validate_contract`` or a model directly;
* the pack fails closed: a missing directory, an empty folder or a stray file is
  a failure, never an empty parameter set that pytest reports as a skip;
* the README and the fixtures cover each other: every rule id has an invalid
  fixture in its own contract folder, and every ``violates`` is a documented id;
* the closed sets (dimensions, chart types, statuses) equal the README's, and
  the status vocabulary equals ``TestStatus`` -- no fixture exercises every
  member, so nothing else would notice a misspelt one;
* the caps sit exactly where the README puts them, including the ones no
  fixture reaches;
* scalars are strict, unknown keys are tolerated, and the widget config keeps
  them on a round-trip;
* the six flags: ``app.core.viz_flags``, migration 0192 and ``flags.json`` agree,
  and the migration seeds them disabled and removes exactly those six.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.viz_flags import VIZ_FLAG_KEYS
from app.models import viz_contracts as vc

# Aliased: pytest tries to collect any ``Test*`` class it finds in this namespace.
from app.models.postgres import FeatureFlag, TestStatus as RunStatus

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CONTRACTS = REPO / "contracts" / "viz"
FIXTURES = CONTRACTS / "fixtures"
MIGRATION = BACKEND / "migrations" / "versions" / "0192_seed_viz_feature_flags.py"

VERDICTS = ("valid", "invalid")
PROJECT = "11111111-1111-4111-8111-111111111111"


def _cases(verdict: str, pattern: str = "*.json") -> list:
    paths = sorted(FIXTURES.glob(f"*/{verdict}/{pattern}"))
    return [
        pytest.param(path, id=path.relative_to(FIXTURES).as_posix()) for path in paths
    ]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rendered(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error['loc'])}|{error['type']}|{error['msg']}"
        for error in exc.errors()
    ]


# -- the pack itself ------------------------------------------------------------------------


def test_the_fixture_pack_is_present_and_complete():
    """Fail closed. An empty parametrize list is a SKIP, so without this a
    renamed directory would turn the whole file green."""
    assert FIXTURES.is_dir(), f"contract fixtures not found at {FIXTURES}"
    folders = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())
    assert folders == sorted(
        vc.CONTRACT_MODELS
    ), "fixture folders and CONTRACT_MODELS differ"
    for folder in folders:
        for verdict in VERDICTS:
            found = list((FIXTURES / folder / verdict).glob("*.json"))
            assert found, f"no {verdict} fixture for contract {folder!r}"
    # Every file is somewhere the parametrised tests below will look.
    collected = {case.values[0] for verdict in VERDICTS for case in _cases(verdict)}
    assert collected == set(FIXTURES.rglob("*.json"))
    assert _cases("valid", "*hostile*"), "the hostile-text fixtures are gone"


@pytest.mark.parametrize("path", _cases("valid") + _cases("invalid"))
def test_fixture_file_format(path):
    fixture = _load(path)
    assert isinstance(fixture.get("description"), str) and fixture["description"]
    assert "payload" in fixture
    if path.parent.name == "invalid":
        assert isinstance(fixture.get("violates"), str) and fixture["violates"]
    else:
        assert "violates" not in fixture


# -- valid / invalid -------------------------------------------------------------------------


def _text(value) -> str:
    """JSON text, keys sorted. ``3 == 3.0`` in Python, so ``==`` on the decoded
    value cannot see a number that changed form; the text can."""
    return json.dumps(value, sort_keys=True)


@pytest.mark.parametrize("path", _cases("valid"))
def test_valid_fixture_is_accepted_and_round_trips(path):
    payload = _load(path)["payload"]
    model = vc.validate_contract(path.parts[-3], payload)
    # Unchanged means unchanged: a null stays null (never 0), an absent
    # optional key stays absent (never null) and 3 stays 3 (never 3.0).
    assert _text(vc.dump_contract(model)) == _text(payload)


def test_the_integer_values_fixture_would_catch_a_float_only_number():
    """The text comparison above is only as good as its inputs: a fixture set
    with no whole-number ``y`` could not tell ``3`` from ``3.0``."""
    payload = _load(FIXTURES / "chart_series" / "valid" / "series_integer_values.json")[
        "payload"
    ]
    ys = [point["y"] for point in payload["series"][0]["points"]]
    assert any(type(y) is int for y in ys) and any(type(y) is float for y in ys)
    dumped = vc.dump_contract(vc.validate_contract("chart_series", payload))
    assert [type(p["y"]) for p in dumped["series"][0]["points"]] == [
        type(y) for y in ys
    ]


# How each rule's error is recognised. A rule enforced by a validator names
# itself: its message STARTS with the rule id (``Value error, <rule>:``). A rule
# enforced by a field constraint is identified by where it fired and as what.
NAMED_RULES = {
    # payload-wide (change rule 8)
    "well_formed_string",
    "nesting_depth",
    "forbidden_key",
    # counts (change rule 3)
    "non_negative",
    "integer_count",
    "safe_integer",
    # C1
    "project_id_format",
    "release_id_format",
    "unique_release",
    "release_requires_project",
    "unique_suite",
    "window_one_form",
    "window_days_range",
    "window_order",
    # C2
    "totals_subset",
    "truncated_total",
    "truncated_axis",
    "truncated_axes",
    "outside_window",
    "measured_reason",
    "utc_instant",
    # C3
    "kind_enum",
    "unique_series_key",
    "point_cap",
    "cell_cap",
    "cell_index_range",
    "status_vocab",
    "tree_parent_exists",
    "tree_acyclic",
    "unique_node_id",
    "node_cap",
    "edge_endpoints",
    "weight_range",
    # C4
    "unique_instance_id",
    # C5
    "unique_dimension",
    # C6
    "rate_range",
    "metric_reason",
    "comparable_reason",
    "comparable_measured",
}
FIELD_RULES = {
    # C1
    "release_cap": "release_ids|too_long|",
    "suite_name_length": "suite_names.0|string_too_short|",
    "suite_cap": "suite_names|too_long|",
    # C2
    "required_field": "|missing|",
    "timezone_utc": "scope.window.timezone|literal_error|",
    "ignored_dimension": "ignored_filters.0.dimension|literal_error|",
    # C3
    "series_cap": "series.series|too_long|",
    # C4
    "instance_cap": "instances|too_long|",
    "title_length": "instances.0.title|string_too_long|",
    "chart_type_enum": "instances.0.chartType|literal_error|",
    "group_by_cap": "instances.0.groupBy|too_long|",
    "top_n_enum": "instances.0.topN|literal_error|",
    # C5
    "depth_cap": "path|too_long|",
    "dimension_enum": "path.0.dimension|literal_error|",
    "value_length": "path.0.value|string_too_short|",
    # C6
    "comparable_reason_code": "previous.reason_code|literal_error|",
}


def _expected_error(rule: str) -> str:
    """The fragment of ``loc|type|msg`` that the error for ``rule`` carries."""
    if rule in NAMED_RULES:
        return f"|value_error|Value error, {rule}:"
    return FIELD_RULES[rule]


def test_every_rule_has_exactly_one_way_to_be_recognised():
    assert not NAMED_RULES & set(FIELD_RULES)
    documented = (
        set().union(*_readme_rule_ids().values()) | _readme_payload_wide_rule_ids()
    )
    assert NAMED_RULES | set(FIELD_RULES) == documented


@pytest.mark.parametrize("path", _cases("invalid"))
def test_invalid_fixture_is_rejected_for_its_own_rule(path):
    """README change rule 4: each invalid fixture breaks exactly one rule, so it
    must produce exactly one error, and that error must be the named rule's."""
    fixture = _load(path)
    with pytest.raises(ValidationError) as caught:
        vc.validate_contract(path.parts[-3], fixture["payload"])
    rule = fixture["violates"]
    assert (
        rule in NAMED_RULES or rule in FIELD_RULES
    ), f"no expected error recorded for rule {rule!r}"
    rendered = _rendered(caught.value)
    assert len(rendered) == 1, f"expected exactly one error for {rule!r}: {rendered}"
    assert (
        _expected_error(rule) in rendered[0]
    ), f"rejected, but not for {rule!r}: {rendered}"


def test_an_unknown_contract_name_is_an_error_not_a_pass():
    with pytest.raises(ValueError, match="unknown viz contract"):
        vc.validate_contract("heatmap", {})


# -- README <-> fixtures ---------------------------------------------------------------------

_SECTION = re.compile(r"^## .*`fixtures/([a-z_]+)`")
_BACKTICKED = re.compile(r"`([^`]+)`")
_RULE_ID = re.compile(r"[a-z][a-z0-9_]*")


def _cells(line: str) -> list[str]:
    # ``\|`` is a literal pipe inside a cell ("time"\|"category"), not a column break.
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def _readme_sections() -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in (CONTRACTS / "README.md").read_text(encoding="utf-8").splitlines():
        heading = _SECTION.match(line)
        if heading:
            current = sections.setdefault(heading.group(1), [])
        elif line.startswith("## "):
            current = None
        elif current is not None:
            current.append(line)
    return sections


def _readme_rule_ids() -> dict[str, set[str]]:
    """Per fixture folder, the ids in that section's "Rule id" table column.

    Parenthesised asides are dropped first: ``non_negative`` (``n``) names one
    rule, and the field it applies to is not a second one.
    """
    rules: dict[str, set[str]] = {}
    for folder, lines in _readme_sections().items():
        found = rules.setdefault(folder, set())
        column = None
        for line in lines:
            if not line.startswith("|"):
                column = None
                continue
            cells = _cells(line)
            if column is None:
                headers = [cell.lower() for cell in cells]
                column = next(
                    (
                        i
                        for i, name in enumerate(headers)
                        if name in ("rule id", "rule ids")
                    ),
                    None,
                )
                assert (
                    column is not None
                ), f"no Rule id column in the {folder} table: {line}"
                continue
            if column < len(cells):
                cell = re.sub(r"\([^)]*\)", "", cells[column])
                found.update(
                    token
                    for token in _BACKTICKED.findall(cell)
                    if _RULE_ID.fullmatch(token)
                )
    return rules


def _readme_payload_wide_rule_ids() -> set[str]:
    """The "Rule id" column of the table under change rule 8 (indented, so it
    sits inside the numbered list): rules checked on every contract."""
    found: set[str] = set()
    in_change_rules = False
    column = None
    for line in (CONTRACTS / "README.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_change_rules = line.strip() == "## Change rules"
            column = None
            continue
        stripped = line.strip()
        if not in_change_rules or not stripped.startswith("|"):
            column = None
            continue
        cells = _cells(stripped)
        if column is None:
            headers = [cell.lower() for cell in cells]
            assert "rule id" in headers, f"no Rule id column in: {line}"
            column = headers.index("rule id")
            continue
        found.update(
            token
            for token in _BACKTICKED.findall(cells[column])
            if _RULE_ID.fullmatch(token)
        )
    return found


def _readme_list(folder: str, rule: str) -> list[str]:
    """The comma-separated closed set the README gives for ``rule`` in ``folder``."""
    for line in _readme_sections()[folder]:
        cells = _cells(line) if line.startswith("|") else []
        if f"`{rule}`" in cells[:2]:
            lists = [span for span in _BACKTICKED.findall(cells[-1]) if ", " in span]
            assert len(lists) == 1, f"expected one closed set in: {line}"
            return lists[0].split(", ")
    raise AssertionError(f"rule {rule!r} has no row in the {folder} section")


def _violations() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for case in _cases("invalid"):
        path = case.values[0]
        found.setdefault(path.parts[-3], set()).add(_load(path)["violates"])
    return found


def test_the_readme_parser_reads_every_section():
    """A parser that silently finds nothing would make both checks below vacuous."""
    rules = _readme_rule_ids()
    wide = _readme_payload_wide_rule_ids()
    assert sorted(rules) == sorted(vc.CONTRACT_MODELS)
    assert wide == {"well_formed_string", "nesting_depth", "forbidden_key"}
    everything = set().union(*rules.values()) | wide
    assert len(everything) >= 30, f"the README parser found only {sorted(everything)}"
    assert {"release_requires_project", "window_order", "unique_release"} <= rules[
        "scope"
    ]
    assert {
        "totals_subset",
        "measured_reason",
        "non_negative",
        "integer_count",
        "safe_integer",
        "utc_instant",
    } <= rules["envelope"]
    assert {
        "series_cap",
        "status_vocab",
        "tree_acyclic",
        "weight_range",
        "kind_enum",
        "point_cap",
        "cell_cap",
        "node_cap",
        "unique_node_id",
        "integer_count",
        "non_negative",
    } <= rules["chart_series"]
    assert {"instance_cap", "unique_instance_id", "top_n_enum"} <= rules[
        "widget_config"
    ]
    assert rules["drill_path"] == {
        "depth_cap",
        "unique_dimension",
        "dimension_enum",
        "value_length",
        "status_vocab",
    }
    # Field names that sit in parentheses beside a rule id are not rule ids.
    assert not {"n", "value", "value_type", "status", "null"} & rules["chart_series"]


def test_every_readme_rule_has_an_invalid_fixture_in_its_own_folder():
    violations = _violations()
    missing = {
        f"{folder}:{rule}"
        for folder, rules in _readme_rule_ids().items()
        for rule in rules
        if rule not in violations.get(folder, set())
    }
    # A payload-wide rule belongs to every folder; one fixture anywhere shows it.
    anywhere = set().union(*violations.values())
    missing |= {f"*:{rule}" for rule in _readme_payload_wide_rule_ids() - anywhere}
    assert not missing, f"rules with no invalid fixture: {sorted(missing)}"


def test_every_violates_value_is_a_documented_rule():
    sections = _readme_sections()
    wide = _readme_payload_wide_rule_ids()
    unknown = {
        f"{folder}:{rule}"
        for folder, rules in _violations().items()
        for rule in rules
        if rule not in wide
        and rule not in _BACKTICKED.findall("\n".join(sections.get(folder, [])))
    }
    assert (
        not unknown
    ), f"fixtures naming a rule the README does not define: {sorted(unknown)}"


# -- closed sets -----------------------------------------------------------------------------


def test_status_vocab_matches_the_readme_and_the_column_enum():
    documented = _readme_list("drill_path", "status_vocab")
    assert sorted(documented) == sorted(status.value.lower() for status in RunStatus)
    assert sorted(documented) == sorted(vc.STATUS_VOCAB)


def test_every_documented_status_is_accepted_and_the_stored_spelling_is_not():
    for status in _readme_list("drill_path", "status_vocab"):
        vc.validate_contract(
            "drill_path", {"path": [{"dimension": "status", "value": status}]}
        )
        vc.validate_contract("chart_series", _matrix("status", [_cell(value=status)]))
    # The column stores "PASSED"; the contract is lower case and does not fold.
    with pytest.raises(ValidationError, match="status_vocab"):
        vc.validate_contract(
            "drill_path",
            {"path": [{"dimension": "status", "value": RunStatus.PASSED.value}]},
        )


def test_dimension_and_chart_type_sets_match_the_readme():
    assert list(vc.DIMENSIONS) == _readme_list("drill_path", "dimension_enum")
    assert list(vc.CHART_TYPES) == _readme_list("widget_config", "chart_type_enum")
    for dimension in vc.DIMENSIONS:
        if dimension != "status":
            vc.validate_contract(
                "drill_path", {"path": [{"dimension": dimension, "value": "v"}]}
            )
    for chart_type in vc.CHART_TYPES:
        vc.validate_contract(
            "widget_config", _widgets([_instance(chartType=chart_type)])
        )


# -- payload builders ------------------------------------------------------------------------


def _scope(**over) -> dict:
    return {
        "project_id": None,
        "release_ids": [],
        "suite_names": [],
        "window": {"days": 30},
        **over,
    }


def _release(index: int) -> str:
    return f"22222222-2222-4222-8222-{index:012d}"


def _series(count: int = 1, points: int = 1) -> dict:
    return {
        "kind": "series",
        "dimensions": ["day"],
        "x_type": "category",
        "series": [
            {
                "key": f"k{s}",
                "label": f"k{s}",
                "points": [{"x": f"x{p}", "y": 1, "n": 1} for p in range(points)],
            }
            for s in range(count)
        ],
    }


def _one_series(y=1, n=1) -> dict:
    return {"key": "k", "label": "k", "points": [{"x": "a", "y": y, "n": n}]}


def _cell(**over) -> dict:
    return {"x": 0, "y": 0, "value": None, "n": 1, **over}


def _matrix(value_type: str, cells: list) -> dict:
    return {
        "kind": "matrix",
        "value_type": value_type,
        "x_labels": ["a"],
        "y_labels": ["b"],
        "cells": cells,
    }


def _node(node_id: str, parent_id: str | None) -> dict:
    return {
        "id": node_id,
        "parent_id": parent_id,
        "label": node_id,
        "value": 1,
        "measure": None,
    }


def _tree(nodes: list) -> dict:
    return {"kind": "tree", "nodes": nodes}


def _graph(count: int = 2, weight: float = 0.5) -> dict:
    nodes = [{"id": f"g{i}", "label": f"g{i}", "size": 1} for i in range(count)]
    return {
        "kind": "graph",
        "nodes": nodes,
        "edges": [{"source": "g0", "target": f"g{count - 1}", "weight": weight}],
    }


def _instance(**over) -> dict:
    return {"instanceId": "a", "templateId": "pass_rate_trend", **over}


def _widgets(instances: list, **over) -> dict:
    return {"page": "trends", "version": 2, "instances": instances, **over}


def _envelope() -> dict:
    return copy.deepcopy(
        _load(FIXTURES / "envelope" / "valid" / "filtered.json")["payload"]
    )


def _accepts(kind: str, payload) -> bool:
    try:
        vc.validate_contract(kind, payload)
    except ValidationError:
        return False
    return True


# -- caps sit where the README puts them -----------------------------------------------------

# The numbers are the README's, restated rather than read from ``vc.MAX_*``: a
# test that imports the constant agrees with whatever the constant becomes.
_DRILL_DIMENSIONS = ["project", "release", "suite", "test", "branch"]
CAPS = [
    (
        "releases",
        "scope",
        20,
        lambda n: _scope(
            project_id=PROJECT, release_ids=[_release(i) for i in range(n)]
        ),
    ),
    (
        "suites",
        "scope",
        50,
        lambda n: _scope(suite_names=[f"suite-{i}" for i in range(n)]),
    ),
    ("suite name length", "scope", 500, lambda n: _scope(suite_names=["x" * n])),
    ("window days", "scope", 365, lambda n: _scope(window={"days": n})),
    ("series", "chart_series", 8, lambda n: _series(count=n)),
    ("points per series", "chart_series", 366, lambda n: _series(points=n)),
    (
        "matrix cells",
        "chart_series",
        5400,
        lambda n: _matrix("count", [_cell(value=1)] * n),
    ),
    (
        "tree nodes",
        "chart_series",
        500,
        lambda n: _tree(
            [_node("root", None)] + [_node(f"n{i}", "root") for i in range(n - 1)]
        ),
    ),
    ("graph nodes", "chart_series", 200, lambda n: _graph(count=n)),
    (
        "instances",
        "widget_config",
        12,
        lambda n: _widgets([_instance(instanceId=f"i{i}") for i in range(n)]),
    ),
    (
        "title length",
        "widget_config",
        120,
        lambda n: _widgets([_instance(title="t" * n)]),
    ),
    (
        "groupBy",
        "widget_config",
        2,
        lambda n: _widgets([_instance(groupBy=["day", "suite", "release"][:n])]),
    ),
    (
        "drill depth",
        "drill_path",
        4,
        lambda n: {
            "path": [{"dimension": d, "value": "v"} for d in _DRILL_DIMENSIONS[:n]]
        },
    ),
    (
        "drill value length",
        "drill_path",
        2000,
        lambda n: {"path": [{"dimension": "test", "value": "v" * n}]},
    ),
]


@pytest.mark.parametrize(
    "kind, cap, build", [pytest.param(*row[1:], id=row[0]) for row in CAPS]
)
def test_a_cap_admits_the_limit_and_refuses_one_more(kind, cap, build):
    assert _accepts(kind, build(cap)), f"{cap} is within the cap and was refused"
    assert not _accepts(
        kind, build(cap + 1)
    ), f"{cap + 1} is over the cap and was accepted"


@pytest.mark.parametrize(
    "window, accepted",
    [
        ({"from": "2026-01-01", "to": "2026-01-01"}, True),
        ({"from": "2026-01-01", "to": "2027-01-02"}, True),  # 366 days apart
        ({"from": "2026-01-01", "to": "2027-01-03"}, False),  # 367
        ({"from": "2026-02-30", "to": "2026-03-01"}, False),  # not a calendar day
        (
            {"from": "20260101", "to": "20260131"},
            False,
        ),  # ISO basic form is not YYYY-MM-DD
        ({"from": "2026-01-01\n", "to": "2026-01-31"}, False),
        ({"from": "2026-01-01"}, False),
        ({"to": "2026-01-31"}, False),
        ({"days": 1}, True),
        ({"days": 0}, False),
        ({}, False),
        # Real calendar dates, years 0001-9999.
        ({"from": "0001-01-01", "to": "0001-01-01"}, True),
        ({"from": "9999-12-30", "to": "9999-12-31"}, True),
        ({"from": "0000-12-31", "to": "0001-01-01"}, False),
        ({"from": "2028-02-29", "to": "2028-03-01"}, True),
        ({"from": "2026-02-29", "to": "2026-03-01"}, False),
        ({"from": "٢٠٢٦-01-01", "to": "2026-01-31"}, False),  # Arabic-Indic digits
        # null counts as absent inside window, so these are one form each.
        ({"days": 7, "from": None, "to": None}, True),
        ({"days": None, "from": "2026-01-01", "to": "2026-01-02"}, True),
        ({"days": None, "from": None, "to": None}, False),
        # The keys are exactly days/from/to: ``from_`` is unknown, not an alias.
        ({"from_": "2026-01-01", "to": "2026-01-31"}, False),
        ({"days": 7, "from_": "2026-01-01"}, True),
    ],
)
def test_window_forms_and_order(window, accepted):
    assert _accepts("scope", _scope(window=window)) is accepted


def test_no_field_is_populated_by_its_python_name():
    """``from_`` is the attribute; ``from`` is the only wire key, in and out."""
    with pytest.raises(ValidationError, match="window_one_form"):
        vc.ScopeWindow.model_validate({"from_": "2026-01-01", "to": "2026-01-02"})
    applied = _envelope()
    window = applied["scope"]["window"]
    window["from_"] = window.pop("from")
    with pytest.raises(ValidationError) as caught:
        vc.validate_contract("envelope", applied)
    assert _rendered(caught.value) == ["scope.window.from|missing|Field required"]
    model = vc.validate_contract(
        "scope", _scope(window={"from": "2026-01-01", "to": "2026-01-02"})
    )
    assert vc.dump_contract(model)["window"] == {
        "from": "2026-01-01",
        "to": "2026-01-02",
    }
    assert all(
        model_cls.model_config.get("validate_by_name") is False
        for model_cls in vars(vc).values()
        if isinstance(model_cls, type) and issubclass(model_cls, vc.VizContract)
    )


@pytest.mark.parametrize(
    "release_ids, accepted",
    [
        (
            [
                "0f8fad5b-d9cb-469f-a165-70867728950e",
                "0F8FAD5B-D9CB-469F-A165-70867728950E",
            ],
            False,
        ),
        (
            [
                "0f8fad5b-d9cb-469f-a165-70867728950e",
                "0f8fad5b-D9CB-469f-a165-70867728950e",
            ],
            False,
        ),
        (
            [
                "0f8fad5b-d9cb-469f-a165-70867728950e",
                "0f8fad5b-d9cb-469f-a165-70867728950f",
            ],
            True,
        ),
        (["unattributed", "unattributed"], False),
        (["unattributed", "0f8fad5b-d9cb-469f-a165-70867728950e"], True),
    ],
)
def test_unique_release_folds_uuid_case_only(release_ids, accepted):
    payload = _scope(project_id=PROJECT, release_ids=release_ids)
    if accepted:
        # ...and the ids are kept exactly as sent: folding is for comparison only.
        model = vc.validate_contract("scope", payload)
        assert vc.dump_contract(model)["release_ids"] == release_ids
        return
    with pytest.raises(ValidationError) as caught:
        vc.validate_contract("scope", payload)
    assert [line.split("|")[2] for line in _rendered(caught.value)] == [
        "Value error, unique_release: release_ids[1] repeats an earlier item"
    ]


@pytest.mark.parametrize(
    "weight, accepted", [(0, True), (1, True), (1.0001, False), (-0.0001, False)]
)
def test_edge_weight_is_a_closed_unit_interval(weight, accepted):
    assert _accepts("chart_series", _graph(weight=weight)) is accepted


# -- structure rules no fixture reaches ------------------------------------------------------


def test_a_cycle_beside_a_healthy_root_is_still_a_cycle():
    """``tree_cycle.json`` has no root at all, so it never reaches the walk."""
    looped = _tree([_node("root", None), _node("a", "b"), _node("b", "a")])
    with pytest.raises(
        ValidationError, match="tree_acyclic: parent links form a cycle"
    ):
        vc.validate_contract("chart_series", looped)
    with pytest.raises(
        ValidationError, match="tree_acyclic: nodes.1. is its own parent"
    ):
        vc.validate_contract(
            "chart_series", _tree([_node("root", None), _node("a", "a")])
        )


def test_only_a_tree_with_nodes_needs_a_root():
    assert _accepts("chart_series", _tree([]))
    with pytest.raises(ValidationError, match="tree_acyclic: at least one node"):
        vc.validate_contract("chart_series", _tree([_node("a", "b"), _node("b", "a")]))


def test_a_deep_chain_is_a_tree_not_a_stack_overflow():
    chain = [_node("n0", None)] + [_node(f"n{i}", f"n{i - 1}") for i in range(1, 500)]
    assert _accepts("chart_series", _tree(chain))
    assert _accepts("chart_series", _tree(list(reversed(chain))))


def test_node_ids_are_unique_in_a_tree_and_in_a_graph():
    with pytest.raises(ValidationError, match="unique_node_id"):
        vc.validate_contract(
            "chart_series", _tree([_node("a", None), _node("a", None)])
        )
    graph = _graph()
    graph["nodes"].append(dict(graph["nodes"][0]))
    with pytest.raises(ValidationError, match="unique_node_id"):
        vc.validate_contract("chart_series", graph)


def test_a_matrix_value_matches_its_value_type():
    assert _accepts("chart_series", _matrix("status", [_cell(value=None)]))
    assert _accepts("chart_series", _matrix("rate", [_cell(value=96.4)]))
    assert not _accepts("chart_series", _matrix("rate", [_cell(value="passed")]))
    assert not _accepts("chart_series", _matrix("status", [_cell(value=1)]))
    assert not _accepts("chart_series", _matrix("rate", [_cell(y=1)]))


# The README's C2 rows, in order. Five of these are nullable; none is optional.
ENVELOPE_KEYS = [
    "schema_version",
    "scope",
    "totals",
    "pass_rate_basis",
    "ignored_filters",
    "truncated",
    "truncated_total",
    "measured",
    "reason",
    "includes_in_progress",
    "partial_day",
    "generated_at",
    "as_of",
]


@pytest.mark.parametrize("key", ENVELOPE_KEYS)
def test_every_envelope_key_is_required_even_when_nullable(key):
    payload = _envelope()
    assert sorted(payload) == sorted(ENVELOPE_KEYS)
    del payload[key]
    with pytest.raises(ValidationError) as caught:
        vc.validate_contract("envelope", payload)
    assert f"{key}|missing|Field required" in _rendered(caught.value)


def test_envelope_conditionals_and_timestamps():
    blank = _envelope() | {"measured": False, "reason": "   "}
    assert not _accepts("envelope", blank)
    assert not _accepts(
        "envelope", _envelope() | {"truncated": True, "truncated_total": None}
    )
    assert _accepts("envelope", _envelope() | {"truncated": True, "truncated_total": 0})
    executions = _envelope()
    executions["totals"] |= {"matched_executions": 9, "total_executions": 8}
    assert not _accepts("envelope", executions)
    # An unfiltered view matches everything: equal is a subset, not an overflow.
    unfiltered = _envelope()
    unfiltered["totals"] = {
        "matched_runs": 143,
        "total_runs": 143,
        "matched_executions": 0,
        "total_executions": 0,
    }
    assert _accepts("envelope", unfiltered)
    assert _accepts(
        "envelope", _envelope() | {"as_of": "2026-09-19T10:42:07.123456+00:00"}
    )
    assert not _accepts(
        "envelope", _envelope() | {"as_of": "2026-09-19T10:42:07+02:00"}
    )
    assert not _accepts("envelope", _envelope() | {"as_of": "2026-09-19T10:42:07"})
    assert not _accepts("envelope", _envelope() | {"partial_day": "19/09/2026"})


def _only_error(kind: str, payload) -> str:
    """The single error ``payload`` produces, as ``loc|type|msg``."""
    with pytest.raises(ValidationError) as caught:
        vc.validate_contract(kind, payload)
    rendered = _rendered(caught.value)
    assert len(rendered) == 1, rendered
    return rendered[0]


# -- utc_instant -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instant, accepted",
    [
        ("2026-09-19T10:42:07Z", True),
        ("2026-09-19T10:42:07+00:00", True),
        ("2026-09-19T10:42:07.1Z", True),
        ("2026-09-19T10:42:07.123456Z", True),
        ("2026-09-19T23:59:59Z", True),
        ("2028-02-29T00:00:00Z", True),
        ("2026-09-19T24:00:00Z", False),  # Date.parse takes it; the contract does not
        ("2026-02-30T10:42:07Z", False),
        ("2026-09-19T10:60:00Z", False),
        ("2026-09-19T10:42:60Z", False),
        ("2026-09-19 10:42:07Z", False),
        ("2026-09-19T10:42:07+0000", False),
        ("2026-09-19T10:42:07-00:00", False),
        ("2026-09-19T10:42:07+02:00", False),
        ("2026-09-19T10:42:07z", False),
        ("2026-09-19T10:42:07", False),
        ("2026-09-19T10:42:07.1234567Z", False),
        ("2026-09-19T10:42:07.Z", False),
        ("2026-09-19T10:42Z", False),
        ("20260919T104207Z", False),
        ("2026-09-19T10:42:07Z\n", False),
        ("2026-09-19T1٠:42:07Z", False),  # an Arabic-Indic digit
    ],
)
@pytest.mark.parametrize("key", ["generated_at", "as_of"])
def test_utc_instant_is_the_pinned_profile(key, instant, accepted):
    payload = _envelope() | {key: instant}
    if accepted:
        assert _accepts("envelope", payload)
    else:
        assert _only_error("envelope", payload).startswith(
            f"{key}|value_error|Value error, utc_instant:"
        )


# -- counts (change rule 3) ------------------------------------------------------------------


def _set_total(key):
    def build(v):
        payload = _envelope()
        # matched_* at 0 so an honest total of 1 is not a totals_subset breach.
        payload["totals"] |= {"matched_runs": 0, "matched_executions": 0, key: v}
        return payload

    return build


def _applied_days(v):
    payload = _envelope()
    payload["scope"]["window"]["days"] = v
    return payload


# Every field change rule 3 calls a count, with a builder that puts ``v`` there.
COUNT_FIELDS = [
    ("n", "chart_series", lambda v: _series() | {"series": [_one_series(n=v)]}),
    ("totals.matched_runs", "envelope", _set_total("matched_runs")),
    ("totals.total_runs", "envelope", _set_total("total_runs")),
    ("totals.matched_executions", "envelope", _set_total("matched_executions")),
    ("totals.total_executions", "envelope", _set_total("total_executions")),
    (
        "includes_in_progress",
        "envelope",
        lambda v: _envelope() | {"includes_in_progress": v},
    ),
    ("truncated_total", "envelope", lambda v: _envelope() | {"truncated_total": v}),
    ("schema_version", "envelope", lambda v: _envelope() | {"schema_version": v}),
    ("scope.window.days", "envelope", _applied_days),
    ("version", "widget_config", lambda v: _widgets([], version=v)),
    # Rate matrices: the index rules must not depend on value_type.
    ("matrix x", "chart_series", lambda v: _matrix("rate", [_cell(x=v)])),
    ("matrix y", "chart_series", lambda v: _matrix("rate", [_cell(y=v)])),
    (
        "matrix count value",
        "chart_series",
        lambda v: _matrix("count", [_cell(value=v)]),
    ),
]


@pytest.mark.parametrize(
    "value, rule",
    [
        (1.5, "integer_count"),
        (1.0, "integer_count"),  # JavaScript cannot tell it from 1; only this side can
        (2**53, "safe_integer"),
        (10**30, "safe_integer"),
        (-1, "non_negative"),
    ],
)
@pytest.mark.parametrize(
    "kind, build", [pytest.param(*row[1:], id=row[0]) for row in COUNT_FIELDS]
)
def test_a_count_is_a_safe_non_negative_integer(kind, build, value, rule):
    assert _accepts(kind, build(0)) or _accepts(kind, build(1)), "no honest payload"
    assert f"|value_error|Value error, {rule}:" in _only_error(kind, build(value))


@pytest.mark.parametrize(
    "days", [7.5, 1.0, 0, -1, 366, 2**53, 10**30, True, False, "30", [7], {"d": 7}]
)
def test_every_bad_scope_window_days_is_window_days_range(days):
    """Change rule 3, first refinement: the C1 window never reports a count id."""
    assert _only_error("scope", _scope(window={"days": days})).startswith(
        "window.days|value_error|Value error, window_days_range:"
    )


@pytest.mark.parametrize("days", [1, 30, 365])
def test_scope_window_days_accepts_one_to_365(days):
    assert _accepts("scope", _scope(window={"days": days}))


def test_a_negative_matrix_index_is_non_negative_not_cell_index_range():
    """Change rule 3, second refinement."""
    for axis in ("x", "y"):
        line = _only_error("chart_series", _matrix("rate", [_cell(**{axis: -1})]))
        assert f"cells.0.{axis}|value_error|Value error, non_negative:" in line
    past = _only_error("chart_series", _matrix("rate", [_cell(x=1)]))
    assert "Value error, cell_index_range:" in past


def test_the_largest_safe_integer_is_still_a_count():
    top = 2**53 - 1
    payload = _envelope()
    payload["totals"] |= {"total_runs": top, "matched_runs": top}
    payload |= {"includes_in_progress": top, "schema_version": top}
    assert _text(vc.dump_contract(vc.validate_contract("envelope", payload))) == _text(
        payload
    )
    assert _accepts("chart_series", _matrix("count", [_cell(value=top, n=top)]))


# -- numbers keep their form -----------------------------------------------------------------

# Every non-count number: (name, kind, build(v), read(dumped)).
NUMBER_FIELDS = [
    (
        "y",
        "chart_series",
        lambda v: _series() | {"series": [_one_series(y=v)]},
        lambda d: d["series"][0]["points"][0]["y"],
    ),
    (
        "rate value",
        "chart_series",
        lambda v: _matrix("rate", [_cell(value=v)]),
        lambda d: d["cells"][0]["value"],
    ),
    (
        "tree value",
        "chart_series",
        lambda v: _tree([_node("root", None) | {"value": v}]),
        lambda d: d["nodes"][0]["value"],
    ),
    (
        "measure",
        "chart_series",
        lambda v: _tree([_node("root", None) | {"measure": v}]),
        lambda d: d["nodes"][0]["measure"],
    ),
    (
        "size",
        "chart_series",
        lambda v: _graph()
        | {"nodes": [{"id": "g0", "label": "g", "size": v}], "edges": []},
        lambda d: d["nodes"][0]["size"],
    ),
    (
        "weight",
        "chart_series",
        lambda v: _graph(count=1, weight=v),
        lambda d: d["edges"][0]["weight"],
    ),
]


@pytest.mark.parametrize("value", [0, 1, 1.0, 0.25, 0.0])
@pytest.mark.parametrize(
    "kind, build, read", [pytest.param(*row[1:], id=row[0]) for row in NUMBER_FIELDS]
)
def test_a_number_keeps_its_int_or_float_form(kind, build, read, value):
    dumped = vc.dump_contract(vc.validate_contract(kind, build(value)))
    assert type(read(dumped)) is type(value) and read(dumped) == value


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf"), True, "1"]
)
@pytest.mark.parametrize(
    "kind, build, read", [pytest.param(*row[1:], id=row[0]) for row in NUMBER_FIELDS]
)
def test_a_number_is_a_finite_json_number(kind, build, read, value):
    assert not _accepts(kind, build(value))


def test_number_range_rules_name_themselves():
    assert "Value error, weight_range:" in _only_error(
        "chart_series", _graph(weight=1.5)
    )
    negative = _tree([_node("root", None) | {"value": -1}])
    assert "Value error, non_negative:" in _only_error("chart_series", negative)


# -- measured_reason whitespace (change rule 7) ----------------------------------------------

# Exactly the ECMAScript WhiteSpace + LineTerminator set, restated from the README.
ECMASCRIPT_WHITESPACE = [
    *range(0x09, 0x0E),
    0x20,
    0xA0,
    0x1680,
    *range(0x2000, 0x200B),
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
    0xFEFF,
]
# ``str.strip()`` removes these too; JavaScript does not call them whitespace.
PYTHON_ONLY_WHITESPACE = [0x1C, 0x1D, 0x1E, 0x1F, 0x85]


def _not_measured(reason) -> dict:
    return _envelope() | {"measured": False, "reason": reason}


@pytest.mark.parametrize("code", ECMASCRIPT_WHITESPACE, ids=hex)
def test_a_reason_of_only_ecmascript_whitespace_is_blank(code):
    for reason in (chr(code), chr(code) * 3 + " "):
        assert "Value error, measured_reason:" in _only_error(
            "envelope", _not_measured(reason)
        )
    assert _accepts("envelope", _not_measured(chr(code) + "x" + chr(code)))


@pytest.mark.parametrize("code", PYTHON_ONLY_WHITESPACE + [0x200B, 0x180E], ids=hex)
def test_a_character_outside_the_set_is_not_blank(code):
    assert _accepts("envelope", _not_measured(chr(code)))


def test_an_empty_or_missing_reason_is_blank():
    for reason in ("", None):
        assert "Value error, measured_reason:" in _only_error(
            "envelope", _not_measured(reason)
        )


# -- payload-wide rules (change rule 8) ------------------------------------------------------

LONE_HIGH = chr(0xD83D)
LONE_LOW = chr(0xDE00)


def _nest(levels: int, leaf=0):
    """``levels`` nested containers, alternating arrays and objects. Iterative:
    the 100 000-deep case must not need a deep Python stack to build."""
    value = leaf
    for level in range(levels):
        value = [value] if level % 2 else {"n": value}
    return value


# A valid payload per contract with a slot for arbitrary data: (kind, build(extra)).
# ``extra`` goes under an unknown key, which the payload-wide rules still reach.
CARRIERS = [
    ("scope", lambda extra: _scope(futureKey=extra)),
    ("envelope", lambda extra: _envelope() | {"futureKey": extra}),
    ("chart_series", lambda extra: _series() | {"futureKey": extra}),
    ("widget_config", lambda extra: _widgets([], futureKey=extra)),
    ("drill_path", lambda extra: {"path": [], "futureKey": extra}),
]


def _payload_wide_error(kind: str, payload) -> str:
    line = _only_error(kind, payload)
    assert line.startswith("|value_error|Value error, "), line
    return line.split("|", 2)[2].removeprefix("Value error, ")


@pytest.mark.parametrize(
    "kind, build",
    [pytest.param(*row) for row in CARRIERS],
    ids=[row[0] for row in CARRIERS],
)
def test_payload_wide_rules_reach_unknown_keys_in_every_contract(kind, build):
    assert _accepts(kind, build({"ok": ["fine", 1, None]}))
    for bad in (LONE_HIGH, "a" + LONE_LOW, "x" + LONE_LOW + LONE_HIGH):
        assert _payload_wide_error(kind, build([bad])).startswith("well_formed_string:")
        assert _payload_wide_error(kind, build({bad: 1})).startswith(
            "well_formed_string:"
        )
    for key in ("__proto__", "constructor", "prototype"):
        assert _payload_wide_error(kind, build({"deeper": {key: 1}})).startswith(
            "forbidden_key:"
        )
    # A forbidden word as a VALUE is plain text.
    assert _accepts(kind, build(["__proto__", "constructor", "prototype"]))
    # The payload is container 1 and ``futureKey`` holds the rest.
    assert _accepts(kind, build(_nest(31)))
    assert _payload_wide_error(kind, build(_nest(32))).startswith("nesting_depth:")


def test_an_astral_character_is_one_well_formed_code_point():
    emoji = json.loads('"\\ud83d\\ude00"')  # a valid pair, joined by the decoder
    assert len(emoji) == 1
    assert _accepts("scope", _scope(suite_names=[emoji * 500]))


def test_a_lone_surrogate_in_a_known_field_is_reported_before_the_string_check():
    """Pydantic's own str check would say ``string_unicode``; the walk runs first."""
    payload = _scope(suite_names=["half an emoji " + LONE_HIGH])
    assert _payload_wide_error("scope", payload).startswith("well_formed_string:")


@pytest.mark.parametrize(
    "model, payload",
    [
        (vc.Scope, _scope(suite_names=[LONE_HIGH])),
        (vc.EnvelopeMeta, _envelope() | {"reason": LONE_LOW}),
        (vc.ChartSeries, _series() | {"dimensions": [LONE_HIGH]}),
        (vc.WidgetConfig, _widgets([_instance(filters={LONE_HIGH: 1})])),
        (vc.DrillPath, {"path": [{"dimension": "test", "value": LONE_HIGH}]}),
    ],
    ids=lambda value: getattr(value, "__name__", ""),
)
def test_direct_model_use_runs_the_payload_wide_rules_too(model, payload):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    rendered = _rendered(caught.value)
    assert len(rendered) == 1 and "Value error, well_formed_string:" in rendered[0]


def test_filters_are_walked_like_everything_else():
    nested = _widgets([_instance(filters={"a": {"b": [{"__proto__": {}}]}})])
    assert _payload_wide_error("widget_config", nested).startswith("forbidden_key:")
    assert _payload_wide_error(
        "widget_config", _widgets([_instance(constructor=1)])
    ).startswith("forbidden_key:")


def test_the_deepest_accepted_payload_dumps_and_one_more_level_is_refused():
    """Nesting is refused at validation, so ``dump_contract`` never meets a
    payload it cannot serialise. Payload 1, instances 2, instance 3, filters 4."""
    deepest = _widgets([_instance(filters=_nest(29))])
    model = vc.validate_contract("widget_config", deepest)
    assert _text(vc.dump_contract(model)) == _text(deepest)
    assert vc.dump_contract(vc.WidgetConfig.model_validate(deepest)) == deepest
    too_deep = _widgets([_instance(filters=_nest(30))])
    assert _payload_wide_error("widget_config", too_deep).startswith("nesting_depth:")
    with pytest.raises(ValidationError, match="nesting_depth"):
        vc.WidgetConfig.model_validate(too_deep)


def test_a_hundred_thousand_levels_is_a_finding_not_a_recursion_error():
    hostile = _widgets([_instance(filters=_nest(100_000))])
    for kind in vc.CONTRACT_MODELS:
        payload = hostile if kind == "widget_config" else {"futureKey": _nest(100_000)}
        assert _payload_wide_error(kind, payload).startswith("nesting_depth:")
    with pytest.raises(ValidationError, match="nesting_depth"):
        vc.WidgetConfig.model_validate(hostile)


def test_payload_size_is_documented_and_matches_the_constant():
    """``payload_size`` is README prose, not a table row, so no fixture proves
    it; this ties the number and the id to the text instead."""
    text = (CONTRACTS / "README.md").read_text(encoding="utf-8")
    assert "`payload_size`" in text
    assert "more than 1 000 000 values and keys" in text
    assert vc.MAX_PAYLOAD_NODES == 1_000_000
    assert "payload_size" not in _readme_payload_wide_rule_ids()


def test_payload_size_counts_values_and_keys_and_stops_at_a_million():
    # The payload (1) + key "k" (1) + its value, the list (1) + n items.
    at_limit = {"k": [0] * (1_000_000 - 3)}
    assert vc.payload_violation(at_limit) is None
    at_limit["k"].append(0)
    assert vc.payload_violation(at_limit).startswith("payload_size:")
    # An object entry is a key AND a value.
    entries = {str(i): 0 for i in range(500_000)}
    assert vc.payload_violation(entries).startswith("payload_size:")
    del entries["0"]
    assert vc.payload_violation(entries) is None


@pytest.mark.parametrize("kind", sorted(vc.CONTRACT_MODELS))
def test_payload_size_is_refused_in_every_contract(kind):
    flat = {"futureKey": [0] * vc.MAX_PAYLOAD_NODES}
    assert _payload_wide_error(kind, flat).startswith("payload_size:")


# -- strict scalars --------------------------------------------------------------------------


def _totals(matched_runs) -> dict:
    payload = _envelope()
    payload["totals"]["matched_runs"] = matched_runs
    return payload


# (contract, build(value), the honest value, its lax-mode impostor). The honest
# value is asserted too: a payload broken somewhere else would make every
# impostor below "rejected" without strictness having done anything.
IMPOSTORS = [
    ("bool as days", "scope", lambda v: _scope(window={"days": v}), 1, True),
    ("str as days", "scope", lambda v: _scope(window={"days": v}), 30, "30"),
    ("float as days", "scope", lambda v: _scope(window={"days": v}), 30, 30.0),
    ("int as suite name", "scope", lambda v: _scope(suite_names=[v]), "7", 7),
    (
        "bool as sample size",
        "chart_series",
        lambda v: _matrix("count", [_cell(n=v)]),
        1,
        True,
    ),
    (
        "bool as value",
        "chart_series",
        lambda v: _matrix("count", [_cell(value=v)]),
        1,
        True,
    ),
    (
        "str as value",
        "chart_series",
        lambda v: _matrix("count", [_cell(value=v)]),
        1,
        "1",
    ),
    (
        "nan as value",
        "chart_series",
        lambda v: _matrix("rate", [_cell(value=v)]),
        0.5,
        float("nan"),
    ),
    (
        "infinity as y",
        "chart_series",
        lambda v: _series() | {"series": [_one_series(y=v)]},
        2,
        float("inf"),
    ),
    (
        "bool as y",
        "chart_series",
        lambda v: _series() | {"series": [_one_series(y=v)]},
        1,
        True,
    ),
    (
        "int as label",
        "chart_series",
        lambda v: _tree([_node("root", None) | {"label": v}]),
        "7",
        7,
    ),
    ("bool as version", "widget_config", lambda v: _widgets([], version=v), 1, True),
    ("int as page", "widget_config", lambda v: _widgets([], page=v), "3", 3),
    ("str as topN", "widget_config", lambda v: _widgets([_instance(topN=v)]), 10, "10"),
    (
        "null as title",
        "widget_config",
        lambda v: _widgets([_instance(title=v)]),
        "Daily",
        None,
    ),
    (
        "list as filters",
        "widget_config",
        lambda v: _widgets([_instance(filters=v)]),
        {"suites": ["a"]},
        ["a"],
    ),
    (
        "int as drill value",
        "drill_path",
        lambda v: {"path": [{"dimension": "test", "value": v}]},
        "7",
        7,
    ),
    (
        "bool as in-progress count",
        "envelope",
        lambda v: _envelope() | {"includes_in_progress": v},
        0,
        False,
    ),
    (
        "int as truncated",
        "envelope",
        lambda v: _envelope() | {"truncated": v},
        False,
        0,
    ),
    (
        "str as schema version",
        "envelope",
        lambda v: _envelope() | {"schema_version": v},
        1,
        "1",
    ),
    ("bool as matched runs", "envelope", _totals, 1, True),
]


@pytest.mark.parametrize(
    "kind, build, honest, impostor",
    [pytest.param(*row[1:], id=row[0]) for row in IMPOSTORS],
)
def test_scalars_are_not_coerced(kind, build, honest, impostor):
    assert _accepts(kind, build(honest))
    assert not _accepts(kind, build(impostor))


# -- additive only ---------------------------------------------------------------------------


def test_unknown_keys_are_tolerated_everywhere():
    assert _accepts("scope", _scope(futureKey=1, window={"days": 7, "futureKey": [1]}))
    future = _envelope() | {"futureKey": {"a": 1}}
    future["totals"]["futureKey"] = 1
    assert _accepts("envelope", future)
    assert _accepts("chart_series", _series() | {"futureKey": 1})
    assert _accepts(
        "drill_path",
        {"path": [{"dimension": "test", "value": "v", "futureKey": 1}], "futureKey": 1},
    )


def test_widget_config_preserves_unknown_keys_on_a_round_trip():
    """Two writers share ``saved_views.filters``: a key this side does not
    know yet belongs to the other one, and stripping it is data loss."""
    stored = _widgets(
        [
            _instance(
                chartType="line",
                release_id="22222222-2222-4222-8222-222222222221",
                futureKey={"nested": [1, None]},
            ),
            _instance(instanceId="b", futureKey=None),
        ],
        release_id="unattributed",
        futureKey=["kept", 1, None],
    )
    model = vc.validate_contract("widget_config", stored)
    assert vc.dump_contract(model) == stored
    assert (
        vc.dump_contract(vc.validate_contract("widget_config", vc.dump_contract(model)))
        == stored
    )
    # ...and an absent optional key is not written back as null.
    assert "title" not in vc.dump_contract(model)["instances"][0]


# -- untrusted text --------------------------------------------------------------------------


def _strings(value) -> list[bytes]:
    if isinstance(value, str):
        return [value.encode("utf-8")]
    if isinstance(value, dict):
        return [
            item
            for key in sorted(value)
            for item in _strings(key) + _strings(value[key])
        ]
    if isinstance(value, list):
        return [item for element in value for item in _strings(element)]
    return []


@pytest.mark.parametrize("path", _cases("valid", "*hostile*"))
def test_hostile_text_survives_validation_byte_for_byte(path):
    payload = _load(path)["payload"]
    dumped = vc.dump_contract(vc.validate_contract(path.parts[-3], payload))
    assert dumped == payload
    assert _strings(dumped) == _strings(payload)
    assert any(
        b"<" in text for text in _strings(dumped)
    ), "the fixture no longer carries markup"


def test_whitespace_and_case_are_not_normalised():
    names = ["  padded  ", "Padded", "padded", "tab\there", "‮reversed"]
    model = vc.validate_contract("scope", _scope(suite_names=names))
    assert vc.dump_contract(model)["suite_names"] == names


# -- feature flags ---------------------------------------------------------------------------


def _flags() -> list[dict]:
    flags = json.loads((CONTRACTS / "flags.json").read_text(encoding="utf-8"))["flags"]
    assert (
        len(flags) == 6
    ), "flags.json no longer lists six flags: a new flag is a new migration, not an edit to 0192"
    return flags


def _migration():
    spec = importlib.util.spec_from_file_location("m0192", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed(monkeypatch, step: str) -> list:
    """Run one migration step with ``op.execute`` captured: the statements it
    would send, which is not always what its constants say."""
    from alembic import op

    statements: list = []
    monkeypatch.setattr(op, "execute", statements.append, raising=False)
    getattr(_migration(), step)()
    return statements


def test_flag_constants_match_flags_json():
    keys = [flag["key"] for flag in _flags()]
    assert list(VIZ_FLAG_KEYS) == keys
    assert len(set(keys)) == len(keys)
    width = FeatureFlag.__table__.c.key.type.length
    assert all(len(key) <= width for key in keys)


def test_migration_0192_follows_0191():
    migration = _migration()
    assert migration.revision == "0192"
    assert migration.down_revision == "0191"
    claims = [
        path.name
        for path in MIGRATION.parent.glob("*.py")
        if re.search(
            r'^down_revision\s*=\s*["\']0191["\']',
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    ]
    assert claims == [MIGRATION.name], f"0191 has more than one child: {claims}"


def test_migration_0192_seeds_the_flags_json_rows_disabled(monkeypatch):
    statements = _executed(monkeypatch, "upgrade")
    seeded = []
    for statement in statements:
        sql = " ".join(statement.text.split())
        shape = re.fullmatch(
            r"INSERT INTO feature_flags \((?P<columns>[^)]*)\) VALUES \((?P<values>.*)\) ON CONFLICT \(key\) DO NOTHING",
            sql,
        )
        assert shape, f"not an idempotent feature_flags insert: {sql}"
        columns = [column.strip() for column in shape["columns"].split(",")]
        values = [
            value.strip() for value in re.split(r",\s*(?![^()]*\))", shape["values"])
        ]
        assert len(columns) == len(values)
        row = dict(zip(columns, values))
        assert row["enabled_global"] == "false"
        # Off by the switch alone, like 0066/0158: flipping it is the one edit.
        assert row["rollout_percent"] == "100"
        assert (row["key"], row["description"]) == (":key", ":description")
        params = statement.compile().params
        seeded.append({"key": params["key"], "description": params["description"]})
    assert seeded == [
        {"key": flag["key"], "description": flag["description"]} for flag in _flags()
    ]


def test_migration_0192_downgrade_removes_exactly_the_six_keys(monkeypatch):
    statements = _executed(monkeypatch, "downgrade")
    assert {" ".join(statement.text.split()) for statement in statements} == {
        "DELETE FROM feature_flags WHERE key = :key"
    }
    removed = [statement.compile().params["key"] for statement in statements]
    assert sorted(removed) == sorted(VIZ_FLAG_KEYS)
