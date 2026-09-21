"""Visualization contracts C1-C5 as Pydantic models.

The specification is ``contracts/viz/README.md``. The fixtures beside it are
checked twice -- here by ``tests/test_viz_contracts.py`` and on the other side
of the seam by ``frontend/src/lib/viz/contracts.test.ts`` -- so a rule is
written in the README first and lands here second. When a message below starts
with an identifier (``totals_subset: ...``) it is the rule id from the README
tables, the same string an ``invalid/`` fixture carries as ``violates``. Rules a
field constraint can express exactly (caps, lengths, closed sets) are declared
on the field instead, so they also reach the OpenAPI schema.

Properties that hold for every model:

* **Additive only.** Unknown keys are tolerated, so a newer writer does not
  break an older reader. The widget config goes further and *preserves* them
  (``extra="allow"``): the frontend writes the same stored object.
* **Strict scalars.** ``True`` is not a count, ``"30"`` is not a number and
  ``7`` is not a label. Lax coercion would accept all three and let the two
  validators disagree about a payload without either of them failing.
* **Wire names only.** A field is populated by its wire key and nothing else:
  ``from_`` is an unknown key, not a second spelling of ``from``.
* **Numbers keep their form.** ``3`` comes back as ``3``, never ``3.0``, so a
  dump is the payload it was validated from, text for text.
* **Untrusted text.** Test, suite, release and label strings come from ingested
  CI files. Nothing here strips, escapes or rejects markup; treating the value
  as text is the renderer's job, and a validator that "cleaned" it would make
  the filter stop matching the row it was built from.
* **Payload-wide rules first.** Before any field is read, one bounded,
  iterative walk checks every string and key of the whole payload -- unknown
  keys included -- for ``well_formed_string``, ``nesting_depth`` and
  ``forbidden_key`` (README change rule 8).

Ids and dates stay ``str`` on purpose: the wire format is a string, a payload
round-trips unchanged, and strict mode would refuse a string for a ``UUID`` or
``date`` field anyway.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Annotated, Any, Callable, Iterable, Literal, Union, get_args

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainValidator,
    RootModel,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.core.release_filter import UNATTRIBUTED
from app.models.postgres import TestStatus

# ── Vocabulary and caps ──────────────────────────────────────

#: Derived from the column's enum, never restated: the contract spells a status
#: in lower case and ``TestStatus`` stores it in upper case. A sixth status has
#: to reach the README and the frontend guard too, which is what
#: ``test_status_vocab_matches_the_readme_and_the_column_enum`` is for.
STATUS_VOCAB: tuple[str, ...] = tuple(status.value.lower() for status in TestStatus)

Dimension = Literal[
    "day",
    "week",
    "project",
    "release",
    "suite",
    "status",
    "failure_category",
    "branch",
    "environment",
    "ingestion_source",
    "test",
    "error_signature",
]
DIMENSIONS: tuple[str, ...] = get_args(Dimension)

ChartType = Literal[
    "line", "bar", "area", "pie", "gauge", "metric", "table", "stacked_bar", "donut"
]
CHART_TYPES: tuple[str, ...] = get_args(ChartType)

MAX_RELEASES = 20
MAX_SUITES = 50
MAX_SUITE_NAME_LENGTH = 500
MAX_WINDOW_DAYS = 365
MAX_WINDOW_SPAN_DAYS = 366
MAX_SERIES = 8
MAX_POINTS_PER_SERIES = 366
MAX_MATRIX_CELLS = 5400
MAX_TREE_NODES = 500
MAX_GRAPH_NODES = 200
MAX_INSTANCES = 12
MAX_TITLE_LENGTH = 120
MAX_GROUP_BY = 2
MAX_DRILL_DEPTH = 4
MAX_DRILL_VALUE_LENGTH = 2000

#: Change rule 3: the largest integer JavaScript reads exactly.
MAX_SAFE_INTEGER = 2**53 - 1
#: Change rule 8: containers, counting the payload itself as 1.
MAX_NESTING_DEPTH = 32
#: ``payload_size`` (README prose under change rule 8, no table row): values
#: plus object keys in one payload. A full matrix is about 50 000.
MAX_PAYLOAD_NODES = 1_000_000
FORBIDDEN_KEYS: frozenset[str] = frozenset({"__proto__", "constructor", "prototype"})
CHART_KINDS: tuple[str, ...] = ("series", "matrix", "tree", "graph")

# Every pattern below is ASCII-only on purpose: Python's ``\d`` also matches
# Arabic-Indic and full-width digits, JavaScript's does not.

# The canonical hyphenated form only. ``uuid.UUID()`` also takes braces, a URN
# prefix and 32 bare hex digits, none of which the frontend guard would accept.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: C2 ``utc_instant``, the README's RFC 3339 profile, written with ``[0-9]``.
_UTC_INSTANT_RE = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}):[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|\+00:00)"
)
#: Change rule 7: exactly the ECMAScript WhiteSpace + LineTerminator set, never
#: ``str.strip()`` (which also strips U+001C-U+001F and U+0085).
_BLANK_RE = re.compile(
    r"[\u0009-\u000D\u0020\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]*"
)
#: Change rule 8 ``well_formed_string``. ``json.loads`` joins a valid UTF-16
#: pair into one code point, so any surrogate code point left in a Python
#: ``str`` is one JSON sent unpaired (or Python code built by hand).
_SURROGATE_RE = re.compile(r"[\uD800-\uDFFF]")


def _is_uuid(value: str) -> bool:
    return _UUID_RE.fullmatch(value) is not None


def _parse_day(value: str) -> date:
    """``YYYY-MM-DD`` naming a real calendar day, years 0001-9999.

    ``date.fromisoformat`` alone also takes ``20260917`` and ``2026-W38-4``.
    """
    if not _DAY_RE.fullmatch(value):
        raise ValueError("expected YYYY-MM-DD")
    return date.fromisoformat(value)


def _check_day(value: str | None) -> str | None:
    if value is not None:
        _parse_day(value)
    return value


def _check_utc_instant(value: str) -> str:
    """C2 ``utc_instant``: the pinned profile, a real date, hour 00-23, then a parse.

    ``datetime.fromisoformat`` alone takes a space separator, ``+0000``, basic
    format and any offset; JavaScript's ``Date.parse`` takes ``T24:00:00``.
    """
    match = _UTC_INSTANT_RE.fullmatch(value)
    if match is None:
        raise ValueError(
            "utc_instant: expected YYYY-MM-DDTHH:MM:SS[.ffffff] then Z or +00:00"
        )
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        raise ValueError("utc_instant: the date is not a real calendar date") from None
    if int(match.group(2)) > 23:
        raise ValueError("utc_instant: the hour is 00-23")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("utc_instant: the time is not a real time of day") from None
    return value


def _check_count(value: Any) -> Any:
    """Change rule 3, ahead of strict ``int`` so each failure names its rule.

    Anything that is not a JSON number (``True``, ``"3"``) passes through for
    strict ``int`` to refuse as a type error.
    """
    if isinstance(value, float):
        # ``1.0`` too: JSON text with a fraction is not a count, whatever its value.
        raise ValueError("integer_count: a count is a number with no fraction")
    if isinstance(value, int) and not isinstance(value, bool):
        if value > MAX_SAFE_INTEGER:
            raise ValueError("safe_integer: a count is at most 2**53 - 1")
        if value < 0:
            raise ValueError("non_negative: a count is not negative")
    return value


# The bounds come before the validator so they reach the JSON schema as
# ``minimum``/``maximum``; the validator runs first either way.
#: Change rule 3: an integer >= 0 with no fraction, at most 2**53 - 1.
Count = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER), BeforeValidator(_check_count)]
#: A count that starts at 1 (``schema_version``, ``version``).
PositiveCount = Annotated[
    int, Field(ge=1, le=MAX_SAFE_INTEGER), BeforeValidator(_check_count)
]


def _check_window_days(value: Any) -> Any:
    """C1 ``window_days_range`` owns EVERY bad scope ``window.days`` -- a fraction,
    0, 366, 2**53, ``true``, ``"30"`` -- so the count ids never fire here
    (change rule 3, first refinement). ``None`` is "absent" and passes."""
    if value is None:
        return value
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_WINDOW_DAYS
    ):
        raise ValueError(
            f"window_days_range: window.days is an integer 1-{MAX_WINDOW_DAYS}"
        )
    return value


#: C1 ``window_days_range``: an integer 1-365, and nothing else reports on it.
WindowDays = Annotated[
    int, Field(ge=1, le=MAX_WINDOW_DAYS), BeforeValidator(_check_window_days)
]


def _finite_number(
    rule: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> PlainValidator:
    """A JSON number, returned AS IS: ``3`` stays an ``int``, ``3.0`` a ``float``.

    Strict ``float`` would turn ``3`` into ``3.0``, and a ``StrictInt | float``
    union reports two errors for one bad value. One plain validator does both
    jobs and fails once, with ``rule`` naming a range breach.
    """

    def check(value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid_type: expected a number")
        # JSON has no NaN or Infinity, so a number on this seam is always finite.
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("invalid_type: expected a finite number")
        if (minimum is not None and value < minimum) or (
            maximum is not None and value > maximum
        ):
            bounds = f"{minimum if minimum is not None else '-inf'}..{maximum if maximum is not None else 'inf'}"
            raise ValueError(f"{rule}: expected a number in {bounds}")
        return value

    return PlainValidator(check, json_schema_input_type=Union[int, float])


#: Any finite number (``y``, a rate cell, ``measure``).
Number = Annotated[Union[int, float], _finite_number()]
#: A finite number >= 0 (tree ``value``, graph ``size``).
NonNegativeNumber = Annotated[
    Union[int, float], _finite_number("non_negative", minimum=0)
]
#: C3 ``weight_range``.
UnitNumber = Annotated[
    Union[int, float], _finite_number("weight_range", minimum=0, maximum=1)
]


# ── Payload-wide rules (README change rule 8) ────────────────


def payload_violation(payload: Any) -> str | None:
    """The first change-rule-8 breach in ``payload`` as ``"rule_id: why"``, or ``None``.

    Iterative, so a 100 000-deep payload costs a list, not the C stack. It stops
    at the first breach, never descends past ``MAX_NESTING_DEPTH``, and counts
    values and keys (the payload itself is value 1; each array item is one more;
    each object entry is a key and a value, two more) BEFORE iterating a
    container, refusing past ``MAX_PAYLOAD_NODES`` with ``payload_size`` -- so the
    work is bounded whatever arrives. Messages never quote the payload: it is
    untrusted text.
    """
    stack: list[tuple[Any, int]] = [(payload, 1)]
    seen = 1
    while stack:
        value, depth = stack.pop()
        if isinstance(value, str):
            if _SURROGATE_RE.search(value):
                return "well_formed_string: a string holds a lone UTF-16 surrogate"
            continue
        if isinstance(value, dict):
            children: Iterable[Any] = value.values()
            keys: Iterable[Any] = value.keys()
        elif isinstance(value, (list, tuple)):
            children, keys = value, ()
        else:
            continue
        if depth > MAX_NESTING_DEPTH:
            return (
                f"nesting_depth: more than {MAX_NESTING_DEPTH} nested objects or arrays"
            )
        seen += 2 * len(value) if isinstance(value, dict) else len(value)
        if seen > MAX_PAYLOAD_NODES:
            return f"payload_size: more than {MAX_PAYLOAD_NODES} values and keys in one payload"
        for key in keys:
            if isinstance(key, str):
                if _SURROGATE_RE.search(key):
                    return "well_formed_string: an object key holds a lone UTF-16 surrogate"
                if key in FORBIDDEN_KEYS:
                    return f"forbidden_key: {key!r} is never an object key"
        stack.extend((child, depth + 1) for child in children)
    return None


#: ``validate_contract`` has already walked the payload. A module-private
#: object, so no caller-supplied context can switch the walk off by accident.
_WALKED = object()


def _payload_wide_rules(data: Any, info: ValidationInfo) -> Any:
    if not (isinstance(info.context, dict) and info.context.get(_WALKED) is True):
        violation = payload_violation(data)
        if violation is not None:
            raise ValueError(violation)
    return data


def _first_duplicate(values: Iterable[str]) -> int | None:
    """Index of the first repeated value. The index, not the value: it is untrusted text."""
    seen: set[str] = set()
    for index, value in enumerate(values):
        if value in seen:
            return index
        seen.add(value)
    return None


class VizContract(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        strict=True,
        # The wire key only: with population by name, ``{"from_": ...}`` would
        # be read as ``from`` here and as an unknown key by the frontend guard.
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )


class VizPayload(VizContract):
    """A top-level contract: the payload-wide rules run before any field is read,
    so direct ``Model.model_validate`` use is covered as well as ``validate_contract``."""

    @model_validator(mode="before")
    @classmethod
    def _payload_wide(cls, data: Any, info: ValidationInfo) -> Any:
        return _payload_wide_rules(data, info)


# ── C1 · Scope ───────────────────────────────────────────────


class ScopeWindow(VizContract):
    # Inside ``window`` only, a key sent as ``null`` counts as absent.
    days: WindowDays | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    @model_validator(mode="after")
    def _one_form_in_order(self) -> "ScopeWindow":
        has_days = self.days is not None
        has_range = self.from_ is not None and self.to is not None
        half_range = (self.from_ is None) != (self.to is None)
        if half_range or has_days == has_range:
            raise ValueError("window_one_form: exactly one of {days} or {from, to}")
        if self.from_ is not None and self.to is not None:
            try:
                start, end = _parse_day(self.from_), _parse_day(self.to)
            except ValueError:
                raise ValueError(
                    "window_order: from and to are YYYY-MM-DD calendar days"
                ) from None
            if start > end:
                raise ValueError("window_order: from must not be after to")
            if (end - start).days > MAX_WINDOW_SPAN_DAYS:
                raise ValueError(
                    f"window_order: from..to spans more than {MAX_WINDOW_SPAN_DAYS} days"
                )
        return self


class Scope(VizPayload):
    """What a report is filtered by. OR within a dimension, AND across dimensions."""

    project_id: str | None
    release_ids: list[str] = Field(max_length=MAX_RELEASES)
    suite_names: list[
        Annotated[str, Field(min_length=1, max_length=MAX_SUITE_NAME_LENGTH)]
    ] = Field(max_length=MAX_SUITES)
    window: ScopeWindow

    @field_validator("project_id")
    @classmethod
    def _project_id_format(cls, value: str | None) -> str | None:
        if value is not None and not _is_uuid(value):
            raise ValueError("project_id_format: project_id is a UUID or null")
        return value

    @field_validator("release_ids")
    @classmethod
    def _release_ids(cls, value: list[str]) -> list[str]:
        for index, item in enumerate(value):
            if item != UNATTRIBUTED and not _is_uuid(item):
                raise ValueError(
                    f"release_id_format: release_ids[{index}] is neither a UUID nor {UNATTRIBUTED!r}"
                )
        # A UUID names the same release in either case; the sentinel is exact
        # (and "UNATTRIBUTED" has already failed release_id_format above).
        duplicate = _first_duplicate(
            item.lower() if _is_uuid(item) else item for item in value
        )
        if duplicate is not None:
            raise ValueError(
                f"unique_release: release_ids[{duplicate}] repeats an earlier item"
            )
        return value

    @field_validator("suite_names")
    @classmethod
    def _unique_suites(cls, value: list[str]) -> list[str]:
        duplicate = _first_duplicate(value)
        if duplicate is not None:
            raise ValueError(
                f"unique_suite: suite_names[{duplicate}] repeats an earlier item"
            )
        return value

    @model_validator(mode="after")
    def _release_requires_project(self) -> "Scope":
        # A release belongs to exactly one project, so a release filter over
        # "all accessible projects" would silently match one project's runs.
        if self.project_id is None and self.release_ids:
            raise ValueError(
                "release_requires_project: release_ids must be empty when project_id is null"
            )
        return self


# ── C2 · Envelope meta ───────────────────────────────────────


class ScopeProject(VizContract):
    id: str
    name: str


class ScopeRelease(VizContract):
    id: str
    name: str
    status: str


class AppliedWindow(VizContract):
    from_: str = Field(alias="from")
    to: str
    days: Count
    timezone: Literal["UTC"]

    _days = field_validator("from_", "to")(_check_day)


class AppliedScope(VizContract):
    """What the server applied, not what was asked."""

    projects: list[ScopeProject]
    releases: list[ScopeRelease]
    suites: list[str]
    window: AppliedWindow


class Totals(VizContract):
    matched_runs: Count
    total_runs: Count
    matched_executions: Count
    total_executions: Count

    @model_validator(mode="after")
    def _totals_subset(self) -> "Totals":
        if self.matched_runs > self.total_runs:
            raise ValueError("totals_subset: matched_runs exceeds total_runs")
        if self.matched_executions > self.total_executions:
            raise ValueError(
                "totals_subset: matched_executions exceeds total_executions"
            )
        return self


class IgnoredFilter(VizContract):
    dimension: Literal["release", "suite", "window"]
    reason: str


class EnvelopeMeta(VizPayload):
    """The additive ``meta`` object on an analytics response.

    Every key is required. A nullable one is sent as ``null``, never left out:
    "not measured" and "the server forgot" must not look the same.
    """

    schema_version: PositiveCount
    scope: AppliedScope
    totals: Totals
    pass_rate_basis: Literal["executions", "unique_tests"] | None
    ignored_filters: list[IgnoredFilter]
    truncated: bool
    truncated_total: Count | None
    measured: bool
    reason: str | None
    includes_in_progress: Count
    partial_day: str | None
    generated_at: str
    as_of: str

    _partial_day = field_validator("partial_day")(_check_day)
    _instants = field_validator("generated_at", "as_of")(_check_utc_instant)

    @model_validator(mode="after")
    def _conditional_fields(self) -> "EnvelopeMeta":
        if self.truncated and self.truncated_total is None:
            raise ValueError(
                "truncated_total: truncated_total is required when truncated is true"
            )
        if not self.measured and _BLANK_RE.fullmatch(self.reason or ""):
            raise ValueError(
                "measured_reason: a reason with a non-whitespace character is required when measured is false"
            )
        return self


# ── C3 · ChartSeries ─────────────────────────────────────────


def _cap(rule: str, cap: int, noun: str) -> Callable[[Any], Any]:
    """A ``mode="before"`` list cap whose message names its rule id.

    It runs before the items are validated, so an oversized list costs one
    ``len()``. ``max_length`` stays on the field for the JSON schema.
    """

    def check(value: Any) -> Any:
        if isinstance(value, list) and len(value) > cap:
            raise ValueError(f"{rule}: {len(value)} {noun} > {cap}")
        return value

    return check


class SeriesPoint(VizContract):
    x: str
    # null is "no data" and is drawn as a gap; it is never a zero.
    y: Number | None
    n: Count


class Series(VizContract):
    key: str
    label: str
    points: list[SeriesPoint] = Field(max_length=MAX_POINTS_PER_SERIES)

    _point_cap = field_validator("points", mode="before")(
        _cap("point_cap", MAX_POINTS_PER_SERIES, "points in one series")
    )


class SeriesChart(VizContract):
    kind: Literal["series"]
    dimensions: list[str]
    x_type: Literal["time", "category"]
    series: list[Series] = Field(max_length=MAX_SERIES)

    @field_validator("series")
    @classmethod
    def _unique_series_key(cls, value: list[Series]) -> list[Series]:
        duplicate = _first_duplicate(item.key for item in value)
        if duplicate is not None:
            raise ValueError(
                f"unique_series_key: series[{duplicate}].key repeats an earlier key"
            )
        return value


class MatrixCell(VizContract):
    x: Count
    y: Count
    # Checked against ``value_type`` by the chart, which is the one that knows it.
    value: Number | str | None
    n: Count


class MatrixChart(VizContract):
    kind: Literal["matrix"]
    value_type: Literal["rate", "count", "status"]
    x_labels: list[str]
    y_labels: list[str]
    cells: list[MatrixCell] = Field(max_length=MAX_MATRIX_CELLS)

    _cell_cap = field_validator("cells", mode="before")(
        _cap("cell_cap", MAX_MATRIX_CELLS, "cells")
    )

    @model_validator(mode="after")
    def _cells(self) -> "MatrixChart":
        columns, rows = len(self.x_labels), len(self.y_labels)
        for index, cell in enumerate(self.cells):
            if cell.x >= columns or cell.y >= rows:
                raise ValueError(
                    f"cell_index_range: cells[{index}] does not address a label"
                )
            if self.value_type == "status":
                if cell.value is not None and cell.value not in STATUS_VOCAB:
                    raise ValueError(
                        f"status_vocab: cells[{index}].value is not a test status"
                    )
            elif isinstance(cell.value, str):
                raise ValueError(
                    f"cells[{index}].value is a number or null when value_type is {self.value_type}"
                )
            elif self.value_type == "count" and cell.value is not None:
                # A count cell is a count (change rule 3): the same three rules.
                try:
                    _check_count(cell.value)
                except ValueError as exc:
                    rule, _, why = str(exc).partition(": ")
                    raise ValueError(f"{rule}: cells[{index}].value: {why}") from None
        return self


class TreeNode(VizContract):
    id: str
    parent_id: str | None
    label: str
    value: NonNegativeNumber
    measure: Number | None


class TreeChart(VizContract):
    kind: Literal["tree"]
    nodes: list[TreeNode] = Field(max_length=MAX_TREE_NODES)

    _node_cap = field_validator("nodes", mode="before")(
        _cap("node_cap", MAX_TREE_NODES, "tree nodes")
    )

    @field_validator("nodes")
    @classmethod
    def _forest(cls, value: list[TreeNode]) -> list[TreeNode]:
        duplicate = _first_duplicate(node.id for node in value)
        if duplicate is not None:
            raise ValueError(
                f"unique_node_id: nodes[{duplicate}].id repeats an earlier id"
            )
        parent_of = {node.id: node.parent_id for node in value}
        for index, node in enumerate(value):
            if node.parent_id is not None and node.parent_id not in parent_of:
                raise ValueError(
                    f"tree_parent_exists: nodes[{index}].parent_id names no node"
                )
            if node.parent_id == node.id:
                raise ValueError(f"tree_acyclic: nodes[{index}] is its own parent")
        # An empty list is how a coverage map says "no data"; only a tree that
        # has nodes needs a root.
        if value and not any(node.parent_id is None for node in value):
            raise ValueError("tree_acyclic: at least one node must be a root")
        # Walk each node up to a root once. ``settled`` holds ids already known
        # to reach one, so the whole pass is linear in the node count.
        settled: set[str] = set()
        for start in parent_of:
            trail: list[str] = []
            on_trail: set[str] = set()
            current: str | None = start
            while current is not None and current not in settled:
                if current in on_trail:
                    raise ValueError("tree_acyclic: parent links form a cycle")
                on_trail.add(current)
                trail.append(current)
                current = parent_of[current]
            settled.update(trail)
        return value


class GraphNode(VizContract):
    id: str
    label: str
    size: NonNegativeNumber


class GraphEdge(VizContract):
    source: str
    target: str
    weight: UnitNumber


class GraphChart(VizContract):
    kind: Literal["graph"]
    nodes: list[GraphNode] = Field(max_length=MAX_GRAPH_NODES)
    edges: list[GraphEdge]

    _node_cap = field_validator("nodes", mode="before")(
        _cap("node_cap", MAX_GRAPH_NODES, "graph nodes")
    )

    @model_validator(mode="after")
    def _edges_join_nodes(self) -> "GraphChart":
        duplicate = _first_duplicate(node.id for node in self.nodes)
        if duplicate is not None:
            raise ValueError(
                f"unique_node_id: nodes[{duplicate}].id repeats an earlier id"
            )
        ids = {node.id for node in self.nodes}
        for index, edge in enumerate(self.edges):
            if edge.source not in ids or edge.target not in ids:
                raise ValueError(
                    f"edge_endpoints: edges[{index}] references a node that does not exist"
                )
        return self


Chart = Annotated[
    Union[SeriesChart, MatrixChart, TreeChart, GraphChart],
    Field(discriminator="kind"),
]


class ChartSeries(RootModel[Chart]):
    """The one normalised model renderers, the table view, CSV export and
    keyboard navigation all read. ``.root`` is the concrete chart for ``kind``."""

    @model_validator(mode="before")
    @classmethod
    def _payload_wide_then_kind(cls, data: Any, info: ValidationInfo) -> Any:
        data = _payload_wide_rules(data, info)
        # Named here rather than left to the discriminator, whose error carries
        # no rule id. A non-object is left for the type error it is.
        if isinstance(data, dict) and data.get("kind") not in CHART_KINDS:
            raise ValueError(f"kind_enum: kind is one of {', '.join(CHART_KINDS)}")
        return data


# ── C4 · Widget config ───────────────────────────────────────


class WidgetInstance(VizContract):
    """One placed visualisation inside ``saved_views.filters``.

    Attribute names ARE the wire keys. The frontend writes this same stored
    object, so a dump that forgot ``by_alias`` must not be able to write
    ``instance_id`` beside the ``instanceId`` the other writer reads.
    """

    model_config = ConfigDict(extra="allow")

    instanceId: str = Field(min_length=1)
    templateId: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    chartType: ChartType | None = None
    metricVariant: str | None = None
    filters: dict[str, Any] | None = None
    groupBy: list[Dimension] | None = Field(default=None, max_length=MAX_GROUP_BY)
    topN: Literal[5, 10, 25, 50] | None = None
    scale: Literal["linear", "log"] | None = None
    stack: Literal["none", "absolute", "percent"] | None = None
    bucket: Literal["day", "week"] | None = None

    @model_validator(mode="after")
    def _optional_is_not_nullable(self) -> "WidgetInstance":
        # ``None`` above means "key absent". The README marks every nullable
        # field with ``| null`` and marks none of these, and the frontend type
        # is ``title?: string`` -- it omits the key, it never writes null.
        nulls = sorted(
            name
            for name in type(self).model_fields
            if name in self.model_fields_set and getattr(self, name) is None
        )
        if nulls:
            raise ValueError(
                f"optional keys are omitted, never null: {', '.join(nulls)}"
            )
        return self


class WidgetConfig(VizPayload):
    """``saved_views.filters`` for an analytics page. Unknown keys survive a round-trip."""

    model_config = ConfigDict(extra="allow")

    page: str
    version: PositiveCount
    instances: list[WidgetInstance] = Field(max_length=MAX_INSTANCES)

    @field_validator("instances")
    @classmethod
    def _unique_instance_id(cls, value: list[WidgetInstance]) -> list[WidgetInstance]:
        duplicate = _first_duplicate(item.instanceId for item in value)
        if duplicate is not None:
            raise ValueError(
                f"unique_instance_id: instances[{duplicate}].instanceId repeats an earlier id"
            )
        return value


# ── C5 · Drill path ──────────────────────────────────────────


class DrillLevel(VizContract):
    dimension: Dimension
    value: str = Field(min_length=1, max_length=MAX_DRILL_VALUE_LENGTH)

    @model_validator(mode="after")
    def _status_vocab(self) -> "DrillLevel":
        if self.dimension == "status" and self.value not in STATUS_VOCAB:
            raise ValueError("status_vocab: a status level names a test status")
        return self


class DrillPath(VizPayload):
    """Ordered from the top level down."""

    path: list[DrillLevel] = Field(max_length=MAX_DRILL_DEPTH)

    @field_validator("path")
    @classmethod
    def _unique_dimension(cls, value: list[DrillLevel]) -> list[DrillLevel]:
        duplicate = _first_duplicate(level.dimension for level in value)
        if duplicate is not None:
            raise ValueError(
                f"unique_dimension: path[{duplicate}] repeats an earlier dimension"
            )
        return value


# ── Registry ─────────────────────────────────────────────────

#: Keyed by the fixture folder under ``contracts/viz/fixtures``.
CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "scope": Scope,
    "envelope": EnvelopeMeta,
    "chart_series": ChartSeries,
    "widget_config": WidgetConfig,
    "drill_path": DrillPath,
}


def validate_contract(kind: str, payload: Any) -> BaseModel:
    """Validate ``payload`` against contract ``kind``; raises ``ValidationError`` on a broken rule.

    The payload-wide rules are checked here first, and the model is told so,
    so the walk runs once per call. Direct ``Model.model_validate`` use runs it
    in the model's own ``mode="before"`` validator instead.
    """
    try:
        model = CONTRACT_MODELS[kind]
    except KeyError:
        raise ValueError(
            f"unknown viz contract {kind!r}; expected one of {sorted(CONTRACT_MODELS)}"
        ) from None
    violation = payload_violation(payload)
    if violation is not None:
        # The same shape the model's own validator would raise.
        raise ValidationError.from_exception_data(
            model.__name__,
            [
                {
                    "type": "value_error",
                    "loc": (),
                    "input": payload,
                    "ctx": {"error": ValueError(violation)},
                }
            ],
        )
    return model.model_validate(payload, context={_WALKED: True})


def dump_contract(model: BaseModel) -> Any:
    """The wire form of a validated contract.

    ``exclude_unset`` is the point: an optional key that was absent stays
    absent. A plain ``model_dump()`` would write ``"title": null`` into a stored
    widget config, which this module then refuses to read back. Nesting is
    capped at validation (``nesting_depth``), so an accepted payload always dumps.
    """
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)
