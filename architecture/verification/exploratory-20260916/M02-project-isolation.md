# M02 project isolation

**Candidate:** runtime commit
`1cff9ae4b942f805a700339a035b14f69216d95e`, tag
`build-20260916-174750`, schema `0189`.

The first live API pass created unique A/B QA leads, projects, runs, activity,
and an A-bound API key. A's unscoped project and run lists contained only A.
B project detail, runs, search, suites, activity, activity export, ingestion,
and A-bound-key run access all returned 403 without the B sentinel. B remained
inaccessible after soft deletion. Cleanup deleted both synthetic projects and
deactivated both synthetic users.

**Result:** RUNNING. Permanent three-browser coverage for stale persisted B
scope is prepared but has not run; CLI and MCP repetitions remain.
