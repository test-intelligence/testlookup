# Celery queue monitoring

Use the Celery broker and workers section of the TestLookup Overview dashboard to correlate fresh queue depth, worker-observed wait and completed attempts. The API and worker scrape jobs both need to be healthy. The reference Compose stack and Kubernetes Operator manifests use the same recording and alert rules. [Required telemetry and discovery failures](telemetry-contract.md).

| Signal | What it measures |
|---|---|
| `testlookup:celery_queue_pending` | Pending broker deliveries per logical queue. The API sums Redis or Valkey `LLEN` over all priority lists for each declared queue, including each ingestion shard and `agent_children`. The rule keeps only healthy scrape targets with a successful queue observation at most 60 seconds old, then takes the maximum across API replicas reading the same broker. |
| `testlookup_celery_queue_depth_observed_timestamp_seconds` | UTC time of the latest successful sample for that queue. An explicit failed bin records zero for the queue. If collection never returns, the previous observation expires. A successful empty queue yields a fresh timestamp and depth zero. |
| `celery_task_queue_wait_seconds` | Time until a worker starts a broker-delivered task, measured from the start of publication or the later scheduled execution time. This can include publish I/O, broker waiting and worker reservation. Workers report a bounded queue family, so all ingestion shards share one wait series. |
| `celery_task_queue_wait_unavailable_total` | Task starts with missing or invalid timing metadata. Track this beside the wait histogram to see which starts its percentiles cover. |
| `celery_task_runtime_seconds_count` | Completed worker attempts. This includes retries and failures. Use the downstream and pipeline outbox signals for durable outcomes. [Operational event delivery](pipeline-event-delivery.md). |

The broker read requires `LLEN` on the deployment's existing queue keys. It transfers list sizes and uses the existing collector deadline and connection pool. Priority and key-prefix settings must match the real broker. Queue arguments, results and payloads stay out of the sampler. `testlookup:celery_queue_pending` measures current broker-pending delivery counts. The sustained-backlog rules measure repeated observations. Worker wait measurements become available after the delivery begins. Exact oldest broker age remains unavailable from this size-only reader.

Worker progress counts include starts with old, unstamped messages. A new publication or retry gets its own timestamp. Scheduled delay is excluded from the worker wait calculation. Keep API, scheduler and worker clocks synchronized. Queue depth excludes work already held by workers, and some low-volume queues naturally have gaps between observations of worker progress.

## Alert response

| Alert | Reference threshold | Investigation |
|---|---|---|
| `TestLookupCeleryQueueBacklog` | More than 100 pending deliveries for five minutes. | Compare depth with worker attempts, wait and readiness. Check routing, throughput and scaling. |
| `TestLookupChildQueueNoHealthyTarget` | A positive `agent_children` backlog for two minutes when the profile declares the child role and its scrape targets are all unhealthy or missing. | Check the child deployment and its worker probe, then verify discovery, scrape reachability and network policy. |
| `TestLookupChildQueueNoProgress` | A positive child backlog for five minutes with a healthy scrape target and no starts or completed attempts seen in the preceding three minutes. | Inspect the actual child subscription, startup events, broker connectivity and concurrency. Check for long-running work or blocked I/O. |

Roll out the observing API and workers before loading the revised queue rules. Confirm each required target has data. Scope views and rules with the installation's `cluster`, `namespace` and `telemetry_profile` labels. A successful worker scrape proves the metrics listener answered. Check the worker readiness probe for the current parent. [Worker readiness](worker-readiness.md).

In a disposable deployment, stop its child consumer and publish synthetic work through the real router at multiple priorities. Confirm `agent_children` depth rises, the expected-role signal stays declared and the queue alert fires. Restore the consumer and check that pending depth, alert state and durable results recover. Verify fresh zero for a drained queue and a missing recording when an observation fails. Run the source rule fixtures with `promtool test rules infra/monitoring/tests/celery-queues.yml`.

The producer and worker boundaries use the published Celery signals. The broker reader follows Redis transport's documented priority lists, and alerting uses scoped fresh data and worker throughput. [Celery signals](https://docs.celeryq.dev/en/stable/userguide/signals.html), [Celery Redis transport](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html), [Prometheus instrumentation practices](https://prometheus.io/docs/practices/instrumentation/).
