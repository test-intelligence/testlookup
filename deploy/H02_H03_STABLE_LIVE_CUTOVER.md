# H02/H03 stable live protocol cutover

The stable live protocol stores one sanitized manifest per accepted HTTP batch
in a per-session Redis Stream (`testlookup:live:evidence:{live_session_uuid}`).
Each manifest carries `events_json`, `event_ids_json`, count, digest, and the
stable client batch ID. The `live-persistence-v1` consumer acknowledges and
deletes that exact entry only after its idempotent PostgreSQL projection and
watermark commit. Writers never trim evidence and consumers never range-trim.
A TTL-less per-session Hash is the active-run batch ledger. Stable event IDs are
held in pending, outcome, and received Sets, so capacity and terminal aggregates
are exact `SCARD` values and interrupted retries remain idempotent. Close fences
new batches; the ledger and Sets receive a 15-day TTL only after the pending Set
is empty.

## Redis durability prerequisite

Do not start this cutover unless Redis persists `/data` and uses an eviction
policy that cannot evict TTL-less evidence Streams:

- Compose: the `redis_data` volume, AOF, `appendfsync everysec`, and
  `volatile-lru` are configured in `docker-compose.yml`. Back up the named
  volume before the maintenance window.
- Homelab: `k8s/overlays/homelab/infra-redis.yaml` uses a 5 Gi `local-path`
  PVC, AOF/everysec, and `volatile-lru`. Verify the PVC is `Bound` and run the
  existing restart sentinel drill before production traffic.
- Air-gapped OpenShift: `k8s/overlays/openshift-artifactory/infra-redis.yaml`
  uses `STORAGE_CLASS_PLACEHOLDER` for a 5 Gi PVC, AOF/everysec, and
  `volatile-lru`. The deploy script must replace the placeholder with a CSI
  class that survives pod replacement.
- Standard OpenShift: this overlay intentionally supplies no Redis workload.
  The cluster operator must provide the `testlookup-redis` Service endpoint and
  configure `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` for an
  authenticated, TLS-protected Redis service. That service must enable durable
  append-only persistence (or an equivalent managed multi-AZ durability
  guarantee), backups, point-in-time recovery, and `noeviction` or a policy
  that cannot evict TTL-less evidence/broker keys. A disposable cache endpoint
  does not satisfy this contract. The current atomic admission script touches
  several keys in one Lua call, so use a non-clustered endpoint; Redis Cluster
  is unsupported until all related keys use one explicit hash slot.

Record these preflight results in the change ticket:

```bash
redis-cli "$REDIS_URL" CONFIG GET appendonly appendfsync maxmemory-policy
redis-cli "$REDIS_URL" INFO persistence
kubectl -n testlookup get pvc redis-data
```

For a managed Redis service where `CONFIG GET` is restricted, attach the
provider configuration showing persistence, failover, backup, and eviction
settings.

## Expand/migrate/contract order

1. Back up PostgreSQL and Redis. Pause the old API producers, periodic drainer,
   close-session jobs, and reaper. Leave Redis and PostgreSQL available.
2. Apply the new consumer/drainer image first, with its replicas still at zero
   and producers still paused. Confirm the image understands both the per-run
   evidence Stream and the legacy LIST fallback, uses stable `event_id` values,
   and creates the `live-persistence-v1` group. Do not let the fallback drainer
   consume or delete a legacy LIST before step 3 migrates it.
3. From the same new image, migrate all frozen legacy LISTs:

   ```bash
   cd /app/backend
   python -m scripts.sanitize_live_persistence \
     --batch-size 200 \
     --confirm-live-writers-paused \
     --migrate-legacy-live-lists-only
   ```

   The command sanitizes each payload with the M11 boundary, derives one stable
   legacy batch ID from each canonical sanitized page, appends one batch
   manifest with deterministic event IDs, records its accepted dedupe receipt
   and outstanding count, and atomically advances a per-run checkpoint.
   It keeps each source LIST until the new consumer has persisted its Stream.
   Rerun the command after interruption; committed pages are not duplicated.
4. Start only the new persistence consumers. Keep all producers paused. Wait
   until every `testlookup:live:evidence:*` Stream reports group
   `live-persistence-v1` with `pending=0` and `lag=0`. Confirm PostgreSQL
   contains each migrated `event_id` once and each run watermark covers the
   final migrated event.
5. Remove the legacy LISTs through the guarded cleanup mode:

   ```bash
   cd /app/backend
   python -m scripts.sanitize_live_persistence \
     --batch-size 200 \
     --confirm-live-writers-paused \
     --cleanup-drained-legacy-lists
   ```

   Cleanup fails closed when the checkpoint is incomplete, the group is
   missing, lag is unknown/nonzero, pending entries remain, outstanding evidence
   is nonzero, or Redis changes a watched key. Resolve the condition and rerun;
   do not delete keys manually.
6. Deploy and resume the new producers. During the compatibility release they
   write the evidence Stream and best-effort websocket fan-out. New producers
   no longer append the legacy LIST. Keep the new consumer deployed before every
   producer rollout.
7. Submit a canary batch, kill a consumer after its SQL commit and before Redis
   acknowledgement, then allow immediate `XAUTOCLAIM` reclaim/replay. Verify
   PostgreSQL converges to
   one row per stable event, the watermark advances, and no accepted event is
   missing. Restart Redis and repeat a canary to prove AOF/PVC recovery.

Remove legacy LIST read/write compatibility only in a later release after all
environments have completed this procedure and telemetry shows no fallback
reads for the full rollback window. Never roll producers back to a LIST-only
image after legacy LIST cleanup.
