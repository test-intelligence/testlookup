# Product overview

[Documentation home](../README.md)

TestLookup turns test execution data into a project-scoped history of quality, evidence and decisions. Its central objects are projects, test runs, per-run executions, canonical test identities, analyses, releases and reviews. Teams can start with report files and deterministic analysis, then enable richer live events, model-assisted investigation and external integrations.

## People and jobs

| Persona | Main job | Primary surfaces | Success evidence |
|---|---|---|---|
| Test/automation engineer | Import results and distinguish product, test-data, automation, flaky and infrastructure failures | Runs, individual tests, My Failures, compare, live execution | Durable run, explainable classification, assigned action |
| QA engineer | Maintain coverage and authored test content | Suites, canonical tests, test management, plans, knowledge generation | Reviewed test lifecycle and traceable execution history |
| QA lead | Review AI conclusions and decide release readiness | Reviews, intelligence, release gate, releases, quarantine | Accepted/rejected report, auditable decision and reasons |
| Developer/service owner | Investigate failures in an owned area | Evidence, clusters, ownership, commit attribution, Jira defects | Reproducible evidence and a tracked correction |
| Platform administrator | Operate storage, identity, workers and policies | Settings, identity audit, integration health, retention, storage | Healthy queues, authorized access, recoverable data |
| Viewer/manager | Understand trends and delivery risk | Overview, summary reports, value metrics | Scoped, current information with clear denominators and review state |
| CI/agent client | Submit telemetry and retrieve policy results | API, CLI, SDK, MCP | Stable IDs, explicit terminal state, actionable errors |

Roles are `VIEWER < TESTER < QA_ENGINEER < QA_LEAD < ADMIN`. Project membership and API-key scope apply in addition to role. This table describes typical responsibilities, not a complete permission grant; handlers and dependency checks are authoritative. [Security](../architecture/security.md).

## Feature domains

**Collection and history.** Upload supported reports, post batches, receive object-storage sentinel events, or stream live SDK events. File parsing, persistence and AI work are asynchronous. Suite/canonical identities connect executions across runs; CI metadata, environment and execution timestamps improve comparison and release attribution.

**Failure intelligence.** Rules, trained classifiers and configured language models share analysis contracts. The offline graph produces anomalies, classifications and a summary; the deep graph adds clusters, child investigations, specialist evidence, risk and a decision report. Findings include provenance, confidence and missing-evidence signals rather than a guarantee of root cause.

**Human governance.** Per-project agent settings and versioned workflow definitions constrain execution. Reports have a human review lifecycle; external distribution is controlled separately. Release decisions, overrides, assignment, flaky quarantine and action proposals retain audit context. Review enforcement is an installation choice; its default observation mode must not be presented as enforced approval.

**Test management.** Authored test cases, plans and strategies are distinct from observed execution records. Knowledge retrieval and generated test content can support authoring, with feature gates, source provenance and review. Lifecycle version 2 is gated; callers should use returned allowed actions instead of inventing transitions.

**Communication and access.** In-app views, downloads, share links, notification subscriptions, Jira/GitHub/GitLab and MCP provide different access paths. A connector must be configured and allowed by policy. SSO/SCIM and project API keys are implemented; older “future enterprise” marketing labels do not describe the source snapshot.

## Scope, assumptions and expectations

TestLookup ingests evidence from an external test runner. Importing a file does not execute that test suite again. Investigation/Fixer automation has its own explicit policies and action records; it is not the normal test execution engine.

Correct comparisons assume stable test names/classes and meaningful project, environment, branch and CI identity. Missing history or evidence can produce neutral, unknown or degraded results. An empty or all-skipped finalized run is `STOPPED`, not a successful validation. Metrics may temporarily differ during live materialization; wait for finalization before making a release decision.

Availability depends on PostgreSQL, MongoDB, Redis, object storage and correctly subscribed workers. Local models are optional for core collection, but model-specific features need an available provider and installed weights. The repository supplies configurable budgets and tests; it does not establish a universal latency, availability or accuracy SLA. Offline deployments need pre-staged images, Python/JS dependencies and model weights.

Evidence: [router registration](../../backend/app/bootstrap.py), [UI routes](../../frontend/src/App.tsx), [roles/models](../../backend/app/models/postgres.py), [analysis router](../../backend/app/services/analysis_router.py), [run status](../../backend/app/services/run_status.py), [report policy](../../backend/app/services/report_distribution_policy.py).
