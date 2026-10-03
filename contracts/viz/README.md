# Visualization contracts (`contracts/viz`)

Frozen seams between the backend analytics API and the frontend chart kit, so the
two sides of the Visualization Upgrade can be built independently without drifting.

**One set of fixtures, two validators.** Every file under `fixtures/` is checked by

- `backend/tests/test_viz_contracts.py` against the Pydantic models in `backend/app/models/viz_contracts.py`
- `frontend/src/lib/viz/contracts.test.ts` against the guards in `frontend/src/lib/viz/contracts.ts`

A fixture under `valid/` must be accepted by **both**; a fixture under `invalid/` must be
rejected by **both**. Adding a rule means adding an `invalid/` fixture first — if only one
side rejects it, that side's suite fails and the drift is visible in the PR that caused it.

## Fixture file format

```json
{ "description": "why this case exists", "violates": "rule_id (invalid/ only)", "payload": { } }
```

`violates` names the rule in the tables below. It is documentation plus a completeness
check: every rule id listed here must have at least one `invalid/` fixture.

## Change rules

1. **Additive only.** New optional fields and new enum members are allowed. Removing or
   renaming a field, tightening a cap, or changing a meaning needs a new contract version
   and a new fixture folder.
2. Every string that names a test, suite, release, project, group or label is **untrusted
   plain text** (it comes from ingested CI files). Validators never reject it for containing
   markup; renderers must never interpret it as markup.
3. Counts are integers ≥ 0. A value that was not measured is `null`, never `0`. A count is a
   JSON number **with no fraction** (`integer_count`: `1.5` is rejected; producers never send
   `1.0` — JavaScript cannot tell it from `1`, so only the backend can catch it) and at most
   2⁵³ − 1 (`safe_integer`), so JavaScript reads it exactly. "Count" means every field typed
   `int` below: `n`, `totals.*`, `includes_in_progress`, `truncated_total`, `schema_version`,
   `version`, the envelope's `scope.window.days`, matrix `x`/`y`, matrix `value` when
   `value_type` is `count`, every C6 period count (`runs` … `duration_runs`, `window.days`),
   and the Wave 3 additions: matrix `counts.*`, tree `stats.test_count`, `stats.executions`,
   `stats.flaky_count`, `stats.staleness_days`, points `size`, `n` and `excluded.*`.
   Two refinements keep the ids unambiguous:
   - The **C1 scope** `window.days` is not covered here: every bad value there (fraction, 0,
     366, 2⁵³) reports `window_days_range`.
   - Matrix `x`/`y` below 0 report `non_negative`; `cell_index_range` is for an index ≥ 0 that
     points past its label list.
   Every date (`YYYY-MM-DD`, or the date part of an instant) is a real calendar date in
   years 0001–9999; year 0000 is rejected.
4. Every invalid fixture breaks **exactly one** rule — the one in `violates` — so a validator
   cannot pass the fixture by rejecting it for an unrelated reason. Both suites assert the reason.
5. String length limits count **Unicode code points** (Python `len(s)`, JavaScript
   `[...s].length`), not UTF-16 units.
6. Optional fields may be **absent** but not `null`, unless the tables below write `| null`.
   All four scope fields are required.
7. **Whitespace** means exactly the ECMAScript `WhiteSpace` and `LineTerminator` set:
   U+0009–U+000D, U+0020, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F,
   U+3000, U+FEFF. Neither Python's `str.strip()` nor any other language default is used.
8. Payload-wide rules, checked on every kind, including inside unknown keys:

   | Rule id | Rule |
   |---|---|
   | `well_formed_string` | Every string (values and object keys) is well-formed Unicode: no lone UTF-16 surrogate. |
   | `nesting_depth` | At most 32 nested containers (objects or arrays), counting the payload itself as 1. |
   | `forbidden_key` | No object key is `__proto__`, `constructor` or `prototype`. Preserving unknown keys must never let a later merge rewrite a prototype. |

   Both sides also refuse a payload with more than 1 000 000 values and keys in total, with
   an error starting `payload_size`. Counting: the payload itself is 1, each array item is 1,
   and each object entry is 2 (its key and its value). Exactly 1 000 000 is accepted. It bounds the work the walk above can do on hostile input
   (the largest legitimate payload, a full matrix, is about 50 000). It is covered by unit tests
   on each side rather than a fixture, because a fixture that size does not belong in the repo.
   The walk reports the **first** payload-wide violation it meets and stops.

---

## C1 · Scope — `fixtures/scope`

What a report is filtered by. Built by the frontend, parsed by the backend.

| Field | Type | Rule id | Rule |
|---|---|---|---|
| `project_id` | UUID string or `null` | `project_id_format` | `null` = all accessible projects. Single-valued by design. |
| `release_ids` | string[] | `release_id_format` | Each item is a UUID or the sentinel `"unattributed"`. |
| | | `release_cap` | At most 20. |
| | | `unique_release` | No duplicates. UUIDs compare case-insensitively. |
| | | `release_requires_project` | Must be empty when `project_id` is `null`. |
| `suite_names` | string[] | `suite_name_length` | Each item 1–500 characters. |
| | | `suite_cap` | At most 50. |
| | | `unique_suite` | No duplicates. |
| `window` | `{days?: int \| null, from?: string \| null, to?: string \| null}` | `window_one_form` | Exactly one of `{days}` or `{from, to}`. Inside `window` only, a key set to `null` counts as absent. The keys are exactly `days`, `from`, `to`; any other key (e.g. `from_`) is an unknown key, not an alias. |
| | | `window_days_range` | `days` is an integer 1–365. |
| | | `window_order` | `from` ≤ `to`, both `YYYY-MM-DD` real calendar dates (years 0001–9999), and `to − from` ≤ 366 days (a difference, so 08-20 → 09-19 is 30). |

Semantics: **OR within a dimension, AND across dimensions.**

**When `project_id` is `null` is refused.** Two error codes say "All Projects is not supported", and a
client handles both: a route that always needs one project (coverage map, test scatter, and every route
whose scope policy is `project_required`) answers 422 `missing_parameter` with `param: "project_id"`; the
heatmap takes All Projects for its suite kinds, and its one-project kinds (`kind=test_run`,
`kind=suite_release`) answer 422 `project_required` with `param: "project_id"`. Neither echoes a value.

Wire mapping (query string): `project_id` · `release_id` (repeated) · `suite_name` (repeated) ·
`days` or `from`/`to`. An empty list is sent as **no parameter**. Exactly one value is sent as a
single parameter, so legacy single-value calls are unchanged on the wire.

URL mapping (browser address bar): `release` (repeated, existing key) · `suites` (repeated) ·
`window`. The keys `suite` and `days` are page-local elsewhere and are never read or written.

## C2 · Envelope `meta` — `fixtures/envelope`

Additive `meta` object on every analytics response.

| Field | Type | Rule id | Rule |
|---|---|---|---|
| all fields below | | `required_field` | All are required; nullable ones may be `null`. |
| `schema_version` | int ≥ 1 | | |
| `scope.projects[]` | `{id, name}` | | What the server applied, not what was asked. |
| `scope.releases[]` | `{id, name, status}` | | `status` is an open string. |
| `scope.suites[]` | string | | |
| `scope.window` | `{from, to, days, timezone}` | `timezone_utc` | `timezone` is always `"UTC"`. |
| `totals` | `{matched_runs, total_runs, matched_executions, total_executions}` | `non_negative`, `integer_count`, `safe_integer`, `totals_subset` | `matched_* ≤ total_*`. Totals ignore release and suite filters but respect project and window. |
| `pass_rate_basis` | `"executions"` \| `"unique_tests"` \| `null` | | |
| `ignored_filters[]` | `{dimension, reason}` | `ignored_dimension` | `dimension` ∈ `release`, `suite`, `window`. |
| `truncated` / `truncated_total` | bool / int \| `null` | `truncated_total` | `truncated_total` is required (non-null) when `truncated` is true. With `truncated_axes`, it is the full count of the axis that lost whole buckets (`x`), else of the series axis. |
| `truncated_axes` | `{x?, series?: {dimension, kept, total}}` \| absent | `truncated_axis`, `truncated_axes` | Optional and additive (VIZ-203). Present only when something was truncated; never `{}`. Each entry has `kept < total`, `truncated` must be true, and `truncated_total` must equal one of the entries' `total`. One number cannot answer for two axes: 400 suite buckets keyed by 12 environments truncates both. |
| `outside_window` | `{buckets, executions, first, last}` \| absent | `outside_window` | Optional and additive (VIZ-203). Rows whose bucket fell outside a generated axis — a run with a future `created_at`. They are not drawn; they are counted, so a clock-skewed agent does not lose its run in silence. `buckets ≥ 1` and `first ≤ last`: a zero here is nothing to report, so the key is omitted instead. |
| `comparability` | `{comparable, reason, reason_code}` \| absent | `comparability_reason` | Optional and additive (VIZ-404). `comparable: false` carries a `reason` with at least one non-whitespace character and a `reason_code`; `comparable: true` carries neither (both `null`). All three keys are required inside the object; the object itself is never `null`. |
| `comparability.reason_code` | string \| `null` | `comparability_reason_code` | `different_suites`, `partial_coverage` |
| `measured` / `reason` | bool / string \| `null` | `measured_reason` | A `reason` with at least one non-whitespace character is required when `measured` is false. |
| `includes_in_progress` | int ≥ 0 | `non_negative` | Number of in-progress runs inside the scope. |
| `partial_day` | `YYYY-MM-DD` \| `null` | | The current UTC day when it is still accumulating. |
| `generated_at`, `as_of` | UTC instant | `utc_instant` | Exactly `YYYY-MM-DDTHH:MM:SS`, optionally `.` and 1–6 digits, then `Z` or `+00:00` — an RFC 3339 profile. The date is a real calendar date, the hour is 00–23, minutes and seconds are 00–59 (no leap second). No space separator, no `+0000`, no `-00:00`, no basic format. |

**`comparability` (VIZ-404)** says whether the series of a comparison chart can be read
like-for-like. `/analytics/chart-data` sends it only when the second `group_by` (the series
dimension) is `release` or `branch` **and** the chart has at least two series to compare
(an `__other__` roll-up counts as one). Every other response — every other route, a single
`group_by`, a series dimension of `suite`, `environment` …, a chart with one series — omits
it. **Absent means "not assessed", never "comparable"**: a reader must not draw a
"comparable" badge from a missing key.

The judgement is made over exactly the scope the chart was drawn from (project, releases,
suites, window, the tenant filter) and over the series the chart kept. It compares the
**effective suites** (a live-stream run's own label, else the row's suite; trimmed and
matched case-insensitively, as every suite filter is) that each series' runs executed:

- `partial_coverage` — at least one compared series has runs in scope but **no per-test
  rows** at all (a live-stream run whose rows have not landed, an upload that carried only
  totals), so the suites it ran are unknown. Checked first: an unknown set is not a
  different one. The `reason` says how many of the compared series have no per-test results.
- `different_suites` — every series has per-test rows, but the sets of effective suites
  differ. The `reason` says how many series were compared, how many suites ran in at least
  one of them and how many ran in all of them.

`reason` names counts only — never a suite, release or branch — so it cannot carry a
name from outside the caller's scope into a banner. `comparable: true` means every compared
series ran exactly the same set of suites. The window is the same for every series by
construction (one `window` per request), so window length is not a reason this API reports;
a partially-landed series (some runs with rows, some without) is judged on the rows it has.

## C3 · ChartSeries — `fixtures/chart_series`

The single normalised model the renderers, the table view, CSV export and keyboard
navigation all read. Discriminated by `kind` (`kind_enum`): `series`, `matrix`, `tree`, `graph`, `points`.

| Kind | Shape | Rule ids |
|---|---|---|
| any | `{kind, …}` | `kind_enum` (`kind` is one of the five below) |
| `series` | `{dimensions: string[], x_type: "time"\|"category", series: [{key, label, points: [{x: string, y: number\|null, n: int, measured?: bool, reason?: string\|null}]}], x_labels?: {string: string}}` | `series_cap` (≤ 8 series), `unique_series_key`, `point_cap` (≤ 366 points per series), `non_negative` (`n`), `measured_reason` (a point saying it was not measured carries a non-whitespace reason) |
| `matrix` | `{value_type: "rate"\|"count"\|"status", x_labels, y_labels, cells: [{x: int, y: int, value, n: int}]}` | `cell_index_range`, `cell_cap` (≤ 5 400 cells), `status_vocab` (when `value_type` is `status`, `value` ∈ `passed, failed, broken, skipped, unknown` or `null`), `non_negative` and `integer_count` (when `value_type` is `count`, `value` is an integer ≥ 0 or `null`) |
| `tree` | `{nodes: [{id, parent_id\|null, label, value: number ≥ 0, measure: number\|null}]}` | `tree_parent_exists`, `tree_acyclic` (no cycles and no self-parent; a **non-empty** tree has ≥ 1 root; an empty `nodes` list is valid and means no data), `unique_node_id`, `node_cap` (≤ 500 nodes) |
| `graph` | `{nodes: [{id, label, size ≥ 0}], edges: [{source, target, weight}]}` | `edge_endpoints`, `weight_range` (0–1), `unique_node_id`, `node_cap` (≤ 200 nodes) |
| `matrix` keys (Wave 3) | `x_keys?: string[]`, `y_keys?: string[]` | `key_count` (each list is exactly as long as its label list), `unique_key` (no key repeats inside one list) |
| `matrix` cell counts (Wave 3) | `cells[].counts?: {passed, failed, broken, skipped, unknown}`, every key required, each a count | `counts_sum` (the five add up to the cell's sample size `n`), `required_field` (a status missing from `counts`) |
| `matrix` unit (Wave 3) | `unit?: "percent"\|"ratio"` | `rate_unit_range` (when `value_type` is `rate`: 0–100 for `percent` and when `unit` is absent, 0–1 for `ratio`), `empty_sample` (a `rate` cell over nothing is null: `n` is 0, or `counts` show nothing evaluated) |
| `tree` node stats (Wave 3) | `nodes[].stats?: {test_count: int, executions: int, pass_rate: number 0–100\|null, flaky_count: int, flaky_share: number 0–1\|null, last_executed_at: instant\|null, staleness_days: int\|null, recency: "seen"\|"unknown"\|"never"}`, every key required | `rate_range` (`pass_rate` is 0–100), `share_range` (`flaky_share` is 0–1), `utc_instant` (`last_executed_at`, the C2 profile), `recency_null_pair` (`seen` carries a date and a staleness; `unknown` and `never` carry neither), `empty_sample` (no executions means a null pass rate; no tests means a null flaky share) |
| `graph` node group (Wave 3) | `nodes[].group?: string` | — |
| `points` (Wave 3) | `{kind, x: axis, y: axis, size: {key, label}, points: [{id, label, x: number, y: number, size: int, n: int}], medians?: {x: number, y: number}, excluded?: {below_min_executions: int, no_duration: int, no_evaluated: int}}`; an axis is `{key, label, unit: "ms"\|"percent"\|"ratio"\|"count", scale: "linear"\|"log"}`, every axis key required | `point_cap` (≤ 5 000 points), `unique_point_id`, `log_axis_positive` (every value on a log axis is above 0, medians included), `rate_unit_range` (a `percent` axis holds 0–100, a `ratio` axis 0–1), `non_negative` (an `ms` or `count` axis holds no value below 0), `empty_sample` (a point stands on at least one evaluated sample), `required_field` (an axis without its unit) |

`y: null` and `value: null` mean **no data** and are drawn as a gap or an empty cell, never as zero.
Numbers are finite: `NaN` and `±Infinity` are rejected wherever a number is allowed.
A whole-number value keeps its integer form on a round trip (`3` stays `3`, never `3.0`).

Two optional, additive keys on `series` (VIZ-203). A point may carry **measured** and
**reason**: a null `y` says there is nothing to draw, these say why, so a reader can tell
"nothing ran here" from "a rate over nothing is not 0%". Both may be absent; `measured`
is a boolean, never null. A chart may carry **x_labels**, display names for `x` values that
are ids (a project or release bucket) — the `x` key stays the id, because that is what a
drill-down (C5) sends back.

### Wave 3 additions (VIZ-205, 206, 207, 506)

All optional and additive: every payload written before them is unchanged and still valid,
and like every optional key they may be absent but never `null` (change rule 6). The one
exception is the `rate` range below, which an absent `unit` now also obeys.

**Matrix `x_keys` / `y_keys`.** Stable ids parallel to `x_labels` / `y_labels`: a UTC day
`YYYY-MM-DD`, a suite key, a release id (or `unattributed`), an environment, a test
fingerprint, a run id. The labels stay display text and may repeat (two releases with one
name); the keys are what a drill-down, a cross-filter or a rows request sends back, so they
are unique within their list and exactly as long as the label list. They are untrusted text
like the labels: never parsed, only echoed. Absent means the labels are the only handle.

**Matrix cell `counts`.** The status counts behind the cell — `passed`, `failed`, `broken`,
`skipped`, `unknown`, all five present, each a count — adding up to the cell's `n` (its
executions). They are what a tooltip states ("220 executions, 3 failed"); a cell with no
executions sends all five as `0`, which is a true zero, while its `value` stays `null`.

**Matrix `unit`.** What a `rate` value is measured in: `"percent"` (0–100, percentage
points) or `"ratio"` (0–1). **Absent means `percent`**, which is what `/analytics/chart-data`
and `/analytics/heatmap` send, so one reader divides by 100 once. A value outside its unit's
range is rejected (`rate_unit_range`); the key is ignored for `count` and `status` matrices.
A `rate` cell over nothing is `null`, never `0` (`empty_sample`): `n` is `0`, or `counts`
show nothing evaluated (passed + failed + broken = 0: skipped and unknown are outside a pass
rate's denominator). A cell with `n > 0` and only skipped results is that second case: it is
"not measured", not 0%.

**Tree node `stats`.** Per-node figures for the coverage map. `value` stays the rectangle
size (`test_count`) and `measure` stays the pass rate; `stats` carries the rest, every key
present (a list rather than a table: tables in this section are rule tables):

- `test_count` — tests (a count). Never `null`.
- `executions` — executions in the window (a count). Never `null`; `0` is a real "nothing ran".
- `pass_rate` — percent 0–100 over the evaluated executions. `null` when nothing was
  evaluated, and always `null` when `executions` is `0`.
- `flaky_count` — tests (a count). Never `null`.
- `flaky_share` — ratio 0–1, `flaky_count / test_count`. `null` when there is nothing to
  divide by, and always `null` when `test_count` is `0`.
- `last_executed_at` — a UTC instant (the C2 `utc_instant` profile). `null` when no
  execution is known.
- `staleness_days` — whole days since `last_executed_at` (a count). `null` exactly when
  `last_executed_at` is.
- `recency` — `seen`, `unknown` or `never`. Never `null`.

`recency` says why a date is or is not there (`recency_null_pair`): `seen` carries both
`last_executed_at` and `staleness_days`; `unknown` (the record of the last run was lost — a
deleted run's id was cleared) and `never` (no execution was ever recorded) carry neither. A
reader must not show `unknown` as "never run".

**Graph node `group`.** Free text, untrusted: the dominant failure category of a failure-group
node, for its colour. Absent means no category.

**`points`.** One mark per entity (a test) on two numeric axes plus a size — the scatter.
Each axis names its `key` and display `label`, its `unit` (`ms`, `percent` 0–100, `ratio`
0–1, `count`) and its `scale` (`linear` or `log`). Point `x` and `y` are finite numbers,
never `null`: a test that cannot be placed is not drawn at zero, it is left out and counted
in `excluded` (`below_min_executions`, `no_duration`, `no_evaluated` — counts of TESTS). On
a `log` axis every value is above 0 (`log_axis_positive`); a value at 0 is the "no duration
plotted at zero" bug. `size` is a count (executions); `n` is the evaluated sample behind the
point and is at least 1 (`empty_sample`). `id` is unique (`unique_point_id`) and is what a
rows request sends back; `label` is display text. At most 5 000 points (`point_cap`).
`medians` (unweighted, over the returned points) is absent when there is nothing to take a
median of — never `{x: 0, y: 0}`. `excluded` absent means the producer excludes nothing.

## C4 · Widget config — `fixtures/widget_config`

The object stored in `saved_views.filters` by the analytics pages. The `page` / `version` /
`instances` shape is what is persisted today (`version: 2`); the last five instance keys are additive.

| Field | Rule id | Rule |
|---|---|---|
| `page` | | string |
| `version` | | int ≥ 1 |
| `instances` | `instance_cap`, `unique_instance_id` | ≤ 12; `instanceId` unique |
| `instances[].instanceId`, `.templateId` | `required_field` | non-empty strings |
| `.title` | `title_length` | ≤ 120 characters |
| `.chartType` | `chart_type_enum` | `line, bar, area, pie, gauge, metric, table, stacked_bar, donut` |
| `.metricVariant` | | string |
| `.filters` | | object |
| `.groupBy` | `group_by_cap` | ≤ 2 items from the dimension enum (C5) except `class`, which only a coverage-map drill path uses (no chart-data request groups by it) |
| `.topN` | `top_n_enum` | 5, 10, 25 or 50 |
| `.scale` | | `linear` \| `log` |
| `.stack` | | `none` \| `absolute` \| `percent` |
| `.bucket` | | `day` \| `week` |

Unknown keys are preserved, never stripped: two writers share this object. Consumers copy it
with spread (`{...x}`), never `Object.assign` or a deep merge.

Stored rows written before this contract may use the legacy shapes `instances: ["id", …]`
or a `widgets` key. `normalizeInstances` in the frontend still reads those; this contract
describes what is **written** from now on, and is not applied to legacy rows on read.

## C5 · Drill path — `fixtures/drill_path`

`{ "path": [ { "dimension", "value" } ] }`, ordered from the top level down.

| Rule id | Rule |
|---|---|
| `depth_cap` | At most 4 levels. |
| `unique_dimension` | A dimension appears once. |
| `dimension_enum` | `day, week, project, release, suite, status, failure_category, branch, environment, ingestion_source, test, error_signature, class` |
| `value_length` | 1–2 000 characters. |
| `status_vocab` | When `dimension` is `status`, `value` ∈ `passed, failed, broken, skipped, unknown`. |

`class` (Wave 3, VIZ-502) is the coverage map's middle level, "Class / file": its `value` is
the class KEY the coverage map sent as the node id's class part (`__none__` for tests with no
class), echoed and never parsed. It is a drill level only: it is not a C4 `groupBy`
dimension and `/analytics/chart-data` does not group by it.

## C5 · URL encoding — the `drill` and `rows` keys

How a drill path and an open rows panel live in the address bar (VIZ-602, 603). There is no
fixture folder for it: a parsed URL IS a C5 drill path and is judged by the rules above.

| Key | Form | Notes |
|---|---|---|
| `drill` | repeatable `drill=<dimension>~<value>`, in path order: `?drill=suite~payments&drill=status~failed` | The path, top level first. Split on the **first** `~` only: `drill=test~a~b` is dimension `test`, value `a~b`. `URLSearchParams` does all percent-encoding and decoding; nothing else escapes or unescapes a value. |
| `rows` | repeatable: first `rows=by~<owner>`, then `rows=<dimension>~<value>` | The open rows panel: its OWNER (the section that opened it, its section id: `heatmap-test_run`, `scatter-suite`, `coverage-map`, …; `[a-z][a-z0-9_-]{0,63}`) and its selectors (a heatmap cell: `rows=by~trends-heatmap&rows=suite~payments&rows=day~2026-09-12`). Absent means the panel is closed. The selectors follow the same split and rules as a drill path. The owner is there because one page can hold two rows hosts whose selectors look the same (Suite detail: a test x run heatmap cell and a scatter point are both a lone `test`); only the owner opens its panel, so one URL never opens two. A list without a valid owner entry first is not applied (read as an invalid level, with the notice). `by` is not a dimension. |
| `drill.<id>` | reserved | One path per chart, for Wave 4. This wave has one drill host per page (Coverage: the treemap; Failure analysis: the ladder), so the plain key is enough. |

Reading:

- A bad URL never throws. The path is read level by level and truncated at the first level
  that breaks a C5 rule (an entry with no `~` is such a level); the page says so through its
  scope notice, as filter keys do (VIZ-306).
- When the encoded `drill` and `rows` parameters together are longer than 6 000 characters,
  the deepest drill level is dropped, with a notice, until they fit.

Writing:

- Each drill step PUSHES a history entry, so Back and Forward walk the levels; a filter change
  keeps REPLACING the entry, as it does today.
- `drill` and `rows` are reserved URL keys (the frontend's `RESERVED_URL_KEYS`): scope writers
  leave every other key, its order and its repetition alone, and no page-local key (`suite`,
  `days`, `name`, `tab`, …) is read or written by the drill.

What each dimension asks of the next request:

| Dimension | Next request |
|---|---|
| `suite`, `release` | extends the scope (`suite_name`, `release_id`) |
| `status` | selects the metric (`failed`, `broken`, …) |
| `class` | the coverage map's `class_key` |
| `test`, `error_signature` | ends the ladder: the rows panel opens with that selector |
| any other | becomes a rows selector, `bucket_<dimension>` |

## C6 · Report metrics — `fixtures/report_metrics`

The additive, **opt-in** `report_metrics` object on `GET /api/v1/metrics/summary`: present
only when the request carries `include=report_metrics` (repeatable or comma-separated;
unknown tokens are ignored). Without it the block is not computed and the response, its
SQL and its cache key are exactly those of the endpoint before the block existed; a client
that wants the block must ask for it. It holds the numbers the
report metrics strip (VIZ-302) shows, for the current window and the one before it, computed
over the SAME scope and basis as the endpoint's existing fields (`total_executions_7d`,
`avg_pass_rate_7d`, `avg_duration_ms` keep their shapes and values). Counts are
run-aggregate test executions (`pass_rate_basis` `executions`); under a suite filter a run
contributes its rows in the suite by effective suite, or its run totals while its rows have
not landed. Durations are run wall-clock (`test_runs.duration_ms`) of the runs in scope.

A **period** is `{runs, total_tests, passed, failed, broken, skipped, unknown, pass_rate,
total_duration_ms, avg_duration_ms, duration_runs, reasons, window}`. `unknown` is the tests
with no verdict (`total_tests` minus the four statuses, per run, never below 0).
`duration_runs` is how many of `runs` reported a duration, so a partial total is visible.
`window` is `{from, to, days}` (`YYYY-MM-DD`, UTC).

| Field | Type | Rule id | Rule |
|---|---|---|---|
| all fields below | | `required_field` | Every key is required, in both periods; a nullable one is sent as `null`, never left out. |
| `schema_version` | int ≥ 1 | | |
| `pass_rate_basis` | `"executions"` \| `"unique_tests"` | | The population the counts and the rate are over. |
| `current`, `previous` | period | | `previous` is the window of the same length ending where `current` starts. |
| period counts | int \| `null` | `non_negative`, `integer_count`, `safe_integer` | Change rule 3. A value that was not measured is `null`, never `0`. |
| period `pass_rate` | number \| `null` | `rate_range` | 0–100: passed over evaluated (passed + failed + broken). `null` when nothing was evaluated. |
| period `reasons` | `{metric: string}` | `metric_reason` | Every metric that is `null` has a reason under its own name with a non-whitespace character. |
| `previous.comparable` | bool | `comparable_reason` | `false` carries a non-blank `reason` and a `reason_code`; `true` carries neither (both `null`). |
| | | `comparable_measured` | `true` only when both periods have runs (`runs` ≥ 1). A delta is drawn only when `true`. |
| `previous.reason_code` | string \| `null` | `comparable_reason_code` | `no_data`, `partial_window`, `different_basis`, `not_measured` |
| `previous.reason` | string \| `null` | | Human text for the code: the previous window has no runs; the scope's history starts inside it (no run in the scope in the 90 days before the previous window opens -- a bounded lookback, so a scope silent for longer reads as starting there); one period counts whole-run totals where the other counts suite rows; the current window has no runs. |

## Feature flags — `flags.json`

The six rollout flags. Keys use underscores, because the flag API only accepts
`^[a-z][a-z0-9_]*$` — a dotted key could never be recreated through it. Migration `0192`
seeds them **disabled** with `rollout_percent` 100, like earlier flag migrations, so switching
`enabled_global` on is the whole rollout step. The frontend constants in
`frontend/src/config/vizFlags.ts` and the backend constants in `backend/app/core/viz_flags.py`
are each tested against this file.
