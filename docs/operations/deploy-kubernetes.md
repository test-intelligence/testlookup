# Deploy on Kubernetes

[Documentation home](../README.md) · [Deployment guide](deployment.md)

TestLookup ships a Kustomize base (`k8s/base`) and overlays per environment and platform. This page covers the base, the generic overlays (`dev`, `staging`, `prod`, `self-hosted`), the deploy and migration scripts, monitoring and backups. Platform-specific pages: [managed clouds (EKS, GKE, AKS)](deploy-cloud.md), [OpenShift](deploy-openshift.md), [single-node K3s](deploy-k3s.md). A map of every overlay is in [`k8s/README.md`](../../k8s/README.md).

## What the base deploys

| Resource | Names | Notes |
|---|---|---|
| Namespace | `testlookup` | Overlays may rename it (`testlookup-dev`, `testlookup-staging`) |
| Deployments | `testlookup-backend`, `testlookup-frontend`, `testlookup-mcp`, `testlookup-worker-critical`, `testlookup-worker-ingestion`, `testlookup-worker-ai`, `testlookup-worker-default`, `testlookup-worker-children`, `testlookup-beat`, `testlookup-ollama` | Each worker subscribes to its own queue; the ingestion worker also consumes `ingestion.shard.0` to `ingestion.shard.7`; `worker-children` runs `agent_children` with concurrency 1; exactly one beat |
| Services | `testlookup-backend`, `testlookup-backend-diagnostics`, `testlookup-frontend`, `testlookup-mcp`, `testlookup-ollama`, and `testlookup-postgres`, `testlookup-mongo`, `testlookup-redis`, `testlookup-minio`, `testlookup-chromadb` | The datastore Services select `app: testlookup-<name>` pods, which the base does not create (see below) |
| Ingress | `testlookup-ingress` (UI and API host), `testlookup-mcp-ingress` (MCP host) | NGINX class, cert-manager annotation, hosts `testlookup.example.com` and `testlookup-mcp.example.com` |
| Autoscaling and disruption | HPAs for backend, frontend, the four queue workers and MCP; PodDisruptionBudgets | Overlays set the ranges |
| Network policies | `default-deny-all` plus explicit allows per component | See [network policies](#network-policies) |
| Config | ConfigMap `testlookup-config`, Secret template `testlookup-secrets`, RBAC | See below |
| Storage | PVC `ollama-models-pvc` (50Gi) for model weights | The Ollama container has a 32Gi memory limit |

### Datastores

The base does **not** run PostgreSQL, MongoDB, Valkey/Redis, the S3-compatible store or ChromaDB. You either:

- point the configuration at managed or existing services (recommended for production): set `DATABASE_URL` and `MONGO_URI` in the Secret, and `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `MINIO_ENDPOINT`, `MINIO_USE_SSL`, `CHROMA_HOST` and `CHROMA_PORT` in a ConfigMap patch; or
- run them in the cluster with pods labelled `app: testlookup-postgres`, `app: testlookup-mongo`, `app: testlookup-redis`, `app: testlookup-minio` and `app: testlookup-chromadb`, so the base Services find them. The `homelab` (single-node K3s) and `openshift-artifactory` overlays contain complete `infra-*.yaml` examples you can copy into your own overlay.

Whatever you use for the broker must persist data and must not evict keys without a TTL; the requirements are spelled out under [external Valkey or Redis](deploy-openshift.md#external-valkey-or-redis-requirements). External PostgreSQL must report at least `PG_FLEET_MAX_CONNECTIONS` (400 by default) from `SHOW max_connections`; see [database connection budget](database-connection-budget.md).

## Configuration

### Secret `testlookup-secrets`

`k8s/base/secrets.yaml` is a template with placeholder values. Create the real Secret out of band (External Secrets Operator, Vault, your cloud's secret manager, or `kubectl create secret`) and do not commit it. The production startup check refuses placeholder values.

| Key | Required | Value |
|---|---|---|
| `APP_SECRET_KEY` | yes | `openssl rand -hex 32`; encrypts stored integration credentials. Keep a copy: losing it makes those credentials unreadable |
| `JWT_SECRET_KEY` | yes | `openssl rand -hex 32`; signs access tokens |
| `WEBHOOK_SECRET` | yes | `openssl rand -hex 32`; authenticates inbound live-event and object-store webhooks |
| `DATABASE_URL` | yes | `postgresql+asyncpg://<user>:<password>@<host>:5432/testlookup` |
| `POSTGRES_PASSWORD` | yes | The database password (also used by the backup component) |
| `MONGO_URI` | yes | `mongodb://<user>:<password>@<host>:27017/testlookup_logs?authSource=admin` |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | yes | Credentials for the S3-compatible endpoint |
| `OPENAI_API_KEY`, `JIRA_API_TOKEN`, `SPLUNK_API_TOKEN`, `OCP_SA_TOKEN`, `SLACK_BOT_TOKEN` | no | Only for hosted model providers or integrations you enable |

```bash
kubectl create namespace testlookup
kubectl -n testlookup create secret generic testlookup-secrets \
  --from-literal=APP_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=WEBHOOK_SECRET="$(openssl rand -hex 32)" \
  --from-literal=DATABASE_URL='postgresql+asyncpg://testlookup_user:<db-password>@db.internal:5432/testlookup' \
  --from-literal=POSTGRES_PASSWORD='<db-password>' \
  --from-literal=MONGO_URI='mongodb://testlookup:<mongo-password>@mongo.internal:27017/testlookup_logs?authSource=admin' \
  --from-literal=MINIO_ACCESS_KEY='<access-key>' \
  --from-literal=MINIO_SECRET_KEY='<secret-key>'
```

### ConfigMap `testlookup-config`

Override these with a patch in your overlay (the generic overlays show the pattern):

| Key | Base value | Change it when |
|---|---|---|
| `CORS_ORIGINS`, `PUBLIC_BASE_URL` | `https://testlookup.example.com` | Always: the URL users open. Links in Jira, Slack and email use `PUBLIC_BASE_URL` |
| `TRUSTED_PROXY_IPS` | All three RFC 1918 private ranges | The base suits clusters whose users arrive from public addresses. On-premise clusters must narrow it to the pod CIDR (the `self-hosted` and `openshift` overlays do); never use `*` |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | `redis://testlookup-redis:6379/0` and `/1` | External or authenticated Valkey/Redis (`rediss://` for TLS) |
| `MINIO_ENDPOINT`, `MINIO_BUCKET_NAME`, `MINIO_USE_SSL` | `testlookup-minio:9000`, `test-telemetry`, `false` | External S3-compatible storage |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `MONGO_HOST`, `MONGO_PORT`, `MONGO_DB` | In-namespace service names | External databases (also read by the backup component) |
| `LLM_PROVIDER`, `LLM_MODEL`, `AI_OFFLINE_MODE` | `ollama`, the default local model, `true` | Choosing another model or a hosted provider. `AI_OFFLINE_MODE=true` blocks hosted providers |
| `OLLAMA_BASE_URL`, `CHROMA_HOST`, `CHROMA_PORT` | In-namespace services | Models or vectors served elsewhere |
| `LIVE_INGEST_SHARD_COUNT` | `8` | Only together with the worker subscriptions; follow [shard count change](ingestion-shard-count-change.md) |
| `PG_FLEET_*` | Fleet budget for the base replica counts | When you change replicas or pools |
| `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT` | Tracing off | Set both to enable tracing; CI fails an overlay that enables tracing without an endpoint |
| `OFFLINE_NOTIFICATION_ALLOWED_HOSTS`, `OFFLINE_EMAIL_ALLOWED_RECIPIENT_DOMAINS` | empty | Allowing Slack/Teams webhooks or an SMTP relay while offline |

Every setting the backend reads, with its default, is in the generated [configuration reference](../reference/configuration.md).

## Generic overlays

| Overlay | Namespace | Images | What it changes |
|---|---|---|---|
| `dev` | `testlookup-dev` | `testlookup/*:dev-latest`, `imagePullPolicy: Always` | One replica each, beat scaled to 0, HPAs pinned to 1, `APP_ENV=development`, `LOG_LEVEL=DEBUG` |
| `staging` | `testlookup-staging` | `testlookup/*:staging-latest`, `imagePullPolicy: Always` | Two API and UI replicas, one per worker, HPA ceilings of 2, `APP_ENV=staging`, `AI_OFFLINE_MODE=false` (hosted providers allowed) |
| `prod` | `testlookup` | `testlookup/*:0.0.1` | `APP_ENV=production`, `AI_OFFLINE_MODE=true`, TLS on the ingress, worker replica floors and wider HPA ranges |
| `self-hosted` | `testlookup` | `testlookup/*:0.0.1` | Builds on `prod` for kubeadm, K3s, RKE2, Rancher, OpenStack/VMware clusters and kind/minikube: NGINX ingress class, `TRUSTED_PROXY_IPS` set to common on-premise pod CIDRs, optional StorageClass patch |

Before applying any overlay, replace the example host names and `CORS_ORIGINS` (`*.yourdomain.com`, `testlookup.example.com`) with yours, and make the images resolvable: the overlays reference `testlookup/backend`, `testlookup/frontend` and `testlookup/mcp`. Either push your builds under those names (`make build-push REGISTRY=<registry> VERSION=<tag>`), set them with `kustomize edit set image`, or let `scripts/deploy-k8s.sh` rewrite them (`--registry`, `--image-tag`, `--release-manifest`). Published images are `ghcr.io/test-intelligence/testlookup/{backend,frontend,mcp}` with tags `latest`, the commit SHA, and `vX.Y.Z` for releases.

### Deploy with Make (dev, staging, prod)

```bash
make k8s-deploy-dev       # or k8s-deploy-staging / k8s-deploy-prod
make k8s-rollout-async-dev
make k8s-status-async K8S_NAMESPACE=testlookup-dev
```

Each `k8s-deploy-*` target renders the overlay to find the backend image, then:

1. On an existing install, runs the migration Job with the new image before anything else changes.
2. Runs `scripts/prepare-live-fanout-cutover.sh` (a one-time live-protocol cutover that is a no-op once complete).
3. Applies the overlay with `kubectl apply -k`.
4. On a fresh install, runs the migration Job after the apply.

`make k8s-migrate IMAGE=<backend image>` (or `K8S_OVERLAY=<dir>`) runs only the migration step; `make k8s-scale-worker QUEUE=ai REPLICAS=3` scales one queue worker.

### Deploy with `scripts/deploy-k8s.sh`

The script drives the `self-hosted` and cloud overlays:

```bash
scripts/deploy-k8s.sh --cloud=self-hosted --registry=registry.example.com --image-tag=v0.1.0
```

| Flag | Meaning |
|---|---|
| `--cloud=<aws\|gcp\|azure\|self-hosted>` | Required. Selects `k8s/overlays/aws-eks`, `gcp-gke`, `azure-aks` or `self-hosted` (aliases: `aws-eks`; `gcp-gke`, `gke`; `azure-aks`, `aks`; `on-prem`, `k3s`, `generic`) |
| `--image-tag=<tag>` | Image tag for all three images (default: the overlay's `0.0.1`) |
| `--registry=<url>` | Registry prefix, producing `<url>/testlookup/<image>` |
| `--release-manifest=<path>` | Deploy exactly the digests in a materialized release manifest ([deploy from CI](deploy-cloud.md#deploy-from-ci)) |
| `--namespace=<ns>` | Target namespace (default `testlookup`; keep it equal to the overlay's namespace) |
| `--dry-run` | Print the rendered manifests and exit |
| `--skip-secrets-check` | Do not stop when `testlookup-secrets` is missing (not recommended) |

What it does: checks the kubectl context, creates the namespace if needed, refuses to continue when `testlookup-secrets` is missing (and prints a `kubectl create secret` template), rewrites images in a temporary copy of `k8s/` when you pass `--registry`, `--image-tag` or `--release-manifest` (this needs the standalone `kustomize` binary), runs the migration Job, applies the overlay, waits for the backend, MCP and frontend rollouts, verifies every deployment's image against the release manifest when one is given, and creates the first admin user. The admin password is generated at first install and printed once; set `ADMIN_USERNAME`, `ADMIN_EMAIL` or `ADMIN_PASSWORD` in the environment to choose your own.

### Migrations: `scripts/run-k8s-migrations.sh`

```bash
scripts/run-k8s-migrations.sh <namespace> <backend-image> [release-id]
```

Runs `alembic upgrade head` as one Kubernetes Job (`backoffLimit: 0`, kept for 7 days after it finishes) using the target backend image with `testlookup-config` and `testlookup-secrets`, and waits for it. Environment: `KCLI` (default `kubectl`; set `oc` on OpenShift), `MIGRATION_TIMEOUT_SECONDS` (default 600), `MIGRATION_POLL_SECONDS` (default 2), `MIGRATION_ATTEMPT_ID`. Always migrate with the image you are about to roll out, before the new pods start serving.

## Network policies

`default-deny-all` blocks all traffic, then explicit policies open what each component needs: DNS egress for every pod; the frontend (port 80), the backend (port 8000) and MCP (port 8002) accept traffic from the `ingress-nginx` namespace and from OpenShift's router, matched by the `openshift-ingress` namespace name and by the `policy-group.network.openshift.io/ingress` namespace label (which also covers a router on the host network); the backend also accepts port 8000 from the frontend, workers and MCP; workers and the backend accept metrics scrapes from the `monitoring`, `openshift-monitoring` and `openshift-user-workload-monitoring` namespaces, or any namespace labelled `testlookup.io/metrics-scraper: "true"`. Backend and worker egress is open because datastores may live outside the namespace.

The base assumes the datastores run outside the namespace. An overlay that runs PostgreSQL, MongoDB, Redis or the object store inside it, as `homelab` and `openshift-artifactory` do, includes the [`colocated-data-stores` component](../../k8s/components/colocated-data-stores/networkpolicy.yaml):

```yaml
components:
  - ../../components/colocated-data-stores
```

Its `allow-data-stores` policy admits the backend and the workers to each store on that store's port (5432, 27017, 6379, 9000), and beat to Redis only. The migration Job carries the backend's label, so it reaches PostgreSQL too. Without the component, `default-deny-all` leaves the stores unreachable. An in-namespace ChromaDB is covered by the base `allow-chromadb`, which admits only the backend and the workers on port 8000.

If your ingress controller runs in a namespace with another name (Traefik in `kube-system`, for example) or sends traffic straight to pod IPs (AWS ALB `target-type: ip`, GKE container-native load balancing), add a matching `from` rule to `allow-frontend` and `allow-backend` in your overlay. The `aws-eks` and `gcp-gke` overlays already do this for MCP.

## Monitoring: PodMonitor and PrometheusRule

`k8s/monitoring` is opt-in because it needs the Prometheus Operator CRDs (`monitoring.coreos.com`); applying it to a cluster without them fails. Apply it after the application overlay:

```bash
kubectl apply -k k8s/monitoring
```

| File | Contents |
|---|---|
| `podmonitors.yaml` | `PodMonitor` `testlookup-backend` (port `http`, `/metrics` on 8000) and `testlookup-worker` (port `metrics`, 9100), each setting the `job` label the rules select on |
| `prometheusrule.yaml` | `PrometheusRule` `testlookup-alerts`, generated from `infra/monitoring/prometheus-rules/testlookup-alerts.yml` by `scripts/render_prometheusrule.py`: application alerts (backend or worker down, error rate, slow requests, AI pipeline failures, LLM circuit breaker, DLQ, stale reviews, live-event archive failures), infrastructure alerts (DB pool, broker memory, queue backlog, task latency, disk, OOM kills), latency SLOs for key API routes, and the two backup alerts |

The backup alerts read kube-state-metrics, and the disk and OOM alerts read node-exporter; without those exporters the rules stay silent. If your Prometheus selects rules and monitors by label (kube-prometheus-stack uses `release: <name>`), add that label in `k8s/monitoring/kustomization.yaml` as its comment shows.

## Backups

Add the [backup component](../../k8s/components/backup/README.md) to an overlay whose datastores you run in the cluster:

```yaml
components:
  - ../../components/backup
```

It creates a daily CronJob (`testlookup-backup`, 02:17) that writes `pg_dump`, `mongodump` and the object-store contents with a checksummed manifest to the `testlookup-backups` PVC, keeps the newest `BACKUP_KEEP` archives (14), and a restore CronJob template that runs only after you set `RESTORE_CONFIRM=yes` on `testlookup-restore-request`. Valkey, ChromaDB and Ollama are excluded. Copy archives off the cluster on a schedule. For managed datastores, use the provider's snapshots instead and add egress rules if you still run the component against external stores.

## Verify the rollout

```bash
make k8s-rollout-async K8S_NAMESPACE=testlookup
kubectl -n testlookup get pods,svc,ingress
kubectl -n testlookup exec deploy/testlookup-backend -- python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health/ready').read())"
```

Then open the UI, upload one report and confirm it reaches a finished run. The [startup and upgrade order](deployment.md#startup-and-upgrade-order) lists the full checks.
