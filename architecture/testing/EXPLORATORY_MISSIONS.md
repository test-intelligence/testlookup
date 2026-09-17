# Live exploratory mission catalogue

Part of [the Sol execution package](EXPLORATORY_EXECUTION_PACKAGE.md).
All missions are **NOT RUN** at planning time. Prior tests are starting points,
not results for these missions. Sol owns execution and evidence.

## Common protocol — mandatory for every mission

1. Record mission/iteration ID, UTC start/end, source SHA, deployed image IDs,
   browser/viewport, persona and role, project/fixture IDs, relevant settings,
   test commands and expected outcome before the first action.
2. Use synthetic fixtures tagged `exp-<run-id>-<mission>`. Maintain two projects:
   A accessible by the test persona and B inaccessible to it; a separate admin
   prepares B. Producer and reviewer must be distinct human JWT principals.
   Never attach existing customer evidence or real secrets to an artifact.
3. Observe the actual browser plus backend result. Preserve sanitized request
   method/path/status, correlation/request IDs, console errors, worker task and
   pipeline IDs, durable-state/audit evidence and sink payloads where relevant.
   Read minimal scoped fields; never dump entire databases or Kubernetes secrets.
4. Save first-attempt trace and failure screenshot; redact cookies, tokens,
   auth state, private evidence and personal data before sharing. Screenshots
   supplement assertions; they do not prove backend authority or delivery.
5. Every mission runs its happy, refusal and edge variants. Assert absence of
   unintended side effects on refusal. Poll bounded conditions using actual
   configured deadlines; do not replace unknown outcomes with arbitrary sleeps.
6. On failure, assign `EXP-BUG-nnn`, freeze fixture and evidence pointers, reduce
   to minimal reproduction, and compare with the source contract. Do not “fix”
   an expected policy denial. Follow the runbook's unit/E2E/review cycle.
7. Restore changed test settings/faults in `finally`/cleanup and verify recovery.
   Cleanup only recorded synthetic IDs. Record anything retained for debugging.
8. End with PASS / FAIL / BLOCKED, covered variants, evidence paths, discovered
   bugs and next hypothesis. Record skip reason rather than converting to PASS.

Logging/bug-capture requirements above apply to every mission. Each mission
below adds its specific observability and failure artifacts.

## Fixtures, environments and browser matrix

- **F1 Identity:** admin/operator, A-only viewer, A-only QA Engineer, A Lead
  producer and A Lead reviewer, B-only user, scoped API keys and MCP identity.
- **F2 Evidence:** 5-case run (4 passed, 1 reproducible failure), all-green,
  zero-case, all-skipped, duplicate and malformed reports; supported parser
  formats from the code; 32,767 and 50,000 cases only in the scale phase.
- **F3 Lineage:** release R in A, baseline and candidate runs, one failure
  cluster, promoted defect, reviewed/unreviewed/superseded report versions.
- **F4 Delivery:** dedicated email/webhook/comment/Jira test destinations with
  request journal and unique run markers; never production recipients.
- **F5 Faults:** dedicated worker/queue/datastores/provider or isolated network
  fault proxy. Record injection and recovery commands before use.
- **F6 Time/content:** UTC timestamps spanning DST, leap-day and end-of-month,
  Unicode/RTL, long text, quotes, HTML-looking text and inert prompt-injection
  instructions. Use harmless sentinels, no external exfiltration destination.

All product missions first run in Chromium. Repeat authentication, review,
release, project isolation, upload/download and repaired UI flows in Firefox
and WebKit. M19 includes 375px, 768px, desktop, 200% zoom, keyboard-only and
available screen-reader checks. If a browser/runtime is unavailable, record
BLOCKED; Chromium is not evidence for all engines.

## M01 — Authentication and session continuity (P0, identity QA)

**Preconditions:** F1; real form login available; current auth expiry/refresh
contract inspected. Admin-only global setup is not sufficient for this mission.

**Steps:** (1) Open `/reviews` or an actual protected deep link while logged out;
log in and verify intended destination. (2) Submit incorrect credentials and
empty/Unicode input; verify error and no authenticated state. (3) Expire/revoke
a test session and trigger concurrent reads. (4) Interrupt network during token
refresh, restore, retry; log out from one tab and revisit another.
(5) Complete first-use onboarding in a synthetic empty project, update profile
and exercise the authenticated password-reset path; reload to verify persistence.

**Expected:** no protected data before authentication; contract-correct refresh
or login redirection; bounded refresh attempts; network failures distinguished
from explicit invalid credentials; logout clears private visible/cache state.

**Observe/capture:** auth request statuses and counts, navigation history,
sanitized storage-key presence (not token values), console and server request
IDs. Capture any redirect loop, swallowed error or repeated mutation.

## M02 — Project isolation through every interface (P0, security QA)

**Preconditions:** F1 plus distinct A/B sentinel names, runs and artifacts.

**Steps:** (1) As A-only user read lists/details/search/export. (2) Substitute B
IDs in URLs, query strings and bodies, including child IDs with A parent.
(3) Warm A cache, switch scope while delaying B requests and return a failure;
retry and use browser back/forward. (4) Repeat API calls with scoped key and
MCP/CLI. Test all-project selection and deleted/stale project selection.

**Expected:** no B data, filenames, counts or content; route-specific 403/404
according to access contract; no mutation on denied access; UI never displays
the previous project's rows under a new selection.

**Observe/capture:** scope-qualified SWR keys, actual request project IDs,
response body sentinels, exported bytes and audit rows. Capture body/URL mismatch
and timing anomalies for investigation without claiming timing leakage from
a single measurement.

## M03 — Upload, parse, deduplicate and recover (P0, ingestion QA)

**Preconditions:** F2; disposable project; supported format/size limits read
from backend and upload UI; real workers and object store available.

**Steps:** (1) Upload every advertised report format and assert counts/results.
(2) Submit malformed, empty, wrong-format and boundary-size payloads.
(3) Double-click/replay an upload and disconnect after server acceptance but
before browser response; recover through supported retry. (4) Upload Unicode
names, duplicate case identifiers and all-skipped data; refresh during parsing.

**Expected:** accepted work remains durable; rejected payload has an actionable
error and no misleading completed run; duplicates follow documented identity
rules; UI counts and stored cases match. Unknown outcomes are reconciled before
resubmission. Zero/skipped denominators are honest.

**Observe/capture:** content hash, request/run IDs, parse logs, object metadata,
outbox/task IDs and eventual rows. Preserve minimized report fixture and replay
sequence for each parsing/dedup defect.

## M04 — Full ingest → intelligence → defect → release journey (P0, product QA)

**Preconditions:** F2 five-case report, F3 release association, worker/model
settings recorded, deterministic facts defined independently of AI prose.

**Steps:** (1) Ingest through public UI/API. (2) Wait for actual queued work to
produce intelligence. (3) Open failure evidence and cluster; promote to a local
HIGH defect. (4) Open release and recompute readiness. (5) Reload all pages and
retrieve public APIs with the same actor; compare lineage and counts.

**Expected:** one connected project/run/cluster/defect/release lineage; promoted
defect inherits release; policy explains its effect; no fabricated evidence.
AI degradation is visible and does not erase deterministic facts.

**Observe/capture:** shared lineage manifest, real broker/task completion,
database and API facts, append-only decision ID, UI trace. The existing
PostgreSQL integration fixture is a useful oracle, not a substitute for this
real network/worker journey.

**Executed 2026-09-16 (shipped):** the real queued pipeline completed 16 stages
with no failures, one promoted HIGH defect blocked a five-test release as
`NO_GO`, and API, PostgreSQL, reload, and three-browser facts agreed. Shipped
EXP-BUG-013 so `/releases/:releaseId` renders and expands the requested release.
The final fix resolves the release independently of the persisted project list
and honors the real `#phase-…` action target after async detail rendering.
The healthy hosted-LLM path ran; degraded-model behavior remains assigned to
its dedicated fault/degradation missions. Evidence:
`architecture/verification/exploratory-20260916/M04-intelligence-release.md`.

## M05 — Defect deduplication and ambiguous Jira outcome (P0, integration QA)

**Preconditions:** F3/F4; dedicated Jira sandbox and supported reconciliation
procedure. Local-defect variants can run while sandbox access is blocked.

**Steps:** (1) Promote and file a defect; repeat same intent. (2) Race two
requests. (3) Let sandbox receive create, then drop its response. (4) Retry,
reconcile by application signature, and inspect history. (5) Exercise disabled
connector, invalid mapping, 429 and 5xx; foreign project and viewer negatives.

**Expected:** no duplicate issue from ambiguous create; `outcome_unknown` stays
explicit until reconciliation; no blind “confirm not filed” automation; local
defect persists with truthful integration state. Refused role leaves no effect.

**Observe/capture:** sink issue count, signature, ledger claim, response reason,
audit rows and timestamps. Capture both sides of the lost-response boundary.

**Executed 2026-09-16 (blocked at external boundary):** all 37 focused endpoint
tests, all three real-PostgreSQL exactly-once tests, and 36 local
defect/promotion tests passed against the exact homelab-deployed candidate. The
database proof serialized a double submit, survived a post-acceptance local
rollback, kept an ambiguous claim explicit, refused blind retry, and reconciled
by signature label; three wrong-behavior mutations were killed. A dedicated
Jira sandbox and fault-capable request journal are unavailable, so real Jira
create/drop-response, 429/5xx, sink-count, and eventual-index behavior remain
BLOCKED. Evidence:
`architecture/verification/exploratory-20260916/M05-defect-deduplication.md`.

## M06 — Release decisions, overrides and outcome history (P0, release QA)

**Preconditions:** F3; policy thresholds, roles and review settings known.

**Steps:** (1) Compute healthy and failing releases; inspect reasons and evidence.
(2) Move each relevant numeric threshold below/at/above its boundary; include
zero/all-skipped runs. (3) Override through allowed role/reason workflow and
reload history. (4) Race recompute/override; try stale decision and viewer writes.
(5) Mark test incident/rollback and inspect audit and subsequent evaluation data.

**Expected:** explainable result consistent across UI/API/export; immutable
decision/outcome history; authorized overrides preserve original facts; no
optimistic GO from missing evidence or unreviewed gated report.

**Observe/capture:** exact policy snapshot, counts, decision/evidence hashes,
override/outcome/audit IDs. Assert business verdict and reason, not only color.

**Executed 2026-09-17 — PASSED.** Exact candidate `5d8dd8a5` was deployed
before final tests. Live healthy/failing/skipped/empty boundaries produced
`GO`/`NO_GO`/`NOT_EVALUATED`/`NOT_EVALUATED`; viewer writes were refused;
incident and rollback outcomes survived reload with two activity events. Five
real PostgreSQL journeys covered release/phase history, concurrent overrides,
and override/recompute serialization. The broad release suite passed 811 tests
and six mutations were killed. EXP-BUG-014 through EXP-BUG-017 shipped.
Evidence: `architecture/verification/exploratory-20260916/M06-release-decisions.md`.

## M07 — Review subject, races and separation of duties (P0, security QA)

**Preconditions:** F1 producer/reviewer; F3 pending report and distinct Investigator
subjects; dedicated enforcement-on test stack, plus shadow-mode comparison.

**Steps:** (1) Read pending report and queue. (2) Attempt producer self-review in
act mode; try QA Engineer, API key and synthetic principal. (3) Accept as distinct
authorized reviewer; separately reject with and without reason. (4) Race accept
and reject from two tabs. (5) Change evidence/supersede; retry rejected pipeline
and invocation; verify review of one Investigator cannot authorize another.

**Expected:** one durable settlement; refusal does not change state; rejected
retry refused; identity restricted as contracted; public status projects
correctly; stale/superseded authority cannot authorize a later action.

**Observe/capture:** subject/evidence hash, proposer, audit-only reviewer identity,
state transition timeline and loser response. Preserve both concurrent requests.

**Execution 2026-09-17:** **PARTIAL.** Exact deployed candidate `bbce4f5f`
passed 323 focused backend tests, 15 real-PostgreSQL race/resume tests, 22
focused frontend tests, and the three-browser review transition. Sixteen asserted
wrong-behaviour mutations were killed. EXP-BUG-018 through EXP-BUG-022 fixed
evidence identity and serialization, parent/Investigator authority confusion,
proposal-time mode separation, distinct Investigator supersession, and queued
worker resurrection. A stable parent-row lock also closed the no-row phantom
race between concurrent first review inserts; both finalizers now take that
parent before their child row, matching retention/reset cascade order. The
shared homelab kept
`REVIEW_GATE_ENFORCED=false`;
the required dedicated enforcement-on stack is unavailable, so that deployed
identity matrix remains blocked. Full record:
`architecture/verification/exploratory-20260916/M07-review-authority.md`.

## M08 — Distribution, drafts and notification replay (P0, delivery QA)

**Preconditions:** M07; F4; all real distribution consumers enumerated from
`report_distribution_policy.py` callers. Record global enforcement and project
draft setting; isolate changes from shared homelab settings.

**Steps:** (1) For summary, decision report and exact Investigator subject test
pending/accepted/rejected/superseded/no-review states against each consumer that
actually accepts that subject: export, share, digest attachment, notification,
the implemented `release.decided` webhook, and applicable PR/MR comment path.
Do not assume every subject is emitted to every channel. (2) Test legitimate
project/interactive draft exception and unauthorized override. (3) Retry sink
failure before/after acceptance; verify deployed delivery and attachment bytes.
(4) Compare enforcement-off “would refuse” audit behavior.

**Expected:** gated text withheld by default when enforced; accepted content
delivered; allowed pending draft watermarked and audited; rejected/superseded
not distributed as draft; no stale narrative or cross-subject acceptance.

**Observe/capture:** channel × subject × state × draft-policy matrix; real sink
payload hashes/counts, audit and notification history. Missing sink access is
BLOCKED, not passed by inspecting a mocked manager call.
PR/MR comments currently originate before the AI pipeline and are not
automatically reposted after acceptance; record this as a known gap rather than
inventing a full Cartesian expectation.

**Execution 2026-09-17:** **PARTIAL.** Exact executable candidate `b3a0d5ee`
was deployed before final tests as `build-20260917-084451`. The final focused
repair suite passed 251 tests, all six asserted mutation harnesses killed 48 wrong
behaviors, Ruff passed, the mypy ratchet held at 369 errors, all 43 quality
guards passed, and all 259 guard/ratchet self-tests passed. EXP-BUG-023 through
EXP-BUG-048 fixed missing JSON draft marking/auditing, cross-version and
cross-evidence authority, terminal narrative/verdict leakage, unauthorized or
unaudited advisory release values, invented withheld release signals, false
delivery audits, stale notification/webhook retries, and stale delivery history.
Queued summaries and release webhooks now retain the exact pipeline-and-evidence
subject; queued summaries also retain their immutable source bytes and release
events use the immutable report's decision bytes after publication. Legacy rows
without exact identity fail closed under enforcement. The `release.decided`
sink and release-readiness API use the persisted decision pipeline, while human
override events do not require an agent report. The shared homelab remains
enforcement-off. Decision-row locking orders agent and override events, and each
override retains its committed audit ordinal and snapshot. Immutable summary
jobs no longer depend on a Mongo read. The homelab has no
dedicated SMTP, Slack, Teams, public webhook, GitHub, or GitLab receiver, so the
required real-sink bytes/counts remain blocked.
Full record:
`architecture/verification/exploratory-20260916/M08-distribution-replay.md`.

## M09 — Search and evidence retrieval (P1, search QA)

**Preconditions:** indexed A/B corpus with known terms and citations; isolated
Chroma fault path; keyword/semantic/hybrid modes supported by current code.

**Steps:** (1) Search each mode and page/sort/filter through known results.
(2) Use empty, long, Unicode and punctuation queries, missing index and stale
deleted evidence. (3) Disable vector dependency, verify supported fallback,
restore and re-query. (4) Attempt B references and unsafe-looking attachment URLs.

**Expected:** scope and counts consistent; fallback truthfully labeled; no stale
deleted authority or arbitrary script execution; missing evidence explicit.

**Observe/capture:** search mode/status/latency, result IDs, provider failures,
index/relational discrepancy and opened evidence. Do not demand exact semantic
ordering where the contract promises relevance rather than stable rank.

**Execution 2026-09-17 (shipped):** PASS on exact deployed executable candidate
`0c867696` (`build-20260917-104804`). EXP-BUG-049 through EXP-BUG-056 fixed
single-session query overlap, false hybrid fallback labels, unreachable later
hybrid pages, silent adapter/DB failures, cross-project suite collapse and
false exact totals and duplicate cross-project flaky identities. Bounded semantic and mixed-entity retrieval remains bounded
by design and now identifies counts as lower bounds. Full evidence:
`architecture/verification/exploratory-20260916/M09-search-evidence.md`.

## M10 — RAG, knowledge and AI explanation trust (P1, evidence QA)

**Preconditions:** controlled knowledge source and failed run; missing/conflicting
evidence fixtures; provider identity and budget recorded.

**Steps:** (1) Ingest/sync a small known source and ask answerable/unanswerable
questions. (2) Open every citation and source detail; edit/delete source, repeat.
(3) Include inert instructions in retrieved content asking to ignore project
scope or invent evidence. (4) Explore empty/error drawers, unsafe URL schemes,
long answers and provider timeout; refresh conversation or generation view.

**Expected:** claims distinguish suggestions from facts; unsupported evidence
explicit, citations correct and scoped; retrieved text cannot grant tools or
authority; rendering/link handling safe; failed generations do not look complete.

**Observe/capture:** sanitized prompt/output hashes, source/citation IDs, policy
decision and provenance. Reproduce rendering/security bugs deterministically;
evaluate answer quality separately from a single model phrasing difference.

**Execution 2026-09-17 (shipped, partial):** Exact executable candidate
`805f7714` was deployed first as `build-20260917-125909`. EXP-BUG-057 through
EXP-BUG-069 fixed cross-tenant chat scope, revoked-session access, raw prompt
persistence, fabricated citations, prompt injection and delimiter escape,
orphan/stale vector authority,
unsafe rendering/links, false successful generations and false synchronized
sources. The broad backend suite passed 406 tests, four focused frontend tests
passed, and 19 wrong-behavior mutations were killed. Ruff, TypeScript, ESLint,
the 369-error mypy ratchet, all 43 guards and 238 guard self-tests passed.
Homelab has zero knowledge sources and no installed model, so controlled positive
ingest, answerable/conflicting generation and real-provider timeout remain
blocked. CLI/MCP RAG surfaces and richer chat source detail also remain gaps.
Evidence: `architecture/verification/exploratory-20260916/M10-rag-trust.md`.

## M11 — Configuration precedence and tool authority (P0, agent QA)

**Preconditions:** registered read-only/report/mutating capabilities; copied
test-only project configs; environment ceilings captured without credentials.

**Steps:** (1) Read defaults, save allowed project values and reload. (2) Invoke
with narrower tier/retry/tools overrides. (3) Attempt widening, unknown fields,
endpoint/API key fields, unsupported tools, and attempts×timeout over deadline.
(4) Change project config after a run; inspect frozen snapshot and retry behavior.
(5) Test `shadow`, `suggest`, `act` with accepted and pending proposing review.

**Expected:** tighten-only validation; no silent no-op accepted settings; frozen
authority remains immutable; changed config follows rerun/fingerprint contract;
mutation requires BOTH current `act` and accepted exact subject.

**Observe/capture:** sanitized version/snapshot/clamps, validation reason, tool
ledger and absence of external side effect. Test legacy Investigator/Fixer GET
projection and retired PUT 405 without migrating them again.

## M12 — Invoke, idempotency, polling and event streams (P0, API QA)

**Preconditions:** catalog and generated API docs; independently invokable
capabilities selected via current catalog, real worker/Redis.

**Steps:** (1) Exercise asynchronous and sync-eligible requests using stored
subject. (2) Repeat identical idempotency key then reuse it for changed payload;
race submissions. (3) Consume stream ticket once, replay/expire it and try another
actor. (4) Disconnect stream/VPN; resume through polling/retry links. (5) Exhaust
sync slots with bounded concurrency; cancel and retry permitted runs.

**Expected:** catalog `invokable` truth honored; bounded waiting with correct
200/202/409/422/503 contract; durable invocation uniqueness; single-use ticket;
poll fallback and four public statuses; canceled/rejected run not resurrected.

**Observe/capture:** invocation/pipeline IDs, key hash, request counts, stream
events, ticket status (never its secret), attempts and durable terminal result.

## M13 — Custom workflow and reviewer governance (P1, workflow QA)

**Preconditions:** built-ins, project configs and versioned draft fixtures;
documented eval gate and workflow runtime deviations read from §12.

**Steps:** (1) Fork built-in, edit, preview, validate, evaluate and publish;
execute supported published version. (2) Submit unknown capability, missing
dependency, cycle, invalid JSON condition, too-deep tree, disallowed tool and
cross-project config. (3) Race publication; try editing published version and
replay against different version. (4) Trigger reviewer reject/retry/pass flags
and exhaust its shared model-call budget.

**Expected:** bounded validation; immutable publication and frozen plan authority;
unsupported runtime bindings explicitly rejected; reviewer cannot manufacture
human acceptance or exceed shared budget.

**Observe/capture:** definition version/hash, compile/eval reason, graph/node
trace, reviewer verdict and call ledger. Treat advertised but unsupported
behavior as a documented discrepancy; do not weaken compiler to make it run.

## M14 — Worker death, stale writes and recovery (P0, reliability QA)

**Preconditions:** F5 dedicated stack with long-running synthetic job; actual
heartbeat, lease, reaper and retry/deadline timings recorded; rollback ready.

**Steps:** (1) Start job and observe lease. (2) Pause/kill its worker after a
checkpoint; observe expiry/reaper and resumed attempt. (3) Resume old process
after replacement holds authority. (4) Race cancellation with retryable failure.
(5) Repeat at outbox-dispatch and finalization boundaries using targeted fault
hooks/process control; restore worker in every path.

**Expected:** stale fence writes affect zero rows; cancel wins; checkpoint
authority preserved; one durable result, no dropped accepted work; attempts and
public states match policy. Exhaustion lands failed/DLQ, not endless running.

**Observe/capture:** lease/token hashes, attempt timeline, checkpoint/result IDs,
worker logs, DB authoritative state and outbox/side-effect count. Never pause
all shared workers to test one fixture.

## M15 — DLQ, traces and alerts (P1, operator QA)

**Preconditions:** M14/F5; real monitoring installed or explicitly blocked;
canonical admin DLQ list/replay and source-specific replay limitations inspected.

**Steps:** (1) Trace UI request to task/stage and durable result. (2) Create a
known allowlisted failed task; list and replay as admin, repeat concurrently.
(3) Submit unknown/inspection-only entry and unauthorized actor. (4) Exercise
in-progress age, DLQ depth and pending-review age past configured thresholds;
recover and observe alert clear. Use isolated seeded clocks/data, never wait
24 hours or mutate shared production timestamps to manufacture a pass.

**Expected:** allowed replay only; retained record on ambiguous delivery; metrics
and spans reflect actual behavior; all three alerts fire and clear. Deadline
age is distinct from lease expiry; `retry_wait` remains active.

**Observe/capture:** trace/span IDs, scrape samples, alert evaluator timestamps,
DLQ entry/task IDs and duplicate protection. Record the documented at-least-once
replay limitation rather than claiming cross-system atomicity.

## M16 — Test Management and lifecycle races (P1, product QA)

**Preconditions:** synthetic cases/suites/plans, lifecycle roles/reasons from
`test_case_lifecycle_service.py`, ownership and quarantine fixtures.

**Steps:** (1) Create/edit/link cases, suites and plans; import/export and inspect
versions/audit. (2) Exercise each permitted and forbidden lifecycle transition,
ownership change, quarantine/release and history view. (3) Race edits/transition
from stale tabs, change filters/pagination and retry after table/health outage.
(4) Test duplicate names, empty suites, maximum field lengths and foreign IDs.

**Expected:** one authoritative transition/version, required reason and role;
stable filtering and correct totals; failed load not presented as empty catalog;
same-scope stale data labeled; recovery single-flight and durable after reload.

**Observe/capture:** versions/audit rows, request counts, focused screenshot of
failure/retry state, export content and related release effects.

## M17 — Analytics, dashboards and saved views (P1, product QA)

**Preconditions:** corpus with independently calculated counts, widget limits,
saved-view ownership and date-range semantics inspected.

**Steps:** (1) Add/reorder/remove widgets, save and reopen view. (2) Duplicate to
limit and attempt one more; open malformed/stale saved layout. (3) Change date,
project, tags and all-project selection; compare summary to run rows. (4) Test
zero/all-skipped denominator, no data, API outage and DST-crossing range.
(5) Reconcile value/performance and billing usage/cost displays with synthetic
ledger facts; inspect supported empty/disabled states without making purchases.

**Expected:** persisted layout and bounded limits; truthful numbers and empty/
error distinctions; foreign view access refused; no silent dropped widget or
NaN/Infinity; retry recovers without losing the user's valid selection.

**Observe/capture:** expected calculation worksheet, request filters, saved-view
JSON with synthetic data, before/after layout and console errors.

## M18 — Integration, digest and notification settings (P1, integration QA)

**Preconditions:** F4 sinks; supported connector fields, masked-secret update
contract and schedule timezone rules inspected; test accounts only.

**Steps:** (1) Configure/test/save/reload each supported connector used by this
deployment. (2) Save masked unchanged credentials then deliberately replace
test credentials; inspect responses and log redaction. (3) Test validation,
provider 401/429/5xx, cancellation and outage recovery. (4) Schedule digest at
DST/end-month boundaries and verify selection window, delivery and history.
(5) Exercise settings navigation, isolated feature-flag changes and seed-data
controls on disposable fixtures; confirm no accidental shared-project reset.

**Expected:** test versus save clearly distinguished; unchanged secret remains
valid without exposure; invalid settings not falsely saved; scope/roles enforced;
digest timing/content and review policy correct; duplicates controlled.

**Observe/capture:** redacted settings diff, connection diagnostic, sink request
count, scheduler timezone and delivery/audit IDs. Never copy real credentials.

## M19 — Accessibility, UX and browser lifecycle (P1, accessibility QA)

**Preconditions:** critical routes from M01–M18; browser matrix; keyboard and
available screen reader; axe tool added by Sol if absent and justified.

**Steps:** (1) Complete login, project selection, upload, triage, review and release
with keyboard only. (2) Open nested dialogs, cycle Tab/Shift+Tab, Escape and focus
return. (3) Use mobile/tablet/200% zoom, long Unicode/RTL text, dark/light themes.
(4) Run automated accessibility scan, inspect labels/live errors/color dependence
manually. (5) Refresh deep links, duplicate tabs and deploy across a cached chunk.

**Expected:** named controls, visible reachable focus, no keyboard trap or hidden
essential content; recoverable errors announced; safe stale-asset recovery;
user edits not falsely reported saved. Automated scan is not complete WCAG proof.

**Observe/capture:** role/name/focus order, viewport screenshots, axe findings,
screen-reader observations and navigation/asset responses, each linked to bug ID.

## M20 — Keys, MFA/SSO/SCIM and permission changes (P0, identity QA)

**Preconditions:** dedicated IdP/test tenant where supported, F1, key issuance
and revoke rules, enabled identity features inventoried.

**Steps:** (1) Create/use/revoke scoped key; race revoke with subsequent request,
test wrong-project use and review mutation refusal. (2) Exercise MFA setup,
challenge/recovery and invalid/replayed token. (3) Exercise SSO allowed callback,
bad/replayed state, disallowed redirect and disabled user. (4) Change membership/
role or SCIM deprovision, revisit old tabs and use old credentials.
Create/manage synthetic users through supported administration; invitation or
recovery deliveries go only to the dedicated sink. Verify duplicate/disabled-user
handling and least-privilege defaults without contacting real recipients.

**Expected:** no elevated stale authority or bypass; secret revealed only per
contract; authentication and recovery rules enforced; identity audit complete.

**Observe/capture:** redacted protocol error/redirect destination, identity-event
IDs and post-revoke statuses. Disabled features are recorded with config proof;
enabled features without test IdP access are BLOCKED.

## M21 — Retention, protected evidence, deletion and export (P0, data QA)

**Preconditions:** dedicated synthetic project/stores; successful disposable
backup/restore rehearsal; retention clocks and current protections inspected.
Legal holds are future product work and are recorded as a gap, not exercised.

**Steps:** (1) Preview retention for old/unprotected and protected reviewed/release
evidence. (2) Execute only scoped synthetic deletion; verify rows, objects,
indexes and links. (3) Interrupt/retry job at a store boundary. (4) Test viewer,
foreign project, protected subject, stale preview and repeated delete. (5) Export
remaining evidence and reconcile content/history.

**Expected:** preview truthful; protected evidence preserved; async deletion
state/retry honest; no orphan leakage or unrelated deletion; append-only audit
rules preserved and approved data clock enforced.

**Observe/capture:** preview counts, synthetic ID allowlist, deletion-job timeline,
store-specific before/after counts, audit and restore record. Never run whole-
namespace teardown or purge unrelated data to prepare a mission.

## M22 — CLI, MCP and SDK contract parity (P1, client QA)

**Preconditions:** installed clients from candidate source and real backend;
scoped credentials; generated agent curl/Postman docs available.

**Steps:** (1) Discover supported commands/tools; execute read and allowed invoke
journeys from CLI, MCP and installed SDKs. (2) Compare review envelopes/statuses
with REST/UI. (3) Expire/revoke credential, lose network, submit invalid/foreign
IDs; inspect output and exit codes. (4) Confirm MCP lacks review accept/reject;
API-key CLI profile cannot bypass JWT-only review.

**Expected:** aligned wire contracts, useful errors/nonzero failure exit, no
secret leakage, bounded retries and no duplicate mutation; unsupported capabilities
explicitly declined.

**Observe/capture:** sanitized command and exit code, MCP tool inventory/result,
request IDs and backend effects. SDK unit tests alone are not this live proof.

## M23 — Live event ordering, disconnect and replay (P0, streaming QA)

**Preconditions:** real supported live SDK/ingest endpoint, F5 Redis/worker;
sequence/dedup semantics read from live recovery code.

**Steps:** (1) Stream a known run and compare live UI to durable final results.
(2) Send duplicate and out-of-order events using supported protocol. (3) Break
client connection after acknowledgment; reconnect/replay. (4) Restart isolated
Redis/consumer and verify recovery. (5) Attempt foreign run events and stale
session reset; finish then inspect history/search.

**Expected:** acknowledged data reconciles to authority; ordering/dedup rules
hold; no foreign write or stale reset; restart doesn't silently discard queues;
live/final counts agree and recovery limitation is explicit.

**Observe/capture:** event sequence/ack IDs, stream/persistence offsets, Redis
durability configuration, worker logs and final run counts. Do not infer zero
power-loss data loss from AOF `everysec`; distinguish pod restart from disk loss.

## M24 — Bounded load, network churn and combined outages (P1, reliability QA)

**Preconditions:** F5; metrics healthy; runbook load budget recorded before run;
dedicated dataset, no active shared workloads in fault target.

**Steps:** (1) Establish single-user reference for list/search/upload/triage.
(2) Increase concurrency 1→2→5 only within declared budget; exercise 1k, 10k,
32,767 and 50k cases sequentially when resource checks permit. (3) Inject one
bounded provider/vector/broker outage, restore, then the two highest-risk pairs.
(4) Emulate browser offline/online during upload/review/polling; if authorized
device VPN switching is available, separately repeat actual VPN transition.

**Expected:** no lost accepted data, runaway retry, false success or cross-scope
cache; latency/queue depth recover to recorded budget after restoration; correct
fail-closed cost reservation and advertised fallbacks. Browser offline simulation
is not claimed as proof of actual VPN/DNS/proxy behavior.

**Observe/capture:** p50/p95/p99, error rates, queues, CPU/RAM/disk, time-to-recovery
and sink effects. Abort thresholds/cleanup in runbook are mandatory.

## M25 — Offline policy, model budgets and evaluation evidence (P0, eval QA)

**Preconditions:** installed exact local model tags, adequate memory, candidate
golden datasets and evaluation procedure from `architecture/AI_EVALUATION.md`.

**Steps:** (1) Invoke representative deterministic/SLM/LLM paths; record routing,
repair, escalation and fallback. (2) Pin SLM and try low-confidence escalation;
exhaust token/call/cost budgets. (3) Under offline mode try cloud and off-box
“local” endpoint including cache-hit path; verify no outbound call. (4) Run fresh
paired E9.3 inference and reviewer/workflow gates as applicable. (5) Test missing
samples, stale manifest, same-manifest labels and holdout boundaries.

**Expected:** budgeted bounded escalation; explicit pin cannot loosen; offline
ceiling holds; insufficient evidence cannot pass quality gates; provenance
resolves to actual model/dataset/hash; no default promotion without passing G2.

**Observe/capture:** actual provider/model digest, network deny observation,
call/cost ledger, raw-output hashes and eval verdict/confidence/sample counts.
Mocks validate contracts only. Hardware/model absence blocks inference claims.

## M26 — Candidate deployment, rollback and homelab acceptance (P0, operator QA)

**Preconditions:** locally green reviewed candidate commits, runbook deployment
preflight, backed-up disposable/approved homelab data, prior digests/schema known.

**Steps:** (1) Build and deploy from clean candidate using supported homelab script.
(2) Verify candidate migration and every serving application image ID, not just
ingress health. (3) Run all new E2E, full applicable live mission matrix and
critical browser engines. (4) Rehearse rollback on disposable environment and
restore candidate; verify accepted queue work and persistent state. (5) After
single merge verify final main tree/image relationship and smoke again.

**Expected:** actual candidate serves, worker/MCP/frontend/backend versions agree;
schema compatible; no false successful rollout against old pods; rollback usable;
critical user journey and telemetry recover. Any failure blocks merge/release.

**Observe/capture:** source SHA/tree, script exit, image tags/digests, rollout
events, migration job/head, sanitized smoke and rollback evidence. Follow runbook
on `--skip-build`: it selects a registry tag and is not an exact-candidate selector.
