# QA Run 2026-08-29-01

Status: VERIFIED FIXED IN HOMELAB — one confirmed UI defect found, reviewed, fixed, tested, deployed, and verified. GitHub publication/merge was intentionally not performed in this exploratory loop.

## Results

- Defects found: 1 (`S4/P2`), discovered during exploratory testing and independently confirmed.
- Defects fixed: 1; no deferred product defect.
- Source/test change: `frontend/src/pages/OverviewPage.tsx`, unit regression, and browser regression spec.
- Focused tests: 33/33 passed.
- Full frontend tests: 164 files, 1,122/1,122 passed.
- Browser regression: authenticated Chromium 2/2 against homelab.
- Lint: 0 errors; 18 existing warnings.
- Production build: passed.
- Homelab image: `build-20260829-050941`; rollout and health verification passed.
- Live dashboard geometry: corrected from `154px / 800px` with `0px` lede width to `374.109px / 579.891px` with `207.109px` lede width.

## What was tested

Authenticated route and project-scope sweeps, read-only filters, test-management tabs, run detail/intelligence, compare, suite coverage, docs, browser layout, console diagnostics, API health/readiness/details/OpenAPI, and deployed dashboard behavior.

## Remaining coverage gap

State-changing journeys (uploads/ingestion, release overrides, quarantine transitions, integrations, exports, and account mutations) remain for a separately approved stateful exploratory session. No code changes are justified from those unexecuted paths in this run.
