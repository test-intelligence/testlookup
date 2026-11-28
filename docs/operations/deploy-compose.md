# Deploy with Docker Compose

[Documentation home](../README.md) · [Deployment guide](deployment.md)

Docker Compose runs the whole stack on one host. There are four Compose files you can start from and two overrides:

| File | Builds or pulls | Use it for |
|---|---|---|
| `docker-compose.yml` | Builds images from source, mounts source for reload | Demo and development from a clone (`make quickstart`, `make dev`, `make dev-llm`) |
| `docker-compose.dev-lite.yml` | Builds from source | A smaller stack for low-resource machines (`make dev-lite`) |
| `docker-compose.release.yml` | Pulls prebuilt images from `ghcr.io/test-intelligence/testlookup` | A host that runs published images, installed with `install.sh` or by hand |
| `docker-compose.airgap.yml` (override) | Re-points every image at your registry | Air-gapped hosts, layered on the release file |
| `docker-compose.monitoring.yml` (override) | Pulls Prometheus, Grafana, Jaeger | Metrics, dashboards and traces next to the stack |
| `docker-compose.telemetry.yml` (override) | Adds Collector, persistent Jaeger and Loki | [Durable logs and traces](durable-telemetry.md) |
| `docker-compose.gcp-vm.yml` (override) | Production build targets | A cloud VM; see [single VM](deploy-single-vm.md) |

## Prerequisites

- Docker Engine or Docker Desktop with Compose v2 (`docker compose version`).
- For the clone-based paths: Git, GNU Make and Bash. On Windows run the `make` commands from Git Bash or WSL; the Makefile sets `SHELL := bash`.
- Podman works if you export `DOCKER_COMPOSE="podman compose"` once; the Makefile reads that variable.
- Memory and disk: see the [sizing guide](../../user-guide/sizing.md). The local-LLM profile needs considerably more than the core stack.

The Compose files use fixed container names (`testlookup_backend`, `testlookup_postgres`, ...) and fixed host ports, so run one stack per host or write a full override that renames containers, ports, networks and volumes.

## Demo and development stack

From a clone:

```bash
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
make quickstart
```

| Target | What it does |
|---|---|
| `make quickstart` | Alias for `make demo`: generates `.env` if missing, starts the core stack, waits for `/health/ready`, uploads the JUnit files in `samples/junit/` and prints the URLs |
| `make dev` | Starts the core stack without local models; analysis falls back to the ML and rules engines |
| `make dev-llm` | Starts the stack with the `local-llm` profile (Ollama and ChromaDB), waits for Ollama and pulls the pinned models |
| `make dev-setup` | First-time alias for `make dev-llm` |
| `make smoke` | Runs `scripts/smoke.py` against `localhost:8000` and `localhost:3000` and prints PASS/FAIL per check |
| `make dev-logs`, `make dev-logs-seed`, `make logs` | Follow all logs, the demo-data seeder, or backend and worker |
| `make stop` | Stops every service, including the `local-llm` profile; data volumes are kept |
| `make restart` | Restarts every service |
| `make clean CONFIRM=yes` | Stops the stack and deletes all volumes (PostgreSQL, MongoDB, Valkey, object store, ChromaDB). Not a routine shutdown |

The `.env` target runs `scripts/gen-dev-env.sh` only when `.env` does not exist. It copies `.env.example`, fills every secret with a random value, keeps `DATABASE_URL` and `MONGO_URI` in step with the generated passwords, and sets `APP_ENV=development`, `APP_DEBUG=true` and `DEV_AUTO_LOGIN_ENABLED=true` so the login page offers Quick Login buttons. That file is for a local machine only (on Linux and macOS it is created readable by your user only); existing `.env` values are never overwritten.

Because Quick Login hands out an admin session without a password, the development stacks (`docker-compose.yml`, `docker-compose.dev-lite.yml`) publish every port on `127.0.0.1` only. To open the stack from another machine, prefer an SSH tunnel; otherwise set `FRONTEND_PUBLISH_ADDR` and `BACKEND_PUBLISH_ADDR` to `0.0.0.0` in `.env` and turn Quick Login off (`DEV_AUTO_LOGIN_ENABLED=false`) first.

Once it is up:

| What | Where |
|---|---|
| Dashboard | [localhost:3000](http://localhost:3000) |
| API reference (Swagger) | [localhost:8000/api-docs](http://localhost:8000/api-docs) |
| In-app user guide | [localhost:3000/docs](http://localhost:3000/docs) |
| MCP server (SSE, bearer token required) | `http://127.0.0.1:8002/sse` (bound to loopback) |
| Flower (Celery monitor, basic auth from `.env`) | `http://127.0.0.1:5555` |
| PostgreSQL | `127.0.0.1:5433` |
| Ollama and ChromaDB (`local-llm` profile) | `127.0.0.1:11434` (Ollama has no authentication); ChromaDB on `127.0.0.1:8001` (loopback only: it has no authentication, so only the backend and workers share its network) |

### Services

`db-migrate` runs `alembic upgrade head` once and the backend waits for it to finish. `db-budget-check` checks the PostgreSQL connection budget, `minio-setup` creates the storage buckets, and `seed-init` loads demo projects and history (`scripts/seed_dev_data.py`). The long-running services are `postgres`, `mongo`, `redis` (Valkey), `minio` (the S3-compatible store), `backend`, `worker` (queues `default`, `critical`, `ingestion`, `ai_analysis`), `worker-children` (the isolated `agent_children` queue), `beat`, `flower`, `frontend` and `mcp`; the `local-llm` profile adds `ollama` and `chromadb`.

### Local models

`make dev-llm` pulls three models into the `ollama_models` volume: the Makefile's `OLLAMA_SLM_MODEL`, `OLLAMA_LLM_MODEL` and `OLLAMA_EMBEDDING_MODEL` (see the top of the [Makefile](../../Makefile) for the pinned tags). Override any of them per run, for example `make dev-llm OLLAMA_LLM_MODEL=qwen2.5:7b-instruct`. `make list-llm` shows what is installed. Download time and inference speed depend on your hardware.

## Dev-lite stack

```bash
make dev-lite        # docker compose -f docker-compose.dev-lite.yml up -d --build
make dev-lite-stop   # docker compose -f docker-compose.dev-lite.yml down
```

The lite stack omits Ollama, ChromaDB, Flower, the MCP server and the beat scheduler. AI features degrade to the rules and ML engines because Ollama is not running. It uses its own container names (`testlookup_lite_*`) and volumes (`lite_*`) but the same host ports as the full stack, so stop one before starting the other. It needs `POSTGRES_PASSWORD`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` in `.env`.

## Release images without a clone

`install.sh` installs TestLookup from published images, with nothing built from source. Published releases require Docker with Compose v2, Bash, and a [GitHub CLI](https://cli.github.com/) version that provides `gh api`, `gh release download`, and `gh release verify-asset` plus artifact provenance verification. Authenticate with `gh auth login`, or set `GH_TOKEN`; a fine-grained token needs Contents and Attestations read access to this repository. Download the installer from an eligible immutable release and authenticate both its release-asset digest and workflow provenance before execution:

```bash
(
  set -euo pipefail
  TL_REPOSITORY="test-intelligence/testlookup"
  TL_RELEASE_FILTER='
    .tag_name as $tag
    | ([.assets[].name]) as $assets
    | select(
        .draft == false
        and .prerelease == false
        and .immutable == true
        and ($tag | test("^v[0-9]+\\.[0-9]+\\.[0-9]+$"))
        and (($assets | index("install.sh")) != null)
        and (($assets | index("testlookup-offline-\($tag | ltrimstr("v")).intoto.jsonl")) != null)
      )
    | .tag_name
  '
  if [ -n "${TL_INSTALLER_VERSION:-}" ]; then
    [[ "$TL_INSTALLER_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
      printf 'error: TL_INSTALLER_VERSION must be a vX.Y.Z release tag.\n' >&2
      exit 1
    }
    TL_SELECTED="$(gh api "repos/${TL_REPOSITORY}/releases/tags/${TL_INSTALLER_VERSION}" \
      --jq "$TL_RELEASE_FILTER")" || {
      printf 'error: could not inspect %s; run gh auth login or set GH_TOKEN.\n' "$TL_INSTALLER_VERSION" >&2
      exit 1
    }
    [ "$TL_SELECTED" = "$TL_INSTALLER_VERSION" ] || {
      printf 'error: %s is not an eligible immutable installer release with install.sh and its version-matched provenance bundle.\n' "$TL_INSTALLER_VERSION" >&2
      exit 1
    }
  else
    TL_CANDIDATES="$(gh api --paginate "repos/${TL_REPOSITORY}/releases?per_page=100" \
      --jq ".[] | ${TL_RELEASE_FILTER}")" || {
      printf 'error: could not inspect TestLookup releases; run gh auth login or set GH_TOKEN.\n' >&2
      exit 1
    }
    TL_INSTALLER_VERSION="${TL_CANDIDATES%%$'\n'*}"
    [ -n "$TL_INSTALLER_VERSION" ] || {
      printf 'error: no eligible immutable TestLookup release includes install.sh and its version-matched provenance bundle; use a source clone until one is published.\n' >&2
      exit 1
    }
  fi
  TL_BUNDLE="testlookup-offline-${TL_INSTALLER_VERSION#v}.intoto.jsonl"
  TL_BOOTSTRAP="$(mktemp -d)"
  trap 'rm -rf "$TL_BOOTSTRAP"' EXIT
  gh release download "$TL_INSTALLER_VERSION" --repo test-intelligence/testlookup \
    --pattern install.sh --pattern "$TL_BUNDLE" --dir "$TL_BOOTSTRAP" || {
      printf 'error: failed to download the authenticated installer assets for %s.\n' "$TL_INSTALLER_VERSION" >&2
      exit 1
    }
  gh release verify-asset "$TL_INSTALLER_VERSION" "$TL_BOOTSTRAP/install.sh" \
    --repo test-intelligence/testlookup || {
      printf 'error: install.sh failed immutable release-asset verification for %s.\n' "$TL_INSTALLER_VERSION" >&2
      exit 1
    }
  gh attestation verify "$TL_BOOTSTRAP/install.sh" \
    --repo test-intelligence/testlookup \
    --signer-workflow test-intelligence/testlookup/.github/workflows/offline-bundle.yml \
    --source-ref refs/heads/main \
    --bundle "$TL_BOOTSTRAP/$TL_BUNDLE" || {
      printf 'error: install.sh failed trusted offline-bundle.yml/main provenance verification for %s.\n' "$TL_INSTALLER_VERSION" >&2
      exit 1
    }
  TL_REF="$TL_INSTALLER_VERSION" TESTLOOKUP_VERSION="$TL_INSTALLER_VERSION" \
    bash "$TL_BOOTSTRAP/install.sh"
)
```

The bootstrap ignores drafts, prereleases, mutable releases, and releases missing either exact installer asset. It checks `install.sh` against the selected immutable release's recorded digest and verifies that the same bytes were signed by `offline-bundle.yml` on `main`, then pins the install to that tag before Bash interprets the file. The installer then checks for Docker and Compose v2, downloads `docker-compose.release.yml`, `.env.example`, `scripts/gen-dev-env.sh` and `scripts/init-db.sql` into `./testlookup`, authenticates the release checksums and the complete application-and-infrastructure digest set, generates `.env` with `scripts/gen-dev-env.sh --release` (an existing `.env` is reused), and starts Compose with a generated `docker-compose.images.yml` digest override.

`--release` fills every secret with a random value and keeps the secure defaults: `APP_ENV=production` and `DEV_AUTO_LOGIN_ENABLED=false`, so there is no passwordless login. It also writes a random `INITIAL_ADMIN_PASSWORD` into `.env`. On first start the `db-migrate` service runs `alembic upgrade head` and then `scripts/create_admin.py`, which creates the user `admin` with that password on a new database only (an existing admin is never modified) and requires a password change at the first sign-in.

The installer ends by saying where the admin password is, never the value, because a terminal's scrollback or a CI log keeps what is printed: it is stored in `.env` (mode 600) as `INITIAL_ADMIN_PASSWORD`. If `.env` has no `INITIAL_ADMIN_PASSWORD`, create an admin from a terminal with `docker compose -f docker-compose.release.yml -f docker-compose.images.yml exec backend python /app/scripts/create_admin.py`, which shows a generated password once. Sign in at [localhost:3000](http://localhost:3000) and set your own password. After that first sign-in, delete the `INITIAL_ADMIN_PASSWORD` line from `.env`: it is only used to create the admin on a new database, and `.env` is passed to several services.

The installer also explains that port 3000 is plain HTTP bound to `127.0.0.1` by default and points shared deployments to [Before other people use it](#before-other-people-use-it). It stops if it cannot set `.env` to mode 600.

| Variable | Default | Effect |
|---|---|---|
| `TL_DIR` | `testlookup` | Install directory |
| `TL_REF` | the latest published release | Branch, tag or commit the files are downloaded from. With a release tag (`vX.Y.Z`) the installer authenticates `SHA256SUMS`, `release-manifest.json`, and every application and infrastructure digest before startup. With neither this nor `TESTLOOKUP_VERSION` set, it looks up the latest release; if the lookup fails it stops. `TL_REF=main` explicitly installs the unverified tip and uses `latest` |
| `TESTLOOKUP_VERSION` | the release in `TL_REF` | Release to install. With a release tag (`vX.Y.Z`) and no `TL_REF`, files follow the same release; when both are release tags they must match |
| `TL_RAW_BASE` | `https://raw.githubusercontent.com/test-intelligence/testlookup` | Where the files are downloaded from (a mirror) |
| `TL_RELEASE_BASE` | `https://github.com/test-intelligence/testlookup/releases/download` | Where `SHA256SUMS`, `release-manifest.json`, and the release's Sigstore bundle are downloaded from (a mirror) |
| `TL_PROFILE` | none | Adds a Compose profile: `demo` (seed data) or `local-llm` (Ollama and ChromaDB) |
| `TL_NO_UP` | `0` | `1` downloads and generates `.env` but does not start anything |

Pass installer options on the final `bash` command in the authenticated block above. For an evaluation, add `TL_PROFILE=demo`; without it the first dashboard is empty until you send a report. For a team instance, leave it out. The images are about 1 GB to download (0.95 GB for linux/amd64, measured on 11 November 2026).

The block selects the newest eligible published release and pins both the verified installer and its downloads to that tag. To install a particular release, set `TL_INSTALLER_VERSION=vX.Y.Z` before the block; an invalid, mutable, draft, prerelease, or asset-less tag fails without silently selecting another release. The existing `v0.1.0` release predates `install.sh` and its matching provenance asset, so the default fails closed until a newer eligible release is published; use a source clone for that historical release. After both installer checks, the installer verifies `SHA256SUMS` and `release-manifest.json`, compares the four downloaded files, checks every manifest digest against CI's signed provenance for the release commit, and writes `docker-compose.images.yml` before startup. It stops, with nothing installed or run, when any asset, signature, version binding, component, digest, or checksum is missing or mismatched. For `main`, or another branch or commit explicitly selected by the operator after authenticating an eligible installer, it says the subsequently downloaded files and tag-addressed images are not verified.

### Image tags

Application images are `ghcr.io/test-intelligence/testlookup/backend`, `.../frontend` and `.../mcp`; `TESTLOOKUP_IMAGE` can repoint them to a mirror while preserving each authenticated digest. CI publishes `latest` and the full commit SHA for every build of `main`, and the release workflow adds a `vX.Y.Z` alias after verification. The installer never relies on mutable aliases: `docker-compose.images.yml` names `repository@sha256:...` for every application and infrastructure service. Keep both `-f docker-compose.release.yml -f docker-compose.images.yml` in later Compose commands.

### Without the installer

This manual path uses the tag expressions in `docker-compose.release.yml`; it does not authenticate or materialize the release digest manifest. Use `install.sh` when you need the signed digest guarantee.

```bash
mkdir testlookup && cd testlookup && mkdir scripts
base=https://raw.githubusercontent.com/test-intelligence/testlookup/main
curl -fsSLO "$base/docker-compose.release.yml"
curl -fsSLo .env.example "$base/.env.example"
curl -fsSLo scripts/gen-dev-env.sh "$base/scripts/gen-dev-env.sh"
curl -fsSLo scripts/init-db.sql "$base/scripts/init-db.sql"
bash scripts/gen-dev-env.sh --release
docker compose -f docker-compose.release.yml up -d
grep '^INITIAL_ADMIN_PASSWORD=' .env     # the password for user admin
```

For an installer-managed directory, keep the generated digest override when enabling optional profiles:

```bash
docker compose -f docker-compose.release.yml -f docker-compose.images.yml --profile demo up -d
docker compose -f docker-compose.release.yml -f docker-compose.images.yml --profile local-llm up -d
docker compose -f docker-compose.release.yml -f docker-compose.images.yml exec ollama ollama pull qwen2.5:7b-instruct
docker compose -f docker-compose.release.yml -f docker-compose.images.yml exec ollama ollama pull nomic-embed-text:v1.5
```

The release file publishes every port on `127.0.0.1` by default: the frontend (3000, `FRONTEND_PUBLISH_ADDR`), backend (8000, `BACKEND_PUBLISH_ADDR`), PostgreSQL (5433, `POSTGRES_PUBLISH_ADDR`), the S3 store (9000, `MINIO_PUBLISH_ADDR`) and Ollama (11434, `OLLAMA_PUBLISH_ADDR`). The frontend proxies `/api/`, `/ws/` and `/webhooks/` to the backend, so browsers only need port 3000. A publish-address variable is an explicit opt-in to another interface; the release stack does not add TLS when one is set.

### Before other people use it

A release `.env` already runs in production mode with random secrets; the backend refuses to start if any of them is a placeholder. For a shared host also:

1. Set `CORS_ORIGINS` and `PUBLIC_BASE_URL` to the `https://` URL people will open, and put a TLS-terminating reverse proxy on this host in front of `127.0.0.1:3000`. Keep the default loopback binding: the proxy can reach it, but network clients cannot bypass TLS and send passwords, session tokens or API keys directly to the plain HTTP port. Set `FRONTEND_PUBLISH_ADDR` only when a deliberately secured network path requires another bind address; the release stack does not provide TLS on that port.
2. `REDIS_PASSWORD` must be set: the release stack refuses to start without it (Compose prints `REDIS_PASSWORD is required`), and in production the backend refuses to start when a Redis URL names another host and carries no password. `scripts/gen-dev-env.sh` writes one. If you built `.env` by hand, generate one with `openssl rand -hex 24`.
3. After the first sign-in, delete the `INITIAL_ADMIN_PASSWORD` line from `.env`.
4. Schedule backups (below) and keep `docker-compose.images.yml` in every Compose command.

Run `docker compose -f docker-compose.release.yml -f docker-compose.images.yml up -d` again after editing `.env`. If you generated `.env` without `--release` (the local/demo mode with `APP_ENV=development` and passwordless login), regenerate it with `bash scripts/gen-dev-env.sh --release --force` before a shared install; `--force` replaces every secret, so do it before any data exists.

### Upgrading a release install

Take a backup, set `TL_INSTALLER_VERSION=vX.Y.Z`, then rerun the authenticated installer block above for the new release. It replaces the digest override while preserving the existing `.env`.

The `db-migrate` service runs `alembic upgrade head` with the new backend digest, and the backend, workers and beat wait for it to complete. From a clone you can run the same sequence with checks and a smoke test: `make preflight TAG=vX.Y.Z` then `make upgrade TAG=vX.Y.Z`, with `TL_COMPOSE` set to the install's full Compose command (for example `TL_COMPOSE="docker compose -f docker-compose.release.yml -f docker-compose.images.yml -p testlookup"`, see `scripts/ops/lib.sh`).

## Air-gapped override

On a host without internet access, load the images from the offline bundle into a local registry, then layer the override on the release file:

```bash
export TESTLOOKUP_REGISTRY=registry.internal/testlookup
export TESTLOOKUP_VERSION=0.1.0
docker compose -f docker-compose.release.yml -f docker-compose.airgap.yml up -d
```

The override re-points every image, including PostgreSQL, Valkey and the object store, at `${TESTLOOKUP_REGISTRY}`. Both variables are required, so a missing value fails instead of silently pulling from a public registry. MongoDB is not in the bundle (it is licensed SSPL-1.0): mirror `mongo:7` into the same registry yourself or point `MONGO_URI` at an existing MongoDB. The full procedure, including bundle verification and model weights, is the [air-gapped install guide](../../user-guide/air-gapped-install.md); models are covered by the [offline model pack](../../user-guide/offline-model-pack.md).

## Monitoring add-on

```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

| Service | Port | Notes |
|---|---|---|
| Grafana | [localhost:3001](http://localhost:3001) | User `admin`, password `GF_SECURITY_ADMIN_PASSWORD` from `.env` (required); the TestLookup overview dashboard is provisioned from `infra/monitoring/grafana/` |
| Prometheus | [localhost:9090](http://localhost:9090) | Scrape config `infra/monitoring/prometheus.yml`, alert rules `infra/monitoring/prometheus-rules/`, 15-day retention |
| Jaeger | [localhost:16686](http://localhost:16686) | OTLP receivers on 4317 (gRPC) and 4318 (HTTP) |

All of these listen on `127.0.0.1` only (`MONITORING_PUBLISH_ADDR`): Jaeger, the OTLP receivers and Prometheus have no authentication. Prometheus runs without `--web.enable-lifecycle`, so restart its container to reload the configuration.

The optional objectives overlay records core API availability and latency for a named installation, with separate adoption and routing for burn alerts. [API objectives](api-objectives.md).

The override sets `OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` and `METRICS_ENABLED=true` on the backend. The same alert rules are rendered for Kubernetes in [`k8s/monitoring`](deploy-kubernetes.md#monitoring-podmonitor-and-prometheusrule). Metric and alert reference: [observability](../../architecture/OBSERVABILITY.md).

The alert routing overlay delivers incident notifications and an independent watchdog over authenticated HTTPS. The monitoring guides cover required roles, broker backlog and worker progress. [Alert routing overlay](alert-routing.md), [telemetry contract](telemetry-contract.md), [Celery queue monitoring](celery-queue-monitoring.md).

### Authenticate the API scrape

Use `docker-compose.monitoring-auth.yml` after the monitoring file with either the source or release stack. Set `METRICS_API_TOKEN_SOURCE` and `METRICS_PROMETHEUS_TOKEN_SOURCE` to the absolute host paths of two protected files containing the same bearer token. Grant file read access to the corresponding container users and restrict access to the files on the host. Compose mounts each file only in the service that needs it. File-based Compose secrets use host file ownership and permissions. [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/).

```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml \
  -f docker-compose.monitoring-auth.yml up -d
```

Check that the `testlookup-backend` job is up and returns samples in Prometheus. From the private network, an API scrape with missing or mismatched credentials should return 401. Worker scrapes use the private Compose network on port 9100. Keep that port off published host and ingress routes. The bearer token crosses the private service network, so use a TLS scrape endpoint when crossing a trust boundary. [Prometheus scrape authorization](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#http_config).

Rotate by updating the two source files and recreating `backend` and `prometheus` with the same Compose file set. Recreation remounts files even if the secret manager atomically replaced them. Expect transient 401s while the two sides switch. Confirm the backend target is up with the new token and the old token returns 401 before ending the maintenance window.

## Backups, restore and upgrades

All commands run through `docker compose exec/run`, so the host needs only Docker and Bash.

| Command | Effect |
|---|---|
| `make backup` | Writes `backups/testlookup-backup-<UTC>.tar.gz`: PostgreSQL dump, MongoDB archive, object-store data and a manifest with the Alembic head. `QUIESCE=1` stops the app first for a consistent snapshot; `BACKUP_DIR` changes the destination |
| `make restore FILE=backups/<name>.tar.gz` | Validates the archive, stops the app, restores PostgreSQL, MongoDB and the object store, flushes Valkey, migrates and restarts. Refuses when the deployed schema is newer than the backup's unless `FORCE=1`; prompts unless `CONFIRM=yes` |
| `make preflight TAG=vX.Y.Z` | Checks image tags, pending migrations and disk headroom |
| `make upgrade TAG=vX.Y.Z` | Pulls (release stack) or rebuilds (source stack), migrates, restarts in order and runs the smoke check; prints rollback steps on failure |
| `make verify-ops-scripts` | Lints and unit-tests the scripts without a running stack |

Valkey, ChromaDB and Ollama volumes are not in the archive. The archive is not encrypted and holds the whole database, including password hashes; `make backup` writes it readable by its owner only (`umask 077`). Copy archives off the host, and encrypt the copy first (for example `gpg --symmetric` or `age`). Details: [administration guide](../../user-guide/administration.md#backup-restore--upgrades).

## Troubleshooting

- `make` fails with `CreateProcess ... docker compose`: Docker is not on `PATH`, or you are on Podman without `DOCKER_COMPOSE="podman compose"`.
- Compose aborts with `... must be set`: a required variable is missing from `.env`. Regenerate with `bash scripts/gen-dev-env.sh --force` (this replaces all secrets) or add the variable.
- Port already in use: another stack or a local PostgreSQL/Valkey is using 3000, 8000, 5433, 6379 or 9000.
- The dashboard is empty after `make dev`: seed data loads in the background; watch `make dev-logs-seed`.
- More: [troubleshooting guide](../../user-guide/troubleshooting.md).
