# Agentic Runtime Adapter

Status: Phase 3 durable cluster-child vertical slice; default-off and backward compatible

## Purpose

The runtime adapter exposes the existing LangGraph pipeline through versioned
`AgenticRunV1`, `AgentTaskV1`, `AgentFindingV1`, and `CapabilitySpecV1`
contracts. It does not introduce a second execution engine or duplicate the
pipeline database model.

## Current flow

1. `agent_planner.build_workflow_plan` emits planner-v2 metadata for every
   current stage: capability identity, selection reason, dependencies,
   evidence prerequisites, permission, latency/timeout, budget, fallback and
   concurrency class.
2. The plan is canonically hashed and persisted through the existing pipeline
   `execution_metadata` path.
3. `GET /api/v1/agents/pipelines/{pipeline_id}/agentic-runtime` applies the
   same tenant authorization as the timeline and replay endpoints.
4. The adapter maps `AgentPipelineRun` and `AgentStageResult` into a single
   immutable read contract with parent task IDs, allocated budget, actual
   usage, selection rationale and stop reason.
5. Authorized child pipelines are recursively projected under their persisted
   parent cluster task with typed hypothesis findings, bounded/redacted
   evidence, child spend, attempts, and stop reasons.
6. Planner-v1 records remain readable. They are explicitly labeled
   `legacy-unhashed`; the adapter does not invent an authority hash for them.

## Trust and compatibility boundaries

- The endpoint is a projection, not a new source of truth.
- Existing pipeline, timeline, replay and investigation APIs are unchanged.
- Capability permissions are descriptive in this slice. Execution-time
  enforcement remains in the existing agents and tool authorization layer.
- Findings and evidence are projected only from the typed Investigator
  hypothesis/verdict contract. Arbitrary stage JSON is never promoted into a
  trusted claim.
- Migration `0121` persists parent pipeline/task identity, spawn depth,
  cluster scope, stable task/idempotency keys, task budgets/dependencies, and
  the child dispatch outbox. Runtime linkage never trusts display cluster IDs.
- Initial plans are written with the pipeline row and verified by recomputing
  their canonical hash. Missing legacy plans are `legacy_unhashed`; malformed
  or changed version-2 plans are `failed`, never inferred as verified.
- Cross-pipeline checkpoint restore is checksum-authorized. A completed stage
  is restored only when its `checkpoint_data` hashes to the persisted
  `_replay.output_checksum_sha256` and carries runtime-version metadata;
  missing legacy replay metadata or tampered checkpoint bodies are ignored and
  the stage is rerun.
- Explicit zero call/token/cost/time limits remain zero (a hard disable),
  distinct from an unspecified `null` limit. Investigator run-level budgets
  are exposed separately from task allocations.
- Runtime output is capped at 200 persisted stage attempts and reports both
  the observed row count and `tasks_truncated`.

## Investigator aggregate budget authority

- Every optional hypothesis and synthesis LLM call must acquire a row-locked
  reservation on its `AgentInvestigation` before provider invocation.
- Ledger schema version 2 validates receipt numbers, timestamps, status and
  the exact investigation/project/run/pipeline/stage binding. Malformed or
  versionless receipts fail closed.
- Logical reservation IDs are stable across broker delivery. Active and
  completed receipts both deny replay, so a provider call cannot be repeated
  under the same task identity.
- Admission includes actual plus outstanding call/token usage. Settlement
  records full provider-observed usage, including overage, and retains a
  bounded completed receipt.
- Reservations carry a conservative priced token envelope. If settlement or
  a worker is lost, terminal/reaper recovery charges reserved calls, tokens
  and cost and marks the accounting source as degraded.
- A conditional `queued -> running` update is the execution claim. The stable
  UUIDv5 pipeline identity and the scheduled stuck-pipeline reaper recover
  startup failures and expired execution leases.
- Investigation, pipeline, promotion counter and AgentRun terminal writes are
  committed together. Replayed finalization only reconciles the pipeline and
  does not duplicate the ledger or promotion count.
- Accounting faults remain visible as task/run stop reasons but do not imply
  `budget_exhausted`; only call, token or wall-clock limits do.

## Decision-graph aggregate budget authority

- The effective project policy is frozen into the initial, canonically hashed workflow
  plan and the parent `AgentPipelineRun.execution_metadata.run_budget`; final
  explanatory plans cannot replace this authority.
- Every LLM-capable decision-graph stage receives a deterministic calls/tokens/
  cost/time envelope. `None` means legacy/unbounded metadata; explicit zero is a
  hard disable. The envelope is admitted under a row lock on the parent pipeline,
  so parallel branches cannot reserve beyond the aggregate cap.
- Provider invocation goes through the shared `BudgetedLLM` boundary. Denied
  reservations fail before a provider call; successful calls record observed
  invocation and provider-reported token usage even when an agent omitted
  explicit metrics. Settlement retains bounded receipts and marks overruns.
- Terminal success/failure reconciles outstanding envelopes conservatively and
  persists `budget_spend`, stop reasons, and completed receipts with the initial
  plan. Investigator children remain on their separate `AgentInvestigation` ledger
  and are not double-accounted by the parent graph ledger.
## Failure-cluster child orchestration

- The `cluster_investigator_children` feature flag and the project
  Investigator policy must both allow expansion. The flag is absent/off by
  default, so upgrades create no child work.
- Clusters are validated and frozen per producing pipeline before planning.
  Membership is non-empty, unique, non-overlapping, UUID-normalized, bounded,
  and immutable for that pipeline.
- The deterministic planner ranks by cluster size and stable identity, starts
  with a maximum fan-out of one, records selected and skipped candidates, and
  allocates calls/tokens/cost/time without exceeding the frozen parent budget.
  Explicit `max_seconds=0` creates no child.
- Child creation, the parent cluster task, and an ID-only dispatch outbox are
  committed together under a project row lock. Dispatch re-loads and verifies
  the immutable feature/policy snapshot stored on the parent pipeline; caller
  state cannot enable work or raise a frozen cap. Stable spawn keys make parent
  replay idempotent. Broker delivery occurs after commit, stale sent rows whose
  child is still queued are redelivered, and duplicate deliveries are rejected
  by the child's atomic queued-to-running claim. Permanent delivery failure
  terminalizes the queued child and its task instead of leaking active capacity.
- Children run on the isolated `agent_children` queue (concurrency one in
  the homelab manifest). The parent uses a bounded join; timeout terminalizes
  queued children and their outbox rows, requests cooperative cancellation of
  running children, and produces an explicit degraded result.
- A child re-resolves project, run, producing pipeline, cluster row, member
  tests, and verified evidence server-side. Redis receives only the
  investigation UUID. Stale/foreign/non-failing membership fails before any
  model reservation, and all external evidence/context is recursively redacted
  and bounded again immediately before each LLM prompt is formatted.
- Joined child results are sanitized, hashed into the canonical evidence
  bundle and decision report, compared by the terminal critic, and disclosed
  as missing/degraded when selected work does not complete.

## Same-pipeline resume and attempt idempotency

- Failed or partial parent pipelines can be resumed with the existing pipeline
  identity through `app.worker.tasks.resume_agent_pipeline` (or
  `app.agents.workflow.resume_pipeline`). A `SELECT ... FOR UPDATE` claim allows
  only one terminal-to-running transition; duplicate deliveries are rejected
  before graph execution.
- The stored `initial_workflow_plan`, cluster-child settings, and analysis-mode
  resolution are required authority. Resume never rebuilds a plan or routing
  mode from current flags, budgets, Redis, or environment. The test-run
  project is re-resolved from PostgreSQL before the claim is committed.
- Completed stage rows remain immutable and are replayed only when their
  checkpoint body, attempt number, runtime-version snapshot, and deterministic
  idempotency receipt validate. Non-completed rows are reset to `pending` with
  an incremented `attempt`; each successful checkpoint persists a new receipt.
- Pipeline-bound stages (`failure_clustering`, child dispatch/join,
  `release_risk`, decision report, and critic) are never imported from a
  different pipeline. They execute under the same pipeline identity on resume,
  preserving evidence and publication authority.
- Resume is intentionally non-retrying at the Celery layer. A failed claim,
  malformed plan, missing authority, or already-running/terminal pipeline fails
  closed and does not issue another model call.
## Child investigation resume and reconciliation

- A failed or cancelled failure-cluster child can be requeued with the stable investigation UUID through `resume_agent_child_investigation` or `resume_investigation`. The operation locks the investigation and its child pipeline before changing state.
- Completed hypothesis stages and their durable reservation tombstones remain authoritative. Incomplete stages are reset to `pending`, increment their attempt, and clear only transient execution receipts; no completed provider reservation is replayed.
- The child re-resolves its run, project, cluster membership, and evidence bundle on the resumed attempt. A changed or missing member fails closed before model invocation.
- Child resume is non-retrying at Celery and remains bounded by the existing ledger, outbox, and stale-reaper controls. A pending reservation or a non-terminal child is not resumed.

## Immutable DecisionReportV1 persistence

The terminal critic now writes an immutable `DecisionReportV1` document to the
`decision_reports` Mongo collection. Each published document is tenant-, run-,
and pipeline-bound, receives a monotonically increasing per-run
`report_version`, and links to the previous published version through
`supersedes_report_id`. Failed or rejected critic attempts are written to the
separate `decision_report_attempts` collection and never replace the last
verified report. `run_summaries` remains a compatibility projection and exposes
both the active report identity and the latest rejected attempt, so consumers
can distinguish a stale verified report from a newer rejected execution.
Publication is idempotent for terminal retries: an existing published
`(test_run_id, pipeline_run_id)` is returned unchanged. Concurrent critics
allocate versions optimistically against Mongo's unique
`(test_run_id, report_version)` index; a duplicate-key race re-reads the latest
version and retries within a bounded limit, preserving the supersession chain
without mutating either document.
## Provider settlement retry protocol

Stage usage is first persisted as a bounded `pending_settlements` receipt in the
pipeline metadata together with the completed stage. A second transaction then
retries the receipt using the centralized budget ledger. Normal pipeline
finalization retries pending receipts before conservative reconciliation, and
the stale-pipeline reaper retries them again before charging unresolved
reservations. Failed database commits therefore leave an auditable receipt
rather than silently losing observed provider usage; repeated retries are
bounded by the receipt map and never issue another provider call.
## Graph budget settlement recovery

Graph-stage reservations are persisted in the pipeline execution metadata. The
stale pipeline reaper now conservatively reconciles any outstanding graph
receipts while terminalizing a worker-lost or timed-out pipeline, records the
reconciliation timestamp/reason, and commits the pipeline state and budget
ledger together. This prevents a provider call that completed before worker
loss from leaving an active reservation that blocks later stages.
## Contract Agent domain-evidence slice

- `contract_validation` is a project-level feature flag, disabled by default and
  frozen into `AgentPipelineRun.execution_metadata.contract_agent_settings` at
  creation. Same-pipeline resume uses that persisted snapshot rather than a
  current environment value.
- In deep workflows with failed tests and the flag enabled, the planner and
  graph insert `contract_validation` between triage and gap/refinement. Offline
  and live workflows remain unchanged. The stage has a typed
  `ContractAgentOutput` and deterministic `not_enough_evidence` fallback.
- Contract checks require project, test-run, pipeline, and test-case scope.
  REST payload lookups are server-bound to that tuple; missing or legacy scope
  never falls back to a global test-case query. Findings are bounded, sanitized,
  structural references and contain no raw response bodies.
- `contract_findings` is included in the immutable DecisionReport projection,
  RunEvidenceBundle specialist hash, critic referential checks, and report source
  stages. The terminal report discloses failed contract validation as degraded
  only when the enabled specialist could not produce authoritative evidence.
- A deterministic pilot corpus now exercises enabled contribution, disabled
  no-op behavior, authoritative scope forwarding, and redaction of contract
  violation values. This is structural evidence only; live API-contract
  usefulness and latency remain a Phase 4 pilot gate.
## Log Intelligence domain-evidence slice

- `log_intelligence` is a project-level feature flag, disabled by default and frozen in `AgentPipelineRun.execution_metadata.log_intelligence_settings` for replay.
- Deep plans include the typed capability and hash the enabled/disabled selection. The graph runs the bounded Log/trace specialist after contract validation and preserves deterministic `not_enough_evidence`/`failed` output when the flag is off or evidence is unavailable.
- The specialist sanitizes service, timestamp, correlation, related-service values, tool payloads, summaries, references, and exception categories before contract validation. Splunk/trace tools short-circuit on `AI_OFFLINE_MODE`, reject malformed timestamps, and never log raw exception text.
- `log_findings` is included in the immutable DecisionReport projection, specialist evidence hash, critic comparison/repair checks, and source-stage attribution. Tampering changes the evidence digest and enabled failures are disclosed as degraded.
- The slice is intentionally default-off and bounded to five deterministic
  failure-cluster representatives; a sanitized golden/evaluation corpus and
  environment-backed tool measurement remain prerequisites for pilot enablement.
## RegressionWatchman domain-evidence slice

- `regression_watchman` is a project-level feature flag, disabled by default and frozen in `AgentPipelineRun.execution_metadata.regression_watchman_settings` for replay.
- Deep planning and graph execution place the specialist after Log Intelligence and before gap/refinement. It receives the existing failure clusters and baseline history, and its typed `regression_classification` output is deterministic when offline or when no baseline evidence exists.
- LLM refinement is redacted, bounded, and explicitly skipped under `AI_OFFLINE_MODE`; exception paths persist only sanitized error categories.
- Regression classifications are included in the immutable DecisionReport projection, critic comparisons/repairs, source-stage attribution, and RunEvidenceBundle specialist hash. Enabled failures with no classification are disclosed as degraded.
- A sanitized deterministic pilot corpus now covers new-regression, flaky-recurrence, environmental-anomaly, low-history, disabled-mode, and privacy paths. The slice remains default-off until live historical-provider quality/latency measurement and persisted cluster-snapshot correlation are approved.
## Remaining Phase 3 gates

- The asynchronous report-supersession foundation now exists behind the
  default-off `async_decision_report_supersession` flag. A deep parent freezes
  the flag in `AgentPipelineRun.execution_metadata`; when enabled, the join
  records only a tenant/run/pipeline request in
  `decision_report_supersession_requests` and returns immediately. A bounded
  worker re-resolves terminal child rows and the latest immutable report,
  produces a sanitized deterministic child projection, and publishes a new
  `DecisionReportV1` linked through `supersedes_report_id`. The superseded
  version is never mutated. Because this first slice is not a second critic
  pass, the new version is explicitly `verification.status=degraded` and
  requires human review when child outcomes are degraded. The worker now
  independently replays the prior report hash, tenant/run identity, terminal
  child projection, and supersession hash before publication; failures reject
  the request without publishing. This is a delta validator, not a replacement
  for the original policy critic, so enabling the flag still requires a live
  PostgreSQL/Mongo publication test.
- Real PostgreSQL verification now covers budget row-lock contention, invalid
  settlement preservation, same-pipeline resume claims (including a populated
  failure-cluster child with a completed hypothesis), verified-artifact
  immutability, durable outbox recovery, concurrent dispatch serialization,
  child cancellation, retention cascades, and startup claim failure before any
  provider call, action-ledger idempotency, and report-evaluation-cycle
  persistence. The disposable homelab fixtures are removed after each test;
  the latest run passed all ten integration tests. Live PostgreSQL+Mongo
  supersession publication and idempotent replay are included. The remaining
  broker-loss visibility-timeout drill was completed on the current deployed
  revision; the same task ID was redelivered after the drill timeout and
  acknowledged successfully. The deployed
  Celery child-delivery contract now has a regression for
  late acknowledgements, the isolated queue, and no blind retries. A
  separate live PostgreSQL outbox test now covers pending dispatch, stale-sent
  redelivery, deterministic task identity, terminal-child exclusion, and
  concurrent parent dispatch serialization; Redis persistence across pod
  restart is now verified with AOF/PVC; an active-load drill observed the
  disposable late-acked messages retained in Redis ``unacked`` across the
  restart. Celery now explicitly enables
  ``worker_cancel_long_running_tasks_on_connection_loss`` so interrupted
  executions are cancelled for broker redelivery. The deployed revision has
  also passed the opt-in disposable visibility regression, and CI runs that
  same late-ack/redelivery test against its Redis service. A homelab Redis
  pod-restart drill
  now confirms the namespaced AOF marker survives replacement, the base and
  incremental AOF files load, and the active worker fleet returns ready. A
  disposable-authority PostgreSQL
  test also proves that a child startup failure immediately after the durable
  claim is finalized as failed, records the Investigator ledger row, keeps the
  stable child pipeline terminal, and never invokes the graph/provider boundary;
  the same test forces the Mongo audit mirror to fail and confirms the
  PostgreSQL terminal write remains committed with sanitized errors.
- The explicitly injected external-store failure/retry race now passes: a
  forced first Mongo publication failure leaves the PostgreSQL request
  retryable, the next attempt publishes one immutable version, and a replay is
  skipped without creating a duplicate. The live
  PostgreSQL+Mongo supersession publication/idempotent replay test has now
  passed in the deployed backend pod.
  Remaining pre-pilot work is governance and evaluation evidence, not an
  untested child claim, outbox, cancellation, retention, or artifact-authority
  path.

## Homelab deployment verification (2026-08-13 UTC)

- Immutable build tag: `build-20260813-033346`.
- Secret-free build-context SHA-256: `ebcaa5a39732ca96b872842c4606504a07c981903d925ef102a0ec4f65c26baa`.
- Resolved backend digest: `sha256:04b93d29eac54ba4c3a555886c08e7cadf72c238c95f9cc348f05d5ae4b42076`.
- Resolved frontend digest: `sha256:d25aa538d0d3e3da52986bc3672418e51c0263a67687cf3b2d5d7619022217c3`.
- Resolved MCP digest: `sha256:b0ca5b695aa7b5763b966b2373b8b1b5f0f498270b0130cb6c6d67b3403a6383`.
- Kubernetes state: all 18 TestLookup pods Ready (15 deployments; backend,
  critical, and ingestion workers use 2 replicas). Both backend pods resolved the same
  digest and source-module hash.
- Database schema: Alembic `0128 (head)`.
- Live probe: in-pod `/health/live` returned `status=alive`.
- Runtime API: the deployed OpenAPI document contains
  `/api/v1/agents/pipelines/{pipeline_id}/agentic-runtime`.
- Retention authority smoke: a temporary published Mongo report with a nested
  artifact reference was protected by the deployed retention helper while a
  live PostgreSQL `SELECT 1` succeeded; the temporary report was deleted.
- Report publication smoke: publishing the same pipeline twice returned one
  immutable report ID and left one published document.
- The homelab Kustomize overlay remains declarative; no temporary ConfigMap or
  builder/PVC resources remain in the namespace.

The deploy script accepts Docker or Podman through `CONTAINER_ENGINE`, keeps
the Kubernetes TLS compatibility override explicit and process-local, and
fails closed when registry or Ready-pod image authority cannot be proven.

The current worktree privacy, report-version, and pilot-readiness revision is
included in the immutable build above. It was built with a disposable
in-cluster Kaniko job after the source archive checksum was verified, then
rolled out only after all three registry manifest digests matched the Ready
pod image IDs. Temporary builder jobs, namespace, source archive, and build
context were removed after rollout.

## Change/Ownership domain-evidence slice

The deep workflow includes a default-off `change_ownership` specialist after
RegressionWatchman. When the project flag `AIQ_CHANGE_OWNERSHIP_ENABLED` is
enabled, the stage re-resolves the TestRun by `(project_id, run_id)`, compares
it with the latest passing baseline, and resolves ownership only for
PostgreSQL-authoritative FAILED/BROKEN cluster members from that same run.

The result is deterministic and typed as `ChangeOwnershipAgentOutput`; it
contains bounded baseline deltas and structural ownership resolutions only.
No LLM call is made, raw errors are reduced to exception types, and no caller
supplied cluster/evidence payload is trusted. The result is included in the
signed decision projection, evidence bundle specialist hashes, and terminal
critic checks. The flag is frozen in pipeline execution metadata so retries
cannot silently change the analysis authority.

This slice is a foundation for later change-correlation enrichment (commit
ownership, review context, and pilot evaluation); external SCM retrieval and
report-level utility metrics remain deferred until the corresponding privacy
and evaluation gates are approved.
## Report-grounded chat sessions (Phase 3)

A chat session may pin an immutable published `DecisionReportV1` using `active_test_run_id`, `active_report_id`, and `active_report_version` on `ChatSession`. Session creation verifies project membership, run ownership, and an exact published Mongo document (`project_id`, `test_run_id`, `report_id`, and version). The binding is persisted by migration `0122` and is returned by the chat-session API.

ConversationAgent treats the session binding as authoritative: it loads only that report version, sanitizes the bounded decision projection before prompt assembly, and emits a `decision_report` source containing the report identity and version. If the document is unavailable or rejected, the prompt receives an explicit unavailable marker and does not substitute the latest singleton run summary. Per-message `project_id` overrides are rejected when they conflict with the session project. This preserves report-version traceability for chat answers while allowing older unbound sessions to retain their existing recent-run behavior.

## Phase 5 typed claims and action proposals (2026-08-12)

The terminal decision report now exposes a bounded, typed claim projection. Each `DecisionClaimV1` is explicitly classified as a `fact`, `inference`, `unknown`, or `recommendation` and carries a confidence score, confidence basis, source stage, evidence references, and counter-evidence. Claims are deterministic projections of the signed run evidence and release/quality outputs; they are included in the canonical decision hash and therefore cannot be changed without critic detection.

`ProposedActionV1` is an approval-aware recommendation, not an execution receipt. It includes owner, rationale, risk, required permission, evidence, and a deterministic idempotency key. The UI separates claim categories and displays action governance metadata. No external mutation is performed by this projection; execution remains behind the existing release/action approval boundary.

The fields are optional in the frontend reader for compatibility with older published reports. New backend reports always emit bounded arrays, and missing/failed specialist context produces an explicit unknown claim rather than an inferred fact.

## Homelab deployment record — typed claims/actions

- Build tag: `build-20260812-084146`
- Backend/frontend images: deployed and Ready (backend 2/2, frontend 1/1)
- Alembic: `0122 (head)`
- Typed backend imports: `typed-claims-imports-ok`
- Health: `http://192.168.0.201/health/live` with Host `testlookup.local` returned `status=alive`
- Child worker and all application workloads: Ready
- Deployment script completed with frontend digest verification

## Claim-level evidence inspection (2026-08-12)

Typed claims now carry a bounded projection of the signed evidence authority: the decision evidence hash, up to five authorized artifact references (artifact/evidence ID, source, kind, checksum, scope, sensitivity, freshness, and sanitized excerpt), and the run metric definition reference. The report hash remains visible in the drawer so the user can distinguish the evidence context from a newer or stale report.

The frontend opens these references in a modal drawer with separate evidence and counter-evidence sections, confidence basis, source stage, and report fingerprint. It does not fetch unbound IDs or expose raw URI/body fields; older reports without claim arrays remain compatible and show no fabricated evidence. This is an inspection projection only; artifact mutation and external actions remain behind existing authorization boundaries.

## Homelab deployment record — claim evidence drawer

- Build tag: `build-20260812-085810`
- Backend/frontend images: deployed and Ready (backend 2/2, frontend 1/1)
- Alembic: `0122 (head)`
- Typed imports: `claim-drawer-imports-ok`
- Health: `http://192.168.0.201/health/live` returned `status=alive`
- Child worker and application workloads: Ready
- Frontend image digest verification completed by the deployment script

## Homelab deployment record — report-preserving claim evidence drawer

- Build tag: `build-20260812-090708`
- Backend/frontend images: deployed and Ready (backend 2/2, frontend 1/1)
- Alembic: `0122 (head)`
- Typed imports: `claim-drawer-imports-ok`
- Health: `http://192.168.0.201/health/live` returned `status=alive`
- Child worker and application workloads: Ready
- Frontend image digest verification completed by the deployment script

## Phase 5 report-scoped structured feedback (2026-08-12)

`DecisionReportFeedback` is a separate immutable SQL audit record from legacy
analysis feedback. A reviewer can rate report utility (`useful`,
`partially_useful`, `not_useful`) or correct a typed claim (`category`, `cause`,
`flaky`, `release`). The API resolves the exact published report by
`project_id + test_run_id + report_id + report_version`, derives the evidence
fingerprint from the durable report, and accepts only evidence IDs already
bound to that claim. Raw excerpts, URIs, and caller-supplied claim text are not
persisted in the feedback row.

The endpoint is run-membership guarded and supports idempotency keys. Migration
`0123` adds the tenant/run/report/version/hash fields, bounded correction
metadata, vocabulary checks, report indexes, and a unique user/idempotency
constraint. The Run Intelligence UI exposes utility buttons and a claim
correction dialog only for a currently verified report; corrections require a
reason and bound evidence selection. Existing analysis feedback remains
backward compatible and continues to feed the training loop.

## Homelab deployment record — report-scoped feedback

- Build tag: `build-20260812-143108`
- Backend/frontend images: deployed and Ready (backend 2/2, frontend 1/1)
- Alembic: `0123 (head)`
- Backend imports: `report-feedback-imports-ok`
- API route: `/api/v1/runs/{run_id}/decision-reports/{report_id}/feedback` registered
- Health: `http://192.168.0.201/health/live` returned `status=alive`
- Child worker and application workloads: Ready
- Frontend image digest verification completed by the deployment script

## Phase 3 cluster-child orchestration slice (2026-08-12)

The deep workflow now has a bounded, default-off cluster-child path. Failure
clusters are re-resolved against the authoritative run/project and FAILED or
BROKEN test members, ranked deterministically, and expanded into a hashed plan.
The parent transaction creates/reuses child investigations, durable task rows,
and UUID-only dispatch outbox messages. Child delivery is bounded by project,
daily, member, time, call, token, and cost budgets. Spawn keys and Celery task
IDs are deterministic, parent settings are frozen, and replay cannot create a
second child or spend outside the parent plan.

The outbox relays pending and stale-sent rows to the dedicated `agent_children`
queue, recovers publish loss while a child is still queued, and terminalizes
permanent delivery failure. Join timeout, parent cancellation, stale-claim
reaping, and relay exhaustion reconcile investigation, parent task, and outbox
state atomically. Scoped Investigator evidence fails closed on membership
mismatch, and prompts/errors are sanitized before model or durable boundaries.
The `/api/v1/agents/pipelines/{pipeline_id}/agentic-runtime` projection exposes
the recursive parent/child task tree with bounded findings/evidence metadata,
attempts, budgets, stop reasons, and truncation status.

Validation for this slice: the focused cluster-child and privacy/log regression
sets pass (56 cluster-child tests plus the broader Log/report/privacy set);
Ruff, Python compilation,
offline migration checks, and diff checks passed. Review report:
`docs/reviews/phase3-cluster-child-runtime/00-action-plan.md`.

The child feature remains shadow/dark-gated pending project policy, pilot, and
the remaining real-PostgreSQL cancellation/retention race approval. SQL outbox dispatch, stale-sent
  redelivery, deterministic task identity, terminal-child exclusion, Redis
  pod-restart persistence, and concurrent queued/running child cancellation
  plus retention cascade cleanup are covered by live checks. A
populated child-resume claim was also exercised against the homelab PostgreSQL
service using a disposable authoritative cluster fixture; the fixture was
removed after verification. A homelab rollout was verified on
2026-08-12 using the overlay build documented in
`docs/deployment/homelab-phase6-report-eval-20260812.md`; all backend workers,
beat, API, and frontend pods were Ready, the child worker imported the runtime,
and `/health/live` returned `alive`. The deployment still does not enable the
feature flag or substitute for live PostgreSQL contention/rollback tests.

Current Phase 3 iteration validation: the source-focused Phase 3 regression set
passes; three live
PostgreSQL child-runtime integration tests passed in the homelab (outbox,
concurrent cancellation, and retention cascade); Redis AOF/PVC restart
persistence passed; Ruff, compilation, and diff checks passed.

## Report-level evaluation gate (Phase 6 foundation, 2026-08-13)

`decision_report_eval_service` now evaluates the immutable terminal projection
after critic repair and before publication. It measures material-claim citation
validity and groundedness against the signed snapshot evidence IDs, reports
contradiction rate, and fails closed on explicit release-policy contradictions.
Calibration is connected to the existing local `AgentEvalSample` harness when a
representative corpus is supplied; report utility is measured when structured
feedback counts are supplied. Missing corpus or feedback is recorded as
`not_evaluated`, never as a false pass. A sanitized five-report structural
corpus now exercises grounded valid citations, calibration, utility, and hard
rejection for invalid citations and policy contradictions; it is not a
substitute for approved pilot labels.

The evaluator result is embedded in `DecisionReportV1.verification` under
`report_evaluation` and is included in the critic check set. Invalid citations
or policy contradictions reject publication; threshold warnings remain visible
for shadow/canary analysis. The implementation and review are recorded in
`docs/reviews/phase6-report-eval/00-action-plan.md`. GA still requires an
approved representative corpus, two consecutive passing cycles, pilot utility
evidence, and provider/memory/action-governance completion. Authoritative
report-cycle evaluation now aggregates only persisted utility feedback scoped
to the authorized project and requested runs; claim corrections are excluded
from the utility denominator.

## Centralized LLM provider/privacy boundary (2026-08-12)

`llm_policy_service` is now the shared construction and invocation policy for
chat and embedding providers. It publishes local/remote capability profiles,
preserves `AI_OFFLINE_MODE` as a hard egress ceiling, supports an explicit
provider allowlist (`AI_LLM_PROVIDER_ALLOWLIST`), and validates configured
HTTP(S) endpoint origins (`AI_LLM_ALLOWED_BASE_URLS`). `BudgetedLLM` sanitizes
plain strings, nested payloads, LangChain messages, and prompt-value objects at
the actual invocation boundary, then records only provider/model/redaction
counts in bounded context and logs. No prompt or secret is included in the
audit event.

The boundary is backward-compatible with the existing offline default. Remote
provider enablement still requires deployment policy configuration and remains
subject to the broader provider-residency, durable audit, and pilot governance
gates.

Each completed agent stage now copies the bounded `llm_privacy_events` context
into `AgentPipelineRun.execution_metadata.provider_policy_audit`. The durable
projection is limited to the last 100 sanitized provider/model/redaction-count
events; invocation prompts, payloads, credentials, and arbitrary event fields
are rejected before persistence. This provides a report/pipeline audit seam
without turning execution metadata into a prompt store. Remote-provider
allowlist and residency approvals remain deployment gates.

## Memory provenance and lifecycle (2026-08-12)

`AgentMemoryEntry` now carries explicit `source_type`, `trust_level`,
`lifecycle_status`, `source_snapshot_id`, `source_hash`, `expires_at`, and
supersession metadata. All recall, timeline, snapshot, and replay reads are
tenant-scoped and fail closed to active, non-expired rows. New payloads are
sanitized before PostgreSQL or Chroma persistence, and provenance values are
validated against the bounded contract.

Retention keeps expired rows as an audit trail, marks them `expired`, and
removes matching Chroma vectors. Supersession is an explicit same-tenant
transition rather than an overwrite, so report replay cannot silently consume
stale or replaced memory. Migration `0124` adds the schema and reversible
constraints/indexes. Focused lifecycle, migration, retention, and legacy
memory tests pass; the two Celery sweep tests remain environment-dependent
when the optional local Celery package is unavailable.

## Planner-driven stage execution (2026-08-12)

The compiled LangGraph topology remains stable for checkpoint compatibility,
but stage invocation is now governed by the immutable `initial_workflow_plan`.
An explicit `planned=false` stage is durably recorded as a conditional skip
with `stop_reason=planner_not_selected` and is never sent to its agent. Missing
stage metadata in legacy plans remains executable for compatibility. This
separates graph shape from capability selection while preserving checkpoint,
parent/child, and timeline contracts.

## Typed action proposal and execution ledger (2026-08-12)

DecisionReport `proposed_actions` are now materialized as tenant-scoped
`AgentActionLedger` rows during pipeline terminalization. The ledger stores the
sanitized request projection and hash, required permission, approval state,
idempotency key, execution result, failure code, and rollback payload. A row
cannot move from review to execution without an explicit guarded transition,
and reusing an idempotency key with a different payload fails closed. This is
proposal-only: external Jira/release mutations still require their existing
approval and integration boundaries.

The action-ledger rollout is documented in
`docs/deployment/homelab-agent-action-ledger-20260812.md`; migration `0125`
introduced the ledger and the current homelab head is `0128`. Generic approval API wiring, external execution
outbox delivery, and live PostgreSQL race tests remain follow-up gates before
enabling autonomous action execution.

Approved action proposals also have a durable `AgentActionDispatchOutbox`
delivery intent (`0126`). It is unique per action/idempotency key, leases
pending or expired work with row locks, and records bounded retry/failure
metadata. This slice stops before any autonomous external executor: an
integration-specific worker must prove idempotent provider calls and rollback
before the outbox can cause a mutation.

Report-level evaluation cycles also aggregate only tenant- and run-authorized
action-ledger statuses. They persist bounded proposal, terminal-resolution, and
unresolved counts; the settings dashboard surfaces the latest proposal count
and resolution rate. Cycles built from caller-supplied corpora intentionally
mark action governance unavailable because those corpora are not authoritative
action ledgers.

The current action-governance revision is deployed to the homelab with immutable
backend/frontend digests and Alembic `0128 (head)`; the exact rollout and
runtime checks are recorded in
`docs/deployment/homelab-phase6-action-governance-20260813.md`.

## Protected PostgreSQL CI gate (2026-08-13)

The CI workflow now includes a separate `postgres-integration` job. It starts a
clean PostgreSQL 16 and MongoDB 7 service, applies the current Alembic head,
seeds disposable project/run/test/cluster authority rows, and runs the Phase 3
claim, budget, outbox, evidence, retention, resume, report-evaluation, and
PostgreSQL+Mongo report-supersession tests with the integration DSN set. The
job is a dependency of image publication, so a skipped or failed protected
database/publication suite cannot be hidden behind a successful build. The
first GitHub execution remains required evidence; the same protected list has
passed against the deployed homelab PostgreSQL/Mongo services, while local
YAML, quality-gate, and router regression checks pass.
