# Requirements and Behavior Matrix

This matrix joins the current PRD, the agentic requirements, public API
contracts, and executable evidence. “Live” means deployment-backed evidence;
“CI” means blocking on every relevant change.

| ID | Requirement / behavior | Primary evidence | Level | Residual gap |
|---|---|---|---|---|
| REQ-01 | Ingest CI/local results without losing cases, including large runs | ingestion unit/integration and 32,767/50,000 PostgreSQL boundary proof | CI/release | Browser upload-to-results is live-only |
| REQ-02 | Keep every core read/write project scoped | authz/tenant-isolation suites and router guards | CI | Periodic production canary is absent |
| REQ-03 | Explain failed runs with traceable evidence | intelligence, evidence, citation, artifact and agent-eval suites | CI/live | Provider-specific phrasing is evaluated, not deterministic |
| REQ-04 | Cluster related failures and avoid duplicate defects | clustering, duplicate-candidate, promotion and Jira adapter tests | CI/live | Third-party Jira sandbox is release-only |
| REQ-05 | Compute explainable, auditable release readiness | release-policy, override, audit and decision-report suites | CI/live | Full UI decision/override path remains live-only |
| REQ-06 | Fall back deterministically when AI/search integrations fail | provider-policy, offline ceiling, keyword fallback and outage tests | CI | Cross-service outage combinations need chaos sessions |
| REQ-07 | Support semantic, keyword and hybrid search | search service/API/component/live Playwright suites | CI/live | Scale/relevance distributions need production-like corpus |
| REQ-08 | Enforce roles and separation of duties | API authorization ratchet, review service/integration and hermetic browser review-role tests | CI/live | Complete deployed role matrix remains to be verified (M01/M02/M07/M20) |
| REQ-09 | Gate unreviewed AI narratives and actions | review-gate API/worker/notification/export tests, including exact Investigator review subjects shipped in PR #119 | CI/live | Actual delivery across every subject/state/channel remains to be verified (M08) |
| REQ-10 | Preserve immutable, resumable workflow authority | compiler, frozen context, checkpoint, replay and mutation suites | CI | Generic live inference binding remains a documented workflow gap |
| REQ-11 | Route models by capability, cost and escalation policy | ModelRouter, registry invariant, fallback and eval suites | CI | Tier promotion requires E9.3 inference evidence |
| REQ-12 | Protect secrets and never store provider credentials in configs | schema, redaction, settings, API and security tests | CI | Secret-store outage recovery needs operational exercise |
| REQ-13 | Audit sensitive configuration, review and release actions | activity/audit guards and endpoint/service tests | CI | Cross-system audit reconciliation is manual |
| REQ-14 | Keep expensive processing asynchronous and observable | worker, outbox, DLQ, alerts, fencing and retry suites | CI/live | Sustained broker partition/load is not a per-PR test |
| REQ-15 | Give users a reliable login-to-triage journey | component suites, hermetic auth journeys, live navigation/probes | CI/live | The complete run-to-defect-to-release journey is not hermetic |
| REQ-16 | Make regressions measurable | backend/frontend floors, independent MCP 41% and CLI 62% floors, quality guards and mutation checks | CI | Coverage percentages do not replace live journey and failure-path assertions |

Reconciled against `34110eac` on 2026-09-16; no new tests were run for this
documentation update. [The exploratory package](EXPLORATORY_EXECUTION_PACKAGE.md)
maps the remaining live evidence to executable missions.

## Trace rules

- A requirement is **covered** only when an assertion observes its outcome.
  A route existing, a mock being configured, or a screenshot being taken is not
  enough.
- Security and tenant requirements need a negative case against a second actor
  or project.
- Asynchronous requirements need durable-state assertions after retry/restart,
  not only a successful task call.
- AI requirements separate deterministic contract evidence from inference
  quality evidence. A mocked model cannot establish model quality.
- Live-only evidence is a release condition until an equivalent hermetic or
  service-level test blocks CI.
