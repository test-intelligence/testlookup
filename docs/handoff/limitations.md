# Known limitations, troubleshooting and future work

[Documentation home](../README.md)

These are observed contracts/operational constraints or explicitly identified follow-up work. They are not claims that the current deployment is failing.

## Important limits

- API contracts are not uniformly complete: some endpoints return plain dicts, streamed files or manually shaped responses. [Generated gap list](../reference/api-contract-gaps.md) makes this visible; use the linked handler/serializer until a response model is added.
- Source import and schema generation do not prove live migration compatibility, database availability, queue delivery, model behavior or external integration credentials. This task did not deploy or run production traffic.
- Report review enforcement defaults off. The system can record would-refuse audit events without preventing distribution. Enable it intentionally after checking downstream consumers and existing reports.
- Asynchronous paths are eventually consistent. Upload 202, Redis task status, durable run, pipeline completion, accepted review and successful delivery are separate observations. Redis file status expires after 24 hours.
- Core AI can use rules/available ML; deep/model/RAG features need configured resources, flags and evidence. A fallback preserves availability but can reduce analytical depth. Confidence is not a calibrated probability unless a specific evaluator demonstrates it.
- Offline AI policy permits validated local/private endpoints and does not implement a universal network firewall. Model/image/dependency downloads must happen before sealing a deployment.
- The modern feature-flag service defaults unknown flags off (subject to explicit legacy environment fallback); the legacy facade has fail-open global semantics. Changing callers without checking this can change behavior.
- Admission quota/backpressure probes can fail open on Redis errors. They are traffic-management mechanisms, not guaranteed hard capacity protection during an outage.
- Multi-store persistence has no distributed transaction. Outboxes/reconciliation/tombstones improve recovery but do not guarantee globally exactly-once effects.
- Homelab local-path storage is node-local. Multiple application replicas do not protect a failed disk; off-node backups and restore drills remain necessary.
- Several large modules concentrate responsibilities (`postgres.py`, `schemas.py`, workflow/tasks and quality gate). Splitting them is a maintainability proposal, not prerequisite work for this documentation branch.

## Symptom-to-boundary troubleshooting

| Symptom | First checks |
|---|---|
| Accepted upload never appears | Correct run/task IDs; file status; object storage; worker dispatch/queue subscription; parsing error and run activity |
| Upload status is 404 | Same project/credential; status TTL/Redis; durable run existence |
| Live view updates but run details lag | Drainer/finalizer, outbox, ingestion queue and canonical test-run ID |
| Pipeline stays in progress | Beat, queued worker subscription, next retry, lease/reaper state, child consumer, model timeout |
| AI output lacks expected detail | Actual mode/tier/provenance, evidence availability, budget, selected workflow, skip/degradation metadata |
| “Feature enabled” but action unavailable | Project/role allowlist, rollout bucket, fresh config, legacy vs scoped flag service, required provider |
| Report export is refused | Review subject/state, enforcement, allowed pending-draft exception and authorization |
| Release unexpectedly green/neutral | Evidence denominator, skipped/unknown/stopped runs, attribution/phase scope, policy snapshot, review projection |
| UI login HTTP 200 but no session | MFA challenge or required enrollment shape, forced password reset |
| `/health/details` is 200 but system degraded | Read component statuses; use `/health/ready` and queue/worker proof |
| Second clone cannot start stack | Fixed container names/ports and shared host Docker state; isolate topology or stop the existing stack intentionally |
| `/docs` opens wrong page or Swagger lacks schema | Ingress `/api-docs` routing; frontend `/docs`; schema path under `/api-docs` |

## Prioritized future improvements

Complete typed response contracts for unstructured endpoints and validate frontend/CLI/MCP consumers against them. Add a lightweight CI drift job for this generated documentation. Periodically reconcile legacy docs with source and current deployment evidence. Measure latency/throughput/accuracy on representative data before publishing capacity claims. Keep service extraction and storage consolidation decisions driven by measured operational cost rather than module count.

For actionable status/owners/evidence, use the [review action register](../reviews/2026-09-18-action-register.md).
