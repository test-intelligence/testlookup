# M17 — Analytics, dashboards and saved views

## Result

**PARTIAL — the exact executable candidate is deployed and all safe local and
deployed read-only variants pass; interactive shared-homelab mutations and
destructive outage injection remain blocked.** Candidate `f7001619` was
deployed as `build-20260918-041409`. `/health/version` returned the full
revision, every application deployment became ready on the candidate tag and
digest, health was green, and Alembic reported `0191 (head)`.

## What was proved

- Saved-view direct reads and owner mutations enforce current project
  membership. Project-less shared rows remain private to their owner, while
  all-project lists contain only accessible project views and the caller's
  global views.
- The API accepts legacy filter-only views but rejects unknown pages, malformed
  analytics layout entries, and layouts above the 12-instance limit.
- Page and project define one frontend persistence scope. The client requests
  only that page, prefers the caller's owned row, never patches a shared row,
  fences writes until hydration, retries failed hydration, repairs stale
  layouts, and persists the newly selected value.
- Every widget offered by the picker maps to a rendered panel, and deselecting
  the tested Trends, Coverage, Defects, and Failures panels removes them.
- Trend and comparison periods use UTC calendar boundaries. Sparse coverage
  points cannot redefine the requested comparison windows, and Billing renders
  the backend's half-open UTC month as the correct inclusive range.
- Committed ingestion invalidates both project and all-project analytics cache
  keys. Value Metrics and Billing show retryable request failures instead of
  presenting an outage as empty or zero data.

## Verification

- Focused backend suite: **48 passed**. Broad analytics, saved-view, metrics,
  value, coverage, authorization and transaction-boundary suite: **301 passed**.
- Focused frontend suite: **46 passed**. Full frontend suite: **1,799 passed in
  241 files** after correcting a stale source-contract assertion for the
  already-memoized routed Releases array.
- Backend Ruff passed; mypy held at **367/367**; TypeScript passed; ESLint
  reported **0 errors** and 19 existing warnings.
- Quality-gate, mypy-ratchet and CI-security self-tests passed **273 tests**;
  all **43 guards** remained green. Generated agent API documentation matched
  OpenAPI and Alembic had the single head `0191`.
- Mutation harness: **34 unsafe changes killed**. Every selector was asserted
  to apply exactly once and source restoration was retried and verified after
  each mutation.
- Homelab authority: revision
  `f7001619ab0c84e02fc78c209c13662beb252d7e`; backend and worker digest
  `sha256:cc2fc4c7e125e8b2401874cd038cd9ab8e459f9f2a52823945f30a928cedb1b2`;
  frontend digest
  `sha256:a3bd457a2ff0f6c3815e7e3f53f27861a0e8030e33a1d8f5284ddcd9b242cffe`;
  MCP digest
  `sha256:0dbe7c85653c85043e4ae28d35d60cb52f8bf31176acc6370c5626f88d6c7263`.
- The deployed `/overview` route returned 200 and an unauthenticated saved-view
  request returned 401 through ingress.
- Independent final re-review returned **APPROVE** after verifying the legacy
  page fallback, deferred create-response fence, UTC SQL bucket and terminal
  cache invalidation plus their focused mutations.

## Defects fixed

EXP-BUG-102 through EXP-BUG-107.

## Deviations and remaining gaps

The shared homelab has no disposable analytics project or scoped test
credential. Creating, changing, sharing, or deleting saved views in an
arbitrary project was therefore not attempted. The deployed checks were limited
to exact revision, image, readiness, health, and schema authority; API/router,
frontend, real query-construction, and mutation tests exercised the behavioral
contracts locally against the same executable sources.

The current picker toggles a bounded set of one-instance widget templates. The
underlying stored format supports ordered and duplicate instances, but the
shipping pages render fixed section order and expose no reorder or duplicate
controls. Multi-tab first-save conflict resolution also has no server-side
version/idempotency token. These product gaps keep M17 partial.

Database, Redis, and network outage injection remains deferred because the only
available namespace is shared. Retryable UI error-state tests cover the
presentation contract without disrupting homelab users. Billing purchase paths
were not exercised, as required by the mission.
