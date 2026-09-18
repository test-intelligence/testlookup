# Glossary

[Documentation home](../README.md)

| Term | Meaning in this codebase |
|---|---|
| Project | Primary authorization and configuration scope for test intelligence |
| Test run / `TestRun` | One collected execution context/build with aggregates and result records |
| Execution / `TestCase` | A test outcome within a particular run |
| Canonical test | Stable suite/test identity linking executions over time |
| Managed test case | Authored test content with review and lifecycle state |
| Fingerprint | Stable test-name/class identity hash; distinct from run ingestion identity |
| Ingestion identity | Source-aware run deduplication key including CI context |
| Sentinel | Stored metadata/event indicating a report set is ready for ingestion |
| Materialization | Moving buffered live execution data into persistent run/case records |
| Finalization | Computing terminal execution status/aggregates and staging downstream work |
| Outbox | Durable intent recorded with business state, later dispatched/reconciled |
| Pipeline | One agent workflow execution with frozen authority, attempts and stage records |
| Invocation | API request for a catalog capability executed through the pipeline engine |
| Capability | Registered agent/tool contract and policy metadata |
| SLM / LLM | Smaller/larger language-model tiers selected by routing policy |
| Checkpoint | Persisted stage output eligible for reuse only under valid authority/input context |
| Lease / fencing | Time-bounded claim plus authority token preventing stale worker writes |
| Degraded | Completed output with failed/skipped/limited evidence stages recorded in metadata |
| Review envelope | AI-generation and human-review state attached to an output |
| Passed pipeline | Review/terminal workflow meaning; not interchangeable with a passed test run |
| Distribution | Output leaving in-app context through export/share/notification/CI/client channels |
| Release gate | Policy/evidence decision for a run, release or phase; inspect exact scope |
| NOT_EVALUATED | Release-level evidence is insufficient for a decision |
| PENDING_REVIEW | Outward review-gate projection; not one of the persisted release verdict constants |
| Quarantine | Governed flaky-test exclusion/recheck lifecycle, not deletion of history |
| RAG | Retrieval-augmented generation using registered source content/provenance |
| DLQ | Dead-letter record/queue for exhausted or unprocessable asynchronous work |
| Beat | Celery scheduler dispatching periodic tasks/reconciliation |
| MCP | Model Context Protocol adapter exposing REST-backed tools/resources/prompts |
| Offline mode | Application AI/egress policy; full air-gap also requires deployment isolation |
| Ratchet | Guard that rejects new violations while explicitly tracked baseline debt remains |
