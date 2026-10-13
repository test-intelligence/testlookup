# Kubernetes manifests

Kustomize base, overlays, an opt-in monitoring bundle and two components (backups, and the network policy for in-cluster datastores) for TestLookup. Start with the [deployment guide](../docs/operations/deployment.md) to choose a mode.

| Path | What it is | Deploy with | Guide |
|---|---|---|---|
| `base/` | Backend, frontend, MCP, queue workers, beat, Ollama, Services, Ingress, HPAs, PDBs, network policies, ConfigMap and a Secret template. No datastores | Through an overlay | [Kubernetes: what the base deploys](../docs/operations/deploy-kubernetes.md#what-the-base-deploys) |
| `overlays/dev` | Namespace `testlookup-dev`, single replicas, beat off, `dev-latest` tags | `make k8s-deploy-dev` | [Generic overlays](../docs/operations/deploy-kubernetes.md#generic-overlays) |
| `overlays/staging` | Namespace `testlookup-staging`, small HPA ceilings, `staging-latest` tags | `make k8s-deploy-staging` | [Generic overlays](../docs/operations/deploy-kubernetes.md#generic-overlays) |
| `overlays/prod` | Namespace `testlookup`, TLS, production replica floors and HPA ranges | `make k8s-deploy-prod` | [Generic overlays](../docs/operations/deploy-kubernetes.md#generic-overlays) |
| `overlays/self-hosted` | `prod` plus NGINX ingress and on-premise trusted-proxy ranges | `scripts/deploy-k8s.sh --cloud=self-hosted` | [Deploy with deploy-k8s.sh](../docs/operations/deploy-kubernetes.md#deploy-with-scriptsdeploy-k8ssh) |
| `overlays/aws-eks` | `prod` plus ALB ingress, gp3 StorageClass, ECR image names | `scripts/deploy-k8s.sh --cloud=aws` | [AWS EKS](../docs/operations/deploy-cloud.md#aws-eks) |
| `overlays/gcp-gke` | `prod` plus GCE ingress, managed certificate, Artifact Registry image names | `scripts/deploy-k8s.sh --cloud=gcp` | [Google GKE](../docs/operations/deploy-cloud.md#google-gke) |
| `overlays/azure-aks` | `prod` plus NGINX ingress, `managed-csi` StorageClass, ACR image names | `scripts/deploy-k8s.sh --cloud=azure` | [Azure AKS](../docs/operations/deploy-cloud.md#azure-aks) |
| `overlays/openshift` | Routes instead of Ingress, restricted SCC; expects external datastores | `make k8s-deploy-openshift` | [Standard OpenShift](../docs/operations/deploy-openshift.md#standard-openshift-overlay) |
| `overlays/openshift-artifactory` | Complete stack with in-cluster datastores; every image from one Artifactory | `make k8s-deploy-openshift-artifactory` | [Air-gapped OpenShift](../docs/operations/deploy-openshift.md#air-gapped-openshift-with-one-artifactory) |
| `overlays/homelab` | Single-node K3s: in-cluster datastores, Traefik on HTTP, local-path storage | `homelabsetup/deploy-homelab.sh` | [Single-node K3s](../docs/operations/deploy-k3s.md) |
| `monitoring/` | `PodMonitor`s and a `PrometheusRule` for the Prometheus Operator (opt-in) | `kubectl apply -k k8s/monitoring` | [Monitoring](../docs/operations/deploy-kubernetes.md#monitoring-podmonitor-and-prometheusrule) |
| `components/backup/` | Daily backup CronJob, retention and an on-demand restore | Add to an overlay's `components:` | [Backup component](components/backup/README.md) |
| `components/colocated-data-stores/` | Network policy that admits the backend, workers and beat to PostgreSQL, MongoDB, Redis and the object store when an overlay runs them in the namespace (`homelab`, `openshift-artifactory`) | Add to an overlay's `components:` | [Network policies](../docs/operations/deploy-kubernetes.md#network-policies) |

Rules that apply to every overlay:

- Create the `testlookup-secrets` Secret yourself before the first deploy; `base/secrets.yaml` holds placeholders only ([keys](../docs/operations/deploy-kubernetes.md#secret-testlookup-secrets)).
- Run database migrations with the new backend image before the new pods serve traffic: the Make targets and `scripts/deploy-k8s.sh` call `scripts/run-k8s-migrations.sh` for you.
- `:latest` tags are rejected by CI; pin a release tag, commit SHA or digest.
- Every image and tag is listed in `deploy/images.manifest.txt`; `make images-check` fails on drift.
