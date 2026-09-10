# Deployment Topologies

> Companion to [README.md](./README.md) §2 (containers & ports). The same
> images run in every topology below — what changes is orchestration, ingress,
> and where state lives. Verified against the tree 2026-07-02.

## 1. Choosing a topology

| You want… | Use | Entry point |
|---|---|---|
| Local dev / evaluation | Docker Compose (source build) | `make quickstart` / `make dev` |
| Self-host without cloning | Compose (pinned release images) | `install.sh` one-liner → `docker-compose.release.yml` |
| Minimal-footprint dev | `docker-compose.dev-lite.yml` | compose directly |
| A single cloud VM | `docker-compose.yml` + `docker-compose.gcp-vm.yml` | compose on the VM |
| Kubernetes anywhere | `k8s/base` + an overlay | `kubectl apply -k k8s/overlays/<name>` |
| **No egress at all** (air-gapped site) | **offline install bundle** — one tarball with every image, manifests and an import script | `make offline-bundle` → [user-guide/air-gapped-install.md](../user-guide/air-gapped-install.md) |
| Metrics stack alongside | `docker-compose.monitoring.yml` + `infra/monitoring` | compose profile |

## 2. Compose variants

- **`docker-compose.yml`** — the development stack: builds images from source,
  bind-mounts source for reload, brings up postgres/mongo/redis/minio +
  backend/frontend/workers. `make dev-llm` adds Ollama + ChromaDB.
- **`docker-compose.release.yml`** — the no-clone self-host artifact: **pulls
  pinned `ghcr.io/anandtopu/testlookup/{backend,frontend,mcp}` images**
  (`TESTLOOKUP_VERSION`, default `latest`; semver tags published by
  `release.yml` on `v*.*.*` tag push, with SBOM + provenance and digests in the
  release summary). No `build:`, no host-source mounts. Optional profiles:
  `--profile demo` (seed data) and `--profile local-llm` (Ollama). Fetched and
  booted by the `install.sh` one-liner.
- **`docker-compose.airgap.yml`** — an **override**, never used alone. Layered
  on `docker-compose.release.yml` it re-points *every* image (app and
  third-party) at `${TESTLOOKUP_REGISTRY}`; without it Compose has no way to
  redirect the hardcoded Docker Hub references for postgres/mongo/redis/MinIO.
  Both `TESTLOOKUP_REGISTRY` and `TESTLOOKUP_VERSION` are mandatory (`:?`) so a
  missing value fails loudly instead of silently pulling from `docker.io`.
  `make dev` and `install.sh` never load it and are unaffected.
- **`docker-compose.dev-lite.yml`** — a slimmer dev stack for
  resource-constrained machines.
- **`docker-compose.gcp-vm.yml`** — production-target override for the base
  Compose file. Render and deploy it with `docker-compose.yml` and the `async`
  profile so the main, critical, and isolated child queues all have consumers.
- **`docker-compose.monitoring.yml`** — Prometheus/Grafana sidecar stack
  (dashboards and scrape configs under `infra/monitoring`).

All compose files default the security-critical env the same way
(`${APP_ENV:-production}`, `${DEV_AUTO_LOGIN_ENABLED:-false}`) — see
[SECURITY.md §4](./SECURITY.md#4-secure-by-default-deployment-posture).

## 3. Kubernetes — one base, many overlays

`k8s/base` is a complete Kustomize base: deployments for backend, frontend,
MCP, and the Celery workers (subscribed to **all** ingestion shard queues — the
invariant from [INGESTION_SCALE.md §3](./INGESTION_SCALE.md#3-shard-queues-workeringestion_routingpy)),
plus configmap/secrets, services, ingress, HPA, PDB, RBAC, network policies,
and an optional Ollama deployment.

Overlays specialize it per target:

| Overlay | Target / notes |
|---|---|
| `dev`, `staging`, `prod` | environment tiers used by the deploy workflows |
| `gcp-gke`, `aws-eks`, `azure-aks` | managed-cloud variants (wired to `deploy-gke/eks/aks.yml`) |
| `homelab` | K3s with a local registry (NodePort **30500** — bootstrap, deploy script, and kustomization must agree on the port or locally-built images ImagePullBackOff) |
| `openshift` / `openshift-artifactory` | OpenShift Routes with edge TLS; the `-artifactory` variant is fully **air-gapped** — its `images:` block re-points *every* image (app + infra) at one registry. Two ways in: registry-to-registry mirroring when the build host can reach both sides (`openshiftsetup/*.sh`), or the **offline bundle** when it cannot — [user-guide/air-gapped-install.md](../user-guide/air-gapped-install.md). The bundle renders this same overlay. |
| `self-hosted` | generic on-prem cluster |

## 4. CI/CD pipelines (`.github/workflows/`)

- **`ci.yml`** — the merge gate (backend/frontend/MCP tests + lint, quality
  gates, docs/mermaid check, no-`:latest` check across **both** `k8s/` and the
  compose files, and an image-manifest drift job); on main push it also
  publishes `:latest` + `:sha-<sha>` images.
- **`offline-bundle.yml`** — builds the air-gap bundle with the network on,
  then verifies checksums, `docker load`, retag and an offline
  application-module import inside `--network none --pull=never` containers.
  Scope is deliberate: it proves **bundle integrity + offline import**, not a
  full-stack end-to-end deployment.
- **`release.yml`** — on a `v*.*.*` tag: semver-tagged GHCR images
  (`:vX.Y.Z :X.Y.Z :X.Y :X`), SBOM + `provenance: mode=max`, build-provenance
  args baked into the backend image (surfaced at `/health/details`), and a
  release summary with content-addressable digests to pin.
- **`deploy-staging.yml` / `deploy-production.yml` / `deploy-gke|eks|aks.yml`**
  — environment deploys over the matching overlays (cloud ones need their
  cloud credentials configured).

## 4b. One image manifest behind every surface

`deploy/images.manifest.txt` is the single source of truth for every container
image TestLookup deploys: reference, pinned tag, role (`app` / `infra`), which
surfaces use it, and whether it belongs in the offline bundle. Plain text with
four columns, deliberately dependency-free so bash, Python and the CI guard can
all read it without PyYAML.

Everything derives from it — `scripts/release/offline-bundle.sh`,
`openshiftsetup/mirror-images.sh`, `deploy-openshift-artifactory.sh` — and
`scripts/release/check_image_drift.py` (CI: **Images — Manifest drift**, or
`make images-check`) fails the build when Compose, `k8s/**`, the
`openshift-artifactory` overlay or the mirror scripts disagree with it.

One documented exception lives in that file: MinIO is pinned to **two**
different tags, one for Compose and one for Kubernetes, each with its reason
inline. The bundle ships both.

## 5. Environment realities

- **TLS-intercepting endpoint protection** (corporate AV/VPN HTTPS scanning)
  breaks in-container `pip`/`npm` with "unable to get local issuer
  certificate". The fix is CA injection into the Dockerfiles at build time
  (`INSTALL_EXTRA_CA`, staged by the homelab deploy script) — not disabling
  verification in the images.
- **Ports** (defaults): frontend 3000, backend 8000, MCP SSE 8002, Ollama
  11434; infra services on their standard ports. The frontend build is
  deploy-target agnostic — unset `VITE_API_BASE_URL` means same-origin
  relative URLs behind any ingress.

## 6. Day-2 operations

Compose deployments get one-command ops via `scripts/ops/` (all datastore
access goes through `docker compose exec/run` — no host-side client tools):

- **`make backup` / `make restore FILE=…`** — single-archive backup
  (pg_dump + mongodump + MinIO volume + manifest with the alembic head);
  restore refuses schema-mismatched archives unless forced.
- **`make preflight` / `make upgrade [TAG=vX.Y.Z]`** — pre-upgrade report
  (images, pending migrations, disk headroom) and the pull → migrate →
  restart → smoke sequence. No automatic rollback by design — the
  pre-upgrade backup is the rollback path.

Operator-facing detail: [user-guide/administration.md](../user-guide/administration.md#backup-restore--upgrades);
capacity planning: [user-guide/sizing.md](../user-guide/sizing.md).

## Related docs

- First-run walkthrough: [GETTING_STARTED.md](../GETTING_STARTED.md)
- Runtime containers & who talks to what: [README.md §1–2](./README.md#1-system-context)
- Secure-by-default posture: [SECURITY.md](./SECURITY.md)
