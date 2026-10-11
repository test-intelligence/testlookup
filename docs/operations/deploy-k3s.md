# Deploy on K3s (single node or more)

This path runs the whole stack — the TestLookup API, workers, web UI and MCP
server plus PostgreSQL, MongoDB, Valkey, the S3-compatible object store,
ChromaDB and Ollama — inside one [K3s](https://k3s.io) cluster. One machine is
enough; more nodes join as K3s agents. It is the smallest Kubernetes install
and the closest one to production shape. For a laptop demo, Compose is simpler
([deploy-compose.md](deploy-compose.md)); for managed clusters see
[deployment.md](deployment.md).

Files involved:

| File | Purpose |
|---|---|
| [`homelabsetup/deploy-homelab.sh`](../../homelabsetup/deploy-homelab.sh) | Builds the three images, pushes them to an in-cluster registry, generates secrets, applies the overlay, migrates, creates the first admin |
| [`homelabsetup/bootstrap-homelab.sh`](../../homelabsetup/bootstrap-homelab.sh) | Optional: creates the K3s cluster over SSH (server + agents, MetalLB, cert-manager, registry mirrors) |
| [`homelabsetup/homelab.env.example`](../../homelabsetup/homelab.env.example) | Every site setting, documented |
| [`k8s/overlays/homelab/`](../../k8s/overlays/homelab/kustomization.yaml) | The overlay: in-cluster datastores on `local-path` storage, Traefik ingress on plain HTTP, single replicas |

## Requirements

- A Linux machine running K3s with its built-in Traefik and `local-path`
  StorageClass. A small team needs about 8 GB RAM and 100 GB of SSD for a
  year of results, plus about 8 GB RAM for the in-cluster 7B model; see
  [sizing](../../user-guide/sizing.md).
- A build machine (it can be the same one) with `kubectl` pointing at the
  cluster, `git`, `curl`, `openssl` and Docker or Podman. On Windows, run the
  scripts from Git Bash.

## 1. Site configuration

Nothing about your network is built into the scripts. Copy the template
outside the repository and edit it:

```bash
mkdir -p ~/.config/testlookup
cp homelabsetup/homelab.env.example ~/.config/testlookup/homelab.env
```

The scripts read `~/.config/testlookup/homelab.env` (or
`$XDG_CONFIG_HOME/testlookup/homelab.env`) when it exists, or the file named by
`TESTLOOKUP_HOMELAB_ENV`. The file is parsed as `KEY=VALUE` lines, never
executed, and a variable already set in your environment wins over it. With no
file at all the defaults apply:

| Variable | Default | Meaning |
|---|---|---|
| `K3S_HOST` | `testlookup.localhost` | Host name the ingress answers on. `*.localhost` resolves to the loopback address, so the default works in a browser on the node itself |
| `K3S_REGISTRY` | `registry.local:30500` | Registry the nodes pull TestLookup images from |
| `K3S_REGISTRY_FALLBACK` | (none) | Second address of the same registry, tried when the name does not resolve on the build machine |
| `K3S_PATCHES_DIR` | (none) | Directory of extra kustomize patches applied on every deploy |
| `K3S_CREDENTIALS_FILE` | `homelabsetup/.homelab-credentials` | Where a first install writes generated secrets and the admin login (mode 600, gitignored) |
| `K3S_LLM_MODEL` / `K3S_EMBEDDING_MODEL` | `qwen2.5:7b-instruct` / `nomic-embed-text:v1.5` | Models pulled into Ollama |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin` / `admin@example.com` / generated | First admin account, created on a fresh install only |

`bootstrap-homelab.sh` additionally needs `K3S_NODES` (`name=ip` pairs, the
first one runs the K3s server), `K3S_NODE_USER` and, unless you pass
`--skip-prereqs`, `K3S_LB_RANGE` for MetalLB. See the template for all keys.

## 2. The cluster

**One node, installed by hand** (the shortest path):

```bash
# on the node
curl -sfL https://get.k3s.io | sh -
sudo mkdir -p /etc/rancher/k3s
sudo tee /etc/rancher/k3s/registries.yaml >/dev/null <<'YAML'
mirrors:
  "registry.local:30500":
    endpoint:
      - "http://127.0.0.1:30500"
YAML
echo "127.0.0.1 registry.local" | sudo tee -a /etc/hosts
sudo systemctl restart k3s
```

Copy `/etc/rancher/k3s/k3s.yaml` to the build machine (replace `127.0.0.1`
with the node's address) and point `KUBECONFIG` at it. The build machine must
resolve `registry.local` to the node and trust `registry.local:30500` as an
insecure registry (Docker: `insecure-registries` in `daemon.json`; Podman:
`registries.conf`, or set `K3S_REGISTRY_FALLBACK=<node-ip>:30500`).

**Several nodes, over SSH:** fill in `K3S_NODES`, `K3S_NODE_USER` and
`K3S_LB_RANGE`, then

```bash
./homelabsetup/bootstrap-homelab.sh            # cluster only
./homelabsetup/bootstrap-homelab.sh --deploy   # cluster + TestLookup
```

It installs a pinned K3s release (`K3S_VERSION` overrides it), joins the
agents, installs MetalLB and cert-manager, writes the registry mirror on every
node and saves a kubeconfig (`K3S_KUBECONFIG_OUT`). It is idempotent; re-run it
after adding a node.

## 3. Deploy

```bash
./homelabsetup/deploy-homelab.sh                              # first install
./homelabsetup/deploy-homelab.sh --skip-registry --skip-models   # every update
```

| Step | What happens |
|---|---|
| 0 | Preflight: cluster reachable, `local-path` present, site config loaded. **On an existing install the deploy refuses to change the ingress host** (so a missing config file cannot move your install); `ALLOW_HOST_CHANGE=true` moves it on purpose |
| 1 | In-cluster registry (`registry:2` on NodePort 30500, data on one node). Skipped with `--skip-registry` |
| 2 | Build the backend, frontend and MCP images from a clean checkout (the script refuses untracked or modified build inputs), tag them `build-<UTC time>`, push. `--skip-build` reuses the newest tag present for all three |
| 3-4 | Namespace and secrets. On a first install every secret is random (`openssl rand`), printed once and saved to `K3S_CREDENTIALS_FILE`; an existing `testlookup-secrets` is never regenerated |
| 5 | Render a per-deploy overlay in `homelabsetup/.render/` (the tracked overlay plus your host, registry, image tag and `K3S_PATCHES_DIR` patches), run database migrations with the new image, apply, wait for every pod and check that each one runs the digest just pushed and that `/health/version` reports this commit |
| 6-7 | Wait for the datastores; create the object-store buckets from inside the backend pod (`python /app/scripts/s3_setup.py`) |
| 8 | Pull the models into Ollama. Skipped with `--skip-models` |
| 9 | Print the Traefik address and the hosts-file line, if your host name needs one |
| 10 | Create the first admin account **only if none exists**. The password is `ADMIN_PASSWORD` or a random one, shown once and saved to the credentials file. An existing admin is never modified |
| 11 | Report pod health; the script exits non-zero when any new pod is unhealthy |

Open `http://<K3S_HOST>` and sign in with the admin login from step 10. The
ingress is plain HTTP; terminate TLS in front of Traefik, or add a certificate
and the HTTPS redirect middleware in `k8s/overlays/homelab/` with a site patch,
if the cluster is reachable from an untrusted network.

## Site patches

`K3S_PATCHES_DIR` holds strategic-merge patches applied on top of the overlay
on every deploy, in file-name order. Each names the `kind`, `metadata.name` and
`metadata.namespace` of what it changes. Use them for anything site-specific
instead of `kubectl patch`, which the next deploy would silently revert. For
example, a hosted LLM provider instead of the in-cluster Ollama:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: testlookup-config
  namespace: testlookup
data:
  AI_OFFLINE_MODE: "false"
  LLM_PROVIDER: "openrouter"
  LLM_MODEL: "<provider/model>"
  AI_LLM_PROVIDER_ALLOWLIST: "ollama,openrouter"
  AI_LLM_ALLOWED_BASE_URLS: "http://testlookup-ollama:11434,https://openrouter.ai"
```

(the API key goes into the `testlookup-secrets` Secret, not into a patch), or
pinning a datastore image an existing install was created with — see
[UPGRADING.md](../../UPGRADING.md) for the Redis to Valkey and object-store
changes.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Every TestLookup pod is `ImagePullBackOff` | The node's `/etc/rancher/k3s/registries.yaml` has no mirror for `K3S_REGISTRY` (the deploy checks this before applying and prints the fix), or the name does not resolve on the node |
| `docker push` fails with "server gave HTTP response to HTTPS client" | The build machine's engine does not trust the registry as insecure, or it resolved a different name than the one it trusts |
| The deploy stops with "already serves TestLookup on host ..." | Your site config did not load, or names another host. Fix `TESTLOOKUP_HOMELAB_ENV`, or set `ALLOW_HOST_CHANGE=true` |
| New backend pods crash with `InvalidPasswordError` | The deploy converges the Postgres role password to the Secret on every run; if the password contains characters that need SQL quoting, run `ALTER USER` by hand |
| "Deployment DEGRADED" and exit 1 | A new pod failed while the previous ReplicaSet kept serving; read the named pod's logs |
| The browser keeps redirecting to `https://` | Clear the site's HSTS entry; the ingress is HTTP-only |

Teardown: `--teardown` deletes the application and keeps the PersistentVolumeClaims;
`--teardown-all` deletes the namespace and all data after a typed confirmation.
`bootstrap-homelab.sh --teardown` uninstalls K3s from every configured node.
