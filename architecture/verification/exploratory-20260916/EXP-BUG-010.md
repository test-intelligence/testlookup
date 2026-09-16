# EXP-BUG-010 — feature-flag status accepted an inaccessible project scope

**Mission / requirement / severity:** M02, project isolation through every
interface, P1.

The feature-flag status route accepted an optional `project_id` and passed it
straight to rollout evaluation. It validated UUID syntax but never checked
that the caller could access the named project. During stale-selection healing,
an A-only browser therefore received `200` for both `manual_upload` and
`ask_ai_chat` queries carrying B's project ID. The response is only a boolean,
but it is still a successful project-scoped answer outside the caller's tenant.

The route now resolves every supplied project through the shared
`resolve_project_scope` authority before evaluating the flag. Invalid IDs keep
their 400 contract, inaccessible projects return 403, and unscoped calls remain
available to any authenticated user.

**Regression tests:** the focused API regression requires denial before flag
evaluation. The live M02 browser journey retains a stale B selection, captures
every response URL containing B's ID, and requires all of them to fail while
the selector heals to A.

**Red evidence:** exact deployed revision
`c3645dde15f5d3da7d28307cb3da5b8ce668fad1` returned 200 for both feature-flag
status requests in Chromium, Firefox, and WebKit. A traced ten-run Chromium
reproduction failed the same assertion in eight journey executions; the trace
identified the two endpoint URLs. One unrelated repeated run hit the login
rate limit, which the test now avoids by reusing its already-issued A token.

**Green evidence:** exact revision
`05ae055aa119ffb42268b7cf9a29467b5480d1d9` was deployed as
`build-20260916-183915` before validation. `/health/version` reported that
revision, dependency health was green, and schema head was `0189`. Each of the
nine application deployments had at least one ready replica on the exact tag
and digest; both observed critical-worker replicas reported that exact digest.
The focused API
suite passed 10 tests, the authorization ratchet passed 52, and the M02 journey
passed in Chromium, Firefox, and WebKit. TypeScript, ESLint, five project-store
regressions, Ruff, the 369-error mypy ratchet, all 43 quality guards, and 238
guard self-tests passed.

**Mutation:** `scripts/mutation_check_exploratory_m02_feature_flags.py` removed
the scope resolver call, required the focused regression to fail, asserted the
mutation applied exactly once, and restored the source byte-for-byte.

**Independent review:** APPROVE, staged remediation hash
`a54afebdc74adcec467c120a57b344a0daf3cd2f`; no findings and no reviewer edits.

**Deployment deviation:** the first full deploy stopped at the authority check
while a second critical-worker replica was still converging. Both replicas
reported the exact pushed digest on immediate inspection. The idempotent
`--skip-build` continuation completed, and the independent post-deploy check
verified one ready replica for every application deployment on the exact tag
and digest before tests began.
