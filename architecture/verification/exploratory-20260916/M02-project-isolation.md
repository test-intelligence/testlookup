# M02 project isolation

**Red candidate:** live-journey commit
`c3645dde15f5d3da7d28307cb3da5b8ce668fad1`, tag
`build-20260916-181940`, schema `0189`.

**Fix candidate:** `05ae055aa119ffb42268b7cf9a29467b5480d1d9`, tag
`build-20260916-183915`, schema `0189`.

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

**Result:** RUNNING. Browser and direct API isolation are green; CLI and MCP
repetitions remain.
