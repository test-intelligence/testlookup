# Proposed API objectives and response policy

These objectives are an **unadopted proposal**. The ordinary monitoring profile does not load the objective rules. The optional profile loads an inert adoption example that keeps objective paging disabled. An installation owner must review the eligible population, targets, data coverage, alert receiver and response policy before adopting either objective.

| Proposed objective | Good eligible attempt | Target | Rolling period |
| --- | --- | --- | --- |
| api_availability | A terminal 2xx or 3xx response | 99.9% | 30 days |
| api_latency | A terminal 2xx or 3xx response whose final response body completes within its existing route budget | 95% | 30 days |

The latency proposal aligns with the existing p95 route budgets and awaits site acceptance. Google recommends baselining indicators and agreeing the objective and response policy with stakeholders before adoption. See [Implementing SLOs](https://sre.google/workbook/implementing-slos/) and the [example SLO document](https://sre.google/workbook/slo-document/).

## Measured boundary

`JourneySLIMiddleware` produces reset-aware process counters:

- `testlookup_api_journey_events_total{installation,environment,objective,outcome}`, with the two closed objectives above and outcomes `good`/`bad`.
- `testlookup_api_sli_configured{installation,environment,objective}=1` on an enabled API target. Both counter outcomes are initialized even before traffic.
- `OBSERVABILITY_INSTALLATION` enables measurement and must be a stable, non-secret installation identifier. `APP_ENV` supplies the environment. Neither field may identify a tenant or user.

The eligible population is the six core GET templates in `app/services/journey_sli_contracts.py`: project list (200 ms), run list (250 ms), scoped run read (250 ms), run intelligence (800 ms), search (300 ms), and flaky coach (600 ms). Ordinary 4xx responses are excluded. A 429, 5xx, observed exception/cancellation or incomplete terminal handler is bad for both objectives. Health, metrics, administrative, export and unrelated routes are excluded. Route names, raw URLs, users and project IDs are not metric labels.

These are **observed completed-attempt indicators**. A process that dies before recording its outcome, a request still hung in flight, an unreachable installation and client-side/network failures before the API are censored. Use independent probes, target-discovery alerts and customer-facing measurements alongside these indicators. The durable accepted-to-completed boundary requires separate evidence. Use SQL retained-state cohort gauges for the diagnostics described in [journey-slis.md](journey-slis.md), and source these rate windows from the API counters.

Each recording groups by `installation,environment,cluster,namespace,objective`. The first two labels originate in the application. Cluster/namespace must reflect actual local scrape labels when present. Application and local scrape labels define rule-evaluation scope. Assign distinct installation identifiers to separate deployments with otherwise identical labels. The dashboard selects one exact scope and never sums across installations.

## Unknown, quiet traffic and history

A current scope is valid only if every observed configured API target has both outcome series and its `up` sample equals one. A missing outcome pair or down configured target invalidates assessment. Independent target/series-absence rules cover targets that have vanished entirely from service discovery.

Rules sum each replica's `increase` before dividing. This handles independent counter resets and weights the fleet by eligible attempts. Counter increases are estimates between scrapes. The denominator retains its measured value. Zero eligible attempts yield **no ratio/burn/budget** and remain unknown. Quiet but fully configured targets do not trigger the measurement-unavailable alert. Prometheus documents the counter reset and extrapolation behavior of [increase/rate](https://prometheus.io/docs/prometheus/latest/querying/functions/).

The fast group evaluates every minute. A five-minute window requires all six minute observations including its baseline, and other fast windows require the equivalent complete history. Every five-minute checkpoint requires that complete minute history. Thirty-day coverage requires all 8,640 consecutive checkpoints. A missing checkpoint makes the full window unknown until it rolls out. This coverage measure conservatively requires complete observations.

Heavy three-day and thirty-day raw-counter calculations evaluate every five minutes. Current complete-window gates suppress cached assessments during gaps. The dashboard displays the thirty-day evaluation age. No-data values stay unknown. Allow at least thirty days plus initial scheduling alignment for a complete budget. Rebuilding Prometheus storage restarts this history. The ordinary 15-day retention cannot establish a thirty-day budget.

The optional Compose profile requests 35-day retention and requires a site-selected size cap. A size cap that evicts history early still makes the window unknown. Before adoption, measure rule-group evaluation duration, missed/failed iterations, query sample counts, disk growth and CPU under the actual replica/scrape count. Provision enough storage and headroom. A five-minute group must finish comfortably within its interval. Capacity acceptance belongs to the installation and its measured fleet.

## Proposed response policy

The policy follows Google's multiwindow pattern, which requires simultaneous long and short burns so a recovered burst stops paging promptly. The longer window controls budget significance. The shorter window confirms an ongoing problem. See [Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/).

| Candidate action | Long window and burn | Short window and burn | Additional guard |
| --- | --- | --- | --- |
| Page, critical | 1 hour above 14.4x | 5 minutes above 14.4x | At least 3 estimated short-window bad attempts, 2-minute hold |
| Page, critical | 6 hours above 6x | 30 minutes above 6x | At least 3 estimated short-window bad attempts, 2-minute hold |
| Ticket candidate, warning | 3 days above 1x | 6 hours above 1x | At least 3 estimated short-window bad attempts, 15-minute hold |

The reference policy proposes the three-bad-attempt guard as a low-traffic safeguard. Revisit it with the owner's actual traffic and independent probe coverage: it deliberately suppresses pages for a very small number of failed attempts.

`TestLookupAPIObjectiveBudgetBurnPage` carries `severity=critical`. `TestLookupAPIObjectiveBudgetBurnTicket` carries `severity=warning`. It creates a ticket **only if the site's receiver implements ticket routing**. The reference Alertmanager sends both severities to its incident webhook with different timing. The site receiver owns ticket creation. Configure and verify the adopted receiver separately using [alert-routing.md](alert-routing.md), including resolved delivery.

`TestLookupAPIObjectiveDataUnavailable` warns after five minutes only for an adopted scope with incomplete current measurement. `TestLookupAPIObjectivePolicyAmbiguous` warns if multiple active owners match one scope. That ambiguity disables objective paging. Initial window warmup remains a measurement state.

## Compose adoption

The source files are `infra/monitoring/objectives/api-slos.yml` and `adoption.example.yml`. Keep paging inert while collecting a baseline:

```sh
export OBSERVABILITY_INSTALLATION=your-stable-installation
export SLO_PROMETHEUS_RETENTION_SIZE=20GB  # example only: choose within the volume budget
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml \
  -f docker-compose.objectives.yml config --quiet
```

Layer the existing authenticated-scrape, telemetry and alerting overlays used by the installation into that same command and preserve the reviewed overlay order. This command renders configuration only. For prebuilt release images, replace the first file with `docker-compose.release.yml`:

```sh
docker compose -f docker-compose.release.yml -f docker-compose.monitoring.yml \
  -f docker-compose.objectives.yml config --quiet
```

Offline release bundles include the config-only `docker-compose.objectives.yml` overlay and the `infra/monitoring` tree. Preserve their relative paths and append the objective overlay to the bundle's reviewed Compose file list. It reuses the existing Prometheus image and adds no image download. Validate the complete offline render against the bundle manifest and the installation's registry overrides before rollout.

Copy `adoption.example.yml` to a site-owned file. Replace installation/environment/cluster/namespace with the **actual local series labels**, set one accountable owner per objective, and leave each `vector(0)` unchanged until the objective and response policy are accepted. Record owner acceptance, date, rationale, baseline, traffic exclusions, independent monitoring, retention capacity, receiver test and rollback location in the site's change record. Then change only the accepted objective's expression to `vector(1)` and set `SLO_ADOPTION_RULES_FILE` to that file. Do not load both the example and its replacement.

Validate the source and the chosen site file before the separately authorized rollout:

```sh
promtool check rules infra/monitoring/objectives/api-slos.yml /path/to/site-adoption.yml
promtool test rules infra/monitoring/tests/objective-burn-rates.yml \
  infra/monitoring/tests/objective-window-coverage.yml
```

To disable objective paging, return the site policy to `vector(0)`, validate it and follow the installation's reload/rollout procedure. Keep measurement and diagnostic alerts enabled while investigating.

## Prometheus Operator adoption

Generate the release artifact after editing the source, then inspect the optional overlay:

```sh
python scripts/render_prometheusrule.py
kubectl kustomize k8s/monitoring-objectives
```

The base application, its authenticated metrics PodMonitors and the ordinary monitoring rules must already be selected and scraped. Set `OBSERVABILITY_INSTALLATION` on the API workload through the site's application patch. Configure at least 35-day Prometheus retention with an adequate volume/size limit in the site's Prometheus resource. Storage configuration belongs to that resource.

`k8s/monitoring-objectives/prometheusrule.yaml` contains the objective calculations and alerts. The site supplies the adoption policy separately. Create a separate site-owned `PrometheusRule` whose `spec.groups` contains the reviewed adoption file's groups. Give it the installation namespace and labels selected by that Prometheus resource. Its `ruleNamespaceSelector` must include the namespace, and `ruleSelector` must select both the calculation rule and adoption rule. For kube-prometheus-stack this often requires the matching release label. Wrap the source groups in a Kubernetes manifest. Render/validate the site manifest and apply only through the normal authorized deployment process. Scope adoption labels to the actual scraped cluster/namespace.

## Respond and verify

1. Confirm the exact scope, active owner, current validity, window coverage and budget as-of age in **TestLookup â€” API Objectives (Proposed)**. Unknown data is not evidence of compliance. Check discovery and scrape failures first.
2. For a burn, compare the long/short windows and bad-attempt counts. Use the overview route diagnostics, safe logs and sanitized traces to identify the affected route or dependency. For availability, verify independent reachability to cover fully censored outages.
3. Restore the failing service/dependency or roll back the triggering change through the incident process. Do not change the target or exclude errors merely to silence a burn.
4. Confirm the short window recovers, the alert resolves at the receiver, and the longer-window debt remains visible. A page clearing does not replenish the rolling budget immediately.
5. For unavailable measurement, restore both outcome series and successful scrapes, then allow complete windows to warm up. For ambiguous ownership, retain exactly one accepted scoped policy. Record incident and policy decisions.

Native Prometheus fixtures cover weighted replica totals, independent resets, installation/environment/cluster/namespace isolation, quiet/missing data, incomplete replicas, minimum-event suppression, adoption isolation, owner ambiguity, 1h/6h/3d burns and recovery. The thirty-day fixture tests the exact checkpoint boundary and insufficient fifteen-day history with native PromQL. It does not simulate thirty days of wall-clock traffic or certify a production fleet's query/storage capacity.
