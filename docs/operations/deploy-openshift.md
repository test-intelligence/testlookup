# Deploy on OpenShift

[Documentation home](../README.md) · [Deployment guide](deployment.md) · [Kubernetes guide](deploy-kubernetes.md)

Two overlays target OpenShift:

| Overlay | Datastores | Images | Use it when |
|---|---|---|---|
| `k8s/overlays/openshift` | Provided by you (PostgreSQL, MongoDB, Valkey/Redis, S3-compatible store) | Any registry the cluster can pull from | A connected OpenShift cluster with platform-managed data services |
| `k8s/overlays/openshift-artifactory` | Run in the cluster (PostgreSQL, MongoDB, Valkey, object store, ChromaDB) | Every image, application and third-party, from one Artifactory | An air-gapped cluster that can reach only one internal registry |

Both build on [`k8s/base`](deploy-kubernetes.md#what-the-base-deploys), so the Secret keys, ConfigMap keys, migration Job and monitoring bundle described on the Kubernetes page apply here too. Use `oc` or `kubectl`; the scripts accept `KCLI=oc`.

## What the OpenShift overlays change

- **Routes instead of Ingress.** The base Ingress objects are removed and three Routes are added, all with edge TLS termination and an HSTS header: `testlookup` (frontend), `testlookup-api` (backend) and `testlookup-mcp` (MCP, one-hour timeout for SSE, connection rate limits). The MCP Route redirects HTTP to HTTPS in both overlays; the Artifactory overlay redirects on all three. The standard overlay's example hosts are `testlookup.apps.example.com`, `testlookup-api.apps.example.com` and `testlookup-mcp.apps.example.com`.
- **Security context constraints.** The standard overlay removes the fixed `runAsUser` from the backend, worker and beat Deployments so the `restricted-v2` SCC can assign a UID from the project range, and annotates pods with `openshift.io/scc: restricted-v2` (remove that annotation if your service account is granted `anyuid`). The Artifactory overlay instead expects the `anyuid` SCC for the stock datastore images, which its deploy script grants by default (`GRANT_ANYUID`).
- **Trusted proxies.** `TRUSTED_PROXY_IPS` is `10.128.0.0/14`, the OpenShift SDN/OVN-Kubernetes default cluster network. Replace it with your cluster network: `oc get network.config/cluster -o jsonpath='{.spec.clusterNetwork[*].cidr}'`. Too narrow loses the real client IP; too wide lets a caller forge it.
- **Production defaults.** `APP_ENV=production`, `AI_OFFLINE_MODE=true` (only in-cluster or private model endpoints), and `CORS_ORIGINS` / `TESTLOOKUP_MCP_ALLOWED_HOSTS` set to the Route hosts (example hosts in the standard overlay; the Artifactory script substitutes yours).

## Standard OpenShift overlay

`k8s/overlays/openshift` deploys the application only. It does not deploy PostgreSQL, MongoDB, Valkey/Redis or object storage: provide each one and wire it as described under [datastores](deploy-kubernetes.md#datastores).

1. Edit `k8s/overlays/openshift/route.yaml` hosts, the `CORS_ORIGINS` and `TESTLOOKUP_MCP_ALLOWED_HOSTS` patches, the `TRUSTED_PROXY_IPS` patch and the image tags in `kustomization.yaml`.
2. Create the project and the Secret `testlookup-secrets` ([keys](deploy-kubernetes.md#secret-testlookup-secrets)).
3. Point `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`, plus the database and object-store settings, at your services with a ConfigMap patch, or provide a `testlookup-redis` Service endpoint.
4. Deploy. `make k8s-deploy-openshift` renders the overlay, runs the migration Job (before the apply on an upgrade, after it on a fresh install) and applies it with `kubectl`. With `oc` only: `KCLI=oc scripts/run-k8s-migrations.sh testlookup <backend-image>` then `oc apply -k k8s/overlays/openshift`.
5. Check the Routes: `make k8s-status-openshift`.
6. Create the first admin: `make create-admin-k8s` prints a generated password once.

### External Valkey or Redis requirements

The broker holds more than a cache: Celery tasks, per-session live-event streams with their batch ledger, and keys that have no TTL until a live session closes. The standard OpenShift overlay intentionally ships no broker workload. The cluster operator must provide the `testlookup-redis` endpoint (or point `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` elsewhere) for an authenticated, TLS-protected Valkey or Redis-compatible service, with:

- append-only persistence (or an equivalent managed multi-zone durability guarantee);
- backups and point-in-time recovery;
- a `maxmemory-policy` of `noeviction`, or another policy that cannot evict TTL-less evidence/broker keys (`volatile-lru`, which the shipped in-cluster configurations use, only evicts keys with a TTL);
- a non-clustered endpoint: the live-event admission script touches several keys in one Lua call, so Redis Cluster mode is not supported.

A disposable cache endpoint does not meet this contract: losing it loses queued work and in-flight live evidence. Record the provider's persistence, backup and eviction settings, or the output of `CONFIG GET appendonly appendfsync maxmemory-policy` where `CONFIG` is allowed, before the first production rollout.

## Air-gapped OpenShift with one Artifactory

`k8s/overlays/openshift-artifactory` deploys the complete stack, datastores included, and rewrites every image to a single Artifactory Docker repository, so the cluster never contacts a public registry. It also enables the [backup component](../../k8s/components/backup/README.md). It is driven by `openshiftsetup/deploy-openshift-artifactory.sh`, which runs on a build host that can reach both the internet (or a mirror) and the Artifactory.

The committed overlay contains `*_PLACEHOLDER` tokens (registry, tag, storage class, Route hosts). The script substitutes them in place, applies, and restores them afterwards. Applying the overlay directly fails at image pull on purpose.

### Prerequisites on the build host

- `docker`, `oc` (or `kubectl`; Route certificate injection and the `anyuid` grant need `oc`), `curl`, `openssl`.
- `oc login` to the target cluster.
- Push access to the Artifactory repository.

### Configure

```bash
cp openshiftsetup/artifactory.env.example openshiftsetup/artifactory.env
# edit openshiftsetup/artifactory.env, then:
make k8s-deploy-openshift-artifactory
```

Every value can also come from the environment (the environment wins). `ARTIFACTORY_ENV_FILE` points the scripts at another config file. Keep the filled-in file out of version control.

| Variable | Required | Meaning |
|---|---|---|
| `ARTIFACTORY_REGISTRY` | yes | Docker repository base for every image, without scheme or trailing slash, e.g. `artifactory.example.com/testlookup-docker` |
| `ARTIFACTORY_PULL_REGISTRY` | no | Address the cluster pulls from when it differs from the push address (for example a virtual repository); defaults to `ARTIFACTORY_REGISTRY` |
| `ARTIFACTORY_USERNAME`, `ARTIFACTORY_PASSWORD` | no | Used for `docker login` and for the in-cluster pull secret `artifactory-pull` (linked to the default service account). Use an API key or identity token as the password. Without them the script assumes Docker is already logged in and the cluster can pull anonymously |
| `ARTIFACTORY_EMAIL` | no | Email on the pull secret |
| `APPS_DOMAIN` | yes, unless all three hosts are set | The cluster's apps wildcard domain; hosts become `testlookup.`, `testlookup-api.` and `testlookup-mcp.` under it |
| `ROUTE_HOST_FRONTEND`, `ROUTE_HOST_API`, `ROUTE_HOST_MCP` | no | Explicit Route hosts; override `APPS_DOMAIN` |
| `ROUTE_TLS_CERT`, `ROUTE_TLS_KEY`, `ROUTE_TLS_CACERT` | no | PEM files injected into all three Routes. Empty uses the router's default wildcard certificate |
| `NAMESPACE` | no | Project name, default `testlookup` |
| `STORAGE_CLASS` | no | StorageClass for the PVCs; empty auto-detects the cluster default. Pick one whose volumes survive pod replacement |
| `GRANT_ANYUID` | no | `true` grants the `anyuid` SCC to the namespace's default service account so the stock datastore images run with their expected UIDs; set `false` (or pass `--no-anyuid`) on hardened clusters with UID-agnostic images |
| `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_FULL_NAME` | no | The first admin account |
| `ADMIN_PASSWORD` | no | Leave empty: the script generates the password at first install and prints it once. Set it only to choose your own |
| `INFRA_IMAGES` | no | Space-separated third-party images to mirror. Empty derives the list from `deploy/images.manifest.txt`; set it only to mirror a subset |
| `PULL_OLLAMA_MODELS`, `OLLAMA_MODELS` | no | `true` pulls the listed models into the in-cluster Ollama after deploy. This needs a model mirror reachable from the cluster; otherwise load weights from an [offline model pack](../../user-guide/offline-model-pack.md) |
| `APP_TAG` | no | With `--skip-build`, the existing `build-YYYYMMDD-HHMMSS` tag to deploy; empty picks the newest tag present for all three images |

### What the script does

1. Preflight: checks Docker, `curl`, cluster login and the configuration.
2. Builds the backend, frontend and MCP images and pushes them under an immutable `build-YYYYMMDD-HHMMSS` tag.
3. Mirrors the third-party images into the same Artifactory, keeping their repository paths.
4. Creates the project and the `artifactory-pull` image-pull secret, and grants `anyuid` when enabled.
5. Creates `testlookup-secrets` with generated values on first install (an existing Secret is kept) and writes the generated database and object-store credentials to `openshiftsetup/.openshift-credentials`, which Git ignores.
6. Resolves the StorageClass, substitutes the placeholders and runs the migration Job (before the apply on an upgrade, after it on a fresh install), then applies the overlay.
7. Injects your Route certificate when configured, waits for the datastores and backend, creates the storage buckets, creates the first admin, and optionally pulls Ollama models.

| Flag | Effect |
|---|---|
| `--skip-build` | Reuse an existing tag instead of building |
| `--skip-push` | Build but do not push (local smoke test) |
| `--skip-mirror` | Do not mirror third-party images (already present). `make k8s-deploy-openshift-artifactory-update` uses this to redeploy new application images |
| `--no-anyuid` | Do not grant the `anyuid` SCC |
| `--skip-admin` | Do not create the first admin |
| `--dry-run` | Render the manifests and exit |
| `--teardown` | Delete the application, keep PVCs |
| `--teardown-all` | Delete the whole namespace and its data (asks for `yes`) |

### Pre-seed the registry separately

`openshiftsetup/mirror-images.sh` (or `make k8s-mirror-images-openshift`) mirrors only the third-party images, for teams that seed Artifactory ahead of time or refresh images without rebuilding the application. It reads the same `artifactory.env`, requires `ARTIFACTORY_REGISTRY`, derives the image list from `deploy/images.manifest.txt` unless `INFRA_IMAGES` is set, and accepts `--dry-run` to print the plan on a host without Docker.

MongoDB (`mongo:7`) is mirrored from its upstream image like the other datastores; it is licensed SSPL-1.0, so check that this fits your organization before mirroring it.

### When the build host cannot reach both sides

If no host can reach both the internet and the Artifactory, use the [offline bundle](../../user-guide/air-gapped-install.md#6c-openshift) instead: it carries the images across the gap on removable media and renders this same overlay on the far side.

## Related

- [Kubernetes guide](deploy-kubernetes.md): Secret and ConfigMap keys, migrations, network policies, monitoring
- [Backup component](../../k8s/components/backup/README.md)
- [Air-gapped install](../../user-guide/air-gapped-install.md)
