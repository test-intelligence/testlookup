# Deploy on managed Kubernetes (EKS, GKE, AKS)

[Documentation home](../README.md) · [Deployment guide](deployment.md) · [Kubernetes guide](deploy-kubernetes.md)

Each managed cloud has an overlay that builds on `k8s/overlays/prod` and swaps in the platform's ingress, storage class and registry. All three are deployed with `scripts/deploy-k8s.sh --cloud=<aws|gcp|azure>`. The base [Secret and ConfigMap keys](deploy-kubernetes.md#configuration), [migrations](deploy-kubernetes.md#migrations-scriptsrun-k8s-migrationssh) and [network policies](deploy-kubernetes.md#network-policies) apply unchanged.

| Cloud | Overlay | Ingress and TLS | Storage class | Images | Infrastructure as code |
|---|---|---|---|---|---|
| AWS | `k8s/overlays/aws-eks` | AWS Load Balancer Controller (ALB), ACM certificate | `gp3` (EBS CSI, created by the overlay) | Amazon ECR | `infra/aws/` (Terraform) |
| Google Cloud | `k8s/overlays/gcp-gke` | GCE Ingress, Google-managed certificate, global static IP | `standard-rwo` | Artifact Registry | none |
| Azure | `k8s/overlays/azure-aks` | NGINX Ingress with cert-manager (AGIC alternative commented in the overlay) | `managed-csi` | Azure Container Registry | none |

None of the overlays deploys the datastores. Use the cloud's managed services, or run them in the cluster with the labels described under [datastores](deploy-kubernetes.md#datastores). Whatever serves as the broker must meet the [Valkey/Redis durability requirements](deploy-openshift.md#external-valkey-or-redis-requirements).

## Run the deploy

Run the script from the repository root, with your kubectl context pointing at the cluster:

```bash
scripts/deploy-k8s.sh --cloud=<aws|gcp|azure> --registry=<registry> --image-tag=<tag>
```

The script stops if the namespace has no `testlookup-secrets` Secret. It then runs the migration Job with the new backend image, applies the overlay and waits for the rollouts. The other flags are in the [Kubernetes guide](deploy-kubernetes.md#deploy-with-scriptsdeploy-k8ssh).

## Deploy from CI

The repository has no workflow that deploys to a cloud. To build one, follow the pattern in `release.yml`:

1. Call the reusable workflow `require-verified-sha.yml` with the full commit SHA. It passes only if exactly one successful `ci.yml` run on a push to `main` exists for that commit. It returns the run ID and the name of that run's `verified-images-<sha>` artifact, the digest manifest CI wrote when it published the images. The artifact expires after 14 days.
2. In a job that `needs` the verifier, download the artifact with `gh run download` and check it with `python3 scripts/release/release_manifest.py validate --expected-source-sha <sha>`.
3. Run `release_manifest.py materialize --destination-prefix <registry>` to write the digest references the cluster will pull. If that registry is not the one CI pushed to, copy each image there by digest first (for example with `crane copy`).
4. Run `scripts/deploy-k8s.sh --cloud=<cloud> --release-manifest=<materialized file>`. With a manifest, the script deploys only those digests and afterwards checks every Deployment's image against it.

Sign in to the cloud with OpenID Connect (`id-token: write`) instead of long-lived keys. To require an approval before each deploy, run the deploy job in a GitHub environment with required reviewers.

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

`validate.sh` needs no AWS account. No CI workflow runs it, so run it yourself after you change `infra/aws/` or the `aws-eks` overlay. The design and cost reasoning are in [`infra/aws/STRATEGY.md`](../../infra/aws/STRATEGY.md); the commands are in [`infra/aws/README.md`](../../infra/aws/README.md).

### Wire the application

1. Copy the Secrets Manager secret into `testlookup-secrets` (External Secrets Operator or a one-off `kubectl create secret`). The Terraform secret carries `DATABASE_URL`, `MONGO_URI`, `REDIS_URL`, `APP_SECRET_KEY`, `JWT_SECRET_KEY` and `WEBHOOK_SECRET`. You still need `POSTGRES_PASSWORD`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`, and a ConfigMap patch that sets `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` to the ElastiCache `rediss://` endpoint (the base ConfigMap points them at the in-cluster service).
2. Object storage: the application reads `MINIO_ENDPOINT`, `MINIO_USE_SSL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET_NAME` and `KNOWLEDGE_DOCS_BUCKET`, and also writes to fixed bucket names (`pipeline-artifacts`, `compliance-packs`, `training-data` by default). Amazon S3 bucket names are global, so those names may be unavailable in your account; the S3 keys in the Terraform secret (`S3_TELEMETRY_BUCKET`, `S3_KNOWLEDGE_BUCKET`) are not read by the application. Until bucket names are fully configurable, the simplest working setup is an S3-compatible store in the cluster (the `minio` Service pattern from the `homelab` overlay), with the Terraform buckets reserved for backups.
3. DocumentDB implements a subset of the MongoDB API. Test your workload against it, or use MongoDB Atlas or a self-managed MongoDB instead.
4. In `k8s/overlays/aws-eks/kustomization.yaml`, replace the ACM certificate ARN (`arn:aws:acm:REGION:ACCOUNT:certificate/REPLACE-ME`, on both Ingresses), the ECR image names (`ACCOUNT.dkr.ecr.REGION.amazonaws.com/...`) and the hosts.
5. Deploy: `scripts/deploy-k8s.sh --cloud=aws --registry=<account>.dkr.ecr.<region>.amazonaws.com --image-tag=<tag>`.

The overlay installs a `gp3` StorageClass marked as the cluster default, uses ALB `target-type: ip` with `/health/live` health checks, keeps MCP on the same ALB group with a one-hour idle timeout, and opens the MCP pods' network policy to the ALB (the ALB security group is the perimeter; MCP still requires a bearer token). With `target-type: ip` the ALB also reaches the frontend and backend pods directly, so add the ALB's source range to `allow-frontend` and `allow-backend` ([network policies](deploy-kubernetes.md#network-policies)).

## Google GKE

1. Reserve the global static IP and point DNS at it before the certificate can provision: `gcloud compute addresses create testlookup-ip --global`.
2. Replace the domains in `k8s/overlays/gcp-gke/managed-cert.yaml` (`testlookup.example.com`, `testlookup-mcp.example.com`) and the MCP host rule in the overlay.
3. Provide the datastores (for example Cloud SQL for PostgreSQL, MongoDB Atlas, Memorystore, and an S3-compatible store) and create `testlookup-secrets`.
4. Replace `REGION-docker.pkg.dev/PROJECT/...` in the overlay's `images:` block, or pass `--registry` to `deploy-k8s.sh`.
5. Deploy with `scripts/deploy-k8s.sh --cloud=gcp ...`.

The overlay switches to the GCE Ingress class with the `testlookup-cert` ManagedCertificate and the `testlookup-ip` address, serves the MCP host from the same Ingress (and deletes the separate MCP Ingress), enables container-native load balancing (`cloud.google.com/neg`) on the backend, frontend and MCP Services, adds a `BackendConfig` with a one-hour timeout and `/health/live` health check for MCP, and sets `TRUSTED_PROXY_IPS` and the MCP network policy to Google's front-end and health-check ranges `35.191.0.0/16` and `130.211.0.0/22`. Because the load balancer reaches the frontend and backend pods directly, add those two ranges to `allow-frontend` and `allow-backend` as well.

## Azure AKS

1. Install the NGINX Ingress Controller and cert-manager with a `letsencrypt-prod` ClusterIssuer (or switch to Application Gateway Ingress Controller with the commented patch in the overlay).
2. Provide the datastores (for example Azure Database for PostgreSQL Flexible Server, a MongoDB-compatible service, Azure Cache for Redis, and an S3-compatible store) and create `testlookup-secrets`.
3. Replace `REGISTRY.azurecr.io` in the overlay's `images:` block and the hosts.
4. Deploy with `scripts/deploy-k8s.sh --cloud=azure ...`.

The overlay keeps the NGINX annotations from the base (120-second proxy timeouts, 100 MB request bodies, TLS redirect) and pins PVCs to `managed-csi`. Azure Blob Storage is not S3-compatible on its own; use an S3-compatible gateway or store.

## Related

- [Kubernetes guide](deploy-kubernetes.md) for the base, secrets, monitoring and backups
- [Sizing](../../user-guide/sizing.md)
- [Startup and upgrade order](deployment.md#startup-and-upgrade-order)
