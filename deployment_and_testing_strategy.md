# TestLookup: Deployment and Testing Strategy

This document defines a practical release strategy across local development, Kubernetes/OpenShift, and cloud platforms while preserving one application architecture.

---

## 1) Strategy goals

- Keep one application codebase and one container set (`backend`, `frontend`, `mcp`).
- Enable environment-specific runtime behavior through overlays and env vars.
- Validate async workloads (`worker`, `beat`) explicitly in all non-local targets.
- Support public cloud and private cloud without forking manifests.

---

## 2) Deployment targets

1. **Local Docker Compose** - full-stack developer environment.
2. **GCP VM + Compose override** - low-cost internet-accessible team environment.
3. **Cloud Run + Cloud SQL (GCP)** - managed stateless app services.
4. **Kubernetes overlays** - `dev`, `staging`, `prod`.
5. **OpenShift overlay** - Route-based exposure and SCC-friendly security context.

---

## 3) Local machine deployment and testing

### 3.1 Deploy

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

### 3.2 Validate

```bash
docker compose ps
curl http://localhost:8000/health
```

### 3.3 Test sequence

```bash
make test-backend
make test-frontend
make test-e2e
```

Gate to pass before promoting:

- API health endpoint returns healthy.
- Authentication flow works from UI.
- At least one ingestion scenario completes end-to-end.

---

## 4) Kubernetes and OpenShift deployment strategy

### 4.1 Environment overlays

- `k8s/overlays/dev`: low resource, `beat=0`, `worker=1`
- `k8s/overlays/staging`: pre-prod validation, `beat=1`, `worker=1`
- `k8s/overlays/prod`: production profile, `beat=1`, `worker=3` + worker HPA
- `k8s/overlays/openshift`: Route resources and OpenShift security-context patch

### 4.2 Deploy commands

```bash
kubectl apply -k k8s/overlays/dev
kubectl apply -k k8s/overlays/staging
kubectl apply -k k8s/overlays/prod
kubectl apply -k k8s/overlays/openshift
```

### 4.3 Async rollout gates

```bash
make k8s-rollout-async-dev
make k8s-rollout-async-staging
make k8s-rollout-async-prod
```

Release gate to pass before promoting overlay:

- backend rollout healthy
- worker/beat rollout healthy
- migrations applied
- `/health` and UI smoke tests pass

---

## 5) GCP VM deployment strategy (cost-aware)

Use for team-shared dev/test where managed services are not yet required.

Deploy:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec backend alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async up -d worker beat
```

Validation:

```bash
docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml ps
curl http://localhost:8000/health
```

---

## 6) Cloud Run + Cloud SQL strategy (managed path)

Use for cleaner internet exposure and managed PostgreSQL.

- Deploy `backend`, `frontend`, and optional `mcp` as Cloud Run services.
- Use Cloud SQL for PostgreSQL.
- Use managed/external MongoDB, Redis, and object storage.
- Run Celery as Cloud Run Jobs or a separate worker platform if async load is required.

See `docs/cloud-run-cloud-sql.md` for full operational steps.

---

## 7) Multi-cloud strategy (AWS/GCP/Azure/private)

- **Runtime standard:** Kubernetes manifests and overlays.
- **Registry:** ECR/Artifact Registry/ACR as cloud-specific implementations.
- **Secrets:** cloud-native secret managers or Vault with External Secrets.
- **Data tier:** managed Postgres/Redis/Mongo-compatible services per platform.
- **Ingress:** Ingress controller on Kubernetes; Route on OpenShift.

---

## 8) Promotion and rollback model

Promotion order:

1. Local verification
2. Dev overlay
3. Staging overlay
4. Prod overlay

Rollback policy:

- revert to last known-good image tags
- re-apply previous overlay revision
- re-run smoke checks (`/health`, auth, ingestion sample)

---

## 9) Operational checks per deployment

- **Service health:** backend `/health`, UI reachable, MCP SSE (if enabled)
- **Data path:** DB migration version current, ingestion write/read sanity
- **Async path:** worker queue consumption, beat periodic task execution
- **Security:** no plaintext secrets in docs or manifests, only placeholders
- **Observability:** logs available for backend/worker/beat and deployment events
