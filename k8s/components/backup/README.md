# Kubernetes backup and restore (re-audit M25)

Namespace `testlookup` throughout. Enabled in the `homelab` and
`openshift-artifactory` overlays, whose Postgres, MongoDB and MinIO run in the
cluster. To enable it elsewhere, add to the overlay's `kustomization.yaml`:

```yaml
components:
  - ../../components/backup
```

## What is backed up

A daily CronJob (`testlookup-backup`, 02:17, cluster time zone, usually UTC)
writes one archive to the `testlookup-backups` PVC:

```
testlookup-backup-<UTC timestamp>.tar.gz
  manifest.json       versions, alembic head, sha256 of every component
  postgres.dump       pg_dump -Fc (all relational data)
  mongo.archive.gz    mongodump --archive --gzip (pipeline + audit event logs)
  minio_data.tar.gz   every bucket and object (reports, compliance packs, RAG docs)
```

Redis, ChromaDB and Ollama are left out on purpose, as in
`scripts/ops/backup.sh`: they are rebuildable, and replaying a stale Celery
queue after a restore is dangerous.

`pg_dump` comes from `postgres:16-alpine`. An external PostgreSQL newer than 16
needs a matching client image. If `DATABASE_URL` carries asyncpg-only query
parameters (such as `?ssl=require`), set `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_USER`, `POSTGRES_DB` and `POSTGRES_PASSWORD` instead. `lib.sh` falls
back to them.

## Retention

The newest `BACKUP_KEEP` archives are kept; the default is 14, which is two
weeks of dailies. Set it on the `finalize` container. Older archives are
deleted at the end of each successful run. A failed run writes nothing and
deletes nothing.

## Checking it works

```sh
kubectl -n testlookup get cronjob testlookup-backup
kubectl -n testlookup create job testlookup-backup-now --from=cronjob/testlookup-backup
kubectl -n testlookup wait --for=condition=complete job/testlookup-backup-now --timeout=30m
kubectl -n testlookup logs job/testlookup-backup-now --all-containers
```

(Name a manual Job `testlookup-backup-<something>` so the failure alert
covers it too.)

## Alerts

`k8s/monitoring` (opt-in, Prometheus Operator) ships two rules for this
component, from `infra/monitoring/prometheus-rules/testlookup-alerts.yml`:

| Alert | Fires when |
|---|---|
| `TestLookupBackupJobFailed` | a Job owned by the `testlookup-backup` CronJob (or named `testlookup-backup-*`) has FAILED: its `Failed` condition is true (`kube_job_status_condition`), which is set only once every retry (`backoffLimit`) failed. A backup whose first pod failed and whose retry succeeded does not fire. It keeps firing while the failed Job exists: fix the cause, then `kubectl -n testlookup delete job <name>`. |
| `TestLookupBackupStale` | no successful run for 26 hours (`kube_cronjob_status_last_successful_time`), including a CronJob that is suspended or has never succeeded. |

Both read **kube-state-metrics**. kube-prometheus-stack deploys and scrapes
it; on any other Prometheus, scrape kube-state-metrics or these rules have no
data and stay silent.

## Network access

The backup pod may reach DNS and the in-namespace Postgres (5432), MongoDB
(27017) and MinIO (9000), nothing else (`networkpolicy.yaml`). If your stores
are external, add an egress rule for their addresses in your overlay; until
you do, the backup fails and `TestLookupBackupJobFailed` fires.

## Off-cluster copy

The PVC is in the same cluster, and often on the same storage, as the data it
protects. Copy the archives off the cluster on a schedule, for example:

```sh
POD=$(kubectl -n testlookup get pod -l app.kubernetes.io/component=backup \
      --field-selector=status.phase=Succeeded -o name | tail -1)
# or run any pod that mounts the PVC, then:
kubectl -n testlookup cp <pod>:/backups ./testlookup-backups
```

## Restore (runbook)

A restore replaces the current Postgres and MongoDB contents and re-uploads
every MinIO object from the archive.

1. **Pick the archive.**

   ```sh
   kubectl -n testlookup run ls-backups --rm -it --restart=Never --image=busybox:1.36 \
     --overrides='{"spec":{"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"testlookup-backups"}}],"containers":[{"name":"ls","image":"busybox:1.36","command":["ls","-l","/backups"],"volumeMounts":[{"name":"b","mountPath":"/backups"}]}]}}'
   ```

2. **Stop writers.** Suspend the backup and scale the app to zero:

   ```sh
   kubectl -n testlookup patch cronjob testlookup-backup -p '{"spec":{"suspend":true}}'
   kubectl -n testlookup scale deployment -l app.kubernetes.io/component=api --replicas=0
   kubectl -n testlookup scale deployment -l app.kubernetes.io/component=worker --replicas=0
   kubectl -n testlookup scale deployment -l app.kubernetes.io/component=scheduler --replicas=0
   kubectl -n testlookup scale deployment testlookup-mcp --replicas=0
   ```

3. **Request the restore.** Nothing runs until `RESTORE_CONFIRM` is `yes`:

   ```sh
   kubectl -n testlookup patch configmap testlookup-restore-request --type merge \
     -p '{"data":{"RESTORE_FILE":"testlookup-backup-20260911T021700Z.tar.gz","RESTORE_CONFIRM":"yes"}}'
   ```

4. **Run it.**

   ```sh
   kubectl -n testlookup create job restore-now --from=cronjob/testlookup-restore
   kubectl -n testlookup wait --for=condition=complete job/restore-now --timeout=4h
   kubectl -n testlookup logs job/restore-now --all-containers
   ```

   `verify` checks every sha256 in `manifest.json` before any data is
   touched. A corrupt or altered archive stops the Job there. Postgres is
   restored in a single transaction.

5. **Migrate, then start.** The backup may predate the running code. Run the
   migrations with the deployed backend image, then scale the app back up.
   Flush Redis, so queued tasks do not refer to data that no longer exists:

   ```sh
   bash scripts/run-k8s-migrations.sh testlookup <backend image of the running release>
   kubectl -n testlookup exec deploy/testlookup-redis -- redis-cli FLUSHALL   # in-cluster Redis
   kubectl -n testlookup scale deployment -l app.kubernetes.io/component=api --replicas=2
   kubectl -n testlookup scale deployment -l app.kubernetes.io/component=worker --replicas=1
   kubectl -n testlookup scale deployment -l app.kubernetes.io/component=scheduler --replicas=1
   kubectl -n testlookup scale deployment testlookup-mcp --replicas=1
   ```

   Then re-apply the overlay (`kubectl apply -k k8s/overlays/<name>`). This
   restores the replica counts and resets `RESTORE_CONFIRM` to `no`. Resume
   the backup:

   ```sh
   kubectl -n testlookup patch cronjob testlookup-backup -p '{"spec":{"suspend":false}}'
   ```

6. **Check.** Semantic search vectors (ChromaDB) rebuild within the hour
   (`reindex_search` beat). Open the dashboard and confirm the latest runs are
   those in the backup.

Archives written by the Compose `make backup` hold a raw MinIO volume and are
restored with `make restore`. `restore.sh` refuses them.
