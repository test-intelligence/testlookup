# Deployment, homelab and day-two operations

[Documentation home](../README.md)

Deployment assets describe supported topologies, not proof that a particular cluster is healthy. This documentation task did not start, migrate, upgrade or contact a live application stack.

## Deployment matrix

| Surface | Intended use | Queue/model considerations |
|---|---|---|
| `docker-compose.yml` | Source-built local/core stack | General worker consumes default/critical/ingestion/ai_analysis; separate child worker; API/main worker set live shard count 0; `local-llm` profile adds Ollama/Chroma |
| `docker-compose.dev-lite.yml` | Reduced local footprint | Explicit shard count 0 and isolated child consumer; inspect services before assuming parity with full stack |
| `docker-compose.release.yml` | Prebuilt release images | Shard count 8 with shard-aware worker subscriptions; optional demo/model profiles |
| Base + `docker-compose.gcp-vm.yml` | GCP VM deployment override | Use the `async` profile for worker/beat consumers; `ai` profile governs model services in this override |
| Release + `docker-compose.airgap.yml` | Private registry/offline image deployment | Requires explicit registry/version; stage all third-party images and weights |
| `docker-compose.monitoring.yml` | Optional monitoring services | Prometheus/Grafana/tracing configuration in `infra/monitoring` |
| `k8s/base` + overlays | Kubernetes application/worker deployment | Queue subscriptions, connection budget, probes, network policies, resource requests and migrations must match selected overlay |
| `k8s/overlays/homelab` | K3s/local registry setup | NodePort registry 30500, local-path volumes, pinned build tags, homelab deployment script |
| OpenShift/Artifactory overlays | OpenShift and air-gap registry workflows | Route/TLS/image mirroring differences; follow `openshiftsetup` and offline-bundle tooling |

Cloud/environment overlays and CI workflows exist for several targets. The presence of manifests is not evidence each overlay was deployed or has a current support SLA. [Kubernetes assets](../../k8s), [image manifest](../../deploy/images.manifest.txt), [deployment deep dive](../../architecture/DEPLOYMENT.md).

## Startup and upgrade order

1. Choose a reproducible image/commit and configuration; supply production secrets, URLs, TLS and trusted-proxy settings. Keep UI/API/MCP versions coherent.
2. Provision persistent services and verify capacity. PostgreSQL connection budgeting includes API/worker processes, reserved connections and migration allowance.
3. Run the migration job to the intended head using the matching backend image. Readiness is not a substitute for migration completion.
4. Start API, all required worker queues and one intended scheduler authority. Confirm `agent_children` and every configured ingestion shard have consumers.
5. Verify ingress paths, `/health/version`, readiness, metrics, one ingest-to-report flow and external integration behavior under the actual policy.

Use `make preflight TAG=...`, `make backup`, and `make upgrade TAG=...` only after reading the [ops scripts](../../scripts/ops). Backup supports quiescing for a coherent snapshot; restore is destructive and has its own confirmation guards. Do not assume downgrading one schema revision is a safe release rollback after data migrations. Preserve backups and previous immutable image digests.

## Persistence, capacity and models

Back up PostgreSQL, MongoDB and object storage together according to the runbook; consider Redis persistence/recovery semantics, model artifacts, keys/configuration and vector reindex/rebuild needs. Loss of the application encryption key can make saved encrypted credentials unusable even if the database is restored.

Redis is used for more than cache: broker/live data and temporary status share its failure domain. The shipped configuration uses AOF and eviction choices; `appendfsync everysec` is not a zero-data-loss guarantee. Match configured memory to event/queue volume and watch admission thresholds. Dedicated child workers and DB fleet budgeting bound concurrency; increasing replica counts without recalculating connections can refuse startup or exhaust the DB.

Local LLM RAM/VRAM and disk needs depend on quantization/context/concurrency. The default 3B/14B pair is substantially larger than the deterministic core; benchmark on the target machine. Weights are stored outside Git (Compose named volume or deployment storage). The `dev-llm` target downloads models, which is a network/bootstrap operation and cannot be assumed available after sealing an air-gap.

## Homelab specifics

The [homelab guide](../../homelabsetup/DEPLOY_TESTLOOKUP.md) and scripts cover K3s, Traefik/MetalLB, local registry, secrets, images, MinIO setup, models and admin setup. IPs, resource figures and observed replica counts in that guide are dated examples. Discover the current VIP and inspect the deployment before using them.

`local-path` volumes attach data to node disks. They are not replicated storage; a disk/node loss can lose data. Replica count does not change that property. Keep off-node backups and test restore. `BUILD_TAG_PLACEHOLDER` must be replaced by the deploy workflow; bypassing it can leave images unpullable. Respect the registry port agreement between bootstrap, deployment and overlay.

## Offline installation

`deploy/images.manifest.txt` and `scripts/release` support pinned image inventory, drift checks and offline bundles. The air-gap Compose override redirects infrastructure as well as app images. Prestage model/embedding weights and dependency artifacts, validate checksums, import images and prove network-denied startup/representative flows. Bundle integrity/import checks are narrower than full offline end-to-end acceptance. [Air-gap runbook](../../user-guide/air-gapped-install.md).

## Observability and troubleshooting

| Signal | Meaning / response |
|---|---|
| `/health/live` | API process can respond; avoid dependency restarts based on this alone |
| `/health/ready` | Critical PostgreSQL/Mongo/Redis readiness; 503 removes traffic |
| `/health/version` | Cheap version/revision/build/environment attribution |
| `/health/details` | Bounded dependency report; can be HTTP 200 with degraded components |
| `/metrics` | HTTP and domain counters/gauges when enabled; queue depth refreshes on scrape |
| Worker/beat logs and Flower | Dispatch/consumption/retry visibility; protect operational credentials |
| Project activity / decision trail | User-visible receipt and agent reasoning/provenance events |
| Upload/outbox/DLQ/pipeline records | Locate the failed asynchronous boundary, not just HTTP health |

Structured logs and optional OpenTelemetry/Prometheus/Grafana support operations. Celery tracing is not automatically guaranteed by installing an OTel library. Alert on stalled pipelines, lease/retry exhaustion, outbox/DLQ backlog, queue imbalance, DB connections and Redis memory. [Observability deep dive](../../architecture/OBSERVABILITY.md).
