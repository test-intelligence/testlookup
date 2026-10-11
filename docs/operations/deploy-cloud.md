# Deploy on managed Kubernetes (EKS, GKE, AKS)

[Documentation home](../README.md) · [Deployment guide](deployment.md) · [Kubernetes guide](deploy-kubernetes.md)

Each managed cloud has an overlay that builds on `k8s/overlays/prod` and swaps in the platform's ingress, storage class and registry. All three are deployed with `scripts/deploy-k8s.sh --cloud=<aws|gcp|azure>`, by hand or from a GitHub Actions workflow. The base [Secret and ConfigMap keys](deploy-kubernetes.md#configuration), [migrations](deploy-kubernetes.md#migrations-scriptsrun-k8s-migrationssh) and [network policies](deploy-kubernetes.md#network-policies) apply unchanged.

| Cloud | Overlay | Ingress and TLS | Storage class | Images | Infrastructure as code | Workflow |
|---|---|---|---|---|---|---|
| AWS | `k8s/overlays/aws-eks` | AWS Load Balancer Controller (ALB), ACM certificate | `gp3` (EBS CSI, created by the overlay) | ECR names in the overlay; the workflow deploys GHCR digests | `infra/aws/` (Terraform) | `.github/workflows/deploy-eks.yml` |
| Google Cloud | `k8s/overlays/gcp-gke` | GCE Ingress, Google-managed certificate, global static IP | `standard-rwo` | Artifact Registry | none | `.github/workflows/deploy-gke.yml` |
| Azure | `k8s/overlays/azure-aks` | NGINX Ingress with cert-manager (AGIC alternative commented in the overlay) | `managed-csi` | Azure Container Registry | none | `.github/workflows/deploy-aks.yml` |

None of the overlays deploys the datastores. Use the cloud's managed services, or run them in the cluster with the labels described under [datastores](deploy-kubernetes.md#datastores). Whatever serves as the broker must meet the [Valkey/Redis durability requirements](deploy-openshift.md#external-valkey-or-redis-requirements).

## The deploy workflows

`deploy-eks.yml`, `deploy-gke.yml` and `deploy-aks.yml` are manual (`workflow_dispatch`) and take one input, `source_sha`: the full 40-character commit SHA to deploy. Each one:

1. Calls `require-verified-sha.yml`, which requires exactly one successful `ci.yml` run on a push to `main` for that commit, with an unexpired `verified-images-<sha>` artifact (the digest manifest CI produced when it published the images). Commits that never ran CI on `main`, such as fork or pull-request builds, cannot be deployed this way.
2. Runs in the GitHub environment `production`, so you can add required reviewers there.
3. Authenticates to the cloud with OpenID Connect (the workflows request `id-token: write`; no long-lived cloud keys).
4. Validates the manifest, copies the images to the cloud registry by digest where applicable, and runs `scripts/deploy-k8s.sh --cloud=<cloud> --release-manifest=...`, which migrates, deploys only digest-pinned images and verifies every Deployment's image afterwards.

Store these as secrets of the `production` environment (or the repository):

| Workflow | Secrets |
|---|---|
| `deploy-eks.yml` | `AWS_ROLE_TO_ASSUME` (IAM role trusted for GitHub OIDC), `AWS_REGION`, `EKS_CLUSTER_NAME` |
| `deploy-gke.yml` | `GCP_WIF_PROVIDER` (Workload Identity Federation provider resource name), `GCP_SERVICE_ACCOUNT`, `GCP_PROJECT_ID`, `GKE_CLUSTER_NAME`, `GKE_LOCATION`, `GCP_AR_LOCATION` (Artifact Registry region, e.g. `us`), `GCP_AR_REPOSITORY` |
| `deploy-aks.yml` | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (federated credential for GitHub OIDC), `AZURE_RESOURCE_GROUP`, `AKS_CLUSTER_NAME`, `AZURE_ACR_NAME` |

The cluster must already contain the namespace's `testlookup-secrets` Secret; `deploy-k8s.sh` stops if it is missing. There is no separate production or staging workflow: point a workflow at a different cluster by changing its secrets, or run `scripts/deploy-k8s.sh` yourself.

## AWS EKS

### Provision the infrastructure

`infra/aws/` is a Terraform root with four modules and per-environment variables (`environments/dev.tfvars`, `staging.tfvars`, `prod.tfvars`):

| Module | Creates |
|---|---|
| `network` | VPC, subnets across availability zones, NAT |
| `ecr` | ECR repositories for the backend, frontend and MCP images |
| `eks` | EKS cluster and managed node group |
| `data` | RDS for PostgreSQL 16, Amazon DocumentDB, ElastiCache (Redis OSS 7.1, TLS and auth token), two encrypted S3 buckets, and a Secrets Manager secret with the generated credentials |

```bash
./infra/aws/scripts/validate.sh                 # offline gate: fmt, validate, tflint, tfsec, kustomize render, hadolint, shellcheck
./infra/aws/scripts/bootstrap-state.sh          # once: S3 state bucket + DynamoDB lock table
export TF_STATE_BUCKET=<state bucket> TF_LOCK_TABLE=<lock table>
./infra/aws/scripts/plan.sh dev                 # read-only review
./infra/aws/scripts/deploy.sh dev --confirm     # apply; prod also asks you to type "prod"
aws eks update-kubeconfig --region <region> --name <cluster name>
```

`validate.sh` needs no AWS account and is what `.github/workflows/aws-infra-validate.yml` runs on pull requests that touch `infra/aws/**` or the `aws-eks` overlay. The design and cost reasoning are in [`infra/aws/STRATEGY.md`](../../infra/aws/STRATEGY.md); the commands are in [`infra/aws/README.md`](../../infra/aws/README.md).

### Wire the application

1. Copy the Secrets Manager secret into `testlookup-secrets` (External Secrets Operator or a one-off `kubectl create secret`). The Terraform secret carries `DATABASE_URL`, `MONGO_URI`, `REDIS_URL`, `APP_SECRET_KEY`, `JWT_SECRET_KEY` and `WEBHOOK_SECRET`. You still need `POSTGRES_PASSWORD`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`, and a ConfigMap patch that sets `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` to the ElastiCache `rediss://` endpoint (the base ConfigMap points them at the in-cluster service).
2. Object storage: the application reads `MINIO_ENDPOINT`, `MINIO_USE_SSL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET_NAME` and `KNOWLEDGE_DOCS_BUCKET`, and also writes to fixed bucket names (`pipeline-artifacts`, `compliance-packs`, `training-data` by default). Amazon S3 bucket names are global, so those names may be unavailable in your account; the S3 keys in the Terraform secret (`S3_TELEMETRY_BUCKET`, `S3_KNOWLEDGE_BUCKET`) are not read by the application. Until bucket names are fully configurable, the simplest working setup is an S3-compatible store in the cluster (the `minio` Service pattern from the `homelab` overlay), with the Terraform buckets reserved for backups.
3. DocumentDB implements a subset of the MongoDB API. Test your workload against it, or use MongoDB Atlas or a self-managed MongoDB instead.
4. In `k8s/overlays/aws-eks/kustomization.yaml`, replace the ACM certificate ARN (`arn:aws:acm:REGION:ACCOUNT:certificate/REPLACE-ME`, on both Ingresses), the ECR image names (`ACCOUNT.dkr.ecr.REGION.amazonaws.com/...`) and the hosts.
5. Deploy: `scripts/deploy-k8s.sh --cloud=aws --registry=<account>.dkr.ecr.<region>.amazonaws.com --image-tag=<tag>`, or run `deploy-eks.yml`. The workflow deploys the verified digests from `ghcr.io/test-intelligence/testlookup`, so the cluster must be able to pull from GHCR.

The overlay installs a `gp3` StorageClass marked as the cluster default, uses ALB `target-type: ip` with `/health/live` health checks, keeps MCP on the same ALB group with a one-hour idle timeout, and opens the MCP pods' network policy to the ALB (the ALB security group is the perimeter; MCP still requires a bearer token). With `target-type: ip` the ALB also reaches the frontend and backend pods directly, so add the ALB's source range to `allow-frontend` and `allow-backend` ([network policies](deploy-kubernetes.md#network-policies)).

## Google GKE

1. Reserve the global static IP and point DNS at it before the certificate can provision: `gcloud compute addresses create testlookup-ip --global`.
2. Replace the domains in `k8s/overlays/gcp-gke/managed-cert.yaml` (`testlookup.example.com`, `testlookup-mcp.example.com`) and the MCP host rule in the overlay.
3. Provide the datastores (for example Cloud SQL for PostgreSQL, MongoDB Atlas, Memorystore, and an S3-compatible store) and create `testlookup-secrets`.
4. Replace `REGION-docker.pkg.dev/PROJECT/...` in the overlay's `images:` block, or let the workflow materialize the Artifact Registry references.
5. Deploy with `scripts/deploy-k8s.sh --cloud=gcp ...` or run `deploy-gke.yml`.

The overlay switches to the GCE Ingress class with the `testlookup-cert` ManagedCertificate and the `testlookup-ip` address, serves the MCP host from the same Ingress (and deletes the separate MCP Ingress), enables container-native load balancing (`cloud.google.com/neg`) on the backend, frontend and MCP Services, adds a `BackendConfig` with a one-hour timeout and `/health/live` health check for MCP, and sets `TRUSTED_PROXY_IPS` and the MCP network policy to Google's front-end and health-check ranges `35.191.0.0/16` and `130.211.0.0/22`. Because the load balancer reaches the frontend and backend pods directly, add those two ranges to `allow-frontend` and `allow-backend` as well.

## Azure AKS

1. Install the NGINX Ingress Controller and cert-manager with a `letsencrypt-prod` ClusterIssuer (or switch to Application Gateway Ingress Controller with the commented patch in the overlay).
2. Provide the datastores (for example Azure Database for PostgreSQL Flexible Server, a MongoDB-compatible service, Azure Cache for Redis, and an S3-compatible store) and create `testlookup-secrets`.
3. Replace `REGISTRY.azurecr.io` in the overlay's `images:` block and the hosts.
4. Deploy with `scripts/deploy-k8s.sh --cloud=azure ...` or run `deploy-aks.yml`.

The overlay keeps the NGINX annotations from the base (120-second proxy timeouts, 100 MB request bodies, TLS redirect) and pins PVCs to `managed-csi`. Azure Blob Storage is not S3-compatible on its own; use an S3-compatible gateway or store.

## Related

- [Kubernetes guide](deploy-kubernetes.md) for the base, secrets, monitoring and backups
- [Sizing](../../user-guide/sizing.md)
- [Startup and upgrade order](deployment.md#startup-and-upgrade-order)
