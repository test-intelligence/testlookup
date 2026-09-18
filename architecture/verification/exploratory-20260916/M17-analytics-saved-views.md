# M17 — Analytics, dashboards and saved views

## Result

**PARTIAL — the exact executable candidate is deployed and all safe local and
deployed read-only variants pass; interactive shared-homelab mutations and
destructive outage injection remain blocked.** Candidate `e637346d` was
deployed as `build-20260918-032752`. `/health/version` returned the full
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

- Focused backend suite: **46 passed**. Broad analytics, saved-view, metrics,
  value, coverage, authorization and transaction-boundary suite: **300 passed**.
- Focused frontend suite: **45 passed**. Full frontend suite: **1,798 passed in
  241 files** after correcting a stale source-contract assertion for the
  already-memoized routed Releases array.
- Backend Ruff passed; mypy held at **367/367**; TypeScript passed; ESLint
  reported **0 errors** and 19 existing warnings.
- Quality-gate, mypy-ratchet and CI-security self-tests passed **273 tests**;
  all **43 guards** remained green. Generated agent API documentation matched
  OpenAPI and Alembic had the single head `0191`.
- Mutation harness: **30 unsafe changes killed**. Every selector was asserted
  to apply exactly once and source restoration was retried and verified after
  each mutation.
- Homelab authority: revision
  `e637346dae823cedb1d1e3fcbbd800951f9d2b25`; backend and worker digest
  `sha256:ba560367d166a5e78abc9d39343513483f1a1368112a30d027a8095b70a06e1d`;
  frontend digest
  `sha256:a8712a8c3ac8ef868d75added1696cc72a2e8a814519f2028bec84d31fc45943`;
  MCP digest
  `sha256:0dbe7c85653c85043e4ae28d35d60cb52f8bf31176acc6370c5626f88d6c7263`.
- The deployed `/overview` route returned 200 and an unauthenticated saved-view
  request returned 401 through ingress.

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
