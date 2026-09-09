# Changing the ingestion shard count

TestLookup routes a project with the first four bytes of its MD5 digest modulo
`LIVE_INGEST_SHARD_COUNT`. The mapping is stable only while the count stays
fixed. For uniform hashes, changing 8 shards to 9 preserves about 1/9 of the
mappings and moves about 8/9. This is ordinary modulo routing, not consistent
hashing. Sharding provides deterministic queue locality; Celery prefork workers
can still execute tasks for one project concurrently.

Changing the count is a maintenance-window operation. A mixed producer fleet
can route the same project to old and new queues, and a queued outbox row keeps
the queue selected when it was staged. Use the procedure below for both forward
changes and rollback.

## Prepare the release

1. Record the old and new counts. Render the target manifests and set every
   producer's `LIVE_INGEST_SHARD_COUNT` to the new value. In this repository the
   setting must agree in `k8s/base/configmap.yaml`, or in both the `backend` and
   `worker` services of the applicable Compose file.
2. Expand the ingestion worker `-Q` argument to the union returned by
   `shard_change_queue_union(old_count, new_count)`. Include the legacy
   `ingestion` queue. For 8 to 9 this is
   `ingestion,ingestion.shard.0,...,ingestion.shard.8`. Deploy this consumer-only
   change while all producers still use the old count. Verify it with
   `celery -A app.worker.celery_app inspect active_queues`.
   The release Compose `worker` combines `default` and ingestion consumption,
   so do not recreate it for this phase. Start a temporary ingestion-only
   consumer, explicitly pinned to the old count, before stopping that combined
   worker (replace the example union for other count pairs):

   ```bash
   export OLD_COUNT=8
   export UNION_QUEUES=ingestion,ingestion.shard.0,ingestion.shard.1,ingestion.shard.2,ingestion.shard.3,ingestion.shard.4,ingestion.shard.5,ingestion.shard.6,ingestion.shard.7,ingestion.shard.8
   docker compose -f docker-compose.release.yml run --no-deps -d \
     --name testlookup-ingestion-drain \
     -e LIVE_INGEST_SHARD_COUNT="$OLD_COUNT" worker \
     celery -A app.worker.celery_app worker --loglevel=info \
     --concurrency=4 --prefetch-multiplier=1 -Q "$UNION_QUEUES"
   ```

   This temporary process consumes ingestion queues only and cannot execute the
   relay/recovery tasks on `default`.
3. Keep the union subscription through the cutover and rollback window. Remove
   obsolete queues only in a later release after the gates below remain clear
   for the full redelivery window.

## Stop every publisher

1. Enable the maintenance response at the ingress/load-balancer boundary so no
   API request can create or recover a live run.
2. Stop Celery beat. Its `relay-run-downstream-outbox` and
   `auto-recover-completed-live-runs` schedules publish ingestion work. Keep the
   default worker running temporarily so it can relay durable intents that were
   committed before maintenance mode began.
3. Kubernetes: record the current API replica count and delete the API HPA
   temporarily so it cannot recreate pods, then scale `testlookup-backend` and `testlookup-beat` to zero. Do not
   stop `testlookup-worker-default` yet. For example:

   ```bash
   kubectl -n testlookup delete hpa testlookup-backend-hpa
   kubectl -n testlookup scale deployment/testlookup-backend deployment/testlookup-beat --replicas=0
   ```

   Compose: `docker compose -f docker-compose.release.yml stop backend beat`.
   Account for environment-specific publisher processes before proceeding.
4. Leave the default worker and expanded ingestion consumers running while the
   durable outbox drains in the next section.

## Drain durable and broker state

First, require no nonterminal durable publish intent. Run this query repeatedly
until it returns no rows; investigate or allow each row to complete rather than
deleting it:

```sql
SELECT id, run_id, project_id, status, queue, next_attempt_at,
       lease_expires_at, processing_task_id
FROM run_downstream_outbox
WHERE operation = 'persist_live_session'
  AND status IN ('waiting', 'pending', 'sending', 'published', 'processing')
ORDER BY created_at;
```

With beat disabled, explicitly request one relay pass, wait for it to finish,
and repeat it after `next_attempt_at` for retriable rows until the SQL gate is
empty. The default worker must remain active to execute this task:

```bash
celery -A app.worker.celery_app call \
  app.worker.tasks.relay_run_downstream_outbox --queue default
```

After this query returns no rows, gracefully stop `testlookup-worker-default`
in Kubernetes. Export and temporarily delete
`testlookup-worker-default-hpa` before scaling its deployment to zero. With
release Compose, stop the combined `worker`; the temporary
`testlookup-ingestion-drain` consumer from the preparation phase must remain
running. Re-run the SQL query after the default publisher stops. Proceed only
when it still returns no rows.

Then require zero messages in every physical Redis priority list. Kombu's
default Redis priority steps are `0,3,6,9`; priority 7 work is stored in the
priority-6 list whose key suffix begins with bytes `06 16`. Run this for every
logical queue in the old/new union:

```bash
sep="$(printf '\006\026')"
for queue in ingestion ingestion.shard.{0..8}; do
  for suffix in "" "${sep}3" "${sep}6" "${sep}9"; do
    test "$(redis-cli --raw LLEN "${queue}${suffix}")" = 0 || exit 1
  done
done
```

Also require Celery inspection to show no `persist_live_session` task in
`active`, `reserved`, or `scheduled`, and inspect Redis's late-ack delivery
state:

```bash
celery -A app.worker.celery_app inspect active
celery -A app.worker.celery_app inspect reserved
celery -A app.worker.celery_app inspect scheduled
redis-cli --raw HVALS unacked | grep -F 'app.worker.tasks.persist_live_session'
```

The final command must produce no matches. Do not delete `unacked` or
`unacked_index`: an interrupted late-ack task must be restored by the broker.
If any ingestion worker was lost or forcibly stopped, keep publishers paused
and the union consumers running for at least the configured 3600-second Redis
`visibility_timeout`, plus the task's retry countdowns, then repeat every gate.
Only proceed when the SQL query, all priority lists, all three Celery states,
and Redis unacked state are clear at the same observation point.

## Switch and verify

1. Kubernetes: record the current ingestion replica count, delete
   `testlookup-worker-ingestion-hpa`, then scale
   `testlookup-worker-ingestion` to zero. The API and default-worker HPAs must
   already be absent. No old-count producer or consumer process may remain.
   Generate clean restore manifests from the source-controlled overlay rather
   than reapplying `kubectl get` output with server metadata. Apply only the new
   ConfigMap and patch only the ingestion Deployment's `-Q` argument while all
   publisher Deployments stay at zero, then restart and scale ingestion first:

   ```bash
   kubectl kustomize k8s/overlays/prod > /tmp/testlookup-target.yaml
   kubectl -n testlookup delete hpa testlookup-worker-ingestion-hpa
   kubectl -n testlookup scale deployment/testlookup-worker-ingestion --replicas=0
   kubectl -n testlookup apply -f /tmp/new-count-configmap.yaml
   kubectl -n testlookup patch deployment testlookup-worker-ingestion \
     --type=json --patch-file /tmp/new-ingestion-command-patch.json
   kubectl -n testlookup scale deployment/testlookup-worker-ingestion --replicas=1
   kubectl -n testlookup rollout status deployment/testlookup-worker-ingestion
   ```

   `/tmp/new-count-configmap.yaml` and the JSON patch must be extracted from the
   reviewed target render. Do not run `kubectl apply -k` during this barrier: it
   can restore publisher replicas and HPAs prematurely. Verify `active_queues`
   covers the union before continuing.
2. Release Compose: stop the old temporary drain consumer, then start the new
   regular combined worker with `--no-deps` so its `backend` dependency remains
   stopped. Verify its `active_queues` covers the union:

   ```bash
   docker stop testlookup-ingestion-drain
   docker rm testlookup-ingestion-drain
   docker compose -f docker-compose.release.yml up -d --no-deps worker
   ```

   Environment variables update only when a process is recreated.
3. Start one default worker and one API replica while their HPAs and beat remain
   stopped. Send one canary project and calculate its expected queue with
   `shard_for_count(project_id, new_count)`. After closing the live session,
   invoke exactly one `relay_run_downstream_outbox` pass with the command above.
   Wait for that canary's outbox row and run to reach a terminal state, and
   confirm no work appeared on an unexpected queue.
   In release Compose the combined worker is already the default worker; start
   the API explicitly with
   `docker compose -f docker-compose.release.yml up -d --no-deps backend`.
4. After the canary passes, apply the full reviewed Kubernetes overlay to
   restore source-controlled replica and HPA resources, or start the remaining
   Compose services. Restore ingress last. Confirm every running producer
   reports the new count. Monitor queue depth, outbox age/failures, task retries,
   and ingestion latency through the rollback window.

## Roll back

Keep the union consumer set. Repeat the full stop-and-drain gates, stop the
ingestion consumers, restore the old count to every producer, restart every
process, and run a canary calculated with the old count. Resume traffic only
after the canary completes. Never change the hash function in place or delete
queues during a count-change release; both can strand compatible queued work.

Before scheduling a change, run the fixed-corpus and deployment-contract tests:

```bash
cd backend
pytest -q tests/test_ingestion_routing.py tests/regression/test_ingestion_shard_change_contract.py
```

If online shard-count changes become frequent, introduce a versioned routing
epoch and dual-read/drain protocol before adopting rendezvous hashing.
