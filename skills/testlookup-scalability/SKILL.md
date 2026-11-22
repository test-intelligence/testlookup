---
name: testlookup-scalability
description: Review and improve TestLookup capacity, elasticity, and workload isolation. Use when changing workload, worker concurrency, ingestion shards, API or stream replicas, datastore pools, scaling limits, or operational headroom.
---

# TestLookup Scalability

## Model the load and dependencies

- Read `architecture/INGESTION_SCALE.md`, `docs/operations/database-connection-budget.md` and `docs/operations/ingestion-shard-count-change.md`. Record rendered replicas and worker concurrency, queue subscriptions, datastore and provider limits, and steady, peak and skewed workloads in explicit units. Include report size, events per second and concurrent streams when they drive capacity.
- Sum the connection demand across deployments loaded at once, including rollout surge, processes per replica, and each process's pool and overflow. Add migrations and compare with usable server and role capacity after reserves. Use `python scripts/validate_db_connection_budget.py --manifest <rendered.yaml>` for Kubernetes or the documented `--compose` mode with a fixture environment.
- Estimate backlog growth from arrival rate minus completion rate. Estimate drain time from backlog divided by the spare completion rate when that rate is positive. Measure oldest work age and queue depth by workload or shard. Check the first saturated store, queue, provider or network boundary, including fan-out and reconnect cost.
- Inspect `k8s/base/hpa.yaml`, worker deployment definitions and `backend/app/worker/ingestion_routing.py`. Evaluate dependencies at target replicas and during rollout. For a shard change, verify the mapping and ordering semantics in code, old and new queue subscriptions, publisher coordination, drain and canary readback in an isolated rehearsal. Record the rollback condition and how durable work is accounted for.
- When API process count changes, include the subscription and connection footprint of `backend/app/streams/live_fanout.py` and the elected live-event consumer. Use the WebSocket skill to verify reconnect and persisted completion under the proposed topology.

## Verify capacity and recovery

- Use focused tests such as `backend/tests/integration/test_db_connection_budget_postgres.py`, `backend/tests/integration/test_ingest_admission_http.py` and `backend/tests/integration/test_compose_queue_delivery_redis.py` for specific invariants. Check routing and declared queue consumers against the actual target.
- For a bounded scale trial, define steady and burst workloads, headroom target, stop conditions and recovery window. Measure completion, duplicates, queue age and depth, datastore and broker use, latency, refusals and recovery. Include a skewed-project fixture and a normal control when relevant. Cross-check aggregate metrics against known producer counts.
- For elasticity, record scale-out and scale-in triggers and timings. Verify drain and completion or replay of accepted work when capacity shrinks. For workload isolation, compare progress and latency for a normal project with and without a competing high-volume project. Assess both against the configured fairness or rate-control policy.
- Report the supported load by profile, limiting dependency, headroom at peak and during rollout, and measured recovery time. Mark estimated limits separately and state their assumptions. Tie each proposed change to the limiting resource, an acceptance threshold, and a follow-up verification of user-visible behavior or durable completion.
