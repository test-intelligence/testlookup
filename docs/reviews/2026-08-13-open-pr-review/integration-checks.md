# Cross-file Integration Checks

## Dependency overlap

PR #569 and #335 modify the same two frontend dependency files. #569 is the
later 3.9.6 resolution and explicitly supersedes #335; merging both would
either conflict or regress the dependency version.

## Changelog flow

PRs #568, #569, #570, and #571 all insert entries near the same unreleased
section in `CHANGELOG.md`. GitHub's current `mergeable=true` values are not a
guarantee after another PR changes the base. The merge operator must preserve
all entries and rerun checks after each conflict resolution.

## Frontend onboarding flow

`OverviewPage.project?.id` → `FirstRunGuide.projectId` →
`buildSteps(projectId)` → `uploadCommand(projectId)` → rendered command and
clipboard callback. Types and tests align. The only edge mismatch is that the
helper tests trimmed truthiness but not output (P2).

## Health endpoint flow

The existing health router is already included by the backend application, so
the new decorator is reachable in the normal architecture. The new handler
uses the same `settings`, `build_provenance()`, and uptime conventions as
`/health/details`; no migration, queue, or dependency contract changes were
introduced. A route-level test is still the strongest missing integration
proof.

## CLI transport flow

`get_profile()` → `base_url` → `httpx.AsyncClient` → `map_connection_error()`
for transport failures. HTTP status failures continue through
`map_http_error()` in the shared client. The direct upload path uses the same
transport mapper but retains its pre-existing generic status exception. The
base URL is also user-controlled and should be sanitized before being echoed.

## CI and review metadata

All five PR head commits have a successful `TestLookup — CI/CD` workflow run.
The connector reports no submitted reviews or inline review threads. No live
PostgreSQL migration or homelab deployment is part of these PRs; the protected
post-merge checks remain required.

