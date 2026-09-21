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
   `version`, the envelope's `scope.window.days`, matrix `x`/`y`, and matrix `value` when
   `value_type` is `count`. Two refinements keep the ids unambiguous:
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
| `truncated` / `truncated_total` | bool / int \| `null` | `truncated_total` | `truncated_total` is required (non-null) when `truncated` is true. |
| `measured` / `reason` | bool / string \| `null` | `measured_reason` | A `reason` with at least one non-whitespace character is required when `measured` is false. |
| `includes_in_progress` | int ≥ 0 | `non_negative` | Number of in-progress runs inside the scope. |
| `partial_day` | `YYYY-MM-DD` \| `null` | | The current UTC day when it is still accumulating. |
| `generated_at`, `as_of` | UTC instant | `utc_instant` | Exactly `YYYY-MM-DDTHH:MM:SS`, optionally `.` and 1–6 digits, then `Z` or `+00:00` — an RFC 3339 profile. The date is a real calendar date, the hour is 00–23, minutes and seconds are 00–59 (no leap second). No space separator, no `+0000`, no `-00:00`, no basic format. |

## C3 · ChartSeries — `fixtures/chart_series`

The single normalised model the renderers, the table view, CSV export and keyboard
navigation all read. Discriminated by `kind` (`kind_enum`): `series`, `matrix`, `tree`, `graph`.

| Kind | Shape | Rule ids |
|---|---|---|
| any | `{kind, …}` | `kind_enum` (`kind` is one of the four below) |
| `series` | `{dimensions: string[], x_type: "time"\|"category", series: [{key, label, points: [{x: string, y: number\|null, n: int}]}]}` | `series_cap` (≤ 8 series), `unique_series_key`, `point_cap` (≤ 366 points per series), `non_negative` (`n`) |
| `matrix` | `{value_type: "rate"\|"count"\|"status", x_labels, y_labels, cells: [{x: int, y: int, value, n: int}]}` | `cell_index_range`, `cell_cap` (≤ 5 400 cells), `status_vocab` (when `value_type` is `status`, `value` ∈ `passed, failed, broken, skipped, unknown` or `null`), `non_negative` and `integer_count` (when `value_type` is `count`, `value` is an integer ≥ 0 or `null`) |
| `tree` | `{nodes: [{id, parent_id\|null, label, value: number ≥ 0, measure: number\|null}]}` | `tree_parent_exists`, `tree_acyclic` (no cycles and no self-parent; a **non-empty** tree has ≥ 1 root; an empty `nodes` list is valid and means no data), `unique_node_id`, `node_cap` (≤ 500 nodes) |
| `graph` | `{nodes: [{id, label, size ≥ 0}], edges: [{source, target, weight}]}` | `edge_endpoints`, `weight_range` (0–1), `unique_node_id`, `node_cap` (≤ 200 nodes) |

`y: null` and `value: null` mean **no data** and are drawn as a gap or an empty cell, never as zero.
Numbers are finite: `NaN` and `±Infinity` are rejected wherever a number is allowed.
A whole-number value keeps its integer form on a round trip (`3` stays `3`, never `3.0`).

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
| `.groupBy` | `group_by_cap` | ≤ 2 items from the dimension enum (C5) |
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
| `dimension_enum` | `day, week, project, release, suite, status, failure_category, branch, environment, ingestion_source, test, error_signature` |
| `value_length` | 1–2 000 characters. |
| `status_vocab` | When `dimension` is `status`, `value` ∈ `passed, failed, broken, skipped, unknown`. |

## Feature flags — `flags.json`

The six rollout flags. Keys use underscores, because the flag API only accepts
`^[a-z][a-z0-9_]*$` — a dotted key could never be recreated through it. Migration `0192`
seeds them **disabled** with `rollout_percent` 100, like earlier flag migrations, so switching
`enabled_global` on is the whole rollout step. The frontend constants in
`frontend/src/config/vizFlags.ts` and the backend constants in `backend/app/core/viz_flags.py`
are each tested against this file.
