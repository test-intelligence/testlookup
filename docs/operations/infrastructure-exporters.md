# Private infrastructure exporter contract

The optional infrastructure profile connects Prometheus to site-owned host and PostgreSQL exporters over mutual TLS. It adds configuration and expected-target records. The site supplies the agents, private network path, trusted certificates and restricted producer accounts. The application continues to expose [Redis resource metrics and SQLAlchemy pool measurements](dependency-metrics.md).

| Role | Job label | Required samples per configured instance |
| --- | --- | --- |
| `host-exporter` | `testlookup-infra-host` | `node_filesystem_avail_bytes{mountpoint="/"}`, `node_filesystem_size_bytes{mountpoint="/"}`, `node_vmstat_oom_kill` |
| `postgres-exporter` | `testlookup-infra-postgres` | `pg_up`, `pg_settings_max_connections` |

Each instance has explicit `cluster`, `namespace`, `telemetry_profile`, `telemetry_role`, `job` and `instance` labels. `instance` is a stable site alias. Addresses, database names, SQL text and credentials stay out of these labels. The generator accepts up to 16 configured instances. It emits one independently declared `testlookup:infrastructure_expected_target` record for each, so removing a scrape job leaves its expectation visible.

The host contract accepts one root filesystem pair and the kernel OOM counter. The PostgreSQL contract accepts server health and its configured connection ceiling. Application pool saturation and Redis memory keep their existing producers and rules. Additional MongoDB, object-storage, managed-service and cluster capacity signals require separately reviewed provider contracts. Site inventory records those prerequisites and their owners.

## Producer and trust prerequisites

Reuse an approved platform agent where its samples and authority match this contract. On a Linux host, a native `node_exporter` service can run as a dedicated unprivileged account with selected `filesystem` and `vmstat` collectors. Check that its mount namespace and `/` describe the intended host filesystem. A container's filesystem describes that container's namespace. This profile adds no host-root bind, host PID/network namespace, Docker socket, privileged container or exporter image.

For a site-managed native agent, select the required collectors and private listener using its pinned version's documented flags:

```text
--collector.disable-defaults
--collector.filesystem
--collector.vmstat
--web.listen-address=<private-address>:9100
--web.config.file=/etc/node_exporter/web.yml
```

The kernel and service account must expose the required samples. Prometheus exporter-toolkit supports this server-side mutual-TLS configuration:

```yaml
tls_server_config:
  cert_file: /etc/exporter/server.crt
  key_file: /etc/exporter/server.key
  client_ca_file: /etc/exporter/monitoring-ca.crt
  client_auth_type: RequireAndVerifyClientCert
  min_version: TLS12
```

Restrict the private listener to the selected Prometheus identity and network. Use a dedicated client trust chain or the toolkit's allowed client SAN policy for that installation. Rotate exporter and monitoring trust through the site's normal certificate lifecycle.

PostgreSQL exporters use a separate monitoring login with `CONNECT` and the monitoring-view grants needed by enabled collectors. Upstream recommends `pg_monitor` for supported PostgreSQL versions and describes `pg_read_all_stats` as a narrower statistics alternative. Keep application table privileges separate. Enable only needed collectors and retain query-text collection disabled. Supply producer credentials from platform Secret files, verify database TLS, and set bounded database connection/query timeouts. The scrape profile has no database credential. Pin, scan and inventory the selected agent version and image or native package through the site's existing release process.

## Configure source or release Compose

Copy `infra/monitoring/infrastructure/site.example.yml` to a private site file. Set the deployment scope, stable instance aliases and actual private addresses. Each target declares `private_endpoint: true`. Literal addresses are restricted to private or loopback ranges. DNS names require site-controlled private resolution and egress admission. The offline validator performs no lookup and makes no network-privacy claim from a hostname.

Generate the two mount files:

```sh
python scripts/monitoring/configure_infrastructure_metrics.py \
  --site /secure/testlookup/infrastructure-site.yml \
  --scrape-out /secure/testlookup/infrastructure-scrape.yml \
  --rules-out /secure/testlookup/infrastructure-contract.yml
```

Generate into a fresh private staging directory. Validate every output before
rollout and mount the scrape file and independent inventory together. Treat a
failed generation or partial output set as unusable. Run the pinned `promtool`
against the complete mounted configuration before restarting Prometheus. Pass
the generated rule file to `scripts/monitoring/check_mounted_prometheus.py`
with `--infrastructure-rules` to verify the mounted target inventory.

Set these file-path variables in the installation's private environment:

```sh
INFRASTRUCTURE_SCRAPE_CONFIG_SOURCE=/secure/testlookup/infrastructure-scrape.yml
INFRASTRUCTURE_RULES_SOURCE=/secure/testlookup/infrastructure-contract.yml
INFRASTRUCTURE_CA_SOURCE=/secure/testlookup/exporter-ca.crt
INFRASTRUCTURE_CLIENT_CERT_SOURCE=/secure/testlookup/monitoring-client.crt
INFRASTRUCTURE_CLIENT_KEY_SOURCE=/secure/testlookup/monitoring-client.key
```

Layer `docker-compose.infrastructure-metrics.yml` after the monitoring overlay and keep the installation's existing authentication, alerting and telemetry overlays:

```sh
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml \
  -f docker-compose.infrastructure-metrics.yml config
```

Use `docker-compose.release.yml` as the first file for release images. The overlay mounts the generated scrape include at `/etc/prometheus/telemetry-scrape-infrastructure.yml` and the independent contract under `/etc/prometheus/rules/`. Three Compose Secrets supply the CA, client certificate and client key under `/etc/prometheus/infrastructure/`. Ensure the Prometheus runtime UID can read the mounted files. The application services receive none of these mounts and have no startup dependency on the exporters.

Reload configuration through the site's controlled Prometheus restart or signal procedure. The reference profile keeps its unauthenticated lifecycle HTTP endpoint disabled. Disconnected installations transfer this config-only overlay, generator and generated site files alongside their existing monitoring bundle and locally managed trust. Agent packages and images remain separately pinned site prerequisites.

## Prometheus Operator, K3s and OpenShift

Set `profile: kubernetes` and the actual `monitoring_namespace` in the site file. Generate the native scrape files and the additional Operator manifests:

```sh
python scripts/monitoring/configure_infrastructure_metrics.py \
  --site /secure/testlookup/infrastructure-site.yml \
  --scrape-out /secure/testlookup/infrastructure-scrape.yml \
  --rules-out /secure/testlookup/infrastructure-contract.yml \
  --operator-out /secure/testlookup/infrastructure-operator.yml
```

The Operator output contains `testlookup-infrastructure-scrapes`, a namespace-local Secret holding the job list, and `testlookup-infrastructure-contract`, a PrometheusRule declaring the expected targets. It contains file references and configuration. The site separately manages a `testlookup-infrastructure-client` Secret with `ca.crt`, `client.crt` and `client.key` in the Prometheus workload's namespace.

Merge the generated jobs with the site's existing `additionalScrapeConfigs` job list. Preserve its current scrapes, Secret mounts and selectors. For a Prometheus resource with an empty additional-scrape list, the relevant site-owned fields are:

```yaml
spec:
  secrets:
    - testlookup-infrastructure-client
  additionalScrapeConfigs:
    name: testlookup-infrastructure-scrapes
    key: scrapes.yml
```

Prometheus Operator mounts those TLS files below `/etc/prometheus/secrets/testlookup-infrastructure-client/`. Select the independent contract and the shared application rules through the site's PrometheusRule namespace and label selectors. Verify that its admission policy permits the configuration Secret and only the declared private egress targets. Keep exporter ingress restricted to the Prometheus workload and client certificate. NetworkPolicy can restrict IPs or selected pods and ports. DNS policy and managed-service routing remain platform responsibilities.

This native job-list integration preserves `follow_redirects: false`, verified TLS and all scrape limits. It works through the existing Operator additional-scrape mechanism and requires no additional ScrapeConfig CRD. Installation-wide Prometheus enforcement can reduce effective limits further. Verify the actual generated Prometheus configuration and samples after admission.

## Bounds, failure states and acceptance

Each job scrapes one target every 30 seconds with a five-second timeout. It accepts at most a 2 MB uncompressed body, 128 selected samples, 10 labels per sample and 128 bytes per label value. It refuses redirects and ambient proxy routing, verifies the configured certificate name and requires TLS 1.2 or later. Exporter timestamps and conflicting source labels cannot replace the declared scope. Metric relabeling keeps only the finite metric and label contract. Additional filesystem and database dimensions are removed. A duplicate root series or an exceeded scrape limit requires investigation.

Confirm each required target is selected, `up=1`, and has every required series. An HTTP success establishes transport reachability. `pg_up=0` reports a database connection failure even when the exporter is reachable. Check the Infrastructure exporters row in TestLookup Overview for root capacity, kernel OOM kills and PostgreSQL reachability. The common host alerts select the generated `testlookup-infra-host` job and the documented legacy `node-exporter` job. Assign one alert owner when a platform already monitors the same hosts. Check independent expected-target records after removing a target in a disposable profile. [Alert routing](alert-routing.md).

In a disposable installation, verify a healthy control, a low root-space sample, a kernel OOM-counter increment where supported, PostgreSQL connection refusal and recovery, wrong certificate/name refusal and missing-target detection. Recover the exact expected values and target count. For production host coverage, record the actual host/mount authority and agent permissions. A synthetic metrics endpoint verifies the transport and rule data path. It supplies no evidence of real host disk pressure or a deployed platform agent.

Run the focused configuration and optional native Prometheus tests:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest scripts/monitoring/tests/test_infrastructure_metrics.py -q
```

Set `PROMETHEUS_TEST_BINARY` to the installation's checksum-verified Prometheus binary to include the local mutual-TLS fixture. It verifies scope and metric filtering, synthetic dependency changes, certificate-name and missing-client refusal, redirect refusal and recovery. Docker Compose render tests cover both source and release files. Site producer acceptance remains separate from these fixtures.

Use the upstream contracts when reviewing a site's producer identity, collector configuration and scrape limits. [Node Exporter](https://github.com/prometheus/node_exporter/blob/master/README.md), [PostgreSQL exporter permissions](https://github.com/prometheus-community/postgres_exporter/blob/master/docs/database-permissions.md), [Exporter Toolkit mutual TLS](https://github.com/prometheus/exporter-toolkit/blob/master/docs/web-configuration.md), [Prometheus scrape configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/), [Operator additional scrape configuration](https://prometheus-operator.dev/docs/api-reference/api/#monitoring.coreos.com/v1.PrometheusSpec).
