# Deployment guide

[Documentation home](../README.md)

TestLookup ships one set of images (backend, frontend, MCP) and several ways to run them. This page helps you pick a mode, lists what each mode needs, and covers the operations every mode shares: secrets, TLS, backups, start-up order, upgrades and health signals. Each mode has its own page with the commands.

The manifests in this repository describe supported topologies. They are not evidence that a particular cluster is healthy; verify your own installation with the checks at the end of this page.

## Pick a deployment mode

| Mode | What you run | Datastores | Typical use | Guide |
|---|---|---|---|---|
| Compose: demo / dev | `docker-compose.yml` via `make quickstart`, `make dev`, `make dev-llm` | In Compose | Evaluation, local development | [Compose](deploy-compose.md#demo-and-development-stack) |
| Compose: dev-lite | `docker-compose.dev-lite.yml` via `make dev-lite` | In Compose | Low-resource laptops | [Compose](deploy-compose.md#dev-lite-stack) |
| Compose: release images, no clone | `install.sh` downloads `docker-compose.release.yml` and pulls prebuilt images | In Compose | A single host running published images | [Compose](deploy-compose.md#release-images-without-a-clone) |
| Compose: air-gapped | `docker-compose.release.yml` + `docker-compose.airgap.yml` | In Compose | Hosts with no internet access | [Compose](deploy-compose.md#air-gapped-override), [air-gapped install](../../user-guide/air-gapped-install.md) |
| Compose: monitoring add-on | `docker-compose.monitoring.yml` layered on the stack | n/a | Prometheus, Grafana and Jaeger next to Compose | [Compose](deploy-compose.md#monitoring-add-on) |
| Single VM (GCP) | `docker-compose.yml` + `docker-compose.gcp-vm.yml` | In Compose on the VM | A small team on one cloud VM | [Single VM](deploy-single-vm.md) |
| Cloud Run templates | `infra/cloudrun/*` | Managed services you provide | Serverless API/UI/MCP with managed data | [Single VM and Cloud Run](deploy-single-vm.md#cloud-run-templates) |
| Kubernetes: generic overlays | `k8s/base` + `k8s/overlays/{dev,staging,prod,self-hosted}` | Provided by you (managed or in-cluster) | Any conformant cluster | [Kubernetes](deploy-kubernetes.md) |
| Kubernetes: single-node K3s | `k8s/overlays/homelab` + `homelabsetup/deploy-homelab.sh` | In-cluster | One machine, plain HTTP on a private network | [Single-node K3s](deploy-k3s.md) |
| OpenShift | `k8s/overlays/openshift` | Provided by you (Valkey/Redis must be durable) | OpenShift with Routes | [OpenShift](deploy-openshift.md#standard-openshift-overlay) |
| OpenShift + Artifactory | `k8s/overlays/openshift-artifactory` + `openshiftsetup/` | In-cluster | Air-gapped OpenShift that pulls from one registry | [OpenShift](deploy-openshift.md#air-gapped-openshift-with-one-artifactory) |
| Offline bundle | `make offline-bundle`, `scripts/release/import-bundle.sh` | Compose, Kubernetes or OpenShift | Sites with no egress at all | [Air-gapped install](../../user-guide/air-gapped-install.md) |
| AWS EKS | `infra/aws/` (Terraform) + `k8s/overlays/aws-eks` + `deploy-eks.yml` | RDS, DocumentDB, ElastiCache, S3 | AWS with managed data services | [Managed clouds](deploy-cloud.md#aws-eks) |
| Google GKE | `k8s/overlays/gcp-gke` + `deploy-gke.yml` | Provided by you | GKE with Google-managed certificates | [Managed clouds](deploy-cloud.md#google-gke) |
| Azure AKS | `k8s/overlays/azure-aks` + `deploy-aks.yml` | Provided by you | AKS with NGINX ingress | [Managed clouds](deploy-cloud.md#azure-aks) |

Rules of thumb:

- Trying TestLookup: `make quickstart` from a clone, or the `install.sh` one-liner if you do not want a clone.
- One team, one host: release images on Compose, with backups (`make backup`) on a schedule.
- Several teams, or you need replicas and rolling upgrades: Kubernetes with managed PostgreSQL, MongoDB, Valkey/Redis and S3-compatible storage.
- No internet at the install site: the [offline bundle](../../user-guide/air-gapped-install.md). Models are prepared separately with the [offline model pack](../../user-guide/offline-model-pack.md).

Hardware sizing for each profile (small, medium, large, plus the local-LLM add-on) is in the [sizing guide](../../user-guide/sizing.md).

## What every installation runs

| Component | Image / service | Notes |
|---|---|---|
| Frontend | `testlookup/frontend` | nginx serving the React app; proxies `/api/`, `/ws/` and `/webhooks/` to the backend |
| Backend API | `testlookup/backend` | FastAPI on port 8000; Swagger at `/api-docs` |
| Workers and scheduler | `testlookup/backend` running Celery | Queues `critical`, `ingestion` (plus shard queues), `ai_analysis`, `default`, and the isolated `agent_children` queue; one beat scheduler |
| MCP server | `testlookup/mcp` | SSE on port 8002 (bearer-authenticated) or stdio |
| PostgreSQL 16 | `postgres:16-alpine` | System of record |
| MongoDB 7 | `mongo:7` | Evidence, summaries and decision documents. MongoDB is licensed SSPL-1.0 and is not included in the offline bundle; provide it yourself there |
| Valkey 8 | `valkey/valkey:8.1-alpine` | Celery broker and result backend, live-event streams, caches. Settings keep the `REDIS_*` names; any Redis-protocol server that meets the durability notes below works |
| S3-compatible object store | `ghcr.io/versity/versitygw` (Apache-2.0) | Uploads, reports, exports, knowledge documents. Settings keep the `MINIO_*` names and point at any S3-compatible endpoint |
| Ollama (optional) | `ollama/ollama` | Local models. Default chat model `qwen2.5:7b-instruct`, embeddings `nomic-embed-text:v1.5` |
| ChromaDB (optional) | `chromadb/chroma` | Vector store for semantic search and knowledge retrieval |

`deploy/images.manifest.txt` is the single list of every image and tag; `make images-check` fails when Compose, Kubernetes or the mirror scripts drift from it.

## Required secrets by mode

The backend refuses to start with `APP_ENV=production` or `staging` when `APP_SECRET_KEY`, `JWT_SECRET_KEY` or `WEBHOOK_SECRET` is still a placeholder, when the password inside `MONGO_URI` is a placeholder, or when `DEV_AUTO_LOGIN_ENABLED` is true ([config checks](../../backend/app/core/config.py)). Generate each secret with `openssl rand -hex 32`.

| Mode | Where secrets live | Keys you must set |
|---|---|---|
| Compose (all variants) | `.env` next to the compose file. `make quickstart` generates it with `scripts/gen-dev-env.sh` (development mode); `install.sh` uses `scripts/gen-dev-env.sh --release` (production mode) | `POSTGRES_PASSWORD`, `MONGO_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` (Compose refuses to start without them), `FLOWER_PASSWORD` (source-built stack), `APP_SECRET_KEY`, `JWT_SECRET_KEY`, `WEBHOOK_SECRET`; `REDIS_PASSWORD` turns on broker authentication; `INITIAL_ADMIN_PASSWORD` for the first admin of a release install; `GF_SECURITY_ADMIN_PASSWORD` for the monitoring add-on |
| Single VM (GCP) | `.env` copied from `.env.gcp-vm.example` | The Compose keys above plus `GCP_PROJECT_ID` and `IMAGE_TAG` (required by `docker-compose.gcp-vm.yml`) |
| Kubernetes, all overlays | Secret `testlookup-secrets` in the target namespace | `APP_SECRET_KEY`, `JWT_SECRET_KEY`, `DATABASE_URL`, `POSTGRES_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MONGO_URI`, `WEBHOOK_SECRET`; optional provider and integration tokens. See [Kubernetes secrets](deploy-kubernetes.md#secret-testlookup-secrets) |
| OpenShift + Artifactory | Created by `deploy-openshift-artifactory.sh` on first install | Generated; you supply only the Artifactory credentials in `artifactory.env` |
| EKS via Terraform | AWS Secrets Manager (written by Terraform) | Synced into `testlookup-secrets` before deploy |
| CI deploy workflows | GitHub environment `production` secrets | Cloud credentials per workflow, listed in [managed clouds](deploy-cloud.md) |

Never commit `.env`, `openshiftsetup/artifactory.env` or a filled-in Secret manifest. Losing `APP_SECRET_KEY` makes saved encrypted credentials (integration tokens) unreadable even after a database restore, so back it up with your secrets store.

### The first administrator

A production-mode installation has no passwordless login. `scripts/create_admin.py` creates the first `ADMIN` user on a database that has none (an existing admin is never modified); when no password is supplied it generates one, prints it once, and requires a password change at the first sign-in.

- **Release install (`install.sh`)**: the installer prints the admin login once; the password is stored in `.env` as `INITIAL_ADMIN_PASSWORD`, and the `db-migrate` service creates the user `admin` with it on first start. See [release images](deploy-compose.md#release-images-without-a-clone).
- **Source-built Compose**: `make create-admin` runs the script in the backend container. The local/demo stack (`make quickstart`, `make dev`) runs in development mode with passwordless Quick Login instead.
- **Kubernetes and OpenShift**: `scripts/deploy-k8s.sh` and `openshiftsetup/deploy-openshift-artifactory.sh` create the admin at first install and print the generated password once; `make create-admin-k8s` runs the script by hand.

## TLS and ingress

| Mode | How traffic reaches TestLookup | TLS |
|---|---|---|
| Compose | Frontend on host port 3000 (source build) or 3000 mapped to nginx port 80 (release images); backend 8000; MCP 8002 bound to loopback | None built in. Put a reverse proxy (nginx, Caddy, Traefik) in front and terminate TLS there |
| Single VM | Frontend nginx on port 80 | Add a reverse proxy or load balancer with a certificate before exposing it publicly |
| Kubernetes `prod` / `self-hosted` / `azure-aks` | NGINX Ingress `testlookup-ingress` (`/api/`, `/api-docs`, `/ws/`, `/webhooks/`, `/health/`, `/metrics` to the backend, `/` to the frontend) and `testlookup-mcp-ingress` on its own host | cert-manager annotation `cert-manager.io/cluster-issuer: letsencrypt-prod`, Secrets `testlookup-tls-cert` and `testlookup-mcp-tls-cert` |
| GKE | GCE Ingress | Google-managed certificate (`managed-cert.yaml`) |
| EKS | AWS Load Balancer Controller (ALB) | ACM certificate ARN annotation |
| OpenShift | Routes with edge termination and HTTP-to-HTTPS redirect | Router default wildcard certificate, or your own PEM files |
| Single-node K3s | Traefik on plain HTTP | Private networks only; see [single-node K3s](deploy-k3s.md) |

Behind any proxy, set `CORS_ORIGINS` and `PUBLIC_BASE_URL` to the URL users open, and set `TRUSTED_PROXY_IPS` to the proxy's address range only (never `*`): the API uses it to find the real client address for rate limiting and audit logs. The overlays carry commented defaults for each platform.

## Startup and upgrade order

1. Choose a reproducible image tag or digest and configuration; supply production secrets, URLs, TLS and trusted-proxy settings. Keep UI, API and MCP versions coherent.
2. Provision persistent services and verify capacity. PostgreSQL connection budgeting includes API and worker processes, reserved connections and the migration allowance; see [database connection budget](database-connection-budget.md).
3. Run the migration job to the intended head using the matching backend image (Compose: the `db-migrate` service; Kubernetes: `scripts/run-k8s-migrations.sh`). Readiness is not a substitute for migration completion.
4. Start the API, every required worker queue and exactly one scheduler (beat). Confirm `agent_children` and every configured ingestion shard have consumers.
5. Verify ingress paths, `/health/version`, readiness, metrics, one ingest-to-report flow and external integration behavior under the actual policy.

For Compose, `make preflight TAG=...`, `make backup` and `make upgrade TAG=...` run this sequence; read the [ops scripts](../../scripts/ops) first. `upgrade.sh` pulls (release stack) or rebuilds (source stack), runs `alembic upgrade head` before replacing the app, restarts in dependency order and runs the smoke check. It does not roll back automatically: the pre-upgrade backup is the rollback path. Do not assume downgrading one schema revision is a safe release rollback after data migrations. Keep backups and the previous image digests.

Version-specific steps, such as moving an existing install from Redis to Valkey, are in [UPGRADING.md](../../UPGRADING.md). Changing `LIVE_INGEST_SHARD_COUNT` needs a coordinated producer and worker change: follow [ingestion shard count change](ingestion-shard-count-change.md).

## Backups

| Mode | Tooling | What it covers |
|---|---|---|
| Compose | `make backup` (`QUIESCE=1` stops the app first, `BACKUP_DIR` sets the destination) and `make restore FILE=... [FORCE=1] [CONFIRM=yes]` | PostgreSQL dump, MongoDB archive, object-store data and a manifest with the Alembic head, in one `.tar.gz` |
| Kubernetes | The [backup component](../../k8s/components/backup/README.md): a daily CronJob to a PVC plus an on-demand restore Job | Same three stores; enabled in the K3s and OpenShift + Artifactory overlays, opt-in elsewhere |
| Managed services | Your provider's snapshots and point-in-time recovery | Back up PostgreSQL, MongoDB and the bucket together |

Valkey/Redis, ChromaDB and Ollama are not in the archive on purpose: they are rebuildable, and replaying a stale broker queue after a restore is dangerous. Back up PostgreSQL, MongoDB and object storage together so their references stay consistent, copy archives off the host, and test a restore. Operator detail: [administration guide](../../user-guide/administration.md#backup-restore--upgrades).

## Persistence, capacity and models

Valkey/Redis is more than a cache: it is the Celery broker, holds live-event streams and temporary status, and shares their failure domain. The shipped Compose and in-cluster configurations use append-only files with `appendfsync everysec` and the `volatile-lru` eviction policy, which only evicts keys that carry a TTL. That is not a zero-data-loss guarantee. An external Valkey/Redis must persist data and must not evict keys without a TTL; the [OpenShift page](deploy-openshift.md#external-valkey-or-redis-requirements) states the full contract. Match memory to event and queue volume and watch admission thresholds. Dedicated child workers and the database connection budget bound concurrency; raising replica counts without recalculating connections can refuse startup or exhaust the database.

Local-model RAM, VRAM and disk depend on the model, quantization, context and concurrency; benchmark on the target machine. Weights live outside Git, in a Compose volume or deployment storage. `make dev-llm` downloads models, which needs network access; an air-gapped site loads them from an [offline model pack](../../user-guide/offline-model-pack.md). `AI_OFFLINE_MODE=true` (the default) blocks hosted model providers and other outbound AI calls while still allowing local endpoints such as Ollama; it is not a substitute for network isolation.

`local-path` volumes (K3s) attach data to one node's disk; replicas do not protect against losing that disk. Keep off-node backups.

## Observability and troubleshooting

| Signal | Meaning / response |
|---|---|
| `/health/live` | The API process can respond; do not restart dependencies based on this alone |
| `/health/ready` | Critical PostgreSQL, MongoDB and Valkey/Redis readiness; 503 removes the pod from traffic |
| `/health/version` | Version, revision, build and environment attribution |
| `/health/details` | Bounded dependency report; can return HTTP 200 with degraded components |
| `/metrics` | HTTP and domain counters and gauges when `METRICS_ENABLED`; queue depth refreshes on scrape |
| Worker and beat logs, Flower | Dispatch, consumption and retry visibility; Flower is bound to loopback in Compose |
| Project activity and decision trail | User-visible receipts and agent reasoning/provenance events |
| Upload, outbox, DLQ and pipeline records | Locate the failed asynchronous step, not just HTTP health |

Prometheus alert rules live in `infra/monitoring/prometheus-rules/`; Compose loads them through the [monitoring add-on](deploy-compose.md#monitoring-add-on) and Kubernetes through [`k8s/monitoring`](deploy-kubernetes.md#monitoring-podmonitor-and-prometheusrule). Alert on stalled pipelines, lease or retry exhaustion, outbox/DLQ backlog, queue imbalance, database connections and broker memory. Installing an OpenTelemetry library does not by itself trace Celery tasks. [Observability deep dive](../../architecture/OBSERVABILITY.md), [troubleshooting](../../user-guide/troubleshooting.md).

## Related

- [Architecture overview](../../ARCHITECTURE.md) and [deployment topologies](../../architecture/DEPLOYMENT.md)
- [Local development](development.md) for host-based iteration
- [Configuration reference](../reference/configuration.md) (generated from the settings class) and [worker operations](../reference/worker-operations.md)
