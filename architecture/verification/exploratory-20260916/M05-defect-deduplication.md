# M05 — Defect deduplication and ambiguous Jira outcome

**Result:** BLOCKED (all repository and real-PostgreSQL variants passed; a
dedicated Jira sandbox is unavailable)

**Window:** 2026-09-16T23:04Z–2026-09-16T23:25Z

**Source under test:** `49ce63a18245292be95804cf2ae7c2a6ca28066e`
(`d711e93f69e186123d40e067f61ca98a9124b056`)

**Environment:** homelab namespace `testlookup`, Kubernetes context `default`;
the integration suite reached its PostgreSQL service through a temporary local
port-forward. Jira calls in the integration proof used the repository's
counting fake, so no external issue was created.

## Deploy-before-test proof

Before the M05 tests, `http://testlookup.local/health/version` reported build
revision `49ce63a18245292be95804cf2ae7c2a6ca28066e`, built at
`2026-09-16T22:40:49Z`. All ten application Deployments reported their desired
ready replica count and Alembic reported `0189 (head)`. The deployed executable
therefore matched the candidate source used by the tests.

## Executed variants

The focused Jira endpoint suite passed **37/37**. It covers an existing open
signature, create and staged local linkage, disabled/offline and unconfigured
connectors, provider rejection, unreachable metadata, webhook gating, route
authorization, stable signatures, a commit lost after Jira accepted, explicit
`jira_outcome_unknown`, safe retry, label reconciliation, multiple label
matches, explicit human `confirm_not_filed`, failed reconciliation search, a
new generation after closure, and lock-before-dedup ordering.

The real PostgreSQL proof passed **3/3** against the deployed homelab schema:

- two concurrent submissions serialized and produced one counted Jira POST,
  one Defect row, and a deduplicated second response;
- a simulated router rollback after Jira acceptance preserved the independently
  committed ledger result, and retry linked it without a second POST;
- a timed-out POST left the claim `executing`, returned 409
  `jira_outcome_unknown`, refused a blind retry while Jira search was empty,
  then reconciled `IT-77` when the signature label became visible.

The local defect/promotion policy suites passed **36/36**. They preserve the
truthful local Defect path when Jira is offline or approval is absent, keep
deduplication project-scoped, and retain the source release association. M04's
live journey separately proved that a promoted local HIGH defect persisted and
affected its release decision after reload.

No application defect was found in the executable paths exercised here.

## Mutation proof

A temporary harness asserted each replacement occurred exactly once and always
restored the original bytes. All **3/3** wrong-behavior mutations were killed:

1. removing the PostgreSQL signature advisory lock;
2. treating an ambiguous transport outcome as an ordinary retryable failure;
3. reversing the `confirm_not_filed` guard so an unconfirmed retry could post.

The service sources matched their original bytes after the run, and the working
tree contains no tracked application change from the harness.

## Commands and results

- `pytest tests/test_defect_jira_endpoint.py -q -p no:testlookup` — 37 passed.
- `pytest tests/integration/test_defect_jira_exactly_once_postgres.py -q -p no:testlookup` — 3 passed against real PostgreSQL.
- `pytest tests/test_defect_promotion_service.py tests/regression/test_defect_promotion_tenant_and_offline.py tests/regression/test_defect_commander_jira_approval.py -q -p no:testlookup` — 36 passed.
- temporary exact-replacement mutation harness — 3 mutations killed.

## Remaining blocked boundary

F4 is absent: there is no dedicated Jira sandbox, request journal, or supported
fault proxy. Consequently this run does not claim a real external issue count,
a real accepted-create/drop-response boundary, real Jira 429/5xx behavior, or
end-to-end reconciliation against Jira's eventual search index. Those steps
remain BLOCKED rather than being inferred from the fake adapter. Resume M05 when
a disposable Jira project and fault-capable endpoint are provided; use only
synthetic `exp-*-M05` issues and retain both client and sink timestamps.
