# Alert delivery and recovery notifications

The ordinary monitoring overlay evaluates rules without sending notifications.
Add `docker-compose.alerting.yml` only after choosing an incident receiver and an
independent heartbeat service. The reference integration speaks the
[Alertmanager webhook protocol](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config).
An enterprise gateway can translate that protocol into the site's on-call,
ticketing or chat service. An arbitrary chat incoming-webhook URL is not
necessarily compatible with this payload.

## Configure a reference Compose installation

Create four private UTF-8 files using the site's secret manager: an HTTPS
incident webhook URL, its bearer token, an HTTPS watchdog webhook URL and its
separate bearer token. Do not put credentials in Compose environment values,
the checked-in YAML, URL userinfo, shell history or release artifacts. URLs may
also contain credentials and are mounted as secrets. Both endpoints must
accept `Authorization: Bearer ...`; redirects are disabled and server
certificates are verified. The heartbeat service must alert on **absence** of
the `TestLookupWatchdog` firing message, rather than treating each heartbeat
as an incident.

Set these environment variables to absolute host **file paths**:

```sh
export ALERTMANAGER_INCIDENT_URL_SOURCE=/private/monitoring/incident-url
export ALERTMANAGER_INCIDENT_TOKEN_SOURCE=/private/monitoring/incident-token
export ALERTMANAGER_WATCHDOG_URL_SOURCE=/private/monitoring/watchdog-url
export ALERTMANAGER_WATCHDOG_TOKEN_SOURCE=/private/monitoring/watchdog-token
export ALERTING_PROMETHEUS_CONFIG_SOURCE=/private/monitoring/prometheus-alerting.yml
python scripts/monitoring/configure_alerting.py \
  --installation production-site-a --environment production --owner platform-oncall \
  --output "$ALERTING_PROMETHEUS_CONFIG_SOURCE"
```

The helper requires PyYAML, validates the four files without printing values
or contacting their endpoints, and derives the alternate main configuration
from the current `infra/monitoring/prometheus.yml`. It preserves the current
rules and scrape includes. Installation/environment are mandatory stable
identifiers; the owner fills only an absent alert-owner label. Regenerate
after every base monitoring configuration change and validate the mounted
result with the pinned `promtool check config`. A previously generated file
does not automatically incorporate new base scrape settings.

The overlay also mounts its independent Alertmanager expected-role recording
rule. With the common telemetry rules loaded, deleting the Alertmanager
scrape job still produces a required-role alert. Disabling alerting must
remove both the overlay and its generated main configuration.

Then render and review the selected profile before starting it:

```sh
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml \
  -f docker-compose.monitoring-auth.yml -f docker-compose.alerting.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml \
  -f docker-compose.monitoring-auth.yml -f docker-compose.alerting.yml up -d
```

Supply the metrics-token paths required by the authentication overlay as
described in [authenticated metrics](deploy-compose.md#authenticate-the-api-scrape). The
authentication and alerting overlays may be layered in either order: one
replaces the backend scrape include and the other replaces the main config.
Use the same base/image overrides as the installation; the commands above
show the source-build reference profile.

The official image runs as `nobody` (UID/GID 65534). Compose local file secrets
are bind mounts: UID/GID/mode remapping is not guaranteed. Make the mounted
files readable by that container identity while restricting the enclosing
host directory to the operator, and verify that `alertmanager-config-check`
succeeds. Do not copy private values into a support bundle. For a private
certificate authority, mount its PEM and add `tls_config.ca_file` to each
receiver in a reviewed site configuration; keep certificate verification on.

Alertmanager has no host port. Prometheus sends to its private service DNS;
Alertmanager persists silences and notification deduplication state in
`alertmanager-data` with 120 hours' retention. The API can inject alerts,
create silences and reload configuration, so keep untrusted containers off
the monitoring network. Use an authenticated operator tunnel/proxy for API
access. This single-node overlay is a reference deployment, not an HA claim.
For production HA, use an existing managed/clustered Alertmanager with
authenticated TLS ingress and send Prometheus to **every** replica as
[upstream recommends](https://prometheus.io/docs/alerting/latest/ha/).

## Routing, context and maintenance

Alerts group by installation, environment, cluster, namespace and alert name.
Critical alerts wait 10 seconds and repeat every 30 minutes; other incidents
wait 30 seconds and repeat every four hours. A separate watchdog route sends
each minute. Both routes send resolved notifications. The external heartbeat
service must distinguish `status=firing` from `status=resolved` and page if
firing heartbeats stop for the site's chosen allowance, for example three
minutes. A receiver on the same host or cluster cannot independently detect
that host or cluster failing.

Use `severity`, an accountable `owner`, and annotations `summary`,
`description`, `runbook_url`, `dashboard_url` on production rules. Link to
the installed dashboard and an accessible operational runbook. The transport
preserves these fields; it does not invent a runbook for existing rules that
lack one. Existing rule-by-rule ownership/link coverage still needs review.
Avoid user identifiers, credentials and payloads in labels or annotations.

Warning inhibition is enabled only when **both** alerts have a nonempty
`alert_family`, match installation/environment/cluster/namespace and the
critical source remains active. Use that label only for two severities of the
same underlying condition. A critical alert without this opt-in cannot mute
unrelated warnings. For maintenance, create a time-bounded silence matching
the exact installation and affected alert/component, with accountable author
and reason. Avoid broad `.*` silences; do not silence the watchdog to hide a
broken monitoring path. Preserve `alertmanager-data` across replacement to
retain silences and deduplication state. Back it up with the site's volume
snapshot procedure; losing the volume can repeat incidents and lose silences.

Check Prometheus `/api/v1/alertmanagers` for the active destination, the
`alertmanager` scrape for health, and notification-failure/config-reload
rules for delivery errors. A working Prometheus-to-Alertmanager connection
alone does not prove receiver delivery. A failed receiver also cannot report
its own failure over the failed route: independent heartbeat absence is the
last-mile outage signal.

Rotate URL/token files using the secret manager, revalidate/regenerate and
recreate the config-check and Alertmanager services. File bind mounts may
retain an old inode after an atomic host-file replacement. Test a disposable
receiver before activating the new production credential.

## Kubernetes, K3s and OpenShift reuse

Prefer the site's existing Prometheus Operator Alertmanager. Mount the four
values from a namespace-local Secret and reuse the checked-in routing config,
adjusting absolute file paths to those mounts. Put `installation` and
`environment` in Prometheus external labels and preserve its rule-specific
owner labels; configure Prometheus alerting endpoints through the Operator's
`spec.alerting.alertmanagers` with matching namespace, Service and named port.
Keep TLS, service-account/RBAC and NetworkPolicy consistent with that site's
operator. The Compose service name `alertmanager:9093` is not a Kubernetes
endpoint. Kubernetes Secrets are namespaced; an operator-specific
`AlertmanagerConfig` needs its own selected namespace and Secret selectors.
Select the site's Alertmanager self-monitor/ServiceMonitor separately: the
application PodMonitor does not scrape an externally owned Alertmanager.
Review and render that site's CRD version before deployment. This change
provides a reusable routing contract, not a new in-cluster Alertmanager.

The monitoring sidecar is intentionally excluded from the application/offline
image manifest under the existing image-drift policy. Its official version,
manifest-index digest, source and binary checksums are recorded separately in
[`image-lock.json`](../../infra/monitoring/alertmanager/image-lock.json).
Mirror that exact digest and distribute site configuration/secrets separately
for an air-gapped monitoring installation; there is no new offline-bundle
coverage claim.

## Disposable delivery verification

`scripts/monitoring/tests/test_alert_routing.py` tests static safety and config
preservation. Set `ALERTMANAGER_TEST_BINARY`, `AMTOOL_TEST_BINARY` and
`PROMETHEUS_TEST_BINARY` to checksum-verified official release binaries to run
its real delivery case. It starts only loopback listeners, generates an
ephemeral TLS certificate and credentials, evaluates synthetic rules in a
temporary Prometheus, and observes Alertmanager firing/resolved delivery,
deduplication, maintenance silence, scoped inhibition, separate watchdog
delivery and an HTTP 503 followed by retry/recovery. No actual on-call service
is contacted. Skipping this case is not full signal-path verification.
