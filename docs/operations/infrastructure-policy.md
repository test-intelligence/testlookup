# Rendered infrastructure policy

The reference gate checks the effective Compose merge and Kubernetes overlay before image publication. It uses the repository-versioned Python policy in `scripts/security/check_rendered_infrastructure.py` and the existing locked PyYAML dependency. The policy version and both script hashes are retained with the candidate SHA and render hashes. Existing image inventory, secret, dependency and image scans continue to cover their own boundaries.

The gate runs local configuration commands. It creates no containers, images, workloads, provider plans, cluster connections or notifications. The reference renderer substitutes synthetic values, supplies its own interpolation file and uses `--no-env-resolution` for service environment files. It retains a sanitized policy report and discards the rendered configuration and temporary fixture values.

## Selected controls

| Rule | Checked property | Reference basis |
| --- | --- | --- |
| OPS001 | Operational Compose listeners and the API listener that shares `/metrics` bind only to loopback. Operational Kubernetes Services remain private. Ingress/Route targets resolve through Service selectors and named ports. Metrics and pprof routes, their descendants and catch-all backend routes require private access. NetworkPolicy ingress to operational ports requires namespace/pod peers or explicit internal CIDRs. | [Prometheus security model](https://prometheus.io/docs/operating/security/), [Docker published ports](https://docs.docker.com/engine/network/port-publishing/) |
| CTR001 | Privileged execution, host namespaces, host ports/devices and selected sensitive host mounts are refused. Local-volume driver options receive the same sensitive-path check. Explicit UID 0 requests in Kubernetes are refused. | Host-access controls in [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/) |
| CTR002 | Unconfined seccomp/proc settings and excessive added capabilities are refused. Compose permits CHOWN for an isolated initializer with a read-only root, ALL capabilities dropped, no network/public ports and named-volume mounts. | Capability and seccomp controls in [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/) |
| APP001 | TestLookup application containers keep non-root execution, bounded seccomp, no privilege escalation and ALL capabilities dropped. Long-lived application workloads retain their existing read-only root filesystem. Jobs preserve the privilege controls while allowing the filesystem behavior used by the backup/restore tools. OpenShift can assign its UID while preserving the non-root contract. | Existing TestLookup deployment contract and selected Restricted controls in [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/) |
| NET001 | Each rendered workload namespace includes default-deny ingress and egress policies. Selected additive ingress rules are also checked for public access to operational ports. | [Kubernetes NetworkPolicy behavior](https://kubernetes.io/docs/concepts/services-networking/network-policies/) |

The operational catalog covers the reference metrics, alerting, log/trace stores, Grafana/Flower, datastore and local model/vector services. Standard operational ports are recognized independently of service names. Kubernetes checks follow named target ports into selected workloads, which preserves detection when a Service is renamed. Public frontend and approved API path routing remain supported. The loopback rule deliberately also applies to a direct API host port because its listener serves operational paths.

Internal NetworkPolicy CIDRs are RFC 1918 IPv4, loopback/link-local addresses and IPv6 unique-local/link-local addresses. Wider or public ranges need site review. Represent intended private ranges directly. An all-address CIDR with exclusions still requires review. Controller-specific backend `ImplementationSpecific` routing also requires explicit review because its regex/rewrite semantics vary.

These are selected deployment controls. Kubernetes admission, schema validation, CNI enforcement, host firewall behavior, runtime image identity and authenticated gateway/TLS configuration have their own installation acceptance. NetworkPolicies are additive and require a supporting network plugin. The checker evaluates selected high-confidence exposure patterns and preserves the existing detailed `backend/tests/regression/test_k8s_pod_security.py` suite. It does not certify the complete Pod Security Standards or a CIS/NIST compliance program. Terraform/provider plans, Cloud Run templates, custom workload CRDs, arbitrary ingress-controller rewrite programs and external managed services require their applicable reviews. A new operational image/port or workload kind needs a policy-catalog review.

## Reference execution

Use the existing hashed `scripts/requirements-ci.txt` or backend lock. The renderer requires Docker Compose with `config --no-env-resolution` support and Kustomize. CI uses its reviewed renderer versions. A missing tool, unsupported workload shape, failed render, oversized input or invalid exception fails the command.

```sh
python -m pytest scripts/security/tests -q
python scripts/security/render_reference_profiles.py \
  --source-sha "$GITHUB_SHA" --output .tmp/infrastructure-policy.json
```

The complete reference inventory currently has 27 profiles:

- Thirteen Compose combinations cover source, release, dev-lite, GCP VM, air-gap, demo, monitoring, durable telemetry and combined authenticated alerting/objective profiles. Both supported auth/alerting overlay orders are included.
- Kubernetes base and all thirteen overlays cover the generic, cloud, K3s, OpenShift and telemetry-client variants.

Every profile must pass its normal render and two in-memory negative controls. The controls publish an actual protected datastore/API listener and request a privileged API container. Each mutation must produce a new matching finding. Those mutated documents are never written to a deployment file or submitted to a runtime. A disabled detector makes the controls fail.

The evidence contains `scope=reference-matrix`, the expected profile inventory, per-profile render hashes, policy/renderer hashes, source SHA, UTC creation time, findings, reviewed exception IDs and both negative-control results. CI should omit `--profile` and require the full inventory. A developer can repeat `--profile` for focused diagnosis, which is recorded as `scope=focused-subset`.

For an existing private rendered artifact, use the same checker directly:

```sh
python scripts/security/check_rendered_infrastructure.py \
  --format compose --input /private/site-render.json --profile site-release \
  --source-sha "$CANDIDATE_SHA" --output /private/site-policy.json
```

Compose input must be normalized `docker compose config --format json` output. Kubernetes input must be the complete application `kustomize build` YAML, including its NetworkPolicies and Services. Treat a site render as secret-bearing configuration. Retain the sanitized policy report in release evidence. Review the real site substitutions and approved deployment diff separately from the synthetic reference run.

## Exact, expiring site exceptions

The reference profiles need no exceptions. A site whose reviewed gateway or private host binding requires a deviation can provide a versioned JSON file with `--exceptions`. Every entry identifies one exact profile, rule, resource and field. It needs an accountable owner, a bounded rationale and an expiry within 90 days. Wildcards, placeholder owners, duplicate scopes/IDs, stale entries and expired exceptions fail validation. Moving a finding to another field or route cannot reuse its exception. Route resource IDs include a hash of the path, matching mode and port.

```json
{
  "version": 1,
  "exceptions": [{
    "id": "site-metrics-gateway",
    "profile": "site-release",
    "rule": "OPS001",
    "resource": "compose/service/prometheus/port/9090",
    "field": "ports.host_ip",
    "owner": "platform-team",
    "reason": "Private site listener is reachable only through the reviewed authenticated gateway.",
    "expires": "2026-12-11"
  }]
}
```

The example date intentionally expires. Set the entry from the actual review date and remove it when the underlying configuration is corrected. Record the gateway/host access evidence and approval in the site's change record. The report includes the exception ID, owner and expiry while the versioned source retains the rationale. Keep exception values free of credentials and private payloads.

The gate returns 0 for a complete healthy assessment, 1 for policy findings and 2 when validation cannot complete. Repair the configuration or supply the narrowly reviewed site exception, then rerun the complete required scope. A missing evidence file or focused subset provides no full-reference acceptance.
