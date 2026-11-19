# Upgrading TestLookup

How to move an existing install to a newer version, and what each version
changes that you may have to act on. Release mechanics for maintainers are in
[RELEASING.md](RELEASING.md); every change is listed in
[CHANGELOG.md](CHANGELOG.md).

## The supported path

Upgrade one release line at a time (for example 0.1.x to 0.2.x), always to the
latest patch release of the target line.

1. **Read this file's section for every version you cross.**
2. **Back up first.** Compose: `make backup` (PostgreSQL, MongoDB and the object
   store into `backups/`; see
   [user-guide/administration.md](user-guide/administration.md)). Kubernetes:
   run the backup CronJob on demand
   ([k8s/components/backup/README.md](k8s/components/backup/README.md)).
3. **Check readiness.** Compose: `make preflight`.
4. **Upgrade.** Compose from a clone: `git pull` then `make upgrade` (it runs
   `alembic upgrade head` before restarting the application). Compose release
   images: set `TESTLOOKUP_VERSION=vX.Y.Z` in `.env` and
   `docker compose -f docker-compose.release.yml up -d` (the `db-migrate`
   service migrates first). Kubernetes: `scripts/deploy-k8s.sh` or the overlay
   of your choice (the migration Job runs before the rollout; see
   [docs/operations/deployment.md](docs/operations/deployment.md)). Single-node
   K3s: `./k3ssetup/deploy-k3s.sh --skip-registry --skip-models`.
5. **Verify.** `GET /health/ready` is `ok` and `GET /health/version` reports
   the new commit.

Database migrations only move forward in production. To roll back, restore the
backup taken in step 2 together with the previous version's images.

## After 0.1.0 (the next release)

Security fixes in this release change what some roles may do and what some API
calls answer. Nothing in the database changes and there is no migration.

### Project roles decide writes together with the instance role

A user has a role on the instance and a role in each project. A write into a
project now needs the route's role in both; before, the instance role and any
membership were enough. An instance ADMIN is not affected, and reads are
unchanged. A member whose project role is lower than their instance role gets
403 "Requires at least <role> role for this project" on actions their project
role does not cover.

Before upgrading, list those members and raise the project role where needed:

```sql
SELECT u.username, u.role AS instance_role, p.slug, m.role AS project_role
FROM project_members m
JOIN users u ON u.id = m.user_id
JOIN projects p ON p.id = m.project_id
WHERE u.role <> 'ADMIN' AND m.role <> u.role;
```

People added through the app normally have the same role in both places: a
project's creator and service accounts get their instance role, and an invited
member gets the role chosen in the invitation.

### Actions that now need a role

| Action | Needs | Before |
|---|---|---|
| Create or edit a test plan, add or remove items, record an execution; edit a test strategy | TESTER | any member |
| Set a suite-review verdict; start AI plan, strategy or coverage generation; run the duplicate-case sweep; recover a live run | QA_ENGINEER | any member |
| Change a strategy's status; create a public share link; read a webhook's delivery history | QA_LEAD | any member |
| Queue the training export or a fine-tune; probe the deployment-wide integrations | ADMIN | QA_LEAD |

Members below QA_LEAD see a webhook's target URL as `https://host/...`.

### Sessions

- Access tokens last 30 minutes by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`,
  was 720). A value set in your `.env` or manifests stays as it is; the 0.1.0
  `.env.example` set 720, so check it. `expires_in` in token responses follows
  the setting.
- The web app, the CLI and the MCP server renew on their own. The Python, JS
  and Go SDKs do not refresh a password sign-in: a run longer than the token
  lifetime should use an API key.
- A refresh token used again more than 10 seconds after it was rotated
  (`JWT_REFRESH_REUSE_LEEWAY_SECONDS`) signs the user out of every device.
- The SSE and WebSocket live streams refuse a logged-out token, a refresh
  token and an account on a temporary password, and an SSE stream ends when
  its token expires. Clients reconnect with a fresh access token.
- A live ingestion session ends 24 hours after it was opened, and at once when
  its API key is revoked or its owner is deactivated or removed from the
  project. A run longer than 24 hours opens a new session.

### API answers that changed

| Call | Now | Before |
|---|---|---|
| Sign-in on a locked account, wrong password | 429 with `Retry-After` | 401 |
| `POST /auth/change-password`, `/auth/mfa/disable`, `/auth/mfa/recovery-codes` after repeated wrong guesses | 429 | 400 without limit |
| `POST /auth/first-time-reset`, `/auth/change-password`, `/auth/logout` with an API key | 403 | accepted |
| Create a user, register or accept an invitation with a username containing other than letters, digits, `.`, `_`, `-` | 422 | accepted |
| Gate evaluate with an unknown phase id | 404 | 200 "no such phase" |
| Link a run, or evaluate a gate, with a phase of another release | 404 | accepted |
| `GET .../ownership/resolve/{cluster_id}` when the project has no such cluster | 404 | answered from another project's cluster |
| `POST /search/reindex?project_id=` as QA_LEAD | 403 | queued an instance-wide reindex |
| `GET /test-management/cases/ai-task/{task_id}` for a task that is not yours | 404 | the task's result |
| `GET /webhooks/{id}/deliveries?limit=` above 200 | 422 | accepted |
| Create an `uploaded_document` knowledge source with a key outside the project | 422 | accepted |
| `GET /ai-eval/runs/{id}` for another project's run, as QA_LEAD | 404 | 200 |
| A second project's `run_start` on `/ws/events` with a run id another project uses (project-bound keys) | 202 | 409 |
| MCP `create_share_link` without `confirm=true` | returns a notice, creates nothing | created the link |

Existing usernames that contain `@` keep working for sign-in.

### Development starts need real secrets

With `APP_ENV=development` (the default when the variable is unset) the API
refuses to start while `JWT_SECRET_KEY` is the placeholder from the source.
`scripts/gen-dev-env.sh` writes generated secrets; `ALLOW_INSECURE_DEV_SECRETS=true`
starts anyway and is only for a machine nobody else can reach. Production and
staging already refused placeholder secrets.

## 0.1.0 — the first public release

0.1.0 is the first tagged release. If you run a snapshot built from the
repository before this release (any commit before the `v0.1.0` tag), these
changes apply.

### Redis → Valkey 8

The bundled broker and cache is now **Valkey 8.1** (BSD-3-Clause) instead of
`redis:7-alpine`. Settings (`REDIS_*`, `CELERY_*`) and `redis://` URLs are
unchanged.

Valkey cannot load the data files written by Redis 7.4 or later (Valkey
reads Redis 2.x–7.2 data), and `redis:7-alpine` was 7.4 running with
append-only persistence, so Valkey would refuse to start on the old volume.
TestLookup keeps working state in Redis — Celery queues and results, live-run
buffers (drained to PostgreSQL every 30 seconds), live session tokens, the
dead-letter queue, the token-revocation list, leases, rate limits and caches;
the durable records live in PostgreSQL and MongoDB. Before switching:

1. Stop new work: pause CI uploads and let live runs finish.
2. Let the workers drain (`celery -A app.worker.celery_app inspect active` and
   `inspect reserved` are empty) and wait at least 30 seconds after the last
   live run.
3. Export anything you want from the dead-letter queue
   (`GET /api/v1/admin/maintenance/dlq`).
4. Stop the stack and **remove the old Redis data volume** (Compose:
   `docker volume rm <project>_redis_data`; Kubernetes: the `redis-data` PVC)
   so Valkey starts empty.
5. Start the new version.

Starting empty means: access tokens revoked by a logout or a password change
are accepted again until they expire (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`,
720 by default in 0.1.0) — rotate `JWT_SECRET_KEY` during the upgrade to force everyone to
sign in again if that matters; a promoted fine-tuned model falls back to
`LLM_MODEL` until it is promoted again; the next search-index pass re-scans from
the start; live sessions that were open must reconnect.

To keep the old server instead (its license is then your choice), pin it:
Compose — a `docker-compose.override.yml` for the `redis` service that sets
`image:`, `command:` (`redis-server ...`) and the healthcheck (`redis-cli
ping`), because the new Compose files call `valkey-server` / `valkey-cli`;
single-node K3s — a site patch that sets the `testlookup-redis` container image
back (its command and probes still use the `redis-*` names); OpenShift with
Artifactory — the image plus the command and probes.

### MinIO-compatible server → versitygw

The bundled S3-compatible store is now **versitygw** (Apache-2.0) instead of
the AGPL-licensed `pgsty/silo` server and `pgsty/mc` client. `MINIO_*`
settings and the `minio` / `testlookup-minio` service names are unchanged.
versitygw stores objects as plain files under the `s3/` sub-directory of the
same volume, so it never reads or modifies the old server's files, and it
starts **empty**. Copy your objects across once:

Compose:

1. `docker compose up -d` with the new version (versitygw starts, and the
   setup service creates the empty buckets).
2. Run the old server once more on the old data, on the same network:

   ```bash
   docker run -d --name legacy-s3 --network <project>_testlookup_net \
     -v <project>_minio_data:/data \
     -e MINIO_ROOT_USER=<MINIO_ACCESS_KEY> -e MINIO_ROOT_PASSWORD=<MINIO_SECRET_KEY> \
     <the image you ran before> server /data
   ```

   (PGSTY Silo images name the binary `silo`: `... silo server /data`.)
3. Copy every object across with the backend image's setup script:

   ```bash
   docker compose run --rm \
     -e S3_COPY_FROM_ACCESS_KEY=<MINIO_ACCESS_KEY> -e S3_COPY_FROM_SECRET_KEY=<MINIO_SECRET_KEY> \
     minio-setup python scripts/s3_setup.py --copy-from-endpoint http://legacy-s3:9000
   ```

   It skips objects that are already present with the same content, so it is
   safe to re-run; a failed object makes it exit non-zero.
4. `docker rm -f legacy-s3`. Once you have checked the new store, everything
   on the volume except `s3/` can be deleted.

Kubernetes: run the same copy from a one-off Pod on the node that holds the
`minio-data` PVC, with the old server and the backend image
(`python /app/scripts/s3_setup.py --copy-from-endpoint http://127.0.0.1:9000`,
credentials from `testlookup-secrets`) as two containers.

Kubernetes / K3s installs that cannot migrate yet can pin the previous server
with a site patch on the `testlookup-minio` Deployment (see
[docs/operations/deploy-k3s.md](docs/operations/deploy-k3s.md#site-patches)).
The legacy bucket-notification webhook (`make setup-minio`) is not configured
for versitygw: the default ingest path never needed it.

### MongoDB in the offline bundle

The air-gapped offline bundle no longer contains the MongoDB image (it is
licensed SSPL-1.0). Mirror `mongo:7` into your registry yourself before running
`import-bundle.sh`; see
[user-guide/air-gapped-install.md](user-guide/air-gapped-install.md).

### Chroma's data volume

`chromadb/chroma` 1.x keeps its data in `/data` (`persist_path` in the image's
`/config.yaml`); the Chroma 0.x settings `IS_PERSISTENT` /
`PERSIST_DIRECTORY` are ignored. The manifests mounted the volume at
`/chroma/chroma`, so a 1.x server wrote its collections into the container
and lost them whenever the container was recreated.

The volume is now mounted at `/data`: Compose uses a new named volume,
`chroma_data_v1`, and Kubernetes mounts the same claim on a fresh `chroma-1x`
sub-path. Nothing is deleted: the old `chroma_data` volume, and the 0.x files
at the claim's root, stay as they were. Remove them once you no longer need
them.

Chroma holds derived data only, so after upgrading:

- **Semantic search:** reindex each project with
  `POST /api/v1/search/reindex?project_id=<id>&full=true` (QA lead or admin
  token).
- **Knowledge sources:** sync each source again
  (`POST /api/v1/knowledge-sources/<id>/sync`).
- **Analysis and semantic caches** refill as they are used.
- **Duplicate and defect indexes and agent memory** repopulate as new runs,
  defects and investigations arrive.

### Default local model

The default `LLM_MODEL` is now `qwen2.5:7b-instruct` (Apache-2.0) instead of
`qwen2.5:3b-instruct-q5_K_M` (Qwen Research License, non-commercial). If you
never set `LLM_MODEL`, pull the new model before upgrading
(`ollama pull qwen2.5:7b-instruct`, or `make pull-llm`), or set `LLM_MODEL` to
the model you already have. It needs about 5 GB of disk and 6–8 GB of RAM
during inference; see [user-guide/offline-model-pack.md](user-guide/offline-model-pack.md).

### Feature flag `manual_upload`

New installs start with manual upload **on**. An existing install keeps its
stored value; turn it on in **Settings → Feature flags** if you want the Upload
button.

### Fine-tuning switch moved to the admin API

**Settings → AI** no longer shows the **Fine-Tuning Pipeline** toggle.
Fine-tuning itself is unchanged and stays off by default: an administrator
turns it on or off with `PUT /api/v1/settings/ai` and
`{"finetune_enabled": true}` (or `false`), and `GET /api/v1/training/status`
shows the effective value and where it comes from. A value stored with the old
toggle is kept, and saving the page never changes it. See
[docs/operations/fine-tuning.md](docs/operations/fine-tuning.md).

### Admin accounts and deploy scripts

No script creates an admin with a built-in default password any more. On a
fresh install the deploy generates a random password, shows it once and asks
for a change at first sign-in. **An existing admin is never modified** by a
deploy. If you relied on the old default password anywhere (scripts, tests,
bookmarks), change that account's password now.

A release install (`install.sh`) created before 0.1.0 used the local/demo
`.env`: `APP_ENV=development` with passwordless dev-login. Upgrading keeps your
`.env`, so it keeps that behaviour. To switch to the secure defaults, set
`APP_ENV=production` and `DEV_AUTO_LOGIN_ENABLED=false`, add a strong
`INITIAL_ADMIN_PASSWORD`, and restart: the `db-migrate` service creates the
`admin` account if none exists yet.

### Single-node K3s (`k3ssetup/`)

Site-specific values moved out of the repository into a local env file
(`TESTLOOKUP_K3S_ENV`, template `k3ssetup/k3s.env.example`). Before
your next deploy, create that file with at least the host your install serves
(`K3S_HOST`) and any registry address you used; put the AI provider settings
your overlay used to carry into a site patch (`K3S_PATCHES_DIR`). If the file
is missing, the deploy stops before changing anything because the host would
change. The `make k8s-stop-homelab` / `k8s-restart-homelab` targets were
removed (their scripts were never in the repository).

The path itself was renamed from `homelabsetup/` and `k8s/overlays/homelab/`
to `k3ssetup/` and `k8s/overlays/k3s/`: run `./k3ssetup/deploy-k3s.sh` (was
`deploy-homelab.sh`) and `make k8s-deploy-k3s` (was `k8s-deploy-homelab`), and
point any script of your own at the new paths. Nothing else needs to change:

- `TESTLOOKUP_HOMELAB_ENV`, `~/.config/testlookup/homelab.env` and
  `HOMELAB_NODE<n>_PASS` are still read when their new names
  (`TESTLOOKUP_K3S_ENV`, `k3s.env`, `K3S_NODE<n>_PASS`) are not set;
- an existing `homelabsetup/.homelab-credentials` is still used (git leaves
  the ignored file in place), so no second admin password is generated;
- the rendered manifests keep every resource name, so the next deploy
  updates in place (only comments inside two ConfigMaps change).

### Kubernetes, Terraform and dev-login defaults

- **`testlookup-secrets` is no longer rendered** by `k8s/base` (it held placeholders). Create the Secret before `kubectl apply -k`, as the deploy guide and `scripts/deploy-k8s.sh` already required. The Secret in your cluster stays: `kubectl apply -k` deletes nothing it no longer renders unless you apply with `--prune`.
- **Backend and worker egress excludes the cloud metadata service.** If those pods get cloud credentials from it (an EC2 instance role, GKE Workload Identity, an Azure IMDS managed identity), add an egress rule for it to `allow-backend` and `allow-workers` in your overlay; see [network policies](docs/operations/deploy-kubernetes.md#network-policies). IRSA, EKS Pod Identity and Azure Workload Identity are not affected.
- **`public_access_cidrs` is required** by `infra/aws/terraform`: set your office or VPN CIDRs in your tfvars file (Terraform asks for it otherwise). To keep the old, internet-wide endpoint, set `["0.0.0.0/0"]` explicitly.
- **`DEV_AUTO_LOGIN_ENABLED` defaults to `false`.** The development stacks write `true` to `.env`, so `make dev`, `make dev-lite` and Codespaces are unchanged. If you start the API another way and use Quick Login, set `DEV_AUTO_LOGIN_ENABLED=true` with `APP_ENV=development`.
- **The dev-lite MongoDB requires a password** (`MONGO_USER`/`MONGO_PASSWORD` from `.env`, as in `docker-compose.yml`). MongoDB creates the user only on an empty volume, so remove the old MongoDB volume once, which deletes the local test logs (PostgreSQL's volume stays): `make dev-lite-stop`, `docker volume rm testlookup_lite_mongo_data`, then `make dev-lite`. An `.env` from before `MONGO_PASSWORD` existed needs it added (`scripts/gen-dev-env.sh` writes it for a new `.env`).

### Go reporter module path

The Go reporter's module path is `github.com/test-intelligence/testlookup/client/go`; it was `github.com/testlookup/testlookup-go`, which nothing served, so every user had a `replace` directive pointing at a clone. Rename the path in your `go.mod` (`require` and `replace`) and in your imports:

```go
import "github.com/test-intelligence/testlookup/client/go/testlookup"
```

You can drop the `replace` directive and run `go get github.com/test-intelligence/testlookup/client/go@main` (or `@vX.Y.Z` once `client/go/vX.Y.Z` is tagged); keep it if you build from a clone or from your server's SDK Downloads ZIP.

### Install locations

`install.sh` and `docker-compose.release.yml` now use
`github.com/test-intelligence/testlookup` and
`ghcr.io/test-intelligence/testlookup`. An install created with the old
namespace keeps working only while those images exist; set
`TESTLOOKUP_IMAGE=ghcr.io/test-intelligence/testlookup` in `.env`.
