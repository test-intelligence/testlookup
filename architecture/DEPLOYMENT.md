# Deployment Topologies

> Companion to [README.md](./README.md) §2 (containers & ports). The same
> images run in every topology below — what changes is orchestration, ingress,
> and where state lives. This page explains the design; the step-by-step guide
> for each mode, with required secrets, TLS and backups, starts at
> [docs/operations/deployment.md](../docs/operations/deployment.md).

## 1. Choosing a topology

| You want… | Use | Entry point |
|---|---|---|
| Local dev / evaluation | Docker Compose (source build) | `make quickstart` / `make dev` ([guide](../docs/operations/deploy-compose.md)) |
| Self-host without cloning | Compose (pinned release images) | `install.sh` one-liner → `docker-compose.release.yml` ([guide](../docs/operations/deploy-compose.md#release-images-without-a-clone)) |
| Minimal-footprint dev | `docker-compose.dev-lite.yml` | `make dev-lite` |
| A single cloud VM | `docker-compose.yml` + `docker-compose.gcp-vm.yml` | compose on the VM ([guide](../docs/operations/deploy-single-vm.md)) |
| Kubernetes anywhere | `k8s/base` + an overlay | `scripts/deploy-k8s.sh` or `make k8s-deploy-<env>` ([guide](../docs/operations/deploy-kubernetes.md)) |
| **No egress at all** (air-gapped site) | **offline install bundle** — one tarball with every image, manifests and an import script | `make offline-bundle` → [user-guide/air-gapped-install.md](../user-guide/air-gapped-install.md) |
| Metrics stack alongside | `docker-compose.monitoring.yml` + `infra/monitoring` | compose override |

## 2. Compose variants

- **`docker-compose.yml`** — the development stack: builds images from source,
  bind-mounts source for reload, brings up PostgreSQL, MongoDB, Valkey, the
  S3-compatible store, backend, frontend, workers, beat and MCP. `make dev-llm`
  adds Ollama + ChromaDB through the `local-llm` profile.
- **`docker-compose.release.yml`** — the no-clone self-host artifact: **pulls
  pinned `ghcr.io/test-intelligence/testlookup/{backend,frontend,mcp}` images**
  (`TESTLOOKUP_IMAGE` overrides the namespace; `TESTLOOKUP_VERSION`, default
  `latest`). No `build:`, no host-source mounts. Optional profiles:
  `--profile demo` (seed data) and `--profile local-llm` (Ollama + ChromaDB).
  Fetched and booted by the `install.sh` one-liner, which generates a
  production-mode `.env` and the first admin's password.
- **`docker-compose.airgap.yml`** — an **override**, never used alone. Layered
  on `docker-compose.release.yml` it re-points *every* image (app and
  third-party) at `${TESTLOOKUP_REGISTRY}`; without it Compose has no way to
  redirect the hardcoded public-registry references for the datastores.
  Both `TESTLOOKUP_REGISTRY` and `TESTLOOKUP_VERSION` are mandatory (`:?`) so a
  missing value fails loudly instead of silently pulling from a public registry.
  `make dev` and `install.sh` never load it and are unaffected.
- **`docker-compose.dev-lite.yml`** — a slimmer dev stack for
  resource-constrained machines (no Ollama, ChromaDB, Flower, MCP or beat).
- **`docker-compose.gcp-vm.yml`** — production-target override for the base
  Compose file. Render and deploy it with `docker-compose.yml` and the `async`
  profile so the main, critical, and isolated child queues all have consumers.
- **`docker-compose.monitoring.yml`** — Prometheus, Grafana and Jaeger
  (dashboards, scrape configs and alert rules under `infra/monitoring`).

All compose files default the security-critical env the same way
(`${APP_ENV:-production}`, `${DEV_AUTO_LOGIN_ENABLED:-false}`) — see
[SECURITY.md §4](./SECURITY.md#4-secure-by-default-deployment-posture).

## 3. Kubernetes — one base, many overlays

`k8s/base` is a complete Kustomize base for the application: deployments for
backend, frontend, MCP, the per-queue Celery workers (the ingestion worker
subscribes to **all** ingestion shard queues — the invariant from
[INGESTION_SCALE.md §3](./INGESTION_SCALE.md#3-shard-queues-workeringestion_routingpy)),
the isolated child worker and beat, plus configmap/secret template, services,
ingress, HPA, PDB, RBAC, network policies, and an Ollama deployment. It does
not run the datastores; overlays or the operator provide them.

Overlays specialize it per target ([map of every overlay](../k8s/README.md)):

| Overlay | Target / notes |
|---|---|
| `dev`, `staging`, `prod` | environment tiers (`make k8s-deploy-<env>`) |
| `self-hosted` | generic on-prem cluster built on `prod` (`scripts/deploy-k8s.sh --cloud=self-hosted`) |
| `gcp-gke`, `aws-eks`, `azure-aks` | managed-cloud variants built on `prod` (`scripts/deploy-k8s.sh --cloud=gcp\|aws\|azure`) ([guide](../docs/operations/deploy-cloud.md)) |
| `homelab` | single-node K3s with in-cluster datastores and a local registry; the deploy script, the registry and the kustomization must agree on the registry port or locally built images fail with ImagePullBackOff ([guide](../docs/operations/deploy-k3s.md)) |
| `openshift` / `openshift-artifactory` | OpenShift Routes with edge TLS; the `-artifactory` variant is fully **air-gapped** — its `images:` block re-points *every* image (app + infra) at one registry. Two ways in: registry-to-registry mirroring when the build host can reach both sides (`openshiftsetup/*.sh`), or the **offline bundle** when it cannot — [user-guide/air-gapped-install.md](../user-guide/air-gapped-install.md). The bundle renders this same overlay ([guide](../docs/operations/deploy-openshift.md)) |

`k8s/monitoring` (PodMonitors + a PrometheusRule rendered from the Compose alert
rules) and `k8s/components/backup` (daily dump CronJob + restore) are opt-in
add-ons. `k8s/components/colocated-data-stores` holds the network policy that
opens in-namespace datastores to the backend, workers and beat; `homelab` and
`openshift-artifactory` include it.

## 4. CI/CD pipelines (`.github/workflows/`)

- **`ci.yml`** — the merge gate (backend/frontend/MCP tests + lint, quality
  gates, docs/mermaid check, no-`:latest` check across **both** `k8s/` and the
  compose files, and an image-manifest drift job); on a push to `main` it also
  publishes `:latest` and `:<commit sha>` images to
  `ghcr.io/test-intelligence/testlookup` and a `verified-images-<sha>` digest
  manifest.
- **`offline-bundle.yml`** — builds the air-gap bundle with the network on,
  then verifies checksums, `docker load`, retag and an offline
  application-module import inside `--network none --pull=never` containers.
  Scope is deliberate: it proves **bundle integrity + offline import**, not a
  full-stack end-to-end deployment.
- **`release.yml`** — on a `vX.Y.Z` tag that matches `VERSION` and is
  reachable from `main`: requires a verified CI run for that commit, scans the
  exact image digests with Trivy and keeps SBOM evidence, then promotes the
  verified digests to the `vX.Y.Z` tag without rebuilding.
- **`require-verified-sha.yml`** — the reusable gate `release.yml` calls:
  exactly one successful `ci.yml` push run on `main` for the commit, with its
  digest manifest. No workflow deploys to a cloud; a CI deploy built on this
  gate is sketched in
  [deploy from CI](../docs/operations/deploy-cloud.md#deploy-from-ci).

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
Exceptions (an image used by only one surface, or excluded from the bundle)
are recorded in the manifest with their reason inline.

## 5. Environment realities

- **TLS-intercepting endpoint protection** (corporate antivirus or VPN HTTPS
  scanning) breaks in-container `pip`/`npm` with "unable to get local issuer
  certificate". The fix is CA injection into the Dockerfiles at build time
  (`INSTALL_EXTRA_CA` with the CA staged in `backend/certs/` or
  `frontend/certs/`) — not disabling verification in the images.
- **Ports** (defaults): frontend 3000, backend 8000, MCP SSE 8002, Ollama
  11434; infra services on their standard ports. The frontend build is
  deploy-target agnostic — unset `VITE_API_BASE_URL` means same-origin
  relative URLs behind any ingress.

## 6. Day-2 operations

Compose deployments get one-command ops via `scripts/ops/` (all datastore
access goes through `docker compose exec/run` — no host-side client tools):

- **`make backup` / `make restore FILE=…`** — single-archive backup
  (pg_dump + mongodump + object-store data + manifest with the alembic head);
  restore refuses an archive older than the deployed schema unless forced.
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
