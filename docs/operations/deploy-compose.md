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

The `.env` target runs `scripts/gen-dev-env.sh` only when `.env` does not exist. It copies `.env.example`, fills every secret with a random value, keeps `DATABASE_URL` and `MONGO_URI` in step with the generated passwords, and sets `APP_ENV=development`, `APP_DEBUG=true` and `DEV_AUTO_LOGIN_ENABLED=true` so the login page offers Quick Login buttons. That file is for a local machine only; existing `.env` values are never overwritten.

Once it is up:

| What | Where |
|---|---|
| Dashboard | [localhost:3000](http://localhost:3000) |
| API reference (Swagger) | [localhost:8000/api-docs](http://localhost:8000/api-docs) |
| In-app user guide | [localhost:3000/docs](http://localhost:3000/docs) |
| MCP server (SSE, bearer token required) | `http://127.0.0.1:8002/sse` (bound to loopback) |
| Flower (Celery monitor, basic auth from `.env`) | `http://127.0.0.1:5555` |
| PostgreSQL | host port 5433 |
| Ollama and ChromaDB (`local-llm` profile) | port 11434; ChromaDB on `127.0.0.1:8001` (loopback only: it has no authentication, so only the backend and workers share its network) |

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

`install.sh` installs TestLookup from published images, with nothing built from source:

```bash
curl -fsSL https://raw.githubusercontent.com/test-intelligence/testlookup/main/install.sh | bash
```

It checks for Docker and Compose v2, downloads `docker-compose.release.yml`, `.env.example`, `scripts/gen-dev-env.sh` and `scripts/init-db.sql` into `./testlookup`, generates `.env` with `scripts/gen-dev-env.sh --release` (an existing `.env` is reused), and runs `docker compose -f docker-compose.release.yml up -d`. Read the script before piping it to a shell if your policy requires it.

`--release` fills every secret with a random value and keeps the secure defaults: `APP_ENV=production` and `DEV_AUTO_LOGIN_ENABLED=false`, so there is no passwordless login. It also writes a random `INITIAL_ADMIN_PASSWORD` into `.env`. On first start the `db-migrate` service runs `alembic upgrade head` and then `scripts/create_admin.py`, which creates the user `admin` with that password on a new database only (an existing admin is never modified) and requires a password change at the first sign-in.

The installer prints the admin login once at the end; the password is stored in `.env` as `INITIAL_ADMIN_PASSWORD`. If `.env` has no `INITIAL_ADMIN_PASSWORD`, a random password is generated and printed once in `docker compose -f docker-compose.release.yml logs db-migrate`. Sign in at [localhost:3000](http://localhost:3000) and set your own password.

| Variable | Default | Effect |
|---|---|---|
| `TL_DIR` | `testlookup` | Install directory |
| `TL_REF` | `main` | Branch, tag or commit the files are downloaded from |
| `TESTLOOKUP_VERSION` | `latest` | Image tag to run |
| `TL_PROFILE` | none | Adds a Compose profile: `demo` (seed data) or `local-llm` (Ollama and ChromaDB) |
| `TL_NO_UP` | `0` | `1` downloads and generates `.env` but does not start anything |

Pass them before `bash`, for example `curl -fsSL .../install.sh | TL_PROFILE=demo bash`.

### Image tags

Images are `ghcr.io/test-intelligence/testlookup/backend`, `.../frontend` and `.../mcp`; `TESTLOOKUP_IMAGE` overrides the registry and namespace. CI publishes `latest` and the full commit SHA for every build of `main`, and the release workflow adds a `vX.Y.Z` tag to the verified images when a release is tagged. `latest` moves; for a reproducible install, pin `TESTLOOKUP_VERSION` to a release tag or commit SHA in `.env`.

### Without the installer

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

Optional profiles:

```bash
docker compose -f docker-compose.release.yml --profile demo up -d        # demo projects and history (reuses the existing admin)
docker compose -f docker-compose.release.yml --profile local-llm up -d   # Ollama + ChromaDB
docker compose -f docker-compose.release.yml exec ollama ollama pull qwen2.5:7b-instruct
docker compose -f docker-compose.release.yml exec ollama ollama pull nomic-embed-text:v1.5
```

The release file publishes the backend on `127.0.0.1:8000` by default (`BACKEND_PUBLISH_ADDR`) and the frontend on port 3000. The frontend proxies `/api/`, `/ws/` and `/webhooks/` to the backend, so browsers only need port 3000.

### Before other people use it

A release `.env` already runs in production mode with random secrets; the backend refuses to start if any of them is a placeholder. For a shared host also:

1. Set `CORS_ORIGINS` and `PUBLIC_BASE_URL` to the URL people will open, and put a TLS-terminating reverse proxy in front of port 3000.
2. Check that `REDIS_PASSWORD` is set, so the broker requires authentication.
3. Schedule backups (below) and pin `TESTLOOKUP_VERSION`.

Run `docker compose -f docker-compose.release.yml up -d` again after editing `.env`. If you generated `.env` without `--release` (the local/demo mode with `APP_ENV=development` and passwordless login), regenerate it with `bash scripts/gen-dev-env.sh --release --force` before a shared install; `--force` replaces every secret, so do it before any data exists.

### Upgrading a release install

Take a backup, set `TESTLOOKUP_VERSION` to the new tag in `.env`, then:

```bash
docker compose -f docker-compose.release.yml pull
docker compose -f docker-compose.release.yml up -d
```

The `db-migrate` service runs `alembic upgrade head` with the new backend image, and the backend, workers and beat wait for it to complete. From a clone you can run the same sequence with checks and a smoke test: `make preflight TAG=vX.Y.Z` then `make upgrade TAG=vX.Y.Z`, with `TL_COMPOSE` set to the install's full Compose command (for example `TL_COMPOSE="docker compose -f docker-compose.release.yml -p testlookup"`, see `scripts/ops/lib.sh`).

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

The override sets `OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` and `METRICS_ENABLED=true` on the backend. The same alert rules are rendered for Kubernetes in [`k8s/monitoring`](deploy-kubernetes.md#monitoring-podmonitor-and-prometheusrule). Metric and alert reference: [observability](../../architecture/OBSERVABILITY.md).

## Backups, restore and upgrades

All commands run through `docker compose exec/run`, so the host needs only Docker and Bash.

| Command | Effect |
|---|---|
| `make backup` | Writes `backups/testlookup-backup-<UTC>.tar.gz`: PostgreSQL dump, MongoDB archive, object-store data and a manifest with the Alembic head. `QUIESCE=1` stops the app first for a consistent snapshot; `BACKUP_DIR` changes the destination |
| `make restore FILE=backups/<name>.tar.gz` | Validates the archive, stops the app, restores PostgreSQL, MongoDB and the object store, flushes Valkey, migrates and restarts. Refuses when the deployed schema is newer than the backup's unless `FORCE=1`; prompts unless `CONFIRM=yes` |
| `make preflight TAG=vX.Y.Z` | Checks image tags, pending migrations and disk headroom |
| `make upgrade TAG=vX.Y.Z` | Pulls (release stack) or rebuilds (source stack), migrates, restarts in order and runs the smoke check; prints rollback steps on failure |
| `make verify-ops-scripts` | Lints and unit-tests the scripts without a running stack |

Valkey, ChromaDB and Ollama volumes are not in the archive. Copy archives off the host. Details: [administration guide](../../user-guide/administration.md#backup-restore--upgrades).

## Troubleshooting

- `make` fails with `CreateProcess ... docker compose`: Docker is not on `PATH`, or you are on Podman without `DOCKER_COMPOSE="podman compose"`.
- Compose aborts with `... must be set`: a required variable is missing from `.env`. Regenerate with `bash scripts/gen-dev-env.sh --force` (this replaces all secrets) or add the variable.
- Port already in use: another stack or a local PostgreSQL/Valkey is using 3000, 8000, 5433, 6379 or 9000.
- The dashboard is empty after `make dev`: seed data loads in the background; watch `make dev-logs-seed`.
- More: [troubleshooting guide](../../user-guide/troubleshooting.md).
