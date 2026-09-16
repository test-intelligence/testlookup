# M02 project isolation

**Candidate:** live-journey commit
`c3645dde15f5d3da7d28307cb3da5b8ce668fad1`, tag
`build-20260916-181940`, schema `0189`.

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
EXP-BUG-010; its fix is prepared for the next exact-candidate deployment.

**Result:** RUNNING. EXP-BUG-010 must pass the three-browser retest; CLI and MCP
repetitions remain.
