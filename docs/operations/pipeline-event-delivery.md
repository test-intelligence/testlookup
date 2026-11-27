# Recover shared pipeline-event delivery

Pipeline event delivery uses a shared SQL intent and an append-only Mongo
materialization. This is delivery of audit/timeline evidence, not a request
to run the pipeline's tools, model calls or business mutations again.
The source's SQL acceptance/transaction contract determines whether an event
is accepted; an event refused before that boundary is not recoverable from
this outbox. A source retry needs its original stable event ID to refer to
the same logical event. A new legitimate decision/attempt uses a new ID.

## Acceptance and rollout

Migration 0263 adds the scoped SQL outbox. Stage and pipeline completion
events and AgentRun ledger mirrors share the source transaction. A rollback
removes the still-uncommitted intent. Observations such as a decision or cache
hit use a separate short transaction after resolving a durable project/run
scope. The timeline can read accepted evidence immediately and deduplicates it
with materialized history by event ID.

A history archive requires delivered events for its selected canonical
pipelines. The export fails before publishing if those events are
pending, leased or failed. Once delivery is confirmed, request the export
again.

Admission reserves space in a transaction and caps the installation at 10,000
retained intents. It sanitizes event detail and replaces oversized or invalid
detail with a compact omission marker. Each serialized envelope is limited
to 16 KiB. Refusals from contention, capacity, invalid scope or PostgreSQL
unavailability produce a bounded metric reason and an operational event ID.
Business processing can continue when telemetry admission is refused.

Apply the migration before enabling workers with the new relay. For an
application rollback, leave the table in place while accepted events drain.
Migration downgrade refuses to remove a table with pending, leased or failed
intents. Back up the accepted queue before a maintenance operation that
replaces the database.

## Observe the shared state

`GET /api/v1/agents/event-log/health` requires QA lead privileges and scopes
results to the caller's accessible projects. Its optional `project_id` limits
the view further; an inaccessible project is refused. An instance admin may
read the installation view. The response reads SQL, so independent API
processes observe worker-written backlog rather than local process globals.
It returns counts, oldest age, status counts and up to 20 compact failed-event
references; it never returns event detail or connection/provider error text.

The legacy `write_failure_count` is now the number of currently unresolved
intents with a recorded delivery error. It is not a monotonic lifetime total.
`dead_letter_count` means terminal failed receipts, `dead_letter_limit` is the
installation admission capacity, and `recent_dead_letters` contains safe
references rather than payloads. Delivered receipt pruning can reduce these
counts. A new, briefly pending intent is normal; unresolved failures or a
backlog older than five minutes make health degraded.

The collector `pipeline_events` publishes these absolute shared gauges:

| Signal | Meaning |
|---|---|
| `testlookup_pipeline_event_outbox_entries{status}` | Retained pending/sending/delivered/failed rows |
| `testlookup_pipeline_event_outbox_oldest_age_seconds{status}` | Oldest unresolved intent age; delivered is zero |
| `testlookup_pipeline_event_outbox_retry_overdue_seconds{status}` | Due retry or expired lease age |
| `testlookup_pipeline_event_outbox_capacity` | Installation row limit, including retained acknowledgements |

Read them only with successful, fresh collection. SQL failure or malformed
lease state must not appear as measured healthy zero. API replicas observe
the same table: fleet queries take a fresh maximum rather than adding those
copies. There are no project, event, task or payload labels.

`TestLookupPipelineEventDeliveryFailed` identifies terminal unresolved
receipts; `TestLookupPipelineEventBacklogStale` identifies a relay falling
behind; `TestLookupPipelineEventCapacityHigh` warns before admission fills.
`TestLookupPipelineEventAdmissionRefused` uses the separate source counter
`testlookup_pipeline_event_admission_failures_total{reason}`. Admission
refusal is a distinct evidence gap and cannot be repaired by replaying
accepted rows. Counters still depend on their producing process being
scraped; SQL-backed backlog is the durable shared delivery state.

## Automatic materialization and replacement

The scheduled relay claims one row at a time, with a two-minute lease and at
most eight automatic attempts. One invocation starts at most 50 deliveries
within a 20-second loop budget; an in-progress database/driver operation has
its own timeout and can extend the last iteration. Worker/task hard limits
remain an independent deployment bound. No lease is taken for an entire
unprocessed batch.

Before writing Mongo, the worker takes the project's shared history lock,
then re-reads/locks the still-owned intent. Purge takes the corresponding
exclusive project lock and removes eligible SQL intents before Mongo
deletion. This ordering prevents an ordinary concurrent replay from
restoring deliberately purged history. A stale/deleted/replaced claim does
not write Mongo. Driver cancellation, process termination and ambiguous
remote completion still require the selected purge/reconciliation policy;
this is not a distributed transaction or a claim of atomic cross-store erase.

Mongo uses the stable event ID as `_id`. Inserts require journalled,
acknowledged writes under a two-second client-side operation deadline. The
default minimum is `w=1`, `j=true`; a site's stronger majority/custom write
concern is preserved, and duplicate confirmation reads the primary even if
the URI otherwise prefers secondaries. A duplicate key is confirmed by comparing the immutable logical
payload and project/run identity, rather than accepted blindly or overwritten.
BSON timestamp precision is normalized. If a source explicitly reuses its
event ID after the SQL receipt expired, only its new intake timestamp is
excluded from identity comparison; the original Mongo timestamp is preserved.
A different payload under that ID becomes a terminal identity conflict.

Transient errors back off from two seconds to a five-minute maximum. Failure
codes are a small fixed vocabulary; exceptions, URLs and payloads are not
returned in health or used as metric labels. Exhausted attempts and invalid
identity/payload retain a `failed` intent. A failed SQL receipt can coexist
with already accepted Mongo evidence after an ambiguous write; it indicates
unresolved delivery accounting, not proof that the logical event was lost.

Delivered SQL receipts normally remain for one hour. At 90% admission
pressure, a bounded prune may remove newer **acknowledged** receipts to
restore room promptly after a Mongo outage. It never removes pending,
leased or failed evidence. Mongo retention, backup and erasure remain their
own policy; deleting a receipt does not delete the materialized event.

## Explicit repair

Restore the default-queue consumer, relay schedule, SQL/Mongo access and
storage first. Inspect the selected project's failed event references. For
one eligible terminal failure, call:

```http
POST /api/v1/agents/event-log/{event_id}/replay?project_id={project_id}
```

The route requires QA lead privileges and project access, resets only that
failed intent to pending and records a project-activity outcome with the
actor and stable reference. It sends no notification and performs no tool or
pipeline action. The caller's transaction commits the requeue and project
activity through the existing activity writer; that derived ledger is not a
tamper-proof compliance history. Delivery occurs through the scheduled
relay. Repeating a request after it is pending/sending/delivered returns 409;
an event outside the selected project is not returned. Payload replacement,
arbitrary task names and bulk unscoped replay are unsupported.

After recovery, verify the row is delivered (or aged out after
acknowledgement), the timeline contains one logical `event_id`, and fresh
backlog/error gauges recover. For an identity conflict, investigate the
source contract; repeatedly requeueing does not authorize overwriting an
immutable event. Never delete unresolved rows to quiet a capacity alert.

## Verification scope

`backend/tests/regression/test_pipeline_event_delivery.py` exercises actual
isolated SQL transactions and a synthetic Mongo sink: outage/recovery,
accepted-before-cancellation replacement, duplicate identity validation,
deleted/stale leases, purge contention, retry ceilings, pressure pruning,
timestamp-preserving source retry, scoped independent-session health, role/
project refusals and persisted project activity. Prometheus semantic fixtures
in `infra/monitoring/tests/pipeline-event-delivery.yml` check alert/recovery,
stale replicas and replicated capacity counts. SQLite does not prove
PostgreSQL advisory/row locking or real Mongo journal/write acknowledgement;
those require the separate disposable-store and independent-process witness.

Driver deadline semantics follow the official
[Motor/PyMongo client-side operation timeout guidance](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-options/csot/).
