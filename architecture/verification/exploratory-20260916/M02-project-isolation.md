# M02 project isolation

**Red candidate:** live-journey commit
`c3645dde15f5d3da7d28307cb3da5b8ce668fad1`, tag
`build-20260916-181940`, schema `0189`.

**Fix candidate:** `05ae055aa119ffb42268b7cf9a29467b5480d1d9`, tag
`build-20260916-183915`, schema `0189`.

**Final validation candidate:**
`f051798cef9a9913cd12ab3f23a23a42fdb28f6c`, tag
`build-20260916-194417`, schema `0189`. All nine application deployments were
fully available on the exact tag and manifest digests. `/health/version`
reported the exact revision and readiness/details were healthy before tests.

The first live API pass created unique A/B QA leads, projects, runs, activity,
and an A-bound API key. A's unscoped project and run lists contained only A.
B project detail, runs, search, suites, activity, activity export, ingestion,
and A-bound-key run access all returned 403 without the B sentinel. B remained
inaccessible after soft deletion. Cleanup deleted both synthetic projects and
deactivated both synthetic users.

The permanent browser journey then ran against that exact deployment. Its API,
key, list, UI visibility, stale-selection healing, and cleanup checks passed,
but Chromium, Firefox, and WebKit each observed two successful requests that
still carried B's ID. A traced ten-run reproduction identified both as
`manual_upload` and `ask_ai_chat` feature-flag status requests. The release
request using the same B ID correctly returned 403. This is tracked as
EXP-BUG-010. Exact revision `05ae055a` subsequently closed the missing guard;
the focused API suite, authorization ratchet, mutation check, and Chromium,
Firefox, and WebKit live journeys all passed after deployment.

The final validation covered the remaining interface and recovery paths:

- Chromium, Firefox, and WebKit passed the live A/B project-isolation journey,
  including unscoped list isolation, inaccessible/deleted B scope healing, API
  key denial, and absence of successful B-ID requests.
- The delayed scoped-outage regression passed retry plus browser back/forward
  history while preserving B's scope and never rendering A's cached row.
- Real CLI subprocesses isolated A's project list and detail and denied B
  project, run-list, run-detail, and search requests.
- A real MCP stdio session isolated A's project list and detail and denied B
  project, run-list, run-detail, and global-search calls.
- Direct child lookup using B's test ID beneath A's run returned 404 without B
  content.
- The live fixture now completes the required admin-issued password reset and
  proves bootstrap-token revocation before it begins the isolation journey.

Supporting checks passed: 23 project-scope frontend unit tests, TypeScript,
warning-free lint on both isolation specifications, 173 MCP tests, 137 CLI
tests with two skips, 421 key backend tests, the 369-error mypy ratchet, all 43
quality guards, 238 guard self-tests, and the agent API documentation check.

**Result:** PASSED. Independent closure review approved the browser, API, CLI,
MCP, direct-child, mutation, and deployment evidence at staged diff hash
`17b170100beb1edf159969c16e5388cec3ba9ffe`.
