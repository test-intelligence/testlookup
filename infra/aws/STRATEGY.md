# TestLookup — AWS Deployment Strategy

> Status: **infrastructure-as-code skeleton, validated offline (no AWS account
> used).** Provisioning is gated behind explicit confirmation; nothing here runs
> `terraform apply` on its own. See [README.md](./README.md) to run the checks.

## 1. Goals & non-goals

**Goals**
- Run the existing TestLookup stack on AWS with **managed services** for the
  stateful tier and **EKS** for the stateless app/workers — reusing the
  `k8s/overlays/aws-eks` Kustomize overlay already in the repo.
- One reproducible IaC definition, three environments (dev/staging/prod), driven
  by `-var-file`.
- **Validate everything without a real AWS account** — fmt, validate, lint,
  security scan, kustomize render, Dockerfile + shell lint — as a CI gate and a
  local script.

**Non-goals (this iteration)**
- No `terraform apply` from this repo against a real account.
- Ollama / ChromaDB are **not** externalised (no managed AWS equivalent) — they
  remain in-cluster on EBS. GPU node groups for Ollama are a follow-up.
- App-layer GitOps (Argo/Flux) is noted but not wired; deploys use Kustomize +
  `kubectl`/CI for now.

## 2. Target architecture

```
                       Internet
                          │
                   Route 53 / ACM
                          │
                 ┌────────▼────────┐         AWS account (per env)
                 │  ALB (ingress)  │  ← AWS Load Balancer Controller (IRSA)
                 └────────┬────────┘
        ┌──────────────── EKS cluster (private node group, 2-3 AZ) ───────────────┐
        │  frontend (nginx)   backend (FastAPI)   mcp   celery workers ×5   beat   │
        │  ChromaDB (EBS gp3)         Ollama (EBS gp3, optional GPU nodes)         │
        └───────┬─────────────────┬──────────────────────┬───────────────────────┘
                │ 5432            │ 27017                 │ 6379
        ┌───────▼──────┐  ┌───────▼────────┐     ┌────────▼─────────┐
        │ RDS Postgres │  │  DocumentDB    │     │ ElastiCache Redis │
        │ (Multi-AZ)   │  │ (Mongo-compat) │     │ (cluster + TLS)   │
        └──────────────┘  └────────────────┘     └───────────────────┘
                │                                          
        ┌───────▼───────────────────────────────────────────────────┐
        │ S3 (telemetry + knowledge-docs)   Secrets Manager (conn strings) │
        │ ECR (backend/frontend/mcp)        CloudWatch (logs/metrics)      │
        └────────────────────────────────────────────────────────────┘
```

## 3. Service mapping (compose/k8s → AWS)

| TestLookup component | Today (compose/homelab) | AWS target |
|---|---|---|
| frontend, backend, mcp, 5 celery workers, beat | k8s Deployments | **EKS** managed node group (private subnets) |
| PostgreSQL 16 | container | **RDS for PostgreSQL** (gp3, encrypted, Multi-AZ in prod) |
| MongoDB 7 | container | **DocumentDB** (MongoDB-compatible; app uses Motor) |
| Redis 7 (Streams + Celery + cache) | container | **ElastiCache for Redis** (TLS + auth token) |
| MinIO (object store) | container | **S3** (`telemetry`, `knowledge-docs` buckets) |
| ChromaDB (vectors) | container | **in-cluster** on EBS gp3 (no managed equiv) |
| Ollama (LLM) | container | **in-cluster**, optional GPU node group (offline default) |
| Container images | local registry | **ECR** (scan-on-push, immutable tags, lifecycle) |
| Secrets / conn strings | `.env` / k8s Secret | **Secrets Manager** → synced into the cluster |
| Ingress | nginx + Traefik | **ALB** via AWS Load Balancer Controller + **ACM** TLS |
| Logs/metrics/traces | Prometheus/Grafana/Jaeger | CloudWatch (+ keep in-cluster Prom/Grafana; ADOT later) |

The `aws-eks` overlay already expects exactly this: `ingressClassName: alb`, the
`gp3` StorageClass (EBS CSI), ECR image refs, and connection strings supplied via
the `testlookup-secrets` Secret. This IaC produces those inputs.

## 4. Module layout

`terraform/` composes four building blocks (self-contained — only the `aws`,
`random`, `tls` providers, so it validates offline):

| Module | Provisions |
|---|---|
| `network` | VPC, public/private subnets across N AZs, IGW, NAT, route tables, EKS subnet tags |
| `ecr` | One repo per image, scan-on-push, immutable tags, lifecycle expiry |
| `eks` | Cluster, IRSA OIDC provider, managed node group, core add-ons (vpc-cni, coredns, kube-proxy, EBS CSI) |
| `data` | RDS Postgres, DocumentDB, ElastiCache Redis, S3 buckets, data-tier SG, generated creds → Secrets Manager |

## 5. Environments

| | dev | staging | prod |
|---|---|---|---|
| VPC CIDR | 10.40/16 | 10.41/16 | 10.42/16 |
| AZs / NAT | 2 / single | 3 / single | 3 / **per-AZ** |
| Nodes | 2× t3.large | 3× t3.large | 4–10× m5.xlarge |
| RDS | t3.medium, single-AZ | t3.large | r6g.large, **Multi-AZ** |
| DocumentDB | 1 instance | 2 | 3 |
| Redis replicas | 0 | 1 | 2 (failover + Multi-AZ) |

State is isolated per env: S3 key `aws/<env>/terraform.tfstate`, DynamoDB lock.

## 6. Security

- **Network**: nodes + databases in **private** subnets; data-tier security group
  only admits the EKS node SG on 5432/27017/6379; NAT-only egress.
- **Encryption**: RDS/DocDB/Redis/S3/EBS encrypted at rest; Redis + DocumentDB
  TLS in transit; ECR AES256.
- **IAM**: least-privilege cluster/node roles; **IRSA** OIDC provider so
  controllers (ALB, EBS CSI, External Secrets) assume scoped roles — no node-wide
  credentials.
- **Secrets**: generated by Terraform (`random_password`), stored only in
  **Secrets Manager**, never in code or tfvars. Sync into the cluster
  `testlookup-secrets` Secret with the **External Secrets Operator** (recommended)
  or `kubectl create secret ... --from...` in CI. The app already reads these
  exact keys (`DATABASE_URL`, `MONGO_URI`, `REDIS_URL`, `JWT_SECRET_KEY`, …).
- **Account guard**: `allowed_account_ids` blocks an apply into the wrong account.
- **Deletion protection** on RDS + DocumentDB; S3 versioning; 7-day backups.

## 7. CI/CD

1. **Infra PR** → `.github/workflows/aws-infra-validate.yml` runs the offline gate
   (fmt, validate, tflint, tfsec, kustomize render, hadolint, shellcheck). No AWS.
2. **Infra apply** → `plan.sh` (review) → `deploy.sh <env> --confirm` (gated;
   prod requires typed-name match). In CI, use OIDC-assumed roles, never static keys.
3. **App release** → build `backend/frontend/mcp` images (the backend build stages
   client SDKs first), push to **ECR** with an immutable `build-<ts>` tag, then
   `kustomize edit set image` + `kubectl apply -k k8s/overlays/aws-eks`.

## 8. Observability, DR, cost

- **Observability**: EKS control-plane logs → CloudWatch; container logs via
  Fluent Bit; keep the in-cluster Prometheus/Grafana from `infra/monitoring`;
  ADOT → X-Ray for traces is a follow-up.
- **DR**: automated RDS/DocDB snapshots (7-day) + final snapshots on destroy;
  S3 versioning; Redis daily snapshots (5-day). Cross-region replication is a
  prod follow-up.
- **Cost**: dev uses single-NAT + burstable instances + 0 Redis replicas;
  scale-to-need via the node group autoscaler bounds. ECR lifecycle caps image
  sprawl. NAT gateways and Multi-AZ data are the main prod cost drivers.

## 9. Validation without a real AWS account

Every check is static / dry-run / render:

| Check | Tool | Proves |
|---|---|---|
| Formatting | `terraform fmt -check` | canonical style |
| Resolves | `terraform init -backend=false` | providers/modules resolve, no state, no AWS |
| Correctness | `terraform validate` | HCL + provider-schema valid |
| Lint | `tflint` | best-practice/provider rules |
| Security | `tfsec` / `checkov` | encryption, public-access, IAM findings |
| Render | `kustomize build aws-eks` | the k8s overlay composes |
| Image lint | `hadolint` | Dockerfile correctness |
| Script lint | `shellcheck` | deploy scripts |

`scripts/validate.sh` runs them locally (tool-or-Docker, auto-skip if absent);
the CI workflow runs the full set on Linux. **Dev-host note:** a TLS-inspecting
AV (Norton/NordVPN) intercepts terraform's loopback plugin mTLS, so native
`terraform validate` fails locally with *“plugin did not respond”*. Use
`./scripts/validate.sh --tf-docker` (runs terraform in a container, where
loopback isn't intercepted). CI Linux runners are unaffected.

## 10. Follow-ups before prod

- Wire **External Secrets Operator** + the IRSA role for Secrets Manager sync.
- IRSA roles + Helm installs for **AWS Load Balancer Controller**, **EBS CSI**,
  **cluster-autoscaler/Karpenter**, **External DNS**.
- GPU node group + taints/tolerations if Ollama runs in-cluster (else keep offline).
- Pin `public_access_cidrs`; consider a fully private EKS endpoint + bastion/SSM.
- `tfsec`/`checkov` to **hard-fail** in CI once findings are triaged.
