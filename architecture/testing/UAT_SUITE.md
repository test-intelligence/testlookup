# User Acceptance Test Suite

Acceptance is performed on a release candidate with production-like PostgreSQL,
Redis, worker and browser configuration. Each case records the actor, project,
fixture IDs, expected business outcome, actual outcome and evidence link.

| ID | Persona and goal | Acceptance procedure | Pass criteria |
|---|---|---|---|
| UAT-01 | New QA lead reaches value | Create/select a project, follow getting-started, ingest the supplied failed run | Run appears with exact totals and a clear next action without admin help |
| UAT-02 | Engineer triages a failure | Open failed run → intelligence → cluster → evidence → owner | Explanation is understandable, cited, project scoped and actionable |
| UAT-03 | QA lead promotes a defect | From a cluster, inspect duplicate candidates and promote | Existing match is offered; one durable local/external issue and audit event result |
| UAT-04 | Release manager makes a decision | Select release, inspect gate factors, record allowed override | Verdict, evidence freshness and override rationale are visible and auditable |
| UAT-05 | Reviewer controls AI output | As producer create pending report; as another lead accept/reject | Producer is refused; public content is gated until accepted; rejection stays terminal |
| UAT-06 | Team works during AI outage | Disable provider/vector service and repeat triage/search | Deterministic/keyword paths remain useful; unavailable features say why |
| UAT-07 | Admin manages access | Exercise viewer/tester/engineer/lead roles and revoke an API key | UI and API agree; revoked key stops; no project data crosses roles/tenants |
| UAT-08 | QA lead publishes workflow | Fork, edit, validate, evaluate and publish a workflow | Invalid definitions fail clearly; evidence is shown; invoked run freezes exact version |
| UAT-09 | Operator recovers failure | Pause worker/broker, observe alert/DLQ, restore and replay supported entry | No duplicate side effect; alert clears; audit/correlation trail explains recovery |
| UAT-10 | User survives connectivity change | Start authenticated work, change VPN/network, restore, use offered retry | Session is not silently destroyed by transient failure; mutation occurs at most once |
| UAT-11 | Privacy owner deletes data | Preview retention/deletion, confirm, wait for completion | Preview matches deletion, protected data stays, result and actor are audited |
| UAT-12 | End user finds prior knowledge | Search known failure in semantic/hybrid mode and open result | Relevant same-project evidence is found; fallback remains clear during vector outage |

## Exit criteria

- UAT-01 through UAT-07 and UAT-10 pass with no workaround.
- No open critical issue; high issues have an owner and explicit release decision.
- Counts, identities and timestamps match database/API evidence.
- Accessibility checks cover keyboard-only completion of the critical path.
- Operations has executed recovery, not merely reviewed a runbook.
