# Deploying TestLookup on K3s Homelab

> **Prerequisite:** Steps 1–7 of `k3s_setup.md` are complete (skipping step 6b — Longhorn is NOT used).
> You have a 3-node K3s cluster with MetalLB (192.168.0.200–220), **local-path** storage (K3s built-in default), cert-manager, Traefik ingress, and Helm installed.
>
> **Storage:** This deployment uses K3s's built-in `local-path` StorageClass for all persistent volumes. Data is stored directly on the node where the pod runs. This is fine for a homelab but means data is lost if the node's disk fails.

---

## Architecture on Homelab

```
Dev PC (kubectl + Rancher Desktop)
    │
    ▼  http://testlookup.local  →  Traefik (K3s built-in)  →  MetalLB VIP .200
    │
┌───┴──────────────────────────────────────────────────────────────────┐
│  K3s Cluster — Namespace: testlookup                                 │
│                                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐  │
│  │  Frontend    │  │   Backend    │  │  Celery Workers (4 queues) │  │
│  │  (nginx)     │  │  (FastAPI)   │  │  + Beat scheduler          │  │
│  └──────┬──────┘  └──────┬───────┘  └────────────┬───────────────┘  │
│         │                │                        │                  │
│  ┌──────┴────────────────┴────────────────────────┴───────────────┐  │
│  │  PostgreSQL 16 │ MongoDB 7 │ Redis 7 │ MinIO │ ChromaDB │Ollama│  │
│  │  (local-path)  │(local-p.) │(in-mem) │(l-p) │ (l-p)   │(l-p) │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  Node 1 (.101)         Node 2 (.102)         Node 3 (.103)          │
│  control-plane+worker  worker                worker                 │
└──────────────────────────────────────────────────────────────────────┘
```

**Resource budget (~87 GB RAM across 3 nodes):**

| Component | Pods | Memory (req/lim) |
|-----------|------|------------------|
| Backend + Frontend | 2 | 576Mi / 2.3Gi |
| Workers (4) + Beat | 5 | 1.8Gi / 10Gi |
| Ollama | 1 | 2Gi / 16Gi |
| PostgreSQL | 1 | 512Mi / 2Gi |
| MongoDB | 1 | 512Mi / 2Gi |
| Redis | 1 | 256Mi / 768Mi |
| MinIO | 1 | 256Mi / 1Gi |
| ChromaDB | 1 | 256Mi / 1Gi |
| MCP Server | 1 | 128Mi / 256Mi |
| **Total requests** | **14** | **~6.3 Gi** |

Plenty of headroom on 87 GB.

---

## Automated Deployment

A script is provided to automate the entire deployment:

```bash
# From the testlookup repo root on your dev machine (Git Bash):
./homelabsetup/deploy-homelab.sh

# Or via Make:
make k8s-deploy-homelab

# Subsequent deploys (skip registry + models):
make k8s-deploy-homelab-update
```

The script handles all steps below (registry, images, namespace, secrets, Kustomize, MinIO bucket, Ollama models, admin user, DNS). You can also run individual steps manually as described below.

**Flags:**
- `--skip-registry` — Skip registry deployment (already running)
- `--skip-build` — Skip Docker image builds (images already pushed)
- `--skip-models` — Skip Ollama model pull (already downloaded)
- `--skip-dns` — Skip DNS/hosts file reminder
- `--teardown` — Delete the deployment (keeps PVCs and data)
- `--teardown-all` — Delete namespace + all data (DESTRUCTIVE)

---

## Step 0 — Prerequisites on the Dev Machine

### 0a. Hosts file entries

Add to `C:\Windows\System32\drivers\etc\hosts` (run editor as Admin):

```
192.168.0.101 registry.local k8s-node1
192.168.0.102 k8s-node2
192.168.0.103 k8s-node3
192.168.0.200 testlookup.local
```

> **Note:** `testlookup.local` points to the MetalLB VIP (192.168.0.200), NOT a node IP. The Traefik ingress uses Host-based routing on this VIP.

### 0b. Configure Rancher Desktop for insecure registry

Rancher Desktop uses provisioning scripts (not Docker Desktop's daemon.json UI).

Create/edit the provisioning script:

**Windows path:** `%LOCALAPPDATA%\rancher-desktop\provisioning\insecure-registry.start`

```sh
#!/bin/sh
# Configure Docker daemon to trust our local HTTP registry
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "insecure-registries": ["registry.local:30500", "192.168.0.101:30500"]
}
EOF
```

> **Important:** The old approach of appending `DOCKER_OPTS` to `/etc/conf.d/docker` does NOT work with Rancher Desktop. You must write `/etc/docker/daemon.json` directly.

**Restart Rancher Desktop** for the script to take effect:

```powershell
rdctl shutdown
# Then reopen Rancher Desktop from the Start menu
```

Verify the insecure registry is configured:

```bash
docker info | grep -A5 "Insecure"
# Should show: registry.local:30500 and 192.168.0.101:30500
```

### 0c. Verify kubectl access

```bash
kubectl get nodes
# Should show 3 nodes, all Ready
```

---

## Step 1 — Set Up a Local Container Registry

K3s uses containerd (not Docker), so we deploy a container registry as a Kubernetes pod with a NodePort service.

### 1a. Deploy the registry inside K3s

```bash
# Create a persistent directory on node1 for registry storage
ssh labadmin@192.168.0.101 "sudo mkdir -p /opt/registry"

# Apply the registry deployment from your dev machine
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: registry
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: registry-pv
spec:
  capacity:
    storage: 20Gi
  accessModes: [ReadWriteOnce]
  hostPath:
    path: /opt/registry
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: ["k8s-node1"]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: registry-data
  namespace: registry
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ""
  volumeName: registry-pv
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: registry
  namespace: registry
spec:
  replicas: 1
  selector:
    matchLabels:
      app: registry
  template:
    metadata:
      labels:
        app: registry
    spec:
      nodeSelector:
        kubernetes.io/hostname: k8s-node1
      containers:
        - name: registry
          image: registry:2
          ports:
            - containerPort: 5000
          volumeMounts:
            - name: data
              mountPath: /var/lib/registry
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: registry-data
---
apiVersion: v1
kind: Service
metadata:
  name: registry
  namespace: registry
spec:
  type: NodePort
  selector:
    app: registry
  ports:
    - port: 5000
      targetPort: 5000
      nodePort: 30500
EOF

# Wait for the registry pod to start
kubectl -n registry wait --for=condition=ready pod -l app=registry --timeout=60s
```

The registry is now accessible at `http://<any-node-ip>:30500`.

### 1b. Configure K3s to trust the local registry (ALL nodes)

> **Critical:** This must be done on **every** node (node1, node2, node3). If any node is missing the mirror config, pods scheduled on that node will get `ErrImagePull` errors trying to pull from `registry.local:5000`.

SSH into **each node** and run:

```bash
sudo mkdir -p /etc/rancher/k3s

sudo tee /etc/rancher/k3s/registries.yaml <<'EOF'
mirrors:
  "registry.local:5000":
    endpoint:
      - "http://192.168.0.101:30500"
EOF
```

Then restart the K3s service:

```bash
# On node1 (control-plane):
sudo systemctl restart k3s

# On node2 and node3 (agents):
sudo /usr/local/bin/k3s-killall.sh
sudo systemctl start k3s-agent
# Wait 15 seconds, then verify:
sudo systemctl status k3s-agent
```

> **Known issue:** Restarting `k3s-agent` can fail with `bind: address already in use` on ports 10248/10250 if old processes are still running. Run `k3s-killall.sh` first to clean up stale processes, then `systemctl start k3s-agent`.

After restarting all nodes, verify from your dev machine:

```bash
kubectl get nodes
# All 3 nodes should be Ready
```

Also open firewall ports for MetalLB speaker communication (needed for VIP ARP announcements):

```bash
# On EACH node:
sudo ufw allow 7946/tcp
sudo ufw allow 7946/udp
```

**How the registry mirror works:** The Kustomize manifests reference images as `registry.local:5000/testlookup/*`. When K3s containerd resolves that mirror name, it redirects to `http://192.168.0.101:30500`. Your dev machine pushes directly to `registry.local:30500` (or `192.168.0.101:30500`). Both paths reach the same registry — the image repository name (`testlookup/backend`, etc.) is the same regardless of the host:port used.

### 1c. Verify the registry

```bash
curl http://registry.local:30500/v2/_catalog
# Should return: {"repositories":[]}
```

---

## Step 2 — Build and Push Container Images

On your **dev machine** (where the testlookup repo is cloned and Rancher Desktop is running):

```bash
cd /path/to/testlookup_new

# Build production images
docker build -t registry.local:30500/testlookup/backend:latest \
  --target production -f backend/Dockerfile backend/

docker build -t registry.local:30500/testlookup/frontend:latest \
  --target production -f frontend/Dockerfile frontend/

docker build -t registry.local:30500/testlookup/mcp:latest \
  -f mcp/Dockerfile mcp/

# Push to local registry
docker push registry.local:30500/testlookup/backend:latest
docker push registry.local:30500/testlookup/frontend:latest
docker push registry.local:30500/testlookup/mcp:latest
```

> **If push fails with `http: server gave HTTP response to HTTPS client`:** The insecure-registries config is not applied. Re-check step 0b and restart Rancher Desktop.

Verify:

```bash
curl http://registry.local:30500/v2/_catalog
# Should return: {"repositories":["testlookup/backend","testlookup/frontend","testlookup/mcp"]}
```

---

## Step 3 — Create the Namespace

```bash
kubectl create namespace testlookup
```

---

## Step 4 — Generate and Apply Secrets

Generate strong random values and create the secrets. **Do not commit these values.**

The secrets are created imperatively via `kubectl create secret` (not via the Kustomize base `secrets.yaml` template, which contains placeholders only). The homelab Kustomize overlay excludes the base secrets template.

```bash
# Generate random values
APP_SECRET=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
PG_PASSWORD=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
MINIO_ACCESS="testlookup_minio"
MINIO_SECRET=$(openssl rand -base64 16 | tr -d '=/+' | head -c 24)
WEBHOOK_SECRET=$(openssl rand -hex 32)
MCP_USER="mcp_service"
MCP_PASS=$(openssl rand -base64 12 | tr -d '=/+' | head -c 16)

DATABASE_URL="postgresql+asyncpg://testlookup_user:${PG_PASSWORD}@testlookup-postgres:5432/testlookup"
MONGO_URI="mongodb://testlookup-mongo:27017"

# Create the secret (kubectl encodes to base64 automatically with --from-literal)
kubectl -n testlookup create secret generic testlookup-secrets \
  --from-literal=APP_SECRET_KEY="${APP_SECRET}" \
  --from-literal=JWT_SECRET_KEY="${JWT_SECRET}" \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --from-literal=POSTGRES_PASSWORD="${PG_PASSWORD}" \
  --from-literal=MINIO_ACCESS_KEY="${MINIO_ACCESS}" \
  --from-literal=MINIO_SECRET_KEY="${MINIO_SECRET}" \
  --from-literal=MONGO_URI="${MONGO_URI}" \
  --from-literal=WEBHOOK_SECRET="${WEBHOOK_SECRET}" \
  --from-literal=MCP_USERNAME="${MCP_USER}" \
  --from-literal=MCP_PASSWORD="${MCP_PASS}"

# IMPORTANT: Save these credentials — you'll need them later
echo "============================================"
echo "PostgreSQL password: ${PG_PASSWORD}"
echo "MinIO credentials:   ${MINIO_ACCESS} / ${MINIO_SECRET}"
echo "MCP credentials:     ${MCP_USER} / ${MCP_PASS}"
echo "============================================"
```

---

## Step 5 — Deploy with Kustomize

From the testlookup repo root on your dev machine:

```bash
# Preview what will be applied
kubectl kustomize k8s/overlays/homelab

# Apply everything
kubectl apply -k k8s/overlays/homelab
```

This deploys all 14 pods: infrastructure (PostgreSQL, MongoDB, Redis, MinIO, ChromaDB) + application (backend, frontend, 4 workers, beat, Ollama, MCP).

**What the homelab overlay patches (vs. the base manifests):**

| Patch | Why |
|-------|-----|
| Deletes base nginx Ingress | Replaced by Traefik IngressRoute |
| Deletes base secrets template | Real secrets created imperatively in step 4 |
| Sets all deployments to 1 replica | Homelab resource constraints |
| Caps HPAs at min=1, max=2 | Prevent over-scaling on limited hardware |
| Ollama PVC → `local-path` StorageClass | K3s built-in (base uses `standard`) |
| Ollama memory: 2Gi req / 16Gi limit | Smaller models for homelab |
| Frontend: `runAsNonRoot: false` | nginx:alpine runs as root |
| Frontend: mounts K8s nginx config | Removes `proxy_pass` to `backend` (Traefik handles API routing) |
| Beat: `celerybeat-schedule` → `/tmp/` | Writable by non-root uid 1000 |
| ConfigMap: `DEV_AUTO_LOGIN_ENABLED=false` | Backend refuses to start in production with this enabled |
| ConfigMap: `APP_ENV=production` | Enables security checks |
| Images → `registry.local:5000/*` | K3s containerd mirrors to local registry |

**Storage note:** All PVCs use the `local-path` StorageClass (K3s built-in). The local-path provisioner creates directories under `/opt/local-path-provisioner/` on the node where the pod is scheduled. Data persists across pod restarts but is tied to that specific node.

---

## Step 6 — Wait for Infrastructure to Start

Infrastructure pods must be ready before the backend can connect.

```bash
# Watch all pods
kubectl -n testlookup get pods -w

# Wait for databases to become ready (takes 30-90 seconds)
kubectl -n testlookup wait --for=condition=ready pod -l app=testlookup-postgres --timeout=120s
kubectl -n testlookup wait --for=condition=ready pod -l app=testlookup-mongo --timeout=120s
kubectl -n testlookup wait --for=condition=ready pod -l app=testlookup-redis --timeout=120s
kubectl -n testlookup wait --for=condition=ready pod -l app=testlookup-minio --timeout=120s
```

The backend pod will retry `alembic upgrade head` every 5 seconds until PostgreSQL is ready — this is expected behavior (see the startup probe with 12 retries).

---

## Step 7 — Create the MinIO Buckets

The backend expects a `test-telemetry` bucket. Create it once:

```bash
# Port-forward MinIO API
kubectl -n testlookup port-forward svc/testlookup-minio 9000:9000 &
PF_PID=$!

# Wait for port-forward to be ready
sleep 3

# Install mc (MinIO client) if not already present
# On Windows (Git Bash): curl -O https://dl.min.io/client/mc/release/windows-amd64/mc.exe
# On Ubuntu: wget https://dl.min.io/client/mc/release/linux-amd64/mc && chmod +x mc && sudo mv mc /usr/local/bin/

# Configure and create bucket (use the MINIO_SECRET from step 4)
mc alias set homelab http://localhost:9000 testlookup_minio <MINIO_SECRET_FROM_STEP_4>
mc mb homelab/test-telemetry --ignore-existing
mc mb homelab/knowledge-docs --ignore-existing

# Stop the port-forward
kill $PF_PID 2>/dev/null
```

---

## Step 8 — Pull Ollama Models

```bash
# Exec into the Ollama pod
kubectl -n testlookup exec -it deployment/testlookup-ollama -- bash

# Pull the default models (inside the pod)
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# Verify
ollama list

# Exit
exit
```

This downloads ~5 GB of model data into the local-path persistent volume. It survives pod restarts.

---

## Step 9 — Configure DNS on Your Dev Machine

> If you already added `testlookup.local` to your hosts file in step 0a, skip this step.

Add to your `hosts` file so `testlookup.local` resolves to the Traefik MetalLB VIP.

First, find the Traefik external IP:

```bash
kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
# Should return: 192.168.0.200
```

On **Windows** (run as Admin PowerShell):

```powershell
Add-Content C:\Windows\System32\drivers\etc\hosts "192.168.0.200 testlookup.local"
ipconfig /flushdns
```

On **Linux/Mac**:

```bash
echo "192.168.0.200 testlookup.local" | sudo tee -a /etc/hosts
```

> **Browser note:** Some browsers (especially Chrome) may auto-upgrade `.local` domains to HTTPS. If the page doesn't load, try:
> 1. Use `http://` explicitly in the URL bar
> 2. Try in an incognito/private window
> 3. Try Microsoft Edge instead of Chrome
> 4. Clear HSTS cache: `chrome://net-internals/#hsts` → delete `testlookup.local`

---

## Step 10 — Create the Initial Admin User

In production mode there is no dev-login bypass and no seed data. The self-registration
endpoint (`POST /api/v1/auth/register`) creates users with `role=VIEWER` only. You need
to bootstrap the first admin user via a one-shot script.

```bash
kubectl -n testlookup exec -it deployment/testlookup-backend -- \
    python /app/scripts/create_admin.py
```

This auto-generates a strong password and prints it to the console:

```
  ==================================================
  Admin user created successfully!
  ==================================================
    username : admin
    email    : admin@testlookup.local
    password : <random-16-char-password>
    role     : ADMIN
  ==================================================
  ** Password was auto-generated. Save it now — it cannot be retrieved later. **
  ==================================================
```

**Save the password immediately.** You can then log in at `http://testlookup.local` with username `admin` and the generated password.

To customize the admin credentials, pass environment variables:

```bash
kubectl -n testlookup exec -it deployment/testlookup-backend -- \
    env ADMIN_EMAIL=you@example.com \
        ADMIN_USERNAME=myadmin \
        ADMIN_PASSWORD=MyStr0ngP@ss! \
    python /app/scripts/create_admin.py
```

The script is idempotent — running it again when the user already exists prints the existing user info without changes.

### After first login

1. Log in with the admin credentials
2. Go to **Settings > AI Configuration** to verify the analysis mode
3. Go to **Projects** to create your first project
4. Additional users can self-register (they get VIEWER role) — promote them via **Users** page

---

## Step 11 — Verify the Deployment

### Check all pods are running

```bash
kubectl -n testlookup get pods
```

Expected output (all Running, 1/1 Ready):

```
NAME                                          READY   STATUS    RESTARTS
testlookup-postgres-xxxx                      1/1     Running   0
testlookup-mongo-xxxx                         1/1     Running   0
testlookup-redis-xxxx                         1/1     Running   0
testlookup-minio-xxxx                         1/1     Running   0
testlookup-chromadb-xxxx                      1/1     Running   0
testlookup-ollama-xxxx                        1/1     Running   0
testlookup-backend-xxxx                       1/1     Running   0
testlookup-frontend-xxxx                      1/1     Running   0
testlookup-worker-critical-xxxx               1/1     Running   0
testlookup-worker-ingestion-xxxx              1/1     Running   0
testlookup-worker-ai-xxxx                     1/1     Running   0
testlookup-worker-default-xxxx                1/1     Running   0
testlookup-beat-xxxx                          1/1     Running   0
testlookup-mcp-xxxx                           1/1     Running   0
```

### Test endpoints

```bash
# Health check (use Traefik IP with Host header for reliable test)
curl -H "Host: testlookup.local" http://192.168.0.200/health/live
# -> {"status":"alive",...}

# API docs
curl -s -H "Host: testlookup.local" http://192.168.0.200/docs | head -5
# -> Should return HTML (Swagger UI)

# Frontend
curl -s -H "Host: testlookup.local" http://192.168.0.200/ | head -5
# -> Should return HTML (React SPA)
```

### Open in browser

Navigate to **http://testlookup.local** — you should see the TestLookup login page.

---

## Updating the Application

When you make code changes and want to redeploy:

```bash
# 1. Rebuild and push images
docker build -t registry.local:30500/testlookup/backend:latest \
  --target production -f backend/Dockerfile backend/
docker push registry.local:30500/testlookup/backend:latest

docker build -t registry.local:30500/testlookup/frontend:latest \
  --target production -f frontend/Dockerfile frontend/
docker push registry.local:30500/testlookup/frontend:latest

# 2. Restart deployments to pull new images
kubectl -n testlookup rollout restart deployment/testlookup-backend
kubectl -n testlookup rollout restart deployment/testlookup-frontend
kubectl -n testlookup rollout restart deployment/testlookup-worker-critical
kubectl -n testlookup rollout restart deployment/testlookup-worker-ingestion
kubectl -n testlookup rollout restart deployment/testlookup-worker-ai
kubectl -n testlookup rollout restart deployment/testlookup-worker-default
kubectl -n testlookup rollout restart deployment/testlookup-beat

# 3. Watch the rollout
kubectl -n testlookup rollout status deployment/testlookup-backend
```

Or use the shortcut:

```bash
make k8s-deploy-homelab-update
```

---

## Troubleshooting

> **Real-world gotchas from the 2026-04-25 deploy session — read this first.**
> Three issues hit during a fresh-cluster bring-up that aren't obvious from the manifests:
>
> 1. **NetworkPolicy blocks colocated data stores.** The base `default-deny-all` policy in `k8s/base/networkpolicy.yaml` denies all ingress to every pod in `testlookup`. The base file's own comment flags this — it assumes data stores live OUTSIDE the namespace. The homelab overlay colocates them, so backend → postgres connections fail with `Connection refused` to the postgres ClusterIP. **Fix**: this overlay now ships `netpol-homelab.yaml` (`allow-data-stores` + `allow-traefik-ingress`). If you see backend `ConnectionRefusedError` to a `10.43.x.x:5432`-shaped IP, verify `kubectl -n testlookup get networkpolicy` shows `allow-data-stores`.
>
> 2. **Frontend nginx needs `runAsUser: 0` AND default capabilities.** `nginx:alpine`'s entrypoint envsubst writes to `/etc/nginx/conf.d/`, which UID 101 can't write. The homelab overlay sets `runAsUser: 0`, but you also can't `drop: [ALL]` capabilities — nginx master needs `CHOWN`/`SETUID`/`SETGID` to fork workers. The overlay also mounts a pre-rendered `frontend-nginx-configmap.yaml` over the nginx config and bypasses `/docker-entrypoint.sh` via `command: [nginx, "-g", "daemon off;"]`. If you see `chown(...) Operation not permitted` or `mkdir(...) Permission denied`, check that `kubectl -n testlookup get deployment testlookup-frontend -o yaml | grep -A20 securityContext` shows no `capabilities.drop`.
>
> 3. **Rancher Desktop's insecure-registry config keeps disappearing.** The `%LOCALAPPDATA%\rancher-desktop\provisioning\insecure-registry.start` script approach is fragile (factory resets / version upgrades wipe it). Reliable workaround when `docker push` fails with `http: server gave HTTP response to HTTPS client`: skip the registry, sideload images directly into containerd on each node:
>     ```bash
>     docker save registry.local:5000/testlookup/backend:$TAG -o /tmp/img.tar
>     for node in 192.168.0.101 192.168.0.102 192.168.0.103; do
>       scp /tmp/img.tar labadmin@$node:/tmp/
>       ssh labadmin@$node "sudo k3s ctr images import /tmp/img.tar"
>     done
>     ```
>     With `imagePullPolicy: IfNotPresent` and a non-`:latest` tag, K3s uses the local image without trying the registry.
>
> **K3s `:latest` cache trap.** When you overwrite a `:latest` image in the registry, neither `kubectl rollout restart` nor `imagePullPolicy: Always` reliably pulls the new content on K3s — the local containerd cache wins. Either `crictl rmi` on every node, or (much better) tag with the git SHA: `kubectl set image deployment/testlookup-backend backend=registry.local:5000/testlookup/backend:$(git rev-parse --short HEAD)`.

### Backend pod in CrashLoopBackOff

```bash
# Check logs — usually a DB connection or migration issue
kubectl -n testlookup logs deployment/testlookup-backend --tail=50

# Common causes:
# 1. PostgreSQL not ready yet -> wait, the startup probe retries
# 2. Wrong DATABASE_URL in secret -> delete and recreate the secret
# 3. Migration error -> exec into pod and run: alembic upgrade head
# 4. "DEV_AUTO_LOGIN_ENABLED is True in production" -> ensure the homelab
#    overlay sets DEV_AUTO_LOGIN_ENABLED=false in the configmap patch
```

### ErrImagePull / ImagePullBackOff

```bash
# Check which node the pod is on
kubectl -n testlookup describe pod <pod-name> | grep "Node:"

# Check if that node has registries.yaml configured
ssh labadmin@<node-ip> "cat /etc/rancher/k3s/registries.yaml"

# If missing, add it (see step 1b) and restart k3s-agent:
sudo /usr/local/bin/k3s-killall.sh
sudo systemctl start k3s-agent
```

### Frontend CrashLoopBackOff with `host not found in upstream "backend"`

The nginx.conf baked into the Docker image has `proxy_pass http://backend:8000` which works in Docker Compose but fails in K8s (the service name is `testlookup-backend`). The homelab overlay mounts a K8s-specific nginx config that removes the proxy block (Traefik handles API routing). If you see this error, the ConfigMap mount may not be applied:

```bash
kubectl -n testlookup get configmap | grep nginx
# Should show: frontend-nginx-config-xxxxx

# Force re-apply
kubectl apply -k k8s/overlays/homelab
kubectl -n testlookup rollout restart deployment/testlookup-frontend
```

### Frontend `CreateContainerConfigError` — `runAsNonRoot and image will run as root`

The nginx:alpine image runs as root. The homelab overlay patches `runAsNonRoot: false` for the frontend. If this error appears, the overlay patch may not be applied:

```bash
kubectl -n testlookup get deployment testlookup-frontend -o jsonpath='{.spec.template.spec.containers[0].securityContext}'
# Should show: {"allowPrivilegeEscalation":false,"readOnlyRootFilesystem":false,"runAsNonRoot":false}
```

### Beat `Permission denied: 'celerybeat-schedule'`

The homelab overlay redirects the schedule file to `/tmp/celerybeat-schedule` which is writable by uid 1000. If you see this error, the command patch may not be applied:

```bash
kubectl -n testlookup get deployment testlookup-beat -o jsonpath='{.spec.template.spec.containers[0].command}'
# Should include: -s /tmp/celerybeat-schedule
```

### K3s agent won't restart — `bind: address already in use`

Old kubelet/kube-proxy processes are still holding ports 10248 and 10250:

```bash
sudo /usr/local/bin/k3s-killall.sh
sudo ss -tlnp | grep -E '10248|10250'
# If still bound:
sudo fuser -k 10248/tcp
sudo fuser -k 10250/tcp
sudo systemctl start k3s-agent
```

### MetalLB VIP not reachable (ping says "Destination host unreachable")

```bash
# Check MetalLB speaker communication
kubectl -n metallb-system logs -l component=speaker --tail=20 | grep -i "error\|timeout"
# If you see "Push/Pull with k8s-nodeX failed: i/o timeout":
# Open MetalLB memberlist port on ALL nodes:
ssh labadmin@192.168.0.10X "sudo ufw allow 7946/tcp && sudo ufw allow 7946/udp"

# Restart speakers after fixing firewall
kubectl -n metallb-system rollout restart daemonset/speaker
```

> **Note:** ICMP ping to the MetalLB VIP may not work even when HTTP works fine. This is normal — MetalLB L2 mode with K3s kube-proxy doesn't forward ICMP. Test with `curl` instead of `ping`.

### PVC stuck in Pending (local-path)

```bash
# Verify local-path provisioner is healthy (K3s built-in)
kubectl -n kube-system get pods -l app=local-path-provisioner

# Check PVC events
kubectl -n testlookup describe pvc postgres-data

# local-path only provisions when a pod is scheduled.
# If the PVC is Pending, check that the pod referencing it exists.
```

### Ollama model not loading

```bash
# Check Ollama logs
kubectl -n testlookup logs deployment/testlookup-ollama --tail=20

# Verify the PVC is bound
kubectl -n testlookup get pvc ollama-models-pvc

# If models were lost (PVC recreated), pull again:
kubectl -n testlookup exec -it deployment/testlookup-ollama -- ollama pull qwen2.5:7b
```

### Ingress not routing

```bash
# Check Traefik is running
kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik

# Check ingress resource
kubectl -n testlookup get ingress

# Check Traefik logs for routing errors
kubectl -n kube-system logs -l app.kubernetes.io/name=traefik --tail=20
```

### View resource usage

```bash
# Per-node usage
kubectl top nodes

# Per-pod usage in the namespace
kubectl -n testlookup top pods
```

### Check storage usage

```bash
# List all PVCs and their status
kubectl -n testlookup get pvc

# Check actual disk usage on nodes
ssh labadmin@192.168.0.101 "df -h /opt/local-path-provisioner"
```

---

## Port Forwarding (Direct Access)

For debugging, you can bypass the ingress and access services directly:

```bash
# Backend API (Swagger docs at http://localhost:8000/docs)
kubectl -n testlookup port-forward svc/testlookup-backend 8000:8000

# Frontend (http://localhost:3000)
kubectl -n testlookup port-forward svc/testlookup-frontend 3000:80

# PostgreSQL (connect with psql or DBeaver)
kubectl -n testlookup port-forward svc/testlookup-postgres 5432:5432

# MinIO Console (http://localhost:9001)
kubectl -n testlookup port-forward svc/testlookup-minio 9001:9001

# Ollama API (http://localhost:11434)
kubectl -n testlookup port-forward svc/testlookup-ollama 11434:11434
```

---

## Kustomize Overlay Files Reference

The homelab overlay is at `k8s/overlays/homelab/` and contains:

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Patches, configmap overrides, image rewrites |
| `ingress-traefik.yaml` | Traefik Ingress with Host-based routing for `testlookup.local` |
| `infra-postgres.yaml` | PostgreSQL 16 + PVC (20Gi, local-path) |
| `infra-mongo.yaml` | MongoDB 7 + PVC (20Gi, local-path) |
| `infra-redis.yaml` | Redis 7 broker/cache with AOF, TTL-aware eviction, and a 5Gi PVC |
| `infra-minio.yaml` | MinIO S3 + PVC (50Gi, local-path) |
| `infra-chromadb.yaml` | ChromaDB vector DB + PVC (10Gi, local-path) |
| `nginx-k8s.conf` | K8s-specific nginx config (no proxy_pass — Traefik handles API routing) |

---

## Pause / Resume the Homelab

When the homelab needs to be idle (vacation, power maintenance, debugging
a cold start) but not torn down, use the pair below instead of
`cleanup-homelab.sh`. Both preserve every PVC — Postgres, Mongo, MinIO,
ChromaDB, Ollama models all keep their state across the pause.

```bash
# Graceful pause — drains workloads, then stops K3s on every node.
# Adds an annotation per Deployment so the restart script can scale
# them back to the same replica count.
./homelabsetup/stop-homelab.sh

# Resume — starts K3s (control node first, then workers), waits for
# every node Ready, then scales the workloads back to their pre-pause
# values. Probes /health/details at the end.
./homelabsetup/restart-homelab.sh
```

Useful flags (both scripts):

- `--apps-only` — pause/resume the TestLookup workloads but leave the
  K3s cluster running. Fastest pause/resume; pair the two flags.
- `--dry-run` — print every `kubectl scale` + `ssh` command without
  running it. Use this to preview before the first real run.
- `--no-ssh` — skip the K3s start/stop steps. Use when you'll stop
  or start K3s by hand on each node (the scripts still scale the
  workloads correctly).

`stop-homelab.sh` extras:

- `--skip-scale` — go straight to stopping K3s without draining
  pods. Faster but in-flight DB writes / Celery tasks aren't
  drained cleanly.
- `--drain-timeout N` — seconds to wait for pods to terminate after
  `scale --replicas=0` (default 90).

`restart-homelab.sh` extras:

- `--api-timeout N` (default 180) — how long to wait for the K3s
  API server on the control node to respond after `systemctl start`.
- `--ready-timeout N` (default 240) — how long to wait for every
  node to report `Ready`.
- `--workload-timeout N` (default 240) — how long to wait for
  `kubectl rollout status` on backend + frontend after the scale-up.

The scripts share the same node list (`NODES=`) and SSH user
(`NODE_USER=labadmin`) as `deploy-homelab.sh` and
`cleanup-homelab.sh`, so updating one entry stays consistent.

## Tear Down

`cleanup-homelab.sh` reverses `deploy-homelab.sh` and reclaims disk; for
short pauses use `stop-homelab.sh` / `restart-homelab.sh` (above).

```bash
# Delete the application (keeps PVCs and data)
kubectl delete -k k8s/overlays/homelab

# Delete everything including persistent data (DESTRUCTIVE)
kubectl delete namespace testlookup
```
