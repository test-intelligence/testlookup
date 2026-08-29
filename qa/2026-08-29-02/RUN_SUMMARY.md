# QA Run 2026-08-29-02

Status: FIXED LOCALLY — the commit-range defect is covered and all affected CLI/SDK tests pass. Publication and merge remain pending final review/remote authorization.

## Results

- Defects found: 1 (`P1`), confirmed in both the Python SDK and CLI copies of commit-range collection.
- Defects fixed: 1; both implementations now reject a non-checkout `repo_path` before probing Git.
- New test files: none required; the existing shared 161-case regression suite failed before the fix and passes after it.
- Full frontend tests: 164 files, 1,123/1,123 passed.
- Full CLI + SDK tests: 254/254 passed after the fix.
- Backend tests: 7,445 passed and 58 skipped; remaining failures/errors are Windows shell/toolchain limitations documented in `baseline.md`.
- Frontend lint/build/theme/bundle checks: passed; lint has 18 existing warnings.
- Live UAT: not run because Docker is not installed in this environment.

## Coverage gap assessment

The highest-value newly observed gap was explicit-path isolation in the local Git collector. Existing tests covered non-Git paths, but the implementation allowed Git to walk upward into the parent checkout, so the negative-path regression exposed a real data-attribution defect.

State-changing live journeys (uploads/ingestion, release overrides, quarantine transitions, integrations, exports, and account mutations) remain deferred until a Docker-backed or deployed environment is available.

