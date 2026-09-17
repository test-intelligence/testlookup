# M09 — search and evidence retrieval

**Result:** PASS — bounded-provider behavior is disclosed as partial and all
identified scope, fallback, failure, pagination and session-safety defects are
covered by executable regressions.

**Window:** 2026-09-17T09:35Z–2026-09-17T10:35Z

**Final executable candidate:** `0da8f248ef0734e1bddc208f556e491c83001c11`,
deployed before validation as `build-20260917-101919`, built at
`2026-09-17T10:19:20Z`. `/health/version` reported the exact revision after all
application and worker rollouts became ready. Image digests were
`sha256:03bd2dc35ac5372c8f774b665854a2e06fbefd47f8aa32345744380559fda654`
for backend and workers,
`sha256:7483ce9312282608799a9e1f18a417f57056ec4016ad1717c4932e9c5cfd1106`
for frontend, and
`sha256:14feb62ea9366d481cb3440ed8831ba0ce91b657ddeffb414cc4dfe85292be5a`
for MCP.

## Findings and fixes

Eight defects are recorded as EXP-BUG-049 through EXP-BUG-056. Entity-count
queries now execute sequentially on the request's single `AsyncSession`.
Chroma failure in hybrid mode reaches the keyword fallback and is labeled as
keyword. Hybrid retrieval uses one fixed 200-result candidate universe across
pages, and date-filtered semantic search uses the same bounded window. Semantic
and hybrid responses disclose that their candidate-derived
totals are lower bounds.

Global search now reports failed adapters, cap hits and inexact counts through
additive `result_status`, `failed_entity_types` and `counts_are_exact` fields.
The web, CLI and MCP consumers render lower bounds and unavailable sources
instead of presenting incomplete samples as exact. Suite and flaky results use
project-bound identities, so identical names or fingerprints remain distinct.
Suite links are encoded and project-scoped, and web navigation selects the
matching authorized project. Similar-search database failures propagate
instead of looking like valid empty evidence.

## Verification

- 142 broad backend search tests plus 10 focused M09 regressions passed against
  the exact deployed candidate, including
  empty/browse paths, punctuation escaping, Unicode, tenant and deleted-project
  scope, period filtering, fallback labeling, evidence lookup and pagination.
- The M09 backend and consumer mutation harnesses killed all 16 mutations. They assert every
  replacement applies exactly once, requires each wrong behavior to fail, and
  restores the original bytes.
- 18 SearchPage tests, TypeScript type checking and ESLint passed. ESLint
  retained the repository's 19 pre-existing warnings and reported no errors.
- All 138 CLI tests passed with 2 skips; all 174 MCP tests passed.
- Ruff passed for the changed Python surfaces. The mypy ratchet held at 369
  errors in 114 files. All 43 quality guards and 238 guard self-tests passed.
- PostgreSQL migration and every backend, frontend, MCP and worker rollout
  completed before the validation run; live and version health were green.

## Deviations and remaining gaps

Semantic ranking is intentionally relevance-based and Chroma queries remain
bounded at 200 candidates. Global fan-out adapters also retain their existing
small per-entity caps. The response now marks those cap hits as partial, so the
reported number is an explicit lower bound rather than a false exact total.
Exact arbitrary-depth semantic and mixed-entity pagination would require a
cursor/count contract and is deferred.

The mission used deterministic provider-failure injection and the deployed
service's real relational search paths. No destructive deletion of shared
homelab evidence was performed. Existing retention and project-deletion tests
prove stale relational/vector authority cleanup without risking other users'
data in the shared namespace.
