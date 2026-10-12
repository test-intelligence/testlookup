# Deploy on a single VM

[Documentation home](../README.md) · [Deployment guide](deployment.md) · [Compose guide](deploy-compose.md)

This page runs the full stack on one Linux VM with Docker Compose, using production build targets: `docker-compose.yml` plus the `docker-compose.gcp-vm.yml` override and the `.env.gcp-vm.example` template. The commands use Google Compute Engine, but any Linux VM with Docker works the same way once it exists. The second part covers the [Cloud Run templates](#cloud-run-templates) in `infra/cloudrun/`.

If you do not need to build from source, the [release-image install](deploy-compose.md#release-images-without-a-clone) on the same VM is simpler.

## What the override changes

| Service | Change |
|---|---|
| `backend` | Production build target, `uvicorn` with 2 workers, `APP_ENV=production` and `APP_DEBUG=false` by default, `TRUSTED_PROXY_IPS=172.16.0.0/12` (the Docker bridge range the frontend's nginx connects from) |
| `frontend` | Production nginx image on host port 80; the API URL is compiled in from `VITE_API_BASE_URL` |
| `worker`, `worker-children`, `beat`, `flower` | Moved to the `async` profile; the worker consumes `default,critical,ingestion,ai_analysis` with `CELERY_WORKER_CONCURRENCY` (template default 1) |
| `ollama`, `chromadb` | Moved to the `ai` profile |
| All application images | Tagged `gcr.io/${GCP_PROJECT_ID}/testlookup-{backend,frontend,mcp}:${IMAGE_TAG}`; both variables are required. The images are built on the VM; nothing is pushed unless you push them |

Uploads are processed by the Celery workers, so always run the `async` profile. Without it the API accepts files that are never parsed.

## Sizing

Use at least the small profile from the [sizing guide](../../user-guide/sizing.md): 4 vCPU, 8 GB RAM and a 100 GB SSD for a year of retention at up to 50k results a day. The local-model profile adds about 4 vCPU and 8 GB RAM; a GPU makes it practical. On Compute Engine, `e2-standard-4` (4 vCPU, 16 GB) covers both.

## Step 1: create the VM

From Cloud Shell or a workstation with `gcloud`:

```bash
gcloud services enable compute.googleapis.com
gcloud compute addresses create testlookup-ip --region=us-central1
gcloud compute instances create testlookup-vm \
  --zone=us-central1-a \
  --machine-type=e2-standard-4 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-type=pd-balanced --boot-disk-size=100GB \
  --address=testlookup-ip \
  --tags=testlookup-web
gcloud compute firewall-rules create testlookup-allow-web \
  --allow=tcp:22,tcp:80,tcp:443 \
  --target-tags=testlookup-web
```

The reserved address keeps the external IP stable across restarts; the frontend has the URL compiled in. Open only 22, 80 and 443: the API (8000), the object store (9000), PostgreSQL (5433) and the MCP server stay unreachable from outside. Restrict `--source-ranges` to your network if you can.

## Step 2: install Docker and get the code

```bash
gcloud compute ssh testlookup-vm --zone=us-central1-a
```

On the VM, install Docker Engine and the Compose plugin from Docker's Debian repository ([Docker's instructions](https://docs.docker.com/engine/install/debian/)), add your user to the `docker` group, then:

```bash
sudo apt-get install -y git make
git clone https://github.com/test-intelligence/testlookup.git
cd testlookup
cp .env.gcp-vm.example .env
```

## Step 3: generate secrets

```bash
for name in APP_SECRET_KEY JWT_SECRET_KEY WEBHOOK_SECRET POSTGRES_PASSWORD MONGO_PASSWORD MINIO_SECRET_KEY FLOWER_PASSWORD REDIS_PASSWORD; do
  echo "$name: $(openssl rand -hex 32)"
done
```

Keep the output somewhere safe; you paste the values into `.env` in the next step. `APP_SECRET_KEY` encrypts stored integration credentials, so losing it makes them unreadable.

## Step 4: configure `.env`

Edit `.env` (`nano .env`) and set:

```bash
APP_SECRET_KEY=<your generated key from step 3>
JWT_SECRET_KEY=<your generated key from step 3>
WEBHOOK_SECRET=<your generated key from step 3>
POSTGRES_PASSWORD=<your generated key from step 3>
MONGO_PASSWORD=<your generated key from step 3>
MINIO_ACCESS_KEY=testlookup
MINIO_SECRET_KEY=<your generated key from step 3>
FLOWER_PASSWORD=<your generated key from step 3>
REDIS_PASSWORD=<your generated key from step 3>

GCP_PROJECT_ID=<your project id>
IMAGE_TAG=<a tag for this build, e.g. the commit from git rev-parse --short HEAD>
COMPOSE_FILE=docker-compose.yml:docker-compose.gcp-vm.yml
COMPOSE_PROFILES=async

CORS_ORIGINS=http://<VM external IP>
PUBLIC_BASE_URL=http://<VM external IP>
VITE_API_BASE_URL=http://<VM external IP>
```

Notes:

- `WEBHOOK_SECRET`, `REDIS_PASSWORD`, `GCP_PROJECT_ID`, `IMAGE_TAG`, `COMPOSE_FILE`, `COMPOSE_PROFILES` and `PUBLIC_BASE_URL` are not in the template; add the lines. In production mode the backend refuses to start while `APP_SECRET_KEY`, `JWT_SECRET_KEY` or `WEBHOOK_SECRET` is a placeholder.
- `COMPOSE_FILE` and `COMPOSE_PROFILES` make a plain `docker compose ...` (and the `make backup` / `make upgrade` scripts) use the override and the worker profile, so you do not have to repeat `-f` and `--profile` flags.
- `VITE_API_BASE_URL` is the frontend's own address: its nginx proxies `/api/`, `/ws/` and `/webhooks/` to the backend over the internal network, so port 8000 never needs to be public.
- Leave `APP_ENV=production` and `APP_DEBUG=false` from the template.

### Choose how analysis runs

TestLookup is offline-first: the default is a local model served by Ollama, and nothing leaves the VM. The template ships a hosted-provider example instead; for the local default, change the AI lines to:

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b-instruct
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text:v1.5
AI_OFFLINE_MODE=true
COMPOSE_PROFILES=async,ai
```

and pull the models once the stack is running (step 5):

```bash
docker compose exec ollama ollama pull qwen2.5:7b-instruct
docker compose exec ollama ollama pull nomic-embed-text:v1.5
```

To use a hosted model provider instead, set `AI_OFFLINE_MODE=false`, `LLM_PROVIDER` (`openai`, `gemini`, `anthropic` or `openrouter`), `LLM_MODEL` and the matching key (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`), and leave the `ai` profile out. Test failure details are then sent to that provider. Without any model, analysis uses the ML and rules engines.

## Step 5: start the stack

```bash
docker compose up -d --build
docker compose ps
```

The first build takes several minutes. `db-migrate` applies the schema before the backend starts, and `minio-setup` creates the storage buckets. Check readiness from the VM:

```bash
curl -s http://localhost:8000/health/ready
```

The override also runs the `seed-init` service, which loads the demo projects and five demo accounts with random passwords that are never printed (one of them is named `admin`). Delete the demo projects from the UI when you no longer need them.

## Step 6: create your administrator

```bash
docker compose exec -e ADMIN_USERNAME=<your user name> -e ADMIN_EMAIL=<your email> backend python /app/scripts/create_admin.py
```

The script creates an `ADMIN` user, generates a random password when `ADMIN_PASSWORD` is not set, prints it once, and requires a new password at the first sign-in. Use a user name other than `admin`, which the demo seed already took. Sign in at `http://<VM external IP>`.

## Step 7: verify

- Open `http://<VM external IP>` and sign in.
- Upload a JUnit XML file (Runs → Upload) and wait until the run shows its results.
- For the API reference and the MCP server, tunnel from your workstation instead of opening ports:

```bash
gcloud compute ssh testlookup-vm --zone=us-central1-a -- -N -L 8000:127.0.0.1:8000 -L 8002:127.0.0.1:8002
```

Then browse [localhost:8000/api-docs](http://localhost:8000/api-docs), and point MCP clients at `http://127.0.0.1:8002/sse` with their own `Authorization: Bearer <access token>` header. The MCP container publishes 8002 on the VM's loopback only. Client configuration: [`mcp/README.md`](../../mcp/README.md).

## Step 8: add TLS

Before real users or credentials reach the VM, terminate HTTPS in front of port 80: a reverse proxy on the VM (Caddy or nginx with a certificate) or a Google Cloud load balancer. Then set `CORS_ORIGINS`, `PUBLIC_BASE_URL` and `VITE_API_BASE_URL` to the `https://` address and rebuild the frontend with `docker compose up -d --build frontend`. If the proxy runs outside the Docker network, include its address in `TRUSTED_PROXY_IPS` (never `*`).

## Operating the VM

| Task | Command |
|---|---|
| Logs | `docker compose logs -f backend worker` |
| Migration status | `docker compose exec backend alembic current` |
| Backup | `make backup` (`QUIESCE=1` for a consistent snapshot); copy `backups/` off the VM |
| Upgrade | `git pull`, set a new `IMAGE_TAG`, then `make upgrade` (rebuilds, migrates, restarts, smoke-tests) |
| Stop / start | `docker compose stop` / `docker compose up -d`; data stays in the named volumes |

Containers use `restart: unless-stopped`, so they come back when the VM reboots. Data lives in the Docker volumes `postgres_data`, `mongo_data`, `redis_data` and `minio_data`; `docker compose down -v` deletes them. Restore and upgrade details: [Compose guide](deploy-compose.md#backups-restore-and-upgrades).

## Cloud Run templates

`infra/cloudrun/` contains starting points for running the API, UI and MCP server on Cloud Run with managed data services. They are reference templates and are not exercised in CI.

| File | Purpose |
|---|---|
| `cloudbuild.backend.yaml`, `cloudbuild.frontend.yaml` | Build the production images into Artifact Registry (`${_REGION}-docker.pkg.dev/${_PROJECT_ID}/${_REPO}/...:latest`); the frontend takes `_VITE_API_BASE_URL` |
| `cloudbuild.mcp.yaml` | Builds the MCP image tagged with `$COMMIT_SHA` and `latest` (Cloud Build fills `$COMMIT_SHA` for trigger-based builds) |
| `backend.env.example` | Backend settings: `DATABASE_URL` in Cloud SQL Unix-socket form (`?host=/cloudsql/PROJECT:REGION:INSTANCE`), `MONGO_URI`, broker URLs, S3-compatible endpoint and keys, model provider |
| `frontend.env.example` | Build-time `VITE_API_BASE_URL` and app name/version |
| `mcp.env.example` | `TESTLOOKUP_API_URL`, `TESTLOOKUP_MCP_ALLOWED_HOSTS` (the exact public host) and timeouts; no user credential is stored |

What you provide: Cloud SQL for PostgreSQL, a MongoDB service, a Valkey/Redis service that meets the [durability requirements](deploy-openshift.md#external-valkey-or-redis-requirements), and an S3-compatible object store. Things to know before you start:

- The templates cover the API, UI and MCP only. Celery workers and the beat scheduler are long-running consumers; run them where they stay up (this VM with only the `async` profile pointed at the same datastores, or a Kubernetes cluster). Without workers, uploads are accepted and never processed.
- The backend listens on 8000, the MCP server on 8002 and the frontend on 80; deploy each Cloud Run service with the matching `--port`.
- `backend.env.example` sets `APP_ENV=production` but has no `WEBHOOK_SECRET`; add one, or the backend refuses to start. The example's AI settings use hosted providers; set `AI_OFFLINE_MODE=true` and a private model endpoint if data must stay in your project.
- The frontend's nginx proxies to `BACKEND_UPSTREAM` (default `backend:8000`), which does not resolve on Cloud Run; set it to a resolvable `host:port`, and set `VITE_API_BASE_URL` to the backend's URL so browsers call the API directly (add the frontend URL to the backend's `CORS_ORIGINS`).
- MCP on Cloud Run: keep IAM enabled. Clients send the Cloud Run identity token in `X-Serverless-Authorization` and their own TestLookup access token in `Authorization`.
- Known issue: `cloudbuild.backend.yaml` and `cloudbuild.frontend.yaml` pass the repository root (`.`) as the build context, but the Dockerfiles expect `backend/` and `frontend/`. Change the final argument to `backend` or `frontend` before running them.
