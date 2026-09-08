# M11 live persistence sanitization cutover

M11 changes all live ingestion writers to redact free-form failure evidence,
nested metadata, tags, and legacy extension fields before Redis, MongoDB, or
PostgreSQL persistence. The deployment also needs one maintenance pass for
records written by an older release.

Run this procedure once for every environment after the new backend and worker
images are built and pinned by immutable digest:

1. Pause API traffic, Celery workers, and the live stream consumer. This avoids
   concurrent writes while Redis LIST keys and immutable Stream entries are
   replaced. Keep PostgreSQL, MongoDB, and Redis running.
2. Update the backend, worker, and live-consumer deployment definitions to the
   new image digests while keeping every writer stopped. Verify the digest used
   by the maintenance container matches the stopped deployments; do not resume
   an older image after the scrub.
3. Take encrypted PostgreSQL, MongoDB, Redis, and ChromaDB snapshots under the
   environment's incident-recovery role. Deny application/service-account read
   access, enable access logging, record a deletion date at the end of the
   approved rollback window, and delete the snapshots on that date. These
   snapshots contain the raw evidence being removed and must not enter a normal
   restore, analytics, or developer workflow.
4. From the new backend image and its `/app/backend` working directory, run:

   ```bash
   python -m scripts.sanitize_live_persistence \
     --batch-size 200 \
     --confirm-live-writers-paused
   ```

   The command keyset-pages PostgreSQL archives, live `test_cases`, and MongoDB;
   purges the fully consumed live Stream without replay; preserves DLQ Stream
   IDs; sanitizes and checkpoint-migrates each legacy live-buffer LIST into its
   stable per-run evidence Stream; deletes and boundedly rebuilds
   the complete search index from sanitized SQL (including inactive projects,
   which removes stale/orphaned documents); and deletes the derived per-project AI
   semantic-cache collections. It can be rerun safely after interruption.
   MongoDB documents receive
   `_m11_sanitization_version: 1` for audit visibility. The scrub still checks
   every document because the legacy endpoint allowed clients to forge that
   field before M11.
5. Rerun the command. Every transformation is idempotent; counts report rows
   examined, so a nonzero count does not mean raw evidence was found.
6. Inspect historical rows in `test_runs.event_archive`, `test_cases`,
   `live_execution_events`, `testlookup:stream:dlq`, and
   `testlookup:live:testcases:*`, plus the ChromaDB `test_case_search` search
   collection. Confirm the AI cache collections named `ai_analysis_cache` or
   `ai_analysis_cache_*` were deleted. Confirm that the original email,
   password, token, Basic/Bearer value, IPv4, and IPv6 are absent. Confirm the purged
   `testlookup:stream:live_events` has zero pending entries and reading `>` from
   its consumer group does not deliver acknowledged history. Confirm each
   per-run evidence entry is a sanitized batch manifest whose event IDs match
   its `session_id`, `batch_id`, and zero-based event indexes.
7. Follow [H02/H03 stable live protocol cutover](H02_H03_STABLE_LIVE_CUTOVER.md)
   to drain the migrated evidence Stream and remove legacy LISTs. Do not remove
   a LIST merely because migration completed: the new persistence consumer must
   first report zero lag, zero pending entries, and zero outstanding events in
   the run's batch-state Hash.
8. Resume the new live stream consumer, Celery workers, and API deployment.
9. Submit a new synthetic failed-test canary. Inspect every store listed above,
   including the newly written live Stream entry, and confirm the raw canaries
   are absent before ending the maintenance window.

Do not roll back to a pre-M11 backend or worker image. If recovery requires a
pre-scrub snapshot, keep all writers paused, restore it only under the recovery
role, redeploy the sanitizing images, and rerun this procedure before traffic
resumes.

Do not skip the pause. The command refuses to purge the live Stream unless its
only consumer group has zero lag and zero pending entries. It then recreates the
group at `$`, so acknowledged history cannot be delivered again. The DLQ has no
consumer group and is rebuilt with its original IDs.
