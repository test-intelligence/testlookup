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
   K3s: `./homelabsetup/deploy-homelab.sh --skip-registry --skip-models`.
5. **Verify.** `GET /health/ready` is `ok` and `GET /health/version` reports
   the new commit.

Database migrations only move forward in production. To roll back, restore the
backup taken in step 2 together with the previous version's images.

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
default 720) — rotate `JWT_SECRET_KEY` during the upgrade to force everyone to
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

### Single-node K3s (`homelabsetup/`)

Site-specific values moved out of the repository into a local env file
(`TESTLOOKUP_HOMELAB_ENV`, template `homelabsetup/homelab.env.example`). Before
your next deploy, create that file with at least the host your install serves
(`K3S_HOST`) and any registry address you used; put the AI provider settings
your overlay used to carry into a site patch (`K3S_PATCHES_DIR`). If the file
is missing, the deploy stops before changing anything because the host would
change. The `make k8s-stop-homelab` / `k8s-restart-homelab` targets were
removed (their scripts were never in the repository).

### Install locations

`install.sh` and `docker-compose.release.yml` now use
`github.com/test-intelligence/testlookup` and
`ghcr.io/test-intelligence/testlookup`. An install created with the old
namespace keeps working only while those images exist; set
`TESTLOOKUP_IMAGE=ghcr.io/test-intelligence/testlookup` in `.env`.
