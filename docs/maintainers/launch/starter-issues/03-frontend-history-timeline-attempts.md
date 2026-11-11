---
title: Use the shared retry helper in the test history timeline
labels: [good first issue, frontend, bug]
---

## What

`frontend/src/utils/retryEvidence.ts` exists "so run detail, test detail and the history timeline all agree on what 'attempt' means" (lines 10-11). Its `retryAttempts()` (lines 29-33) counts a run flagged `is_flaky_run` with no `retry_count` as attempt 2, on purpose: "otherwise the whole point (this test did not pass cleanly) is erased".

The history timeline does not use it. It repeats the logic inline at `frontend/src/components/suites/TestHistoryTimeline.tsx:104` and `:143`, and builds the tooltip at `:151` as `passed on attempt ${(item.retry_count ?? 0) + 1}`. For a run with `retry_count: null, is_flaky_run: true`, the timeline says "passed on attempt 1", while the run page (`frontend/src/pages/run/RunTestsTab.tsx:415-421`, which calls `retryAttempts`) says attempt 2.

## Why it helps

One page should not contradict another about the same test result. It also removes duplicated logic.

## Where to start

- `frontend/src/utils/retryEvidence.ts:29-38`: `retryAttempts()` and `wasRetried()`.
- `frontend/src/components/suites/TestHistoryTimeline.tsx:104`, `:143`, `:151`: the three inline copies. The item type (`frontend/src/types/suites.ts:105-106`) already has the `retry_count` and `is_flaky_run` fields the helper reads.
- `frontend/src/pages/run/RunTestsTab.tsx:51` and `:415-421`: how the run page uses the helper.
- Tests to copy: `frontend/src/components/suites/TestHistoryTimeline.test.tsx:56-66` (the two retry tests) and any small util test such as `frontend/src/utils/runPassRate.test.ts`.

## Done when

- [ ] `TestHistoryTimeline.tsx` uses `wasRetried()` and `retryAttempts()` instead of its inline checks.
- [ ] A new test in `TestHistoryTimeline.test.tsx` renders an item with `retry_count: null, is_flaky_run: true` and checks that the cell title contains `passed on attempt 2`. It fails before your change and passes after.
- [ ] A new `frontend/src/utils/retryEvidence.test.ts` covers: no retries (1 attempt), `retry_count: 2` (3 attempts), flaky flag only (2 attempts), and `null`/missing fields (1 attempt, not retried).
- [ ] `cd frontend && npx vitest run src/components/suites/TestHistoryTimeline.test.tsx src/utils/retryEvidence.test.ts` passes, and so do `npm run lint` and `npm run type-check`.
- [ ] A line under `[Unreleased]` → Fixed in `CHANGELOG.md`.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

## Size

About 1–2 hours; no Docker stack needed. Node 22.13+ and `npm ci` in `frontend/`.
