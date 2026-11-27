# Upload publication and recovery

JSON and file uploads reserve a PostgreSQL `upload_dispatch_receipts` row before writing their temporary object or publishing a Celery message. The receipt owns the task UUID, run UUID, object key, content digest and accepted parser metadata. A 202 means the payload is ready and publication returned. Parsing, persistence and finalization still run asynchronously. Poll the receipt for its business outcome.

A 503 containing `Location`, `X-Upload-Task-ID` and `X-Upload-Run-ID` preserves an existing receipt. Publication may have succeeded, finished late, or remained unsubmitted. Keep these identifiers and poll the original status URL. A 503 without these headers rejects admission before reserving a receipt. HTTP retries and Celery `SUCCESS` alone do not establish ingestion completion.

## Client retries

Supply an `Idempotency-Key` for each logical upload. It accepts 1–128 ASCII letters, digits, `_`, `.`, `:`, or `-`, starting with a letter or digit. Keep the same key, file bytes and accepted metadata when repeating the POST after an interrupted response. The server stores a digest of the key, scoped to project, authenticated account and upload route. Reusing it for different content or metadata returns 409. Independent file uploads without a key continue to create separate runs.

A same-key POST reuses the first receipt's task, run, object and parser policy. It can finish an interrupted object write and republish that same task. A terminal receipt returns its existing identity and leaves the queued object deleted. Keep the original generated build label and archive bytes when retrying. Repacking a ZIP can change its bytes.

Receipt replay protection lasts until terminal receipt expiry. Cleaned terminal receipts become eligible for expiry after 30 days. A key reused after its receipt has expired starts a new upload. Unresolved receipts remain available for explicit recovery.

## Inspect and resume

`GET /api/v1/ingest/uploads/{task_id}` uses the receipt's stored project for authorization. Its normal state is `pending → parsing → ingesting → succeeded|failed`. A terminal state is immutable. Additional fields distinguish `payload_state=reserved`, `ready`, or `deleted`. `accepted_at` records receipt reservation, which can precede storage of the bytes. `payload_ready_at` records confirmed payload preparation. `completed_at` records the terminal business status. A ready payload still needs broker delivery and processing.

After an API restart or an unconfirmed publication, an authorized ingestion user can call `POST /api/v1/ingest/uploads/{task_id}/retry`. It verifies the original object's digest and publishes the stored task identity and metadata. It can reconcile an object that was written before the API stopped marking it ready. If another consumer or preparer holds the receipt lock, the endpoint returns the original receipt with 503. A reserved receipt whose object was never written needs the original keyed POST or an operator resolution.

Recovery is explicit. There is no automatic relay of initial uploads in this iteration. A broker outage or failed worker preflight can leave a durable pending receipt until someone resumes it. The per-project allowance admits at most 100 uploads whose temporary payload cleanup is still outstanding, including unresolved work and terminal work awaiting successful deletion. Resolving and cleaning existing uploads restores capacity. Admission uses a short serialized project check and a one-second PostgreSQL statement timeout.

Each API process admits one payload preparer at a time to leave room for its dependent metadata queries in the bounded database pool. A known full slot returns ordinary 503 with `Retry-After: 1` before creating a receipt. A preparation that exceeds its HTTP wait retains its receipt and continues under the same lock until the actual write finishes. The same applies when a request disconnects. A repeated keyed POST skips rewriting an already ready payload.

To inspect stranded custody from an authorized maintenance database session, select only identifiers and lifecycle fields:

```sql
SELECT task_id, run_id, project_id, kind, state, created_at,
       payload_ready_at, completed_at, payload_deleted_at
FROM upload_dispatch_receipts
WHERE completed_at IS NULL OR payload_deleted_at IS NULL
ORDER BY created_at
LIMIT 100;
```

Do not print `task_kwargs` or object content into diagnostic logs. They can contain CI labels and test evidence.

## Terminal cleanup and operator resolution

The worker commits a terminal business status before deleting the temporary queued object. Duplicate broker messages check the receipt before loading a payload and leave completed work intact. A failed delete keeps its locator for the next bounded hourly sweep. `POST /api/v1/ingest/uploads/{task_id}/cleanup` permits an authorized ingestion user to retry a terminal payload deletion. It refuses unresolved work with 409. Cleanup does not delete normalized run data or the file worker's separate raw-report archive.

For a stranded upload whose original bytes cannot be recovered, a privileged operator can invoke the maintenance helper from the normal application environment:

```python
from app.worker.loop_runner import run_async
from app.worker.upload_receipts import abandon_upload, sweep_terminal_uploads

task_id = "<the exact receipt UUID reviewed by the operator>"
run_async(abandon_upload(task_id, confirmation_task_id=task_id))
run_async(sweep_terminal_uploads(limit=20))
```

The exact UUID must be repeated as confirmation. The helper refuses a currently locked consumer or preparer. It records terminal `failed` with `operator_abandoned` and preserves already committed run data. Later messages carrying the receipt identity skip business execution. Maintenance-shell access and its audit trail govern this operator action. There is no public abandonment endpoint.

The sweep clamps its batch to 1–100 receipts. It removes objects only for terminal receipts and clears their no-longer-needed task metadata. It rotates through retained terminal locators, repeating the idempotent delete to catch an object arriving after a lost storage acknowledgement. A finite batch of receipts older than 30 days expires after a fresh confirmed deletion. Project hard deletion outside the normal soft-delete API retains receipt custody because this table has no project cascade. Workers refuse missing or inactive projects. Project reset refuses unresolved receipts so an old accepted upload cannot recreate a reset run.

The default hourly batch is 20. Expiry can lag its eligibility time when the backlog exceeds that cadence. Operators can run additional bounded sweeps and should size their maintenance cadence against upload volume. The SQL inspection above exposes unresolved custody and pending deletion without printing report metadata.

## Deployment and limits

Apply migration 0265 first. Upgrade and restart ingestion workers before enabling the new API producer. New messages contain optional `upload_receipt_id`, which older workers do not accept. Existing messages without that field continue through the legacy task signatures and best-effort Redis status path. Legacy receipts are not backfilled.

Use a direct PostgreSQL connection or a session-pooling proxy. Transaction pooling does not support the session advisory lock used here. The supported worker topology uses one synchronous task per Celery child. Each active upload reserves one connection from that child's existing bounded pool while processing uses another. Shipped production worker pools provide one base connection plus one overflow connection. Custom pool limits must allow at least two simultaneous connections per ingestion child. This reservation stays within the configured fleet maximum and keeps PostgreSQL out of an idle transaction. Unlock failures invalidate the physical connection before returning it to the pool.

The lock excludes concurrent consumers while its PostgreSQL session remains connected. Process or connection loss releases it. The existing ingestion pipeline resumes the same run and upserts its test identities after a partially committed attempt. Downstream finalization uses its own durable outbox. These stores do not form one atomic transaction. Loss of the lock session during a partially committed attempt relies on those idempotent persistence boundaries and explicit recovery. No universal exactly-once side-effect guarantee is claimed.

Unit and disposable PostgreSQL tests cover receipt identity, late publication, retries, authorization, bounded admission, session lock release, terminal-write failure, cleanup and retained custody. The separate disposable release-stack proof covers candidate processing and durable readback. Production rollout and production fault injection remain operator acceptance work.
