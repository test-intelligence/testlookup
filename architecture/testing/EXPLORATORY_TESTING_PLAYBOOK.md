# Exploratory Testing Playbook

Use 60–90 minute charters. Record build SHA, persona, project, data fixture,
browser, start/end time, observations, screenshots/traces, correlation IDs, and
every created defect. Vary one axis at a time, then combine the two riskiest.

| Charter | Persona | Mission and attacks | Oracles |
|---|---|---|---|
| EXP-01 Scope escape | Security-minded QA lead | Switch projects during slow requests; edit IDs; use stale tabs, exports and search | No foreign names/counts/timing; audit identifies actor/project |
| EXP-02 Evidence trust | Skeptical release manager | Follow every AI claim to source; remove baseline/artifact; create contradictory data | Missing evidence is explicit; deterministic facts win; no fabricated citation |
| EXP-03 Review pressure | Producer and reviewer | Self-review, two tabs, accept/reject races, retry rejected work, notification timing | One durable resolution; separation enforced; narrative remains gated |
| EXP-04 Connectivity churn | Remote end user | Change VPN/network during login, refresh, upload, polling and review | No silent logout on transient errors; safe retry guidance; no duplicate mutation |
| EXP-05 Worker failure | Operator | Kill worker/broker between proposal, commit and finalize; restore out of order | Fencing prevents stale writes; alert/DLQ evidence; one terminal result |
| EXP-06 Empty-to-scale | New user and enterprise lead | No project, no run, one run, 10k/50k cases, long names and Unicode | Honest empty states; bounded rendering; counts and filters remain correct |
| EXP-07 Accessibility | Keyboard/screen-reader user | Navigate dialogs, tables, graphs and toasts; zoom 200%; high contrast | Focus visible/trapped/restored; names and status do not rely on color |
| EXP-08 Time | Global team | DST boundaries, timezone changes, future/stale timestamps, long-running jobs | Consistent UTC storage and local display; age/alert thresholds correct |
| EXP-09 Provider outage | Operator | Fail LLM, vector store, Jira, email and secret store singly and in pairs | Deterministic fallback or explicit refusal; no secret/narrative leak |
| EXP-10 Workflow hostility | QA lead | Deep condition AST, cycles, huge fan-out, stale versions, concurrent publish | Bounded validation; immutable published version; reasoned refusal |
| EXP-11 Browser lifecycle | Everyday user | Back/forward, refresh, duplicate tabs, deploy mid-session, expired chunks | Deep links survive; one-shot chunk recovery; user work is not misrepresented |
| EXP-12 Observability | On-call engineer | Trigger each critical failure and trace UI → API → task → DB → alert | Correlation is possible; metrics fire and clear; messages suggest action |

## Heuristics

- **CRUD:** create, read, update, delete, repeat, cancel, race, and retry.
- **SFDIPOT:** structure, function, data, interfaces, platform, operations, time.
- **FEW HICCUPPS:** familiarity, explainability, world, history, image,
  comparable products, claims, user expectations, product, purpose, standards.
- **State:** refresh each intermediate state, replay the previous action, and
  attempt every forbidden transition.
- **Data:** null, empty, one, many, maximum, duplicate, Unicode, very long,
  malformed, stale, cross-project, and unauthorized.

Stop a session immediately for cross-project disclosure, secret exposure,
review bypass, destructive action without confirmation, or a stale worker
overwriting newer authority. Preserve evidence before attempting cleanup.
