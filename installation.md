# TestLookup Installation and Deployment Guide

This guide reflects the current platform architecture and deployment options:

1. Local development with Docker Compose
2. Single-VM deployment on Google Compute Engine (cost-aware)
3. Cloud Run + Cloud SQL deployment on GCP
4. Kubernetes/OpenShift deployment with environment overlays
5. Multi-cloud Kubernetes path (AWS/GCP/Azure/private)

---

## 1) Current deployment model (quick map)

- **Local/dev:** `docker compose` with optional `worker`, `beat`, `ollama`, `chromadb`
- **Kubernetes:** `k8s/overlays/dev`, `k8s/overlays/staging`, `k8s/overlays/prod`
- **OpenShift:** `k8s/overlays/openshift` (Route based exposure)
- **Managed cloud path:** `docker-compose.gcp-vm.yml` and `.github/workflows/deploy-gke.yml` for GCP
- **CI/CD:** GitHub Actions and Jenkins pipeline in `Jenkinsfile`

---

## 2) What is realistic on Google Cloud free tier

The current stack in `docker-compose.yml` includes multiple stateful services and AI components. On an Always Free VM, running everything at once is usually not practical.

Recommended baseline for testing:

- Run: `postgres`, `mongo`, `redis`, `minio`, `backend`, `frontend`
- Add only when needed: `worker`, `beat`, `flower`
- Add for AI assistant integration: `mcp` (lightweight — 256MB RAM, no DB access)
- Keep disabled on free-tier VM by default: `ollama`, `chromadb`
- Prefer cloud LLM API for testing (`gemini` or `openai`) instead of local Ollama

---

## 3) GCP prerequisites

- A Google Cloud account with billing enabled
- Docker knowledge and Git access to this repo
- Domain name optional (recommended later for HTTPS)

Set budget alerts before deploying:

- 50%, 90%, and 100% thresholds
- Email notifications enabled

---

## 4) Compute Engine VM deployment (cost-aware)

### 3.1 Create project and VM

Use Cloud Shell:

```bash
gcloud projects create testlookup-free --name="TestLookup Free"
gcloud config set project testlookup-free
gcloud services enable compute.googleapis.com
```

Create VM (adjust region/zone if needed):

```bash
gcloud compute instances create testlookup-vm \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-type=pd-standard \
  --boot-disk-size=30GB \
  --tags=testlookup-web
```

Open only required ports:

```bash
gcloud compute firewall-rules create testlookup-allow-web \
  --allow=tcp:22,tcp:80,tcp:8000 \
  --target-tags=testlookup-web \
  --source-ranges=0.0.0.0/0
```

> The packaged MCP port is bound to VM loopback. Use the SSH tunnel below; do
> not expose bearer-authenticated MCP over a public plaintext port.

### 3.2 Install Docker + Compose on VM

```bash
gcloud compute ssh testlookup-vm --zone=us-central1-a
```

Inside VM:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```

### 4.3 Deploy this repository with GCP compose override

```bash
git clone https://github.com/yourorg/testlookup.git
cd testlookup
cp .env.gcp-vm.example .env
```

Edit `.env` — set real application secrets and the VM public IP for `VITE_API_BASE_URL`, then start:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec backend alembic upgrade head
```

Validate:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml ps
curl http://localhost:8000/health
```

Get VM public IP:

```bash
curl ifconfig.me
```

Open:

- `http://<VM_IP>` — frontend dashboard
- `http://<VM_IP>:8000/docs` — backend API docs
- `http://127.0.0.1:8002/sse` through an SSH tunnel — MCP SSE endpoint

### 4.4 Connect your AI Assistant to the VM-hosted MCP

On your local machine, create an encrypted tunnel and leave it running:

```bash
gcloud compute ssh testlookup-vm --zone=<ZONE> -- -N -L 8002:127.0.0.1:8002
```

Then add a per-client TestLookup access token to your MCP configuration:

```json
{
  "mcpServers": {
    "testlookup": {
      "url": "http://127.0.0.1:8002/sse",
      "headers": {
        "Authorization": "Bearer ${TESTLOOKUP_ACCESS_TOKEN}"
      }
    }
  }
}
```

Or use stdio mode (runs locally, connects to the remote backend):

```json
{
  "mcpServers": {
    "testlookup": {
      "command": "python",
      "args": ["/path/to/testlookup/mcp/server.py"],
      "env": {
        "TESTLOOKUP_API_URL": "http://<VM_IP>:8000",
        "TESTLOOKUP_USERNAME": "your-user",
        "TESTLOOKUP_PASSWORD": "your-pass"
      }
    }
  }
}
```

### 4.5 Optional: enable async workers later

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async up -d worker beat
```

### 4.6 Optional: enable local AI later (not free-tier friendly)

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile ai up -d ollama chromadb
```

---

## 5) Cloud Run + Cloud SQL deployment path (cleaner internet access)

This path gives cleaner public access for frontend/backend/MCP while moving PostgreSQL to managed Cloud SQL.

Important: Cloud Run cannot host stateful local services like MongoDB/MinIO/Redis for production-style usage. For a cleaner setup:

- PostgreSQL: Cloud SQL (managed)
- MongoDB: MongoDB Atlas free/shared tier
- Redis: Memorystore (paid) or external Redis provider for test usage
- S3-compatible object storage: Cloud Storage S3 interoperability endpoint or external S3-compatible provider
- MCP Server: Cloud Run service (SSE transport with Cloud Run IAM plus caller bearer authentication)

Use `docker-compose.gcp-vm.yml` with the runbook in `deploymentsteps.md` for full steps.

---

## 6) Kubernetes and OpenShift deployment path

Use Kustomize overlays for environment-specific rollout:

```bash
# Kubernetes
kubectl apply -k k8s/overlays/dev
kubectl apply -k k8s/overlays/staging
kubectl apply -k k8s/overlays/prod

# OpenShift-compatible overlay (includes Route resources)
kubectl apply -k k8s/overlays/openshift
```

Async runtime checks:

```bash
make k8s-rollout-async-dev
make k8s-rollout-async-staging
make k8s-rollout-async-prod
```

Notes:

- `worker` and `beat` are deployed as first-class workloads.
- Production overlay includes worker HPA for queue-driven scaling.
- OpenShift overlay removes fixed backend UID and uses Route exposure.

---

## 7) Security baseline

- Use strong random values for `APP_SECRET_KEY` and `JWT_SECRET_KEY`
- Require each remote MCP client to send its own TestLookup bearer token and terminate TLS before the MCP service
- Restrict firewall to `22`, `80`, `443` once stable; keep MCP port `8002` private
- Do not expose database ports publicly
- Rotate API keys and secrets periodically
- On Cloud Run, keep IAM enabled. Send the Cloud Run identity token in
  `X-Serverless-Authorization` and the caller's TestLookup token in
  `Authorization`; configure `TESTLOOKUP_MCP_ALLOWED_HOSTS` with the service host

---

## 8) Cost control checklist

- Create billing budget alerts
- Stop VM when not in use:

```bash
gcloud compute instances stop testlookup-vm --zone=us-central1-a
```

- Start VM only when needed:

```bash
gcloud compute instances start testlookup-vm --zone=us-central1-a
```

- Remove unused images/volumes periodically on VM:

```bash
docker system prune -af
```

- MCP server has minimal resource usage (256MB RAM, no persistent storage) — safe to leave running.

---

## 9) Quick troubleshooting

- Backend unhealthy:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml logs -f backend
```

- DB migration issues:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec backend alembic current
```

- Frontend cannot reach backend: ensure `VITE_API_BASE_URL` in `.env` points to your public backend URL and rebuild frontend:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d --build frontend
```

- MCP server not responding:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml logs -f mcp
# Check that TESTLOOKUP_API_URL inside the container can reach the backend
docker compose exec mcp python -c "import httpx; import asyncio; print(asyncio.run(httpx.AsyncClient().get('http://backend:8000/health')))"
```

- MCP authentication errors: obtain a TestLookup access token for the agent's own user and send it as `Authorization: Bearer <token>` on the SSE connection and message requests.

---

## 10) Multi-cloud deployment references

- `deploymentsteps.md` - beginner-friendly GCP VM runbook
- `k8s/overlays/` - AWS EKS / GCP GKE / Azure AKS / OpenShift / homelab Kustomize overlays
- `.github/workflows/deploy-*.yml` - the deployment pipelines those overlays are driven by
- `Jenkinsfile` and `jenkins/` - Jenkins CI/CD reference
- `README.md` - architecture and system diagram
