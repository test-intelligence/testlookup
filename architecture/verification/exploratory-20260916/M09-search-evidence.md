# M09 — search and evidence retrieval

**Result:** PASS — bounded-provider behavior is disclosed as partial and all
identified scope, fallback, failure, pagination and session-safety defects are
covered by executable regressions.

**Window:** 2026-09-17T09:35Z–2026-09-17T10:07Z

**Final executable candidate:** `5e43ba14d7bf8072d107d02d356b2e3bbcc86d05`,
deployed before validation as `build-20260917-095930`, built at
`2026-09-17T09:59:31Z`. `/health/version` reported the exact revision after all
application and worker rollouts became ready. Image digests were
`sha256:9fdee82d24c6edbb2977354c3617f98ce01747c26a934d8225f592713546b400`
for backend and workers,
`sha256:7483ce9312282608799a9e1f18a417f57056ec4016ad1717c4932e9c5cfd1106`
for frontend, and
`sha256:9629601d5d55dcf9606447abb0ae3f24d28df00495108ef0df14b82d25085bc7`
for MCP.

## Findings and fixes

Seven defects are recorded as EXP-BUG-049 through EXP-BUG-055. Entity-count
queries now execute sequentially on the request's single `AsyncSession`.
Chroma failure in hybrid mode reaches the keyword fallback and is labeled as
keyword. Hybrid retrieval sizes its bounded candidate pool for the requested
page, and date-filtered semantic search uses the full 200-result bounded
window. Semantic and hybrid responses disclose that their candidate-derived
totals are lower bounds.

Global search now reports failed adapters, cap hits and inexact counts through
additive `result_status`, `failed_entity_types` and `counts_are_exact` fields.
The web, CLI and MCP consumers render lower bounds and unavailable sources
instead of presenting incomplete samples as exact. Suite results retain their
project identity, group identical names separately per project, use encoded
project-scoped links, and select the matching authorized project before web
navigation. Similar-search database failures propagate instead of looking like
valid empty evidence.

## Verification

- 142 backend search tests passed against exact deployed `5e43ba14`, including
  empty/browse paths, punctuation escaping, Unicode, tenant and deleted-project
  scope, period filtering, fallback labeling, evidence lookup and pagination.
- The focused M09 mutation harness killed all 9 mutations. It asserts every
  replacement applies exactly once, requires each wrong behavior to fail, and
  restores the original bytes.
- 18 SearchPage tests, TypeScript type checking and ESLint passed. ESLint
  retained the repository's 19 pre-existing warnings and reported no errors.
- All 137 CLI tests passed with 2 skips; all 173 MCP tests passed.
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
